# -*- coding: utf-8 -*-
"""
Genera el dashboard "Desvios · CUIT/Establecimiento" YA OPTIMIZADO:
  - refresh 1m
  - filtro CUIT con IN no-correlacionado (semi-join, identico a EXISTS)
  - paneles 800/801 con Marca/Banco/Motivo Emisor/Motivo Adqte
Cuadrantes y rechazos: identicos al original.
Salida: desvios_optimizado.json
"""
import json

DS = {"type": "grafana-athena-datasource", "uid": "bex5w10g549vkd"}
CONN = {"catalog": "AwsDataCatalog", "database": "payway_poststage_prod_db", "region": "us-east-1",
        "resultReuseEnabled": True, "resultReuseMaxAgeInMinutes": 5}


def T(sql, refId="A", fmt=1):
    return {"connectionArgs": CONN, "datasource": DS, "format": fmt, "rawSQL": sql, "refId": refId}


def row(rid, title, y):
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": rid, "title": title, "type": "row"}


# ---- fragmentos SQL faithful ----
COLS = ("CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,"
        "TRIM(a.codrespuestaautorizacion) AS cr,TRIM(a.codtipomensajeautorizacion) AS cmsg,"
        "NULLIF(TRIM(a.codrespuestaiss),'') AS ciss,NULLIF(TRIM(a.nrorechazointerno),'') AS nri,"
        "CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END AS marca_f,"
        "TRIM(a.codbancoemisor) AS cbe_f,"
        "CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END AS ciss_norm")

# CUIT optimizado (IN no correlacionado). Variante A: input sin REPLACE.
CUITA = ("(COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN "
         "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
         "WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT)))")
# Variante B: input con REPLACE (panel 900).
CUITB = ("(COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN "
         "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
         "WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT)))")

ESTBIN_BASE = ("('${establecimiento:raw}'='' OR TRIM(a.nroestablecimiento)='${establecimiento:raw}') "
               "AND ('${bin:raw}'='' OR substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8)='${bin:raw}')")

# CUITs excluidos del dashboard (lista NOC, dedupe aplicado). Se resuelve UNA vez
# como anti-join. Guardas contra NULL: filas con nroestablecimiento no numerico no
# se pierden, y el subquery exige nro_establecimiento IS NOT NULL para que el
# NOT IN no anule todo el resultado.
CUITS_EXCLUIDOS = "30712334610,30715313681,30717791904,30718438906,30716325586,30691399768,33714595569,30709228419"
EXCL = (" AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR "
        "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN "
        "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
        "WHERE e.nro_establecimiento IS NOT NULL AND "
        "TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN (" + CUITS_EXCLUIDOS + ")))")

ESTBIN = ESTBIN_BASE + EXCL

FILT = ("filt AS (SELECT * FROM b WHERE 1=1 "
        "AND ('${marca:text}'='All' OR marca_f IN (${marca:sqlstring})) "
        "AND ('${banco:text}'='All' OR CAST(TRY_CAST(cbe_f AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) "
        "AND ('${motivo_emisor:text}'='All' OR ciss_norm IN (${motivo_emisor:sqlstring})) "
        "AND ('${motivo_adqte:text}'='All' OR nri IN (${motivo_adqte:sqlstring})))")


def base_b(incl1420):
    c = " AND TRIM(a.codtipomensajeautorizacion)<>'1420'" if incl1420 else ""
    return ("WITH b AS (SELECT " + COLS + " FROM payway_poststage_prod_db.ctx_autorizaciones_eps a "
            "WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d') "
            "AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')" + c + " AND " + CUITA + " AND " + ESTBIN + "),\n" + FILT)


panels = []

def stat(pid, x, title, fields, unit, decimals, steps, fixedcolor=None, w=6):
    fc = {"color": {"mode": "thresholds"}, "decimals": decimals, "mappings": [],
          "thresholds": {"mode": "absolute", "steps": steps}, "unit": unit}
    if unit == "percent":
        fc["max"] = 100; fc["min"] = 0
    return {
        "datasource": DS, "fieldConfig": {"defaults": fc, "overrides": []},
        "gridPos": {"h": 4, "w": w, "x": x, "y": 1}, "id": pid,
        "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": fields, "values": False},
                    "showPercentChange": False, "textMode": "auto", "wideLayout": True},
        "pluginVersion": "10.4.7", "title": title, "type": "stat"
    }

# Estado General
panels.append(row(100, "Estado General", 0))

p1 = stat(1, 0, "TAA", "/^taa$/", "percent", 2,
          [{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "#73bf69", "value": 85}])
p1["targets"] = [T(base_b(True) + "\nSELECT ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo();")]
panels.append(p1)

p2 = stat(2, 6, "Total TRX", "", "short", 2, [{"color": "#5794F2", "value": None}])
p2["targets"] = [T(base_b(True) + "\nSELECT COUNT(*) AS total FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo();")]
panels.append(p2)

p3 = stat(3, 12, "Denegadas", "/^denegadas$/", "short", 0, [{"color": "#f2495c", "value": None}])
p3["targets"] = [T(base_b(True) + "\nSELECT SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS denegadas FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo();")]
panels.append(p3)

p4 = stat(4, 18, "Tasa Rechazo", "/^tasa_rechazo$/", "percent", 2,
          [{"color": "#73bf69", "value": None}, {"color": "orange", "value": 15}, {"color": "red", "value": 20}])
p4["targets"] = [T(base_b(True) + "\nSELECT ROUND(100.0*SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS tasa_rechazo FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo();")]
panels.append(p4)

# Filtros inline (referencia directa a columnas de a) usados por paneles marca/producto/820/911...
INLINE = (
    " AND ('${marca:text}'='All' OR (CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END) IN (${marca:sqlstring}))"
    " AND ('${banco:text}'='All' OR CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring}))"
    " AND ('${motivo_emisor:text}'='All' OR (CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END) IN (${motivo_emisor:sqlstring}))"
    " AND ('${motivo_adqte:text}'='All' OR NULLIF(TRIM(a.nrorechazointerno),'') IN (${motivo_adqte:sqlstring}))"
)

# fieldConfig de linea estandar (timeseries) con overrides arbitrarios
def ts_fc(overrides, unit="short", decimals=None, soft=None, fill=8, extra_def=None):
    custom = {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text", "axisLabel": "",
              "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "line", "fillOpacity": fill,
              "gradientMode": "none", "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "insertNulls": False, "lineInterpolation": "smooth", "lineWidth": 2, "pointSize": 5,
              "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": True,
              "stacking": {"group": "A", "mode": "none"}, "thresholdsStyle": {"mode": "off"}}
    if soft:
        custom["axisSoftMin"], custom["axisSoftMax"] = soft
    d = {"color": {"mode": "palette-classic"}, "custom": custom, "mappings": [],
         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}, "unit": unit}
    if decimals is not None:
        d["decimals"] = decimals
    if extra_def:
        d.update(extra_def)
    return {"defaults": d, "overrides": overrides}

def ovr(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": props}

CLR = lambda c: {"id": "color", "value": {"fixedColor": c, "mode": "fixed"}}

def ts_panel(pid, x, y, w, h, title, fc, targets, transformations=None, links=None):
    p = {"datasource": DS, "fieldConfig": fc, "gridPos": {"h": h, "w": w, "x": x, "y": y}, "id": pid,
         "options": {"legend": {"calcs": ["mean", "min"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "targets": targets, "title": title, "type": "timeseries"}
    if transformations:
        p["transformations"] = transformations
    return p

# ---- helper paneles por MARCA/PRODUCTO (8 paneles casi identicos) ----
def marca_overrides(taa_name):
    base_line = [
        ovr("Baseline D-1", [CLR("#ff9830"), {"id": "unit", "value": "percent"}, {"id": "custom.lineWidth", "value": 1},
            {"id": "custom.lineStyle", "value": {"dash": [8, 4], "fill": "dash"}}, {"id": "custom.fillOpacity", "value": 0},
            {"id": "custom.axisPlacement", "value": "left"}]),
        ovr(taa_name, [CLR("#73bf69"), {"id": "unit", "value": "percent"}, {"id": "custom.axisPlacement", "value": "left"},
            {"id": "custom.lineWidth", "value": 2}]),
        ovr("Aprobadas", [CLR("#5794F2"), {"id": "unit", "value": "short"}, {"id": "custom.axisPlacement", "value": "right"},
            {"id": "custom.axisLabel", "value": "Volumen TRX"}, {"id": "custom.lineWidth", "value": 1},
            {"id": "custom.fillOpacity", "value": 0}, {"id": "custom.lineStyle", "value": {"fill": "solid"}}]),
        ovr("Denegadas", [CLR("#f2495c"), {"id": "unit", "value": "short"}, {"id": "custom.axisPlacement", "value": "right"},
            {"id": "custom.axisLabel", "value": "Volumen TRX"}, {"id": "custom.lineWidth", "value": 1},
            {"id": "custom.fillOpacity", "value": 0}, {"id": "custom.lineStyle", "value": {"fill": "solid"}}]),
    ]
    return base_line

def marca_sql(instr_set, taa_name, baseline=False):
    bk = ("date_trunc('minute',CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)"
          "-(EXTRACT(MINUTE FROM CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)%15)*INTERVAL '1' MINUTE AS bk")
    tsexpr = "CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR"
    instr = ",".join("'%s'" % s for s in instr_set)
    if not baseline:
        where = ("a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')\n"
                 "    AND " + tsexpr + ">=$__timeFrom() AND " + tsexpr + "<$__timeTo()")
        return ("WITH agg AS (\n  SELECT \n    " + bk + ",\n"
                "    SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) IN('0000','0400','0900') THEN 1 ELSE 0 END) AS aprob,\n"
                "    SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS deneg,\n"
                "    COUNT(*) AS total\n  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n  WHERE " + where + "\n"
                "    AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')\n    AND TRIM(a.codtipomensajeautorizacion)<>'1420'\n"
                "    AND a.INSTRMTYP IN(" + instr + ") AND " + CUITA + " AND " + ESTBIN + INLINE + "\n  GROUP BY 1\n)\n"
                "SELECT bk AS time,\n  ROUND(100.0*aprob/NULLIF(total,0),2) AS \"" + taa_name + "\",\n  aprob AS \"Aprobadas\",\n  deneg AS \"Denegadas\"\nFROM agg ORDER BY 1;")
    else:
        where = ("a.fecha BETWEEN date_format(date_add('hour',-3,date_add('day',-1,$__timeFrom())),'%Y-%m-%d') AND date_format(date_add('hour',-3,date_add('day',-1,$__timeTo())),'%Y-%m-%d')\n"
                 "    AND " + tsexpr + ">=date_add('day',-1,$__timeFrom()) AND " + tsexpr + "<date_add('day',-1,$__timeTo())")
        return ("WITH agg AS (\n  SELECT \n    " + bk + ",\n"
                "    SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) IN('0000','0400','0900') THEN 1 ELSE 0 END) AS aprob,\n"
                "    COUNT(*) AS total\n  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n  WHERE " + where + "\n"
                "    AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')\n    AND TRIM(a.codtipomensajeautorizacion)<>'1420'\n"
                "    AND a.INSTRMTYP IN(" + instr + ") AND " + CUITA + " AND " + ESTBIN + INLINE + "\n  GROUP BY 1\n)\n"
                "SELECT bk+INTERVAL '1' DAY AS time,\n  ROUND(100.0*aprob/NULLIF(total,0),2) AS \"Baseline D-1\"\nFROM agg ORDER BY 1;")

def marca_panel(pid, x, y, w, title, taa_name, instr_set):
    fc = ts_fc(marca_overrides(taa_name), unit="short")
    tg = [T(marca_sql(instr_set, taa_name, False), "A"), T(marca_sql(instr_set, taa_name, True), "B")]
    return ts_panel(pid, x, y, w, 7, title, fc, tg)

# ---- Panel 10: TAA Actual vs Baseline ----
fc10 = {"defaults": {"color": {"fixedColor": "#73bf69", "mode": "fixed"},
        "custom": {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text", "axisLabel": "",
                   "axisPlacement": "auto", "axisSoftMax": 100, "axisSoftMin": 70, "barAlignment": 0, "drawStyle": "line",
                   "fillOpacity": 8, "gradientMode": "none", "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                   "insertNulls": False, "lineInterpolation": "smooth", "lineWidth": 2, "pointSize": 5,
                   "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": True,
                   "stacking": {"group": "A", "mode": "none"}, "thresholdsStyle": {"mode": "dashed"}},
        "decimals": 1, "mappings": [],
        "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "#73bf69", "value": 85}]},
        "unit": "percent"},
        "overrides": marca_overrides("TAA Actual")}
# corregir override Baseline de panel10 (sin axisPlacement left, fillOpacity 0, lineWidth1)
fc10["overrides"][0] = ovr("Baseline D-1", [CLR("#ff9830"), {"id": "custom.lineWidth", "value": 1},
    {"id": "custom.lineStyle", "value": {"dash": [8, 4], "fill": "dash"}}, {"id": "custom.fillOpacity", "value": 0}])
tail10A = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo())\n"
           "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,"
           "ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS \"TAA Actual\","
           "SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Aprobadas\","
           "SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Denegadas\" FROM f GROUP BY 1 ORDER BY 1;")
sql10B = ("WITH lim AS (SELECT date_add('day',-1,$__timeFrom()) AS d,date_add('day',-1,$__timeTo()) AS h,"
          "date_format(date_add('hour',-3,date_add('day',-1,$__timeFrom())),'%Y-%m-%d') AS fi,"
          "date_format(date_add('hour',-3,date_add('day',-1,$__timeTo())),'%Y-%m-%d') AS ff),\n"
          "b AS (SELECT " + COLS + " FROM payway_poststage_prod_db.ctx_autorizaciones_eps a CROSS JOIN lim l "
          "WHERE a.fecha BETWEEN l.fi AND l.ff AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') "
          "AND TRIM(a.codtipomensajeautorizacion)<>'1420' AND " + CUITA + " AND " + ESTBIN + "),\n" + FILT + ",\n"
          "f AS (SELECT * FROM filt WHERE ts>=(SELECT d FROM lim) AND ts<(SELECT h FROM lim))\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE+INTERVAL '1' DAY AS time,"
          "ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS \"Baseline D-1\" FROM f GROUP BY 1 ORDER BY 1;")
p10 = {"datasource": DS, "fieldConfig": fc10, "gridPos": {"h": 8, "w": 16, "x": 0, "y": 6}, "id": 10,
       "options": {"legend": {"calcs": ["lastNotNull", "mean", "min"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                   "tooltip": {"mode": "multi", "sort": "none"}},
       "targets": [T(base_b(True) + tail10A, "A"), T(sql10B, "B")],
       "title": "TAA Actual vs Baseline D-1 + Volumen Aprobadas/Denegadas · 15m", "type": "timeseries"}
panels.append(row(200, "¿Cuándo baja la TAA?", 5))
panels.append(p10)

# ---- Panel 11: Δpp ----
fc11 = {"defaults": {"color": {"mode": "thresholds"},
        "custom": {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text", "axisLabel": "",
                   "axisPlacement": "auto", "axisSoftMax": 10, "axisSoftMin": -10, "barAlignment": 0, "drawStyle": "bars",
                   "fillOpacity": 80, "gradientMode": "none", "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                   "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 0, "pointSize": 5,
                   "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": False,
                   "stacking": {"group": "A", "mode": "none"}, "thresholdsStyle": {"mode": "dashed"}},
        "decimals": 1, "mappings": [],
        "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -3}, {"color": "#73bf69", "value": 0}]},
        "unit": "short"}, "overrides": []}
sql11 = ("WITH raw AS (SELECT " + COLS + " FROM payway_poststage_prod_db.ctx_autorizaciones_eps a "
         "WHERE a.fecha BETWEEN LEAST(date_format(date_add('hour',-3,date_add('day',-1,$__timeFrom())),'%Y-%m-%d'),date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d')) "
         "AND GREATEST(date_format(date_add('hour',-3,date_add('day',-1,$__timeTo())),'%Y-%m-%d'),date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')) "
         "AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') AND TRIM(a.codtipomensajeautorizacion)<>'1420' AND " + CUITA + " AND " + ESTBIN + "),\n"
         + FILT.replace("FROM b", "FROM raw") + ",\n"
         "act AS (SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS bk,ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() GROUP BY 1),\n"
         "bas AS (SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE+INTERVAL '1' DAY AS bk,ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa FROM filt WHERE ts>=date_add('day',-1,$__timeFrom()) AND ts<date_add('day',-1,$__timeTo()) GROUP BY 1)\n"
         "SELECT a.bk AS time,ROUND(a.taa-b.taa,1) AS \"Δpp vs D-1\" FROM act a JOIN bas b ON a.bk=b.bk ORDER BY 1;")
p11 = {"datasource": DS, "fieldConfig": fc11, "gridPos": {"h": 8, "w": 8, "x": 16, "y": 6}, "id": 11,
       "options": {"legend": {"calcs": ["mean", "min"], "displayMode": "list", "placement": "bottom", "showLegend": True},
                   "tooltip": {"mode": "single", "sort": "none"}},
       "targets": [T(sql11, "A")], "title": "Desvío TAA (Δpp)", "type": "timeseries"}
panels.append(p11)

panels.append(row(810, "Establecimientos del CUIT · qué local impacta la tasa", 14))

# ---- Panel 800: tabla establecimientos (CON fix de filtros) ----
EC = ("ec AS (SELECT nro_establecimiento, MAX(nombre_fantasia) AS nf \n  FROM prisma_ab_analytics_prod_db.establecimiento \n"
      "  WHERE TRY_CAST(REPLACE(cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT)\n  GROUP BY nro_establecimiento)")
trx_where = ("a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')\n"
             "    AND EXISTS(SELECT 1 FROM ec WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=ec.nro_establecimiento)\n    AND " + ESTBIN + INLINE)
sql800 = ("WITH " + EC + ",\ntrx AS (\n  SELECT TRIM(a.nroestablecimiento) AS nroest,\n"
          "    CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,\n"
          "    TRIM(a.codrespuestaautorizacion) AS cr,\n    TRIM(a.codtipomensajeautorizacion) AS cmsg,\n    TRIM(a.codprocesoautorizacion1) AS cproc\n"
          "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n  WHERE " + trx_where + "\n),\n"
          "act AS (\n  SELECT nroest,\n    COUNT(*) AS total,\n    SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS aprob,\n"
          "    SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS deneg\n  FROM trx\n"
          "  WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cproc NOT IN('92','30')\n  GROUP BY nroest\n),\n"
          "base AS (\n  SELECT TRIM(a.nroestablecimiento) AS nroest,\n"
          "    ROUND(100.0*SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa_base\n"
          "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n"
          "  WHERE a.fecha BETWEEN date_format(date_add('hour',-3,date_add('day',-1,$__timeFrom())),'%Y-%m-%d') AND date_format(date_add('hour',-3,date_add('day',-1,$__timeTo())),'%Y-%m-%d')\n"
          "    AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR>=date_add('day',-1,$__timeFrom()) AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR<date_add('day',-1,$__timeTo())\n"
          "    AND TRIM(a.codtipomensajeautorizacion)<>'1420' AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')\n"
          "    AND EXISTS(SELECT 1 FROM ec WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=ec.nro_establecimiento)\n    AND " + ESTBIN + INLINE + "\n  GROUP BY TRIM(a.nroestablecimiento)\n)\n"
          "SELECT \n  act.nroest AS \"Establecimiento\",\n  COALESCE(ec.nf, act.nroest) AS \"Nombre\",\n  act.total AS \"Total TRX\",\n  act.deneg AS \"Denegadas\",\n"
          "  ROUND(100.0*act.aprob/NULLIF(act.total,0),2) AS \"TAA %\",\n  base.taa_base AS \"Baseline D-1 %\",\n"
          "  ROUND(ROUND(100.0*act.aprob/NULLIF(act.total,0),2) - base.taa_base, 1) AS \"Δpp\"\n"
          "FROM act \nLEFT JOIN ec ON TRY_CAST(act.nroest AS INTEGER)=ec.nro_establecimiento\nLEFT JOIN base ON act.nroest=base.nroest\nORDER BY act.deneg DESC\nLIMIT 100;")
fc800 = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}, "mappings": [],
         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
         "overrides": [
            ovr("TAA %", [{"id": "unit", "value": "percent"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "green", "value": 85}]}}]),
            ovr("Δpp", [{"id": "unit", "value": "short"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -3}, {"color": "green", "value": 0}]}}]),
            ovr("Denegadas", [{"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}}, {"id": "color", "value": {"mode": "continuous-reds"}}])
         ]}
p800 = {"datasource": DS, "fieldConfig": fc800, "gridPos": {"h": 9, "w": 24, "x": 0, "y": 15}, "id": 800,
        "options": {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                    "showHeader": True, "sortBy": [{"desc": True, "displayName": "Denegadas"}]},
        "pluginVersion": "10.4.7", "targets": [T(sql800)], "title": "Establecimientos del CUIT · ranking por denegaciones", "type": "table"}
panels.append(p800)

# ---- Panel 801: TAA por establecimiento Top 8 (CON fix de filtros) ----
sql801 = ("WITH " + EC + ",\nb AS (\n  SELECT CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,\n"
          "    TRIM(a.nroestablecimiento) AS nroest,\n    TRIM(a.codrespuestaautorizacion) AS cr,\n    TRIM(a.codtipomensajeautorizacion) AS cmsg,\n    TRIM(a.codprocesoautorizacion1) AS cproc\n"
          "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n  WHERE " + trx_where + "\n),\n"
          "f AS (SELECT * FROM b WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cproc NOT IN('92','30')),\n"
          "vol AS (SELECT nroest, COUNT(*) AS n FROM f GROUP BY nroest ORDER BY n DESC LIMIT 8),\n"
          "agg AS (\n  SELECT date_trunc('minute',f.ts)-(EXTRACT(MINUTE FROM f.ts)%15)*INTERVAL '1' MINUTE AS bk,\n"
          "    COALESCE(ec.nf, f.nroest)||' ('||f.nroest||')' AS est,\n    ROUND(100.0*SUM(CASE WHEN f.cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa\n"
          "  FROM f \n  JOIN vol ON f.nroest=vol.nroest\n  LEFT JOIN ec ON TRY_CAST(f.nroest AS INTEGER)=ec.nro_establecimiento\n  GROUP BY 1,2\n)\n"
          "SELECT bk AS time, est AS metric, taa AS \"TAA %\" FROM agg ORDER BY 1;")
fc801 = ts_fc([], unit="percent", decimals=1, soft=(50, 100), fill=0)
fc801["defaults"]["custom"]["lineWidth"] = 2
p801 = {"datasource": DS, "fieldConfig": fc801, "gridPos": {"h": 8, "w": 24, "x": 0, "y": 24}, "id": 801,
        "options": {"legend": {"calcs": ["mean", "min"], "displayMode": "table", "placement": "right", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [T(sql801, "A", 0)],
        "transformations": [{"id": "partitionByValues", "options": {"fields": ["metric"], "keepFields": False, "naming": {"asLabels": True}}}],
        "title": "TAA por establecimiento · Top 8 por volumen · 15m", "type": "timeseries"}
panels.append(p801)

panels.append(row(300, "¿Por qué baja? · Cuadrantes + Motivos", 32))
F_DENEG = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900'))\n")
LINK = {"targetBlank": False, "title": "🔎 Ver CUITs afectados (mismo filtro)",
        "url": "/d/desvios-establecimientos?${cuit:queryparam}&${establecimiento:queryparam}&${marca:queryparam}&${banco:queryparam}&${motivo_emisor:queryparam}&${motivo_adqte:queryparam}&${__url_time_range}"}
RISK_NRI = "'TA001','TA002','TA003','TA004','TA005','TA006','TA007','TA008','TA009','TCADQ','LMT01','LMT02','LIM01','LIM02'"

# Panel 20 Cuadrantes
tail20 = (F_DENEG + "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,"
          "SUM(CASE WHEN nri IS NULL AND ciss IS NOT NULL AND TRIM(ciss) NOT IN('59','63','83') THEN 1 ELSE 0 END) AS \"Op. Emisor\","
          "SUM(CASE WHEN TRIM(ciss) IN('59','63','83') THEN 1 ELSE 0 END) AS \"Ri. Emisor\","
          "SUM(CASE WHEN nri IS NOT NULL AND nri NOT IN(" + RISK_NRI + ") THEN 1 ELSE 0 END) AS \"Op. Adqte.\","
          "SUM(CASE WHEN nri IN(" + RISK_NRI + ") THEN 1 ELSE 0 END) AS \"Ri. Adqte.\" FROM f GROUP BY 1 ORDER BY 1;")
fc20 = ts_fc([ovr("Op. Emisor", [CLR("#f2495c")]), ovr("Ri. Emisor", [CLR("#ff9830")]),
              ovr("Op. Adqte.", [CLR("#0FB496")]), ovr("Ri. Adqte.", [CLR("#5794F2")])], unit="short", fill=12)
fc20["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p20 = ts_panel(20, 0, 33, 8, 7, "Cuadrantes · 15m", fc20, [T(base_b(False) + tail20)])
p20["options"]["legend"]["calcs"] = ["sum"]; p20["options"]["legend"]["displayMode"] = "list"
panels.append(p20)

# Panel 21 Fondos + DNH
tail21 = (F_DENEG + "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,"
          "SUM(CASE WHEN ciss IN('51','0051') THEN 1 ELSE 0 END) AS \"Fondos insuf.\","
          "SUM(CASE WHEN ciss IN('05','0005') THEN 1 ELSE 0 END) AS \"Do Not Honor\" FROM f GROUP BY 1 ORDER BY 1;")
fc21 = ts_fc([ovr("Fondos insuf.", [CLR("#f2495c")]), ovr("Do Not Honor", [CLR("#ff6347")])], unit="short", fill=10)
fc21["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
fc21["defaults"]["links"] = [LINK]
p21 = ts_panel(21, 8, 33, 8, 7, "Fondos Insuf. + Do Not Honor · 15m", fc21, [T(base_b(False) + tail21)])
p21["options"]["legend"]["calcs"] = ["sum"]; p21["options"]["legend"]["displayMode"] = "list"
panels.append(p21)

# Panel 22 Otros motivos
tail22 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND (ciss IS NULL OR ciss NOT IN('51','0051','05','0005')))\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,"
          "SUM(CASE WHEN ciss IN('54','0054') THEN 1 ELSE 0 END) AS \"Tarj. expirada\","
          "SUM(CASE WHEN ciss IN('55','0055') THEN 1 ELSE 0 END) AS \"PIN incorrecto\","
          "SUM(CASE WHEN ciss IN('57','0057') THEN 1 ELSE 0 END) AS \"Trx no permitida\","
          "SUM(CASE WHEN ciss IN('59','0059','63','0063','83','0083') THEN 1 ELSE 0 END) AS \"Fraude/Seguridad\","
          "SUM(CASE WHEN ciss IN('61','0061','65','0065') THEN 1 ELSE 0 END) AS \"Límites\","
          "SUM(CASE WHEN ciss IN('91','0091','96','0096') THEN 1 ELSE 0 END) AS \"Error sistema\","
          "SUM(CASE WHEN nri LIKE 'CAT%' OR nri LIKE 'MAC%' OR nri LIKE 'TPE%' THEN 1 ELSE 0 END) AS \"Prog. reintentos\" FROM f GROUP BY 1 ORDER BY 1;")
fc22 = ts_fc([], unit="short", fill=8)
fc22["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p22 = ts_panel(22, 16, 33, 8, 7, "Otros Motivos (sin Fondos/DNH) · 15m", fc22, [T(base_b(False) + tail22)])
p22["options"]["legend"]["calcs"] = ["sum"]; p22["options"]["legend"]["displayMode"] = "list"
panels.append(p22)

panels.append(row(310, "Detalle por cuadrante · motivos individuales (denegaciones)", 40))

# Panel 23 Op. Emisor Top 20
cat23 = ("('01','Llame emisor'),('14','Tarj. invalida'),('41','Perdida'),('43','Robada'),('54','Expirada'),('55','PIN incorrecto'),"
         "('57','No permitida'),('61','Excede limite'),('62','Restringida'),('65','Excede freq.'),('91','Emisor no disp.'),('96','Error sistema'),"
         "('00','Aprobado'),('02','Consulte al emisor'),('03','Estab. invalido'),('04','Capturar tarjeta'),('06','Error'),('07','Recoger (fraude)'),"
         "('08','Honrar con identif.'),('10','Aprob. parcial'),('12','Trx invalida'),('13','Monto invalido'),('15','Emisor invalido'),('19','Vuelva a ingresar'),"
         "('1A','Autenticacion adic.'),('21','Ninguna accion'),('30','Error formato'),('39','Sin cuenta credito'),('46','Cuenta cerrada'),('52','Sin cta corriente'),"
         "('53','Sin cta ahorros'),('58','No permitida terminal'),('64','No cumple ALD'),('6P','Verificacion fallida'),('70','Datos PIN requeridos'),('71','PIN no cambiado'),"
         "('75','Excede intentos PIN'),('76','Reversion no solicit.'),('77','From Account invalido'),('78','Cuenta invalida'),('80','Sin impacto'),('81','Error cripto PIN'),"
         "('82','CAM/CVV negativo'),('84','Ciclo autoriz. inv.'),('85','No rechazado'),('86','No verifica PIN'),('88','Fallo cripto'),('89','PIN inaceptable'),"
         "('92','No enrutar'),('94','Trx duplicada'),('N3','Servicio caja no disp.'),('N4','Excede limite efectivo'),('N7','Falla CVV2'),('N8','Excede monto preaut.')")
tail23 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND nri IS NULL AND ciss IS NOT NULL AND ciss_norm NOT IN('59','63','83') AND ciss_norm NOT IN('51','05'))\n"
          ", cat(codigo,desc_short) AS (VALUES " + cat23 + "),\n"
          "codigos_con_desc AS (\n  SELECT f.ts, \n    COALESCE(f.ciss_norm,f.ciss,'sin') AS cod,\n    COALESCE(cat.desc_short, COALESCE(f.ciss_norm,f.ciss,'sin')) AS d\n  FROM f LEFT JOIN cat ON cat.codigo = f.ciss_norm\n),\n"
          "totales AS (SELECT cod, d, COUNT(*) AS total FROM codigos_con_desc GROUP BY cod, d),\n"
          "top20 AS (SELECT cod, d, ROW_NUMBER() OVER (ORDER BY total DESC) AS rn FROM totales)\n"
          "SELECT date_trunc('minute',c.ts)-(EXTRACT(MINUTE FROM c.ts)%15)*INTERVAL '1' MINUTE AS time,\n  c.cod || '-' || c.d AS motivo,\n  COUNT(*) AS rechazos\n"
          "FROM codigos_con_desc c JOIN top20 t ON t.cod = c.cod AND t.rn <= 20\nGROUP BY 1, 2 ORDER BY 1;")
fc23 = ts_fc([], unit="short", fill=8); fc23["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
fc23["defaults"]["links"] = [LINK]
p23 = {"datasource": DS, "fieldConfig": fc23, "gridPos": {"h": 7, "w": 6, "x": 0, "y": 41}, "id": 23,
       "options": {"legend": {"calcs": ["sum", "max"], "displayMode": "table", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}},
       "targets": [T(base_b(False) + tail23, "A", 0)],
       "transformations": [{"id": "partitionByValues", "options": {"fields": ["motivo"], "keepFields": False, "naming": {"asLabels": True}}}],
       "title": "Op. Emisor · Top 20 motivos · 15m", "type": "timeseries"}
panels.append(p23)

# Panel 24 Riesgo Emisor motivos
tail24 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND ciss IN('59','0059','63','0063','83','0083'))\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,\n"
          "SUM(CASE WHEN ciss IN('59','0059') THEN 1 ELSE 0 END) AS \"59-Fraude sospecha\",\n"
          "SUM(CASE WHEN ciss IN('63','0063') THEN 1 ELSE 0 END) AS \"63-Viol. seguridad\",\n"
          "SUM(CASE WHEN ciss IN('83','0083') THEN 1 ELSE 0 END) AS \"83-Fraude MC\"\nFROM f GROUP BY 1 ORDER BY 1;")
fc24 = ts_fc([ovr("59-Fraude sospecha", [CLR("#f2495c")]), ovr("63-Viol. seguridad", [CLR("#ff9830")]), ovr("83-Fraude MC", [CLR("#e02f44")])], unit="short", fill=8)
fc24["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p24 = ts_panel(24, 6, 41, 6, 7, "Riesgo Emisor · motivos · 15m", fc24, [T(base_b(False) + tail24)])
p24["options"]["legend"]["calcs"] = ["sum"]; p24["options"]["legend"]["displayMode"] = "list"
panels.append(p24)

# Panel 25 Op. Adquirente motivos
tail25 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND nri IS NOT NULL AND nri NOT IN(" + RISK_NRI + "))\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,\n"
          "SUM(CASE WHEN nri LIKE 'CAT%' THEN 1 ELSE 0 END) AS \"CAT* (Prog. reintentos)\",\n"
          "SUM(CASE WHEN nri LIKE 'MAC%' THEN 1 ELSE 0 END) AS \"MAC* (MAC)\",\n"
          "SUM(CASE WHEN nri LIKE 'TPE%' THEN 1 ELSE 0 END) AS \"TPE* (Terminal)\",\n"
          "SUM(CASE WHEN nri LIKE 'DERP%' THEN 1 ELSE 0 END) AS \"DERP* (Derivacion)\",\n"
          "SUM(CASE WHEN nri LIKE 'PIN%' THEN 1 ELSE 0 END) AS \"PIN*\",\n"
          "SUM(CASE WHEN nri NOT LIKE 'CAT%' AND nri NOT LIKE 'MAC%' AND nri NOT LIKE 'TPE%' AND nri NOT LIKE 'DERP%' AND nri NOT LIKE 'PIN%' THEN 1 ELSE 0 END) AS \"Otros\"\nFROM f GROUP BY 1 ORDER BY 1;")
fc25 = ts_fc([], unit="short", fill=8); fc25["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p25 = ts_panel(25, 12, 41, 6, 7, "Op. Adquirente · motivos · 15m", fc25, [T(base_b(False) + tail25)])
p25["options"]["legend"]["calcs"] = ["sum"]; p25["options"]["legend"]["displayMode"] = "list"
panels.append(p25)

# Panel 26 Riesgo Adquirente motivos
tail26 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND nri IN(" + RISK_NRI + "))\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,\n"
          + ",\n".join("SUM(CASE WHEN nri='%s' THEN 1 ELSE 0 END) AS \"%s\"" % (c, c) for c in ["TA001","TA002","TA003","TA004","TA005","TA006","TA007","TA008","TA009","TCADQ"])
          + ",\nSUM(CASE WHEN nri IN('LMT01','LMT02') THEN 1 ELSE 0 END) AS \"LMT*\",\n"
          "SUM(CASE WHEN nri IN('LIM01','LIM02') THEN 1 ELSE 0 END) AS \"LIM*\"\nFROM f GROUP BY 1 ORDER BY 1;")
fc26 = ts_fc([], unit="short", fill=8); fc26["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p26 = ts_panel(26, 18, 41, 6, 7, "Riesgo Adquirente · motivos · 15m", fc26, [T(base_b(False) + tail26)])
p26["options"]["legend"]["calcs"] = ["sum"]; p26["options"]["legend"]["displayMode"] = "list"
panels.append(p26)

panels.append(row(821, "Descomposición CodResp → Origen · de qué está hecho cada rechazo", 48))
CAT_ISS = ("('00','Aprobado'),('01','Llame emisor'),('02','Consulte emisor'),('03','Estab. invalido'),('04','Capturar tarjeta'),('05','Denegada'),('06','Error'),('07','Recoger (fraude)'),('08','Honrar con identif.'),('10','Aprob. parcial'),('12','Trx invalida'),('13','Monto invalido'),('14','Tarj. invalida'),('15','Emisor invalido'),('19','Vuelva a ingresar'),('21','Ninguna accion'),('30','Error formato'),('39','Sin cuenta credito'),('41','Tarj. perdida'),('43','Tarj. robada'),('46','Cuenta cerrada'),('51','Fondos insuf.'),('52','Sin cta corriente'),('53','Sin cta ahorros'),('54','Tarj. expirada'),('55','PIN incorrecto'),('57','Trx no permitida'),('58','No permitida terminal'),('59','Fraude sospecha'),('61','Excede limite'),('62','Tarj. restringida'),('63','Viol. seguridad'),('64','No cumple ALD'),('65','Excede freq.'),('70','Datos PIN req.'),('71','PIN no cambiado'),('75','Excede intentos PIN'),('76','Reversion no solic.'),('77','From Account inv.'),('78','Cuenta invalida'),('79','Ciclo vida/Reversada'),('80','Sin impacto'),('81','Error cripto PIN'),('82','CAM/CVV negativo'),('83','Fraude MC'),('84','Ciclo autoriz. inv.'),('85','No rechazado'),('86','No verifica PIN'),('88','Fallo cripto'),('89','PIN inaceptable'),('91','Emisor no disp.'),('92','No enrutar'),('93','Violacion ley'),('94','Trx duplicada'),('96','Error sistema'),('1A','Autenticacion adic.'),('6P','Verificacion fallida'),('N3','Servicio caja no disp.'),('N4','Excede limite efectivo'),('N7','Falla CVV2'),('N8','Excede monto preaut.'),('P5','Desbloqueo PIN deneg.'),('P6','Cambio PIN denegado'),('Q1','Autenticacion fallida'),('R0','Suspension pago'),('R1','Revocacion autoriz.'),('R2','No califica Visa PIN'),('R3','Detener comerciantes'),('Z5','Cuenta valida monto no'),('Z6','Uso invalido MCC'),('B1','Recargo no permitido')")
CAT_NRI = ("('CAT01','Reintentos Visa Cat.1'),('CAT23','Reintentos Visa Cat.2-4'),('MAC02','Reintentos MC 72h'),('MAC03','Reintentos MC 31d'),('MAC24','Reintentos MC 1h'),('MAC25','Reintentos MC 24h'),('MAC26','Reintentos MC 48h'),('MAC27','Reintentos MC 96h'),('MAC28','Reintentos MC 6d'),('MAC29','Reintentos MC 8d'),('MAC30','Reintentos MC 10d'),('TPE01','Reintentos MC 7trx/dia'),('TPE02','Reintentos MC 35trx/mes'),('PA001','Supera limite devol.'),('PA002','Digito verif. invalido'),('PA003','Fullbanda no manual'),('PA004','Bandera mismatch'),('PA005','Supera limite monto'),('PA006','Sin bandera config.'),('BI001','Bimon solo Debito'),('BI002','Bimon est. no habil.'),('BI003','Bimon no extranjeras'),('BI004','Bimon no propina'),('BI005','Bimon no aut.parc/DA'),('TIP01','Propina no aut.parc'),('TIP02','Propina supera 20%'),('TIP03','Propina no cuotas'),('TIP04','Propina no preaut'),('TIP05','Propina rubro no habil'),('TIP06','Propina monto cero'),('99991','Lista negra'),('99995','Cuotas no permit. est.')")
ciss_norm_expr = "CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END"
sql820 = ("WITH b AS (\n  SELECT \n    TRIM(a.codrespuestaautorizacion) AS codresp,\n    NULLIF(TRIM(a.codrespuestaiss),'') AS ciss,\n    NULLIF(TRIM(a.nrorechazointerno),'') AS nri,\n"
          "    " + ciss_norm_expr + " AS ciss_norm,\n"
          "    CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,\n"
          "    TRIM(a.codtipomensajeautorizacion) AS cmsg,\n    TRIM(a.codprocesoautorizacion1) AS cproc,\n"
          "    CASE WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'MASTERCARD' ELSE 'VISA' END AS marca_cat,\n"
          "    CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END AS marca_f,\n"
          "    TRIM(a.codbancoemisor) AS cbe_f\n  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n"
          "  WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')\n"
          "    AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')\n    AND " + CUITA + " AND " + ESTBIN + INLINE + "\n),\n"
          "f AS (SELECT * FROM b WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND codresp NOT IN('0000','0400','0900')),\n"
          "clasificado AS (\n  SELECT codresp,\n    CASE \n      WHEN nri IS NULL AND ciss IS NOT NULL AND ciss_norm NOT IN('59','63','83','51','05') THEN 'Op. Emisor'\n"
          "      WHEN nri IS NOT NULL AND nri NOT IN(" + RISK_NRI + ") THEN 'Op. Adquirente'\n      ELSE NULL END AS cuadrante,\n"
          "    CASE WHEN nri IS NULL AND ciss IS NOT NULL THEN ciss_norm WHEN nri IS NOT NULL THEN nri ELSE NULL END AS origen\n  FROM f\n),\n"
          "agg AS (SELECT cuadrante, codresp, origen, COUNT(*) AS rechazos FROM clasificado WHERE cuadrante IS NOT NULL AND origen IS NOT NULL GROUP BY cuadrante, codresp, origen),\n"
          "cat_iss(codigo,d) AS (VALUES " + CAT_ISS + "),\n"
          "cat_nri(codigo,d) AS (VALUES " + CAT_NRI + ")\n"
          "SELECT \n  agg.cuadrante AS \"Cuadrante\",\n  agg.codresp || '-' || COALESCE(ci2.d, agg.codresp) AS \"CodResp (comercio)\",\n"
          "  agg.origen || '-' || COALESCE(\n    CASE WHEN agg.cuadrante='Op. Emisor' THEN ci.d ELSE cn.d END, \n    agg.origen) AS \"Codigo Origen\",\n"
          "  agg.rechazos AS \"Rechazos\",\n  ROUND(100.0*agg.rechazos/SUM(agg.rechazos) OVER (PARTITION BY agg.cuadrante, agg.codresp),1) AS \"% dentro CodResp\"\n"
          "FROM agg\nLEFT JOIN cat_iss ci ON agg.cuadrante='Op. Emisor' AND ci.codigo=agg.origen\nLEFT JOIN cat_nri cn ON agg.cuadrante='Op. Adquirente' AND cn.codigo=agg.origen\n"
          "LEFT JOIN cat_iss ci2 ON ci2.codigo=agg.codresp\nORDER BY agg.rechazos DESC\nLIMIT 200;")
fc820 = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}, "mappings": [],
         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
         "overrides": [
            ovr("Rechazos", [{"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}}, {"id": "color", "value": {"mode": "continuous-reds"}}]),
            ovr("% dentro CodResp", [{"id": "unit", "value": "percent"}, {"id": "custom.cellOptions", "value": {"mode": "basic", "type": "gauge"}}, {"id": "max", "value": 100}, {"id": "min", "value": 0}]),
            ovr("Cuadrante", [{"id": "custom.width", "value": 140}])
         ]}
p820 = {"datasource": DS, "fieldConfig": fc820, "gridPos": {"h": 11, "w": 24, "x": 0, "y": 49}, "id": 820,
        "options": {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                    "showHeader": True, "sortBy": [{"desc": True, "displayName": "Rechazos"}]},
        "pluginVersion": "10.4.7", "targets": [T(sql820)],
        "title": "Relación CodResp (comercio) → Código Origen (emisor/adqte) · descomposición", "type": "table"}
panels.append(p820)

# ---- Panel 30: TAA por marca ----
panels.append(row(400, "¿Qué marca se desvía? · TAA por Bandera (consistente con gráfico)", 60))
COLS30 = ("CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,"
          "TRIM(a.codrespuestaautorizacion) AS cr,TRIM(a.codtipomensajeautorizacion) AS cmsg,"
          "CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE NULL END AS marca,"
          "NULLIF(TRIM(a.codrespuestaiss),'') AS ciss,NULLIF(TRIM(a.nrorechazointerno),'') AS nri,"
          "CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END AS marca_f,"
          "TRIM(a.codbancoemisor) AS cbe_f," + ciss_norm_expr + " AS ciss_norm")
b30 = ("WITH b AS (SELECT " + COLS30 + " FROM payway_poststage_prod_db.ctx_autorizaciones_eps a "
       "WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d') "
       "AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') AND " + CUITA + " AND " + ESTBIN + "),\n" + FILT)
def taamarca(m):
    return ("ROUND(100.0*SUM(CASE WHEN marca='%s' AND cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN marca='%s' THEN 1 ELSE 0 END),0),2) AS \"%s\"" % (m, m, m))
tail30 = (",\nf AS (SELECT * FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND marca IS NOT NULL)\n"
          "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time,"
          + ",".join(taamarca(m) for m in ["Visa", "Mastercard", "Cabal", "Amex", "UPI"]) + " FROM f GROUP BY 1 ORDER BY 1;")
fc30 = ts_fc([ovr("Visa", [CLR("#1a56db")]), ovr("Mastercard", [CLR("#eb4034")]), ovr("Cabal", [CLR("#a78bfa")]),
              ovr("Amex", [CLR("#34d399")]), ovr("UPI", [CLR("#fbbf24")])], unit="percent", decimals=1, soft=(70, 100), fill=0)
fc30["defaults"]["custom"]["lineWidth"] = 2
fc30["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p30 = ts_panel(30, 0, 61, 24, 8, "TAA por Marca · 15m", fc30, [T(b30 + tail30)])
p30["options"]["legend"]["calcs"] = ["lastNotNull", "mean"]; p30["options"]["legend"]["displayMode"] = "list"
panels.append(p30)

# ---- Paneles por marca/producto ----
panels.append(row(420, "Detalle por marca · TAA + Aprobadas + Denegadas (eje secundario)", 69))
panels.append(marca_panel(911, 0, 70, 12, "Visa · TAA + Aprob/Deneg · 15m", "TAA Visa", ["VD","VK","V6","VB","VN","SR","VC"]))
panels.append(marca_panel(101, 12, 70, 12, "Mastercard · TAA + Aprob/Deneg · 15m", "TAA Mastercard", ["MD","MC","MS","MK"]))
panels.append(marca_panel(102, 0, 77, 12, "Cabal · TAA + Aprob/Deneg · 15m", "TAA Cabal", ["PA"]))
panels.append(marca_panel(103, 12, 77, 12, "Amex · TAA + Aprob/Deneg · 15m", "TAA Amex", ["AX"]))
panels.append(marca_panel(104, 0, 84, 12, "UPI · TAA + Aprob/Deneg · 15m", "TAA UPI", ["UC"]))
panels.append(row(430, "Detalle por producto · Crédito/Débito/Prepago (clasificación por INSTRMTYP)", 91))
panels.append(marca_panel(110, 0, 92, 8, "Crédito · TAA + Aprob/Deneg · 15m", "TAA Crédito", ["VC","MC","AX","VB","MS","AC"]))
panels.append(marca_panel(111, 8, 92, 8, "Débito · TAA + Aprob/Deneg · 15m", "TAA Débito", ["VD","MD","PA","SD","BD","VN"]))
panels.append(marca_panel(112, 16, 92, 8, "Prepago · TAA + Aprob/Deneg · 15m", "TAA Prepago", ["VK","MK","PK","UC","SR","V6"]))

# ---- Lista de bancos (codigo, nombre) — una sola vez ----
BANCOS = [
 ("1","BANKBOSTON N.A."),("5","ABN AMRO BANK"),("6","BANCO PATAGONIA S.A."),("7","BANCO GALICIA"),("8","BANCO SHAW"),
 ("9","REBA COMPAÑIA FINANCIERA S.A. VISA DEBIT"),("10","BANCO PATAGONIA S.A."),("12","BANCO DE CREDITO ARGENTINO"),
 ("13","BANCO POPULAR ARGENTINO"),("14","BANCO DE LA PROVINCIA DE BS.AS."),("15","ICBC"),("16","CITIBANK"),
 ("17","BANCO BBVA ARGENTINA S.A."),("20","BANCO DE CORDOBA"),("27","BANCO SUPERVIELLE S.A."),("29","BANCO CIUDAD DE BUENOS AIRES"),
 ("34","BANCO PATAGONIA S.A."),("39","BANCO CAJA"),("43","BANCO COMAFI"),("44","BANCO HIPOTECARIO"),("45","BANCO DE SAN JUAN"),
 ("46","BANCO DO BRASIL"),("50","BANCO TORNQUIST"),("54","BANCO BISEL S.A."),("56","BANCO CREDITO PROVINCIAL S A"),
 ("59","NUEVO BANCO DE ENTRE RIOS S.A."),("60","BANCO DEL TUCUMAN S.A."),("61","BANCO DE MENDOZA S.A."),
 ("62","BANCO ISRAELITA DE CORDOBA"),("64","BANCO MONSERRAT"),("65","BANCO MUNICIPAL DE ROSARIO"),
 ("66","BANCO MUNICIPAL DE ROSARIO/CONSUMAX"),("67","BANCO MACRO S.A."),("71","BANCO DE SANTA FE S.A."),
 ("72","BANCO SANTANDER RIO S A"),("75","BANCO DE PREVISION SOCIAL"),("77","CIA FINANCIERA ARGENTINA S.A."),
 ("79","BANCO REGIONAL DE CUYO S A"),("81","BANCO SOCIAL DE CBA."),("83","BANCO DEL CHUBUT S.A."),("84","BANCO PATAGONIA S.A."),
 ("86","BANCO SANTA CRUZ"),("87","BANCO SGO DEL ESTERO S.A."),("89","BANCO MACRO S.A."),("92","BANCO DE CATAMARCA"),
 ("93","BANCO DE LA PAMPA"),("94","BANCO DE CORRIENTES S.A."),("97","BANCO PROVINCIA DE NEUQUEN"),("98","BANCO DE MISIONES S.A."),
 ("105","BANCO COMERCIAL DEL TANDIL S A"),("107","BANCO COM DE TRES ARROYOS"),("109","REBA COMPAÑIA FINANCIERA S.A. MASTERCAR"),
 ("110","BANCO DE CORONEL DORREGO S A"),("112","BANCO DE JUNIN S A"),("113","BANCO DE LA EDIFICADORA DE OLAVARRIA"),
 ("114","BAPRO PURCHASING RECARGABLE"),("115","BANCO DE OLAVARRIA"),("117","BANCO BBVA ARGENTINA S.A. MASTERCARD"),
 ("120","BANCO CORDOBA AGRO PURCHASING"),("121","COMAFI PROVENCRED"),("128","NUEVO BANCO DE AZUL"),
 ("130","BANCO POPULAR FINANCIERO S A"),("133","TRANSANDINO"),("134","BANCO PATAGONIA MASTERCARD"),
 ("135","BANCO UNION COMERCIAL E INDUSTRIAL C"),("137","BANCO MACRO S.A."),("140","BANCO FEDERAL ARGENTINO"),
 ("143","BRUBANK S.A.U."),("145","BANCO SAN JUAN MASTERCARD"),("147","BIBANK S.A."),("148","BANCO MUNICIPAL DE LA PLATA"),
 ("150","GALICIA MAS"),("151","ACISO BANCO"),("156","BANCO BICA SA"),("158","OPEN BANK ARGENTINA S.A."),("161","BANCO PLATENSE"),
 ("162","BANCO MAYO COOP LTDO"),("167","BANCO REAL DE ARGENTINA S.A."),("172","BANCO SANTANDER RIO - MASTER CARD"),
 ("173","BANCO DE CORRIENTES"),("175","BANCO SUDECOR LITORAL S.A."),("178","BANCO DE BALCARCE COOP LTDA"),
 ("179","BANCO ALMAFUERTE COOP LTDO"),("180","BANCO MACRO S.A. MASTERCARD"),("185","BANCO DEL TUCUMAN S.A. MASTERCARD"),
 ("186","BANCO SANTA CRUZ MASTERCARD"),("191","BANCO CREDICOOP"),("198","BANCO DE VALORES"),("199","BANCO COMAFI MASTER"),
 ("200","MONEDERO BASE"),("202","MONEDERO EMISION"),("203","BANCO CREDICOOP CL - CABAL"),("204","NUEVO BANCO DEL CHACO - CABAL"),
 ("205","CAJA POPULAR DE TUCUMAN - CABAL"),("206","TARJETA PLATA - CABAL"),("207","BANCO GALICIA MONEDERO"),("208","CARTASUR - CABAL"),
 ("209","CLIPER - CABAL"),("210","CANCILLER - CABAL"),("211","FAVACARD - CABAL"),("212","BAPRO MONEDERO"),
 ("213","CREDICLUB SAN JORGE - CABAL"),("214","BAPRO BUSINESS RECARGABLE"),("215","ICBC MONEDERO"),("217","BBVA MONEDERO"),
 ("218","UNICA - CABAL"),("219","CREDICOOP MONEDERO"),("220","BANCO DE CORDOBA MASTERCARD"),("221","LIDERAR CARD - CABAL"),
 ("222","UNION SUNCHALES - CABAL"),("223","ASOC. MUTUAL VENADO TUERTO - CABAL"),("224","CREDI PAZ - CABAL"),("225","GRAMIT S.A."),
 ("226","MUTUAL DE LOS ARROYOS - CABAL"),("227","ASOC MUTAL CLUB ATETICO PILAR - CABAL"),("228","MUTUAL DE LAS COMUNIDADES - CABAL"),
 ("229","BANCO ITAU BUEN AYRE S.A."),("230","ARECOCARD - CABAL"),("231","AMR - CABAL"),("232","LETæS BIT"),("233","LINKINGPOS"),
 ("234","BANCO LOCAL COOP LTDO"),("235","BANCO CREDICOOP - COOPESUR"),("236","PROPAGO"),("237","XPERIENCE"),
 ("238","SG FINANCIAL TECHNOLOGY"),("240","HSBC BANK MONEDERO"),("241","BANCO DE FORMOSA PREPAID"),("242","POLLUX"),
 ("243","CARREFOUR MASTER PREPAID"),("244","PAGO24"),("245","BANCO CIUDAD - CABAL"),("246","BANCO DE FORMOSA - CABAL"),
 ("247","MUTUAL ALMAFUERTE - CABAL"),("248","AL2"),("249","BANCO PATRICIOS C L"),("250","NUBI"),("251","BANCO FEIGIN S A"),
 ("252","SIDECREER - CABAL"),("253","SILOHUD ARGENTINA S.A.S"),("255","BANCO MACRO S.A."),("256","CREDINOS COOP DE CREDITO LTDA"),
 ("257","COMAFI DIGITAL"),("258","PAGOS DIGITALES"),("259","BANCO BMA S.A.U."),("261","SOCIEDAD MILITAR SEGURO DE VIDA"),
 ("263","E-COBROS"),("264","FRAVEGA S.A.C.I.E.I."),("265","GALICIA MAS"),("266","BNP PARIBAS"),("267","BANCO MACRO MONEDERO"),
 ("268","BANCO DE TIERRA DEL FUEGO"),("269","BANCO DE LA REP ORIENTAL DEL URUGUAY S.A"),("270","BANCO SANTANDER RIO MONEDERO"),
 ("271","BANCO VELOX"),("272","PAGO24 EXT"),("273","BANCO LOS TILOS S A"),("277","BANCO SAENZ S.A."),("281","PROVIDIAN BANK S.A."),
 ("285","BANCO MACRO S.A."),("290","BANCO CAUDAL"),("292","CABAL PIP"),("293","BANCO MERCURIO"),("296","BANCO BASEL"),
 ("297","BANCO SUPERVIELLE S.A."),("299","BANCO COMAFI"),("301","BANCO PIANO"),("303","BANCO FINANSUR"),("306","BANCO MACRO S.A."),
 ("309","NUEVO BANCO DE LA RIOJA"),("310","BANCO DEL SOL S.A."),("311","NUEVO BANCO DEL CHACO S.A."),("312","NB DEL CHACO ALIMENTOS"),
 ("313","BANCO CREDICOOP - ARGENCOOP"),("314","BANCO MACRO S.A."),("315","BANCO DE FORMOSA S.A"),("316","CITIBANK"),("319","BANCO CMF"),
 ("322","BANCO INDUSTRIAL"),("323","CORP BANCA"),("324","BANCO DE JUJUY"),("326","MERCOBANK"),("328","BANCO MENDOZA S.A."),
 ("330","NUEVO BANCO DE SANTA FE"),("331","BANCO CETELEM"),("332","BSF CARREFOUR"),("341","BANCO MAS VENTAS S A"),("350","DISCOVER"),
 ("359","BANCO ENTRE RIOS MASTERCARD"),("381","COMAFI EX-PROVIDIAN"),("384","UALA S.A"),("386","NUEVO BANCO DE ENTRE RIOS S.A.II"),
 ("389","COLUMBIA S.A."),("391","CABAL"),("399","BANCO COMAFI PROVEN"),("405","BST"),("406","CREDILOGROS DEBITO"),
 ("410","GARBARINO (BANCO ROBERTS S.A.)"),("411","BANCO DE LA NACION ARGENTINA-AGRONACION"),("412","BAPRO"),("413","PACTAR"),
 ("414","PROCAMPO"),("415","GALICIA RURAL"),("416","NATIVA NACION"),("417","PYMENACION"),("418","BAPRO DESARROLLO SOCIAL"),
 ("419","TARJETA TURISTICA MUNICIPIOS"),("420","TARJETA COMFIAR S.A."),("421","PROVENCRED"),("422","TARJETA VISA ALIMENTAR"),
 ("425","TARJETA NARANJA S.A"),("426","BANCO BICA"),("430","MARA"),("431","BANCO COINAG S.A."),("435","GALICIA AHORA"),
 ("438","CORDIAL IUDU"),("440","RED AUSTRAL"),("448","BANCO DINO S.A."),("450","BANCO GALICIA NARANJA"),("451","MIS MONEDAS CARD"),
 ("452","GIFT CARD"),("453","TARJETA DEMOS"),("454","TARJETA 10"),("455","BANCO GALICIA NEVADA"),("456","WISH GIFT"),
 ("457","ALTO PALERMO S.A (APSA)"),("458","ARVENZIS S.A."),("459","MIN. DES. SOCIAL CORRIENTES SAPUCAY"),
 ("460","BANCO COOPESUR (CABAL ELECTRON)"),("461","PROA"),("462","SOLO DEPORTES"),("463","MAKRO"),("464","BAPRO I.O.M.A"),
 ("467","BANCO DEL TUCUMAN II"),("468","BANCO FUEGUINA"),("469","BTF TARJETA SOCIAL"),("470","BANCO ARGENCOOP (CABAL ELECTRON)"),
 ("472","SANTANDER RIO - AMERICAN EXPRESS"),("480","DISCOCARD"),("481","COMAFI FIDUCIARIO EX-PROVIDIAN"),
 ("488","BANCO CAJA POPULAR DE TUCUMAN"),("490","MARCAS PRIVADAS SERVICIOS FINANCIEROS"),("491","D.S.F."),("494","AGRO CORRIENTES"),
 ("498","MENDOZA PLAZA SHOPPING"),("499","TARJETAS DEL MAR"),("509","Sin descripcion"),("511","BANCO DE LA NACION ARGENTINA"),
 ("512","TICKET NACION"),("513","Sin descripcion"),("515","TARSHOP S.A."),("516","CITIBANK"),("518","Sin descripcion"),
 ("519","Sin descripcion"),("520","Sin descripcion"),("530","BANCO SANTA FE MASTERCARD"),("532","BSF CARREFOUR MASTERCARD"),
 ("534","BP PROGRAMAS REGIONALES"),("541","COPRES"),("543","BANCO COMAFI"),("550","PROA - PRESTAMOS"),("567","MACRO RECARGABLE GOB"),
 ("590","TAF - COTO"),("591","FINANCIAL SERVICES"),("598","COBRO ANTICIPADO"),("599","VISA ARGENTINA"),("600","LINK"),
 ("606","BANCO MACRO S.A."),("607","AMEX BANCO GALICIA"),("609","REBA COMPAÑIA FINANCIERA S.A."),("614","BAPRO AMEX"),
 ("616","CITIBANK COBE"),("634","AMEX BANCO PATAGONIA S.A."),("640","BANCO GALICIA NARANJA AMEX"),("643","SCOTIABANK QUILMES - AMEX"),
 ("650","GALICIA MAS"),("653","FIDEICOMISO BANSUD - AMEX II"),("667","BANCO MACRO S.A."),("685","BANCO MACRO S.A."),
 ("698","LATIN PAY S.A."),("700","BANELCO"),("701","BCO PROVINCIA - ESPECIAL PLANES CUOTAS"),("702","CREDINOS COOP. LTDA. VISA"),
 ("714","Sin descripcion"),("715","BANCO PIANO"),("798","Sin descripcion"),("800","EXTERBANCA CASA BANCARIA"),
 ("810","BANCO DEL SOL DIGITAL"),("814","BAPRO-URUGUAY"),("840","BANCO DE GALICIA (CAYMAN) LTD."),("850","INTERCONTINENTAL BANK"),
 ("875","TRADE & COMMERCE BANK"),("885","Sin descripcion"),("900","VISA ARGENTINA"),("902","PAYWAY S.A.U"),("914","PAYWAY S.A.U."),
 ("917","CREDILOGROS (917)"),("920","VISA ARGENTINA"),("998","Sin Descripcion"),("999","VISA INTERNACIONAL"),
]
print("BANCOS:", len(BANCOS))

# ---- Panel 120: Top 10 bancos ----
panels.append(row(720, "Detalle por banco emisor · Top 10 por rechazos", 99))
VALUES_120 = ",".join("('%s','%s')" % (c, n) for c, n in BANCOS)
tail120 = (",\ndenegados AS (SELECT ts, cbe_f FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND cbe_f IS NOT NULL AND cbe_f<>''),\n"
           "top10 AS (SELECT cbe_f, COUNT(*) AS n FROM denegados GROUP BY cbe_f ORDER BY n DESC LIMIT 10),\n"
           "bancos AS (SELECT cod_raw,nombre FROM (VALUES " + VALUES_120 + ") AS bancos(cod_raw,nombre)),\n"
           "labeled AS (SELECT d.ts, COALESCE(bk.nombre,d.cbe_f)||' ('||d.cbe_f||')' AS banco FROM denegados d JOIN top10 t ON d.cbe_f=t.cbe_f LEFT JOIN bancos bk ON TRY_CAST(d.cbe_f AS INTEGER)=TRY_CAST(bk.cod_raw AS INTEGER))\n"
           "SELECT date_trunc('minute',ts)-(EXTRACT(MINUTE FROM ts)%15)*INTERVAL '1' MINUTE AS time, banco AS metric, COUNT(*) AS rechazos FROM labeled GROUP BY 1,2 ORDER BY 1;")
fc120 = ts_fc([], unit="short", fill=8); fc120["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}]
fc120["defaults"]["links"] = [LINK]
p120 = {"datasource": DS, "fieldConfig": fc120, "gridPos": {"h": 9, "w": 24, "x": 0, "y": 100}, "id": 120,
        "options": {"legend": {"calcs": ["sum", "max"], "displayMode": "table", "placement": "right", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [T(base_b(False) + tail120, "A", 0)],
        "transformations": [{"id": "partitionByValues", "options": {"fields": ["metric"], "keepFields": False, "naming": {"asLabels": True}}}],
        "title": "Top 10 Bancos Emisores · Rechazos por banco · 15m", "type": "timeseries"}
panels.append(p120)

# ---- Panel 38: Top 10 BINes ----
panels.append(row(710, "Top 10 BINes por Rechazos · evolución temporal (prueba BIN)", 109))
b38 = ("WITH b AS (SELECT " + COLS + ",substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8) AS bin8 "
       "FROM payway_poststage_prod_db.ctx_autorizaciones_eps a "
       "WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d') "
       "AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') AND " + CUITA + " AND " + ESTBIN + "),\n" + FILT)
tail38 = (",\ndenegados AS (SELECT ts, bin8 FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo() AND cmsg<>'1420' AND cr NOT IN('0000','0400','0900') AND bin8 IS NOT NULL AND bin8<>''),\n"
          "top10 AS (SELECT bin8, COUNT(*) AS n FROM denegados GROUP BY bin8 ORDER BY n DESC LIMIT 10)\n"
          "SELECT date_trunc('minute',d.ts)-(EXTRACT(MINUTE FROM d.ts)%15)*INTERVAL '1' MINUTE AS time,d.bin8 AS metric,COUNT(*) AS rechazos FROM denegados d JOIN top10 t ON d.bin8=t.bin8 GROUP BY 1,2 ORDER BY 1;")
fc38 = ts_fc([], unit="short", fill=8); fc38["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
p38 = {"datasource": DS, "fieldConfig": fc38, "gridPos": {"h": 8, "w": 24, "x": 0, "y": 110}, "id": 38,
       "options": {"legend": {"calcs": ["sum", "max"], "displayMode": "table", "placement": "right", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}},
       "targets": [T(b38 + tail38, "A", 0)],
       "transformations": [{"id": "partitionByValues", "options": {"fields": ["metric"], "keepFields": False, "naming": {"asLabels": True}}}],
       "title": "Top 10 BINes (8 díg.) por Rechazos · evolución temporal · 15m", "type": "timeseries"}
panels.append(p38)

# ---- Panel 900: CUITs afectados ----
panels.append(row(910, "¿A qué CUITs impacta el desvío?", 118))
FILT_BODY = ("1=1 AND ('${marca:text}'='All' OR marca_f IN (${marca:sqlstring})) "
             "AND ('${banco:text}'='All' OR CAST(TRY_CAST(cbe_f AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) "
             "AND ('${motivo_emisor:text}'='All' OR ciss_norm IN (${motivo_emisor:sqlstring})) "
             "AND ('${motivo_adqte:text}'='All' OR nri IN (${motivo_adqte:sqlstring}))")
cols900 = ("CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR AS ts,"
           "TRIM(a.nroestablecimiento) AS nroest,TRIM(a.codrespuestaautorizacion) AS cr,TRIM(a.codtipomensajeautorizacion) AS cmsg,"
           "NULLIF(TRIM(a.codrespuestaiss),'') AS ciss,NULLIF(TRIM(a.nrorechazointerno),'') AS nri,"
           "CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END AS marca_f,"
           "TRIM(a.codbancoemisor) AS cbe_f," + ciss_norm_expr + " AS ciss_norm")
ctx = "payway_poststage_prod_db.ctx_autorizaciones_eps a"
b900 = ("b AS (SELECT " + cols900 + " FROM " + ctx + " WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d') AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') AND " + CUITB + " AND " + ESTBIN + ")")
bb900 = ("bb AS (SELECT " + cols900 + " FROM " + ctx + " WHERE a.fecha BETWEEN date_format(date_add('hour',-3,date_add('day',-1,$__timeFrom())),'%Y-%m-%d') AND date_format(date_add('hour',-3,date_add('day',-1,$__timeTo())),'%Y-%m-%d') AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30') AND " + CUITB + " AND " + ESTBIN + ")")
sql900 = ("WITH \nest AS (SELECT nro_establecimiento, MAX(nombre_fantasia) AS nf, MAX(cuit_establecimiento_host) AS cuit FROM prisma_ab_analytics_prod_db.establecimiento GROUP BY nro_establecimiento),\n"
          + b900 + ",\nfilt AS (SELECT * FROM b WHERE " + FILT_BODY + "),\n"
          "act AS (\n  SELECT e.cuit AS cuit, MAX(e.nf) AS nombre,\n    COUNT(*) AS total,\n    SUM(CASE WHEN f.cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS aprob,\n    SUM(CASE WHEN f.cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS deneg\n  FROM filt f JOIN est e ON TRY_CAST(f.nroest AS INTEGER)=e.nro_establecimiento\n  WHERE f.ts>=$__timeFrom() AND f.ts<$__timeTo() AND f.cmsg<>'1420'\n  GROUP BY e.cuit\n),\n"
          + bb900 + ",\nfb AS (SELECT * FROM bb WHERE " + FILT_BODY + "),\n"
          "base AS (\n  SELECT e.cuit AS cuit,\n    ROUND(100.0*SUM(CASE WHEN f.cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS taa_base\n  FROM fb f JOIN est e ON TRY_CAST(f.nroest AS INTEGER)=e.nro_establecimiento\n  WHERE f.ts>=date_add('day',-1,$__timeFrom()) AND f.ts<date_add('day',-1,$__timeTo()) AND f.cmsg<>'1420'\n  GROUP BY e.cuit\n)\n"
          "SELECT \n  act.cuit AS \"CUIT\",\n  COALESCE(act.nombre, act.cuit) AS \"Nombre\",\n  act.total AS \"Total TRX\",\n  act.deneg AS \"Denegadas\",\n  ROUND(100.0*act.aprob/NULLIF(act.total,0),2) AS \"TAA %\",\n  base.taa_base AS \"Baseline D-1 %\",\n  ROUND(ROUND(100.0*act.aprob/NULLIF(act.total,0),2) - base.taa_base, 1) AS \"Δpp\"\nFROM act LEFT JOIN base ON act.cuit=base.cuit\nWHERE act.cuit IS NOT NULL\n"
          "  AND COALESCE(TRY_CAST(REPLACE(act.cuit,'-','') AS BIGINT),-1) NOT IN (" + CUITS_EXCLUIDOS + ")\n"
          "ORDER BY act.deneg DESC\nLIMIT 50;")
fc900 = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}, "mappings": [],
         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
         "overrides": [
            ovr("TAA %", [{"id": "unit", "value": "percent"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "green", "value": 85}]}}]),
            ovr("Δpp", [{"id": "unit", "value": "short"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -5}, {"color": "green", "value": 0}]}}]),
            ovr("Denegadas", [{"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}}, {"id": "color", "value": {"mode": "continuous-reds"}}]),
            ovr("CUIT", [{"id": "custom.width", "value": 150}])
         ]}
p900 = {"datasource": DS, "fieldConfig": fc900, "gridPos": {"h": 11, "w": 24, "x": 0, "y": 119}, "id": 900,
        "options": {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                    "showHeader": True, "sortBy": [{"desc": True, "displayName": "Denegadas"}]},
        "pluginVersion": "10.4.7", "targets": [T(sql900)],
        "title": "CUITs afectados · ranking por denegaciones (respeta todos los filtros) · Top 50", "type": "table"}
panels.append(p900)
print("paneles construidos:", len(panels))

# ---------------- TEMPLATING ----------------
def textbox(name, label, desc):
    return {"current": {"selected": False, "text": "", "value": ""}, "description": desc, "hide": 0,
            "label": label, "name": name, "options": [], "query": "", "skipUrlSync": False, "type": "textbox"}

def custom_multi(name, label, desc, pairs, query):
    opts = [{"selected": True, "text": "All", "value": "$__all"}]
    for t, v in pairs:
        opts.append({"selected": False, "text": t, "value": v})
    return {"current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "description": desc, "hide": 0,
            "includeAll": True, "label": label, "multi": True, "name": name, "options": opts, "query": query,
            "skipUrlSync": False, "type": "custom"}

MARCA = [("Visa","Visa"),("Mastercard","Mastercard"),("Cabal","Cabal"),("Amex","Amex"),("UPI","UPI"),("Otra","Otra")]
MOT_EMI = [("00","Aprobado"),("01","Llame emisor"),("02","Consulte emisor"),("03","Estab. invalido"),("04","Capturar tarjeta"),
 ("05","Denegada (DNH)"),("06","Error"),("07","Recoger (fraude)"),("08","Honrar con identif."),("10","Aprob. parcial"),
 ("12","Trx invalida"),("13","Monto invalido"),("14","Tarj. invalida"),("15","Emisor invalido"),("19","Vuelva a ingresar"),
 ("21","Ninguna accion"),("30","Error formato"),("39","Sin cuenta credito"),("41","Tarj. perdida"),("43","Tarj. robada"),
 ("46","Cuenta cerrada"),("51","Fondos insuf."),("52","Sin cta corriente"),("53","Sin cta ahorros"),("54","Tarj. expirada"),
 ("55","PIN incorrecto"),("57","Trx no permitida"),("58","No permitida terminal"),("59","Fraude sospecha"),("61","Excede limite"),
 ("62","Tarj. restringida"),("63","Viol. seguridad"),("64","No cumple ALD"),("65","Excede freq."),("70","Datos PIN requeridos"),
 ("71","PIN no cambiado"),("75","Excede intentos PIN"),("76","Reversion no solicit."),("77","From Account invalido"),
 ("78","Cuenta invalida"),("79","Ciclo vida/Reversada"),("80","Sin impacto"),("81","Error cripto PIN"),("82","CAM/CVV negativo"),
 ("83","Fraude MC"),("84","Ciclo autoriz. inv."),("85","No rechazado"),("86","No verifica PIN"),("88","Fallo cripto"),
 ("89","PIN inaceptable"),("91","Emisor no disp."),("92","No enrutar"),("93","Violacion ley"),("94","Trx duplicada"),
 ("96","Error sistema"),("1A","Autenticacion adic."),("6P","Verificacion fallida"),("B1","Recargo no permitido"),
 ("N3","Servicio caja no disp."),("N4","Excede limite efectivo"),("N7","Falla CVV2"),("N8","Excede monto preaut."),
 ("P5","Desbloqueo PIN deneg."),("P6","Cambio PIN denegado"),("Q1","Autenticacion fallida"),("R0","Suspension de pago"),
 ("R1","Revocacion autoriz."),("R2","No califica Visa PIN"),("R3","Detener comerciantes"),("Z5","Cuenta valida monto no"),
 ("Z6","Uso invalido MCC")]
MOT_ADQ = ["TA001","TA002","TA003","TA004","TA005","TA006","TA007","TA008","TA009","TCADQ","LMT01","LMT02","LIM01","LIM02",
           "CAT01","CAT23","MAC02","MAC03","MAC27","DERP1","DERP2"]

v_cuit = textbox("cuit", "CUIT", "CUIT (vacío = todos)")
v_est = textbox("establecimiento", "Establecimiento", "Filtrar por nro_establecimiento específico (vacío = todos los del CUIT seleccionado)")
v_bin = {"current": {"text": "", "value": ""}, "hide": 0, "label": "BIN (8 díg.)", "name": "bin",
         "options": [{"selected": True, "text": "", "value": ""}], "query": "", "skipUrlSync": False, "type": "textbox"}
v_marca = custom_multi("marca", "Marca", "Filtrar por marca", MARCA, "Visa,Mastercard,Cabal,Amex,UPI,Otra")
v_banco = custom_multi("banco", "Banco Emisor", "Código del banco emisor",
                       [("%s - %s" % (c, n), c) for c, n in BANCOS],
                       ",".join("%s - %s : %s" % (c, n, c) for c, n in BANCOS))
v_memi = custom_multi("motivo_emisor", "Motivo Emisor", "Motivo del emisor (2 dígitos)",
                      [("%s - %s" % (c, d), c) for c, d in MOT_EMI],
                      ",".join("%s - %s : %s" % (c, d, c) for c, d in MOT_EMI))
v_madq = custom_multi("motivo_adqte", "Motivo Adqte", "Motivo adquirente (NRI)",
                      [(c, c) for c in MOT_ADQ], ",".join(MOT_ADQ))

dash = {
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
    "description": "Template + CUITs afectados + drill-down. FIX filtro banco (padding 3 díg) + filtro BIN nuevo. OPTIMIZADO: refresh 1m, CUIT semi-join, filtros 800/801.",
    "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 2, "id": 294, "links": [], "liveNow": False,
    "panels": panels, "refresh": "1m", "schemaVersion": 39,
    "tags": ["payway", "noc", "desvios", "cuit", "establecimientos", "template"],
    "templating": {"list": [v_cuit, v_est, v_bin, v_marca, v_banco, v_memi, v_madq]},
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"], "time_options": ["1h", "3h", "6h", "12h", "24h"]},
    "timezone": "browser",
    "title": "Desvíos · CUIT/Establecimiento · Versión Establecimientos + Drill-down CUITs",
    "uid": "desvios", "version": 1, "weekStart": ""
}

with open("desvios_optimizado.json", "w", encoding="utf-8") as fh:
    json.dump(dash, fh, ensure_ascii=False, indent=2)
print("OK -> desvios_optimizado.json | paneles:", len(panels))
