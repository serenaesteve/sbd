#!/bin/bash
# Instala o actualiza la biblioteca, conectada al `sbd servidor` de esta máquina. Se puede repetir.
#   sudo bash orm/despliegue/desplegar.sh         (desde la carpeta de SBD)
# No toca los datos: están en la base «biblioteca» del servidor de SBD.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
SBD=/srv/sbd/venv/bin/sbd
DATOS=/srv/sbd/datos

id biblioteca >/dev/null 2>&1 || useradd --system --home-dir /srv/biblioteca --no-create-home --shell /usr/sbin/nologin biblioteca
install -d -m 755 /srv/biblioteca
[ -d /srv/biblioteca/venv ] || python3 -m venv /srv/biblioteca/venv
/srv/biblioteca/venv/bin/pip install -q --force-reinstall --no-deps "$RAIZ"
/srv/biblioteca/venv/bin/pip install -q --force-reinstall "$RAIZ/orm"

# La primera vez: su base, un usuario que solo llega a ella y su clave de API
if [ ! -f /srv/biblioteca/entorno ]; then
  CLAVE_USUARIO=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  (cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e "crear base biblioteca si no existe" >/dev/null)
  (cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e "crear usuario biblioteca_app clave \"$CLAVE_USUARIO\" puede escribir en biblioteca" >/dev/null)
  CLAVE=$(cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e 'crear clave api para biblioteca_app llamada "web de la biblioteca"' | grep -o 'sbd_k[^ ]*')
  [ -n "$CLAVE" ] || { echo "✕ no se ha podido crear la clave de API" >&2; exit 1; }
  ( umask 077; printf 'SBD_CLAVE=%s\n' "$CLAVE" > /srv/biblioteca/entorno )
fi

install -m 644 "$RAIZ/orm/despliegue/biblioteca.service" /etc/systemd/system/biblioteca.service
systemctl daemon-reload
systemctl enable biblioteca >/dev/null 2>&1
systemctl restart biblioteca
echo "✓ biblioteca en marcha en 127.0.0.1:8097"
