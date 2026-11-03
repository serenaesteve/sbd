"""Las dos clases de objetos que entiende el Baúl.

- Persistente: tiene identidad. Se guarda aparte y los demás lo referencian: si dos libros tienen el
  mismo autor, es el mismo objeto Autor, también después de cerrar y volver a abrir.
- Valor: no tiene identidad; se guarda dentro del objeto que lo contiene (una dirección, un periodo…).
  Dos valores con los mismos datos son iguales.

Los atributos que empiezan por «_» no se guardan: sirven para datos de trabajo.

Una clase renombrada declara sus nombres de antes, y los objetos guardados con ellos se siguen cargando:

    class Lector(baul.Persistente, antes='Socio'): ...
"""

CLASES = {}  # nombre -> clase, para poder reconstruir los objetos al cargarlos (también por sus nombres de antes)


def registrar(cls, antes=()):
    for nombre in (cls.__name__, *([antes] if isinstance(antes, str) else antes)):
        previa = CLASES.get(nombre)
        if previa is not None and (previa.__module__, previa.__qualname__) != (cls.__module__, cls.__qualname__):
            raise TypeError(f"ya hay otra clase llamada {nombre}: el Baúl guarda los objetos por el nombre de su clase")
        CLASES[nombre] = cls


def es_fantasma(obj):
    """Un objeto persistente que todavía no se ha cargado: tiene su OID y su clase, pero no sus datos."""
    return vars(obj).get('_fantasma') is not None


def estado(obj):
    """Los atributos que se guardan."""
    if isinstance(obj, Persistente) and es_fantasma(obj):
        obj._fantasma._despertar([obj])
    return {k: v for k, v in vars(obj).items() if not k.startswith('_')}


class Persistente:
    _oid = None       # identidad en la base: la pone el Baúl la primera vez que lo guarda
    _version = 0      # cuántas veces se ha guardado: sirve para detectar cambios ajenos
    _fantasma = None  # el Baúl que lo cargará la primera vez que se use (carga perezosa)

    def __init_subclass__(cls, antes=(), **kw):
        super().__init_subclass__(**kw)
        registrar(cls, antes)

    # Carga perezosa: Python solo llama a __getattr__ si el atributo no está. En un fantasma no está
    # ninguno, así que se cargan sus datos y se vuelve a mirar.
    def __getattr__(self, nombre):
        baul = vars(self).get('_fantasma')
        if baul is None or nombre.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{nombre}'")
        baul._despertar([self])
        return getattr(self, nombre)

    def __setattr__(self, nombre, valor):
        if not nombre.startswith('_') and es_fantasma(self):
            self._fantasma._despertar([self])   # antes de cambiar nada, sus datos: si no, se perderían
        object.__setattr__(self, nombre, valor)

    def __delattr__(self, nombre):
        if not nombre.startswith('_') and es_fantasma(self):
            self._fantasma._despertar([self])
        object.__delattr__(self, nombre)

    @property
    def oid(self):
        return self._oid

    def __repr__(self):
        datos = ', '.join(f"{k}={v!r}" for k, v in list(estado(self).items())[:3]
                          if not isinstance(v, (Persistente, list, dict, set, tuple)))
        return f"<{type(self).__name__} {self._oid or 'nuevo'}{': ' + datos if datos else ''}>"


class Valor:
    def __init_subclass__(cls, antes=(), **kw):
        super().__init_subclass__(**kw)
        registrar(cls, antes)

    def __eq__(self, otro):
        return type(self) is type(otro) and estado(self) == estado(otro)

    def __hash__(self):
        return hash((type(self).__name__, tuple(sorted((k, repr(v)) for k, v in estado(self).items()))))

    def __repr__(self):
        return f"{type(self).__name__}({', '.join(f'{k}={v!r}' for k, v in estado(self).items())})"
