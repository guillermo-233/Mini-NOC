from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import datetime
from datetime import datetime, timedelta, timezone
import os
import subprocess

app = FastAPI(title="Mini-NOC API")

# Permitir CORS para que la interfaz web (HTML/JS) pueda consumir la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = '/home/admin/mininuc.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def get_freshness(cursor, table_name, time_column="timestamp"):
    """
    Obtiene el timestamp más reciente de una tabla para cumplir
    el requisito de frescura de los datos.
    """
    try:
        cursor.execute(f"SELECT MAX({time_column}) as last_update FROM {table_name}")
        row = cursor.fetchone()
        return row["last_update"] if row and row["last_update"] else None
    except sqlite3.OperationalError:
         return None

@app.get("/api/snmp/interfaces")
def get_interfaces_status():
    """
    Devuelve el estado operativo más reciente y la última tasa de tráfico
    conocida para cada interfaz.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT if_index as ifIndex, descr, oper_status as ifOperStatus, in_bps, out_bps
        FROM snmp_data
        WHERE (if_index, timestamp) IN (
            SELECT if_index, MAX(timestamp)
            FROM snmp_data
            GROUP BY if_index
        )
    """
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        freshness = get_freshness(cursor, "snmp_data")
        
        data = [dict(row) for row in rows]
        return {"freshness": freshness, "data": data}
    finally:
        conn.close()

@app.get("/api/syslog")
def get_syslog_events(
    limit: int = Query(50, description="Número máximo de eventos a devolver")
):
    """
    Devuelve los eventos de syslog más recientes.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT timestamp as ts, source_ip as host, severity as tag, message FROM syslog_data ORDER BY timestamp DESC LIMIT ?"
    try:
        cursor.execute(query, (limit,))
        rows = cursor.fetchall()
        freshness = get_freshness(cursor, "syslog_data")
        
        data = [dict(row) for row in rows]
        return {"freshness": freshness, "data": data}
    finally:
        conn.close()

@app.get("/api/ipfix/top-talkers")
def get_top_talkers(minutes: int = Query(5)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        freshness = get_freshness(cursor, "flow_data", time_column="ts")

        query = """
            SELECT 
                sa as source_ip, 
                da as destination_ip, 
                dp as destination_port, 
                pr as protocol, 
                SUM(byt) as total_bytes 
            FROM flow_data 
            WHERE ts >= datetime('now', 'localtime', ?) 
            GROUP BY sa, da, dp, pr 
            ORDER BY total_bytes DESC 
            LIMIT 10
        """
        time_modifier = f"-{minutes} minutes"
        cursor.execute(query, (time_modifier,))

        columns = [column[0] for column in cursor.description]
        data = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return {"freshness": freshness, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/snmp/bandwidth")
def get_bandwidth(range: str = "15m"):
    if range == "15m":
        time_limit = 15 * 60
    elif range == "1h":
        time_limit = 60 * 60
    elif range == "24h":
        time_limit = 24 * 60 * 60
    else:
        time_limit = 15 * 60

    conn = sqlite3.connect('/home/admin/mininuc.db', timeout=30.0)
    cursor = conn.cursor()
    
    limit_time = (datetime.now(timezone.utc) - timedelta(seconds=time_limit)).isoformat()
    
    cursor.execute('''
        SELECT descr, timestamp, in_bps, out_bps 
        FROM snmp_data 
        WHERE timestamp >= ? 
        ORDER BY timestamp ASC
    ''', (limit_time,))
    
    rows = cursor.fetchall()
    conn.close()

    results = {}
    for descr, timestamp, in_bps, out_bps in rows:
        if descr not in results:
            results[descr] = {"in": [], "out": [], "timestamps": []}
            
        formatted_time = timestamp[11:19]
        
        results[descr]["timestamps"].append(formatted_time)
        results[descr]["in"].append(in_bps)
        results[descr]["out"].append(out_bps)

    return results

@app.get("/api/health")
def get_noc_health():
    """
    Devuelve el tamaño de la BD, estado de los recolectores systemd y 
    la última muestra recibida (Standard Level).
    """
    # 1. Tamaño físico de la BD en MB
    db_size_mb = 0.0
    if os.path.exists(DB_FILE):
        db_size_mb = round(os.path.getsize(DB_FILE) / (1024 * 1024), 2)
        
    # 2. Estado de los servicios systemd
    services = ["snmp-poller", "syslog-poller", "flow-poller", "alert-engine"]
    collectors_status = {}
    for srv in services:
        try:
            res = subprocess.run(["systemctl", "is-active", f"{srv}.service"], capture_output=True, text=True)
            collectors_status[srv] = res.stdout.strip()
        except Exception:
            collectors_status[srv] = "unknown"
            
    # 3. Última muestra registrada por cada recolector
    last_samples = {}
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        try:
            cursor.execute("SELECT MAX(timestamp) FROM snmp_data")
            row = cursor.fetchone()
            last_samples["snmp"] = row[0] if row and row[0] else "No data"
        except Exception:
            last_samples["snmp"] = "Error"
            
        try:
            cursor.execute("SELECT MAX(timestamp) FROM syslog_data")
            row = cursor.fetchone()
            last_samples["syslog"] = row[0] if row and row[0] else "No data"
        except Exception:
            last_samples["syslog"] = "Error"
            
        try:
            cursor.execute("SELECT MAX(ts) FROM flow_data")
            row = cursor.fetchone()
            last_samples["flow"] = row[0] if row and row[0] else "No data"
        except Exception:
            last_samples["flow"] = "Error"
    finally:
        conn.close()

    return {
        "db_size_mb": f"{db_size_mb} MB",
        "services": collectors_status,
        "last_samples": last_samples
    }

@app.get('/api/timeline')
def get_timeline(timestamp: str, window: int = 5):
    try:
        # Asegurar compatibilidad de formato ISO para la conversión y corregir espacios
        center_time_str = timestamp.replace('Z', '+00:00').replace(' ', '+')
        center_time = datetime.fromisoformat(center_time_str)
        if center_time.tzinfo is None:
            center_time = center_time.replace(tzinfo=timezone.utc)  # sin zona: se asume UTC
        center_time = center_time.astimezone(timezone.utc)  # los datos se guardan en UTC
        
        # Calcular el marco temporal (rango inicio y fin)
        start_time = (center_time - timedelta(minutes=window)).isoformat()
        end_time = (center_time + timedelta(minutes=window)).isoformat()
        # Los flujos guardan ts como 'YYYY-MM-DD HH:MM:SS.mmm' (UTC, sin zona): se compara en ese mismo formato
        flow_start = start_time[:19].replace('T', ' ')
        flow_end = end_time[:19].replace('T', ' ')
        
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row  # Necesario para convertir registros a diccionarios
        
        # Extraer datos de SNMP
        snmp_data = conn.execute(
            'SELECT * FROM snmp_data WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC', 
            (start_time, end_time)
        ).fetchall()
        
        # Extraer datos de Syslog
        syslog_data = conn.execute(
            'SELECT * FROM syslog_data WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC', 
            (start_time, end_time)
        ).fetchall()
        
        # Extraer datos de Flujos (usando 'ts' en lugar de 'timestamp')
        flow_data = conn.execute(
            'SELECT * FROM flow_data WHERE ts BETWEEN ? AND ? ORDER BY ts ASC LIMIT 2000',
            (flow_start, flow_end)
        ).fetchall()
        
        conn.close()
        
        return {
            "timeline_request": {
                "center": center_time_str,
                "window_minutes": window,
                "range": {"start": start_time, "end": end_time}
            },
            "data": {
                "snmp": [dict(row) for row in snmp_data],
                "syslog": [dict(row) for row in syslog_data],
                "flows": [dict(row) for row in flow_data]
            }
        }

    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de timestamp inválido. Usa formato ISO 8601")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

from alerts_api import router as alerts_router
app.include_router(alerts_router)
from flows_api import router as flows_router
app.include_router(flows_router)

app.mount("/", StaticFiles(directory="/home/admin/static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)