"""Baúl: una base de datos de objetos de Python, que los guarda en YAML o en SBD.

    import baul

    class Autor(baul.Persistente):
        def __init__(self, nombre):
            self.nombre = nombre

    with baul.abrir('sbd:///datos/objetos.base') as bd:      # o 'biblioteca.yaml', o 'sbd+https://:CLAVE@servidor/base'
        bd.raiz['autores'] = [Autor('Borges')]
        bd.confirmar()
        print(bd.buscar(Autor, nombre='Borges'))

Cada objeto Persistente tiene su identidad (un OID) y se guarda entero, con sus listas, sus
diccionarios y sus objetos de valor dentro, y con referencias a los demás objetos persistentes.
"""
from .base import Baul, abrir, copiar
from .errores import Conflicto, ErrorBaul
from .objetos import Persistente, Valor

__all__ = ['Baul', 'Conflicto', 'ErrorBaul', 'Persistente', 'Valor', 'abrir', 'copiar']
