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

cp "$REPO"/153/*.service /etc/systemd/system/
systemctl daemon-reload
echo "Desplegado. Reinicia con: systemctl restart snmp-poller syslog-poller flow-poller alert-engine mininuc-api"
