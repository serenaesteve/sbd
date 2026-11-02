"""Comprueba lo que dice RA2-Acceso-a-Datos.md: cada apartado, ejecutándose de verdad.

    python3 ejemplos/pruebas_ra2.py

Trabaja en una carpeta temporal: no toca ninguna base real ni necesita instalar nada.
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sbd  # noqa: E402
from sbd.web.server import PanelServer  # noqa: E402

fallos = 0


def comprobar(texto, obtenido, esperado):
    global fallos
    bien = obtenido == esperado
    fallos += not bien
    print(f"  {'bien' if bien else 'MAL '}  {texto}: {obtenido!r}" + ('' if bien else f" (se esperaba {esperado!r})"))


def falla_con(funcion, tipo):
    try:
        funcion()
    except tipo as e:
        return getattr(e, 'tipo', None) or type(e).__name__
    return 'no ha fallado'


with tempfile.TemporaryDirectory() as tmp:
    print('b) Embebido: dentro del programa, y un cerrojo impide abrirlo dos veces')
    ruta = os.path.join(tmp, 'notas.base')
    db = sbd.conectar(ruta)
    db.execute('crear tabla notas { id entero clave  texto texto }')
    db.execute('insertar en notas { texto: ? }', ['hola'])
    comprobar('otro programa no puede abrirla', falla_con(lambda: sbd.conectar(ruta), sbd.SBDError), 'SBDError')

    print('g) Objetos con el resultado')
    r = db.execute('buscar notas')
    comprobar('columnas', r.columns, ['id', 'texto'])
    comprobar('filas', r.rows, [(1, 'hola')])
    comprobar('diccionarios', r.dicts(), [{'id': 1, 'texto': 'hola'}])

    print('i) Al cerrar se suelta el cerrojo y los datos quedan guardados')
    db.close()
    with sbd.conectar(ruta) as otra:
        comprobar('se vuelve a abrir', otra.query('buscar notas'), [{'id': 1, 'texto': 'hola'}])

    print('b, d) Independiente: un servidor aparte, con clave de API')
    srv = sbd.servidor(os.path.join(tmp, 'datos'))
    admin = sbd.Sesion(srv)
    admin.ejecutar('crear base tienda  crear usuario web clave "una clave de prueba" puede escribir en tienda')
    clave = admin.ejecutar('crear clave api para web').rows[0][0]
    http = PanelServer(('127.0.0.1', 0), srv)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{http.server_address[1]}'
    c = sbd.conectar_servidor(url, clave=clave, base='tienda')
    comprobar('el servidor responde', c.salud()['ok'], True)
    mala = sbd.conectar_servidor(url, clave='sbd_kmala_x', base='tienda')
    comprobar('una clave falsa no entra', falla_con(lambda: mala.consultar('tablas'), sbd.ErrorServidor), 'clave')

    print('e) Definir la estructura')
    c.todas('''crear tabla clientes { id entero clave  nombre texto(60) obligatorio  email texto unico }
               crear tabla pedidos { id entero clave  cliente_id entero obligatorio -> clientes(id)  total dinero = 0 }
               indexar pedidos (cliente_id)
               crear tabla cuentas { id entero clave  saldo dinero = 0 comprobar saldo >= 0 }
               insertar en cuentas { saldo: 100 } { saldo: 0 }''')
    comprobar('tablas creadas', sorted(f['nombre'] for f in c.consultar('tablas')), ['clientes', 'cuentas', 'pedidos'])

    print('f, h) Modificar y consultar')
    ana = c.ejecutar('insertar en clientes { nombre: ?, email: ? } devolver id', ['Ana', 'ana@ejemplo.es']).escalar()
    c.ejecutar('insertar en pedidos { cliente_id: ?, total: ? } { cliente_id: ?, total: ? }', [ana, '10.50', ana, '20'])
    comprobar('filas cambiadas', c.ejecutar('actualizar pedidos poner total = total + 1 donde cliente_id = ?', [ana]).filas_afectadas, 2)
    gasto = c.consultar('''buscar pedidos como p {
      unir clientes como c en c.id = p.cliente_id
      agrupar c.nombre
      mostrar c.nombre, contar() como pedidos, sumar(p.total) como gasto }''')
    comprobar('unión y agrupación', [(g['nombre'], g['pedidos'], str(g['gasto'])) for g in gasto], [('Ana', 2, '32.50')])

    print('a) Inyección: pegar el texto del usuario frente a pasarlo como parámetro')
    c.todas('crear tabla c { id entero clave  nombre texto }\ninsertar en c { nombre: "Ana" } { nombre: "Luis" }')
    malo = 'x" vaciar c --'
    c.consultar('buscar c donde nombre = ?', [malo])
    comprobar('con «?» no pasa nada: filas', c.consultar('buscar c mostrar contar() como n')[0]['n'], 2)
    c.todas(f'buscar c donde nombre = "{malo}"')
    comprobar('pegándolo, se vacía la tabla: filas', c.consultar('buscar c mostrar contar() como n')[0]['n'], 0)

    saldos = lambda: [str(f['saldo']) for f in c.consultar('buscar cuentas ordenar id mostrar saldo')]

    print('j) Transacción: si falla una orden, se deshacen las dos')
    def transaccion_que_falla():
        with c.transaccion() as t:
            t.ejecutar('actualizar cuentas poner saldo = saldo - 30 donde id = 1')
            t.ejecutar('actualizar cuentas poner saldo = saldo - 30 donde id = 2')
    comprobar('la segunda incumple «comprobar»', falla_con(transaccion_que_falla, sbd.ErrorServidor), 'orden')
    comprobar('los saldos no han cambiado', saldos(), ['100.00', '0.00'])
    with c.transaccion() as t:
        t.ejecutar('actualizar cuentas poner saldo = saldo - 30 donde id = 1')
        t.ejecutar('actualizar cuentas poner saldo = saldo + 30 donde id = 2')
    comprobar('y si todo va bien, se guardan', saldos(), ['70.00', '30.00'])

    print('k) Procedimiento almacenado: se guarda en la base y se ejecuta con parámetros')
    c.ejecutar('''crear procedimiento transferir(origen, destino, cantidad) {
      actualizar cuentas poner saldo = saldo - :cantidad donde id = :origen
      actualizar cuentas poner saldo = saldo + :cantidad donde id = :destino
      buscar cuentas donde id en [:origen, :destino] ordenar id mostrar saldo
    }''')
    comprobar('está guardado en la base', c.consultar('procedimientos')[0]['procedimiento'], 'transferir')
    filas = c.consultar('ejecutar transferir(?, ?, ?)', [1, 2, '20'])
    comprobar('ejecutarlo devuelve filas', [str(f['saldo']) for f in filas], ['50.00', '50.00'])
    comprobar('si falla, no se aplica nada', falla_con(lambda: c.ejecutar('ejecutar transferir(1, 2, 999)'), sbd.ErrorServidor), 'orden')
    comprobar('los saldos no han cambiado', saldos(), ['50.00', '50.00'])

    print('k) Y sigue ahí después de apagar el servidor')
    http.shutdown()
    http.server_close()
    admin.cerrar()
    srv.cerrar()
    with sbd.servidor(os.path.join(tmp, 'datos')) as srv2:
        s = sbd.Sesion(srv2, 'tienda')
        comprobar('tras reiniciar', [str(f[0]) for f in s.ejecutar('ejecutar transferir(1, 2, 5)').rows], ['45.00', '55.00'])

print()
print('Todo correcto.' if not fallos else f'{fallos} comprobaciones han fallado.')
sys.exit(1 if fallos else 0)
