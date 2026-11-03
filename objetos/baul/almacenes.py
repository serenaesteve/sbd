"""Dónde se guardan los objetos. Los dos almacenes guardan lo mismo, una fila (o entrada) por objeto:
oid, clase, versión y su YAML, y además las raíces con nombre.

- AlmacenYAML: un fichero YAML legible, que se reescribe entero y de forma atómica al confirmar.
- AlmacenSBD: tablas en una base de SBD, abriendo su fichero o por la API de un `sbd servidor`.
  Cada confirmación es una transacción de SBD, y la versión de cada fila detecta cambios ajenos.
  Al abrir solo lee de cada objeto su oid, su clase y su versión; sus datos, cuando se usa (leer).

Los dos dan una firma que cambia cada vez que alguien guarda: si no ha cambiado, no hay nada que releer.
"""
import datetime
import os
import tempfile
from urllib.parse import unquote, urlparse

import yaml

import sbd

from .errores import Conflicto, ErrorBaul

ESQUEMA = '''
crear tabla objetos {
  oid       entero clave,
  clase     texto obligatorio,
  version   entero obligatorio comprobar version >= 1,
  datos     texto obligatorio,
  guardado  fechahora = ahora()
}
indexar objetos (clase)
crear tabla raices {
  nombre  texto clave,
  oid     entero obligatorio -> objetos(oid)
}
'''

# Cuántas veces se ha guardado: cada confirmación le suma uno, en su misma transacción
GENERACION = '''
crear tabla baul {
  dato   texto clave,
  valor  entero obligatorio
}
insertar en baul { dato: "generacion", valor: 0 }
'''

HUECO = 64  # al cargar varios: si entre dos OID pedidos hay menos que esto, van en el mismo tramo


class Fila:
    __slots__ = ('oid', 'clase', 'version', 'datos')

    def __init__(self, oid, clase, version, datos):
        self.oid, self.clase, self.version, self.datos = oid, clase, version, datos


class AlmacenYAML:
    """Todo el baúl en un fichero YAML. Un cerrojo impide que dos programas lo abran a la vez."""

    def __init__(self, ruta):
        self.ruta = os.path.abspath(ruta)
        self.descripcion = f'fichero YAML {self.ruta}'
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        from sbd.cerrojo import Cerrojo
        self._cerrojo = Cerrojo(self.ruta + '.lock', f"El baúl {os.path.basename(self.ruta)}", 'Baúl')
        self._generacion = 0   # nadie más puede escribir el fichero (tiene el cerrojo): basta con contar aquí

    def cargar(self):
        if not os.path.exists(self.ruta):
            return [], {}
        with open(self.ruta, encoding='utf-8') as f:
            try:
                doc = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ErrorBaul(f"{self.ruta} no es un YAML válido: {e}") from None
        if doc.get('formato') != 'baúl':
            raise ErrorBaul(f"{self.ruta} no es un fichero del Baúl")
        filas = [Fila(o['oid'], o['clase'], o['version'], o['datos']) for o in doc.get('objetos', [])]
        return filas, dict(doc.get('raices') or {})

    def firma(self):
        return self._generacion

    def guardar(self, cambios, todas, raices):
        """Reescribe el fichero entero: primero a un temporal y luego lo cambia por el bueno."""
        doc = {'formato': 'baúl', 'guardado': datetime.datetime.now().replace(microsecond=0),
               'raices': raices,
               'objetos': [{'oid': f.oid, 'clase': f.clase, 'version': f.version, 'datos': f.datos}
                           for f in sorted(todas, key=lambda f: f.oid)]}
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.ruta), prefix='.baul-', suffix='.yaml')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write('# Baúl: una base de datos de objetos. Cada objeto guarda su clase, su versión y sus datos;\n'
                        '# «!ref N» apunta al objeto con oid N.\n')
                yaml.dump(doc, f, Dumper=_EscritorFichero, allow_unicode=True, sort_keys=False, width=100)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.ruta)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._generacion += 1
        return self._generacion

    def cerrar(self):
        if self._cerrojo is not None:
            self._cerrojo.soltar()
            self._cerrojo = None


class _EscritorFichero(yaml.SafeDumper):
    """Los datos de cada objeto (ya en YAML) se escriben como bloque literal, para que se lean bien."""


def _texto_en_bloque(d, v):
    return d.represent_scalar('tag:yaml.org,2002:str', v, style='|' if '\n' in v else None)


_EscritorFichero.add_representer(str, _texto_en_bloque)


class AlmacenSBD:
    """Los objetos en dos tablas de una base de SBD: «objetos» y «raices»."""

    def __init__(self, url):
        u = urlparse(url)
        if u.scheme == 'sbd':
            ruta = unquote(u.path)[1:] if u.path.startswith('/') else unquote(u.path)
            if not ruta:
                raise ErrorBaul("falta la ruta del fichero: sbd:///datos/objetos.base")
            os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
            try:
                self._db = sbd.conectar(ruta)
            except sbd.SBDError as e:
                raise ErrorBaul(str(e)) from None
            self._cliente = None
            self.descripcion = f'SBD, fichero {os.path.abspath(ruta)}'
        else:
            esquema = 'https' if u.scheme.endswith('https') else 'http'
            if not u.password or not u.path.strip('/'):
                raise ErrorBaul("por el servidor hace falta la clave y la base: sbd+https://:CLAVE@servidor/base")
            self._db = None
            self._cliente = sbd.Cliente(f'{esquema}://{u.hostname}' + (f':{u.port}' if u.port else ''),
                                        unquote(u.password), u.path.strip('/'))
            try:
                self._cliente.abrir_sesion()   # su transacción dura entre peticiones y es solo suya
            except sbd.SBDError as e:
                raise ErrorBaul(f"no se puede conectar con el servidor de SBD: {e}") from None
            self.descripcion = f'servidor de SBD {self._cliente.url}, base «{u.path.strip("/")}»'
        tablas = self._tablas()
        if 'objetos' not in tablas or 'baul' not in tablas:
            self._orden('empezar')
            self._orden((ESQUEMA if 'objetos' not in tablas else '') + GENERACION)   # una base de antes: solo le falta esta
            self._orden('confirmar')

    escuchar = None  # una función que recibe cada orden enviada a SBD (la web la enseña)

    def _orden(self, texto, params=()):
        if self.escuchar is not None:
            self.escuchar(' '.join(texto.split()), list(params))
        try:
            if self._cliente is not None:
                return self._cliente.ejecutar(texto, params)
            return self._db.execute(texto, params)
        except sbd.SBDError as e:
            raise ErrorBaul(str(e)) from None

    def _tablas(self):
        return {f[0] for f in self._orden('tablas').rows}

    def cargar(self):
        """Solo el índice: qué objetos hay y de qué clase. Los datos de cada uno los trae leer()."""
        filas = [Fila(*f, None) for f in self._orden('buscar objetos ordenar oid mostrar oid, clase, version').rows]
        raices = dict(self._orden('buscar raices mostrar nombre, oid').rows)
        return filas, raices

    def leer(self, oids):
        """Los datos de varios objetos. Los pide por tramos de OID seguidos («donde oid entre ? y ?»),
        que SBD resuelve con el índice de la clave: «oid en [...]» recorrería la tabla entera."""
        pedidos = sorted(set(oids))
        tramos = []
        for oid in pedidos:
            if tramos and oid - tramos[-1][1] <= HUECO:
                tramos[-1][1] = oid
            else:
                tramos.append([oid, oid])
        quiero = set(pedidos)
        filas = []
        for desde, hasta in tramos:
            filas += [Fila(*f) for f in self._orden('buscar objetos donde oid entre ? y ? mostrar oid, clase, version, datos',
                                                     [desde, hasta]).rows if f[0] in quiero]
        return filas

    def quien_apunta(self, oid):
        """Los objetos guardados que apuntan a este. Lo busca SBD en sus datos, sin traerlos todos: una
        referencia se escribe «!ref '7'». Lo que encuentra es solo un primer filtro (un texto también
        podría contener '7'); quien lo llama comprueba de verdad las referencias."""
        return [Fila(*f) for f in self._orden('buscar objetos donde datos contiene ? mostrar oid, clase, version, datos',
                                               [f"'{oid}'"]).rows]

    def firma(self):
        return self._orden('buscar baul donde dato = "generacion" mostrar valor').scalar()

    def guardar(self, cambios, todas, raices):
        """Todo en una transacción de SBD. Si una versión no cuadra, otro la cambió: se deshace todo."""
        nuevos, cambiados, borrados = cambios
        self._orden('empezar')
        try:
            for f in nuevos:
                self._orden('insertar en objetos { oid: ?, clase: ?, version: ?, datos: ? }', [f.oid, f.clase, f.version, f.datos])
            for f in cambiados:
                r = self._orden('actualizar objetos poner clase = ?, datos = ?, version = ?, guardado = ahora() donde oid = ? y version = ?',
                                [f.clase, f.datos, f.version, f.oid, f.version - 1])
                if r.rowcount != 1:
                    raise Conflicto(f"otro programa ha cambiado o borrado el objeto {f.oid} ({f.clase}) después de cargarlo")
            self._orden('vaciar raices')
            for nombre, oid in raices.items():
                self._orden('insertar en raices { nombre: ?, oid: ? }', [nombre, oid])
            for f in borrados:
                r = self._orden('borrar de objetos donde oid = ? y version = ?', [f.oid, f.version])
                if r.rowcount != 1:
                    raise Conflicto(f"otro programa ha cambiado o borrado el objeto {f.oid} ({f.clase}) después de cargarlo")
            self._orden('actualizar baul poner valor = valor + 1 donde dato = "generacion"')
            generacion = self.firma()
            self._orden('confirmar')
            return generacion
        except BaseException as e:
            self._orden('deshacer')
            if isinstance(e, ErrorBaul) and 'no se puede repetir' in str(e):
                raise Conflicto("otro programa ha guardado a la vez objetos nuevos con el mismo OID") from None
            raise

    def siguiente_oid(self):
        return (self._orden('buscar objetos mostrar maximo(oid)').scalar() or 0) + 1

    def cerrar(self):
        if self._cliente is not None:
            try:
                self._cliente.cerrar_sesion()
            except sbd.SBDError:
                pass
            self._cliente = None
        if self._db is not None:
            self._db.close()
            self._db = None


def crear(url):
    if url.startswith(('sbd://', 'sbd+http://', 'sbd+https://')):
        return AlmacenSBD(url)
    if url.endswith(('.yaml', '.yml')):
        return AlmacenYAML(url)
    raise ErrorBaul(f"no sé abrir «{url}»: usa un fichero .yaml, sbd:///fichero.base o sbd+https://:CLAVE@servidor/base")
