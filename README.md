# SBD

Un motor de bases de datos con **lenguaje propio en español**, escrito desde cero en Python y sin depender de nada externo: ni de otra base de datos ni de SQL. Guarda varias bases en un directorio y se maneja desde la consola, desde un panel web o desde tus aplicaciones (Python, JavaScript y PHP) a través de su API.

```bash
pip install .                           # instala el comando «sbd» (o usa «python -m sbd» sin instalar)
sbd -f ejemplos/tienda.sbd              # crea la base de ejemplo «tienda» en ./datos
sbd                                     # consola
sbd servidor --abrir                    # panel web y API en http://127.0.0.1:8080
python -m unittest discover -s tests    # tests
```

Requiere Python 3.11 o superior y nada más. Para ponerlo en un servidor de verdad, lee [PRODUCCION.md](PRODUCCION.md).

```
sbd:tienda› buscar clientes donde ciudad = "Madrid" ordenar nombre mostrar nombre, email
nombre          │ email
────────────────┼───────────────────
Lucía Fernández │ lucia@example.com
Pablo Martín    │ nulo
(2 filas)
```

---

## El lenguaje

Cada orden empieza por su palabra clave, así que no hace falta terminarla con nada. Pueden ir varias seguidas, en una línea o en varias, y el `;` es opcional. Los comentarios empiezan por `--` o `#`.

### Valores

| Escribes | Es |
|---|---|
| `42` · `3.14` · `-7` | números |
| `"Hola"` · `'Hola'` | texto (la comilla doblada se escapa: `"dijo ""sí"""`) |
| `2024-05-01` · `2024-05-01 10:30` | fechas y momentos, sin comillas |
| `verdadero` · `falso` · `nulo` | lógicos y «sin valor» |
| `` `nombre raro` `` | un nombre con espacios o que coincide con una palabra del lenguaje |
| `?` | parámetro (desde Python) |

### Bases

```
bases                       -- lista las bases del directorio
crear base tienda           -- la crea y pasa a usarla
usar tienda
borrar base tienda          -- la mueve a datos/.papelera
papelera                    -- lo que hay en la papelera
recuperar base tienda       -- la devuelve (o: recuperar base tienda como tienda_vieja)

copiar base tienda a tienda_pruebas          -- una copia exacta, para probar sin miedo
copias                                       -- las copias de seguridad diarias que guarda el servidor
restaurar base tienda del 2026-10-27 como tienda_27     -- la de ese día, aparte; sin «como», si ya no existe
historial                                    -- quién cambió qué y cuándo (o: historial de tienda limite 100)
```

El servidor hace una copia de cada base al día y guarda las de los últimos 7 días (`--copias`). Se hace al arrancar y luego cada día, al pasar la medianoche, y lleva también los usuarios y las claves de API. `historial` lee el registro de auditoría: cada orden que cambia algo queda anotada con quién la hizo, y las claves nunca aparecen.

### Tablas

```
crear tabla clientes {
  id      entero clave                  -- se numera sola
  nombre  texto(60) obligatorio
  email   texto unico
  ciudad  texto = "Madrid"              -- valor por defecto
  saldo   decimal = 0 comprobar saldo >= 0
  alta    fecha = hoy()
}

crear tabla pedidos {
  id          entero clave
  cliente_id  entero obligatorio -> clientes(id) al borrar cascada
  total       decimal
  comprobar total >= 0
}
```

- **Tipos:** `entero`, `decimal`, `dinero`, `texto`, `texto(n)` (como mucho n caracteres), `logico`, `fecha` y `fechahora`.
- **`dinero`** guarda importes exactos con dos decimales y redondea como en un ticket (2,345 → 2,35). `0.1 + 0.2` da `0.30` y no `0.30000000000000004`, así que úsalo para precios, saldos y totales. A las aplicaciones les llega como texto exacto (`"12.50"`); el cliente de Python lo convierte en `Decimal`.
- **Marcas de columna:** `clave`, `obligatorio`, `unico`, `= valor por defecto`, `comprobar condición` y `-> tabla(columna)`.
- **Al borrar** la fila a la que se apunta: `al borrar impedir` (lo que pasa si no pones nada), `al borrar cascada` o `al borrar anular`.
- **Restricciones de tabla:** `clave (a, b)`, `unico (a, b)`, `comprobar condición` y `(a, b) -> otra(x, y)`.

```
crear tabla ricos = buscar clientes donde saldo > 1000     -- tabla a partir de una consulta
crear vista madrileños = buscar clientes donde ciudad = "Madrid"
añadir a clientes telefono texto
quitar de clientes telefono
renombrar tabla clientes a socios       -- las vistas que la usan se actualizan solas
renombrar columna socios.alta a desde
indexar clientes (email)            -- o: indexar unico clientes (email) como idx_email
borrar indice clientes_email_idx
borrar tabla pedidos   ·   borrar vista madrileños
tablas   ·   describir clientes   ·   indices de clientes
```

### Datos

```
insertar en clientes { nombre: "Ana", email: "ana@ejemplo.es" }
insertar en clientes { nombre: "Luis" } { nombre: "Sara", ciudad: "Bilbao" }
insertar en clientes (nombre, ciudad) ("Marta", "Valencia") ("Pablo", "Madrid")
insertar en archivo desde buscar clientes donde alta < 2020-01-01

actualizar clientes poner ciudad = "Toledo", saldo = saldo + 10 donde id = 2
actualizar productos poner precio = precio * 1.1 todas

borrar de clientes donde id = 2
vaciar clientes
```

Lo que no pongas al insertar toma su valor por defecto (también puedes escribir `defecto`). Por seguridad, `actualizar` sin `donde` exige `todas`, y para borrar todas las filas está `vaciar`: no se puede borrar una tabla entera por despiste.

```
insertar o actualizar en clientes { email: "ana@ejemplo.es", nombre: "Ana B." }   -- si ya existe, la cambia
insertar en pedidos { cliente_id: 3 } devolver id, fecha                          -- te devuelve lo creado
actualizar productos poner precio = precio * 1.1 donde stock = 0 devolver nombre, precio
borrar de pedidos donde estado = "cancelado" devolver *
```

`insertar o actualizar` decide si la fila existe por su clave o por cualquier columna única.

**¿Va rápido?** `explicar` cuenta cómo se va a ejecutar una consulta, sin ejecutarla:

```
sbd:tienda› explicar buscar pedidos donde fecha > hace 30 dias
paso   │ cómo
───────┼──────────────────────────────────────────────────────────────────
origen │ pedidos: usa el índice pedidos_fecha_idx (rango en fecha) → 412 de 100000 filas
donde  │ se comprueba la condición en cada fila candidata
```

Los índices sirven para `=` y para rangos (`>`, `<`, `entre`); los `unir` por igualdad no los necesitan. Si dice «se recorren sus … filas» en una tabla grande, prueba con `indexar tabla (columna)`.

### Procedimientos

Órdenes guardadas en la base, con nombre y parámetros, que se ejecutan juntas:

```
crear procedimiento transferir(origen, destino, cantidad) {
  actualizar cuentas poner saldo = saldo - :cantidad donde id = :origen
  actualizar cuentas poner saldo = saldo + :cantidad donde id = :destino
  buscar cuentas donde id en [:origen, :destino]
}

ejecutar transferir(1, 2, 30)       -- también: ejecutar transferir(?, ?, ?) desde un programa
procedimientos                      -- los que hay, con su definición
borrar procedimiento transferir
```

- **Todo o nada:** si una orden del cuerpo falla, se deshace la llamada entera. Dentro de `empezar … confirmar`, solo se deshace esa llamada y la transacción sigue abierta.
- **Parámetros con `:`:** `:saldo` es el parámetro y `saldo` la columna, aunque se llamen igual.
- **Qué devuelve:** las filas de la última consulta del cuerpo o, si no hay ninguna, un resumen con las filas afectadas.
- **Qué puede llevar dentro:** `buscar`, `insertar`, `actualizar`, `borrar`, `vaciar` y `ejecutar` (otro procedimiento, hasta 16 niveles). No admite `empezar` ni `confirmar`, cambios de estructura, `importar` ni órdenes de servidor. Tampoco hay `si` ni bucles: para decidir según los datos, usa `comprobar` en las tablas o la lógica de tu programa.
- **Permisos:** se ejecutan con los permisos de quien llama. Quien solo puede leer ve la lista, pero no los ejecuta. Los límites de una cuenta también se aplican.
- **Si cambia la estructura:** al renombrar una tabla, sus procedimientos se actualizan solos; al renombrar una columna, SBD avisa de cuáles revisar. Las tablas se buscan al ejecutar, así que un nombre mal escrito da error en ese momento, no al crearlo.

### Importar CSV y Excel

```
importar "clientes.csv" en clientes
importar "ventas 2024.xlsx" en ventas
```

Si la tabla no existe, se crea con las columnas del fichero, adivinando el tipo de cada una. Si existe, las columnas se emparejan por nombre. Entiende el separador `;`, decimales con coma (`1.234,50`), fechas `dd/mm/aaaa` y CSV guardados por Excel en Windows. Los códigos con ceros delante (`00123`) se quedan como texto. Es todo o nada: si una fila no cabe, no entra ninguna. En el panel se importa subiendo el fichero.

### Buscar

```
buscar clientes
buscar clientes donde ciudad = "Madrid" ordenar nombre limite 10 mostrar nombre, email

buscar pedidos como p {
  unir clientes como c en c.id = p.cliente_id
  donde p.total > 50
  agrupar c.nombre
  teniendo contar() > 1
  mostrar c.nombre como cliente, contar() como pedidos, sumar(p.total) como gasto
  ordenar gasto desc
}
```

Las cláusulas van **en cualquier orden**, en una línea o entre llaves:

| Cláusula | Para qué |
|---|---|
| `donde condición` | filtrar filas |
| `mostrar a, b como x` · `mostrar distintos a` | qué columnas salen (si no pones nada, todas) |
| `ordenar a desc, b` · `… nulos ultimo` | orden |
| `limite 10` · `saltar 20` | paginar |
| `agrupar a, b` · `teniendo condición` | resumir por grupos y filtrar los grupos |
| `unir t en …` · `unir opcional t en …` · `unir completo` · `unir inverso` · `unir t usando col` · `cruzar t` | combinar tablas |

```
calcular 2 + 2, hoy(), mayusculas("sbd")                 -- sin tabla
buscar a mostrar x union buscar b mostrar x              -- también: union todo, comun, excepto
buscar (buscar pedidos agrupar cliente_id mostrar cliente_id, contar() como n) como t donde t.n > 2

con ricos = (buscar clientes donde saldo > 1000)
buscar ricos mostrar contar()

con n(x) = (calcular 1 union todo buscar n donde x < 10 mostrar x + 1)   -- se usa a sí misma: recursiva
buscar n
```

**Mirar las filas vecinas** con `sobre (por … ordenar …)`:

```
buscar ventas mostrar vendedor, region, total,
  puesto() sobre (por region ordenar total desc) como puesto,      -- 1, 2, 3… dentro de cada región
  acumulado(total) sobre (ordenar fecha) como acumulado,           -- suma acumulada
  sumar(total) sobre (por region) como total_region,                -- total del grupo en cada fila
  anterior(total) sobre (ordenar fecha) como el_de_antes
```

Valen `numero`, `puesto` (con huecos en los empates), `puesto_denso`, `acumulado`, `anterior(x, [n], [si no hay])`, `siguiente(…)` y los resúmenes `contar`, `sumar`, `media`, `minimo`, `maximo` y `juntar`.

### Fechas

```
buscar pedidos donde fecha > hace 30 dias
calcular hoy() + 2 semanas, dentro de 3 meses, 2024-05-01 10:00 + 90 minutos
calcular 2024-01-31 + 1 mes                       -- 2024-02-29: los meses se suman bien
calcular 2024-05-10 - 2024-05-01                  -- 9 (días)
buscar socios mostrar nombre, edad(nacimiento), dia_semana(alta), nombre_mes(alta)
```

Los intervalos se escriben como se dicen: `3 dias`, `1 semana`, `2 meses`, `1 año`, `4 horas`, `30 minutos`, `10 segundos`. Con una columna, `dias(plazo)`, `meses(n)`…

### Condiciones y expresiones

```
y   o   no                         =  !=  <  <=  >  >=
x es nulo        x no es nulo      x es verdadero
x en [1, 2, 3]   x no en (buscar …)
x entre 10 y 20
nombre contiene "an"    nombre empieza por "A"    nombre termina en "z"    nombre parece "A_a%"
existe (buscar pedidos donde cliente_id = clientes.id)
caso cuando total > 100 entonces "grande" cuando total > 10 entonces "mediano" sino "pequeño" fin
"Hola, " & nombre                  -- & une textos
entero("42")   texto(5)   decimal(x)   fecha("2024-05-01")    -- conversiones
```

### Funciones

| | |
|---|---|
| **Resumen** | `contar()` · `contar(x)` · `contar(distintos x)` · `sumar` · `media` · `minimo` · `maximo` · `juntar(x, ", ")` |
| **Texto** | `mayusculas` · `minusculas` · `longitud` · `recortar` · `extraer(t, desde, cuántos)` · `reemplazar` · `posicion` · `invertir` · `repetir` · `rellenar_izq` · `rellenar_der` · `unir_texto` |
| **Números** | `redondear(x, decimales)` · `absoluto` · `techo` · `suelo` · `raiz` · `potencia` · `resto` · `signo` · `aleatorio` · `exp` · `ln` · `log10` |
| **Nulos** | `primero_no_nulo(a, b, …)` · `si_nulo(x, otro)` · `nulo_si(a, b)` · `mayor(a, b, …)` · `menor(a, b, …)` · `tipo(x)` |
| **Fechas** | `hoy()` · `ahora()` · `hora_actual()` · `dia_de(x)` · `año` · `mes` · `dia` · `hora` · `minuto` · `segundo` · `formato_fecha(x, "%d/%m/%Y")` · `dias_entre(a, b)` · `meses_entre(a, b)` · `edad(nacimiento)` · `dia_semana` · `nombre_mes` · `inicio_mes` · `fin_mes` |
| **Intervalos** | `dias(n)` · `semanas(n)` · `meses(n)` · `años(n)` · `horas(n)` · `minutos(n)` · `segundos(n)` · `hace …` · `dentro de …` |

### Cambios seguros

```
empezar
borrar de pedidos donde estado = "cancelado"
deshacer          -- o «confirmar» para guardarlos
```

Sin `empezar`, cada orden se guarda al terminar. Una orden que falla a medias no deja nada a medias.

### Usuarios

```
crear usuario ana clave "una clave larga" puede escribir en tienda, blog
crear usuario visitas clave "otra clave larga" puede leer
cambiar clave de ana a "clave nueva"
cambiar permiso de ana a todo
borrar usuario visitas
usuarios
```

- **`leer`:** solo consulta: buscar, describir, tablas…
- **`escribir`:** cambia datos y estructura en sus bases.
- **`todo`:** además gestiona bases y usuarios.
- **`en …`:** limita a qué bases llega cada uno; sin `en`, llega a todas.
- **Claves:** tienen que tener 8 caracteres como mínimo y se guardan con *scrypt*, nunca en claro.
- **Consola:** no pide entrar, porque quien la abre ya tiene acceso a los ficheros.
- **Panel:** en cuanto existe un usuario, pide entrar.

### Cuentas propias (registro abierto)

Si quien administra abre el registro (**Usuarios → Registro público**), cualquiera puede crearse una cuenta desde la pantalla de entrada:

- **Solo ve lo suyo:** cada cuenta ve y maneja únicamente sus propias bases. Dentro de ellas puede hacer de todo: tablas, datos, copias, papelera y claves de API para sus aplicaciones.
- **No puede tocar nada más:** las bases de los demás y los usuarios quedan fuera de su alcance.
- **Tiene límites,** que se cambian en los mismos ajustes. Por defecto son 10 bases, 100 MB y 250.000 filas. Al llegar a uno, puede seguir consultando y borrando, pero no añadir.
- **Un nombre de base ya usado por otra persona no vale,** aunque esa base esté borrada. Así nadie puede recuperar la papelera ni las copias de otro.
- **Hay frenos contra el abuso:**
  - como mucho 3 cuentas nuevas por hora desde la misma dirección y 30 en total;
  - si el servidor llega a la memoria máxima, las cuentas no pueden añadir datos hasta que baje;
  - las bases que nadie usa en 10 minutos se guardan y salen de la memoria.

### Claves de API

Las aplicaciones no entran con usuario y clave: usan una **clave de API**, que actúa con los permisos de su usuario. Lo normal es un usuario por aplicación, con lo justo:

```
crear usuario web_tienda clave "una clave larga de verdad" puede escribir en tienda
crear clave api para web_tienda llamada "web de producción"     -- se muestra UNA vez: cópiala
claves api                                                     -- las que hay y cuándo se usaron
borrar clave api k6e893e56                                     -- deja de valer al momento
```

Se guardan como huella (*sha256*), nunca en claro. Si una se filtra, bórrala y crea otra.

### Mensajes de error

Explican qué ha pasado y cómo arreglarlo, en el idioma del lenguaje:

```
✕ no entiendo «nombre» (línea 1, columna 32): aquí debería acabar la orden (¿falta una coma o sobra una palabra?)
✕ pedidos.cliente_id apunta a clientes(id) = 99, y esa fila no existe
✕ ¿todas las filas de «clientes»? Si es lo que quieres, añade «todas» …
✕ «select» es SQL. En SBD se escribe: buscar tabla donde … mostrar …
```

---

## Consola

```bash
sbd [directorio] [-b base] [-e "orden"] [-f script.sbd]
sbd --servidor https://datos.miweb.es --clave sbd_…        # contra un servidor remoto (o SBD_CLAVE=…)
```

El prompt muestra la base en uso (`sbd:tienda›`) y una `*` mientras hay una transacción abierta. Una orden puede ocupar varias líneas: mientras esté a medias, por ejemplo con una `{` abierta, la consola espera a que la termines. **Tab** completa tablas, columnas, bases y palabras del lenguaje. Órdenes propias de la consola: `ayuda`, `leer script.sbd`, `tiempo` y `salir`.

Si el servidor ya está en marcha con esa carpeta, la consola no la abre por su cuenta: se conecta a él (con la clave local que el servidor deja en `datos/.sbd.servidor`, legible solo por su dueño). Así nunca se pisan los cambios, y puedes usar la consola en un servidor de producción sin pararlo.

## Panel web

```bash
sbd servidor [directorio] [--puerto 8080] [--abrir]
```

El mismo proceso sirve el panel, la API para aplicaciones (`/api/v1/`) y la consola conectada.

**El archivador.** Funciona como phpMyAdmin. A la izquierda está el índice, con las bases y sus tablas. A la derecha, la carpeta de lo que tienes abierto, con una pestaña para cada cosa:

| Abierto | Pestañas |
|---|---|
| Todas las bases | **Bases** (crear, abrir, exportar, borrar) · **Código** · **Usuarios** (y claves de API) · **Copias e historial** (copias diarias, papelera, quién cambió qué) |
| Una base | **Tablas** (filas, columnas, vaciar, borrar) · **Código** · **Nueva tabla** · **Importar** CSV o Excel · **Exportar** · **Operaciones** (copiar o borrar la base, últimos cambios) |
| Una tabla | **Examinar** · **Estructura** · **Código** · **Buscar** · **Insertar** · **Operaciones** (renombrar, copiar, exportar, vaciar, borrar) |

- **Código:** un editor grande para escribir SBD directamente: crear tablas, consultas o scripts enteros.
  - Lleva números de línea, resaltado, sangría automática y autocompletado de tablas, columnas, bases y palabras del lenguaje.
  - **Ctrl+⏎** lo ejecuta todo. Si hay texto seleccionado, solo ese.
  - Cada orden muestra su resultado debajo, y si una falla, dice cuál y por qué.
  - Hay plantillas con el nombre y las columnas de la tabla abierta, una chuleta de ejemplos y las órdenes anteriores.
- **Examinar:** la tabla con sus filas.
  - Pulsando la cabecera se ordena, y también se puede filtrar y pasar de página.
  - Con doble clic se cambia una celda. Cada fila tiene editar, copiar y borrar, y se pueden marcar varias para borrarlas a la vez.
- **Estructura:** las columnas, con renombrar, indexar y quitar, más formularios para añadir una columna y crear índices. Debajo están las relaciones y la definición en SBD.
- **Buscar e Insertar:** formularios con un campo por columna.
- **Siempre se ve la orden SBD:** cada botón enseña la orden que ejecuta (o va a ejecutar), igual que phpMyAdmin enseña el SQL. Con «editar en Código» la llevas al editor para retocarla.
- **Lo que destruye pide confirmación:** borrar, vaciar o quitar enseña la orden exacta antes de hacerlo.
- **Además:** el sello de «transacción abierta» con confirmar y deshacer, y el tema papel o pizarra. En el móvil, el índice se abre como un cajón.

**Seguridad:**
- **Sin usuarios:** el panel solo se abre en este equipo (`127.0.0.1`).
- **Con usuarios:** pide entrar, y cada persona ve y hace solo lo que permite su usuario. La sesión va en una cookie `HttpOnly` y `SameSite=Strict`, y tras 5 claves fallidas seguidas hay que esperar.
- **Claves en pantalla:** las de `crear usuario` y `cambiar clave` no se muestran ni se guardan en el historial.
- **Abrirlo a la red** (`--host 0.0.0.0`) exige que exista al menos un usuario. Sin HTTPS, las claves viajarían sin cifrar: usa `--certificado` y `--clave-privada`, ponlo detrás de un proxy con HTTPS o usa un túnel SSH. Está todo en [PRODUCCION.md](PRODUCCION.md).

**Si el servidor es remoto** (por ejemplo, con VS Code por SSH), reenvía el puerto: pestaña *Ports* → *Forward a Port* → `8080`, o `ssh -L 8080:127.0.0.1:8080 usuario@servidor`, y abre http://localhost:8080.

## Desde tus aplicaciones

Las aplicaciones hablan con el servidor (`sbd servidor`) por HTTP. Hay un cliente para cada lenguaje, sin dependencias, y todos funcionan igual: `consultar` devuelve filas como diccionarios u objetos, `ejecutar` devuelve el resultado completo y `transaccion` hace todo o nada. Los valores siempre van como parámetros `?`, nunca pegados al texto de la orden.

**Python** (incluido en el paquete):

```python
import sbd, os

db = sbd.conectar_servidor('https://datos.miweb.es', clave=os.environ['SBD_CLAVE'], base='tienda')
for p in db.consultar('buscar productos donde precio < ? ordenar precio', [20]):
    print(p['nombre'], p['precio'])                       # precio: Decimal('12.50')

id = db.ejecutar('insertar en pedidos { cliente_id: ? } devolver id', [3]).escalar()
with db.transaccion() as t:
    t.ejecutar('actualizar productos poner stock = stock - 1 donde id = ?', [7])
    t.ejecutar('insertar en lineas { pedido_id: ?, producto_id: 7, cantidad: 1 }', [id])
```

**JavaScript** (navegador y Node 18+), en [sbd/web/static/sbd-cliente.js](sbd/web/static/sbd-cliente.js), y también lo sirve el propio servidor en `/cliente/sbd.js`:

```js
import { SBD } from './sbd-cliente.js';
const db = new SBD({ url: 'https://datos.miweb.es', clave: process.env.SBD_CLAVE, base: 'tienda' });
const baratos = await db.consultar('buscar productos donde precio < ?', [20]);
await db.transaccion(async t => { await t.ejecutar('vaciar carrito'); });
```

**PHP** 7.4+, en [clientes/php/SBD.php](clientes/php/SBD.php) (usa curl si está y, si no, streams):

```php
require 'SBD.php';
$db = new SBD('https://datos.miweb.es', getenv('SBD_CLAVE'), 'tienda');
foreach ($db->consultar('buscar productos donde precio < ?', [20]) as $p) echo $p['nombre'], "\n";
$db->transaccion(function (SBD $t) { $t->ejecutar('borrar de carrito donde caduca < ahora()'); });
```

Los errores llegan como excepción (`ErrorServidor`, `ErrorSBD`, `SBDException`) con el mensaje en español y un `tipo` para decidir qué hacer: `sintaxis`, `orden`, `permiso`, `ocupada`, `clave`… La API está descrita entera en [PRODUCCION.md](PRODUCCION.md#la-api).

**Con un ORM:** la carpeta [orm/](orm/README.md) tiene un conector de SQLAlchemy (`create_engine("sbd:///datos/mi_base.base")` o, por un servidor, `"sbd+https://:CLAVE@datos.miweb.es/mi_base"`) y una biblioteca de ejemplo con su web. Traduce el SQL que genera SQLAlchemy a las órdenes de SBD; el lenguaje de SBD no cambia, y SBD sigue sin depender de nada, porque SQLAlchemy solo se instala con `pip install ./orm`.

**Como base de objetos:** la carpeta [objetos/](objetos/README.md) tiene el Baúl, que guarda objetos de Python con su identidad, sus referencias y su herencia, en un fichero YAML o en SBD (`baul.abrir('sbd:///datos/objetos.base')`). En SBD, cada objeto es una fila con su YAML.

**Sin servidor**, dentro del mismo programa (scripts, pruebas, herramientas de una persona):

```python
import sbd

s = sbd.Sesion(sbd.servidor('datos'), 'tienda')
for nombre, email in s.ejecutar('buscar clientes donde ciudad = ? mostrar nombre, email', ['Madrid']):
    print(nombre, email)

db = sbd.conectar('notas.base')        # un único fichero, sin directorio
db.execute('crear tabla notas { id entero clave  texto texto }')
db.query('buscar notas')               # [{'id': …, 'texto': …}, …]
```

## Cómo está hecho

```
texto ──▶ lexer.py ──▶ parser.py ──▶ árbol (nodes.py) ──▶ executor.py ──▶ Result
                                                              │
   servidor.py: varias bases en un directorio    database.py: tablas, transacciones, disco
                                                 storage.py:  filas e índices
```

- **Ejecución:** los `unir` por igualdad usan *hash join*, y las condiciones del `donde` se empujan a cada tabla del `unir` antes de combinar. Un `donde` con `=` o con un rango aprovecha el índice si existe (los índices guardan sus claves ordenadas), y las subconsultas que no dependen de la fila exterior se calculan una sola vez. Ninguna orden puede tardar más de 30 segundos (`--limite-segundos`): si se pasa, se corta y se deshace.
- **Transacciones:** cada cambio anota cómo deshacerse; los cambios de estructura copian la tabla en vez de tocarla.
- **Disco, en dos ficheros por base:**
  - `nombre.base` es una foto completa: JSON con la estructura escrita en SBD y los datos.
  - `nombre.diario` recibe una línea por cada cambio confirmado, solo con las filas que cambiaron. Con 20.000 filas, guardar un cambio pasa de 92 ms a menos de 1 ms.
  - Al abrir, se carga la foto y se repite el diario encima. Cuando el diario crece más que la foto, se compacta en una foto nueva; también al cerrar.
  - La foto se escribe de forma atómica: primero un temporal con `fsync`, luego un renombrado y un `fsync` de la carpeta. El diario solo se borra después.
  - Al confirmar, la transacción entera se escribe como una sola línea del diario, con `fsync`, antes de responder. Una línea a medio escribir se descarta, así que nunca queda una transacción a medias.
  - Está probado matando el proceso con `kill -9` y cortando el diario en cada byte, y las pruebas comprueban el orden de los `fsync`. Un corte de luz real no se ha probado: la seguridad depende también de que el disco cumpla con `fsync`.
- **Cerrojos:** el sistema operativo bloquea la carpeta y cada fichero mientras un programa los usa, y los suelta solo si ese programa muere.
- **Servidor:** un solo proceso tiene las bases abiertas y ejecuta las órdenes de una en una, como SQLite: cada orden ve siempre datos coherentes. Cada sesión tiene su propia transacción; si otra sesión intenta escribir en una base con una transacción abierta ajena, espera hasta 5 segundos y, si sigue ocupada, recibe el error `ocupada`. Una transacción olvidada se deshace sola a los 2 minutos sin uso.

## Límites conocidos

- **Cada base vive entera en memoria**, unos 1,8 KB por fila. Va cómoda hasta unos cientos de miles de filas por base. Con un millón funciona (1,8 GB de RAM y 13 s para abrirla), pero lo que recorre la tabla entera tarda segundos. Para decenas de millones no está pensada. Las cifras medidas están en [PRODUCCION.md](PRODUCCION.md#cuánto-aguanta).
- **Un solo proceso** atiende todas las peticiones, de una en una: unos cientos de peticiones por segundo. Sobra para webs, tiendas y herramientas internas, pero no para miles de peticiones por segundo.
- **Sin réplicas:** hay una sola máquina. Las copias diarias están en el mismo disco, así que llévalas también a otro sitio (está explicado en PRODUCCION.md).
- Todavía no tiene disparadores, y los procedimientos no tienen `si` ni bucles.
- Al renombrar una columna, si otra tabla de la misma vista tiene una columna con ese nombre, la vista no se toca y se avisa de que hay que revisarla a mano.
