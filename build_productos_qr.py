# -*- coding: utf-8 -*-
"""
Genera dashboard NUEVO "Payway · Producto / QR-Billeteras / Modalidades":
  1. Tipo de Producto (clasificacion nueva con PB=Marcas Privadas, PN=Naranja Pura,
     PA debito por BIN 604201/65008700)
  2. QR y Billeteras (fill9: pos1='Q' es QR, pos2 = wallet id mapeado)
  3. Modalidades (DA / E-commerce / Presente segun coddebitoautomatico y
     codidentpresenciacliente)
Hereda de la v2.0: exclusion de 8 CUITs (anti-join), refresh 1m, Athena result
reuse 5 min, buckets de 15 minutos, macros $__timeFrom/$__timeTo con poda de
particion `fecha`.
Salida: dashboard_producto_qr_modalidades.json
"""
import json

DS = {"type": "grafana-athena-datasource", "uid": "bex5w10g549vkd"}
CONN = {"catalog": "AwsDataCatalog", "database": "payway_poststage_prod_db", "region": "us-east-1",
        "resultReuseEnabled": True, "resultReuseMaxAgeInMinutes": 5}

CUITS_EXCLUIDOS = "30712334610,30715313681,30717791904,30718438906,30716325586,30691399768,33714595569,30709228419"
EXCL = (" AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR "
        "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN "
        "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
        "WHERE e.nro_establecimiento IS NOT NULL AND "
        "TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN (" + CUITS_EXCLUIDOS + ")))")

TSEXPR = "CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR"
BK = ("date_trunc('minute'," + TSEXPR + ")-(EXTRACT(MINUTE FROM " + TSEXPR + ")%15)*INTERVAL '1' MINUTE")
FECHA_RANGO = ("a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') "
               "AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')")
TS_RANGO = "(" + TSEXPR + ")>=$__timeFrom() AND (" + TSEXPR + ")<$__timeTo()"

# --- Clasificacion Tipo de Producto (exacta a la definicion del usuario) ---
TIPO_PRODUCTO = ("CASE \n"
    "        WHEN a.INSTRMTYP IN ('VD', 'MD', 'MS') THEN 'Débito'\n"
    "        WHEN a.INSTRMTYP = 'PA' AND (SUBSTR(TRIM(a.nrotarjeta), 1, 6) = '604201' OR SUBSTR(TRIM(a.nrotarjeta), 1, 8) = '65008700') THEN 'Débito'\n"
    "        WHEN a.INSTRMTYP IN ('V6', 'VB', 'VN', 'VC', 'MC', 'AX') THEN 'Crédito'\n"
    "        WHEN a.INSTRMTYP = 'PA' THEN 'Crédito' \n"
    "        WHEN a.INSTRMTYP IN ('VK', 'SR', 'MK') THEN 'Prepaga'\n"
    "        WHEN a.INSTRMTYP = 'PB' THEN 'Marcas Privadas'\n"
    "        WHEN a.INSTRMTYP = 'PN' THEN 'Naranja Pura'\n"
    "        WHEN a.INSTRMTYP = 'UC' THEN 'Indeterminado (Crédito/Débito)'\n"
    "        ELSE 'Otro'\n"
    "    END")
INSTR_LIST = "'VD','VK','V6','VB','VN','SR','VC','MD','MC','MS','MK','AX','PA','UC','PB','PN'"

# --- Mapeo billeteras (exacto a la definicion del usuario) ---
WALLET_MAP = ("CASE SUBSTRING(a.fill9, 2, 1)\n"
    "        WHEN '1' THEN 'Tp'\n"
    "        WHEN '2' THEN 'Macro'\n"
    "        WHEN '3' THEN 'Modo (MODO)'\n"
    "        WHEN '4' THEN 'Bimo'\n"
    "        WHEN '5' THEN 'Pagochat'\n"
    "        WHEN '6' THEN 'Bapro_cuentaDNI (CDNI)'\n"
    "        WHEN '7' THEN 'Bna'\n"
    "        WHEN '8' THEN 'Bsf'\n"
    "        WHEN '9' THEN 'Cenco'\n"
    "        WHEN 'M' THEN 'Mercado Pago (MEPA)'\n"
    "        WHEN 'A' THEN 'Plus Pagos (PLUS)'\n"
    "        WHEN 'B' THEN 'Nubi (NUBI)'\n"
    "        WHEN 'C' THEN 'Naranja X (NARX)'\n"
    "        WHEN 'D' THEN 'Persona Pay (PEPA)'\n"
    "        WHEN 'Y' THEN 'Yacaré (YACA)'\n"
    "        ELSE 'NO MAPEADO / DESCONOCIDO'\n"
    "    END")

# --- Modalidad (exacta a la definicion del usuario) ---
MODALIDAD = ("CASE \n"
    "        WHEN a.coddebitoautomatico = '1' THEN 'Débito Automático'\n"
    "        WHEN a.codidentpresenciacliente = '8' THEN 'E-commerce'\n"
    "        WHEN a.codidentpresenciacliente <> '8' THEN 'Presente'\n"
    "        ELSE 'Indeterminado'\n"
    "    END")

APROB = "TRIM(a.codrespuestaautorizacion) IN('0000','0400','0900')"
DENEG = "TRIM(a.codrespuestaautorizacion) NOT IN('0000','0400','0900')"
FILTROS_STD = ("AND TRIM(a.codprocesoautorizacion1) NOT IN ('92','30')\n"
               "    AND TRIM(a.codtipomensajeautorizacion) <> '1420'")


def T(sql, refId="A", fmt=1):
    return {"connectionArgs": CONN, "datasource": DS, "format": fmt, "rawSQL": sql, "refId": refId}


def row(rid, title, y):
    return {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": rid, "title": title, "type": "row"}


PARTITION_TX = [{"id": "partitionByValues", "options": {"fields": ["metric"], "keepFields": False, "naming": {"asLabels": True}}}]


def ts_panel(pid, x, y, w, h, title, unit, targets, decimals=None, soft=None, legend_calcs=None, table_legend=False, partition=True):
    custom = {"axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text", "axisLabel": "",
              "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "line", "fillOpacity": 8,
              "gradientMode": "none", "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "insertNulls": False, "lineInterpolation": "smooth", "lineWidth": 2, "pointSize": 5,
              "scaleDistribution": {"type": "linear"}, "showPoints": "never", "spanNulls": True,
              "stacking": {"group": "A", "mode": "none"}, "thresholdsStyle": {"mode": "off"}}
    if soft:
        custom["axisSoftMin"], custom["axisSoftMax"] = soft
        custom["fillOpacity"] = 0
    d = {"color": {"mode": "palette-classic"}, "custom": custom, "mappings": [],
         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}, "unit": unit}
    if decimals is not None:
        d["decimals"] = decimals
    p = {"datasource": DS, "fieldConfig": {"defaults": d, "overrides": []},
         "gridPos": {"h": h, "w": w, "x": x, "y": y}, "id": pid,
         "options": {"legend": {"calcs": legend_calcs or ["mean", "min"],
                                "displayMode": "table" if table_legend else "list",
                                "placement": "right" if table_legend else "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "targets": targets, "title": title, "type": "timeseries"}
    if partition:
        p["transformations"] = PARTITION_TX
    return p


def table_panel(pid, x, y, w, h, title, targets, overrides, sort_by):
    return {"datasource": DS,
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False},
                                          "mappings": [],
                                          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
                             "overrides": overrides},
            "gridPos": {"h": h, "w": w, "x": x, "y": y}, "id": pid,
            "options": {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                        "showHeader": True, "sortBy": [{"desc": True, "displayName": sort_by}]},
            "pluginVersion": "10.4.7", "targets": targets, "title": title, "type": "table"}


def ovr(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": props}

TAA_CELL = [{"id": "unit", "value": "percent"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 80}, {"color": "green", "value": 85}]}}]
GRAD_RED = [{"id": "custom.cellOptions", "value": {"mode": "gradient", "type": "color-background"}}, {"id": "color", "value": {"mode": "continuous-reds"}}]

panels = []

# =====================================================================
# SECCION 1: TIPO DE PRODUCTO
# =====================================================================
panels.append(row(100, "Tipo de Producto · clasificación con Marcas Privadas (PB) y Naranja Pura (PN)", 0))

base_producto = ("WITH agg AS (\n  SELECT \n    " + BK + " AS bk,\n    " + TIPO_PRODUCTO + " AS tipo_producto,\n"
    "    SUM(CASE WHEN " + APROB + " THEN 1 ELSE 0 END) AS aprob,\n"
    "    SUM(CASE WHEN " + DENEG + " THEN 1 ELSE 0 END) AS deneg,\n"
    "    COUNT(*) AS total\n"
    "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n"
    "  WHERE " + FECHA_RANGO + "\n    AND " + TS_RANGO + "\n    " + FILTROS_STD + "\n"
    "    AND a.INSTRMTYP IN (" + INSTR_LIST + ")" + EXCL + "\n  GROUP BY 1, 2\n)\n")

sql_prod_taa = base_producto + ("SELECT bk AS time, tipo_producto AS metric, "
    "ROUND(100.0*aprob/NULLIF(total,0),2) AS \"TAA %\" FROM agg ORDER BY 1, 2;")
sql_prod_deneg = base_producto + ("SELECT bk AS time, tipo_producto AS metric, deneg AS \"Denegadas\" FROM agg ORDER BY 1, 2;")
sql_prod_tabla = base_producto.replace(BK + " AS bk,\n    ", "") \
    .replace("GROUP BY 1, 2", "GROUP BY 1") + \
    ("SELECT tipo_producto AS \"Tipo Producto\", total AS \"Total Op\", aprob AS \"Aprobadas\", deneg AS \"Denegadas\",\n"
     "  ROUND(100.0*aprob/NULLIF(total,0),2) AS \"TAA %\",\n"
     "  ROUND(100.0*total/NULLIF(SUM(total) OVER (),0),1) AS \"% del total\"\n"
     "FROM agg ORDER BY total DESC;")

panels.append(ts_panel(1, 0, 1, 12, 8, "TAA por Tipo de Producto · 15m", "percent",
                       [T(sql_prod_taa, "A", 0)], decimals=1, soft=(50, 100),
                       legend_calcs=["lastNotNull", "mean"], table_legend=True))
panels.append(ts_panel(2, 12, 1, 12, 8, "Denegadas por Tipo de Producto · 15m", "short",
                       [T(sql_prod_deneg, "A", 0)], legend_calcs=["sum", "max"], table_legend=True))
panels.append(table_panel(3, 0, 9, 24, 7, "Resumen por Tipo de Producto · rango seleccionado",
                          [T(sql_prod_tabla)],
                          [ovr("TAA %", TAA_CELL), ovr("Denegadas", GRAD_RED),
                           ovr("% del total", [{"id": "unit", "value": "percent"},
                               {"id": "custom.cellOptions", "value": {"mode": "basic", "type": "gauge"}},
                               {"id": "max", "value": 100}, {"id": "min", "value": 0}])],
                          "Total Op"))

# =====================================================================
# SECCION 2: QR Y BILLETERAS (fill9)
# =====================================================================
panels.append(row(200, "QR y Billeteras · fill9 (pos.1 = 'Q' pago QR, pos.2 = wallet id)", 16))

base_qr = ("WITH q AS (\n  SELECT \n    " + BK + " AS bk,\n"
    "    SUBSTRING(a.fill9, 2, 1) AS wallet_id_codigo,\n    " + WALLET_MAP + " AS billetera,\n"
    "    TRIM(a.codrespuestaautorizacion) AS cr\n"
    "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n"
    "  WHERE " + FECHA_RANGO + "\n    AND " + TS_RANGO + "\n"
    "    AND SUBSTRING(a.fill9, 1, 1) = 'Q'\n"
    "    AND TRIM(a.codtipomensajeautorizacion) <> '1500'" + EXCL + "\n)\n")

sql_qr_ts = base_qr + ("SELECT bk AS time, billetera AS metric, COUNT(*) AS \"Operaciones\" FROM q GROUP BY 1, 2 ORDER BY 1;")
sql_qr_tabla = base_qr + ("SELECT billetera AS \"Billetera\", wallet_id_codigo AS \"Cód.\",\n"
    "  COUNT(*) AS \"Operaciones\",\n"
    "  SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Aprobadas\",\n"
    "  SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Denegadas\",\n"
    "  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS \"TAA %\",\n"
    "  ROUND(100.0*COUNT(*)/NULLIF(SUM(COUNT(*)) OVER (),0),1) AS \"% del total\"\n"
    "FROM q GROUP BY 1, 2 ORDER BY 3 DESC;")

panels.append(ts_panel(10, 0, 17, 14, 8, "Operaciones QR por Billetera · 15m", "short",
                       [T(sql_qr_ts, "A", 0)], legend_calcs=["sum", "max"], table_legend=True))
panels.append(table_panel(11, 14, 17, 10, 8, "Ranking Billeteras QR · rango seleccionado",
                          [T(sql_qr_tabla)],
                          [ovr("TAA %", TAA_CELL), ovr("Denegadas", GRAD_RED),
                           ovr("% del total", [{"id": "unit", "value": "percent"},
                               {"id": "custom.cellOptions", "value": {"mode": "basic", "type": "gauge"}},
                               {"id": "max", "value": 100}, {"id": "min", "value": 0}])],
                          "Operaciones"))

# =====================================================================
# SECCION 3: MODALIDADES
# =====================================================================
panels.append(row(300, "Modalidades · Débito Automático / E-commerce / Presente", 25))

base_mod = ("WITH m AS (\n  SELECT \n    " + BK + " AS bk,\n    " + MODALIDAD + " AS modalidad,\n"
    "    TRIM(a.codrespuestaautorizacion) AS cr\n"
    "  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a\n"
    "  WHERE " + FECHA_RANGO + "\n    AND " + TS_RANGO + "\n    " + FILTROS_STD + EXCL + "\n)\n")

sql_mod_taa = base_mod + ("SELECT bk AS time, modalidad AS metric,\n"
    "  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS \"TAA %\"\n"
    "FROM m GROUP BY 1, 2 ORDER BY 1;")
sql_mod_vol = base_mod + ("SELECT bk AS time, modalidad AS metric, COUNT(*) AS \"Transacciones\" FROM m GROUP BY 1, 2 ORDER BY 1;")
sql_mod_tabla = base_mod + ("SELECT modalidad AS \"Modalidad\", COUNT(*) AS \"Transacciones\",\n"
    "  SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Aprobadas\",\n"
    "  SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Denegadas\",\n"
    "  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS \"TAA %\",\n"
    "  ROUND(100.0*COUNT(*)/NULLIF(SUM(COUNT(*)) OVER (),0),1) AS \"% del total\"\n"
    "FROM m GROUP BY 1 ORDER BY 2 DESC;")

panels.append(ts_panel(20, 0, 26, 12, 7, "TAA por Modalidad · 15m", "percent",
                       [T(sql_mod_taa, "A", 0)], decimals=1, soft=(50, 100),
                       legend_calcs=["lastNotNull", "mean"]))
panels.append(ts_panel(21, 12, 26, 12, 7, "Volumen por Modalidad · 15m", "short",
                       [T(sql_mod_vol, "A", 0)], legend_calcs=["sum", "max"]))
panels.append(table_panel(22, 0, 33, 24, 6, "Resumen por Modalidad · rango seleccionado",
                          [T(sql_mod_tabla)],
                          [ovr("TAA %", TAA_CELL), ovr("Denegadas", GRAD_RED),
                           ovr("% del total", [{"id": "unit", "value": "percent"},
                               {"id": "custom.cellOptions", "value": {"mode": "basic", "type": "gauge"}},
                               {"id": "max", "value": 100}, {"id": "min", "value": 0}])],
                          "Transacciones"))

# =====================================================================
# DASHBOARD
# =====================================================================
dash = {
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts", "type": "dashboard"}]},
    "description": ("Nuevos atributos: Tipo de Producto (con PB=Marcas Privadas y PN=Naranja Pura, "
                    "PA débito por BIN 604201/65008700), QR y Billeteras (fill9), Modalidades "
                    "(DA/E-commerce/Presente). Excluye 8 CUITs (anti-join). Result reuse 5m."),
    "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 2, "links": [], "liveNow": False,
    "panels": panels, "refresh": "1m", "schemaVersion": 39,
    "tags": ["payway", "noc", "producto", "qr", "billeteras", "modalidades"],
    "templating": {"list": []},
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {"refresh_intervals": ["1m", "5m", "15m"], "time_options": ["1h", "3h", "6h", "12h", "24h"]},
    "timezone": "browser",
    "title": "Payway · Tipo Producto · QR/Billeteras · Modalidades",
    "uid": "payway-prod-qr-mod", "version": 1, "weekStart": ""
}

with open("dashboard_producto_qr_modalidades.json", "w", encoding="utf-8") as fh:
    json.dump(dash, fh, ensure_ascii=False, indent=2)
print("OK -> dashboard_producto_qr_modalidades.json | paneles:", len(panels))
