import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from pysnmp.hlapi.v3arch.asyncio import *

OIDS = {
    'descr': '1.3.6.1.2.1.2.2.1.2',
    'admin_status': '1.3.6.1.2.1.2.2.1.7',
    'oper_status': '1.3.6.1.2.1.2.2.1.8',
    'hc_in_octets': '1.3.6.1.2.1.31.1.1.1.6',
    'hc_out_octets': '1.3.6.1.2.1.31.1.1.1.10'
}

def init_db():
    """Inicializa la base de datos SQLite recomendada para entornos ligeros."""
    conn = sqlite3.connect('/home/admin/mininuc.db', timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('''
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
    conn.commit()
    return conn

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

async def main():
    ip = '192.168.153.1'
    community = 'public'
    interval = 15
    db_conn = init_db()

    sample1 = await poll_device(ip, community)
    t1 = time.monotonic()

    while True:
        await asyncio.sleep(interval)
        sample2 = await poll_device(ip, community)
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
        sample1 = sample2

if __name__ == '__main__':
    asyncio.run(main())
