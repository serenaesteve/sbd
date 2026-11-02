"""Ejecución de órdenes: evaluación de expresiones, consultas (buscar) y cambios de datos.

Durante una consulta, cada fila candidata es un dict {alias: fila} donde `fila` es el
dict de columnas de esa tabla (o None si viene de la parte vacía de un OUTER JOIN).
Un Env une esa fila con el Scope (qué columnas tiene cada alias) y con el Env exterior,
lo que permite las subconsultas correlacionadas.
"""
import re
import time
from functools import lru_cache

from .errors import SBDError
from .functions import FUNCTIONS
from .nodes import (AGGREGATES, WINDOW_ONLY, ArgProc, Between, Binary, Case, Cast, Column, DefaultValue, Exists,
                    FuncCall, InList, InSelect, IsNull, Join, Like, Literal, Param, Select, Star, Subquery, SubqueryRef,
                    TableRef, Unary, Window, flatten_and, has_aggregate, is_aggregate, mentions_table, walk)
from .result import Result
from .values import (Intervalo, arithmetic, compare, convert, hash_key, sort_key, sumar_numeros, to_number,
                     to_text, truth)

MAX_RECURSIVE_ITERATIONS = 100_000
MAX_CROSS_ROWS = 5_000_000
MAX_RECURSIVE_ROWS = 1_000_000
_DEFAULT = object()  # marca "usar el valor por defecto" al insertar


class Scope:
    """Qué tablas (o alias) hay en una consulta y qué columnas tiene cada una."""
    __slots__ = ('aliases', 'columns', '_cache')

    def __init__(self, sources):
        self.aliases = [alias for alias, _ in sources]
        self.columns = {alias: set(cols) for alias, cols in sources}
        self._cache = {}

    def resolve(self, table, name):
        key = (table, name)
        if key in self._cache:
            return self._cache[key]
        if table is not None:
            cols = self.columns.get(table)
            if cols is None:
                alias = None
            elif name in cols:
                alias = table
            else:
                raise SBDError(f"la columna «{table}.{name}» no existe")
        else:
            matches = [a for a in self.aliases if name in self.columns[a]]
            if len(matches) > 1:
                raise SBDError(f"la columna «{name}» es ambigua (está en {', '.join(matches)}); "
                               f"escribe tabla.{name}")
            alias = matches[0] if matches else None
        self._cache[key] = alias
        return alias


class Env:
    __slots__ = ('scope', 'row', 'outer', 'group', 'outputs')

    def __init__(self, scope, row, outer=None, group=None):
        self.scope = scope
        self.row = row
        self.outer = outer
        self.group = group      # filas del grupo actual (consultas con agregados)
        self.outputs = None     # columnas de «mostrar», visibles desde «ordenar»


EMPTY_SCOPE = Scope([])
_SIN_INDICE = object()
_GIRADO = {'=': '=', '<': '>', '<=': '>=', '>': '<', '>=': '<='}  # 5 < x  es  x > 5
_OP_NAMES = {'union': 'union', 'union all': 'union todo', 'intersect': 'comun', 'except': 'excepto'}
_NO_CONSTANTE = (Column, Star, DefaultValue, Subquery, Exists, InSelect, Window)


def _constante(expr):
    """Vale lo mismo en todas las filas («hace 30 dias», «? * 2»…): se puede buscar en un índice."""
    return all(not isinstance(n, _NO_CONSTANTE)
               and not (isinstance(n, FuncCall) and (n.name == 'aleatorio' or is_aggregate(n)))
               for n in walk(expr))


def _plural(n, word):
    s = '' if n == 1 else 's'
    return f"{n} fila{s} {word}{s}"


@lru_cache(maxsize=256)
def _like_regex(pattern):
    parts = []
    for ch in pattern:
        if ch == '%':
            parts.append('.*')
        elif ch == '_':
            parts.append('.')
        else:
            parts.append(re.escape(ch))
    return re.compile(''.join(parts), re.IGNORECASE | re.DOTALL)


def _and3(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _combine(op, left, right):
    if op == 'union all':
        return left + right
    if op == 'union':
        return _dedupe(left + right)
    other = set(right)
    if op == 'intersect':
        return _dedupe([r for r in left if r in other])
    return _dedupe([r for r in left if r not in other])  # except


def _sort_items(items, order_by):
    """items = [(valores, claves)]. Ordenación estable por pasadas, de la última clave a la primera."""
    for i in range(len(order_by) - 1, -1, -1):
        item = order_by[i]
        # Por defecto los nulos van primero en orden ascendente y al final en descendente.
        null_flag = 0 if item.nulls is None or (item.nulls == 'first') != item.desc else 2
        items.sort(key=lambda it: (null_flag,) if it[1][i] is None else (1, sort_key(it[1][i])),
                   reverse=item.desc)


MAX_PROFUNDIDAD = 16  # procedimientos llamándose unos a otros


class Executor:
    """Ejecuta una sentencia. Se crea uno nuevo por sentencia."""

    def __init__(self, db, params=(), time_limit=None):
        self.db = db
        self.params = params
        self.args = {}          # los «:nombre» del procedimiento que se está ejecutando
        self.profundidad = 0
        self._deadline = time.monotonic() + time_limit if time_limit else None
        self._steps = 0
        self.cte_stack = []
        self._subquery_cache = {}
        self._in_cache = {}
        self._outer_lookups = 0
        self._active_views = set()
        self._pending_delete = set()
        self._windows = {}
        self._ambiguas = set()

    def execute(self, stmt):
        method = getattr(self, '_exec_' + type(stmt).__name__, None)
        if method is None:
            raise SBDError(f"orden no soportada: {type(stmt).__name__}")
        return method(stmt)

    # =================================================================== DDL
    def _exec_CreateTable(self, stmt):
        return self.db.create_table(stmt, self)

    def _exec_DropTable(self, stmt):
        return self.db.drop_table(stmt)

    def _exec_CreateIndex(self, stmt):
        return self.db.create_index(stmt)

    def _exec_DropIndex(self, stmt):
        return self.db.drop_index(stmt)

    def _exec_CreateView(self, stmt):
        return self.db.create_view(stmt)

    def _exec_DropView(self, stmt):
        return self.db.drop_view(stmt)

    def _exec_AlterTable(self, stmt):
        return self.db.alter_table(stmt, self)

    # ============================================================ utilidades
    def _exec_CreateProcedure(self, stmt):
        return self.db.create_procedure(stmt)

    def _exec_DropProcedure(self, stmt):
        return self.db.drop_procedure(stmt)

    def _exec_ShowProcedures(self, stmt):
        return Result(['procedimiento', 'parámetros', 'definición'],
                      [(p.name, ', '.join(p.params), p.sql) for _, p in sorted(self.db.procedures.items())])

    def _exec_CallProcedure(self, stmt):
        """Las órdenes del cuerpo comparten el «deshacer» de esta orden: si una falla, se deshacen todas."""
        proc = self.db.procedures.get(stmt.name)
        if proc is None:
            raise SBDError(f"no existe ningún procedimiento «{stmt.name}» (escribe «procedimientos» para ver los que hay)")
        if len(stmt.args) != len(proc.params):
            raise SBDError(f"el procedimiento {stmt.name}({', '.join(proc.params)}) necesita "
                           f"{len(proc.params)} valor(es) y se le han pasado {len(stmt.args)}")
        if self.profundidad >= MAX_PROFUNDIDAD:
            raise SBDError(f"demasiados procedimientos llamándose unos a otros (el máximo es {MAX_PROFUNDIDAD}): "
                           f"¿{stmt.name} se llama a sí mismo?")
        env = Env(EMPTY_SCOPE, {})
        args = dict(zip(proc.params, [self.eval(a, env) for a in stmt.args]))
        ultima, afectadas, ultimo_id = None, 0, None
        for orden in proc.body:
            if self._deadline is not None and time.monotonic() > self._deadline:
                raise SBDError("la orden ha tardado demasiado y se ha cortado; no se ha guardado nada de ella")
            sub = Executor(self.db)
            sub.args, sub.profundidad, sub._deadline = args, self.profundidad + 1, self._deadline
            r = sub.execute(orden)
            if r.columns is not None:
                ultima = r
            if not isinstance(orden, Select):
                afectadas += r.rowcount
                ultimo_id = r.lastrowid if r.lastrowid is not None else ultimo_id
        if ultima is not None:
            return Result(ultima.columns, ultima.rows, lastrowid=ultimo_id)
        return Result(rowcount=afectadas, lastrowid=ultimo_id, message=(
            f"Procedimiento {stmt.name} ejecutado: {_plural(afectadas, 'afectada')}"))

    def _exec_ShowTables(self, stmt):
        rows = [(name, 'tabla', len(t.rows)) for name, t in sorted(self.db.tables.items())]
        rows += [(name, 'vista', None) for name in sorted(self.db.views)]
        return Result(['nombre', 'tipo', 'filas'], rows)

    def _exec_ShowIndexes(self, stmt):
        tables = [self.db.table(stmt.table)] if stmt.table else list(self.db.tables.values())
        rows = []
        for t in tables:
            for index in t.indexes.values():
                rows.append((index.name, t.name, ', '.join(index.columns),
                             'sí' if index.unique else 'no',
                             'restricción de la tabla' if index.auto else 'indexar'))
        return Result(['indice', 'tabla', 'columnas', 'unico', 'origen'], rows)

    def _exec_Explain(self, stmt):
        """Cómo se va a resolver la consulta, sin ejecutarla."""
        pasos = []
        for i, cte in enumerate(stmt.query.ctes):
            pasos.append(('con', f"{cte.name}: se calcula antes que la consulta principal"
                          + (" (se usa a sí misma: se repite hasta que no salen filas nuevas)"
                             if mentions_table(cte.query, cte.name) else '')))
        for n, core in enumerate(stmt.query.cores):
            if n:
                pasos.append(('unión', {'union': 'union: se juntan y se quitan las repetidas',
                                        'union all': 'union todo: se juntan tal cual',
                                        'intersect': 'comun: solo las que están en ambas',
                                        'except': 'excepto: las de la primera que no están en la segunda'}
                              [stmt.query.ops[n - 1]]))
            if core.from_ is None:
                pasos.append(('calcular', 'sin tabla: una sola fila'))
            else:
                self._explicar_origen(core.from_, core.where, pasos, superior=True)
            if core.where is not None:
                pasos.append(('donde', 'se comprueba la condición en cada fila candidata'))
            if core.group_by or any(has_aggregate(i.expr) for i in core.items if not isinstance(i.expr, Star)):
                pasos.append(('agrupar', f"se reúnen las filas en grupos ({len(core.group_by)} criterio(s))"
                              if core.group_by else 'se resume todo en una fila'))
            if core.having is not None:
                pasos.append(('teniendo', 'se descartan los grupos que no cumplen'))
            if any(isinstance(x, Window) for i in core.items for x in walk(i.expr)):
                pasos.append(('sobre', 'se miran las filas vecinas (numero, puesto, acumulado…)'))
            if core.distinct:
                pasos.append(('distintos', 'se quitan las filas repetidas'))
        if stmt.query.order_by:
            pasos.append(('ordenar', f"se ordena por {len(stmt.query.order_by)} criterio(s)"))
        if stmt.query.limit is not None or stmt.query.offset is not None:
            pasos.append(('limite', 'se cortan las filas que sobran al final'))
        return Result(['paso', 'cómo'], pasos)

    def _explicar_origen(self, item, where, pasos, superior=False):
        if isinstance(item, Join):
            if superior:
                self._ambiguas = self._columnas_repetidas(item)
            self._explicar_origen(item.left, where, pasos, superior)
            self._explicar_origen(item.right, where, pasos, superior)
            clase = {'inner': 'unir', 'left': 'unir opcional', 'right': 'unir inverso', 'full': 'unir completo',
                     'cross': 'cruzar'}[item.kind]
            cond = self._using_condition(item.using, *self._fuentes(item)) if item.using else item.on
            if cond is None:
                pasos.append((clase, 'cada fila con cada fila (producto de las dos)'))
                return
            fuentes = self._fuentes(item)
            scope = Scope(fuentes[0] + fuentes[1])
            plan = self._hash_plan(cond, scope, {a for a, _ in fuentes[0]}, {a for a, _ in fuentes[1]})
            pasos.append((clase, 'por igualdad, con una tabla hash: cada fila busca directamente su pareja'
                          if plan else 'comparando cada pareja de filas (la condición no es una igualdad simple)'))
            return
        if isinstance(item, SubqueryRef):
            pasos.append(('origen', f"subconsulta «{item.alias}»: se calcula primero"))
            return
        nombre = item.name
        if self._lookup_cte(nombre) is not None or (nombre not in self.db.tables and nombre in self.db.views):
            pasos.append(('origen', f"{nombre}: es una vista, se calcula su consulta"))
            return
        if nombre not in self.db.tables:
            pasos.append(('origen', f"{nombre}: se calcula antes (consulta auxiliar)"))
            return
        table = self.db.table(nombre)
        plan = self._index_plan(table, item.alias or nombre, where) if superior and where is not None else None
        total = len(table.rows)
        if plan is None:
            pasos.append(('origen', f"{nombre}: se recorren sus {total:,} filas".replace(',', '.')
                          + (' (no hay índice que sirva para esta condición)' if where is not None else '')))
        elif plan[0] is None:
            pasos.append(('origen', f"{nombre}: ninguna fila puede cumplirla (se compara con nulo)"))
        else:
            index, filas, clase = plan
            pasos.append(('origen', f"{nombre}: usa el índice {index.name} ({clase} en {', '.join(index.columns)}) "
                                    f"→ {len(filas):,} de {total:,} filas".replace(',', '.')))

    def _fuentes(self, item):
        """Las columnas de cada lado de un «unir», sin leer las filas."""
        def columnas(x):
            if isinstance(x, Join):
                return columnas(x.left) + columnas(x.right)
            if isinstance(x, SubqueryRef):
                return [(x.alias, [])]
            cte = self._lookup_cte(x.name)
            if cte is not None:
                return [(x.alias or x.name, cte[0])]
            if x.name in self.db.tables:
                return [(x.alias or x.name, self.db.tables[x.name].colnames)]
            return [(x.alias or x.name, [])]
        return columnas(item.left), columnas(item.right)

    def _exec_Describe(self, stmt):
        headers = ['columna', 'tipo', 'obligatorio', 'claves', 'por defecto']
        if stmt.name not in self.db.tables and stmt.name in self.db.views:
            names, _ = self._run_view(stmt.name)
            return Result(headers, [(n, None, 'no', '', None) for n in names])
        t = self.db.table(stmt.name)
        rows = []
        for col in t.columns:
            keys = []
            if col.name in t.primary_key:
                keys.append('clave')
            if [col.name] in t.uniques:
                keys.append('único')
            for fk in t.foreign_keys:
                if col.name in fk.columns:
                    keys.append(f"→ {fk.ref_table}({', '.join(fk.ref_columns)})")
            rows.append((col.name, col.type_sbd(), 'sí' if col.not_null else 'no',
                         ', '.join(keys), col.default_sql))
        return Result(headers, rows)

    # ========================================================== expresiones
    def eval(self, node, env):
        if self._deadline is not None:
            self._steps += 1
            if not self._steps & 2047 and time.monotonic() > self._deadline:
                raise SBDError("la orden ha tardado demasiado y se ha cortado (límite del servidor). "
                               "Prueba con «limite», un «donde» más preciso o un índice")
        try:
            method = _DISPATCH[type(node)]
        except KeyError:
            raise SBDError(f"expresión no válida: {type(node).__name__}") from None
        return method(self, node, env)

    def _eval_literal(self, node, env):
        return node.value

    def _eval_param(self, node, env):
        if node.index >= len(self.params):
            raise SBDError("faltan valores para los parámetros «?»")
        return self.params[node.index]

    def _eval_arg(self, node, env):
        return self.args[node.name]

    def _eval_column(self, node, env):
        if node.table is None and env.outputs is not None and node.name in env.outputs:
            return env.outputs[node.name]
        e, depth = env, 0
        while e is not None:
            alias = e.scope.resolve(node.table, node.name)
            if alias is not None:
                if depth:
                    self._outer_lookups += 1
                row = e.row.get(alias)
                return None if row is None else row[node.name]
            e, depth = e.outer, depth + 1
        if node.table is not None:
            raise SBDError(f"no hay ninguna tabla o alias «{node.table}» en esta consulta (en {node.table}.{node.name})")
        raise SBDError(f"la columna «{node.name}» no existe")

    def _eval_star(self, node, env):
        raise SBDError("«*» solo se puede usar en «mostrar *»")

    def _eval_default(self, node, env):
        raise SBDError("«defecto» solo se puede usar como valor al insertar o en «poner»")

    def _eval_unary(self, node, env):
        value = self.eval(node.operand, env)
        if node.op == 'not':
            t = truth(value)
            return None if t is None else not t
        if value is None:
            return None
        if isinstance(value, Intervalo):
            return -value if node.op == '-' else value
        number = to_number(value)
        return -number if node.op == '-' else number

    def _eval_binary(self, node, env):
        op = node.op
        if op == 'and':
            left = truth(self.eval(node.left, env))
            if left is False:
                return False
            return _and3(left, truth(self.eval(node.right, env)))
        if op == 'or':
            left = truth(self.eval(node.left, env))
            if left is True:
                return True
            right = truth(self.eval(node.right, env))
            if right is True:
                return True
            return None if left is None or right is None else False
        left = self.eval(node.left, env)
        right = self.eval(node.right, env)
        if op in ('is', 'is not'):
            same = (left is None and right is None) or (
                left is not None and right is not None and compare(left, right) == 0)
            return same if op == 'is' else not same
        if left is None or right is None:
            return None
        if op == '=':
            return compare(left, right) == 0
        if op == '!=':
            return compare(left, right) != 0
        if op == '<':
            return compare(left, right) < 0
        if op == '<=':
            return compare(left, right) <= 0
        if op == '>':
            return compare(left, right) > 0
        if op == '>=':
            return compare(left, right) >= 0
        if op == '||':
            return to_text(left) + to_text(right)
        return arithmetic(op, left, right)

    def _eval_is_null(self, node, env):
        return (self.eval(node.expr, env) is None) != node.negated

    @staticmethod
    def _in(value, candidates, negated):
        if value is None:
            return None
        saw_null = False
        for candidate in candidates:
            if candidate is None:
                saw_null = True
            elif compare(value, candidate) == 0:
                return not negated
        return None if saw_null else negated

    def _eval_in_list(self, node, env):
        value = self.eval(node.expr, env)
        return self._in(value, (self.eval(item, env) for item in node.items), node.negated)

    def _eval_in_select(self, node, env):
        value = self.eval(node.expr, env)
        names, rows = self._run_subquery(node.query, env)
        if len(names) != 1:
            raise SBDError("la consulta dentro de «en ( )» debe mostrar una sola columna")
        if not rows:
            return node.negated
        key = id(node.query)
        if key not in self._subquery_cache:
            return self._in(value, (r[0] for r in rows), node.negated)
        # Subconsulta no correlacionada: se busca en una tabla hash en vez de recorrerla.
        probe = self._in_cache.get(key)
        if probe is None:
            buckets, saw_null = {}, False
            for (candidate,) in rows:
                if candidate is None:
                    saw_null = True
                else:
                    buckets.setdefault(hash_key(candidate), []).append(candidate)
            probe = self._in_cache[key] = (buckets, saw_null)
        if value is None:
            return None
        buckets, saw_null = probe
        candidates = buckets.get(hash_key(value), ())
        if any(compare(value, c) == 0 for c in candidates):
            return not node.negated
        return None if saw_null else node.negated

    def _eval_between(self, node, env):
        value = self.eval(node.expr, env)
        low = self.eval(node.low, env)
        high = self.eval(node.high, env)
        ge = None if value is None or low is None else compare(value, low) >= 0
        le = None if value is None or high is None else compare(value, high) <= 0
        result = _and3(ge, le)
        if node.negated and result is not None:
            return not result
        return result

    def _eval_like(self, node, env):
        value = self.eval(node.expr, env)
        pattern = self.eval(node.pattern, env)
        if value is None or pattern is None:
            return None
        text, pattern = to_text(value).casefold(), to_text(pattern).casefold()
        if node.mode == 'contains':
            matched = pattern in text
        elif node.mode == 'starts':
            matched = text.startswith(pattern)
        elif node.mode == 'ends':
            matched = text.endswith(pattern)
        else:
            matched = _like_regex(pattern).fullmatch(text) is not None
        return matched != node.negated

    def _eval_case(self, node, env):
        if node.operand is not None:
            value = self.eval(node.operand, env)
            for cond, result in node.whens:
                candidate = self.eval(cond, env)
                if value is not None and candidate is not None and compare(value, candidate) == 0:
                    return self.eval(result, env)
        else:
            for cond, result in node.whens:
                if truth(self.eval(cond, env)) is True:
                    return self.eval(result, env)
        return None if node.else_ is None else self.eval(node.else_, env)

    def _eval_cast(self, node, env):
        return convert(self.eval(node.expr, env), node.type, node.length, 'conversión')

    def _eval_exists(self, node, env):
        _, rows = self._run_subquery(node.query, env)
        return bool(rows)

    def _eval_subquery(self, node, env):
        names, rows = self._run_subquery(node.query, env)
        if len(names) != 1:
            raise SBDError("una consulta usada como valor debe mostrar una sola columna")
        if len(rows) > 1:
            raise SBDError("una consulta usada como valor devolvió más de una fila")
        return rows[0][0] if rows else None

    def _clear_caches(self):
        self._subquery_cache.clear()
        self._in_cache.clear()

    def _run_subquery(self, query, env):
        # Si la subconsulta no ha leído ninguna columna de fuera, su resultado no
        # depende de la fila exterior y se reutiliza durante el resto de la sentencia.
        key = id(query)
        cached = self._subquery_cache.get(key)
        if cached is not None:
            return cached
        before = self._outer_lookups
        result = self.run_select(query, env)
        if self._outer_lookups == before:
            self._subquery_cache[key] = result
        return result

    def _eval_func(self, node, env):
        name = node.name
        if is_aggregate(node):
            if env.group is None:
                raise SBDError(f"{name}() resume un grupo de filas y no se puede usar aquí")
            return self._aggregate(node, env)
        if name in WINDOW_ONLY:
            raise SBDError(f"{name}() mira las filas vecinas y necesita «sobre»: "
                           f"{name}() sobre (por grupo ordenar columna)")
        if node.star:
            raise SBDError(f"{name}(*) no es válido; solo existe contar(*)")
        spec = FUNCTIONS.get(name)
        if spec is None:
            raise SBDError(f"función desconocida: {name}()")
        low, high, fn = spec
        if len(node.args) < low or (high is not None and len(node.args) > high):
            raise SBDError(f"número incorrecto de argumentos para {name}()")
        args = [self.eval(a, env) for a in node.args]
        try:
            return fn(*args)
        except (ValueError, ArithmeticError) as e:
            raise SBDError(f"error en {name}(): {e}") from None

    def _aggregate(self, node, env):
        name = node.name
        if node.star:
            if name != 'contar':
                raise SBDError(f"{name}(*) no es válido")
            return len(env.group)
        max_args = 2 if name == 'juntar' else 1
        if not 1 <= len(node.args) <= max_args:
            raise SBDError(f"número incorrecto de argumentos para {name}()")
        values = []
        for row_env in env.group:
            value = self.eval(node.args[0], row_env)
            if value is not None:
                values.append(value)
        if node.distinct:
            values = _dedupe(values)
        if name == 'contar':
            return len(values)
        if not values:
            return None
        if name == 'sumar':
            return sumar_numeros(values)
        if name == 'media':
            return sumar_numeros(values) / len(values)
        if name == 'minimo':
            return min(values, key=sort_key)
        if name == 'maximo':
            return max(values, key=sort_key)
        separator = self.eval(node.args[1], env) if len(node.args) > 1 else ','
        return to_text(separator).join(to_text(v) for v in values)

    # ================================================================ buscar
    def _exec_Select(self, stmt):
        names, rows = self.run_select(stmt, None)
        return Result(names, rows)

    def run_select(self, stmt, outer):
        """Devuelve (nombres de columna, lista de tuplas)."""
        if not stmt.ctes:
            return self._run_select_body(stmt, outer)
        frame = {}
        self.cte_stack.append(frame)
        try:
            for cte in stmt.ctes:
                frame[cte.name] = self._materialize_cte(cte, frame, outer)
            self._clear_caches()
            return self._run_select_body(stmt, outer)
        finally:
            self.cte_stack.pop()
            self._clear_caches()

    def _run_select_body(self, stmt, outer):
        if len(stmt.cores) == 1:
            names, items = self._run_core(stmt.cores[0], outer, stmt.order_by)
            if stmt.order_by:
                _sort_items(items, stmt.order_by)
            rows = [values for values, _ in items]
        else:
            names, items = self._run_core(stmt.cores[0], outer, None)
            rows = [values for values, _ in items]
            for op, core in zip(stmt.ops, stmt.cores[1:]):
                other_names, other = self._run_core(core, outer, None)
                if len(other_names) != len(names):
                    raise SBDError(f"{_OP_NAMES[op]}: las consultas muestran distinto número de columnas "
                                   f"({len(names)} y {len(other_names)})")
                rows = _combine(op, rows, [values for values, _ in other])
            if stmt.order_by:
                rows = self._sort_compound(rows, names, stmt.order_by, outer)
        offset = self._limit_value(stmt.offset, outer, 'saltar') or 0
        limit = self._limit_value(stmt.limit, outer, 'limite')
        if offset or limit is not None:
            rows = rows[offset:None if limit is None else offset + limit]
        return names, rows

    def _sort_compound(self, rows, names, order_by, outer):
        scope = Scope([('', names)])
        items = []
        for row in rows:
            env = Env(scope, {'': dict(zip(names, row))}, outer)
            items.append((row, [self._order_key(o, row, names, env) for o in order_by]))
        _sort_items(items, order_by)
        return [row for row, _ in items]

    def _order_key(self, item, values, names, env):
        expr = item.expr
        if isinstance(expr, Literal) and type(expr.value) is int:  # ordenar 2
            if not 1 <= expr.value <= len(values):
                raise SBDError(f"ordenar {expr.value}: solo se muestran {len(values)} columnas")
            return values[expr.value - 1]
        env.outputs = dict(zip(names, values))
        try:
            return self.eval(expr, env)
        finally:
            env.outputs = None

    def _limit_value(self, expr, outer, what):
        if expr is None:
            return None
        value = self.eval(expr, Env(EMPTY_SCOPE, {}, outer))
        if value is None:
            return None
        number = to_number(value)
        if number != int(number):
            raise SBDError(f"«{what}» necesita un número entero")
        number = int(number)
        if number < 0:
            if what == 'limite':
                return None
            raise SBDError("«saltar» no puede ser negativo")
        return number

    def _run_core(self, core, outer, order_by):
        if core.from_ is None:
            sources, rows = [], [{}]
        else:
            # En un «unir», cada tabla aprovecha sus índices con la parte de «donde» que habla de ella.
            self._ambiguas = self._columnas_repetidas(core.from_) if isinstance(core.from_, Join) else set()
            sources, rows = self._from(core.from_, outer, core.where)
        scope = Scope(sources)
        self._comprobar_columnas(core, scope, outer)
        envs = [Env(scope, row, outer) for row in rows]
        if core.where is not None:
            if has_aggregate(core.where):
                raise SBDError("contar(), sumar()… no se pueden usar en «donde»: para filtrar grupos usa «teniendo»")
            envs = [env for env in envs if truth(self.eval(core.where, env)) is True]

        exprs, names = self._expand_items(core.items, sources)
        order_by = order_by or []
        grouped = (bool(core.group_by) or core.having is not None
                   or any(has_aggregate(e) for e in exprs)
                   or any(has_aggregate(o.expr) for o in order_by))
        if grouped:
            contexts = []
            for first, members in self._group(envs, core.group_by, exprs, names, scope):
                genv = Env(scope, first, outer, members)
                if core.having is None or truth(self.eval(core.having, genv)) is True:
                    contexts.append(genv)
        else:
            contexts = envs
        windows = [n for e in exprs + [o.expr for o in order_by] for n in walk(e) if isinstance(n, Window)]
        if windows:
            self._compute_windows(windows, contexts, scope, outer)
        items = []
        for env in contexts:
            values = tuple(self.eval(e, env) for e in exprs)
            items.append((values, [self._order_key(o, values, names, env) for o in order_by]))

        if core.distinct:
            seen, unique = set(), []
            for item in items:
                if item[0] not in seen:
                    seen.add(item[0])
                    unique.append(item)
            items = unique
        return names, items

    # ------------------------------------------------ funciones con «sobre»
    def _compute_windows(self, windows, contexts, scope, outer):
        for window in windows:
            name = window.func.name
            if name not in WINDOW_ONLY and name not in AGGREGATES:
                raise SBDError(f"{name}() no se puede usar con «sobre»; valen numero, puesto, puesto_denso, "
                               "acumulado, anterior, siguiente y contar/sumar/media/minimo/maximo/juntar")
            groups = {}
            for env in contexts:
                key = tuple(self.eval(p, env) for p in window.partition)
                groups.setdefault(key, []).append(env)
            values = {}
            for members in groups.values():
                ordered = [(env, [self.eval(o.expr, env) for o in window.order]) for env in members]
                _sort_items(ordered, window.order)
                values.update(self._window_values(window, [env for env, _ in ordered],
                                                  [keys for _, keys in ordered], scope, outer))
            self._windows[id(window)] = values

    def _window_values(self, window, envs, keys, scope, outer):
        func = window.func
        name, args = func.name, func.args
        if name in AGGREGATES:
            total = self._aggregate(func, Env(scope, {}, outer, envs))
            return {id(env): total for env in envs}
        if name == 'numero':
            return {id(env): i + 1 for i, env in enumerate(envs)}
        if name in ('puesto', 'puesto_denso'):
            out, rank, dense, previous = {}, 0, 0, object()
            for i, (env, k) in enumerate(zip(envs, keys)):
                if k != previous:
                    rank, dense, previous = i + 1, dense + 1, k
                out[id(env)] = rank if name == 'puesto' else dense
            return out
        if name == 'acumulado':
            if len(args) != 1:
                raise SBDError("acumulado() necesita una columna: acumulado(total)")
            out, running = {}, None
            for env in envs:
                value = self.eval(args[0], env)
                if value is not None:
                    running = to_number(value) if running is None else sumar_numeros([running, value])
                out[id(env)] = running
            return out
        # anterior / siguiente
        if not 1 <= len(args) <= 3:
            raise SBDError(f"{name}(columna, [cuántas filas], [valor si no hay]) recibe de 1 a 3 valores")
        step = int(to_number(self.eval(args[1], envs[0]))) if len(args) > 1 else 1
        if name == 'anterior':
            step = -step
        out = {}
        for i, env in enumerate(envs):
            j = i + step
            if 0 <= j < len(envs):
                out[id(env)] = self.eval(args[0], envs[j])
            else:
                out[id(env)] = self.eval(args[2], env) if len(args) > 2 else None
        return out

    def _eval_window(self, node, env):
        values = self._windows.get(id(node))
        if values is None or id(env) not in values:
            raise SBDError(f"{node.func.name}(…) sobre (…) solo puede ir en «mostrar» u «ordenar»")
        return values[id(env)]

    def _comprobar_columnas(self, core, scope, outer):
        """Una columna que no existe o es ambigua da error siempre, aunque no haya filas que mirar."""
        expresiones = [i.expr for i in core.items if not isinstance(i.expr, Star)]
        if core.where is not None:
            expresiones.append(core.where)
        for expr in expresiones:
            for n in walk(expr):
                if isinstance(n, Column) and scope.resolve(n.table, n.name) is None and outer is None:
                    if n.table is not None:
                        raise SBDError(f"no hay ninguna tabla o alias «{n.table}» en esta consulta "
                                       f"(en {n.table}.{n.name})")
                    raise SBDError(f"la columna «{n.name}» no existe")

    def _expand_items(self, items, sources):
        exprs, names = [], []
        for item in items:
            if isinstance(item.expr, Star):
                if item.expr.table is not None:
                    chosen = [s for s in sources if s[0] == item.expr.table]
                    if not chosen:
                        raise SBDError(f"no hay ninguna tabla o alias «{item.expr.table}» en esta consulta")
                else:
                    chosen = sources
                    if not chosen:
                        raise SBDError("«*» necesita una tabla: usa buscar en vez de calcular")
                for alias, cols in chosen:
                    for col in cols:
                        exprs.append(Column(col, alias))
                        names.append(col)
            else:
                exprs.append(item.expr)
                if item.alias:
                    names.append(item.alias)
                elif isinstance(item.expr, Column):
                    names.append(item.expr.name)
                else:
                    names.append(item.text)
        return exprs, names

    def _group(self, envs, group_by, exprs, names, scope):
        if not group_by:
            return [(envs[0].row if envs else {}, envs)]
        keys = [self._resolve_group_expr(g, exprs, names, scope) for g in group_by]
        groups = {}
        for env in envs:
            key = tuple(self.eval(k, env) for k in keys)
            groups.setdefault(key, []).append(env)
        return [(members[0].row, members) for members in groups.values()]

    def _resolve_group_expr(self, expr, exprs, names, scope):
        """«agrupar» admite posiciones (agrupar 1) y los nombres puestos con «como»."""
        if isinstance(expr, Literal) and type(expr.value) is int:
            if not 1 <= expr.value <= len(exprs):
                raise SBDError(f"agrupar {expr.value}: solo se muestran {len(exprs)} columnas")
            expr = exprs[expr.value - 1]
        elif (isinstance(expr, Column) and expr.table is None
              and scope.resolve(None, expr.name) is None and expr.name in names):
            expr = exprs[names.index(expr.name)]
        if has_aggregate(expr):
            raise SBDError("no se puede agrupar por contar(), sumar()…")
        return expr

    # ------------------------------------------------------------ orígenes
    def _from(self, item, outer, where=None):
        if isinstance(item, TableRef):
            return self._from_table(item, outer, where)
        if isinstance(item, SubqueryRef):
            names, rows = self.run_select(item.query, outer)
            return [(item.alias, names)], [{item.alias: dict(zip(names, r))} for r in rows]
        return self._join(item, outer, where)

    def _columnas_repetidas(self, item):
        """Columnas que están en más de una tabla del «unir»: sin «tabla.» delante no se usan para índices."""
        vistas, repetidas = set(), set()
        for _, columnas in self._fuentes(item)[0] + self._fuentes(item)[1]:
            for c in columnas:
                (repetidas if c in vistas else vistas).add(c)
        return repetidas

    def _lookup_cte(self, name):
        for frame in reversed(self.cte_stack):
            if name in frame:
                return frame[name]
        return None

    def _run_view(self, name):
        if name in self._active_views:
            raise SBDError(f"la vista «{name}» se usa a sí misma")
        saved, self.cte_stack = self.cte_stack, []
        self._active_views.add(name)
        try:
            return self.run_select(self.db.views[name][1], None)
        finally:
            self._active_views.discard(name)
            self.cte_stack = saved

    def _from_table(self, ref, outer, where):
        alias = ref.alias or ref.name
        derived = self._lookup_cte(ref.name)
        if derived is None and ref.name not in self.db.tables and ref.name in self.db.views:
            derived = self._run_view(ref.name)
        if derived is not None:
            names, rows = derived
            return [(alias, names)], [{alias: dict(zip(names, r))} for r in rows]
        table = self.db.table(ref.name)
        rowids = self._index_candidates(table, alias, where) if where is not None else None
        if rowids is None:
            data = [row for _, row in table.scan()]
        else:
            data = [table.rows[rowid] for rowid in rowids]
        return [(alias, table.colnames)], [{alias: row} for row in data]

    def _index_candidates(self, table, alias, where):
        """Los rowids que pueden cumplir «donde» según un índice, o None si hay que recorrer la tabla.

        Es solo un filtro previo: la condición completa se evalúa igualmente sobre ellos.
        """
        plan = self._index_plan(table, alias, where)
        return None if plan is None else plan[1]

    def _index_value(self, table, alias, col, val):
        """El valor de «columna OP valor» listo para buscarlo en un índice, o _SIN_INDICE."""
        if not (isinstance(col, Column) and col.table in (None, alias)
                and _constante(val) and table.has_column(col.name)):
            return _SIN_INDICE
        if col.table is None and col.name in self._ambiguas:
            return _SIN_INDICE
        try:
            value = self.eval(val, Env(EMPTY_SCOPE, {}))
        except SBDError:
            return _SIN_INDICE  # que el error salga al evaluar la condición, como sin índice
        if value is None:
            return None
        column = table.column(col.name)
        if column.type is None or (column.type in ('TEXT', 'DATE', 'TIMESTAMP') and not isinstance(value, str)):
            return _SIN_INDICE  # la comparación mezclaría tipos: mejor recorrer la tabla
        try:
            return convert(value, column.type)
        except SBDError:
            return _SIN_INDICE

    def _index_plan(self, table, alias, where):
        """(índice, rowids, 'igualdad' | 'rango') o None."""
        if not table.indexes:
            return None
        equal, ranges = {}, {}
        for part in flatten_and(where):
            if isinstance(part, Between) and not part.negated:
                low = self._index_value(table, alias, part.expr, part.low)
                high = self._index_value(table, alias, part.expr, part.high)
                if low is None or high is None:
                    return None, [], 'igualdad'
                if low is not _SIN_INDICE and high is not _SIN_INDICE:
                    r = ranges.setdefault(part.expr.name, [None, True, None, True])
                    r[0], r[1], r[2], r[3] = low, True, high, True
                continue
            if not (isinstance(part, Binary) and part.op in ('=', '<', '<=', '>', '>=')):
                continue
            for col, val, op in ((part.left, part.right, part.op), (part.right, part.left, _GIRADO[part.op])):
                value = self._index_value(table, alias, col, val)
                if value is _SIN_INDICE:
                    continue
                if value is None:
                    return None, [], 'igualdad'  # comparar con nulo nunca es verdadero
                if op == '=':
                    equal[col.name] = value
                else:
                    r = ranges.setdefault(col.name, [None, True, None, True])
                    if op in ('>', '>='):
                        r[0], r[1] = value, op == '>='
                    else:
                        r[2], r[3] = value, op == '<='
                break
        best = None
        for index in table.indexes.values():
            if all(c in equal for c in index.columns):
                if best is None or (index.unique, len(index.columns)) > (best.unique, len(best.columns)):
                    best = index
        if best is not None:
            return best, best.find(tuple(equal[c] for c in best.columns)), 'igualdad'
        for index in table.indexes.values():
            if len(index.columns) == 1 and index.columns[0] in ranges:
                low, low_inclusive, high, high_inclusive = ranges[index.columns[0]]
                return index, index.find_range(low, low_inclusive, high, high_inclusive), 'rango'
        return None

    def _join(self, join, outer, where=None):
        # Las condiciones que usan los índices (=, <, >, entre) nunca se cumplen con nulos: descartar antes
        # las filas de cualquiera de los dos lados no cambia el resultado final de «donde».
        left_sources, left_rows = self._from(join.left, outer, where)
        right_sources, right_rows = self._from(join.right, outer, where)
        left_aliases = {a for a, _ in left_sources}
        right_aliases = {a for a, _ in right_sources}
        duplicated = left_aliases & right_aliases
        if duplicated:
            raise SBDError(f"«{duplicated.pop()}» aparece dos veces en la consulta; ponle otro nombre con «como»")
        sources = left_sources + right_sources
        scope = Scope(sources)
        cond = join.on
        if join.using:
            cond = self._using_condition(join.using, left_sources, right_sources)
        if cond is None:  # cruzar
            if len(left_rows) * len(right_rows) > MAX_CROSS_ROWS:
                raise SBDError(f"cruzar esas tablas daría {len(left_rows) * len(right_rows):,} filas; "
                               "añade una condición con «unir … en …»".replace(',', '.'))
            return sources, [{**l, **r} for l in left_rows for r in right_rows]

        left_null = dict.fromkeys(left_aliases)
        right_null = dict.fromkeys(right_aliases)
        plan = self._hash_plan(cond, scope, left_aliases, right_aliases)
        if plan is not None:
            # Hash join: se agrupan las filas de la derecha por la clave de la igualdad.
            left_key, right_key = plan
            buckets = {}
            for j, r in enumerate(right_rows):
                key = self.eval(right_key, Env(scope, {**left_null, **r}, outer))
                if key is not None:
                    buckets.setdefault(hash_key(key), []).append(j)

            def candidates(l):
                key = self.eval(left_key, Env(scope, {**l, **right_null}, outer))
                return () if key is None else buckets.get(hash_key(key), ())
        else:
            every = range(len(right_rows))

            def candidates(l):
                return every

        result, matched_right = [], set()
        for l in left_rows:
            matched = False
            for j in candidates(l):
                combined = {**l, **right_rows[j]}
                if truth(self.eval(cond, Env(scope, combined, outer))) is True:
                    result.append(combined)
                    matched = True
                    matched_right.add(j)
            if not matched and join.kind in ('left', 'full'):
                result.append({**l, **right_null})
        if join.kind in ('right', 'full'):
            for j, r in enumerate(right_rows):
                if j not in matched_right:
                    result.append({**left_null, **r})
        return sources, result

    def _hash_plan(self, cond, scope, left_aliases, right_aliases):
        for part in flatten_and(cond):
            if isinstance(part, Binary) and part.op == '=':
                a = self._aliases_used(part.left, scope)
                b = self._aliases_used(part.right, scope)
                if not a or not b:
                    continue
                if a <= left_aliases and b <= right_aliases:
                    return part.left, part.right
                if b <= left_aliases and a <= right_aliases:
                    return part.right, part.left
        return None

    def _aliases_used(self, expr, scope):
        used = set()
        for n in walk(expr):
            if isinstance(n, (Subquery, Exists, InSelect)) or is_aggregate(n):
                return None
            if isinstance(n, Column):
                alias = scope.resolve(n.table, n.name)
                if alias is None:
                    return None
                used.add(alias)
        return used

    def _using_condition(self, using, left_sources, right_sources):
        cond = None
        for name in using:
            lefts = [a for a, cols in left_sources if name in cols]
            rights = [a for a, cols in right_sources if name in cols]
            if len(lefts) != 1 or len(rights) != 1:
                raise SBDError(f"usando {name}: la columna debe existir exactamente una vez a cada lado")
            part = Binary('=', Column(name, lefts[0]), Column(name, rights[0]))
            cond = part if cond is None else Binary('and', cond, part)
        return cond

    # ------------------------------------------------------------------ WITH
    def _materialize_cte(self, cte, frame, outer):
        query = cte.query
        if not mentions_table(query, cte.name):  # recursiva si se usa a sí misma
            names, rows = self.run_select(query, outer)
            return self._cte_names(cte, names), rows
        if len(query.cores) != 2 or query.ops[0] not in ('union', 'union all'):
            raise SBDError(f"la consulta recursiva «{cte.name}» debe tener la forma: "
                           "consulta_inicial union [todo] consulta_que_la_usa")
        names, items = self._run_core(query.cores[0], outer, None)
        names = self._cte_names(cte, names)
        distinct = query.ops[0] == 'union'
        rows = [values for values, _ in items]
        if distinct:
            rows = _dedupe(rows)
        seen = set(rows)
        result, working, iterations = list(rows), rows, 0
        while working:
            iterations += 1
            if iterations > MAX_RECURSIVE_ITERATIONS or len(result) > MAX_RECURSIVE_ROWS:
                raise SBDError(f"la consulta recursiva «{cte.name}» no termina (demasiadas vueltas)")
            frame[cte.name] = (names, working)
            self._clear_caches()
            step_names, items = self._run_core(query.cores[1], outer, None)
            if len(step_names) != len(names):
                raise SBDError(f"la consulta recursiva «{cte.name}» muestra un número de columnas distinto en cada parte")
            new = [values for values, _ in items]
            if distinct:
                new = [r for r in _dedupe(new) if r not in seen]
                seen.update(new)
            result.extend(new)
            working = new
        self._clear_caches()
        return names, result

    @staticmethod
    def _cte_names(cte, names):
        if cte.columns is None:
            return names
        if len(cte.columns) != len(names):
            raise SBDError(f"«{cte.name}» declara {len(cte.columns)} columnas "
                           f"pero su consulta devuelve {len(names)}")
        return list(cte.columns)

    # ================================================================== DML
    def _exec_Insert(self, stmt):
        table = self.db.table(stmt.table)
        if stmt.columns is None:
            columns = table.colnames
        else:
            columns = list(stmt.columns)
            for c in columns:
                table.column(c)
            if len(set(columns)) != len(columns):
                raise SBDError("hay una columna repetida al insertar")
        if stmt.query is not None:
            _, value_rows = self.run_select(stmt.query, None)
        else:
            env = Env(EMPTY_SCOPE, {})
            value_rows = [[_DEFAULT if isinstance(e, DefaultValue) else self.eval(e, env) for e in row]
                          for row in stmt.values]
        if stmt.upsert and not any(i.unique for i in table.indexes.values()):
            raise SBDError(f"«insertar o actualizar» necesita saber si la fila ya existe: {table.name} "
                           "no tiene clave ni columnas únicas")
        count, updated, last_id, affected = 0, 0, None, []
        auto = table.auto_column
        for values in value_rows:
            if len(values) != len(columns):
                raise SBDError(f"la fila necesita {len(columns)} valores y tiene {len(values)}")
            provided = dict(zip(columns, values))
            row = self._build_row(table, provided)
            existing = table.conflicting_rowids(row) if stmt.upsert else set()
            if len(existing) > 1:
                raise SBDError(f"la fila coincide con {len(existing)} filas distintas de {table.name} "
                               "(por columnas únicas diferentes): no sé cuál actualizar")
            if existing:
                rowid = existing.pop()
                old = table.rows[rowid]
                new = dict(old)
                for name, value in provided.items():
                    if value is not _DEFAULT:
                        new[name] = row[name]
                self.check_row(table, new, old)
                self._check_referenced_update(table, rowid, old, new)
                self.db.update_row(table, rowid, new)
                updated += 1
                row = new
            else:
                self.check_row(table, row)
                rowid = self.db.insert_row(table, row)
                count += 1
            self._clear_caches()
            last_id = row[auto] if auto else rowid
            affected.append(row)
        message = _plural(count, 'insertada')
        if stmt.upsert:
            message = f"{_plural(count, 'insertada')} y {updated} actualizada{'s' if updated != 1 else ''}"
        return self._result(table, stmt.returning, affected, count + updated, message, last_id)

    def _result(self, table, returning, rows, rowcount, message, lastrowid=None):
        """Resultado de un cambio de datos; con «devolver», trae además las filas afectadas."""
        if not returning:
            return Result(rowcount=rowcount, message=message, lastrowid=lastrowid)
        sources = [(table.name, table.colnames)]
        exprs, names = self._expand_items(returning, sources)
        scope = Scope(sources)
        out = [tuple(self.eval(e, Env(scope, {table.name: row})) for e in exprs) for row in rows]
        return Result(names, out, rowcount=rowcount, message=message, lastrowid=lastrowid)

    def _build_row(self, table, provided):
        row = {}
        for col in table.columns:
            value = provided.get(col.name, _DEFAULT)
            if value is _DEFAULT:
                value = self.eval_default(table, col)
            row[col.name] = convert(value, col.type, col.length, f"columna '{table.name}.{col.name}'")
        auto = table.auto_column
        if auto is not None and row[auto] is None:
            row[auto] = table.autoinc + 1
        return row

    def eval_default(self, table, col):
        if col.default is None:
            return None
        value = self.eval(col.default, Env(EMPTY_SCOPE, {}))
        return convert(value, col.type, col.length, f"valor por defecto de {table.name}.{col.name}")

    def _exec_Update(self, stmt):
        table = self.db.table(stmt.table)
        names = [name for name, _ in stmt.assignments]
        for name in names:
            table.column(name)
        if len(set(names)) != len(names):
            raise SBDError("hay una columna repetida en «poner»")
        scope = Scope([(table.name, table.colnames)])
        changes = []
        # Primero se calculan todos los valores nuevos y después se aplican.
        for rowid, row in self._matching_rows(table, stmt.where):
            env = Env(scope, {table.name: row})
            new = dict(row)
            for name, expr in stmt.assignments:
                col = table.column(name)
                if isinstance(expr, DefaultValue):
                    value = self.eval_default(table, col)
                else:
                    value = self.eval(expr, env)
                new[name] = convert(value, col.type, col.length, f"columna '{table.name}.{name}'")
            changes.append((rowid, row, new))
        for rowid, old, new in changes:
            self.check_row(table, new, old)
            self._check_referenced_update(table, rowid, old, new)
            self.db.update_row(table, rowid, new)
            self._clear_caches()
        return self._result(table, stmt.returning, [new for _, _, new in changes], len(changes),
                            _plural(len(changes), 'actualizada'))

    def _exec_Delete(self, stmt):
        table = self.db.table(stmt.table)
        targets = self._matching_rows(table, stmt.where)
        self._pending_delete = {(table.name, rowid) for rowid, _ in targets}
        deleted = []
        for rowid, row in targets:
            if rowid in table.rows:
                self._delete_row(table, rowid)
                deleted.append(row)
        self._pending_delete = set()
        return self._result(table, stmt.returning, deleted, len(deleted), _plural(len(deleted), 'eliminada'))

    def _matching_rows(self, table, where):
        if where is None:
            return table.scan()
        rowids = self._index_candidates(table, table.name, where)
        rows = table.scan() if rowids is None else [(rowid, table.rows[rowid]) for rowid in rowids]
        scope = Scope([(table.name, table.colnames)])
        return [(rowid, row) for rowid, row in rows
                if truth(self.eval(where, Env(scope, {table.name: row}))) is True]

    # ----------------------------------------------------------- restricciones
    def check_row(self, table, row, old=None):
        """obligatorio, comprobar y referencias (unico lo comprueba el índice al guardar)."""
        for col in table.columns:
            if col.not_null and row[col.name] is None:
                raise SBDError(f"{table.name}.{col.name} es obligatorio: no puede quedar vacío (nulo)")
        if table.checks:
            env = Env(Scope([(table.name, table.colnames)]), {table.name: row})
            for expr, sql in table.checks:
                if truth(self.eval(expr, env)) is False:
                    raise SBDError(f"la fila no cumple «comprobar {sql}» de la tabla {table.name}")
        for fk in table.foreign_keys:
            values = tuple(row[c] for c in fk.columns)
            if any(v is None for v in values):
                continue
            if old is not None and tuple(old[c] for c in fk.columns) == values:
                continue
            if fk.ref_table == table.name and tuple(row[c] for c in fk.ref_columns) == values:
                continue  # la fila se referencia a sí misma
            parent = self.db.tables.get(fk.ref_table)
            if parent is None or not self._lookup(parent, fk.ref_columns, values):
                shown = ', '.join(repr(v) for v in values)
                raise SBDError(f"{table.name}.{', '.join(fk.columns)} apunta a {fk.ref_table}"
                               f"({', '.join(fk.ref_columns)}) = {shown}, y esa fila no existe")

    def _lookup(self, table, columns, values):
        """rowids de `table` cuyas `columns` son iguales a `values`."""
        wanted = {}
        for c, v in zip(columns, values):
            try:
                wanted[c] = convert(v, table.column(c).type)
            except SBDError:
                return []
        index = table.find_index(columns)
        if index is not None:
            return index.find(tuple(wanted[c] for c in index.columns))
        return [rowid for rowid, r in table.rows.items()
                if all(r[c] is not None and compare(r[c], v) == 0 for c, v in wanted.items())]

    def _referencing(self, table, row, rowid):
        """(tabla hija, clave foránea, rowids hijos) que apuntan a esta fila."""
        for child in list(self.db.tables.values()):
            for fk in child.foreign_keys:
                if fk.ref_table != table.name:
                    continue
                values = tuple(row[c] for c in fk.ref_columns)
                if any(v is None for v in values):
                    continue
                ids = [cid for cid in self._lookup(child, fk.columns, values)
                       if not (child is table and cid == rowid)]
                yield child, fk, ids

    def _delete_row(self, table, rowid):
        row = table.rows.get(rowid)
        if row is None:
            return
        refs = list(self._referencing(table, row, rowid))
        for child, fk, ids in refs:
            blocking = [cid for cid in ids if (child.name, cid) not in self._pending_delete]
            if blocking and fk.on_delete in ('restrict', 'no action'):
                raise SBDError(f"no se puede borrar la fila de {table.name}: hay filas de {child.name} que "
                               f"apuntan a ella ({child.name}.{', '.join(fk.columns)}). Bórralas antes o declara "
                               f"la referencia con «al borrar cascada»")
        self.db.delete_row(table, rowid)
        self._clear_caches()
        for child, fk, ids in refs:
            if fk.on_delete == 'cascade':
                for cid in ids:
                    self._delete_row(child, cid)
            elif fk.on_delete == 'set null':
                for cid in ids:
                    child_row = child.rows.get(cid)
                    if child_row is None:
                        continue
                    new = dict(child_row)
                    for c in fk.columns:
                        new[c] = None
                    self.check_row(child, new, child_row)
                    self.db.update_row(child, cid, new)

    def _check_referenced_update(self, table, rowid, old, new):
        for child in self.db.tables.values():
            for fk in child.foreign_keys:
                if fk.ref_table != table.name:
                    continue
                old_key = tuple(old[c] for c in fk.ref_columns)
                if old_key == tuple(new[c] for c in fk.ref_columns) or any(v is None for v in old_key):
                    continue
                ids = [cid for cid in self._lookup(child, fk.columns, old_key)
                       if not (child is table and cid == rowid)]
                if ids:
                    raise SBDError(f"no se puede cambiar {table.name}.{', '.join(fk.ref_columns)}: "
                                   f"hay filas de {child.name} que apuntan a este valor")


_DISPATCH = {
    Literal: Executor._eval_literal,
    Param: Executor._eval_param,
    ArgProc: Executor._eval_arg,
    Column: Executor._eval_column,
    Star: Executor._eval_star,
    DefaultValue: Executor._eval_default,
    Unary: Executor._eval_unary,
    Binary: Executor._eval_binary,
    IsNull: Executor._eval_is_null,
    InList: Executor._eval_in_list,
    InSelect: Executor._eval_in_select,
    Between: Executor._eval_between,
    Like: Executor._eval_like,
    FuncCall: Executor._eval_func,
    Case: Executor._eval_case,
    Cast: Executor._eval_cast,
    Exists: Executor._eval_exists,
    Subquery: Executor._eval_subquery,
    Window: Executor._eval_window,
}
