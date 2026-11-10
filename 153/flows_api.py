import ipaddress
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from flow_poller import is_internal  # una sola definición de "red interna" para todo el sistema

DB_FILE = '/home/admin/mininuc.db'
STALE_SECONDS = 300          # si el flujo más reciente es más antiguo, se avisa de datos obsoletos
MAX_MINUTES = 7 * 24 * 60    # la retención es de 7 días
VALID_BY = ('hosts', 'pairs', 'ports')
VALID_SCOPE = ('internal', 'all')

router = APIRouter()


def parse_flow_ts(ts):
    """'2026-11-08 19:31:53.138' (UTC, sin zona) -> datetime con zona UTC."""
    return datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


def cutoff(minutes):
    """Inicio de la ventana en el mismo formato en que se guarda ts (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')


def check_minutes(minutes):
    if minutes < 1 or minutes > MAX_MINUTES:
        raise HTTPException(status_code=400, detail=f'minutes debe estar entre 1 y {MAX_MINUTES}')


def data_state(conn, has_rows):
    """Frescura y motivo por el que una vista puede estar vacía u obsoleta."""
    last = conn.execute('SELECT MAX(ts) FROM flow_data').fetchone()[0]
    if last is None:
        return {'freshness': None, 'age_seconds': None, 'status': 'no_data',
                'message': 'No hay flujos almacenados: el recolector IPFIX todavía no ha guardado nada.'}
    last_dt = parse_flow_ts(last)
    age = int((datetime.now(timezone.utc) - last_dt).total_seconds())
    state = {'freshness': last_dt.isoformat(), 'age_seconds': age, 'status': 'ok', 'message': ''}
    if age > STALE_SECONDS:
        state['status'] = 'stale'
        state['message'] = (f'Los flujos más recientes tienen {age // 60} min de antigüedad: '
                            'el recolector IPFIX o el envío desde el router pueden estar parados.')
    elif not has_rows:
        state['status'] = 'no_traffic'
        state['message'] = 'No hubo tráfico en esta ventana de tiempo.'
    return state


def empty_response(minutes, extra):
    return {'window_minutes': minutes, **extra, 'freshness': None, 'age_seconds': None,
            'status': 'no_data', 'message': 'No hay flujos almacenados: el recolector IPFIX todavía no ha guardado nada.',
            'data': []}


def top_hosts(conn, since, limit, scope):
    rows = conn.execute('''
        SELECT host, SUM(bytes_out) AS bytes_out, SUM(bytes_in) AS bytes_in, SUM(flows) AS flows
        FROM (
            SELECT real_src AS host, SUM(byt) AS bytes_out, 0 AS bytes_in, COUNT(*) AS flows
            FROM flow_data WHERE ts >= ? GROUP BY real_src
            UNION ALL
            SELECT real_dst AS host, 0 AS bytes_out, SUM(byt) AS bytes_in, COUNT(*) AS flows
            FROM flow_data WHERE ts >= ? GROUP BY real_dst
        )
        GROUP BY host
    ''', (since, since)).fetchall()
    hosts = []
    for r in rows:
        if scope == 'internal' and not is_internal(r['host']):
            continue
        hosts.append({'host': r['host'], 'bytes_out': r['bytes_out'], 'bytes_in': r['bytes_in'],
                      'total_bytes': r['bytes_out'] + r['bytes_in'], 'flows': r['flows']})
    hosts.sort(key=lambda h: h['total_bytes'], reverse=True)
    return hosts[:limit]


def top_pairs(conn, since, limit):
    rows = conn.execute('''
        SELECT real_src AS src, real_dst AS dst, SUM(byt) AS total_bytes, SUM(pkt) AS packets, COUNT(*) AS flows
        FROM flow_data WHERE ts >= ?
        GROUP BY real_src, real_dst ORDER BY total_bytes DESC LIMIT ?
    ''', (since, limit)).fetchall()
    return [dict(r) for r in rows]


def top_ports(conn, since, limit):
    # Puerto de servicio = el menor de los dos (el otro suele ser efímero), así se unen los dos sentidos.
    rows = conn.execute('''
        SELECT pr AS protocol, MIN(sp, dp) AS port, SUM(byt) AS total_bytes, SUM(pkt) AS packets, COUNT(*) AS flows
        FROM flow_data WHERE ts >= ?
        GROUP BY pr, MIN(sp, dp) ORDER BY total_bytes DESC LIMIT ?
    ''', (since, limit)).fetchall()
    return [dict(r) for r in rows]


@router.get('/api/flows/top')
def flows_top(minutes: int = 60, by: str = 'hosts', limit: int = 10, scope: str = 'internal'):
    """Top talkers de una ventana: por hosts internos, pares de hosts o puertos de servicio.
    Las direcciones son las REALES (tras deshacer el NAT), no la WAN del router."""
    check_minutes(minutes)
    if by not in VALID_BY:
        raise HTTPException(status_code=400, detail='by debe ser hosts, pairs o ports')
    if scope not in VALID_SCOPE:
        raise HTTPException(status_code=400, detail='scope debe ser internal o all')
    limit = max(1, min(limit, 100))
    extra = {'by': by, 'scope': scope if by == 'hosts' else None}
    since = cutoff(minutes)

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        try:
            if by == 'hosts':
                data = top_hosts(conn, since, limit, scope)
            elif by == 'pairs':
                data = top_pairs(conn, since, limit)
            else:
                data = top_ports(conn, since, limit)
            state = data_state(conn, bool(data))
        except sqlite3.OperationalError:
            return empty_response(minutes, extra)
        return {'window_minutes': minutes, **extra, **state, 'data': data}
    finally:
        conn.close()


@router.get('/api/flows/host/{ip}')
def flows_host(ip: str, minutes: int = 60, limit: int = 20):
    """Conversaciones de un host: con quién habla, por qué puerto y cuántos bytes en cada sentido."""
    check_minutes(minutes)
    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail='IP no válida')
    limit = max(1, min(limit, 100))
    since = cutoff(minutes)
    extra = {'host': ip}

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute('''
                SELECT CASE WHEN real_src = :ip THEN real_dst ELSE real_src END AS peer,
                       pr AS protocol,
                       MIN(sp, dp) AS port,
                       SUM(CASE WHEN real_src = :ip THEN byt ELSE 0 END) AS bytes_out,
                       SUM(CASE WHEN real_dst = :ip THEN byt ELSE 0 END) AS bytes_in,
                       SUM(pkt) AS packets,
                       COUNT(*) AS flows
                FROM flow_data
                WHERE (real_src = :ip OR real_dst = :ip) AND ts >= :since
                GROUP BY peer, pr, MIN(sp, dp)
                ORDER BY (bytes_out + bytes_in) DESC
                LIMIT :limit
            ''', {'ip': ip, 'since': since, 'limit': limit}).fetchall()
            data = [dict(r) for r in rows]
            state = data_state(conn, bool(data))
        except sqlite3.OperationalError:
            return empty_response(minutes, extra)
        if state['status'] == 'no_traffic':
            state['message'] = 'Este host no tuvo tráfico en esta ventana de tiempo.'
        return {'window_minutes': minutes, **extra, **state, 'data': data}
    finally:
        conn.close()
