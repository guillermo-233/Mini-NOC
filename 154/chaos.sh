#!/bin/bash
# Script de caos del Mini-NOC. Se ejecuta desde d13-basic-154.
# Provoca incidentes conocidos y escribe la hora UTC exacta de cada uno,
# para contrastarlos luego con el dashboard y con la base de datos.
#
# Requisitos previos:
#   - en 153:  ./serve_testfile.sh start     (sirve un fichero de 100 MiB)
#   - en el router: interfaz de prueba   /interface vlan add name=test-down vlan-id=99 interface=net154
#   - en 154:  sshpass, wget, curl
#
# Uso:  ROUTER_PASS='...' ./chaos.sh [all|marker|logins|nat|traffic|iface]
set -u

ROUTER_IP="${ROUTER_IP:-192.168.153.1}"
ROUTER_USER="${ROUTER_USER:-admin}"
: "${ROUTER_PASS:?Define ROUTER_PASS (contraseña del router) en el entorno}"
SERVER_URL="${SERVER_URL:-http://192.168.153.4:8000/test100M.bin}"
FILE_BYTES="${FILE_BYTES:-104857600}"
RATE="${RATE:-1m}"                  # límite de wget: 1 MB/s = ~8,4 Mbit/s
FAILED_LOGINS="${FAILED_LOGINS:-8}"
TEST_IFACE="${TEST_IFACE:-test-down}"
IFACE_DOWN_SECONDS="${IFACE_DOWN_SECONDS:-75}"
NAT_TARGET="${NAT_TARGET:-speedtest.tele2.net}"
PAUSE="${PAUSE:-20}"
PHASE="${1:-all}"

case "$PHASE" in all|marker|logins|nat|traffic|iface) ;; *)
  echo "Uso: $0 [all|marker|logins|nat|traffic|iface]" >&2; exit 2 ;; esac
case "$TEST_IFACE" in ether*|sfp*|wlan*|bridge*|WAN*)
  echo "TEST_IFACE=$TEST_IFACE no permitido: solo la VLAN de prueba (test-down)." >&2; exit 2 ;; esac

LOG="chaos_$(date -u +%Y%m%dT%H%M%SZ).log"
MY_IP="$(ip -4 route get "$(echo "$SERVER_URL" | sed -E 's#https?://([^:/]+).*#\1#')" 2>/dev/null | sed -nE 's/.* src ([0-9.]+).*/\1/p' | head -1)"
START="$(date -u +'%Y-%m-%d %H:%M:%S')"
FAILED=0
IFACE_DISABLED=0

now() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
say() { echo "[$(now)] $*" | tee -a "$LOG"; }
rt()  { SSHPASS="$ROUTER_PASS" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
          -o PubkeyAuthentication=no "$ROUTER_USER@$ROUTER_IP" "$@"; }

restore_iface() {
  if [ "$IFACE_DISABLED" = 1 ]; then
    say "Restaurando $TEST_IFACE (salida del script)"
    rt "/interface enable $TEST_IFACE" >/dev/null 2>&1 && IFACE_DISABLED=0
  fi
}
trap restore_iface EXIT
trap 'echo "Interrumpido." >&2; exit 130' INT TERM

phase_marker() {
  say "MARCADOR: inicio del caos (el syslog del router debe mostrarlo)"
  rt "/log info message=\"CHAOS marcador $(now)\"" >/dev/null 2>&1 || { say "ERROR: no se pudo escribir el marcador"; FAILED=1; }
}

phase_logins() {
  say "INCIDENTE logins: $FAILED_LOGINS intentos SSH fallidos (usuario fakeuser) + 1 con nombre HTML"
  for i in $(seq 1 "$FAILED_LOGINS"); do
    SSHPASS="mala-$i" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
      -o PubkeyAuthentication=no fakeuser@"$ROUTER_IP" "exit" >/dev/null 2>&1
    sleep 1
  done
  SSHPASS="mala" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
    -o PubkeyAuthentication=no -l '<b>negrita</b>' "$ROUTER_IP" "exit" >/dev/null 2>&1
  say "Fin logins fallidos (esperado: alerta Login Failures si >5 en 1 min)"
}

phase_nat() {
  say "INCIDENTE nat: 3 conexiones salientes a $NAT_TARGET (flujos con SNAT)"
  for i in 1 2 3; do curl -s -m 5 -o /dev/null "http://$NAT_TARGET/" ; done
  say "Fin nat (el host atribuido en Top Talkers debe ser $MY_IP, no la IP WAN)"
}

phase_traffic() {
  say "Comprobando $SERVER_URL"
  if ! curl -sI -m 5 "$SERVER_URL" | head -1 | grep -q 200; then
    say "ERROR: el servidor no responde 200. En 153: ./serve_testfile.sh start"; FAILED=1; return
  fi
  say "INCIDENTE traffic: descarga de $FILE_BYTES B limitada a $RATE/s"
  wget -q -O /dev/null --limit-rate="$RATE" "$SERVER_URL" || { say "ERROR: wget falló"; FAILED=1; }
  WIRE_BYTES=$(awk -v b="$FILE_BYTES" 'BEGIN{printf "%d", b*1.0465}')
  say "Fin traffic (esperado en la interfaz ~$WIRE_BYTES B con cabeceras)"
}

phase_iface() {
  if [ "$(rt "/interface print count-only where name=$TEST_IFACE" 2>/dev/null | tr -d '\r\n ')" != "1" ]; then
    say "ERROR: no existe la interfaz $TEST_IFACE. Créala: /interface vlan add name=test-down vlan-id=99 interface=net154"
    FAILED=1; return
  fi
  say "INCIDENTE iface: deshabilito $TEST_IFACE"
  rt "/interface disable $TEST_IFACE" >/dev/null 2>&1 && IFACE_DISABLED=1 || { say "ERROR: no se pudo deshabilitar"; FAILED=1; return; }
  sleep "$IFACE_DOWN_SECONDS"
  say "INCIDENTE iface: habilito $TEST_IFACE"
  rt "/interface enable $TEST_IFACE" >/dev/null 2>&1 && IFACE_DISABLED=0 || { say "ERROR: no se pudo habilitar"; FAILED=1; }
}

print_summary() {
  END="$(date -u +'%Y-%m-%d %H:%M:%S')"
  cat <<EOT | tee -a "$LOG"

=== RESUMEN (UTC) inicio=$START fin=$END, fase=$PHASE, origen=$MY_IP ===
Comprobaciones en 153 (sqlite3 /home/admin/mininuc.db):
  alertas:   SELECT id,rule_name,entity,status,start_time,end_time FROM alerts WHERE start_time>='$START' ORDER BY id;
  syslog:    SELECT timestamp,severity,message FROM syslog_data WHERE timestamp>='$START' ORDER BY timestamp;
  area SNMP: SELECT SUM(out_bps/8*15.0) FROM snmp_data WHERE descr='net154' AND timestamp>='$START';
  flujos:    SELECT SUM(byt) FROM flow_data WHERE da='$MY_IP' AND ts>='$START';
  NAT:       SELECT real_src,COUNT(*) FROM flow_data WHERE sa='$MY_IP' AND da NOT LIKE '192.168.15%' AND ts>='$START' GROUP BY 1;
Dashboard: alerta Interface Down y resuelta, Login Failures, gráfica con meseta ~8,4 Mbit/s,
           Top Talkers con el host interno correcto, logs con <b>negrita</b> como texto.
EOT
}

say "=== INICIO CHAOS (fase: $PHASE) ==="
case "$PHASE" in
  all) phase_marker; sleep "$PAUSE"; phase_logins; sleep "$PAUSE"; phase_nat; sleep "$PAUSE"; phase_traffic; sleep "$PAUSE"; phase_iface ;;
  marker) phase_marker ;; logins) phase_logins ;; nat) phase_nat ;; traffic) phase_traffic ;; iface) phase_iface ;;
esac
say "=== FIN CHAOS ==="
print_summary
[ "$FAILED" = 0 ] || exit 1
