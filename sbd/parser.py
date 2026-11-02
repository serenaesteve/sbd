"""Analizador sintáctico de SBD: texto -> árbol de órdenes.

Cada orden empieza por su palabra clave (buscar, insertar, crear…), así que no
hace falta terminarlas con nada; el punto y coma es opcional. Las cláusulas de
una consulta pueden ir en cualquier orden, en una línea o dentro de { }.
"""
from .errors import IncompleteInputError, SBDError
from .functions import ALIASES
from .lexer import tokenize
from .values import UNIDADES, intervalo
from .nodes import (CTE, AlterTable, ArgProc, Begin, Between, Binary, CallProcedure, Case, Cast, ChangePassword,
                    ChangePermission, Column, ColumnDef, Commit, CopyDatabase, CreateApiKey, CreateDatabase,
                    CreateIndex, CreateProcedure, CreateTable,
                    CreateUser, CreateView, DefaultValue, Delete, DropApiKey, DropProcedure, DropUser, ShowApiKeys,
                    ShowProcedures, ShowUsers,
                    Describe, DropDatabase, DropIndex, DropTable, DropView, Exists, Explain, ForeignKey, FuncCall,
                    Import, InList, Insert, InSelect, IsNull, Join, Like, Literal, OrderItem, Param,
                    RecoverDatabase, RestoreBackup, Rollback, Select, SelectCore, SelectItem, ShowBackups,
                    ShowHistory,
                    ShowDatabases,
                    ShowIndexes, ShowTables, ShowTrash, Star,
                    Subquery, SubqueryRef, TableConstraint, TableRef, Unary, Update, UseDatabase, Window)

# Palabras que no pueden ser nombres sin `comillas invertidas`: empiezan un valor o una consulta.
RESERVED = frozenset({'nulo', 'verdadero', 'falso', 'caso', 'existe', 'no', 'buscar', 'calcular'})

TYPES = {'entero': 'INTEGER', 'decimal': 'REAL', 'texto': 'TEXT', 'logico': 'BOOLEAN',
         'lógico': 'BOOLEAN', 'fecha': 'DATE', 'fechahora': 'TIMESTAMP', 'dinero': 'MONEY'}

STATEMENTS = frozenset({
    'buscar', 'calcular', 'con', 'insertar', 'actualizar', 'borrar', 'vaciar', 'crear', 'añadir',
    'anadir', 'quitar', 'renombrar', 'indexar', 'usar', 'bases', 'tablas', 'indices', 'índices',
    'describir', 'empezar', 'confirmar', 'deshacer', 'papelera', 'recuperar', 'importar', 'cambiar',
    'usuarios', 'claves', 'copiar', 'copias', 'restaurar', 'historial', 'explicar', 'ejecutar', 'procedimientos'})

CLAUSES = frozenset({'unir', 'cruzar', 'donde', 'agrupar', 'teniendo', 'mostrar', 'distintos',
                     'ordenar', 'limite', 'límite', 'saltar'})

# Todas las palabras del lenguaje: al escribir un nombre que coincide, se pone entre ``.
KEYWORDS = RESERVED | STATEMENTS | CLAUSES | frozenset({
    'y', 'o', 'es', 'en', 'entre', 'contiene', 'empieza', 'termina', 'parece', 'como', 'union',
    'comun', 'común', 'excepto', 'poner', 'todas', 'desde', 'cuando', 'entonces', 'sino', 'fin',
    'clave', 'obligatorio', 'unico', 'único', 'comprobar', 'usando', 'devolver', 'sobre', 'puede', 'usuario',
    'procedimiento'})

ON_DELETE = {'impedir': 'restrict', 'cascada': 'cascade', 'anular': 'set null'}
COMPARISON = frozenset({'=', '!=', '<', '<=', '>', '>='})

# Quien venga de SQL recibe la forma equivalente en vez de un error seco.
SQL_HINTS = {
    'select': 'buscar tabla donde … mostrar …',
    'insert': 'insertar en tabla { campo: valor }',
    'update': 'actualizar tabla poner campo = valor donde …',
    'delete': 'borrar de tabla donde …',
    'create': 'crear tabla nombre { columna tipo … }',
    'drop': 'borrar tabla nombre',
    'alter': 'añadir a tabla …  ·  quitar de tabla …  ·  renombrar …',
    'begin': 'empezar', 'commit': 'confirmar', 'rollback': 'deshacer',
    'show': 'tablas  ·  bases  ·  indices', 'describe': 'describir tabla', 'use': 'usar base',
    'with': 'con nombre = (buscar …) buscar nombre',
    'call': 'ejecutar nombre(valores)', 'exec': 'ejecutar nombre(valores)',
}

# Lo único que puede ir dentro de un procedimiento. Es una lista de lo permitido, no de lo prohibido:
# una orden nueva queda fuera hasta decidirlo. «importar», por ejemplo, leería ficheros del servidor
# sin pasar por los permisos de la sesión.
EN_PROCEDIMIENTO = (Select, Insert, Update, Delete, CallProcedure)


def parse_script(source):
    return Parser(source).parse_script()


class Parser:
    def __init__(self, source):
        self.source = source
        self.tokens = tokenize(source)
        self.pos = 0
        self.param_count = 0
        self.args_proc = None  # los parámetros del procedimiento que se está leyendo

    # ------------------------------------------------------------ utilidades
    def peek(self, offset=0):
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def advance(self):
        tok = self.tokens[self.pos]
        if tok.type != 'eof':
            self.pos += 1
        return tok

    def at(self, *words, offset=0):
        tok = self.peek(offset)
        return tok.type == 'word' and tok.value in words

    def at_op(self, *ops, offset=0):
        tok = self.peek(offset)
        return tok.type == 'op' and tok.value in ops

    def accept(self, *words):
        return self.advance().value if self.at(*words) else None

    def accept_op(self, op):
        if self.at_op(op):
            self.advance()
            return True
        return False

    def expect(self, *words):
        if not self.at(*words):
            self.error(f"se esperaba «{words[0]}»")
        return self.advance().value

    def expect_op(self, op):
        if not self.at_op(op):
            self.error(f"se esperaba «{op}»")
        return self.advance()

    def where(self, pos):
        line = self.source.count('\n', 0, pos) + 1
        column = pos - (self.source.rfind('\n', 0, pos) + 1) + 1
        return f"línea {line}, columna {column}"

    def error(self, message):
        tok = self.peek()
        if tok.type == 'eof':
            # Cortado a mitad: la consola usa esto para seguir pidiendo líneas.
            raise IncompleteInputError(f"la orden está incompleta: {message}")
        found = self.source[tok.pos:tok.end]
        raise SBDError(f"no entiendo «{found}» ({self.where(tok.pos)}): {message}")

    def is_ident(self, offset=0):
        tok = self.peek(offset)
        return tok.type == 'ident' or (tok.type == 'word' and tok.value not in RESERVED)

    def ident(self, what='un nombre'):
        if not self.is_ident():
            self.error(f"se esperaba {what}")
        return self.advance().value

    def ident_list(self):
        self.expect_op('(')
        names = [self.ident()]
        while self.accept_op(','):
            names.append(self.ident())
        self.expect_op(')')
        return names

    def text_since(self, start):
        return self.source[start.pos:self.tokens[self.pos - 1].end]

    def at_statement_end(self):
        tok = self.peek()
        return tok.type == 'eof' or self.at_op(';') or (tok.type == 'word' and tok.value in STATEMENTS)

    # -------------------------------------------------------------- órdenes
    def parse_script(self):
        return [stmt for stmt, _ in self.parse_script_spans()]

    def parse_script_spans(self):
        """[(orden, texto exacto de esa orden)] para todo el script."""
        statements = []
        while True:
            while self.accept_op(';'):
                pass
            if self.peek().type == 'eof':
                return statements
            start = self.peek()
            statements.append((self.parse_statement(), self.text_since(start)))
            if not self.at_statement_end():
                self.error("aquí debería acabar la orden (¿falta una coma o sobra una palabra?)")

    def parse_statement(self):
        tok = self.peek()
        if tok.type != 'word':
            self.error("se esperaba una orden: buscar, insertar, actualizar, crear…")
        word = tok.value
        handlers = {
            'buscar': self.parse_query, 'calcular': self.parse_query, 'con': self.parse_query,
            'insertar': self.parse_insert, 'actualizar': self.parse_update,
            'borrar': self.parse_borrar, 'vaciar': self.parse_vaciar, 'crear': self.parse_create,
            'añadir': self.parse_add_column, 'anadir': self.parse_add_column,
            'quitar': self.parse_drop_column, 'renombrar': self.parse_rename,
            'indexar': self.parse_index, 'usar': self.parse_use,
        }
        if word in handlers:
            return handlers[word]()
        simple = {'bases': ShowDatabases, 'tablas': ShowTables, 'empezar': Begin,
                  'confirmar': Commit, 'deshacer': Rollback, 'papelera': ShowTrash, 'usuarios': ShowUsers,
                  'copias': ShowBackups, 'procedimientos': ShowProcedures}
        if word in simple:
            self.advance()
            return simple[word]()
        if word in ('indices', 'índices'):
            self.advance()
            return ShowIndexes(self.ident('una tabla') if self.accept('de') else None)
        if word == 'ejecutar':
            self.advance()
            nombre = self.ident('el nombre del procedimiento')
            self.expect_op('(')
            args = [] if self.at_op(')') else self.parse_expr_list()
            self.expect_op(')')
            return CallProcedure(nombre, args)
        if word == 'explicar':
            self.advance()
            if not self.at('buscar', 'calcular', 'con'):
                self.error("«explicar» va delante de una consulta: explicar buscar …")
            return Explain(self.parse_query())
        if word == 'historial':
            self.advance()
            base = self.ident('el nombre de una base') if self.accept('de') else None
            limit = 50
            if self.accept('limite', 'límite'):
                tok = self.peek()
                if tok.type != 'num' or not isinstance(tok.value, int):
                    self.error("se esperaba un número entero")
                limit = self.advance().value
            return ShowHistory(base, limit)
        if word == 'copiar':
            self.advance()
            self.expect('base')
            name = self.ident('el nombre de la base')
            self.expect('a')
            return CopyDatabase(name, self.ident('el nombre de la copia'))
        if word == 'restaurar':
            self.advance()
            self.expect('base')
            name = self.ident('el nombre de la base')
            self.expect('del', 'de')
            day = self.peek()
            if day.type != 'date':
                self.error("se esperaba el día de la copia: restaurar base tienda del 2026-10-27")
            self.advance()
            return RestoreBackup(name, day.value[:10], self.ident('el nombre nuevo') if self.accept('como') else None)
        if word == 'claves':
            self.advance()
            self.expect('api')
            return ShowApiKeys()
        if word == 'cambiar':
            return self.parse_change_user()
        if word == 'importar':
            self.advance()
            ruta = self.peek()
            if ruta.type != 'str':
                self.error('se esperaba el fichero entre comillas: importar "clientes.csv" en clientes')
            self.advance()
            self.expect('en')
            return Import(ruta.value, self.ident('el nombre de la tabla'))
        if word == 'recuperar':
            self.advance()
            self.expect('base')
            name = self.ident('el nombre de la base borrada')
            return RecoverDatabase(name, self.ident('el nombre nuevo') if self.accept('como') else None)
        if word == 'describir':
            self.advance()
            return Describe(self.ident('el nombre de una tabla'))
        if word in SQL_HINTS:
            raise SBDError(f"«{self.source[tok.pos:tok.end]}» es SQL. En SBD se escribe: {SQL_HINTS[word]}")
        self.error("orden desconocida (prueba con buscar, insertar, crear… o escribe ayuda)")

    # ------------------------------------------------------------- consultas
    def parse_query(self):
        ctes = []
        if self.accept('con'):
            recursive = self.accept('recursivo', 'recursiva') is not None
            while True:
                name = self.ident('el nombre de la consulta auxiliar')
                columns = self.ident_list() if self.at_op('(') else None
                self.expect_op('=')
                self.expect_op('(')
                query = self.parse_query()
                self.expect_op(')')
                ctes.append(CTE(name, columns, query, recursive))
                if not self.accept_op(','):
                    break
            if not self.at('buscar', 'calcular'):
                self.error("después de «con …» va la consulta principal: buscar o calcular")
        parts = [self.parse_query_part()]
        ops = []
        while self.at('union', 'comun', 'común', 'excepto'):
            word = self.advance().value
            op = {'union': 'union', 'comun': 'intersect', 'común': 'intersect', 'excepto': 'except'}[word]
            if op == 'union' and self.accept('todo', 'todos'):
                op = 'union all'
            ops.append(op)
            parts.append(self.parse_query_part())
        if any(tail for _, tail in parts[:-1]):
            raise SBDError("en una unión, «ordenar», «limite» y «saltar» van solo al final "
                           "y se aplican al resultado completo")
        tail = parts[-1][1]
        return Select([core for core, _ in parts], ops, tail.get('ordenar', []),
                      tail.get('limite'), tail.get('saltar'), ctes)

    def parse_query_part(self):
        if self.accept('calcular'):
            core = SelectCore(self.parse_items())
            has_source = False
        else:
            self.expect('buscar')
            core = SelectCore([SelectItem(Star(), None, '*')], self.parse_source())
            has_source = True
        tail, seen = {}, set()
        braced = self.accept_op('{')
        while True:
            if braced and self.accept_op('}'):
                break
            tok = self.peek()
            word = tok.value if tok.type == 'word' else None
            if word not in CLAUSES:
                if braced:
                    self.error("se esperaba una cláusula (donde, mostrar, ordenar…) o «}»")
                break
            key = {'límite': 'limite'}.get(word, word)
            if key not in ('unir', 'cruzar') and key in seen:
                self.error(f"«{word}» aparece dos veces en la misma consulta")
            seen.add(key)
            self.advance()
            if key in ('unir', 'cruzar'):
                if not has_source:
                    self.error("«calcular» no tiene tabla: para unir tablas usa «buscar»")
                core.from_ = self.parse_join(core.from_, key)
            elif key == 'donde':
                core.where = self.parse_expr()
            elif key == 'agrupar':
                self.accept('por')
                core.group_by = self.parse_expr_list()
            elif key == 'teniendo':
                core.having = self.parse_expr()
            elif key == 'mostrar':
                if self.accept('distintos', 'distintas'):
                    core.distinct = True
                core.items = self.parse_items()
            elif key == 'distintos':
                core.distinct = True
            elif key == 'ordenar':
                self.accept('por')
                tail['ordenar'] = [self.parse_order_item()]
                while self.accept_op(','):
                    tail['ordenar'].append(self.parse_order_item())
            else:  # limite / saltar
                tail[key] = self.parse_expr()
        return core, tail

    def parse_source(self):
        if self.accept_op('('):
            query = self.parse_query()
            self.expect_op(')')
            self.expect('como')
            return SubqueryRef(query, self.ident('un nombre para la subconsulta'))
        name = self.ident('el nombre de una tabla')
        alias = self.ident('un alias') if self.accept('como') else None
        return TableRef(name, alias)

    def parse_join(self, left, word):
        if word == 'cruzar':
            return Join(left, self.parse_source(), 'cross')
        kind = {'opcional': 'left', 'completo': 'full', 'inverso': 'right'}.get(
            self.accept('opcional', 'completo', 'inverso'), 'inner')
        right = self.parse_source()
        if self.accept('en'):
            return Join(left, right, kind, on=self.parse_expr())
        if self.accept('usando'):
            using = self.ident_list() if self.at_op('(') else [self.ident('una columna')]
            return Join(left, right, kind, using=using)
        self.error("después de «unir tabla» va «en condición» o «usando columna»")

    def parse_items(self):
        items = [self.parse_item()]
        while self.accept_op(','):
            items.append(self.parse_item())
        return items

    def parse_item(self):
        if self.accept_op('*'):
            return SelectItem(Star(), None, '*')
        if self.is_ident() and self.at_op('.', offset=1) and self.at_op('*', offset=2):
            table = self.ident()
            self.advance()
            self.advance()
            return SelectItem(Star(table), None, f'{table}.*')
        start = self.peek()
        expr = self.parse_expr()
        text = self.text_since(start)
        alias = None
        if self.accept('como'):
            if self.peek().type == 'str':
                alias = self.advance().value
            else:
                alias = self.ident('un nombre para la columna')
        return SelectItem(expr, alias, text)

    def parse_order_item(self):
        expr = self.parse_expr()
        desc = self.accept('desc', 'descendente') is not None
        if not desc:
            self.accept('asc', 'ascendente')
        nulls = None
        if self.at('nulos') and self.at('primero', 'primeros', 'ultimo', 'último', 'ultimos', 'últimos', offset=1):
            self.advance()
            nulls = 'first' if self.advance().value.startswith('primero') else 'last'
        return OrderItem(expr, desc, nulls)

    # ----------------------------------------------------------- expresiones
    def parse_expr_list(self):
        items = [self.parse_expr()]
        while self.accept_op(','):
            items.append(self.parse_expr())
        return items

    def parse_expr(self):
        left = self.parse_and()
        while self.accept('o'):
            left = Binary('or', left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.accept('y'):
            left = Binary('and', left, self.parse_not())
        return left

    def parse_not(self):
        if self.accept('no'):
            return Unary('not', self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        while True:
            tok = self.peek()
            if tok.type == 'op' and tok.value in COMPARISON:
                self.advance()
                left = Binary(tok.value, left, self.parse_additive())
                continue
            negated = False
            if self.at('no') and self.at('es', 'en', 'entre', 'contiene', 'empieza', 'termina', 'parece', offset=1):
                self.advance()
                negated = True
            if self.accept('es'):
                negated = negated != (self.accept('no') is not None)
                if self.accept('nulo', 'nula'):
                    left = IsNull(left, negated)
                elif self.at('verdadero', 'falso'):
                    value = self.advance().value == 'verdadero'
                    left = Binary('is not' if negated else 'is', left, Literal(value))
                else:
                    self.error("después de «es» va nulo, verdadero o falso")
            elif self.accept('en'):
                if self.accept_op('['):
                    items = [] if self.at_op(']') else self.parse_expr_list()
                    self.expect_op(']')
                    left = InList(left, items, negated)
                elif self.accept_op('('):
                    if not self.at('buscar', 'calcular', 'con'):
                        self.error("dentro de «en ( )» va una consulta; para una lista usa corchetes: en [1, 2, 3]")
                    left = InSelect(left, self.parse_query(), negated)
                    self.expect_op(')')
                else:
                    self.error("después de «en» va una lista [a, b] o una consulta (buscar …)")
            elif self.accept('entre'):
                low = self.parse_additive()
                self.expect('y')
                left = Between(left, low, self.parse_additive(), negated)
            elif self.accept('contiene'):
                left = Like(left, self.parse_additive(), negated, 'contains')
            elif self.accept('empieza'):
                self.accept('por', 'con')
                left = Like(left, self.parse_additive(), negated, 'starts')
            elif self.accept('termina'):
                self.accept('en', 'con')
                left = Like(left, self.parse_additive(), negated, 'ends')
            elif self.accept('parece'):
                self.accept('a')
                left = Like(left, self.parse_additive(), negated, 'pattern')
            else:
                if negated:
                    self.error("se esperaba es, en, entre, contiene… después de «no»")
                return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at_op('+', '-', '&'):
            op = self.advance().value
            left = Binary('||' if op == '&' else op, left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at_op('*', '/', '%'):
            op = self.advance().value
            left = Binary(op, left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.at_op('-', '+'):
            op = self.advance().value
            operand = self.parse_unary()
            if (isinstance(operand, Literal) and isinstance(operand.value, (int, float))
                    and not isinstance(operand.value, bool)):
                return Literal(-operand.value if op == '-' else operand.value)
            return Unary(op, operand)
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        if tok.type == 'num' and self.at(*UNIDADES, offset=1) and not self.at_op('(', offset=2):
            self.advance()  # 3 dias, 2 semanas, 1 mes…
            return Literal(intervalo(tok.value, self.advance().value))
        if tok.type in ('num', 'str', 'date'):
            self.advance()
            return Literal(tok.value)
        if tok.type == 'param':
            self.advance()
            self.param_count += 1
            return Param(self.param_count - 1)
        if self.at_op(':'):
            self.advance()
            nombre = self.ident('el nombre de un parámetro')
            if self.args_proc is None:
                raise SBDError(f"«:{nombre}» solo vale dentro de un procedimiento; aquí usa «?» o el valor")
            if nombre not in self.args_proc:
                raise SBDError(f"el procedimiento no tiene ningún parámetro «{nombre}» "
                               f"(tiene: {', '.join(self.args_proc) or 'ninguno'})")
            return ArgProc(nombre)
        if self.accept_op('('):
            if self.at('buscar', 'calcular', 'con'):
                query = self.parse_query()
                self.expect_op(')')
                return Subquery(query)
            expr = self.parse_expr()
            self.expect_op(')')
            return expr
        if tok.type == 'word':
            word = tok.value
            if word in ('nulo', 'nula', 'verdadero', 'falso'):
                self.advance()
                return Literal({'verdadero': True, 'falso': False}.get(word))
            if word == 'caso':
                return self.parse_case()
            if word == 'existe':
                self.advance()
                self.expect_op('(')
                query = self.parse_query()
                self.expect_op(')')
                return Exists(query)
            if word in ('buscar', 'calcular'):
                self.error("una consulta dentro de otra va entre paréntesis: (buscar …)")
            if word == 'hace' and not self.at_op('(', offset=1):  # hace 30 dias
                self.advance()
                return FuncCall('hace', [self.parse_additive()])
            if word == 'dentro' and self.at('de', offset=1):      # dentro de 2 semanas
                self.advance()
                self.advance()
                return FuncCall('dentro_de', [self.parse_additive()])
            if word in TYPES and self.at_op('(', offset=1):  # entero("42"), texto(5)…
                self.advance()
                self.advance()
                expr = self.parse_expr()
                self.expect_op(')')
                return Cast(expr, TYPES[word])
        if self.is_ident():
            name = self.ident()
            if self.at_op('('):
                return self.parse_function(ALIASES.get(name, name))
            if self.accept_op('.'):
                return Column(self.ident('un nombre de columna'), name)
            return Column(name)
        self.error("se esperaba un valor: un número, un \"texto\", una columna…")

    def parse_function(self, name):
        self.expect_op('(')
        if self.accept_op('*'):
            self.expect_op(')')
            return FuncCall(name, [], star=True)
        distinct = self.accept('distintos', 'distintas', 'distinto') is not None
        args = [] if self.at_op(')') else self.parse_expr_list()
        self.expect_op(')')
        func = FuncCall(name, args, distinct, star=(name == 'contar' and not args))
        if not self.accept('sobre'):
            return func
        self.expect_op('(')
        partition, order = [], []
        while not self.accept_op(')'):
            if self.accept('por'):
                partition = self.parse_expr_list()
            elif self.accept('ordenar'):
                self.accept('por')
                order = [self.parse_order_item()]
                while self.accept_op(','):
                    order.append(self.parse_order_item())
            else:
                self.error("dentro de «sobre ( )» va «por columnas» y/o «ordenar columnas»")
        return Window(func, partition, order)

    def parse_case(self):
        self.expect('caso')
        operand = None if self.at('cuando') else self.parse_expr()
        whens = []
        while self.accept('cuando'):
            cond = self.parse_expr()
            self.expect('entonces')
            whens.append((cond, self.parse_expr()))
        if not whens:
            self.error("«caso» necesita al menos un «cuando … entonces …»")
        else_ = self.parse_expr() if self.accept('sino') else None
        self.expect('fin')
        return Case(operand, whens, else_)

    # ---------------------------------------------------------------- datos
    def parse_value(self):
        if self.accept('defecto'):
            return DefaultValue()
        return self.parse_expr()

    def looks_like_column_list(self):
        """(a, b, c) con solo nombres: lista de columnas. Si no, es una fila de valores."""
        i = 1
        while True:
            if not self.is_ident(i) or self.at_op('(', '.', offset=i + 1):
                return False
            if self.at_op(')', offset=i + 1):
                return True
            if not self.at_op(',', offset=i + 1):
                return False
            i += 2

    def parse_returning(self):
        return self.parse_items() if self.accept('devolver') else None

    def parse_insert(self):
        node = self._parse_insert_body()
        node.returning = self.parse_returning()
        return node

    def _parse_insert_body(self):
        self.expect('insertar')
        upsert = False
        if self.accept('o'):
            self.expect('actualizar')
            upsert = True
        self.expect('en')
        table = self.ident('el nombre de una tabla')
        node = self._parse_insert_data(table)
        node.upsert = upsert
        return node

    def _parse_insert_data(self, table):
        if self.at_op('{'):
            records = []
            while self.accept_op('{'):
                record = {}
                while not self.accept_op('}'):
                    name = self.ident('el nombre de un campo')
                    self.expect_op(':')
                    if name in record:
                        raise SBDError(f"el campo «{name}» aparece dos veces en la misma ficha")
                    record[name] = self.parse_value()
                    if not self.accept_op(','):
                        self.expect_op('}')
                        break
                records.append(record)
                self.accept_op(',')
            columns = []
            for record in records:
                columns += [c for c in record if c not in columns]
            rows = [[record.get(c, DefaultValue()) for c in columns] for record in records]
            return Insert(table, columns, rows)
        columns = None
        if self.at_op('(') and self.looks_like_column_list():
            columns = self.ident_list()
        if self.accept('desde'):
            return Insert(table, columns, None, self.parse_query())
        rows = []
        while self.accept_op('('):
            row = [self.parse_value()]
            while self.accept_op(','):
                row.append(self.parse_value())
            self.expect_op(')')
            rows.append(row)
            self.accept_op(',')
        if not rows:
            self.error("faltan los datos: { campo: valor }, (valor, valor…) o desde (buscar …)")
        return Insert(table, columns, rows)

    def parse_update(self):
        self.expect('actualizar')
        table = self.ident('el nombre de una tabla')
        assignments, where, everything, returning = None, None, False, None
        while True:
            if self.accept('poner'):
                assignments = []
                while True:
                    column = self.ident('una columna')
                    self.expect_op('=')
                    assignments.append((column, self.parse_value()))
                    if not self.accept_op(','):
                        break
            elif self.accept('donde'):
                where = self.parse_expr()
            elif self.accept('todas', 'todo', 'todos'):
                everything = True
            elif self.at('devolver'):
                returning = self.parse_returning()
            else:
                break
        if not assignments:
            self.error("falta «poner columna = valor»")
        if where is None and not everything:
            raise SBDError(f"¿todas las filas de «{table}»? Si es lo que quieres, añade «todas»: "
                           f"actualizar {table} poner … todas. Si no, indica cuáles con «donde …»")
        return Update(table, assignments, where, returning)

    def parse_borrar(self):
        self.expect('borrar')
        if self.accept('de'):
            table = self.ident('el nombre de una tabla')
            if not self.accept('donde'):
                raise SBDError(f"indica qué filas borrar con «donde …». Para borrarlas todas: vaciar {table}")
            where = self.parse_expr()
            return Delete(table, where, self.parse_returning())
        what = self.accept('tabla', 'vista', 'indice', 'índice', 'base', 'usuario', 'clave', 'procedimiento')
        if what is None:
            self.error("se esperaba «de tabla donde …», o tabla, vista, indice, base, usuario o procedimiento")
        if what == 'clave':
            self.expect('api')
            return DropApiKey(self.ident('el identificador de la clave'))
        if what == 'usuario':
            return DropUser(self.ident('el nombre del usuario'))
        name = self.ident()
        if_exists = self.parse_if_exists()
        node = {'tabla': DropTable, 'vista': DropView, 'indice': DropIndex, 'índice': DropIndex,
                'base': DropDatabase, 'procedimiento': DropProcedure}[what]
        return node(name, if_exists)

    def parse_vaciar(self):
        self.expect('vaciar')
        return Delete(self.ident('el nombre de una tabla'), None)

    # -------------------------------------------------------------- esquema
    def parse_if_not_exists(self):
        if self.at('si') and self.at('no', offset=1) and self.at('existe', offset=2):
            for _ in range(3):
                self.advance()
            return True
        return False

    def parse_if_exists(self):
        if self.at('si') and self.at('existe', offset=1):
            self.advance()
            self.advance()
            return True
        return False

    def parse_password(self):
        tok = self.peek()
        if tok.type != 'str':
            self.error('se esperaba la clave entre comillas: clave "…"')
        self.advance()
        return tok.value

    def parse_permission(self):
        self.expect('puede')
        permission = self.expect('leer', 'escribir', 'todo')
        bases = ['*']
        if self.accept('en'):
            if self.accept('todas'):
                return permission, bases
            bases = [self.ident('el nombre de una base')]
            while self.accept_op(','):
                bases.append(self.ident('el nombre de una base'))
        return permission, bases

    def parse_change_user(self):
        self.expect('cambiar')
        what = self.expect('clave', 'permiso')
        self.expect('de')
        name = self.ident('el nombre del usuario')
        self.expect('a')
        if what == 'clave':
            return ChangePassword(name, self.parse_password())
        permission = self.expect('leer', 'escribir', 'todo')
        bases = ['*']
        if self.accept('en'):
            bases = [self.ident('el nombre de una base')]
            while self.accept_op(','):
                bases.append(self.ident('el nombre de una base'))
        return ChangePermission(name, permission, bases)

    def parse_use(self):
        self.expect('usar')
        self.accept('base')
        return UseDatabase(self.ident('el nombre de una base de datos'))

    def parse_create(self):
        self.expect('crear')
        what = self.accept('tabla', 'vista', 'base', 'indice', 'índice', 'usuario', 'clave', 'procedimiento')
        if what == 'clave':
            self.expect('api')
            user = self.ident('el nombre del usuario') if self.accept('para') else None
            label = None
            if self.accept('llamada', 'llamado'):
                tok = self.peek()
                if tok.type != 'str':
                    self.error('se esperaba el nombre de la clave entre comillas: llamada "inventario"')
                label = self.advance().value
            return CreateApiKey(user, label)
        if what == 'usuario':
            name = self.ident('el nombre del usuario')
            self.expect('clave')
            password = self.parse_password()
            permission, bases = self.parse_permission() if self.at('puede') else ('leer', ['*'])
            return CreateUser(name, password, permission, bases)
        if what == 'tabla':
            return self.parse_create_table()
        if what == 'procedimiento':
            return self.parse_create_procedure()
        if what == 'vista':
            name = self.ident('el nombre de la vista')
            if_not_exists = self.parse_if_not_exists()
            self.expect_op('=')
            start = self.peek()
            query = self.parse_query()
            return CreateView(name, query, self.text_since(start), if_not_exists)
        if what == 'base':
            name = self.ident('el nombre de la base de datos')
            return CreateDatabase(name, self.parse_if_not_exists())
        if what in ('indice', 'índice'):
            raise SBDError("los índices se crean con «indexar tabla (columna)»")
        self.error("se esperaba tabla, vista o base")

    def parse_create_procedure(self):
        name = self.ident('el nombre del procedimiento')
        self.expect_op('(')
        params = [] if self.at_op(')') else [self.ident('el nombre de un parámetro')]
        while self.accept_op(','):
            params.append(self.ident('el nombre de un parámetro'))
        self.expect_op(')')
        if len(set(params)) != len(params):
            raise SBDError(f"el procedimiento «{name}» repite un parámetro")
        self.expect_op('{')
        fuera, self.args_proc = self.args_proc, params
        interrogantes = self.param_count
        cuerpo = []
        primero = self.peek()
        while not self.at_op('}'):
            if self.accept_op(';'):
                continue
            orden = self.parse_statement()
            if not isinstance(orden, EN_PROCEDIMIENTO):
                raise SBDError("dentro de un procedimiento solo van buscar, insertar, actualizar, borrar, vaciar "
                               "y ejecutar (ya es todo o nada: no hacen falta «empezar» ni «confirmar»)")
            cuerpo.append(orden)
            if not (self.at_op('}') or self.at_statement_end()):
                self.error("aquí debería acabar la orden (¿falta una coma o sobra una palabra?)")
        self.args_proc = fuera
        if not cuerpo:
            raise SBDError(f"el procedimiento «{name}» necesita al menos una orden")
        if self.param_count != interrogantes:
            raise SBDError("dentro de un procedimiento, los valores se escriben con «:nombre», no con «?»")
        sql = self.text_since(primero)
        self.expect_op('}')
        return CreateProcedure(name, params, cuerpo, sql)

    def parse_create_table(self):
        name = self.ident('el nombre de la tabla')
        if_not_exists = self.parse_if_not_exists()
        if self.accept_op('='):
            return CreateTable(name, [], [], if_not_exists, self.parse_query())
        self.expect_op('{')
        columns, constraints = [], []
        while not self.accept_op('}'):
            if self.accept_op(','):
                continue
            if self.at('clave') and self.at_op('(', offset=1):
                self.advance()
                constraints.append(TableConstraint('primary', self.ident_list()))
            elif self.at('unico', 'único') and self.at_op('(', offset=1):
                self.advance()
                constraints.append(TableConstraint('unique', self.ident_list()))
            elif self.at('comprobar') and not self.at(*TYPES, offset=1):
                self.advance()
                expr, sql = self.parse_check()
                constraints.append(TableConstraint('check', check=expr, check_sql=sql))
            elif self.at_op('('):
                cols = self.ident_list()
                self.expect_op('->')
                constraints.append(TableConstraint('foreign', cols, references=self.parse_reference(cols)))
            else:
                columns.append(self.parse_column_def())
        if not columns:
            raise SBDError(f"la tabla «{name}» necesita al menos una columna")
        return CreateTable(name, columns, constraints, if_not_exists)

    def parse_type(self):
        word = self.advance().value
        typ, length = TYPES[word], None
        if self.accept_op('('):
            size = self.peek()
            if size.type != 'num' or not isinstance(size.value, int):
                self.error("se esperaba un número entero (la longitud máxima)")
            self.advance()
            if self.accept_op(','):
                self.advance()
            self.expect_op(')')
            if typ == 'TEXT':
                length = size.value
        return typ, length

    def parse_column_def(self):
        inicio = self.peek()
        cd = ColumnDef(self.ident('el nombre de una columna'))
        if self.at(*TYPES):
            cd.type, cd.length = self.parse_type()
        while True:
            # Una palabra de las restricciones seguida de un tipo es otra columna («clave texto»),
            # y «clave (a, b)» / «unico (a, b)» es una restricción de la tabla. Pero en
            # «id entero clave  fecha fecha» la columna se llama como el tipo: si leerlo como otra
            # columna dejaría la siguiente sin tipo, la palabra es una restricción de esta.
            if self.at('clave', 'obligatorio', 'obligatoria', 'unico', 'único', 'unica', 'única', 'comprobar') \
                    and self.at(*TYPES, offset=1) \
                    and not (self.at(*TYPES, offset=2) and not self.at(*TYPES, offset=3)):
                return cd
            if self.at('clave', 'unico', 'único') and self.at_op('(', offset=1):
                return cd
            if self.accept('clave'):
                cd.primary_key = True
            elif self.accept('obligatorio', 'obligatoria'):
                cd.not_null = True
            elif self.accept('unico', 'único', 'unica', 'única'):
                cd.unique = True
            elif self.accept_op('='):
                start = self.peek()
                cd.default = self.parse_additive()
                cd.default_sql = self.text_since(start)
            elif self.at('comprobar'):
                # Es de la tabla (no de esta columna) si empieza en otra línea o si la columna ya tiene
                # su «comprobar»: así «comprobar a > 0» y «comprobar b > 0» seguidos no se pisan.
                if cd.check is not None or '\n' in self.source[inicio.pos:self.peek().pos]:
                    return cd
                self.advance()
                cd.check, cd.check_sql = self.parse_check()
            elif self.accept_op('->'):
                cd.references = self.parse_reference([cd.name])
            else:
                return cd

    def parse_check(self):
        start = self.peek()
        expr = self.parse_expr()
        return expr, self.text_since(start)

    def parse_reference(self, columns):
        table = self.ident('la tabla a la que apunta')
        ref_columns = self.ident_list() if self.at_op('(') else None
        on_delete = 'restrict'
        if self.at('al') and self.at('borrar', offset=1):
            self.advance()
            self.advance()
            action = self.accept(*ON_DELETE)
            if action is None:
                self.error("después de «al borrar» va impedir, cascada o anular")
            on_delete = ON_DELETE[action]
        return ForeignKey(list(columns), table, ref_columns, on_delete)

    def parse_index(self):
        self.expect('indexar')
        unique = self.accept('unico', 'único') is not None
        table = self.ident('el nombre de una tabla')
        columns = self.ident_list()
        name = self.ident('el nombre del índice') if self.accept('como') else f"{table}_{'_'.join(columns)}_idx"
        return CreateIndex(name, table, columns, unique)

    def parse_add_column(self):
        self.advance()
        self.expect('a')
        table = self.ident('el nombre de una tabla')
        return AlterTable(table, 'add', column=self.parse_column_def())

    def parse_drop_column(self):
        self.expect('quitar')
        self.expect('de')
        table = self.ident('el nombre de una tabla')
        return AlterTable(table, 'drop', name=self.ident('la columna que quieres quitar'))

    def parse_rename(self):
        self.expect('renombrar')
        what = self.expect('tabla', 'columna')
        if what == 'tabla':
            old = self.ident('el nombre de la tabla')
            self.expect('a')
            return AlterTable(old, 'rename_table', new_name=self.ident('el nombre nuevo'))
        table = self.ident('la tabla')
        self.expect_op('.')
        column = self.ident('la columna')
        self.expect('a')
        return AlterTable(table, 'rename_column', name=column, new_name=self.ident('el nombre nuevo'))
