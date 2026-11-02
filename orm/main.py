"""La biblioteca desde la línea de órdenes.

    python3 main.py iniciar                 crea las tablas y carga el ejemplo
    python3 main.py libros [texto]          busca libros
    python3 main.py socios
    python3 main.py alta-socio NOMBRE EMAIL
    python3 main.py alta-libro TITULO ISBN AUTOR [--precio 9.95] [--ejemplares 2] [--genero Novela]
    python3 main.py prestar SOCIO LIBRO [LIBRO…]
    python3 main.py devolver PRESTAMO
    python3 main.py prestamos
    python3 main.py informes                consultas con SQL

Añade --ver-sql (antes de la orden) para ver el SQL que genera el ORM.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # el SBD de la carpeta de arriba

from sqlalchemy import exc, select  # noqa: E402

from biblioteca import bd, ejemplo  # noqa: E402
from biblioteca.modelos import Libro, Socio  # noqa: E402
from biblioteca.servicio import Biblioteca, ErrorBiblioteca  # noqa: E402


def tabla(cabecera, filas):
    filas = [[('' if c is None else str(c)) for c in f] for f in filas]
    anchos = [max([len(h)] + [len(f[i]) for f in filas]) for i, h in enumerate(cabecera)]
    print('  '.join(h.ljust(a) for h, a in zip(cabecera, anchos)).rstrip())
    print('  '.join('─' * a for a in anchos))
    for f in filas:
        print('  '.join(c.ljust(a) for c, a in zip(f, anchos)).rstrip())
    print()


def orden_iniciar(b, args):
    if b.cuantos(Libro):
        print("La biblioteca ya tiene datos; no se ha cargado nada.")
        return
    ejemplo.cargar(b.sesion)
    print(f"Biblioteca lista: {b.cuantos(Libro)} libros y {b.cuantos(Socio)} socios.")


def orden_libros(b, args):
    tabla(['nº', 'título', 'autor', 'año', 'precio', 'libres', 'géneros'],
          [(x.id, x.titulo, x.autor.nombre, x.anio, x.precio, f'{x.disponibles}/{x.ejemplares}',
            ', '.join(g.nombre for g in x.generos)) for x in b.buscar_libros(args.texto)])


def orden_socios(b, args):
    socios = b.sesion.scalars(select(Socio).order_by(Socio.id))
    tabla(['nº', 'nombre', 'email', 'alta', 'libros'],
          [(s.id, s.nombre, s.email, s.alta, len(s.prestamos_abiertos())) for s in socios])


def orden_alta_socio(b, args):
    socio = b.alta_socio(args.nombre, args.email)
    print(f"Socio {socio.id} creado: {socio.nombre}.")


def orden_alta_libro(b, args):
    libro = b.alta_libro(args.titulo, args.isbn, args.autor, precio=args.precio, ejemplares=args.ejemplares,
                         generos=args.genero)
    print(f"Libro {libro.id} creado: {libro.titulo}.")


def orden_prestar(b, args):
    for p in b.prestar(args.socio, args.libros):
        print(f"Préstamo {p.id}: «{p.libro.titulo}» a {p.socio.nombre}, hasta el {p.vence}.")


def orden_devolver(b, args):
    p = b.devolver(args.prestamo)
    print(f"«{p.libro.titulo}» devuelto." + (f" Multa: {p.multa} €." if p.multa else ''))


def orden_prestamos(b, args):
    tabla(['nº', 'socio', 'libro', 'desde', 'vence'],
          [(p.id, p.socio.nombre, p.libro.titulo, p.fecha, p.vence) for p in b.prestamos_abiertos()])


def orden_informes(b, args):
    print('Libros más prestados')
    tabla(['título', 'autor', 'préstamos'], b.mas_prestados())
    print('Socios con libros vencidos')
    tabla(['nombre', 'email', 'vencidos', 'desde'], b.morosos())
    print('Multas por autor')
    tabla(['autor', 'multas', 'estado'], b.recaudado_por_autor())
    print('Socios que nunca han pedido nada')
    tabla(['nombre'], [(n,) for n in b.socios_sin_prestamos()])
    print('Los más caros de cada género')
    tabla(['género', 'título', 'precio', 'puesto'], b.ranking_por_genero())


def analizador():
    p = argparse.ArgumentParser(description='Biblioteca con SQLAlchemy sobre SBD')
    p.add_argument('--ver-sql', action='store_true', help='enseña el SQL que genera el ORM')
    o = p.add_subparsers(dest='orden', required=True)
    o.add_parser('iniciar').set_defaults(hacer=orden_iniciar)
    x = o.add_parser('libros')
    x.add_argument('texto', nargs='?', default='')
    x.set_defaults(hacer=orden_libros)
    o.add_parser('socios').set_defaults(hacer=orden_socios)
    x = o.add_parser('alta-socio')
    x.add_argument('nombre')
    x.add_argument('email')
    x.set_defaults(hacer=orden_alta_socio)
    x = o.add_parser('alta-libro')
    x.add_argument('titulo')
    x.add_argument('isbn')
    x.add_argument('autor')
    x.add_argument('--precio', default='0')
    x.add_argument('--ejemplares', type=int, default=1)
    x.add_argument('--genero', action='append', default=[])
    x.set_defaults(hacer=orden_alta_libro)
    x = o.add_parser('prestar')
    x.add_argument('socio', type=int)
    x.add_argument('libros', type=int, nargs='+')
    x.set_defaults(hacer=orden_prestar)
    x = o.add_parser('devolver')
    x.add_argument('prestamo', type=int)
    x.set_defaults(hacer=orden_devolver)
    o.add_parser('prestamos').set_defaults(hacer=orden_prestamos)
    o.add_parser('informes').set_defaults(hacer=orden_informes)
    return p


def main(argv=None):
    args = analizador().parse_args(argv)
    motor = bd.crear_motor(ver_sql=args.ver_sql)
    try:
        bd.preparar(motor)
        with bd.fabrica_sesiones(motor)() as sesion:
            args.hacer(Biblioteca(sesion), args)
        return 0
    except ErrorBiblioteca as e:
        print(f"No se puede: {e}", file=sys.stderr)
    except exc.IntegrityError as e:
        print(f"La base de datos lo rechaza: {e.orig}", file=sys.stderr)
    except exc.DBAPIError as e:
        print(f"Error de la base de datos: {e.orig}", file=sys.stderr)
    finally:
        motor.dispose()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
