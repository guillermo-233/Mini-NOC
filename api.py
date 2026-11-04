from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import datetime

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

def get_freshness(cursor, table_name, time_column="ts"):
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
        SELECT ifIndex, descr, ifOperStatus, in_bps, out_bps, ts
        FROM snmp_data
        WHERE (ifIndex, ts) IN (
            SELECT ifIndex, MAX(ts)
            FROM snmp_data
            GROUP BY ifIndex
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
    
    query = "SELECT ts, host, tag, message FROM syslog_data ORDER BY ts DESC LIMIT ?"
    try:
        cursor.execute(query, (limit,))
        rows = cursor.fetchall()
        freshness = get_freshness(cursor, "syslog_data")
        
        data = [dict(row) for row in rows]
        return {"freshness": freshness, "data": data}
    finally:
        conn.close()

@app.get("/api/ipfix/top-talkers")
def get_top_talkers(
    minutes: int = Query(5, description="Ventana de tiempo en minutos")
):
    """
    Devuelve las IPs internas que más tráfico han generado en los últimos N minutos.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculamos la fecha límite restando N minutos a la hora UTC actual
    time_limit = (datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
    
    # Agrupamos por la IP real de origen
    query = """
        SELECT real_src, SUM(byt) as total_bytes
        FROM flow_data
        WHERE ts >= ? AND real_src != '0.0.0.0'
        GROUP BY real_src
        ORDER BY total_bytes DESC
        LIMIT 10
    """
    
    try:
        cursor.execute(query, (time_limit,))
        rows = cursor.fetchall()
        freshness = get_freshness(cursor, "flow_data")
        
        data = [dict(row) for row in rows]
        return {"freshness": freshness, "data": data}
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # Se ejecuta en todas las interfaces en el puerto 8080
    uvicorn.run(app, host="0.0.0.0", port=8080)
