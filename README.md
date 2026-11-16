# Tableros Grafana · Payway NOC

## Qué importar

| Tablero | Archivo | uid |
|---|---|---|
| **Desvíos · NOC** (vigente) | `desvios_v19.json` | `payway-desvios` |

En Grafana: **Dashboards → New → Import → Upload JSON**.

- `payway-desvios` es un uid **estable**: las próximas versiones lo reemplazan en
  el mismo lugar y no rompen links ni favoritos. Las versiones anteriores
  (`desvios-v17-noc`, `desvios-v17-abt`, `desvios-v16-7`) se pueden borrar de
  Grafana una vez importada la v19.
- **One page live monitoring** (de Observabilidad): para agregarle los links de
  ida y vuelta, exportar la versión vigente (2.6.1 o posterior) y correr
  `python3 add_links_live_monitoring.py exportado.json salida.json`. Solo agrega
  dos links arriba; no toca paneles, SQL ni variables.

Guía de uso: `DOC_desvios_v19.md` (lógica de desvíos y qué cambió) y
`historico/DOC_desvios_v18.md` (recorrido e interacción, sin cambios).

## Marcas y tipo de producto

`logica_marcas.py` es la lista común de marca y tipo de producto por
`instrmtyp` (`8-LOGICA-COMPARTIDA/marcas/`, 02/10/2026: PB, PC, PM y PP son
Marcas Privadas). Todos los generadores la toman de ahí, así el filtro y el
desglose usan la misma lista. Si la lista cambia, se cambia ese archivo y se
regenera.

## Lógica de desvíos

`logica_desvios.py` es la lógica de las alertas de Observabilidad (Salud de los
Clientes / Conexiones): habitual = mediana de D-7, D-14 y D-21 sin feriados, umbrales
y z de volumen, aceptación, denegadas y reversos. El tablero la toma de ahí; si las
alertas cambian un umbral o la lista de feriados, se cambia ese archivo y se regenera.

## Cómo se genera

Cada versión sale de la anterior con un script. Ningún JSON se edita a mano.

```
desvios_v16.8_origen.json ──build_desvios_abt.py──▶ desvios_v17.0_abt.json   (CTX → ABT0, producto/gateway/modalidad)
                          ──build_desvios_noc.py──▶ desvios_v17.1_noc.json   (vista NOC)
                          ──build_desvios_v18.py──▶ desvios_v19.json         (recorrido simple, interacción y lógica de las alertas)
```

## Cómo se valida

| Comando | Qué verifica |
|---|---|
| `python3 check_desvios_v19.py` | Estructura, layout, links, sin D-1, lógica de las alertas en estado y tabla, feriados |
| `python3 tests_logica/test_estado_vs_alertas.py` | El SQL del estado da lo mismo que el `evaluar()` de las alertas (5000 casos al azar) |
| `python3 test_desvios_noc.py desvios_v19.json` | Ejecuta el SQL sobre 4 semanas sintéticas de ABT0 con incidentes conocidos (`pip install duckdb sqlglot pytz`) |
| `trino_check/run.sh desvios_v19.json` | Analiza todo el SQL con Trino 446, el motor de Athena v3 (Java 21 + Maven) |

## Carpetas

- `trino_check/`: analizador de SQL con Trino.
- `historico/`: versiones y scripts reemplazados (línea v2.x y el tablero
  suelto de Producto / QR / Modalidades, que quedó integrado en Desvíos).
