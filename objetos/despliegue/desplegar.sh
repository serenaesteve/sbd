#!/bin/bash
# Instala o actualiza el Baúl en la web, conectado al `sbd servidor` de esta máquina. Se puede repetir.
#   sudo bash objetos/despliegue/desplegar.sh         (desde la carpeta de SBD)
# No toca los datos: los objetos están en la base «objetos» del servidor de SBD.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
SBD=/srv/sbd/venv/bin/sbd
DATOS=/srv/sbd/datos

id objetos >/dev/null 2>&1 || useradd --system --home-dir /srv/objetos --no-create-home --shell /usr/sbin/nologin objetos
install -d -m 755 /srv/objetos
[ -d /srv/objetos/venv ] || python3 -m venv /srv/objetos/venv
/srv/objetos/venv/bin/pip install -q --force-reinstall --no-deps "$RAIZ"
/srv/objetos/venv/bin/pip install -q --force-reinstall "$RAIZ/objetos"

# La primera vez: su base, un usuario que solo llega a ella y su clave de API
if [ ! -f /srv/objetos/entorno ]; then
  CLAVE_USUARIO=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  (cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e "crear base objetos si no existe" >/dev/null)
  (cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e "crear usuario objetos_app clave \"$CLAVE_USUARIO\" puede escribir en objetos" >/dev/null)
  CLAVE=$(cd /srv/sbd && sudo -u sbd "$SBD" "$DATOS" -e 'crear clave api para objetos_app llamada "web del Baúl"' | grep -o 'sbd_k[^ ]*')
  [ -n "$CLAVE" ] || { echo "✕ no se ha podido crear la clave de API" >&2; exit 1; }
  ( umask 077; printf 'SBD_CLAVE=%s\n' "$CLAVE" > /srv/objetos/entorno )
fi

install -m 644 "$RAIZ/objetos/despliegue/objetos.service" /etc/systemd/system/objetos.service
systemctl daemon-reload
systemctl enable objetos >/dev/null 2>&1
systemctl restart objetos
echo "✓ Baúl en marcha en 127.0.0.1:8099"
