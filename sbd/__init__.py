"""SBD: un motor de bases de datos con lenguaje propio, escrito en Python puro.

    import sbd

    srv = sbd.servidor('datos')              # directorio con varias bases
    s = sbd.Sesion(srv)
    s.ejecutar('crear base tienda')
    s.ejecutar('crear tabla notas { id entero clave  texto texto obligatorio }')
    s.ejecutar('insertar en notas { texto: ? }', ['Comprar café'])
    for fila in s.ejecutar('buscar notas ordenar id'):
        print(fila)

    db = sbd.conectar('notas.base')          # o un único fichero, sin servidor

    remota = sbd.conectar_servidor('https://datos.miweb.es', clave='sbd_…', base='tienda')
"""
__version__ = '3.2.0'

from .cliente import Cliente, ErrorServidor, conectar_servidor  # noqa: E402
from .database import Database  # noqa: E402
from .errors import BaseOcupada, PermisoDenegado, SBDError  # noqa: E402
from .result import Result  # noqa: E402
from .servidor import Servidor, Sesion  # noqa: E402

__all__ = ['BaseOcupada', 'Cliente', 'Database', 'ErrorServidor', 'PermisoDenegado', 'Result', 'SBDError',
           'Servidor', 'Sesion', 'conectar', 'conectar_servidor', 'servidor']


def conectar(ruta=None):
    """Abre (o crea) un fichero de base de datos. Sin ruta, la base vive solo en memoria."""
    return Database(ruta)


def servidor(directorio='datos', limite_segundos=None):
    """Un directorio con varias bases de datos (una por fichero .base)."""
    return Servidor(directorio, limite_segundos=limite_segundos)
