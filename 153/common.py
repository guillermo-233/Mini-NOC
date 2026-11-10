"""Utilidades compartidas por todos los componentes del Mini-NOC.

Regla de oro: TODO instante guardado en la base de datos es una cadena UTC de ANCHO FIJO:
    2026-11-09T10:15:30.123Z
Al tener siempre la misma longitud y la misma zona, comparar cadenas (>=, <, BETWEEN, MAX)
equivale a comparar instantes, y los índices sobre estas columnas se pueden usar.
La conversión a Europe/Madrid se hace SOLO al mostrar (navegador).
"""
import ipaddress
import sqlite3
import time
from datetime import datetime, timedelta, timezone

DB_FILE = '/home/admin/mininuc.db'

# Redes internas de este entorno (las usan el recolector de flujos y la API de flujos).
INTERNAL_NETS = [ipaddress.ip_network(n) for n in ('192.168.153.0/24', '192.168.154.0/24')]


# ---------------------------------------------------------------- tiempo
def fmt(dt):
    """datetime (con zona) -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' en UTC."""
    return dt.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def utc_now():
    return fmt(datetime.now(timezone.utc))


def utc_ago(seconds):
    """Instante de hace `seconds` segundos, en el formato de la base de datos."""
    return fmt(datetime.now(timezone.utc) - timedelta(seconds=seconds))


def parse_iso(text):
    """Acepta '...Z', '...+00:00' o '... ' (espacio en lugar de T). Sin zona => UTC. Devuelve datetime UTC."""
    t = text.strip().replace(' ', 'T')
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize(text):
    """Cualquier ISO 8601 razonable -> formato fijo de la base de datos."""
    return fmt(parse_iso(text))


# ---------------------------------------------------------------- base de datos
SCHEMA = [
    '''CREATE TABLE IF NOT EXISTS snmp_data (
        timestamp TEXT, if_index TEXT, descr TEXT, admin_status TEXT, oper_status TEXT,
        in_bytes INTEGER, out_bytes INTEGER, in_bps REAL, out_bps REAL)''',
    '''CREATE TABLE IF NOT EXISTS syslog_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, source_ip TEXT, severity TEXT, message TEXT)''',
    '''CREATE TABLE IF NOT EXISTS flow_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, te TEXT, pr TEXT, sa TEXT, sp INTEGER, da TEXT, dp INTEGER,
        pkt INTEGER, byt INTEGER, in_if INTEGER, out_if INTEGER, xsa TEXT, xda TEXT,
        real_src TEXT, real_dst TEXT, src_internal INTEGER, dst_internal INTEGER,
        UNIQUE(ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda))''',
    '''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
        entity TEXT NOT NULL, message TEXT, start_time DATETIME NOT NULL, end_time DATETIME)''',
    '''CREATE TABLE IF NOT EXISTS collector_state (name TEXT PRIMARY KEY, value TEXT)''',
    '''CREATE TABLE IF NOT EXISTS users (
        name TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, created TEXT NOT NULL)''',
    # Latido de cada recolector: la usan la página de salud, la alerta de datos obsoletos
    # y los mensajes "por qué está vacío este panel".
    '''CREATE TABLE IF NOT EXISTS collector_status (
        name TEXT PRIMARY KEY, last_run TEXT, last_success TEXT,
        last_error TEXT, last_error_msg TEXT,
        errors INTEGER NOT NULL DEFAULT 0, items_total INTEGER NOT NULL DEFAULT 0)''',
    # Índices: cada uno sirve a una consulta concreta de la API o del motor de alertas.
    'CREATE INDEX IF NOT EXISTS idx_snmp_ts      ON snmp_data(timestamp)',
    'CREATE INDEX IF NOT EXISTS idx_snmp_if_ts   ON snmp_data(if_index, timestamp)',
    'CREATE INDEX IF NOT EXISTS idx_syslog_ts    ON syslog_data(timestamp)',
    'CREATE INDEX IF NOT EXISTS idx_flow_ts      ON flow_data(ts)',
    'CREATE INDEX IF NOT EXISTS idx_flow_rsrc_ts ON flow_data(real_src, ts)',
    'CREATE INDEX IF NOT EXISTS idx_flow_rdst_ts ON flow_data(real_dst, ts)',
    'CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts(status, rule_name)',
]


def connect(path=None):
    """Conexión con WAL (varios procesos escriben y la API lee a la vez) y espera ante bloqueos."""
    conn = sqlite3.connect(path or DB_FILE, timeout=30.0)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_schema(conn):
    for stmt in SCHEMA:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # Un índice sobre una columna que no existe en una tabla antigua no debe impedir arrancar.
            if not stmt.lstrip().upper().startswith('CREATE INDEX'):
                raise
    conn.commit()


# ---------------------------------------------------------------- latido de los recolectores
class Heartbeat:
    """Anota en collector_status que un recolector sigue vivo (ok) o ha fallado (error).

    min_interval limita las escrituras (el recolector de syslog itera cada segundo)."""

    def __init__(self, conn, name, min_interval=0):
        self.conn, self.name, self.min_interval = conn, name, min_interval
        self.pending = 0
        self._last = None      # None = aún no se ha escrito nunca: la primera vez siempre se escribe

    def _due(self):
        return self._last is None or time.monotonic() - self._last >= self.min_interval

    def ok(self, items=0):
        self.pending += items
        if not self._due():
            return
        now = utc_now()
        self.conn.execute(
            '''INSERT INTO collector_status (name, last_run, last_success, items_total)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run,
                   last_success = excluded.last_success,
                   items_total = items_total + excluded.items_total''',
            (self.name, now, now, self.pending))
        self.conn.commit()
        self.pending, self._last = 0, time.monotonic()

    def error(self, msg):
        if not self._due():
            return
        now = utc_now()
        self.conn.execute(
            '''INSERT INTO collector_status (name, last_run, last_error, last_error_msg, errors)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run,
                   last_error = excluded.last_error, last_error_msg = excluded.last_error_msg,
                   errors = errors + 1''',
            (self.name, now, now, str(msg)[:300]))
        self.conn.commit()
        self._last = time.monotonic()
