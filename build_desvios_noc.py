#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera "Desvios · v17.1 · NOC" a partir de la v17.0 (ABT).

Pensado para operar un incidente, no solo para mirar la TAA:

1) Seccion NOC arriba de todo:
   - Hasta que minuto hay dato, cuanto atraso tiene la ABT y con que resolucion
     se esta mirando.
   - TRX por minuto y volumen contra el mismo horario de D-1. En una caida total
     la TAA no baja (no entra nada), lo que baja es el volumen.
   - Procesamiento completo: Total / Aprobadas / Denegadas / Reversos por minuto,
     % contra D-1 y latencia p50/p95.
   - Gateways: TRX por minuto y TAA por gateway, y una tabla de estado actual
     contra D-1 con el ultimo minuto con trafico de cada uno.

2) Panel 10: agrega Total TRX y Total D-1.

3) Resolucion configurable ($resolucion) en todos los graficos que antes eran
   fijos a 15 minutos.

Los graficos NOC:
   - Rellenan con cero los buckets sin trafico. Sin eso, un minuto sin filas no
     existe y Grafana une los puntos: el corte no se ve.
   - Terminan en el ultimo minuto cerrado de la ABT (se descarta el minuto en
     carga) y expresan todo en TRX por minuto, asi el ultimo bucket parcial no
     dibuja una caida falsa y cambiar la resolucion no cambia la escala.

Uso:
    python3 build_desvios_noc.py
"""

import copy
import json
import sys

from build_desvios_abt import (ABT0, ANCLA, CUIT_ABT, EXCL_ABT, filtros_nuevos,
                               join_dafil)

ENTRADA = "desvios_v17.0_abt.json"
VARS_ABT = "abt_vars_nuevos.json"
SALIDA = "desvios_v17.1_noc.json"

UID = "desvios-v17-noc"
TITULO = "Desvíos · v17.1 · NOC · Procesamiento, Gateways y TAA (ABT)"
TAGS = ["payway", "noc", "desvios", "abt", "v17-1"]
TZ = "America/Argentina/Buenos_Aires"

DS = {"type": "grafana-athena-datasource", "uid": "bex5w10g549vkd"}
DS_DASH = {"type": "datasource", "uid": "-- Dashboard --"}
CONN = {"catalog": "AwsDataCatalog", "database": "payway_poststage_prod_db",
        "region": "us-east-1", "resultReuseEnabled": True,
        "resultReuseMaxAgeInMinutes": 5}

APROB = "('0000','0400','0900')"

# ---------------------------------------------------------------------------
# Fragmentos SQL
# ---------------------------------------------------------------------------

# Tamano del bucket en minutos. 'auto' sigue la misma regla que el tablero de
# live monitoring; el operador puede forzar 1 minuto durante un incidente.
BK = ("(CASE WHEN ${resolucion:raw} > 0 THEN ${resolucion:raw}"
      " WHEN date_diff('minute',$__timeFrom(),$__timeTo()) <= 180 THEN 1"
      " WHEN date_diff('minute',$__timeFrom(),$__timeTo()) <= 720 THEN 5"
      " WHEN date_diff('minute',$__timeFrom(),$__timeTo()) <= 2880 THEN 15"
      " ELSE 60 END)")


def bucket(x):
    return ("date_trunc('minute',%s)-(EXTRACT(MINUTE FROM %s)%%%s)*INTERVAL '1' MINUTE"
            % (x, x, BK))


def part(ts):
    return "date_format(date_add('hour',-3,%s),'%%Y-%%m-%%d')" % ts


MARCA_F = ("CASE WHEN a.instrmtyp IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' "
           "WHEN a.instrmtyp IN('MD','MC','MS','MK') THEN 'Mastercard' "
           "WHEN a.instrmtyp='AX' THEN 'Amex' WHEN a.instrmtyp='PA' THEN 'Cabal' "
           "WHEN a.instrmtyp='UC' THEN 'UPI' ELSE 'Otra' END")
CISS_NORM = ("CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) "
             "WHEN length(TRIM(a.codrespuestaiss))>=2 THEN "
             "substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) "
             "ELSE NULL END")
GW = ("IF(length(TRIM(COALESCE(a.programaperiferiaorigen,''))) >= 4, "
      "substr(TRIM(a.programaperiferiaorigen), -4), "
      "TRIM(COALESCE(a.programaperiferiaorigen,'')))")

FILT = ("filt AS (SELECT * FROM b WHERE 1=1 "
        "AND ('${marca:text}'='All' OR marca_f IN (${marca:sqlstring})) "
        "AND ('${banco:text}'='All' OR cbe_f IN (${banco:sqlstring})) "
        "AND ('${motivo_emisor:text}'='All' OR ciss_norm IN (${motivo_emisor:sqlstring})) "
        "AND ('${motivo_adqte:text}'='All' OR nri IN (${motivo_adqte:sqlstring})))")

# En ABT0 minuto_utc es timestamp(6) with time zone, y sequence() de Trino no
# acepta timestamps con zona. Todo el SQL NOC trabaja con timestamp UTC sin zona.
TS_UTC = "CAST(%s AT TIME ZONE 'UTC' AS timestamp)"

# Ultimo minuto cargado en ABT0, sin filtros y sin depender del rango elegido.
GM = ("gm AS (SELECT " + TS_UTC % "MAX(minuto_utc)" + " AS m FROM " + ABT0 +
      " WHERE fecha_particion >= date_format(date_add('day',-1,current_date),'%Y-%m-%d'))")

# Ventanas estaticas (solo macros) para que Athena pode particiones y row groups:
# ultimas 12 h del rango + las mismas 6 h de D-1. Cubre las ventanas "actuales"
# ancladas al ultimo minuto con dato mientras la ABT no atrase mas de 6 h.
VENTANA_ESTADO = (
    "((a.minuto_utc >= date_add('hour',-12,$__timeTo()) AND a.minuto_utc < $__timeTo())"
    " OR (a.minuto_utc >= date_add('minute',-375,date_add('day',-1,$__timeTo()))"
    " AND a.minuto_utc < date_add('day',-1,$__timeTo())))")
PART_ESTADO = (part("date_add('minute',-375,date_add('day',-1,$__timeTo()))"),
               part("$__timeTo()"))

VENTANA_RANGO_D1 = (
    "((a.minuto_utc >= $__timeFrom() AND a.minuto_utc < $__timeTo())"
    " OR (a.minuto_utc >= date_add('day',-1,$__timeFrom())"
    " AND a.minuto_utc < date_add('day',-1,$__timeTo())))")
PART_RANGO_D1 = (part("date_add('day',-1,$__timeFrom())"), part("$__timeTo()"))

VENTANA_RANGO = "(a.minuto_utc >= $__timeFrom() AND a.minuto_utc < $__timeTo())"
PART_RANGO = (part("$__timeFrom()"), part("$__timeTo()"))


def base(ventana, particiones):
    """CTE b + filt con TODOS los filtros del tablero (los mismos que la v17.0)."""
    lo, hi = particiones
    return (
        "b AS (SELECT " + TS_UTC % "a.minuto_utc" + " AS ts, TRIM(a.cod_respuesta) AS cr, "
        "TRIM(a.cod_tipo_mensaje) AS cmsg, NULLIF(TRIM(a.nrorechazointerno),'') AS nri, "
        + MARCA_F + " AS marca_f, TRIM(a.codbancoemisor) AS cbe_f, "
        + CISS_NORM + " AS ciss_norm, " + GW + " AS gw, a.latencia_ms AS lat "
        "FROM " + ABT0 + " a" + join_dafil("a.nro_establecimiento") +
        " WHERE a.fecha_particion BETWEEN " + lo + " AND " + hi +
        " AND " + ventana +
        " AND TRIM(a.cod_proceso_autorizacion) NOT IN('92','30')"
        " AND " + CUIT_ABT +
        " AND " + ANCLA + EXCL_ABT + filtros_nuevos("a.nro_establecimiento", "a.instrmtyp") +
        "),\n" + FILT
    )


# t0 / t1: rango real a dibujar. t1 corta en el ultimo minuto cerrado de la ABT.
LIM_RANGO = ("lim AS (SELECT date_trunc('minute',CAST($__timeFrom() AS timestamp)) AS t0, "
             "LEAST(date_trunc('minute',CAST($__timeTo() AS timestamp)), (SELECT m FROM gm)) AS t1)")

# Grilla completa de buckets entre t0 y t1 (sequence admite hasta 10.000 pasos).
GRID = (
    "grid AS (SELECT g AS time FROM lim CROSS JOIN UNNEST("
    "CASE WHEN lim.t1 > " + bucket("GREATEST(lim.t0, lim.t1 - (" + BK + "*9999)*INTERVAL '1' MINUTE)") +
    " THEN sequence(" + bucket("GREATEST(lim.t0, lim.t1 - (" + BK + "*9999)*INTERVAL '1' MINUTE)") +
    ", lim.t1 - INTERVAL '1' MINUTE, " + BK + "*INTERVAL '1' MINUTE)"
    " ELSE CAST(ARRAY[] AS array(timestamp)) END) AS t(g))"
)

# Minutos efectivos de cada bucket (el primero y el ultimo pueden ser parciales).
MINUTOS = ("date_diff('minute', GREATEST(g.time, l.t0), "
           "LEAST(g.time + " + BK + "*INTERVAL '1' MINUTE, l.t1))")


def gw_values(vars_abt):
    plat = [v for v in vars_abt if v["name"] == "plataforma"][0]
    filas = []
    for o in plat["options"]:
        if o["value"] == "$__all":
            continue
        filas.append("('%s','%s')" % (o["value"], o["text"].replace("'", "''")))
    return "gwmap AS (SELECT * FROM (VALUES " + ",".join(filas) + ") AS v(cod, nombre))"


GW_NOMBRE = ("COALESCE(m.nombre, CASE WHEN COALESCE(%s,'')='' THEN 'Sin plataforma' "
             "ELSE 'No mapeado' END) || ' (' || COALESCE(NULLIF(%s,''),'-') || ')'")


# ---------------------------------------------------------------------------
# SQL de cada panel NOC
# ---------------------------------------------------------------------------

def sql_kpis():
    return (
        "-- KPIs NOC. Una sola consulta alimenta los 6 indicadores (paneles 1000-1005).\n"
        "-- r = fin exclusivo de la ventana 'actual': el ultimo minuto de la ABT o el fin\n"
        "--     del rango, lo que sea anterior. Mirando un incidente pasado, alcanza con\n"
        "--     cerrar el rango en el momento que se quiere revisar.\n"
        "WITH " + GM + ",\n"
        "lim AS (SELECT m AS gm, LEAST(date_trunc('minute',CAST($__timeTo() AS timestamp)), m) AS r FROM gm),\n"
        + base(VENTANA_ESTADO, PART_ESTADO) + ",\n"
        "x AS (SELECT ts FROM filt WHERE cmsg<>'1420'),\n"
        "k AS (SELECT\n"
        "  SUM(CASE WHEN ts >= l.r - INTERVAL '5' MINUTE AND ts < l.r THEN 1 ELSE 0 END) AS n5,\n"
        "  SUM(CASE WHEN ts >= l.r - INTERVAL '15' MINUTE AND ts < l.r THEN 1 ELSE 0 END) AS n15,\n"
        "  SUM(CASE WHEN ts >= l.r - INTERVAL '15' MINUTE - INTERVAL '1' DAY AND ts < l.r - INTERVAL '1' DAY THEN 1 ELSE 0 END) AS n15d1,\n"
        "  MAX(CASE WHEN ts >= l.r - INTERVAL '6' HOUR AND ts < l.r THEN ts END) AS lt\n"
        "  FROM x CROSS JOIN lim l)\n"
        "SELECT\n"
        "  date_format(l.r - INTERVAL '1' MINUTE - INTERVAL '3' HOUR, '%d/%m %H:%i') || ' hs' AS ultimo_minuto,\n"
        "  date_diff('minute', l.gm, CAST(current_timestamp AT TIME ZONE 'UTC' AS timestamp)) AS atraso_min,\n"
        "  CAST(" + BK + " AS varchar) || ' min' AS resolucion,\n"
        "  ROUND(k.n5 / 5.0, 1) AS trx_min,\n"
        "  ROUND(100.0 * k.n15 / NULLIF(k.n15d1, 0), 1) AS pct_vs_d1,\n"
        "  date_diff('minute', k.lt, l.r - INTERVAL '1' MINUTE) AS silencio_min\n"
        "FROM lim l CROSS JOIN k"
    )


def sql_procesamiento():
    return (
        "-- Procesamiento completo en TRX por minuto: actual y mismo horario de D-1.\n"
        "-- Tambien devuelve % vs D-1 y latencia, que usan los paneles 1011 y 1012.\n"
        "WITH " + GM + ",\n"
        + LIM_RANGO + ",\n"
        + base(VENTANA_RANGO_D1, PART_RANGO_D1) + ",\n"
        "x AS (SELECT CASE WHEN ts >= l.t0 THEN 'act' ELSE 'd1' END AS serie,\n"
        "  CASE WHEN ts >= l.t0 THEN ts ELSE ts + INTERVAL '1' DAY END AS tsa, cr, cmsg, lat\n"
        "  FROM filt CROSS JOIN lim l\n"
        "  WHERE (ts >= l.t0 AND ts < l.t1)\n"
        "     OR (ts >= l.t0 - INTERVAL '1' DAY AND ts < l.t1 - INTERVAL '1' DAY AND ts < l.t0)),\n"
        "agg AS (SELECT " + bucket("tsa") + " AS time, serie,\n"
        "  SUM(CASE WHEN cmsg<>'1420' THEN 1 ELSE 0 END) AS total,\n"
        "  SUM(CASE WHEN cmsg<>'1420' AND cr IN" + APROB + " THEN 1 ELSE 0 END) AS aprob,\n"
        "  SUM(CASE WHEN cmsg<>'1420' AND cr NOT IN" + APROB + " THEN 1 ELSE 0 END) AS deneg,\n"
        "  SUM(CASE WHEN cmsg='1420' THEN 1 ELSE 0 END) AS rev,\n"
        "  approx_percentile(CASE WHEN cmsg<>'1420' AND lat >= 0 THEN CAST(lat AS double) END, 0.95) AS p95,\n"
        "  approx_percentile(CASE WHEN cmsg<>'1420' AND lat >= 0 THEN CAST(lat AS double) END, 0.50) AS p50\n"
        "  FROM x GROUP BY 1, 2),\n"
        + GRID + "\n"
        "SELECT g.time,\n"
        "  ROUND(1.0 * COALESCE(a.total,0) / NULLIF(" + MINUTOS + ",0), 1) AS \"Total TRX/min\",\n"
        "  ROUND(1.0 * COALESCE(a.aprob,0) / NULLIF(" + MINUTOS + ",0), 1) AS \"Aprobadas/min\",\n"
        "  ROUND(1.0 * COALESCE(a.deneg,0) / NULLIF(" + MINUTOS + ",0), 1) AS \"Denegadas/min\",\n"
        "  ROUND(1.0 * COALESCE(a.rev,0) / NULLIF(" + MINUTOS + ",0), 1) AS \"Reversos/min\",\n"
        "  ROUND(1.0 * COALESCE(d.total,0) / NULLIF(" + MINUTOS + ",0), 1) AS \"Total D-1 TRX/min\",\n"
        "  ROUND(100.0 * COALESCE(a.total,0) / NULLIF(d.total,0), 1) AS \"% vs D-1\",\n"
        "  ROUND(a.p95, 0) AS \"Latencia p95 (ms)\",\n"
        "  ROUND(a.p50, 0) AS \"Latencia p50 (ms)\"\n"
        "FROM grid g CROSS JOIN lim l\n"
        "LEFT JOIN agg a ON a.time = g.time AND a.serie = 'act'\n"
        "LEFT JOIN agg d ON d.time = g.time AND d.serie = 'd1'\n"
        "ORDER BY 1"
    )


def sql_gateways_series(vars_abt):
    return (
        "-- TRX por minuto y TAA por gateway (top 10 por volumen en el rango).\n"
        "-- Buckets sin trafico en cero: si un gateway se cae, la linea baja a 0.\n"
        "WITH " + GM + ",\n"
        + LIM_RANGO + ",\n"
        + base(VENTANA_RANGO, PART_RANGO) + ",\n"
        + gw_values(vars_abt) + ",\n"
        "x AS (SELECT ts, gw, cr FROM filt CROSS JOIN lim l\n"
        "  WHERE ts >= l.t0 AND ts < l.t1 AND cmsg<>'1420'),\n"
        "top AS (SELECT gw, COUNT(*) AS n FROM x GROUP BY gw ORDER BY n DESC LIMIT 10),\n"
        "agg AS (SELECT " + bucket("ts") + " AS time, gw, COUNT(*) AS total,\n"
        "  SUM(CASE WHEN cr IN" + APROB + " THEN 1 ELSE 0 END) AS aprob\n"
        "  FROM x WHERE gw IN (SELECT gw FROM top) GROUP BY 1, 2),\n"
        + GRID + "\n"
        "SELECT g.time, " + GW_NOMBRE % ("t.gw", "t.gw") + " AS metric,\n"
        "  ROUND(1.0 * COALESCE(a.total,0) / NULLIF(" + MINUTOS + ",0), 1) AS trx_min,\n"
        "  ROUND(100.0 * a.aprob / NULLIF(a.total,0), 2) AS taa\n"
        "FROM grid g CROSS JOIN lim l CROSS JOIN top t\n"
        "LEFT JOIN agg a ON a.time = g.time AND a.gw = t.gw\n"
        "LEFT JOIN gwmap m ON m.cod = t.gw\n"
        "ORDER BY 1"
    )


def sql_gateways_estado(vars_abt):
    return (
        "-- Estado por gateway al cierre del rango (o al ultimo minuto con dato):\n"
        "-- ultimos 15 min cerrados contra los mismos 15 min de D-1.\n"
        "WITH " + GM + ",\n"
        "lim AS (SELECT LEAST(date_trunc('minute',CAST($__timeTo() AS timestamp)), m) AS r FROM gm),\n"
        + base(VENTANA_ESTADO, PART_ESTADO) + ",\n"
        + gw_values(vars_abt) + ",\n"
        "x AS (SELECT ts, gw, cr FROM filt WHERE cmsg<>'1420'),\n"
        "act AS (SELECT gw, COUNT(*) AS n, SUM(CASE WHEN cr IN" + APROB + " THEN 1 ELSE 0 END) AS ap\n"
        "  FROM x CROSS JOIN lim l WHERE ts >= l.r - INTERVAL '15' MINUTE AND ts < l.r GROUP BY gw),\n"
        "d1 AS (SELECT gw, COUNT(*) AS n, SUM(CASE WHEN cr IN" + APROB + " THEN 1 ELSE 0 END) AS ap\n"
        "  FROM x CROSS JOIN lim l\n"
        "  WHERE ts >= l.r - INTERVAL '15' MINUTE - INTERVAL '1' DAY AND ts < l.r - INTERVAL '1' DAY GROUP BY gw),\n"
        "ult AS (SELECT gw, MAX(ts) AS lt, COUNT(*) AS n6h\n"
        "  FROM x CROSS JOIN lim l WHERE ts >= l.r - INTERVAL '6' HOUR AND ts < l.r GROUP BY gw),\n"
        "gws AS (SELECT gw FROM act UNION SELECT gw FROM d1 UNION SELECT gw FROM ult)\n"
        "SELECT " + GW_NOMBRE % ("g.gw", "g.gw") + " AS \"Gateway\",\n"
        "  COALESCE(a.n,0) AS \"TRX últ. 15 min\",\n"
        "  COALESCE(d.n,0) AS \"TRX D-1 mismo horario\",\n"
        "  ROUND(100.0 * COALESCE(a.n,0) / NULLIF(d.n,0), 1) AS \"% vs D-1\",\n"
        "  ROUND(100.0 * a.ap / NULLIF(a.n,0), 2) AS \"TAA últ. 15 min\",\n"
        "  ROUND(100.0 * d.ap / NULLIF(d.n,0), 2) AS \"TAA D-1\",\n"
        "  ROUND(100.0 * a.ap / NULLIF(a.n,0) - 100.0 * d.ap / NULLIF(d.n,0), 1) AS \"Δ TAA (pp)\",\n"
        "  date_format(u.lt - INTERVAL '3' HOUR, '%H:%i') AS \"Último tráfico (hs)\",\n"
        "  date_diff('minute', u.lt, l.r - INTERVAL '1' MINUTE) AS \"Silencio (min)\",\n"
        "  COALESCE(u.n6h,0) AS \"TRX últ. 6 h\"\n"
        "FROM gws g CROSS JOIN lim l\n"
        "LEFT JOIN act a ON a.gw = g.gw\n"
        "LEFT JOIN d1 d ON d.gw = g.gw\n"
        "LEFT JOIN ult u ON u.gw = g.gw\n"
        "LEFT JOIN gwmap m ON m.cod = g.gw\n"
        "WHERE COALESCE(a.n,0) + COALESCE(d.n,0) >= 15\n"
        "ORDER BY 4 ASC NULLS LAST, 2 DESC"
    )


# ---------------------------------------------------------------------------
# Paneles
# ---------------------------------------------------------------------------

def target(sql, ref="A"):
    return {"connectionArgs": dict(CONN), "datasource": dict(DS), "format": 1,
            "rawSQL": sql, "refId": ref}


def target_dash(panel_id):
    return {"datasource": dict(DS_DASH), "panelId": panel_id, "refId": "A",
            "withTransforms": False}


def row(pid, title, y):
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
            "id": pid, "panels": [], "title": title, "type": "row"}


def thr(*steps):
    out = []
    for i, (color, value) in enumerate(steps):
        out.append({"color": color, "value": None if i == 0 else value})
    return {"mode": "absolute", "steps": out}


def stat(pid, title, field, x, y, desc, unit="none", thresholds=None, decimals=None,
         mappings=None, fuente=None):
    p = {
        "datasource": dict(DS_DASH) if fuente else dict(DS),
        "description": desc,
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"},
            "mappings": mappings or [],
            "thresholds": thresholds or thr(("#5794F2", None)),
            "unit": unit}, "overrides": []},
        "gridPos": {"h": 4, "w": 4, "x": x, "y": y},
        "id": pid,
        "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "center",
                    "orientation": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^%s$/" % field,
                                      "values": True, "limit": 1},
                    "showPercentChange": False, "textMode": "value", "wideLayout": True},
        "pluginVersion": "10.4.7",
        "title": title,
        "type": "stat",
        "targets": [target_dash(fuente)] if fuente else [target(sql_kpis())],
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    return p


def ts_custom(**kw):
    c = {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text",
         "axisLabel": "", "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "line",
         "fillOpacity": 0, "gradientMode": "none",
         "hideFrom": {"legend": False, "tooltip": False, "viz": False},
         "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 2, "pointSize": 4,
         "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": False,
         "stacking": {"group": "A", "mode": "none"}, "thresholdsStyle": {"mode": "off"}}
    c.update(kw)
    return c


def ov(name, **props):
    m = {"color": lambda v: {"id": "color", "value": {"fixedColor": v, "mode": "fixed"}},
         "width": lambda v: {"id": "custom.lineWidth", "value": v},
         "dash": lambda v: {"id": "custom.lineStyle",
                            "value": {"dash": [8, 4], "fill": "dash"}},
         "fill": lambda v: {"id": "custom.fillOpacity", "value": v},
         "axis": lambda v: {"id": "custom.axisPlacement", "value": v},
         "unit": lambda v: {"id": "unit", "value": v}}
    return {"matcher": {"id": "byName", "options": name},
            "properties": [m[k](v) for k, v in props.items()]}


def timeseries(pid, title, desc, grid, targets, unit, overrides=None, transformations=None,
               thresholds=None, thresholds_style="off", legend_calcs=None, extra_defaults=None,
               custom=None, legend_table=False, datasource=None):
    d = {"color": {"mode": "palette-classic"},
         "custom": ts_custom(thresholdsStyle={"mode": thresholds_style}, **(custom or {})),
         "mappings": [], "thresholds": thresholds or thr(("green", None)), "unit": unit}
    d.update(extra_defaults or {})
    return {
        "datasource": datasource or dict(DS),
        "description": desc,
        "fieldConfig": {"defaults": d, "overrides": overrides or []},
        "gridPos": grid, "id": pid,
        "options": {"legend": {"calcs": legend_calcs or ["lastNotNull", "min", "mean"],
                               "displayMode": "table" if legend_table else "list",
                               "placement": "right" if legend_table else "bottom",
                               "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": targets, "title": title,
        "transformations": transformations or [], "type": "timeseries",
    }


def filtro_campos(nombres):
    return {"id": "filterFieldsByName", "options": {"include": {"names": nombres}}}


PARTICION = {"id": "partitionByValues",
             "options": {"fields": ["metric"], "keepFields": False,
                         "naming": {"asLabels": True}}}


def paneles_noc(vars_abt):
    P = []
    y = 0
    P.append(row(900, "NOC · ¿Está procesando? · frescura del dato, volumen total y latencia", y))
    y += 1

    P.append(stat(
        1000, "Datos hasta (último minuto cerrado)", "ultimo_minuto", 0, y,
        "Último minuto completo que se está mirando, en hora Argentina. Es el último "
        "minuto cargado en ABT0 menos el que está en carga, o el fin del rango si el "
        "rango termina antes. Todos los gráficos NOC terminan en este minuto.",
        thresholds=thr(("#5794F2", None))))
    P.append(stat(
        1001, "Atraso del dato (ABT0 vs ahora)", "atraso_min", 4, y,
        "Minutos entre ahora y el último minuto cargado en ABT0, sin filtros. "
        "Si está en rojo, la falta de tráfico que se ve puede ser la ABT atrasada y "
        "no una caída: confirmar contra la fuente antes de escalar.",
        unit="m", decimals=0, fuente=1000,
        thresholds=thr(("green", None), ("orange", 15), ("red", 30))))
    P.append(stat(
        1002, "Resolución efectiva", "resolucion", 8, y,
        "Tamaño de bucket que están usando los gráficos. Se elige con la variable "
        "Resolución; en 'auto' depende del largo del rango (≤3 h: 1 min, ≤12 h: 5 min, "
        "≤48 h: 15 min, más: 60 min). Durante un incidente conviene forzar 1 min.",
        fuente=1000, thresholds=thr(("#8AB8FF", None))))
    P.append(stat(
        1003, "TRX por minuto · últimos 5 min", "trx_min", 12, y,
        "Promedio de transacciones por minuto (sin reversos) en los 5 minutos cerrados "
        "previos al minuto de 'Datos hasta'. Respeta todos los filtros.",
        unit="short", decimals=1, fuente=1000, thresholds=thr(("#5794F2", None))))
    P.append(stat(
        1004, "Volumen vs D-1 · últimos 15 min", "pct_vs_d1", 16, y,
        "Transacciones de los últimos 15 minutos cerrados contra los mismos 15 minutos "
        "de ayer. Es el indicador de caída: en un corte la TAA puede seguir alta, lo "
        "que baja es el volumen. Arriba de 130% puede indicar reintentos masivos.",
        unit="percent", decimals=1, fuente=1000,
        thresholds=thr(("red", None), ("orange", 70), ("green", 90), ("orange", 130))))
    P.append(stat(
        1005, "Silencio del segmento filtrado", "silencio_min", 20, y,
        "Minutos desde la última transacción que cumple los filtros, medido contra "
        "'Datos hasta' (no contra el reloj, así el atraso de la ABT no cuenta). "
        "Filtrando por Plataforma de pago muestra si ese gateway dejó de transaccionar. "
        "Sin filtros debería estar en 0.",
        unit="m", decimals=0, fuente=1000,
        thresholds=thr(("green", None), ("orange", 2), ("red", 5)),
        mappings=[{"type": "special", "options": {"match": "null", "result": {
            "text": "> 6 h", "color": "red", "index": 0}}}]))
    y += 4

    P.append(timeseries(
        1010, "Procesamiento · TRX por minuto: Total vs D-1, Aprobadas, Denegadas, Reversos",
        "Todo expresado en transacciones por minuto, así cambiar la resolución no cambia "
        "la escala y el último bucket parcial no dibuja una caída falsa. Los buckets sin "
        "tráfico valen 0: una caída se ve como caída. Total excluye reversos (1420) y "
        "procesos 92/30, igual que el resto del tablero.",
        {"h": 9, "w": 12, "x": 0, "y": y},
        [target(sql_procesamiento())], "short",
        overrides=[ov("Total TRX/min", color="#B877D9", width=3),
                   ov("Total D-1 TRX/min", color="#B877D9", width=1, dash=True),
                   ov("Aprobadas/min", color="#73BF69", width=1),
                   ov("Denegadas/min", color="#F2495C", width=1),
                   ov("Reversos/min", color="#FF9830", width=1)],
        transformations=[filtro_campos(["time", "Total TRX/min", "Total D-1 TRX/min",
                                        "Aprobadas/min", "Denegadas/min", "Reversos/min"])],
        custom={"axisSoftMin": 0}))
    P.append(timeseries(
        1011, "Volumen vs D-1 (%)",
        "Total de cada bucket contra el mismo bucket de ayer. 100% = igual que ayer. "
        "Debajo de 70% (rojo) es una caída de volumen aunque la TAA se vea bien.",
        {"h": 9, "w": 6, "x": 12, "y": y},
        [target_dash(1010)], "percent", datasource=dict(DS_DASH),
        transformations=[filtro_campos(["time", "% vs D-1"])],
        thresholds=thr(("red", None), ("orange", 70), ("green", 90), ("orange", 130)),
        thresholds_style="line+area",
        overrides=[ov("% vs D-1", color="#8AB8FF")],
        custom={"axisSoftMin": 0, "axisSoftMax": 130}))
    P.append(timeseries(
        1012, "Latencia de autorización · p50 / p95 (ms)",
        "Percentiles de latencia (latencia_ms de ABT0) de las transacciones del bucket. "
        "Una suba de p95 antes de una caída de volumen suele indicar saturación o "
        "timeouts contra el autorizador o una marca.",
        {"h": 9, "w": 6, "x": 18, "y": y},
        [target_dash(1010)], "ms", datasource=dict(DS_DASH),
        transformations=[filtro_campos(["time", "Latencia p95 (ms)", "Latencia p50 (ms)"])],
        overrides=[ov("Latencia p95 (ms)", color="#F2495C"),
                   ov("Latencia p50 (ms)", color="#8AB8FF", width=1)],
        custom={"axisSoftMin": 0}))
    y += 9

    P.append(row(1100, "NOC · Gateways · ¿quién cayó y quién se recupera?", y))
    y += 1
    P.append(timeseries(
        1101, "TRX por minuto por gateway · top 10 del rango",
        "Transacciones por minuto de los 10 gateways con más volumen en el rango. Los "
        "buckets sin tráfico valen 0: un gateway caído baja a cero y se ve cuándo "
        "vuelve. Para mirar uno solo, usar el filtro Plataforma de pago.",
        {"h": 9, "w": 12, "x": 0, "y": y},
        [target(sql_gateways_series(vars_abt))], "short",
        transformations=[filtro_campos(["time", "metric", "trx_min"]), PARTICION],
        extra_defaults={"displayName": "${__field.labels.metric}"},
        legend_calcs=["lastNotNull", "min", "mean"], legend_table=True,
        custom={"axisSoftMin": 0}))
    P.append(timeseries(
        1102, "TAA por gateway · top 10 del rango",
        "Tasa de aceptación por gateway (mismos 10 que el gráfico de al lado). Un hueco "
        "en la línea es un bucket sin tráfico: mirarlo junto con el volumen.",
        {"h": 9, "w": 12, "x": 12, "y": y},
        [target_dash(1101)], "percent", datasource=dict(DS_DASH),
        transformations=[filtro_campos(["time", "metric", "taa"]), PARTICION],
        extra_defaults={"displayName": "${__field.labels.metric}", "decimals": 1},
        legend_calcs=["lastNotNull", "min", "mean"], legend_table=True,
        custom={"axisSoftMax": 100}))
    y += 9

    tabla = {
        "datasource": dict(DS),
        "description": (
            "Estado de cada gateway en los últimos 15 minutos cerrados contra los mismos "
            "15 minutos de ayer. Ordenado por '% vs D-1' ascendente: los caídos quedan "
            "arriba. 'Silencio' cuenta minutos desde su última transacción. Si se mira "
            "un incidente pasado, la tabla muestra el estado al cierre del rango. "
            "Se omiten gateways con menos de 15 transacciones sumando ambas ventanas."),
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                    "inspect": False, "filterable": True},
                         "mappings": [], "thresholds": thr(("green", None))},
            "overrides": [
                {"matcher": {"id": "byName", "options": "% vs D-1"},
                 "properties": [{"id": "unit", "value": "percent"},
                                {"id": "custom.cellOptions",
                                 "value": {"type": "color-background", "mode": "basic"}},
                                {"id": "thresholds", "value": thr(("red", None), ("orange", 70),
                                                                  ("green", 90), ("orange", 130))}]},
                {"matcher": {"id": "byName", "options": "Silencio (min)"},
                 "properties": [{"id": "custom.cellOptions",
                                 "value": {"type": "color-background", "mode": "basic"}},
                                {"id": "thresholds", "value": thr(("green", None), ("orange", 2),
                                                                  ("red", 5))},
                                {"id": "mappings", "value": [{"type": "special", "options": {
                                    "match": "null", "result": {"text": "> 6 h",
                                                                "color": "red", "index": 0}}}]}]},
                {"matcher": {"id": "byName", "options": "Δ TAA (pp)"},
                 "properties": [{"id": "custom.cellOptions",
                                 "value": {"type": "color-text"}},
                                {"id": "thresholds", "value": thr(("red", None), ("orange", -5),
                                                                  ("green", -2))}]},
                {"matcher": {"id": "byRegexp", "options": "^TAA.*"},
                 "properties": [{"id": "unit", "value": "percent"},
                                {"id": "decimals", "value": 2}]},
                {"matcher": {"id": "byName", "options": "Gateway"},
                 "properties": [{"id": "custom.width", "value": 240}]},
            ]},
        "gridPos": {"h": 11, "w": 24, "x": 0, "y": y},
        "id": 1103,
        "options": {"cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"],
                                                   "countRows": False, "fields": ""},
                    "showHeader": True,
                    "sortBy": [{"displayName": "% vs D-1", "desc": False}]},
        "pluginVersion": "10.4.7",
        "targets": [target(sql_gateways_estado(vars_abt))],
        "title": "Gateways · estado últimos 15 min vs D-1 (caídos arriba)",
        "type": "table",
    }
    P.append(tabla)
    y += 11
    return P, y


# ---------------------------------------------------------------------------
# Ajustes sobre los paneles existentes
# ---------------------------------------------------------------------------

def resolucion_configurable(dash):
    """Cambia los buckets fijos de 15' por el bucket de $resolucion."""
    import re
    pat = re.compile(r"(EXTRACT\(MINUTE FROM [\w.]+\))%15\)")
    n = 0
    for p in dash["panels"]:
        for t in p.get("targets") or []:
            s = t.get("rawSQL")
            if not s:
                continue
            s2, k = pat.subn(lambda m: m.group(1) + "%" + BK + ")", s)
            if k:
                t["rawSQL"] = s2
                n += k
        if p.get("title", "").endswith(" · 15m"):
            p["title"] = p["title"][:-len(" · 15m")] + " · ${resolucion:text}"
    return n


def total_en_panel_10(dash):
    p = [x for x in dash["panels"] if x["id"] == 10][0]
    cola = " FROM f GROUP BY 1 ORDER BY 1;"
    a, b = p["targets"]
    assert a["rawSQL"].count(cola) == 1 and b["rawSQL"].count(cola) == 1
    a["rawSQL"] = a["rawSQL"].replace(cola, ",COUNT(*) AS \"Total TRX\"" + cola)
    b["rawSQL"] = b["rawSQL"].replace(cola, ",COUNT(*) AS \"Total D-1\"" + cola)
    ovs = p["fieldConfig"]["overrides"]
    for nombre, dash_ in (("Total TRX", False), ("Total D-1", True)):
        props = [{"id": "color", "value": {"fixedColor": "#B877D9", "mode": "fixed"}},
                 {"id": "unit", "value": "short"},
                 {"id": "custom.axisPlacement", "value": "right"},
                 {"id": "custom.axisLabel", "value": "Volumen TRX"},
                 {"id": "custom.lineWidth", "value": 1 if dash_ else 2},
                 {"id": "custom.fillOpacity", "value": 0}]
        if dash_:
            props.append({"id": "custom.lineStyle",
                          "value": {"dash": [8, 4], "fill": "dash"}})
        ovs.append({"matcher": {"id": "byName", "options": nombre}, "properties": props})
    p["title"] = ("TAA Actual vs Baseline D-1 + Volumen Total/Aprobadas/Denegadas "
                  "(vs D-1) · ${resolucion:text}")
    p["description"] = ((p.get("description") or "").rstrip() + "\n\n"
                        "Total TRX = Aprobadas + Denegadas del bucket (sin reversos). "
                        "El último bucket puede estar incompleto; para ver caídas usar "
                        "el gráfico 'Procesamiento' de la sección NOC, que va en TRX "
                        "por minuto.").strip()


VAR_RESOLUCION = {
    "current": {"selected": True, "text": "auto (según rango)", "value": "0"},
    "description": "Tamaño del bucket de todos los gráficos. Durante un incidente, 1 min.",
    "hide": 0, "includeAll": False, "label": "Resolución", "multi": False,
    "name": "resolucion",
    "options": [
        {"selected": True, "text": "auto (según rango)", "value": "0"},
        {"selected": False, "text": "1 min", "value": "1"},
        {"selected": False, "text": "5 min", "value": "5"},
        {"selected": False, "text": "15 min", "value": "15"},
        {"selected": False, "text": "60 min", "value": "60"},
    ],
    "query": "auto (según rango) : 0, 1 min : 1, 5 min : 5, 15 min : 15, 60 min : 60",
    "queryValue": "", "skipUrlSync": False, "type": "custom",
}


def main():
    dash = json.load(open(ENTRADA, encoding="utf-8"))
    vars_abt = json.load(open(VARS_ABT, encoding="utf-8"))

    n_bk = resolucion_configurable(dash)
    total_en_panel_10(dash)

    nuevos, alto = paneles_noc(vars_abt)
    for p in dash["panels"]:
        p["gridPos"]["y"] += alto
    dash["panels"] = nuevos + dash["panels"]

    lista = dash["templating"]["list"]
    if not any(v["name"] == "resolucion" for v in lista):
        lista.insert(0, copy.deepcopy(VAR_RESOLUCION))

    dash["uid"] = UID
    dash["title"] = TITULO
    dash["tags"] = TAGS
    dash["timezone"] = TZ
    dash["version"] = 1
    dash.pop("id", None)
    dash["description"] = (
        "Tablero operativo de desvíos sobre ABT0. Arriba, la vista NOC: hasta qué "
        "minuto hay dato, volumen total vs D-1, latencia y estado por gateway.")

    salida = json.dumps(dash, ensure_ascii=False, indent=2)
    json.loads(salida)
    open(SALIDA, "w", encoding="utf-8").write(salida)
    print("OK")
    print("  paneles NOC agregados:      %d" % len(nuevos))
    print("  buckets 15m -> $resolucion: %d" % n_bk)
    print("  paneles totales:            %d" % len(dash["panels"]))
    print("  salida:                     %s" % SALIDA)


if __name__ == "__main__":
    sys.exit(main())
