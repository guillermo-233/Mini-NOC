import ipaddress
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta

DB_FILE = '/home/admin/mininuc.db'
NFDUMP_DIR = '/var/cache/nfdump/mikrotik'
POLL_INTERVAL = 30          # segundos entre ciclos cuando no queda nada pendiente
BACKFILL_MINUTES = 60       # primer arranque: solo se leen los últimos N minutos
MAX_FILES_PER_CYCLE = 60    # tope de ficheros por ciclo (evita ráfagas de E/S tras una parada larga)
BATCH_SIZE = 1000           # filas por transacción: la memoria queda acotada

NFDUMP_FMT = 'csv:%ts,%te,%pr,%sa,%sp,%da,%dp,%pkt,%byt,%in,%out,%xsa,%xda'
FILE_RE = re.compile(r'^nfcapd\.(\d{12})$')                 # nfcapd.YYYYMMDDhhmm (el fichero .current no encaja)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')

PROTO_MAP = {'6': 'TCP', '17': 'UDP', '1': 'ICMP'}

# Redes internas de este entorno. nfdump da la dirección original (sa/da) y la traducida por NAT (xsa/xda);
# el host "real" de cada extremo es la que pertenece a estas redes, no la dirección WAN del router.
INTERNAL_NETS = [ipaddress.ip_network(n) for n in ('192.168.153.0/24', '192.168.154.0/24')]

INSERT_SQL = '''
    INSERT OR IGNORE INTO flow_data
    (ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
'''


def init_db():
    """Crea las tablas. UNIQUE desduplica registros idénticos; collector_state guarda por dónde vamos."""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS flow_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            te TEXT,
            pr TEXT,
            sa TEXT,
            sp INTEGER,
            da TEXT,
            dp INTEGER,
            pkt INTEGER,
            byt INTEGER,
            in_if INTEGER,
            out_if INTEGER,
            xsa TEXT,
            xda TEXT,
            real_src TEXT,
            real_dst TEXT,
            UNIQUE(ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS collector_state (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    return conn


def get_state(conn, name):
    row = conn.execute('SELECT value FROM collector_state WHERE name = ?', (name,)).fetchone()
    return row[0] if row else None


def set_state(conn, name, value):
    conn.execute('INSERT OR REPLACE INTO collector_state (name, value) VALUES (?, ?)', (name, value))


def is_internal(ip):
    try:
        return any(ipaddress.ip_address(str(ip).strip()) in net for net in INTERNAL_NETS)
    except ValueError:
        return False


def resolve_real_ip(ip, xip):
    """Host interno de un extremo del flujo, teniendo en cuenta el NAT.
    - Salida (SNAT/masquerade): ip = host interno, xip = dirección WAN del router.
    - Entrada (DNAT):           ip = dirección WAN del router, xip = host interno.
    Se elige la dirección que está en las redes internas; si ninguna lo está, se deja la original."""
    if is_internal(ip):
        return ip
    if is_internal(xip):
        return xip
    return ip


def parse_line(line):
    """Convierte una línea CSV de nfdump en una tupla lista para insertar.
    Devuelve None para cabeceras, resúmenes, líneas vacías o malformadas."""
    parts = [p.strip() for p in line.strip().split(',')]
    if len(parts) != 13 or not DATE_RE.match(parts[0]):
        return None
    ts, te, pr_raw, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda = parts
    try:
        sp, dp, pkt, byt = int(sp), int(dp), int(pkt), int(byt)
        in_if, out_if = int(in_if), int(out_if)
    except ValueError:
        return None
    pr = PROTO_MAP.get(pr_raw, pr_raw)
    real_src = resolve_real_ip(sa, xsa)
    real_dst = resolve_real_ip(da, xda)
    return (ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst)


def new_files(after_stamp):
    """Ficheros nfcapd completos con marca de tiempo posterior a after_stamp, ordenados."""
    found = []
    for root, _dirs, files in os.walk(NFDUMP_DIR):
        for name in files:
            m = FILE_RE.match(name)
            if m and m.group(1) > after_stamp:
                found.append((m.group(1), os.path.join(root, name)))
    found.sort()
    return found


def insert_batch(conn, rows):
    cur = conn.executemany(INSERT_SQL, rows)
    conn.commit()
    return cur.rowcount


def process_file(conn, path, stamp):
    """Lee UN fichero nfcapd en streaming (línea a línea) e inserta por lotes."""
    cmd = ['nfdump', '-r', path, '-q', '-N', '-o', NFDUMP_FMT]
    inserted = 0
    batch = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for line in proc.stdout:
            row = parse_line(line)
            if row:
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    inserted += insert_batch(conn, batch)
                    batch = []
    finally:
        proc.stdout.close()
        rc = proc.wait()
    if batch:
        inserted += insert_batch(conn, batch)
    if rc != 0:
        print(f"IPFIX: nfdump devolvió {rc} al leer {path}", flush=True)
    # El fichero queda marcado como procesado; si se cae a mitad, se relee y UNIQUE evita duplicados
    set_state(conn, 'flow_last_file', stamp)
    conn.commit()
    return inserted


def collect_flows(conn):
    """Procesa los ficheros nuevos. Devuelve True si quedan más pendientes."""
    last = get_state(conn, 'flow_last_file')
    if last is None:
        last = (datetime.now() - timedelta(minutes=BACKFILL_MINUTES)).strftime('%Y%m%d%H%M')

    pending = new_files(last)
    todo = pending[:MAX_FILES_PER_CYCLE]
    total = 0
    for stamp, path in todo:
        total += process_file(conn, path, stamp)

    if todo:
        print(f"IPFIX: {len(todo)} ficheros leídos, {total} flujos nuevos "
              f"(último: {todo[-1][0]}, pendientes: {len(pending) - len(todo)}).", flush=True)
    return len(pending) > len(todo)


def main():
    conn = init_db()
    print("Iniciando recolector IPFIX (lectura incremental por fichero)...", flush=True)
    while True:
        try:
            more = collect_flows(conn)
        except Exception as e:
            print(f"Error en el ciclo IPFIX: {e}", flush=True)
            more = False
        time.sleep(1 if more else POLL_INTERVAL)


if __name__ == '__main__':
    main()
