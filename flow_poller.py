import subprocess
import csv
import io
import sqlite3
import time

DB_FILE = '/home/admin/mininuc.db'
NFDUMP_DIR = '/var/cache/nfdump/mikrotik'
POLL_INTERVAL = 30  # Frecuencia de muestreo en segundos

# Mapeo de códigos numéricos de protocolo a texto
PROTO_MAP = {'6': 'TCP', '17': 'UDP', '1': 'ICMP'}

def init_db():
    """Inicializa la base de datos y crea la tabla con restricciones de unicidad para desduplicar."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
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
    conn.commit()
    return conn

def resolve_real_ip(ip, xip):
    """Retorna la IP real del host interno si existe traducción NAT (xsa/xda != 0.0.0.0)."""
    if xip and str(xip).strip() not in ('0.0.0.0', '::', ''):
        return xip
    return ip

def collect_flows(conn):
    cursor = conn.cursor()
    cmd = [
        'nfdump',
        '-R', NFDUMP_DIR,
        '-q', '-N',
        '-o', 'csv:%ts,%te,%pr,%sa,%sp,%da,%dp,%pkt,%byt,%in,%out,%xsa,%xda'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if not result.stdout:
            return
    except Exception as e:
        print(f"Error al ejecutar nfdump: {e}", flush=True)
        return

    csv_reader = csv.DictReader(io.StringIO(result.stdout.strip()))
    inserted_count = 0
    
    for row in csv_reader:
        # Mapear nombres de cabeceras de nfdump (firstSeen/ts, srcAddr/sa, etc.)
        ts = row.get('firstSeen') or row.get('ts')
        
        # Ignorar líneas de resumen estadístico o aquellas sin timestamp
        if not ts or 'Summary' in str(ts):
            continue
            
        try:
            te = row.get('lastSeen') or row.get('te') or ts
            pr_raw = row.get('proto') or row.get('pr') or ''
            # Traduce si es numérico (6, 17, 1) y déjalo como string si ya lo es
            pr = {'6': 'TCP', '17': 'UDP', '1': 'ICMP'}.get(str(pr_raw), str(pr_raw))
            sa = row.get('srcAddr') or row.get('sa') or ''
            sp = int(row.get('srcPort') or row.get('sp') or 0)
            da = row.get('dstAddr') or row.get('da') or ''
            dp = int(row.get('dstPort') or row.get('dp') or 0)
            pkt = int(row.get('packets') or row.get('pkt') or 0)
            byt = int(row.get('bytes') or row.get('byt') or 0)
            in_if = int(row.get('input') or row.get('in') or 0)
            out_if = int(row.get('output') or row.get('out') or 0)
            xsa = row.get('srcXIP') or row.get('xsa') or '0.0.0.0'
            xda = row.get('dstXIP') or row.get('xda') or '0.0.0.0'
            
            real_src = resolve_real_ip(sa, xsa)
            real_dst = resolve_real_ip(da, xda)
            
            cursor.execute('''
                INSERT OR IGNORE INTO flow_data 
                (ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda, real_src, real_dst))
            
            if cursor.rowcount > 0:
                inserted_count += 1
                
        except (ValueError, TypeError, KeyError) as e:
            # Puedes poner un print aquí temporalmente si necesitas ver qué atributo falla exactamente
            # print("Error al procesar flujo:", e)
            continue

    conn.commit()
    if inserted_count > 0:
        print(f"IPFIX: {inserted_count} nuevos flujos procesados.", flush=True)

def main():
    conn = init_db()
    print("Iniciando recolector de flujos IPFIX...", flush=True)
    while True:
        collect_flows(conn)
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    main()
