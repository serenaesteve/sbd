"""El dialecto de SQLAlchemy para SBD: enseña a SQLAlchemy cómo hablar con el controlador de dbapi.py."""
import datetime
from decimal import Decimal

from sqlalchemy import exc, types
from sqlalchemy.engine import default, reflection
from sqlalchemy.sql import compiler

import sbd

from . import dbapi


class FechaSBD(types.TypeDecorator):
    """SBD guarda las fechas como texto ISO; en Python son objetos date."""
    impl = types.Date
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.isoformat() if value is not None else None

    def process_result_value(self, value, dialect):
        return datetime.date.fromisoformat(value) if value is not None else None


class FechaHoraSBD(types.TypeDecorator):
    impl = types.DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.isoformat(sep=' ', timespec='seconds') if value is not None else None

    def process_result_value(self, value, dialect):
        return datetime.datetime.fromisoformat(value) if value is not None else None


class NumericoSBD(types.TypeDecorator):
    """NUMERIC(n, 2) es «dinero» en SBD y ya llega como Decimal; las demás escalas son decimales normales."""
    impl = types.Numeric
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, Decimal) or not getattr(self.impl_instance, 'asdecimal', True):
            return value
        return Decimal(str(value))


class CompiladorSBD(compiler.SQLCompiler):
    def for_update_clause(self, select, **kw):
        return ''  # SBD atiende las órdenes de una en una: no hay bloqueos de fila que pedir


_TIPOS_SA = {'INTEGER': types.Integer, 'REAL': types.Float, 'TEXT': types.Text, 'BOOLEAN': types.Boolean,
             'DATE': types.Date, 'TIMESTAMP': types.DateTime}


class DialectoSBD(default.DefaultDialect):
    name = 'sbd'
    driver = 'sbd'
    paramstyle = 'qmark'
    default_paramstyle = 'qmark'
    statement_compiler = CompiladorSBD
    supports_statement_cache = True
    supports_alter = False
    supports_native_boolean = True
    supports_native_decimal = True
    supports_default_values = True
    supports_default_metavalue = True
    supports_empty_insert = False
    supports_multivalues_insert = True
    supports_sane_rowcount = True
    supports_sane_multi_rowcount = False
    insert_returning = False
    update_returning = False
    delete_returning = False
    use_insertmanyvalues = False
    postfetch_lastrowid = True
    max_identifier_length = 128
    colspecs = {types.Date: FechaSBD, types.DateTime: FechaHoraSBD, types.Numeric: NumericoSBD, types.Float: types.Float}

    @classmethod
    def import_dbapi(cls):
        return dbapi

    def create_connect_args(self, url):
        return [url.database or ':memoria:'], {}

    def _get_server_version_info(self, connection):
        return tuple(int(x) for x in sbd.__version__.split('.'))

    def _get_default_schema_name(self, connection):
        return None

    def is_disconnect(self, e, connection, cursor):
        return getattr(e, 'conexion_perdida', False)

    # --------------------------------------------------- inspeccionar la base
    @staticmethod
    def _db(connection):
        conexion = connection.connection.driver_connection
        if isinstance(conexion, dbapi.ConexionRemota):
            raise NotImplementedError("por el servidor de SBD solo se pueden listar las tablas y las vistas")
        return conexion.db

    def has_table(self, connection, table_name, schema=None, **kw):
        return table_name in connection.connection.driver_connection.tablas()

    @reflection.cache
    def get_table_names(self, connection, schema=None, **kw):
        return sorted(connection.connection.driver_connection.tablas())

    @reflection.cache
    def get_view_names(self, connection, schema=None, **kw):
        return sorted(connection.connection.driver_connection.vistas())

    def _tabla(self, connection, nombre):
        tabla = self._db(connection).tables.get(nombre)
        if tabla is None:
            raise exc.NoSuchTableError(nombre)
        return tabla

    @reflection.cache
    def get_columns(self, connection, table_name, schema=None, **kw):
        tabla = self._tabla(connection, table_name)
        columnas = []
        for c in tabla.columns:
            if c.type == 'MONEY':
                tipo = types.Numeric(14, 2)
            elif c.type == 'TEXT' and c.length:
                tipo = types.String(c.length)
            else:
                tipo = _TIPOS_SA.get(c.type, types.NullType)()
            columnas.append({'name': c.name, 'type': tipo, 'nullable': not c.not_null,
                             'default': c.default_sql, 'autoincrement': tabla.auto_column == c.name})
        return columnas

    @reflection.cache
    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        return {'constrained_columns': list(self._tabla(connection, table_name).primary_key), 'name': None}

    @reflection.cache
    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        return [{'name': None, 'constrained_columns': list(fk.columns), 'referred_schema': None,
                 'referred_table': fk.ref_table, 'referred_columns': list(fk.ref_columns or []),
                 'options': {'ondelete': fk.on_delete.upper()}}
                for fk in self._tabla(connection, table_name).foreign_keys]

    @reflection.cache
    def get_indexes(self, connection, table_name, schema=None, **kw):
        return [{'name': i.name, 'column_names': list(i.columns), 'unique': i.unique}
                for i in self._tabla(connection, table_name).indexes.values() if not i.auto]

    @reflection.cache
    def get_unique_constraints(self, connection, table_name, schema=None, **kw):
        tabla = self._tabla(connection, table_name)
        return [{'name': i.name, 'column_names': list(i.columns)} for i in tabla.indexes.values()
                if i.auto and i.unique and list(i.columns) != list(tabla.primary_key)]

    @reflection.cache
    def get_check_constraints(self, connection, table_name, schema=None, **kw):
        return [{'name': None, 'sqltext': sql} for _, sql in self._tabla(connection, table_name).checks]


class DialectoSBDServidor(DialectoSBD):
    """create_engine("sbd+https://:CLAVE@sbd.miweb.es/mi_base"): por la API de un `sbd servidor`."""
    driver = 'http'
    supports_statement_cache = True

    def create_connect_args(self, url):
        esquema = 'https' if url.get_backend_name() == 'sbd' and url.get_driver_name() == 'https' else 'http'
        puerto = f':{url.port}' if url.port else ''
        return [], {'url': f'{esquema}://{url.host}{puerto}', 'clave': url.password, 'base': url.database}
