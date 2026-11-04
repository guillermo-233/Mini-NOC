#!/bin/bash
# Ejecutar desde d13-basic-154

ROUTER_IP="192.168.153.1" # Cambia por la IP de tu router si es distinta

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] === INICIANDO SCRIPT DE CAOS ==="

# 1. Ráfaga de tráfico (Descargando un archivo de gran tamaño)
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] 1. Generando ráfaga de tráfico TCP..."
wget -qO /dev/null http://speedtest.tele2.net/10MB.zip
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')]    Ráfaga finalizada."

# 2. Inicios de sesión fallidos
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] 2. Provocando fallos de inicio de sesión SSH..."
for i in {1..3}; do
  sshpass -p "badpassword" ssh -o StrictHostKeyChecking=no fakeuser@$ROUTER_IP "exit" 2>/dev/null
done
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')]    Intentos fallidos completados."

# 3. Interfaz Caída/Levantada
# Asegúrate de haber creado la VLAN test-down previamente en el router:
# /interface vlan add name=test-down vlan-id=99 interface=net154
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] 3. Tirando la interfaz test-down..."
sshpass -p "a85A7B8S" ssh -o StrictHostKeyChecking=no admin@$ROUTER_IP "/interface disable test-down" 2>/dev/null

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')]    Esperando 30 segundos para que el poller SNMP lo note..."
sleep 30

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')]    Levantando la interfaz test-down..."
sshpass -p "a85A7B8S" ssh -o StrictHostKeyChecking=no admin@$ROUTER_IP "/interface enable test-down" 2>/dev/null

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] === FIN DEL SCRIPT DE CAOS ==="
