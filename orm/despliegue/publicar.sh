#!/bin/bash
# Publica la biblioteca en https://sbd.serenaesteve.com/biblioteca/, conectada al SBD de producción.
#   sudo bash /root/sbd/orm/despliegue/publicar.sh
#
# 1. Para SBD y hace una copia completa de sus datos en /root/sbd/respaldos.
# 2. Actualiza SBD a la versión de /root/sbd, con el enlace a la biblioteca en el panel, y comprueba
#    que las bases siguen igual. Se corta unos segundos y se deshacen las transacciones abiertas.
# 3. Instala la biblioteca (orm/despliegue/desplegar.sh).
# 4. Añade /biblioteca/ a Apache. Si la configuración no es válida, deja la anterior.
# Ante cualquier fallo se para y lo dice; los datos no se modifican en ningún paso.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "✕ hay que ejecutarlo como root" >&2; exit 1; }
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
SBD=/srv/sbd/venv/bin/sbd
DATOS=/srv/sbd/datos
CONF=/etc/apache2/sites-available/sbd-ssl.conf
HTTPS=/usr/local/sbin/sbd-activar-https
DOMINIO=sbd.serenaesteve.com
DESTINO="$RAIZ/respaldos/$(date +%Y%m%d-%H%M%S)-antes-de-publicar-biblioteca"
cd /srv/sbd

# El tamaño no cuenta: al parar, SBD pasa su diario al .base y ocupa menos con los mismos datos
bases() { sudo -u sbd "$SBD" "$DATOS" -e 'bases' 2>/dev/null | grep -v biblioteca | awk -F'│' '{print $1 $2}' | sed 's/ *$//'; }

echo "· 1/4 copia de seguridad"
ANTES=$(bases)
[ -n "$ANTES" ] || { echo "✕ no se han podido leer las bases: no toco nada" >&2; exit 1; }
systemctl stop sbd
mkdir -p "$DESTINO"
chmod 700 "$DESTINO"
cp -a "$DATOS" "$DESTINO/datos"
cp -a /etc/systemd/system/sbd.service "$CONF" "$DESTINO/"
diff -r "$DATOS" "$DESTINO/datos" >/dev/null
echo "  $DESTINO"

echo "· 2/4 actualizar SBD"
/srv/sbd/venv/bin/pip install -q --force-reinstall --no-deps "$RAIZ"
"$SBD" servicio "$DATOS" --usuario-sistema sbd --puerto 8090 --proxy "$DOMINIO" --enlace Biblioteca=/biblioteca/ --instalar >/dev/null
systemctl restart sbd
for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:8090/salud >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8090/salud >/dev/null || { echo "✕ SBD no arranca: journalctl -u sbd. Copia en $DESTINO" >&2; exit 1; }
DESPUES=$(bases)
if [ "$ANTES" != "$DESPUES" ]; then
  echo "✕ las bases no coinciden con las de antes. Copia en $DESTINO" >&2
  diff <(echo "$ANTES") <(echo "$DESPUES") >&2 || true
  exit 1
fi
echo "  $("$SBD" version); las bases siguen igual"

echo "· 3/4 instalar la biblioteca"
bash "$RAIZ/orm/despliegue/desplegar.sh"
for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:8097/api/estado >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8097/api/estado >/dev/null || { echo "✕ la biblioteca no arranca: journalctl -u biblioteca" >&2; exit 1; }

echo "· 4/4 Apache"
if ! grep -q 'ProxyPass /biblioteca/' "$CONF"; then
  python3 - "$CONF" "$HTTPS" <<'PY'
import sys
BLOQUE = """RedirectMatch 301 ^/biblioteca$ /biblioteca/
    ProxyPass /biblioteca/ http://127.0.0.1:8097/
    ProxyPassReverse /biblioteca/ http://127.0.0.1:8097/
    ProxyPass /biblioteca !
    ProxyPass / http://127.0.0.1:8090/"""
for ruta in sys.argv[1:]:
    try:
        texto = open(ruta, encoding='utf-8').read()
    except FileNotFoundError:
        continue
    if 'ProxyPass /biblioteca/' not in texto:   # el segundo es el script que reescribe este fichero
        open(ruta, 'w', encoding='utf-8').write(texto.replace('ProxyPass / http://127.0.0.1:8090/', BLOQUE, 1))
PY
  if ! apache2ctl configtest 2>/dev/null; then
    cp -a "$DESTINO/$(basename "$CONF")" "$CONF"
    echo "✕ la configuración de Apache no era válida: he dejado la anterior" >&2
    exit 1
  fi
  systemctl reload apache2
fi
sleep 1

ver() { curl -s -o /dev/null -w '%{http_code}' --resolve "$DOMINIO:443:127.0.0.1" "https://$DOMINIO$1"; }
echo
echo "panel:          $(ver /)"
echo "biblioteca:     $(ver /biblioteca/)"
echo "sin barra:      $(ver /biblioteca) (301 lleva a la de con barra)"
echo "sus libros:     $(ver /biblioteca/api/libros)"
echo "salud:          $(curl -s --resolve "$DOMINIO:443:127.0.0.1" "https://$DOMINIO/salud")"
echo
echo "✓ https://$DOMINIO/biblioteca/   (copia de los datos en $DESTINO)"
