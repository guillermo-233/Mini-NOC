#!/bin/bash
# Despliega el Mini-NOC desde el repositorio a /home/admin e instala los servicios systemd.
# Uso (como root): ./deploy.sh
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/admin

install -d -o admin -g admin "$DEST" "$DEST/static"
cp "$REPO"/153/*.py "$DEST"/
cp "$REPO"/153/static/* "$DEST/static/"
chown admin:admin "$DEST"/*.py
chown -R admin:admin "$DEST/static"

# La base de datos debe pertenecer al usuario con el que corren los servicios.
touch "$DEST/mininuc.db"
chown root:root "$DEST"/mininuc.db*

shopt -s nullglob
cp "$REPO"/153/*.service "$REPO"/153/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nfcapd-cleanup.timer
echo "Desplegado. Reinicia con: systemctl restart snmp-poller syslog-poller flow-poller alert-engine mininuc-api"
