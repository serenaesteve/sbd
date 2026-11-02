"""Configuración del ORM: a qué base se conecta y cómo se abren las sesiones."""
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sqlalchemy_sbd  # noqa: F401 — registra el dialecto «sbd» en SQLAlchemy

from .modelos import Base

CARPETA_DATOS = Path(__file__).resolve().parent.parent / 'datos'
URL_POR_DEFECTO = f"sbd:///{CARPETA_DATOS / 'biblioteca.base'}"


def crear_motor(url=None, ver_sql=False):
    """El motor de SQLAlchemy. La URL se puede cambiar con BIBLIOTECA_URL; con ver_sql se ve el SQL que genera."""
    url = url or os.environ.get('BIBLIOTECA_URL') or URL_POR_DEFECTO
    if url == URL_POR_DEFECTO:
        CARPETA_DATOS.mkdir(exist_ok=True)
    # Por el servidor, las sesiones caducan si no se usan: pool_pre_ping comprueba la conexión antes de usarla
    return create_engine(url, echo=ver_sql, pool_pre_ping=url.startswith('sbd+'))


def preparar(motor):
    """Crea las tablas que falten a partir de las clases mapeadas."""
    Base.metadata.create_all(motor)


def fabrica_sesiones(motor):
    # expire_on_commit=False: los objetos se pueden seguir leyendo después de confirmar
    return sessionmaker(motor, expire_on_commit=False)
