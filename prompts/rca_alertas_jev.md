# RCA de alertas del NOC con Jev

Quiero que armes en este repo una herramienta local que tome una alerta del NOC,
la clasifique con Jev (TypeSafe AI) y haga el análisis de causa raíz (RCA) con
evidencia de Athena.

## Antes de escribir código

1. Leé `README.md`, `DOC_desvios_v18.md`, `DOC_desvios_v17.1_noc.md`,
   `DOC_desvios_v17.0_abt.md` y el SQL de `desvios_v18.json`. Ese tablero es la
   fuente de verdad: el RCA tiene que dar los mismos números que el NOC ve en
   Grafana.
2. Preguntame lo que falte. Como mínimo:
   - Cómo accedo a Athena desde esta máquina: directo con credenciales de AWS
     (perfil, región, workgroup, bucket de resultados) o solo a través de
     Grafana (URL, token de service account, uid del datasource de Athena, vía
     `/api/ds/query`).
   - Cómo llegan las alertas: webhook de Grafana Alerting (JSON), texto de mail
     o Teams, u otro. Pedime 2 o 3 ejemplos reales sin datos sensibles.
   - La URL de Grafana, para armar links al tablero.
   - Si se pueden mandar a la API de TypeSafe datos agregados de producción
     (conteos, tasas, códigos) y si los CUIT y establecimientos van tal cual o
     anonimizados.
3. Mostrame el plan y esperá mi OK.

## Reparto del trabajo

No lo cambies sin avisarme:

- **SQL en Athena** junta la evidencia. Jev no consulta datos.
- **Python** calcula qué explica el desvío. Es determinístico y se prueba con
  tests.
- **Jev** clasifica la alerta y elige entre hipótesis armadas con esa
  evidencia. Devuelve probabilidades, no texto.
- **Vos (Claude)** escribís la explicación final a partir del resultado.

## Flujo del RCA

Seguí el mismo recorrido que el tablero Desvíos v18:

0. **Normalizar la alerta**: métrica, valor, umbral, inicio y filtros (gateway,
   marca, CUIT, etc.). Ventana de análisis: desde 30 minutos antes del inicio
   hasta el último minuto cerrado de la ABT. Comparar contra D-1 a la misma
   hora, como el tablero. Calculá también D-7 y avisá si las dos comparaciones
   no coinciden (feriados, lunes contra domingo).
1. **¿Está procesando?** Atraso de la ABT, volumen contra D-1, TAA contra D-1 y
   latencia p50/p95. Con un atraso de 30 minutos o más, la primera hipótesis
   es "dato atrasado, no caída".
2. **¿Dónde?** Volumen, TAA y minutos de silencio por gateway (sufijo de 4
   dígitos de `programaperiferiaorigen`).
3. **¿Cuándo?** Minuto de inicio y duración del desvío.
4. **¿Por qué?** Denegadas por cuadrante (Operativo/Riesgo Emisor,
   Operativo/Riesgo Adquirente), Fondos insuficientes + Do Not Honor (rechazos
   normales del emisor, no un problema de procesamiento) y los motivos más
   frecuentes de cada cuadrante.
5. **¿Qué marca?** TAA por marca; después producto, banco emisor y comercio
   (CUIT).

En cada dimensión, calculá cuánto del desvío explica cada valor:

- En caídas de volumen: transacciones faltantes contra D-1.
- En caídas de TAA: denegadas en exceso contra D-1, con D-1 escalado al volumen
  actual.

Ordená por contribución. Si una ventana tiene poco volumen (menos de 15
transacciones entre las dos ventanas, el mismo corte que usa la tabla de
gateways), marcá esa señal como ruidosa.

## Jev

- SDK: `pip install typesafe-sdk`. La key va en `TYPESAFE_API_KEY`, nunca en el
  código ni en el repo.
- API verificada en la versión 0.7.1:
  - Llamada: `TypeSafeClient().system_one(state=..., questions=...)`.
  - Tipos de pregunta: `Choice`, `Noul` (sí/no) y `Score`.
  - La respuesta de un `Choice` trae `choice`, `confidence` y `probabilities`.
  - Antes de usarla, confirmá con `inspect` que la versión instalada es igual.
- **Triaje**:
  - `Noul`: ¿es un incidente real o ruido?
  - `Choice`: tipo de alerta (caída de volumen, caída de TAA, latencia, dato
    atrasado, suba de reversos u otro).
  - `Score`: severidad.
- **Hipótesis**:
  - Armá las candidatas con los 3 a 5 valores que más contribuyen al desvío.
    Por ejemplo: "gateway 8002 sin tráfico", "banco emisor X con el código
    91", "riesgo emisor 59/63/83", "rechazo interno TA001", "Fondos/DNH
    normales", "un comercio concentra las denegadas", "ABT atrasada".
  - Pasáselas a Jev en un `Choice`, con la evidencia de cada una en la
    descripción del criterio.
  - Si la primera no supera el umbral de probabilidad (configurable;
    arrancá con 0.6), el resultado es "no concluyente". En ese caso, mostrá
    las 3 primeras y qué habría que consultar después.
- A Jev solo le mandás agregados. Nunca filas de transacciones, PAN, BIN ni
  `nrotarjeta`. No uses la tabla CTX.
- Antes de la primera llamada real, mostrame un payload de ejemplo.

## Reglas para Athena

- Solo SELECT. Filtrá siempre por `fecha_particion` y usá LIMIT en los
  rankings. Apuntá a no más de 10 consultas por RCA.
- Reusá la CTE base y los filtros de `desvios_v18.json`: exclusión de los 8
  CUIT, reversos 1420, procesos 92/30, cuadrantes, marca por `instrmtyp`,
  producto y modalidad. Si podés, extraelos del JSON en vez de copiarlos, así
  no se desincronizan.
- `minuto_utc` es `timestamp(6) with time zone`. Normalizalo con
  `CAST(minuto_utc AT TIME ZONE 'UTC' AS timestamp)`. El motivo está en
  `DOC_desvios_v17.1_noc.md`, en el error de `sequence()`.
- Validá todo el SQL nuevo con `trino_check` (Trino 446, el motor de Athena
  v3). `Check.java` analiza cualquier carpeta de archivos `.sql`.

## Qué entregar

- `rca/`: paquete de Python con CLI. `python -m rca --alerta alerta.json` (o el
  texto de la alerta) genera `rca_out/<fecha>_<alerta>.json` y `.md`.
- El `.md` tiene que traer:
  - Un resumen de 2 líneas.
  - La clasificación de Jev, con sus probabilidades.
  - La línea de tiempo.
  - La evidencia de cada paso, en tablas.
  - La causa raíz probable y las hipótesis descartadas.
  - A quién escalar.
  - Links al tablero `payway-desvios` ya filtrados (rango, gateway, marca y
    resolución de 1 minuto).
- Tests con `pytest`:
  - Armá los datos con el mismo generador de `test_desvios_noc.py`, una ABT0
    sintética en DuckDB con un incidente conocido. Copialo a un fixture; no
    modifiques ese archivo.
  - El RCA tiene que encontrar las cuatro fallas de ese incidente:
    - Caída total entre las 13:05 y las 13:10 UTC.
    - Decidir (8002) sin tráfico entre las 13:00 y las 13:40.
    - Paystore (0001) con una TAA del 60% desde las 15:00.
    - La ABT con 8 minutos de atraso.
  - En los tests, Jev va mockeado. Sumá un test real opcional, marcado `live`,
    que corra solo si hay key.
- `.claude/skills/rca-alerta/SKILL.md`: cuando pegue una alerta o pida un RCA,
  corré la CLI, leé el JSON y escribí la explicación.
- `DOC_rca_alertas.md`, con el mismo estilo que los otros DOC del repo, y una
  fila nueva en el README.

## Cómo trabajar

- Por partes, con los tests pasando en cada una: primero la evidencia sin Jev,
  después Jev y al final la skill.
- No toques los JSON de los tableros ni los scripts que ya existen.
- Commits chicos y sin secretos. Agregá `rca_out/` al `.gitignore`.
- Al final, pedime de 3 a 5 alertas pasadas con la causa ya conocida, corré el
  RCA sobre ellas y decime cuántas acierta la primera hipótesis y cuántas
  entran entre las 3 primeras.
