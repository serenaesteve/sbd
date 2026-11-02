"""El comando «sbd».

    sbd [datos] [-b base] [-e orden] [-f script.sbd]     consola (o conectada al servidor en marcha)
    sbd --servidor https://… --clave sbd_…               consola sobre un servidor remoto
    sbd servidor [datos] [--puerto 8080] …               panel web y API para aplicaciones
    sbd servicio [datos] [--instalar]                    servicio del sistema (systemd)
    sbd version
"""
import os
import shutil
import subprocess
import sys

from . import __version__

UNIDAD = '/etc/systemd/system/sbd.service'


def servicio(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='sbd servicio',
                                     description='Genera (y con --instalar, activa) el servicio de systemd de SBD')
    parser.add_argument('datos', nargs='?', default='datos', help='carpeta de las bases')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--puerto', type=int, default=8080)
    parser.add_argument('--usuario-sistema', default=os.environ.get('USER', 'root'),
                        help='usuario del sistema que ejecuta el servidor')
    parser.add_argument('--certificado')
    parser.add_argument('--clave-privada')
    parser.add_argument('--origenes', default='')
    parser.add_argument('--proxy', default='', metavar='DOMINIOS', help='dominios que atiende el proxy con HTTPS')
    parser.add_argument('--enlace', action='append', default=[], metavar='TEXTO=/RUTA',
                        help='otra página de esta web que enlazar desde el panel')
    parser.add_argument('--instalar', action='store_true',
                        help=f'escribe {UNIDAD} y lo arranca (necesita ser root)')
    args = parser.parse_args(argv)
    datos = os.path.abspath(args.datos)
    paquete = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    instalado = any(paquete.startswith(p) for p in sys.path if 'site-packages' in p or 'dist-packages' in p)
    opciones = [f'--host {args.host}', f'--puerto {args.puerto}']
    if args.certificado:
        opciones.append(f'--certificado {os.path.abspath(args.certificado)}')
    if args.clave_privada:
        opciones.append(f'--clave-privada {os.path.abspath(args.clave_privada)}')
    if args.origenes:
        opciones.append(f'--origenes {args.origenes}')
    if args.proxy:
        opciones.append(f'--proxy {args.proxy}')
    opciones += [f'--enlace {e}' for e in args.enlace]
    escritura = [datos] + ([os.path.dirname(os.path.abspath(args.certificado))] if args.certificado else [])
    unidad = f"""[Unit]
Description=SBD, servidor de bases de datos (panel y API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={args.usuario_sistema}
WorkingDirectory={os.path.dirname(datos)}
{'' if instalado else f'Environment=PYTHONPATH={paquete}{chr(10)}'}ExecStart={sys.executable} -m sbd servidor {datos} {' '.join(opciones)}
Restart=on-failure
RestartSec=3
# Si se para, deshace lo que no estuviera confirmado y compacta los diarios.
KillSignal=SIGINT
TimeoutStopSec=30
# Endurecimiento: solo puede escribir en la carpeta de datos.
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths={' '.join(dict.fromkeys(escritura))}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
    if not args.instalar:
        print(unidad)
        print(f"# Para instalarlo: sudo {sys.executable} -m sbd servicio {' '.join(argv)} --instalar", file=sys.stderr)
        return 0
    if os.geteuid() != 0:
        print("✕ --instalar necesita ejecutarse como root (sudo)", file=sys.stderr)
        return 1
    if not shutil.which('systemctl'):
        print("✕ este sistema no usa systemd: arranca el servidor con tu gestor de servicios", file=sys.stderr)
        return 1
    with open(UNIDAD, 'w', encoding='utf-8') as f:
        f.write(unidad)
    for orden in (['systemctl', 'daemon-reload'], ['systemctl', 'enable', '--now', 'sbd']):
        subprocess.run(orden, check=True)
    print(f"Servicio instalado en {UNIDAD} y en marcha.\n"
          "  Estado:     systemctl status sbd\n"
          "  Registro:   journalctl -u sbd -f\n"
          "  Reiniciar:  systemctl restart sbd")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'servidor':
        from .web.server import main as servidor
        return servidor(argv[1:])
    if argv and argv[0] == 'servicio':
        return servicio(argv[1:])
    if argv and argv[0] in ('version', '--version', '-V'):
        print(f"SBD {__version__}")
        return 0
    from .repl import main as consola
    return consola(argv)


if __name__ == '__main__':
    raise SystemExit(main())
