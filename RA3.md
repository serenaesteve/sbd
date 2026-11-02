## a) Se ha instalado la herramienta ORM

He elegido SQLAlchemy porque es el ORM de referencia en Python, como Hibernate en Java. Se instala con `pip` dentro de un entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install . ./orm
```

Eso instala SBD, SQLAlchemy y mi conector. SBD sigue sin depender de nada: SQLAlchemy solo lo pide `orm/`. `orm/pyproject.toml` fija la versión y registra el dialecto, de modo que SQLAlchemy lo encuentra por sí solo:

```toml
dependencies = ["SQLAlchemy>=2.0,<2.1"]

[project.entry-points."sqlalchemy.dialects"]
sbd = "sqlalchemy_sbd.dialecto:DialectoSBD"
```

## b) Se ha configurado la herramienta ORM

La configuración está en `orm/biblioteca/bd.py`. Una URL indica qué motor usar y dónde están los datos:

**Dos formas de conectarse, con el mismo código en los modelos:**

```python
create_engine('sbd:///datos/biblioteca.base')                       # abre el fichero de la base
create_engine('sbd+https://:CLAVE@sbd.miweb.es/biblioteca')         # por la API de un servidor de SBD
```

```python
URL_POR_DEFECTO = f"sbd:///{CARPETA_DATOS / 'biblioteca.base'}"

def crear_motor(url=None, ver_sql=False):
    url = url or os.environ.get('BIBLIOTECA_URL') or URL_POR_DEFECTO
    return create_engine(url, echo=ver_sql)

def fabrica_sesiones(motor):
    return sessionmaker(motor, expire_on_commit=False)
```

- **La base se puede cambiar sin tocar el código,** con la variable `BIBLIOTECA_URL`.
- **`echo`** enseña el SQL que genera el ORM: `python3 orm/main.py --ver-sql libros`.
- **El conector tiene tres piezas,** porque SQLAlchemy no conoce SBD:
  - `dialecto.py` le dice a SQLAlchemy cómo hablar con SBD: tipos de datos, cómo inspeccionar las tablas y qué funciones admite.
  - `dbapi.py` es el controlador con la interfaz estándar de Python (PEP 249): conexiones, cursores, `commit` y `rollback`.
  - `sql.py` traduce cada orden de SQL a las órdenes de SBD: a su árbol interno si abre el fichero, y a texto en el lenguaje de SBD si va por el servidor. En ese caso recoloca los parámetros, porque en SQL las columnas van antes que la tabla y en SBD, después.

## c) Se han definido configuraciones de mapeo

El mapeo está en `orm/biblioteca/modelos.py`, con clases: cada clase es una tabla y cada atributo, una columna.

```python
class Libro(Base):
    __tablename__ = 'libros'
    __table_args__ = (
        CheckConstraint('ejemplares >= 0', name='ck_libros_ejemplares'),
        CheckConstraint('disponibles >= 0 AND disponibles <= ejemplares', name='ck_libros_disponibles'),
        CheckConstraint('precio >= 0', name='ck_libros_precio'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    isbn: Mapped[str] = mapped_column(String(20), unique=True)
    titulo: Mapped[str] = mapped_column(String(160), index=True)
    anio: Mapped[Optional[int]]
    precio: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal('0'))
    autor_id: Mapped[int] = mapped_column(ForeignKey('autores.id'), index=True)

    autor: Mapped[Autor] = relationship(back_populates='libros')
    generos: Mapped[list[Genero]] = relationship(secondary=libro_genero, back_populates='libros',
                                                 order_by='Genero.nombre')
```

**Qué mapeos he usado:**

| Mapeo | Ejemplo |
|---|---|
| Clave primaria, que se numera sola | `Libro.id` |
| Clave compuesta | `libro_genero (libro_id, genero_id)` |
| Relación 1 a N | `Autor.libros` y `Libro.autor` |
| Relación N a M, con tabla intermedia | `Libro.generos` y `Genero.libros` |
| Borrado en cascada | `cascade='all, delete-orphan'`, `ondelete='CASCADE'` |
| Valores obligatorios y opcionales | `Mapped[str]` y `Mapped[Optional[int]]` |
| Únicos, índices y comprobaciones | `unique=True`, `index=True`, `CheckConstraint` |
| Valores por defecto | `default=datetime.date.today` |
| Versión para detectar conflictos | `version_id_col` en `Libro` |

**Tipos de Python y de SBD:**

| Python | SQLAlchemy | SBD |
|---|---|---|
| `int` | `Integer` | `entero` |
| `str` | `String(n)` | `texto(n)` |
| `Decimal` | `Numeric(8, 2)` | `dinero` |
| `bool` | `Boolean` | `logico` |
| `datetime.date` | `Date` | `fecha` |

Con `Base.metadata.create_all(motor)`, el ORM crea las tablas a partir de las clases. SBD las guarda en su propio idioma:

```
crear tabla libros {
  id          entero clave
  isbn        texto(20) obligatorio unico
  titulo      texto(160) obligatorio
  precio      dinero obligatorio
  …
}
```

## d) Se han aplicado mecanismos de persistencia a los objetos

Trabajo con objetos normales de Python, y la **sesión** decide qué órdenes hay que lanzar. Un objeto pasa por estos estados:

```python
libro = Libro(titulo='Ficciones', isbn='111', autor=autor)   # transitorio: solo existe en Python; id = None
sesion.add(libro)                                            # pendiente: la sesión lo vigila
sesion.commit()                                              # persistente: ya está en la base; id = 1
sesion.close()                                               # separado: el objeto sigue, sin sesión
```

- **Persistencia en cascada:** al guardar el libro se guarda también su autor nuevo, sin añadirlo aparte.
- **Detección de cambios:** basta con cambiar un atributo; la sesión genera el `UPDATE` solo con lo que ha cambiado.
- **Mapa de identidad:** en una sesión, cada fila es un único objeto.
- **Lo guardado sobrevive:** las pruebas cierran el programa, abren la base de nuevo y siguen encontrando los datos.

## e) Se han desarrollado aplicaciones que modifican y recuperan objetos persistentes

La aplicación es `orm/main.py`, que se usa desde la línea de órdenes, y su lógica está en `orm/biblioteca/servicio.py`:

```python
# Recuperar por clave
libro = self.sesion.get(Libro, libro_id)

# Recuperar con una consulta del ORM, cargando sus relaciones
consulta = select(Libro).options(selectinload(Libro.autor), selectinload(Libro.generos)).order_by(Libro.titulo)
consulta = consulta.join(Libro.autor).where(or_(Libro.titulo.like(patron), Autor.nombre.like(patron)))
libros = list(self.sesion.scalars(consulta))

# Modificar: basta con cambiar el objeto
libro.precio = precio

# Borrar: sus préstamos se borran en cascada
self.sesion.delete(socio)
```

```bash
python3 orm/main.py iniciar
python3 orm/main.py libros borges
python3 orm/main.py prestar 4 2 6
python3 orm/main.py devolver 1
```

También tiene una web (`orm/biblioteca/web.py`) con catálogo, préstamos, socios e informes, que se conecta al servidor de SBD y guarda sus datos en una base más de su panel. Mirar es público; prestar, devolver o dar de alta exige haber entrado en el panel con permiso «todo», y la web se lo pregunta al propio servidor de SBD. Se publica en la misma web que SBD, en `/biblioteca/`, con `orm/despliegue/publicar.sh`.

## f) Se han desarrollado aplicaciones que realizan consultas usando el lenguaje SQL

Los informes (`python3 orm/main.py informes`) están escritos en SQL y se ejecutan con `text()`, con los valores siempre como parámetros:

```python
self.sesion.execute(text("""
    SELECT s.nombre, s.email, COUNT(*) AS vencidos, MIN(p.vence) AS desde
    FROM prestamos p
    JOIN socios s ON s.id = p.socio_id
    WHERE p.devuelto IS NULL AND p.vence < :hoy
    GROUP BY s.nombre, s.email
    HAVING COUNT(*) >= 1
    ORDER BY desde, s.nombre
"""), {'hoy': hoy})
```

También hay `LEFT JOIN`, `CASE`, `NOT IN` con subconsulta y una función de ventana (`RANK() OVER (PARTITION BY …)`).

**Por qué funciona en SBD:** `orm/sqlalchemy_sbd/sql.py` analiza el SQL y lo convierte en el mismo árbol de órdenes que produce el lenguaje de SBD. Para comprobar que traduce bien, las pruebas lanzan **75 consultas y 8 modificaciones en SQLite, en SBD abriendo el fichero y en SBD por su servidor, y exigen resultados idénticos en los tres**. Para comprobar esas pruebas, introduje errores a propósito en el traductor; una función sin probar se escapó y añadí más consultas.

**Lo que no traduce:** `ALTER TABLE`, vistas y `LIKE … ESCAPE`. Dan un error que lo dice, nunca un resultado distinto.

## g) Se han gestionado las transacciones

**Todo o nada.** Prestar varios libros es una sola transacción: si uno no se puede prestar, no se presta ninguno.

```python
@contextmanager
def _transaccion(self):
    try:
        yield
        self.sesion.commit()
    except BaseException:
        self.sesion.rollback()
        raise
```

```python
with self._transaccion():
    for libro_id in ids:
        libro = self.sesion.get(Libro, libro_id)
        if libro.disponibles == 0:
            raise ErrorBiblioteca(f"no quedan ejemplares de «{libro.titulo}»")
        libro.disponibles -= 1
        self.sesion.add(Prestamo(socio=socio, libro=libro, fecha=hoy, vence=vence))
```

**Puntos de guardado.** Con `begin_nested()` se puede deshacer solo una parte de la transacción (solo abriendo el fichero; por el servidor avisa de que no está disponible):

```python
with sesion.begin_nested():
    sesion.add(Socio(nombre='Repetido', email=email_que_ya_existe))
    sesion.flush()                  # falla: solo se deshace este bloque
```

**Dos cambios a la vez.** `Libro` lleva una columna `version`. Si otra sesión cambió el libro después de leerlo, el ORM lanza `StaleDataError` en lugar de pisar su cambio (bloqueo optimista).

**La base también se defiende.** Aunque la aplicación fallara, SBD rechaza un stock negativo o un email repetido, y el ORM recibe un `IntegrityError`.

**Cómo se conecta con SBD:** como manda el estándar, la transacción empieza sola con la primera orden que cambia algo, y `commit` y `rollback` son los `confirmar` y `deshacer` de SBD. Por el servidor, cada conexión tiene su propia sesión, así que su transacción dura entre peticiones y es solo suya. La limitación es que solo puede haber una transacción abierta por base: las demás conexiones esperan hasta 5 segundos y luego reciben un error.

## Limitaciones

- **Abriendo el fichero:** solo lo puede tener abierto un programa a la vez, así que si la base es de un servidor hay que conectarse por su API.
- **Por el servidor:** no hay puntos de guardado, e inspeccionar la base solo lista las tablas y las vistas.
- **Sin migraciones:** como el traductor no cubre `ALTER TABLE`, no se puede usar Alembic.
- **SQL parcial:** cubre lo que genera SQLAlchemy y el SQL habitual de consultas, pero no todo el estándar.

## Uso de inteligencia artificial

He desarrollado este proyecto con ayuda de IA (Claude, de Anthropic). Yo he decidido qué construir, y la IA ha escrito el código y la primera versión de este documento. Las pruebas se pueden repetir con `python3 orm/pruebas_ra3.py`.
