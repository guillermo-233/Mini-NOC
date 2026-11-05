from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import datetime
from datetime import datetime, timedelta, timezone

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
    conn = sqlite3.connect(DB_FILE)
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
    
    # Esta consulta asume que guardas el estado en snmp_data. 
    # Deberás ajustarla a tu esquema real si es diferente.
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
        # Indicamos explícitamente que la columna de tiempo se llama 'ts'
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
    # Convertir el rango de tiempo seleccionado a segundos
    if range == "15m":
        time_limit = 15 * 60
    elif range == "1h":
        time_limit = 60 * 60
    elif range == "24h":
        time_limit = 24 * 60 * 60
    else:
        time_limit = 15 * 60

    # Conectar a la base de datos de Mini-NOC
    conn = sqlite3.connect('/home/admin/mininuc.db')
    cursor = conn.cursor()
    
    # Calcular la fecha y hora límite restando los segundos al momento actual en UTC
    limit_time = (datetime.now(timezone.utc) - timedelta(seconds=time_limit)).isoformat()
    
    # Extraer los datos históricos ya calculados por el recolector
    cursor.execute('''
        SELECT descr, timestamp, in_bps, out_bps 
        FROM snmp_data 
        WHERE timestamp >= ? 
        ORDER BY timestamp ASC
    ''', (limit_time,))
    
    rows = cursor.fetchall()
    conn.close()

    # Formatear la respuesta agrupada por interfaz para Chart.js
    results = {}
    for descr, timestamp, in_bps, out_bps in rows:
        if descr not in results:
            results[descr] = {"in": [], "out": [], "timestamps": []}
            
        # Extraer solo la hora (HH:MM:SS) del timestamp ISO para limpiar el eje X del gráfico
        formatted_time = timestamp[11:19]
        
        results[descr]["timestamps"].append(formatted_time)
        results[descr]["in"].append(in_bps)
        results[descr]["out"].append(out_bps)

    return results

app.mount("/", StaticFiles(directory="/home/admin/static", html=True), name="static")
if __name__ == "__main__":
    import uvicorn
    # Se ejecuta en todas las interfaces en el puerto 8080
    uvicorn.run(app, host="0.0.0.0", port=8080)

