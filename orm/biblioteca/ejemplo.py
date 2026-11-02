"""Datos de ejemplo para probar la biblioteca."""
import datetime

from .servicio import Biblioteca

LIBROS = [
    ('Ficciones', '9788420633114', 'Jorge Luis Borges', 1944, '12.50', 3, ['Cuento', 'Fantástico']),
    ('El Aleph', '9788420633121', 'Jorge Luis Borges', 1949, '11.90', 2, ['Cuento', 'Fantástico']),
    ('Rayuela', '9788437604572', 'Julio Cortázar', 1963, '14.95', 2, ['Novela']),
    ('Cien años de soledad', '9788437604947', 'Gabriel García Márquez', 1967, '18.90', 4, ['Novela', 'Realismo mágico']),
    ('La casa de los espíritus', '9788401352836', 'Isabel Allende', 1982, '17.50', 1, ['Novela', 'Realismo mágico']),
    ('Nada', '9788423351855', 'Carmen Laforet', 1945, '9.95', 2, ['Novela']),
]
SOCIOS = [('Ana Ruiz', 'ana@ejemplo.es'), ('Luis Gómez', 'luis@ejemplo.es'), ('Sara Vidal', 'sara@ejemplo.es'),
          ('Marcos Peña', 'marcos@ejemplo.es')]


def cargar(sesion):
    b = Biblioteca(sesion)
    libros = [b.alta_libro(titulo, isbn, autor, anio=anio, precio=precio, ejemplares=ejemplares, generos=generos).id
              for titulo, isbn, autor, anio, precio, ejemplares, generos in LIBROS]
    ana, luis, sara, _ = [b.alta_socio(nombre, email, alta=datetime.date(2026, 9, 1)).id for nombre, email in SOCIOS]
    hace = lambda dias: datetime.date.today() - datetime.timedelta(days=dias)  # noqa: E731
    b.prestar(ana, [libros[0], libros[2]], hoy=hace(40))
    b.prestar(luis, [libros[3]], hoy=hace(30))
    b.prestar(luis, [libros[0]], hoy=hace(3))
    devuelto = b.prestar(sara, [libros[3]], hoy=hace(25))[0]
    b.devolver(devuelto.id, hoy=hace(2))
