# Design Document - Mini-NOC

## 1. Architecture

El Mini-NOC está diseñado siguiendo una arquitectura modular basada en microservicios independientes, ejecutándose en un contenedor Linux (Debian 13, 2 vCPUs, 2 GB RAM) y gestionado íntegramente mediante unidades de `systemd`.

Se eligió esta arquitectura frente a un diseño monolítico asíncrono para garantizar la **resiliencia**: si uno de los recolectores sufre un fallo (por ejemplo, un pico de memoria o un error de parseo en una trama malformada), el resto de recolectores, la base de datos, el motor de alertas y la interfaz web continúan operando con normalidad y reflejan el fallo en el panel de estado.

```
                         mt-firewall (RouterOS 7.15)
        | SNMP (poll 15s)     | syslog (push)        | IPFIX (push)
        v                     v                      v
   +-------------+      +------------+         +------------+
   | snmp-poller |      |  rsyslog   |         |   nfcapd   |
   +------+------+      +-----+------+         +-----+------+
          |                   | (disco)              | (disco)
          |                   v                      v
          |            +---------------+      +--------------+
          |            | syslog-poller |      | flow-poller  |
          |            +------+--------+      +------+-------+
          |                   |                      |
          +-------------------+----------------------+
                              v
                  +-----------------------+
                  | SQLite (mininuc.db)   |
                  +-----------+-----------+
                      |               |
                      v               v
             +--------------+  +--------------+
             | mininuc-api  |  | alert-engine |
             | (port 8080)  |  |  (eval 30s)  |
             +--------------+  +--------------+
```

Los componentes se dividen en tres capas:

* **Capa de Recolección (Collectors):** Procesos independientes en Python (`snmp-poller`, `syslog-poller`, `flow-poller`).
  * **Ingesta y tolerancia a paradas:** Los scripts de Python no abren sockets de red directamente para Syslog ni IPFIX. En su lugar, los demonios nativos del sistema (`rsyslog` y `nfcapd`) reciben el tráfico y lo persisten en disco (`/var/log/remote/...` y `/var/cache/nfdump/mikrotik/`). Si los servicios del Mini-NOC se detienen o reinician durante un despliegue, los demonios nativos siguen capturando datos sin pérdidas. Al arrancar de nuevo, los recolectores consultan su última posición guardada en `collector_state` y procesan los datos pendientes.
* **Capa de Almacenamiento y Lógica (Storage & Alert Engine):** Una base de datos relacional SQLite compartida (`/home/admin/mininuc.db`) almacena toda la telemetría en **UTC**. Un servicio independiente (`alert-engine`) evalúa cada 30 segundos las reglas configuradas, gestionando el ciclo de vida de cada alerta (`firing` -> `resolved`) sin duplicar notificaciones mientras la condición persista.
* **Capa de Presentación (API & Web Interface):** Un servidor backend (`mininuc-api`) expone los datos mediante una API REST en el puerto `8080` y sirve la interfaz web estática (sin dependencias de CDNs externos, asegurando su funcionamiento aunque se pierda la salida a Internet) mostrando las marcas de tiempo convertidas a hora local (`Europe/Madrid`).

---

## 2. Data Model

El esquema de `/home/admin/mininuc.db` está diseñado para aislar cada dominio de telemetría y mantener la persistencia de estado:

* **`snmp_metrics`**: Almacena las muestras periódicas de estado y tráfico de cada interfaz descubierta dinámicamente (índices no consecutivos).
  * *Campos principales:* `id`, `timestamp` (UTC), `device_ip`, `if_index`, `if_name`, `admin_status`, `oper_status`, `in_octets` (64-bit `ifHCInOctets`), `out_octets` (64-bit `ifHCOutOctets`), `in_bps`, `out_bps`.
  * *Cálculo de tasas:* Las tasas en `bit/s` se calculan a partir de la diferencia entre contadores de 64 bits consecutivos dividida por el intervalo real en segundos. Si un contador decrementa (reinicio del router), se descarta el cálculo para ese intervalo evitando picos falsos.
* **`syslog_events`**: Almacena los eventos enviados por el router.
  * *Campos principales:* `id`, `timestamp` (UTC, normalizado desde la hora local del router que carece de zona horaria), `device_ip`, `facility`, `severity`, `message`.
* **`flow_data`**: Almacena los registros históricos de flujos IPFIX deduplicados.
  * *Campos principales:* `id`, `ts` (inicio UTC), `te` (fin UTC), `pr` (protocolo), `sa` (IP origen), `sp` (puerto origen), `da` (IP destino), `dp` (puerto destino), `pkt` (paquetes), `byt` (bytes), `in_if`, `out_if`, `xsa` (IP origen post-NAT), `xda` (IP destino post-NAT), `real_src`, `real_dst`.
  * *Atribución NAT e Idempotencia:* Resuelve el host interno real (`192.168.153.0/24` o `192.168.154.0/24`) a partir de los campos traducidos `xsa`/`xda`. Incluye una restricción `UNIQUE` sobre la tupla identificativa del flujo para descartar registros duplicados.
* **`collector_state`**: Tabla clave-valor para persistencia operativa (por ejemplo, `flow_last_file` con el último fichero de `nfdump` procesado, el offset de lectura de syslog, y las marcas de tiempo de última ejecución exitosa para la vista *NOC Health*).
* **`alerts`**: Registra el historial y estado activo de las incidencias (`id`, `rule_name`, `target`, `severity`, `state` [`firing`/`resolved`], `start_time`, `end_time`, `details`).
* **`users`**: Almacena las credenciales con hash seguro para el control de acceso a la interfaz web.

### Consultas de la Interfaz Web e Índices

La interfaz web consulta la API principalmente filtrando por **ventanas temporales** (`15 min`, `1 h`, `24 h` y la vista de línea temporal de incidentes) y por **dispositivo/interfaz**. Para evitar escaneos completos de tabla (*full table scans*) y mantener bajo el uso de CPU:
* Se han definido índices sobre la columna temporal (`timestamp` en `snmp_metrics` y `syslog_events`; `ts` en `flow_data`) y sobre `device_ip` / direcciones IP internas.

---

## 3. Storage Estimation (1 Week)

Para validar la viabilidad de SQLite en un entorno con 2 GB de RAM y almacenamiento limitado, se estima el volumen de datos generado durante **7 días** de operación continua basándose en el tamaño en bytes de los tipos de datos de cada tabla y las tasas de muestreo configuradas:

1. **Métricas SNMP (`snmp_metrics`):**
   * *Frecuencia:* 1 muestra cada 15 segundos = 4 muestras/minuto = 240 muestras/hora = **5.760 muestras/día por interfaz**.
   * *Tamaño estimado:* ~100 bytes por fila (timestamp UTC ISO-8601, IPs, strings de interfaz y enteros de 64 bits).
   * *Volumen semanal:* $5.760 \times 100\text{ B} \approx 576\text{ KB/día}$ por interfaz. Para 5 interfaces activas: $\sim 2,88\text{ MB/día} \rightarrow$ **~20,2 MB/semana**.
2. **Eventos Syslog (`syslog_events`):**
   * *Tasa estimada:* ~100 eventos/día en condiciones normales de laboratorio, a ~200 bytes por registro.
   * *Volumen semanal:* $\sim 20\text{ KB/día} \rightarrow$ **~0,14 MB/semana (< 1 MB)**.
3. **Flujos IPFIX (`flow_data`):**
   * *Tasa estimada:* ~2.000 flujos únicos/día tras eliminar duplicados, a ~150 bytes por registro.
   * *Volumen semanal:* $\sim 300\text{ KB/día} \rightarrow$ **~2,1 MB/semana**.
4. **Índices y metadatos SQLite (~25% adicional):** **~5,6 MB/semana**.

**Total estimado:** Una semana completa de monitorización ocupa **menos de 30 MB en disco**, lo que demuestra que SQLite es una solución ligera y más que suficiente para la escala del proyecto.

---

## 4. Design Decisions and Reasons

* **Sondeo SNMP cada 15 segundos:** Se eligió este intervalo porque ofrece suficiente granularidad temporal para detectar caídas breves de interfaz o picos de tráfico dentro de la ventana de evaluación de alertas (2 minutos), manteniendo al mismo tiempo un coste de red y CPU insignificante (apenas 4 ciclos de peticiones UDP por minuto hacia el router MikroTik).
* **Procesamiento incremental en streaming para IPFIX (`subprocess.Popen`):** Inicialmente, leer todo el directorio de `nfdump` de golpe (`nfdump -R`) provocaba picos de consumo de RAM superiores a 3,8 GB, causando el cierre del proceso por falta de memoria (OOM) en el contenedor de 2 GB. Se rediseñó el recolector para procesar únicamente los ficheros nuevos respecto a `collector_state`, leyendo la salida de `nfdump` línea a línea mediante tuberías (`Popen`).
* **Inserción por lotes (`BATCH_SIZE = 1000`):** En lugar de hacer `COMMIT` por cada flujo individual o acumular decenas de miles en memoria, los registros se insertan en transacciones de 1.000 filas. Esto mantiene la memoria acotada (< 30 MB de RSS) y reduce drásticamente el bloqueo de escritura en disco.
* **Separación entre desarrollo (`~/mini-noc`) y producción (`/home/admin`) mediante `deploy.sh`:** Automatiza la copia de código, el ajuste de permisos y el reinicio ordenado de los servicios `systemd`, evitando inconsistencias entre el repositorio Git y los procesos en ejecución.

---

## 5. Known Limitations & Scalability (100x Larger Network)

### Limitaciones conocidas en el entorno actual
* **Contención de escritura en SQLite:** Al compartir un único fichero `mininuc.db` entre tres recolectores concurrentes, el motor de alertas y la API, pueden producirse esperas puntuales por bloqueo de escritura (`database is locked`) durante ráfagas intensas de flujos. Se mitiga mediante un `timeout` de conexión y escritura por lotes.
* **Sobrecarga inicial por duplicados históricos en origen:** Debido a cambios previos en la configuración de exportación IPFIX del router, los ficheros antiguos en caché contienen flujos repetidos. Aunque la restricción `UNIQUE` impide que se dupliquen en la base de datos o distorsionen las gráficas, el parser debe procesarlos la primera vez que lee esos ficheros.
* **Crecimiento indefinido sin purga activa:** Si se desactiva la tarea de retención automática, el historial crecerá sin límite hasta agotar el espacio disponible en el contenedor.

### ¿Dónde fallaría primero esta arquitectura si la red fuera 100 veces más grande?

Si escaláramos este diseño de 1–2 dispositivos a **100–200 routers** con un volumen de telemetría 100 veces superior, la arquitectura fallaría en el siguiente orden:

1. **Bloqueos de escritura en SQLite (Primer punto de ruptura):** SQLite solo permite un escritor simultáneo a nivel de base de datos. Con 100 recolectores insertando métricas SNMP cada 15 segundos más 100 veces el volumen de Syslog e IPFIX de forma concurrente, la cola de escrituras saturaría el bloqueo del archivo, provocando errores `database is locked` y pérdida de muestras. *(Solución: migrar a una base de datos cliente-servidor orientada a series temporales o búsqueda distribuida, como Elasticsearch o PostgreSQL/TimescaleDB).*
2. **Saturación del ciclo de sondeo SNMP y procesos `nfdump`:** Un sondeo secuencial de 100 routers tardaría más que la ventana de 15 segundos (provocando solapamiento de ciclos), y lanzar múltiples subprocesos `nfdump` simultáneos saturaría las 2 vCPUs asignadas. *(Solución: paralelización asíncrona distribuida y colectores de flujos nativos en memoria/streaming).*
3. **Agotamiento de almacenamiento e I/O en un único nodo:** Pasar de ~30 MB/semana a más de 3 GB/semana (o decenas de GB en redes reales con mucho tráfico) en un único contenedor degradaría los tiempos de respuesta de las consultas agregadas de `Top Talkers` en la interfaz web.