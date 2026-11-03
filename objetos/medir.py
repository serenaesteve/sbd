"""Cuánto tarda el Baúl con muchos objetos, en un fichero de SBD y por un servidor de SBD.

    python3 objetos/medir.py            (5000 objetos; python3 objetos/medir.py 20000 para más)

Trabaja en una carpeta temporal y con un servidor de SBD propio: no toca ninguna base real.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

import baul  # noqa: E402
import sbd  # noqa: E402
from sbd.web.server import PanelServer  # noqa: E402


class Ficha(baul.Persistente):
    def __init__(self, n, anterior):
        self.n, self.titulo, self.anterior, self.etiquetas = n, f'ficha {n}', anterior, ['a', 'b', n % 7]


def ms(desde):
    return f'{(time.perf_counter() - desde) * 1000:.0f} ms'


N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
with tempfile.TemporaryDirectory() as tmp:
    srv = sbd.servidor(os.path.join(tmp, 'datos'))
    admin = sbd.Sesion(srv)
    admin.ejecutar('crear base objetos  crear usuario app clave "clave de la aplicacion" puede escribir en objetos')
    clave = admin.ejecutar('crear clave api para app').rows[0][0]
    http = PanelServer(('127.0.0.1', 0), srv)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    urls = {'fichero de SBD': f'sbd:///{tmp}/medir.base',
            'servidor de SBD': f"sbd+http://:{clave}@127.0.0.1:{http.server_address[1]}/objetos"}
    print(f'{N} objetos encadenados (cada uno apunta al anterior)')
    for nombre, url in urls.items():
        with baul.abrir(url) as bd:
            anterior = None
            for i in range(N):
                anterior = Ficha(i, anterior)
            bd.raiz['ultima'] = anterior
            bd.confirmar()
        t = time.perf_counter()
        bd = baul.abrir(url)
        abrir = ms(t)
        t = time.perf_counter()
        bd.raiz['ultima'].titulo
        uno = ms(t)
        t = time.perf_counter()
        bd.refrescar()
        refrescar = ms(t)
        t = time.perf_counter()
        bd.cargar()
        todo = ms(t)
        bd.cerrar()
        print(f'  {nombre}: abrir {abrir} · usar un objeto {uno} · refrescar sin cambios {refrescar} · cargarlos todos {todo}')
    http.shutdown()
    http.server_close()
    admin.cerrar()
    srv.cerrar()
