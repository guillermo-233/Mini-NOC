import asyncio
import time
from pysnmp.hlapi.v3arch.asyncio import *

# OIDs de ifTable e ifXTable
OIDS = {
    'descr': '1.3.6.1.2.1.2.2.1.2',
    'admin_status': '1.3.6.1.2.1.2.2.1.7',
    'oper_status': '1.3.6.1.2.1.2.2.1.8',
    'hc_in_octets': '1.3.6.1.2.1.31.1.1.1.6',
    'hc_out_octets': '1.3.6.1.2.1.31.1.1.1.10'
}

async def get_bulk_snmp(ip, community, base_oid):
    """Realiza un SNMP WALK asíncrono sobre un OID base."""
    snmpEngine = SnmpEngine()
    results = {}
    
    target = await UdpTransportTarget.create((ip, 161))
    
    # 2. Iterar usando async for
    async for errorIndication, errorStatus, errorIndex, varBindTable in walk_cmd(
        snmpEngine,
        CommunityData(community),
        target,
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False
    ):
        if errorIndication or errorStatus:
            print(f"Error SNMP: {errorIndication or errorStatus}")
            break
            
        # 3. Extraer el valor del OID encontrado
        for varBind in varBindTable:
            # varBindTable contiene tuplas (oid, valor)
            oid, value = varBind
            idx = str(oid).split('.')[-1]
            results[idx] = value.prettyPrint()
            
    return results

async def poll_device(ip, community):
    """Recolecta todos los OIDs necesarios de un dispositivo de forma dinámica."""
    device_data = {}
    for key, oid in OIDS.items():
        data = await get_bulk_snmp(ip, community, oid)
        for idx, value in data.items():
            if idx not in device_data:
                device_data[idx] = {}
            device_data[idx][key] = value
    return device_data

def calculate_rates(sample1, sample2, interval_seconds):
    """Calcula la velocidad en bit/s y maneja el desbordamiento de contadores."""
    rates = {}
    for idx in sample1:
        if idx in sample2:
            in_bytes_1 = int(sample1[idx].get('hc_in_octets', 0))
            in_bytes_2 = int(sample2[idx].get('hc_in_octets', 0))
            out_bytes_1 = int(sample1[idx].get('hc_out_octets', 0))
            out_bytes_2 = int(sample2[idx].get('hc_out_octets', 0))
            
            # Control de reinicio (wrap-around) para contadores de 64 bits o si caen
            if in_bytes_2 < in_bytes_1:
                in_bytes_2 += 2**64
            if out_bytes_2 < out_bytes_1:
                out_bytes_2 += 2**64
                
            in_bps = (in_bytes_2 - in_bytes_1) * 8 / interval_seconds
            out_bps = (out_bytes_2 - out_bytes_1) * 8 / interval_seconds
            
            rates[idx] = {
                'descr': sample2[idx].get('descr', ''),
                'admin_status': sample2[idx].get('admin_status', '0'),
                'oper_status': sample2[idx].get('oper_status', '0'),
                'in_bps': round(in_bps, 2),
                'out_bps': round(out_bps, 2)
            }
    return rates

async def main():
    ip = '192.168.153.1'
    community = 'public'
    interval = 15
    
    print(f"Iniciando sondeo a {ip} cada {interval} segundos...")
    sample1 = await poll_device(ip, community)
    
    while True:
        await asyncio.sleep(interval)
        sample2 = await poll_device(ip, community)
        rates = calculate_rates(sample1, sample2, interval)
        
        print(f"\n--- Resultados a las {time.strftime('%X')} ---")
        for idx, data in rates.items():
            print(f"[{idx}] {data['descr']} | Admin: {data['admin_status']} Oper: {data['oper_status']} | IN: {data['in_bps']} bps | OUT: {data['out_bps']} bps")
        
        # Actualizamos la muestra de referencia para el próximo intervalo
        sample1 = sample2

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSondeo detenido manualmente.")