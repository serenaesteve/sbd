# Baúl: una base de datos de objetos

El Baúl guarda **objetos de Python tal cual**, sin tablas ni mapeo, y los devuelve con su identidad, sus referencias, sus colecciones y su herencia. Puede guardarlos en dos sitios, con el mismo código:

- **Un fichero YAML** legible a mano: `biblioteca.yaml`.
- **SBD**, abriendo el fichero de una base (`sbd:///datos/objetos.base`) o por la API de un servidor (`sbd+https://:CLAVE@servidor/base`). Cada objeto es una fila con su YAML, así que se ve desde la consola y el panel de SBD.

```python
import baul

class Autor(baul.Persistente):
    def __init__(self, nombre):
        self.nombre = nombre
        self.obras = []

with baul.abrir('sbd:///datos/objetos.base') as bd:
    bd.raiz['borges'] = Autor('Jorge Luis Borges')     # se guarda todo lo que cuelga de una raíz
    bd.confirmar()
    print(bd.buscar(Autor, nombre__contiene='borges'))
```

## Instalar y usar

Desde la carpeta de SBD (la de arriba), con Python 3.11 o superior:

```bash
pip install . ./objetos                        # SBD y el Baúl (que solo necesita PyYAML)
python3 objetos/main.py iniciar                # la biblioteca de ejemplo, en SBD
python3 objetos/main.py fondo borges
python3 objetos/main.py prestar 13 9           # el socio 13 se lleva la publicación 9 (son sus OID)
python3 objetos/main.py ver 13                 # un objeto, tal como está guardado
python3 objetos/main.py exportar copia.yaml    # todo el baúl a YAML
python3 objetos/main.py --bd copia.yaml fondo  # y se usa igual desde el YAML
```

Los objetos guardados en SBD se ven con sus herramientas:

```bash
python3 -m sbd objetos/datos -b biblioteca -e 'buscar objetos agrupar clase mostrar clase, contar()'
```

## Qué guarda

| | |
|---|---|
| `baul.Persistente` | Un objeto con identidad (OID). Se guarda aparte, y los demás lo referencian: dos libros del mismo autor apuntan al mismo objeto, también al volver a abrir. |
| `baul.Valor` | Un objeto sin identidad, como una dirección, que se guarda dentro del que lo contiene. |
| Tipos básicos | Textos, números, `Decimal`, fechas, `None`, listas, tuplas, conjuntos y diccionarios, anidados como se quiera. |
| No se guarda | Los atributos que empiezan por `_`. |

**Persistencia por alcance:** al confirmar se guarda todo objeto al que se llegue desde una raíz (`bd.raiz['nombre']`) o desde un objeto ya guardado. `bd.guardar(obj)` guarda uno suelto.

**Carga perezosa:** al abrir solo se lee qué objetos hay y de qué clase. Los datos de cada uno se leen la primera vez que se usa. Las consultas cargan de una vez todos los que necesitan, y `bd.cargar(lista)` hace lo mismo con los que se le den. Con 5000 objetos, abrir tarda unos 60 ms (`python3 objetos/medir.py`).

**Clases renombradas:** `class Lector(baul.Persistente, antes='Socio')` sigue cargando lo guardado como `Socio`, y `bd.migrar()` lo reescribe con el nombre nuevo. Vale igual para `baul.Valor`.

## Las operaciones

| | |
|---|---|
| `baul.abrir(url)` · `bd.cerrar()` · `with` | Abrir y cerrar la conexión. |
| `bd.confirmar()` | Guarda todos los cambios: o todos o ninguno. |
| `bd.deshacer()` | Devuelve los mismos objetos a como estaban guardados. |
| `with bd.transaccion():` | Confirma al salir, o deshace si hay una excepción. |
| `bd.cambios()` | Cuántos objetos han cambiado. Se detecta solo, también dentro de listas y valores. |
| `bd.borrar(obj)` | No deja confirmar si otro objeto lo sigue usando. |
| `bd.todos(Clase)` | Los objetos de esa clase y de sus subclases. |
| `bd.buscar(Clase, autor__nombre='Borges', precio__menor=10)` | Consulta por ejemplo. El `__` baja por los atributos; también hay `__contiene`, `__mayor`, `__menor`, `__desde`, `__hasta`, `__en` y `__distinto`. |
| `bd.consultar(Clase, lambda o: …, ordenar='precio', desc=True, limite=5)` | Consulta nativa, con una función de Python. |
| `bd.quien_usa(obj)` · `bd.referencias(obj)` | Los objetos que apuntan a obj, y a los que apunta. En SBD, quién lo usa lo busca la base. |
| `bd.cargar(lista)` · `bd.cargar()` | Carga de una vez esos objetos, o todos. |
| `bd.refrescar()` | Se pone al día con lo que hayan guardado otros programas. Si nadie ha guardado nada, no relee. |
| `bd.migrar()` | Reescribe con su nombre de ahora los objetos de clases renombradas. |
| `baul.copiar(origen, destino)` | Copia todos los objetos, con sus OID, de YAML a SBD o al revés. |

**Varios programas a la vez:** por un servidor de SBD, cada objeto lleva una versión. Si otro programa lo cambió después de cargarlo, `confirmar()` lanza `Conflicto` y no guarda nada. Un fichero YAML o de SBD solo lo puede abrir un programa a la vez.

## En la web

Está publicado en **https://sbd.serenaesteve.com/objetos/**, al lado del panel de SBD y de la biblioteca del RA3, y guarda sus objetos en la base «objetos» del mismo servidor. Enseña las clases, los objetos y sus referencias, que se pueden seguir pulsándolas. De cada objeto enseña también quién lo usa, su fila de SBD con el YAML y las órdenes que ha recibido SBD.

- **Mirar es público.** El correo y la calle de los socios salen tapados.
- **Prestar y devolver** exige haber entrado en el panel con permiso «todo». La web lo comprueba preguntando al panel con la cookie de la sesión.

```bash
SBD_CLAVE=sbd_k... PYTHONPATH=. python3 -m biblioteca.web --sbd http://127.0.0.1:8090 --base objetos   # desde objetos/
sudo bash objetos/despliegue/publicar.sh       # publicarlo: copia los datos de SBD antes de tocar nada
```

## Comprobar

```bash
python3 objetos/pruebas_ra4.py                          # cada criterio del RA4, con su resultado
python3 objetos/medir.py                                # cuánto tarda con 5000 objetos
cd objetos && python3 -m unittest discover -s tests     # 131 pruebas, en YAML, SBD y servidor de SBD, y la web
```

## Límites

- **El índice sí se lee entero al abrir:** el OID, la clase y la versión de cada objeto. Con millones de objetos también se notaría.
- **Las consultas cargan los objetos de su clase** y las evalúa Python, sin índices sobre sus atributos.
- **Sin foto fija:** un objeto que todavía no se ha cargado se lee como esté al usarlo, que puede ser más nuevo que otros ya cargados. Las versiones siguen impidiendo pisar un cambio ajeno.
- **Cambios hechos a mano:** `bd.refrescar()` sabe que hay algo nuevo por un contador que suma cada confirmación del Baúl. Si alguien cambia la tabla `objetos` desde el panel de SBD, el Baúl no se entera hasta que se vuelve a abrir.
- **El YAML se lee entero:** la carga perezosa solo ahorra crear los objetos, porque el fichero se lee de una vez.
- **Clases por nombre:** cada clase se guarda con su nombre (o con los de antes que declare), así que no puede haber dos con el mismo.
