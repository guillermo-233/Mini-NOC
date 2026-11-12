#!/usr/bin/env python3
"""Borra ficheros nfcapd con más de N días (por defecto 7, NOC_NFCAPD_DAYS) y carpetas vacías.
Uso: nfcapd_cleanup.py [--dry-run] [--days N] [--dir RUTA]"""
import argparse, os, re, sys
from datetime import datetime, timedelta, timezone

ROOT = '/var/cache/nfdump/mikrotik'
NAME = re.compile(r'^nfcapd\.(\d{12})$')


def cleanup(root, days, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    limit = (now - timedelta(days=days)).strftime('%Y%m%d%H%M')
    removed = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            m = NAME.match(f)
            if m and m.group(1) < limit:   # el nombre ya es UTC; el fichero en curso nunca es tan viejo
                removed += 1
                if not dry_run:
                    os.remove(os.path.join(dirpath, f))
    if not dry_run:
        for dirpath, _d, _f in os.walk(root, topdown=False):
            if dirpath != root and not os.listdir(dirpath):
                os.rmdir(dirpath)
    return removed


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--days', type=int, default=int(os.environ.get('NOC_NFCAPD_DAYS', 7)))
    ap.add_argument('--dir', default=ROOT)
    a = ap.parse_args()
    n = cleanup(a.dir, a.days, a.dry_run)
    print(f"{'Borraría' if a.dry_run else 'Borrados'} {n} ficheros nfcapd con más de {a.days} días")
    sys.exit(0)
