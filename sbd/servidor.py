"""Varias bases de datos en un mismo directorio.

Cada base es un fichero «nombre.base» dentro del directorio de datos. Una Sesion
recuerda qué base se está usando y entiende las órdenes que trabajan con bases
enteras: crear base, usar, bases y borrar base.

«borrar base» no destruye nada: mueve el fichero a la carpeta .papelera, de
donde «recuperar base» lo devuelve. Un cerrojo sobre el directorio impide que dos
programas de SBD (la consola y el panel, por ejemplo) escriban a la vez en él.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
import tempfile
from contextlib import contextmanager

from .cerrojo import Cerrojo
from .database import Database
from .parser import Parser
from .errors import PermisoDenegado, SBDError
from .nodes import (AlterTable, Begin, ChangePassword, ChangePermission, Commit, CopyDatabase, CreateApiKey, CreateIndex,
                    CreateTable, Explain, CreateDatabase, CreateUser, Describe, DropApiKey, DropDatabase, DropUser,
                    Delete, DropTable, DropIndex, DropView,
                    Import, Insert, RecoverDatabase, Rollback, Select, RestoreBackup, ShowApiKeys, ShowBackups,
                    ShowDatabases, ShowHistory, ShowIndexes, ShowTables, ShowTrash, ShowUsers, Update, UseDatabase)
from .result import Result
from .values import json_por_defecto

EXTENSION = '.base'
_VALID_NAME = re.compile(r'[a-zñ_][a-z0-9ñ_]{0,62}\Z')
PERMISOS = ('leer', 'escribir', 'todo')
SOLO_LECTURA = (Select, ShowTables, ShowIndexes, Describe, ShowDatabases, UseDatabase, Begin, Commit, Rollback,
                Explain)
SIN_AUDITAR = SOLO_LECTURA + (ShowTrash, ShowUsers, ShowApiKeys, ShowBackups, ShowHistory)
_CLAVE_EN_TEXTO = re.compile(r"""(\bclave\b(?:\s+de\s+\S+\s+a)?\s+)("(?:[^"]|"")*"|'(?:[^']|'')*')""", re.IGNORECASE)
GESTION = (CreateDatabase, DropDatabase, RecoverDatabase, ShowTrash, CreateUser, DropUser, ChangePermission, ShowUsers,
           CopyDatabase, ShowBackups, RestoreBackup, ShowHistory)
MIN_CLAVE = 8
# «cuenta»: alguien que se ha registrado. Solo ve sus bases, hace de todo en ellas y tiene límites.
CUENTA = 'cuenta'
USUARIOS_PROPIOS = (CreateUser, DropUser, ChangePermission, ShowUsers)
NUEVA_BASE = (CreateDatabase, CopyDatabase, RestoreBackup, RecoverDatabase)
CRECE = (Insert, Update, CreateTable, CreateIndex, AlterTable, Import)  # lo que puede ocupar más memoria
AJUSTES = {'registro': False, 'max_bases': 10, 'max_mb': 100, 'max_filas': 250_000, 'max_memoria_mb': 6000}
RESERVADOS = {'admin', 'administrador', 'root', 'sbd', 'sistema', 'soporte', 'todo', 'todas', 'nadie'}
_EN_PAPELERA = re.compile(r'([a-zñ_][a-z0-9ñ_]*)-(\d{8}-\d{6})' + re.escape(EXTENSION) + r'\Z')


class Servidor:
    def __init__(self, directorio='datos', quien='Python', limite_segundos=None):
        self.limite_segundos = limite_segundos  # tope de duración de cada orden
        self.directorio = os.path.abspath(directorio)
        self.proteger_ultimo_usuario = False
        os.makedirs(self.directorio, exist_ok=True)
        self.quien = quien
        self._cerrojo = Cerrojo(os.path.join(self.directorio, '.sbd.lock'),
                                f"La carpeta de datos {self.directorio}", quien)
        self._abiertas = {}
        self.papelera_dir = os.path.join(self.directorio, '.papelera')
        self.copias_dir = os.path.join(self.directorio, '.copias')
        self.auditoria_dir = os.path.join(self.directorio, '.auditoria')
        self._ruta_usuarios = os.path.join(self.directorio, '.usuarios')
        self._usuarios = self._leer_usuarios()
        self._ruta_claves = os.path.join(self.directorio, '.claves')
        self._claves = self._leer_json(self._ruta_claves, 'claves')
        self._ruta_ajustes = os.path.join(self.directorio, '.ajustes')
        self._ajustes = {**AJUSTES, **self._leer_json(self._ruta_ajustes, 'ajustes')}
        self._uso = {}       # base -> último momento en que se usó (para soltar las que nadie usa)
        self._filas = {}     # base cerrada -> cuántas filas tenía (para los límites sin volver a abrirla)

    # ------------------------------------------------------------ ajustes
    def ajustes(self):
        return dict(self._ajustes)

    def cambiar_ajustes(self, **cambios):
        for clave, valor in cambios.items():
            if clave not in AJUSTES:
                raise SBDError(f"ajuste desconocido: {clave}")
            if clave == 'registro':
                valor = bool(valor)
            elif not isinstance(valor, int) or isinstance(valor, bool) or valor < 1:
                raise SBDError(f"«{clave}» tiene que ser un número entero mayor que cero")
            self._ajustes[clave] = valor
        self._guardar_json(self._ruta_ajustes, 'ajustes', self._ajustes)
        return self.ajustes()

    def _leer_json(self, ruta, campo):
        if not os.path.exists(ruta):
            return {}
        with open(ruta, encoding='utf-8') as f:
            return json.load(f).get(campo, {})

    def _guardar_json(self, ruta, campo, datos):
        tmp = ruta + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({campo: datos}, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, ruta)

    # --------------------------------------------------------- claves de API
    def crear_clave_api(self, usuario, nombre=None):
        """La clave completa solo se ve ahora; después solo queda su resumen."""
        if usuario not in self._usuarios:
            raise SBDError(f"no existe ningún usuario «{usuario}»: crea antes el usuario con «crear usuario …»")
        ident = 'k' + secrets.token_hex(4)  # empieza por letra: así se escribe tal cual en «borrar clave api»
        while ident in self._claves:
            ident = 'k' + secrets.token_hex(4)
        secreto = secrets.token_urlsafe(24)
        self._claves[ident] = {'usuario': usuario, 'nombre': nombre or '',
                               'resumen': hashlib.sha256(secreto.encode()).hexdigest(),
                               'creada': time.strftime('%Y-%m-%d %H:%M'), 'usada': None}
        self._guardar_json(self._ruta_claves, 'claves', self._claves)
        return f"sbd_{ident}_{secreto}", ident

    def verificar_clave_api(self, clave):
        """El usuario dueño de la clave, o None."""
        partes = str(clave).split('_', 2)
        if len(partes) != 3 or partes[0] != 'sbd':
            return None
        datos = self._claves.get(partes[1])
        if datos is None:
            return None
        if not hmac.compare_digest(hashlib.sha256(partes[2].encode()).hexdigest(), datos['resumen']):
            return None
        datos['usada'] = time.strftime('%Y-%m-%d %H:%M')
        return self.usuario(datos['usuario'])

    def lista_claves(self, usuario=None):
        return [(i, d['usuario'], d['nombre'], d['creada'], d['usada'] or 'nunca')
                for i, d in sorted(self._claves.items(), key=lambda kv: kv[1]['creada'])
                if usuario is None or d['usuario'] == usuario]

    def borrar_clave_api(self, ident, usuario=None):
        datos = self._claves.get(ident)
        if datos is None or (usuario is not None and datos['usuario'] != usuario):
            raise SBDError(f"no hay ninguna clave «{ident}» (escribe «claves api» para verlas)")
        del self._claves[ident]
        self._guardar_json(self._ruta_claves, 'claves', self._claves)

    # ---------------------------------------------------------------- usuarios
    def _leer_usuarios(self):
        if not os.path.exists(self._ruta_usuarios):
            return {}
        with open(self._ruta_usuarios, encoding='utf-8') as f:
            return json.load(f).get('usuarios', {})

    def _guardar_usuarios(self):
        tmp = self._ruta_usuarios + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'usuarios': self._usuarios}, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._ruta_usuarios)

    @staticmethod
    def _resumen_clave(clave, sal):
        return hashlib.scrypt(clave.encode('utf-8'), salt=sal, n=2 ** 14, r=8, p=1, dklen=32).hex()

    def hay_usuarios(self):
        return bool(self._usuarios)

    def usuario(self, nombre):
        datos = self._usuarios.get(nombre)
        return None if datos is None else {'nombre': nombre, 'permiso': datos['permiso'], 'bases': list(datos['bases'])}

    def contar_cuentas(self):
        return sum(1 for d in self._usuarios.values() if d['permiso'] == CUENTA)

    def registrar(self, nombre, clave):
        """Una cuenta nueva desde la web: sin bases todavía, con los límites de los ajustes."""
        if not self._ajustes['registro']:
            raise PermisoDenegado("el registro de cuentas nuevas está cerrado")
        if nombre in RESERVADOS:
            raise SBDError(f"«{nombre}» está reservado: elige otro nombre")
        self.validar(nombre, 'nombre de usuario')
        if nombre in self._usuarios:
            raise SBDError(f"ya existe una cuenta «{nombre}»: elige otro nombre")
        self._comprobar_clave(clave)
        sal = secrets.token_bytes(16)
        self._usuarios[nombre] = {'sal': sal.hex(), 'resumen': self._resumen_clave(clave, sal), 'permiso': CUENTA,
                                  'bases': [], 'borradas': [], 'alta': time.strftime('%Y-%m-%d %H:%M')}
        self._guardar_usuarios()
        return self.usuario(nombre)

    def es_suya(self, usuario, base, o_borrada=False):
        datos = self._usuarios.get(usuario, {})
        return base in datos.get('bases', ()) or (o_borrada and base in datos.get('borradas', ()))

    def apuntar_base(self, usuario, base, borrada=False):
        """La base pasa a ser de la cuenta (o deja de serlo y queda en su papelera)."""
        datos = self._usuarios.get(usuario)
        if datos is None or datos['permiso'] != CUENTA:
            return
        datos.setdefault('borradas', [])
        if borrada:
            datos['bases'] = [b for b in datos['bases'] if b != base]
            if base not in datos['borradas']:
                datos['borradas'].append(base)
        elif base not in datos['bases']:
            datos['bases'].append(base)
        self._guardar_usuarios()

    def nombre_ajeno(self, usuario, nombre):
        """¿Ha usado otra persona este nombre de base? (existe, o está en la papelera o en las copias).
        Si una cuenta pudiera reutilizarlo, recuperaría la papelera o las copias de la otra."""
        if self.es_suya(usuario, nombre, o_borrada=True):
            return False
        if self.existe(nombre) or any(e[0] == nombre for e in self.papelera()) or any(c[1] == nombre for c in self.copias()):
            return True
        return any(nombre in d.get('borradas', ()) for n, d in self._usuarios.items() if n != usuario)

    # ------------------------------------------------------------- límites
    def filas_de(self, base):
        if base in self._abiertas:
            return sum(len(t.rows) for t in self._abiertas[base].tables.values())
        if base not in self._filas:
            self._filas[base] = sum(len(t.rows) for t in self.abrir(base).tables.values())
        return self._filas[base]

    def uso(self, usuario):
        bases = [b for b in self._usuarios.get(usuario, {}).get('bases', []) if self.existe(b)]
        return {'bases': len(bases), 'bytes': sum(os.path.getsize(f) for b in bases for f in self._ficheros(b)),
                'filas': sum(self.filas_de(b) for b in bases), **self._ajustes}

    @staticmethod
    def memoria_mb():
        try:
            with open('/proc/self/status') as f:
                for linea in f:
                    if linea.startswith('VmRSS:'):
                        return int(linea.split()[1]) // 1024
        except OSError:
            pass
        return 0

    def comprobar_cuota(self, usuario, nueva_base=False):
        """Para las cuentas: lanza un error claro si la orden las haría pasar de sus límites."""
        u = self.uso(usuario)
        mb = u['bytes'] / 1024 / 1024
        if nueva_base and u['bases'] >= u['max_bases']:
            raise PermisoDenegado(f"tu cuenta ya tiene {u['bases']} bases, el máximo: borra alguna para crear otra")
        if mb >= u['max_mb']:
            raise PermisoDenegado(f"tu cuenta ocupa {mb:.1f} MB de {u['max_mb']}: borra datos para seguir añadiendo")
        if u['filas'] >= u['max_filas']:
            raise PermisoDenegado(f"tu cuenta tiene {u['filas']:,} filas, el máximo es {u['max_filas']:,}: "
                                  "borra filas o tablas para seguir añadiendo".replace(',', '.'))
        if self.memoria_mb() >= u['max_memoria_mb']:
            raise PermisoDenegado("el servidor está lleno ahora mismo: vuelve a intentarlo en un rato")

    def comprobar_cuota_resultado(self, usuario, db, adicional=False):
        """Comprueba el resultado antes de confirmarlo, incluyendo el diario pendiente."""
        u = self.uso(usuario)
        filas = sum(len(t.rows) for t in db.tables.values())
        total_filas = u['filas'] + (filas if adicional else 0)
        if total_filas > u['max_filas']:
            raise PermisoDenegado('la operación superaría el máximo de filas de tu cuenta')
        foto = len(json.dumps(db._snapshot_data(), ensure_ascii=False, default=json_por_defecto).encode('utf-8'))
        pendientes = [r for _, r in (db._undo or ()) if r is not None]
        diario = sum(len(json.dumps(r, ensure_ascii=False, default=json_por_defecto).encode('utf-8')) + 128
                     for r in pendientes)
        proyectado = max(foto + db._journal_bytes, db.bytes_on_disk + diario)
        total_bytes = u['bytes'] + proyectado - (0 if adicional else db.bytes_on_disk)
        if total_bytes > u['max_mb'] * 1024 * 1024:
            raise PermisoDenegado('la operación superaría el máximo de espacio de tu cuenta')

    def soltar_inactivas(self, segundos):
        """Guarda y saca de la memoria las bases que nadie ha usado en un rato."""
        ahora = time.monotonic()
        sueltas = []
        for nombre, db in list(self._abiertas.items()):
            if not db.in_transaction and ahora - self._uso.get(nombre, 0) > segundos:
                self._filas[nombre] = sum(len(t.rows) for t in db.tables.values())
                db.close()
                del self._abiertas[nombre]
                sueltas.append(nombre)
        return sueltas

    def lista_usuarios(self):
        return [self.usuario(n) for n in sorted(self._usuarios)]

    def verificar(self, nombre, clave):
        """El usuario si la clave es correcta; None si no (tarda lo mismo exista o no)."""
        datos = self._usuarios.get(nombre)
        sal = bytes.fromhex(datos['sal']) if datos else b'\0' * 16
        resumen = self._resumen_clave(clave, sal)
        if datos and hmac.compare_digest(resumen, datos['resumen']):
            return self.usuario(nombre)
        return None

    def _comprobar_clave(self, clave):
        if len(clave) < MIN_CLAVE:
            raise SBDError(f"la clave necesita al menos {MIN_CLAVE} caracteres")

    def _comprobar_permiso(self, permiso, bases):
        if permiso not in PERMISOS:
            raise SBDError("el permiso es leer, escribir o todo")
        for b in bases:
            if b != '*':
                self.validar(b)

    def crear_usuario(self, nombre, clave, permiso='leer', bases=('*',)):
        self.validar(nombre)
        if nombre in self._usuarios:
            raise SBDError(f"ya existe un usuario «{nombre}»")
        self._comprobar_clave(clave)
        self._comprobar_permiso(permiso, bases)
        sal = secrets.token_bytes(16)
        self._usuarios[nombre] = {'sal': sal.hex(), 'resumen': self._resumen_clave(clave, sal),
                                  'permiso': permiso, 'bases': list(bases)}
        self._guardar_usuarios()

    def cambiar_clave(self, nombre, clave):
        if nombre not in self._usuarios:
            raise SBDError(f"no existe ningún usuario «{nombre}»")
        self._comprobar_clave(clave)
        sal = secrets.token_bytes(16)
        self._usuarios[nombre].update(sal=sal.hex(), resumen=self._resumen_clave(clave, sal))
        self._guardar_usuarios()

    def cambiar_permiso(self, nombre, permiso, bases):
        if nombre not in self._usuarios:
            raise SBDError(f"no existe ningún usuario «{nombre}»")
        self._comprobar_permiso(permiso, bases)
        if permiso != 'todo' and self._usuarios[nombre]['permiso'] == 'todo' and self._ultimo_con_todo(nombre):
            raise SBDError(f"{nombre} es el único usuario con permiso «todo»: dáselo antes a otro")
        self._usuarios[nombre].update(permiso=permiso, bases=list(bases))
        self._guardar_usuarios()

    def borrar_usuario(self, nombre):
        if nombre not in self._usuarios:
            raise SBDError(f"no existe ningún usuario «{nombre}»")
        if self.proteger_ultimo_usuario and len(self._usuarios) == 1:
            raise SBDError('no se puede borrar el último usuario mientras el servidor está expuesto')
        if self._usuarios[nombre]['permiso'] == 'todo' and self._ultimo_con_todo(nombre) and len(self._usuarios) > 1:
            raise SBDError(f"{nombre} es el único usuario con permiso «todo»: dáselo antes a otro")
        del self._usuarios[nombre]
        self._guardar_usuarios()
        self._claves = {i: d for i, d in self._claves.items() if d['usuario'] != nombre}  # sus claves dejan de valer
        self._guardar_json(self._ruta_claves, 'claves', self._claves)

    def _ultimo_con_todo(self, nombre):
        return [n for n, d in self._usuarios.items() if d['permiso'] == 'todo'] == [nombre]

    def validar(self, nombre, que='nombre de base'):
        if not isinstance(nombre, str) or not _VALID_NAME.match(nombre):
            raise SBDError(f"«{nombre}» no vale como {que}: usa minúsculas, números y _ "
                           "(sin espacios ni tildes salvo la ñ), empezando por una letra")
        return nombre

    def ruta(self, nombre):
        return os.path.join(self.directorio, self.validar(nombre) + EXTENSION)

    def existe(self, nombre):
        return os.path.exists(self.ruta(nombre))

    def nombres(self):
        return sorted(f[:-len(EXTENSION)] for f in os.listdir(self.directorio)
                      if f.endswith(EXTENSION) and _VALID_NAME.match(f[:-len(EXTENSION)]))

    def abrir(self, nombre):
        self._uso[nombre] = time.monotonic()
        if nombre not in self._abiertas:
            if not self.existe(nombre):
                raise SBDError(f"no existe ninguna base «{nombre}» (créala con «crear base {nombre}»)")
            db = Database(self.ruta(nombre), self.quien)
            db.time_limit = self.limite_segundos
            self._abiertas[nombre] = db
            self._filas.pop(nombre, None)
        return self._abiertas[nombre]

    def crear(self, nombre, si_no_existe=False):
        if self.existe(nombre):
            if si_no_existe:
                return False
            raise SBDError(f"ya existe una base llamada «{nombre}»")
        db = Database(self.ruta(nombre), self.quien)
        db.time_limit = self.limite_segundos
        db._save()
        self._abiertas[nombre] = db
        self._uso[nombre] = time.monotonic()
        return True

    def borrar(self, nombre, si_existe=False):
        if not self.existe(nombre):
            if si_existe:
                return None
            raise SBDError(f"no existe ninguna base «{nombre}»")
        db = self._abiertas.get(nombre)
        if db is not None and db.in_transaction:
            raise SBDError(f"«{nombre}» tiene una transacción abierta: «confirmar» o «deshacer» antes de borrarla")
        if db is not None:
            db.close()  # compacta el diario en la foto y suelta el cerrojo
            del self._abiertas[nombre]
        os.makedirs(self.papelera_dir, exist_ok=True)
        sello = time.strftime('%Y%m%d-%H%M%S')
        while os.path.exists(os.path.join(self.papelera_dir, f"{nombre}-{sello}{EXTENSION}")):
            time.sleep(1)
            sello = time.strftime('%Y%m%d-%H%M%S')
        destino = os.path.join(self.papelera_dir, f"{nombre}-{sello}{EXTENSION}")
        shutil.move(self.ruta(nombre), destino)
        diario = self.ruta(nombre)[:-len(EXTENSION)] + '.diario'
        if os.path.exists(diario):
            shutil.move(diario, destino[:-len(EXTENSION)] + '.diario')
        if os.path.exists(self.ruta(nombre) + '.lock'):
            os.remove(self.ruta(nombre) + '.lock')
        return destino

    # ---------------------------------------------------------- auditoría
    def auditar(self, quien, base, orden, resultado):
        """Una línea por cada orden que cambia algo: quién, cuándo, dónde y qué."""
        os.makedirs(self.auditoria_dir, exist_ok=True)
        linea = json.dumps({'cuando': time.strftime('%Y-%m-%d %H:%M:%S'), 'quien': quien, 'base': base,
                            'orden': _CLAVE_EN_TEXTO.sub(r'\1"••••••••"', orden), 'resultado': resultado},
                           ensure_ascii=False)
        with open(os.path.join(self.auditoria_dir, time.strftime('%Y-%m') + '.log'), 'a', encoding='utf-8') as f:
            f.write(linea + '\n')

    def historial(self, base=None, limite=50, solo=None):
        if not os.path.isdir(self.auditoria_dir):
            return []
        filas = []
        for nombre in sorted(os.listdir(self.auditoria_dir), reverse=True):
            with open(os.path.join(self.auditoria_dir, nombre), encoding='utf-8') as f:
                lineas = f.readlines()
            for linea in reversed(lineas):
                try:
                    e = json.loads(linea)
                except ValueError:
                    continue
                if (base is None or e['base'] == base) and (solo is None or e['base'] in solo):
                    filas.append((e['cuando'], e['quien'], e['base'] or '', e['orden'], e['resultado']))
                    if len(filas) >= limite:
                        return filas
        return filas

    # ------------------------------------------------------------- copias
    def _ficheros(self, nombre):
        ruta = self.ruta(nombre)
        return [p for p in (ruta, ruta[:-len(EXTENSION)] + '.diario') if os.path.exists(p)]

    def copiar(self, nombre, destino):
        """Copia lo confirmado de una base (los ficheros del disco) con otro nombre."""
        if not self.existe(nombre):
            raise SBDError(f"no existe ninguna base «{nombre}»")
        self.validar(destino)
        if self.existe(destino):
            raise SBDError(f"ya existe una base «{destino}»")
        for origen in self._ficheros(nombre):
            extension = EXTENSION if origen.endswith(EXTENSION) else '.diario'
            shutil.copy2(origen, self.ruta(destino)[:-len(EXTENSION)] + extension)

    def copia_diaria(self, conservar=7):
        """Copia de todas las bases en .copias/AAAA-MM-DD; borra las de hace más de «conservar» días."""
        dia = time.strftime('%Y-%m-%d')
        carpeta = os.path.join(self.copias_dir, dia)
        os.makedirs(carpeta, exist_ok=True)
        for nombre in self.nombres():
            for origen in self._ficheros(nombre):
                shutil.copy2(origen, os.path.join(carpeta, os.path.basename(origen)))
        for origen in (self._ruta_usuarios, self._ruta_claves):  # para poder rehacer el servidor entero
            if os.path.exists(origen):
                shutil.copy2(origen, os.path.join(carpeta, os.path.basename(origen)))
        for vieja in sorted(os.listdir(self.copias_dir))[:-conservar] if conservar > 0 else []:
            shutil.rmtree(os.path.join(self.copias_dir, vieja), ignore_errors=True)
        return carpeta

    def copias(self):
        """[(día, base, bytes)], lo más reciente primero."""
        if not os.path.isdir(self.copias_dir):
            return []
        filas = []
        for dia in sorted(os.listdir(self.copias_dir), reverse=True):
            carpeta = os.path.join(self.copias_dir, dia)
            for f in sorted(os.listdir(carpeta)):
                if f.endswith(EXTENSION):
                    nombre = f[:-len(EXTENSION)]
                    tamaño = sum(os.path.getsize(os.path.join(carpeta, g)) for g in (f, nombre + '.diario')
                                 if os.path.exists(os.path.join(carpeta, g)))
                    filas.append((dia, nombre, tamaño))
        return filas

    def restaurar(self, nombre, dia, como=None):
        destino = self.validar(como or nombre)
        carpeta = os.path.join(self.copias_dir, dia)
        origen = os.path.join(carpeta, self.validar(nombre) + EXTENSION)
        if not os.path.exists(origen):
            raise SBDError(f"no hay copia de «{nombre}» del {dia} (escribe «copias» para verlas)")
        if self.existe(destino):
            raise SBDError(f"ya existe una base «{destino}»: restaura con otro nombre, "
                           f"restaurar base {nombre} del {dia} como {nombre}_{dia.replace('-', '')}")
        shutil.copy2(origen, self.ruta(destino))
        diario = os.path.join(carpeta, nombre + '.diario')
        if os.path.exists(diario):
            shutil.copy2(diario, self.ruta(destino)[:-len(EXTENSION)] + '.diario')

    def papelera(self):
        """[(nombre, cuándo se borró, bytes, fichero)], lo más reciente primero."""
        if not os.path.isdir(self.papelera_dir):
            return []
        entradas = []
        for f in os.listdir(self.papelera_dir):
            m = _EN_PAPELERA.match(f)
            if m:
                cuando = time.strftime('%Y-%m-%d %H:%M:%S', time.strptime(m.group(2), '%Y%m%d-%H%M%S'))
                entradas.append((m.group(1), cuando, os.path.getsize(os.path.join(self.papelera_dir, f)), f))
        return sorted(entradas, key=lambda e: e[1], reverse=True)

    def recuperar(self, nombre, como=None):
        """Devuelve a su sitio la copia más reciente de «nombre» que haya en la papelera."""
        self.validar(nombre)
        destino = self.validar(como or nombre)
        copias = [e for e in self.papelera() if e[0] == nombre]
        if not copias:
            raise SBDError(f"no hay ninguna copia de «{nombre}» en la papelera (escribe «papelera» para verla)")
        if self.existe(destino):
            raise SBDError(f"ya existe una base «{destino}»: recupérala con otro nombre, "
                           f"recuperar base {nombre} como {nombre}_copia")
        origen = os.path.join(self.papelera_dir, copias[0][3])
        shutil.move(origen, self.ruta(destino))
        diario = origen[:-len(EXTENSION)] + '.diario'
        if os.path.exists(diario):
            shutil.move(diario, self.ruta(destino)[:-len(EXTENSION)] + '.diario')
        return copias[0][1]

    def caducar(self, max_idle):
        """Deshace las transacciones abandonadas. Devuelve las bases afectadas."""
        return [n for n, db in self._abiertas.items() if db.expire_idle(max_idle)]

    def resumen(self, nombres=None):
        """Nombre, tamaño en bytes y número de tablas de cada base (de todas, o de las de «nombres»)."""
        datos = []
        for nombre in self.nombres() if nombres is None else [n for n in self.nombres() if n in nombres]:
            db = self.abrir(nombre)
            datos.append({'nombre': nombre, 'bytes': db.bytes_on_disk,
                          'tablas': len(db.tables), 'vistas': len(db.views),
                          'transaccion': db.in_transaction})
        return datos

    def cerrar(self):
        for db in self._abiertas.values():
            db.close()
        self._abiertas.clear()
        if self._claves:
            self._guardar_json(self._ruta_claves, 'claves', self._claves)  # guarda el «último uso»
        if self._cerrojo is not None:
            self._cerrojo.soltar()
            self._cerrojo = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()


class Sesion:
    """Una conversación con el servidor: qué base se usa y cómo ejecutar órdenes."""

    def __init__(self, servidor, base=None, permite_ficheros=True, usuario=None, id=None, directorio=None,
                 etiqueta=None):
        self.servidor = servidor
        self.directorio = directorio  # para resolver «importar "fichero"» relativo a quien lo escribe
        self.permite_ficheros = permite_ficheros  # «importar "fichero"» lee del disco: no desde la web
        self.usuario = usuario                    # None: sin límites (consola, Python)
        self.id = id or secrets.token_hex(12)     # dueño de las transacciones que abra
        self.etiqueta = etiqueta or (usuario['nombre'] if usuario else servidor.quien)  # quién firma en la auditoría
        self.base = None
        if base:
            self.usar(base)

    @property
    def db(self):
        if self.base is None:
            raise SBDError("no hay ninguna base en uso: escribe «usar nombre» "
                           "(o «crear base nombre» para empezar una)")
        return self.servidor.abrir(self.base)

    def puede_ver(self, base):
        return self.usuario is None or '*' in self.usuario['bases'] or base in self.usuario['bases']

    def bases_visibles(self):
        return [b for b in self.servidor.nombres() if self.puede_ver(b)]

    def _permitir(self, orden):
        u = self.usuario
        if u is None:
            return
        if isinstance(orden, ChangePassword):
            if u['permiso'] != 'todo' and orden.name != u['nombre']:
                raise PermisoDenegado("solo puedes cambiar tu propia clave")
            return
        if isinstance(orden, (CreateApiKey, DropApiKey, ShowApiKeys)):
            if isinstance(orden, CreateApiKey) and orden.user not in (None, u['nombre']) and u['permiso'] != 'todo':
                raise PermisoDenegado("solo puedes crear claves de API para ti")
            return
        if u['permiso'] == CUENTA:
            self._permitir_cuenta(orden)
            return
        if isinstance(orden, GESTION) and u['permiso'] != 'todo':
            raise PermisoDenegado("gestionar bases y usuarios necesita el permiso «todo»")
        if u['permiso'] == 'leer' and not isinstance(orden, SOLO_LECTURA):
            raise PermisoDenegado(f"el usuario {u['nombre']} solo puede leer (buscar, describir, tablas…)")
        base = orden.name if isinstance(orden, UseDatabase) else self.base
        if base and not isinstance(orden, GESTION) and not self.puede_ver(base):
            raise PermisoDenegado(f"el usuario {u['nombre']} no tiene acceso a la base «{base}»")

    def _permitir_cuenta(self, orden):
        """Una cuenta registrada: todo dentro de sus bases, nada fuera, y sin pasarse de sus límites."""
        nombre = self.usuario['nombre']
        servidor = self.servidor
        if isinstance(orden, USUARIOS_PROPIOS):
            raise PermisoDenegado("una cuenta no gestiona usuarios: para dar acceso a una aplicación, "
                                  "crea una clave de API (crear clave api)")
        if isinstance(orden, (DropDatabase, CopyDatabase)) and not servidor.es_suya(nombre, orden.name):
            raise PermisoDenegado(f"la base «{orden.name}» no es tuya")
        if isinstance(orden, (RestoreBackup, RecoverDatabase)) and not servidor.es_suya(nombre, orden.name, o_borrada=True):
            raise PermisoDenegado(f"la base «{orden.name}» no es tuya")
        if isinstance(orden, ShowHistory) and orden.base and not servidor.es_suya(nombre, orden.base):
            raise PermisoDenegado(f"la base «{orden.base}» no es tuya")
        if isinstance(orden, NUEVA_BASE):
            nueva = orden.name if isinstance(orden, CreateDatabase) else (getattr(orden, 'new_name', None) or orden.name)
            if isinstance(orden, CreateDatabase) and orden.if_not_exists and servidor.es_suya(nombre, nueva):
                return
            servidor.validar(nueva)
            if servidor.nombre_ajeno(nombre, nueva):
                raise SBDError(f"ya existe (o ha existido) una base llamada «{nueva}»: elige otro nombre")
            servidor.comprobar_cuota(nombre, nueva_base=True)
            return
        if isinstance(orden, GESTION):
            return  # el resto (papelera, copias, historial) se filtra al responder
        base = orden.name if isinstance(orden, UseDatabase) else self.base
        if base and not self.puede_ver(base):
            raise PermisoDenegado(f"la base «{base}» no es tuya")
        if isinstance(orden, CRECE):
            servidor.comprobar_cuota(nombre)

    def _es_cuenta(self):
        return self.usuario is not None and self.usuario['permiso'] == CUENTA

    def _apuntar(self, base, borrada=False):
        """Si quien la crea (o la borra) es una cuenta, la base pasa a ser suya (o sale de su lista)."""
        if self._es_cuenta():
            self.servidor.apuntar_base(self.usuario['nombre'], base, borrada)
            self.usuario = self.servidor.usuario(self.usuario['nombre'])

    def usar(self, nombre):
        if not self.puede_ver(nombre):
            raise PermisoDenegado(f"el usuario {self.usuario['nombre']} no tiene acceso a la base «{nombre}»")
        if self.base and self.base != nombre and self.servidor.abrir(self.base).owner == self.id:
            raise SBDError(f"«{self.base}» tiene una transacción abierta: «confirmar» o «deshacer» antes de cambiar de base")
        self.servidor.abrir(nombre)
        self.base = nombre

    def ejecutar(self, texto, params=()):
        resultado = Result(message='Nada que hacer')
        for resultado in self.iterar(texto, params):
            pass
        return resultado

    def iterar(self, texto, params=()):
        params = tuple(params)
        parser = Parser(texto)
        ordenes = parser.parse_script_spans()
        if parser.param_count != len(params):
            raise SBDError(f"la orden tiene {parser.param_count} parámetro(s) «?» "
                           f"pero se pasaron {len(params)} valor(es)")
        for orden, fuente in ordenes:
            yield self.ejecutar_orden(orden, params, fuente)

    def ejecutar_orden(self, orden, params=(), texto=None):
        """Ejecuta una orden ya analizada; si cambia algo, la apunta en la auditoría."""
        db = self.servidor.abrir(self.base) if self._es_cuenta() and self.base else None
        limitar = not isinstance(orden, (Delete, DropTable, DropIndex, DropView))
        if isinstance(orden, Commit) and db:
            limitar = bool(db._quota_marks)
        with self.limitar(db, habilitado=limitar):
            resultado = self._ejecutar_orden(orden, params)
        if texto is not None and not isinstance(orden, SIN_AUDITAR):
            base = getattr(orden, 'name', None) if isinstance(orden, (CreateDatabase, DropDatabase)) else self.base
            self.servidor.auditar(self.etiqueta, base, texto, resultado.message)
        return resultado

    @contextmanager
    def limitar(self, db, habilitado=True):
        anterior = db.validate_write if db else None
        if db and self._es_cuenta() and habilitado:
            db.validate_write = lambda actual: self.servidor.comprobar_cuota_resultado(self.usuario['nombre'], actual)
        try:
            yield
        finally:
            if db:
                db.validate_write = anterior

    def _comprobar_nueva_copia(self, nombre):
        if not self._es_cuenta():
            return
        try:
            db = self.servidor.abrir(nombre)
            self.servidor.comprobar_cuota_resultado(self.usuario['nombre'], db, adicional=True)
        except BaseException:
            db = self.servidor._abiertas.pop(nombre, None)
            if db:
                db.close()
            for ruta in self.servidor._ficheros(nombre):
                os.remove(ruta)
            raise

    def _comprobar_papelera(self, nombre):
        if not self._es_cuenta():
            return
        copias = [e for e in self.servidor.papelera() if e[0] == nombre]
        if not copias:
            return  # recuperar dará el error habitual
        origen = os.path.join(self.servidor.papelera_dir, copias[0][3])
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, 'copia.base')
            shutil.copy2(origen, ruta)
            diario = origen[:-len(EXTENSION)] + '.diario'
            if os.path.exists(diario):
                shutil.copy2(diario, os.path.join(carpeta, 'copia.diario'))
            db = Database(ruta)
            try:
                self.servidor.comprobar_cuota_resultado(self.usuario['nombre'], db, adicional=True)
            finally:
                db.close()

    def _ejecutar_orden(self, orden, params=()):
        servidor = self.servidor
        self._permitir(orden)
        if isinstance(orden, ShowHistory):
            filas = servidor.historial(orden.base, orden.limit, set(self.usuario['bases']) if self._es_cuenta() else None)
            return Result(['cuándo', 'quién', 'base', 'orden', 'resultado'], filas)
        if isinstance(orden, CreateUser):
            servidor.crear_usuario(orden.name, orden.password, orden.permission, orden.bases)
            donde = 'todas las bases' if '*' in orden.bases else ', '.join(orden.bases)
            return Result(message=f"Usuario {orden.name} creado: puede {orden.permission} en {donde}")
        if isinstance(orden, ChangePassword):
            servidor.cambiar_clave(orden.name, orden.password)
            return Result(message=f"Clave de {orden.name} cambiada")
        if isinstance(orden, ChangePermission):
            servidor.cambiar_permiso(orden.name, orden.permission, orden.bases)
            return Result(message=f"{orden.name} ahora puede {orden.permission}")
        if isinstance(orden, DropUser):
            servidor.borrar_usuario(orden.name)
            return Result(message=f"Usuario {orden.name} borrado")
        if isinstance(orden, CreateApiKey):
            usuario = orden.user or (self.usuario['nombre'] if self.usuario else None)
            if usuario is None:
                raise SBDError("indica de quién es la clave: crear clave api para ana")
            clave, ident = servidor.crear_clave_api(usuario, orden.label)
            return Result(['clave', 'id', 'usuario'], [(clave, ident, usuario)],
                          message="Clave creada. Cópiala ahora: no se volverá a mostrar")
        if isinstance(orden, (ShowApiKeys, DropApiKey)):
            propio = None if self.usuario is None or self.usuario['permiso'] == 'todo' else self.usuario['nombre']
            if isinstance(orden, ShowApiKeys):
                return Result(['id', 'usuario', 'nombre', 'creada', 'último uso'], servidor.lista_claves(propio))
            servidor.borrar_clave_api(orden.key_id, propio)
            return Result(message=f"Clave {orden.key_id} borrada: las aplicaciones que la usaban ya no pueden entrar")
        if isinstance(orden, ShowUsers):
            return Result(['usuario', 'permiso', 'bases'],
                          [(u['nombre'], u['permiso'], ', '.join(u['bases']).replace('*', 'todas'))
                           for u in servidor.lista_usuarios()])
        if isinstance(orden, ShowDatabases):
            filas = [(b['nombre'], b['tablas'], b['bytes'], '← en uso' if b['nombre'] == self.base else '')
                     for b in servidor.resumen(self.bases_visibles())]
            return Result(['base', 'tablas', 'bytes', ''], filas)
        if isinstance(orden, CreateDatabase):
            creada = servidor.crear(orden.name, orden.if_not_exists)
            if creada:
                self._comprobar_nueva_copia(orden.name)
                self._apuntar(orden.name)
            if self.base is None or creada:
                self.usar(orden.name)
            mensaje = f"Base {orden.name} creada; ahora estás usándola" if creada else f"La base {orden.name} ya existía"
            return Result(message=mensaje)
        if isinstance(orden, UseDatabase):
            self.usar(orden.name)
            db = servidor.abrir(orden.name)
            return Result(message=f"Usando {orden.name} ({len(db.tables)} tablas)")
        if isinstance(orden, DropDatabase):
            destino = servidor.borrar(orden.name, orden.if_exists)
            if destino is None:
                return Result(message=f"La base {orden.name} no existía; nada que borrar")
            if self.base == orden.name:
                self.base = None
            self._apuntar(orden.name, borrada=True)
            return Result(message=f"Base {orden.name} borrada (copia en {os.path.relpath(destino, servidor.directorio)})")
        if isinstance(orden, Import) and not self.permite_ficheros:
            raise PermisoDenegado("por la red no se leen ficheros del servidor: en el panel, usa «importar» "
                                  "del fichero de la izquierda; desde una aplicación, sube los datos con insertar")
        if isinstance(orden, Import) and self.directorio and not os.path.isabs(orden.path):
            orden = Import(os.path.join(self.directorio, orden.path), orden.table)
        if isinstance(orden, CopyDatabase):
            servidor.copiar(orden.name, orden.new_name)
            self._comprobar_nueva_copia(orden.new_name)
            self._apuntar(orden.new_name)
            return Result(message=f"Base {orden.name} copiada como {orden.new_name}")
        if isinstance(orden, ShowBackups):
            copias = servidor.copias()
            if self._es_cuenta():
                copias = [c for c in copias if servidor.es_suya(self.usuario['nombre'], c[1], o_borrada=True)]
            return Result(['día', 'base', 'bytes'], copias)
        if isinstance(orden, RestoreBackup):
            servidor.restaurar(orden.name, orden.day, orden.new_name)
            self._comprobar_nueva_copia(orden.new_name or orden.name)
            self._apuntar(orden.new_name or orden.name)
            return Result(message=f"Base {orden.new_name or orden.name} restaurada desde la copia del {orden.day}")
        if isinstance(orden, ShowTrash):
            filas = [(n, cuando, b) for n, cuando, b, _ in servidor.papelera()
                     if not self._es_cuenta() or servidor.es_suya(self.usuario['nombre'], n, o_borrada=True)]
            return Result(['base', 'borrada', 'bytes'], filas)
        if isinstance(orden, RecoverDatabase):
            self._comprobar_papelera(orden.name)
            cuando = servidor.recuperar(orden.name, orden.new_name)
            nombre = orden.new_name or orden.name
            self._apuntar(nombre)
            return Result(message=f"Base {nombre} recuperada (la copia del {cuando})")
        return self.db.run_statement(orden, params, self.id, self.etiqueta)

    def en_transaccion(self):
        if self.base is None or not self.servidor.existe(self.base):
            return False
        db = self.servidor.abrir(self.base)
        return db.in_transaction and db.owner == self.id

    def catalogo(self):
        """Lo que la consola necesita para completar con Tab."""
        db = self.servidor.abrir(self.base) if self.base else None
        return {'bases': self.bases_visibles(),
                'tablas': {n: t.colnames for n, t in db.tables.items()} if db else {},
                'vistas': list(db.views) if db else []}

    def cerrar(self):
        """Fin de la sesión: deshace la transacción que dejara abierta."""
        for db in list(self.servidor._abiertas.values()):
            db.end_session(self.id)
