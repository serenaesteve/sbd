"""Importar CSV y Excel (.xlsx) a una tabla.

Si la tabla no existe se crea adivinando el tipo de cada columna. Si existe, las
columnas del fichero se emparejan por nombre. Todo entra en una sola transacción:
o se importan todas las filas o ninguna.

Entiende lo habitual en hojas en español: separador «;», decimales con coma
(«3,5» o «1.234,5»), fechas «dd/mm/aaaa» y códigos con ceros delante, que se
quedan como texto.
"""
import csv
import datetime
import io
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from .errors import SBDError
from .nodes import Begin, Commit, Insert, Literal, Rollback
from .parser import Parser
from .result import Result
from .storage import quote_ident
from .values import TYPE_NAMES

_M = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_NS = {'m': _M}

_ENTERO = re.compile(r'-?\d+\Z')
_DECIMAL = re.compile(r'-?(\d+\.\d+|\d{1,3}(\.\d{3})*,\d+|\d+,\d+)\Z')
_FECHA_ES = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\Z')
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?\Z')
_LOGICOS = {'verdadero', 'falso', 'sí', 'si', 'no', 'true', 'false'}


# ------------------------------------------------------------------ lectura
def leer_csv(texto):
    texto = texto.lstrip('﻿')
    primera = texto.split('\n', 1)[0]
    separador = max([',', ';', '\t', '|'], key=primera.count)
    filas = [fila for fila in csv.reader(io.StringIO(texto), delimiter=separador)
             if any(celda.strip() for celda in fila)]
    if not filas:
        raise SBDError("el fichero está vacío")
    cabecera, datos = filas[0], filas[1:]
    ancho = len(cabecera)
    datos = [[(c if c.strip() else None) for c in (fila + [''] * ancho)[:ancho]] for fila in datos]
    return [c.strip() for c in cabecera], datos


def leer_texto(datos):
    try:
        return datos.decode('utf-8-sig')
    except UnicodeDecodeError:
        return datos.decode('cp1252')  # CSV guardado por Excel en Windows


def leer_xlsx(datos):
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        raise SBDError("no es un fichero de Excel (.xlsx) válido") from None
    with z:
        nombres = set(z.namelist())
        compartidos = []
        if 'xl/sharedStrings.xml' in nombres:
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', _NS):
                compartidos.append(''.join(t.text or '' for t in si.iter(f'{{{_M}}}t')))
        estilos_fecha = _estilos_de_fecha(z.read('xl/styles.xml')) if 'xl/styles.xml' in nombres else set()
        libro = ET.fromstring(z.read('xl/workbook.xml'))
        hoja = libro.find('m:sheets', _NS)[0]
        rid = hoja.get(f'{{{_R}}}id')
        relaciones = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        destino = next(r.get('Target') for r in relaciones if r.get('Id') == rid)
        ruta = destino.lstrip('/') if destino.startswith('/') else 'xl/' + destino
        raiz = ET.fromstring(z.read(ruta))

    filas = []
    for fila in raiz.iter(f'{{{_M}}}row'):
        celdas = {}
        for c in fila.findall('m:c', _NS):
            columna = _indice_columna(c.get('r')) if c.get('r') else len(celdas)
            celdas[columna] = _valor_celda(c, compartidos, estilos_fecha)
        filas.append([celdas.get(i) for i in range(max(celdas) + 1)] if celdas else [])
    filas = [f for f in filas if any(v not in (None, '') for v in f)]
    if not filas:
        raise SBDError("la primera hoja del Excel está vacía")
    cabecera = ['' if v is None else str(v).strip() for v in filas[0]]
    ancho = len(cabecera)
    datos = [(f + [None] * ancho)[:ancho] for f in filas[1:]]
    return cabecera, datos


def _indice_columna(referencia):
    n = 0
    for ch in referencia:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _estilos_de_fecha(xml):
    raiz = ET.fromstring(xml)
    propios = {}
    formatos = raiz.find('m:numFmts', _NS)
    if formatos is not None:
        for f in formatos:
            propios[int(f.get('numFmtId'))] = f.get('formatCode', '')
    fechas = set()
    xfs = raiz.find('m:cellXfs', _NS)
    if xfs is not None:
        for i, xf in enumerate(xfs.findall('m:xf', _NS)):
            fid = int(xf.get('numFmtId', 0))
            codigo = re.sub(r'"[^"]*"|\[[^\]]*\]', '', propios.get(fid, '')).lower()
            if 14 <= fid <= 22 or 45 <= fid <= 47 or re.search(r'[dyh]', codigo):
                fechas.add(i)
    return fechas


def _valor_celda(c, compartidos, estilos_fecha):
    tipo = c.get('t', 'n')
    v = c.find('m:v', _NS)
    texto = v.text if v is not None else None
    if tipo == 's':
        return compartidos[int(texto)] if texto is not None else None
    if tipo == 'inlineStr':
        nodo = c.find('m:is', _NS)
        return ''.join(t.text or '' for t in nodo.iter(f'{{{_M}}}t')) if nodo is not None else None
    if tipo == 'b':
        return None if texto is None else texto == '1'
    if tipo == 'str':
        return texto
    if tipo == 'e' or texto is None:
        return None
    numero = float(texto)
    if int(c.get('s', 0)) in estilos_fecha:
        momento = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=numero)
        if numero.is_integer():
            return momento.date().isoformat()
        return momento.isoformat(sep=' ', timespec='seconds')
    return int(numero) if numero.is_integer() else numero


# --------------------------------------------------------------- tipos
def _clase(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 'BOOLEAN'
    if isinstance(v, int):
        return 'INTEGER'
    if isinstance(v, float):
        return 'REAL'
    s = v.strip()
    if _ENTERO.match(s):
        return 'TEXT' if len(s.lstrip('-')) > 1 and s.lstrip('-').startswith('0') else 'INTEGER'
    if _DECIMAL.match(s):
        return 'REAL'
    fecha = _FECHA_ES.match(s)
    if fecha or _ISO.match(s):
        return 'TIMESTAMP' if (fecha and fecha.group(4)) or (not fecha and len(s) > 10) else 'DATE'
    if s.lower() in _LOGICOS:
        return 'BOOLEAN'
    return 'TEXT'


def inferir_tipo(valores):
    clases = {_clase(v) for v in valores} - {None}
    if not clases:
        return 'TEXT'
    if len(clases) == 1:
        return clases.pop()
    if clases == {'INTEGER', 'REAL'}:
        return 'REAL'
    if clases == {'DATE', 'TIMESTAMP'}:
        return 'TIMESTAMP'
    return 'TEXT'


def normalizar(v, tipo):
    """El valor del fichero, listo para que lo convierta el motor."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    if tipo in ('INTEGER', 'REAL', 'MONEY') and _DECIMAL.match(s) and ',' in s:
        return s.replace('.', '').replace(',', '.')
    if tipo in ('DATE', 'TIMESTAMP'):
        m = _FECHA_ES.match(s)
        if m:
            dia, mes, año, h, mi, se = m.groups()
            iso = f"{año}-{int(mes):02d}-{int(dia):02d}"
            if h:
                iso += f" {int(h):02d}:{mi}:{se or '00'}"
            return iso
    return s


def nombre_columna(cabecera, i):
    sin_tildes = ''.join(c for c in unicodedata.normalize('NFD', cabecera.strip().lower())
                         if unicodedata.category(c) != 'Mn' or c == '̃')  # conserva la ñ
    nombre = re.sub(r'[^\wñ]+', '_', unicodedata.normalize('NFC', sin_tildes)).strip('_')
    if not nombre:
        return f'columna_{i + 1}'
    return f'c_{nombre}' if nombre[0].isdigit() else nombre


# ------------------------------------------------------------- importar
def importar(db, tabla, cabecera, filas, session=None, label=None):
    """Mete las filas en la tabla (creándola si no existe). Devuelve un Result."""
    if not cabecera:
        raise SBDError("el fichero no tiene cabecera con los nombres de las columnas")
    columnas = []
    for i, c in enumerate(cabecera):
        nombre = nombre_columna(c, i)
        while nombre in columnas:
            nombre += '_2'
        columnas.append(nombre)
    creada = tabla not in db.tables
    propia = not db.in_transaction
    if propia:
        db.run_statement(Begin(), session=session, label=label)
    marca = len(db._undo)
    try:
        if creada:
            if tabla in db.views:
                raise SBDError(f"«{tabla}» es una vista: no se puede importar en ella")
            tipos = [inferir_tipo([f[i] for f in filas]) for i in range(len(columnas))]
            lineas = []
            if 'id' not in columnas:
                lineas.append('id entero clave')
            for c, t in zip(columnas, tipos):
                lineas.append(f"{quote_ident(c)} {TYPE_NAMES[t]}")
            orden = f"crear tabla {quote_ident(tabla)} {{\n  " + '\n  '.join(lineas) + "\n}"
            for stmt in Parser(orden).parse_script():
                db.run_statement(stmt, session=session, label=label)
        t = db.table(tabla)
        por_nombre = {nombre_columna(c.name, 0): c.name for c in t.columns}
        destino, sobran = [], []
        for c in columnas:
            if t.has_column(c):
                destino.append(c)
            elif c in por_nombre:
                destino.append(por_nombre[c])
            else:
                sobran.append(c)
        if sobran:
            raise SBDError(f"{tabla} no tiene las columnas {', '.join(sobran)} del fichero "
                           f"(sus columnas son: {', '.join(t.colnames)})")
        tipos = [t.column(c).type for c in destino]
        valores = [[Literal(normalizar(v, tipo)) for v, tipo in zip(f, tipos)] for f in filas]
        if valores:
            db.run_statement(Insert(tabla, destino, valores), session=session, label=label)
        if propia:
            db.run_statement(Commit(), session=session, label=label)
    except BaseException:
        if propia:
            if db.in_transaction:
                db.run_statement(Rollback(), session=session, label=label)
        elif db.in_transaction and db.owner == session:
            db._undo_to(marca)
        raise
    n = len(filas)
    nota = f" (tabla {tabla} creada con {len(columnas)} columnas)" if creada else ''
    return Result(rowcount=n, message=f"{n} fila{'s' if n != 1 else ''} importada{'s' if n != 1 else ''} "
                                      f"en {tabla}{nota}")


def leer_fichero(nombre, datos):
    """(cabecera, filas) de un CSV o un .xlsx."""
    if nombre.lower().endswith(('.xlsx', '.xlsm')):
        return leer_xlsx(datos)
    if nombre.lower().endswith('.xls'):
        raise SBDError("el formato .xls antiguo no se puede leer: guárdalo como .xlsx o .csv")
    return leer_csv(leer_texto(datos))


def importar_fichero(db, ruta, tabla, session=None, label=None):
    if not os.path.exists(ruta):
        raise SBDError(f"no existe el fichero {ruta}")
    with open(ruta, 'rb') as f:
        cabecera, filas = leer_fichero(ruta, f.read())
    return importar(db, tabla, cabecera, filas, session=session, label=label)
