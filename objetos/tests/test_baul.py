"""El Baúl, probado igual con sus tres almacenes: un fichero YAML, SBD abriendo su fichero y SBD por su servidor."""
import datetime
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

AQUI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

import yaml  # noqa: E402

import baul  # noqa: E402
import sbd  # noqa: E402
from baul import formato  # noqa: E402
from baul.objetos import es_fantasma  # noqa: E402
from biblioteca import ejemplo  # noqa: E402
from biblioteca.modelos import Autor, Direccion, Libro, Prestamo, Publicacion, Revista, Socio  # noqa: E402
from biblioteca.servicio import ErrorBiblioteca, Servicio  # noqa: E402
from sbd.web.server import PanelServer  # noqa: E402

HOY = datetime.date(2026, 11, 1)


class Punto(baul.Valor):
    def __init__(self, x, y):
        self.x, self.y = x, y


class Nodo(baul.Persistente):
    def __init__(self, nombre, **resto):
        self.nombre = nombre
        vars(self).update(resto)


class ConAlmacen(unittest.TestCase):
    """Cada prueba de las subclases se ejecuta tres veces, una por almacén."""
    almacen = None

    def setUp(self):
        if self.almacen is None:
            self.skipTest('clase base')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        if self.almacen == 'yaml':
            self.url = os.path.join(self.tmp.name, 'baul.yaml')
        elif self.almacen == 'sbd':
            self.url = f"sbd:///{os.path.join(self.tmp.name, 'baul.base')}"
        else:
            srv = sbd.servidor(os.path.join(self.tmp.name, 'datos'))
            self.addCleanup(srv.cerrar)
            admin = sbd.Sesion(srv)
            self.addCleanup(admin.cerrar)
            admin.ejecutar('crear base objetos  crear usuario app clave "clave de la aplicacion" puede escribir en objetos')
            clave = admin.ejecutar('crear clave api para app').rows[0][0]
            http = PanelServer(('127.0.0.1', 0), srv)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            self.servidor, self.clave = srv, clave
            self.url = f"sbd+http://:{clave}@127.0.0.1:{http.server_address[1]}/objetos"

    def abrir(self):
        bd = baul.abrir(self.url)
        self.addCleanup(bd.cerrar)
        return bd

    def reabrir(self, bd):
        bd.cerrar()
        return self.abrir()


class Conexiones(ConAlmacen):
    def test_abrir_y_cerrar(self):
        bd = self.abrir()
        self.assertTrue(bd.abierto)
        self.assertEqual(bd.todos(), [])
        bd.cerrar()
        self.assertFalse(bd.abierto)
        bd.cerrar()                          # cerrar dos veces no falla
        with self.assertRaises(baul.ErrorBaul):
            bd.todos()
        with baul.abrir(self.url) as otra:   # y con «with» se cierra sola
            self.assertTrue(otra.abierto)
        self.assertFalse(otra.abierto)

    def test_lo_no_confirmado_se_pierde_al_cerrar(self):
        bd = self.abrir()
        bd.raiz['x'] = Nodo('a')
        bd = self.reabrir(bd)
        self.assertEqual(bd.raiz, {})


class ObjetosSimples(ConAlmacen):
    def test_tipos_basicos(self):
        bd = self.abrir()
        valores = dict(texto='Ñandú «comillas» "dobles" \'simples\'\nsegunda línea', vacio='', entero=-42, grande=10 ** 18,
                       real=3.25, verdad=True, falso=False, nada=None, dinero=Decimal('19.99'), cero=Decimal('0.10'),
                       dia=datetime.date(2024, 2, 29), momento=datetime.datetime(2026, 11, 3, 10, 30, 5),
                       parece_fecha='2024-01-01', parece_numero='007', parece_si='yes', parece_nulo='null')
        bd.raiz['n'] = Nodo('simple', **valores)
        self.assertEqual(bd.confirmar(), 1)
        n = self.reabrir(bd).raiz['n']
        for clave, valor in valores.items():
            self.assertEqual(getattr(n, clave), valor, clave)
            self.assertIs(type(getattr(n, clave)), type(valor), clave)

    def test_recibe_un_oid_y_lo_conserva(self):
        bd = self.abrir()
        n = Nodo('a')
        self.assertIsNone(n.oid)
        bd.raiz['n'] = n
        bd.confirmar()
        oid = n.oid
        self.assertIsInstance(oid, int)
        bd = self.reabrir(bd)
        self.assertEqual(bd.raiz['n'].oid, oid)
        self.assertIs(bd.obtener(oid), bd.raiz['n'])

    def test_los_atributos_con_guion_bajo_no_se_guardan(self):
        bd = self.abrir()
        n = Nodo('a')
        n._cache = 'temporal'
        bd.raiz['n'] = n
        bd.confirmar()
        self.assertFalse(hasattr(self.reabrir(bd).raiz['n'], '_cache'))

    def test_lo_que_no_sabe_guardar_lo_dice(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a', fichero=open(__file__))
        self.addCleanup(bd.raiz['n'].fichero.close)
        with self.assertRaises(baul.ErrorBaul) as ctx:
            bd.confirmar()
        self.assertIn('Nodo.fichero', str(ctx.exception))
        bd.raiz['n'] = Nodo('a', datos={(1, 2): 'x'})
        with self.assertRaises(baul.ErrorBaul):
            bd.confirmar()


class ObjetosEstructurados(ConAlmacen):
    def test_colecciones_y_valores_anidados(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a', lista=[1, [2, [3]], {'k': Punto(1, 2)}], tupla=(1, 'dos'), conjunto={'b', 'a'},
                            dicc={'x': [Punto(0, 0), None], 7: 'clave numérica'}, punto=Punto(Decimal('1.5'), datetime.date(2020, 1, 1)))
        bd.confirmar()
        n = self.reabrir(bd).raiz['n']
        self.assertEqual(n.lista, [1, [2, [3]], {'k': Punto(1, 2)}])
        self.assertEqual((n.tupla, n.conjunto), ((1, 'dos'), {'a', 'b'}))
        self.assertIsInstance(n.tupla, tuple)
        self.assertEqual(n.dicc, {'x': [Punto(0, 0), None], 7: 'clave numérica'})
        self.assertEqual(n.punto, Punto(Decimal('1.5'), datetime.date(2020, 1, 1)))
        self.assertIsInstance(n.punto, Punto)

    def test_referencias_compartidas_y_ciclos(self):
        bd = self.abrir()
        autor = Autor('Borges')
        a, b = Libro('Ficciones', 1944, autor, '1'), Libro('El Aleph', 1949, autor, '2')
        bd.raiz['libros'] = Nodo('estante', libros=[a, b], favorito=a)
        bd.confirmar()
        estante = self.reabrir(bd).raiz['libros']
        x, y = estante.libros
        self.assertIs(x.autor, y.autor)                  # el mismo objeto, no dos copias
        self.assertIs(x.autor.obras[0], x)               # el ciclo autor ⇄ libro se mantiene
        self.assertIs(estante.favorito, x)
        self.assertEqual(len({id(o) for o in [x.autor, *x.autor.obras]}), 3)

    def test_herencia_y_consulta_polimorfica(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('fondo', fondo=[Libro('L', 2000, Autor('A'), 'i'), Revista('R', 2001, 7)])
        bd.confirmar()
        bd = self.reabrir(bd)
        self.assertEqual(sorted(type(p).__name__ for p in bd.todos(Publicacion)), ['Libro', 'Revista'])
        self.assertEqual([p.titulo for p in bd.todos(Revista)], ['R'])
        self.assertEqual(bd.todos(Revista)[0].describir(), 'R, nº 7')   # los métodos son los de su clase

    def test_persistencia_por_alcance(self):
        bd = self.abrir()
        hoja = Nodo('hoja')
        bd.raiz['r'] = Nodo('r', hijos=[Nodo('medio', hijos=[hoja])])
        bd.confirmar()
        self.assertIsNotNone(hoja.oid)                    # se guardó sin pedirlo: se llegaba a él
        suelto = Nodo('suelto')
        bd.guardar(suelto)                                # a este no se llega: hay que pedirlo
        bd.confirmar()
        self.assertEqual(sorted(n.nombre for n in self.reabrir(bd).todos(Nodo)), ['hoja', 'medio', 'r', 'suelto'])


class Consultas(ConAlmacen):
    def setUp(self):
        super().setUp()
        self.bd = self.abrir()
        ejemplo.cargar(self.bd)
        self.bd = self.reabrir(self.bd)
        self.s = Servicio(self.bd)

    def test_por_ejemplo(self):
        bd = self.bd
        self.assertEqual({l.titulo for l in bd.buscar(Libro, autor__nombre='Jorge Luis Borges')}, {'Ficciones', 'El Aleph'})
        self.assertEqual([l.titulo for l in bd.buscar(Libro, autor__pais='España')], ['Nada'])
        self.assertEqual({p.titulo for p in bd.buscar(Publicacion, precio__menor=Decimal('10'))}, {'Nada', 'Sur'})
        self.assertEqual([p.titulo for p in bd.buscar(Publicacion, generos__contiene='Realismo mágico')], ['Cien años de soledad'])
        self.assertEqual([s.nombre for s in bd.buscar(Socio, direccion__ciudad='Barcelona')], ['Luis Gómez'])
        self.assertEqual(bd.buscar(Socio, direccion__ciudad='Lugo'), [])
        self.assertEqual([s.nombre for s in bd.buscar(Socio, etiquetas__avisos='correo')], ['Ana Ruiz'])
        self.assertEqual(bd.primero(Libro, isbn='9788437604572').titulo, 'Rayuela')
        self.assertEqual({p.titulo for p in bd.buscar(Publicacion, anio__en=[1931, 1963])}, {'Sur', 'Rayuela'})

    def test_nativas_con_orden_y_limite(self):
        bd = self.bd
        baratos = bd.consultar(Publicacion, lambda p: p.precio < 15, ordenar='precio', desc=True, limite=2)
        self.assertEqual([p.titulo for p in baratos], ['Rayuela', 'Ficciones'])
        self.assertEqual(bd.contar(Libro), 5)
        self.assertEqual(bd.contar(Publicacion, lambda p: 'Novela' in p.generos), 3)
        # Un objeto sin el atributo no rompe la consulta: simplemente no cumple
        self.assertEqual([p.titulo for p in bd.consultar(Publicacion, lambda p: p.isbn.startswith('978842063'))], ['Ficciones', 'El Aleph'])

    def test_navegando_por_el_grafo(self):
        ana = self.bd.primero(Socio, email='ana@ejemplo.es')
        self.assertEqual(sorted(p.publicacion.titulo for p in ana.prestamos), ['Ficciones', 'Rayuela'])
        self.assertEqual({o.titulo for o in ana.prestamos[0].publicacion.autor.obras} if isinstance(ana.prestamos[0].publicacion, Libro) else set(),
                         {'Ficciones', 'El Aleph'})
        self.assertEqual([(p.socio.nombre, p.publicacion.titulo) for p in self.s.vencidos()],
                         [('Ana Ruiz', 'Ficciones'), ('Ana Ruiz', 'Rayuela'), ('Luis Gómez', 'Cien años de soledad')])
        self.assertEqual(self.s.multas_de(self.bd.primero(Socio, email='sara@ejemplo.es')), Decimal('4.00'))
        self.assertEqual(list(self.s.por_genero()), ['Cuento', 'Ensayo', 'Fantástico', 'Novela', 'Realismo mágico'])


class Modificar(ConAlmacen):
    def test_un_cambio_se_detecta_solo(self):
        bd = self.abrir()
        n = Nodo('a', lista=[1, 2], punto=Punto(1, 1))
        bd.raiz['n'] = n
        bd.confirmar()
        self.assertEqual(bd.cambios(), 0)
        n.lista.append(3)                     # un cambio dentro de una lista
        n.punto.x = 9                         # y dentro de un objeto de valor
        self.assertEqual(bd.cambios(), 1)
        self.assertEqual(bd.confirmar(), 1)
        self.assertEqual(bd.confirmar(), 0)   # si no hay cambios, no escribe nada
        n = self.reabrir(bd).raiz['n']
        self.assertEqual((n.lista, n.punto.x, n._version), ([1, 2, 3], 9, 2))

    def test_borrar_respeta_las_referencias(self):
        bd = self.abrir()
        a, b = Nodo('a'), Nodo('b')
        bd.raiz['a'] = a
        a.amigo = b
        bd.confirmar()
        bd.borrar(b)
        with self.assertRaises(baul.ErrorBaul) as ctx:
            bd.confirmar()                    # «a» todavía lo usa
        self.assertIn('Nodo', str(ctx.exception))
        del a.amigo
        bd.confirmar()
        self.assertIsNone(b.oid)
        bd = self.reabrir(bd)
        self.assertEqual([n.nombre for n in bd.todos(Nodo)], ['a'])

    def test_cambiar_y_quitar_raices(self):
        bd = self.abrir()
        bd.raiz['a'] = Nodo('a')
        bd.confirmar()
        bd.raiz['b'] = bd.raiz.pop('a')
        bd.confirmar()
        self.assertEqual(list(self.reabrir(bd).raiz), ['b'])

    def test_servicio_prestar_y_devolver(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        s = Servicio(bd)
        sara = bd.primero(Socio, email='sara@ejemplo.es')
        nada = bd.primero(Libro, titulo='Nada')
        p = s.prestar(sara, nada, hoy=HOY)[0]
        self.assertEqual((nada.disponibles, p.vence), (1, HOY + datetime.timedelta(days=15)))
        s.devolver(p, hoy=HOY + datetime.timedelta(days=21))
        bd = self.reabrir(bd)
        p = bd.obtener(p.oid)
        self.assertEqual((p.multa, p.publicacion.disponibles, p.socio.nombre), (Decimal('3.00'), 2, 'Sara Vidal'))

    def test_baja_de_un_socio_con_su_historial(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        s = Servicio(bd)
        sara = bd.primero(Socio, email='sara@ejemplo.es')
        antes = bd.contar(Prestamo)
        s.baja_socio(sara)
        bd = self.reabrir(bd)
        self.assertIsNone(bd.primero(Socio, email='sara@ejemplo.es'))
        self.assertEqual(bd.contar(Prestamo), antes - 1)
        with self.assertRaises(ErrorBiblioteca):
            Servicio(bd).baja_socio(bd.primero(Socio, email='ana@ejemplo.es'))   # tiene libros sin devolver


class Transacciones(ConAlmacen):
    def test_deshacer_devuelve_los_mismos_objetos_a_como_estaban(self):
        bd = self.abrir()
        n = Nodo('a', lista=[1])
        bd.raiz['n'] = n
        bd.confirmar()
        n.nombre, n.lista[0] = 'cambiado', 99
        bd.raiz['otro'] = Nodo('nuevo')
        bd.deshacer()
        self.assertIs(bd.raiz['n'], n)             # el mismo objeto…
        self.assertEqual((n.nombre, n.lista), ('a', [1]))   # …con sus datos de antes
        self.assertNotIn('otro', bd.raiz)
        self.assertEqual(bd.cambios(), 0)

    def test_todo_o_nada(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        s = Servicio(bd)
        luis = bd.primero(Socio, email='luis@ejemplo.es')
        sur, nada = bd.primero(Revista, titulo='Sur'), bd.primero(Libro, titulo='Nada')
        s.prestar(bd.primero(Socio, email='sara@ejemplo.es'), sur)   # el único ejemplar
        with self.assertRaises(ErrorBiblioteca):
            s.prestar(luis, [nada, sur])                              # «Nada» sí se podría; «Sur» no
        self.assertEqual((nada.disponibles, len(luis.prestamos)), (2, 1))
        bd = self.reabrir(bd)
        self.assertEqual(bd.primero(Libro, titulo='Nada').disponibles, 2)

    def test_una_transaccion_no_empieza_con_cambios_pendientes(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a')
        with self.assertRaises(baul.ErrorBaul):
            with bd.transaccion():
                pass


class TransaccionesCompartidas(ConAlmacen):
    """Dos programas con la misma base."""

    def test_un_cambio_ajeno_no_se_pisa(self):
        if self.almacen != 'servidor':
            self.skipTest('solo el servidor admite dos programas a la vez')
        a = self.abrir()
        a.raiz['n'] = Nodo('original')
        a.confirmar()
        b = self.abrir()
        self.assertEqual(b.raiz['n'].nombre, 'original')   # b ya lo ha cargado…
        a.raiz['n'].nombre = 'de a'                        # …y a lo cambia después
        a.confirmar()
        b.raiz['n'].nombre = 'de b'
        b.raiz['extra'] = Nodo('nuevo de b')
        with self.assertRaises(baul.Conflicto):
            b.confirmar()
        b.refrescar()
        self.assertEqual(b.raiz['n'].nombre, 'de a')     # no se ha pisado
        self.assertNotIn('extra', b.raiz)                # y lo demás de b tampoco entró
        b.raiz['n'].nombre = 'de b, ya al día'
        b.confirmar()
        self.assertTrue(a.refrescar())                   # a ve que alguien ha guardado y relee
        self.assertEqual(a.raiz['n'].nombre, 'de b, ya al día')

    def test_un_objeto_sin_cargar_se_lee_como_esta_al_usarlo(self):
        if self.almacen != 'servidor':
            self.skipTest('solo el servidor admite dos programas a la vez')
        a = self.abrir()
        a.raiz['n'] = Nodo('original')
        a.confirmar()
        b = self.abrir()                                   # b todavía no lo ha usado
        a.raiz['n'].nombre = 'de a'
        a.confirmar()
        self.assertEqual(b.raiz['n'].nombre, 'de a')       # lo lee al usarlo: ya con el cambio de a
        b.raiz['n'].nombre = 'de b'
        self.assertEqual(b.confirmar(), 1)                 # no pisa nada: había leído la versión buena
        a.raiz['x'] = Nodo('x')
        a.confirmar()
        c = self.abrir()                                   # c sabe que existe, pero no lo ha cargado
        x = a.raiz.pop('x')
        a.borrar(x)
        a.confirmar()
        with self.assertRaises(baul.Conflicto):            # al usarlo, ya no está: lo dice
            c.raiz['x'].nombre
        self.assertTrue(c.refrescar())
        self.assertNotIn('x', c.raiz)

    def test_un_fichero_solo_lo_abre_un_programa(self):
        if self.almacen == 'servidor':
            self.skipTest('por el servidor pueden ser varios')
        self.abrir()
        with self.assertRaises((baul.ErrorBaul, sbd.SBDError)):
            baul.abrir(self.url)


class Integracion(ConAlmacen):
    def test_se_puede_copiar_entre_almacenes(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        antes = {o.oid: (type(o).__name__, bd.como_yaml(o)) for o in bd.todos()}
        bd.cerrar()
        otro = os.path.join(self.tmp.name, 'copia.yaml') if self.almacen != 'yaml' else f"sbd:///{self.tmp.name}/copia.base"
        self.assertEqual(baul.copiar(self.url, otro), len(antes))
        with baul.abrir(otro) as copia:
            self.assertEqual({o.oid: (type(o).__name__, copia.como_yaml(o)) for o in copia.todos()}, antes)
            self.assertEqual(copia.raiz['biblioteca'].nombre, 'Biblioteca del barrio')
        with self.assertRaises(baul.ErrorBaul):
            baul.copiar(self.url, otro)            # a una base que ya tiene objetos, no


class EnSBD(ConAlmacen):
    def test_sbd_ve_los_objetos_como_filas(self):
        if self.almacen == 'yaml':
            self.skipTest('solo en SBD')
        bd = self.abrir()
        ejemplo.cargar(bd)
        ana = bd.primero(Socio, email='ana@ejemplo.es')
        bd.cerrar()
        if self.almacen == 'sbd':
            db = sbd.conectar(self.url.removeprefix('sbd:///'))
            self.addCleanup(db.close)
            ejecutar = db.execute
        else:
            s = sbd.Sesion(self.servidor, 'objetos')
            self.addCleanup(s.cerrar)
            ejecutar = s.ejecutar
        filas = ejecutar('buscar objetos donde clase = ? mostrar oid, version, datos', ['Socio']).rows
        self.assertEqual(len(filas), 3)
        datos = {oid: d for oid, _, d in filas}[ana.oid]
        self.assertIn('ana@ejemplo.es', datos)
        self.assertIn('!Direccion', datos)
        self.assertIsInstance(yaml.load(datos, Loader=formato.Lector), dict)
        self.assertEqual(ejecutar('buscar raices mostrar nombre').rows, [('biblioteca',)])
        cuenta = ejecutar('buscar objetos agrupar clase ordenar clase mostrar clase, contar()').rows
        self.assertEqual(dict(cuenta), {'Autor': 4, 'Biblioteca': 1, 'Libro': 5, 'Prestamo': 4, 'Revista': 1, 'Socio': 3})


class YAMLLegible(ConAlmacen):
    almacen = 'yaml'

    def test_el_fichero_se_lee_a_mano(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        bd.cerrar()
        texto = Path(self.url).read_text(encoding='utf-8')
        self.assertIn('formato: baúl', texto)
        self.assertIn('clase: Libro', texto)
        self.assertIn("!ref", texto)
        self.assertIn('Calle Mayor 3', texto)

    def test_si_falla_al_escribir_el_fichero_bueno_no_se_toca(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a')
        bd.confirmar()
        antes = Path(self.url).read_bytes()
        bd.raiz['n'].nombre = 'b'
        original = yaml.dump

        def roto(*a, **k):
            raise OSError('disco lleno')
        yaml.dump = roto
        try:
            with self.assertRaises(OSError):
                bd._almacen.guardar(([], [], []), [], {})
        finally:
            yaml.dump = original
        self.assertEqual(Path(self.url).read_bytes(), antes)
        self.assertEqual([p.name for p in Path(self.url).parent.iterdir() if p.name.startswith('.baul-')], [])

    def test_una_clase_que_no_existe_lo_dice(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a')
        bd.confirmar()
        bd.cerrar()
        Path(self.url).write_text(Path(self.url).read_text(encoding='utf-8').replace('clase: Nodo', 'clase: Fantasma'), encoding='utf-8')
        with self.assertRaises(baul.ErrorBaul) as ctx:
            baul.abrir(self.url)
        self.assertIn('Fantasma', str(ctx.exception))


class Ficha(baul.Persistente, antes=('Tarjeta',)):
    """Una clase renombrada: antes se llamaba Tarjeta."""


class Coordenada(baul.Valor, antes='Posicion'):
    pass


def fantasmas(bd):
    return {o.oid for o in bd.todos() if es_fantasma(o)}


class CargaPerezosa(ConAlmacen):
    """Al abrir solo se lee qué objetos hay; los datos de cada uno, al usarlo."""

    def abrir_ejemplo(self):
        bd = self.abrir()
        ejemplo.cargar(bd)
        return self.reabrir(bd)

    def ordenes(self, bd):
        vistas = []
        if hasattr(bd._almacen, 'escuchar'):
            bd._almacen.escuchar = lambda texto, params: vistas.append(texto)
        return vistas

    def test_al_abrir_no_se_carga_ningun_objeto(self):
        bd = self.abrir_ejemplo()
        todos = {o.oid for o in bd.todos()}
        self.assertEqual(fantasmas(bd), todos)
        self.assertEqual(len(bd.todos(Publicacion)), 6)          # la clase se sabe sin cargarlos
        libro = bd.primero(Libro, titulo='Ficciones')            # la consulta sí carga los libros…
        autor = vars(libro)['autor']
        self.assertTrue(es_fantasma(autor))                      # …pero no sus autores
        self.assertEqual(autor.nombre, 'Jorge Luis Borges')      # hasta que se usan
        self.assertFalse(es_fantasma(autor))
        self.assertIn(libro, autor.obras)                        # y la identidad se mantiene
        self.assertIs(bd.raiz['biblioteca'], bd.obtener(bd.raiz['biblioteca'].oid))

    def test_cambiar_un_objeto_sin_cargar_no_pierde_sus_otros_datos(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a', color='rojo', lista=[1, 2])
        bd.confirmar()
        bd = self.reabrir(bd)
        n = bd.raiz['n']
        self.assertTrue(es_fantasma(n))
        n.nombre = 'b'                                          # primero carga sus datos, después cambia
        self.assertEqual(bd.cambios(), 1)
        bd.confirmar()
        n = self.reabrir(bd).raiz['n']
        self.assertEqual((n.nombre, n.color, n.lista), ('b', 'rojo', [1, 2]))
        with self.assertRaises(AttributeError):
            n.no_existe

    def test_lo_que_no_se_usa_no_se_vuelve_a_guardar(self):
        bd = self.abrir_ejemplo()
        bd.primero(Socio, email='ana@ejemplo.es').telefono = '600 000 000'
        self.assertEqual(bd.cambios(), 1)
        self.assertEqual(bd.confirmar(), 1)
        self.assertGreater(len(fantasmas(bd)), 10)              # los demás siguen sin cargar

    def test_una_consulta_carga_de_una_vez(self):
        if self.almacen == 'yaml':
            self.skipTest('el YAML se lee entero: no hay consultas que contar')
        bd = self.abrir_ejemplo()
        vistas = self.ordenes(bd)
        encontrados = bd.buscar(Libro, autor__nombre__contiene='borges')
        self.assertEqual({l.titulo for l in encontrados}, {'Ficciones', 'El Aleph'})
        self.assertEqual(len([o for o in vistas if 'donde oid entre' in o]), 2)    # los libros y sus autores
        vistas.clear()
        bd.buscar(Libro, autor__nombre__contiene='borges')
        self.assertEqual(vistas, [])                                            # ya estaban cargados

    def test_quien_usa_un_objeto_lo_busca_la_base(self):
        bd = self.abrir_ejemplo()
        borges = bd.primero(Autor, nombre='Jorge Luis Borges')
        vistas = self.ordenes(bd)
        self.assertEqual({o.titulo for o in bd.quien_usa(borges)}, {'Ficciones', 'El Aleph'})
        if self.almacen != 'yaml':
            self.assertEqual(len(vistas), 1)
            self.assertIn('datos contiene', vistas[0])
        self.assertEqual({type(o).__name__ for o in bd.referencias(borges)}, {'Libro'})
        nuevo = Nodo('nuevo', autor=borges)                      # uno sin guardar también cuenta
        bd.guardar(nuevo)
        self.assertIn(nuevo, bd.quien_usa(borges))

    def test_borrar_comprueba_quien_lo_usa_sin_cargarlo_todo(self):
        bd = self.abrir_ejemplo()
        borges = bd.primero(Autor, nombre='Jorge Luis Borges')
        bd.borrar(borges)
        with self.assertRaises(baul.ErrorBaul) as ctx:
            bd.confirmar()
        self.assertIn('Libro', str(ctx.exception))
        self.assertGreater(len(fantasmas(bd)), 5)

    def test_un_texto_que_parece_una_referencia_no_lo_es(self):
        bd = self.abrir()
        a = Nodo('a')
        bd.raiz['a'] = a
        bd.raiz['b'] = Nodo('b', nota=f"'{1}'")                  # el texto '1', no el objeto 1
        bd.confirmar()
        bd = self.reabrir(bd)
        self.assertEqual(bd.quien_usa(bd.raiz['a']), [])

    def test_cerrado_no_se_puede_cargar(self):
        bd = self.abrir_ejemplo()
        libro = bd.todos(Libro)[0]
        bd.cerrar()
        with self.assertRaises(baul.ErrorBaul):
            libro.titulo


class Refrescar(ConAlmacen):
    def test_si_nadie_ha_guardado_no_relee(self):
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a')
        bd.confirmar()
        n = bd.raiz['n']
        n.nombre = 'sin confirmar'
        self.assertFalse(bd.refrescar())          # nada nuevo: no relee…
        self.assertIs(bd.raiz['n'], n)            # …conserva los mismos objetos…
        self.assertEqual(n.nombre, 'a')           # …y descarta lo no confirmado

    def test_una_base_de_antes_recibe_su_contador(self):
        if self.almacen != 'sbd':
            self.skipTest('basta con probarlo en un fichero de SBD')
        bd = self.abrir()
        bd.raiz['n'] = Nodo('a')
        bd.confirmar()
        bd.cerrar()
        db = sbd.conectar(self.url.removeprefix('sbd:///'))
        db.execute('borrar tabla baul')                          # como las bases creadas antes de esta versión
        db.close()
        bd = self.abrir()
        self.assertEqual(bd.raiz['n'].nombre, 'a')
        self.assertFalse(bd.refrescar())


class ClasesRenombradas(ConAlmacen):
    def guardar_con_nombres_antiguos(self):
        bd = self.abrir()
        datos = "titulo: la de antes\nsitio: !Posicion {x: 1, y: 2}\n"
        fila = baul.almacenes.Fila(1, 'Tarjeta', 1, datos)
        bd._almacen.guardar(([fila], [], []), [fila], {'t': 1})
        return self.reabrir(bd)

    def test_se_cargan_con_su_nombre_de_ahora(self):
        bd = self.guardar_con_nombres_antiguos()
        t = bd.raiz['t']
        self.assertIsInstance(t, Ficha)
        self.assertEqual(t.titulo, 'la de antes')
        self.assertIsInstance(t.sitio, Coordenada)
        self.assertEqual((t.sitio.x, t.sitio.y), (1, 2))
        self.assertEqual(bd.cambios(), 1)                         # cargada, se guardaría con el nombre nuevo

    def test_migrar_pone_la_base_al_dia(self):
        bd = self.guardar_con_nombres_antiguos()
        self.assertEqual(bd.migrar(), 1)
        bd = self.reabrir(bd)
        self.assertEqual(bd._guardado[1].clase, 'Ficha')
        self.assertIn('!Coordenada', bd.como_yaml(bd.raiz['t']))
        self.assertEqual(bd.migrar(), 0)

    def test_basta_con_que_cambie_el_nombre_de_la_clase(self):
        bd = self.abrir()
        fila = baul.almacenes.Fila(1, 'Tarjeta', 1, "titulo: sin objetos de valor\n")
        bd._almacen.guardar(([fila], [], []), [fila], {'t': 1})
        bd = self.reabrir(bd)
        self.assertEqual(bd.migrar(), 1)                          # el YAML es el mismo: cambia solo la clase
        self.assertEqual(self.reabrir(bd)._guardado[1].clase, 'Ficha')

    def test_dos_clases_no_pueden_compartir_un_nombre(self):
        with self.assertRaises(TypeError):
            type('Otra', (baul.Persistente,), {}, antes='Ficha')
        with self.assertRaises(TypeError):
            type('Tarjeta', (baul.Persistente,), {})


class Consola(unittest.TestCase):
    def test_la_aplicacion_de_consola(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sbd:///{tmp}/b.base"

            def lanzar(*args):
                r = subprocess.run([sys.executable, str(AQUI / 'main.py'), '--bd', url, *args], capture_output=True, text=True)
                return r.returncode, r.stdout + r.stderr
            self.assertEqual(lanzar('iniciar')[0], 0)
            codigo, salida = lanzar('fondo', 'borges')
            self.assertIn('Ficciones, de Jorge Luis Borges', salida)
            sara = next(l.split()[0] for l in lanzar('socios')[1].splitlines() if 'Sara' in l)
            nada = next(l.split()[0] for l in lanzar('fondo', 'nada')[1].splitlines() if 'Nada' in l)
            codigo, salida = lanzar('prestar', sara, nada)
            self.assertEqual(codigo, 0, salida)
            prestamo = salida.split()[1].rstrip(':')
            self.assertIn('devuelto', lanzar('devolver', prestamo)[1])
            self.assertIn('!ref', lanzar('ver', prestamo)[1])
            self.assertEqual(lanzar('prestar', sara, '9999')[0], 1)
            self.assertIn('Las más prestadas', lanzar('informe')[1])
            copia = os.path.join(tmp, 'copia.yaml')
            self.assertIn('objetos copiados', lanzar('exportar', copia)[1])
            self.assertIn('formato: baúl', Path(copia).read_text(encoding='utf-8'))


for _nombre, _clase in list(globals().items()):
    if isinstance(_clase, type) and issubclass(_clase, ConAlmacen) and _clase is not ConAlmacen and _clase.almacen is None:
        for _almacen in ('yaml', 'sbd', 'servidor'):
            globals()[f'{_nombre}_{_almacen}'] = type(f'{_nombre}_{_almacen}', (_clase,), {'almacen': _almacen})
        del globals()[_nombre]


class Web(unittest.TestCase):
    """El explorador: mirar es público, prestar y devolver exige permiso «todo» en el panel de SBD."""

    def setUp(self):
        import json as _json
        self.json = _json
        from biblioteca.web import crear_servidor
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        srv = sbd.servidor(os.path.join(self.tmp.name, 'datos'))
        self.addCleanup(srv.cerrar)
        admin = sbd.Sesion(srv)
        self.addCleanup(admin.cerrar)
        admin.ejecutar('crear base objetos  crear usuario app clave "clave de la aplicacion" puede escribir en objetos  '
                       'crear usuario jefa clave "clave de la jefa" puede todo')
        clave = admin.ejecutar('crear clave api para app').rows[0][0]
        http = PanelServer(('127.0.0.1', 0), srv)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.panel = f'http://127.0.0.1:{http.server_address[1]}'
        self.url = f"sbd+http://:{clave}@127.0.0.1:{http.server_address[1]}/objetos"
        web = crear_servidor(('127.0.0.1', 0), self.url, self.panel, 'prueba')
        threading.Thread(target=web.serve_forever, daemon=True).start()
        self.addCleanup(web.app.bd.cerrar)
        self.addCleanup(web.server_close)
        self.addCleanup(web.shutdown)
        self.base = f'http://127.0.0.1:{web.server_address[1]}'
        self.admin = admin

    def pedir(self, ruta, cuerpo=None, galleta=None):
        import urllib.error
        import urllib.request
        cabeceras = {'Cookie': galleta} if galleta else {}
        datos = None
        if cuerpo is not None:
            datos = self.json.dumps(cuerpo).encode()
            cabeceras['Content-Type'] = 'application/json'
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + ruta, data=datos, headers=cabeceras)) as r:
                return r.status, self.json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, self.json.load(e)

    def entrar(self):
        import urllib.request
        req = urllib.request.Request(self.panel + '/api/entrar', headers={'Content-Type': 'application/json'},
                                     data=self.json.dumps({'usuario': 'jefa', 'clave': 'clave de la jefa'}).encode())
        with urllib.request.urlopen(req) as r:
            return r.headers['Set-Cookie'].split(';')[0]

    def test_explorar_el_grafo(self):
        _, clases = self.pedir('/api/clases')
        self.assertEqual({c['clase']: c['objetos'] for c in clases['clases']}['Libro'], 5)
        raiz = clases['raices'][0]
        self.assertEqual((raiz['nombre'], raiz['clase']), ('biblioteca', 'Biblioteca'))
        _, libros = self.pedir('/api/lista?clase=Libro')
        ficciones = next(o for o in libros['objetos'] if o['resumen'] == 'Ficciones')
        _, ficha = self.pedir(f"/api/objeto/{ficciones['ref']}")
        self.assertEqual(ficha['herencia'], ['Publicacion'])
        atributos = {a['nombre']: a for a in ficha['atributos']}
        self.assertEqual(atributos['autor']['clase'], 'Autor')                # una referencia, para seguirla
        self.assertEqual(atributos['precio'], {'nombre': 'precio', 'valor': '12.50', 'tipo': 'Decimal'})
        self.assertEqual(atributos['generos']['tipo'], 'conjunto')
        self.assertIn('!ref', ficha['yaml'])
        self.assertEqual(ficha['ordenes'][0]['sbd'], 'buscar baul donde dato = "generacion" mostrar valor')
        _, autor = self.pedir(f"/api/objeto/{atributos['autor']['ref']}")
        self.assertIn(ficciones['ref'], [o['ref'] for o in autor['lo_usan']])  # el ciclo, desde el otro lado
        self.assertEqual(self.pedir('/api/objeto/99999')[0], 404)
        self.assertEqual(self.pedir('/api/lista?clase=Fantasma')[0], 404)

    def test_recien_arrancada_solo_carga_lo_que_ensena(self):
        from biblioteca.web import Aplicacion
        app = Aplicacion(self.url, self.panel, 'otra')            # como en producción al reiniciar el servicio
        self.addCleanup(app.bd.cerrar)
        raiz = app.bd.raiz['biblioteca']
        ficha = app.objeto(raiz.oid, None)
        self.assertEqual(ficha['lo_usan'], [])
        cargados = [o for o in app.bd.todos() if not es_fantasma(o)]
        self.assertEqual(set(cargados), {raiz, *app.bd.referencias(raiz)})   # ella y lo que enlaza, nada más
        self.assertLess(len(cargados), len(app.bd.todos()))
        otra_vez = app.objeto(raiz.oid, None)
        self.assertEqual(otra_vez['ordenes'][0]['sbd'], 'buscar baul donde dato = "generacion" mostrar valor')
        self.assertFalse(any('oid entre' in o['sbd'] for o in otra_vez['ordenes']))   # no vuelve a traer objetos

    def test_consultas_por_ejemplo(self):
        for tipo, valor, esperado in [('autor', 'borges', {'Ficciones', 'El Aleph'}), ('ciudad', 'Madrid', {'Ana Ruiz'}),
                                      ('genero', 'Ensayo', {'Sur'}), ('barato', '10', {'Nada', 'Sur'})]:
            _, r = self.pedir(f'/api/consulta?tipo={tipo}&valor={valor}')
            self.assertEqual({o['resumen'] for o in r['objetos']}, esperado, tipo)
            self.assertTrue(r['llamada'].startswith('bd.buscar('))
        self.assertEqual(self.pedir('/api/consulta?tipo=barato&valor=mucho')[0], 400)
        self.assertEqual(self.pedir('/api/consulta?tipo=nada&valor=x')[0], 400)

    def test_lo_privado_solo_lo_ve_quien_administra(self):
        _, socios = self.pedir('/api/lista?clase=Socio')
        ana = next(o for o in socios['objetos'] if o['resumen'] == 'Ana Ruiz')
        _, ficha = self.pedir(f"/api/objeto/{ana['ref']}")
        texto = self.json.dumps(ficha, ensure_ascii=False)
        self.assertNotIn('ana@ejemplo.es', texto)
        self.assertNotIn('Calle Mayor', texto)
        self.assertIn('Madrid', texto)
        _, ficha = self.pedir(f"/api/objeto/{ana['ref']}", galleta=self.entrar())
        self.assertIn('ana@ejemplo.es', self.json.dumps(ficha, ensure_ascii=False))

    def test_prestar_y_devolver_exigen_permiso(self):
        _, pubs = self.pedir('/api/lista?clase=Publicacion')
        nada = next(o['ref'] for o in pubs['objetos'] if o['resumen'] == 'Nada')
        _, socios = self.pedir('/api/lista?clase=Socio')
        sara = next(o['ref'] for o in socios['objetos'] if o['resumen'] == 'Sara Vidal')
        self.assertEqual(self.pedir('/api/prestar', {'socio': sara, 'publicacion': nada})[0], 403)
        jefa = self.entrar()
        _, ficha = self.pedir(f'/api/objeto/{nada}', galleta=jefa)
        self.assertEqual(ficha['acciones'][0]['accion'], 'prestar')
        _, r = self.pedir('/api/prestar', {'socio': sara, 'publicacion': nada}, jefa)
        self.assertIn('prestado a Sara Vidal', r['mensaje'])
        self.assertIn('confirmar', [o['sbd'] for o in r['ordenes']])
        _, r = self.pedir('/api/devolver', {'prestamo': r['oid']}, jefa)
        self.assertIn('devuelto', r['mensaje'])
        _, r = self.pedir('/api/devolver', {'prestamo': r['oid']}, jefa)
        self.assertIn('ya se devolvió', r['error'])
        self.assertEqual(self.pedir('/api/prestar', {'socio': nada, 'publicacion': sara}, jefa)[0], 400)
        # SBD lo ve: la fila del préstamo, con su versión
        filas = self.admin.ejecutar('usar objetos  buscar objetos donde clase = "Prestamo" ordenar oid desc limite 1 mostrar version').rows
        self.assertEqual(filas, [(2,)])

    def test_la_pagina_se_sirve(self):
        import urllib.request
        for ruta, trozo in [('/', b'Baul'.replace(b'u', 'ú'.encode())), ('/app.js', b'verObjeto'), ('/app.css', b'--papel')]:
            with urllib.request.urlopen(self.base + ruta) as r:
                self.assertIn(trozo, r.read())


if __name__ == '__main__':
    unittest.main()
