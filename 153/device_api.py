import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

DB_FILE = '/home/admin/mininuc.db'
STALE_SECONDS = 60      # el poller sondea cada ~15 s: 4 ciclos sin datos nuevos = obsoleto

# ifAdminStatus / ifOperStatus (RFC 2863)
IF_STATUS = {1: 'up', 2: 'down', 3: 'testing', 4: 'unknown', 5: 'dormant', 6: 'notPresent', 7: 'lowerLayerDown'}

router = APIRouter()


def service_state(name):
    """Estado del servicio systemd (active / inactive / failed / unknown)."""
    try:
        r = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def parse_iso(value):
    return datetime.fromisoformat(value) if value else None


def status_name(value):
    """'1' -> 'up', '2' -> 'down'. Si no es un número conocido, se devuelve tal cual."""
    try:
        return IF_STATUS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


def interface_state(admin, oper):
    """up: funciona. admin_down: desactivada a propósito. down: debería funcionar y no lo hace."""
    if str(admin) == '2':
        return 'admin_down'
    if str(oper) == '1':
        return 'up'
    return 'down'


def device_summary(row, collector, now):
    """Estado del router a partir de la fila de snmp_device y del estado del servicio recolector."""
    info = {'reachable': None, 'last_attempt': None, 'last_success': None, 'attempt_age_seconds': None,
            'success_age_seconds': None, 'uptime_seconds': None, 'boot_time': None,
            'consecutive_failures': 0, 'last_error': None}
    if row is None:
        return 'unknown', 'El poller SNMP todavía no ha registrado ningún sondeo del dispositivo.', info

    attempt, success = parse_iso(row['last_attempt']), parse_iso(row['last_success'])
    uptime = row['uptime_seconds']
    info.update({
        'reachable': bool(row['reachable']),
        'last_attempt': row['last_attempt'],
        'last_success': row['last_success'],
        'attempt_age_seconds': int((now - attempt).total_seconds()) if attempt else None,
        'success_age_seconds': int((now - success).total_seconds()) if success else None,
        'uptime_seconds': uptime,
        'boot_time': (now - timedelta(seconds=uptime)).replace(microsecond=0).isoformat() if uptime is not None and success else None,
        'consecutive_failures': row['consecutive_failures'],
        'last_error': row['last_error'],
    })

    if collector != 'active':
        return 'collector_down', (f'El poller SNMP no está en marcha (estado: {collector}): '
                                  'no se sabe si el router responde. Los datos mostrados son los últimos conocidos.'), info
    if not row['reachable']:
        n = row['consecutive_failures']
        return 'unreachable', (f'El router no responde a SNMP ({n} sondeos fallidos seguidos). '
                               'Los datos mostrados son los últimos conocidos.'), info
    if info['attempt_age_seconds'] is not None and info['attempt_age_seconds'] > STALE_SECONDS:
        return 'stale', f'El último sondeo tiene {info["attempt_age_seconds"]} s: el poller no está actualizando el estado.', info
    return 'reachable', '', info


@router.get('/api/device')
def get_device():
    """Estado del router (alcanzabilidad, sysUpTime, último sondeo correcto) y de sus interfaces."""
    now = datetime.now(timezone.utc)
    collector = service_state('snmp-poller')
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        try:
            device_row = conn.execute('SELECT * FROM snmp_device ORDER BY device LIMIT 1').fetchone()
        except sqlite3.OperationalError:
            device_row = None
        try:
            rows = conn.execute('''
                SELECT s.if_index, s.descr, s.admin_status, s.oper_status, s.in_bps, s.out_bps, s.timestamp
                FROM snmp_data s
                JOIN (SELECT if_index, MAX(timestamp) AS ts FROM snmp_data GROUP BY if_index) m
                  ON s.if_index = m.if_index AND s.timestamp = m.ts
                ORDER BY CAST(s.if_index AS INTEGER)
            ''').fetchall()
            freshness = conn.execute('SELECT MAX(timestamp) FROM snmp_data').fetchone()[0]
        except sqlite3.OperationalError:
            rows, freshness = [], None
    finally:
        conn.close()

    status, message, info = device_summary(device_row, collector, now)

    interfaces = []
    for r in rows:
        age = int((now - parse_iso(r['timestamp'])).total_seconds())
        interfaces.append({
            'if_index': int(r['if_index']) if str(r['if_index']).isdigit() else r['if_index'],
            'name': r['descr'],
            'admin_status': status_name(r['admin_status']),
            'oper_status': status_name(r['oper_status']),
            'state': interface_state(r['admin_status'], r['oper_status']),
            'in_bps': r['in_bps'],
            'out_bps': r['out_bps'],
            'last_update': r['timestamp'],
            'age_seconds': age,
            'stale': age > STALE_SECONDS,
        })

    return {
        'device': device_row['device'] if device_row else None,
        'status': status,
        'message': message,
        'collector': collector,
        **info,
        'freshness': freshness,
        'interfaces': interfaces,
    }
