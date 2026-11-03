"""Comprueba lo que dice RA4.md: cada criterio, ejecutándose de verdad.

    python3 objetos/pruebas_ra4.py

Trabaja en una carpeta temporal y con un servidor de SBD propio: no toca ninguna base real.
"""
import datetime
import os
import sys
import tempfile
import threading
from decimal import Decimal
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

import baul  # noqa: E402
from baul.objetos import es_fantasma  # noqa: E402
import sbd  # noqa: E402
from biblioteca import ejemplo  # noqa: E402
from biblioteca.modelos import Autor, Direccion, Libro, Publicacion, Revista, Socio  # noqa: E402
from biblioteca.servicio import ErrorBiblioteca, Servicio  # noqa: E402
from sbd.web.server import PanelServer  # noqa: E402

fallos = 0


class Miembro(baul.Persistente, antes='Abonado'):
    """Antes se llamaba Abonado."""


def comprobar(texto, obtenido, esperado):
    global fallos
    bien = obtenido == esperado
    fallos += not bien
    print(f"  {'bien' if bien else 'MAL '}  {texto}: {obtenido!r}" + ('' if bien else f" (se esperaba {esperado!r})"))


with tempfile.TemporaryDirectory() as tmp:
    yaml_url = os.path.join(tmp, 'biblioteca.yaml')
    sbd_url = f"sbd:///{tmp}/biblioteca.base"
    yaml_renombrada = os.path.join(tmp, 'renombrada.yaml')

    print('b) Abrir y cerrar conexiones')
    bd = baul.abrir(sbd_url)
    comprobar('abierta', bd.abierto, True)
    bd.cerrar()
    comprobar('cerrada', bd.abierto, False)
    try:
        bd.todos()
        comprobar('cerrada ya no se usa', 'se ha usado', 'error')
    except baul.ErrorBaul:
        comprobar('cerrada ya no se usa', 'error', 'error')
    with baul.abrir(sbd_url) as bd:
        pass
    comprobar('con «with» se cierra sola', bd.abierto, False)

    print('c) Objetos simples')
    with baul.abrir(sbd_url) as bd:
        autor = Autor('Jorge Luis Borges', 'Argentina')
        bd.guardar(autor)
        comprobar('antes de confirmar no tiene identidad', autor.oid, None)
        bd.confirmar()
        comprobar('al confirmar recibe su OID', autor.oid, 1)
    with baul.abrir(sbd_url) as bd:
        leido = bd.obtener(1)
        comprobar('al reabrir es un Autor', type(leido).__name__, 'Autor')
        comprobar('con sus datos', (leido.nombre, leido.pais), ('Jorge Luis Borges', 'Argentina'))
        bd.borrar(leido)
        bd.confirmar()

    print('d) Objetos estructurados')
    with baul.abrir(sbd_url) as bd:
        ejemplo.cargar(bd)
    with baul.abrir(sbd_url) as bd:
        ficciones = bd.primero(Libro, titulo='Ficciones')
        aleph = bd.primero(Libro, titulo='El Aleph')
        comprobar('referencia compartida: el mismo autor', ficciones.autor is aleph.autor, True)
        comprobar('ciclo: el autor tiene sus obras', ficciones in ficciones.autor.obras, True)
        comprobar('conjunto de géneros', ficciones.generos, {'Cuento', 'Fantástico'})
        ana = bd.primero(Socio, email='ana@ejemplo.es')
        comprobar('objeto de valor dentro', ana.direccion, Direccion('Calle Mayor 3', 'Madrid', '28013'))
        comprobar('diccionario dentro', ana.etiquetas, {'club de lectura': True, 'avisos': 'correo'})
        comprobar('herencia: una revista también es publicación', sorted(type(p).__name__ for p in bd.todos(Publicacion))[-1], 'Revista')
        comprobar('el precio es un Decimal exacto', ficciones.precio, Decimal('12.50'))

        print('e) Consultas')
        s = Servicio(bd)
        comprobar('por ejemplo', sorted(l.titulo for l in bd.buscar(Libro, autor__nombre='Jorge Luis Borges')), ['El Aleph', 'Ficciones'])
        comprobar('bajando por el grafo', [x.nombre for x in bd.buscar(Socio, direccion__ciudad='Barcelona')], ['Luis Gómez'])
        comprobar('nativa, con orden', [p.titulo for p in bd.consultar(Publicacion, lambda p: p.precio < 12, ordenar='precio')],
                  ['Sur', 'Nada', 'El Aleph'])
        comprobar('navegando', sorted(p.publicacion.titulo for p in ana.prestamos), ['Ficciones', 'Rayuela'])
        comprobar('vencidos', len(s.vencidos()), 3)

        print('f) Modificar objetos')
        precio = ficciones.precio
        ficciones.precio = Decimal('13.00')
        ana.direccion.ciudad = 'Alcalá de Henares'
        comprobar('el Baúl detecta los cambios solo', bd.cambios(), 2)
        bd.confirmar()
    with baul.abrir(sbd_url) as bd:
        comprobar('y quedan guardados', (bd.primero(Libro, titulo='Ficciones').precio,
                                         bd.primero(Socio, email='ana@ejemplo.es').direccion.ciudad), (Decimal('13.00'), 'Alcalá de Henares'))

        print('g) Transacciones')
        s = Servicio(bd)
        sur, nada = bd.primero(Revista, titulo='Sur'), bd.primero(Libro, titulo='Nada')
        s.prestar(bd.primero(Socio, email='sara@ejemplo.es'), sur)
        luis = bd.primero(Socio, email='luis@ejemplo.es')
        try:
            s.prestar(luis, [nada, sur])
        except ErrorBiblioteca as e:
            print(f"        la biblioteca responde: {e}")
        comprobar('no se prestó ninguno: «Nada» sigue igual', nada.disponibles, 2)
        comprobar('ni Luis tiene un préstamo más', len(luis.prestamos), 1)
        nada.titulo = 'cambio que se va a deshacer'
        bd.deshacer()
        comprobar('deshacer: el mismo objeto vuelve a como estaba', nada.titulo, 'Nada')

    print('a) Lo que se ha resuelto de los inconvenientes: memoria y clases renombradas')
    with baul.abrir(sbd_url) as bd:
        ordenes = []
        bd._almacen.escuchar = lambda texto, params: ordenes.append(texto)
        sin_cargar = [o for o in bd.todos() if es_fantasma(o)]
        comprobar('al abrir no se carga ningún objeto', len(sin_cargar), bd.contar())
        borges = bd.buscar(Libro, autor__nombre__contiene='borges')
        comprobar('una consulta carga lo que necesita', sorted(l.titulo for l in borges), ['El Aleph', 'Ficciones'])
        comprobar('con dos órdenes a SBD (libros y autores), no una por objeto', len([o for o in ordenes if 'donde oid entre' in o]), 2)
        quedan = len([o for o in bd.todos() if es_fantasma(o)])
        libros = bd.todos(Libro)
        comprobar('y lo demás sigue sin cargar: todos menos los libros y sus autores', quedan,
                  bd.contar() - len(libros) - len({id(l.autor) for l in libros}))
    with baul.abrir(yaml_renombrada) as bd:
        fila = baul.almacenes.Fila(1, 'Abonado', 1, 'nombre: Marta\n')
        bd._almacen.guardar(([fila], [], []), [fila], {'m': 1})   # como lo guardó una versión anterior del programa
    with baul.abrir(yaml_renombrada) as bd:
        comprobar('una clase renombrada carga lo guardado con el nombre antiguo', type(bd.raiz['m']).__name__, 'Miembro')
        comprobar('migrar lo reescribe con el nombre nuevo', bd.migrar(), 1)
    comprobar('y así queda en el fichero', 'clase: Miembro' in Path(yaml_renombrada).read_text(encoding='utf-8'), True)

    print('g) Dos programas a la vez, por un servidor de SBD')
    srv = sbd.servidor(os.path.join(tmp, 'servidor'))
    admin = sbd.Sesion(srv)
    admin.ejecutar('crear base objetos  crear usuario app clave "clave de la aplicacion" puede escribir en objetos')
    clave = admin.ejecutar('crear clave api para app').rows[0][0]
    http = PanelServer(('127.0.0.1', 0), srv)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    servidor_url = f"sbd+http://:{clave}@127.0.0.1:{http.server_address[1]}/objetos"
    a, b = baul.abrir(servidor_url), None
    a.raiz['nota'] = Autor('Original')
    a.confirmar()
    b = baul.abrir(servidor_url)
    print(f"        B lo lee: {b.raiz['nota'].nombre!r}")
    a.raiz['nota'].nombre = 'cambio de A'
    a.confirmar()
    b.raiz['nota'].nombre = 'cambio de B'
    try:
        b.confirmar()
        comprobar('B no pisa el cambio de A', 'lo ha pisado', 'Conflicto')
    except baul.Conflicto:
        comprobar('B no pisa el cambio de A', 'Conflicto', 'Conflicto')
    comprobar('B ve que alguien ha guardado y relee', b.refrescar(), True)
    comprobar('B, al refrescar, ve el de A', b.raiz['nota'].nombre, 'cambio de A')
    comprobar('si nadie guarda, refrescar no relee', b.refrescar(), False)
    a.cerrar()
    b.cerrar()
    print('Integración con SBD: los objetos son filas de una base normal')
    filas = admin.ejecutar('usar objetos  buscar objetos mostrar oid, clase, version, datos').rows
    comprobar('SBD los lee con su lenguaje', [(f[1], f[2]) for f in filas], [('Autor', 2)])
    comprobar('y su YAML', filas[0][3].strip(), 'nombre: cambio de A\npais: null\nobras: []')
    http.shutdown()
    http.server_close()
    admin.cerrar()
    srv.cerrar()

    print('Integración: el mismo grafo de objetos, de SBD a YAML')
    with baul.abrir(sbd_url) as bd:
        cuantos = bd.contar()
    comprobar('se copian todos', baul.copiar(sbd_url, yaml_url), cuantos)
    with baul.abrir(yaml_url) as bd:
        comprobar('en el YAML están los mismos', bd.primero(Socio, email='ana@ejemplo.es').direccion.ciudad, 'Alcalá de Henares')

print()
print('Todo correcto.' if not fallos else f'{fallos} comprobaciones han fallado.')
sys.exit(1 if fallos else 0)
