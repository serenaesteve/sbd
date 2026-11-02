# Biblioteca con ORM sobre SBD

Una biblioteca de préstamos hecha con **SQLAlchemy**, que guarda sus datos en **SBD**, el motor de bases de datos propio con lenguaje en español. Como SBD no habla SQL, el proyecto incluye el conector que los une. Tiene aplicación de consola y una web que enseña, en cada acción, el objeto, el SQL y la orden de SBD.

```
Clases de Python (biblioteca/modelos.py)
      │  el ORM las convierte en SQL
      ▼
SQLAlchemy
      │  sqlalchemy_sbd/dialecto.py: «sbd:///datos/biblioteca.base» o «sbd+https://:CLAVE@servidor/base»
      ▼
Controlador estándar de Python (sqlalchemy_sbd/dbapi.py)
      │  sqlalchemy_sbd/sql.py traduce cada orden de SQL a las órdenes de SBD
      ▼
SBD (la carpeta de arriba, sin cambios en su lenguaje): el fichero, o un `sbd servidor` por su API
```

## Instalar

Hace falta Python 3.11 o superior. Desde la carpeta de SBD (la de arriba):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install . ./orm
```

SBD sigue sin depender de nada: SQLAlchemy solo se instala con `./orm`.

## Usar

Desde esta carpeta, `orm/`:

```bash
python3 main.py iniciar                      # crea las tablas y carga el ejemplo
python3 main.py libros                       # lista los libros
python3 main.py libros borges                # los busca por título o autor
python3 main.py prestar 4 2 6                # el socio 4 se lleva los libros 2 y 6
python3 main.py devolver 1                   # calcula la multa si llega tarde
python3 main.py informes                     # consultas escritas en SQL
python3 main.py --ver-sql libros             # enseña el SQL que genera el ORM
```

También hay `socios`, `prestamos`, `alta-socio` y `alta-libro`.

Los datos son una base normal de SBD, así que se pueden abrir con sus herramientas mientras la aplicación no esté en marcha:

```bash
python3 -m sbd datos -b biblioteca -e 'buscar libros mostrar titulo, precio'     # consola
python3 -m sbd servidor datos --puerto 8097 --abrir                              # panel web
```

## La web

```bash
python3 -m sbd servidor datos --puerto 8090 --enlace Biblioteca=/biblioteca/    # el servidor de SBD
python3 -m sbd datos -e 'crear base biblioteca'                                 # desde otra terminal
python3 -m sbd datos -e 'crear usuario biblioteca_app clave "una clave larga" puede escribir en biblioteca'
python3 -m sbd datos -e 'crear clave api para biblioteca_app'
SBD_CLAVE=sbd_… python3 -m biblioteca.web --sbd http://127.0.0.1:8090 --puerto 8097
```

La primera vez crea las tablas y carga el ejemplo. Mirar es público y el correo de los socios sale tapado; cambiar datos exige haber entrado en el panel de SBD con permiso «todo». Son datos de ejemplo: al estar a la vista de cualquiera, no pongas datos reales.

`sudo bash orm/despliegue/publicar.sh` la publica en `https://sbd.serenaesteve.com/biblioteca/`: hace una copia de los datos, actualiza SBD, instala la biblioteca y añade la ruta a Apache. Para actualizar solo la biblioteca: `sudo bash orm/despliegue/desplegar.sh`.

## Comprobar

```bash
python3 pruebas_ra3.py                       # recorre los criterios del RA3 y enseña cada resultado
python3 -m unittest discover -s tests        # pruebas del conector y de la biblioteca
```

Las pruebas lanzan las mismas órdenes en SQLite, en SBD abriendo el fichero y en SBD por su servidor, y exigen resultados idénticos.

## Usar el conector en otro proyecto

```python
import sqlalchemy_sbd                    # no hace falta si está instalado con pip
from sqlalchemy import create_engine

motor = create_engine('sbd:///datos/mi_base.base')                     # abre el fichero
motor = create_engine('sbd+https://:CLAVE@datos.miweb.es/mi_base')     # por un `sbd servidor`
```

**Lo que no cubre:**

- **Abriendo el fichero:** solo lo puede tener abierto un programa a la vez.
- **Por el servidor:** no hay puntos de guardado, e inspeccionar la base solo lista las tablas y las vistas.
- **SQL:** no hay `ALTER TABLE`, vistas ni `LIKE … ESCAPE`, y por eso tampoco migraciones con Alembic. Lo que no cubre da un error que lo dice.
- **Concurrencia:** solo una transacción abierta por base. Las demás conexiones esperan hasta 5 segundos y después reciben un error.
- **Fechas:** `DateTime` guarda hasta los segundos, sin microsegundos.
- **`text()`:** las fechas llegan como texto, porque sin el mapeo SQLAlchemy no sabe su tipo. Para crear un procedimiento de SBD, usa `exec_driver_sql`, porque `text()` tomaría sus `:nombre` como parámetros propios.
