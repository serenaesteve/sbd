## a) Se han identificado las ventajas e inconvenientes de las bases de datos que almacenan objetos

Para este RA he creado el **Baúl**, una base de datos orientada a objetos, en la carpeta `objetos/`. Guarda objetos de Python tal cual y los puede guardar en dos sitios: un fichero **YAML** o **SBD**, mi base de datos, abriendo su fichero o por la API de su servidor. Como ejemplo he usado la misma biblioteca que en el RA3, así se puede comparar el mismo problema resuelto con tablas y con objetos.

| | Relacional con ORM (RA3) | Orientada a objetos (Baúl) |
|---|---|---|
| Estructura | Clases y, además, tablas que hay que mapear | Solo las clases |
| Identidad | Una clave primaria, que es un dato más | Un OID que el objeto conserva siempre |
| Relaciones | Claves ajenas, `JOIN` y tablas intermedias (libro-género) | Referencias directas, como `libro.autor`, y conjuntos dentro del objeto |
| Objetos anidados | Otra tabla o columnas sueltas | Dentro del objeto: `socio.direccion` |
| Herencia | Difícil de mapear | Natural: una consulta de `Publicacion` devuelve libros y revistas |
| Consultas | SQL, potente y estándar | Navegar por el grafo, o consultas por ejemplo y con funciones de Python |

**Ventajas que he comprobado:**
- **Nada que traducir:** el modelo es el mismo en el programa y en la base. En el RA3 necesitaba un mapeo y un conector; aquí guardo el objeto y vuelve igual.
- **Identidad:** si dos libros tienen el mismo autor, al volver a abrir la base el autor sigue siendo un único objeto, y los ciclos (`autor.obras` ⇄ `libro.autor`) se mantienen.
- **Estructuras complejas sin esfuerzo:** listas, diccionarios, conjuntos y objetos dentro de objetos, sin crear tablas.
- **Cambiar el modelo es fácil:** añadir un atributo no exige migrar la base.

**Inconvenientes:**
- **Sin un lenguaje de consultas estándar:** no hay nada como SQL, y cada base de objetos tiene el suyo. Hubo un intento, el OQL del grupo ODMG, pero casi nadie lo adoptó. Las consultas grandes, con agrupaciones o informes, son mucho más cómodas en una base relacional.
- **Atado al lenguaje:** los objetos dependen de las clases de Python. Otro programa en otro lenguaje no los entiende sin conocerlas.
- **Cambiar las clases tiene consecuencias:** como cada objeto se guarda con el nombre de su clase, renombrarla deja de cargar los objetos guardados.
- **Rendimiento:** si para abrir la base hay que leer todos los objetos, con muchos se hace lento.
- **Menos herramientas:** hay pocos sistemas maduros, y menos programas de informes, copias o administración.

**Lo que he resuelto en mi Baúl:**
- **Rendimiento, con carga perezosa.** Al abrir solo leo qué objetos hay y de qué clase. Cada uno es un «fantasma» con su OID, y sus datos se leen la primera vez que se usa. Las consultas cargan de una vez todos los que necesitan, con pocas órdenes a SBD en vez de una por objeto. Con 5000 objetos en un fichero de SBD (por el servidor sale casi igual):

  | | Antes (lo cargaba todo) | Ahora |
  |---|---|---|
  | Abrir la base | 2,2 s | unos 60 ms |
  | Refrescar si nadie ha cambiado nada | 2,1 s | 1–2 ms |
  | Cargar los 5000 | (al abrir) | 0,3–0,4 s |

  «Ahora» sale de `python3 objetos/medir.py`, y «antes», del mismo script con la versión anterior del Baúl (commit `550ad71`). Cargarlos todos es más rápido que antes porque SBD los trae por tramos de OID con su índice y porque el YAML se lee con libyaml, la parte de PyYAML escrita en C.

- **Clases renombradas.** Una clase declara cómo se llamaba antes (`class Lector(baul.Persistente, antes='Socio')`) y lo guardado se sigue cargando. `bd.migrar()` lo reescribe con el nombre nuevo.
- **Herramientas, integrándolo con SBD.** Los objetos se guardan como filas de una base de SBD, así que tienen sus transacciones, sus copias diarias, su servidor y su panel.

**Comparado con bases de objetos que existen:**

| | Baúl | ZODB | db4o | ObjectDB |
|---|---|---|---|---|
| Lenguaje | Python | Python | Java y .NET | Java |
| Cómo se guarda un objeto | Heredando de `Persistente` | Heredando de `Persistent` | Cualquier objeto | Con las anotaciones de JPA o JDO |
| Consultas | Por ejemplo y nativas (funciones de Python) | Navegando, y con índices hechos a mano (BTrees) | Por ejemplo, nativas y SODA | JPQL y JDOQL, parecidos a SQL |
| Carga | Perezosa, con «fantasmas» | Perezosa, con «fantasmas» | Por profundidad de activación | Perezosa |
| Dónde guarda | YAML o SBD | Un fichero, un servidor (ZEO) o una base relacional (RelStorage) | Un fichero o un servidor | Un fichero o un servidor |
| Hoy | Mi proyecto | Se sigue usando, por ejemplo en Plone | Abandonada | Comercial |

El Baúl se parece:
- a ZODB, en los fantasmas y la persistencia por alcance;
- a db4o, en las consultas por ejemplo y nativas;
- a RelStorage, en guardar los objetos dentro de una base relacional, aunque yo uso la mía.

La diferencia con ObjectDB es que sigue los estándares de Java (JPA), así que el mismo programa puede cambiar a una base relacional. Con mi Baúl, o con ZODB, no se puede.

## b) Se han establecido y cerrado conexiones

`baul.abrir()` abre la conexión, y la URL decide dónde están los objetos:

```python
bd = baul.abrir('biblioteca.yaml')                                   # un fichero YAML
bd = baul.abrir('sbd:///datos/biblioteca.base')                       # SBD, abriendo su fichero
bd = baul.abrir('sbd+https://:CLAVE@sbd.serenaesteve.com/objetos')    # SBD, por su servidor
...
bd.cerrar()
```

Lo normal es usar `with`, que cierra la conexión también si hay un error:

```python
with baul.abrir('sbd:///datos/biblioteca.base') as bd:
    ...
```

- **Qué hace al abrir:** lee qué objetos hay, sin sus datos, y, en un fichero, pone un cerrojo, de modo que otro programa no puede abrirlo a la vez. Por el servidor abre una sesión de SBD, con su propia transacción.
- **Qué hace al cerrar:** suelta el cerrojo o cierra la sesión. Lo que no se haya confirmado se pierde.
- **Después de cerrar,** cualquier operación da un error claro, también usar un objeto que todavía no se había cargado. Cerrar dos veces no falla.

## c) Se ha gestionado la persistencia de objetos simples

Una clase se hace persistente heredando de `baul.Persistente`. No necesita nada más:

```python
class Autor(baul.Persistente):
    def __init__(self, nombre, pais=None):
        self.nombre = nombre
        self.pais = pais
        self.obras = []
```

```python
with baul.abrir(url) as bd:
    autor = Autor('Jorge Luis Borges', 'Argentina')
    bd.guardar(autor)       # todavía no tiene identidad: autor.oid es None
    bd.confirmar()          # ahora sí: autor.oid == 1

with baul.abrir(url) as bd:
    autor = bd.obtener(1)   # vuelve como Autor, con sus datos
```

Guarda textos, números, `Decimal` exactos, fechas, `True`, `False` y `None`, cada uno con su tipo. Las pruebas comprueban también los casos difíciles: un texto que parece una fecha (`'2024-01-01'`) o un número (`'007'`) vuelve como texto, no como fecha o número. Los atributos que empiezan por `_` no se guardan: sirven para datos de trabajo.

En YAML, el autor queda así:

```yaml
- oid: 1
  clase: Autor
  version: 1
  datos: |
    nombre: Jorge Luis Borges
    pais: Argentina
    obras: []
```

## d) Se ha gestionado la persistencia de objetos estructurados

El modelo de la biblioteca (`objetos/biblioteca/modelos.py`) tiene todo tipo de estructuras:

```
Publicacion ─┬─ Libro        herencia
             └─ Revista
Autor ⇄ Libro               referencias en los dos sentidos (un ciclo)
Socio ── Direccion          un objeto de valor, guardado dentro del socio
Socio ⇄ Prestamo ⇄ Libro    cada préstamo apunta al libro y al socio, y los dos lo tienen en su lista
```

- **Referencias:** un objeto persistente se guarda aparte, y los demás lo apuntan con su OID (`!ref 3`). Al cargar, el Baúl crea primero todos los objetos vacíos y luego los rellena; así las referencias, también las circulares, apuntan siempre al mismo objeto.
- **Objetos de valor:** `Direccion` hereda de `baul.Valor`, que no tiene identidad y se guarda dentro del socio (`!Direccion {calle: …}`).
- **Colecciones:** listas, tuplas, conjuntos y diccionarios, anidados como se quiera.
- **Herencia:** cada objeto se guarda con su clase, y vuelve como `Libro` o `Revista`, con sus métodos.
- **Persistencia por alcance:** no hace falta guardar cada objeto. Al confirmar se guarda todo lo que se puede alcanzar desde una raíz (`bd.raiz['biblioteca']`) o desde un objeto ya guardado. Al dar de alta un libro, se guarda también su autor si es nuevo.

Así queda un préstamo en SBD, en la columna `datos` de su fila:

```yaml
socio: !ref '13'
publicacion: !ref '7'
fecha: 2026-10-04
vence: 2026-10-19
devuelto: null
multa: !decimal '0.00'
```

## e) Se han desarrollado aplicaciones que realizan consultas

La aplicación es `objetos/main.py`, y sus consultas están en `objetos/biblioteca/servicio.py`. Hay tres formas de consultar:

```python
# Por ejemplo: el «__» baja por los atributos, también dentro de objetos de valor y diccionarios
bd.buscar(Libro, autor__nombre='Jorge Luis Borges')
bd.buscar(Socio, direccion__ciudad='Barcelona')
bd.buscar(Publicacion, precio__menor=Decimal('10'), generos__contiene='Novela')

# Nativa: la condición es una función de Python, con orden y límite
bd.consultar(Publicacion, lambda p: p.precio < 15, ordenar='precio', desc=True, limite=2)

# Navegando por el grafo, sin consultar nada
[p.publicacion.titulo for p in socio.prestamos]
```

- **Son polimórficas:** `bd.todos(Publicacion)` devuelve libros y revistas.
- **No se rompen:** si un objeto no tiene el atributo que se pregunta (una revista no tiene `autor`), sencillamente no cumple la condición.
- **Cargan de una vez:** `bd.buscar(Libro, autor__nombre__contiene='borges')` manda dos órdenes a SBD, una para los libros y otra para sus autores. Si cargara uno a uno, serían una por objeto.
- **Quién usa un objeto:** `bd.quien_usa(borges)` devuelve los objetos que apuntan a él. No hace falta cargarlos todos: SBD busca su OID en los datos guardados.

```bash
python3 objetos/main.py fondo borges
python3 objetos/main.py vencidos
python3 objetos/main.py informe
```

**También está en la web,** en https://sbd.serenaesteve.com/objetos/, junto al panel de SBD y la biblioteca del RA3:
- Se recorren los objetos pulsando sus referencias, de un libro a su autor y de ahí a sus otras obras.
- Tiene consultas por ejemplo, como «libros cuyo autor contiene borges» o «socios de Madrid».
- De cada objeto enseña quién lo usa, la fila de SBD donde está guardado y las órdenes que ha recibido SBD para enseñarlo.

Mirar es público, pero el correo y la calle de los socios salen tapados. Prestar y devolver solo se puede habiendo entrado en el panel con permiso «todo».

**En SBD,** los mismos objetos también se consultan con su lenguaje:

```
buscar objetos agrupar clase mostrar clase, contar()
buscar objetos donde clase = "Socio" y datos contiene "Madrid"
```

## f) Se han modificado los objetos almacenados

Para modificar, basta con cambiar el objeto. El Baúl detecta qué ha cambiado comparando cada objeto con lo que tiene guardado, también si el cambio está dentro de una lista o de un objeto de valor:

```python
libro.precio = Decimal('13.00')
socio.direccion.ciudad = 'Alcalá de Henares'
bd.cambios()      # 2
bd.confirmar()    # guarda solo esos dos, y les sube la versión
```

Solo se guarda lo que se ha cargado: un objeto que nadie ha usado no puede haber cambiado. Y cambiar un objeto sin cargar primero carga sus datos, para que no se pierdan los atributos que no se han tocado.

Borrar respeta las referencias: si otro objeto o una raíz sigue apuntando al que se borra, `confirmar()` no lo deja y dice quién lo usa, buscándolo como `quien_usa`. Por eso dar de baja a un socio (`Servicio.baja_socio`) primero lo quita de las listas y borra sus préstamos.

**Si cambia una clase:** al renombrarla se declara su nombre de antes, y `bd.migrar()` reescribe los objetos guardados con el nombre nuevo:

```python
class Lector(baul.Persistente, antes='Socio'):
    ...
```

## g) Se han gestionado las transacciones

**Todo o nada.** `confirmar()` guarda todos los cambios de una vez o ninguno:
- **En SBD** es una transacción: `empezar … confirmar`, o `deshacer` si algo falla.
- **En YAML** el fichero se escribe entero en un temporal y luego sustituye al bueno. Si falla a medias, el fichero anterior queda intacto, y hay una prueba que lo comprueba.

```python
with bd.transaccion():
    for publicacion in publicaciones:
        if publicacion.disponibles == 0:
            raise ErrorBiblioteca(f"no quedan ejemplares de «{publicacion.titulo}»")
        publicacion.disponibles -= 1
        Prestamo(socio, publicacion, hoy)
```

Si al prestar «Nada» y «Sur» a la vez ya no queda «Sur», no se presta ninguno.

**Deshacer devuelve los mismos objetos a como estaban.** `deshacer()` no solo descarta lo pendiente: vuelve a poner en cada objeto sus datos guardados. Quien tuviera una referencia al objeto la sigue teniendo, con sus datos de antes.

**Dos programas a la vez.** Por el servidor de SBD, dos programas pueden usar los mismos objetos. Cada fila lleva su versión, y se guarda con:

```
actualizar objetos poner datos = ?, version = ? donde oid = ? y version = ?
```

Si otro programa cambió el objeto antes, no se actualiza ninguna fila: el Baúl deshace toda la transacción y lanza `Conflicto`, en vez de pisar el cambio ajeno. Con `bd.refrescar()` se cargan los datos nuevos para volver a intentarlo.

- **Saber si hay algo nuevo:** cada confirmación suma uno a un contador, en su misma transacción (la tabla `baul`). `bd.refrescar()` lo mira primero y, si nadie ha guardado nada, no relee la base. La web lo hace en cada visita.
- **Un límite de la carga perezosa:** un objeto que todavía no se ha cargado se lee como esté al usarlo, que puede ser más nuevo que otros ya cargados. Las versiones siguen impidiendo pisar un cambio ajeno, pero no hay una foto fija de toda la base. ZODB la tiene (MVCC); mi Baúl no.

## h) Se han probado y documentado las aplicaciones desarrolladas

**Pruebas:**
- **131 pruebas automáticas** (`objetos/tests/test_baul.py`). Casi todas se ejecutan tres veces, una por almacén: YAML, SBD abriendo el fichero y SBD por un servidor de verdad. Cubren:
  - abrir y cerrar;
  - tipos simples y estructurados;
  - referencias compartidas y ciclos;
  - herencia;
  - persistencia por alcance;
  - consultas;
  - detección de cambios;
  - borrado con integridad;
  - transacciones y conflictos entre dos programas;
  - escritura atómica del YAML;
  - copiar entre almacenes;
  - la aplicación de consola;
  - la carga perezosa: qué se carga y cuántas órdenes recibe SBD;
  - refrescar sin releer;
  - las clases renombradas;
  - la web, incluido que sin permiso no deja prestar y que no enseña los datos privados.
- **Las pruebas fallan cuando deben:** introduje errores a propósito en el Baúl y las pruebas los detectaron. Por ejemplo:
  - que `deshacer` no restaurase los objetos, o que no comprobara las versiones;
  - que cambiar un objeto sin cargar perdiera sus otros datos;
  - que refrescar no mirase el contador;
  - que se olvidara la clase nueva al migrar.
- **`objetos/pruebas_ra4.py`** recorre cada criterio de este documento y enseña el resultado.
- **`objetos/medir.py`** mide cuánto tarda con 5000 objetos, en un fichero de SBD y por el servidor.

```bash
python3 objetos/pruebas_ra4.py
python3 objetos/medir.py
cd objetos && python3 -m unittest discover -s tests
```

**Documentación:**
- `objetos/README.md` explica cómo instalarlo y usarlo, con todas las operaciones y sus límites.
- Cada módulo empieza explicando qué hace. `formato.py`, por ejemplo, describe cómo se escribe cada tipo en YAML.

## Uso de inteligencia artificial

He desarrollado este proyecto con ayuda de IA (Claude, de Anthropic). Yo he decidido qué construir, y la IA ha escrito el código y la primera versión de este documento. Las pruebas se pueden repetir con `python3 objetos/pruebas_ra4.py`.
