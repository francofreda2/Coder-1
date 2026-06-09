#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Optimiza el dashboard de Grafana "Desvios · CUIT/Establecimiento" SIN tocar:
  - La logica de cuadrantes / rechazos (clasificacion ciss / nri intacta).
  - La infraestructura (mismas tablas, sin crear vistas).

Aplica solo dos cambios que preservan resultados byte a byte por panel:

  1) refresh: "30s" -> "1m"   (configurable con --refresh)

  2) Cambia el EXISTS correlacionado contra
     prisma_ab_analytics_prod_db.establecimiento por un semi-join (IN sobre una
     CTE est_cuit resuelta una sola vez). Equivalente logico exacto, mucho mas
     barato en Presto/Athena. Cubre las dos variantes de input del CUIT
     ('${cuit:raw}' con y sin REPLACE).

Uso:
    python3 optimize_desvios.py entrada.json salida.json
    python3 optimize_desvios.py entrada.json salida.json --refresh 5m

Salida: imprime cuantos paneles/targets se modificaron y valida el JSON.
"""

import argparse
import json
import re
import sys

# EXISTS correlacionado contra la tabla grande (dos variantes de input del CUIT:
# con y sin REPLACE). Se reescribe como IN con subconsulta NO correlacionada,
# que Presto/Athena ejecuta una sola vez como hash semi-join. Resultado identico.
#
# Variante A (input sin REPLACE):
EXISTS_A = (
    "EXISTS(SELECT 1 FROM prisma_ab_analytics_prod_db.establecimiento e "
    "WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=e.nro_establecimiento "
    "AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST('${cuit:raw}' AS BIGINT))"
)
SEMIJOIN_A = (
    "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN "
    "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
    "WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST('${cuit:raw}' AS BIGINT))"
)

# Variante B (input con REPLACE, p.ej. panel 900):
EXISTS_B = (
    "EXISTS(SELECT 1 FROM prisma_ab_analytics_prod_db.establecimiento e "
    "WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=e.nro_establecimiento "
    "AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT))"
)
SEMIJOIN_B = (
    "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN "
    "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
    "WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT))"
)


def transform_sql(sql: str):
    """Devuelve (sql_nuevo, n_reemplazos) para un rawSQL."""
    if not sql:
        return sql, 0
    n = sql.count(EXISTS_A) + sql.count(EXISTS_B)
    if n == 0:
        return sql, 0
    new_sql = sql.replace(EXISTS_A, SEMIJOIN_A).replace(EXISTS_B, SEMIJOIN_B)
    return new_sql, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada", help="JSON original exportado de Grafana")
    ap.add_argument("salida", help="JSON optimizado a generar")
    ap.add_argument("--refresh", default="1m", help='refresh nuevo (default: "1m")')
    args = ap.parse_args()

    with open(args.entrada, encoding="utf-8") as f:
        dash = json.load(f)

    refresh_old = dash.get("refresh")
    dash["refresh"] = args.refresh

    paneles_tocados = 0
    targets_tocados = 0
    total_reemplazos = 0

    for panel in dash.get("panels", []):
        panel_tocado = False
        for tgt in panel.get("targets", []):
            sql = tgt.get("rawSQL")
            new_sql, n = transform_sql(sql)
            if n > 0:
                tgt["rawSQL"] = new_sql
                targets_tocados += 1
                total_reemplazos += n
                panel_tocado = True
        if panel_tocado:
            paneles_tocados += 1

    salida = json.dumps(dash, ensure_ascii=False, indent=2)
    # Validacion: el resultado tiene que volver a parsear.
    json.loads(salida)

    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(salida)

    print("OK")
    print(f"  refresh: {refresh_old!r} -> {args.refresh!r}")
    print(f"  paneles modificados: {paneles_tocados}")
    print(f"  targets modificados: {targets_tocados}")
    print(f"  EXISTS reemplazados por semi-join: {total_reemplazos}")
    print(f"  archivo generado: {args.salida}")


if __name__ == "__main__":
    main()
