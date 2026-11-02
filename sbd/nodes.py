"""Nodos del árbol sintáctico que genera el parser de SBD."""
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Optional

node = dataclass(eq=False)


class Expr:
    """Clase base de todas las expresiones."""


# --------------------------------------------------------------- expresiones

@node
class Literal(Expr):
    value: Any


@node
class Param(Expr):
    index: int


@node
class Column(Expr):
    name: str
    table: Optional[str] = None


@node
class Star(Expr):
    table: Optional[str] = None


@node
class DefaultValue(Expr):
    """La palabra «defecto» como valor al insertar o en «poner»."""


@node
class Unary(Expr):
    op: str
    operand: Expr


@node
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@node
class IsNull(Expr):
    expr: Expr
    negated: bool = False


@node
class InList(Expr):
    expr: Expr
    items: list
    negated: bool = False


@node
class InSelect(Expr):
    expr: Expr
    query: Any
    negated: bool = False


@node
class Between(Expr):
    expr: Expr
    low: Expr
    high: Expr
    negated: bool = False


@node
class Like(Expr):
    expr: Expr
    pattern: Expr
    negated: bool = False
    mode: str = 'pattern'  # pattern (con % y _), contains, starts, ends


@node
class FuncCall(Expr):
    name: str
    args: list
    distinct: bool = False
    star: bool = False


@node
class Window(Expr):
    """f(…) sobre (por …  ordenar …): se calcula mirando las filas vecinas del resultado."""
    func: 'FuncCall'
    partition: list
    order: list  # [OrderItem]


@node
class Case(Expr):
    operand: Optional[Expr]
    whens: list  # [(condición, resultado)]
    else_: Optional[Expr] = None


@node
class Cast(Expr):
    expr: Expr
    type: str
    length: Optional[int] = None


@node
class Exists(Expr):
    query: Any


@node
class Subquery(Expr):
    query: Any


# ------------------------------------------------------------------ consultas

@node
class SelectItem:
    expr: Expr
    alias: Optional[str]
    text: str


@node
class TableRef:
    name: str
    alias: Optional[str] = None


@node
class SubqueryRef:
    query: Any
    alias: str


@node
class Join:
    left: Any
    right: Any
    kind: str  # inner, left, right, full, cross
    on: Optional[Expr] = None
    using: Optional[list] = None


@node
class OrderItem:
    expr: Expr
    desc: bool = False
    nulls: Optional[str] = None  # 'first' | 'last'


@node
class SelectCore:
    items: list
    from_: Any = None
    where: Optional[Expr] = None
    group_by: list = field(default_factory=list)
    having: Optional[Expr] = None
    distinct: bool = False


@node
class CTE:
    name: str
    columns: Optional[list]
    query: Any
    recursive: bool = False


@node
class Select:
    cores: list
    ops: list = field(default_factory=list)
    order_by: list = field(default_factory=list)
    limit: Optional[Expr] = None
    offset: Optional[Expr] = None
    ctes: list = field(default_factory=list)


# ----------------------------------------------------------------------- DDL

@node
class ForeignKey:
    columns: list
    ref_table: str
    ref_columns: Optional[list] = None
    on_delete: str = 'restrict'  # restrict, no action, cascade, set null


@node
class ColumnDef:
    name: str
    type: Optional[str] = None
    length: Optional[int] = None
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False
    default: Optional[Expr] = None
    default_sql: Optional[str] = None
    check: Optional[Expr] = None
    check_sql: Optional[str] = None
    references: Optional[ForeignKey] = None


@node
class TableConstraint:
    kind: str  # primary, unique, check, foreign
    columns: list = field(default_factory=list)
    check: Optional[Expr] = None
    check_sql: Optional[str] = None
    references: Optional[ForeignKey] = None


@node
class CreateTable:
    name: str
    columns: list
    constraints: list
    if_not_exists: bool = False
    query: Any = None


@node
class DropTable:
    name: str
    if_exists: bool = False


@node
class CreateIndex:
    name: str
    table: str
    columns: list
    unique: bool = False
    if_not_exists: bool = False


@node
class DropIndex:
    name: str
    if_exists: bool = False


@node
class ArgProc(Expr):
    """«:nombre»: un parámetro dentro del cuerpo de un procedimiento."""
    name: str


@node
class CreateProcedure:
    name: str
    params: list
    body: list
    sql: str  # el cuerpo tal como se escribió


@node
class DropProcedure:
    name: str
    if_exists: bool = False


@node
class CallProcedure:
    name: str
    args: list


@node
class ShowProcedures:
    pass


@node
class CreateView:
    name: str
    query: Any
    sql: str
    if_not_exists: bool = False


@node
class DropView:
    name: str
    if_exists: bool = False


@node
class AlterTable:
    table: str
    action: str  # add, drop, rename_table, rename_column
    column: Optional[ColumnDef] = None
    name: Optional[str] = None
    new_name: Optional[str] = None


# ----------------------------------------------------------------------- DML

@node
class Insert:
    table: str
    columns: Optional[list]
    values: Optional[list] = None
    query: Any = None
    upsert: bool = False          # insertar o actualizar
    returning: Optional[list] = None  # devolver …


@node
class Update:
    table: str
    assignments: list  # [(columna, expresión)]
    where: Optional[Expr] = None
    returning: Optional[list] = None


@node
class Delete:
    table: str
    where: Optional[Expr] = None
    returning: Optional[list] = None


# ------------------------------------------------------------ transacciones

@node
class Begin:
    pass


@node
class Commit:
    pass


@node
class Rollback:
    pass


# --------------------------------------------------------------- utilidades

@node
class ShowTables:
    pass


@node
class CreateDatabase:
    name: str
    if_not_exists: bool = False


@node
class DropDatabase:
    name: str
    if_exists: bool = False


@node
class UseDatabase:
    name: str


@node
class ShowDatabases:
    pass


@node
class ShowTrash:
    pass


@node
class CreateUser:
    name: str
    password: str
    permission: str = 'leer'   # leer, escribir, todo
    bases: list = field(default_factory=lambda: ['*'])


@node
class DropUser:
    name: str


@node
class ChangePassword:
    name: str
    password: str


@node
class ChangePermission:
    name: str
    permission: str
    bases: list = field(default_factory=lambda: ['*'])


@node
class ShowUsers:
    pass


@node
class CreateApiKey:
    user: Optional[str] = None
    label: Optional[str] = None


@node
class DropApiKey:
    key_id: str


@node
class ShowApiKeys:
    pass


@node
class CopyDatabase:
    name: str
    new_name: str


@node
class ShowBackups:
    pass


@node
class RestoreBackup:
    name: str
    day: str
    new_name: Optional[str] = None


@node
class ShowHistory:
    base: Optional[str] = None
    limit: int = 50


@node
class Explain:
    query: Any


@node
class Import:
    path: str
    table: str


@node
class RecoverDatabase:
    name: str
    new_name: Optional[str] = None


@node
class ShowIndexes:
    table: Optional[str] = None


@node
class Describe:
    name: str


# ------------------------------------------------------------ recorrer el AST

AGGREGATES = frozenset({'contar', 'sumar', 'media', 'minimo', 'maximo', 'juntar'})
WINDOW_ONLY = frozenset({'numero', 'puesto', 'puesto_denso', 'acumulado', 'anterior', 'siguiente'})


def is_aggregate(n):
    return isinstance(n, FuncCall) and n.name in AGGREGATES


def children(n):
    """Subexpresiones directas (no entra en subconsultas)."""
    if isinstance(n, Window):
        # La función de la ventana no cuenta como agregado del grupo; sus argumentos sí.
        yield from n.func.args
        yield from n.partition
        yield from (o.expr for o in n.order)
        return
    for f in fields(n):
        value = getattr(n, f.name)
        if isinstance(value, Expr):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Expr):
                    yield item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Expr):
                            yield sub


def walk(n):
    yield n
    for child in children(n):
        yield from walk(child)


def has_aggregate(expr):
    return any(is_aggregate(n) for n in walk(expr))


def flatten_and(expr):
    if isinstance(expr, Binary) and expr.op == 'and':
        return flatten_and(expr.left) + flatten_and(expr.right)
    return [expr]


def columns_in(expr):
    return {n.name for n in walk(expr) if isinstance(n, Column)}


def all_nodes(n):
    """Todos los nodos del árbol, también dentro de subconsultas."""
    if isinstance(n, (list, tuple)):
        for x in n:
            yield from all_nodes(x)
    elif is_dataclass(n):
        yield n
        for f in fields(n):
            yield from all_nodes(getattr(n, f.name))


def mentions_table(n, name):
    """True si en algún punto del árbol (incluidas subconsultas) se lee la tabla `name`."""
    if isinstance(n, TableRef):
        return n.name == name
    if isinstance(n, (list, tuple)):
        return any(mentions_table(x, name) for x in n)
    if is_dataclass(n):
        return any(mentions_table(getattr(n, f.name), name) for f in fields(n))
    return False
