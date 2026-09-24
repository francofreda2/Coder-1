# Tableros Grafana · Payway NOC

## Qué importar

| Tablero | Archivo | uid |
|---|---|---|
| **Desvíos · NOC** (vigente) | `desvios_v18.json` | `payway-desvios` |
| One page live monitoring, con links a Desvíos | `one_page_live_monitoring_con_links.json` | `payway-tx-v21` |

En Grafana: **Dashboards → New → Import → Upload JSON**.

- `payway-desvios` es un uid **estable**: las próximas versiones lo reemplazan en
  el mismo lugar y no rompen links ni favoritos. Las versiones anteriores
  (`desvios-v17-noc`, `desvios-v17-abt`, `desvios-v16-7`) se pueden borrar de
  Grafana una vez importada la v18.
- `one_page_live_monitoring_con_links.json` es el tablero de Observabilidad tal
  como estaba, con dos links agregados arriba (nada más). Importarlo **reemplaza**
  el que está en Grafana. Si ese tablero cambió desde entonces, en lugar de
  importar este archivo conviene exportar el actual y correr
  `python3 add_links_live_monitoring.py exportado.json salida.json`.

Guía de uso: `DOC_desvios_v18.md`.

## Cómo se genera

Cada versión sale de la anterior con un script. Ningún JSON se edita a mano.

```
desvios_v16.8_origen.json ──build_desvios_abt.py──▶ desvios_v17.0_abt.json   (CTX → ABT0, producto/gateway/modalidad)
                          ──build_desvios_noc.py──▶ desvios_v17.1_noc.json   (vista NOC)
                          ──build_desvios_v18.py──▶ desvios_v18.json         (recorrido simple e interacción)
```

## Cómo se valida

| Comando | Qué verifica |
|---|---|
| `python3 check_desvios_v18.py` | Estructura, layout, links y que el SQL sea el mismo que la v17.1 |
| `python3 test_desvios_noc.py desvios_v18.json` | Ejecuta el SQL sobre una ABT0 sintética con un incidente conocido (`pip install duckdb sqlglot pytz`) |
| `trino_check/run.sh desvios_v18.json` | Analiza todo el SQL con Trino 446, el motor de Athena v3 (Java 21 + Maven) |

## Carpetas

- `trino_check/`: analizador de SQL con Trino.
- `historico/`: versiones y scripts reemplazados (línea v2.x y el tablero
  suelto de Producto / QR / Modalidades, que quedó integrado en Desvíos).
