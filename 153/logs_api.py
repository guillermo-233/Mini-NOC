import ipaddress
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

DB_FILE = '/home/admin/mininuc.db'
MAX_MINUTES = 7 * 24 * 60    # la retención es de 7 días
MAX_LIMIT = 500
MAX_TEXT = 100

# Niveles de severidad de syslog, de más a menos grave
SEVERITIES = ['emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info', 'debug']
ALIASES = {'error': 'err', 'warn': 'warning', 'critical': 'crit', 'emergency': 'emerg', 'panic': 'emerg'}

# En la base de datos la severidad se guarda como "facility.nivel" (p. ej. local0.err)
LEVEL_SQL = "CASE WHEN instr(severity, '.') > 0 THEN substr(severity, instr(severity, '.') + 1) ELSE severity END"

router = APIRouter()


def service_state(name):
    """Estado del servicio systemd (active / inactive / failed / unknown)."""
    try:
        r = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def parse_severities(value):
    """'err,warning' -> ['err', 'warning']. Acepta alias (error, warn, critical...)."""
    levels = []
    for part in (value or '').split(','):
        part = part.strip().lower()
        if not part:
            continue
        part = ALIASES.get(part, part)
        if part not in SEVERITIES:
            raise HTTPException(status_code=400, detail='severity debe ser uno o varios de: ' + ', '.join(SEVERITIES))
        if part not in levels:
            levels.append(part)
    return levels


def cutoff(minutes):
    """Inicio de la ventana en el mismo formato en que se guarda timestamp (ISO 8601 UTC)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def build_where(since, host, text, levels):
    clauses = ['timestamp >= :since']
    params = {'since': since}
    if host:
        clauses.append('source_ip = :host')
        params['host'] = host
    if text:
        clauses.append('instr(lower(message), lower(:q)) > 0')
        params['q'] = text
    if levels:
        names = []
        for i, level in enumerate(levels):
            names.append(f':lvl{i}')
            params[f'lvl{i}'] = level
        clauses.append(f'({LEVEL_SQL}) IN ({", ".join(names)})')
    return ' AND '.join(clauses), params


def split_severity(raw):
    """'local0.err' -> ('local0', 'err')."""
    if raw and '.' in raw:
        facility, level = raw.split('.', 1)
        return facility, level
    return None, raw


def empty_response(minutes, filters, collector):
    return {'window_minutes': minutes, 'filters': filters, 'collector': collector, 'freshness': None,
            'status': 'no_data',
            'message': 'Todavía no se ha guardado ningún evento de syslog (el router solo envía logs cuando ocurre algo).',
            'total_matching': 0, 'counts': [], 'hosts': [], 'data': []}


@router.get('/api/logs')
def get_logs(minutes: int = 1440, limit: int = 50, host: str = '', severity: str = '', q: str = ''):
    """Eventos de syslog con filtros por host, severidad y texto, más el recuento por severidad.
    El recuento ignora el filtro de severidad (así se ve cuántos hay de cada nivel con los otros filtros)."""
    if minutes < 1 or minutes > MAX_MINUTES:
        raise HTTPException(status_code=400, detail=f'minutes debe estar entre 1 y {MAX_MINUTES}')
    limit = max(1, min(limit, MAX_LIMIT))
    host = host.strip()
    if host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise HTTPException(status_code=400, detail='host debe ser una IP válida')
    levels = parse_severities(severity)
    text = q.strip()[:MAX_TEXT]
    filters = {'host': host or None, 'severity': levels, 'q': text or None}
    since = cutoff(minutes)
    collector = service_state('syslog-poller')

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        try:
            last = conn.execute('SELECT MAX(timestamp) FROM syslog_data').fetchone()[0]
            if last is None:
                return empty_response(minutes, filters, collector)

            where, params = build_where(since, host, text, levels)
            rows = conn.execute(
                f'SELECT id, timestamp, source_ip, severity, message FROM syslog_data WHERE {where} '
                'ORDER BY timestamp DESC, id DESC LIMIT :limit', {**params, 'limit': limit}).fetchall()
            total = conn.execute(f'SELECT COUNT(*) FROM syslog_data WHERE {where}', params).fetchone()[0]

            where_c, params_c = build_where(since, host, text, [])
            count_rows = conn.execute(
                f'SELECT {LEVEL_SQL} AS level, COUNT(*) AS n FROM syslog_data WHERE {where_c} GROUP BY level',
                params_c).fetchall()
            by_level = {r['level']: r['n'] for r in count_rows}
            counts = [{'severity': s, 'count': by_level[s]} for s in SEVERITIES if s in by_level]
            counts += [{'severity': s, 'count': n} for s, n in by_level.items() if s not in SEVERITIES]

            hosts = [r[0] for r in conn.execute(
                'SELECT DISTINCT source_ip FROM syslog_data WHERE timestamp >= :since ORDER BY source_ip LIMIT 50',
                {'since': since}).fetchall()]

            in_window = conn.execute('SELECT COUNT(*) FROM syslog_data WHERE timestamp >= :since',
                                     {'since': since}).fetchone()[0]
        except sqlite3.OperationalError:
            return empty_response(minutes, filters, collector)
    finally:
        conn.close()

    data = []
    for r in rows:
        facility, level = split_severity(r['severity'])
        data.append({'id': r['id'], 'ts': r['timestamp'], 'host': r['source_ip'],
                     'facility': facility, 'severity': level, 'message': r['message']})

    status, message = 'ok', ''
    if total == 0:
        if in_window == 0:
            status, message = 'no_events', 'No hay eventos de syslog en esta ventana de tiempo.'
        else:
            status, message = 'no_match', 'Ningún evento coincide con los filtros aplicados.'
    return {'window_minutes': minutes, 'filters': filters, 'collector': collector, 'freshness': last,
            'status': status, 'message': message, 'total_matching': total,
            'counts': counts, 'hosts': hosts, 'data': data}
