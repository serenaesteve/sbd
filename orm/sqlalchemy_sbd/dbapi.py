"""SBD con la interfaz estándar de bases de datos de Python (PEP 249, DB-API 2.0).

Admite SQL y también órdenes en el lenguaje de SBD: si el texto no empieza por una palabra de
SQL, se lo pasa tal cual a SBD. Cada conexión es una sesión de SBD, con su propia transacción.

Dos formas de conectarse, con la misma interfaz:
- Connection abre el fichero de la base dentro de este programa.
- ConexionRemota habla con un `sbd servidor` por su API, con una clave: es lo que permite usar el ORM
  con una base que el servidor ya tiene abierta. La API recibe texto, así que el SQL se le envía
  traducido al lenguaje de SBD.
"""
import datetime
from decimal import Decimal
import os
import re
import secrets
import threading
import time

from sbd import BaseOcupada, Cliente, Database, ErrorServidor, PermisoDenegado, SBDError
from sbd.errors import IncompleteInputError
from sbd.nodes import (Begin, Commit, Describe, Explain, Rollback, Select, ShowIndexes, ShowProcedures, ShowTables)
from sbd.parser import Parser
from sbd.result import Result

from .sql import ErrorSQL, a_texto_sbd, es_sql, traducir

apilevel = '2.0'
threadsafety = 1   # los hilos pueden compartir el módulo, pero cada uno necesita su conexión
paramstyle = 'qmark'
ESPERA_OCUPADA = 5.0  # segundos que espera una conexión si otra tiene una transacción abierta


class Warning(Exception):  # noqa: A001 — el nombre lo fija el estándar PEP 249
    pass


class Error(Exception):
    pass


class InterfaceError(Error):
    pass


class DatabaseError(Error):
    pass


class DataError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class InternalError(DatabaseError):
    pass


class ProgrammingError(DatabaseError):
    pass


class NotSupportedError(DatabaseError):
    pass


# SBD explica los fallos en español, sin códigos; estas frases son las de sus restricciones.
_INTEGRIDAD = ('no se puede repetir', 'es obligatorio', 'no cumple «comprobar', ' apunta a ', 'que apuntan a')


def _traducir_error(e):
    if isinstance(e, (BaseOcupada, PermisoDenegado)):
        return OperationalError(str(e))
    if isinstance(e, IncompleteInputError):
        return ProgrammingError(str(e))
    if isinstance(e, ErrorServidor):
        if e.tipo == 'sintaxis':
            return ProgrammingError(str(e))
        if e.tipo in ('conexion', 'http', 'sesion', 'clave', 'interno'):
            error = OperationalError(str(e))
            error.conexion_perdida = e.tipo in ('conexion', 'http', 'sesion')  # el pool abrirá otra
            return error
    mensaje = str(e)
    return (IntegrityError if any(f in mensaje for f in _INTEGRIDAD) else DatabaseError)(mensaje)




class _BaseCompartida:
    """Las conexiones a un mismo fichero comparten su Database: SBD solo admite abrirlo una vez por programa."""
    abiertas = {}
    cerrojo = threading.RLock()

    def __init__(self, ruta):
        self.ruta, self.db, self.usos = ruta, Database(ruta, 'SQLAlchemy'), 0
        self.hilo = threading.RLock()  # el motor atiende las órdenes de una en una

    @classmethod
    def abrir(cls, ruta):
        clave = ruta if ruta == ':memoria:' else os.path.abspath(ruta)
        with cls.cerrojo:
            base = cls.abiertas.get(clave)
            if base is None:
                try:
                    base = cls(None if ruta == ':memoria:' else clave)
                except SBDError as e:
                    raise OperationalError(str(e)) from None
                cls.abiertas[clave] = base
            base.usos += 1
            return clave, base

    @classmethod
    def soltar(cls, clave):
        with cls.cerrojo:
            base = cls.abiertas[clave]
            base.usos -= 1
            if base.usos == 0:
                del cls.abiertas[clave]
                base.db.close()


_SAVEPOINT = re.compile(r'\s*(SAVEPOINT|RELEASE(?:\s+SAVEPOINT)?|ROLLBACK\s+TO(?:\s+SAVEPOINT)?)\s+"?(\w+)"?\s*;?\s*\Z', re.IGNORECASE)

# Lo que solo lee no abre transacción: leer no debe bloquear a las demás conexiones.
_SOLO_LEEN = (Select, ShowTables, ShowIndexes, ShowProcedures, Describe, Explain)


def _adaptar(valor):
    """Las fechas de Python llegan a SBD como su texto ISO, que es como las guarda."""
    if isinstance(valor, datetime.datetime):
        return valor.isoformat(sep=' ', timespec='seconds')
    if isinstance(valor, datetime.date):
        return valor.isoformat()
    return valor


class Cursor:
    arraysize = 1

    def __init__(self, conexion):
        self.connection = conexion
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self._filas = iter(())
        self._cerrado = False

    def execute(self, operation, parameters=()):
        if self._cerrado:
            raise InterfaceError("el cursor está cerrado")
        r = self.connection._ejecutar(operation, tuple(_adaptar(v) for v in parameters or ()))
        if r.columns is None:
            self.description = None
            self._filas = iter(())
        else:
            self.description = [(c, None, None, None, None, None, None) for c in r.columns]
            self._filas = iter(r.rows)
        self.rowcount = r.rowcount
        self.lastrowid = r.lastrowid
        return self

    def executemany(self, operation, seq_of_parameters):
        total = 0
        for parametros in seq_of_parameters:
            self.execute(operation, parametros)
            total += self.rowcount
        self.rowcount = total
        return self

    def fetchone(self):
        return next(self._filas, None)

    def fetchmany(self, size=None):
        size = self.arraysize if size is None else size
        filas = []
        for _ in range(size):
            fila = next(self._filas, None)
            if fila is None:
                break
            filas.append(fila)
        return filas

    def fetchall(self):
        return list(self._filas)

    def __iter__(self):
        return self._filas

    def close(self):
        self._cerrado = True
        self._filas = iter(())

    def setinputsizes(self, sizes):
        pass

    def setoutputsize(self, size, column=None):
        pass


class Connection:
    Error, DatabaseError, IntegrityError, OperationalError, ProgrammingError = (
        Error, DatabaseError, IntegrityError, OperationalError, ProgrammingError)

    def __init__(self, ruta):
        self._clave, self._base = _BaseCompartida.abrir(ruta)
        self._sesion = 'orm:' + secrets.token_hex(8)
        self._savepoints = {}  # nombre -> hasta dónde deshacer dentro de la transacción

    def _abierta(self):
        if self._base is None:
            raise InterfaceError("la conexión está cerrada")
        return self._base

    @property
    def db(self):
        if self._base is None:
            raise InterfaceError("la conexión está cerrada")
        return self._base.db

    def _analizar(self, texto, n_parametros):
        if es_sql(texto):
            try:
                orden, esperados = traducir(texto)
            except ErrorSQL as e:
                raise ProgrammingError(str(e)) from None
        else:
            try:
                parser = Parser(texto)
                ordenes = parser.parse_script()
            except SBDError as e:
                raise ProgrammingError(str(e)) from None
            if len(ordenes) != 1:
                raise ProgrammingError("solo se admite una orden cada vez")
            orden, esperados = ordenes[0], parser.param_count
        if esperados != n_parametros:
            raise ProgrammingError(f"la orden tiene {esperados} parámetro(s) «?» y se han pasado {n_parametros}")
        return orden

    def _savepoint(self, accion, nombre):
        """Puntos de guardado: SBD anota cómo deshacer cada cambio, así que basta con recordar por dónde iba."""
        accion = accion.upper()  # SAVEPOINT, RELEASE o ROLLBACK
        with self._base.hilo:
            db = self.db
            if accion == 'SAVEPOINT':
                if not db.in_transaction:
                    db.run_statement(Begin(), (), self._sesion, 'SQLAlchemy')
                self._savepoints[nombre] = len(db._undo)
            else:
                if not self._mia() or nombre not in self._savepoints:
                    raise OperationalError(f"no existe el punto de guardado «{nombre}»")
                if accion == 'ROLLBACK':
                    db._undo_to(self._savepoints[nombre])
                else:
                    del self._savepoints[nombre]
        return Result(message=f"{accion} {nombre}")

    def _ejecutar(self, texto, parametros):
        self._abierta()
        punto = _SAVEPOINT.match(texto)
        if punto:
            return self._savepoint(punto.group(1).split()[0], punto.group(2))
        orden = self._analizar(texto, len(parametros))
        limite = time.monotonic() + ESPERA_OCUPADA
        while True:
            try:
                with self._base.hilo:
                    db = self.db
                    # Como manda el estándar, la transacción empieza sola con la primera orden que cambia algo.
                    if not isinstance(orden, (*_SOLO_LEEN, Begin, Commit, Rollback)) and not db.in_transaction:
                        db.run_statement(Begin(), (), self._sesion, 'SQLAlchemy')
                    if isinstance(orden, (Commit, Rollback)):
                        self._savepoints.clear()
                    return db.run_statement(orden, parametros, self._sesion, 'SQLAlchemy')
            except BaseOcupada as e:
                if time.monotonic() >= limite:
                    raise _traducir_error(e) from None
                time.sleep(0.02)
            except SBDError as e:
                raise _traducir_error(e) from None

    def cursor(self):
        return Cursor(self)

    def tablas(self):
        return self.db.tables

    def vistas(self):
        return self.db.views

    def _mia(self):
        db = self.db
        return db.in_transaction and db.owner == self._sesion

    def commit(self):
        with self._abierta().hilo:
            self._savepoints.clear()
            if self._mia():
                try:
                    self.db.run_statement(Commit(), (), self._sesion)
                except SBDError as e:
                    raise _traducir_error(e) from None

    def rollback(self):
        with self._abierta().hilo:
            self._savepoints.clear()
            if self._mia():
                self.db.run_statement(Rollback(), (), self._sesion)

    def close(self):
        if self._base is None:
            return
        self.rollback()
        base, self._base = self._base, None
        _BaseCompartida.soltar(self._clave)
        del base


class ConexionRemota:
    """Una sesión en un `sbd servidor`: su transacción dura entre peticiones y es solo suya."""
    Error, DatabaseError, IntegrityError, OperationalError, ProgrammingError = (
        Error, DatabaseError, IntegrityError, OperationalError, ProgrammingError)

    def __init__(self, url, clave, base):
        self._cliente = Cliente(url, clave, base)
        self._en_transaccion = False
        try:
            self._cliente.abrir_sesion()
        except SBDError as e:
            raise _traducir_error(e) from None

    def _preparar(self, texto, parametros):
        """(texto en SBD, parámetros en su orden, árbol o None)."""
        if _SAVEPOINT.match(texto):
            raise NotSupportedError("los puntos de guardado solo funcionan abriendo el fichero de la base, "
                                    "no por el servidor de SBD")
        if es_sql(texto):
            try:
                nodo, esperados = traducir(texto)
                sbd_texto, orden_parametros = a_texto_sbd(texto)
            except ErrorSQL as e:
                raise ProgrammingError(str(e)) from None
            if esperados != len(parametros):
                raise ProgrammingError(f"la orden tiene {esperados} parámetro(s) «?» y se han pasado {len(parametros)}")
            return sbd_texto, [parametros[i] for i in orden_parametros], nodo
        try:
            parser = Parser(texto)
            ordenes = parser.parse_script()
        except SBDError as e:
            raise ProgrammingError(str(e)) from None
        if len(ordenes) != 1:   # igual que en SQL: nunca dos órdenes pegadas
            raise ProgrammingError("solo se admite una orden cada vez")
        if parser.param_count != len(parametros):
            raise ProgrammingError(f"la orden tiene {parser.param_count} parámetro(s) «?» y se han pasado {len(parametros)}")
        return texto, list(parametros), ordenes[0]

    def _ejecutar(self, texto, parametros):
        if self._cliente is None:
            raise InterfaceError("la conexión está cerrada")
        sbd_texto, valores, nodo = self._preparar(texto, parametros)
        # JSON no tiene Decimal, y un importe como texto no se compararía bien con una columna «dinero»
        valores = [float(v) if isinstance(v, Decimal) else v for v in valores]
        try:
            if isinstance(nodo, (Commit, Rollback)):
                self._en_transaccion = False
            elif isinstance(nodo, Begin):
                self._en_transaccion = True
            elif not isinstance(nodo, _SOLO_LEEN) and not self._en_transaccion:
                self._cliente.ejecutar('empezar')
                self._en_transaccion = True
            return self._cliente.ejecutar(sbd_texto, valores)
        except SBDError as e:
            raise _traducir_error(e) from None

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self._terminar('confirmar')

    def rollback(self):
        self._terminar('deshacer')

    def _terminar(self, orden):
        if self._cliente is None:
            raise InterfaceError("la conexión está cerrada")
        if self._en_transaccion:
            self._en_transaccion = False
            try:
                self._cliente.ejecutar(orden)
            except SBDError as e:
                raise _traducir_error(e) from None

    def tablas(self):
        return self._catalogo()['tablas']

    def vistas(self):
        return self._catalogo()['vistas']

    def _catalogo(self):
        try:
            return self._cliente.catalogo()
        except SBDError as e:
            raise _traducir_error(e) from None

    def close(self):
        if self._cliente is None:
            return
        cliente, self._cliente = self._cliente, None
        try:
            cliente.cerrar_sesion()   # el servidor deshace lo que quedara sin confirmar
        except SBDError:
            pass


def connect(database=':memoria:', *, url=None, clave=None, base=None):
    if url:
        return ConexionRemota(url, clave, base)
    return Connection(database)
