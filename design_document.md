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

* **`snmp_data`**: muestras periódicas (cada 15 s) de cada interfaz, descubierta dinámicamente (los `ifIndex` no son consecutivos).
  * *Campos:* `timestamp` (UTC), `if_index`, `descr` (nombre), `admin_status`, `oper_status` (`'1'` up / `'2'` down), `in_bytes`, `out_bytes` (contadores de 64 bits `ifHCInOctets`/`ifHCOutOctets`), `in_bps`, `out_bps`.
  * *Cálculo de tasas:* `delta × 8 / segundos reales transcurridos`, medidos con `time.monotonic()` y no con los 15 s nominales (con 15 s fijos las tasas salían un 4,7 % altas, porque el ciclo real dura ~15,7 s). Si un contador decrece (reinicio del router) la tasa queda a `NULL` en ese intervalo para no inventar un pico.
* **`snmp_device`**: una fila con el estado del dispositivo: `sysUpTime`, alcanzabilidad y última respuesta (panel de dispositivo).
* **`syslog_data`**: `id`, `timestamp` (UTC), `source_ip`, `severity` (se guarda como `local0.err`; la API la separa en facilidad y severidad), `message`.
  * *Zona horaria:* el router envía la hora local de Madrid etiquetada como `+00:00`. El recolector la reinterpreta como `Europe/Madrid` y la convierte a UTC (en horario de verano y de invierno).
* **`flow_data`**: flujos IPFIX deduplicados.
  * *Campos:* `id`, `ts`/`te` (inicio/fin UTC), `pr`, `sa`, `sp`, `da`, `dp`, `pkt`, `byt`, `in_if`, `out_if`, `xsa`, `xda` (direcciones post-NAT), `real_src`, `real_dst`, `src_internal`, `dst_internal`.
  * *Atribución NAT:* `real_src`/`real_dst` se calculan eligiendo, entre la dirección original y la traducida, la que pertenece a `INTERNAL_NETS` (`192.168.153.0/24`, `192.168.154.0/24`). Con SNAT (salida a Internet) `xsa` es la IP WAN (`192.168.100.56`), así que el host real es `sa`; con DNAT (entrada) `xda` es el host interno. Un primer diseño elegía siempre la dirección traducida y atribuía todo el tráfico saliente a la IP WAN.
  * *Idempotencia:* `UNIQUE(ts, te, pr, sa, sp, da, dp, pkt, byt, in_if, out_if, xsa, xda)` + `INSERT OR IGNORE`: reprocesar un fichero no duplica nada.
* **`collector_state`**: Tabla clave-valor para persistencia operativa (por ejemplo, `flow_last_file` con el último fichero de `nfdump` procesado, el offset de lectura de syslog, y las marcas de tiempo de última ejecución exitosa para la vista *NOC Health*).
* **`collector_status`**: latido de cada recolector (`last_run`, `last_success`, `last_error`, `errors`, `items_total`); alimenta la página de salud, la alerta *Collector Down* y los mensajes de "por qué está vacío este panel".
* **`alerts`**: historial y estado de las incidencias (`id`, `rule_name`, `severity`, `status` [`firing`/`resolved`], `entity`, `message`, `start_time`, `end_time`).
* **`users`**: `name`, `pw_hash`, `created`; control de acceso a la interfaz web (login con cookie de sesión firmada; la API responde 401 sin sesión).

### Consultas de la Interfaz Web e Índices

La interfaz web consulta la API principalmente filtrando por **ventanas temporales** (`15 min`, `1 h`, `24 h` y la vista de línea temporal de incidentes) y por **dispositivo/interfaz**. Para evitar escaneos completos de tabla (*full table scans*) y mantener bajo el uso de CPU:
* Índices: `idx_snmp_ts(timestamp)`, `idx_snmp_if_ts(if_index, timestamp)` (gráfica por interfaz), `idx_syslog_ts(timestamp)`, `idx_flow_ts(ts)`, `idx_flow_rsrc_ts(real_src, ts)`, `idx_flow_rdst_ts(real_dst, ts)` (Top Talkers por host interno) e `idx_alerts_state(status, rule_name)` (el motor de alertas busca la alerta activa de cada regla).

---

## 3. Storage Estimation (1 Week)

La estimación se hace con **medidas reales** del laboratorio, no solo con tamaños teóricos:

| Tabla | Tasa | Tamaño/fila | 1 semana |
|---|---|---|---|
| `snmp_data` | 5.760 filas/día por interfaz × ~5 interfaces | ~100 B | ~20 MB |
| `syslog_data` | ~100–500 eventos/día | ~200 B | < 1 MB |
| `flow_data` | **117.000–130.000 flujos/día** (medido) | **~451 B** (medido, con índices) | **~350–390 MiB** |

* **Flujos (medida):** 331.535 filas ocupaban 142,7 MiB → ~451 B/fila. Los índices son el 64 % del espacio y el índice `UNIQUE` por sí solo 45,6 MB. Una estimación inicial de ~2.000 flujos/día y 150 B/fila era dos órdenes de magnitud baja: el router exporta todos los flujos de la red de laboratorio, no solo los "interesantes".
* **Ficheros `nfcapd`:** ~28 KB por fichero (uno por minuto) → ~40 MB/día; el archivo llegó a 966 MB antes de añadir la purga (`nfcapd_cleanup.py`, 7 días).
* **Total:** una semana ocupa **~400 MiB en SQLite** más ~300 MB de `nfcapd` retenido. Cabe sobradamente en el contenedor, pero **dominan los flujos**, no las métricas SNMP.
* **Memoria:** el `flow-poller` usa ~10 MB de memoria anónima; el pico de 159 MB que se vio era caché de página, no RSS del proceso.

---

## 4. Design Decisions and Reasons

* **Sondeo SNMP cada 15 segundos:** Se eligió este intervalo porque ofrece suficiente granularidad temporal para detectar caídas breves de interfaz o picos de tráfico dentro de la ventana de evaluación de alertas (2 minutos), manteniendo al mismo tiempo un coste de red y CPU insignificante (apenas 4 ciclos de peticiones UDP por minuto hacia el router MikroTik).
* **Procesamiento incremental en streaming para IPFIX (`subprocess.Popen`):** Inicialmente, leer todo el directorio de `nfdump` de golpe (`nfdump -R`) provocaba picos de consumo de RAM superiores a 3,8 GB, causando el cierre del proceso por falta de memoria (OOM) en el contenedor de 2 GB. Se rediseñó el recolector para procesar únicamente los ficheros nuevos respecto a `collector_state`, leyendo la salida de `nfdump` línea a línea mediante tuberías (`Popen`).
* **Inserción por lotes (`BATCH_SIZE = 1000`):** En lugar de hacer `COMMIT` por cada flujo individual o acumular decenas de miles en memoria, los registros se insertan en transacciones de 1.000 filas. Esto mantiene la memoria acotada (< 30 MB de RSS) y reduce drásticamente el bloqueo de escritura en disco.
* **Separación entre desarrollo (`~/mini-noc`) y producción (`/home/admin`) mediante `deploy.sh`:** Automatiza la copia de código, el ajuste de permisos y el reinicio ordenado de los servicios `systemd`, evitando inconsistencias entre el repositorio Git y los procesos en ejecución.
* **Tasas SNMP con tiempo real:** el intervalo efectivo es ~15,7 s (petición + espera). Dividir por 15 daba un 4,7 % de más; se verificó descargando un fichero de 104.857.600 B: esperado ≈109,64 MB en la interfaz (con cabeceras), delta de contadores 109.736.157, área bajo la curva 109.731.097.
* **Atribución NAT por redes internas** (ver §2) en vez de asumir que la dirección traducida es la interna.
* **Zona horaria:** todo se guarda en UTC; solo la interfaz convierte a `Europe/Madrid`. El syslog se corrige en la ingesta porque el router etiqueta mal la zona.
* **Tolerancia a paradas y rotación (syslog):** se guarda inodo + offset en `collector_state`; si el inodo cambia se termina de leer `syslog.log.1` antes de seguir con el nuevo, sin perder ni repetir líneas.
* **Servicios como `root`:** la base de datos tiene un único propietario, evitando `attempt to write a readonly database` entre servicios con usuarios distintos. `deploy.sh` crea la BD con ese propietario.
* **Seguridad:** acceso con login y sesión firmada; SNMPv3 authPriv (`SHA` + `AES`); los logs se pintan con `textContent` (un usuario fallido llamado `<b>negrita</b>` no inyecta HTML); sin CORS abierto; sin CDNs externos.
* **Motor de alertas (cada 30 s, ciclo de vida `firing`→`resolved` sin duplicados, cada regla aislada para que un fallo no pare las demás):**
  * *Interface Down:* `oper_status = 2` en interfaces no ignoradas (`ether4`, la de gestión, está excluida).
  * *High Bandwidth:* **todas** las muestras de la ventana por encima del umbral y al menos el 60 % de las esperadas (evita falsos positivos por un solo pico o por huecos). `NOC_BW_THRESHOLD_BPS` (80 Mbit/s) y `NOC_BW_MINUTES` (2).
  * *Login Failures:* más de 5 fallos en 1 minuto.
  * *No Data* por fuente: SNMP 180 s, IPFIX 300 s, syslog desactivada por defecto (el router puede estar legítimamente callado); `NOC_STALE_*`.
  * *Collector Down:* servicio parado, confirmado en 2 ciclos consecutivos. Es **redundante a propósito** con *No Data* (causa vs. impacto).
* **Retención de 7 días** de datos en SQLite (motor de alertas) y de ficheros `nfcapd` (temporizador `systemd` diario a las 03:30, `--dry-run` disponible).
* **Verificación:** unitaria (11 ficheros de test) y de extremo a extremo con `154/chaos.sh`, que provoca tráfico, fallos de login, tráfico NAT y la caída de la VLAN `test-down` imprimiendo la hora UTC de cada incidente. Las alertas de datos obsoletos y de recolector caído se verificaron parando servicios a mano (p. ej. `flow-poller` parado el 8-nov a las 20:09: *No Data* IPFIX se resolvió tras reiniciar y ponerse al día con 3.146 ficheros en ~2,5 min).

---

## 5. Known Limitations & Scalability (100x Larger Network)

### Limitaciones conocidas en el entorno actual
* **Contención de escritura en SQLite:** Al compartir un único fichero `mininuc.db` entre tres recolectores concurrentes, el motor de alertas y la API, pueden producirse esperas puntuales por bloqueo de escritura (`database is locked`) durante ráfagas intensas de flujos. Se mitiga mediante un `timeout` de conexión y escritura por lotes.
* **Sobrecarga inicial por duplicados históricos en origen:** Debido a cambios previos en la configuración de exportación IPFIX del router, los ficheros antiguos en caché contienen flujos repetidos. Aunque la restricción `UNIQUE` impide que se dupliquen en la base de datos o distorsionen las gráficas, el parser debe procesarlos la primera vez que lee esos ficheros.
* **Crecimiento indefinido sin purga activa:** Si se desactiva la tarea de retención automática, el historial crecerá sin límite hasta agotar el espacio disponible en el contenedor.

* **DNAT** muestra el puerto externo, no el interno. El puerto de servicio se deduce con la heurística `MIN(sp, dp)`, que falla si ambos son efímeros.
* Los flujos se indexan por su **hora de inicio**: una conexión larga aparece en el minuto en que empezó, no repartida.
* `snmp_data` no tiene columna `device`: para dos routers habría que añadirla (y `device_ip` a los índices).
* `sysUpTime` es de 32 bits y se desborda a los ~497 días.
* No hay alerta de uso de disco, y **nadie vigila al vigilante**: si cae toda la máquina, no se avisa a nadie.
* `enforce_retention` ejecuta `julianday()` sobre cada fila cada 30 s (escaneo completo); debería ser horario y usar comparación de cadenas indexable.
* Suma de bytes de flujos (105.068.559) vs. contadores SNMP (109,7 M) en la prueba de 100 MiB: ~4,3 % de diferencia, atribuible a cabeceras y a la agregación de segmentos (3.088 paquetes de ~34 KB); hipótesis no comprobada con captura de paquetes.

### ¿Dónde fallaría primero esta arquitectura si la red fuera 100 veces más grande?

Si escaláramos este diseño de 1–2 dispositivos a **100–200 routers** con un volumen de telemetría 100 veces superior, la arquitectura fallaría en el siguiente orden:

1. **Bloqueos de escritura en SQLite (Primer punto de ruptura):** SQLite solo permite un escritor simultáneo a nivel de base de datos. Con 100 recolectores insertando métricas SNMP cada 15 segundos más 100 veces el volumen de Syslog e IPFIX de forma concurrente, la cola de escrituras saturaría el bloqueo del archivo, provocando errores `database is locked` y pérdida de muestras. *(Solución: migrar a una base de datos cliente-servidor orientada a series temporales o búsqueda distribuida, como Elasticsearch o PostgreSQL/TimescaleDB).*
2. **Saturación del ciclo de sondeo SNMP y procesos `nfdump`:** Un sondeo secuencial de 100 routers tardaría más que la ventana de 15 segundos (provocando solapamiento de ciclos), y lanzar múltiples subprocesos `nfdump` simultáneos saturaría las 2 vCPUs asignadas. *(Solución: paralelización asíncrona distribuida y colectores de flujos nativos en memoria/streaming).*
3. **Agotamiento de almacenamiento e I/O en un único nodo:** Pasar de ~400 MiB/semana a ~40 GB/semana (100 ×; el índice `UNIQUE` crece con ello y cada inserción cuesta más) en un único contenedor degradaría los tiempos de respuesta de las consultas agregadas de `Top Talkers` en la interfaz web.