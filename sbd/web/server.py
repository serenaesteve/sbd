"""Servidor de SBD: panel web, API para aplicaciones y conexión de la consola, en un solo proceso.

    sbd servidor [datos] [--puerto 8080] [--host 127.0.0.1] [--certificado … --clave-privada …]

Rutas:
  /                 el panel (cuaderno)
  /api/…            lo que usa el panel (sesión con usuario y clave)
  /api/v1/…         la API para aplicaciones (cabecera «Authorization: Bearer sbd_…»)
  /salud            para comprobar que el servidor responde
  /cliente/sbd.js   la librería de JavaScript

Un único proceso tiene abiertas las bases; las peticiones se atienden de una en una
(como SQLite), lo que hace que cada orden vea siempre datos coherentes.
"""
import argparse
import base64
import csv
import io
import json
import math
import os
import re
import secrets
import ssl
import sys
import threading
import time
import traceback
import webbrowser
from decimal import Decimal
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote as url_quote, urlparse

from .. import __version__
from ..errors import BaseOcupada, IncompleteInputError, PermisoDenegado, SBDError
from ..functions import FUNCTIONS
from ..importar import importar, leer_fichero
from ..nodes import (AGGREGATES, WINDOW_ONLY, Describe, Explain, Import, Select, ShowDatabases, ShowIndexes,
                     ShowTables, ShowUsers)
from ..parser import KEYWORDS, TYPES, Parser
from ..servidor import Servidor, Sesion
from ..storage import quote_ident
from ..values import Intervalo, format_value, quote, type_name

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
    '/cliente/sbd.js': ('sbd-cliente.js', 'text/javascript; charset=utf-8'),
}
MAX_FILAS_PANEL = 2000
MAX_BODY = 50 * 1024 * 1024
LOCAL_HOSTS = {'localhost', '127.0.0.1', '[::1]'}
READ_ONLY = (Select, ShowTables, ShowIndexes, Describe, ShowDatabases, ShowUsers, Explain)
DURACION_SESION_WEB = 12 * 3600
DURACION_TRANSACCION = 120   # una transacción sin usar se deshace a los 2 minutos
DURACION_SESION_API = 600    # una sesión de la API sin usar se cierra a los 10 minutos
BASE_INACTIVA = 600          # una base que nadie usa en 10 minutos se guarda y sale de la memoria
ALTAS_POR_IP = 3             # cuentas nuevas por hora desde una misma dirección
ALTAS_POR_HORA = 30          # y en total, para frenar a quien quiera llenar el servidor
ESPERA_OCUPADA = 5.0         # cuánto espera una petición si otra sesión tiene la base ocupada
COOKIE = 'sbd_sesion'
FICHERO_SERVIDOR = '.sbd.servidor'  # url y clave local para que la consola se conecte
_CLAVE = re.compile(r'''(\bclave\b(?:\s+de\s+\S+\s+a)?\s+)("(?:[^"]|"")*"|'(?:[^']|'')*')''', re.IGNORECASE)


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST, tipo='peticion'):
        super().__init__(message)
        self.status = status
        self.tipo = tipo


def json_value(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return format_value(v)
    if isinstance(v, Decimal):
        return format(v, 'f')  # el dinero viaja como texto exacto: "12.50"
    if isinstance(v, Intervalo):
        return str(v)
    return v


def json_param(v):
    """Los parámetros llegan como JSON: números, textos, verdadero/falso o nulo."""
    if isinstance(v, (dict, list)):
        raise ApiError("los parámetros tienen que ser números, textos, verdadero/falso o nulo")
    return v


def ocultar_claves(texto):
    """Las claves de «crear usuario» y «cambiar clave» no vuelven al navegador."""
    return _CLAVE.sub(r'\1"••••••••"', texto)


def tipo_de_error(e):
    if isinstance(e, IncompleteInputError):
        return 'incompleta', HTTPStatus.BAD_REQUEST
    if isinstance(e, BaseOcupada):
        return 'ocupada', HTTPStatus.CONFLICT
    if isinstance(e, PermisoDenegado):
        return 'permiso', HTTPStatus.FORBIDDEN
    if isinstance(e, ApiError):
        return e.tipo, e.status
    return 'orden', HTTPStatus.BAD_REQUEST


class Nucleo:
    """Todo lo que hace el servidor. Un único hilo toca las bases a la vez (self.lock)."""

    def __init__(self, servidor, copias=7):
        self.servidor = servidor
        self.lock = threading.RLock()
        self.copias = copias
        self.clave_local = 'sbdl_' + secrets.token_urlsafe(32)  # la consola de este equipo
        self._web = {}       # testigo -> (usuario, caduca)
        self._api = {}       # id de sesión -> {'usuario', 'base', 'ultima'}
        self._fallos = {}    # ip -> (intentos fallidos seguidos, bloqueado hasta)
        self._altas = []     # (momento, ip) de las cuentas creadas en la última hora
        self._ultima_copia = None

    # ------------------------------------------------------- mantenimiento
    def mantenimiento(self):
        """Cada pocos segundos: deshace transacciones abandonadas, cierra sesiones viejas y hace la copia diaria."""
        with self.lock:
            self.servidor.caducar(DURACION_TRANSACCION)
            self.servidor.soltar_inactivas(BASE_INACTIVA)
            ahora = time.time()
            for sid, datos in list(self._api.items()):
                if ahora - datos['ultima'] > DURACION_SESION_API:
                    self._cerrar_sesion_api(sid)
            for testigo, (_, caduca) in list(self._web.items()):
                if caduca < ahora:
                    del self._web[testigo]
            hoy = time.strftime('%Y-%m-%d')
            if self.copias and self._ultima_copia != hoy:
                try:
                    self.servidor.copia_diaria(self.copias)
                    self._ultima_copia = hoy
                except OSError as e:
                    print(f"✕ no se pudo hacer la copia diaria: {e}", file=sys.stderr)

    # ------------------------------------------------------- identidad
    def entrar(self, nombre, clave, ip):
        ahora = time.time()
        intentos, hasta = self._fallos.get(ip, (0, 0))
        if hasta > ahora:
            raise ApiError(f"demasiados intentos fallidos: espera {int(hasta - ahora) + 1} segundos",
                           HTTPStatus.TOO_MANY_REQUESTS, 'demasiados_intentos')
        usuario = self.servidor.verificar(str(nombre), str(clave))
        if usuario is None:
            intentos += 1
            self._fallos[ip] = (intentos, ahora + 30 * (intentos - 4) if intentos >= 5 else 0)
            time.sleep(0.4)
            raise ApiError("usuario o clave incorrectos", HTTPStatus.UNAUTHORIZED, 'credenciales')
        self._fallos.pop(ip, None)
        return self._abrir_sesion_web(usuario), usuario

    def _abrir_sesion_web(self, usuario):
        testigo = secrets.token_urlsafe(32)
        self._web[testigo] = (usuario['nombre'], time.time() + DURACION_SESION_WEB)
        return testigo

    def registrar(self, nombre, clave, ip):
        """Crea una cuenta desde la pantalla de entrada (si el registro está abierto) y entra con ella."""
        ahora = time.time()
        self._altas = [(t, i) for t, i in self._altas if ahora - t < 3600]
        if sum(1 for _, i in self._altas if i == ip) >= ALTAS_POR_IP or len(self._altas) >= ALTAS_POR_HORA:
            raise ApiError("se han creado demasiadas cuentas seguidas: vuelve a intentarlo dentro de un rato",
                           HTTPStatus.TOO_MANY_REQUESTS, 'demasiados_intentos')
        usuario = self.servidor.registrar(str(nombre).strip().lower(), str(clave))
        self._altas.append((ahora, ip))
        self.servidor.auditar(usuario['nombre'], None, 'crear cuenta', f"Cuenta {usuario['nombre']} creada desde {ip}")
        return self._abrir_sesion_web(usuario), usuario

    def cuenta(self, usuario):
        """Lo que ocupa una cuenta y sus límites (para quien se registró)."""
        if usuario is None or usuario['permiso'] != 'cuenta':
            raise ApiError("esto es para las cuentas registradas", HTTPStatus.NOT_FOUND, 'no_existe')
        uso = self.servidor.uso(usuario['nombre'])
        return {k: uso[k] for k in ('bases', 'bytes', 'filas', 'max_bases', 'max_mb', 'max_filas')}

    def ajustes(self, usuario, cambios=None):
        if usuario is not None and usuario['permiso'] != 'todo':
            raise PermisoDenegado("los ajustes del servidor necesitan el permiso «todo»")
        datos = self.servidor.cambiar_ajustes(**cambios) if cambios else self.servidor.ajustes()
        return {**datos, 'cuentas': self.servidor.contar_cuentas(), 'memoria_mb': self.servidor.memoria_mb()}

    def salir(self, testigo, sid):
        datos = self._web.pop(testigo or '', None)
        if sid:
            self._terminar(f"panel:{datos[0] if datos else ''}:{sid}")

    def usuario_web(self, testigo):
        """None si no hay usuarios (todo abierto); el usuario; o ApiError si hace falta entrar."""
        if not self.servidor.hay_usuarios():
            return None
        sesion = self._web.get(testigo or '')
        if sesion is None or sesion[1] < time.time():
            self._web.pop(testigo or '', None)
            raise ApiError("hay que entrar con un usuario", HTTPStatus.UNAUTHORIZED, 'entrar')
        usuario = self.servidor.usuario(sesion[0])
        if usuario is None:
            raise ApiError("ese usuario ya no existe", HTTPStatus.UNAUTHORIZED, 'entrar')
        return usuario

    def usuario_api(self, autorizacion):
        """(usuario, es_local) a partir de «Authorization: Bearer …»."""
        clave = (autorizacion or '').removeprefix('Bearer ').strip()
        if not clave:
            raise ApiError("falta la clave de acceso: cabecera «Authorization: Bearer sbd_…» "
                           "(se crea con «crear clave api para usuario»)", HTTPStatus.UNAUTHORIZED, 'clave')
        if secrets.compare_digest(clave, self.clave_local):
            return None, True
        usuario = self.servidor.verificar_clave_api(clave)
        if usuario is None:
            raise ApiError("clave de acceso no válida o borrada", HTTPStatus.UNAUTHORIZED, 'clave')
        return usuario, False

    def estado(self, testigo):
        registro = self.servidor.ajustes()['registro']
        if not self.servidor.hay_usuarios():
            return {'sin_usuarios': True, 'usuario': None, 'registro': False}
        try:
            return {'sin_usuarios': False, 'usuario': self.usuario_web(testigo), 'registro': registro}
        except ApiError:
            return {'sin_usuarios': False, 'usuario': None, 'necesita_entrar': True, 'registro': registro}

    # ------------------------------------------------------- sesiones
    def _sesion(self, usuario, base=None, sid=None, **opciones):
        sesion = Sesion(self.servidor, usuario=usuario, id=sid, **opciones)
        if base:
            self.servidor.validar(base)
            if not self.servidor.existe(base):
                raise ApiError(f"no existe ninguna base «{base}»", HTTPStatus.NOT_FOUND, 'no_existe')
            if not sesion.puede_ver(base):
                raise PermisoDenegado(f"no tienes acceso a la base «{base}»")
            sesion.base = base
        return sesion

    def _terminar(self, sid):
        for db in list(self.servidor._abiertas.values()):
            db.end_session(sid)

    def crear_sesion_api(self, usuario, es_local, base):
        sid = 'api:' + secrets.token_urlsafe(18)
        self._sesion(usuario, base)  # comprueba que la base existe y que hay permiso
        self._api[sid] = {'usuario': usuario['nombre'] if usuario else None, 'local': es_local,
                          'base': base, 'ultima': time.time()}
        return sid

    def _cerrar_sesion_api(self, sid):
        self._api.pop(sid, None)
        self._terminar(sid)

    def cerrar_sesion_api(self, usuario, sid):
        datos = self._api.get(sid)
        if datos is None or datos['usuario'] != (usuario['nombre'] if usuario else None):
            raise ApiError("esa sesión no existe o ya se cerró", HTTPStatus.NOT_FOUND, 'sesion')
        self._cerrar_sesion_api(sid)

    # ------------------------------------------------------- panel: lectura
    def bases(self, usuario):
        sesion = self._sesion(usuario)
        # Solo se abren las bases que esta persona puede ver; la carpeta del servidor, solo para quien lo administra.
        return {'directorio': self.servidor.directorio if usuario is None or usuario['permiso'] == 'todo' else '',
                'bases': self.servidor.resumen(sesion.bases_visibles()),
                'palabras': sorted(KEYWORDS), 'tipos': sorted(TYPES),
                'funciones': sorted(set(FUNCTIONS) | AGGREGATES | WINDOW_ONLY)}

    def base(self, usuario, nombre, sid=None):
        sesion = self._sesion(usuario, nombre, sid)
        db = sesion.db
        tablas = [{'nombre': t.name, 'filas': len(t.rows), 'clave': t.primary_key,
                   'columnas': [{'nombre': c.name, 'tipo': c.type_sbd()} for c in t.columns]}
                  for t in db.tables.values()]
        return {'nombre': nombre, 'bytes': db.bytes_on_disk, **self._estado_base(sesion),
                'tablas': sorted(tablas, key=lambda t: t['nombre']), 'vistas': sorted(db.views)}

    def catalogo(self, usuario, base, sid=None):
        sesion = self._sesion(usuario, base, sid)
        return sesion.catalogo()

    def tabla(self, usuario, base, nombre):
        db = self._sesion(usuario, base).db
        if nombre in db.views and nombre not in db.tables:
            sql = db.views[nombre][0]
            columnas = db.execute(f"buscar {quote_ident(nombre)} limite 0").columns
            return {'nombre': nombre, 'tipo': 'vista', 'clave': [], 'indices': [], 'apuntan_aqui': [],
                    'comprobaciones': [], 'filas': None,
                    'columnas': [{'nombre': c, 'tipo': None, 'tipo_base': None, 'longitud': None,
                                  'obligatorio': False, 'defecto': None, 'clave': False, 'unico': False,
                                  'ref': None} for c in columnas],
                    'definicion': f"crear vista {quote_ident(nombre)} = {sql}"}
        t = db.table(nombre)
        columnas = []
        for c in t.columns:
            ref = next(({'tabla': fk.ref_table, 'columna': fk.ref_columns[fk.columns.index(c.name)],
                         'al_borrar': fk.on_delete} for fk in t.foreign_keys if c.name in fk.columns), None)
            columnas.append({'nombre': c.name, 'tipo': c.type_sbd(), 'tipo_base': c.type, 'longitud': c.length,
                             'obligatorio': c.not_null, 'defecto': c.default_sql,
                             'clave': c.name in t.primary_key, 'unico': [c.name] in t.uniques,
                             'auto': c.name == t.auto_column, 'ref': ref})
        apuntan = [{'tabla': o.name, 'columnas': fk.columns} for o in db.tables.values()
                   for fk in o.foreign_keys if fk.ref_table == nombre]
        definicion = [t.to_sbd()] + [i.to_sbd(t.name) for i in t.indexes.values() if not i.auto]
        return {'nombre': nombre, 'tipo': 'tabla', 'columnas': columnas, 'clave': t.primary_key,
                'indices': [{'nombre': i.name, 'columnas': i.columns, 'unico': i.unique, 'auto': i.auto}
                            for i in t.indexes.values()],
                'apuntan_aqui': apuntan, 'comprobaciones': [sql for _, sql in t.checks],
                'filas': len(t.rows), 'definicion': '\n'.join(definicion)}

    # ------------------------------------------------------- ejecutar
    def ejecutar(self, sesion, texto, params=(), una_vez=False, max_filas=None):
        """Ejecuta un texto con varias órdenes. «una_vez»: si deja una transacción abierta, se deshace."""
        db_actual = self.servidor.abrir(sesion.base) if sesion.base else None
        if db_actual is not None and db_actual.in_transaction and db_actual.owner != sesion.id:
            raise BaseOcupada(f"la base está ocupada: {db_actual.owner_label} tiene una transacción abierta")
        try:
            parser = Parser(texto)
            ordenes = parser.parse_script_spans()
            if parser.param_count != len(params):
                raise SBDError(f"la orden tiene {parser.param_count} parámetro(s) «?» "
                               f"pero se pasaron {len(params)}")
        except SBDError as e:
            return {'resultados': [], 'error': str(e), 'tipo': 'incompleta' if isinstance(e, IncompleteInputError)
                    else 'sintaxis', 'orden_fallida': ocultar_claves(texto.strip()), 'incompleta':
                    isinstance(e, IncompleteInputError), 'base': sesion.base}
        resultados, fallo = [], None
        for orden, fuente in ordenes:
            inicio = time.perf_counter()
            try:
                r = sesion.ejecutar_orden(orden, params, fuente)
            except SBDError as e:
                fallo = {'error': str(e), 'tipo': tipo_de_error(e)[0], 'orden_fallida': ocultar_claves(fuente)}
                if isinstance(e, BaseOcupada) and not resultados:
                    raise  # no se ha hecho nada: se puede esperar y volver a intentar
                break
            resultados.append(self._resultado(ocultar_claves(fuente), r, time.perf_counter() - inicio,
                                              not isinstance(orden, READ_ONLY), max_filas))
        if una_vez and sesion.en_transaccion():
            sesion.cerrar()
            fallo = fallo or {'error': "la transacción no se cerró («confirmar» o «deshacer») y se ha deshecho: "
                                       "en una sola petición, envía «empezar … confirmar» juntos, o abre una sesión",
                              'tipo': 'transaccion_abierta', 'orden_fallida': None}
        respuesta = {'resultados': resultados, 'base': sesion.base, **self._estado_base(sesion)}
        if fallo:
            respuesta.update(fallo)
        return respuesta

    @staticmethod
    def _resultado(orden, r, segundos, cambia, max_filas):
        filas = r.rows if max_filas is None else r.rows[:max_filas]
        tipos = None
        if r.columns:
            tipos = []
            for i in range(len(r.columns)):
                valor = next((f[i] for f in r.rows if f[i] is not None), None)
                tipos.append(None if valor is None else type_name(valor))
        return {'orden': orden, 'columnas': r.columns, 'tipos': tipos,
                'filas': [[json_value(v) for v in row] for row in filas],
                'total': len(r.rows), 'recortado': len(filas) < len(r.rows), 'mensaje': r.message,
                'filas_afectadas': r.rowcount, 'ultimo_id': r.lastrowid, 'cambia': cambia,
                'ms': round(segundos * 1000, 2)}

    def _estado_base(self, sesion):
        if sesion.base is None or not self.servidor.existe(sesion.base):
            return {'transaccion': False, 'ocupada_por': None}
        db = self.servidor.abrir(sesion.base)
        mia = db.in_transaction and db.owner == sesion.id
        return {'transaccion': mia, 'ocupada_por': db.owner_label if db.in_transaction and not mia else None}

    def ejecutar_panel(self, usuario, base, texto, sid):
        sesion = self._sesion(usuario, base, f"panel:{usuario['nombre'] if usuario else ''}:{sid}" if sid else None)
        return self.ejecutar(sesion, texto, una_vez=not sid, max_filas=MAX_FILAS_PANEL)

    def ejecutar_api(self, usuario, es_local, cuerpo, directorio=None):
        sid = cuerpo.get('sesion')
        base = cuerpo.get('base') or None
        if sid:
            datos = self._api.get(sid)
            if datos is None or datos['usuario'] != (usuario['nombre'] if usuario else None):
                raise ApiError("esa sesión no existe o ha caducado; abre otra", HTTPStatus.NOT_FOUND, 'sesion')
            datos['ultima'] = time.time()
            base = base or datos['base']
        params = [json_param(p) for p in (cuerpo.get('parametros') or [])]
        etiqueta = 'consola' if es_local else f"{usuario['nombre']} · api"
        sesion = self._sesion(usuario, base, sid, permite_ficheros=es_local,
                              directorio=directorio if es_local else None, etiqueta=etiqueta)
        respuesta = self.ejecutar(sesion, str(cuerpo.get('orden', '')), params, una_vez=not sid)
        if sid:
            self._api[sid]['base'] = sesion.base
        return respuesta

    def importar(self, usuario, base, tabla, nombre_fichero, contenido, sid=None):
        sesion = self._sesion(usuario, base, sid)
        orden = f"importar {quote(os.path.basename(nombre_fichero))} en {quote_ident(tabla)}"
        inicio = time.perf_counter()
        try:
            sesion._permitir(Import(nombre_fichero, tabla))
            datos = base64.b64decode(contenido, validate=True)
            cabecera, filas = leer_fichero(nombre_fichero, datos)
            db = sesion.db
            with sesion.limitar(db):
                r = importar(db, tabla, cabecera, filas, session=sesion.id, label=sesion.etiqueta)
            self.servidor.auditar(sesion.etiqueta, base, orden, r.message)
        except (SBDError, ValueError) as e:
            mensaje = str(e) if isinstance(e, SBDError) else "el fichero no ha llegado bien; vuelve a subirlo"
            return {'resultados': [], 'error': mensaje, 'orden_fallida': orden, 'base': base,
                    **self._estado_base(sesion)}
        return {'resultados': [self._resultado(orden, r, time.perf_counter() - inicio, True, None)],
                'base': base, **self._estado_base(sesion)}

    # ------------------------------------------------------- volcado
    def volcar(self, usuario, base, tabla, formato):
        db = self._sesion(usuario, base).db
        if formato == 'sbd':
            return self._volcar_sbd(base, db, tabla), 'text/plain; charset=utf-8', 'sbd'
        if not tabla:
            raise ApiError("CSV y JSON vuelcan una sola tabla o vista")
        r = db.execute(f"buscar {quote_ident(tabla)}")
        if formato == 'csv':
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(r.columns)
            for row in r.rows:
                writer.writerow(['' if v is None else format_value(v) for v in row])
            return '﻿' + buffer.getvalue(), 'text/csv; charset=utf-8', 'csv'
        if formato == 'json':
            data = [{c: json_value(v) for c, v in zip(r.columns, row)} for row in r.rows]
            return json.dumps(data, ensure_ascii=False, indent=2), 'application/json; charset=utf-8', 'json'
        raise ApiError(f"formato desconocido: {formato}")

    def _volcar_sbd(self, base, db, tabla):
        lineas = [f"-- Volcado de SBD · base {base} · {time.strftime('%Y-%m-%d %H:%M')}",
                  f"-- Para restaurarlo: sbd -f {tabla or base}.sbd", '',
                  f"crear base {quote_ident(base)} si no existe", f"usar {quote_ident(base)}", '']
        if tabla and tabla in db.views and tabla not in db.tables:
            return '\n'.join(lineas + [f"crear vista {quote_ident(tabla)} = {db.views[tabla][0]}"]) + '\n'
        tablas = [db.table(tabla)] if tabla else list(db.tables.values())
        for t in tablas:
            lineas.append(t.to_sbd())
            lineas += [i.to_sbd(t.name) for i in t.indexes.values() if not i.auto]
            lineas.append('')
        for t in tablas:
            filas = t.scan()
            columnas = ', '.join(quote_ident(c) for c in t.colnames)
            for inicio in range(0, len(filas), 500):
                lineas.append(f"insertar en {quote_ident(t.name)} ({columnas})")
                lineas += ['  (' + ', '.join(quote(row[c]) for c in t.colnames) + ')'
                           for _, row in filas[inicio:inicio + 500]]
            if filas:
                lineas.append('')
        if not tabla:
            lineas += [f"crear vista {quote_ident(v)} = {sql}" for v, (sql, _) in db.views.items()]
        return '\n'.join(lineas) + '\n'


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    server_version = f'SBD/{__version__}'
    protocol_version = 'HTTP/1.1'

    # ------------------------------------------------------- utilidades
    def log_message(self, fmt, *args):
        pass  # el registro lo escribe registrar(), con más información

    def registrar(self, status, inicio, quien=''):
        if not self.server.silencio:
            ms = (time.perf_counter() - inicio) * 1000
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.ip()} {self.command} "
                  f"{urlparse(self.path).path} {int(status)} {ms:.1f}ms {quien}".rstrip(), file=sys.stderr, flush=True)

    def send_body(self, status, body, content_type, extra=None):
        data = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if self.server.tls:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self._status = status

    def send_json(self, data, status=HTTPStatus.OK, extra=None):
        self.send_body(status, json.dumps(data, ensure_ascii=False), 'application/json; charset=utf-8', extra)

    def cors(self):
        origen = self.headers.get('Origin')
        if origen and (origen in self.server.origenes or '*' in self.server.origenes):
            return {'Access-Control-Allow-Origin': origen, 'Vary': 'Origin',
                    'Access-Control-Allow-Headers': 'Authorization, Content-Type, SBD-Sesion',
                    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS', 'Access-Control-Max-Age': '600'}
        return {}

    def host_allowed(self):
        """Evita el DNS rebinding cuando el servidor solo escucha en este equipo."""
        if self.server.any_host:
            return True
        host = self.headers.get('Host') or ''
        host = host.split(']')[0] + ']' if host.startswith('[') else host.rsplit(':', 1)[0]
        return host.lower() in LOCAL_HOSTS or host.lower() in self.server.dominios

    def del_proxy(self):
        """La petición la reenvía el proxy de este equipo (solo cuenta con --proxy)."""
        return bool(self.server.dominios) and self.client_address[0] in ('127.0.0.1', '::1')

    def ip(self):
        """Quién llama de verdad. Detrás del proxy, la última IP que él añade a X-Forwarded-For
        (las anteriores las puede escribir cualquiera)."""
        if self.del_proxy():
            reenviada = (self.headers.get('X-Forwarded-For') or '').split(',')[-1].strip()
            if reenviada:
                return reenviada
        return self.client_address[0]

    def https(self):
        return self.server.tls or (self.del_proxy() and self.headers.get('X-Forwarded-Proto') == 'https')

    def testigo(self):
        cookie = SimpleCookie(self.headers.get('Cookie') or '')
        return cookie[COOKIE].value if COOKIE in cookie else None

    def sid_panel(self):
        sid = (self.headers.get('SBD-Sesion') or '').strip()
        return sid if re.fullmatch(r'[A-Za-z0-9_-]{16,64}', sid) else None

    def leer_cuerpo(self):
        if self._cuerpo is not None:  # si se reintenta por base ocupada, el cuerpo ya se leyó
            return self._cuerpo
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            # Exigir JSON obliga al navegador a pedir permiso (CORS): otra web no puede enviar órdenes.
            raise ApiError('se esperaba Content-Type: application/json', HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        if self.headers.get('Transfer-Encoding'):
            raise ApiError('Transfer-Encoding no está admitido')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ApiError('Content-Length no es válido') from None
        if length < 0:
            raise ApiError('Content-Length no es válido')
        if length > MAX_BODY:
            raise ApiError('la petición es demasiado grande (máximo 50 MB)', HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            # Un plazo total evita que un cliente lo prolongue enviando un byte cada poco.
            fin = time.monotonic() + 15
            partes, restante = [], length
            while restante:
                plazo = fin - time.monotonic()
                if plazo <= 0:
                    raise TimeoutError
                self.connection.settimeout(plazo)
                parte = self.rfile.read1(min(restante, 65536))
                if not parte:
                    break
                partes.append(parte)
                restante -= len(parte)
            datos = b''.join(partes)
            if len(datos) != length:
                raise ApiError('cuerpo incompleto')
            cuerpo = json.loads(datos or b'{}')
        except TimeoutError:
            self.close_connection = True
            raise ApiError('tiempo de lectura agotado', HTTPStatus.REQUEST_TIMEOUT) from None
        except ValueError:
            raise ApiError('el cuerpo no es JSON válido') from None
        finally:
            self.connection.settimeout(15)
        if not isinstance(cuerpo, dict):
            raise ApiError('el cuerpo tiene que ser un objeto JSON')
        self._cuerpo = cuerpo
        return cuerpo

    def atender(self, funcion):
        """Llama a la función con el núcleo bloqueado; si la base está ocupada por otra sesión, espera un poco."""
        # El mismo objeto atiende todas las peticiones de una conexión (keep-alive): nada se arrastra.
        self._cuerpo = None
        self._status = 0
        inicio = time.perf_counter()
        quien = ''
        extra = self.cors() if urlparse(self.path).path.startswith('/api/v1/') else {}
        try:
            if not self.host_allowed():
                raise ApiError('Host no permitido', HTTPStatus.FORBIDDEN, 'host')
            if self.command == 'POST':
                self.leer_cuerpo()  # nunca esperar a la red con el cerrojo del motor adquirido
            limite = time.monotonic() + ESPERA_OCUPADA
            while True:
                try:
                    with self.server.nucleo.lock:
                        respuesta = funcion()
                    break
                except BaseOcupada:
                    if time.monotonic() > limite:
                        raise
                    time.sleep(0.05)
            if respuesta is None:
                return
            if isinstance(respuesta, tuple):  # (cuerpo, tipo, cabeceras): descarga
                cuerpo, content_type, cabeceras = respuesta
                self.send_body(HTTPStatus.OK, cuerpo, content_type, {**extra, **cabeceras})
            else:
                datos, cabeceras = respuesta if isinstance(respuesta, list) else (respuesta, {})
                status = HTTPStatus.OK
                if isinstance(datos, dict) and datos.get('error') and urlparse(self.path).path.startswith('/api/v1/'):
                    status = tipo_de_error_por_nombre(datos.get('tipo'))
                self.send_json(datos, status, {**extra, **cabeceras})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except (ApiError, SBDError) as e:
            if self.command == 'POST' and self._cuerpo is None:
                self.close_connection = True
            tipo, status = tipo_de_error(e)
            try:
                self.send_json({'error': str(e), 'tipo': tipo, 'entrar': tipo == 'entrar'}, status, extra)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
        except Exception:
            print(f"✕ error interno en {self.command} {self.path}:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            self.send_json({'error': 'error interno del servidor (está anotado en su registro)', 'tipo': 'interno'},
                           HTTPStatus.INTERNAL_SERVER_ERROR, extra)
        finally:
            self.registrar(self._status, inicio, quien)

    # ------------------------------------------------------- rutas
    def do_OPTIONS(self):
        extra = self.cors()
        self.send_body(HTTPStatus.NO_CONTENT if extra else HTTPStatus.FORBIDDEN, b'', 'text/plain', extra)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        n = self.server.nucleo
        if url.path in STATIC_FILES:
            def estatico():
                nombre, content_type = STATIC_FILES[url.path]
                with open(os.path.join(STATIC_DIR, nombre), 'rb') as f:
                    return f.read(), content_type, {}
            return self.atender(estatico)
        rutas = {
            '/salud': lambda: {'ok': True, 'version': __version__, 'bases': len(n.servidor.nombres())},
            '/api/estado': lambda: n.estado(self.testigo()),
            '/api/cuenta': lambda: n.cuenta(n.usuario_web(self.testigo())),
            '/api/ajustes': lambda: n.ajustes(n.usuario_web(self.testigo())),
            '/api/bases': lambda: n.bases(n.usuario_web(self.testigo())),
            '/api/base': lambda: n.base(n.usuario_web(self.testigo()), q.get('nombre', ''), self._sid_web()),
            '/api/tabla': lambda: n.tabla(n.usuario_web(self.testigo()), q.get('base', ''), q.get('nombre', '')),
            '/api/volcar': lambda: self._volcar(n, q),
            '/api/v1/catalogo': lambda: n.catalogo(n.usuario_api(self.headers.get('Authorization'))[0],
                                                   q.get('base') or None),
        }
        self.atender(rutas.get(url.path, no_encontrada))

    def _sid_web(self):
        sid = self.sid_panel()
        if not sid:
            return None
        datos = self.server.nucleo._web.get(self.testigo() or '')
        return f"panel:{datos[0] if datos else ''}:{sid}"

    def _volcar(self, n, q):
        base, tabla = q.get('base', ''), q.get('tabla') or None
        cuerpo, content_type, ext = n.volcar(n.usuario_web(self.testigo()), base, tabla, q.get('formato', 'sbd'))
        return cuerpo, content_type, {'Content-Disposition': f"attachment; filename*=UTF-8''{url_quote(tabla or base)}.{ext}"}

    def do_POST(self):
        n = self.server.nucleo
        ruta = urlparse(self.path).path

        def manejar():
            cuerpo = self.leer_cuerpo()
            if ruta in ('/api/entrar', '/api/registrar'):
                hacer = n.registrar if ruta == '/api/registrar' else n.entrar
                testigo, usuario = hacer(cuerpo.get('usuario', ''), cuerpo.get('clave', ''), self.ip())
                galleta = (f"{COOKIE}={testigo}; HttpOnly; SameSite=Strict; Path=/; Max-Age={DURACION_SESION_WEB}"
                           + ('; Secure' if self.https() else ''))
                return [{'usuario': usuario}, {'Set-Cookie': galleta}]
            if ruta == '/api/ajustes':
                return n.ajustes(n.usuario_web(self.testigo()), cuerpo)
            if ruta == '/api/salir':
                n.salir(self.testigo(), self.sid_panel())
                return [{'ok': True}, {'Set-Cookie': f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"}]
            if ruta == '/api/ejecutar':
                return n.ejecutar_panel(n.usuario_web(self.testigo()), cuerpo.get('base') or None,
                                        str(cuerpo.get('orden', '')), self.sid_panel())
            if ruta == '/api/importar':
                return n.importar(n.usuario_web(self.testigo()), cuerpo.get('base') or '', str(cuerpo.get('tabla', '')),
                                  str(cuerpo.get('fichero', 'datos.csv')), str(cuerpo.get('contenido', '')),
                                  self._sid_web())
            usuario, local = n.usuario_api(self.headers.get('Authorization'))
            if ruta == '/api/v1/ejecutar':
                return n.ejecutar_api(usuario, local, cuerpo, self.headers.get('SBD-Directorio'))
            if ruta == '/api/v1/sesiones':
                return {'sesion': n.crear_sesion_api(usuario, local, cuerpo.get('base') or None)}
            raise ApiError('ruta no encontrada', HTTPStatus.NOT_FOUND, 'ruta')
        self.atender(manejar)

    def do_DELETE(self):
        n = self.server.nucleo
        ruta = urlparse(self.path).path

        def manejar():
            m = re.fullmatch(r'/api/v1/sesiones/([A-Za-z0-9:_-]+)', ruta)
            if not m:
                raise ApiError('ruta no encontrada', HTTPStatus.NOT_FOUND, 'ruta')
            usuario, _ = n.usuario_api(self.headers.get('Authorization'))
            n.cerrar_sesion_api(usuario, m.group(1))
            return {'ok': True}
        self.atender(manejar)


def no_encontrada():
    raise ApiError('ruta no encontrada', HTTPStatus.NOT_FOUND, 'ruta')


def tipo_de_error_por_nombre(tipo):
    return {'ocupada': HTTPStatus.CONFLICT, 'permiso': HTTPStatus.FORBIDDEN}.get(tipo, HTTPStatus.BAD_REQUEST)


class ServidorHTTP(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, servidor, any_host=False, silencio=True, origenes=(), contexto_tls=None, copias=0,
                 dominios=()):
        super().__init__(address, Handler)
        self.nucleo = Nucleo(servidor, copias)
        if any_host or dominios:
            servidor.proteger_ultimo_usuario = True
        self.any_host = any_host
        self.dominios = {d.lower() for d in dominios}
        self.silencio = silencio
        self.origenes = set(origenes)
        self.tls = contexto_tls is not None
        if contexto_tls is not None:
            self.socket = contexto_tls.wrap_socket(self.socket, server_side=True)

    def mantener(self, cada=10):
        """Hilo de mantenimiento: transacciones abandonadas, sesiones viejas y copia diaria."""
        def bucle():
            while not self._parar.wait(cada):
                self.nucleo.mantenimiento()
        self._parar = threading.Event()
        hilo = threading.Thread(target=bucle, name='sbd-mantenimiento', daemon=True)
        hilo.start()
        return hilo

    def server_close(self):
        if hasattr(self, '_parar'):
            self._parar.set()
        super().server_close()


PanelServer = ServidorHTTP  # nombre antiguo


def escribir_fichero_servidor(directorio, url, clave):
    ruta = os.path.join(directorio, FICHERO_SERVIDOR)
    with open(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as f:
        json.dump({'url': url, 'clave': clave, 'proceso': os.getpid()}, f)
    return ruta


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sbd servidor', description='Servidor de SBD: panel, API y consola')
    parser.add_argument('datos', nargs='?', default='datos', help='carpeta de las bases (por defecto ./datos)')
    parser.add_argument('--host', default='127.0.0.1', help='interfaz de red (por defecto solo este equipo)')
    parser.add_argument('--puerto', type=int, default=8080)
    parser.add_argument('--certificado', help='certificado TLS (PEM) para servir HTTPS')
    parser.add_argument('--clave-privada', help='clave privada del certificado (PEM)')
    parser.add_argument('--origenes', default='', help='webs que pueden llamar a la API desde el navegador, '
                                                        'separadas por comas (https://miapp.es)')
    parser.add_argument('--limite-segundos', type=float, default=30, help='lo más que puede tardar una orden')
    parser.add_argument('--copias', type=int, default=7, help='días de copias de seguridad que se guardan (0: ninguna)')
    parser.add_argument('--silencio', action='store_true', help='no escribe una línea por petición')
    parser.add_argument('--proxy', default='', metavar='DOMINIOS',
                        help='está detrás de un proxy con HTTPS en este equipo que atiende estos dominios '
                             '(separados por comas): los acepta, y toma la IP real de X-Forwarded-For')
    parser.add_argument('--abrir', action='store_true', help='abre el navegador al arrancar')
    args = parser.parse_args(argv)
    dominios = [d.strip() for d in args.proxy.split(',') if d.strip()]

    try:
        servidor = Servidor(args.datos, quien='servidor', limite_segundos=args.limite_segundos or None)
    except SBDError as e:
        print(f"✕ {e}", file=sys.stderr)
        return 1
    expuesto = args.host not in ('127.0.0.1', 'localhost', '::1')
    if (expuesto or dominios) and not servidor.hay_usuarios():
        print("✕ Para abrir el servidor a la red hace falta al menos un usuario. Créalo así:\n"
              f"   sbd {args.datos} -e 'crear usuario admin clave \"…\" puede todo'", file=sys.stderr)
        servidor.cerrar()
        return 1
    contexto = None
    if args.certificado:
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.minimum_version = ssl.TLSVersion.TLSv1_2
        contexto.load_cert_chain(args.certificado, args.clave_privada)
    try:
        http = ServidorHTTP((args.host, args.puerto), servidor, any_host=expuesto, silencio=args.silencio,
                            origenes=[o.strip() for o in args.origenes.split(',') if o.strip()],
                            contexto_tls=contexto, copias=args.copias, dominios=dominios)
    except OSError as e:
        print(f"✕ no se puede escuchar en {args.host}:{args.puerto}: {e.strerror}", file=sys.stderr)
        servidor.cerrar()
        return 1
    esquema = 'https' if contexto else 'http'
    url = f"{esquema}://{'127.0.0.1' if args.host in ('0.0.0.0', '', '::') else args.host}:{args.puerto}"
    ruta_info = escribir_fichero_servidor(servidor.directorio, url, http.nucleo.clave_local)
    http.nucleo.mantenimiento()
    http.mantener()
    print(f"SBD {__version__} — datos en {servidor.directorio}", file=sys.stderr)
    print(f"Panel en {url}/   ·   API en {url}/api/v1/   (Ctrl+C para parar)", file=sys.stderr)
    if servidor.hay_usuarios():
        print("Hay usuarios: el panel pide entrar y la API pide clave.", file=sys.stderr)
    if dominios:
        print(f"Detrás del proxy de {', '.join('https://' + d for d in dominios)}", file=sys.stderr)
    if expuesto and not contexto:
        print("Aviso: accesible desde la red sin HTTPS; las claves viajarían sin cifrar. "
              "Usa --certificado/--clave-privada o un proxy con HTTPS.", file=sys.stderr)
    if args.abrir:
        webbrowser.open(url + '/')
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        print(file=sys.stderr)
    finally:
        http.server_close()
        with http.nucleo.lock:
            servidor.cerrar()
        if os.path.exists(ruta_info):
            os.remove(ruta_info)
    return 0
