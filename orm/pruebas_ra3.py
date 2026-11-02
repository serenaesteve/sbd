"""Comprueba lo que dice RA3-Acceso-a-Datos.md: cada criterio, ejecutándose de verdad.

    python3 orm/pruebas_ra3.py

Trabaja en una carpeta temporal: no toca la base de la biblioteca.
"""
import datetime
import importlib.metadata
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # el SBD de la carpeta de arriba

from sqlalchemy import exc, func, inspect, select, text  # noqa: E402

import sbd  # noqa: E402
from biblioteca import bd, ejemplo  # noqa: E402
from biblioteca.modelos import Autor, Libro, Prestamo, Socio  # noqa: E402
from biblioteca.servicio import Biblioteca, ErrorBiblioteca  # noqa: E402

fallos = 0


def comprobar(texto, obtenido, esperado):
    global fallos
    bien = obtenido == esperado
    fallos += not bien
    print(f"  {'bien' if bien else 'MAL '}  {texto}: {obtenido!r}" + ('' if bien else f" (se esperaba {esperado!r})"))


with tempfile.TemporaryDirectory() as tmp:
    ruta = os.path.join(tmp, 'biblioteca.base')

    print('a) La herramienta ORM está instalada')
    comprobar('SQLAlchemy es la versión 2.0', importlib.metadata.version('sqlalchemy').startswith('2.0'), True)

    print('b) Configuración: una URL elige el motor, y es SBD')
    motor = bd.crear_motor(f'sbd:///{ruta}')
    comprobar('dialecto', motor.dialect.name, 'sbd')

    print('c) Mapeo: las clases crean las tablas')
    bd.preparar(motor)
    insp = inspect(motor)
    comprobar('tablas', insp.get_table_names(), ['autores', 'generos', 'libro_genero', 'libros', 'prestamos', 'socios'])
    comprobar('precio es Numeric(8, 2)', str(Libro.__table__.c.precio.type), 'NUMERIC(8, 2)')
    comprobar('relación 1 a N', Autor.libros.property.direction.name, 'ONETOMANY')
    comprobar('relación N a M', Libro.generos.property.direction.name, 'MANYTOMANY')

    print('d) Persistencia: un objeto se guarda sin escribir ninguna orden')
    Sesiones = bd.fabrica_sesiones(motor)
    s = Sesiones()
    autor = Autor(nombre='Jorge Luis Borges', pais='Argentina')
    libro = Libro(titulo='Ficciones', isbn='111', precio=Decimal('12.50'), ejemplares=2, disponibles=2, autor=autor)
    comprobar('antes de guardar no tiene id', libro.id, None)
    s.add(libro)
    s.commit()
    comprobar('al guardar, la base le da id', libro.id, 1)
    comprobar('y el autor se guarda con él', autor.id, 1)

    print('e) Modificar y recuperar objetos')
    libro.precio = Decimal('15.00')
    s.commit()
    s.close()
    s = Sesiones()
    recuperado = s.get(Libro, 1)
    comprobar('otra sesión lo recupera', recuperado.titulo, 'Ficciones')
    comprobar('con el cambio guardado', recuperado.precio, Decimal('15.00'))
    comprobar('y su relación se carga sola', recuperado.autor.nombre, 'Jorge Luis Borges')
    comprobar('el ORM consulta sin SQL', s.scalars(select(Libro.titulo).where(Libro.precio > 10)).all(), ['Ficciones'])
    s.delete(recuperado)
    s.commit()
    comprobar('y se borra', s.get(Libro, 1), None)

    print('f) Consultas con SQL')
    ejemplo.cargar(s)
    b = Biblioteca(s)
    filas = s.execute(text("""
        SELECT a.nombre, COUNT(*) AS libros, SUM(l.ejemplares) AS ejemplares
        FROM libros l JOIN autores a ON a.id = l.autor_id
        WHERE l.precio > :minimo
        GROUP BY a.nombre HAVING COUNT(*) >= 2
        ORDER BY a.nombre"""), {'minimo': 5}).all()
    comprobar('JOIN, GROUP BY y HAVING', [tuple(f) for f in filas], [('Jorge Luis Borges', 2, 5)])
    comprobar('subconsulta', b.socios_sin_prestamos(), ['Marcos Peña'])
    comprobar('función de ventana', b.ranking_por_genero()[0].puesto, 1)
    malo = "x' OR '1'='1"
    comprobar('un parámetro no se puede inyectar', s.execute(text('SELECT id FROM socios WHERE email = :e'), {'e': malo}).all(), [])

    print('g) Transacciones')
    id_de = lambda titulo: s.scalar(select(Libro.id).where(Libro.titulo == titulo))  # noqa: E731
    nada, casa = id_de('Nada'), id_de('La casa de los espíritus')
    libres = s.get(Libro, nada).disponibles
    prestamos = b.cuantos(Prestamo)
    socia = b.alta_socio('Prueba', 'prueba@ejemplo.es').id
    b.prestar(socia, casa)                     # se lleva el único ejemplar
    otra = b.alta_socio('Otra', 'otra@ejemplo.es').id
    try:
        b.prestar(otra, [nada, casa])          # «Nada» se podría; el otro ya no: no se presta ninguno
        print('  MAL   tenía que fallar')
        fallos += 1
    except ErrorBiblioteca as e:
        print(f"        la biblioteca responde: {e}")
    comprobar('rollback: «Nada» sigue igual', s.get(Libro, nada).disponibles, libres)
    comprobar('rollback: solo hay un préstamo más', b.cuantos(Prestamo), prestamos + 1)
    with s.begin_nested():
        s.add(Socio(nombre='Punto de guardado', email='sp@ejemplo.es'))
    try:
        with s.begin_nested():
            s.add(Socio(nombre='Repetido', email='sp@ejemplo.es'))
            s.flush()
    except exc.IntegrityError:
        pass
    s.commit()
    comprobar('savepoint: solo se deshace su parte', s.scalar(select(func.count()).where(Socio.email == 'sp@ejemplo.es')), 1)
    s.close()
    motor.dispose()

    print('Integración: lo que guarda el ORM es una base normal de SBD')
    with sbd.conectar(ruta) as db:
        comprobar('la lee en su propio lenguaje', db.execute('buscar socios donde email = ? mostrar nombre', ['sp@ejemplo.es']).rows,
                  [('Punto de guardado',)])
        esquema = '\n'.join(db.schema_sbd())
        comprobar('y guarda la estructura en su idioma', 'crear tabla libros' in esquema and 'CREATE' not in esquema, True)

print()
print('Todo correcto.' if not fallos else f'{fallos} comprobaciones han fallado.')
sys.exit(1 if fallos else 0)
