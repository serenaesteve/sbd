# Resultado de aprendizaje 2

**Desarrolla aplicaciones que gestionan información almacenada en bases de datos relacionales identificando y utilizando mecanismos de conexión.**

Respondo a cada criterio con mi proyecto, **SBD**: un gestor de bases de datos relacional propio, escrito en Python, con un lenguaje en español en lugar de SQL. Tiene tablas, claves, referencias entre tablas, uniones y transacciones. Por eso las órdenes de los ejemplos no están en SQL.

Todo lo que se afirma aquí se puede comprobar con un solo comando, que trabaja en una carpeta temporal y enseña el resultado de cada apartado:

```bash
python3 ejemplos/pruebas_ra2.py
```

## Equivalencias con SQL y JDBC

SBD no usa SQL ni JDBC, pero cubre las mismas funciones. Esta tabla relaciona cada concepto de la unidad con su equivalente:

| Concepto | SQL y JDBC | SBD |
|---|---|---|
| Abrir la conexión | `DriverManager.getConnection(url, usuario, clave)` | `sbd.conectar_servidor(url, clave=…, base=…)` o `sbd.conectar(fichero)` |
| Orden con parámetros | `PreparedStatement` con `?` | `ejecutar(orden, [valores])` con `?` |
| Recorrer el resultado | `ResultSet` | `Result` / `Resultado` |
| Filas afectadas | `executeUpdate()` | `rowcount` / `filas_afectadas` |
| Definir la estructura | `CREATE TABLE`, `CREATE INDEX`, `CREATE VIEW` | `crear tabla`, `indexar`, `crear vista` |
| Consultar | `SELECT … JOIN … WHERE … GROUP BY` | `buscar … unir … donde … agrupar` |
| Modificar | `INSERT`, `UPDATE`, `DELETE` | `insertar`, `actualizar`, `borrar` |
| Transacciones | `setAutoCommit(false)`, `commit()`, `rollback()` | `empezar`, `confirmar`, `deshacer`, o `with db.transaccion()` |
| Procedimientos | `CREATE PROCEDURE` y `CallableStatement` | `crear procedimiento` y `ejecutar nombre(…)` |
| Liberar recursos | `close()`, *try-with-resources* | `close()`, `with` |

| Criterio | Dónde está en SBD | ¿Cumplido? |
|---|---|---|
| a) Ventajas e inconvenientes de los conectores | Este documento | Sí |
| b) Gestores embebidos e independientes | `sbd.conectar`, `sbd.servidor`, `sbd servidor` | Sí |
| c) Conector idóneo | `sbd/cliente.py`, `clientes/php/SBD.php`, `sbd-cliente.js` | Sí |
| d) Establecer la conexión | `conectar`, `conectar_servidor` | Sí |
| e) Definir la estructura | `crear base`, `crear tabla`, `indexar`, `crear vista` | Sí |
| f) Modificar el contenido | `insertar`, `actualizar`, `borrar` | Sí |
| g) Objetos para el resultado | `Result`, `Resultado` | Sí |
| h) Efectuar consultas | `buscar`, `unir`, `agrupar`, `con` | Sí |
| i) Eliminar objetos al terminar | `close()`, `with`, `cerrar_sesion()`, `borrar …` | Sí |
| j) Gestionar transacciones | `empezar`, `confirmar`, `deshacer`, `transaccion()` | Sí |
| k) Procedimientos almacenados | `crear procedimiento`, `ejecutar` | Sí |

## a) Ventajas e inconvenientes de utilizar conectores

Un conector es la pieza que permite a un programa hablar con el gestor de base de datos sin ocuparse de cómo se guardan los datos. SBD tiene los suyos: `sbd.conectar` y `sbd.Sesion` para trabajar dentro del mismo programa, y clientes por HTTP para Python, JavaScript y PHP.

**Ventajas:**
- **Separan la aplicación de los datos.** El programa envía órdenes y recibe filas, sin saber cómo están guardadas.
- **Parámetros.** Los valores van aparte de la orden con `?`, lo que evita la inyección de órdenes.
- **Transacciones y errores ya resueltos.** El conector da las transacciones y convierte los fallos en excepciones con un tipo: `sintaxis`, `orden`, `permiso`, `ocupada`, `clave`…
- **Conexión remota y desde varios lenguajes.** La misma base se puede usar desde otra máquina y desde programas distintos.

**Inconvenientes:**
- **Dependencia.** La aplicación queda ligada al conector y a su versión.
- **Portabilidad.** SBD no tiene conector JDBC ni ODBC: las herramientas que necesitan esos estándares no pueden conectarse, y una aplicación hecha para SBD no serviría para otro gestor sin reescribir sus órdenes.
- **Coste.** Cada capa añade tiempo. Medido en mi servidor, una lectura por clave tarda unos 0,17 ms dentro del motor y unos 1,7 ms pasando por HTTP.
- **Riesgo si se usa mal.** El conector no protege de todo: si se pega en la orden un texto como `x" vaciar c --`, la tabla se vacía. Con `?`, ese mismo texto es solo un dato. `ejemplos/pruebas_ra2.py` hace las dos cosas y enseña el resultado.

## b) Gestores de bases de datos embebidos e independientes

SBD funciona de las dos formas:

- **Embebido:** el motor corre dentro del propio programa, sin instalar ni arrancar nada. Puede ser un único fichero o una carpeta con varias bases.

  ```python
  import sbd

  with sbd.conectar('notas.base') as db:      # un fichero
      db.execute('crear tabla notas { id entero clave  texto texto }')

  with sbd.servidor('datos') as srv:          # una carpeta con varias bases
      sesion = sbd.Sesion(srv)
      sesion.ejecutar('crear base tienda')
  ```

- **Independiente:** SBD corre como un proceso aparte y los programas se conectan por HTTP con una clave de API.

  ```bash
  sbd servidor datos --puerto 8080
  ```

  Es el modo que uso en producción, en <https://sbd.serenaesteve.com>, como servicio de systemd detrás de Apache con HTTPS.

**Cuándo usar cada uno:** el embebido es más rápido y más sencillo, pero solo lo puede usar un programa a la vez, porque un cerrojo impide que otro abra el mismo fichero. El independiente admite varios programas y usuarios, y permite trabajar desde otra máquina, a cambio de tener un servidor en marcha.

## c) Se ha utilizado el conector idóneo en la aplicación

Elijo el conector según el caso:

| Situación | Conector |
|---|---|
| Un script o una herramienta de una sola persona en Python | `sbd.conectar` o `sbd.Sesion` (embebido) |
| Una aplicación en Python contra el servidor | `sbd.conectar_servidor` (`sbd/cliente.py`) |
| Una aplicación en JavaScript (navegador o Node) | `sbd-cliente.js` |
| Una aplicación en PHP | `clientes/php/SBD.php` |
| Un lenguaje sin cliente propio | La API HTTP directamente, con JSON |

Las librerías de Python y PHP tienen pruebas automáticas, y la de JavaScript se ha probado en el navegador. No uso JDBC porque SBD está hecho en Python y no ofrece ese estándar.

## d) Se ha establecido la conexión

**En modo embebido**, basta con indicar el fichero. Si no existe, se crea:

```python
db = sbd.conectar('notas.base')
```

**En modo servidor**, hacen falta la dirección, la clave de API y la base. La clave se crea para un usuario con los permisos justos, y no debe escribirse en el código:

```
crear usuario web_tienda clave "una clave larga" puede escribir en tienda
crear clave api para web_tienda llamada "web de producción"
```

```python
import os, sbd

db = sbd.conectar_servidor('https://sbd.serenaesteve.com', clave=os.environ['SBD_CLAVE'], base='tienda')
print(db.salud()['ok'])   # True
```

Los fallos de conexión llegan como excepción: con una clave incorrecta, `ErrorServidor` de tipo `clave` y código 401. En modo embebido, si otro programa ya tiene la base abierta, SBD lo indica con un error en lugar de arriesgarse a pisar los datos.

## e) Se ha definido la estructura de la base de datos

La estructura se define con órdenes del lenguaje:

```
crear base tienda

crear tabla clientes {
  id      entero clave
  nombre  texto(60) obligatorio
  email   texto unico
  ciudad  texto = "Madrid"
}

crear tabla pedidos {
  id          entero clave
  cliente_id  entero obligatorio -> clientes(id) al borrar cascada
  total       dinero = 0 comprobar total >= 0
  fecha       fecha = hoy()
}

indexar pedidos (cliente_id)
crear vista madrileños = buscar clientes donde ciudad = "Madrid"
```

- **Tipos:** `entero`, `decimal`, `dinero`, `texto`, `texto(n)`, `logico`, `fecha` y `fechahora`.
- **Restricciones:** clave primaria (`clave`), referencia a otra tabla (`->`), `unico`, `obligatorio`, `comprobar` y valores por defecto.
- **Otros objetos:** índices y vistas.
- **Cambios posteriores:** `añadir a`, `quitar de`, `renombrar tabla` y `renombrar columna`.

## f) Se han desarrollado aplicaciones que modifican el contenido de la base de datos

```python
# Insertar con parámetros y recuperar el id generado
nuevo = db.ejecutar('insertar en clientes { nombre: ?, email: ? } devolver id',
                    ['Ana', 'ana@ejemplo.es']).escalar()

# Saber cuántas filas han cambiado
r = db.ejecutar('actualizar pedidos poner total = total + 1 donde cliente_id = ?', [nuevo])
print(r.filas_afectadas)

# Insertar, o actualizar si ya existe
db.ejecutar('insertar o actualizar en clientes { email: ?, nombre: ? }', ['ana@ejemplo.es', 'Ana B.'])

# Borrar
db.ejecutar('borrar de pedidos donde id = ?', [7])
```

Como medida de seguridad, `actualizar` sin `donde` da error si no se añade `todas`, y para borrar todas las filas hay que usar `vaciar`. Así no se cambia una tabla entera por despiste.

## g) Se han definido los objetos destinados a almacenar el resultado de las consultas

Cada orden devuelve un objeto con su resultado:

| | `Result` (embebido) | `Resultado` (servidor) |
|---|---|---|
| Columnas | `columns` | `columnas` |
| Filas | `rows` | `filas` |
| Filas afectadas | `rowcount` | `filas_afectadas` |
| Último id | `lastrowid` | `ultimo_id` |
| Un solo valor | `scalar()` | `escalar()` |
| Filas como diccionarios | `dicts()` | `dicts()` |

```python
r = db.execute('buscar notas')
print(r.columns)   # ['id', 'texto']
print(r.rows)      # [(1, 'hola')]
print(r.dicts())   # [{'id': 1, 'texto': 'hola'}]
```

El tipo `dinero` llega en Python como `Decimal`, para no perder céntimos: `Decimal('20.99')`.

## h) Se han desarrollado aplicaciones que efectúan consultas

```python
# Con filtro, orden y parámetros; cada fila es un diccionario
db.consultar('buscar clientes donde ciudad = ? ordenar nombre limite 10', ['Madrid'])

# Unión de tablas, agrupación y totales
db.consultar('''buscar pedidos como p {
  unir clientes como c en c.id = p.cliente_id
  agrupar c.nombre
  mostrar c.nombre, contar() como pedidos, sumar(p.total) como gasto
  ordenar gasto desc
}''')

# Una consulta intermedia con nombre
db.consultar('con grandes = (buscar pedidos donde total > ?) buscar grandes mostrar contar() como n', [5])
```

También hay subconsultas, consultas recursivas, `union`, y funciones de ventana con `sobre (por … ordenar …)`.

## i) Se han eliminado los objetos una vez finalizada su función

- **Conexión embebida:** `close()`, o mejor `with`, que la cierra también si hay un error. Al cerrar, deshace lo que no estuviera confirmado, compacta el diario y suelta el cerrojo, de modo que otro programa ya puede abrir la base. La prueba es que, tras el `with`, la puedo volver a abrir y los datos siguen ahí.
- **Sesiones en el servidor:** `cerrar_sesion()`, que también se puede hacer con `with`. `transaccion()` abre su propia sesión y la cierra al terminar. Además, el servidor deshace sola una transacción abandonada a los 2 minutos sin uso y cierra una sesión a los 10.
- **Resultados:** SBD los entrega completos en memoria, así que no hay un cursor abierto que cerrar como el `ResultSet` de JDBC. Son objetos normales de Python, y el recolector de basura los libera cuando dejan de usarse.
- **Consultas intermedias:** lo definido con `con` solo existe mientras dura la consulta.
- **Objetos de la base:** `borrar tabla`, `borrar vista` y `borrar indice`. `borrar base` la mueve a una papelera, desde donde se puede recuperar.
- **Memoria del servidor:** las bases que nadie usa en 10 minutos se guardan y se sacan de la memoria.

## j) Se han gestionado las transacciones

**En el lenguaje:**

```
empezar
actualizar cuentas poner saldo = saldo - 30 donde id = 1
actualizar cuentas poner saldo = saldo + 30 donde id = 2
confirmar       -- o «deshacer»
```

**Desde un programa**, el cliente confirma al terminar y deshace si hay una excepción:

```python
with db.transaccion() as t:
    t.ejecutar('actualizar cuentas poner saldo = saldo - 30 donde id = 1')
    t.ejecutar('actualizar cuentas poner saldo = saldo - 30 donde id = 2')
```

En `ejemplos/pruebas_ra2.py` esto se hace con dos cuentas, de 100 € y 0 €. La segunda orden incumple `comprobar saldo >= 0`, así que salta el error y la primera también se deshace: los saldos siguen en 100 y 0.

**Cómo funciona por dentro:**
- **Atomicidad:** cada cambio anota cómo deshacerse. Si falla una orden, no queda nada a medias.
- **Aislamiento:** una transacción pertenece a una sesión. Si otra quiere usar esa base, espera hasta 5 segundos y, si sigue ocupada, recibe el error `BaseOcupada`. Es un sistema sencillo: solo puede haber una transacción abierta por base.
- **Durabilidad:** al confirmar, la transacción se escribe en un diario con `fsync` antes de responder. Hay pruebas que matan el proceso con `kill -9` a mitad de una transacción y cortan el diario en cada byte, y nunca queda una transacción a medias. Un corte de luz real no se ha probado.

## k) Se han ejecutado procedimientos almacenados en la base de datos

SBD tiene procedimientos almacenados: órdenes guardadas en la base, con nombre y parámetros, que se ejecutan juntas.

**Crearlo:**

```
crear procedimiento transferir(origen, destino, cantidad) {
  actualizar cuentas poner saldo = saldo - :cantidad donde id = :origen
  actualizar cuentas poner saldo = saldo + :cantidad donde id = :destino
  buscar cuentas donde id en [:origen, :destino] ordenar id mostrar saldo
}
```

**Ejecutarlo desde una aplicación**, con parámetros como cualquier otra orden:

```python
filas = db.consultar('ejecutar transferir(?, ?, ?)', [1, 2, '20'])
print(filas)   # [{'saldo': Decimal('50.00')}, {'saldo': Decimal('50.00')}]
```

**Cómo funcionan:**
- **Se guardan en la base.** Siguen ahí al reiniciar el servidor, y `copiar base` y el volcado a script los conservan. `procedimientos` los lista y `borrar procedimiento` los elimina.
- **Son todo o nada.** Si falla una orden del cuerpo, se deshace la llamada entera. Con los saldos anteriores, `ejecutar transferir(1, 2, 999)` da error y ningún saldo cambia.
- **Devuelven filas.** El resultado es el de la última consulta del cuerpo y se recoge en los mismos objetos que cualquier consulta. Si no hay ninguna, devuelve un resumen con las filas afectadas.
- **Tienen permisos.** Se ejecutan con los permisos de quien llama: un usuario que solo puede leer no puede ejecutarlos.

**Ventaja frente a tener la lógica en la aplicación:** si dos programas usan la misma base, los dos llaman al mismo procedimiento en lugar de repetir las órdenes.

**Limitaciones:** son más sencillos que los de MySQL o PostgreSQL. No tienen `si` ni bucles, ni variables locales, y dentro solo admiten órdenes de datos (`buscar`, `insertar`, `actualizar`, `borrar`, `vaciar` y `ejecutar`). Para decidir según los datos, hay que usar reglas `comprobar` en las tablas o lógica en la aplicación. Tampoco hay disparadores.

## Uso de inteligencia artificial

He desarrollado SBD con ayuda de IA (Claude, de Anthropic). Yo he decidido qué construir, el lenguaje en español y cómo debía funcionar, y la IA ha escrito el código. Las pruebas se pueden repetir con `python3 ejemplos/pruebas_ra2.py`.
