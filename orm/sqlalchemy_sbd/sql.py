"""Traduce SQL al árbol de órdenes de SBD, sin pasar por el lenguaje de SBD.

Cubre lo que genera SQLAlchemy y lo habitual al escribir SQL a mano: SELECT con JOIN, WHERE,
GROUP BY, HAVING, ORDER BY, LIMIT, subconsultas, WITH, UNION y funciones de ventana; INSERT,
UPDATE y DELETE; CREATE y DROP de tablas e índices; BEGIN, COMMIT y ROLLBACK.
Lo que no cubre da un error que lo dice, nunca un resultado distinto.
"""
import re
from functools import lru_cache

from sbd.nodes import (CTE, Begin, Between, Binary, Case, Cast, Column, ColumnDef, Commit, CreateIndex, CreateTable,
                       DefaultValue, Delete, DropIndex, DropTable, Exists, ForeignKey, FuncCall, InList, Insert, InSelect,
                       IsNull, Join, Like, Literal, OrderItem, Param, Rollback, Select, SelectCore, SelectItem, Star,
                       Subquery, SubqueryRef, TableConstraint, TableRef, Unary, Update, Window)
from sbd.storage import quote_ident
from sbd.values import quote


class ErrorSQL(Exception):
    """El SQL está mal escrito o usa algo que este traductor no cubre."""


_TOKEN = re.compile(r"""
    (?P<espacio>\s+|--[^\n]*|/\*.*?\*/)
  | (?P<texto>'(?:[^']|'')*')
  | (?P<nombre>"(?:[^"]|"")*"|`(?:[^`]|``)*`)
  | (?P<numero>\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?)
  | (?P<palabra>[^\W\d]\w*)
  | (?P<signo><=|>=|<>|!=|\|\||[=<>+\-*/%(),.;?])
""", re.VERBOSE | re.DOTALL)

# Funciones de SQL y su nombre en SBD. Las que no están aquí pasan tal cual: así valen también las de SBD.
_FUNCIONES = {
    'count': 'contar', 'sum': 'sumar', 'avg': 'media', 'min': 'minimo', 'max': 'maximo',
    'lower': 'minusculas', 'upper': 'mayusculas', 'length': 'longitud', 'char_length': 'longitud',
    'trim': 'recortar', 'replace': 'reemplazar', 'substr': 'extraer', 'substring': 'extraer',
    'abs': 'absoluto', 'round': 'redondear', 'ceil': 'techo', 'ceiling': 'techo', 'floor': 'suelo',
    'sqrt': 'raiz', 'power': 'potencia', 'mod': 'resto', 'sign': 'signo', 'random': 'aleatorio',
    'coalesce': 'primero_no_nulo', 'ifnull': 'si_nulo', 'nullif': 'nulo_si', 'greatest': 'mayor', 'least': 'menor',
    'now': 'ahora', 'current_timestamp': 'ahora', 'current_date': 'hoy', 'concat': 'unir_texto',
    'row_number': 'numero', 'rank': 'puesto', 'dense_rank': 'puesto_denso', 'lag': 'anterior', 'lead': 'siguiente',
}

# Palabras que nunca son un alias: si vienen detrás de una tabla o una columna, empieza otra cosa.
_NO_ALIAS = frozenset("""from where group having order limit offset union intersect except join inner left right full
    cross on using as set values and or not returning""".split())

# Sin comillas no pueden ser una columna: así «SELECT FROM» es un error y no una columna llamada from.
_RESERVADAS = _NO_ALIAS | frozenset("select in is like between when then else end".split())

_TIPOS = {
    'integer': 'INTEGER', 'int': 'INTEGER', 'bigint': 'INTEGER', 'smallint': 'INTEGER',
    'varchar': 'TEXT', 'char': 'TEXT', 'text': 'TEXT', 'clob': 'TEXT',
    'boolean': 'BOOLEAN', 'bool': 'BOOLEAN', 'date': 'DATE', 'datetime': 'TIMESTAMP', 'timestamp': 'TIMESTAMP',
    'float': 'REAL', 'real': 'REAL', 'double': 'REAL',
}


class _Token:
    __slots__ = ('tipo', 'valor', 'inicio', 'fin')

    def __init__(self, tipo, valor, inicio, fin):
        self.tipo, self.valor, self.inicio, self.fin = tipo, valor, inicio, fin


def _trocear(sql):
    tokens, pos = [], 0
    while pos < len(sql):
        m = _TOKEN.match(sql, pos)
        if m is None:
            raise ErrorSQL(f"no entiendo «{sql[pos:pos + 12]}» (posición {pos})")
        pos = m.end()
        tipo, texto = m.lastgroup, m.group()
        if tipo == 'espacio':
            continue
        if tipo == 'texto':
            valor = texto[1:-1].replace("''", "'")
        elif tipo == 'nombre':
            valor = texto[1:-1].replace(texto[0] * 2, texto[0])
        elif tipo == 'numero':
            valor = int(texto) if texto.isdigit() else float(texto)
        elif tipo == 'palabra':
            valor = texto.lower()
        else:
            valor = texto
        tokens.append(_Token(tipo, valor, m.start(), m.end()))
    tokens.append(_Token('fin', None, len(sql), len(sql)))
    return tokens


class _Traductor:
    def __init__(self, sql):
        self.sql = sql
        self.tokens = _trocear(sql)
        self.pos = 0
        self.parametros = 0

    # ------------------------------------------------------------ utilidades
    @property
    def tok(self):
        return self.tokens[self.pos]

    def mirar(self, salto=1):
        return self.tokens[min(self.pos + salto, len(self.tokens) - 1)]

    def avanzar(self):
        tok = self.tok
        self.pos += 1
        return tok

    def en(self, *palabras):
        return self.tok.tipo == 'palabra' and self.tok.valor in palabras

    def acepta(self, *palabras):
        if self.en(*palabras):
            return self.avanzar().valor
        return None

    def espera(self, palabra):
        if not self.acepta(palabra):
            self.error(f"se esperaba {palabra.upper()}")

    def en_signo(self, *signos):
        return self.tok.tipo == 'signo' and self.tok.valor in signos

    def acepta_signo(self, *signos):
        if self.en_signo(*signos):
            return self.avanzar().valor
        return None

    def espera_signo(self, signo):
        if not self.acepta_signo(signo):
            self.error(f"se esperaba «{signo}»")

    def error(self, mensaje):
        tok = self.tok
        visto = 'el final' if tok.tipo == 'fin' else f"«{self.sql[tok.inicio:tok.fin]}»"
        raise ErrorSQL(f"{mensaje}, y aquí hay {visto} (posición {tok.inicio}): {self.sql.strip()[:120]}")

    def nombre(self, que='un nombre'):
        if self.tok.tipo in ('palabra', 'nombre'):
            return self.avanzar().valor
        self.error(f"se esperaba {que}")

    def lista_nombres(self):
        self.espera_signo('(')
        nombres = [self.nombre()]
        while self.acepta_signo(','):
            nombres.append(self.nombre())
        self.espera_signo(')')
        return nombres

    def alias(self):
        if self.acepta('as'):
            return self.nombre('un alias')
        if self.tok.tipo == 'nombre' or (self.tok.tipo == 'palabra' and self.tok.valor not in _NO_ALIAS):
            return self.avanzar().valor
        return None

    # --------------------------------------------------------------- órdenes
    def orden(self):
        palabra = self.tok.valor if self.tok.tipo == 'palabra' else None
        if palabra in ('select', 'with'):
            nodo = self.consulta()
        elif palabra == 'insert':
            nodo = self.insert()
        elif palabra == 'update':
            nodo = self.update()
        elif palabra == 'delete':
            nodo = self.delete()
        elif palabra == 'create':
            nodo = self.create()
        elif palabra == 'drop':
            nodo = self.drop()
        elif palabra in ('begin', 'start'):
            self.avanzar()
            self.acepta('transaction')
            nodo = Begin()
        elif palabra == 'commit':
            self.avanzar()
            nodo = Commit()
        elif palabra == 'rollback':
            self.avanzar()
            nodo = Rollback()
        else:
            self.error("esta orden de SQL no está soportada")
        self.acepta_signo(';')
        if self.tok.tipo != 'fin':
            self.error("solo se admite una orden cada vez, y aquí debería acabar")
        return nodo

    # -------------------------------------------------------------- consultas
    def consulta(self):
        ctes = []
        if self.acepta('with'):
            recursiva = self.acepta('recursive') is not None
            while True:
                nombre = self.nombre('el nombre de la consulta auxiliar')
                columnas = self.lista_nombres() if self.en_signo('(') else None
                self.espera('as')
                self.espera_signo('(')
                ctes.append(CTE(nombre, columnas, self.consulta(), recursiva))
                self.espera_signo(')')
                if not self.acepta_signo(','):
                    break
        cores, ops = [self.select_core()], []
        while self.en('union', 'intersect', 'except'):
            op = self.avanzar().valor
            if op == 'union' and self.acepta('all'):
                op = 'union all'
            ops.append(op)
            cores.append(self.select_core())
        orden = []
        if self.acepta('order'):
            self.espera('by')
            orden = [self.orden_item()]
            while self.acepta_signo(','):
                orden.append(self.orden_item())
        limite = saltar = None
        if self.acepta('limit'):
            limite = self.expresion()
        if self.acepta('offset'):
            saltar = self.expresion()
        return Select(cores, ops, orden, limite, saltar, ctes)

    def select_core(self):
        self.espera('select')
        distinto = self.acepta('distinct') is not None
        self.acepta('all')
        items = [self.select_item()]
        while self.acepta_signo(','):
            items.append(self.select_item())
        core = SelectCore(items, distinct=distinto)
        if self.acepta('from'):
            core.from_ = self.origen()
            while self.acepta_signo(','):
                core.from_ = Join(core.from_, self.origen(), 'cross')
        if self.acepta('where'):
            core.where = self.expresion()
        if self.acepta('group'):
            self.espera('by')
            core.group_by = [self.expresion()]
            while self.acepta_signo(','):
                core.group_by.append(self.expresion())
        if self.acepta('having'):
            core.having = self.expresion()
        return core

    def select_item(self):
        inicio = self.tok.inicio
        if self.acepta_signo('*'):
            return SelectItem(Star(), None, '*')
        if (self.tok.tipo in ('palabra', 'nombre') and self.mirar().valor == '.' and self.mirar().tipo == 'signo'
                and self.mirar(2).valor == '*' and self.mirar(2).tipo == 'signo'):
            tabla = self.avanzar().valor
            self.avanzar()
            self.avanzar()
            return SelectItem(Star(tabla), None, f'{tabla}.*')
        expr = self.expresion()
        fin = self.tokens[self.pos - 1].fin
        return SelectItem(expr, self.alias(), self.sql[inicio:fin])

    def origen(self):
        izquierda = self.tabla_o_subconsulta()
        while True:
            if self.acepta('cross'):
                self.espera('join')
                izquierda = Join(izquierda, self.tabla_o_subconsulta(), 'cross')
                continue
            tipo = 'inner'
            if self.acepta('inner'):
                pass
            elif self.en('left', 'right', 'full'):
                tipo = self.avanzar().valor
                self.acepta('outer')
            elif not self.en('join'):
                return izquierda
            self.espera('join')
            derecha = self.tabla_o_subconsulta()
            if self.acepta('on'):
                izquierda = Join(izquierda, derecha, tipo, on=self.expresion())
            elif self.acepta('using'):
                izquierda = Join(izquierda, derecha, tipo, using=self.lista_nombres())
            else:
                self.error("un JOIN necesita ON o USING")

    def tabla_o_subconsulta(self):
        if self.acepta_signo('('):
            if self.en('select', 'with'):
                consulta = self.consulta()
                self.espera_signo(')')
                alias = self.alias()
                if alias is None:
                    self.error("una subconsulta en FROM necesita un alias")
                return SubqueryRef(consulta, alias)
            dentro = self.origen()
            self.espera_signo(')')
            return dentro
        return TableRef(self.nombre('el nombre de una tabla'), self.alias())

    def orden_item(self):
        expr = self.expresion()
        desc = False
        if self.acepta('desc'):
            desc = True
        else:
            self.acepta('asc')
        nulos = None
        if self.acepta('nulls'):
            nulos = self.acepta('first', 'last')
            if nulos is None:
                self.error("después de NULLS va FIRST o LAST")
        return OrderItem(expr, desc, nulos)

    # ------------------------------------------------------------ expresiones
    def expresion(self):
        izquierda = self.expr_y()
        while self.acepta('or'):
            izquierda = Binary('or', izquierda, self.expr_y())
        return izquierda

    def expr_y(self):
        izquierda = self.expr_no()
        while self.acepta('and'):
            izquierda = Binary('and', izquierda, self.expr_no())
        return izquierda

    def expr_no(self):
        if self.acepta('not'):
            return Unary('not', self.expr_no())
        return self.comparacion()

    def comparacion(self):
        izquierda = self.suma()
        while True:
            if self.en_signo('=', '!=', '<>', '<', '<=', '>', '>='):
                op = self.avanzar().valor
                izquierda = Binary('!=' if op == '<>' else op, izquierda, self.suma())
                continue
            if self.acepta('is'):
                negado = self.acepta('not') is not None
                if self.acepta('null'):
                    izquierda = IsNull(izquierda, negado)
                elif self.en('true', 'false'):
                    izquierda = Binary('is not' if negado else 'is', izquierda, Literal(self.avanzar().valor == 'true'))
                else:
                    self.error("después de IS va NULL, TRUE o FALSE")
                continue
            negado = False
            if self.en('not') and self.mirar().tipo == 'palabra' and self.mirar().valor in ('in', 'between', 'like', 'ilike'):
                self.avanzar()
                negado = True
            if self.acepta('in'):
                self.espera_signo('(')
                if self.en('select', 'with'):
                    izquierda = InSelect(izquierda, self.consulta(), negado)
                else:
                    elementos = [self.expresion()]
                    while self.acepta_signo(','):
                        elementos.append(self.expresion())
                    izquierda = InList(izquierda, elementos, negado)
                self.espera_signo(')')
            elif self.acepta('between'):
                bajo = self.suma()
                self.espera('and')
                izquierda = Between(izquierda, bajo, self.suma(), negado)
            elif self.acepta('like', 'ilike'):
                izquierda = Like(izquierda, self.suma(), negado, 'pattern')
                if self.en('escape'):
                    raise ErrorSQL("LIKE con ESCAPE no está soportado: SBD busca el texto tal cual con "
                                   "«contiene»; en SQLAlchemy, usa contains() sin autoescape")
            else:
                if negado:
                    self.error("después de NOT va IN, BETWEEN o LIKE")
                return izquierda

    def suma(self):
        izquierda = self.producto()
        while self.en_signo('+', '-', '||'):
            op = self.avanzar().valor
            izquierda = Binary(op, izquierda, self.producto())
        return izquierda

    def producto(self):
        izquierda = self.unario()
        while self.en_signo('*', '/', '%'):
            op = self.avanzar().valor
            izquierda = Binary(op, izquierda, self.unario())
        return izquierda

    def unario(self):
        if self.en_signo('-', '+'):
            op = self.avanzar().valor
            valor = self.unario()
            if isinstance(valor, Literal) and isinstance(valor.value, (int, float)) and not isinstance(valor.value, bool):
                return Literal(-valor.value if op == '-' else valor.value)
            return Unary(op, valor)
        return self.primario()

    def primario(self):
        tok = self.tok
        if tok.tipo in ('numero', 'texto'):
            self.avanzar()
            return Literal(tok.valor)
        if self.acepta_signo('?'):
            self.parametros += 1
            return Param(self.parametros - 1)
        if self.acepta_signo('('):
            if self.en('select', 'with'):
                consulta = self.consulta()
                self.espera_signo(')')
                return Subquery(consulta)
            expr = self.expresion()
            self.espera_signo(')')
            return expr
        if tok.tipo == 'palabra':
            palabra = tok.valor
            if palabra in ('null', 'true', 'false'):
                self.avanzar()
                return Literal({'true': True, 'false': False}.get(palabra))
            if palabra == 'case':
                return self.caso()
            if palabra == 'cast':
                return self.conversion()
            if palabra == 'exists':
                self.avanzar()
                self.espera_signo('(')
                consulta = self.consulta()
                self.espera_signo(')')
                return Exists(consulta)
            if palabra in ('current_date', 'current_timestamp') and not (self.mirar().valor == '(' and self.mirar().tipo == 'signo'):
                self.avanzar()
                return FuncCall(_FUNCIONES[palabra], [])
            if palabra == 'default':
                self.avanzar()
                return DefaultValue()
            if palabra in _RESERVADAS and not (self.mirar().valor == '(' and self.mirar().tipo == 'signo'):
                self.error("se esperaba un valor")
        if tok.tipo in ('palabra', 'nombre'):
            self.avanzar()
            if tok.tipo == 'palabra' and self.en_signo('('):
                return self.funcion(tok.valor)
            if self.acepta_signo('.'):
                return Column(self.nombre('un nombre de columna'), tok.valor)
            return Column(tok.valor)
        self.error("se esperaba un valor")

    def funcion(self, nombre):
        self.espera_signo('(')
        sbd = _FUNCIONES.get(nombre, nombre)
        if self.acepta_signo('*'):
            self.espera_signo(')')
            func = FuncCall(sbd, [], star=True)
        else:
            distinto = self.acepta('distinct') is not None
            args = []
            if not self.en_signo(')'):
                args.append(self.expresion())
                while self.acepta_signo(','):
                    args.append(self.expresion())
            self.espera_signo(')')
            func = FuncCall(sbd, args, distinto, star=(sbd == 'contar' and not args))
        if not self.acepta('over'):
            return func
        self.espera_signo('(')
        particion, orden = [], []
        if self.acepta('partition'):
            self.espera('by')
            particion = [self.expresion()]
            while self.acepta_signo(','):
                particion.append(self.expresion())
        if self.acepta('order'):
            self.espera('by')
            orden = [self.orden_item()]
            while self.acepta_signo(','):
                orden.append(self.orden_item())
        self.espera_signo(')')
        return Window(func, particion, orden)

    def caso(self):
        self.espera('case')
        operando = None if self.en('when') else self.expresion()
        casos = []
        while self.acepta('when'):
            condicion = self.expresion()
            self.espera('then')
            casos.append((condicion, self.expresion()))
        if not casos:
            self.error("CASE necesita al menos un WHEN")
        resto = self.expresion() if self.acepta('else') else None
        self.espera('end')
        return Case(operando, casos, resto)

    def conversion(self):
        self.espera('cast')
        self.espera_signo('(')
        expr = self.expresion()
        self.espera('as')
        tipo, largo = self.tipo()
        self.espera_signo(')')
        return Cast(expr, tipo, largo)

    def tipo(self):
        nombre = self.nombre('un tipo')
        if nombre == 'double':
            self.acepta('precision')
        numeros = []
        if self.acepta_signo('('):
            numeros.append(self.entero())
            while self.acepta_signo(','):
                numeros.append(self.entero())
            self.espera_signo(')')
        if nombre in ('numeric', 'decimal'):
            # Con dos decimales es dinero: exacto. Con cualquier otra escala, un decimal normal.
            return ('MONEY', None) if len(numeros) == 2 and numeros[1] == 2 else ('REAL', None)
        if nombre not in _TIPOS:
            raise ErrorSQL(f"SBD no tiene un tipo equivalente a «{nombre.upper()}»")
        tipo = _TIPOS[nombre]
        return tipo, numeros[0] if tipo == 'TEXT' and numeros else None

    def entero(self):
        if self.tok.tipo != 'numero' or not isinstance(self.tok.valor, int):
            self.error("se esperaba un número entero")
        return self.avanzar().valor

    # ---------------------------------------------------------------- datos
    def insert(self):
        self.espera('insert')
        self.espera('into')
        tabla = self.nombre('el nombre de una tabla')
        if self.acepta('default'):
            self.espera('values')
            return Insert(tabla, [], [[]])
        columnas = None
        if self.en_signo('(') and not (self.mirar().tipo == 'palabra' and self.mirar().valor in ('select', 'with')):
            columnas = self.lista_nombres()
        if self.en('select', 'with'):
            return Insert(tabla, columnas, None, self.consulta())
        self.espera('values')
        filas = [self.fila()]
        while self.acepta_signo(','):
            filas.append(self.fila())
        return Insert(tabla, columnas, filas)

    def fila(self):
        self.espera_signo('(')
        valores = [self.expresion()]
        while self.acepta_signo(','):
            valores.append(self.expresion())
        self.espera_signo(')')
        return valores

    def update(self):
        self.espera('update')
        tabla = self.nombre('el nombre de una tabla')
        self.espera('set')
        cambios = []
        while True:
            columna = self.nombre('una columna')
            if self.acepta_signo('.'):
                columna = self.nombre('una columna')
            self.espera_signo('=')
            cambios.append((columna, self.expresion()))
            if not self.acepta_signo(','):
                break
        return Update(tabla, cambios, self.expresion() if self.acepta('where') else None)

    def delete(self):
        self.espera('delete')
        self.espera('from')
        tabla = self.nombre('el nombre de una tabla')
        return Delete(tabla, self.expresion() if self.acepta('where') else None)

    # ------------------------------------------------------------- estructura
    def create(self):
        self.espera('create')
        unico = self.acepta('unique') is not None
        if self.acepta('index'):
            existe = self.si_no_existe()
            nombre = self.nombre('el nombre del índice')
            self.espera('on')
            return CreateIndex(nombre, self.nombre('una tabla'), self.lista_nombres(), unico, existe)
        if unico or not self.acepta('table'):
            self.error("con SQL solo se pueden crear tablas e índices")
        existe = self.si_no_existe()
        nombre = self.nombre('el nombre de la tabla')
        if self.acepta('as'):
            return CreateTable(nombre, [], [], existe, self.consulta())
        self.espera_signo('(')
        columnas, restricciones = [], []
        while True:
            restriccion = self.restriccion_tabla()
            if restriccion is not None:
                restricciones.append(restriccion)
            else:
                columnas.append(self.columna())
            if not self.acepta_signo(','):
                break
        self.espera_signo(')')
        return CreateTable(nombre, columnas, restricciones, existe)

    def si_no_existe(self):
        if self.acepta('if'):
            self.espera('not')
            self.espera('exists')
            return True
        return False

    def restriccion_tabla(self):
        if self.acepta('constraint'):
            self.nombre('el nombre de la restricción')
        if self.acepta('primary'):
            self.espera('key')
            return TableConstraint('primary', self.lista_nombres())
        if self.en('unique') and self.mirar().valor == '(':
            self.avanzar()
            return TableConstraint('unique', self.lista_nombres())
        if self.en('check') and self.mirar().valor == '(':
            self.avanzar()
            condicion, sbd = self.comprobacion()
            return TableConstraint('check', check=condicion, check_sql=sbd)
        if self.acepta('foreign'):
            self.espera('key')
            columnas = self.lista_nombres()
            return TableConstraint('foreign', columnas, references=self.referencia(columnas))
        return None

    def comprobacion(self):
        self.espera_signo('(')
        condicion = self.expresion()
        self.espera_signo(')')
        return condicion, a_sbd(condicion)

    def referencia(self, columnas):
        self.espera('references')
        tabla = self.nombre('la tabla a la que apunta')
        destino = self.lista_nombres() if self.en_signo('(') else None
        al_borrar = 'restrict'
        while self.acepta('on'):
            if self.acepta('update'):
                if self.acepta('no'):
                    self.espera('action')
                    continue
                self.acepta('restrict')
                if self.en('cascade', 'set'):
                    raise ErrorSQL("SBD no tiene ON UPDATE CASCADE ni ON UPDATE SET: las claves no se propagan al cambiar")
                continue
            self.espera('delete')
            if self.acepta('cascade'):
                al_borrar = 'cascade'
            elif self.acepta('set'):
                self.espera('null')
                al_borrar = 'set null'
            elif self.acepta('no'):
                self.espera('action')
            else:
                self.espera('restrict')
        return ForeignKey(list(columnas), tabla, destino, al_borrar)

    def columna(self):
        cd = ColumnDef(self.nombre('el nombre de una columna'))
        cd.type, cd.length = self.tipo()
        while True:
            if self.acepta('constraint'):
                self.nombre('el nombre de la restricción')
            if self.acepta('primary'):
                self.espera('key')
                self.acepta('autoincrement')
                cd.primary_key = True
            elif self.acepta('not'):
                self.espera('null')
                cd.not_null = True
            elif self.acepta('null'):
                pass
            elif self.acepta('unique'):
                cd.unique = True
            elif self.acepta('default'):
                cd.default = self.suma()
                cd.default_sql = a_sbd(cd.default)
            elif self.en('check'):
                self.avanzar()
                cd.check, cd.check_sql = self.comprobacion()
            elif self.en('references'):
                cd.references = self.referencia([cd.name])
            else:
                return cd

    def drop(self):
        self.espera('drop')
        que = self.acepta('table', 'index')
        if que is None:
            self.error("con SQL solo se pueden borrar tablas e índices")
        existe = False
        if self.acepta('if'):
            self.espera('exists')
            existe = True
        nombre = self.nombre('un nombre')
        return (DropTable if que == 'table' else DropIndex)(nombre, existe)


# ---------------------------------------------------------- de vuelta a SBD
_OPERADORES_SBD = {'and': 'y', 'or': 'o', '||': '&', 'is': 'es', 'is not': 'no es'}
_UNIONES_SBD = {'union': 'union', 'union all': 'union todo', 'intersect': 'comun', 'except': 'excepto'}
_JOINS_SBD = {'inner': 'unir', 'left': 'unir opcional', 'right': 'unir inverso', 'full': 'unir completo'}
_TIPOS_SBD = {'INTEGER': 'entero', 'REAL': 'decimal', 'TEXT': 'texto', 'BOOLEAN': 'logico', 'DATE': 'fecha',
              'TIMESTAMP': 'fechahora', 'MONEY': 'dinero'}
_AL_BORRAR_SBD = {'cascade': 'cascada', 'set null': 'anular', 'restrict': 'impedir', 'no action': 'impedir'}


class _Escritor:
    """Escribe un árbol de órdenes en el lenguaje de SBD.

    En SBD los «?» se numeran según aparecen en el texto, y el orden de las cláusulas no es el de SQL
    (la tabla va antes que las columnas). Por eso se anota qué parámetro original corresponde a cada «?».
    """

    def __init__(self):
        self.parametros = []

    def orden(self, n):
        metodo = getattr(self, 'o_' + type(n).__name__, None)
        if metodo is None:
            raise ErrorSQL(f"esta orden no se puede enviar al servidor de SBD: {type(n).__name__}")
        return metodo(n)

    # -------------------------------------------------------------- consultas
    def o_Select(self, q):
        partes = []
        if q.ctes:
            partes.append('con recursivo' if any(c.recursive for c in q.ctes) else 'con')
            auxiliares = []
            for c in q.ctes:
                columnas = f"({', '.join(quote_ident(x) for x in c.columns)})" if c.columns else ''
                auxiliares.append(f"{quote_ident(c.name)}{columnas} = ({self.o_Select(c.query)})")
            partes.append(', '.join(auxiliares))
        partes.append(self.core(q.cores[0]))
        for op, core in zip(q.ops, q.cores[1:]):
            partes.append(_UNIONES_SBD[op])
            partes.append(self.core(core))
        if q.order_by:
            partes.append('ordenar ' + self.lista(q.order_by, self.orden_item))
        if q.limit is not None:
            partes.append('limite ' + self.e(q.limit))
        if q.offset is not None:
            partes.append('saltar ' + self.e(q.offset))
        return ' '.join(partes)

    def core(self, c):
        if c.from_ is None:
            partes = ['calcular ' + self.lista(c.items, self.item)]
        else:
            partes = ['buscar ' + self.origen(c.from_)]
        if c.where is not None:
            partes.append('donde ' + self.e(c.where))
        if c.group_by:
            partes.append('agrupar ' + self.lista(c.group_by, self.e))
        if c.having is not None:
            partes.append('teniendo ' + self.e(c.having))
        if c.from_ is not None:
            partes.append(('mostrar distintos ' if c.distinct else 'mostrar ') + self.lista(c.items, self.item))
        elif c.distinct:
            raise ErrorSQL("SELECT DISTINCT sin FROM no se puede enviar al servidor de SBD")
        return ' '.join(partes)

    def lista(self, elementos, escribir):
        escritos = []
        for x in elementos:
            escritos.append(escribir(x))
        return ', '.join(escritos)

    def item(self, i):
        if isinstance(i.expr, Star):
            return f"{quote_ident(i.expr.table)}.*" if i.expr.table else '*'
        texto = self.e(i.expr)
        return f"{texto} como {quote_ident(i.alias)}" if i.alias else texto

    def origen(self, f):
        if not isinstance(f, Join):
            return self.fuente(f)
        izquierda = self.origen(f.left)
        if isinstance(f.right, Join):
            raise ErrorSQL("un JOIN entre paréntesis no se puede enviar al servidor de SBD: escríbelo sin paréntesis")
        if f.kind == 'cross':
            return f"{izquierda} cruzar {self.fuente(f.right)}"
        derecha = self.fuente(f.right)
        if f.using:
            return f"{izquierda} {_JOINS_SBD[f.kind]} {derecha} usando ({', '.join(quote_ident(c) for c in f.using)})"
        return f"{izquierda} {_JOINS_SBD[f.kind]} {derecha} en {self.e(f.on)}"

    def fuente(self, f):
        if isinstance(f, SubqueryRef):
            return f"({self.o_Select(f.query)}) como {quote_ident(f.alias)}"
        return quote_ident(f.name) + (f" como {quote_ident(f.alias)}" if f.alias else '')

    def orden_item(self, o):
        texto = self.e(o.expr) + (' desc' if o.desc else '')
        if o.nulls:
            texto += ' nulos primero' if o.nulls == 'first' else ' nulos ultimo'
        return texto

    # ------------------------------------------------------------ expresiones
    def e(self, n):
        metodo = getattr(self, 'e_' + type(n).__name__, None)
        if metodo is None:
            raise ErrorSQL(f"esta expresión no se puede escribir en SBD: {type(n).__name__}")
        return metodo(n)

    def e_Literal(self, n):
        if n.value is None:
            return 'nulo'
        if n.value is True:
            return 'verdadero'
        if n.value is False:
            return 'falso'
        return quote(n.value) if isinstance(n.value, str) else repr(n.value)

    def e_Param(self, n):
        self.parametros.append(n.index)
        return '?'

    def e_Column(self, n):
        return f"{quote_ident(n.table)}.{quote_ident(n.name)}" if n.table else quote_ident(n.name)

    def e_DefaultValue(self, n):
        return 'defecto'

    def e_Binary(self, n):
        izquierda = self.e(n.left)
        return f"({izquierda} {_OPERADORES_SBD.get(n.op, n.op)} {self.e(n.right)})"

    def e_Unary(self, n):
        return f"(no {self.e(n.operand)})" if n.op == 'not' else f"({n.op}{self.e(n.operand)})"

    def e_IsNull(self, n):
        return f"({self.e(n.expr)} {'no es' if n.negated else 'es'} nulo)"

    def e_InList(self, n):
        izquierda = self.e(n.expr)
        return f"({izquierda} {'no en' if n.negated else 'en'} [{self.lista(n.items, self.e)}])"

    def e_InSelect(self, n):
        izquierda = self.e(n.expr)
        return f"({izquierda} {'no en' if n.negated else 'en'} ({self.o_Select(n.query)}))"

    def e_Between(self, n):
        izquierda = self.e(n.expr)
        bajo = self.e(n.low)
        return f"({izquierda} {'no entre' if n.negated else 'entre'} {bajo} y {self.e(n.high)})"

    def e_Like(self, n):
        izquierda = self.e(n.expr)
        return f"({izquierda} {'no parece' if n.negated else 'parece'} {self.e(n.pattern)})"

    def e_FuncCall(self, n):
        if n.star:
            return f"{n.name}()"
        return f"{n.name}({'distintos ' if n.distinct else ''}{self.lista(n.args, self.e)})"

    def e_Window(self, n):
        funcion = self.e_FuncCall(n.func)
        dentro = []
        if n.partition:
            dentro.append('por ' + self.lista(n.partition, self.e))
        if n.order:
            dentro.append('ordenar ' + self.lista(n.order, self.orden_item))
        return f"{funcion} sobre ({' '.join(dentro)})"

    def e_Case(self, n):
        partes = ['caso']
        if n.operand is not None:
            partes.append(self.e(n.operand))
        for condicion, resultado in n.whens:
            partes.append('cuando ' + self.e(condicion))
            partes.append('entonces ' + self.e(resultado))
        if n.else_ is not None:
            partes.append('sino ' + self.e(n.else_))
        partes.append('fin')
        return ' '.join(partes)

    def e_Cast(self, n):
        return f"{_TIPOS_SBD[n.type]}({self.e(n.expr)})"

    def e_Exists(self, n):
        return f"existe ({self.o_Select(n.query)})"

    def e_Subquery(self, n):
        return f"({self.o_Select(n.query)})"

    # ------------------------------------------------------------------ datos
    def o_Insert(self, n):
        t = quote_ident(n.table)
        if n.query is not None:
            columnas = f" ({', '.join(quote_ident(c) for c in n.columns)})" if n.columns else ''
            return f"insertar en {t}{columnas} desde {self.o_Select(n.query)}"
        if n.columns is None:
            return f"insertar en {t} " + ' '.join(f"({self.lista(f, self.e)})" for f in n.values)
        fichas = []
        for fila in n.values:
            campos = []
            for columna, valor in zip(n.columns, fila):
                campos.append(f"{quote_ident(columna)}: {self.e(valor)}")
            fichas.append('{ ' + ', '.join(campos) + ' }' if campos else '{}')
        return f"insertar en {t} " + ' '.join(fichas)

    def o_Update(self, n):
        cambios = []
        for columna, valor in n.assignments:
            cambios.append(f"{quote_ident(columna)} = {self.e(valor)}")
        texto = f"actualizar {quote_ident(n.table)} poner {', '.join(cambios)}"
        return f"{texto} donde {self.e(n.where)}" if n.where is not None else f"{texto} todas"

    def o_Delete(self, n):
        if n.where is None:
            return f"vaciar {quote_ident(n.table)}"
        return f"borrar de {quote_ident(n.table)} donde {self.e(n.where)}"

    # ------------------------------------------------------------- estructura
    def o_CreateTable(self, n):
        t = quote_ident(n.name) + (' si no existe' if n.if_not_exists else '')
        if n.query is not None:
            return f"crear tabla {t} = {self.o_Select(n.query)}"
        lineas = [self.columna(c) for c in n.columns]
        for r in n.constraints:
            if r.kind == 'primary':
                lineas.append(f"clave ({', '.join(quote_ident(c) for c in r.columns)})")
            elif r.kind == 'unique':
                lineas.append(f"unico ({', '.join(quote_ident(c) for c in r.columns)})")
            elif r.kind == 'check':
                lineas.append(f"comprobar {r.check_sql}")
            else:
                lineas.append(f"({', '.join(quote_ident(c) for c in r.columns)}) {self.referencia(r.references)}")
        return f"crear tabla {t} {{\n  " + ',\n  '.join(lineas) + '\n}'

    def columna(self, c):
        tipo = _TIPOS_SBD[c.type] + (f'({c.length})' if c.length else '')
        partes = [quote_ident(c.name), tipo]
        if c.primary_key:
            partes.append('clave')
        if c.not_null:
            partes.append('obligatorio')
        if c.unique:
            partes.append('unico')
        if c.default_sql:
            partes.append(f'= {c.default_sql}')
        if c.check_sql:
            partes.append(f'comprobar {c.check_sql}')
        if c.references:
            partes.append(self.referencia(c.references))
        return ' '.join(partes)

    def referencia(self, fk):
        destino = f"({', '.join(quote_ident(c) for c in fk.ref_columns)})" if fk.ref_columns else ''
        return f"-> {quote_ident(fk.ref_table)}{destino} al borrar {_AL_BORRAR_SBD[fk.on_delete]}"

    def o_CreateIndex(self, n):
        columnas = ', '.join(quote_ident(c) for c in n.columns)
        return f"indexar {'unico ' if n.unique else ''}{quote_ident(n.table)} ({columnas}) como {quote_ident(n.name)}"

    def o_DropTable(self, n):
        return f"borrar tabla {quote_ident(n.name)}" + (' si existe' if n.if_exists else '')

    def o_DropIndex(self, n):
        return f"borrar indice {quote_ident(n.name)}" + (' si existe' if n.if_exists else '')

    def o_Begin(self, n):
        return 'empezar'

    def o_Commit(self, n):
        return 'confirmar'

    def o_Rollback(self, n):
        return 'deshacer'


def a_sbd(expresion):
    """Una expresión en el lenguaje de SBD. SBD guarda así los CHECK y DEFAULT en su esquema, en su propio idioma."""
    return _Escritor().e(expresion)


@lru_cache(maxsize=512)
def a_texto_sbd(sql):
    """(la orden escrita en SBD, qué parámetro original va en cada «?»)."""
    nodo, _ = traducir(sql)
    escritor = _Escritor()
    texto = escritor.orden(nodo)
    return texto, tuple(escritor.parametros)


# ------------------------------------------------------------------- entrada
_ORDENES_SQL = frozenset({'select', 'with', 'insert', 'update', 'delete', 'create', 'drop', 'begin', 'start',
                          'commit', 'rollback', 'alter', 'savepoint', 'release'})


def es_sql(texto):
    """¿Empieza por una palabra de SQL? Si no, es una orden en el lenguaje de SBD."""
    m = re.match(r"(?:\s+|--[^\n]*\n|/\*.*?\*/)*([^\W\d]\w*)", texto, re.DOTALL)
    return m is not None and m.group(1).lower() in _ORDENES_SQL


@lru_cache(maxsize=512)
def traducir(sql):
    """(árbol de la orden, cuántos «?» lleva)."""
    t = _Traductor(sql)
    return t.orden(), t.parametros
