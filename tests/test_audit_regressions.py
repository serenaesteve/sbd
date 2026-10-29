import base64
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import sbd
from sbd.database import Database
from sbd.errors import SBDError
from sbd.servidor import Sesion
from sbd.web.server import ApiError, Handler, Nucleo, ServidorHTTP


class TestAuditRegressions(unittest.TestCase):
    def test_servidor_expuesto_no_borra_ultimo_usuario(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            srv.crear_usuario('admin', 'clave1234', 'todo')
            http = ServidorHTTP(('127.0.0.1', 0), srv, any_host=True)
            try:
                with self.assertRaises(SBDError):
                    Sesion(srv, usuario=srv.usuario('admin')).ejecutar('borrar usuario admin')
                with self.assertRaises(ApiError):
                    http.nucleo.usuario_web(None)
            finally:
                http.server_close()
                srv.cerrar()

    def test_cuerpo_incompleto_no_bloquea_otras_peticiones(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            http = ServidorHTTP(('127.0.0.1', 0), srv)
            hilo = threading.Thread(target=http.serve_forever, daemon=True)
            hilo.start()
            port = http.server_address[1]
            sock = socket.create_connection(('127.0.0.1', port))
            leyendo = threading.Event()
            leer = Handler.leer_cuerpo
            def avisar(handler):
                leyendo.set()
                return leer(handler)
            try:
                with patch.object(Handler, 'leer_cuerpo', avisar):
                    sock.sendall(b'POST /api/entrar HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{')
                    self.assertTrue(leyendo.wait(2))
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/salud', timeout=2) as respuesta:
                        self.assertEqual(respuesta.status, 200)
            finally:
                sock.close()
                http.shutdown()
                http.server_close()
                hilo.join()
                srv.cerrar()

    def test_fallo_de_persistencia_deshace_y_no_reaparece(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'datos.base')
            db = Database(ruta)
            db.execute('crear tabla t { id entero clave }')
            with patch.object(db, '_persist', side_effect=OSError('disco lleno')):
                with self.assertRaises(OSError):
                    db.execute('insertar en t {}')
            self.assertEqual(db.query('buscar t'), [])
            db.execute('empezar')
            db.execute('insertar en t {}')
            with patch.object(db, '_persist', side_effect=OSError('disco lleno')):
                with self.assertRaises(OSError):
                    db.execute('confirmar')
            self.assertFalse(db.in_transaction)
            self.assertEqual(db.query('buscar t'), [])
            db.close()
            with Database(ruta) as abierta:
                self.assertEqual(abierta.query('buscar t'), [])

    def test_fallo_fsync_descarta_diario_parcial(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'datos.base')
            db = Database(ruta)
            db.execute('crear tabla t { id entero clave }')
            fsync = os.fsync
            llamadas = 0
            def fallo_una_vez(fd):
                nonlocal llamadas
                llamadas += 1
                if llamadas == 1:
                    raise OSError('error de fsync')
                fsync(fd)
            with patch('sbd.database.os.fsync', side_effect=fallo_una_vez):
                with self.assertRaises(OSError):
                    db.execute('insertar en t {}')
            self.assertEqual(db.query('buscar t'), [])
            db.close()
            with Database(ruta) as abierta:
                self.assertEqual(abierta.query('buscar t'), [])

    def test_fallo_al_reemplazar_foto_no_confirma_ddl(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'datos.base')
            db = Database(ruta)
            db.execute('crear tabla t { id entero clave }')
            with patch('sbd.database.os.replace', side_effect=OSError('disco lleno')):
                with self.assertRaises(OSError):
                    db.execute('crear tabla otra { id entero clave }')
            self.assertNotIn('otra', db.tables)
            db.close()
            with Database(ruta) as abierta:
                self.assertNotIn('otra', abierta.tables)

    def test_fallo_de_limpieza_del_diario_bloquea_la_base(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'datos.base')
            db = Database(ruta)
            db.execute('crear tabla t { id entero clave }')
            with patch('sbd.database.os.fsync', side_effect=OSError('disco lleno')):
                with self.assertRaisesRegex(SBDError, 'incierta'):
                    db.execute('insertar en t {}')
            with self.assertRaisesRegex(SBDError, 'incierto'):
                db.execute('buscar t')
            db.close()

    def test_cuotas_hacen_atomica_la_operacion(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_filas=2, max_mb=1)
                usuario = srv.registrar('ana', 'clave1234')
                sesion = Sesion(srv, usuario=usuario)
                sesion.ejecutar('crear base prueba  crear tabla t { id entero clave valor texto }')
                with self.assertRaises(SBDError):
                    sesion.ejecutar('insertar en t {} {} {}')
                self.assertEqual(sesion.ejecutar('buscar t').fetchall(), [])
                with self.assertRaises(SBDError):
                    sesion.ejecutar('insertar en t { valor: ? }', ['x' * 1100000])
                self.assertEqual(sesion.ejecutar('buscar t').fetchall(), [])
                sesion.ejecutar('insertar en t {} {}')
                srv.cambiar_ajustes(max_filas=3)
                with self.assertRaises(SBDError):
                    sesion.ejecutar('copiar base prueba a copia')
                self.assertFalse(srv.existe('copia'))
                self.assertEqual(sesion.ejecutar('buscar t').rowcount, 2)
            finally:
                srv.cerrar()

    def test_recuperacion_sobre_cuota_conserva_papelera(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_filas=3)
                sesion = Sesion(srv, usuario=srv.registrar('ana', 'clave1234'))
                sesion.ejecutar('crear base vieja  crear tabla t { id entero clave }  insertar en t {} {}')
                sesion.ejecutar('borrar base vieja')
                sesion.ejecutar('crear base nueva  crear tabla t { id entero clave }  insertar en t {} {}')
                with self.assertRaises(SBDError):
                    sesion.ejecutar('recuperar base vieja')
                self.assertFalse(srv.existe('vieja'))
                self.assertTrue(any(e[0] == 'vieja' for e in srv.papelera()))
            finally:
                srv.cerrar()

    def test_borrar_sigue_posible_si_se_reduce_el_limite(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_filas=5)
                sesion = Sesion(srv, usuario=srv.registrar('ana', 'clave1234'))
                sesion.ejecutar('crear base prueba  crear tabla t { id entero clave }  insertar en t {} {} {}')
                srv.cambiar_ajustes(max_filas=1)
                sesion.ejecutar('empezar  borrar de t donde id = 1  confirmar')
                self.assertEqual(sesion.ejecutar('buscar t').rowcount, 2)
            finally:
                srv.cerrar()

    def test_cuota_se_revalida_al_confirmar_esquemas_en_dos_bases(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_mb=1)
                inicial = Sesion(srv, usuario=srv.registrar('ana', 'clave1234'))
                inicial.ejecutar('crear base a  crear base b')
                usuario = srv.usuario('ana')
                primera = Sesion(srv, base='a', usuario=usuario)
                segunda = Sesion(srv, base='b', usuario=usuario)
                primera.ejecutar('empezar')
                segunda.ejecutar('empezar')
                ddl = 'crear tabla t { valor texto = "' + 'x' * 600_000 + '" }'
                primera.ejecutar(ddl)
                segunda.ejecutar(ddl)
                primera.ejecutar('confirmar')
                with self.assertRaisesRegex(SBDError, 'espacio'):
                    segunda.ejecutar('confirmar')
                self.assertNotIn('t', segunda.db.tables)
                self.assertFalse(segunda.db.in_transaction)
                self.assertLessEqual(srv.uso('ana')['bytes'], 1024 * 1024)
            finally:
                srv.cerrar()

    def test_borrar_tabla_en_transaccion_sigue_posible_sobre_cuota(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                srv.cambiar_ajustes(registro=True, max_filas=5)
                sesion = Sesion(srv, usuario=srv.registrar('ana', 'clave1234'))
                sesion.ejecutar('crear base prueba  crear tabla a { id entero clave }  '
                               'crear tabla b { id entero clave }  insertar en a {} {} {}  insertar en b {}')
                srv.cambiar_ajustes(max_filas=1)
                sesion.ejecutar('empezar  borrar tabla b  confirmar')
                self.assertNotIn('b', sesion.db.tables)
                self.assertEqual(sesion.ejecutar('buscar a').rowcount, 3)
            finally:
                srv.cerrar()

    def test_importacion_web_en_transaccion_y_rollback(self):
        with tempfile.TemporaryDirectory() as carpeta:
            srv = sbd.servidor(carpeta)
            try:
                s = Sesion(srv)
                s.ejecutar('crear base prueba')
                n = Nucleo(srv)
                sid = 'identificador-12345'
                self.assertNotIn('error', n.ejecutar_panel(None, 'prueba', 'empezar', sid))
                contenido = base64.b64encode(b'nombre\nAna\n').decode()
                resultado = n.importar(None, 'prueba', 'personas', 'datos.csv', contenido, 'panel::' + sid)
                self.assertNotIn('error', resultado)
                self.assertNotIn('error', n.ejecutar_panel(None, 'prueba', 'deshacer', sid))
                self.assertNotIn('personas', srv.abrir('prueba').tables)
            finally:
                srv.cerrar()
