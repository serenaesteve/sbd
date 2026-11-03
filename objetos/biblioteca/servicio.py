"""Lo que hace la biblioteca, trabajando solo con objetos."""
import datetime

from .modelos import MULTA_POR_DIA, Autor, Biblioteca, Libro, Prestamo, Publicacion, Socio

MAX_PRESTAMOS = 3


class ErrorBiblioteca(Exception):
    """Una regla de la biblioteca impide hacer lo que se ha pedido."""


class Servicio:
    def __init__(self, bd):
        self.bd = bd

    @property
    def biblioteca(self):
        biblioteca = self.bd.raiz.get('biblioteca')
        if biblioteca is None:
            raise ErrorBiblioteca("la biblioteca está vacía: ejecuta «iniciar»")
        return biblioteca

    def crear(self, nombre):
        with self.bd.transaccion():
            self.bd.raiz['biblioteca'] = Biblioteca(nombre)
        return self.bd.raiz['biblioteca']

    # --------------------------------------------------------- dar de alta
    def autor(self, nombre, pais=None):
        return self.bd.primero(Autor, nombre=nombre) or Autor(nombre, pais)

    def alta(self, publicacion):
        """Basta con colgarla de la raíz: al confirmar se guarda, con su autor si es nuevo."""
        with self.bd.transaccion():
            self.biblioteca.fondo.append(publicacion)
        return publicacion

    def alta_socio(self, socio):
        with self.bd.transaccion():
            if self.bd.primero(Socio, email=socio.email):
                raise ErrorBiblioteca(f"ya hay un socio con el correo {socio.email}")
            self.biblioteca.socios.append(socio)
        return socio

    # -------------------------------------------------------- transacciones
    def prestar(self, socio, publicaciones, hoy=None):
        """Presta una o varias de una vez. Si alguna no se puede, no se presta ninguna."""
        hoy = hoy or datetime.date.today()
        publicaciones = [publicaciones] if isinstance(publicaciones, Publicacion) else list(publicaciones)
        with self.bd.transaccion():
            if not socio.activo:
                raise ErrorBiblioteca(f"{socio.nombre} está de baja")
            if len(socio.abiertos()) + len(publicaciones) > MAX_PRESTAMOS:
                raise ErrorBiblioteca(f"{socio.nombre} no puede tener más de {MAX_PRESTAMOS} a la vez")
            hechos = []
            for p in publicaciones:
                if p.disponibles == 0:
                    raise ErrorBiblioteca(f"no quedan ejemplares de «{p.titulo}»")
                p.disponibles -= 1
                hechos.append(Prestamo(socio, p, hoy))
        return hechos

    def devolver(self, prestamo, hoy=None):
        hoy = hoy or datetime.date.today()
        with self.bd.transaccion():
            if prestamo.devuelto is not None:
                raise ErrorBiblioteca(f"ya se devolvió el {prestamo.devuelto}")
            prestamo.devuelto = hoy
            prestamo.multa = MULTA_POR_DIA * max(0, (hoy - prestamo.vence).days)
            prestamo.publicacion.disponibles += 1
        return prestamo

    def baja_socio(self, socio):
        """Borrarlo de verdad: sus préstamos también, y antes se le quita de todas las listas."""
        with self.bd.transaccion():
            if socio.abiertos():
                raise ErrorBiblioteca(f"{socio.nombre} tiene cosas sin devolver")
            for p in socio.prestamos:
                p.publicacion.prestamos.remove(p)
                self.bd.borrar(p)
            self.biblioteca.socios.remove(socio)
            self.bd.borrar(socio)

    # ------------------------------------------------------------- consultas
    def buscar(self, texto=''):
        t = texto.casefold()
        return self.bd.consultar(Publicacion, lambda p: not t or t in p.titulo.casefold()
                                 or (isinstance(p, Libro) and t in p.autor.nombre.casefold()),
                                 ordenar=lambda p: p.titulo)

    def vencidos(self, hoy=None):
        hoy = hoy or datetime.date.today()
        return self.bd.consultar(Prestamo, lambda p: p.devuelto is None and p.vence < hoy, ordenar='vence')

    def mas_prestadas(self, limite=5):
        return self.bd.consultar(Publicacion, lambda p: p.prestamos, ordenar=lambda p: len(p.prestamos), desc=True, limite=limite)

    def por_genero(self):
        generos = {}
        for p in self.bd.todos(Publicacion):
            for g in p.generos:
                generos.setdefault(g, []).append(p)
        return dict(sorted(generos.items()))

    def multas_de(self, socio):
        return sum((p.multa for p in socio.prestamos), start=MULTA_POR_DIA * 0)
