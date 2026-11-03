import time
import sqlite3
import re
import os

# Rutas de los archivos (ajusta LOG_FILE según dónde guarde rsyslog el archivo en tu Debian)
LOG_FILE = '/var/log/remote/192.168.153.1/syslog.log'
DB_FILE = 'mininuc.db'

# Expresión regular basada en el formato de tus logs
# Captura: 1(Timestamp), 2(IP), 3(Facility.Severity), 4(Mensaje)
LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\s+([\d\.]+)\s+\S+\s+([a-zA-Z0-9]+\.[a-zA-Z0-9]+)\s+(.*)$')

def setup_database():
    """Crea la base de datos y la tabla si no existen."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS syslog_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            source_ip TEXT,
            severity TEXT,
            message TEXT
        )
    ''')
    conn.commit()
    return conn

def follow(file):
    """Generador que emula 'tail -f' para leer nuevas líneas a medida que se escriben."""
    file.seek(0, os.SEEK_END)
    while True:
        line = file.readline()
        if not line:
            time.sleep(0.5)
            continue
        yield line

def main():
    conn = setup_database()
    cursor = conn.cursor()
    
    print(f"Iniciando recolector de syslog. Escuchando nuevas líneas en {LOG_FILE}...")
    
    try:
        with open(LOG_FILE, 'r') as f:
            for line in follow(f):
                match = LOG_PATTERN.match(line)
                if match:
                    timestamp = match.group(1)
                    source_ip = match.group(2)
                    severity = match.group(3)
                    message = match.group(4)
                    
                    cursor.execute('''
                        INSERT INTO syslog_data (timestamp, source_ip, severity, message)
                        VALUES (?, ?, ?, ?)
                    ''', (timestamp, source_ip, severity, message))
                    conn.commit()
                    
                    # Salida útil para la consola y journalctl
                    print(f"[{timestamp}] {severity} de {source_ip}: {message}", flush=True)
    except FileNotFoundError:
        print(f"Error: No se encuentra el archivo {LOG_FILE}. Asegúrate de que rsyslog lo está creando.")
    except KeyboardInterrupt:
        print("\nDeteniendo el recolector...")
    finally:
        conn.close()

if __name__ == '__main__':
    main()
