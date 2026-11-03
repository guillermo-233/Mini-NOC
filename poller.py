import asyncio
import sqlite3
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
    conn = sqlite3.connect('/home/admin/mininuc.db')
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

def calculate_rates(sample1, sample2, interval_seconds):
    rates = {}
    for idx in sample1:
        if idx in sample2:
            in_bytes_1 = int(sample1[idx].get('hc_in_octets', 0))
            in_bytes_2 = int(sample2[idx].get('hc_in_octets', 0))
            out_bytes_1 = int(sample1[idx].get('hc_out_octets', 0))
            out_bytes_2 = int(sample2[idx].get('hc_out_octets', 0))
            
            # Las instrucciones exigen que la velocidad marque 0 si el contador se reinicia (sin picos)
            in_bps = 0.0 if in_bytes_2 < in_bytes_1 else (in_bytes_2 - in_bytes_1) * 8 / interval_seconds
            out_bps = 0.0 if out_bytes_2 < out_bytes_1 else (out_bytes_2 - out_bytes_1) * 8 / interval_seconds
            
            rates[idx] = {
                'descr': sample2[idx].get('descr', ''),
                'admin_status': sample2[idx].get('admin_status', '0'),
                'oper_status': sample2[idx].get('oper_status', '0'),
                'in_bytes': in_bytes_2,
                'out_bytes': out_bytes_2,
                'in_bps': round(in_bps, 2),
                'out_bps': round(out_bps, 2)
            }
    return rates

async def main():
    ip = '192.168.153.1'
    community = 'public'
    interval = 15
    db_conn = init_db()
    
    sample1 = await poll_device(ip, community)
    
    while True:
        await asyncio.sleep(interval)
        sample2 = await poll_device(ip, community)
        rates = calculate_rates(sample1, sample2, interval)
        
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