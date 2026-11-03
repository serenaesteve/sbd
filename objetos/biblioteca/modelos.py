"""Las clases de la biblioteca. Son clases normales de Python: el Baúl las guarda tal cual.

    Publicacion ─┬─ Libro      herencia: una consulta de Publicacion devuelve libros y revistas
                 └─ Revista
    Autor ⇄ Libro             referencias en los dos sentidos (un ciclo), sin claves ajenas
    Socio ── Direccion        un objeto de valor, guardado dentro del socio
    Socio ⇄ Prestamo ⇄ Libro  cada préstamo apunta al libro y al socio, y los dos lo tienen en su lista
"""
import datetime
from decimal import Decimal

import baul

DIAS_DE_PRESTAMO = 15
MULTA_POR_DIA = Decimal('0.50')


class Direccion(baul.Valor):
    def __init__(self, calle, ciudad, codigo_postal=''):
        self.calle = calle
        self.ciudad = ciudad
        self.codigo_postal = codigo_postal


class Autor(baul.Persistente):
    def __init__(self, nombre, pais=None):
        self.nombre = nombre
        self.pais = pais
        self.obras = []


class Publicacion(baul.Persistente):
    def __init__(self, titulo, anio, precio='0', ejemplares=1, generos=()):
        self.titulo = titulo
        self.anio = anio
        self.precio = Decimal(precio)
        self.ejemplares = ejemplares
        self.disponibles = ejemplares
        self.generos = set(generos)
        self.prestamos = []

    def describir(self):
        return self.titulo


class Libro(Publicacion):
    def __init__(self, titulo, anio, autor, isbn, **datos):
        super().__init__(titulo, anio, **datos)
        self.isbn = isbn
        self.autor = autor
        autor.obras.append(self)

    def describir(self):
        return f"{self.titulo}, de {self.autor.nombre}"


class Revista(Publicacion):
    def __init__(self, titulo, anio, numero, **datos):
        super().__init__(titulo, anio, **datos)
        self.numero = numero

    def describir(self):
        return f"{self.titulo}, nº {self.numero}"


class Socio(baul.Persistente):
    def __init__(self, nombre, email, direccion=None, alta=None):
        self.nombre = nombre
        self.email = email
        self.direccion = direccion
        self.alta = alta or datetime.date.today()
        self.activo = True
        self.prestamos = []
        self.etiquetas = {}

    def abiertos(self):
        return [p for p in self.prestamos if p.devuelto is None]


class Prestamo(baul.Persistente):
    def __init__(self, socio, publicacion, fecha):
        self.socio = socio
        self.publicacion = publicacion
        self.fecha = fecha
        self.vence = fecha + datetime.timedelta(days=DIAS_DE_PRESTAMO)
        self.devuelto = None
        self.multa = Decimal('0.00')
        socio.prestamos.append(self)
        publicacion.prestamos.append(self)


class Biblioteca(baul.Persistente):
    """La raíz: desde ella se llega a todo lo demás."""

    def __init__(self, nombre):
        self.nombre = nombre
        self.fondo = []
        self.socios = []
