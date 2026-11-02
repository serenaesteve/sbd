"""El conector: el traductor de SQL, el controlador DB-API y el dialecto de SQLAlchemy."""
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # el SBD de la carpeta de arriba

from sqlalchemy import (Column, ForeignKey, Integer, MetaData, Numeric, String, Table, create_engine, exc, func, inspect, select,
                        text)
from sqlalchemy.orm import Session

import sbd
import sqlalchemy_sbd  # noqa: F401
from sbd.web.server import PanelServer
from biblioteca import bd, ejemplo
from biblioteca.modelos import Autor, Base, Libro, Prestamo, Socio
from biblioteca.servicio import Biblioteca, ErrorBiblioteca
from sqlalchemy_sbd import dbapi
from sqlalchemy_sbd.sql import ErrorSQL, a_texto_sbd, es_sql, traducir

HOY = datetime.date(2026, 11, 1)


def normalizar(valor):
    """SQLite devuelve los importes como float y los booleanos como 0/1; SBD, Decimal y True."""
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, (float, Decimal)):
        return round(float(valor), 4)
    if isinstance(valor, (datetime.date, datetime.datetime)):
        return valor.isoformat()
    return valor


# Cada consulta se lanza en SBD y en SQLite, y tienen que devolver exactamente lo mismo.
CONSULTAS = """
SELECT 1 + 2, 7 - 3, 2 * 5, 'a' || 'b', -3, abs(-4), upper('hola'), lower('HOLA'), length('hola')
SELECT id, titulo FROM libros ORDER BY id
SELECT * FROM autores ORDER BY id
SELECT l.* FROM libros l ORDER BY l.id
SELECT titulo FROM libros WHERE precio > 12 AND anio < 1965 ORDER BY titulo
SELECT titulo FROM libros WHERE precio > 18 OR anio = 1945 ORDER BY titulo
SELECT titulo FROM libros WHERE NOT (precio > 12) ORDER BY titulo
SELECT titulo FROM libros WHERE precio <> 12.5 ORDER BY titulo
SELECT titulo FROM libros WHERE precio != 12.5 AND precio >= 10 AND precio <= 15 ORDER BY titulo
SELECT titulo FROM libros WHERE titulo LIKE '%a%' ORDER BY titulo
SELECT titulo FROM libros WHERE titulo LIKE 'E_ Aleph'
SELECT titulo FROM libros WHERE titulo NOT LIKE '%a%' ORDER BY titulo
SELECT titulo FROM libros WHERE anio BETWEEN 1945 AND 1965 ORDER BY titulo
SELECT titulo FROM libros WHERE anio NOT BETWEEN 1945 AND 1965 ORDER BY titulo
SELECT titulo FROM libros WHERE anio IN (1944, 1963, 2000) ORDER BY titulo
SELECT titulo FROM libros WHERE anio NOT IN (1944, 1963) ORDER BY titulo
SELECT nombre FROM autores WHERE pais IS NULL ORDER BY nombre
SELECT nombre FROM autores WHERE pais IS NOT NULL ORDER BY nombre
SELECT titulo AS t, precio AS p FROM libros ORDER BY p DESC LIMIT 2
SELECT titulo FROM libros ORDER BY titulo LIMIT 2 OFFSET 1
SELECT DISTINCT autor_id FROM libros ORDER BY autor_id
SELECT COUNT(*), COUNT(anio), COUNT(DISTINCT autor_id), SUM(ejemplares), MIN(precio), MAX(precio) FROM libros
SELECT autor_id, COUNT(*) AS n, SUM(ejemplares) AS e FROM libros GROUP BY autor_id ORDER BY autor_id
SELECT autor_id, COUNT(*) AS n FROM libros GROUP BY autor_id HAVING COUNT(*) > 1
SELECT a.nombre, l.titulo FROM libros l JOIN autores a ON a.id = l.autor_id ORDER BY l.id
SELECT a.nombre, l.titulo FROM libros AS l INNER JOIN autores AS a ON a.id = l.autor_id WHERE a.nombre LIKE 'J%' ORDER BY l.id
SELECT s.nombre, p.id FROM socios s LEFT JOIN prestamos p ON p.socio_id = s.id ORDER BY s.id, p.id
SELECT s.nombre, p.id FROM socios s LEFT OUTER JOIN prestamos p ON p.socio_id = s.id WHERE p.id IS NULL
SELECT p.id, s.nombre FROM prestamos p RIGHT JOIN socios s ON p.socio_id = s.id ORDER BY s.id, p.id
SELECT a.nombre, g.nombre FROM autores a CROSS JOIN generos g WHERE a.id = 1 ORDER BY g.id
SELECT a.nombre, g.nombre FROM autores a, generos g WHERE a.id = 1 AND g.id = 1
SELECT l.titulo, g.nombre FROM libros l JOIN libro_genero lg ON lg.libro_id = l.id JOIN generos g ON g.id = lg.genero_id ORDER BY l.id, g.id
SELECT titulo FROM libros WHERE autor_id IN (SELECT id FROM autores WHERE nombre LIKE 'J%') ORDER BY titulo
SELECT nombre FROM socios WHERE id NOT IN (SELECT socio_id FROM prestamos) ORDER BY nombre
SELECT nombre FROM autores a WHERE EXISTS (SELECT 1 FROM libros l WHERE l.autor_id = a.id AND l.precio > 15) ORDER BY nombre
SELECT nombre FROM autores a WHERE NOT EXISTS (SELECT 1 FROM libros l WHERE l.autor_id = a.id AND l.precio > 15) ORDER BY nombre
SELECT titulo, (SELECT nombre FROM autores a WHERE a.id = l.autor_id) AS autor FROM libros l ORDER BY l.id
SELECT titulo FROM libros WHERE precio = (SELECT MAX(precio) FROM libros)
SELECT t.autor_id, t.n FROM (SELECT autor_id, COUNT(*) AS n FROM libros GROUP BY autor_id) AS t WHERE t.n > 1
SELECT x.titulo FROM (SELECT * FROM libros WHERE precio > 12) x ORDER BY x.titulo LIMIT 2
SELECT titulo, CASE WHEN precio > 15 THEN 'caro' WHEN precio > 10 THEN 'medio' ELSE 'barato' END AS rango FROM libros ORDER BY id
SELECT titulo, CASE anio WHEN 1944 THEN 'uno' WHEN 1945 THEN 'dos' END FROM libros ORDER BY id
SELECT CAST('42' AS INTEGER), CAST(7 AS VARCHAR), CAST(ejemplares AS REAL) FROM libros WHERE id = 1
SELECT COALESCE(pais, 'sin país') FROM autores ORDER BY id
SELECT titulo, ejemplares * precio AS valor FROM libros ORDER BY valor DESC, titulo
SELECT nombre FROM socios UNION SELECT nombre FROM autores ORDER BY nombre
SELECT autor_id FROM libros UNION ALL SELECT id FROM autores ORDER BY autor_id
SELECT id FROM autores INTERSECT SELECT autor_id FROM libros WHERE precio > 15 ORDER BY id
SELECT id FROM autores EXCEPT SELECT autor_id FROM libros WHERE precio > 15 ORDER BY id
WITH caros AS (SELECT * FROM libros WHERE precio > 15) SELECT COUNT(*) FROM caros
WITH caros(t) AS (SELECT titulo FROM libros WHERE precio > 15), n AS (SELECT COUNT(*) AS c FROM caros) SELECT c FROM n
WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) SELECT SUM(x) FROM n
SELECT titulo, ROW_NUMBER() OVER (ORDER BY precio DESC) AS n FROM libros ORDER BY n
SELECT autor_id, titulo, RANK() OVER (PARTITION BY autor_id ORDER BY precio DESC) AS r FROM libros ORDER BY autor_id, r
SELECT titulo, SUM(precio) OVER (PARTITION BY autor_id) AS t FROM libros ORDER BY id
SELECT g.nombre, COUNT(lg.libro_id) AS n FROM generos g LEFT JOIN libro_genero lg ON lg.genero_id = g.id GROUP BY g.nombre ORDER BY n DESC, g.nombre
SELECT "titulo" FROM "libros" WHERE "id" = 1
select titulo from libros where id = 1 -- comentario
SELECT /* dentro */ titulo FROM libros WHERE titulo = 'Nada';
SELECT 'con ''comilla''', 'y "dobles"'
SELECT AVG(precio), AVG(ejemplares), SUM(precio) FROM libros
SELECT autor_id, AVG(precio) AS m FROM libros GROUP BY autor_id ORDER BY m DESC, autor_id
SELECT ROUND(AVG(precio), 2) FROM libros
SELECT TRIM('  a '), REPLACE('hola', 'o', '0'), SUBSTR('hola', 2, 2), NULLIF(1, 1), NULLIF(1, 2), IFNULL(NULL, 2)
SELECT ROUND(2.567, 1), ABS(-2.5)
SELECT 10 / 4, 10 % 3, 2 + 3 * 4, (2 + 3) * 4
SELECT titulo FROM libros WHERE LOWER(titulo) = 'nada' AND LENGTH(titulo) = 4
SELECT MIN(titulo), MAX(titulo), MIN(anio), MAX(anio) FROM libros
SELECT NULL IS NULL, 1 = 1, 1 = 2, NOT 1 = 2
SELECT COUNT(*) FROM libros WHERE anio > 3000
SELECT SUM(precio) FROM libros WHERE anio > 3000
SELECT titulo FROM libros WHERE anio > 3000
SELECT p.id, s.nombre, l.titulo, p.multa FROM prestamos p JOIN socios s ON s.id = p.socio_id JOIN libros l ON l.id = p.libro_id WHERE p.devuelto IS NOT NULL
SELECT autor_id, MAX(precio) - MIN(precio) AS d FROM libros GROUP BY autor_id HAVING MAX(precio) - MIN(precio) > 0 ORDER BY d DESC
SELECT DISTINCT a.nombre FROM autores a JOIN libros l ON l.autor_id = a.id JOIN libro_genero lg ON lg.libro_id = l.id ORDER BY a.nombre
""".strip().splitlines()

CAMBIOS = """
UPDATE libros SET ejemplares = ejemplares + 1 WHERE precio > 15
UPDATE libros SET precio = 1.5, anio = 2000 WHERE id = 1
DELETE FROM libro_genero WHERE libro_id = 1
INSERT INTO generos (nombre) VALUES ('Ensayo')
INSERT INTO generos (nombre) VALUES ('Poesía'), ('Teatro')
INSERT INTO autores (nombre, pais) SELECT nombre, 'X' FROM socios WHERE id = 1
DELETE FROM generos WHERE nombre LIKE 'T%'
UPDATE autores SET pais = 'Y'
""".strip().splitlines()


def servidor_de_prueba(prueba, carpeta):
    """Un `sbd servidor` de verdad, en un hilo, con una base y una clave de API. Devuelve (puerto, clave)."""
    srv = sbd.servidor(carpeta)
    prueba.addCleanup(srv.cerrar)
    admin = sbd.Sesion(srv)
    prueba.addCleanup(admin.cerrar)
    admin.ejecutar('crear base biblioteca  crear usuario app clave "clave de la aplicacion" puede escribir en biblioteca')
    clave = admin.ejecutar('crear clave api para app').rows[0][0]
    http = PanelServer(('127.0.0.1', 0), srv)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    prueba.addCleanup(http.server_close)
    prueba.addCleanup(http.shutdown)
    prueba.srv_prueba, prueba.http_prueba = srv, http
    return http.server_address[1], clave


class ConDatos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = os.path.join(self.tmp.name, 'b.base')
        self.motor = create_engine(f'sbd:///{self.ruta}')
        self.addCleanup(self.motor.dispose)
        Base.metadata.create_all(self.motor)


class TestIgualQueSQLite(unittest.TestCase):
    """Las mismas órdenes en SBD y en SQLite: si el traductor se equivoca, aquí se nota."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        servidor, clave = servidor_de_prueba(self, os.path.join(self.tmp.name, 'datos'))
        self.motores = [create_engine(f"sqlite:///{os.path.join(self.tmp.name, 'b.sqlite')}"),
                        create_engine(f"sbd:///{os.path.join(self.tmp.name, 'b.base')}"),
                        create_engine(f"sbd+http://:{clave}@127.0.0.1:{servidor}/biblioteca")]
        for motor in self.motores:
            self.addCleanup(motor.dispose)
            Base.metadata.create_all(motor)
            with Session(motor, expire_on_commit=False) as s:
                ejemplo.cargar(s)
                s.add(Autor(nombre='Sin libros', pais='Chile'))
                s.commit()

    def lanzar(self, motor, sql):
        with motor.connect() as c:
            r = c.execute(text(sql))
            return [tuple(normalizar(v) for v in fila) for fila in r.all()]

    def test_consultas(self):
        self.assertGreater(sqlite3.sqlite_version_info, (3, 39))  # RIGHT JOIN
        for sql in CONSULTAS:
            with self.subTest(sql=sql):
                referencia = self.lanzar(self.motores[0], sql)
                self.assertEqual(self.lanzar(self.motores[1], sql), referencia, 'SBD abriendo el fichero')
                self.assertEqual(self.lanzar(self.motores[2], sql), referencia, 'SBD por su servidor')

    def test_cambios(self):
        for sql in CAMBIOS:
            with self.subTest(sql=sql):
                resultados = []
                for motor in self.motores:
                    with motor.begin() as c:
                        filas = c.execute(text(sql)).rowcount
                    resultados.append(filas)
                self.assertEqual(resultados[1:], [resultados[0]] * 2)
        for tabla in ('libros', 'generos', 'autores', 'libro_genero', 'socios', 'prestamos'):
            with self.subTest(tabla=tabla):
                sql = f'SELECT * FROM {tabla} ORDER BY id' if tabla not in ('libro_genero',) else 'SELECT * FROM libro_genero ORDER BY libro_id, genero_id'
                referencia = self.lanzar(self.motores[0], sql)
                self.assertEqual(self.lanzar(self.motores[1], sql), referencia)
                self.assertEqual(self.lanzar(self.motores[2], sql), referencia)

    def test_el_orm_genera_lo_mismo_en_los_dos(self):
        def recorrido(motor):
            with Session(motor, expire_on_commit=False) as s:
                b = Biblioteca(s)
                salida = [[(x.titulo, x.autor.nombre, [g.nombre for g in x.generos], x.precio) for x in b.buscar_libros(t)]
                          for t in ('', 'a', 'Borges', 'no existe')]
                p = b.prestar(4, [2, 6], hoy=HOY)
                salida.append([(x.id, x.vence, x.multa) for x in p])
                salida.append((b.devolver(p[0].id, hoy=HOY + datetime.timedelta(days=30)).multa,))
                b.cambiar_precio(2, '99.99')
                salida.append((b.libro(2).precio, b.libro(2).version))
                salida.append(s.execute(select(Libro.autor_id, func.count(), func.sum(Libro.ejemplares))
                                        .group_by(Libro.autor_id).order_by(Libro.autor_id)).all())
                salida.append(s.scalars(select(Socio).where(Socio.prestamos.any(Prestamo.devuelto.is_(None)))
                                        .order_by(Socio.id)).all().__len__())
                salida.append([a.nombre for a in s.scalars(select(Autor).where(~Autor.libros.any()).order_by(Autor.id))])
                salida.append(s.scalars(select(Libro.titulo).where(Libro.titulo.contains('a')).order_by(Libro.titulo)
                                        .limit(2).offset(1)).all())
                salida.append(s.scalars(select(Libro.titulo).where(Libro.titulo.in_(['Nada', 'Rayuela', 'x'])).order_by(Libro.id)).all())
                s.rollback()
                return repr(salida)
        referencia = recorrido(self.motores[0])
        self.assertEqual(recorrido(self.motores[1]), referencia)
        self.assertEqual(recorrido(self.motores[2]), referencia)


class TestTraductor(unittest.TestCase):
    def test_distingue_sql_de_sbd(self):
        for sql in ['SELECT 1', '  select 1', '-- x\nSELECT 1', '/* x */ INSERT INTO t VALUES (1)', 'WITH a AS (SELECT 1) SELECT 1']:
            self.assertTrue(es_sql(sql), sql)
        for orden in ['buscar t', 'insertar en t {}', 'ejecutar p()', 'tablas', '']:
            self.assertFalse(es_sql(orden), orden)

    def test_cuenta_los_parametros_en_orden(self):
        orden, n = traducir('SELECT ? FROM (SELECT a FROM t WHERE b = ?) x WHERE a = ? LIMIT ?')
        self.assertEqual(n, 4)
        self.assertEqual(orden.cores[0].items[0].expr.index, 0)
        self.assertEqual(orden.limit.index, 3)

    def test_lo_que_no_cubre_da_error_claro(self):
        for sql, trozo in [('SELECT * FROM t WHERE a LIKE ? ESCAPE ?', 'ESCAPE'),
                           ('CREATE VIEW v AS SELECT 1', 'tablas e índices'),
                           ('ALTER TABLE t ADD c INTEGER', None), ('CREATE TABLE t (c BLOB)', 'BLOB'),
                           ('CREATE TABLE t (a INTEGER REFERENCES x(y) ON UPDATE CASCADE)', 'ON UPDATE'),
                           ('SELECT 1; DROP TABLE t', 'una orden'), ('SELECT FROM', None), ('SELECT 1 +', None),
                           ("SELECT 'sin cerrar", None)]:
            with self.subTest(sql=sql):
                with self.assertRaises(ErrorSQL) as ctx:
                    traducir(sql)
                if trozo:
                    self.assertIn(trozo, str(ctx.exception))

    def test_check_y_default_quedan_en_el_idioma_de_sbd(self):
        orden, _ = traducir("""CREATE TABLE t (id INTEGER PRIMARY KEY, a VARCHAR(5) DEFAULT 'x''y',
            b NUMERIC(8, 2) DEFAULT 1.5, c BOOLEAN DEFAULT TRUE, d DATE DEFAULT CURRENT_DATE,
            CONSTRAINT k CHECK (b >= 0 AND a IS NOT NULL OR c IN (1, 2) OR a LIKE 'x%'))""")
        por = {c.name: c for c in orden.columns}
        self.assertEqual(por['a'].default_sql, '"x\'y"')
        self.assertEqual((por['b'].type, por['b'].default_sql), ('MONEY', '1.5'))
        self.assertEqual(por['c'].default_sql, 'verdadero')
        self.assertEqual(por['d'].default_sql, 'hoy()')
        self.assertIn(' y ', orden.constraints[0].check_sql)
        self.assertNotIn('AND', orden.constraints[0].check_sql)


class TestTextoSBD(unittest.TestCase):
    def test_escribe_ordenes_que_se_leen_en_espanol(self):
        for sql, esperado in [
            ('SELECT titulo FROM libros WHERE id = ?', 'buscar libros donde (id = ?) mostrar titulo'),
            ('SELECT COUNT(*) AS n FROM libros', 'buscar libros mostrar contar() como n'),
            ("INSERT INTO t (a, b) VALUES (?, 'x')", 'insertar en t { a: ?, b: "x" }'),
            ('UPDATE t SET a = 1 WHERE b IS NULL', 'actualizar t poner a = 1 donde (b es nulo)'),
            ('UPDATE t SET a = 1', 'actualizar t poner a = 1 todas'),
            ('DELETE FROM t WHERE a <> 1', 'borrar de t donde (a != 1)'),
            ('DELETE FROM t', 'vaciar t'),
            ('INSERT INTO t DEFAULT VALUES', 'insertar en t {}'),
            ('SELECT 1 + 2', 'calcular (1 + 2)'),
        ]:
            self.assertEqual(a_texto_sbd(sql)[0], esperado)

    def test_los_parametros_se_recolocan(self):
        # En SQL las columnas van antes que la tabla; en SBD, después
        texto, orden = a_texto_sbd('SELECT ? AS a FROM t WHERE b = ? LIMIT ?')
        self.assertEqual(texto, 'buscar t donde (b = ?) mostrar ? como a limite ?')
        self.assertEqual(orden, (1, 0, 2))

    def test_un_nombre_del_lenguaje_va_entre_comillas(self):
        self.assertEqual(a_texto_sbd('SELECT buscar FROM tablas')[0], 'buscar `tablas` mostrar `buscar`')

    def test_las_consultas_de_las_pruebas_se_vuelven_a_leer(self):
        from sbd.parser import Parser
        for sql in [*CONSULTAS, *CAMBIOS]:
            with self.subTest(sql=sql):
                texto, orden = a_texto_sbd(sql)
                parser = Parser(texto)
                self.assertEqual(len(parser.parse_script()), 1)
                self.assertEqual(parser.param_count, len(orden))


class TestPorElServidor(unittest.TestCase):
    """El ORM contra un `sbd servidor` de verdad, que tiene la base abierta."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        puerto, self.clave = servidor_de_prueba(self, os.path.join(self.tmp.name, 'datos'))
        self.url = f'sbd+http://:{self.clave}@127.0.0.1:{puerto}/biblioteca'
        self.motor = create_engine(self.url)
        self.addCleanup(self.motor.dispose)
        Base.metadata.create_all(self.motor)

    def test_la_base_es_del_servidor_y_los_dos_ven_lo_mismo(self):
        with Session(self.motor, expire_on_commit=False) as s:
            libro = Biblioteca(s).alta_libro('Uno', '1', 'Autora', precio='10.50', ejemplares=2, generos=['G'])
        self.assertEqual(self.motor.dialect.driver, 'http')
        admin = sbd.Sesion(self.srv_prueba, 'biblioteca')
        self.addCleanup(admin.cerrar)
        self.assertEqual(admin.ejecutar('buscar libros mostrar titulo, precio').rows, [('Uno', Decimal('10.50'))])
        self.assertIn('crear tabla libros', '\n'.join(admin.db.schema_sbd()))
        admin.ejecutar('actualizar libros poner titulo = "Otro" donde id = 1')
        with Session(self.motor) as s:
            self.assertEqual(s.get(Libro, libro.id).titulo, 'Otro')

    def test_el_dinero_y_las_fechas_van_y_vuelven(self):
        with Session(self.motor) as s:
            a = Autor(nombre='A')
            s.add(Libro(titulo='T', isbn='1', precio=Decimal('19.99'), autor=a))
            s.add(Socio(nombre='S', email='e', alta=datetime.date(2024, 2, 29), activo=False))
            s.commit()
            self.assertEqual(s.scalar(select(Libro.precio)), Decimal('19.99'))
            self.assertEqual(s.scalars(select(Libro).where(Libro.precio == Decimal('19.99'))).one().titulo, 'T')
            socio = s.scalars(select(Socio).where(Socio.alta == datetime.date(2024, 2, 29))).one()
            self.assertEqual((socio.alta, socio.activo), (datetime.date(2024, 2, 29), False))

    def test_transaccion_y_deshacer(self):
        with Session(self.motor) as s:
            s.add(Socio(nombre='S', email='e'))
            s.flush()
            s.rollback()
            self.assertEqual(s.scalar(select(func.count()).select_from(Socio)), 0)
            s.add(Socio(nombre='S', email='e'))
            s.commit()
            self.assertEqual(s.scalar(select(func.count()).select_from(Socio)), 1)
        b_sesion = Session(self.motor, expire_on_commit=False)
        self.addCleanup(b_sesion.close)
        b = Biblioteca(b_sesion)
        b.alta_libro('Uno', '1', 'A', ejemplares=1)
        b.alta_libro('Dos', '2', 'A', ejemplares=1)
        socio = b.alta_socio('Ana', 'a@x')
        b.prestar(socio.id, 1)
        with self.assertRaises(ErrorBiblioteca):
            b.prestar(socio.id, [2, 1])
        self.assertEqual((b.libro(2).disponibles, b.cuantos(Prestamo)), (1, 1))

    def test_otra_conexion_no_ve_lo_que_no_esta_confirmado(self):
        original = dbapi.ESPERA_OCUPADA
        with Session(self.motor) as s:
            s.add(Socio(nombre='S', email='e'))
            s.flush()
            otra = dbapi.connect(url=f'http://127.0.0.1:{self.motor.url.port}', clave=self.clave, base='biblioteca')
            self.addCleanup(otra.close)
            with self.assertRaises(dbapi.OperationalError):
                otra.cursor().execute('SELECT * FROM socios')
            s.rollback()
        c = otra.cursor()
        c.execute('SELECT COUNT(*) FROM socios')
        self.assertEqual(c.fetchone(), (0,))
        self.assertEqual(dbapi.ESPERA_OCUPADA, original)

    def test_errores(self):
        with Session(self.motor) as s:
            s.add(Socio(nombre='S', email='e'))
            s.commit()
            s.add(Socio(nombre='S', email='e'))
            with self.assertRaises(exc.IntegrityError):
                s.commit()
            s.rollback()
            with self.assertRaises(exc.ProgrammingError):
                s.execute(text('SELEC 1'))
            s.rollback()
            with self.assertRaises(exc.ProgrammingError):   # dos órdenes pegadas no pasan
                s.execute(text('buscar socios  vaciar socios'))
            s.rollback()
            with self.assertRaises(exc.ProgrammingError):
                s.execute(text("SELECT 1; DELETE FROM socios"))
            s.rollback()
            self.assertEqual(s.scalar(select(func.count()).select_from(Socio)), 1)
        with self.assertRaises(exc.OperationalError):       # una clave falsa no entra
            create_engine(self.url.replace(self.clave, 'sbd_kmala_x')).connect()

    def test_una_clave_de_otra_base_no_llega(self):
        admin = sbd.Sesion(self.srv_prueba)
        self.addCleanup(admin.cerrar)
        admin.ejecutar('crear base privada  crear tabla p { id entero clave }')
        with self.assertRaises(exc.OperationalError):
            create_engine(self.url.replace('/biblioteca', '/privada')).connect()

    def test_punto_de_guardado_avisa(self):
        with Session(self.motor) as s:
            with self.assertRaises(exc.NotSupportedError):
                with s.begin_nested():
                    s.execute(select(Socio))

    def test_inspeccionar_lista_las_tablas_y_avisa_del_resto(self):
        insp = inspect(self.motor)
        self.assertIn('libros', insp.get_table_names())
        with self.assertRaises(NotImplementedError):
            insp.get_columns('libros')

    def test_si_se_pierde_la_sesion_el_pool_abre_otra(self):
        motor = create_engine(self.url, pool_pre_ping=True)
        self.addCleanup(motor.dispose)
        with motor.connect() as c:
            self.assertEqual(c.execute(text('SELECT 1')).scalar(), 1)
            sid = c.connection.driver_connection._cliente._sesion
        self.http_prueba.nucleo.cerrar_sesion_api(self.srv_prueba.usuario('app'), sid)
        with motor.connect() as c:
            self.assertEqual(c.execute(text('SELECT 2')).scalar(), 2)


class TestDBAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = os.path.join(self.tmp.name, 'd.base')
        self.con = dbapi.connect(self.ruta)
        self.addCleanup(self.con.close)
        self.cur = self.con.cursor()
        self.cur.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, n VARCHAR(10) UNIQUE NOT NULL, q INTEGER CHECK (q > 0))')
        self.con.commit()

    def test_cumple_lo_basico_del_estandar(self):
        self.assertEqual((dbapi.apilevel, dbapi.paramstyle), ('2.0', 'qmark'))
        self.cur.execute('INSERT INTO t (n, q) VALUES (?, ?)', ('a', 1))
        self.assertEqual((self.cur.lastrowid, self.cur.rowcount, self.cur.description), (1, 1, None))
        self.cur.executemany('INSERT INTO t (n, q) VALUES (?, ?)', [('b', 2), ('c', 3)])
        self.assertEqual(self.cur.rowcount, 2)
        self.cur.execute('SELECT id, n FROM t ORDER BY id')
        self.assertEqual([d[0] for d in self.cur.description], ['id', 'n'])
        self.assertEqual(self.cur.fetchone(), (1, 'a'))
        self.assertEqual(self.cur.fetchmany(1), [(2, 'b')])
        self.assertEqual(self.cur.fetchall(), [(3, 'c')])
        self.assertIsNone(self.cur.fetchone())

    def test_deshacer_y_confirmar(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        self.con.rollback()
        self.cur.execute('SELECT COUNT(*) FROM t')
        self.assertEqual(self.cur.fetchone(), (0,))
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        self.con.commit()
        self.con.rollback()
        self.cur.execute('SELECT COUNT(*) FROM t')
        self.assertEqual(self.cur.fetchone(), (1,))

    def test_los_errores_son_del_tipo_correcto(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        for sql in ["INSERT INTO t (n, q) VALUES ('a', 1)", "INSERT INTO t (n, q) VALUES (NULL, 1)",
                    "INSERT INTO t (n, q) VALUES ('b', 0)"]:
            with self.assertRaises(dbapi.IntegrityError, msg=sql):
                self.cur.execute(sql)
        with self.assertRaises(dbapi.ProgrammingError):
            self.cur.execute('SELEC 1')
        with self.assertRaises(dbapi.ProgrammingError):
            self.cur.execute('SELECT ?', (1, 2))
        with self.assertRaises(dbapi.DatabaseError):
            self.cur.execute('SELECT * FROM no_existe')

    def test_una_orden_rechazada_no_estropea_la_transaccion(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        with self.assertRaises(dbapi.IntegrityError):
            self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        self.con.commit()
        self.cur.execute('SELECT n FROM t')
        self.assertEqual(self.cur.fetchall(), [('a',)])

    def test_inyeccion_con_parametros_no_entra(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        malo = "x' OR '1'='1"
        self.cur.execute('SELECT n FROM t WHERE n = ?', (malo,))
        self.assertEqual(self.cur.fetchall(), [])
        with self.assertRaises(dbapi.ProgrammingError):   # y dos órdenes pegadas tampoco pasan
            self.cur.execute("SELECT n FROM t WHERE n = 'x'; DELETE FROM t")
        with self.assertRaises(dbapi.ProgrammingError):
            self.cur.execute('buscar t  vaciar t')
        self.cur.execute('SELECT COUNT(*) FROM t')
        self.assertEqual(self.cur.fetchone(), (1,))

    def test_tambien_entiende_el_lenguaje_de_sbd(self):
        self.cur.execute('insertar en t { n: ?, q: 5 }', ('a',))
        self.cur.execute('buscar t donde n = ? mostrar q', ('a',))
        self.assertEqual(self.cur.fetchall(), [(5,)])

    def test_savepoints_en_el_controlador(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        self.cur.execute('SAVEPOINT uno')
        self.cur.execute("INSERT INTO t (n, q) VALUES ('b', 1)")
        self.cur.execute('ROLLBACK TO SAVEPOINT uno')
        self.cur.execute("INSERT INTO t (n, q) VALUES ('c', 1)")
        self.cur.execute('RELEASE SAVEPOINT uno')
        with self.assertRaises(dbapi.OperationalError):
            self.cur.execute('ROLLBACK TO SAVEPOINT uno')
        self.con.commit()
        self.cur.execute('SELECT n FROM t ORDER BY id')
        self.assertEqual(self.cur.fetchall(), [('a',), ('c',)])

    def test_dos_conexiones_ven_lo_confirmado_y_no_se_pisan(self):
        otra = dbapi.connect(self.ruta)
        self.addCleanup(otra.close)
        original, dbapi.ESPERA_OCUPADA = dbapi.ESPERA_OCUPADA, 0.05
        self.addCleanup(setattr, dbapi, 'ESPERA_OCUPADA', original)
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        c2 = otra.cursor()
        with self.assertRaises(dbapi.OperationalError):
            c2.execute("INSERT INTO t (n, q) VALUES ('b', 1)")
        with self.assertRaises(dbapi.OperationalError):
            c2.execute('SELECT * FROM t')          # no ve datos sin confirmar: espera y avisa
        self.con.commit()
        c2.execute('SELECT n FROM t')
        self.assertEqual(c2.fetchall(), [('a',)])

    def test_cerrar_deshace_lo_no_confirmado_y_suelta_el_fichero(self):
        self.cur.execute("INSERT INTO t (n, q) VALUES ('a', 1)")
        self.con.close()
        self.con.close()
        with self.assertRaises(dbapi.InterfaceError):
            self.con.cursor().execute('SELECT 1')
        db = sbd.conectar(self.ruta)
        self.assertEqual(db.execute('buscar t').rows, [])
        with self.assertRaises(dbapi.OperationalError):   # el fichero está abierto en «otro programa»
            dbapi.connect(self.ruta)
        db.close()

    def test_las_fechas_de_python_se_entienden(self):
        self.cur.execute('CREATE TABLE f (d DATE, m TIMESTAMP)')
        self.cur.execute('INSERT INTO f VALUES (?, ?)', (datetime.date(2026, 1, 2), datetime.datetime(2026, 1, 2, 3, 4, 5)))
        self.cur.execute('SELECT d, m FROM f WHERE d = ?', (datetime.date(2026, 1, 2),))
        self.assertEqual(self.cur.fetchall(), [('2026-01-02', '2026-01-02 03:04:05')])


class TestDialecto(ConDatos):
    def test_inspeccionar_la_base(self):
        insp = inspect(self.motor)
        self.assertEqual(insp.get_table_names(), ['autores', 'generos', 'libro_genero', 'libros', 'prestamos', 'socios'])
        self.assertEqual(insp.get_pk_constraint('libro_genero')['constrained_columns'], ['libro_id', 'genero_id'])
        cols = {c['name']: c for c in insp.get_columns('libros')}
        self.assertIsInstance(cols['precio']['type'], Numeric)
        self.assertEqual(cols['titulo']['type'].length, 160)
        self.assertTrue(cols['id']['autoincrement'])
        self.assertFalse(cols['titulo']['nullable'])
        self.assertTrue(cols['anio']['nullable'])
        self.assertEqual(insp.get_foreign_keys('libros')[0]['referred_table'], 'autores')
        self.assertEqual(insp.get_foreign_keys('libro_genero')[0]['options']['ondelete'], 'CASCADE')
        self.assertIn('ix_libros_titulo', [i['name'] for i in insp.get_indexes('libros')])
        self.assertTrue(insp.get_check_constraints('libros'))
        # Y SQLAlchemy puede construir una tabla leyendo la base
        leida = Table('autores', MetaData(), autoload_with=self.motor)
        self.assertEqual(leida.columns.keys(), ['id', 'nombre', 'pais'])

    def test_create_all_y_drop_all_se_pueden_repetir(self):
        Base.metadata.create_all(self.motor)
        Base.metadata.drop_all(self.motor)
        self.assertEqual(inspect(self.motor).get_table_names(), [])
        Base.metadata.create_all(self.motor)
        self.assertIn('libros', inspect(self.motor).get_table_names())

    def test_los_tipos_van_y_vuelven(self):
        with Session(self.motor) as s:
            s.add(Socio(nombre='Ñu "x" \'y\'', email='ñ@x', alta=datetime.date(2024, 2, 29), activo=False))
            s.commit()
            socio = s.scalars(select(Socio)).one()
            self.assertEqual(socio.nombre, 'Ñu "x" \'y\'')
            self.assertEqual(socio.alta, datetime.date(2024, 2, 29))
            self.assertIs(socio.activo, False)
            self.assertEqual(s.scalars(select(Socio).where(Socio.alta > datetime.date(2024, 1, 1))).one().id, socio.id)
            self.assertEqual(s.scalars(select(Socio).where(Socio.activo.is_(False))).one().id, socio.id)

    def test_el_dinero_es_exacto(self):
        with Session(self.motor) as s:
            a = Autor(nombre='A')
            s.add_all([Libro(titulo=f't{i}', isbn=str(i), precio=Decimal('0.10'), autor=a) for i in range(3)])
            s.commit()
            total = s.scalar(select(func.sum(Libro.precio)))
            self.assertEqual(total, Decimal('0.30'))   # con float daría 0.30000000000000004
            self.assertIsInstance(s.scalars(select(Libro.precio)).first(), Decimal)

    def test_tipos_datetime_y_float(self):
        meta = MetaData()
        from sqlalchemy import DateTime, Float, Text
        t = Table('medidas', meta, Column('id', Integer, primary_key=True), Column('m', DateTime), Column('v', Float),
                  Column('d', Numeric(10, 4)), Column('x', Text))
        meta.create_all(self.motor)
        momento = datetime.datetime(2026, 11, 2, 10, 30, 15)
        with self.motor.begin() as c:
            c.execute(t.insert(), {'m': momento, 'v': 1.25, 'd': Decimal('1.2345'), 'x': 'á'})
            fila = c.execute(select(t)).one()
        self.assertEqual(fila.m, momento)
        self.assertEqual(fila.v, 1.25)
        self.assertEqual(fila.d, Decimal('1.2345'))
        self.assertEqual(fila.x, 'á')

    def test_savepoint_deshace_solo_su_parte(self):
        with Session(self.motor) as s:
            s.add(Socio(nombre='Fuera', email='f@x'))
            s.flush()
            with self.assertRaises(exc.IntegrityError):
                with s.begin_nested():
                    s.add(Socio(nombre='Dentro', email='d@x'))
                    s.flush()
                    s.add(Socio(nombre='Repetido', email='f@x'))
                    s.flush()
            with s.begin_nested():                      # y uno que sale bien se conserva
                s.add(Socio(nombre='Bien', email='b@x'))
            s.commit()
            self.assertEqual(s.scalars(select(Socio.nombre).order_by(Socio.id)).all(), ['Fuera', 'Bien'])
        # También se deshacen al deshacer la transacción de fuera
        with Session(self.motor) as s:
            with s.begin_nested():
                s.add(Socio(nombre='Nunca', email='n@x'))
            s.rollback()
            self.assertEqual(s.scalar(select(func.count()).select_from(Socio)), 2)

    def test_for_update_no_rompe(self):
        with Session(self.motor) as s:
            self.assertEqual(s.scalars(select(Socio).with_for_update()).all(), [])

    def test_ejecutar_un_procedimiento_de_sbd_desde_el_orm(self):
        with self.motor.begin() as c:
            c.execute(text("insertar en autores { nombre: 'A' }"))
            # exec_driver_sql: en text(), SQLAlchemy tomaría «:n» como un parámetro suyo
            c.exec_driver_sql("crear procedimiento renombra(id, n) { actualizar autores poner nombre = :n donde id = :id "
                              "buscar autores donde id = :id mostrar nombre }")
        with self.motor.begin() as c:
            self.assertEqual(c.execute(text('ejecutar renombra(:i, :n)'), {'i': 1, 'n': 'B'}).all(), [('B',)])
        with Session(self.motor) as s:
            self.assertEqual(s.scalars(select(Autor.nombre)).all(), ['B'])


class TestWeb(unittest.TestCase):
    """La web de la biblioteca: mirar es público y cambiar exige entrar en el panel con permiso «todo»."""

    def setUp(self):
        from biblioteca.web import crear_servidor, preparar_datos
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.puerto_sbd, clave = servidor_de_prueba(self, os.path.join(self.tmp.name, 'datos'))
        admin = sbd.Sesion(self.srv_prueba)
        self.addCleanup(admin.cerrar)
        admin.ejecutar('crear usuario jefa clave "clave de la jefa" puede todo  '
                       'crear usuario lectora clave "clave de lectora" puede leer')
        self.motor = bd.crear_motor(f'sbd+http://:{clave}@127.0.0.1:{self.puerto_sbd}/biblioteca')
        self.addCleanup(self.motor.dispose)
        preparar_datos(self.motor)
        preparar_datos(self.motor)   # la segunda vez no duplica nada
        web = crear_servidor(('127.0.0.1', 0), self.motor, f'http://127.0.0.1:{self.puerto_sbd}', 'prueba')
        threading.Thread(target=web.serve_forever, daemon=True).start()
        self.addCleanup(web.server_close)
        self.addCleanup(web.shutdown)
        self.base = f'http://127.0.0.1:{web.server_address[1]}'

    def peticion(self, ruta, cuerpo=None, galleta=None, tipo='application/json'):
        cabeceras = {'Cookie': galleta} if galleta else {}
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            cabeceras['Content-Type'] = tipo
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + ruta, data=datos, headers=cabeceras)) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def entrar(self, usuario, clave):
        req = urllib.request.Request(f'http://127.0.0.1:{self.puerto_sbd}/api/entrar', headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'usuario': usuario, 'clave': clave}).encode())
        with urllib.request.urlopen(req) as r:
            return r.headers['Set-Cookie'].split(';')[0]

    def test_mirar_es_publico_y_ensena_el_camino(self):
        estado, r = self.peticion('/api/libros?q=borges')
        self.assertEqual(estado, 200)
        self.assertEqual({x['autor'] for x in r['datos']}, {'Jorge Luis Borges'})
        self.assertEqual(r['datos'][0]['precio'], '11.90')
        paso = r['ruta'][0]
        self.assertTrue(paso['sql'].startswith('SELECT libros.id'))
        self.assertTrue(paso['sbd'].startswith('buscar libros'))
        self.assertIn('%borges%', paso['valores'])
        self.assertEqual(self.peticion('/api/estado')[1]['puede'], False)
        for ruta in ('/api/socios', '/api/prestamos', '/api/informes'):
            self.assertEqual(self.peticion(ruta)[0], 200, ruta)

    def test_el_correo_solo_lo_ve_quien_administra(self):
        anonimo = json.dumps([self.peticion(r)[1]['datos'] for r in ('/api/socios', '/api/informes')], ensure_ascii=False)
        self.assertNotIn('ana@ejemplo.es', anonimo)
        self.assertIn('a•••@ejemplo.es', anonimo)
        jefa = self.entrar('jefa', 'clave de la jefa')
        self.assertIn('ana@ejemplo.es', json.dumps(self.peticion('/api/socios', galleta=jefa)[1]['datos']))

    def test_cambiar_exige_permiso_todo(self):
        pedido = {'socio': 4, 'libros': [2]}
        self.assertEqual(self.peticion('/api/prestar', pedido)[0], 403)
        lectora = self.entrar('lectora', 'clave de lectora')
        self.assertEqual(self.peticion('/api/prestar', pedido, lectora)[0], 403)
        self.assertEqual(self.peticion('/api/prestar', pedido, 'sbd_sesion=inventada')[0], 403)
        jefa = self.entrar('jefa', 'clave de la jefa')
        self.assertTrue(self.peticion('/api/estado', galleta=jefa)[1]['puede'])
        estado, r = self.peticion('/api/prestar', pedido, jefa)
        self.assertEqual(estado, 200)
        self.assertIn('El Aleph', r['mensaje'])
        self.assertEqual([p['sbd'] for p in r['ruta']][-1], 'confirmar')
        self.assertTrue(any(p['sbd'].startswith('insertar en prestamos') for p in r['ruta']))

    def test_la_regla_rota_vuelve_como_error_y_no_cambia_nada(self):
        jefa = self.entrar('jefa', 'clave de la jefa')
        antes = self.peticion('/api/prestamos')[1]['datos']
        _, r = self.peticion('/api/prestar', {'socio': 1, 'libros': [2, 5, 6]}, jefa)   # ya tiene dos
        self.assertIn('más de 3', r['error'])
        self.assertEqual(self.peticion('/api/prestamos')[1]['datos'], antes)
        _, r = self.peticion('/api/socios', {'nombre': 'Otra', 'email': 'ana@ejemplo.es'}, jefa)
        self.assertIn('no se puede repetir', r['error'])
        self.assertEqual(r['ruta'][-1]['sbd'], 'deshacer')

    def test_datos_que_no_valen(self):
        jefa = self.entrar('jefa', 'clave de la jefa')
        for ruta, cuerpo in [('/api/prestar', {'socio': 'x', 'libros': [1]}), ('/api/prestar', {'socio': 1, 'libros': []}),
                             ('/api/prestar', {'socio': 1, 'libros': [1, 2, 3, 4]}), ('/api/devolver', {'prestamo': True}),
                             ('/api/socios', {'nombre': '', 'email': 'a'}), ('/api/socios', {'nombre': 'x' * 81, 'email': 'a'}),
                             ('/api/libros', {'titulo': 'T', 'isbn': 'i', 'autor': 'A', 'precio': 'mucho'}),
                             ('/api/libros', {'titulo': 'T', 'isbn': 'i', 'autor': 'A', 'precio': '-1'})]:
            estado, r = self.peticion(ruta, cuerpo, jefa)
            self.assertIn('error', r, (ruta, cuerpo))
        self.assertEqual(self.peticion('/api/socios', {'nombre': 'x', 'email': 'y'}, jefa, 'text/plain')[0], 415)
        self.assertEqual(self.peticion('/api/libros?q=' + 'x' * 101)[0], 400)
        self.assertEqual(self.peticion('/no/existe')[0], 404)

    def test_una_busqueda_no_se_puede_inyectar(self):
        malo = urllib.parse.quote('x" vaciar libros --')
        self.assertEqual(self.peticion(f'/api/libros?q={malo}')[1]['datos'], [])
        self.assertEqual(len(self.peticion('/api/libros')[1]['datos']), 6)

    def test_alta_de_libro_con_todo(self):
        jefa = self.entrar('jefa', 'clave de la jefa')
        _, r = self.peticion('/api/libros', {'titulo': 'Nuevo', 'isbn': '999', 'autor': 'Nadie', 'precio': '3,50', 'ejemplares': 2, 'generos': 'Novela, Ensayo'}, jefa)
        self.assertIn('Nuevo', r['mensaje'])
        libro = self.peticion('/api/libros?q=Nuevo')[1]['datos'][0]
        self.assertEqual((libro['precio'], libro['generos'], libro['disponibles']), ('3.50', ['Ensayo', 'Novela'], 2))

    def test_la_pagina_se_sirve(self):
        for ruta, trozo in [('/', b'El camino'), ('/app.js', b'pintarRuta'), ('/app.css', b'--papel')]:
            with urllib.request.urlopen(self.base + ruta) as r:
                self.assertIn(trozo, r.read())
                self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])


class TestBiblioteca(ConDatos):
    def sesion(self):
        s = Session(self.motor, expire_on_commit=False)
        self.addCleanup(s.close)
        return s

    def biblioteca(self):
        s = self.sesion()
        b = Biblioteca(s)
        self.autor_libro = b.alta_libro('Uno', '1', 'Autora', precio='10', ejemplares=1, generos=['G', 'H'])
        b.alta_libro('Dos', '2', 'Autora', precio='5', ejemplares=2)
        self.socio = b.alta_socio('Ana', 'a@x')
        return b

    def test_relaciones(self):
        b = self.biblioteca()
        with Session(self.motor) as otra:
            libro = otra.get(Libro, 1)
            self.assertEqual(libro.autor.nombre, 'Autora')
            self.assertEqual([x.titulo for x in libro.autor.libros], ['Uno', 'Dos'])
            self.assertEqual([g.nombre for g in libro.generos], ['G', 'H'])
            self.assertEqual([x.titulo for x in libro.generos[0].libros], ['Uno'])
        self.assertEqual(b.cuantos(Autor), 1)

    def test_prestar_y_devolver(self):
        b = self.biblioteca()
        p = b.prestar(self.socio.id, 1, hoy=HOY)[0]
        self.assertEqual(p.vence, HOY + datetime.timedelta(days=15))
        self.assertEqual(b.libro(1).disponibles, 0)
        with self.assertRaises(ErrorBiblioteca):
            b.prestar(self.socio.id, 1, hoy=HOY)
        b.devolver(p.id, hoy=HOY + datetime.timedelta(days=20))
        self.assertEqual(b.libro(1).disponibles, 1)
        self.assertEqual(p.multa, Decimal('2.50'))
        with self.assertRaises(ErrorBiblioteca):
            b.devolver(p.id)

    def test_todo_o_nada_y_no_queda_nada_en_disco(self):
        b = self.biblioteca()
        b.prestar(self.socio.id, 1, hoy=HOY)
        with self.assertRaises(ErrorBiblioteca):
            b.prestar(self.socio.id, [2, 1], hoy=HOY)   # el 2 sí se puede; el 1 ya no quedan
        self.assertEqual(b.libro(2).disponibles, 2)
        self.assertEqual(b.cuantos(Prestamo), 1)
        b.sesion.close()
        self.motor.dispose()
        db = sbd.conectar(self.ruta)
        self.assertEqual(db.execute('buscar prestamos mostrar contar()').scalar(), 1)
        self.assertEqual(db.execute('buscar libros donde id = 2 mostrar disponibles').scalar(), 2)
        db.close()

    def test_la_base_defiende_sus_reglas_aunque_falle_la_aplicacion(self):
        b = self.biblioteca()
        libro = b.libro(1)
        libro.disponibles = -1
        with self.assertRaises(exc.IntegrityError):
            b.sesion.commit()
        b.sesion.rollback()
        self.assertEqual(b.libro(1).disponibles, 1)
        with self.assertRaises(exc.IntegrityError):
            b.alta_socio('Otra', 'a@x')
        with self.assertRaises(exc.IntegrityError):
            b.sesion.add(Prestamo(socio_id=99, libro_id=1, vence=HOY))
            b.sesion.commit()
        b.sesion.rollback()

    def test_version_detecta_dos_cambios_a_la_vez(self):
        from sqlalchemy.orm.exc import StaleDataError
        b = self.biblioteca()
        libro = b.libro(1)
        b.sesion.commit()
        self.assertEqual(libro.version, 1)
        with Session(self.motor) as otra:
            otra.get(Libro, 1).precio = Decimal('20')
            otra.commit()
        libro.precio = Decimal('30')
        with self.assertRaises(StaleDataError):
            b.sesion.commit()
        b.sesion.rollback()
        self.assertEqual(b.libro(1).precio, Decimal('20'))

    def test_borrar_en_cascada(self):
        b = self.biblioteca()
        p = b.prestar(self.socio.id, 1, hoy=HOY)[0]
        with self.assertRaises(ErrorBiblioteca):
            b.baja_socio(self.socio.id)
        b.devolver(p.id, hoy=HOY)
        b.baja_socio(self.socio.id)
        self.assertEqual(b.cuantos(Prestamo), 0)
        s = b.sesion
        autor = s.scalar(select(Autor))
        s.delete(autor)
        s.commit()
        self.assertEqual((b.cuantos(Autor), b.cuantos(Libro)), (0, 0))
        self.assertEqual(s.execute(text('SELECT COUNT(*) FROM libro_genero')).scalar(), 0)

    def test_sobrevive_al_reinicio_y_sbd_lo_ve_como_suyo(self):
        b = self.biblioteca()
        b.prestar(self.socio.id, [1, 2], hoy=HOY)
        b.sesion.close()
        self.motor.dispose()
        otro = bd.crear_motor(f'sbd:///{self.ruta}')
        self.addCleanup(otro.dispose)
        with bd.fabrica_sesiones(otro)() as s:
            self.assertEqual(Biblioteca(s).cuantos(Prestamo), 2)
            self.assertEqual(s.get(Libro, 1).prestamos[0].fecha, HOY)
        otro.dispose()
        with sbd.conectar(self.ruta) as db:
            esquema = '\n'.join(db.schema_sbd())
            self.assertIn('crear tabla libros', esquema)
            self.assertIn('dinero', esquema)
            self.assertEqual(db.execute('buscar prestamos como p unir libros como l en l.id = p.libro_id '
                                        'ordenar p.id mostrar l.titulo').rows, [('Uno',), ('Dos',)])

    def test_informes_con_sql(self):
        s = self.sesion()
        ejemplo.cargar(s)
        b = Biblioteca(s)
        self.assertEqual(b.mas_prestados(1)[0].prestamos, 2)
        self.assertEqual([m.nombre for m in b.morosos()], ['Ana Ruiz', 'Luis Gómez'])
        self.assertEqual(b.socios_sin_prestamos(), ['Marcos Peña'])
        self.assertEqual(b.recaudado_por_autor()[0].estado, 'con multas')
        self.assertEqual(b.ranking_por_genero()[0].puesto, 1)


if __name__ == '__main__':
    unittest.main()
