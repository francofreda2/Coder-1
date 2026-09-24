# Desvíos v17.1 · vista NOC

`desvios_v17.1_noc.json` (uid `desvios-v17-noc`) es la v17.0 sobre ABT más una
sección NOC arriba de todo, pensada para operar un incidente y no solo para
mirar la TAA.

## Por qué la v17.0 no alcanzaba en una caída

1. **En una caída total la TAA no baja: baja el volumen.** Si no entra nada,
   aprobadas / total no se mueve (o queda vacía). El tablero no mostraba el total
   de transacciones en el gráfico principal, así que la caída no se veía.
2. **Un minuto sin transacciones no genera fila.** Grafana une los puntos de
   ambos lados y el corte desaparece del gráfico.
3. **Buckets fijos de 15 minutos.** Un corte de 5 minutos queda diluido en un
   33%, y el último bucket, siempre incompleto, dibuja una caída que no existe.
4. **No se sabía hasta qué minuto había dato.** La ABT tiene atraso variable
   (medidos 8 y 221 minutos): "no hay tráfico" y "la ABT viene atrasada" se veían
   igual.
5. **No había nada por gateway más allá del filtro de TAA.**

## Cómo leerlo durante un incidente

Orden sugerido, de arriba hacia abajo:

| Paso | Panel | Qué mirar |
|---|---|---|
| 1 | **Atraso del dato** | Si está en rojo (≥ 30 min), lo que falta puede ser la ABT atrasada y no una caída. Confirmar contra la fuente antes de escalar. |
| 2 | **Datos hasta** | Último minuto cerrado que se está mirando, en hora Argentina. Todos los gráficos NOC terminan ahí. |
| 3 | **Volumen vs D-1 · últimos 15 min** | El indicador de caída. Rojo < 70%. Arriba de 130% puede ser una tormenta de reintentos. |
| 4 | **Procesamiento** + **Volumen vs D-1 (%)** | Cuándo empezó, cuánto cayó y si ya se recupera. La línea violeta punteada es ayer. |
| 5 | **Latencia p50 / p95** | Si el p95 subió antes de que caiga el volumen: saturación o timeouts. |
| 6 | **Tabla de gateways** | Los caídos quedan arriba (ordenada por % vs D-1). "Silencio" = minutos desde su última transacción. |
| 7 | **TRX por minuto por gateway** | Quién se recupera y cuándo. Para aislar uno, filtrar por Plataforma de pago: todo el tablero pasa a ese gateway, y "Silencio del segmento" muestra si dejó de transaccionar. |

Durante el incidente conviene poner **Resolución = 1 min**. El panel
"Resolución efectiva" muestra qué bucket se está usando.

**Para analizar un incidente pasado**, cerrar el rango en el momento que se
quiere revisar: los KPIs y la tabla de gateways muestran el estado a ese cierre,
no al momento actual.

## Qué se agregó

### Sección NOC (12 paneles, 4 consultas a Athena)

| Id | Panel | Detalle |
|---|---|---|
| 1000 | Datos hasta (último minuto cerrado) | Último minuto de ABT0 menos el que está en carga, o el fin del rango si es anterior. Hora Argentina. |
| 1001 | Atraso del dato | Ahora − último minuto en ABT0, sin filtros. Verde < 15, naranja < 30, rojo ≥ 30 min. |
| 1002 | Resolución efectiva | Bucket en uso. |
| 1003 | TRX por minuto · últimos 5 min | Sin reversos, con todos los filtros. |
| 1004 | Volumen vs D-1 · últimos 15 min | Rojo < 70%, naranja 70–90% y > 130%. |
| 1005 | Silencio del segmento filtrado | Minutos desde la última transacción que cumple los filtros, contra "Datos hasta" (el atraso de la ABT no cuenta). |
| 1010 | Procesamiento | Total vs D-1, Aprobadas, Denegadas y Reversos, en TRX por minuto. |
| 1011 | Volumen vs D-1 (%) | Por bucket, con bandas de color. |
| 1012 | Latencia p50 / p95 | `latencia_ms` de ABT0. |
| 1101 | TRX por minuto por gateway | Top 10 del rango. |
| 1102 | TAA por gateway | Los mismos 10. |
| 1103 | Tabla de gateways | Últimos 15 min vs D-1: volumen, %, TAA, Δ TAA, último tráfico, silencio. |

Los KPIs (1000–1005) salen de una sola consulta, y 1011/1012 y 1102 reusan la
consulta de 1010 y 1101 (datasource `-- Dashboard --`), así que la sección suma
4 consultas a Athena y no 12.

### Decisiones de diseño de los gráficos NOC

- **TRX por minuto, no conteo por bucket.** Cambiar la resolución no cambia la
  escala, y el último bucket parcial no dibuja una caída falsa.
- **Grilla completa de buckets.** Los buckets sin tráfico valen 0, así un corte
  se ve como corte y no como una línea que lo saltea.
- **Terminan en el último minuto cerrado de la ABT.** Se descarta el minuto en
  carga.
- **Mismos filtros que el resto del tablero**, incluidos Producto, Plataforma,
  Modalidad y la exclusión de los 8 CUITs.

### Cambios en los paneles existentes

- **Panel 10** ("TAA Actual vs Baseline D-1"): agrega **Total TRX** y **Total
  D-1** en el eje derecho, en violeta, igual que en el gráfico de Procesamiento.
- **Resolución configurable**: los 30 buckets que estaban fijos en 15 minutos
  ahora usan la variable **Resolución** (auto / 1 / 5 / 15 / 60 min). En auto:
  hasta 3 h → 1 min, hasta 12 h → 5 min, hasta 48 h → 15 min, más → 60 min.
  Los títulos que decían "· 15m" ahora muestran la resolución elegida.
- **Zona horaria del tablero: Argentina**, para que el eje de los gráficos
  coincida con las horas de los KPIs aunque el navegador esté en UTC.

La lógica de cuadrantes, rechazos y TAA no se tocó.

## Qué hay que saber

- **Total** excluye reversos (1420) y procesos 92/30, igual que el resto del
  tablero. Los reversos se muestran aparte.
- **Si la ABT atrasa más de 6 horas**, las comparaciones contra D-1 de los KPIs
  y de la tabla quedan incompletas. El panel de atraso ya va a estar en rojo.
- **"Silencio del segmento"** tiene sentido con un filtro de volumen alto
  (gateway, marca). Con un establecimiento chico, minutos sin tráfico son normales.
- **La tabla de gateways** omite los que suman menos de 15 transacciones entre
  las dos ventanas, para no llenarla de ruido.
- **Queda por confirmar en Athena lo mismo que en la v17.0**: que exista
  `codbancoemisor` en ABT0 (ver `DOC_desvios_v17.0_abt.md`, sección 8).
  `latencia_ms` sí está verificada: la usa el tablero de live monitoring.

## Cómo se validó

- `python3 check_desvios_noc.py`: 18 chequeos de estructura (ids, superposición
  de paneles, referencias entre paneles, filtros presentes en las consultas nuevas).
- `python3 test_desvios_noc.py` (requiere `pip install duckdb sqlglot`): arma
  una ABT0 sintética con un incidente conocido (caída total de 5 min, Decidir
  caído 40 min, Paystore con TAA degradada, ABT con 8 min de atraso) y ejecuta
  el SQL real de los paneles. 16 chequeos sobre los números que tiene que dar,
  y las 36 consultas corridas en 4 resoluciones sin error.

Lo que la prueba sintética no cubre es el comportamiento contra el Athena real
(tipos exactos, tiempos, volúmenes). La primera corrida en Grafana es la que lo
confirma.

## Archivos

| Archivo | Qué es |
|---|---|
| `desvios_v17.1_noc.json` | **El tablero a importar** |
| `build_desvios_noc.py` | Lo genera a partir de `desvios_v17.0_abt.json` |
| `check_desvios_noc.py` | Validación estructural |
| `test_desvios_noc.py` | Prueba de ejecución con datos sintéticos |
