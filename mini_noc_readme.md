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
* **Parámetros de recolectores:** Las redes internas supervisadas (ej. `192.168.153.0/24`) y los intervalos de tiempo (ej. `POLL_INTERVAL`) se configuran directamente en la cabecera de los scripts de recolección (`flow_poller.py`, `snmp_poller.py`, etc.). Si realizas cambios en tu equipo local, súbelos a GitHub, ejecuta `git pull` en la máquina y vuelve a lanzar `./deploy.sh`.

## Ejecución y Gestión de Servicios

El script de despliegue configura e inicia el Mini-NOC para que funcione de forma ininterrumpida en segundo plano mediante `systemd`. 

Los servicios que componen el sistema son:
* `snmp-poller`: Recolector de métricas de routers.
* `syslog-poller`: Recolector de registros de eventos.
* `flow-poller`: Recolector de tráfico IPFIX (`nfdump`).
* `alert-engine`: Motor de evaluación de reglas.
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