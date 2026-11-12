# Design Document - Mini-NOC

## 1. Architecture

El Mini-NOC está diseñado con una arquitectura modular basada en microservicios, ejecutándose en un entorno Linux (Debian) y gestionado íntegramente por `systemd`. 

Los componentes principales se dividen en tres capas:

*   **Capa de Recolección (Collectors):** Compuesta por procesos independientes en Python que se ejecutan en segundo plano (`snmp-poller`, `syslog-poller`, `flow-poller`). Estos procesos interactúan con la red y las cachés locales (como `/var/cache/nfdump/mikrotik/`) para extraer métricas, eventos y flujos de red.
*   **Capa de Almacenamiento y Lógica (Storage & Alert Engine):** El núcleo de almacenamiento es una base de datos relacional local en SQLite (`mininuc.db`). Un proceso independiente (`alert-engine`) evalúa periódicamente los datos almacenados frente a reglas predefinidas.
*   **Capa de Presentación (API & Web Interface):** Un servicio backend (`mininuc-api`) expone la información almacenada en la base de datos a través de peticiones HTTP, consumidas por una interfaz web basada en archivos estáticos servidos localmente.

## 2. Data Model

El modelo de datos se centraliza en SQLite. Se ha optado por un esquema relacional ligero con las siguientes tablas principales (basado en la implementación actual del recolector de flujos):

*   **`flow_data`**: Almacena el registro histórico del tráfico (IPFIX). 
    *   Contiene campos para marcas de tiempo (`ts`, `te`), protocolos (`pr`), direcciones IP origen/destino (`sa`, `da`), puertos (`sp`, `dp`), métricas de volumen (`pkt`, `byt`), e interfaces (`in_if`, `out_if`).
    *   **Tratamiento NAT:** Incluye campos para las IPs post-NAT (`xsa`, `xda`) y lógica para resolver los *hosts* internos reales (`real_src`, `real_dst`) comprobando si pertenecen a las subredes internas (ej. `192.168.153.0/24`).
    *   **Restricción `UNIQUE`:** Garantiza la idempotencia y evita registros duplicados si un mismo archivo se lee dos veces por error.
*   **`collector_state`**: Tabla clave-valor utilizada para guardar el estado persistente de los recolectores (ej. `name: flow_last_file`, `value: 202611051230`). Permite que el sistema sepa exactamente por dónde continuar leyendo tras un reinicio.

## 3. Decisions and their reasons

*   **Lectura incremental y en streaming para IPFIX:** Se descartó el uso de `nfdump -R` (que leía todos los archivos de golpe) porque saturaba la RAM del contenedor (llegando a picos de 3.8 GB) y provocaba cierres por falta de memoria (OOM). La decisión fue usar `subprocess.Popen` iterando línea por línea la salida de los ficheros nuevos y guardando un "marcador" del último archivo leído en `collector_state`.
*   **Inserción en base de datos por lotes (Batching):** En lugar de insertar cada flujo individualmente o todos a la vez, se insertan en bloques de 1000 registros (`BATCH_SIZE = 1000`). *Razón:* Mantiene el consumo de memoria acotado a un nivel predecible y reduce enormemente el tiempo de espera por bloqueos I/O en SQLite.
*   **Uso de SQLite en lugar de bases de datos de series temporales (ej. InfluxDB):** *Razón:* Dado que el entorno es un "Mini-NOC" desplegado en un único contenedor o máquina con recursos limitados, SQLite no requiere configuración de red, no consume RAM en reposo y simplifica los respaldos (es un solo archivo `.db`).
*   **Automatización mediante `deploy.sh`:** Se decidió no ejecutar los scripts directamente desde la carpeta del repositorio (`~/mini-noc`). *Razón:* Separar el entorno de desarrollo (Git) del entorno de producción (`/home/admin`). El script estandariza permisos, propiedad de archivos y reinicio de servicios, minimizando el error humano.

## 4. Known limitations

*   **Cuellos de botella de concurrencia en SQLite:** Al tener múltiples *pollers* (SNMP, Syslog, Flows) y la API intentando leer y escribir simultáneamente en el mismo archivo `mininuc.db`, pueden producirse bloqueos (`database is locked`) en picos de alta carga. Esto se ha mitigado parcialmente configurando un `timeout` en las conexiones, pero sigue siendo un límite físico del motor frente a bases de datos cliente-servidor como PostgreSQL.
*   **Impacto de almacenamiento por duplicidad en origen:** Debido a una configuración previa en el *router* donde el target IPFIX se deshabilitó y rehabilitó, se envían flujos duplicados en los archivos antiguos. Aunque la restricción `UNIQUE` de la base de datos evita que esto afecte al motor de alertas y a la visualización, sigue habiendo un coste innecesario de CPU y de lecturas en disco.