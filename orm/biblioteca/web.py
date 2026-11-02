"""La biblioteca en la web: el ORM trabajando contra un `sbd servidor`, y a la vista lo que hace.

    SBD_CLAVE=sbd_… python3 -m biblioteca.web --sbd http://127.0.0.1:8090 --puerto 8097

Mirar es público; cambiar algo exige haber entrado en el panel de SBD con permiso «todo». La sesión
del panel se comprueba preguntándole al propio servidor de SBD, así que no hay otro sistema de usuarios.
Cada respuesta lleva la «ruta» de la acción: qué llamada se hizo, qué SQL generó SQLAlchemy y qué orden
de SBD salió de él.
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import event, exc, select

import sqlalchemy_sbd.sql as traductor

from . import bd, ejemplo
from .modelos import Libro, Socio
from .servicio import Biblioteca, ErrorBiblioteca

ESTATICO = Path(__file__).with_name('estatico')
ARCHIVOS = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}
MAX_CUERPO = 16 * 1024


class Rechazo(Exception):
    def __init__(self, mensaje, estado=HTTPStatus.BAD_REQUEST):
        super().__init__(mensaje)
        self.estado = estado


def texto(valor, que, maximo, obligatorio=True):
    if not isinstance(valor, str) or (obligatorio and not valor.strip()):
        raise Rechazo(f"falta {que}")
    if len(valor) > maximo:
        raise Rechazo(f"{que} es demasiado largo (máximo {maximo})")
    return valor.strip()


def entero(valor, que, minimo=1, maximo=1_000_000):
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        raise Rechazo(f"{que} no es válido")
    return valor


def ocultar_email(email):
    """Los datos están a la vista de cualquiera: el correo completo solo lo ve quien administra."""
    nombre, _, dominio = email.partition('@')
    return f"{nombre[:1]}•••@{dominio}"


def valor_json(v):
    if isinstance(v, Decimal):
        return format(v, 'f')
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v


def filas(resultado):
    return [[valor_json(v) for v in fila] for fila in resultado]


class Aplicacion:
    """Una petición cada vez: la «ruta» que se enseña es solo la de esa petición."""

    def __init__(self, motor, panel):
        self.motor = motor
        self.panel = panel  # la dirección del servidor de SBD, para comprobar quién ha entrado
        self.sesiones = bd.fabrica_sesiones(motor)
        self.cerrojo = threading.Lock()
        self.ruta = []
        event.listen(motor, 'before_cursor_execute', self._al_ejecutar)
        event.listen(motor, 'commit', lambda c: self.ruta.append({'sql': 'COMMIT', 'valores': [], 'sbd': 'confirmar'}))
        event.listen(motor, 'rollback', lambda c: self._al_deshacer())

    def _al_ejecutar(self, conn, cursor, sql, parametros, contexto, varios):
        sbd, valores = sql, list(parametros) if not varios else []
        try:
            if traductor.es_sql(sql):
                sbd, orden = traductor.a_texto_sbd(sql)
                valores = [parametros[i] for i in orden] if not varios else []
        except (traductor.ErrorSQL, IndexError):
            pass
        self.ruta.append({'sql': ' '.join(sql.split()), 'sbd': ' '.join(sbd.split()),
                          'valores': [str(valor_json(v)) for v in valores]})

    def _al_deshacer(self):
        # SQLAlchemy cierra cada lectura con un rollback que no hace nada: solo cuenta si había cambios
        if any(p['sql'].split()[0] in ('INSERT', 'UPDATE', 'DELETE') for p in self.ruta):
            self.ruta.append({'sql': 'ROLLBACK', 'valores': [], 'sbd': 'deshacer'})

    # ----------------------------------------------------------------- quién
    def quien(self, cabecera_cookie):
        """(nombre, puede cambiar datos), preguntándoselo al servidor de SBD."""
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

    # ---------------------------------------------------------------- lectura
    def libros(self, b, q, puede):
        return [{'id': x.id, 'titulo': x.titulo, 'autor': x.autor.nombre, 'anio': x.anio, 'precio': valor_json(x.precio),
                 'disponibles': x.disponibles, 'ejemplares': x.ejemplares, 'generos': [g.nombre for g in x.generos]}
                for x in b.buscar_libros(q)]

    def socios(self, b, q, puede):
        return [{'id': s.id, 'nombre': s.nombre, 'email': s.email if puede else ocultar_email(s.email),
                 'alta': s.alta.isoformat(), 'libros': len(s.prestamos_abiertos())}
                for s in b.sesion.scalars(select(Socio).order_by(Socio.id))]

    def prestamos(self, b, q, puede):
        return [{'id': p.id, 'socio': p.socio.nombre, 'libro': p.libro.titulo, 'fecha': p.fecha.isoformat(),
                 'vence': p.vence.isoformat()} for p in b.prestamos_abiertos()]

    def informes(self, b, q, puede):
        morosos = [[f[0], f[1] if puede else ocultar_email(f[1]), f[2], f[3]] for f in filas(b.morosos())]
        return [
            {'titulo': 'Los más prestados', 'columnas': ['título', 'autor', 'préstamos'], 'filas': filas(b.mas_prestados())},
            {'titulo': 'Socios con libros vencidos', 'columnas': ['nombre', 'email', 'vencidos', 'desde'], 'filas': morosos},
            {'titulo': 'Multas por autor', 'columnas': ['autor', 'multas', 'estado'], 'filas': filas(b.recaudado_por_autor())},
            {'titulo': 'Socios que nunca han pedido nada', 'columnas': ['nombre'], 'filas': [[n] for n in b.socios_sin_prestamos()]},
            {'titulo': 'Los más caros de cada género', 'columnas': ['género', 'título', 'precio', 'puesto'], 'filas': filas(b.ranking_por_genero())},
        ]

    LECTURAS = {'/api/libros': ('libros', 'biblioteca.buscar_libros({q})'), '/api/socios': ('socios', 'sesion.scalars(select(Socio))'),
                '/api/prestamos': ('prestamos', 'biblioteca.prestamos_abiertos()'), '/api/informes': ('informes', 'biblioteca.informes()')}

    def leer(self, ruta, q, cookie):
        metodo, llamada = self.LECTURAS[ruta]
        q = texto(q, 'la búsqueda', 100, obligatorio=False)
        _, puede = self.quien(cookie)
        with self.cerrojo:
            self.ruta = []
            with self.sesiones() as sesion:
                datos = getattr(self, metodo)(Biblioteca(sesion), q, puede)
            return {'datos': datos, 'llamada': llamada.format(q=repr(q) if q else ''), 'ruta': self.ruta}

    # -------------------------------------------------------------- escritura
    def prestar(self, b, c):
        socio = entero(c.get('socio'), 'el socio')
        libros = c.get('libros')
        if not isinstance(libros, list) or not 1 <= len(libros) <= 3:
            raise Rechazo("elige entre uno y tres libros")
        libros = [entero(x, 'el libro') for x in libros]
        hechos = b.prestar(socio, libros)
        mensaje = ', '.join(f"«{p.libro.titulo}»" for p in hechos)
        return f"{mensaje}: a {hechos[0].socio.nombre}, hasta el {hechos[0].vence}.", f"biblioteca.prestar({socio}, {libros})"

    def devolver(self, b, c):
        pid = entero(c.get('prestamo'), 'el préstamo')
        p = b.devolver(pid)
        return (f"«{p.libro.titulo}» devuelto." + (f" Multa: {valor_json(p.multa)} €." if p.multa else ' Sin multa.'),
                f"biblioteca.devolver({pid})")

    def alta_socio(self, b, c):
        s = b.alta_socio(texto(c.get('nombre'), 'el nombre', 80), texto(c.get('email'), 'el correo', 120))
        return f"Socio {s.id} creado: {s.nombre}.", f"biblioteca.alta_socio({s.nombre!r}, …)"

    def alta_libro(self, b, c):
        try:
            precio = Decimal(str(c.get('precio') or '0').replace(',', '.'))
        except InvalidOperation:
            raise Rechazo("el precio no es un número") from None
        if not Decimal('0') <= precio < Decimal('1000000'):
            raise Rechazo("el precio no es válido")
        generos = [texto(g, 'el género', 40) for g in str(c.get('generos') or '').split(',') if g.strip()][:6]
        x = b.alta_libro(texto(c.get('titulo'), 'el título', 160), texto(c.get('isbn'), 'el ISBN', 20), texto(c.get('autor'), 'el autor', 80),
                         precio=precio, ejemplares=entero(c.get('ejemplares', 1), 'los ejemplares', 1, 999), generos=generos)
        return f"Libro {x.id} creado: {x.titulo}.", f"biblioteca.alta_libro({x.titulo!r}, …)"

    ESCRITURAS = {'/api/prestar': 'prestar', '/api/devolver': 'devolver', '/api/socios': 'alta_socio', '/api/libros': 'alta_libro'}

    def escribir(self, ruta, cuerpo, cookie):
        nombre, puede = self.quien(cookie)
        if not puede:
            raise Rechazo("para cambiar datos hay que entrar en el panel de SBD con permiso «todo»", HTTPStatus.FORBIDDEN)
        with self.cerrojo:
            self.ruta = []
            with self.sesiones() as sesion:
                try:
                    mensaje, llamada = getattr(self, self.ESCRITURAS[ruta])(Biblioteca(sesion), cuerpo)
                    return {'mensaje': mensaje, 'llamada': llamada, 'ruta': self.ruta}
                except ErrorBiblioteca as e:
                    return {'error': str(e), 'llamada': ruta, 'ruta': self.ruta}
                except exc.IntegrityError as e:
                    return {'error': str(e.orig), 'llamada': ruta, 'ruta': self.ruta}
                except Rechazo as e:
                    return {'error': str(e), 'llamada': ruta, 'ruta': self.ruta}


class Atencion(BaseHTTPRequestHandler):
    server_version = 'Biblioteca'
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
        except exc.DBAPIError as e:
            print(f"✕ {e.orig}", file=sys.stderr, flush=True)
            self.responder(HTTPStatus.SERVICE_UNAVAILABLE, {'error': 'la base de datos no responde ahora mismo'})
        except Exception:
            traceback.print_exc()
            self.responder(HTTPStatus.INTERNAL_SERVER_ERROR, {'error': 'error interno'})

    def do_GET(self):
        url = urlparse(self.path)
        app = self.server.app
        if url.path in ARCHIVOS:
            nombre = ARCHIVOS[url.path]
            tipo = (mimetypes.guess_type(nombre)[0] or 'text/plain') + '; charset=utf-8'
            return self.responder(HTTPStatus.OK, (ESTATICO / nombre).read_bytes(), tipo)
        if url.path == '/api/estado':
            def estado():
                nombre, puede = app.quien(self.headers.get('Cookie'))
                return {'usuario': nombre, 'puede': puede, 'conexion': self.server.descripcion}
            return self.atender(estado)
        if url.path in app.LECTURAS:
            q = parse_qs(url.query).get('q', [''])[0]
            return self.atender(lambda: app.leer(url.path, q, self.headers.get('Cookie')))
        self.responder(HTTPStatus.NOT_FOUND, {'error': 'no existe'})

    def do_POST(self):
        app = self.server.app

        def hacer():
            ruta = urlparse(self.path).path
            if ruta not in app.ESCRITURAS:
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


def preparar_datos(motor):
    bd.preparar(motor)
    with bd.fabrica_sesiones(motor)() as sesion:
        if not Biblioteca(sesion).cuantos(Libro):
            ejemplo.cargar(sesion)


def crear_servidor(direccion, motor, panel, descripcion):
    servidor = ThreadingHTTPServer(direccion, Atencion)
    servidor.daemon_threads = True
    servidor.app = Aplicacion(motor, panel)
    servidor.descripcion = descripcion
    return servidor


def main(argv=None):
    p = argparse.ArgumentParser(description='La biblioteca en la web, contra un servidor de SBD')
    p.add_argument('--sbd', default='http://127.0.0.1:8090', help='dirección del servidor de SBD')
    p.add_argument('--base', default='biblioteca', help='base de SBD donde guarda sus datos')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--puerto', type=int, default=8097)
    args = p.parse_args(argv)
    clave = os.environ.get('SBD_CLAVE')
    if not clave:
        print('✕ falta la clave de API en la variable SBD_CLAVE', file=sys.stderr)
        return 1
    url = urlparse(args.sbd)
    motor = bd.crear_motor(f'sbd+{url.scheme}://:{clave}@{url.netloc}/{args.base}')
    try:
        preparar_datos(motor)
    except exc.DBAPIError as e:
        print(f"✕ no se puede preparar la base: {e.orig}", file=sys.stderr)
        return 1
    servidor = crear_servidor((args.host, args.puerto), motor, args.sbd.rstrip('/'), f'servidor de SBD · base «{args.base}»')
    print(f"Biblioteca en http://{args.host}:{args.puerto}/, conectada a {args.sbd}", file=sys.stderr, flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
        motor.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
