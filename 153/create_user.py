#!/usr/bin/env python3
"""Gestión de usuarios del Mini-NOC (ejecutar en el contenedor, como root).

    python3 create_user.py add <usuario>       crea el usuario o cambia su contraseña (la pide por teclado)
    python3 create_user.py delete <usuario>    borra el usuario (su sesión deja de valer)
    python3 create_user.py list                lista los usuarios

La contraseña se pide sin eco y nunca queda en el historial de la shell ni en el repositorio.
"""
import getpass
import sys

import auth
import common


def main(argv):
    if len(argv) < 2 or argv[1] not in ('add', 'delete', 'list') or (argv[1] != 'list' and len(argv) != 3):
        print(__doc__)
        return 2
    conn = common.connect()
    common.init_schema(conn)
    cmd = argv[1]
    if cmd == 'list':
        for name, created in conn.execute('SELECT name, created FROM users ORDER BY name'):
            print(f'{name}\tcreado {created}')
        return 0
    name = argv[2]
    if cmd == 'delete':
        print('Borrado.' if auth.delete_user(conn, name) else 'Ese usuario no existe.')
        return 0
    p1 = getpass.getpass(f'Contraseña para {name}: ')
    if p1 != getpass.getpass('Repite la contraseña: '):
        print('Las contraseñas no coinciden.')
        return 1
    try:
        auth.set_user(conn, name, p1)
    except ValueError as e:
        print(f'Error: {e}')
        return 1
    print(f'Usuario {name} guardado.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
