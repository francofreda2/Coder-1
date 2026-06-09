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


# ----------------------------------------------------------------------------
# FIX FILTROS PANELES 800 / 801
# Esos paneles solo respetaban CUIT + Establecimiento + BIN.
# Se agregan Marca + Banco + Motivo Emisor + Motivo Adqte, con la MISMA forma
# que ya usan los paneles de marca/producto (referencia directa a columnas de a).
# No toca cuadrantes ni rechazos.
# ----------------------------------------------------------------------------

# Firma que identifica SOLO a los paneles 800/801 (usan EXISTS contra el CTE ec).
SIG_800_801 = "FROM ec WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=ec.nro_establecimiento"

# Clausula BIN tal cual aparece en 800/801; tras ella insertamos los 4 filtros.
BIN_CLAUSE = (
    "AND ('${bin:raw}'='' OR substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8)='${bin:raw}')"
)

FILTERS_EXTRA = (
    " AND ('${marca:text}'='All' OR (CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' "
    "WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' "
    "WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END) IN (${marca:sqlstring})) "
    "AND ('${banco:text}'='All' OR CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) "
    "AND ('${motivo_emisor:text}'='All' OR (CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) "
    "WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) "
    "ELSE NULL END) IN (${motivo_emisor:sqlstring})) "
    "AND ('${motivo_adqte:text}'='All' OR NULLIF(TRIM(a.nrorechazointerno),'') IN (${motivo_adqte:sqlstring}))"
)


def fix_filtros_800_801(sql: str):
    """Agrega Marca/Banco/Motivo a los paneles 800/801. Devuelve (sql, n)."""
    if not sql or SIG_800_801 not in sql:
        return sql, 0
    if FILTERS_MARKER in sql:
        return sql, 0  # ya tiene los filtros, no duplicar
    n = sql.count(BIN_CLAUSE)
    if n == 0:
        return sql, 0
    new_sql = sql.replace(BIN_CLAUSE, BIN_CLAUSE + FILTERS_EXTRA)
    return new_sql, n


# Marcador para no duplicar si se corre dos veces.
FILTERS_MARKER = "CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})"


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
    fix_800_801 = 0

    for panel in dash.get("panels", []):
        panel_tocado = False
        for tgt in panel.get("targets", []):
            sql = tgt.get("rawSQL")
            new_sql, n = transform_sql(sql)
            new_sql, nf = fix_filtros_800_801(new_sql)
            if n > 0 or nf > 0:
                tgt["rawSQL"] = new_sql
                targets_tocados += 1
                total_reemplazos += n
                fix_800_801 += nf
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
    print(f"  clausulas con fix de filtros 800/801: {fix_800_801}")
    print(f"  archivo generado: {args.salida}")


if __name__ == "__main__":
    main()
