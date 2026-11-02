"""Lo que hace la biblioteca. Trabaja con objetos: la sesión del ORM se encarga de guardarlos."""
import datetime
from contextlib import contextmanager

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import selectinload

from .modelos import DIAS_DE_PRESTAMO, MULTA_POR_DIA, Autor, Genero, Libro, Prestamo, Socio

MAX_PRESTAMOS = 3


class ErrorBiblioteca(Exception):
    """Una regla de la biblioteca impide hacer lo que se ha pedido."""


class Biblioteca:
    def __init__(self, sesion):
        self.sesion = sesion

    @contextmanager
    def _transaccion(self):
        """Todo o nada: confirma al terminar y, si algo falla, deshace todo lo hecho dentro."""
        try:
            yield
            self.sesion.commit()
        except BaseException:
            self.sesion.rollback()
            raise

    # ---------------------------------------------------- guardar y recuperar
    def alta_libro(self, titulo, isbn, autor, *, anio=None, precio='0', ejemplares=1, generos=()):
        """Crea el libro; el autor y los géneros se reutilizan si ya existen. Todo o nada."""
        with self._transaccion():
            libro = Libro(titulo=titulo, isbn=isbn, anio=anio, precio=precio, ejemplares=ejemplares,
                          disponibles=ejemplares, autor=self._autor(autor),
                          generos=[self._genero(g) for g in generos])
            self.sesion.add(libro)
        return libro

    def _autor(self, nombre):
        autor = self.sesion.scalar(select(Autor).where(Autor.nombre == nombre))
        return autor or Autor(nombre=nombre)

    def _genero(self, nombre):
        genero = self.sesion.scalar(select(Genero).where(Genero.nombre == nombre))
        return genero or Genero(nombre=nombre)

    def alta_socio(self, nombre, email, alta=None):
        with self._transaccion():
            socio = Socio(nombre=nombre, email=email)
            if alta:
                socio.alta = alta
            self.sesion.add(socio)
        return socio

    def libro(self, libro_id):
        return self.sesion.get(Libro, libro_id)

    def socio(self, socio_id):
        return self.sesion.get(Socio, socio_id)

    def buscar_libros(self, texto=''):
        """Con el lenguaje de consultas del ORM: no se escribe SQL, lo genera SQLAlchemy."""
        consulta = select(Libro).options(selectinload(Libro.autor), selectinload(Libro.generos)).order_by(Libro.titulo)
        if texto:
            patron = f'%{texto}%'
            consulta = consulta.join(Libro.autor).where(or_(Libro.titulo.like(patron), Autor.nombre.like(patron)))
        return list(self.sesion.scalars(consulta))

    def cambiar_precio(self, libro_id, precio):
        with self._transaccion():
            libro = self.sesion.get(Libro, libro_id)
            if libro is None:
                raise ErrorBiblioteca(f"no existe el libro {libro_id}")
            libro.precio = precio  # basta con cambiar el objeto: la sesión genera el UPDATE
        return libro

    def baja_socio(self, socio_id):
        with self._transaccion():
            socio = self.sesion.get(Socio, socio_id)
            if socio is None:
                raise ErrorBiblioteca(f"no existe el socio {socio_id}")
            if socio.prestamos_abiertos():
                raise ErrorBiblioteca(f"{socio.nombre} tiene libros sin devolver")
            self.sesion.delete(socio)

    # ---------------------------------------------------------- transacciones
    def prestar(self, socio_id, libros_ids, hoy=None):
        """Presta uno o varios libros de una vez. Si alguno no se puede, no se presta ninguno."""
        hoy = hoy or datetime.date.today()
        ids = [libros_ids] if isinstance(libros_ids, int) else list(libros_ids)
        with self._transaccion():
            socio = self.sesion.get(Socio, socio_id)
            if socio is None or not socio.activo:
                raise ErrorBiblioteca(f"no hay ningún socio activo con el número {socio_id}")
            if len(socio.prestamos_abiertos()) + len(ids) > MAX_PRESTAMOS:
                raise ErrorBiblioteca(f"{socio.nombre} no puede tener más de {MAX_PRESTAMOS} libros a la vez")
            prestamos = []
            for libro_id in ids:
                libro = self.sesion.get(Libro, libro_id)
                if libro is None:
                    raise ErrorBiblioteca(f"no existe el libro {libro_id}")
                if libro.disponibles == 0:
                    raise ErrorBiblioteca(f"no quedan ejemplares de «{libro.titulo}»")
                libro.disponibles -= 1
                prestamo = Prestamo(socio=socio, libro=libro, fecha=hoy,
                                    vence=hoy + datetime.timedelta(days=DIAS_DE_PRESTAMO))
                self.sesion.add(prestamo)
                prestamos.append(prestamo)
        return prestamos

    def devolver(self, prestamo_id, hoy=None):
        """Cierra el préstamo, recupera el ejemplar y calcula la multa si llega tarde."""
        hoy = hoy or datetime.date.today()
        with self._transaccion():
            prestamo = self.sesion.get(Prestamo, prestamo_id)
            if prestamo is None:
                raise ErrorBiblioteca(f"no existe el préstamo {prestamo_id}")
            if prestamo.devuelto is not None:
                raise ErrorBiblioteca(f"el préstamo {prestamo_id} ya se devolvió el {prestamo.devuelto}")
            prestamo.devuelto = hoy
            prestamo.multa = MULTA_POR_DIA * max(0, (hoy - prestamo.vence).days)
            prestamo.libro.disponibles += 1
        return prestamo

    def prestamos_abiertos(self):
        consulta = (select(Prestamo).where(Prestamo.devuelto.is_(None)).order_by(Prestamo.vence, Prestamo.id)
                    .options(selectinload(Prestamo.socio), selectinload(Prestamo.libro)))
        return list(self.sesion.scalars(consulta))

    def cuantos(self, clase):
        return self.sesion.scalar(select(func.count()).select_from(clase))

    # ---------------------------------------------------- consultas con SQL
    def mas_prestados(self, limite=5):
        return self.sesion.execute(text("""
            SELECT l.titulo, a.nombre AS autor, COUNT(p.id) AS prestamos
            FROM libros l
            JOIN autores a ON a.id = l.autor_id
            LEFT JOIN prestamos p ON p.libro_id = l.id
            GROUP BY l.titulo, a.nombre
            ORDER BY prestamos DESC, l.titulo
            LIMIT :limite
        """), {'limite': limite}).all()

    def morosos(self, hoy=None):
        """Socios con libros vencidos, y lo que deberían de multa si los devolvieran hoy."""
        return self.sesion.execute(text("""
            SELECT s.nombre, s.email, COUNT(*) AS vencidos, MIN(p.vence) AS desde
            FROM prestamos p
            JOIN socios s ON s.id = p.socio_id
            WHERE p.devuelto IS NULL AND p.vence < :hoy
            GROUP BY s.nombre, s.email
            HAVING COUNT(*) >= 1
            ORDER BY desde, s.nombre
        """), {'hoy': hoy or datetime.date.today()}).all()

    def recaudado_por_autor(self):
        return self.sesion.execute(text("""
            SELECT a.nombre, SUM(p.multa) AS multas,
                   CASE WHEN SUM(p.multa) > 0 THEN 'con multas' ELSE 'sin multas' END AS estado
            FROM autores a
            JOIN libros l ON l.autor_id = a.id
            JOIN prestamos p ON p.libro_id = l.id
            GROUP BY a.nombre
            ORDER BY a.nombre
        """)).all()

    def socios_sin_prestamos(self):
        return self.sesion.execute(text("""
            SELECT nombre FROM socios
            WHERE id NOT IN (SELECT socio_id FROM prestamos)
            ORDER BY nombre
        """)).scalars().all()

    def ranking_por_genero(self):
        """Función de ventana: el puesto de cada libro dentro de su género, por precio."""
        return self.sesion.execute(text("""
            SELECT g.nombre AS genero, l.titulo, l.precio,
                   RANK() OVER (PARTITION BY g.nombre ORDER BY l.precio DESC) AS puesto
            FROM libros l
            JOIN libro_genero lg ON lg.libro_id = l.id
            JOIN generos g ON g.id = lg.genero_id
            ORDER BY g.nombre, puesto, l.titulo
        """)).all()
