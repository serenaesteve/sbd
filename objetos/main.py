"""La biblioteca de objetos desde la línea de órdenes.

    python3 objetos/main.py iniciar                       crea la biblioteca con el ejemplo
    python3 objetos/main.py fondo [texto]                 libros y revistas (con su OID)
    python3 objetos/main.py socios
    python3 objetos/main.py prestar SOCIO OID [OID…]      SOCIO es el OID del socio
    python3 objetos/main.py devolver PRESTAMO
    python3 objetos/main.py vencidos
    python3 objetos/main.py informe
    python3 objetos/main.py ver OID                       el objeto tal como está guardado, en YAML
    python3 objetos/main.py exportar copia.yaml           todo el baúl a un fichero YAML
    python3 objetos/main.py importar copia.yaml           de un YAML a una base vacía

Por defecto usa SBD (objetos/datos/biblioteca.base). Con --bd se elige otra base: un fichero
.yaml, sbd:///otra.base o sbd+https://:CLAVE@servidor/base.
"""
import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))  # el SBD de la carpeta de arriba
sys.path.insert(0, str(AQUI))

import baul  # noqa: E402
from biblioteca import ejemplo  # noqa: E402
from biblioteca.modelos import Libro, Prestamo, Publicacion, Socio  # noqa: E402
from biblioteca.servicio import ErrorBiblioteca, Servicio  # noqa: E402

POR_DEFECTO = f"sbd:///{AQUI / 'datos' / 'biblioteca.base'}"


def tabla(cabecera, filas):
    filas = [['' if c is None else str(c) for c in f] for f in filas]
    anchos = [max([len(h)] + [len(f[i]) for f in filas]) for i, h in enumerate(cabecera)]
    print('  '.join(h.ljust(a) for h, a in zip(cabecera, anchos)).rstrip())
    print('  '.join('─' * a for a in anchos))
    for f in filas:
        print('  '.join(c.ljust(a) for c, a in zip(f, anchos)).rstrip())
    print()


def objeto(bd, oid, clase):
    obj = bd.obtener(oid)
    if not isinstance(obj, clase):
        raise ErrorBiblioteca(f"el objeto {oid} no es un {clase.__name__.lower()}")
    return obj


def main(argv=None):
    p = argparse.ArgumentParser(description='Biblioteca de objetos sobre el Baúl')
    p.add_argument('--bd', default=POR_DEFECTO, help='dónde están los objetos')
    o = p.add_subparsers(dest='orden', required=True)
    o.add_parser('iniciar')
    o.add_parser('fondo').add_argument('texto', nargs='?', default='')
    o.add_parser('socios')
    x = o.add_parser('prestar')
    x.add_argument('socio', type=int)
    x.add_argument('oids', type=int, nargs='+')
    o.add_parser('devolver').add_argument('prestamo', type=int)
    o.add_parser('vencidos')
    o.add_parser('informe')
    o.add_parser('ver').add_argument('oid', type=int)
    o.add_parser('exportar').add_argument('fichero')
    o.add_parser('importar').add_argument('fichero')
    args = p.parse_args(argv)

    try:
        if args.orden == 'exportar':
            print(f"{baul.copiar(args.bd, args.fichero)} objetos copiados a {args.fichero}")
            return 0
        if args.orden == 'importar':
            print(f"{baul.copiar(args.fichero, args.bd)} objetos copiados desde {args.fichero}")
            return 0
        with baul.abrir(args.bd) as bd:
            s = Servicio(bd)
            if args.orden == 'iniciar':
                if bd.raiz.get('biblioteca'):
                    print("La biblioteca ya existe; no se ha cargado nada.")
                else:
                    ejemplo.cargar(bd)
                    print(f"Biblioteca lista en {bd.descripcion}: {bd.contar()} objetos.")
            elif args.orden == 'fondo':
                tabla(['oid', 'tipo', 'publicación', 'año', 'precio', 'libres', 'géneros'],
                      [(x.oid, type(x).__name__.lower(), x.describir(), x.anio, x.precio, f'{x.disponibles}/{x.ejemplares}',
                        ', '.join(sorted(x.generos))) for x in s.buscar(args.texto)])
            elif args.orden == 'socios':
                tabla(['oid', 'nombre', 'correo', 'ciudad', 'prestados', 'multas'],
                      [(x.oid, x.nombre, x.email, x.direccion.ciudad if x.direccion else '', len(x.abiertos()), s.multas_de(x))
                       for x in s.biblioteca.socios])
            elif args.orden == 'prestar':
                socio = objeto(bd, args.socio, Socio)
                for pr in s.prestar(socio, [objeto(bd, x, Publicacion) for x in args.oids]):
                    print(f"Préstamo {pr.oid}: «{pr.publicacion.titulo}» a {socio.nombre}, hasta el {pr.vence}.")
            elif args.orden == 'devolver':
                pr = s.devolver(objeto(bd, args.prestamo, Prestamo))
                print(f"«{pr.publicacion.titulo}» devuelto." + (f" Multa: {pr.multa} €." if pr.multa else ''))
            elif args.orden == 'vencidos':
                tabla(['oid', 'socio', 'publicación', 'venció'],
                      [(x.oid, x.socio.nombre, x.publicacion.titulo, x.vence) for x in s.vencidos()])
            elif args.orden == 'informe':
                print('Las más prestadas')
                tabla(['publicación', 'préstamos'], [(x.describir(), len(x.prestamos)) for x in s.mas_prestadas()])
                print('Por género')
                tabla(['género', 'publicaciones'], [(g, ', '.join(x.titulo for x in ps)) for g, ps in s.por_genero().items()])
                print('Autores y sus obras')
                tabla(['autor', 'país', 'obras'], [(a.nombre, a.pais, len(a.obras)) for a in
                                                   sorted({lib.autor for lib in bd.todos(Libro)}, key=lambda a: a.nombre)])
            elif args.orden == 'ver':
                obj = bd.obtener(args.oid)
                if obj is None:
                    raise ErrorBiblioteca(f"no hay ningún objeto {args.oid}")
                print(f"# {type(obj).__name__} {obj.oid} · versión {obj._version} · en {bd.descripcion}")
                print(bd.como_yaml(obj), end='')
        return 0
    except (ErrorBiblioteca, baul.ErrorBaul) as e:
        print(f"No se puede: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
