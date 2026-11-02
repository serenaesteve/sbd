# SBD en producción

Cómo poner SBD en un servidor de verdad y conectar tus aplicaciones. Para el lenguaje, mira el [README](README.md).

```
  tu web / app ──HTTPS──▶ proxy (Caddy o nginx) ──▶ sbd servidor 127.0.0.1:8080 ──▶ /srv/sbd/datos
                          pone el certificado        panel · API · consola           bases, copias, auditoría
```

**Lista corta:**

1. Instálalo en su propio entorno, con un usuario del sistema solo para él.
2. Crea un usuario `admin` y un usuario con su clave de API para cada aplicación.
3. Instala el servicio de systemd, que se arranca solo y se reinicia si falla.
4. Ponle delante un proxy con HTTPS. No abras el puerto 8080 a internet.
5. Lleva las copias diarias a otra máquina.

---

## 1. Instalar

```bash
sudo useradd --system --create-home --home-dir /srv/sbd --shell /usr/sbin/nologin sbd
sudo -u sbd python3 -m venv /srv/sbd/venv
sudo -u sbd /srv/sbd/venv/bin/pip install /ruta/a/sbd     # la carpeta de este proyecto
sudo -u sbd mkdir /srv/sbd/datos
```

Solo necesita Python 3.11 o superior. No instala ninguna dependencia.

## 2. Usuarios y claves

Abre la consola como el usuario `sbd`. Así la contraseña no queda en el historial de la shell:

```bash
sudo -u sbd /srv/sbd/venv/bin/sbd /srv/sbd/datos
```

```
crear usuario admin clave "una frase larga que solo sepas tú" puede todo

crear base tienda
crear usuario web_tienda clave "otra frase larga, no se usa para entrar" puede escribir en tienda
crear clave api para web_tienda llamada "web de producción"
```

- **La clave de API** (`sbd_k…_…`) se muestra una sola vez. Guárdala en la configuración de la aplicación, en una variable de entorno como `SBD_CLAVE`. No la escribas en el código.
- **Un usuario por aplicación**, con lo justo: `puede leer` para lo que solo consulta y `en tienda` para que no vea otras bases. Si una clave se filtra, `borrar clave api k…` la anula al momento y ya puedes crear otra.
- **`admin`** es para ti, para entrar al panel.

**¿Que se registre cualquiera?** En el panel, en **Usuarios → Registro público**, marca «cualquiera puede crearse una cuenta» y ajusta los límites por cuenta. Cada cuenta solo ve sus bases (lo explica el README, en «Cuentas propias»). Como cada fila ocupa unos 1,8 KB de memoria, ajusta la «memoria máxima del servidor» a lo que le quieras dejar a SBD en esa máquina.

## 3. El servicio

```bash
sudo /srv/sbd/venv/bin/sbd servicio /srv/sbd/datos --usuario-sistema sbd --proxy datos.miweb.es            # enseña la unidad
sudo /srv/sbd/venv/bin/sbd servicio /srv/sbd/datos --usuario-sistema sbd --proxy datos.miweb.es --instalar # la instala y la arranca
```

Escribe `/etc/systemd/system/sbd.service`. La unidad:

- arranca el servidor al encender la máquina y lo reinicia si falla;
- solo le deja escribir en la carpeta de datos (`ProtectSystem=strict`);
- al pararlo le manda `SIGINT`, así que deshace lo que estuviera sin confirmar y compacta los diarios antes de salir.

```bash
systemctl status sbd          # ¿está en marcha?
journalctl -u sbd -f          # una línea por petición: hora, IP, ruta, código, ms, usuario
systemctl restart sbd         # después de actualizar
```

Opciones del servidor (`sbd servidor --help`):

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--host` | `127.0.0.1` | Dónde escucha. Con un proxy, déjalo así. |
| `--puerto` | `8080` | |
| `--proxy dominios` | | Está detrás de un proxy con HTTPS que atiende esos dominios (ver abajo). |
| `--certificado` · `--clave-privada` | | Sirve HTTPS él mismo, sin proxy. |
| `--origenes` | | Las webs que pueden llamar a la API desde el navegador, como `https://mitienda.es` (CORS). |
| `--limite-segundos` | `30` | Lo más que puede tardar una orden. Si se pasa, se corta y se deshace. |
| `--copias` | `7` | Días de copias diarias que se guardan. Con `0` no hace ninguna. |
| `--silencio` | | No escribe una línea por petición. |

Para abrirse a la red (`--host` distinto de `127.0.0.1`) o con `--proxy`, el servidor exige que exista al menos un usuario. Si no lo hay, no arranca.

## 4. HTTPS

**Con un proxy (recomendado).** El proxy se encarga del certificado y lo renueva solo. SBD sigue escuchando solo en `127.0.0.1`. Hay que arrancar SBD con `--proxy datos.miweb.es` para que:

- acepte peticiones dirigidas a ese dominio (sin `--proxy` las rechaza para evitar el *DNS rebinding*);
- tome la IP real de `X-Forwarded-For`, así que quien prueba claves en el panel se bloquea a sí mismo y no a todo el mundo;
- marque la cookie del panel como `Secure`.

Caddy (pide el certificado él solo):

```
datos.miweb.es {
    reverse_proxy 127.0.0.1:8080
}
```

nginx (con el certificado de `certbot --nginx`):

```nginx
server {
    listen 443 ssl;
    server_name datos.miweb.es;
    ssl_certificate     /etc/letsencrypt/live/datos.miweb.es/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/datos.miweb.es/privkey.pem;
    client_max_body_size 50m;                         # para importar ficheros desde el panel

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Apache (con `a2enmod proxy proxy_http headers ssl`; el certificado, con `certbot certonly --webroot`):

```apache
<VirtualHost *:443>
    ServerName datos.miweb.es
    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/datos.miweb.es/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/datos.miweb.es/privkey.pem

    RequestHeader set X-Forwarded-Proto "https"
    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8080/
    ProxyPassReverse / http://127.0.0.1:8080/
</VirtualHost>
```

**Sin proxy.** Con `--host 0.0.0.0 --certificado fullchain.pem --clave-privada privkey.pem`, el servidor sirve HTTPS él mismo, con TLS 1.2 como mínimo y HSTS. Ten en cuenta tres cosas:

- el usuario `sbd` tiene que poder leer los dos ficheros;
- tras cada renovación del certificado hay que reiniciar el servicio (en certbot: `--deploy-hook "systemctl restart sbd"`);
- para usar el puerto 443 necesitaría privilegios, así que lo normal es otro puerto.

**Si tus aplicaciones están en la misma máquina**, pueden hablar con `http://127.0.0.1:8080` directamente, sin salir a la red ni pasar por HTTPS.

## 5. Conectar tus aplicaciones

```python
# Python: viene en el paquete (pip install en el entorno de tu aplicación)
import os, sbd
db = sbd.conectar_servidor('https://datos.miweb.es', clave=os.environ['SBD_CLAVE'], base='tienda')
```

```js
// JavaScript (Node 18+ o navegador): copia sbd/web/static/sbd-cliente.js a tu proyecto
import { SBD } from './sbd-cliente.js';
const db = new SBD({ url: 'https://datos.miweb.es', clave: process.env.SBD_CLAVE, base: 'tienda' });
```

```php
// PHP 7.4+: copia clientes/php/SBD.php a tu proyecto
require 'SBD.php';
$db = new SBD('https://datos.miweb.es', getenv('SBD_CLAVE'), 'tienda');
```

Los tres clientes tienen los mismos métodos:

- `consultar(orden, parámetros)`: las filas como diccionarios, objetos o arrays.
- `ejecutar(orden, parámetros)`: el resultado completo, con `escalar()`, `primera()`, `filasAfectadas` y `ultimoId`.
- `todas(texto)`: varias órdenes de una vez.
- `transaccion(función)`: todo o nada. Si la función lanza una excepción, se deshace.
- `usar(base)` y `salud()`.

**Reglas que conviene seguir siempre:**

- **Los valores van como parámetros `?`, nunca pegados al texto de la orden.** Es lo que impide la inyección de órdenes:
  `db.consultar('buscar clientes donde email = ?', [email])`.
- **El dinero llega como texto exacto** (`"12.50"`). Python lo convierte en `Decimal`. En JavaScript, no lo pases a `Number` si vas a sumar céntimos.
- **Desde el navegador**, la clave queda a la vista de quien abra la página. Llama a SBD desde tu propio servidor (el *backend*). Si de verdad tiene que ser desde el navegador, usa un usuario `puede leer` limitado a una base y pon la web en `--origenes`.
- **Si una orden recibe el error `ocupada`**, otra sesión tiene una transacción abierta en esa base desde hace más de 5 segundos. Reintentar más tarde es seguro.

## La API

Todas las rutas de `/api/v1/` piden la cabecera `Authorization: Bearer sbd_…`. Las que envían datos piden además `Content-Type: application/json`.

| Método y ruta | Cuerpo | Respuesta |
|---|---|---|
| `POST /api/v1/ejecutar` | `{"orden": "…", "parametros": [...], "base": "tienda", "sesion": "…"}` | los resultados (ver abajo) |
| `POST /api/v1/sesiones` | `{"base": "tienda"}` | `{"sesion": "api:…"}` |
| `DELETE /api/v1/sesiones/<id>` | | `{"ok": true}`, y deshace lo que quedara sin confirmar |
| `GET /api/v1/catalogo?base=tienda` | | tablas, columnas, vistas y bases, para autocompletar o generar código |
| `GET /salud` | (sin clave) | `{"ok": true, "version": "3.2.0", "bases": 2}`, para el monitor |
| `GET /cliente/sbd.js` | (sin clave) | el cliente de JavaScript |

En `ejecutar`, `orden` puede llevar varias órdenes seguidas. `base` y `sesion` son opcionales. Los parámetros solo pueden ser números, textos, `true`/`false` o `null`.

```json
{
  "resultados": [
    {"orden": "buscar productos donde precio < ?", "columnas": ["id", "nombre", "precio"],
     "tipos": ["entero", "texto", "dinero"], "filas": [[1, "Té", "4.50"]], "total": 1,
     "filas_afectadas": 1, "ultimo_id": null, "mensaje": "1 fila(s)", "ms": 0.21}
  ],
  "base": "tienda", "transaccion": false
}
```

**Errores.** La respuesta lleva `error` (el mensaje en español), `tipo` y `orden_fallida`. Las órdenes anteriores a la que falló sí se ejecutaron y su resultado viene en `resultados`. Si quieres todo o nada, envía `empezar … confirmar` en el mismo texto: si algo falla por el camino, se deshace entero.

| `tipo` | HTTP | Qué pasa |
|---|---|---|
| `sintaxis` · `incompleta` | 400 | La orden está mal escrita. El mensaje dice la línea, la columna y cómo arreglarla. |
| `orden` | 400 | Está bien escrita pero no se puede hacer: no existe la tabla, choca con una clave única, una referencia apunta a nada… |
| `transaccion_abierta` | 400 | Una petición suelta dejó un `empezar` sin `confirmar`, así que se ha deshecho. |
| `clave` | 401 | Falta la clave, no es válida o se ha borrado. |
| `permiso` | 403 | El usuario de la clave no puede hacer eso o no llega a esa base. |
| `no_existe` · `sesion` | 404 | La base no existe, o la sesión se cerró o caducó. |
| `ocupada` | 409 | Otra sesión tiene una transacción abierta en la base. Vuelve a intentarlo. |
| | 413 · 415 | La petición pasa de 50 MB, o no es JSON. |
| `interno` | 500 | Un fallo de SBD. El detalle queda en `journalctl -u sbd`. |

**Sesiones.** Cada petición sin `sesion` va por su cuenta. Para una transacción que dura varias peticiones (leer, decidir en tu código y escribir), haz esto:

1. Abre una sesión con `POST /api/v1/sesiones`.
2. Envía `empezar`, luego las órdenes que necesites y al final `confirmar`, todas con ese `sesion`.
3. Ciérrala con `DELETE /api/v1/sesiones/<id>`.

Mientras dura la transacción, la base queda reservada para esa sesión, así que tiene que ser corta. Una transacción sin usar durante 2 minutos se deshace sola, y una sesión sin usar durante 10 minutos se cierra. El `transaccion()` de los clientes hace todo esto por ti.

```bash
curl -s https://datos.miweb.es/api/v1/ejecutar \
  -H "Authorization: Bearer $SBD_CLAVE" -H 'Content-Type: application/json' \
  -d '{"base": "tienda", "orden": "buscar productos donde precio < ?", "parametros": [20]}'
```

## 6. Copias de seguridad

Cada día, el servidor copia todas las bases, los usuarios y las claves de API en `datos/.copias/AAAA-MM-DD/`. Guarda las de los últimos 7 días (`--copias`). Desde la consola o el panel:

```
copias                                      -- qué días hay
restaurar base tienda del 2026-10-27 como tienda_27   -- verla aparte, sin tocar la buena

borrar base tienda                          -- volver a ese día: la actual va a la papelera…
restaurar base tienda del 2026-10-27        -- …y la de ese día ocupa su sitio
copiar base tienda a tienda_antes_de_migrar -- antes de un cambio gordo
```

**Las copias están en el mismo disco que las bases**, así que no te salvan si se pierde la máquina. Llévalas a otra con cron:

```bash
# crontab -e del usuario sbd: cada noche a las 3:30
30 3 * * * rsync -a --delete /srv/sbd/datos/.copias/ copias@otra-maquina:/copias/sbd/
```

Copia `.copias/`, no los ficheros vivos de `datos/`: esos cambian mientras el servidor trabaja.

**Rehacer el servidor entero** desde una copia:

1. Para el servicio.
2. Copia en `datos/` el contenido de la carpeta de ese día: los `.base`, los `.diario`, `.usuarios` y `.claves`.
3. Vuelve a arrancarlo.

## 7. Vigilar

- **`/salud`** responde `{"ok": true}` si el servidor está vivo. Sirve para cualquier monitor (UptimeRobot, Uptime Kuma…).
- **`historial`** (o `historial de tienda limite 200`) muestra quién cambió qué y cuándo: cada orden que modifica algo queda en `datos/.auditoria/AAAA-MM.log`, con las claves tapadas.
- **`claves api`** dice cuándo se usó cada clave por última vez. Una que nadie usa sobra.
- **`journalctl -u sbd`** tiene una línea por petición y el detalle de cualquier error interno.

## 8. Actualizar SBD

```bash
sudo -u sbd /srv/sbd/venv/bin/pip install --upgrade /ruta/a/sbd
sudo systemctl restart sbd
```

Al pararse, el servidor compacta los diarios. Al arrancar, lee las bases tal cual, sin migraciones. Por si acaso, haz antes `copiar base` de lo importante.

**La consola en producción** se conecta al servidor en marcha, sin pararlo: `sudo -u sbd /srv/sbd/venv/bin/sbd /srv/sbd/datos`. Desde tu ordenador también puedes usarla contra el servidor remoto: `sbd --servidor https://datos.miweb.es --clave sbd_…`.

## Cuánto aguanta

Medido en este servidor con una tabla de pedidos de 5 columnas (texto, dinero, fecha y dos enteros), con índices en cliente y fecha:

| | 100.000 filas | 1.000.000 filas |
|---|---|---|
| Memoria | 191 MB | 1,8 GB |
| Disco | 6 MB | 58 MB |
| Abrir la base al arrancar | 1,4 s | 12,9 s |
| Insertar y guardar una fila | 0,6 ms | 0,6 ms |
| Buscar por clave o por índice | 0,1 ms | ≤ 0,5 ms |
| Recorrer la tabla entera (sin índice) | 0,4 s | 5,2 s |
| Agrupar la tabla entera | 0,7 s | 8,9 s |

Por la API, con el cliente y el servidor en procesos distintos:

| | |
|---|---|
| Una petición tras otra | 1,7 ms por lectura |
| Con 8 clientes a la vez, abriendo una conexión por petición | entre 400 y 600 peticiones por segundo |
| Dentro del servidor, sin contar HTTP | 0,17 ms por lectura por clave, 0,9 ms por escritura guardada |

**Para qué va bien:** webs, tiendas, paneles internos, APIs de un negocio, herramientas de un equipo. Cientos de miles de filas por base y unas cuantas peticiones por segundo, con picos de cientos.

**Para qué no:**

- **Decenas de millones de filas.** Cada base vive entera en memoria, a unos 1,8 KB por fila.
- **Miles de peticiones por segundo.** Un solo proceso ejecuta las órdenes de una en una.
- **Alta disponibilidad.** No hay réplicas: si la máquina cae, SBD cae con ella hasta que vuelva o restaures una copia en otra.

**Para que vaya rápido:**

- Indexa las columnas por las que buscas (`indexar pedidos (cliente_id)`) y comprueba con `explicar` que se usan.
- Pide solo las columnas que necesitas (`mostrar a, b`) y pon `limite`.
- Agrupa varias escrituras en `empezar … confirmar`: se guardan de una vez.
