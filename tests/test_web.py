import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import sbd
from sbd.web.server import PanelServer


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servidor = sbd.servidor(self.tmp.name)
        sbd.Sesion(self.servidor).ejecutar("""
            crear base biblioteca
            crear tabla autor { id entero clave  nombre texto obligatorio unico }
            crear tabla libro { id entero clave  titulo texto  autor_id entero -> autor(id) }
            insertar en autor (nombre) ("Borges") ("Cortázar")
            insertar en libro (titulo, autor_id) ("Ficciones", 1) ("Rayuela", 2)
        """)
        self.server = PanelServer(('127.0.0.1', 0), self.servidor)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.servidor.cerrar()
        self.tmp.cleanup()

    def get(self, ruta):
        with urllib.request.urlopen(self.base + ruta) as resp:
            return json.load(resp)

    def ejecutar(self, orden, base='biblioteca', content_type='application/json', sesion='pestana-de-prueba-1'):
        cabeceras = {'Content-Type': content_type, 'SBD-Sesion': sesion}
        req = urllib.request.Request(self.base + '/api/ejecutar', headers=cabeceras,
                                     data=json.dumps({'base': base, 'orden': orden}).encode())
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_bases_y_estructura(self):
        datos = self.get('/api/bases')
        self.assertEqual([b['nombre'] for b in datos['bases']], ['biblioteca'])
        self.assertIn('buscar', datos['palabras'])
        base = self.get('/api/base?nombre=biblioteca')
        self.assertEqual([t['nombre'] for t in base['tablas']], ['autor', 'libro'])
        tabla = self.get('/api/tabla?base=biblioteca&nombre=libro')
        self.assertEqual(tabla['columnas'][2]['ref']['tabla'], 'autor')
        self.assertTrue(tabla['definicion'].startswith('crear tabla libro {'))

    def test_ejecutar_devuelve_cada_orden_con_su_texto(self):
        _, datos = self.ejecutar('buscar libro mostrar contar() como n\nbuscar libro ordenar titulo desc mostrar titulo')
        self.assertEqual([r['orden'] for r in datos['resultados']],
                         ['buscar libro mostrar contar() como n', 'buscar libro ordenar titulo desc mostrar titulo'])
        self.assertEqual(datos['resultados'][1]['filas'], [['Rayuela'], ['Ficciones']])
        self.assertFalse(datos['resultados'][0]['cambia'])

    def test_errores_y_ordenes_incompletas(self):
        _, datos = self.ejecutar('insertar en autor { nombre: "Borges" }')
        self.assertIn('no se puede repetir', datos['error'])
        _, datos = self.ejecutar('buscar libro {')
        self.assertTrue(datos['incompleta'])
        _, datos = self.ejecutar('calcular 1  buscar nada  calcular 2')
        self.assertEqual(len(datos['resultados']), 1)
        self.assertEqual(datos['orden_fallida'], 'buscar nada')

    def test_usar_y_crear_bases_cambian_la_base(self):
        _, datos = self.ejecutar('crear base revista')
        self.assertEqual(datos['base'], 'revista')
        _, datos = self.ejecutar('usar biblioteca', base='revista')
        self.assertEqual(datos['base'], 'biblioteca')
        status, datos = self.ejecutar('tablas', base='../fuera')
        self.assertEqual(status, 400)

    def test_transacciones_entre_peticiones(self):
        _, datos = self.ejecutar('empezar  vaciar libro')
        self.assertTrue(datos['transaccion'])
        _, datos = self.ejecutar('tablas', sesion='otra-pestana-distinta')  # otra sesión: espera y avisa
        self.assertIn('ocupada', datos['error'])
        _, datos = self.ejecutar('deshacer')
        self.assertFalse(datos['transaccion'])
        _, datos = self.ejecutar('buscar libro mostrar contar()')
        self.assertEqual(datos['resultados'][0]['filas'], [[2]])

    def test_volcado_sbd_se_puede_restaurar(self):
        with urllib.request.urlopen(self.base + '/api/volcar?base=biblioteca&formato=sbd') as resp:
            volcado = resp.read().decode()
        self.assertNotIn('CREATE', volcado)
        with tempfile.TemporaryDirectory() as otro:
            s = sbd.Sesion(sbd.servidor(otro))
            s.ejecutar(volcado)
            self.assertEqual(s.base, 'biblioteca')
            self.assertEqual(s.ejecutar('buscar libro ordenar id mostrar titulo').fetchall(), [('Ficciones',), ('Rayuela',)])
            s.servidor.cerrar()
        with urllib.request.urlopen(self.base + '/api/volcar?base=biblioteca&tabla=autor&formato=csv') as resp:
            self.assertIn('Cortázar', resp.read().decode('utf-8-sig'))

    def test_seguridad(self):
        status, _ = self.ejecutar('tablas', content_type='text/plain')
        self.assertEqual(status, 415)
        req = urllib.request.Request(self.base + '/api/bases', headers={'Host': 'atacante.example'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)


class TestPanelConUsuarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servidor = sbd.servidor(self.tmp.name)
        sbd.Sesion(self.servidor).ejecutar("""
            crear base tienda  crear tabla p { id entero clave  n texto }  insertar en p { n: "a" }
            crear base privada
            crear usuario jefa clave "clave de la jefa" puede todo
            crear usuario visitas clave "clave de visitas" puede leer en tienda
        """)
        self.server = PanelServer(('127.0.0.1', 0), self.servidor)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.servidor.cerrar()
        self.tmp.cleanup()

    def peticion(self, ruta, cuerpo=None, galleta=None):
        cabeceras = {'Content-Type': 'application/json'}
        if galleta:
            cabeceras['Cookie'] = galleta
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        req = urllib.request.Request(self.base + ruta, data=datos, headers=cabeceras)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp), resp.headers.get('Set-Cookie')
        except urllib.error.HTTPError as e:
            return e.code, json.load(e), None

    def entrar(self, usuario, clave):
        status, datos, galleta = self.peticion('/api/entrar', {'usuario': usuario, 'clave': clave})
        self.assertEqual(status, 200, datos)
        self.assertIn('HttpOnly', galleta)
        self.assertIn('SameSite=Strict', galleta)
        return galleta.split(';')[0]

    def test_hay_que_entrar(self):
        self.assertTrue(self.peticion('/api/estado')[1]['necesita_entrar'])
        status, datos, _ = self.peticion('/api/bases')
        self.assertEqual((status, datos['entrar']), (401, True))
        status, datos, _ = self.peticion('/api/entrar', {'usuario': 'jefa', 'clave': 'mal'})
        self.assertEqual(status, 401)

    def test_permisos_de_cada_usuario(self):
        visitas = self.entrar('visitas', 'clave de visitas')
        _, datos, _ = self.peticion('/api/bases', galleta=visitas)
        self.assertEqual([b['nombre'] for b in datos['bases']], ['tienda'])
        _, datos, _ = self.peticion('/api/ejecutar', {'base': 'tienda', 'orden': 'buscar p mostrar n'}, visitas)
        self.assertEqual(datos['resultados'][0]['filas'], [['a']])
        _, datos, _ = self.peticion('/api/ejecutar', {'base': 'tienda', 'orden': 'vaciar p'}, visitas)
        self.assertIn('solo puede leer', datos['error'])
        status, _, _ = self.peticion('/api/ejecutar', {'base': 'privada', 'orden': 'tablas'}, visitas)
        self.assertEqual(status, 403)
        self.peticion('/api/salir', {}, visitas)
        status, _, _ = self.peticion('/api/bases', galleta=visitas)
        self.assertEqual(status, 401)

    def test_las_claves_no_vuelven_al_navegador(self):
        jefa = self.entrar('jefa', 'clave de la jefa')
        _, datos, _ = self.peticion('/api/ejecutar', {'base': 'tienda',
                                                     'orden': 'crear usuario ana clave "secreto de ana" puede escribir'}, jefa)
        self.assertNotIn('secreto de ana', json.dumps(datos))
        self.assertIn('••••', datos['resultados'][0]['orden'])
        self.assertIsNotNone(self.servidor.verificar('ana', 'secreto de ana'))

    def test_frena_a_quien_prueba_claves(self):
        for _ in range(5):
            self.peticion('/api/entrar', {'usuario': 'jefa', 'clave': 'no es esta'})
        status, datos, _ = self.peticion('/api/entrar', {'usuario': 'jefa', 'clave': 'clave de la jefa'})
        self.assertEqual(status, 429)

    def test_detras_de_un_proxy(self):
        self.server.dominios = {'datos.miweb.es'}

        def entrar(ip, clave, host='datos.miweb.es'):
            req = urllib.request.Request(self.base + '/api/entrar', headers={
                'Content-Type': 'application/json', 'Host': host, 'X-Forwarded-Proto': 'https',
                'X-Forwarded-For': f'1.2.3.4, {ip}'}, data=json.dumps({'usuario': 'jefa', 'clave': clave}).encode())
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, resp.headers.get('Set-Cookie')
            except urllib.error.HTTPError as e:
                return e.code, None

        status, galleta = entrar('203.0.113.7', 'clave de la jefa')
        self.assertEqual(status, 200)
        self.assertIn('Secure', galleta)            # el proxy dice que llegó por HTTPS
        self.assertEqual(entrar('203.0.113.7', 'x', host='otro.example')[0], 403)
        for _ in range(5):
            entrar('198.51.100.9', 'no es esta')
        self.assertEqual(entrar('198.51.100.9', 'clave de la jefa')[0], 429)
        # quien prueba claves no bloquea a los demás, aunque todos lleguen desde el proxy
        self.assertEqual(entrar('203.0.113.7', 'clave de la jefa')[0], 200)

    def test_importar_subiendo_el_fichero(self):
        import base64
        jefa = self.entrar('jefa', 'clave de la jefa')
        csv = 'nombre;precio\nTé;4,5\nCafé;12\n'.encode('utf-8')
        _, datos, _ = self.peticion('/api/importar', {'base': 'tienda', 'tabla': 'productos', 'fichero': 'productos.csv',
                                                     'contenido': base64.b64encode(csv).decode()}, jefa)
        self.assertEqual(datos['resultados'][0]['orden'], 'importar "productos.csv" en productos')
        _, datos, _ = self.peticion('/api/ejecutar', {'base': 'tienda', 'orden': 'buscar productos mostrar nombre, precio'}, jefa)
        self.assertEqual(datos['resultados'][0]['filas'], [['Té', 4.5], ['Café', 12.0]])
        visitas = self.entrar('visitas', 'clave de visitas')
        _, datos, _ = self.peticion('/api/importar', {'base': 'tienda', 'tabla': 'x', 'fichero': 'x.csv',
                                                     'contenido': base64.b64encode(csv).decode()}, visitas)
        self.assertIn('solo puede leer', datos['error'])


class TestCuentas(unittest.TestCase):
    """Registro abierto: cada cuenta ve solo lo suyo y tiene límites."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servidor = sbd.servidor(self.tmp.name)
        sbd.Sesion(self.servidor).ejecutar("""
            crear base tienda  crear tabla p { id entero clave  n texto }  insertar en p { n: "secreto" }
            crear usuario jefa clave "clave de la jefa" puede todo
        """)
        self.server = PanelServer(('127.0.0.1', 0), self.servidor)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.servidor.cerrar()
        self.tmp.cleanup()

    def peticion(self, ruta, cuerpo=None, galleta=None, ip=None):
        cabeceras = {'Content-Type': 'application/json'}
        if galleta:
            cabeceras['Cookie'] = galleta
        datos = None if cuerpo is None else json.dumps(cuerpo).encode()
        req = urllib.request.Request(self.base + ruta, data=datos, headers=cabeceras)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp), (resp.headers.get('Set-Cookie') or '').split(';')[0]
        except urllib.error.HTTPError as e:
            return e.code, json.load(e), None

    def registrar(self, nombre, clave='una clave larga'):
        status, datos, galleta = self.peticion('/api/registrar', {'usuario': nombre, 'clave': clave})
        self.assertEqual(status, 200, datos)
        return galleta

    def orden(self, galleta, texto, base=None):
        return self.peticion('/api/ejecutar', {'base': base, 'orden': texto}, galleta)[1]

    def test_el_registro_empieza_cerrado_y_se_abre_desde_los_ajustes(self):
        self.assertFalse(self.peticion('/api/estado')[1]['registro'])
        status, datos, _ = self.peticion('/api/registrar', {'usuario': 'ana', 'clave': 'una clave larga'})
        self.assertEqual((status, datos['tipo']), (403, 'permiso'))
        jefa = self.peticion('/api/entrar', {'usuario': 'jefa', 'clave': 'clave de la jefa'})[2]
        self.assertTrue(self.peticion('/api/ajustes', {'registro': True}, jefa)[1]['registro'])
        self.assertTrue(self.peticion('/api/estado')[1]['registro'])
        ana = self.registrar('ana')
        self.assertEqual(self.peticion('/api/estado', galleta=ana)[1]['usuario']['permiso'], 'cuenta')
        # una cuenta no toca los ajustes, y los nombres reservados no valen
        self.assertEqual(self.peticion('/api/ajustes', {'registro': False}, ana)[0], 403)
        self.assertEqual(self.peticion('/api/registrar', {'usuario': 'admin', 'clave': 'una clave larga'})[0], 400)
        self.assertEqual(self.peticion('/api/registrar', {'usuario': 'ana', 'clave': 'otra clave larga'})[0], 400)

    def test_cada_cuenta_ve_y_toca_solo_lo_suyo(self):
        self.servidor.cambiar_ajustes(registro=True)
        ana, luis = self.registrar('ana'), self.registrar('luis')
        self.assertEqual(self.peticion('/api/bases', galleta=ana)[1]['bases'], [])
        self.assertEqual(self.peticion('/api/bases', galleta=ana)[1]['directorio'], '')  # la carpeta del servidor, no
        datos = self.orden(ana, 'crear base de_ana  crear tabla notas { id entero clave  t texto }  insertar en notas { t: "hola" }')
        self.assertNotIn('error', datos)
        self.assertEqual([b['nombre'] for b in self.peticion('/api/bases', galleta=ana)[1]['bases']], ['de_ana'])
        self.assertEqual([b['nombre'] for b in self.peticion('/api/bases', galleta=luis)[1]['bases']], [])
        for intento in ['buscar notas', 'usar de_ana', 'copiar base de_ana a robada', 'borrar base de_ana',
                        'historial de de_ana']:
            self.assertEqual(self.orden(luis, intento, 'de_ana' if intento == 'buscar notas' else None).get('tipo'), 'permiso', intento)
        for intento in ['usar tienda', 'usuarios', 'crear usuario x clave "una clave larga" puede todo',
                        'copiar base tienda a mia', 'restaurar base tienda del 2026-01-01 como mia', 'recuperar base tienda como mia']:
            self.assertEqual(self.orden(ana, intento).get('tipo'), 'permiso', intento)
        self.assertIn('ya existe', self.orden(ana, 'crear base tienda')['error'])
        # el historial y las copias de una cuenta solo tienen lo suyo
        filas = self.orden(ana, 'historial')['resultados'][0]['filas']
        self.assertTrue(filas and all(f[2] == 'de_ana' for f in filas))
        self.servidor.copia_diaria()
        self.assertEqual({f[1] for f in self.orden(ana, 'copias')['resultados'][0]['filas']}, {'de_ana'})
        # su base, la gestiona entera: copiar, borrar y recuperar
        self.assertNotIn('error', self.orden(ana, 'copiar base de_ana a de_ana2  borrar base de_ana2'))
        self.assertEqual([f[0] for f in self.orden(ana, 'papelera')['resultados'][0]['filas']], ['de_ana2'])
        self.assertEqual(self.orden(luis, 'papelera')['resultados'][0]['filas'], [])
        self.assertNotIn('error', self.orden(ana, 'recuperar base de_ana2'))
        # y la API también: una clave de ana solo llega a lo de ana
        clave = self.orden(ana, 'crear clave api para ana')['resultados'][0]['filas'][0][0]
        self.assertEqual(sbd.Cliente(self.base, clave, base='de_ana').consultar('buscar notas mostrar t'), [{'t': 'hola'}])
        with self.assertRaises(sbd.PermisoDenegado):
            sbd.Cliente(self.base, clave, base='tienda').consultar('buscar p')

    def test_no_se_reutiliza_el_nombre_de_una_base_ajena(self):
        # Si no, la segunda cuenta recuperaría de la papelera (o de las copias) la base de la primera
        self.servidor.cambiar_ajustes(registro=True)
        ana, luis = self.registrar('ana'), self.registrar('luis')
        self.orden(ana, 'crear base notas  crear tabla x { id entero clave }  borrar base notas')
        self.assertIn('ha existido', self.orden(luis, 'crear base notas')['error'])
        self.assertEqual(self.orden(luis, 'recuperar base notas').get('tipo'), 'permiso')
        self.assertNotIn('error', self.orden(ana, 'recuperar base notas'))

    def test_limites_de_cada_cuenta(self):
        self.servidor.cambiar_ajustes(registro=True, max_bases=1, max_filas=3)
        ana = self.registrar('ana')
        self.orden(ana, 'crear base una  crear tabla t { id entero clave }')
        self.assertIn('máximo', self.orden(ana, 'crear base otra')['error'])
        self.assertNotIn('error', self.orden(ana, 'insertar en t {} {} {}', 'una'))
        datos = self.orden(ana, 'insertar en t {}', 'una')
        self.assertIn('filas', datos['error'])
        self.assertNotIn('error', self.orden(ana, 'borrar de t donde id = 1  insertar en t {}', 'una'))
        uso = self.peticion('/api/cuenta', galleta=ana)[1]
        self.assertEqual((uso['bases'], uso['filas'], uso['max_filas']), (1, 3, 3))
        # quien administra no tiene límites
        jefa = self.peticion('/api/entrar', {'usuario': 'jefa', 'clave': 'clave de la jefa'})[2]
        self.assertNotIn('error', self.orden(jefa, 'crear base sin_limite'))

    def test_frena_a_quien_crea_muchas_cuentas(self):
        self.servidor.cambiar_ajustes(registro=True)
        for n in ('a1', 'a2', 'a3'):
            self.registrar(n)
        status, datos, _ = self.peticion('/api/registrar', {'usuario': 'a4', 'clave': 'una clave larga'})
        self.assertEqual(status, 429)

    def test_las_bases_sin_uso_salen_de_la_memoria(self):
        self.servidor.cambiar_ajustes(registro=True)
        ana = self.registrar('ana')
        self.orden(ana, 'crear base mia  crear tabla t { id entero clave }  insertar en t {} {}')
        # Garantizar inactividad incluso si el reloj monotónico tiene poca resolución.
        self.servidor._uso = {nombre: instante - 1 for nombre, instante in self.servidor._uso.items()}
        self.servidor.soltar_inactivas(0)
        self.assertEqual(self.servidor._abiertas, {})
        self.peticion('/api/bases', galleta=ana)   # listar las suyas no abre las de los demás
        self.assertNotIn('tienda', self.servidor._abiertas)
        self.assertEqual(self.orden(ana, 'buscar t mostrar contar()', 'mia')['resultados'][0]['filas'], [[2]])


if __name__ == '__main__':
    unittest.main()
