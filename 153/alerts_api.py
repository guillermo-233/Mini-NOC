import sqlite3
import subprocess

from fastapi import APIRouter

DB_FILE = '/home/admin/mininuc.db'
router = APIRouter()


def to_iso_utc(value):
    """El motor guarda 'YYYY-MM-DD HH:MM:SS' en UTC; se devuelve en ISO 8601 con zona."""
    if not value:
        return None
    return value.replace(' ', 'T') + '+00:00'


def engine_state():
    """Estado del servicio del motor de alertas (active / inactive / failed / unknown)."""
    try:
        r = subprocess.run(['systemctl', 'is-active', 'alert-engine'],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def alert_to_dict(row):
    return {
        'id': row['id'],
        'rule_name': row['rule_name'],
        'severity': row['severity'],
        'entity': row['entity'],
        'message': row['message'],
        'start_time': to_iso_utc(row['start_time']),
        'end_time': to_iso_utc(row['end_time']),
    }


@router.get('/api/alerts')
def get_alerts(resolved_limit: int = 10):
    """Alertas activas (todas) y las últimas resueltas, con el estado del motor de alertas."""
    resolved_limit = max(1, min(resolved_limit, 100))
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        try:
            active = conn.execute(
                "SELECT id, rule_name, severity, entity, message, start_time, end_time "
                "FROM alerts WHERE status = 'firing' ORDER BY start_time DESC").fetchall()
            resolved = conn.execute(
                "SELECT id, rule_name, severity, entity, message, start_time, end_time "
                "FROM alerts WHERE status = 'resolved' ORDER BY end_time DESC LIMIT ?",
                (resolved_limit,)).fetchall()
            last = conn.execute(
                "SELECT MAX(COALESCE(end_time, start_time)) FROM alerts").fetchone()[0]
        except sqlite3.OperationalError:
            # La tabla aún no existe: el motor de alertas nunca ha arrancado
            active, resolved, last = [], [], None
        return {
            'engine': engine_state(),
            'freshness': to_iso_utc(last),
            'active': [alert_to_dict(r) for r in active],
            'recent_resolved': [alert_to_dict(r) for r in resolved],
        }
    finally:
        conn.close()
