import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone, timedelta

DB_PATH = "/home/admin/mininuc.db"
DB_TIMEOUT = 15

# Interfaces que no generan alerta "Interface Down" (p. ej. ether4, desactivada a propósito
# en la plantilla del CyberRange). Si una de ellas tiene una alerta abierta, se resuelve.
IGNORED_INTERFACES = {'ether4'}

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
        if descr in IGNORED_INTERFACES:
            if descr in active_alerts:
                resolve_alert(active_alerts[descr], descr)
            continue
        if str(oper_status) == '2':
            current_down_entities.add(descr)
            if descr not in active_alerts:
                trigger_alert(rule_name, "CRITICAL", descr, f"La interfaz {descr} ha cambiado su estado a DOWN.")
        else:
            if descr in active_alerts:
                resolve_alert(active_alerts[descr], descr)

# ---------------------------------------------------------------------------
# Ancho de banda: alerta cuando el tráfico de una interfaz está POR ENCIMA del umbral durante N minutos.
# "Durante N minutos" significa que TODAS las muestras de la ventana superan el umbral (un pico corto no
# dispara la alerta) y que hay suficientes muestras (si el poller falla no se concluye nada).
# Configurable con las variables de entorno NOC_BW_THRESHOLD_BPS (bit/s) y NOC_BW_MINUTES.
# ---------------------------------------------------------------------------
BW_DEFAULT_THRESHOLD_BPS = 80_000_000      # 80 Mbit/s (bit/s son unidades decimales)
BW_DEFAULT_MINUTES = 2
BW_POLL_SECONDS = 15                       # intervalo nominal de muestreo del poller SNMP
BW_MIN_SAMPLE_RATIO = 0.6                  # fracción mínima de las muestras esperadas para evaluar la ventana


def positive_int_env(name, default):
    """Entero positivo de una variable de entorno; si falta o no es válido, el valor por defecto."""
    try:
        value = int(os.environ.get(name, '').strip())
    except ValueError:
        return default
    return value if value > 0 else default


def check_high_bandwidth():
    rule_name = "High Bandwidth"
    active_alerts = get_active_alerts(rule_name)
    threshold = positive_int_env('NOC_BW_THRESHOLD_BPS', BW_DEFAULT_THRESHOLD_BPS)
    minutes = positive_int_env('NOC_BW_MINUTES', BW_DEFAULT_MINUTES)
    needed = max(2, int(minutes * 60 / BW_POLL_SECONDS * BW_MIN_SAMPLE_RATIO))
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    with get_db_connection() as conn:
        rows = conn.execute('''
            SELECT descr,
                   COUNT(in_bps),  MIN(in_bps),  AVG(in_bps),
                   COUNT(out_bps), MIN(out_bps), AVG(out_bps)
            FROM snmp_data
            WHERE timestamp >= ?
            GROUP BY descr
        ''', (since,)).fetchall()

    for descr, n_in, min_in, avg_in, n_out, min_out, avg_out in rows:
        directions = (('entrada', n_in, min_in, avg_in), ('salida', n_out, min_out, avg_out))
        sustained = [(name, low, avg) for name, n, low, avg in directions if n >= needed and low > threshold]

        if sustained:
            if descr not in active_alerts:
                detail = ', '.join(f"{name}: mínimo {low / 1e6:.2f} y media {avg / 1e6:.2f} Mbit/s" for name, low, avg in sustained)
                trigger_alert(rule_name, "WARNING", descr,
                              f"Tráfico por encima de {threshold / 1e6:.2f} Mbit/s durante {minutes} min en {descr} ({detail}).")
        elif descr in active_alerts:
            # Se resuelve cuando la media de la ventana vuelve a estar por debajo del umbral (evita parpadeos);
            # si no hay muestras suficientes no se sabe nada y se mantiene el estado.
            measured = [avg for _name, n, _low, avg in directions if n >= needed]
            if measured and all(avg <= threshold for avg in measured):
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

# ---------------------------------------------------------------------------
# Datos obsoletos: una fuente que no produce datos nuevos durante demasiado tiempo.
# Segundos sin datos antes de alertar; se pueden cambiar con las variables de entorno
# NOC_STALE_SNMP, NOC_STALE_IPFIX y NOC_STALE_SYSLOG (0 u "off" desactiva la regla de esa fuente).
# Syslog viene desactivado: el router solo envía logs cuando ocurre algo y puede pasar días en
# silencio sin que haya ningún fallo. Su salud se vigila con la regla "Collector Down".
# ---------------------------------------------------------------------------
STALE_SOURCES = [
    # (entidad, nombre corto, tabla, columna de tiempo, formato, umbral por defecto en s, variable de entorno)
    ('SNMP Poller', 'SNMP', 'snmp_data', 'timestamp', 'iso', 180, 'NOC_STALE_SNMP'),
    ('IPFIX Collector', 'IPFIX', 'flow_data', 'ts', 'flow', 300, 'NOC_STALE_IPFIX'),
    ('Syslog Collector', 'Syslog', 'syslog_data', 'timestamp', 'iso', 0, 'NOC_STALE_SYSLOG'),
]

# Servicios recolectores cuya parada se alerta, y evaluaciones seguidas necesarias para confirmarla
# (evita avisos falsos mientras un servicio se reinicia durante un despliegue).
COLLECTOR_SERVICES = ['snmp-poller', 'syslog-poller', 'flow-poller']
DOWN_CONFIRMATIONS = 2
_down_counts = {}


def stale_threshold(default, env_name):
    """Umbral en segundos: el valor por defecto o el de la variable de entorno. 0 = regla desactivada."""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ('', '0', 'off', 'no'):
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def format_duration(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def last_data_time(table, column, fmt):
    """Momento (UTC) del dato más reciente de una tabla, o None si no hay ninguno o la tabla no existe."""
    try:
        with get_db_connection() as conn:
            value = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    if not value:
        return None
    if fmt == 'flow':       # 'YYYY-MM-DD HH:MM:SS.mmm', UTC sin zona
        return datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_no_data():
    rule_name = "No Data"
    active_alerts = get_active_alerts(rule_name)
    now = datetime.now(timezone.utc)

    for entity, label, table, column, fmt, default, env_name in STALE_SOURCES:
        limit = stale_threshold(default, env_name)
        if limit <= 0:
            # Regla desactivada para esta fuente: no se deja ninguna alerta colgada
            if entity in active_alerts:
                resolve_alert(active_alerts[entity], entity)
            continue

        last = last_data_time(table, column, fmt)
        age = (now - last).total_seconds() if last else None

        if age is None or age > limit:
            if entity not in active_alerts:
                if age is None:
                    message = f"No hay ningún dato de {label} en la base de datos (umbral: {format_duration(limit)})."
                else:
                    message = (f"No se reciben datos de {label} desde hace {format_duration(age)} "
                               f"(umbral: {format_duration(limit)}). Último dato: {last.strftime('%Y-%m-%d %H:%M:%S')} UTC.")
                trigger_alert(rule_name, "CRITICAL", entity, message)
        else:
            if entity in active_alerts:
                resolve_alert(active_alerts[entity], entity)


def service_state(name):
    """Estado del servicio systemd (active / inactive / failed / unknown si no se puede consultar)."""
    try:
        r = subprocess.run(['systemctl', 'is-active', f'{name}.service'], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def check_collectors_down():
    """Alerta si un servicio recolector no está en marcha. Es la forma de vigilar syslog, cuyo silencio es normal."""
    rule_name = "Collector Down"
    active_alerts = get_active_alerts(rule_name)
    for service in COLLECTOR_SERVICES:
        state = service_state(service)
        if state == 'unknown':
            continue                      # no se pudo consultar: ni se dispara ni se resuelve
        if state != 'active':
            _down_counts[service] = _down_counts.get(service, 0) + 1
            if _down_counts[service] >= DOWN_CONFIRMATIONS and service not in active_alerts:
                trigger_alert(rule_name, "CRITICAL", service, f"El servicio {service} no está en marcha (estado: {state}).")
        else:
            _down_counts[service] = 0
            if service in active_alerts:
                resolve_alert(active_alerts[service], service)

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

def run_checks(checks):
    """Ejecuta cada regla por separado: si una falla, las demás se siguen evaluando."""
    for check in checks:
        try:
            check()
        except Exception as e:
            print(f"Error en {check.__name__}: {e}", flush=True)

def run_engine():
    print("Iniciando Motor de Alertas...", flush=True)
    init_db()
    while True:
        run_checks([check_interface_status, check_high_bandwidth, check_syslog_failures,
                    check_no_data, check_collectors_down, enforce_retention])
        time.sleep(30)

if __name__ == "__main__":
    run_engine()