"""Cerrojos entre procesos: impiden que dos programas de SBD escriban a la vez en lo mismo.

El sistema operativo suelta el cerrojo solo si el proceso muere, así que nunca se
queda «pillado» por un cierre brusco.
"""
import os

from .errors import SBDError

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


class Cerrojo:
    def __init__(self, ruta, que, quien):
        self.ruta = ruta
        self._f = open(ruta, 'a+', encoding='utf-8')
        try:
            if fcntl:
                fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            try:
                self._f.seek(0)
                dueño = self._f.read().strip() or 'otro proceso'
            except OSError:  # Windows no permite leer el byte bloqueado por otro proceso.
                dueño = 'otro proceso'
            finally:
                self._f.close()
            raise SBDError(f"{que} ya está abierta en otro programa de SBD ({dueño}). "
                           "Ciérralo o sigue trabajando desde ese programa") from None
        self._f.seek(0)
        self._f.truncate()
        self._f.write(f"{quien}, proceso {os.getpid()}")
        self._f.flush()

    def soltar(self):
        if self._f is None:
            return
        try:
            if fcntl:
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()
            self._f = None
