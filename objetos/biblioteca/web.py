"""El Baúl en la web: un explorador del grafo de objetos, guardado en un `sbd servidor`.

    SBD_CLAVE=sbd_… python3 -m biblioteca.web --sbd http://127.0.0.1:8090 --base objetos --puerto 8099

Mirar es público; prestar y devolver exige haber entrado en el panel de SBD con permiso «todo» (se le
pregunta al propio servidor de SBD). Cada respuesta dice qué llamada se hizo al Baúl y qué órdenes
recibió SBD.
"""
import argparse
import datetime
import json
import mimetypes
import os
import sys
import threading
import traceback
import urllib.error
import urllib.request
from decimal import Decimal
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import baul
from baul.objetos import Persistente, Valor, estado as atributos_de

from . import ejemplo
from .modelos import Prestamo, Publicacion, Socio
from .servicio import ErrorBiblioteca, Servicio

ESTATICO = Path(__file__).with_name('estatico')
ARCHIVOS = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}
MAX_CUERPO = 4096
CAMPOS_PRIVADOS = {'email', 'calle', 'codigo_postal'}  # a la vista de cualquiera, solo los ve quien administra


class Rechazo(Exception):
    def __init__(self, mensaje, estado=HTTPStatus.BAD_REQUEST):
        super().__init__(mensaje)
        self.estado = estado


def resumen(obj):
    for campo in ('titulo', 'nombre'):
        if isinstance(getattr(obj, campo, None), str):
            return getattr(obj, campo)
    if isinstance(obj, Prestamo):
        return f"{obj.publicacion.titulo} → {obj.socio.nombre}"
    return ''


def enlace(obj):
    return {'ref': obj.oid, 'clase': type(obj).__name__, 'resumen': resumen(obj)}


def enlaces(bd, objetos):
    """Los enlaces de una lista. Antes carga de una vez los objetos (y, de los préstamos, su libro y su
    socio, que salen en el resumen): así SBD recibe dos consultas, no una por objeto."""
    objetos = list(objetos)
    bd.cargar(objetos)
    prestamos = [o for o in objetos if isinstance(o, Prestamo)]
    if prestamos:
        bd.cargar([*(p.publicacion for p in prestamos), *(p.socio for p in prestamos)])
    return [enlace(o) for o in objetos]


def describir(valor, puede, nombre=''):
    """Un valor del objeto, listo para pintarlo: las referencias se convierten en enlaces."""
    if nombre in CAMPOS_PRIVADOS and not puede and isinstance(valor, str):
        return {'valor': valor[:1] + '•••' if valor else ''}
    if isinstance(valor, Persistente):
        return enlace(valor)
    if isinstance(valor, Valor):
        return {'valor_de': type(valor).__name__,
                'campos': [{'nombre': k, **describir(v, puede, k)} for k, v in atributos_de(valor).items()]}
    if isinstance(valor, (list, tuple, set, frozenset)):
        elementos = sorted(valor, key=repr) if isinstance(valor, (set, frozenset)) else list(valor)
        return {'lista': [describir(v, puede) for v in elementos],
                'tipo': {list: 'lista', tuple: 'tupla'}.get(type(valor), 'conjunto')}
    if isinstance(valor, dict):
        return {'dicc': [{'nombre': str(k), **describir(v, puede, str(k))} for k, v in valor.items()]}
    if isinstance(valor, Decimal):
        return {'valor': format(valor, 'f'), 'tipo': 'Decimal'}
    if isinstance(valor, (datetime.date, datetime.datetime)):
        return {'valor': valor.isoformat(), 'tipo': type(valor).__name__}
    return {'valor': valor, 'tipo': type(valor).__name__}


def tapar_yaml(texto, puede):
    if puede or texto is None:
        return texto
    return '\n'.join(f"{l.split(':', 1)[0]}: •••" if l.strip().split(':', 1)[0] in CAMPOS_PRIVADOS else l
                     for l in texto.splitlines())


class Aplicacion:
    def __init__(self, url, panel, descripcion):
        self.panel = panel
        self.descripcion = descripcion
        self.cerrojo = threading.Lock()
        self.ordenes = []
        self.bd = baul.abrir(url)
        self.bd._almacen.escuchar = lambda texto, params: self.ordenes.append({'sbd': texto, 'valores': [str(p) for p in params]})

    def quien(self, cabecera_cookie):
        cookie = SimpleCookie(cabecera_cookie or '')
        cabeceras = {'Cookie': f"sbd_sesion={cookie['sbd_sesion'].value}"} if 'sbd_sesion' in cookie else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(f'{self.panel}/api/estado', headers=cabeceras), timeout=5) as r:
                estado = json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            return None, False
        if estado.get('sin_usuarios'):
            return None, True
        usuario = estado.get('usuario')
        return (usuario['nombre'], usuario['permiso'] == 'todo') if usuario else (None, False)

    def _con(self, hacer):
        """Cada petición ve lo último guardado: otros programas pueden estar usando la misma base."""
        with self.cerrojo:
            self.ordenes = []
            self.bd.refrescar()
            resultado = hacer()
            resultado['ordenes'] = self.ordenes
            return resultado

    # ---------------------------------------------------------------- lectura
    def clases(self, cookie):
        def hacer():
            cuentas = {}
            for o in self.bd.todos():   # no carga ningún objeto: su clase se sabe desde que se abre la base
                cuentas[type(o).__name__] = cuentas.get(type(o).__name__, 0) + 1
            return {'clases': [{'clase': c, 'objetos': n} for c, n in sorted(cuentas.items())],
                    'raices': [{'nombre': n, **e} for n, e in zip(self.bd.raiz, enlaces(self.bd, self.bd.raiz.values()))],
                    'llamada': 'bd.todos()'}
        return self._con(hacer)

    def lista(self, clase, cookie):
        cls = baul.objetos.CLASES.get(clase)
        if cls is None or not issubclass(cls, Persistente):
            raise Rechazo('no hay ninguna clase con ese nombre', HTTPStatus.NOT_FOUND)
        return self._con(lambda: {'objetos': enlaces(self.bd, self.bd.todos(cls)), 'llamada': f'bd.todos({clase})'})

    def objeto(self, oid, cookie):
        _, puede = self.quien(cookie)

        def hacer():
            obj = self.bd.obtener(oid)
            if obj is None:
                raise Rechazo(f'no hay ningún objeto {oid}', HTTPStatus.NOT_FOUND)
            lo_usan = enlaces(self.bd, self.bd.quien_usa(obj))
            enlaces(self.bd, self.bd.referencias(obj))   # los que se enseñan como enlaces, de una vez
            return {'oid': obj.oid, 'clase': type(obj).__name__, 'herencia': [c.__name__ for c in type(obj).__mro__[1:]
                                                                              if c.__module__ != 'builtins' and c is not Persistente],
                    'version': obj._version, 'resumen': resumen(obj),
                    'atributos': [{'nombre': k, **describir(v, puede, k)} for k, v in atributos_de(obj).items()],
                    'lo_usan': lo_usan, 'yaml': tapar_yaml(self.bd.como_yaml(obj), puede),
                    'acciones': self._acciones(obj, puede), 'llamada': f'bd.obtener({oid})'}
        return self._con(hacer)

    def _acciones(self, obj, puede):
        if not puede:
            return []
        if isinstance(obj, Publicacion) and obj.disponibles > 0:
            socios = self.bd.todos(Socio)
            self.bd.cargar(socios)
            return [{'accion': 'prestar', 'socios': [enlace(s) for s in socios if s.activo]}]
        if isinstance(obj, Prestamo) and obj.devuelto is None:
            return [{'accion': 'devolver'}]
        return []

    CONSULTAS = {
        'autor': ('Libro', 'autor__nombre__contiene'), 'ciudad': ('Socio', 'direccion__ciudad'),
        'genero': ('Publicacion', 'generos__contiene'), 'barato': ('Publicacion', 'precio__menor'),
    }

    def consulta(self, tipo, valor, cookie):
        if tipo not in self.CONSULTAS:
            raise Rechazo('no conozco esa consulta')
        if not isinstance(valor, str) or not 0 < len(valor) <= 80:
            raise Rechazo('escribe qué buscar (80 caracteres como mucho)')
        clase, campo = self.CONSULTAS[tipo]
        if tipo == 'barato':
            try:
                dato = Decimal(valor.replace(',', '.'))
            except ArithmeticError:
                raise Rechazo('escribe un precio, como 12,50') from None
        else:
            dato = valor
        cls = baul.objetos.CLASES[clase]
        return self._con(lambda: {'objetos': enlaces(self.bd, self.bd.buscar(cls, **{campo: dato})),
                                  'llamada': f"bd.buscar({clase}, {campo}={dato!r})"})

    # -------------------------------------------------------------- escritura
    def escribir(self, ruta, cuerpo, cookie):
        _, puede = self.quien(cookie)
        if not puede:
            raise Rechazo("para cambiar objetos hay que entrar en el panel de SBD con permiso «todo»", HTTPStatus.FORBIDDEN)

        def hacer():
            s = Servicio(self.bd)
            try:
                if ruta == '/api/prestar':
                    socio, pub = self._de(cuerpo.get('socio'), Socio), self._de(cuerpo.get('publicacion'), Publicacion)
                    p = s.prestar(socio, pub)[0]
                    return {'mensaje': f"«{pub.titulo}» prestado a {socio.nombre} hasta el {p.vence}: es el préstamo {p.oid}.",
                            'oid': p.oid, 'llamada': f'servicio.prestar(bd.obtener({socio.oid}), bd.obtener({pub.oid}))'}
                p = self._de(cuerpo.get('prestamo'), Prestamo)
                s.devolver(p)
                return {'mensaje': f"«{p.publicacion.titulo}» devuelto." + (f" Multa: {format(p.multa, 'f')} €." if p.multa else ' Sin multa.'),
                        'oid': p.oid, 'llamada': f'servicio.devolver(bd.obtener({p.oid}))'}
            except (ErrorBiblioteca, baul.ErrorBaul) as e:
                return {'error': str(e), 'llamada': ruta}
        return self._con(hacer)

    def _de(self, oid, clase):
        if isinstance(oid, bool) or not isinstance(oid, int):
            raise Rechazo('falta el objeto')
        obj = self.bd.obtener(oid)
        if not isinstance(obj, clase):
            raise Rechazo(f'el objeto {oid} no es un {clase.__name__.lower()}')
        return obj


class Atencion(BaseHTTPRequestHandler):
    server_version = 'Baul'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, formato, *args):
        pass

    def responder(self, estado, cuerpo, tipo='application/json; charset=utf-8'):
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo, ensure_ascii=False).encode('utf-8')
        self.send_response(estado)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(datos)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                         "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(datos)

    def atender(self, hacer):
        try:
            self.responder(HTTPStatus.OK, hacer())
        except Rechazo as e:
            self.responder(e.estado, {'error': str(e)})
        except baul.ErrorBaul as e:
            print(f"✕ {e}", file=sys.stderr, flush=True)
            self.responder(HTTPStatus.SERVICE_UNAVAILABLE, {'error': 'la base de objetos no responde ahora mismo'})
        except Exception:
            traceback.print_exc()
            self.responder(HTTPStatus.INTERNAL_SERVER_ERROR, {'error': 'error interno'})

    def do_GET(self):
        url = urlparse(self.path)
        app = self.server.app
        cookie = self.headers.get('Cookie')
        q = parse_qs(url.query)
        if url.path in ARCHIVOS:
            nombre = ARCHIVOS[url.path]
            return self.responder(HTTPStatus.OK, (ESTATICO / nombre).read_bytes(), (mimetypes.guess_type(nombre)[0] or 'text/plain') + '; charset=utf-8')
        if url.path == '/api/estado':
            def estado():
                nombre, puede = app.quien(cookie)
                return {'usuario': nombre, 'puede': puede, 'conexion': app.descripcion}
            return self.atender(estado)
        if url.path == '/api/clases':
            return self.atender(lambda: app.clases(cookie))
        if url.path == '/api/lista':
            return self.atender(lambda: app.lista(q.get('clase', [''])[0], cookie))
        if url.path == '/api/consulta':
            return self.atender(lambda: app.consulta(q.get('tipo', [''])[0], q.get('valor', [''])[0], cookie))
        if url.path.startswith('/api/objeto/'):
            numero = url.path.rsplit('/', 1)[1]
            if not numero.isdigit() or len(numero) > 12:
                return self.responder(HTTPStatus.NOT_FOUND, {'error': 'no existe'})
            return self.atender(lambda: app.objeto(int(numero), cookie))
        self.responder(HTTPStatus.NOT_FOUND, {'error': 'no existe'})

    def do_POST(self):
        app = self.server.app

        def hacer():
            ruta = urlparse(self.path).path
            if ruta not in ('/api/prestar', '/api/devolver'):
                raise Rechazo('no existe', HTTPStatus.NOT_FOUND)
            if not (self.headers.get('Content-Type') or '').startswith('application/json'):
                raise Rechazo('se esperaba JSON', HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            largo = int(self.headers.get('Content-Length') or 0)
            if largo > MAX_CUERPO:
                raise Rechazo('demasiado grande', HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                cuerpo = json.loads(self.rfile.read(largo) or b'{}')
            except ValueError:
                raise Rechazo('el JSON no es válido') from None
            if not isinstance(cuerpo, dict):
                raise Rechazo('el JSON no es válido')
            return app.escribir(ruta, cuerpo, self.headers.get('Cookie'))
        self.atender(hacer)


def crear_servidor(direccion, url, panel, descripcion):
    servidor = ThreadingHTTPServer(direccion, Atencion)
    servidor.daemon_threads = True
    servidor.app = Aplicacion(url, panel, descripcion)
    if not servidor.app.bd.raiz.get('biblioteca'):
        ejemplo.cargar(servidor.app.bd)
    return servidor


def main(argv=None):
    p = argparse.ArgumentParser(description='El Baúl en la web, contra un servidor de SBD')
    p.add_argument('--sbd', default='http://127.0.0.1:8090')
    p.add_argument('--base', default='objetos')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--puerto', type=int, default=8099)
    args = p.parse_args(argv)
    clave = os.environ.get('SBD_CLAVE')
    if not clave:
        print('✕ falta la clave de API en la variable SBD_CLAVE', file=sys.stderr)
        return 1
    u = urlparse(args.sbd)
    try:
        servidor = crear_servidor((args.host, args.puerto), f'sbd+{u.scheme}://:{clave}@{u.netloc}/{args.base}',
                                  args.sbd.rstrip('/'), f'servidor de SBD · base «{args.base}»')
    except baul.ErrorBaul as e:
        print(f"✕ {e}", file=sys.stderr)
        return 1
    print(f"Baúl en http://{args.host}:{args.puerto}/, conectado a {args.sbd}", file=sys.stderr, flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
        servidor.app.bd.cerrar()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
