class ErrorBaul(Exception):
    """Algo no se puede guardar, cargar o consultar."""


class Conflicto(ErrorBaul):
    """Otro programa ha cambiado (o borrado) uno de estos objetos después de cargarlo.
    No se ha guardado nada: refresca y vuelve a intentarlo."""
