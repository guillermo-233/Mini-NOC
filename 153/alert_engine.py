import sqlite3
import time
from datetime import datetime, timezone, timedelta

DB_PATH = "/home/admin/mininuc.db"
DB_TIMEOUT = 15

def get_db_connection():
    return sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)

def init_db():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                entity TEXT NOT NULL,
                message TEXT,
                start_time DATETIME NOT NULL,
                end_time DATETIME
            )
        ''')
        conn.commit()

def get_active_alerts(rule_name):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT entity, id FROM alerts WHERE rule_name = ? AND status = 'firing'", (rule_name,))
        return {row[0]: row[1] for row in cursor.fetchall()}

def trigger_alert(rule_name, severity, entity, message):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with get_db_connection() as conn:
        conn.execute('''
            INSERT INTO alerts (rule_name, severity, status, entity, message, start_time)
            VALUES (?, ?, 'firing', ?, ?, ?)
        ''', (rule_name, severity, entity, message, now))
        conn.commit()
    print(f"[{now}] ALERTA DISPARADA: {rule_name} en {entity} - {message}")

def resolve_alert(alert_id, entity):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with get_db_connection() as conn:
        conn.execute('''
            UPDATE alerts SET status = 'resolved', end_time = ? WHERE id = ?
        ''', (now, alert_id))
        conn.commit()
    print(f"[{now}] ALERTA RESUELTA: ID {alert_id} en {entity}")

def check_interface_status():
    rule_name = "Interface Down"
    active_alerts = get_active_alerts(rule_name)
    # Python calcula la fecha límite en formato ISO
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT descr, oper_status 
            FROM snmp_data 
            WHERE timestamp >= ?
            GROUP BY descr 
            HAVING timestamp = MAX(timestamp)
        ''', (threshold,))
        latest_status = cursor.fetchall()

    current_down_entities = set()
    for descr, oper_status in latest_status:
        if str(oper_status) == '2':
            current_down_entities.add(descr)
            if descr not in active_alerts:
                trigger_alert(rule_name, "CRITICAL", descr, f"La interfaz {descr} ha cambiado su estado a DOWN.")
        else:
            if descr in active_alerts:
                resolve_alert(active_alerts[descr], descr)

def check_high_bandwidth():
    rule_name = "High Bandwidth"
    active_alerts = get_active_alerts(rule_name)
    threshold_bps = 80 * 1024 * 1024 # 80 Mbps
    threshold_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT descr, AVG(in_bps) as avg_in, AVG(out_bps) as avg_out
            FROM snmp_data 
            WHERE timestamp >= ?
            GROUP BY descr
        ''', (threshold_time,))
        traffic_data = cursor.fetchall()
        
    current_high_traffic_entities = set()
    for descr, avg_in, avg_out in traffic_data:
        in_bps = avg_in or 0
        out_bps = avg_out or 0
        if (in_bps > threshold_bps) or (out_bps > threshold_bps):
            current_high_traffic_entities.add(descr)
            if descr not in active_alerts:
                max_traffic_mbps = max(in_bps, out_bps) / (1024 * 1024)
                trigger_alert(rule_name, "WARNING", descr, f"Tráfico elevado en {descr}: {max_traffic_mbps:.2f} Mbps sostenido.")
        else:
            if descr in active_alerts:
                resolve_alert(active_alerts[descr], descr)

def check_syslog_failures():
    rule_name = "Login Failures"
    active_alerts = get_active_alerts(rule_name)
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT source_ip, COUNT(*) as failure_count
            FROM syslog_data 
            WHERE timestamp >= ?
              AND message LIKE '%login failure%'
            GROUP BY source_ip
        ''', (threshold,))
        failed_logins = cursor.fetchall()
        
    current_failing_hosts = set()
    for source_ip, failure_count in failed_logins:
        if failure_count > 5:
            current_failing_hosts.add(source_ip)
            if source_ip not in active_alerts:
                trigger_alert(rule_name, "CRITICAL", source_ip, f"Seguridad comprometida: {failure_count} fallos de login en 1 minuto desde {source_ip}.")

    for source_ip, alert_id in active_alerts.items():
        if source_ip not in current_failing_hosts:
            resolve_alert(alert_id, source_ip)

def check_no_data():
    rule_name = "No Data"
    active_alerts = get_active_alerts(rule_name)
    entity = "SNMP Poller"
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) 
            FROM snmp_data 
            WHERE timestamp >= ?
        ''', (threshold,))
        recent_count = cursor.fetchone()[0]
        
    if recent_count == 0:
        if entity not in active_alerts:
            trigger_alert(rule_name, "CRITICAL", entity, "No se han recibido métricas SNMP en los últimos 3 minutos.")
    else:
        if entity in active_alerts:
            resolve_alert(active_alerts[entity], entity)

def enforce_retention():
    """Borra automáticamente los datos de más de 7 días (Standard Level)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # SNMP (columna timestamp)
            cursor.execute("DELETE FROM snmp_data WHERE julianday(timestamp) < julianday('now', '-7 days')")
            
            # Syslog (columna timestamp)
            cursor.execute("DELETE FROM syslog_data WHERE julianday(timestamp) < julianday('now', '-7 days')")
            
            # NetFlow/sFlow (columna ts)
            cursor.execute("DELETE FROM flow_data WHERE julianday(ts) < julianday('now', '-7 days')")
            
            # Alertas resueltas (columna end_time)
            cursor.execute("DELETE FROM alerts WHERE status = 'resolved' AND julianday(end_time) < julianday('now', '-7 days')")
            
            conn.commit()
    except Exception as e:
        print(f"[Retention Error] {e}", flush=True)

def run_engine():
    print("Iniciando Motor de Alertas...")
    init_db()
    while True:
        try:
            check_interface_status()
            check_high_bandwidth()
            check_syslog_failures()
            check_no_data()
            enforce_retention()
        except Exception as e:
            print(f"Error en el ciclo de evaluación: {e}")
        time.sleep(30)

if __name__ == "__main__":
    run_engine()