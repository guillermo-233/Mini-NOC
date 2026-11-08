import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOG_FILE = '/var/log/remote/192.168.153.1/syslog.log'
DB_FILE = '/home/admin/mininuc.db'
POLL_INTERVAL = 1             # segundos entre lecturas
MAX_READ_BYTES = 1024 * 1024  # máximo leído de una vez: memoria acotada

# El router manda su hora local sin zona horaria y rsyslog la etiqueta como +00:00 sin convertirla.
# Por eso se ignora la etiqueta y se interpreta la hora como hora de Madrid.
ROUTER_TZ = ZoneInfo('Europe/Madrid')

STATE_INODE = 'syslog_inode'
STATE_OFFSET = 'syslog_offset'

# Formato: <timestamp> <ip> <host> <facility.severity> <mensaje>
LOG_PATTERN = re.compile(
    r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\s+([\d\.]+)\s+\S+\s+'
    r'([a-zA-Z0-9]+\.[a-zA-Z0-9]+)\s+(.*)$'
)


def setup_database():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS syslog_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            source_ip TEXT,
            severity TEXT,
            message TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_syslog_ts ON syslog_data(timestamp)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS collector_state (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    return conn


def load_state(conn):
    rows = dict(conn.execute(
        'SELECT name, value FROM collector_state WHERE name IN (?, ?)',
        (STATE_INODE, STATE_OFFSET)).fetchall())
    if STATE_INODE not in rows:
        return None, 0
    return int(rows[STATE_INODE]), int(rows.get(STATE_OFFSET, 0))


def save_state(conn, inode, offset):
    conn.execute('INSERT OR REPLACE INTO collector_state (name, value) VALUES (?, ?)',
                 (STATE_INODE, str(inode)))
    conn.execute('INSERT OR REPLACE INTO collector_state (name, value) VALUES (?, ?)',
                 (STATE_OFFSET, str(offset)))


def to_utc(raw_timestamp):
    """Hora del router (Madrid, con etiqueta +00:00 errónea) -> UTC en ISO 8601."""
    dt = datetime.fromisoformat(raw_timestamp)
    return dt.replace(tzinfo=ROUTER_TZ).astimezone(timezone.utc).isoformat()


def parse_line(line):
    """Devuelve (timestamp_utc, source_ip, severity, message) o None si no encaja."""
    match = LOG_PATTERN.match(line.strip())
    if not match:
        return None
    raw_ts, source_ip, severity, message = match.groups()
    try:
        ts = to_utc(raw_ts)
    except ValueError:
        ts = datetime.now(timezone.utc).isoformat()
    return (ts, source_ip, severity, message)


def read_chunk(path, offset):
    """Lee líneas completas desde offset. Devuelve (líneas, nuevo_offset)."""
    with open(path, 'rb') as f:
        f.seek(offset)
        data = f.read(MAX_READ_BYTES)
    end = data.rfind(b'\n')
    if end == -1:
        return [], offset
    chunk = data[:end + 1]
    return chunk.decode('utf-8', errors='replace').splitlines(), offset + len(chunk)


def store(conn, lines, inode, offset):
    """Inserta las líneas y guarda el punto de lectura en la misma transacción."""
    rows = [r for r in (parse_line(l) for l in lines) if r]
    if rows:
        conn.executemany(
            'INSERT INTO syslog_data (timestamp, source_ip, severity, message) VALUES (?, ?, ?, ?)',
            rows)
    save_state(conn, inode, offset)
    conn.commit()
    return len(rows)


def find_rotated(inode):
    """Tras una rotación, el fichero antiguo pasa a ser syslog.log.1."""
    candidate = LOG_FILE + '.1'
    try:
        if os.stat(candidate).st_ino == inode:
            return candidate
    except FileNotFoundError:
        pass
    return None


def drain(conn, path, inode, offset):
    """Termina de leer un fichero ya rotado."""
    while True:
        lines, new_offset = read_chunk(path, offset)
        if new_offset == offset:
            return
        store(conn, lines, inode, new_offset)
        offset = new_offset


def step(conn, inode, offset):
    try:
        st = os.stat(LOG_FILE)
    except FileNotFoundError:
        return inode, offset

    if inode is None:
        # Primer arranque: empezar por el final, sin importar el histórico
        inode, offset = st.st_ino, st.st_size
        save_state(conn, inode, offset)
        conn.commit()
        return inode, offset

    if st.st_ino != inode:
        # Rotación: terminar el fichero antiguo y empezar el nuevo desde el principio
        old = find_rotated(inode)
        if old:
            drain(conn, old, inode, offset)
        inode, offset = st.st_ino, 0
        save_state(conn, inode, offset)
        conn.commit()
        print("Syslog: rotación detectada, leyendo el fichero nuevo.", flush=True)
    elif st.st_size < offset:
        # Fichero truncado (copytruncate)
        offset = 0
        save_state(conn, inode, offset)
        conn.commit()

    if st.st_size > offset:
        lines, new_offset = read_chunk(LOG_FILE, offset)
        if new_offset != offset:
            n = store(conn, lines, inode, new_offset)
            offset = new_offset
            if n:
                print(f"Syslog: {n} líneas nuevas guardadas.", flush=True)
    return inode, offset


def main():
    conn = setup_database()
    inode, offset = load_state(conn)
    print(f"Iniciando recolector de syslog en {LOG_FILE} (inodo={inode}, offset={offset})...", flush=True)
    while True:
        try:
            inode, offset = step(conn, inode, offset)
        except Exception as e:
            print(f"Error en el ciclo de syslog: {e}", flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
