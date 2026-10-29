"""Una base de datos: catálogo de tablas, cambios de estructura, transacciones y disco.

Transacciones: cada cambio registra en un "undo log" la función que lo deshace.
«deshacer» (o un error en mitad de una orden) ejecuta esas funciones en orden inverso.
Sin «empezar», cada orden es su propia transacción y se guarda al terminar.

Persistencia, en dos ficheros:
  nombre.base    foto completa (JSON, con la estructura escrita en SBD). Se reescribe de
                 forma atómica: primero un temporal y luego un renombrado.
  nombre.diario  una línea por cada confirmación con solo las filas que cambiaron.
Al abrir se carga la foto y se repite el diario encima. Cuando el diario crece más que la
foto se compacta en una foto nueva. Cada foto tiene un número de generación: las líneas
de un diario viejo (si se cortó la luz justo al compactar) se ignoran, y una última línea
a medio escribir se descarta.
Los cambios de estructura (crear tablas, añadir columnas…) siempre guardan la foto entera.
"""
import json
import os
import time
from dataclasses import replace
from functools import lru_cache

from .cerrojo import Cerrojo
from .errors import BaseOcupada, SBDError
from .executor import Executor
from .lexer import tokenize
from .nodes import Column as ColumnNode
from .nodes import (Begin, Commit, CreateDatabase, DropDatabase, ForeignKey, Import, RecoverDatabase, Rollback,
                    ShowDatabases, ShowTrash, TableRef, UseDatabase, all_nodes, columns_in, mentions_table,
                    ChangePassword, ChangePermission, CopyDatabase, CreateApiKey, CreateUser, DropApiKey, DropUser,
                    RestoreBackup, ShowBackups, ShowHistory,
                    ShowApiKeys, ShowUsers)
from .parser import Parser
from .result import Result
from .storage import Column, Index, Table, quote_ident
from .values import convert, dinero, json_por_defecto, quote, type_name

FILE_FORMAT = 'sbd'
FILE_VERSION = 2
COMPACT_MIN_BYTES = 1_000_000


def connect(path=None):
    """Abre (o crea) un fichero de base de datos. Sin ruta, vive solo en memoria."""
    return Database(path)


def _tables_in(query):
    return {n.name for n in all_nodes(query) if isinstance(n, TableRef)}


def _replace_name(text, old, new):
    """Sustituye el nombre «old» en el texto (salvo justo después de «como», que es el nombre de salida)."""
    out, last, previous = [], 0, None
    for tok in tokenize(text):
        if tok.type in ('word', 'ident') and tok.value == old and not (
                previous is not None and previous.type == 'word' and previous.value == 'como'):
            out.append(text[last:tok.pos])
            out.append(quote_ident(new))
            last = tok.end
        previous = tok
    out.append(text[last:])
    return ''.join(out)


def _views_note(views):
    return f" · vistas actualizadas: {', '.join(views)}" if views else ''


@lru_cache(maxsize=512)
def _parse_cached(sql):
    """El árbol no se modifica al ejecutarlo, así que una orden repetida se analiza una sola vez."""
    parser = Parser(sql)
    return tuple(parser.parse_script()), parser.param_count


def _infer_type(values):
    kinds = {type_name(v) for v in values if v is not None}
    if kinds == {'entero', 'decimal'}:
        return 'REAL'
    if len(kinds) == 1:
        return {'entero': 'INTEGER', 'decimal': 'REAL', 'texto': 'TEXT', 'logico': 'BOOLEAN'}[kinds.pop()]
    return None


class Database:
    def __init__(self, path=None, quien='Python'):
        self.path = None if path in (None, '', ':memory:') else os.fspath(path)
        self.tables = {}       # nombre -> Table
        self.views = {}        # nombre -> (sql, Select)
        self.index_owner = {}  # nombre de índice -> nombre de tabla
        self.in_transaction = False
        self.loading = False
        self._undo = None      # [(función que deshace, cambio para el diario o None)]
        self._quota_marks = []  # posiciones de cambios que requieren revalidar la cuota al confirmar
        self.owner = None      # sesión dueña de la transacción abierta
        self.owner_label = None
        self.last_activity = 0.0
        self._expired = set()  # sesiones cuya transacción caducó (se les avisa una vez)
        self.generation = 0
        self._snapshot_bytes = 0
        self._journal_bytes = 0
        self._broken = False
        self.validate_write = None
        self._lock = None
        self.time_limit = None  # segundos que puede durar una orden (lo fija el servidor)
        if self.path:
            base = self.path[:-5] if self.path.endswith('.base') else self.path
            self.journal_path = base + '.diario'
            self._lock = Cerrojo(self.path + '.lock', f"La base {os.path.basename(self.path)}", quien)
            try:
                if os.path.exists(self.path):
                    self._load()
            except BaseException:
                self._lock.soltar()
                raise

    # ============================================================ API pública
    def execute(self, sql, params=()):
        """Ejecuta una o varias órdenes y devuelve el Result de la última."""
        result = Result(message='OK')
        for result in self.iterexecute(sql, params):
            pass
        return result

    def executescript(self, sql, params=()):
        return list(self.iterexecute(sql, params))

    def iterexecute(self, sql, params=()):
        params = tuple(params)
        statements, param_count = _parse_cached(sql)
        if param_count != len(params):
            raise SBDError(f"la orden tiene {param_count} parámetro(s) «?» "
                           f"pero se pasaron {len(params)} valor(es)")
        for stmt in statements:
            yield self.run_statement(stmt, params)

    def query(self, sql, params=()):
        """Atajo: devuelve las filas como lista de diccionarios."""
        return self.execute(sql, params).dicts()

    def expire_idle(self, max_idle):
        """Deshace la transacción abierta si su dueño lleva «max_idle» segundos sin usarla."""
        if self.in_transaction and time.monotonic() - self.last_activity > max_idle:
            self._undo_to(0)
            self.in_transaction = False
            self._undo = None
            self._expired.add(self.owner)
            self.owner = self.owner_label = None
            return True
        return False

    def end_session(self, session):
        """La sesión se va: si tenía una transacción abierta, se deshace."""
        if self.in_transaction and self.owner == session:
            self._undo_to(0)
            self.in_transaction = False
            self._undo = None
            self.owner = self.owner_label = None
            return True
        return False

    def close(self):
        if self.in_transaction:
            self._undo_to(0)
            self.in_transaction = False
            self._undo = None
            self.owner = self.owner_label = None
        if self._lock is not None:
            if self._journal_bytes and not self._broken:
                self._save()  # al cerrar se compacta: queda un único fichero
            self._lock.soltar()
            self._lock = None

    @property
    def bytes_on_disk(self):
        if not self.path:
            return 0
        return sum(os.path.getsize(p) for p in (self.path, self.journal_path) if os.path.exists(p))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def schema_sbd(self):
        """Las órdenes que reconstruyen la estructura: tablas, índices y vistas."""
        statements = [t.to_sbd() for t in self.tables.values()]
        for t in self.tables.values():
            statements += [index.to_sbd(t.name) for index in t.indexes.values() if not index.auto]
        statements += [f"crear vista {quote_ident(name)} = {sql}" for name, (sql, _) in self.views.items()]
        return statements

    def table(self, name):
        table = self.tables.get(name)
        if table is None:
            if name in self.views:
                raise SBDError(f"«{name}» es una vista, no una tabla")
            raise SBDError(f"no existe ninguna tabla «{name}» (escribe «tablas» para ver las que hay)")
        return table

    # ========================================================= transacciones
    def run_statement(self, stmt, params=(), session=None, label=None):
        """Ejecuta una orden ya analizada. «session» identifica a quién pertenece la transacción."""
        if self._broken:
            raise SBDError('el estado del diario es incierto: cierra y vuelve a abrir la base')
        if session in self._expired:
            self._expired.discard(session)
            raise SBDError("tu transacción llevaba demasiado tiempo sin usarse y se ha deshecho: "
                           "ningún cambio de ella se ha guardado. Vuelve a «empezar»")
        if self.in_transaction and self.owner != session:
            raise BaseOcupada(f"la base está ocupada: {self.owner_label or 'otra sesión'} tiene una "
                              "transacción abierta. Inténtalo en un momento")
        self.last_activity = time.monotonic()
        if isinstance(stmt, (CreateDatabase, DropDatabase, UseDatabase, ShowDatabases, ShowTrash, RecoverDatabase,
                             CreateUser, DropUser, ChangePassword, ChangePermission, ShowUsers,
                             CreateApiKey, DropApiKey, ShowApiKeys, CopyDatabase, ShowBackups, RestoreBackup,
                             ShowHistory)):
            raise SBDError("las órdenes de bases (crear base, usar, bases…) necesitan un servidor: "
                           "usa sbd.servidor('datos') o la consola, no un fichero suelto")
        if isinstance(stmt, Import):
            from .importar import importar_fichero
            return importar_fichero(self, stmt.path, stmt.table, session=session, label=label)
        if isinstance(stmt, Begin):
            if self.in_transaction:
                raise SBDError("ya hay una transacción abierta: termina con «confirmar» o «deshacer»")
            self.in_transaction = True
            self.owner, self.owner_label = session, label
            self._undo = []
            self._quota_marks = []
            return Result(message='Transacción abierta: los cambios no se guardan hasta «confirmar»')
        if isinstance(stmt, Commit):
            if not self.in_transaction:
                raise SBDError("no hay ninguna transacción en curso")
            try:
                if self.validate_write and self._undo:
                    self.validate_write(self)
                self._persist(self._undo)
            except BaseException:
                self._undo_to(0)
                self._undo = None
                self.in_transaction = False
                self.owner = self.owner_label = None
                raise
            self._undo = None
            self._quota_marks = []
            self.in_transaction = False
            self.owner = self.owner_label = None
            return Result(message='Cambios confirmados y guardados')
        if isinstance(stmt, Rollback):
            if not self.in_transaction:
                raise SBDError("no hay ninguna transacción en curso")
            self._undo_to(0)
            self.in_transaction = False
            self.owner = self.owner_label = None
            self._undo = None
            return Result(message='Cambios deshechos: todo vuelve a como estaba al «empezar»')

        autocommit = not self.in_transaction
        if autocommit:
            self._undo = []
        mark = len(self._undo)
        try:
            result = Executor(self, params, self.time_limit).execute(stmt)
            if self.validate_write and len(self._undo) > mark:
                self.validate_write(self)
                if not autocommit:
                    self._quota_marks.append(len(self._undo))
            if autocommit:
                self._persist(self._undo)
        except BaseException:
            # Una orden fallida no deja cambios a medias (la transacción sigue abierta).
            self._undo_to(mark)
            if autocommit:
                self._undo = None
            raise
        if autocommit:
            self._undo = None
        return result

    def _log(self, undo, redo=None):
        if self._undo is not None:
            self._undo.append((undo, redo))

    def _undo_to(self, mark):
        while len(self._undo) > mark:
            self._undo.pop()[0]()
        self._quota_marks = [pos for pos in self._quota_marks if pos <= mark]

    def _schema_checkpoint(self):
        tables, views, owners = dict(self.tables), dict(self.views), dict(self.index_owner)

        def undo():
            self.tables, self.views, self.index_owner = tables, views, owners
        self._log(undo, ('ddl',))

    # ======================================================= cambios en filas
    def insert_row(self, table, row):
        conflict = table.find_conflict(row)
        if conflict is not None:
            raise self._unique_error(table, conflict, row)
        rowid = table.next_rowid
        table.next_rowid += 1
        table.insert_raw(rowid, row)
        self._log(lambda: table.delete_raw(rowid), ('i', table.name, rowid, row))
        self._bump_autoinc(table, row)
        return rowid

    def update_row(self, table, rowid, row):
        conflict = table.find_conflict(row, ignore=rowid)
        if conflict is not None:
            raise self._unique_error(table, conflict, row)
        old = table.update_raw(rowid, row)
        self._log(lambda: table.update_raw(rowid, old), ('u', table.name, rowid, row))
        self._bump_autoinc(table, row)

    def delete_row(self, table, rowid):
        row = table.delete_raw(rowid)

        def undo():
            table.insert_raw(rowid, row)
            table.unordered = True
        self._log(undo, ('d', table.name, rowid))

    def _bump_autoinc(self, table, row):
        auto = table.auto_column
        if auto is None:
            return
        value = row[auto]
        if isinstance(value, int) and value > table.autoinc:
            previous = table.autoinc
            table.autoinc = value
            self._log(lambda: setattr(table, 'autoinc', previous))

    @staticmethod
    def _unique_error(table, index, row):
        what = 'la clave' if index.auto and index.columns == table.primary_key else 'un valor único'
        pairs = ', '.join(f"{c} = {quote(row[c])}" for c in index.columns)
        return SBDError(f"{table.name} ya tiene una fila con {pairs}, y es {what}: no se puede repetir")

    # ================================================================ tablas
    def create_table(self, stmt, ex):
        name = stmt.name
        if name in self.tables or name in self.views:
            if stmt.if_not_exists:
                return Result(message=f"La tabla {name} ya existía; no se ha tocado")
            raise SBDError(f"ya existe una tabla o vista llamada «{name}»")
        if stmt.query is not None:
            return self._create_table_as(stmt, ex)

        columns, pk, uniques, checks, fks, seen = [], [], [], [], [], set()
        for cd in stmt.columns:
            if cd.name in seen:
                raise SBDError(f"la columna «{cd.name}» está dos veces")
            seen.add(cd.name)
            columns.append(Column(cd.name, cd.type, cd.length, cd.not_null, cd.default, cd.default_sql))
            if cd.primary_key:
                if pk:
                    raise SBDError("una tabla solo puede tener una clave; si es de varias columnas usa «clave (a, b)»")
                pk = [cd.name]
            if cd.unique:
                uniques.append([cd.name])
            if cd.check is not None:
                checks.append((cd.check, cd.check_sql))
            if cd.references is not None:
                fks.append(cd.references)
        for tc in stmt.constraints:
            if tc.kind == 'primary':
                if pk:
                    raise SBDError("una tabla solo puede tener una clave; si es de varias columnas usa «clave (a, b)»")
                pk = list(tc.columns)
            elif tc.kind == 'unique':
                uniques.append(list(tc.columns))
            elif tc.kind == 'check':
                checks.append((tc.check, tc.check_sql))
            else:
                fks.append(tc.references)

        table = Table(name, columns, pk, uniques, checks)
        for c in pk:
            table.column(c).not_null = True
        for cols in uniques:
            for c in cols:
                table.column(c)
        for col in columns:
            ex.eval_default(table, col)  # comprueba que el valor por defecto es válido
        self._add_constraint_indexes(table)
        table.foreign_keys = [self._resolve_fk(table, fk) for fk in fks]

        self._schema_checkpoint()
        self.tables[name] = table
        for index_name in table.indexes:
            self.index_owner[index_name] = name
        return Result(message=f"Tabla {name} creada")

    def _create_table_as(self, stmt, ex):
        names, rows = ex.run_select(stmt.query, None)
        if len(set(names)) != len(names):
            raise SBDError("la consulta muestra dos columnas con el mismo nombre: renombra una con «como»")
        columns = [Column(name, _infer_type(r[i] for r in rows)) for i, name in enumerate(names)]
        table = Table(stmt.name, columns)
        self._schema_checkpoint()
        self.tables[table.name] = table
        for r in rows:
            self.insert_row(table, {c.name: convert(v, c.type) for c, v in zip(columns, r)})
        return Result(rowcount=len(rows), message=f"Tabla {table.name} creada con {len(rows)} filas")

    def _free_index_name(self, base, table):
        name, n = base, 2
        while name in self.index_owner or name in table.indexes:
            name, n = f"{base}{n}", n + 1
        return name

    def _add_constraint_indexes(self, table):
        if table.primary_key:
            name = self._free_index_name(f"{table.name}_pkey", table)
            table.indexes[name] = Index(name, table.primary_key, unique=True, auto=True)
        for cols in table.uniques:
            name = self._free_index_name(f"{table.name}_{'_'.join(cols)}_key", table)
            table.indexes[name] = Index(name, cols, unique=True, auto=True)

    def _resolve_fk(self, table, fk):
        for c in fk.columns:
            table.column(c)
        parent = table if fk.ref_table == table.name else self.tables.get(fk.ref_table)
        if parent is None:
            if self.loading and fk.ref_columns:
                return fk
            raise SBDError(f"la referencia apunta a «{fk.ref_table}», y esa tabla no existe")
        ref_columns = list(fk.ref_columns or parent.primary_key)
        if not ref_columns:
            raise SBDError(f"{parent.name} no tiene clave: indica a qué columna apuntas, -> {parent.name}(columna)")
        if len(ref_columns) != len(fk.columns):
            raise SBDError("la referencia une un número distinto de columnas a cada lado")
        for c in ref_columns:
            parent.column(c)
        if not any(i.unique and set(i.columns) == set(ref_columns) for i in parent.indexes.values()):
            raise SBDError(f"solo se puede apuntar a columnas que sean clave o únicas, "
                           f"y {parent.name}({', '.join(ref_columns)}) no lo es")
        return ForeignKey(list(fk.columns), fk.ref_table, ref_columns, fk.on_delete)

    def drop_table(self, stmt):
        name = stmt.name
        if name not in self.tables:
            if name in self.views:
                raise SBDError(f"«{name}» es una vista: usa «borrar vista {name}»")
            if stmt.if_exists:
                return Result(message=f"La tabla {name} no existía; nada que borrar")
            raise SBDError(f"no existe ninguna tabla «{name}»")
        for other in self.tables.values():
            if other.name != name and any(fk.ref_table == name for fk in other.foreign_keys):
                raise SBDError(f"no se puede borrar {name}: la tabla {other.name} "
                               "tiene una referencia (->) que apunta a ella")
        for view in self._views_using(name):
            raise SBDError(f"no se puede borrar {name}: la vista {view} la usa (bórrala antes: borrar vista {view})")
        self._schema_checkpoint()
        table = self.tables.pop(name)
        for index_name in table.indexes:
            self.index_owner.pop(index_name, None)
        return Result(message=f"Tabla {name} borrada")

    # =============================================================== índices
    def create_index(self, stmt):
        if stmt.name in self.index_owner:
            if stmt.if_not_exists:
                return Result(message=f"El índice {stmt.name} ya existía; no se ha tocado")
            raise SBDError(f"ya existe un índice llamado «{stmt.name}»")
        table = self.table(stmt.table)
        for c in stmt.columns:
            table.column(c)
        if len(set(stmt.columns)) != len(stmt.columns):
            raise SBDError("hay una columna repetida en el índice")
        index = Index(stmt.name, stmt.columns, stmt.unique)
        for rowid, row in table.rows.items():
            if stmt.unique and index.conflicts(row):
                pairs = ', '.join(f"{c} = {quote(row[c])}" for c in index.columns)
                raise SBDError(f"no se puede indexar como único: hay filas repetidas con {pairs}")
            index.add(rowid, row)
        new = table.clone()
        new.indexes[stmt.name] = index
        self._schema_checkpoint()
        self.tables[table.name] = new
        self.index_owner[stmt.name] = table.name
        return Result(message=f"Índice {stmt.name} creado en {table.name}")

    def drop_index(self, stmt):
        owner = self.index_owner.get(stmt.name)
        if owner is None:
            if stmt.if_exists:
                return Result(message=f"El índice {stmt.name} no existía; nada que borrar")
            raise SBDError(f"no existe ningún índice «{stmt.name}» (escribe «indices» para verlos)")
        table = self.tables[owner]
        if table.indexes[stmt.name].auto:
            raise SBDError(f"el índice {stmt.name} existe porque {owner} tiene una clave o columna única; "
                           "no se puede borrar por separado")
        new = table.clone()
        del new.indexes[stmt.name]
        self._schema_checkpoint()
        self.tables[owner] = new
        del self.index_owner[stmt.name]
        return Result(message=f"Índice {stmt.name} borrado")

    # ================================================================ vistas
    def create_view(self, stmt):
        if stmt.name in self.tables or stmt.name in self.views:
            if stmt.if_not_exists:
                return Result(message=f"La vista {stmt.name} ya existía; no se ha tocado")
            raise SBDError(f"ya existe una tabla o vista llamada «{stmt.name}»")
        self._schema_checkpoint()
        self.views[stmt.name] = (stmt.sql, stmt.query)
        return Result(message=f"Vista {stmt.name} creada")

    def drop_view(self, stmt):
        if stmt.name not in self.views:
            if stmt.if_exists:
                return Result(message=f"La vista {stmt.name} no existía; nada que borrar")
            raise SBDError(f"no existe ninguna vista «{stmt.name}»")
        self._schema_checkpoint()
        del self.views[stmt.name]
        return Result(message=f"Vista {stmt.name} borrada")

    # =========================================================== ALTER TABLE
    def alter_table(self, stmt, ex):
        table = self.table(stmt.table)
        handler = {
            'add': self._alter_add_column,
            'drop': self._alter_drop_column,
            'rename_table': self._alter_rename_table,
            'rename_column': self._alter_rename_column,
        }[stmt.action]
        return handler(table, stmt, ex)

    def _alter_add_column(self, table, stmt, ex):
        cd = stmt.column
        if table.has_column(cd.name):
            raise SBDError(f"{table.name} ya tiene una columna «{cd.name}»")
        if cd.primary_key or cd.unique:
            raise SBDError("una columna añadida no puede ser clave ni única directamente: "
                           f"añádela y después usa «indexar unico {table.name} ({cd.name})»")
        col = Column(cd.name, cd.type, cd.length, cd.not_null, cd.default, cd.default_sql)
        new = table.clone()
        new.columns.append(col)
        value = ex.eval_default(new, col)
        if col.not_null and value is None and table.rows:
            raise SBDError("la tabla ya tiene filas: una columna obligatoria nueva necesita un valor por defecto (= valor)")
        if cd.check is not None:
            new.checks.append((cd.check, cd.check_sql))
        if cd.references is not None:
            new.foreign_keys.append(self._resolve_fk(new, cd.references))
        new.rows = {rowid: {**row, col.name: value} for rowid, row in table.rows.items()}
        new.rebuild_indexes()
        if value is not None:
            for row in new.rows.values():
                ex.check_row(new, row)
        self._schema_checkpoint()
        self.tables[table.name] = new
        return Result(message=f"Columna {col.name} añadida a {table.name}")

    def _alter_drop_column(self, table, stmt, ex):
        name = stmt.name
        table.column(name)
        if len(table.columns) == 1:
            raise SBDError("no se puede quitar la única columna de la tabla; para quitar la tabla entera: borrar tabla")
        if name in table.primary_key:
            raise SBDError(f"«{name}» es la clave de {table.name} y no se puede quitar")
        for index in table.indexes.values():
            if name in index.columns:
                what = 'una restricción «unico»' if index.auto else f"el índice {index.name} (bórralo antes: borrar indice {index.name})"
                raise SBDError(f"no se puede quitar «{name}»: la usa {what}")
        if any(name in fk.columns for fk in table.foreign_keys):
            raise SBDError(f"no se puede quitar «{name}»: es una referencia (->) a otra tabla")
        for expr, sql in table.checks:
            if name in columns_in(expr):
                raise SBDError(f"no se puede quitar «{name}»: la usa «comprobar {sql}»")
        for view in self._views_using(table.name):
            if any(isinstance(n, ColumnNode) and n.name == name for n in all_nodes(self.views[view][1])):
                raise SBDError(f"no se puede quitar «{name}»: la vista {view} la usa (bórrala o cámbiala antes)")
        new = table.clone()
        new.columns = [c for c in table.columns if c.name != name]
        new.rows = {rowid: {k: v for k, v in row.items() if k != name} for rowid, row in table.rows.items()}
        new.rebuild_indexes()
        self._schema_checkpoint()
        self.tables[table.name] = new
        return Result(message=f"Columna {name} quitada de {table.name}")

    def _alter_rename_table(self, table, stmt, ex):
        old, new_name = table.name, stmt.new_name
        if new_name in self.tables or new_name in self.views:
            raise SBDError(f"ya existe una tabla o vista llamada «{new_name}»")

        def retarget(t):
            fks = [replace(fk, ref_table=new_name) if fk.ref_table == old else fk for fk in t.foreign_keys]
            if all(a is b for a, b in zip(fks, t.foreign_keys)):
                return t
            copy = t.clone()
            copy.foreign_keys = fks
            return copy

        renamed = retarget(table.clone())
        renamed.name = new_name
        self._schema_checkpoint()
        self.tables = {(new_name if k == old else k): (renamed if k == old else retarget(v))
                       for k, v in self.tables.items()}
        for index_name in renamed.indexes:
            self.index_owner[index_name] = new_name
        views = self._rewrite_views(self._views_using(old), old, new_name)
        return Result(message=f"Tabla {old} renombrada a {new_name}" + _views_note(views))

    def _alter_rename_column(self, table, stmt, ex):
        old, new_name = stmt.name, stmt.new_name
        table.column(old)
        if table.has_column(new_name):
            raise SBDError(f"{table.name} ya tiene una columna «{new_name}»")
        for expr, sql in table.checks:
            if old in columns_in(expr):
                raise SBDError(f"no se puede renombrar «{old}»: la usa «comprobar {sql}»")

        def ren(cols):
            return [new_name if c == old else c for c in cols]

        new = table.clone()
        new.columns = [replace(c, name=new_name) if c.name == old else c for c in table.columns]
        new.primary_key = ren(table.primary_key)
        new.uniques = [ren(u) for u in table.uniques]
        new.foreign_keys = [replace(fk, columns=ren(fk.columns),
                                    ref_columns=ren(fk.ref_columns) if fk.ref_table == table.name else fk.ref_columns)
                            for fk in table.foreign_keys]
        new.rows = {rowid: {(new_name if k == old else k): v for k, v in row.items()}
                    for rowid, row in table.rows.items()}
        new.indexes = {n: i.rebuilt(new.rows, ren(i.columns)) for n, i in table.indexes.items()}
        self._schema_checkpoint()
        self.tables[table.name] = new
        for key, other in list(self.tables.items()):
            if other is new:
                continue
            if any(fk.ref_table == table.name and old in fk.ref_columns for fk in other.foreign_keys):
                copy = other.clone()
                copy.foreign_keys = [replace(fk, ref_columns=ren(fk.ref_columns)) if fk.ref_table == table.name
                                     else fk for fk in other.foreign_keys]
                self.tables[key] = copy
        safe, unsure = [], []
        for view in self._views_using(table.name):
            others = _tables_in(self.views[view][1]) - {table.name}
            clash = any(o in self.tables and self.tables[o].has_column(old) for o in others)
            (unsure if clash else safe).append(view)
        views = self._rewrite_views(safe, old, new_name)
        note = _views_note(views)
        if unsure:
            note += f" · revisa a mano: {', '.join(unsure)} (otra tabla de la vista tiene una columna «{old}»)"
        return Result(message=f"Columna {table.name}.{old} renombrada a {new_name}" + note)

    def _views_using(self, table_name):
        return [name for name, (_, query) in self.views.items() if mentions_table(query, table_name)]

    def _rewrite_views(self, names, old, new):
        """Cambia «old» por «new» en el texto de esas vistas y las vuelve a analizar."""
        updated = []
        for name in names:
            sql, _ = self.views[name]
            new_sql = _replace_name(sql, old, new)
            if new_sql == sql:
                continue
            parser = Parser(new_sql)
            query = parser.parse_query()
            self.views[name] = (new_sql, query)
            updated.append(name)
        return updated

    # ========================================================== persistencia
    def _persist(self, entries):
        """Guarda lo confirmado: una línea en el diario o, si cambió la estructura, la foto entera."""
        if not self.path or not entries:
            return
        redo = [r for _, r in entries if r is not None]
        if not redo:
            return
        if any(r[0] == 'ddl' for r in redo) or not os.path.exists(self.path):
            self._save()
            return
        ops = []
        for r in redo:
            if r[0] == 'd':
                ops.append(['d', r[1], r[2]])
            else:
                ops.append([r[0], r[1], r[2], [r[3][c] for c in self.tables[r[1]].colnames]])
        line = (json.dumps({'g': self.generation, 'ops': ops}, ensure_ascii=False,
                           default=json_por_defecto) + '\n').encode('utf-8')
        previous = os.path.getsize(self.journal_path) if os.path.exists(self.journal_path) else 0
        try:
            with open(self.journal_path, 'ab') as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            try:
                with open(self.journal_path, 'a+b') as f:
                    f.truncate(previous)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                self._broken = True
                raise SBDError('no se pudo revertir el diario; la confirmación es incierta: '
                               'cierra y vuelve a abrir la base') from None
            raise
        self._journal_bytes += len(line)
        if self._journal_bytes > COMPACT_MIN_BYTES and self._journal_bytes > self._snapshot_bytes:
            try:
                self._save()
            except OSError:
                pass  # la confirmación ya está en el diario; se compactará después

    def _snapshot_data(self, generation=None):
        return {
            'format': FILE_FORMAT,
            'version': FILE_VERSION,
            'generacion': self.generation + 1 if generation is None else generation,
            'schema': self.schema_sbd(),
            'data': {
                name: {
                    'next_rowid': t.next_rowid,
                    'autoinc': t.autoinc,
                    'rows': [[rowid] + [row[c] for c in t.colnames] for rowid, row in t.scan()],
                }
                for name, t in self.tables.items()
            },
        }
    def _save(self):
        """Foto completa y diario vacío."""
        if not self.path:
            return
        data = self._snapshot_data()
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=json_por_defecto)
            f.flush()
            os.fsync(f.fileno())
        tamaño = os.path.getsize(tmp)
        os.replace(tmp, self.path)
        self.generation = data['generacion']
        self._snapshot_bytes = tamaño
        try:
            if os.path.exists(self.journal_path):
                os.remove(self.journal_path)
            self._journal_bytes = 0
        except OSError:
            # El diario antiguo se ignora por su generación y puede retirarse después.
            pass

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise SBDError(f"no se puede leer la base de datos {self.path}: {e}") from None
        if not isinstance(data, dict) or data.get('format') != FILE_FORMAT:
            raise SBDError(f"{self.path} no es una base de datos de SBD")
        self.generation = data.get('generacion', 0)
        self._snapshot_bytes = os.path.getsize(self.path)
        self.loading = True
        try:
            ex = Executor(self)
            for sql in data['schema']:
                for stmt in Parser(sql).parse_script():
                    ex.execute(stmt)
            for name, stored in data['data'].items():
                table = self.tables[name]
                names = table.colnames
                money = table.money_columns
                for rowid, *values in stored['rows']:
                    row = dict(zip(names, values))
                    for c in money:
                        if row[c] is not None:
                            row[c] = dinero(row[c])
                    table.insert_raw(rowid, row)
                table.next_rowid = stored['next_rowid']
                table.autoinc = stored['autoinc']
            self._replay_journal()
        finally:
            self.loading = False

    def _replay_journal(self):
        if not os.path.exists(self.journal_path):
            return
        good = 0
        with open(self.journal_path, 'rb') as f:
            content = f.read()
        for raw in content.splitlines(keepends=True):
            try:
                record = json.loads(raw)
            except ValueError:
                break  # última línea a medio escribir: se descarta
            if not raw.endswith(b'\n'):
                break
            good += len(raw)
            if record.get('g') != self.generation:
                continue  # diario de una foto anterior
            for op in record['ops']:
                table = self.tables[op[1]]
                rowid = op[2]
                if op[0] == 'd':
                    table.delete_raw(rowid)
                    continue
                row = dict(zip(table.colnames, op[3]))
                for c in table.money_columns:
                    if row[c] is not None:
                        row[c] = dinero(row[c])
                if op[0] == 'i':
                    table.insert_raw(rowid, row)
                    table.next_rowid = max(table.next_rowid, rowid + 1)
                else:
                    table.update_raw(rowid, row)
                auto = table.auto_column
                if auto and isinstance(row[auto], int):
                    table.autoinc = max(table.autoinc, row[auto])
        if good < len(content):
            with open(self.journal_path, 'r+b') as f:
                f.truncate(good)
        self._journal_bytes = good
