"""Consola de SBD:  python -m sbd [directorio_de_datos]"""
import argparse
import json
import os
import re
import sys
import time

from .cliente import SesionRemota
from .errors import IncompleteInputError, SBDError
from .functions import FUNCTIONS
from .nodes import AGGREGATES, WINDOW_ONLY
from .parser import KEYWORDS, Parser
from .servidor import Servidor, Sesion
from .values import NUMEROS, format_value

AYUDA = """\
Órdenes de SBD (escribe «ayuda» para ver esto otra vez):

  BASES      bases · crear base tienda · usar tienda · borrar base tienda · copiar base tienda a pruebas
  TABLAS     tablas · describir clientes · indices [de clientes]
             crear tabla clientes { id entero clave  nombre texto(60) obligatorio  saldo dinero = 0 }
             añadir a clientes telefono texto · quitar de clientes telefono
             renombrar tabla clientes a socios · renombrar columna clientes.movil a telefono
             indexar clientes (email) · borrar indice clientes_email_idx · borrar tabla clientes
  DATOS      insertar en clientes { nombre: "Ana", ciudad: "Madrid" }
             insertar en clientes (nombre, ciudad) ("Luis", "Sevilla") ("Sara", "Bilbao")
             actualizar clientes poner ciudad = "Toledo" donde id = 2
             borrar de clientes donde id = 2 · vaciar clientes
  BUSCAR     buscar clientes donde ciudad = "Madrid" ordenar nombre mostrar nombre, email
             buscar pedidos como p unir clientes como c en c.id = p.cliente_id
                    agrupar c.nombre mostrar c.nombre, sumar(p.total) como gasto
             calcular 2 + 2, hoy()
  FECHAS     buscar pedidos donde fecha > hace 30 dias · calcular hoy() + 2 semanas, edad(1990-05-01)
  MÁS        insertar o actualizar en clientes { email: "ana@x.es", nombre: "Ana" } devolver id
             buscar ventas mostrar vendedor, puesto() sobre (por region ordenar total desc) como puesto
             importar "clientes.csv" en clientes   (CSV o Excel; si la tabla no existe, se crea)
             explicar buscar pedidos donde fecha > hace 30 dias   (cómo se ejecuta: ¿usa un índice?)
  CAMBIOS    empezar · confirmar · deshacer
  PROCEDIM.  crear procedimiento subir(id, n) { actualizar t poner x = x + :n donde id = :id }
             ejecutar subir(1, 5) · procedimientos · borrar procedimiento subir
  USUARIOS   usuarios · crear usuario ana clave "…" puede leer|escribir|todo [en tienda]
             cambiar clave de ana a "…" · cambiar permiso de ana a leer · borrar usuario ana
  API        crear clave api para ana [llamada "web"] · claves api · borrar clave api k1a2b3c4
  COPIAS     papelera · recuperar base tienda [como tienda_vieja] · copias
             restaurar base tienda del 2026-10-27 [como tienda_27] · historial [de tienda] [limite 100]

Consola: «leer fichero.sbd» ejecuta un script · «tiempo» activa o desactiva el cronómetro
         Tab completa nombres de tablas, columnas y palabras del lenguaje
         «salir» o Ctrl+D para terminar. Una orden puede ocupar varias líneas: la consola
         espera mientras esté a medias (por ejemplo, con una llave { abierta).
"""


def format_table(result):
    if result.columns is None:
        return result.message
    headers = [str(h) for h in result.columns]
    cells = [[format_value(v) for v in row] for row in result.rows]
    numeric = [[isinstance(v, NUMEROS) and not isinstance(v, bool) for v in row] for row in result.rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, text in enumerate(row):
            widths[i] = max(widths[i], len(text))
    rule = '─┼─'.join('─' * w for w in widths)
    out = [' │ '.join(h.ljust(w) for h, w in zip(headers, widths)), rule]
    for row, nums in zip(cells, numeric):
        out.append(' │ '.join(t.rjust(w) if n else t.ljust(w) for t, w, n in zip(row, widths, nums)))
    n = len(cells)
    out.append(f"({n} fila{'s' if n != 1 else ''})")
    return '\n'.join(out)


def is_complete(text):
    """¿Se puede ejecutar ya? Falso si la orden se corta a mitad (llave abierta, texto sin cerrar…)."""
    try:
        Parser(text).parse_script()
    except IncompleteInputError:
        return False
    except SBDError:
        return True  # que lo cuente la ejecución
    return True


CONTEXTO_TABLA = {'buscar', 'unir', 'opcional', 'completo', 'inverso', 'cruzar', 'de', 'describir', 'vaciar',
                  'indexar', 'actualizar', 'tabla', 'columna', 'vista', 'en'}


class Consola:
    def __init__(self, sesion, salida=sys.stdout, donde=''):
        self.sesion = sesion
        self.donde = donde  # «datos en …» o «conectada a …», para el saludo
        self.salida = salida
        self.cronometro = False
        self._opciones = []

    # ------------------------------------------------------------ completar
    def candidatos(self, palabra, antes):
        """Lo que puede ir donde está el cursor: tablas, columnas, bases o palabras del lenguaje."""
        catalogo = self.sesion.catalogo()
        tablas = catalogo['tablas']  # nombre -> [columnas]
        if '.' in palabra:
            tabla, _, prefijo = palabra.partition('.')
            alias = dict(re.findall(r'(\w+)\s+como\s+(\w+)', antes))
            columnas = tablas.get(tabla) or tablas.get({v: k for k, v in alias.items()}.get(tabla, ''), [])
            return [f"{tabla}.{c}" for c in columnas if c.startswith(prefijo)]
        anterior = (re.findall(r'(\w+)\s*$', antes) or [''])[0].lower()
        if anterior == 'usar' or antes.rstrip().endswith('base'):
            opciones = catalogo['bases']
        elif anterior in CONTEXTO_TABLA and tablas:
            opciones = list(tablas) + list(catalogo['vistas'])
        else:
            mencionadas = [cols for n, cols in tablas.items() if re.search(rf'\b{re.escape(n)}\b', antes)]
            opciones = ([c for cols in mencionadas for c in cols] + list(tablas) + sorted(KEYWORDS)
                        + [f + '(' for f in sorted(set(FUNCTIONS) | AGGREGATES | WINDOW_ONLY)])
        vistas = set()
        return [o for o in opciones if o.startswith(palabra) and not (o in vistas or vistas.add(o))]

    def _completar(self, texto, n):
        import readline
        if n == 0:
            linea = readline.get_line_buffer()
            try:
                self._opciones = self.candidatos(texto, linea[:readline.get_begidx()])
            except SBDError:
                self._opciones = []
        return self._opciones[n] if n < len(self._opciones) else None

    def escribir(self, texto=''):
        print(texto, file=self.salida)

    def prompt(self):
        base = self.sesion.base
        if base is None:
            return 'sbd› '
        return f"sbd:{base}{'*' if self.sesion.en_transaccion() else ''}› "

    def ejecutar(self, texto):
        """Ejecuta y muestra cada orden. Devuelve False si alguna falló."""
        inicio = time.perf_counter()
        try:
            for resultado in self.sesion.iterar(texto):
                self.escribir(format_table(resultado))
                if self.cronometro:
                    self.escribir(f"({(time.perf_counter() - inicio) * 1000:.1f} ms)")
                inicio = time.perf_counter()
        except SBDError as e:
            self.escribir(f"✕ {e}")
            return False
        return True

    def orden_de_consola(self, linea):
        """ayuda, salir, leer y tiempo. Devuelve None si no es una de ellas, False para salir."""
        palabra, _, resto = linea.strip().partition(' ')
        palabra = palabra.lower()
        if palabra in ('salir', 'adios', 'adiós'):
            return False
        if palabra == 'ayuda':
            self.escribir(AYUDA)
            return True
        if palabra == 'tiempo':
            self.cronometro = not self.cronometro
            self.escribir(f"cronómetro {'activado' if self.cronometro else 'desactivado'}")
            return True
        if palabra == 'leer' and resto.strip():
            ruta = resto.strip().strip('"\'')
            try:
                with open(ruta, encoding='utf-8') as f:
                    self.ejecutar(f.read())
            except OSError as e:
                self.escribir(f"✕ no se puede leer {ruta}: {e.strerror}")
            return True
        return None

    def bucle(self):
        try:
            import readline  # historial, edición de línea y Tab para completar
            readline.set_completer(self._completar)
            readline.set_completer_delims(' \t\n()[]{},;:=<>!+-*/%&"\'')
            readline.parse_and_bind('tab: complete')
        except ImportError:
            pass
        self.escribir(f"SBD — {self.donde}")
        bases = self.sesion.catalogo()['bases']
        if self.sesion.base:
            self.escribir(f"Usando {self.sesion.base}. Escribe «ayuda» para ver las órdenes.")
        elif bases:
            self.escribir(f"Bases: {', '.join(bases)}. Elige una con «usar nombre», o escribe «ayuda».")
        else:
            self.escribir("No hay ninguna base todavía: empieza con «crear base nombre», o escribe «ayuda».")
        pendiente = []
        while True:
            try:
                linea = input('   … ' if pendiente else self.prompt())
            except EOFError:
                self.escribir()
                break
            except KeyboardInterrupt:
                self.escribir()
                pendiente = []
                continue
            if not pendiente:
                if not linea.strip():
                    continue
                respuesta = self.orden_de_consola(linea)
                if respuesta is False:
                    break
                if respuesta:
                    continue
            pendiente.append(linea)
            texto = '\n'.join(pendiente)
            if is_complete(texto) or not linea.strip():
                pendiente = []
                self.ejecutar(texto)
        if self.sesion.en_transaccion():
            self.escribir("Había cambios sin confirmar: se han deshecho.")


def _info_servidor(datos):
    """Si hay un servidor de SBD en marcha sobre esa carpeta, su dirección y la clave local."""
    try:
        with open(os.path.join(datos, '.sbd.servidor'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def abrir_sesion(args):
    """(sesión, «dónde», función para terminar). Local si se puede; si no, a través del servidor."""
    if args.servidor:
        clave = args.clave or os.environ.get('SBD_CLAVE')
        if not clave:
            raise SBDError("para conectarte a un servidor hace falta su clave: --clave sbd_… o la variable SBD_CLAVE")
        sesion = SesionRemota(args.servidor, clave, args.base)
        return sesion, f"conectada a {args.servidor}", sesion.cerrar
    try:
        servidor = Servidor(args.datos, quien='consola')
    except SBDError:
        info = _info_servidor(args.datos)
        if info is None:
            raise
        sesion = SesionRemota(info['url'], info['clave'], args.base, directorio=os.getcwd(),
                              verificar_certificado=False)  # es este mismo equipo
        return sesion, f"conectada al servidor en marcha ({info['url']})", sesion.cerrar
    sesion = Sesion(servidor, args.base)

    def terminar():
        sesion.cerrar()
        servidor.cerrar()
    return sesion, f"datos en {servidor.directorio}", terminar


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sbd', description='SBD: bases de datos con lenguaje propio')
    parser.add_argument('datos', nargs='?', default='datos',
                        help='directorio donde se guardan las bases (por defecto ./datos)')
    parser.add_argument('-b', '--base', help='base que se usa al empezar')
    parser.add_argument('-e', '--ejecutar', metavar='ORDEN', help='ejecuta esta orden y termina')
    parser.add_argument('-f', '--fichero', metavar='SCRIPT', help='ejecuta este script .sbd y termina')
    parser.add_argument('--servidor', metavar='URL', help='conectarse a un servidor de SBD (https://…)')
    parser.add_argument('--clave', help='clave de API para --servidor (o la variable SBD_CLAVE)')
    args = parser.parse_args(argv)
    try:
        sesion, donde, terminar = abrir_sesion(args)
    except SBDError as e:
        print(f"✕ {e}", file=sys.stderr)
        return 1
    consola = Consola(sesion, donde=donde)
    if args.ejecutar or args.fichero:
        ok = True
        if args.fichero:
            if not os.path.exists(args.fichero):
                print(f"✕ no existe el fichero {args.fichero}", file=sys.stderr)
                terminar()
                return 1
            with open(args.fichero, encoding='utf-8') as f:
                ok = consola.ejecutar(f.read())
        if args.ejecutar and ok:
            ok = consola.ejecutar(args.ejecutar)
        terminar()
        return 0 if ok else 1
    try:
        consola.bucle()
    finally:
        terminar()
    return 0
