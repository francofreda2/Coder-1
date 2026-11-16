# Desvíos · NOC · v19

La v19 deja de comparar contra **D-1** y usa la **misma lógica de desvíos que las
alertas** de Observabilidad («Salud de los Clientes» / «Salud de las Conexiones»):
lo habitual es la mediana de las tres semanas anteriores y un desvío cuenta solo si
supera el umbral **y** es estadísticamente significativo (z).

El recorrido, los links y la interacción son los de la v18 (`historico/DOC_desvios_v18.md`).
Mismo uid `payway-desvios`: se importa encima.

## La lógica (la de las alertas)

**Lo habitual** = la **mediana** de la misma franja horaria en **D-7, D-14 y D-21**
(el mismo día de las tres semanas anteriores). Un día de referencia **no cuenta** si
fue feriado o si no tiene datos en la franja. Con 2 días se promedian; con 1 se usa
ese; con 0 no hay habitual (estado "Sin evaluar" o "Más tráfico").

La mediana hace que un día raro (un D-14 con un incidente o un pico) no mueva lo
habitual, cosa que con D-1 pasaba siempre.

| Señal | Desvío | z (significancia) | Atención | Crítico |
|---|---|---|---|---|
| **Volumen** (tx sin reversos) | `Δ% = act / esp − 1` | `(act − esp) / √esp` (Poisson), z ≤ −2 | −30 % | −60 % |
| **Aceptación** | `Δpp = TAA act − TAA hab` | `Δ / √(p(1−p)/act)` (binomial, p acotado a 0,5 %–99,5 %), z ≤ −2, y al menos 2·ventana tx rechazadas de más | −1,5 pp | −4 pp |
| **Denegadas** | `× = act / esp` | `(act − esp) / √(2·esp)` ≥ 3, y exceso ≥ 2·ventana | ×2 | ×4 |
| **Reversos** (1420) | `× = act / esp` | `(act − esp) / √(2·esp)` ≥ 3, y exceso ≥ ventana | ×3 | ×6 |

- **Piso**: con menos de **50 tx** en la ventana (ahora y habitual) no se evalúa
  ("Sin evaluar"). Así un segmento chico no se pone rojo por ruido.
- **Segmento chico que se para**: si "Sin evaluar" pero en 30' suele tener ≥ 8 tx y
  ahora tiene 0 → crítico "Sin tráfico"; si tiene ≤ 1/3 de lo habitual con z ≤ −3 →
  atención "Menos tráfico".
- **Más tráfico** (azul, informativo): +30 % con z ≥ 2, o tráfico donde no había.
- **Estado** (de mayor a menor): ✖ Más rechazos · ↺ Más reversos · ▼ Sin tráfico /
  Menos tráfico · ▲ Más tráfico · ✔ En banda · ○ Sin evaluar. La causa sale de qué
  señal disparó (si caen volumen y aceptación, manda volumen cuando la caída es ≥ 60 %).

### Ancla y ventana

- **Ancla**: último minuto cargado en ABT0 **menos 15' de asentamiento** (los
  últimos minutos que carga el ETL llegan incompletos). Si la ABT está más de 60'
  atrasada, se ancla en *fin del rango − 60'*. Mirando un rango pasado, el ancla es
  el fin del rango.
- **Ventana** (variable nueva **Ventana**: 5 o 15 minutos, como en las alertas):
  bloque de 5' cerrado que termina en el ancla. Con 5' reacciona rápido; con 15'
  evalúa segmentos más chicos (más tx en la ventana) y es más estable.
- Si la ABT tiene más de **30' de atraso**, el Estado dice **⏱ Dato atrasado**.

Todo esto vive en `logica_desvios.py` (umbrales, feriados, reglas). Si las alertas
cambian un umbral o la lista de feriados, se cambia ahí y se regenera.

## Qué cambió en el tablero

| Antes (v18, D-1) | Ahora (v19, habitual) |
|---|---|
| Indicadores: Volumen vs D-1, TAA, Δ TAA D-1, Silencio, … | **8 indicadores**: Datos hasta · Atraso · Resolución · **Estado** · **Volumen** (tx/min · Δ%) · **Aceptación** (TAA · Δpp) · **Denegadas** (/min · ×) · **Reversos** (/min · ×), cada uno coloreado con la severidad de las alertas |
| Tabla de gateways: TRX 6h, Silencio, TAA vs D-1 | **Gateways · estado vs habitual**: Estado, TRX/min, Habitual/min, Δ vol %, z vol, Acept., Acept. hab., Δ acep pp, Deneg. ×, Revers. ×, Último tráfico, Silencio. Críticos arriba, después por impacto |
| Procesamiento: total vs D-1 | Total TRX/min con **banda habitual** (mín–máx de las 3 semanas) y la mediana punteada |
| Volumen vs D-1 (%) | **Volumen vs habitual (%)**: rojo < 40 %, naranja < 70 %, verde, azul > 130 % |
| TAA actual vs D-1 | **TAA actual vs habitual**, más Total TRX y Total habitual |
| Desvío TAA vs D-1 (pp) | **Desvío de TAA vs habitual (pp)** |
| Detalles: línea D-1 | Detalles: **línea habitual punteada** del mismo segmento |

El indicador **Silencio** se reemplazó por **Estado**; el silencio por gateway sigue
en la tabla (columna Silencio y Último tráfico).

## Diferencias con las alertas (a propósito)

- **Definiciones de conteo**: Desvíos conserva las suyas (excluye 1420 del volumen y
  lo cuenta aparte como reversos; aprobadas según su lista de códigos). Por eso los
  números pueden diferir un poco de los de las alertas.
- **Día de referencia sin datos**: aquí cuenta si tiene filas en los 30' previos al
  ancla; en las alertas, en sus 2 h.
- **Umbrales de color de "Volumen vs habitual (%)"**: 70 % / 40 %, alineados con
  −30 % / −60 % de las alertas (antes 80 % / 50 %).

## Cómo se validó

- `tests_logica/test_estado_vs_alertas.py`: porta a Python el `evaluar()` del
  JavaScript de las alertas y lo compara con el SQL de `logica_desvios.py` (en
  DuckDB) sobre **5000 casos al azar: 0 diferencias**, con todos los estados cubiertos.
- `test_desvios_noc.py desvios_v19.json`: **31 chequeos** ejecutando el SQL real sobre
  4 semanas sintéticas (03/09, 10/09 con Paystore ×3 como día atípico, 17/09 y 24/09)
  con incidentes conocidos: caída total, Decidir caído, Pos Legacy (tarjetas PC)
  caído, TAA de Paystore al 60 %, reversos. Verifica que la mediana ignore el día
  atípico, que el feriado se excluya, que un segmento chico parado se detecte y el
  orden de la tabla. 172 ejecuciones (43 consultas × 4 resoluciones) sin error.
- `check_desvios_v19.py`: **68/68** (no queda D-1, Estado y tabla usan la lógica de
  las alertas, feriados aplicados, 18 consultas de habitual, mapeos de severidad,
  variable Ventana, más todo lo de la v18).
- `trino_check/run.sh desvios_v19.json`: **88/88** en Trino 446 (Athena v3).
- **Grafana 10.4.7 real** con el resultado del SQL sintético cargado: colores de
  los indicadores y de la columna Estado, banda habitual en Procesamiento.

No se probó contra Athena con datos reales: tiempos y volúmenes se confirman al
importarlo. Las consultas del estado leen 7 h de hoy + 2 h de cada una de las tres
semanas anteriores (con poda de particiones).
