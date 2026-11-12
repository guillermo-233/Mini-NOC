# Mini-NOC

Este repositorio contiene el código, los scripts y las unidades de `systemd` necesarios para desplegar un Mini-NOC (Network Operations Center). El sistema incluye recolectores de datos (SNMP, Syslog, flujos IPFIX), almacenamiento en base de datos SQLite, un motor de alertas, una API y una interfaz web.

## Requisitos Previos
* Sistema operativo Linux (entorno Cyberrange / Debian).
* Paquetes básicos instalados: `git`, `curl`, `python3-venv` y `nfdump`.
* Permisos de superusuario (`root` o `sudo`).

## Instalación desde cero

1. **Preparar el entorno de despliegue:**
   Instala las dependencias necesarias en la máquina de destino:
   ```bash
   apt update
   apt install git curl python3-venv
   ```

2. **Configurar acceso al repositorio:**
   Genera una clave SSH de despliegue para clonar el repositorio privado de GitHub:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/noc_deploy -N "" -C "mini-noc deploy key"
   cat ~/.ssh/noc_deploy.pub
   ```
   *Copia la salida del último comando y añádela en GitHub: **Settings** > **Deploy keys** > **Add deploy key**.*

3. **Clonar el proyecto:**
   Configura SSH para que utilice la clave generada y clona el repositorio:
   ```bash
   cat >> ~/.ssh/config <<'EOF'
   Host github.com
       IdentityFile ~/.ssh/noc_deploy
       IdentitiesOnly yes
   EOF
   
   git clone git@github.com:<tu-usuario>/<tu-repo>.git ~/mini-noc
   ```

4. **Ejecutar el script de despliegue:**
   El proyecto incluye el script `deploy.sh` que automatiza la instalación. Este script copia los archivos a `/home/admin`, asigna los permisos correctos, inicializa la base de datos `mininuc.db` y carga los servicios en el sistema.
   ```bash
   cd ~/mini-noc
   ./deploy.sh
   ```

## Configuración

* **Base de datos:** Se inicializa automáticamente en la ruta `/home/admin/mininuc.db` durante el despliegue.
* **Creación de usuarios:** Para configurar el control de acceso a la interfaz web, dirígete al directorio de producción y ejecuta el script de creación de usuarios:
  ```bash
  cd /home/admin
  python3 create_user.py
  ```
* **Parámetros de recolectores:** Las redes internas supervisadas (ej. `192.168.153.0/24`) y los intervalos de tiempo (ej. `POLL_INTERVAL`) se configuran directamente en la cabecera de los scripts de recolección (`flow_poller.py`, `poller.py`, etc.). Si realizas cambios en tu equipo local, súbelos a GitHub, ejecuta `git pull` en la máquina y vuelve a lanzar `./deploy.sh`.

## Ejecución y Gestión de Servicios

El script de despliegue configura e inicia el Mini-NOC para que funcione de forma ininterrumpida en segundo plano mediante `systemd`. 

Los servicios que componen el sistema son:
* `snmp-poller`: Recolector de métricas de routers.
* `syslog-poller`: Recolector de registros de eventos.
* `flow-poller`: Recolector de tráfico IPFIX (`nfdump`).
* `alert-engine`: Motor de evaluación de reglas.
* `nfcapd-cleanup.timer`: purga diaria (03:30) de ficheros `nfcapd` de más de 7 días.
* `mininuc-api`: Backend de la interfaz web.

Puedes gestionar la ejecución de cualquiera de estos módulos utilizando los comandos estándar de `systemctl`:

```bash
# Ver el estado de todos los servicios principales
systemctl status snmp-poller syslog-poller flow-poller alert-engine mininuc-api

# Detener o arrancar un servicio específico (ej. en caso de consumo excesivo)
systemctl stop flow-poller
systemctl start flow-poller

# Reiniciar todos los módulos tras un cambio manual en base de datos
systemctl restart snmp-poller syslog-poller flow-poller alert-engine mininuc-api
```

La interfaz web está en `http://<ip-de-153>:8080` y pide usuario y contraseña (créalos con `create_user.py`).

## Variables de entorno opcionales

Se definen en un *drop-in* de systemd (`systemctl edit alert-engine`, bloque `[Service]` con `Environment=...`).

| Variable | Servicio | Por defecto | Efecto |
|---|---|---|---|
| `NOC_BW_THRESHOLD_BPS` | alert-engine | 80000000 | Umbral de *High Bandwidth* (bit/s) |
| `NOC_BW_MINUTES` | alert-engine | 2 | Ventana de la regla de ancho de banda |
| `NOC_STALE_SNMP` / `NOC_STALE_IPFIX` / `NOC_STALE_SYSLOG` | alert-engine | 180 / 300 / off | Segundos sin datos para *No Data* (`0` u `off` la desactiva) |
| `NOC_NFCAPD_DAYS` | nfcapd-cleanup | 7 | Días de ficheros `nfcapd` retenidos |
| `NOC_SESSION_SECONDS`, `NOC_COOKIE_SECURE`, `NOC_TLS_CERT`, `NOC_TLS_KEY` | mininuc-api | 8 h / off / – | Sesión web y HTTPS opcional |

Probar la purga sin borrar: `python3 /home/admin/nfcapd_cleanup.py --dry-run`.

## Pruebas

Tests unitarios (desde `/home/admin`): `python3 -m unittest discover -p 'test_*.py'`.

## Script de caos (verificación de extremo a extremo)

Provoca incidentes conocidos desde `d13-basic-154` e imprime la hora UTC de cada uno, además de las consultas SQL para comprobarlos.

```bash
# En 153: servir el fichero de 100 MiB para la prueba de tráfico
cd ~/mini-noc/153 && ./serve_testfile.sh start

# En el router (una vez): VLAN de prueba. Nunca se usa ether4.
/interface vlan add name=test-down vlan-id=99 interface=net154

# En 154:
ROUTER_PASS='<contraseña>' ./chaos.sh            # todas las fases
ROUTER_PASS='<contraseña>' ./chaos.sh traffic    # o una sola: marker|logins|nat|traffic|iface
```

Fases: marcador en el syslog, 8 logins fallidos (más uno con nombre HTML), tráfico NAT hacia Internet, descarga de 100 MiB a ~8 Mbit/s y caída/subida de `test-down`. Al terminar, `./serve_testfile.sh stop`.
