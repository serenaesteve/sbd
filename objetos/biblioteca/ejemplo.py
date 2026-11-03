"""Datos de ejemplo."""
import datetime

from .modelos import Direccion, Libro, Revista, Socio
from .servicio import Servicio


def cargar(bd):
    s = Servicio(bd)
    s.crear('Biblioteca del barrio')
    borges, cortazar = s.autor('Jorge Luis Borges', 'Argentina'), s.autor('Julio Cortázar', 'Argentina')
    garcia, laforet = s.autor('Gabriel García Márquez', 'Colombia'), s.autor('Carmen Laforet', 'España')
    fondo = [
        Libro('Ficciones', 1944, borges, '9788420633114', precio='12.50', ejemplares=3, generos={'Cuento', 'Fantástico'}),
        Libro('El Aleph', 1949, borges, '9788420633121', precio='11.90', ejemplares=2, generos={'Cuento', 'Fantástico'}),
        Libro('Rayuela', 1963, cortazar, '9788437604572', precio='14.95', ejemplares=2, generos={'Novela'}),
        Libro('Cien años de soledad', 1967, garcia, '9788437604947', precio='18.90', ejemplares=4, generos={'Novela', 'Realismo mágico'}),
        Libro('Nada', 1945, laforet, '9788423351855', precio='9.95', ejemplares=2, generos={'Novela'}),
        Revista('Sur', 1931, 1, precio='3.00', ejemplares=1, generos={'Ensayo'}),
    ]
    for p in fondo:
        s.alta(p)
    socios = [
        Socio('Ana Ruiz', 'ana@ejemplo.es', Direccion('Calle Mayor 3', 'Madrid', '28013'), datetime.date(2026, 9, 1)),
        Socio('Luis Gómez', 'luis@ejemplo.es', Direccion('Avinguda Diagonal 200', 'Barcelona', '08018'), datetime.date(2026, 9, 1)),
        Socio('Sara Vidal', 'sara@ejemplo.es', None, datetime.date(2026, 9, 1)),
    ]
    for socio in socios:
        s.alta_socio(socio)
    socios[0].etiquetas = {'club de lectura': True, 'avisos': 'correo'}
    bd.confirmar()
    hace = lambda dias: datetime.date.today() - datetime.timedelta(days=dias)  # noqa: E731
    s.prestar(socios[0], [fondo[0], fondo[2]], hoy=hace(40))
    s.prestar(socios[1], [fondo[3]], hoy=hace(30))
    devuelto = s.prestar(socios[2], [fondo[3]], hoy=hace(25))[0]
    s.devolver(devuelto, hoy=hace(2))
