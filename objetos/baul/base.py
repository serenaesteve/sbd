"""El Baúl: la conexión con la base de objetos, su mapa de identidad y sus transacciones.

Carga perezosa: al abrir solo se lee qué objetos hay y de qué clase. Cada uno es un «fantasma» con su
OID y su clase, y sus datos se leen la primera vez que se usa. Se puede pedir que se carguen varios
de una vez (bd.cargar), y las consultas lo hacen solas: una consulta a SBD en vez de una por objeto.
"""
from contextlib import contextmanager

from . import almacenes, formato
from .errores import Conflicto, ErrorBaul
from .objetos import CLASES, Persistente, es_fantasma


class Raices(dict):
    """Los objetos con nombre: el punto de entrada al grafo. bd.raiz['biblioteca'] = …"""

    def __setitem__(self, nombre, obj):
        if not isinstance(nombre, str) or not nombre:
            raise ErrorBaul("el nombre de una raíz tiene que ser un texto")
        if not isinstance(obj, Persistente):
            raise ErrorBaul(f"una raíz tiene que ser un objeto persistente, no un {type(obj).__name__}")
        super().__setitem__(nombre, obj)


class Baul:
    def __init__(self, url):
        self.url = url
        self._almacen = almacenes.crear(url)
        self.descripcion = self._almacen.descripcion
        try:
            self._cargar()
        except BaseException:
            self._almacen.cerrar()
            raise

    # ------------------------------------------------------------- conexión
    def cerrar(self):
        """Cierra la conexión. Lo que no se haya confirmado se pierde."""
        if self._almacen is not None:
            self._almacen.cerrar()
            self._almacen = None

    @property
    def abierto(self):
        return self._almacen is not None

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, traza):
        self.cerrar()

    def _vivo(self):
        if self._almacen is None:
            raise ErrorBaul("el baúl está cerrado")

    # ------------------------------------------------------------------ carga
    def _cargar(self):
        """Lee el índice y crea un fantasma por objeto. Como todos existen ya, aunque vacíos, las
        referencias entre ellos, también las circulares, apuntan siempre al mismo objeto."""
        self._firma = self._almacen.firma()   # antes de leer: si alguien guarda mientras, se volverá a leer
        filas, raices = self._almacen.cargar()
        self._objetos = {}        # oid -> objeto: el mapa de identidad
        self._guardado = {}       # oid -> Fila tal como está en el almacén (sus datos, None hasta cargarlo)
        for f in filas:
            cls = CLASES.get(f.clase)
            if cls is None or not issubclass(cls, Persistente):
                raise ErrorBaul(f"hay objetos de la clase {f.clase} y esa clase no está definida en este programa")
            obj = cls.__new__(cls)
            vars(obj).update(_oid=f.oid, _version=f.version, _fantasma=self)
            self._objetos[f.oid] = obj
            self._guardado[f.oid] = f
        self.raiz = Raices({n: self._buscar_oid(o) for n, o in raices.items()})
        self._raices_guardadas = dict(raices)
        self._nuevos = []         # guardados con bd.guardar() aunque todavía no estén unidos a una raíz
        self._borrados = set()
        self._siguiente = max(self._objetos, default=0) + 1

    def _buscar_oid(self, oid):
        try:
            return self._objetos[oid]
        except KeyError:
            raise ErrorBaul(f"un objeto apunta al {oid}, y ese objeto no está en la base "
                            "(si otro programa lo acaba de crear, bd.refrescar() lo trae)") from None

    def _rellenar(self, obj, datos):
        oid, version = obj._oid, obj._version   # la identidad no se toca: solo los datos
        vars(obj).clear()
        vars(obj).update({k: formato.a_python(v, self._buscar_oid) for k, v in datos.items()})
        vars(obj).update(_oid=oid, _version=version)

    def _despertar(self, objetos, filas=()):
        """Carga los fantasmas de la lista, todos con una consulta. Si ya se tienen sus filas, sin ninguna."""
        fantasmas = [o for o in objetos if vars(o).get('_fantasma') is self]
        if not fantasmas:
            return
        self._vivo()
        leidas = {f.oid: f for f in filas}
        faltan = [o._oid for o in fantasmas if o._oid not in leidas and self._guardado[o._oid].datos is None]
        if faltan:
            leidas.update((f.oid, f) for f in self._almacen.leer(faltan))
        for obj in fantasmas:
            f = leidas.get(obj._oid) or self._guardado[obj._oid]
            if f.datos is None:
                raise Conflicto(f"otro programa ha borrado el objeto {obj._oid} ({type(obj).__name__}) después de abrir la base: "
                                "bd.refrescar() la pone al día")
            vars(obj)['_version'] = f.version     # puede ser más nuevo que el índice: vale lo que se ha leído
            self._guardado[obj._oid] = f
            self._rellenar(obj, formato.leer(f.datos))

    def cargar(self, objetos=None):
        """Carga de una vez los objetos de la lista: una consulta a SBD en vez de una por objeto.
        Sin lista, todos."""
        self._vivo()
        self._despertar(list(self._objetos.values()) if objetos is None else list(objetos))

    def refrescar(self):
        """Pone la base al día con lo que hayan guardado otros programas. Lo no confirmado se descarta.
        Si nadie ha guardado nada desde la última vez, no vuelve a leer: solo deshace. Devuelve si ha releído."""
        self._vivo()
        if self._almacen.firma() == self._firma:
            self.deshacer()
            return False
        self._cargar()
        return True

    # ---------------------------------------------------------- persistencia
    def guardar(self, *objetos):
        """Marca objetos para guardarlos al confirmar, aunque no se pueda llegar a ellos desde una raíz."""
        self._vivo()
        for obj in objetos:
            if not isinstance(obj, Persistente):
                raise ErrorBaul(f"solo se guardan objetos persistentes, no un {type(obj).__name__}")
            if obj._oid is not None and obj._oid not in self._objetos:
                raise ErrorBaul(f"{obj!r} es de otro baúl")
            if obj._oid in self._borrados:
                self._borrados.discard(obj._oid)
            if obj._oid is None and all(o is not obj for o in self._nuevos):
                self._nuevos.append(obj)

    def borrar(self, obj):
        """Lo borra al confirmar. Si otro objeto o una raíz sigue apuntando a él, no deja confirmar."""
        self._vivo()
        if obj._oid is None:
            self._nuevos = [o for o in self._nuevos if o is not obj]
            return
        if self._objetos.get(obj._oid) is not obj:
            raise ErrorBaul(f"{obj!r} no es de este baúl")
        self._borrados.add(obj._oid)

    def _alcanzables(self):
        """Persistencia por alcance: se guarda todo objeto al que se llegue desde una raíz, desde un
        objeto ya guardado o desde uno marcado con guardar()."""
        visto, pendientes = {}, []
        for obj in [*self.raiz.values(), *self._nuevos,
                    *(o for oid, o in self._objetos.items() if oid not in self._borrados)]:
            if id(obj) not in visto:
                visto[id(obj)] = obj
                pendientes.append(obj)
        while pendientes:
            obj = pendientes.pop()
            if es_fantasma(obj):
                continue   # nadie lo ha tocado: lo que tiene dentro ya está guardado
            for hijo in _persistentes_dentro(vars(obj), set()):
                if id(hijo) not in visto:
                    visto[id(hijo)] = hijo
                    pendientes.append(hijo)
        return list(visto.values())

    def confirmar(self):
        """Guarda todos los cambios de una vez: o se guardan todos o ninguno."""
        self._vivo()
        alcanzables = [o for o in self._alcanzables() if o._oid not in self._borrados]
        if hasattr(self._almacen, 'siguiente_oid'):
            self._siguiente = max(self._siguiente, self._almacen.siguiente_oid())
        asignados = {}
        for obj in alcanzables:
            if obj._oid is None:
                asignados[id(obj)] = self._siguiente
                self._siguiente += 1

        def oid_de(o):
            return o._oid if o._oid is not None else asignados[id(o)]

        filas, nuevos, cambiados = {}, [], []
        for obj in alcanzables:
            oid = oid_de(obj)
            if oid in self._borrados:
                continue
            if es_fantasma(obj):
                filas[oid] = self._guardado[oid]   # sin cargar, no ha cambiado
                continue
            datos = formato.escribir(obj, oid_de)
            anterior = self._guardado.get(oid)
            clase = type(obj).__name__
            if anterior is None:
                f = almacenes.Fila(oid, clase, 1, datos)
                nuevos.append(f)
            elif anterior.datos != datos or anterior.clase != clase:   # la clase, si se ha renombrado
                f = almacenes.Fila(oid, clase, anterior.version + 1, datos)
                cambiados.append(f)
            else:
                f = anterior
            filas[oid] = f
        raices = {n: oid_de(o) for n, o in self.raiz.items()}
        # Integridad: un objeto borrado no puede seguir referenciado
        for oid in self._borrados:
            usado = [n for n, o in raices.items() if o == oid]
            usado += [f"{filas[x].clase} {x}" for x in sorted(self._apuntan(oid, filas))]
            if usado:
                raise ErrorBaul(f"no se puede borrar el objeto {oid} ({self._guardado[oid].clase}): todavía lo usa {', '.join(usado)}")
        borrados = [self._guardado[oid] for oid in self._borrados]
        if not (nuevos or cambiados or borrados) and raices == self._raices_guardadas:
            return 0
        firma = self._almacen.guardar((nuevos, cambiados, borrados), list(filas.values()), raices)
        if firma == self._firma + 1:
            self._firma = firma   # solo ha guardado este programa: lo que tiene sigue al día
        # Guardado: ahora sí, los objetos nuevos reciben su identidad
        for obj in alcanzables:
            if obj._oid is None:
                obj._oid = asignados[id(obj)]
                self._objetos[obj._oid] = obj
        for f in (*nuevos, *cambiados):
            self._objetos[f.oid]._version = f.version
            self._guardado[f.oid] = f
        for oid in self._borrados:
            obj = self._objetos.pop(oid)
            del self._guardado[oid]
            obj._oid, obj._version = None, 0
        self._borrados.clear()
        self._nuevos = []
        self._raices_guardadas = raices
        return len(nuevos) + len(cambiados) + len(borrados)

    def deshacer(self):
        """Devuelve cada objeto a como estaba guardado: los mismos objetos, con sus datos de antes."""
        self._vivo()
        for oid, f in self._guardado.items():
            if not es_fantasma(self._objetos[oid]):
                self._rellenar(self._objetos[oid], formato.leer(f.datos))
        self.raiz = Raices({n: self._objetos[o] for n, o in self._raices_guardadas.items()})
        self._nuevos = []
        self._borrados.clear()

    @contextmanager
    def transaccion(self):
        """with bd.transaccion(): … — confirma al terminar; si algo falla, deshace y deja los objetos como estaban."""
        self._vivo()
        if self.cambios():
            raise ErrorBaul("hay cambios sin confirmar: confirma o deshaz antes de empezar una transacción")
        try:
            yield self
            self.confirmar()
        except BaseException:
            self.deshacer()
            raise

    def cambios(self):
        """Cuántos objetos se guardarían ahora mismo (nuevos, cambiados o borrados)."""
        self._vivo()
        cuenta = len(self._borrados)
        oids_temporales = iter(range(10 ** 12, 10 ** 13))
        temporales = {}
        for obj in self._alcanzables():
            if obj._oid is None:
                cuenta += 1
                temporales[id(obj)] = next(oids_temporales)
            elif obj._oid not in self._borrados and not es_fantasma(obj):
                datos = formato.escribir(obj, lambda o: o._oid if o._oid is not None else temporales.setdefault(id(o), next(oids_temporales)))
                guardado = self._guardado[obj._oid]
                cuenta += datos != guardado.datos or type(obj).__name__ != guardado.clase
        return cuenta + (self._oids_raices() != self._raices_guardadas)

    def _oids_raices(self):
        return {n: o._oid for n, o in self.raiz.items()}

    def migrar(self):
        """Reescribe los objetos guardados con el nombre antiguo de una clase (class X(..., antes='Y'))
        con el de ahora. Sin migrar también se cargan; esto solo pone la base al día. Devuelve cuántos."""
        self._vivo()
        if self.cambios():
            raise ErrorBaul("hay cambios sin confirmar: confirma o deshaz antes de migrar")
        self.cargar()
        return self.confirmar()

    # ------------------------------------------------------------- consultas
    def como_yaml(self, obj):
        """El YAML con el que está guardado un objeto (lo que hay en la base, no sus cambios sin confirmar)."""
        self._vivo()
        self._despertar([obj])
        fila = self._guardado.get(obj._oid)
        return fila.datos if fila else None

    def referencias(self, obj):
        """Los objetos persistentes a los que apunta obj, sin repetir."""
        self._vivo()
        self._despertar([obj])
        return list({id(o): o for o in _persistentes_dentro(vars(obj), set())}.values())

    def quien_usa(self, obj):
        """Los objetos que apuntan a obj. No hace falta cargarlos todos: en SBD, la base busca su OID en
        los datos guardados, y los que encuentra ya vienen cargados."""
        self._vivo()
        if obj._oid is None:
            return [o for o in self._objetos.values() if not es_fantasma(o) and o is not obj and obj in self.referencias(o)]
        filas = {oid: self._guardado[oid] for oid, o in self._objetos.items() if es_fantasma(o)}
        usan = self._apuntan(obj._oid, filas)
        usan |= {oid for oid, o in self._objetos.items()
                 if not es_fantasma(o) and o is not obj and any(r is obj for r in _persistentes_dentro(vars(o), set()))}
        nuevos = [o for o in self._alcanzables() if o._oid is None and any(r is obj for r in _persistentes_dentro(vars(o), set()))]
        return [self._objetos[oid] for oid in sorted(usan) if oid not in self._borrados and oid != obj._oid] + nuevos

    def _apuntan(self, oid, filas):
        """De estas filas (oid -> Fila), cuáles apuntan a oid. Las que no tienen los datos los piden al
        almacén, que solo trae las candidatas; de paso, sus fantasmas quedan cargados."""
        sin_datos = {x for x, f in filas.items() if f.datos is None}
        con_datos = {x: f.datos for x, f in filas.items() if f.datos is not None}
        if sin_datos:
            traidas = [f for f in self._almacen.quien_apunta(oid) if f.oid in sin_datos]
            self._despertar([self._objetos[f.oid] for f in traidas], traidas)
            con_datos.update((f.oid, f.datos) for f in traidas)
        return {x for x, datos in con_datos.items() if oid in formato.referencias(formato.leer(datos))}

    def obtener(self, oid):
        self._vivo()
        return self._objetos.get(oid)

    def todos(self, clase=Persistente):
        """Todos los objetos guardados de una clase, también los de sus subclases (consulta polimórfica).
        No los carga: la clase se sabe sin leer sus datos."""
        self._vivo()
        return [o for oid, o in sorted(self._objetos.items()) if isinstance(o, clase) and oid not in self._borrados]

    def consultar(self, clase=Persistente, donde=None, ordenar=None, desc=False, limite=None):
        """Consulta nativa: la condición es una función de Python sobre cada objeto.

            bd.consultar(Libro, lambda l: l.precio < 15 and 'Novela' in l.generos, ordenar=lambda l: l.titulo)
        """
        resultado = self.todos(clase)
        if donde is not None or ordenar is not None:
            self._despertar(resultado)   # todos de una vez: una consulta, no una por objeto
        resultado = [o for o in resultado if donde is None or _cumple(donde, o)]
        if ordenar is not None:
            clave = ordenar if callable(ordenar) else (lambda o: _camino(o, ordenar))
            resultado.sort(key=lambda o: _ordenable(clave(o)), reverse=desc)
        return resultado[:limite] if limite is not None else resultado

    def buscar(self, clase=Persistente, **ejemplo):
        """Consulta por ejemplo: bd.buscar(Libro, autor__nombre='Borges', anio=1944).
        Un doble guion bajo baja por un atributo, y «__contiene», «__mayor», «__menor» y «__en» comparan."""
        candidatos = self.todos(clase)
        self._despertar(candidatos)
        for clave in ejemplo:   # autor__nombre: también los autores, todos de una vez y no uno por libro
            nivel = candidatos
            for parte in clave.split('__')[:-1]:
                nivel = [v for v in (_paso(o, parte) for o in nivel) if isinstance(v, Persistente)]
                self._despertar(nivel)
        return self.consultar(clase, lambda o: all(_coincide(o, k, v) for k, v in ejemplo.items()))

    def primero(self, clase=Persistente, **ejemplo):
        encontrados = self.buscar(clase, **ejemplo)
        return encontrados[0] if encontrados else None

    def contar(self, clase=Persistente, donde=None):
        return len(self.consultar(clase, donde))


def _persistentes_dentro(valor, vistos):
    """Los objetos persistentes que hay dentro de un valor (en listas, diccionarios, objetos de valor…)."""
    if isinstance(valor, Persistente):
        yield valor
        return
    if id(valor) in vistos:
        return
    if isinstance(valor, dict):
        vistos.add(id(valor))
        for v in valor.values():
            yield from _persistentes_dentro(v, vistos)
    elif isinstance(valor, (list, tuple, set, frozenset)):
        vistos.add(id(valor))
        for v in valor:
            yield from _persistentes_dentro(v, vistos)
    elif hasattr(valor, '__dict__') and not isinstance(valor, type):
        vistos.add(id(valor))
        yield from _persistentes_dentro(vars(valor), vistos)


def _cumple(condicion, obj):
    try:
        return bool(condicion(obj))
    except (AttributeError, TypeError, KeyError):
        return False  # un objeto sin ese atributo, o con un valor que no se puede comparar, no cumple


def _paso(obj, parte):
    try:
        return obj.get(parte) if isinstance(obj, dict) else getattr(obj, parte, None)
    except TypeError:
        return None


def _camino(obj, camino):
    for parte in camino.split('__'):
        obj = obj.get(parte) if isinstance(obj, dict) else getattr(obj, parte)
    return obj


_COMPARADORES = {
    'contiene': lambda a, b: b in a if not isinstance(a, str) else str(b).casefold() in a.casefold(),
    'mayor': lambda a, b: a > b, 'menor': lambda a, b: a < b,
    'desde': lambda a, b: a >= b, 'hasta': lambda a, b: a <= b, 'en': lambda a, b: a in b,
    'distinto': lambda a, b: a != b,
}


def _coincide(obj, clave, esperado):
    partes = clave.split('__')
    comparar = _COMPARADORES.get(partes[-1]) if len(partes) > 1 else None
    if comparar:
        partes = partes[:-1]
    try:
        valor = _camino(obj, '__'.join(partes))
    except (AttributeError, KeyError, TypeError):
        return False
    return comparar(valor, esperado) if comparar else valor == esperado


def _ordenable(v):
    # Los vacíos van al final, y los textos sin distinguir mayúsculas
    return (v is None, v.casefold() if isinstance(v, str) else v)


def abrir(url):
    """Abre una base de objetos: 'datos.yaml', 'sbd:///datos/objetos.base' o 'sbd+https://:CLAVE@servidor/base'."""
    return Baul(url)


def copiar(origen, destino):
    """Copia todos los objetos de una base a otra, con sus OID y sus raíces: de YAML a SBD o al revés."""
    with abrir(origen) as a, abrir(destino) as b:
        if b.todos():
            raise ErrorBaul(f"{destino} ya tiene objetos: copia a una base vacía")
        a.cargar()
        filas = [almacenes.Fila(oid, f.clase, 1, f.datos) for oid, f in a._guardado.items()]
        b._almacen.guardar((filas, [], []), filas, dict(a._raices_guardadas))
        b.refrescar()
        return len(filas)
