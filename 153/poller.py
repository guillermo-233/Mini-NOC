import asyncio
import re
import sqlite3
import time
from datetime import datetime, timezone
from pysnmp.hlapi.v3arch.asyncio import *

DB_FILE = '/home/admin/mininuc.db'
DEVICE_IP = '192.168.153.1'
COMMUNITY = 'public'
INTERVAL = 15

OIDS = {
    'descr': '1.3.6.1.2.1.2.2.1.2',
    'admin_status': '1.3.6.1.2.1.2.2.1.7',
    'oper_status': '1.3.6.1.2.1.2.2.1.8',
    'hc_in_octets': '1.3.6.1.2.1.31.1.1.1.6',
    'hc_out_octets': '1.3.6.1.2.1.31.1.1.1.10'
}
SYSUPTIME_OID = '1.3.6.1.2.1.1.3'   # sysUpTime.0 (TimeTicks, centésimas de segundo)


def ensure_schema(conn):
    """Crea las tablas y los índices que usan las consultas de la API."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS snmp_data (
            timestamp TEXT,
            if_index TEXT,
            descr TEXT,
            admin_status TEXT,
            oper_status TEXT,
            in_bytes INTEGER,
            out_bytes INTEGER,
            in_bps REAL,
            out_bps REAL
        )
    ''')
    # Última muestra de cada interfaz y consultas por rango de tiempo
    conn.execute('CREATE INDEX IF NOT EXISTS idx_snmp_if_ts ON snmp_data(if_index, timestamp)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_snmp_ts ON snmp_data(timestamp)')
    # Estado del dispositivo: una fila por router, con alcanzabilidad y sysUpTime
    conn.execute('''
        CREATE TABLE IF NOT EXISTS snmp_device (
            device TEXT PRIMARY KEY,
            last_attempt TEXT,
            last_success TEXT,
            reachable INTEGER,
            uptime_seconds REAL,
            consecutive_failures INTEGER,
            last_error TEXT
        )
    ''')
    conn.commit()


def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    ensure_schema(conn)
    return conn


UPTIME_RE = re.compile(r'^(?:(\d+) days?, )?(\d+):(\d{2}):(\d{2})(?:\.(\d{1,2}))?$')


def parse_uptime_ticks(text):
    """sysUpTime en centésimas de segundo. Acepta '123456' o '1 day, 2:03:04.05'. None si no se entiende."""
    text = str(text).strip()
    if text.isdigit():
        return int(text)
    m = UPTIME_RE.match(text)
    if not m:
        return None
    days, hours, minutes, seconds, centis = m.groups()
    total = (int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 100
    if centis:
        total += int(centis.ljust(2, '0'))
    return total


def record_device_status(conn, device, now_iso, reachable, uptime_seconds, error):
    """Guarda si el router respondió. Si falla, conserva el último sondeo correcto y el último uptime conocidos."""
    conn.execute('''
        INSERT INTO snmp_device (device, last_attempt, last_success, reachable, uptime_seconds, consecutive_failures, last_error)
        VALUES (:device, :now, :success, :reachable, :uptime, :failures, :error)
        ON CONFLICT(device) DO UPDATE SET
            last_attempt = excluded.last_attempt,
            last_success = CASE WHEN excluded.reachable = 1 THEN excluded.last_success ELSE snmp_device.last_success END,
            reachable = excluded.reachable,
            uptime_seconds = CASE WHEN excluded.reachable = 1 THEN excluded.uptime_seconds ELSE snmp_device.uptime_seconds END,
            consecutive_failures = CASE WHEN excluded.reachable = 1 THEN 0 ELSE snmp_device.consecutive_failures + 1 END,
            last_error = excluded.last_error
    ''', {'device': device, 'now': now_iso, 'success': now_iso if reachable else None,
          'reachable': 1 if reachable else 0, 'uptime': uptime_seconds,
          'failures': 0 if reachable else 1, 'error': error})
    conn.commit()


async def get_bulk_snmp(ip, community, base_oid):
    snmpEngine = SnmpEngine()
    results = {}
    target = await UdpTransportTarget.create((ip, 161))

    async for errorIndication, errorStatus, errorIndex, varBindTable in walk_cmd(
        snmpEngine, CommunityData(community), target, ContextData(),
        ObjectType(ObjectIdentity(base_oid)), lexicographicMode=False
    ):
        if errorIndication or errorStatus:
            break
        for varBind in varBindTable:
            oid, value = varBind
            idx = str(oid).split('.')[-1]
            results[idx] = value.prettyPrint()
    return results


async def poll_device(ip, community):
    device_data = {}
    for key, oid in OIDS.items():
        data = await get_bulk_snmp(ip, community, oid)
        for idx, value in data.items():
            if idx not in device_data:
                device_data[idx] = {}
            device_data[idx][key] = value
    return device_data


async def poll_uptime(ip, community):
    """sysUpTime del router en segundos, o None si no se pudo leer."""
    try:
        data = await get_bulk_snmp(ip, community, SYSUPTIME_OID)
    except Exception:
        return None
    for value in data.values():
        ticks = parse_uptime_ticks(value)
        if ticks is not None:
            return ticks / 100.0
    return None


async def poll_and_record_status(conn, ip, community, sample, error=None):
    """Registra si el router respondió en este sondeo (sample vacío = sin respuesta)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    if sample:
        uptime = await poll_uptime(ip, community)
        record_device_status(conn, ip, now_iso, True, uptime, None)
    else:
        record_device_status(conn, ip, now_iso, False, None, error or 'sin respuesta SNMP')


def _rate(bytes_1, bytes_2, interval_seconds):
    """bit/s entre dos lecturas de un contador.
    - Si el contador baja es un reset (reinicio del router): no hay tasa (None),
      para no dibujar picos gigantes ni tráfico cero falso.
    - interval_seconds debe ser el tiempo REAL transcurrido entre las dos lecturas."""
    if bytes_2 < bytes_1 or interval_seconds <= 0:
        return None
    return round((bytes_2 - bytes_1) * 8 / interval_seconds, 2)


def calculate_rates(sample1, sample2, interval_seconds):
    rates = {}
    for idx in sample1:
        if idx in sample2:
            in_bytes_1 = int(sample1[idx].get('hc_in_octets', 0))
            in_bytes_2 = int(sample2[idx].get('hc_in_octets', 0))
            out_bytes_1 = int(sample1[idx].get('hc_out_octets', 0))
            out_bytes_2 = int(sample2[idx].get('hc_out_octets', 0))

            rates[idx] = {
                'descr': sample2[idx].get('descr', ''),
                'admin_status': sample2[idx].get('admin_status', '0'),
                'oper_status': sample2[idx].get('oper_status', '0'),
                'in_bytes': in_bytes_2,
                'out_bytes': out_bytes_2,
                'in_bps': _rate(in_bytes_1, in_bytes_2, interval_seconds),
                'out_bps': _rate(out_bytes_1, out_bytes_2, interval_seconds)
            }
    return rates


async def safe_poll(ip, community):
    """Un fallo de red no debe tumbar el servicio: se registra como 'sin respuesta'."""
    try:
        return await poll_device(ip, community), None
    except Exception as e:
        return {}, f'error en el sondeo: {e}'


async def main():
    db_conn = init_db()

    sample1, error = await safe_poll(DEVICE_IP, COMMUNITY)
    t1 = time.monotonic()
    await poll_and_record_status(db_conn, DEVICE_IP, COMMUNITY, sample1, error)

    while True:
        await asyncio.sleep(INTERVAL)
        sample2, error = await safe_poll(DEVICE_IP, COMMUNITY)
        t2 = time.monotonic()

        # Se divide por el tiempo REAL entre lecturas, no por el intervalo nominal:
        # cada ciclo dura algo más de 15 s (espera + tiempo de las consultas SNMP).
        elapsed = t2 - t1
        rates = calculate_rates(sample1, sample2, elapsed)
        t1 = t2

        # Almacenamiento con marca de tiempo UTC
        timestamp = datetime.now(timezone.utc).isoformat()
        cursor = db_conn.cursor()

        for idx, data in rates.items():
            cursor.execute('''
                INSERT INTO snmp_data (timestamp, if_index, descr, admin_status, oper_status, in_bytes, out_bytes, in_bps, out_bps)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp, idx, data['descr'], data['admin_status'], data['oper_status'], data['in_bytes'], data['out_bytes'], data['in_bps'], data['out_bps']))

        db_conn.commit()
        await poll_and_record_status(db_conn, DEVICE_IP, COMMUNITY, sample2, error)
        sample1 = sample2


if __name__ == '__main__':
    asyncio.run(main())
