import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import sbd
from sbd import SBDError
from sbd.errors import IncompleteInputError


def rows(db, text, params=()):
    return db.execute(text, params).fetchall()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = sbd.conectar()
        self.db.execute("""
            crear tabla dept { id entero clave  nombre texto obligatorio unico }
            crear tabla emp {
              id       entero clave
              nombre   texto(20) obligatorio
              dept_id  entero -> dept(id)
              salario  decimal comprobar salario > 0
              jefe_id  entero -> emp(id)
              activo   logico = verdadero
            }
            insertar en dept (nombre) ("Ventas") ("IT") ("RRHH")
            insertar en emp (nombre, dept_id, salario, jefe_id)
              ("Ana", 2, 3000, nulo) ("Bea", 2, 2500, 1) ("Carlos", 1, 2000, 1)
              ("Dani", 1, 1800, 3) ("Eva", nulo, 1500, nulo)
        """)


class TestLenguaje(Base):
    def test_sin_terminador_y_clausulas_en_cualquier_orden(self):
        self.assertEqual(rows(self.db, "buscar emp mostrar nombre donde salario >= 2000 ordenar salario desc limite 2"),
                         [('Ana',), ('Bea',)])
        self.assertEqual(rows(self.db, "buscar emp { limite 2  saltar 3  ordenar id  mostrar nombre }"),
                         [('Dani',), ('Eva',)])
        results = self.db.executescript("calcular 1  calcular 2; calcular 3")
        self.assertEqual([r.scalar() for r in results], [1, 2, 3])

    def test_literales(self):
        r = self.db.execute('calcular "con ""comillas""", \'simples\', 2024-05-01, 2024-05-01 10:30, '
                            'verdadero, falso, nulo, -3, 2.5e1').fetchone()
        self.assertEqual(r, ('con "comillas"', 'simples', '2024-05-01', '2024-05-01 10:30', True, False, None, -3, 25.0))

    def test_expresiones_y_funciones(self):
        r = self.db.execute('calcular 1 + 2 * 3, 7 / 2, 7.0 / 2, 7 % 3, "a" & "b" & 1, mayusculas("x"), '
                            'longitud("hola"), redondear(2.5), redondear(3.14159, 2), primero_no_nulo(nulo, 5), '
                            'nulo_si(1, 1), absoluto(-4), extraer("abcdef", 2, 3), reemplazar("aXa", "X", "b"), '
                            'recortar("  z  "), entero("42"), texto(7), año(2024-05-01)').fetchone()
        self.assertEqual(r, (7, 3, 3.5, 1, 'ab1', 'X', 4, 3, 3.14, 5, None, 4, 'bcd', 'aba', 'z', 42, '7', 2024))

    def test_logica_con_nulos(self):
        self.assertEqual(self.db.execute("calcular nulo = nulo, nulo es nulo, 1 en [2, nulo], nulo y falso, "
                                         "nulo o verdadero, no nulo, 5 no es nulo").fetchone(),
                         (None, True, None, False, True, None, True))
        self.assertEqual(rows(self.db, "buscar emp donde dept_id es nulo mostrar nombre"), [('Eva',)])
        self.assertEqual(rows(self.db, "buscar emp donde dept_id != 1 mostrar contar()"), [(2,)])

    def test_busquedas_de_texto(self):
        q = lambda cond: [r[0] for r in rows(self.db, f"buscar emp donde {cond} ordenar nombre mostrar nombre")]
        self.assertEqual(q('nombre contiene "AR"'), ['Carlos'])
        self.assertEqual(q('nombre empieza por "b" o nombre termina en "va"'), ['Bea', 'Eva'])
        self.assertEqual(q('nombre no contiene "a"'), [])
        self.assertEqual(q('nombre parece "_a%"'), ['Carlos', 'Dani'])
        self.assertEqual(q('salario entre 1800 y 2500'), ['Bea', 'Carlos', 'Dani'])
        self.assertEqual(q('id no en [1, 2, 3]'), ['Dani', 'Eva'])

    def test_caso(self):
        self.assertEqual(rows(self.db, 'buscar emp donde id < 3 mostrar caso cuando salario > 2600 entonces "alto" '
                                       'sino "medio" fin'), [('alto',), ('medio',)])
        self.assertEqual(rows(self.db, 'buscar emp ordenar id mostrar caso dept_id cuando 1 entonces "V" '
                                       'cuando 2 entonces "I" fin como d'),
                         [('I',), ('I',), ('V',), ('V',), (None,)])

    def test_errores_explican_que_falta(self):
        casos = {
            'select * from emp': 'buscar tabla',
            'buscar emp mostrar id nombre': 'falta una coma',
            'actualizar emp poner salario = 1': 'todas',
            'borrar de emp': 'vaciar emp',
            'buscar nada': 'no existe ninguna tabla',
            'buscar emp donde x = 1': 'columna «x»',
            'buscar emp, dept': 'aquí debería acabar',
            'calcular 1 / 0': 'división por cero',
            'calcular "a" + 1': 'no es un valor numérico',
            'calcular foo(1)': 'función desconocida',
        }
        for texto, pista in casos.items():
            with self.assertRaises(SBDError, msg=texto) as ctx:
                self.db.execute(texto)
            self.assertIn(pista, str(ctx.exception), texto)

    def test_ordenes_incompletas(self):
        for texto in ['buscar emp {', 'buscar emp donde', 'insertar en emp { nombre: "x"', 'calcular "sin cerrar']:
            with self.assertRaises(IncompleteInputError, msg=texto):
                self.db.execute(texto)

    def test_nombres_entre_comillas_invertidas(self):
        self.db.execute('crear tabla `mis notas` { `y` entero  `fecha de alta` fecha  clave texto  fecha fecha }')
        self.db.execute('insertar en `mis notas` { `y`: 1, `fecha de alta`: 2024-01-01, clave: "k", fecha: 2024-02-02 }')
        self.assertEqual(rows(self.db, 'buscar `mis notas` mostrar `y`, `fecha de alta`, clave, fecha'),
                         [(1, '2024-01-01', 'k', '2024-02-02')])


class TestConsultas(Base):
    def test_agrupar(self):
        r = rows(self.db, "buscar emp agrupar dept_id teniendo contar() > 1 ordenar dept_id "
                          "mostrar dept_id, contar(), sumar(salario), media(salario), minimo(nombre), maximo(salario)")
        self.assertEqual(r, [(1, 2, 3800.0, 1900.0, 'Carlos', 2000.0), (2, 2, 5500.0, 2750.0, 'Ana', 3000.0)])
        self.assertEqual(rows(self.db, "buscar emp mostrar contar(), contar(dept_id), contar(distintos dept_id)"), [(5, 4, 2)])
        self.assertEqual(rows(self.db, "buscar emp donde id > 100 mostrar contar(), sumar(salario)"), [(0, None)])
        self.assertEqual(rows(self.db, 'buscar emp donde dept_id = 2 mostrar juntar(nombre, "|")'), [('Ana|Bea',)])
        with self.assertRaises(SBDError):
            self.db.execute("buscar emp donde contar() > 1")

    def test_distintos_y_nulos_al_ordenar(self):
        self.assertEqual(rows(self.db, "buscar emp mostrar distintos dept_id ordenar dept_id"), [(None,), (1,), (2,)])
        self.assertEqual(rows(self.db, "buscar emp distintos mostrar dept_id ordenar dept_id desc"), [(2,), (1,), (None,)])
        self.assertEqual(rows(self.db, "buscar emp mostrar dept_id ordenar dept_id nulos ultimo limite 1"), [(1,)])

    def test_unir(self):
        self.assertEqual(len(rows(self.db, "buscar emp como e unir dept como d en e.dept_id = d.id")), 4)
        self.assertEqual(rows(self.db, "buscar emp como e unir opcional dept como d en d.id = e.dept_id "
                                       "donde d.id es nulo mostrar e.nombre, d.nombre"), [('Eva', None)])
        self.assertEqual(rows(self.db, "buscar emp como e unir inverso dept como d en e.dept_id = d.id "
                                       "donde e.id es nulo mostrar d.nombre"), [('RRHH',)])
        self.assertEqual(len(rows(self.db, "buscar emp como e unir completo dept como d en e.dept_id = d.id")), 6)
        self.assertEqual(rows(self.db, "buscar emp cruzar dept mostrar contar()"), [(15,)])
        self.assertEqual(rows(self.db, "buscar emp como e unir emp como j en e.jefe_id = j.id "
                                       'donde j.nombre = "Carlos" mostrar e.nombre, j.nombre'), [('Dani', 'Carlos')])
        self.db.execute('crear tabla a { k entero  x texto }  crear tabla b { k entero  y texto }'
                        'insertar en a (1, "a1") (2, "a2")  insertar en b (2, "b2") (3, "b3")')
        self.assertEqual(rows(self.db, "buscar a unir b usando k mostrar x, y"), [('a2', 'b2')])

    def test_subconsultas(self):
        self.assertEqual(rows(self.db, "buscar emp donde salario > (buscar emp mostrar media(salario)) "
                                       "ordenar nombre mostrar nombre"), [('Ana',), ('Bea',)])
        self.assertEqual(rows(self.db, "buscar dept como d donde no existe (buscar emp como e donde e.dept_id = d.id) "
                                       "mostrar nombre"), [('RRHH',)])
        self.assertEqual(rows(self.db, "buscar (buscar emp agrupar dept_id mostrar dept_id como d, contar() como n) como t "
                                       "donde t.n = 2 ordenar t.d"), [(1, 2), (2, 2)])
        self.assertEqual(rows(self.db, "buscar dept donde id en (buscar emp donde salario > 2900 mostrar dept_id) "
                                       "mostrar nombre"), [('IT',)])
        with self.assertRaises(SBDError):
            self.db.execute("calcular (buscar emp mostrar id)")

    def test_uniones_de_consultas(self):
        self.assertEqual(rows(self.db, "buscar emp donde dept_id no es nulo mostrar dept_id union "
                                       "buscar dept mostrar id ordenar 1"), [(1,), (2,), (3,)])
        self.assertEqual(len(rows(self.db, "buscar emp mostrar dept_id union todo buscar dept mostrar id")), 8)
        self.assertEqual(rows(self.db, "buscar dept mostrar id comun buscar emp mostrar dept_id ordenar id"), [(1,), (2,)])
        self.assertEqual(rows(self.db, "buscar dept mostrar id excepto buscar emp mostrar dept_id"), [(3,)])
        with self.assertRaises(SBDError):
            self.db.execute("buscar dept ordenar id mostrar id union buscar emp mostrar id")

    def test_consultas_auxiliares_y_recursivas(self):
        self.assertEqual(rows(self.db, "con ricos = (buscar emp donde salario > 2000) buscar ricos mostrar contar()"), [(2,)])
        r = rows(self.db, """
            con sub(id, nombre, nivel) = (
                buscar emp donde nombre = "Ana" mostrar id, nombre, 0
                union todo
                buscar emp como e unir sub como s en e.jefe_id = s.id mostrar e.id, e.nombre, s.nivel + 1
            )
            buscar sub ordenar nivel, nombre mostrar nombre, nivel""")
        self.assertEqual(r, [('Ana', 0), ('Bea', 1), ('Carlos', 1), ('Dani', 2)])
        self.assertEqual(rows(self.db, "con n(x) = (calcular 1 union todo buscar n donde x < 100 mostrar x + 1) "
                                       "buscar n mostrar sumar(x)"), [(5050,)])

    def test_parametros(self):
        self.assertEqual(rows(self.db, "buscar emp donde dept_id = ? y salario > ? mostrar nombre", [2, 2600]), [('Ana',)])
        with self.assertRaises(SBDError):
            self.db.execute("calcular ?", [])


class TestDatos(Base):
    def test_insertar(self):
        r = self.db.execute('insertar en dept { nombre: "Legal" }')
        self.assertEqual((r.rowcount, r.lastrowid), (1, 4))
        self.db.execute('insertar en dept (10, "Diez")')
        self.assertEqual(self.db.execute('insertar en dept { nombre: "Once" }').lastrowid, 11)
        self.db.execute("crear tabla copia { n texto }")
        self.assertEqual(self.db.execute("insertar en copia desde buscar emp donde salario > 2000 mostrar nombre").rowcount, 2)
        self.db.execute('insertar en emp { nombre: "Fer", salario: 1000, activo: defecto } { nombre: "Gil", salario: 900 }')
        self.assertEqual(rows(self.db, 'buscar emp donde nombre en ["Fer", "Gil"] mostrar activo'), [(True,), (True,)])

    def test_tipos(self):
        self.db.execute("crear tabla t { i entero  r decimal  s texto(3)  b logico  d fecha  ts fechahora }")
        self.db.execute('insertar en t ("5", 2, 7, "sí", 2024-03-01, "2024-03-01T10:00")')
        self.assertEqual(rows(self.db, "buscar t"), [(5, 2.0, '7', True, '2024-03-01', '2024-03-01 10:00:00')])
        for bad in ['insertar en t { i: "abc" }', 'insertar en t { s: "abcd" }',
                    'insertar en t { d: "2024-13-01" }', 'insertar en t { b: "quizá" }']:
            with self.assertRaises(SBDError, msg=bad):
                self.db.execute(bad)

    def test_actualizar_y_borrar(self):
        self.assertEqual(self.db.execute("actualizar emp poner salario = salario * 1.1 donde dept_id = 1").rowcount, 2)
        self.assertEqual(rows(self.db, "buscar emp donde id = 3 mostrar redondear(salario)"), [(2200.0,)])
        self.db.execute("actualizar emp poner nombre = mayusculas(nombre), activo = falso donde id = 5")
        self.assertEqual(rows(self.db, "buscar emp donde id = 5 mostrar nombre, activo"), [('EVA', False)])
        self.assertEqual(self.db.execute("actualizar emp poner activo = verdadero todas").rowcount, 5)
        self.assertEqual(self.db.execute("borrar de emp donde id = 5").rowcount, 1)
        self.assertEqual(self.db.execute("buscar emp mostrar contar()").scalar(), 4)
        self.db.execute("vaciar emp")
        self.assertEqual(self.db.execute("buscar emp mostrar contar()").scalar(), 0)

    def test_restricciones(self):
        for bad in ['insertar en dept { nombre: "IT" }',
                    'insertar en dept (1, "Otro")',
                    'insertar en emp { nombre: "X", salario: -1 }',
                    'insertar en emp { salario: 10 }',
                    'insertar en emp { nombre: "X", dept_id: 99 }',
                    'actualizar dept poner nombre = "IT" donde id = 1',
                    'borrar de dept donde id = 1',
                    'actualizar dept poner id = 50 donde id = 1']:
            with self.assertRaises(SBDError, msg=bad):
                self.db.execute(bad)
        self.db.execute("borrar de dept donde id = 3")
        self.db.execute("vaciar emp")  # se referencian entre sí, pero se borran todas a la vez

    def test_cada_orden_es_atomica(self):
        with self.assertRaises(SBDError):
            self.db.execute('insertar en dept (nombre) ("Nuevo") ("IT")')
        self.assertEqual(self.db.execute("buscar dept mostrar contar()").scalar(), 3)
        with self.assertRaises(SBDError):
            self.db.execute("actualizar emp poner salario = salario - 2000 todas")
        self.assertEqual(self.db.execute("buscar emp mostrar sumar(salario)").scalar(), 10800.0)

    def test_al_borrar_cascada_y_anular(self):
        self.db.execute("""
            crear tabla autor { id entero clave  n texto }
            crear tabla libro { id entero clave  autor_id entero -> autor al borrar cascada }
            crear tabla resena { id entero clave  libro_id entero -> libro(id) al borrar anular }
            insertar en autor (1, "A") (2, "B")
            insertar en libro (1, 1) (2, 1) (3, 2)
            insertar en resena (1, 1) (2, 3)
        """)
        self.db.execute("borrar de autor donde id = 1")
        self.assertEqual(rows(self.db, "buscar libro mostrar id"), [(3,)])
        self.assertEqual(rows(self.db, "buscar resena ordenar id"), [(1, None), (2, 3)])


class TestEstructura(Base):
    def test_cambiar_estructura(self):
        self.db.execute("añadir a dept planta entero obligatorio = 1")
        self.assertEqual(rows(self.db, "buscar dept donde id = 1 mostrar planta"), [(1,)])
        self.db.execute("renombrar columna dept.planta a piso")
        self.assertEqual(rows(self.db, "buscar dept donde id = 1 mostrar piso"), [(1,)])
        self.db.execute("quitar de dept piso")
        with self.assertRaises(SBDError):
            self.db.execute("buscar dept mostrar piso")
        self.db.execute("renombrar tabla dept a departamento")
        with self.assertRaises(SBDError):  # la referencia de emp sigue a la tabla renombrada
            self.db.execute('insertar en emp { nombre: "X", dept_id: 99 }')
        self.db.execute("renombrar columna departamento.id a dep_id")
        self.db.execute('insertar en emp { nombre: "X", dept_id: 1, salario: 1 }')
        with self.assertRaises(SBDError):
            self.db.execute("quitar de emp id")

    def test_columna_que_se_llama_como_un_tipo(self):
        # «clave fecha …» puede ser una columna «clave» o la marca «clave» seguida de la columna «fecha»
        self.db.execute('crear tabla a { id entero clave  fecha fecha }')
        self.db.execute('crear tabla b { clave texto  fecha fecha }')
        self.db.execute('crear tabla c { id entero unico  texto texto(20) obligatorio }')
        self.assertEqual([(c.name, c.type_sbd()) for c in self.db.tables['a'].columns],
                         [('id', 'entero'), ('fecha', 'fecha')])
        self.assertEqual(self.db.tables['a'].primary_key, ['id'])
        self.assertEqual([(c.name, c.type_sbd()) for c in self.db.tables['b'].columns],
                         [('clave', 'texto'), ('fecha', 'fecha')])
        self.assertEqual([(c.name, c.type_sbd(), c.not_null) for c in self.db.tables['c'].columns],
                         [('id', 'entero', False), ('texto', 'texto(20)', True)])

    def test_indices(self):
        self.db.execute("indexar emp (salario) como idx_salario")
        self.assertEqual(rows(self.db, "buscar emp donde salario = 2000 mostrar nombre"), [('Carlos',)])
        self.assertEqual(rows(self.db, 'buscar emp donde salario = "2000" mostrar nombre'), [('Carlos',)])
        self.db.execute('actualizar emp poner salario = 2100 donde nombre = "Carlos"')
        self.assertEqual(rows(self.db, "buscar emp donde salario = 2000"), [])
        with self.assertRaises(SBDError):
            self.db.execute("indexar unico emp (dept_id)")
        self.db.execute("indexar unico emp (nombre)")
        with self.assertRaises(SBDError):
            self.db.execute('insertar en emp { nombre: "Ana", salario: 1 }')
        self.db.execute("borrar indice emp_nombre_idx")
        self.db.execute('insertar en emp { nombre: "Ana", salario: 1 }')
        with self.assertRaises(SBDError):
            self.db.execute("borrar indice dept_pkey")

    def test_vistas_y_tablas_desde_consulta(self):
        self.db.execute("crear vista resumen = buscar dept como d unir opcional emp como e en e.dept_id = d.id "
                        "agrupar d.nombre mostrar d.nombre como dept, contar(e.id) como n")
        self.assertEqual(rows(self.db, "buscar resumen ordenar n desc, dept"), [('IT', 2), ('Ventas', 2), ('RRHH', 0)])
        self.db.execute("crear tabla ricos = buscar emp donde salario > 2000 mostrar nombre, salario")
        self.assertEqual(rows(self.db, "describir ricos")[1][1], 'decimal')
        self.db.execute("borrar vista resumen")
        with self.assertRaises(SBDError):
            self.db.execute("borrar tabla dept")  # emp apunta a ella

    def test_listados(self):
        self.assertEqual([r[0] for r in rows(self.db, "tablas")], ['dept', 'emp'])
        self.assertEqual(rows(self.db, "describir dept")[0][:4], ('id', 'entero', 'sí', 'clave'))
        self.assertTrue(rows(self.db, "indices de dept"))

    def test_varios_comprobar_de_tabla_no_se_pisan(self):
        # Antes, el primero se pegaba a la última columna y el segundo lo sobrescribía
        self.db.execute('crear tabla dos { a entero  b entero  comprobar a > 0  comprobar b > 0 }')
        self.db.execute('''crear tabla mov {
            tipo texto
            n entero
            comprobar (tipo = "E" y n > 0) o (tipo = "S" y n < 0)
            comprobar n != 0
        }''')
        for orden in ('insertar en dos { a: -1, b: 5 }', 'insertar en dos { a: 5, b: -1 }',
                      'insertar en mov { tipo: "S", n: 3 }', 'insertar en mov { tipo: "X", n: 0 }'):
            with self.subTest(orden=orden), self.assertRaises(SBDError):
                self.db.execute(orden)
        self.db.execute('insertar en dos { a: 1, b: 1 }')
        self.db.execute('insertar en mov { tipo: "S", n: -3 }')
        copia = sbd.conectar()
        copia.execute('\n'.join(self.db.schema_sbd()))
        with self.assertRaises(SBDError):
            copia.execute('insertar en dos { a: -1, b: 5 }')

    def test_estructura_se_escribe_en_sbd_y_se_relee(self):
        self.db.execute('crear tabla rara { `con espacio` texto = "x"  n entero comprobar n > 0  unico (n, `con espacio`) }')
        copia = sbd.conectar()
        copia.execute('\n'.join(self.db.schema_sbd()))
        self.assertEqual(copia.schema_sbd(), self.db.schema_sbd())


class TestTransacciones(Base):
    def test_deshacer_lo_restaura_todo(self):
        self.db.execute("empezar")
        self.db.execute('insertar en dept { nombre: "Temp" }')
        self.db.execute("actualizar emp poner salario = 1 donde id = 1")
        self.db.execute("borrar de emp donde id = 5")
        self.db.execute("crear tabla tmp { x entero }")
        self.db.execute("añadir a dept extra texto")
        self.db.execute("indexar emp (nombre) como i")
        self.db.execute("deshacer")
        self.assertEqual(self.db.execute("buscar dept mostrar contar()").scalar(), 3)
        self.assertEqual(self.db.execute("buscar emp donde id = 1 mostrar salario").scalar(), 3000.0)
        self.assertEqual([r[0] for r in rows(self.db, "buscar emp mostrar id")], [1, 2, 3, 4, 5])
        self.assertNotIn('tmp', self.db.tables)
        self.assertFalse(self.db.tables['dept'].has_column('extra'))
        self.assertNotIn('i', self.db.index_owner)
        with self.assertRaises(SBDError):
            self.db.execute('insertar en dept { nombre: "IT" }')

    def test_confirmar_y_orden_fallida_dentro_de_transaccion(self):
        self.db.execute('empezar  insertar en dept { nombre: "A" }')
        with self.assertRaises(SBDError):
            self.db.execute('insertar en dept (nombre) ("B") ("IT")')
        self.db.execute("confirmar")
        self.assertEqual(rows(self.db, "buscar dept donde id > 3 mostrar nombre"), [('A',)])
        with self.assertRaises(SBDError):
            self.db.execute("confirmar")


class TestServidor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = sbd.servidor(self.tmp.name)

    def tearDown(self):
        self.srv.cerrar()
        self.tmp.cleanup()

    def test_varias_bases(self):
        s = sbd.Sesion(self.srv)
        with self.assertRaises(SBDError):
            s.ejecutar("tablas")  # todavía no hay base en uso
        s.ejecutar("crear base tienda  crear tabla p { id entero clave  n texto }  insertar en p { n: \"a\" }")
        s.ejecutar("crear base blog  crear tabla posts { id entero clave }")
        self.assertEqual(s.base, 'blog')
        self.assertEqual([r[0] for r in s.ejecutar("bases").rows], ['blog', 'tienda'])
        s.ejecutar("usar tienda")
        self.assertEqual(s.ejecutar("buscar p mostrar n").fetchall(), [('a',)])
        with self.assertRaises(SBDError):
            s.ejecutar("buscar posts")  # está en la otra base
        # se guarda en disco: otro servidor sobre el mismo directorio la ve
        self.srv.cerrar()
        self.srv = sbd.servidor(self.tmp.name)
        otro = sbd.Sesion(self.srv, 'tienda')
        self.assertEqual(otro.ejecutar("buscar p mostrar contar()").scalar(), 1)

    def test_cerrojo_impide_dos_programas_a_la_vez(self):
        with self.assertRaises(SBDError) as ctx:
            sbd.servidor(self.tmp.name)
        self.assertIn('ya está abierta en otro programa', str(ctx.exception))
        self.srv.cerrar()
        sbd.servidor(self.tmp.name).cerrar()  # al cerrarlo, queda libre

    def test_papelera_y_recuperar(self):
        s = sbd.Sesion(self.srv)
        s.ejecutar('crear base notas  crear tabla n { id entero clave  t texto }  insertar en n { t: "hola" }')
        s.ejecutar('borrar base notas')
        self.assertEqual([r[0] for r in s.ejecutar('papelera').rows], ['notas'])
        s.ejecutar('crear base notas')
        with self.assertRaises(SBDError):
            s.ejecutar('recuperar base notas')  # ya existe una con ese nombre
        s.ejecutar('recuperar base notas como notas_viejas  usar notas_viejas')
        self.assertEqual(s.ejecutar('buscar n mostrar t').fetchall(), [('hola',)])
        self.assertEqual(s.ejecutar('papelera').rows, [])

    def test_borrar_base_va_a_la_papelera(self):
        s = sbd.Sesion(self.srv)
        s.ejecutar("crear base vieja")
        mensaje = s.ejecutar("borrar base vieja").message
        self.assertIn('.papelera', mensaje)
        self.assertIsNone(s.base)
        self.assertEqual(self.srv.nombres(), [])
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, '.papelera'))), 1)
        s.ejecutar("borrar base vieja si existe")

    def test_nombres_de_base_seguros(self):
        s = sbd.Sesion(self.srv)
        for nombre in ['../fuera', 'Mayus', 'con espacio', '1empieza']:
            with self.assertRaises(SBDError, msg=nombre):
                self.srv.crear(nombre)

    def test_no_cambia_de_base_con_transaccion_abierta(self):
        s = sbd.Sesion(self.srv)
        s.ejecutar("crear base a  crear base b  usar a  empezar")
        with self.assertRaises(SBDError):
            s.ejecutar("usar b")
        s.ejecutar("deshacer  usar b")


class TestLenguajeAvanzado(Base):
    def test_insertar_o_actualizar(self):
        r = self.db.execute('insertar o actualizar en dept { nombre: "IT" } { nombre: "Legal" } devolver id, nombre')
        self.assertEqual(r.message, '1 fila insertada y 1 actualizada')
        self.assertEqual(r.rows, [(2, 'IT'), (4, 'Legal')])
        self.db.execute('insertar o actualizar en emp { id: 5, nombre: "Eva M.", salario: 1600 }')
        self.assertEqual(rows(self.db, 'buscar emp donde id = 5 mostrar nombre, salario, activo'), [('Eva M.', 1600.0, True)])
        self.db.execute('crear tabla suelta { x entero }')
        with self.assertRaises(SBDError):
            self.db.execute('insertar o actualizar en suelta { x: 1 }')

    def test_devolver(self):
        self.assertEqual(self.db.execute('insertar en dept { nombre: "Legal" } devolver *').rows, [(4, 'Legal')])
        self.assertEqual(self.db.execute('actualizar emp poner salario = salario + 1 donde dept_id = 1 '
                                         'devolver nombre, salario').rows, [('Carlos', 2001.0), ('Dani', 1801.0)])
        r = self.db.execute('borrar de emp donde id = 5 devolver nombre')
        self.assertEqual((r.rows, r.rowcount), ([('Eva',)], 1))

    def test_funciones_sobre_filas_vecinas(self):
        r = rows(self.db, """buscar emp donde dept_id no es nulo ordenar id mostrar nombre,
                numero() sobre (ordenar id) como n,
                puesto() sobre (por dept_id ordenar salario desc) como puesto,
                acumulado(salario) sobre (ordenar id) como acum,
                sumar(salario) sobre (por dept_id) como total_dept,
                anterior(nombre) sobre (ordenar id) como antes,
                siguiente(nombre, 1, "—") sobre (ordenar id) como despues""")
        self.assertEqual(r, [('Ana', 1, 1, 3000.0, 5500.0, None, 'Bea'),
                             ('Bea', 2, 2, 5500.0, 5500.0, 'Ana', 'Carlos'),
                             ('Carlos', 3, 1, 7500.0, 3800.0, 'Bea', 'Dani'),
                             ('Dani', 4, 2, 9300.0, 3800.0, 'Carlos', '—')])
        self.assertEqual(rows(self.db, 'buscar emp agrupar dept_id mostrar dept_id, '
                                       'puesto() sobre (ordenar sumar(salario) desc) como p ordenar p'),
                         [(2, 1), (1, 2), (None, 3)])
        self.db.execute('insertar en emp { nombre: "Fer", dept_id: 2, salario: 3000 }')
        self.assertEqual(rows(self.db, 'buscar emp donde dept_id = 2 ordenar id mostrar puesto() sobre (ordenar salario desc), '
                                       'puesto_denso() sobre (ordenar salario desc)'), [(1, 1), (3, 2), (1, 1)])
        for bad in ['buscar emp mostrar numero()', 'buscar emp donde numero() sobre () > 1',
                    'buscar emp mostrar mayusculas(nombre) sobre ()']:
            with self.assertRaises(SBDError, msg=bad):
                self.db.execute(bad)

    def test_renombrar_actualiza_las_vistas(self):
        self.db.execute('crear vista ricos = buscar emp donde salario > 2000 mostrar nombre como nombre, salario')
        self.assertIn('vistas actualizadas: ricos', self.db.execute('renombrar tabla emp a empleados').message)
        self.db.execute('renombrar columna empleados.nombre a apellido')
        self.assertEqual(self.db.views['ricos'][0], 'buscar empleados donde salario > 2000 mostrar apellido como nombre, salario')
        self.assertEqual(rows(self.db, 'buscar ricos ordenar nombre'), [('Ana', 3000.0), ('Bea', 2500.0)])
        with self.assertRaises(SBDError):
            self.db.execute('quitar de empleados salario')  # la vista la usa
        with self.assertRaises(SBDError):
            self.db.execute('borrar tabla empleados')       # la vista la usa


def _xlsx(filas):
    """Un .xlsx mínimo: cadenas compartidas, números y una celda con formato de fecha."""
    import io
    import zipfile
    compartidas, xml_filas = [], []
    for r, fila in enumerate(filas, 1):
        celdas = []
        for c, v in enumerate(fila):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(v, str):
                compartidas.append(v)
                celdas.append(f'<c r="{ref}" t="s"><v>{len(compartidas) - 1}</v></c>')
            elif isinstance(v, tuple):  # (número de serie, «fecha»)
                celdas.append(f'<c r="{ref}" s="1"><v>{v[0]}</v></c>')
            elif v is not None:
                celdas.append(f'<c r="{ref}"><v>{v}</v></c>')
        xml_filas.append(f'<row r="{r}">{"".join(celdas)}</row>')
    m = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook {m} {rel}><sheets><sheet name="H" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet {m}><sheetData>{"".join(xml_filas)}</sheetData></worksheet>')
        z.writestr('xl/sharedStrings.xml', f'<sst {m}>' + ''.join(f'<si><t>{t}</t></si>' for t in compartidas) + '</sst>')
        z.writestr('xl/styles.xml', f'<styleSheet {m}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    return buffer.getvalue()


class TestImportar(unittest.TestCase):
    def test_csv_en_castellano_crea_la_tabla(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'clientes.csv')
            texto = ('Nombre;Ciudad;Saldo;Alta;Código postal;Activo\n'
                     'Ana López;Madrid;1.234,50;15/01/2024;08001;sí\n'
                     'Luis;Sevilla;20;03/02/2024;41001;no\n'
                     'Sara;;3,5;;00123;\n')
            with open(ruta, 'wb') as f:
                f.write(texto.encode('cp1252'))
            db = sbd.conectar()
            self.assertIn('tabla clientes creada', db.execute(f'importar "{ruta}" en clientes').message)
            self.assertEqual([c.type for c in db.tables['clientes'].columns],
                             ['INTEGER', 'TEXT', 'TEXT', 'REAL', 'DATE', 'TEXT', 'BOOLEAN'])
            self.assertEqual(rows(db, 'buscar clientes donde id = 1'),
                             [(1, 'Ana López', 'Madrid', 1234.5, '2024-01-15', '08001', True)])
            self.assertEqual(rows(db, 'buscar clientes donde id = 3 mostrar ciudad, saldo, codigo_postal'),
                             [(None, 3.5, '00123')])
            db.execute(f'importar "{ruta}" en clientes')  # en una tabla que ya existe: añade
            self.assertEqual(db.execute('buscar clientes mostrar contar()').scalar(), 6)

    def test_columnas_que_no_encajan_no_importan_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'x.csv')
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write('nombre,edad,color\nAna,3,rojo\n')
            db = sbd.conectar()
            db.execute('crear tabla t { id entero clave  nombre texto  edad entero comprobar edad > 5 }')
            with self.assertRaises(SBDError) as ctx:
                db.execute(f'importar "{ruta}" en t')
            self.assertIn('color', str(ctx.exception))
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write('nombre,edad\nAna,30\nLuis,3\n')
            with self.assertRaises(SBDError):  # la segunda fila no cumple «comprobar»
                db.execute(f'importar "{ruta}" en t')
            self.assertEqual(db.execute('buscar t mostrar contar()').scalar(), 0)

    def test_excel(self):
        from sbd.importar import importar, leer_xlsx
        cabecera, filas = leer_xlsx(_xlsx([['Producto', 'Precio', 'Desde'],
                                           ['Té verde', 4.5, (45292, 'fecha')],
                                           ['Café', 12, None]]))
        self.assertEqual(cabecera, ['Producto', 'Precio', 'Desde'])
        self.assertEqual(filas, [['Té verde', 4.5, '2024-01-01'], ['Café', 12, None]])
        db = sbd.conectar()
        importar(db, 'productos', cabecera, filas)
        self.assertEqual(rows(db, 'buscar productos mostrar producto, precio, desde'),
                         [('Té verde', 4.5, '2024-01-01'), ('Café', 12.0, None)])

    def test_desde_la_web_no_se_leen_ficheros_del_servidor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with sbd.servidor(tmp) as srv:
                s = sbd.Sesion(srv, permite_ficheros=False)
                s.ejecutar('crear base b')
                with self.assertRaises(SBDError):
                    s.ejecutar('importar "/etc/passwd" en t')


class TestFechas(Base):
    def test_intervalos_y_cuentas(self):
        q = lambda t: self.db.execute(t).fetchone()
        self.assertEqual(q('calcular texto(3 dias), texto(2 semanas), texto(1 mes + 2 dias), texto(90 minutos)'),
                         ('3 días', '14 días', '1 mes 2 días', '1 hora 30 minutos'))
        self.assertEqual(q('calcular 2024-01-31 + 1 mes, 2024-02-29 + 1 año, 2024-03-01 - 1 dia, '
                           '2024-05-01 10:00 + 90 minutos'),
                         ('2024-02-29', '2025-02-28', '2024-02-29', '2024-05-01 11:30:00'))
        self.assertEqual(q('calcular 2024-05-10 - 2024-05-01, dias_entre(2024-01-01, 2024-12-31), '
                           'meses_entre(2024-01-31, 2024-03-30), edad(1990-10-29, 2026-10-28), edad(1990-10-28, 2026-10-28)'),
                         (9, 365, 1, 35, 36))
        self.assertEqual(q('calcular dia_semana(2026-10-28), nombre_mes(2024-05-01), inicio_mes(2024-05-17), '
                           'fin_mes(2024-02-10), fin_mes(2023-02-10)'),
                         ('miércoles', 'mayo', '2024-05-01', '2024-02-29', '2023-02-28'))

    def test_hace_y_dentro_de(self):
        import datetime
        hoy = datetime.date.today()
        self.assertEqual(self.db.execute('calcular hace 10 dias, dentro de 1 semana').fetchone(),
                         ((hoy - datetime.timedelta(days=10)).isoformat(), (hoy + datetime.timedelta(days=7)).isoformat()))
        self.db.execute('crear tabla t { id entero clave  f fecha  plazo entero }'
                        'insertar en t (f, plazo) (hoy() - 5 dias, 3) (2020-01-01, 10)')
        self.assertEqual(rows(self.db, 'buscar t donde f > hace 30 dias mostrar id, f + dias(plazo) = hoy() - 2 dias'),
                         [(1, True)])

    def test_errores_de_fechas(self):
        for texto in ['calcular 3 dias + 2', 'calcular "hola" + 1 dia', 'calcular 1.5 meses', 'calcular hace 5']:
            with self.assertRaises(SBDError, msg=texto):
                self.db.execute(texto)


class TestDinero(unittest.TestCase):
    def test_exacto_y_redondeo_comercial(self):
        from decimal import Decimal
        db = sbd.conectar()
        db.execute('crear tabla f { id entero clave  importe dinero  iva decimal = 0.21 }'
                   'insertar en f (importe) (0.1) (0.2) ("19,99") (2.675)')
        self.assertEqual([r[0] for r in rows(db, 'buscar f mostrar importe')],
                         [Decimal('0.10'), Decimal('0.20'), Decimal('19.99'), Decimal('2.68')])
        self.assertEqual(rows(db, 'calcular dinero(0.1) + dinero(0.2) = dinero(0.3), 0.1 + 0.2 = 0.3'), [(True, False)])
        self.assertEqual(db.execute('buscar f mostrar sumar(importe)').scalar(), Decimal('22.97'))
        self.assertEqual(rows(db, 'buscar f donde importe = 19.99 mostrar id'), [(3,)])

    def test_se_guarda_exacto(self):
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'f.base')
            db = sbd.conectar(ruta)
            db.execute('crear tabla f { id entero clave  importe dinero }  insertar en f (importe) (0.1)')
            db.execute('actualizar f poner importe = importe * 3 todas')  # va al diario
            db._lock.soltar()
            otra = sbd.Database.__new__(sbd.Database)
            sbd.Database.__init__(otra, ruta)
            self.assertEqual(otra.execute('buscar f mostrar importe').scalar(), Decimal('0.30'))
            otra.close()
            self.assertEqual(sbd.conectar(ruta).execute('buscar f mostrar importe').scalar(), Decimal('0.30'))


class TestProduccion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = sbd.servidor(self.tmp.name, limite_segundos=1)
        self.s = sbd.Sesion(self.srv)
        self.s.ejecutar('crear base b  crear tabla t { id entero clave  x texto }  insertar en t { x: "a" }')

    def tearDown(self):
        self.srv.cerrar()
        self.tmp.cleanup()

    def test_transaccion_de_una_sesion_no_la_ve_ni_la_toca_otra(self):
        from sbd.errors import BaseOcupada
        otra = sbd.Sesion(self.srv, 'b')
        self.s.ejecutar('empezar  insertar en t { x: "mía" }')
        with self.assertRaises(BaseOcupada):
            otra.ejecutar('buscar t')
        with self.assertRaises(BaseOcupada):
            otra.ejecutar('deshacer')
        self.s.ejecutar('confirmar')
        self.assertEqual(otra.ejecutar('buscar t mostrar contar()').scalar(), 2)

    def test_transaccion_abandonada_caduca(self):
        self.s.ejecutar('empezar  insertar en t { x: "olvidada" }')
        self.srv.abrir('b').last_activity -= 1000
        self.assertEqual(self.srv.caducar(120), ['b'])
        with self.assertRaises(SBDError) as ctx:
            self.s.ejecutar('confirmar')
        self.assertIn('se ha deshecho', str(ctx.exception))
        self.assertEqual(self.s.ejecutar('buscar t mostrar contar()').scalar(), 1)

    def test_claves_de_api(self):
        self.s.ejecutar('crear usuario ana clave "clave de ana" puede escribir')
        clave, ident, _ = self.s.ejecutar('crear clave api para ana llamada "web"').rows[0]
        self.assertEqual(self.srv.verificar_clave_api(clave)['nombre'], 'ana')
        self.assertIsNone(self.srv.verificar_clave_api(clave + 'x'))
        self.assertEqual(self.s.ejecutar('claves api').rows[0][:3], (ident, 'ana', 'web'))
        self.s.ejecutar(f'borrar clave api {ident}')
        self.assertIsNone(self.srv.verificar_clave_api(clave))
        clave2 = self.s.ejecutar('crear clave api para ana').rows[0][0]
        self.s.ejecutar('borrar usuario ana')
        self.assertIsNone(self.srv.verificar_clave_api(clave2))  # sin usuario, la clave deja de valer

    def test_copias(self):
        import time as _t
        self.s.ejecutar('empezar  insertar en t { x: "sin confirmar" }')
        self.s.ejecutar('copiar base b a b2')
        self.s.ejecutar('deshacer')
        self.s.ejecutar('crear usuario ana clave "clave de ana" puede leer  crear clave api para ana')
        carpeta = self.srv.copia_diaria()
        self.assertTrue(os.path.exists(os.path.join(carpeta, '.usuarios')))  # el servidor entero, no solo las bases
        self.assertTrue(os.path.exists(os.path.join(carpeta, '.claves')))
        dia = _t.strftime('%Y-%m-%d')
        self.s.ejecutar(f'restaurar base b del {dia} como b_hoy')
        for nombre in ('b2', 'b_hoy'):
            self.assertEqual(sbd.Sesion(self.srv, nombre).ejecutar('buscar t mostrar x').fetchall(), [('a',)])

    def test_limite_de_tiempo(self):
        self.s.ejecutar('empezar')
        for _ in range(2000):
            self.s.ejecutar('insertar en t { x: "y" }')
        self.s.ejecutar('confirmar')
        with self.assertRaises(SBDError) as ctx:
            self.s.ejecutar('buscar t como a unir t como c en a.id + c.id > 0 mostrar contar()')
        self.assertIn('tardado demasiado', str(ctx.exception))


class TestIndicesYPlan(unittest.TestCase):
    def setUp(self):
        self.db = sbd.conectar()
        self.db.execute('crear tabla p { id entero clave  precio dinero  f fecha  c entero }')
        self.db.execute('empezar')
        for i in range(200):
            self.db.execute('insertar en p { precio: ?, f: ?, c: ? }', [f'{i}.50', f'2024-01-{i % 28 + 1:02d}', i % 10])
        self.db.execute('confirmar')

    def test_rangos_usan_el_indice_y_dan_lo_mismo(self):
        sin_indice = sbd.conectar()
        sin_indice.execute('\n'.join(self.db.schema_sbd()))
        sin_indice.execute('empezar')
        for fila in rows(self.db, 'buscar p'):
            sin_indice.execute('insertar en p (?, ?, ?, ?)', list(fila))
        sin_indice.execute('confirmar')
        self.db.execute('indexar p (precio)  indexar p (f)')
        for db in (self.db, sin_indice):  # cambios después de indexar: el índice tiene que seguir al día
            db.execute('borrar de p donde id = 152  actualizar p poner precio = 999 donde id = 11')
        for q in ['buscar p donde precio > 150 mostrar id', 'buscar p donde precio entre 10 y 12 mostrar id',
                  'buscar p donde 20 >= precio mostrar id', 'buscar p donde f < 2024-01-03 y c = 1 mostrar id',
                  'buscar p donde precio > 100 + 50 mostrar id', 'buscar p donde f >= 2024-01-01 + 25 dias mostrar id']:
            self.assertEqual(rows(self.db, q), rows(sin_indice, q), q)
        plan = rows(self.db, 'explicar buscar p donde precio > 150')
        self.assertIn('usa el índice p_precio_idx (rango en precio)', plan[0][1])
        # Lo que vale igual en todas las filas también sirve para buscar en el índice
        plan = rows(self.db, 'explicar buscar p donde f > hace 30 dias')
        self.assertIn('usa el índice p_f_idx (rango en f)', plan[0][1])
        with self.assertRaises(SBDError):
            self.db.execute('buscar p donde precio > 1 / 0')

    def test_unir_aprovecha_los_indices_de_cada_tabla(self):
        self.db.execute('crear tabla l { id entero clave  p_id entero  n entero }  indexar l (p_id)')
        self.db.execute('insertar en l (p_id, n) (1, 5) (1, 6) (2, 7)')
        q = 'buscar p unir l en l.p_id = p.id donde l.p_id = 1 mostrar l.n'
        self.assertEqual(rows(self.db, q), [(5,), (6,)])
        self.assertTrue(any('l_p_id_idx' in paso for _, paso in rows(self.db, 'explicar ' + q)))
        self.assertIn('tabla hash', rows(self.db, 'explicar ' + q)[2][1])

    def test_columnas_mal_escritas_dan_error_aunque_no_haya_filas(self):
        self.db.execute('crear tabla vacia { id entero clave }')
        with self.assertRaises(SBDError):
            self.db.execute('buscar vacia donde nada = 1')
        with self.assertRaises(SBDError):
            self.db.execute('buscar vacia mostrar nada')


class TestDisco(unittest.TestCase):
    def test_reabrir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'prueba.base')
            db = sbd.conectar(ruta)
            db.execute("""
                crear tabla p { id entero clave  `nombre raro` texto = "x" comprobar longitud(`nombre raro`) < 10 }
                crear tabla h { id entero clave  p_id entero -> p(id) al borrar cascada  f fecha }
                indexar h (f) como idx_f
                crear vista v = buscar h mostrar contar() como n
                insertar en p (1, "uno")  insertar en p { id: 2 }
                insertar en h (p_id, f) (1, 2024-01-01) (2, 2024-02-02)
            """)
            db.execute('empezar  insertar en p (3, "no")')  # sin confirmar: no debe guardarse
            with self.assertRaises(SBDError):
                sbd.conectar(ruta)  # el fichero está abierto
            db.close()
            db2 = sbd.conectar(ruta)
            self.assertEqual(rows(db2, 'buscar p ordenar id'), [(1, 'uno'), (2, 'x')])
            self.assertEqual(rows(db2, 'buscar v'), [(2,)])
            self.assertEqual(rows(db2, 'buscar h donde f = 2024-02-02 mostrar p_id'), [(2,)])
            self.assertIn('idx_f', db2.index_owner)
            self.assertEqual(db2.execute('insertar en p { `nombre raro`: "tres" }').lastrowid, 3)
            with self.assertRaises(SBDError):
                db2.execute('insertar en p (9, "demasiado largo")')
            db2.execute("borrar de p donde id = 1")
            db2.close()
            self.assertEqual(rows(sbd.conectar(ruta), 'buscar h mostrar contar()'), [(1,)])

    def test_diario_guarda_solo_lo_que_cambia(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'd.base')
            diario = os.path.join(tmp, 'd.diario')
            db = sbd.conectar(ruta)
            db.execute('crear tabla t { id entero clave  v texto }')
            foto = os.path.getsize(ruta)
            for i in range(50):
                db.execute('insertar en t { v: ? }', [f'fila {i}'])
            db.execute('actualizar t poner v = "cambiada" donde id = 3  borrar de t donde id = 4')
            self.assertEqual(os.path.getsize(ruta), foto)  # la foto no se ha reescrito
            self.assertEqual(sum(1 for _ in open(diario)), 52)
            # otro proceso que abriera ahora vería foto + diario
            db._lock.soltar()
            vista = sbd.Database.__new__(sbd.Database)
            sbd.Database.__init__(vista, ruta)
            self.assertEqual(vista.execute('buscar t mostrar contar()').scalar(), 49)
            self.assertEqual(vista.execute('buscar t donde id = 3 mostrar v').scalar(), 'cambiada')
            self.assertEqual(vista.execute('insertar en t { v: "nueva" }').lastrowid, 51)
            vista.close()  # al cerrar se compacta
            self.assertFalse(os.path.exists(diario))
            self.assertEqual(rows(sbd.conectar(ruta), 'buscar t mostrar contar()'), [(50,)])

    def test_diario_cortado_a_medias(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'd.base')
            db = sbd.conectar(ruta)
            db.execute('crear tabla t { id entero clave  v texto }  insertar en t { v: "a" }  insertar en t { v: "b" }')
            db._lock.soltar()
            with open(os.path.join(tmp, 'd.diario'), 'ab') as f:
                f.write(b'{"g": 1, "ops": [["i", "t", 9, [9, "corte')  # se fue la luz escribiendo
            otra = sbd.conectar(ruta)
            self.assertEqual(rows(otra, 'buscar t mostrar v'), [('a',), ('b',)])
            otra.execute('insertar en t { v: "c" }')
            otra.close()
            self.assertEqual(rows(sbd.conectar(ruta), 'buscar t mostrar v'), [('a',), ('b',), ('c',)])


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCortes(unittest.TestCase):
    """Lo que queda en disco si todo se para de golpe, y en qué orden se hace duradero cada paso."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ruta = os.path.join(self.tmp, 'd.base')

    def morir(self, cuerpo):
        """Ejecuta el código en otro proceso, que termina con kill -9: sin cerrar ni compactar nada."""
        codigo = ('import os, signal, sys\nimport sbd\ndb = sbd.conectar(sys.argv[1])\n' + cuerpo
                  + '\nos.kill(os.getpid(), signal.SIGKILL)\n')
        hecho = subprocess.run([sys.executable, '-c', codigo, self.ruta], env={**os.environ, 'PYTHONPATH': RAIZ},
                               capture_output=True, text=True)
        self.assertEqual(hecho.returncode, -signal.SIGKILL, hecho.stderr)

    def contar(self, ruta=None):
        db = sbd.conectar(ruta or self.ruta)
        try:
            return rows(db, 'buscar t mostrar contar()')[0][0]
        finally:
            db.close()

    def test_kill_a_mitad_de_una_transaccion(self):
        self.morir('db.execute("crear tabla t { id entero clave  n entero }")\n'
                   'for i in range(5): db.execute("insertar en t { n: ? }", [i])\n'
                   'db.execute("empezar")\n'
                   'for i in range(45): db.execute("insertar en t { n: ? }", [i])')
        self.assertEqual(self.contar(), 5)

    def test_kill_justo_despues_de_confirmar(self):
        self.morir('db.execute("crear tabla t { id entero clave  n entero }")\n'
                   'db.execute("empezar")\n'
                   'for i in range(3): db.execute("insertar en t { n: ? }", [i])\n'
                   'db.execute("confirmar")')
        self.assertEqual(self.contar(), 3)

    def test_diario_cortado_en_cada_byte(self):
        self.morir('db.execute("crear tabla t { id entero clave  n entero }")\n'
                   'for k in range(4):\n'
                   '    db.execute("empezar")\n'
                   '    for j in range(3): db.execute("insertar en t { n: ? }", [k])\n'
                   '    db.execute("confirmar")')
        diario = self.ruta[:-len('.base')] + '.diario'
        with open(self.ruta, 'rb') as f:
            foto = f.read()
        with open(diario, 'rb') as f:
            contenido = f.read()
        vistos = set()
        otra = os.path.join(self.tmp, 'corte.base')
        for corte in range(len(contenido) + 1):
            for sobra in (otra, otra[:-len('.base')] + '.diario'):
                if os.path.exists(sobra):
                    os.remove(sobra)
            with open(otra, 'wb') as f:
                f.write(foto)
            with open(otra[:-len('.base')] + '.diario', 'wb') as f:
                f.write(contenido[:corte])
            vistos.add(self.contar(otra))
        self.assertEqual(vistos, {0, 3, 6, 9, 12})  # nunca una transacción a medias

    def test_foto_nueva_con_el_diario_viejo_no_repite_cambios(self):
        # El corte llegó después de renombrar la foto y antes de borrar el diario
        db = sbd.conectar(self.ruta)
        db.execute('crear tabla t { id entero clave  n entero }  insertar en t { n: 1 }  insertar en t { n: 2 }')
        diario = self.ruta[:-len('.base')] + '.diario'
        with open(diario, 'rb') as f:
            viejo = f.read()
        self.assertTrue(viejo)
        db.close()  # compacta: foto nueva y sin diario
        self.assertFalse(os.path.exists(diario))
        with open(diario, 'wb') as f:
            f.write(viejo)
        self.assertEqual(self.contar(), 2)

    def registrar_pasos(self):
        """Apunta, en orden, los fsync (de fichero o de carpeta), los renombrados y los borrados."""
        pasos = []
        fsync, replace, remove = os.fsync, os.replace, os.remove

        def f_fsync(fd):
            pasos.append('fsync carpeta' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'fsync fichero')
            return fsync(fd)

        def f_replace(a, b):
            pasos.append(f'renombrar {os.path.basename(b)}')
            return replace(a, b)

        def f_remove(a):
            pasos.append(f'borrar {os.path.basename(a)}')
            return remove(a)

        return pasos, mock.patch.multiple(os, fsync=f_fsync, replace=f_replace, remove=f_remove)

    @unittest.skipIf(os.name == 'nt', 'en Windows las carpetas no se sincronizan')
    def test_la_foto_es_segura_antes_de_borrar_el_diario(self):
        db = sbd.conectar(self.ruta)
        db.execute('crear tabla t { id entero clave  n entero }  insertar en t { n: 1 }')
        pasos, parche = self.registrar_pasos()
        with parche:
            db._save()
        self.assertEqual(pasos, ['fsync fichero', 'renombrar d.base', 'fsync carpeta', 'borrar d.diario'])
        db.close()

    @unittest.skipIf(os.name == 'nt', 'en Windows las carpetas no se sincronizan')
    def test_el_diario_nuevo_se_apunta_en_la_carpeta_solo_la_primera_vez(self):
        db = sbd.conectar(self.ruta)
        db.execute('crear tabla t { id entero clave  n entero }')
        pasos, parche = self.registrar_pasos()
        with parche:
            db.execute('insertar en t { n: 1 }')
            self.assertEqual(pasos, ['fsync fichero', 'fsync carpeta'])
            pasos.clear()
            db.execute('insertar en t { n: 2 }')
            self.assertEqual(pasos, ['fsync fichero'])
        db.close()

    @unittest.skipIf(os.name == 'nt', 'en Windows las carpetas no se sincronizan')
    def test_usuarios_y_claves_llegan_al_disco_antes_de_renombrar(self):
        srv = sbd.servidor(self.tmp)
        pasos, parche = self.registrar_pasos()
        with parche:
            srv.crear_usuario('ana', 'una clave larga', 'leer')
        self.assertEqual(pasos, ['fsync fichero', 'renombrar .usuarios', 'fsync carpeta'])
        pasos.clear()
        with parche:
            srv.crear_clave_api('ana')
        self.assertEqual(pasos, ['fsync fichero', 'renombrar .claves', 'fsync carpeta'])
        srv.cerrar()



class TestProcedimientos(unittest.TestCase):
    TRANSFERIR = """crear procedimiento transferir(origen, destino, cantidad) {
      actualizar cuentas poner saldo = saldo - :cantidad donde id = :origen
      actualizar cuentas poner saldo = saldo + :cantidad donde id = :destino
      buscar cuentas donde id en [:origen, :destino] ordenar id mostrar saldo
    }"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ruta = os.path.join(self.tmp, 'd.base')
        self.db = sbd.conectar(self.ruta)
        self.addCleanup(lambda: self.db.close())
        self.db.execute('crear tabla cuentas { id entero clave  saldo entero comprobar saldo >= 0 }'
                        '  insertar en cuentas (saldo) (100) (0)')

    def saldos(self):
        return [f[0] for f in rows(self.db, 'buscar cuentas ordenar id mostrar saldo')]

    def test_crear_ejecutar_y_devolver_filas(self):
        self.db.execute(self.TRANSFERIR)
        self.assertEqual(rows(self.db, 'ejecutar transferir(1, 2, 30)'), [(70,), (30,)])
        self.assertEqual(self.saldos(), [70, 30])

    def test_los_valores_pueden_ser_expresiones_y_parametros(self):
        self.db.execute(self.TRANSFERIR)
        self.db.execute('ejecutar transferir(?, ?, 10 + ?)', [1, 2, 5])
        self.assertEqual(self.saldos(), [85, 15])

    def test_todo_o_nada_si_falla_una_orden(self):
        self.db.execute(self.TRANSFERIR)
        with self.assertRaises(SBDError):
            self.db.execute('ejecutar transferir(2, 1, 30)')  # la cuenta 2 no tiene saldo
        self.assertEqual(self.saldos(), [100, 0])
        # Y el orden contrario: falla la segunda orden, después de cambiar la primera
        self.db.execute('crear procedimiento mal(n) { actualizar cuentas poner saldo = :n donde id = 1  '
                        'actualizar cuentas poner saldo = -1 donde id = 2 }')
        with self.assertRaises(SBDError):
            self.db.execute('ejecutar mal(5)')
        self.assertEqual(self.saldos(), [100, 0])
        self.db.close()
        self.db = sbd.conectar(self.ruta)
        self.assertEqual(self.saldos(), [100, 0])  # tampoco llegó al disco

    def test_dentro_de_una_transaccion(self):
        self.db.execute(self.TRANSFERIR)
        self.db.execute('empezar')
        self.db.execute('ejecutar transferir(1, 2, 10)')
        with self.assertRaises(SBDError):
            self.db.execute('ejecutar transferir(2, 1, 500)')
        self.assertEqual(self.saldos(), [90, 10])  # solo se deshace la llamada que falló
        self.db.execute('deshacer')
        self.assertEqual(self.saldos(), [100, 0])

    def test_sobrevive_al_reabrir_y_lo_que_hace_tambien(self):
        self.db.execute(self.TRANSFERIR)
        self.db.close()
        self.db = sbd.conectar(self.ruta)
        self.db.execute('ejecutar transferir(1, 2, 7)')
        self.db.close()
        self.db = sbd.conectar(self.ruta)
        self.assertEqual(self.saldos(), [93, 7])
        fila = rows(self.db, 'procedimientos')[0]
        self.assertEqual(fila[:2], ('transferir', 'origen, destino, cantidad'))

    def test_crearlo_o_borrarlo_en_una_transaccion_se_puede_deshacer(self):
        self.db.execute('empezar')
        self.db.execute(self.TRANSFERIR)
        self.db.execute('deshacer')
        self.assertEqual(rows(self.db, 'procedimientos'), [])
        self.db.execute(self.TRANSFERIR)
        self.db.execute('empezar  borrar procedimiento transferir  deshacer')
        self.assertEqual(len(rows(self.db, 'procedimientos')), 1)
        self.db.execute('borrar procedimiento transferir')
        with self.assertRaises(SBDError):
            self.db.execute('ejecutar transferir(1, 2, 3)')
        self.db.execute('borrar procedimiento transferir si existe')

    def test_un_parametro_no_se_confunde_con_una_columna_que_se_llama_igual(self):
        self.db.execute('crear procedimiento fijar(saldo, id) { actualizar cuentas poner saldo = :saldo donde id = :id }')
        self.db.execute('ejecutar fijar(55, 2)')
        self.assertEqual(self.saldos(), [100, 55])

    def test_un_procedimiento_puede_llamar_a_otro_pero_no_sin_fin(self):
        self.db.execute(self.TRANSFERIR)
        self.db.execute('crear procedimiento doble(a, b, n) { ejecutar transferir(:a, :b, :n)  ejecutar transferir(:a, :b, :n) }')
        self.db.execute('ejecutar doble(1, 2, 5)')
        self.assertEqual(self.saldos(), [90, 10])
        self.db.execute('crear procedimiento sinfin() { actualizar cuentas poner saldo = 1 donde id = 1  ejecutar sinfin() }')
        with self.assertRaises(SBDError) as ctx:
            self.db.execute('ejecutar sinfin()')
        self.assertIn('llamándose', str(ctx.exception))
        self.assertEqual(self.saldos(), [90, 10])

    def test_el_cuerpo_solo_admite_ordenes_de_datos(self):
        for cuerpo in ['importar "/etc/passwd" en t', 'crear tabla x { id entero clave }', 'borrar tabla cuentas',
                       'empezar', 'confirmar', 'deshacer', 'crear base x', 'usar x', 'usuarios', 'copias',
                       'crear usuario x clave "una clave larga" puede todo', 'crear clave api para x',
                       'borrar base x', 'historial', 'crear procedimiento y() { calcular 1 }']:
            with self.assertRaises(SBDError, msg=cuerpo):
                self.db.execute(f'crear procedimiento p() {{ {cuerpo} }}')

    def test_errores_al_escribirlo(self):
        for mal in ['crear procedimiento p() { }',                            # vacío
                    'crear procedimiento p(a, a) { calcular :a }',            # parámetro repetido
                    'crear procedimiento p(a) { calcular :b }',               # parámetro que no existe
                    'crear procedimiento p(a) { calcular ? }',                # «?» dentro del cuerpo
                    'calcular :a',                                            # «:a» fuera de un procedimiento
                    'ejecutar p(1) ejecutar']:
            with self.assertRaises(SBDError, msg=mal):
                self.db.execute(mal)
        self.db.execute('crear procedimiento p(a) { calcular :a }')
        for mal in ['ejecutar p()', 'ejecutar p(1, 2)', 'ejecutar noexiste(1)',
                    'crear procedimiento p(b) { calcular :b }', 'ejecutar p(saldo)']:
            with self.assertRaises(SBDError, msg=mal):
                self.db.execute(mal)
        self.assertEqual(rows(self.db, 'ejecutar p(4)'), [(4,)])

    def test_el_limite_de_tiempo_vale_para_toda_la_llamada(self):
        self.db.time_limit = 0.05
        self.db.execute('empezar')
        for _ in range(3000):
            self.db.execute('insertar en cuentas (saldo) (1)')
        self.db.execute('confirmar')
        self.db.execute('crear procedimiento lento() { buscar cuentas como a unir cuentas como b en a.id + b.id > 0 mostrar contar() }')
        with self.assertRaises(SBDError) as ctx:
            self.db.execute('ejecutar lento()')
        self.assertIn('tardado demasiado', str(ctx.exception))

    def test_usa_los_indices_igual_que_una_orden_suelta(self):
        self.db.execute('crear procedimiento uno(n) { buscar cuentas donde id = :n mostrar saldo }')
        self.assertEqual(rows(self.db, 'ejecutar uno(2)'), [(0,)])

    def test_renombrar_una_tabla_actualiza_sus_procedimientos(self):
        self.db.execute(self.TRANSFERIR)
        r = self.db.execute('renombrar tabla cuentas a saldos')
        self.assertIn('transferir', r.message)
        self.assertEqual(rows(self.db, 'ejecutar transferir(1, 2, 1)'), [(99,), (1,)])
        self.db.close()
        self.db = sbd.conectar(self.ruta)
        self.assertEqual(rows(self.db, 'ejecutar transferir(1, 2, 1)'), [(98,), (2,)])
        self.db.execute('renombrar tabla saldos a cuentas')

    def test_renombrar_no_toca_un_parametro_que_se_llama_como_la_tabla(self):
        self.db.execute('crear procedimiento p(cuentas) { actualizar cuentas poner saldo = :cuentas donde id = 1 }')
        self.db.execute('renombrar tabla cuentas a saldos')
        self.db.execute('ejecutar p(9)')
        self.assertEqual(rows(self.db, 'buscar saldos donde id = 1 mostrar saldo'), [(9,)])
        self.db.execute('renombrar tabla saldos a cuentas')

    def test_renombrar_una_columna_avisa(self):
        self.db.execute('crear tabla notas { id entero clave  texto texto }')
        self.db.execute('crear procedimiento p(t) { insertar en notas { texto: :t } }')
        self.assertIn('revisa a mano los procedimientos: p',
                      self.db.execute('renombrar columna notas.texto a contenido').message)

    def test_palabras_nuevas_siguen_valiendo_como_nombres(self):
        self.db.execute('crear tabla ejecutar { id entero clave  procedimientos texto  procedimiento texto }')
        self.db.execute('insertar en ejecutar { procedimientos: "a", procedimiento: "b" }')
        self.assertEqual(rows(self.db, 'buscar ejecutar mostrar procedimientos, procedimiento'), [('a', 'b')])
        self.db.close()
        self.db = sbd.conectar(self.ruta)
        self.assertEqual(rows(self.db, 'buscar ejecutar mostrar procedimiento'), [('b',)])

    def test_la_sesion_aplica_los_permisos_y_los_limites(self):
        srv = sbd.servidor(os.path.join(self.tmp, 'datos'))
        self.addCleanup(srv.cerrar)
        admin = sbd.Sesion(srv)
        admin.ejecutar('crear base b  crear tabla t { id entero clave }')
        admin.ejecutar('crear procedimiento mete() { insertar en t {} }  crear procedimiento lee() { buscar t }')
        admin.ejecutar('crear usuario lector clave "una clave larga" puede leer  crear usuario escritor clave "una clave larga" puede escribir')
        lector = sbd.Sesion(srv, 'b', usuario=srv.usuario('lector'), permite_ficheros=False)
        self.assertEqual(lector.ejecutar('procedimientos').rowcount, 2)     # ver cuáles hay sí puede
        for orden in ['ejecutar mete()', 'crear procedimiento x() { calcular 1 }', 'borrar procedimiento lee']:
            with self.assertRaises(sbd.PermisoDenegado, msg=orden):
                lector.ejecutar(orden)
        escritor = sbd.Sesion(srv, 'b', usuario=srv.usuario('escritor'), permite_ficheros=False)
        escritor.ejecutar('ejecutar mete()')
        self.assertEqual(admin.ejecutar('buscar t mostrar contar()').scalar(), 1)
        self.assertIn('ejecutar mete()', [f[3] for f in admin.ejecutar('historial').rows])  # queda auditado
        # Una cuenta con su límite de filas tampoco se lo salta con un procedimiento
        srv.cambiar_ajustes(registro=True, max_filas=1)
        srv.registrar('ana', 'una clave larga')
        ana = sbd.Sesion(srv, usuario=srv.usuario('ana'), permite_ficheros=False)
        ana.ejecutar('crear base de_ana  crear tabla t { id entero clave }  crear procedimiento mete() { insertar en t {} }')
        ana.ejecutar('ejecutar mete()')
        with self.assertRaises(sbd.PermisoDenegado):
            ana.ejecutar('ejecutar mete()')
        ana.cerrar(); lector.cerrar(); escritor.cerrar(); admin.cerrar()

    def test_copiar_base_y_volcado_conservan_los_procedimientos(self):
        from sbd.web.server import Nucleo
        srv = sbd.servidor(os.path.join(self.tmp, 'datos'))
        self.addCleanup(srv.cerrar)
        s = sbd.Sesion(srv)
        s.ejecutar('crear base b  crear tabla t { id entero clave  n entero }  '
                   'crear procedimiento p(n) { insertar en t { n: :n } }')
        s.ejecutar('copiar base b a c  usar c  ejecutar p(1)')
        self.assertEqual(s.ejecutar('buscar t mostrar n').rows, [(1,)])
        n = Nucleo(srv)
        volcado = n.volcar(None, 'b', None, 'sbd')[0]
        self.assertIn('crear procedimiento p(n)', volcado)
        self.assertEqual([x['nombre'] for x in n.base(None, 'b')['procedimientos']], ['p'])
        destino = sbd.conectar(os.path.join(self.tmp, 'd2.base'))
        script = '\n'.join(l for l in volcado.splitlines() if not l.startswith(('crear base', 'usar ')))
        destino.execute(script)
        destino.execute('ejecutar p(3)')
        self.assertEqual(rows(destino, 'buscar t mostrar n'), [(3,)])
        destino.close()


if __name__ == '__main__':
    unittest.main()
