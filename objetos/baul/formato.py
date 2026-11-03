"""Cómo se escribe un objeto en YAML, y cómo se lee de vuelta.

Cada Persistente se escribe por separado. Dentro de él:
    !ref 7                      otro objeto persistente (su OID)
    !Direccion {calle: …}       un objeto de valor, entero
    !decimal '12.50'            un Decimal exacto
    !tupla […]  !conjunto […]   tuplas y conjuntos
Las fechas, los números, los textos, las listas y los diccionarios son YAML normal.
"""
import datetime
from decimal import Decimal

import yaml

from .errores import ErrorBaul
from .objetos import CLASES, Persistente, Valor, estado


class Ref:
    """Una referencia a un objeto persistente, mientras se escribe o se lee."""
    __slots__ = ('oid',)

    def __init__(self, oid):
        self.oid = oid

    def __eq__(self, otro):
        return isinstance(otro, Ref) and otro.oid == self.oid

    def __hash__(self):
        return hash(('ref', self.oid))


class _ValorEscrito:
    __slots__ = ('clase', 'datos')

    def __init__(self, clase, datos):
        self.clase, self.datos = clase, datos


class Escritor(yaml.SafeDumper):
    pass


# Para leer, el de libyaml si está, que es varias veces más rápido. Para escribir, siempre el de Python:
# los cambios se detectan comparando el YAML con el guardado, y tiene que salir siempre igual.
class Lector(getattr(yaml, 'CSafeLoader', yaml.SafeLoader)):
    pass


Escritor.add_representer(Ref, lambda d, r: d.represent_scalar('!ref', str(r.oid)))
Escritor.add_representer(Decimal, lambda d, v: d.represent_scalar('!decimal', format(v, 'f')))
Escritor.add_representer(tuple, lambda d, v: d.represent_sequence('!tupla', list(v)))
Escritor.add_representer(_ValorEscrito, lambda d, v: d.represent_mapping('!' + v.clase, v.datos))


def _representar_conjunto(d, v):
    return d.represent_sequence('!conjunto', sorted(v, key=repr))


Escritor.add_representer(set, _representar_conjunto)
Escritor.add_representer(frozenset, _representar_conjunto)
Lector.add_constructor('!ref', lambda l, n: Ref(int(l.construct_scalar(n))))
Lector.add_constructor('!decimal', lambda l, n: Decimal(l.construct_scalar(n)))
Lector.add_constructor('!tupla', lambda l, n: ('tupla', l.construct_sequence(n, deep=True)))
Lector.add_constructor('!conjunto', lambda l, n: ('conjunto', l.construct_sequence(n, deep=True)))


def _construir_valor(lector, sufijo, nodo):
    return _ValorEscrito(sufijo, lector.construct_mapping(nodo, deep=True))


Lector.add_multi_constructor('!', _construir_valor)

_SIMPLES = (str, int, float, bool, type(None), Decimal, datetime.date, datetime.datetime)


def a_escribible(valor, al_ver_persistente, camino='objeto'):
    """El valor con cada Persistente cambiado por su Ref (al_ver_persistente le da el OID)."""
    if isinstance(valor, Persistente):
        return Ref(al_ver_persistente(valor))
    if isinstance(valor, _SIMPLES):
        return valor
    if isinstance(valor, list):
        return [a_escribible(v, al_ver_persistente, f'{camino}[{i}]') for i, v in enumerate(valor)]
    if isinstance(valor, tuple):
        return tuple(a_escribible(v, al_ver_persistente, camino) for v in valor)
    if isinstance(valor, (set, frozenset)):
        return {a_escribible(v, al_ver_persistente, camino) for v in valor}
    if isinstance(valor, dict):
        if not all(isinstance(k, (str, int)) for k in valor):
            raise ErrorBaul(f"{camino}: las claves de un diccionario tienen que ser textos o números")
        return {k: a_escribible(v, al_ver_persistente, f'{camino}[{k!r}]') for k, v in valor.items()}
    if isinstance(valor, Valor):
        return _ValorEscrito(type(valor).__name__, {k: a_escribible(v, al_ver_persistente, f'{camino}.{k}')
                                                     for k, v in estado(valor).items()})
    raise ErrorBaul(f"{camino}: no sé guardar un {type(valor).__name__}; usa una subclase de baul.Valor "
                    "o de baul.Persistente, o un tipo básico (texto, número, fecha, lista, diccionario…)")


def escribir(obj, al_ver_persistente):
    """El YAML de los atributos de un objeto persistente."""
    datos = {k: a_escribible(v, al_ver_persistente, f'{type(obj).__name__}.{k}') for k, v in estado(obj).items()}
    return yaml.dump(datos, Dumper=Escritor, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100)


def leer(texto):
    try:
        datos = yaml.load(texto, Loader=Lector)
    except yaml.YAMLError as e:
        raise ErrorBaul(f"un objeto guardado no es YAML válido: {e}") from None
    return datos or {}


def a_python(valor, buscar):
    """Lo leído de YAML, con cada Ref cambiada por su objeto (buscar(oid) lo da)."""
    if isinstance(valor, Ref):
        return buscar(valor.oid)
    if isinstance(valor, list):
        return [a_python(v, buscar) for v in valor]
    if isinstance(valor, tuple) and len(valor) == 2 and valor[0] in ('tupla', 'conjunto'):
        elementos = [a_python(v, buscar) for v in valor[1]]
        return tuple(elementos) if valor[0] == 'tupla' else set(elementos)
    if isinstance(valor, dict):
        return {k: a_python(v, buscar) for k, v in valor.items()}
    if isinstance(valor, _ValorEscrito):
        cls = CLASES.get(valor.clase)
        if cls is None or not issubclass(cls, Valor):
            raise ErrorBaul(f"hay objetos de valor de la clase {valor.clase}, y esa clase no está definida en este programa")
        v = cls.__new__(cls)
        vars(v).update({k: a_python(x, buscar) for k, x in valor.datos.items()})
        return v
    return valor


def referencias(valor):
    """Los OID a los que apunta un valor leído de YAML."""
    if isinstance(valor, Ref):
        yield valor.oid
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            yield from referencias(v)
    elif isinstance(valor, dict):
        for v in valor.values():
            yield from referencias(v)
    elif isinstance(valor, _ValorEscrito):
        yield from referencias(valor.datos)
