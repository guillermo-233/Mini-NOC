#!/bin/bash
# Sirve un fichero de 100 MiB en 192.168.153.4:8000 para la fase "traffic" de chaos.sh.
# Uso: ./serve_testfile.sh start|stop|status
DIR=/tmp/noc-testfile; PIDF=/tmp/noc-testfile.pid; BIND=192.168.153.4; PORT=8000
case "${1:-status}" in
  start)
    mkdir -p "$DIR"; [ -f "$DIR/test100M.bin" ] || dd if=/dev/urandom of="$DIR/test100M.bin" bs=1M count=100 status=none
    [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && { echo "ya en marcha"; exit 0; }
    (cd "$DIR" && nohup python3 -m http.server "$PORT" --bind "$BIND" >/dev/null 2>&1 & echo $! > "$PIDF")
    echo "sirviendo http://$BIND:$PORT/test100M.bin" ;;
  stop)
    [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -rf "$PIDF" "$DIR"; echo parado ;;
  status)
    [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && echo "en marcha (pid $(cat "$PIDF"))" || echo "parado" ;;
  *) echo "Uso: $0 start|stop|status" >&2; exit 2 ;;
esac
