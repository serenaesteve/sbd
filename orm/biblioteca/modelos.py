"""El mapeo objeto-relacional: cada clase es una tabla y cada atributo, una columna."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Numeric, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DIAS_DE_PRESTAMO = 15
MULTA_POR_DIA = Decimal('0.50')


class Base(DeclarativeBase):
    pass


# Relación N a M: un libro tiene varios géneros y un género, varios libros.
libro_genero = Table(
    'libro_genero', Base.metadata,
    Column('libro_id', ForeignKey('libros.id', ondelete='CASCADE'), primary_key=True),
    Column('genero_id', ForeignKey('generos.id', ondelete='CASCADE'), primary_key=True),
)


class Autor(Base):
    __tablename__ = 'autores'

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), unique=True)
    pais: Mapped[Optional[str]] = mapped_column(String(40))

    # Relación 1 a N: al borrar un autor se borran sus libros (si no tienen préstamos)
    libros: Mapped[list[Libro]] = relationship(back_populates='autor', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Autor {self.id}: {self.nombre}>"


class Genero(Base):
    __tablename__ = 'generos'

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(40), unique=True)

    libros: Mapped[list[Libro]] = relationship(secondary=libro_genero, back_populates='generos')

    def __repr__(self):
        return f"<Genero {self.nombre}>"


class Libro(Base):
    __tablename__ = 'libros'
    __table_args__ = (
        CheckConstraint('ejemplares >= 0', name='ck_libros_ejemplares'),
        CheckConstraint('disponibles >= 0 AND disponibles <= ejemplares', name='ck_libros_disponibles'),
        CheckConstraint('precio >= 0', name='ck_libros_precio'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    isbn: Mapped[str] = mapped_column(String(20), unique=True)
    titulo: Mapped[str] = mapped_column(String(160), index=True)
    anio: Mapped[Optional[int]]
    precio: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal('0'))  # en SBD, «dinero»: exacto
    ejemplares: Mapped[int] = mapped_column(default=1)
    disponibles: Mapped[int] = mapped_column(default=1)
    autor_id: Mapped[int] = mapped_column(ForeignKey('autores.id'), index=True)
    version: Mapped[int] = mapped_column(default=1)

    # Con cada cambio sube la versión: si otra sesión lo cambió antes, el ORM lo detecta y no lo pisa.
    __mapper_args__ = {'version_id_col': version}

    autor: Mapped[Autor] = relationship(back_populates='libros')
    generos: Mapped[list[Genero]] = relationship(secondary=libro_genero, back_populates='libros',
                                                 order_by='Genero.nombre')
    prestamos: Mapped[list[Prestamo]] = relationship(back_populates='libro')

    def __repr__(self):
        return f"<Libro {self.id}: {self.titulo}>"


class Socio(Base):
    __tablename__ = 'socios'

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    alta: Mapped[datetime.date] = mapped_column(Date, default=datetime.date.today)
    activo: Mapped[bool] = mapped_column(default=True)

    prestamos: Mapped[list[Prestamo]] = relationship(back_populates='socio', cascade='all, delete-orphan')

    def prestamos_abiertos(self):
        return [p for p in self.prestamos if p.devuelto is None]

    def __repr__(self):
        return f"<Socio {self.id}: {self.nombre}>"


class Prestamo(Base):
    __tablename__ = 'prestamos'
    __table_args__ = (
        UniqueConstraint('socio_id', 'libro_id', 'fecha', name='uq_prestamo_del_dia'),
        CheckConstraint('multa >= 0', name='ck_prestamos_multa'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    socio_id: Mapped[int] = mapped_column(ForeignKey('socios.id', ondelete='CASCADE'), index=True)
    libro_id: Mapped[int] = mapped_column(ForeignKey('libros.id'), index=True)
    fecha: Mapped[datetime.date] = mapped_column(Date, default=datetime.date.today)
    vence: Mapped[datetime.date] = mapped_column(Date)
    devuelto: Mapped[Optional[datetime.date]] = mapped_column(Date)
    multa: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal('0'))

    socio: Mapped[Socio] = relationship(back_populates='prestamos')
    libro: Mapped[Libro] = relationship(back_populates='prestamos')

    def __repr__(self):
        return f"<Prestamo {self.id}: libro {self.libro_id} a socio {self.socio_id}>"
