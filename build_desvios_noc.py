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
import logica_marcas as lm
import logica_desvios as ld

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


MARCA_F = lm.marca_abt0()
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

# Ventana de estado ($ventana, 5 o 15 minutos, como en las alertas).
VENT = "COALESCE(TRY_CAST(NULLIF('${ventana:raw}','') AS INTEGER), 5)"
H = ld.REF_HORAS


def corrido(ts, horas):
    return "date_add('hour', -%d, %s)" % (horas, ts) if horas else ts


def ventana_or(ventanas):
    return "(" + " OR ".join("(a.minuto_utc >= %s AND a.minuto_utc < %s)" % v for v in ventanas) + ")"


def part_or(ventanas):
    return "(" + " OR ".join("a.fecha_particion BETWEEN %s AND %s" % (part(d), part(h))
                             for d, h in ventanas) + ")"


def con_refs(desde, hasta, actual=True):
    """La ventana [desde, hasta) y la misma franja de cada dia de referencia."""
    return ([(desde, hasta)] if actual else []) + [(corrido(desde, h), corrido(hasta, h)) for h in H]


# Estado: ahora (7 h: la ventana con la ABT atrasada hasta 60' y 6 h de silencio por
# gateway) y las mismas 2 h de cada dia de referencia. Solo macros: Athena poda particiones.
V_ESTADO = [("date_add('hour',-7,$__timeTo())", "$__timeTo()")] + [
    (corrido("date_add('hour',-2,$__timeTo())", h), corrido("$__timeTo()", h)) for h in H]
V_RANGO_REF = con_refs("$__timeFrom()", "$__timeTo()")
V_RANGO = [("$__timeFrom()", "$__timeTo()")]


def base(ventanas, extra=""):
    """CTE b + filt con TODOS los filtros del tablero (los mismos que la v17.0).
    `extra`: predicado adicional sobre `a` (el segmento de un panel de detalle)."""
    return (
        "b AS (SELECT " + TS_UTC % "a.minuto_utc" + " AS ts, TRIM(a.cod_respuesta) AS cr, "
        "TRIM(a.cod_tipo_mensaje) AS cmsg, NULLIF(TRIM(a.nrorechazointerno),'') AS nri, "
        + MARCA_F + " AS marca_f, TRIM(a.codbancoemisor) AS cbe_f, "
        + CISS_NORM + " AS ciss_norm, " + GW + " AS gw, a.latencia_ms AS lat "
        "FROM " + ABT0 + " a" + join_dafil("a.nro_establecimiento") +
        " WHERE " + part_or(ventanas) +
        " AND " + ventana_or(ventanas) +
        " AND TRIM(a.cod_proceso_autorizacion) NOT IN('92','30')" + extra +
        " AND " + CUIT_ABT +
        " AND " + ANCLA + EXCL_ABT + filtros_nuevos("a.nro_establecimiento", "a.instrmtyp") +
        "),\n" + FILT
    )


# Ancla (como en las alertas): el ultimo minuto cargado en ABT0 menos 15' de asentamiento,
# porque los ultimos minutos que carga el ETL llegan incompletos. Si la ABT viene mas
# atrasada que 60', se ancla en fin del rango - 60'. Mirando un rango pasado, el ancla es
# el fin del rango.
FIN_RANGO = "date_trunc('minute', CAST($__timeTo() AS timestamp))"
ANC = ("CASE WHEN g.m >= {f} - INTERVAL '{a}' MINUTE "
       "THEN LEAST({f}, date_trunc('minute', g.m) + INTERVAL '1' MINUTE - INTERVAL '{s}' MINUTE) "
       "ELSE {f} - INTERVAL '{a}' MINUTE END").format(f=FIN_RANGO, a=ld.ATRASO_MAX_MIN,
                                                     s=ld.ASENTAMIENTO_MIN)
# lim: gm = ultimo minuto cargado, m = ancla (fin exclusivo de lo que se dibuja),
# fin = fin de la ventana de estado (bloques de 5' cerrados), vent = $ventana.
LIM_ESTADO = ("lim AS (SELECT g.m AS gm, " + ANC + " AS m, " + VENT + " AS vent FROM gm g),\n"
              "lim2 AS (SELECT gm, m, vent, m - (EXTRACT(MINUTE FROM m) % 5) * INTERVAL '1' MINUTE AS fin FROM lim)")

# t0 / t1: rango real a dibujar. t1 corta en el ancla.
LIM_RANGO = ("lim AS (SELECT date_trunc('minute',CAST($__timeFrom() AS timestamp)) AS t0, "
             "(SELECT " + ANC + " FROM gm g) AS t1)")

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


GW_NOMBRE = ("COALESCE(gm_.nombre, CASE WHEN COALESCE(%s,'')='' THEN 'Sin plataforma' "
             "ELSE 'No mapeado' END) || ' (' || COALESCE(NULLIF(%s,''),'-') || ')'")


# ---------------------------------------------------------------------------
# Estado (las reglas de las alertas, logica_desvios.py)
# ---------------------------------------------------------------------------

def sumas_estado():
    """Por epoca (ahora y cada dia de referencia): tx sin reversos, aprobadas y reversos
    en la ventana de estado; tx en 30' (segmento chico) y filas en 30' (si el dia tiene dato)."""
    cols = []
    epocas = [("act", 0)] + [("r%d" % (i + 1), h) for i, h in enumerate(H)]
    for pre, h in epocas:
        w = ("ts >= l.fin - l.vent * INTERVAL '1' MINUTE - INTERVAL '{h}' HOUR "
             "AND ts < l.fin - INTERVAL '{h}' HOUR").format(h=h)
        w30 = ("ts >= l.fin - INTERVAL '{c}' MINUTE - INTERVAL '{h}' HOUR "
               "AND ts < l.fin - INTERVAL '{h}' HOUR").format(c=ld.CHICA_MIN, h=h)
        s = "COALESCE(SUM(CASE WHEN %s THEN 1 ELSE 0 END), 0) AS %s"
        cols += [s % (w + " AND cmsg<>'1420'", pre + "_t"),
                 s % (w + " AND cmsg<>'1420' AND cr IN" + APROB, pre + "_a"),
                 s % (w + " AND cmsg='1420'", pre + "_r"),
                 s % (w30 + " AND cmsg<>'1420'", "act30" if pre == "act" else pre + "_30"),
                 s % (w30, pre + "_n")]
    return ",\n  ".join(cols)


def src_estado(clave=""):
    """Los dias de referencia que no cuentan (feriado o sin datos en la franja) van en NULL."""
    cols = [clave + "l.vent", "a.act_t", "a.act_a", "a.act_r", "a.act30"]
    for i, h in enumerate(H, 1):
        fuera = "%s OR a.r%d_n = 0" % (ld.es_feriado("l.fin - INTERVAL '%d' HOUR" % h), i)
        cols += ["CASE WHEN %s THEN NULL ELSE a.r%d_%s END AS r%d_%s" % (fuera, i, m, i, m)
                 for m in ("t", "a", "r", "30")]
    return ", ".join(cols)


def texto_estado():
    return ld.con_sev("e.st", ld.texto_tipo("e.tipo"))


def sql_kpis():
    v = "e.vent"
    return (
        "-- KPIs NOC: estado del segmento filtrado con la logica de las alertas (logica_desvios.py).\n"
        "-- Ventana de estado = los ultimos $ventana minutos cerrados (bloques de 5') hasta el ancla,\n"
        "-- contra la mediana de la misma franja en " + ld.REF_TXT + " sin feriados.\n"
        "-- Una sola consulta alimenta los 8 indicadores (paneles 1000-1007).\n"
        "WITH " + GM + ",\n" + LIM_ESTADO + ",\n"
        + base(V_ESTADO) + ",\n"
        "agg AS (SELECT\n  " + sumas_estado() + "\n  FROM filt CROSS JOIN lim2 l),\n"
        "src AS (SELECT " + src_estado() + " FROM agg a CROSS JOIN lim2 l),\n"
        + ld.estado_ctes("src") + "\n"
        "SELECT\n"
        "  date_format(l.m - INTERVAL '1' MINUTE - INTERVAL '3' HOUR, '%d/%m %H:%i') || ' hs' AS ultimo_minuto,\n"
        "  date_diff('minute', l.gm, CAST(current_timestamp AT TIME ZONE 'UTC' AS timestamp)) AS atraso_min,\n"
        "  CAST(" + BK + " AS varchar) || ' min' AS resolucion,\n"
        "  date_format(l.fin - l.vent * INTERVAL '1' MINUTE - INTERVAL '3' HOUR, '%H:%i') || '–'"
        " || date_format(l.fin - INTERVAL '3' HOUR, '%H:%i') AS ventana_txt,\n"
        "  CASE WHEN COALESCE(l.gm < " + FIN_RANGO + " - INTERVAL '" + str(ld.LAG_ALERTA_MIN) + "' MINUTE, true)"
        " THEN 'W|⏱ Dato atrasado' ELSE " + texto_estado() + " END AS estado_txt,\n"
        "  " + ld.con_sev("e.sev_vol", ld.num("1.0 * e.act_t / " + v) + " || '/min · ' || "
                          "COALESCE(" + ld.con_signo("e.dvol_v", 0) + " || '%', IF(e.act_t > 0, 'nuevo', 's/b'))") + " AS vol_txt,\n"
        "  " + ld.con_sev("e.sev_taa", "COALESCE(" + ld.num("e.taa_act") + " || '%', '—') || ' · ' || "
                          "COALESCE(" + ld.con_signo("e.dtaa") + " || 'pp', 's/b')") + " AS taa_txt,\n"
        "  " + ld.con_sev("e.sev_den", ld.num("1.0 * e.act_d / " + v) + " || '/min · ' || "
                          "COALESCE('×' || " + ld.num("e.x_d") + ", 's/b')") + " AS den_txt,\n"
        "  " + ld.con_sev("e.sev_rev", ld.num("1.0 * e.act_r / " + v) + " || '/min · ' || "
                          "COALESCE('×' || " + ld.num("e.x_r") + ", 's/b')") + " AS rev_txt,\n"
        "  e.st, e.causa, e.tipo, e.vent, e.n_ref, e.act_t, e.act_d, e.act_r,\n"
        "  ROUND(e.esp_t, 1) AS esp_t, ROUND(e.dvol_v, 1) AS dvol, ROUND(e.z_vol, 2) AS z_vol,\n"
        "  ROUND(e.taa_act, 2) AS taa_act, ROUND(e.taa_esp, 2) AS taa_esp, ROUND(e.dtaa, 2) AS dtaa, ROUND(e.z_taa, 2) AS z_taa,\n"
        "  ROUND(e.x_d, 2) AS x_d, ROUND(e.z_d, 2) AS z_d, ROUND(e.x_r, 2) AS x_r, ROUND(e.z_r, 2) AS z_r\n"
        "FROM est e CROSS JOIN lim2 l"
    )


def sql_gateways_series(vars_abt):
    return (
        "-- TRX por minuto y TAA por gateway (top 10 por volumen en el rango).\n"
        "-- Buckets sin trafico en cero: si un gateway se cae, la linea baja a 0.\n"
        "WITH " + GM + ",\n"
        + LIM_RANGO + ",\n"
        + base(V_RANGO) + ",\n"
        + gw_values(vars_abt) + ",\n"
        "x AS (SELECT ts, gw, cr FROM filt CROSS JOIN lim l\n"
        "  WHERE ts >= l.t0 AND ts < l.t1 AND cmsg<>'1420'),\n"
        "top AS (SELECT gw, COUNT(*) AS n FROM x GROUP BY gw ORDER BY n DESC LIMIT 10),\n"
        "agg AS (SELECT " + bucket("ts") + " AS time, gw, COUNT(*) AS total,\n"
        "  SUM(CASE WHEN cr IN" + APROB + " THEN 1 ELSE 0 END) AS aprob\n"
        "  FROM x WHERE gw IN (SELECT gw FROM top) GROUP BY 1, 2),\n"
        + GRID + "\n"
        "SELECT g.time, " + GW_NOMBRE % ("t.gw", "t.gw") + " AS metric, t.gw AS cod,\n"
        "  ROUND(1.0 * COALESCE(a.total,0) / NULLIF(" + MINUTOS + ",0), 1) AS trx_min,\n"
        "  ROUND(100.0 * a.aprob / NULLIF(a.total,0), 2) AS taa\n"
        "FROM grid g CROSS JOIN lim l CROSS JOIN top t\n"
        "LEFT JOIN agg a ON a.time = g.time AND a.gw = t.gw\n"
        "LEFT JOIN gwmap gm_ ON gm_.cod = t.gw\n"
        "ORDER BY 1"
    )


def sql_gateways_estado(vars_abt):
    v = "e.vent"
    return (
        "-- Estado por gateway con la logica de las alertas: ultimos $ventana minutos cerrados\n"
        "-- contra la mediana de " + ld.REF_TXT + " sin feriados. Los caidos y degradados arriba.\n"
        "WITH " + GM + ",\n" + LIM_ESTADO + ",\n"
        + base(V_ESTADO) + ",\n"
        + gw_values(vars_abt) + ",\n"
        "agg AS (SELECT gw,\n  " + sumas_estado() + ",\n"
        "  MAX(CASE WHEN ts >= l.m - INTERVAL '6' HOUR AND ts < l.m AND cmsg<>'1420' THEN ts END) AS lt\n"
        "  FROM filt CROSS JOIN lim2 l GROUP BY gw),\n"
        "src AS (SELECT " + src_estado("a.gw, a.lt, ") + " FROM agg a CROSS JOIN lim2 l),\n"
        + ld.estado_ctes("src") + "\n"
        "SELECT " + GW_NOMBRE % ("e.gw", "e.gw") + " AS \"Gateway\",\n"
        "  " + texto_estado() + " AS \"Estado\",\n"
        "  ROUND(1.0 * e.act_t / " + v + ", 1) AS \"TRX/min\",\n"
        "  ROUND(e.esp_t / " + v + ", 1) AS \"Habitual/min\",\n"
        "  ROUND(e.dvol_v, 0) AS \"Δ vol %\",\n"
        "  ROUND(e.z_vol, 1) AS \"z vol\",\n"
        "  ROUND(e.taa_act, 2) AS \"Acept.\",\n"
        "  ROUND(e.taa_esp, 2) AS \"Acept. hab.\",\n"
        "  ROUND(e.dtaa, 1) AS \"Δ acept. pp\",\n"
        "  ROUND(e.x_d, 1) AS \"Deneg. ×\",\n"
        "  ROUND(e.x_r, 1) AS \"Revers. ×\",\n"
        "  date_format(e.lt - INTERVAL '3' HOUR, '%H:%i') AS \"Último tráfico\",\n"
        "  date_diff('minute', e.lt, l.m - INTERVAL '1' MINUTE) AS \"Silencio (min)\",\n"
        "  e.gw AS cod\n"
        "FROM est e CROSS JOIN lim2 l\n"
        "LEFT JOIN gwmap gm_ ON gm_.cod = e.gw\n"
        "WHERE e.act_t + COALESCE(e.esp_t, 0) >= e.vent OR e.st IN ('crit', 'warn')\n"
        "ORDER BY CASE e.st WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 WHEN 'info' THEN 2 WHEN 'ok' THEN 3 ELSE 4 END,\n"
        "  CASE e.causa WHEN 'rechazo' THEN e.exc_d WHEN 'reversos' THEN e.exc_r\n"
        "       WHEN 'volumen' THEN COALESCE(e.esp_t, 0) - e.act_t ELSE COALESCE(e.esp_t, e.act_t) END DESC"
    )


# ---------------------------------------------------------------------------
# Series contra lo habitual (mediana de los dias de referencia, por bucket)
# ---------------------------------------------------------------------------

def epoca_de(lo, hi, actual=True):
    """A que epoca pertenece una fila de filt (0 = ahora, 168/336/504 = dias de referencia)."""
    casos = ([(0, lo, hi)] if actual else []) + [(h, corrido(lo, h), corrido(hi, h)) for h in H]
    return "CASE " + " ".join("WHEN ts >= %s AND ts < %s THEN %d" % (d, h_, e) for e, d, h_ in casos) + " END"


def pivote_refs(tabla, campos, tiempo="g.time"):
    """Joins por dia de referencia; el dia va en NULL si fue feriado o no tiene datos."""
    joins, cols = [], []
    for i, h in enumerate(H, 1):
        joins.append("LEFT JOIN %s r%d ON r%d.time = %s AND r%d.ep = %d" % (tabla, i, i, tiempo, i, h))
        joins.append("LEFT JOIN hay h%d ON h%d.ep = %d" % (i, i, h))
        fuera = "%s OR h%d.ep IS NULL" % (ld.es_feriado("%s - INTERVAL '%d' HOUR" % (tiempo, h)), i)
        cols += ["CASE WHEN %s THEN NULL ELSE COALESCE(r%d.%s, 0) END AS r%d_%s" % (fuera, i, c, i, c)
                 for c in campos]
    return joins, cols


def sql_procesamiento():
    joins, cols = pivote_refs("agg", ["total"])
    k = (1, 2, 3)
    return (
        "-- Procesamiento en TRX por minuto: actual contra lo habitual (mediana de " + ld.REF_TXT + "\n"
        "-- sin feriados) y su rango (minimo y maximo de esos dias). Tambien devuelve % vs habitual\n"
        "-- y latencia, que usan los paneles 1011 y 1012.\n"
        "WITH " + GM + ",\n"
        + LIM_RANGO + ",\n"
        + base(V_RANGO_REF) + ",\n"
        "x AS (SELECT ep, ts + ep * INTERVAL '1' HOUR AS tsa, cr, cmsg, lat FROM (\n"
        "  SELECT f.*, " + epoca_de("l.t0", "l.t1") + " AS ep FROM filt f CROSS JOIN lim l) WHERE ep IS NOT NULL),\n"
        "agg AS (SELECT " + bucket("tsa") + " AS time, ep,\n"
        "  SUM(CASE WHEN cmsg<>'1420' THEN 1 ELSE 0 END) AS total,\n"
        "  SUM(CASE WHEN cmsg<>'1420' AND cr IN" + APROB + " THEN 1 ELSE 0 END) AS aprob,\n"
        "  SUM(CASE WHEN cmsg<>'1420' AND cr NOT IN" + APROB + " THEN 1 ELSE 0 END) AS deneg,\n"
        "  SUM(CASE WHEN cmsg='1420' THEN 1 ELSE 0 END) AS rev,\n"
        "  approx_percentile(CASE WHEN cmsg<>'1420' AND lat >= 0 THEN CAST(lat AS double) END, 0.95) AS p95,\n"
        "  approx_percentile(CASE WHEN cmsg<>'1420' AND lat >= 0 THEN CAST(lat AS double) END, 0.50) AS p50\n"
        "  FROM x GROUP BY 1, 2),\n"
        "hay AS (SELECT DISTINCT ep FROM agg),\n"
        + GRID + ",\n"
        "piv AS (SELECT g.time, " + MINUTOS + " AS mins,\n"
        "  COALESCE(a.total,0) AS total, COALESCE(a.aprob,0) AS aprob, COALESCE(a.deneg,0) AS deneg,\n"
        "  COALESCE(a.rev,0) AS rev, a.p95, a.p50,\n  " + ",\n  ".join(cols) + "\n"
        "  FROM grid g CROSS JOIN lim l\n"
        "  LEFT JOIN agg a ON a.time = g.time AND a.ep = 0\n  " + "\n  ".join(joins) + "),\n"
        "hab AS (SELECT piv.*, " + ld.arr(*["r%d_total" % i for i in k]) + " AS ar FROM piv)\n"
        "SELECT time,\n"
        "  ROUND(1.0 * total / NULLIF(mins,0), 1) AS \"Total TRX/min\",\n"
        "  ROUND(" + ld.med("ar") + " / NULLIF(mins,0), 1) AS \"Habitual TRX/min\",\n"
        "  ROUND(element_at(ar, 1) / NULLIF(mins,0), 1) AS \"Habitual mín\",\n"
        "  ROUND(element_at(ar, cardinality(ar)) / NULLIF(mins,0), 1) AS \"Habitual máx\",\n"
        "  ROUND(1.0 * aprob / NULLIF(mins,0), 1) AS \"Aprobadas/min\",\n"
        "  ROUND(1.0 * deneg / NULLIF(mins,0), 1) AS \"Denegadas/min\",\n"
        "  ROUND(1.0 * rev / NULLIF(mins,0), 1) AS \"Reversos/min\",\n"
        "  ROUND(100.0 * total / NULLIF(" + ld.med("ar") + ", 0), 1) AS \"% vs habitual\",\n"
        "  ROUND(p95, 0) AS \"Latencia p95 (ms)\",\n"
        "  ROUND(p50, 0) AS \"Latencia p50 (ms)\"\n"
        "FROM hab ORDER BY 1"
    )


def sql_habitual(extra="", salidas=("taa",)):
    """Serie por bucket contra lo habitual para los paneles del rango (10, 11 y detalles).
    salidas: "taa" (TAA habitual), "total" (Total habitual por bucket), "dpp" (TAA actual
    menos la habitual). La TAA de un dia cuenta en un bucket si tiene al menos 5 tx."""
    actual = "dpp" in salidas
    vent = V_RANGO_REF if actual else con_refs("$__timeFrom()", "$__timeTo()", actual=False)
    joins, cols = pivote_refs("agg", ["t", "a"], tiempo="tm.time")
    k = (1, 2, 3)
    taa_hab = ld.med(ld.arr(*["IF(r%d_t >= 5, 100.0 * r%d_a / r%d_t)" % (i, i, i) for i in k]))
    out = []
    if "taa" in salidas:
        out.append("ROUND(" + taa_hab + ", 2) AS \"TAA habitual\"")
    if "total" in salidas:
        out.append("ROUND(" + ld.med(ld.arr(*["r%d_t" % i for i in k])) + ", 0) AS \"Total habitual\"")
    if "dpp" in salidas:
        out.append("ROUND(IF(a0_t > 0, 100.0 * a0_a / a0_t) - " + taa_hab + ", 1) AS \"Δpp vs habitual\"")
    return (
        "-- Lo habitual de cada bucket: mediana de la misma franja en " + ld.REF_TXT + ", sin feriados.\n"
        "WITH " + base(vent, extra) + ",\n"
        "x AS (SELECT ep, ts + ep * INTERVAL '1' HOUR AS tsa, cr FROM (\n"
        "  SELECT f.*, " + epoca_de("$__timeFrom()", "$__timeTo()", actual) + " AS ep FROM filt f\n"
        "  WHERE cmsg<>'1420') WHERE ep IS NOT NULL),\n"
        "agg AS (SELECT " + bucket("tsa") + " AS time, ep, COUNT(*) AS t,\n"
        "  SUM(CASE WHEN cr IN" + APROB + " THEN 1 ELSE 0 END) AS a FROM x GROUP BY 1, 2),\n"
        "hay AS (SELECT DISTINCT ep FROM agg),\n"
        "tm AS (SELECT DISTINCT time FROM agg" + (" WHERE ep = 0" if actual else "") + "),\n"
        "piv AS (SELECT tm.time" + (", COALESCE(a0.t, 0) AS a0_t, COALESCE(a0.a, 0) AS a0_a" if actual else "") +
        ",\n  " + ",\n  ".join(cols) + "\n  FROM tm" +
        ("\n  LEFT JOIN agg a0 ON a0.time = tm.time AND a0.ep = 0" if actual else "") +
        "\n  " + "\n  ".join(joins) + ")\n"
        "SELECT time, " + ", ".join(out) + " FROM piv ORDER BY 1"
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
        "gridPos": {"h": 4, "w": 3, "x": x, "y": y},
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
             "options": {"fields": ["metric", "cod"], "keepFields": False,
                         "naming": {"asLabels": True}}}


def paneles_noc(vars_abt):
    P = []
    y = 0
    P.append(row(900, "NOC · ¿Está procesando? · frescura del dato, volumen total y latencia", y))
    y += 1

    sev = ld.mapeos_severidad()
    v_txt = ("ventana de estado = los últimos $ventana minutos cerrados (bloques de 5') hasta "
             "«Datos hasta», contra la mediana de la misma franja en " + ld.REF_TXT +
             " (sin feriados). Misma lógica que las alertas.")
    P.append(stat(
        1000, "Datos hasta", "ultimo_minuto", 0, y,
        "Último minuto que se está mirando, en hora Argentina. Como en las alertas, es el "
        "último minuto cargado en ABT0 menos %d' de asentamiento (los últimos minutos que "
        "carga el ETL llegan incompletos), o el fin del rango si el rango termina antes. "
        "Todos los gráficos NOC terminan acá." % ld.ASENTAMIENTO_MIN,
        thresholds=thr(("#5794F2", None))))
    P.append(stat(
        1001, "Atraso", "atraso_min", 3, y,
        "Minutos entre ahora y el último minuto cargado en ABT0, sin filtros. A partir de "
        "%d' el estado dice «Dato atrasado»: la falta de tráfico puede ser la ABT, no una "
        "caída." % ld.LAG_ALERTA_MIN,
        unit="m", decimals=0, fuente=1000,
        thresholds=thr(("green", None), ("orange", 15), ("red", ld.LAG_ALERTA_MIN))))
    P.append(stat(
        1002, "Resolución", "resolucion", 6, y,
        "Tamaño de bucket de los gráficos (variable Resolución; en 'auto' depende del "
        "largo del rango). Durante un incidente conviene 1 min.",
        fuente=1000, thresholds=thr(("#8AB8FF", None))))
    P.append(stat(
        1003, "Estado", "estado_txt", 9, y,
        "Estado del segmento filtrado con las reglas de las alertas: crítico o atención "
        "si cae el volumen (−%d %% / −%d %%), cae la aceptación (−%s / −%s pp), suben las "
        "denegadas (×%d / ×%d) o los reversos (×%d / ×%d), y además la diferencia es "
        "significativa (z). Con menos de %d tx en la ventana no se evalúa, salvo que un "
        "segmento chico deje de operar en %d'. " % (
            ld.LIM_VOL[0], ld.LIM_VOL[1], ld.LIM_TAA[0], ld.LIM_TAA[1], ld.DEN_X[0], ld.DEN_X[1],
            ld.REV_X[0], ld.REV_X[1], ld.PISO, ld.CHICA_MIN) + v_txt,
        fuente=1000, mappings=sev))
    P.append(stat(
        1004, "Volumen", "vol_txt", 12, y,
        "TRX por minuto en la ventana y diferencia contra lo habitual. Se pinta si cae "
        "%d %% o más y la caída es significativa (z ≤ −%d, Poisson). " % (ld.LIM_VOL[0], ld.Z_MIN) + v_txt,
        fuente=1000, mappings=sev))
    P.append(stat(
        1005, "Aceptación", "taa_txt", 15, y,
        "Tasa de aceptación de la ventana y diferencia en pp contra la habitual. Se pinta "
        "si cae %s pp o más, es significativa (z ≤ −%d, binomial) y explica al menos 2 "
        "rechazos de más por minuto. " % (ld.LIM_TAA[0], ld.Z_MIN) + v_txt,
        fuente=1000, mappings=sev))
    P.append(stat(
        1006, "Denegadas", "den_txt", 18, y,
        "Denegadas por minuto y cuántas veces lo habitual. Se pinta desde ×%d si el "
        "exceso es de al menos 2 por minuto y z ≥ %d. " % (ld.DEN_X[0], ld.Z_DEN) + v_txt,
        fuente=1000, mappings=sev))
    P.append(stat(
        1007, "Reversos", "rev_txt", 21, y,
        "Reversos (1420) por minuto y cuántas veces lo habitual: respuestas que no llegan "
        "a tiempo. Se pinta desde ×%d si el exceso es de al menos 1 por minuto y z ≥ %d. "
        % (ld.REV_X[0], ld.Z_DEN) + v_txt,
        fuente=1000, mappings=sev))
    y += 4

    gris = "#8b93a7"
    P.append(timeseries(
        1010, "Procesamiento · TRX por minuto: actual vs habitual, Aprobadas, Denegadas, Reversos",
        "Todo en transacciones por minuto, así cambiar la resolución no cambia la escala y "
        "el último bucket parcial no dibuja una caída falsa. Los buckets sin tráfico valen 0. "
        "Habitual = mediana del mismo bucket en " + ld.REF_TXT + " sin feriados; la banda "
        "gris va del mínimo al máximo de esos días. Total excluye reversos (1420) y "
        "procesos 92/30.",
        {"h": 9, "w": 12, "x": 0, "y": y},
        [target(sql_procesamiento())], "short",
        overrides=[ov("Total TRX/min", color="#B877D9", width=3),
                   ov("Habitual TRX/min", color=gris, width=1, dash=True),
                   {"matcher": {"id": "byName", "options": "Habitual máx"},
                    "properties": [{"id": "custom.fillBelowTo", "value": "Habitual mín"},
                                   {"id": "custom.lineWidth", "value": 0},
                                   {"id": "custom.fillOpacity", "value": 18},
                                   {"id": "color", "value": {"fixedColor": gris, "mode": "fixed"}},
                                   {"id": "custom.hideFrom", "value": {"legend": True, "tooltip": False, "viz": False}}]},
                   {"matcher": {"id": "byName", "options": "Habitual mín"},
                    "properties": [{"id": "custom.lineWidth", "value": 0},
                                   {"id": "color", "value": {"fixedColor": gris, "mode": "fixed"}},
                                   {"id": "custom.hideFrom", "value": {"legend": True, "tooltip": False, "viz": False}}]},
                   ov("Aprobadas/min", color="#73BF69", width=1),
                   ov("Denegadas/min", color="#F2495C", width=1),
                   ov("Reversos/min", color="#FF9830", width=1)],
        transformations=[filtro_campos(["time", "Total TRX/min", "Habitual TRX/min", "Habitual mín",
                                        "Habitual máx", "Aprobadas/min", "Denegadas/min",
                                        "Reversos/min"])],
        custom={"axisSoftMin": 0}))
    P.append(timeseries(
        1011, "Volumen vs habitual (%)",
        "Total de cada bucket contra la mediana del mismo bucket en " + ld.REF_TXT +
        " (sin feriados). 100 %% = lo habitual. Con los umbrales de las alertas: debajo de "
        "%d %% es atención y debajo de %d %% es crítico; arriba de %d %% es más tráfico que "
        "lo habitual." % (100 - ld.LIM_VOL[0], 100 - ld.LIM_VOL[1], 100 + ld.LIM_VOL[0]),
        {"h": 9, "w": 6, "x": 12, "y": y},
        [target_dash(1010)], "percent", datasource=dict(DS_DASH),
        transformations=[filtro_campos(["time", "% vs habitual"])],
        thresholds=thr(("red", None), ("orange", 100 - ld.LIM_VOL[1]),
                       ("green", 100 - ld.LIM_VOL[0]), ("blue", 100 + ld.LIM_VOL[0])),
        thresholds_style="line+area",
        overrides=[ov("% vs habitual", color="#8AB8FF")],
        custom={"axisSoftMin": 0, "axisSoftMax": 140}))
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
        transformations=[filtro_campos(["time", "metric", "cod", "trx_min"]), PARTICION],
        extra_defaults={"displayName": "${__field.labels.metric}"},
        legend_calcs=["lastNotNull", "min", "mean"], legend_table=True,
        custom={"axisSoftMin": 0}))
    P.append(timeseries(
        1102, "TAA por gateway · top 10 del rango",
        "Tasa de aceptación por gateway (mismos 10 que el gráfico de al lado). Un hueco "
        "en la línea es un bucket sin tráfico: mirarlo junto con el volumen.",
        {"h": 9, "w": 12, "x": 12, "y": y},
        [target_dash(1101)], "percent", datasource=dict(DS_DASH),
        transformations=[filtro_campos(["time", "metric", "cod", "taa"]), PARTICION],
        extra_defaults={"displayName": "${__field.labels.metric}", "decimals": 1},
        legend_calcs=["lastNotNull", "min", "mean"], legend_table=True,
        custom={"axisSoftMax": 100}))
    y += 9

    def colu(nombre, *props):
        return {"matcher": {"id": "byName", "options": nombre}, "properties": list(props)}
    tabla = {
        "datasource": dict(DS),
        "description": (
            "Estado de cada gateway con las reglas de las alertas: los últimos $ventana minutos "
            "cerrados contra la mediana de la misma franja en " + ld.REF_TXT + " (sin feriados). "
            "Primero los críticos, después atención, ordenados por cuánto pesan (transacciones "
            "que faltan o rechazos de más). «z vol» dice si la diferencia de volumen es ruido "
            "(|z| < 2) o real. «Silencio» = minutos desde su última transacción. Mirando un "
            "incidente pasado, la tabla muestra el estado al cierre del rango."),
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                    "inspect": False, "filterable": True, "minWidth": 70},
                         "mappings": [], "thresholds": thr(("green", None))},
            "overrides": [
                colu("Estado", {"id": "mappings", "value": ld.mapeos_severidad()},
                     {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                     {"id": "custom.width", "value": 150}),
                colu("Δ vol %", {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0}),
                colu("z vol", {"id": "decimals", "value": 1}),
                colu("Acept.", {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2}),
                colu("Acept. hab.", {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2}),
                colu("Δ acept. pp", {"id": "unit", "value": "suffix: pp"}, {"id": "decimals", "value": 1}),
                colu("Deneg. ×", {"id": "unit", "value": "prefix:×"}, {"id": "decimals", "value": 1}),
                colu("Revers. ×", {"id": "unit", "value": "prefix:×"}, {"id": "decimals", "value": 1}),
                colu("Silencio (min)", {"id": "displayName", "value": "Silencio"},
                     {"id": "unit", "value": "m"},
                     {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                     {"id": "thresholds", "value": thr(("green", None), ("orange", 2), ("red", 5))},
                     {"id": "mappings", "value": [{"type": "special", "options": {
                         "match": "null", "result": {"text": "> 6 h", "color": "red", "index": 0}}}]}),
                colu("cod", {"id": "custom.hidden", "value": True}),
                colu("Gateway", {"id": "custom.width", "value": 220}),
            ]},
        "gridPos": {"h": 11, "w": 24, "x": 0, "y": y},
        "id": 1103,
        "options": {"cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"],
                                                   "countRows": False, "fields": ""},
                    "showHeader": True},
        "pluginVersion": "10.4.7",
        "targets": [target(sql_gateways_estado(vars_abt))],
        "title": "Gateways · estado vs habitual (críticos arriba)",
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


def habitual_en_paneles(dash):
    """Paneles 10, 11 y los detalles por marca y producto: la referencia pasa de D-1 a lo
    habitual (mediana de los dias de referencia sin feriados), con los mismos filtros."""
    from build_desvios_abt import SEGMENTOS
    P = {p["id"]: p for p in dash["panels"]}

    def renombrar(p, viejo, nuevo):
        for ov_ in p["fieldConfig"]["overrides"]:
            if ov_["matcher"].get("options") == viejo:
                ov_["matcher"]["options"] = nuevo

    p = P[10]
    a, b = p["targets"]
    cola = " FROM f GROUP BY 1 ORDER BY 1;"
    assert a["rawSQL"].count(cola) == 1
    a["rawSQL"] = a["rawSQL"].replace(cola, ",COUNT(*) AS \"Total TRX\"" + cola)
    b["rawSQL"] = sql_habitual(salidas=("taa", "total"))
    renombrar(p, "Baseline D-1", "TAA habitual")
    for nombre, hab_ in (("Total TRX", False), ("Total habitual", True)):
        props = [{"id": "color", "value": {"fixedColor": "#B877D9", "mode": "fixed"}},
                 {"id": "unit", "value": "short"},
                 {"id": "custom.axisPlacement", "value": "right"},
                 {"id": "custom.axisLabel", "value": "Volumen TRX"},
                 {"id": "custom.lineWidth", "value": 1 if hab_ else 2},
                 {"id": "custom.fillOpacity", "value": 0}]
        if hab_:
            props.append({"id": "custom.lineStyle", "value": {"dash": [8, 4], "fill": "dash"}})
        p["fieldConfig"]["overrides"].append({"matcher": {"id": "byName", "options": nombre},
                                              "properties": props})
    p["title"] = ("TAA actual vs habitual + Volumen Total/Aprobadas/Denegadas "
                  "· ${resolucion:text}")
    p["description"] = ((p.get("description") or "").rstrip() + "\n\n"
                        "Habitual = mediana del mismo bucket en " + ld.REF_TXT + " sin feriados "
                        "(la TAA de un día cuenta si tiene al menos 5 tx en el bucket). "
                        "Total TRX = Aprobadas + Denegadas del bucket (sin reversos).").strip()

    p = P[11]
    p["targets"][0]["rawSQL"] = sql_habitual(salidas=("dpp",))
    renombrar(p, "Δpp vs D-1", "Δpp vs habitual")
    p["title"] = "Desvío TAA vs habitual (Δpp)"

    for pid, (_, dim, valor) in SEGMENTOS.items():
        expr = lm.marca_abt0() if dim == "marca" else lm.producto()
        P[pid]["targets"][1]["rawSQL"] = sql_habitual(" AND (%s) = '%s'" % (expr, valor))
        renombrar(P[pid], "Baseline D-1", "TAA habitual")


VAR_VENTANA = {
    "current": {"selected": True, "text": "5 minutos", "value": "5"},
    "description": ("Minutos que se miden para el estado (indicadores y tabla de gateways). "
                    "5' reacciona antes; 15' tiene menos ruido. Igual que en las alertas."),
    "hide": 0, "includeAll": False, "label": "Ventana de estado", "multi": False,
    "name": "ventana",
    "options": [{"selected": True, "text": "5 minutos", "value": "5"},
                {"selected": False, "text": "15 minutos", "value": "15"}],
    "query": "5 minutos : 5, 15 minutos : 15",
    "queryValue": "", "skipUrlSync": False, "type": "custom",
}


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
    habitual_en_paneles(dash)

    nuevos, alto = paneles_noc(vars_abt)
    for p in dash["panels"]:
        p["gridPos"]["y"] += alto
    dash["panels"] = nuevos + dash["panels"]

    lista = dash["templating"]["list"]
    if not any(v["name"] == "resolucion" for v in lista):
        lista.insert(0, copy.deepcopy(VAR_RESOLUCION))
    if not any(v["name"] == "ventana" for v in lista):
        lista.insert(1, copy.deepcopy(VAR_VENTANA))

    dash["uid"] = UID
    dash["title"] = TITULO
    dash["tags"] = TAGS
    dash["timezone"] = TZ
    dash["version"] = 1
    dash.pop("id", None)
    dash["description"] = (
        "Tablero operativo de desvíos sobre ABT0. Arriba, la vista NOC: hasta qué "
        "minuto hay dato, estado vs lo habitual (" + ld.REF_TXT + ", logica de las alertas), "
        "volumen total, latencia y estado por gateway.")

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
