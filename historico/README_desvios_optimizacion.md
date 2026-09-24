# Optimización dashboard Grafana "Desvíos · CUIT/Establecimiento"

`optimize_desvios.py` toma el JSON exportado del dashboard y genera una versión
optimizada **sin tocar la lógica de cuadrantes/rechazos ni la infraestructura**
(mismas tablas, sin vistas).

## Cómo usar

1. En Grafana: **Dashboard → Share → Export → Save to file** → `desvios.json`.
2. Correr:
   ```bash
   python3 optimize_desvios.py desvios.json desvios_optimizado.json
   # refresh distinto (opcional):
   python3 optimize_desvios.py desvios.json desvios_optimizado.json --refresh 5m
   ```
3. En Grafana: **New → Import → Upload JSON** con `desvios_optimizado.json`.

El script imprime cuántos paneles/targets tocó y valida que el JSON resultante
vuelva a parsear. Es idempotente (correrlo dos veces no duplica cambios).

## Qué cambia (todo preserva los resultados por panel)

1. **`refresh`: `30s` → `1m`** (configurable). Menos re-ejecuciones = carga más
   rápida y menor costo Athena.

2. **`EXISTS` correlacionado → `IN` no correlacionado** en el filtro por CUIT
   contra `prisma_ab_analytics_prod_db.establecimiento`. Presto/Athena lo ejecuta
   una sola vez como hash semi-join en lugar de correlacionar por fila.
   Resultado lógico idéntico; además el filtro CUIT pasa a tolerar guiones.

3. **Fix de filtros en paneles 800 y 801** ("Establecimientos del CUIT · ranking"
   y "TAA por establecimiento · Top 8"): antes solo respetaban CUIT +
   Establecimiento + BIN. Se les agregan **Marca + Banco + Motivo Emisor +
   Motivo Adqte**, con la misma forma que ya usan los paneles de marca/producto.

## Qué NO cambia

- Clasificación de cuadrantes y de rechazos (intacta).
- Tablas / infraestructura (sin vistas nuevas).
- Panel 800 ya resolvía el CUIT con un CTE `ec`; su `EXISTS(... FROM ec ...)`
  no se modifica (ya era eficiente).
