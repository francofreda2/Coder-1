#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera "Desvíos · NOC" v18 a partir de la v17.1: misma logica y mismo SQL,
reorganizado para que sea simple de usar durante un incidente.

- Recorrido de arriba hacia abajo en 5 preguntas numeradas:
    1 ¿Está procesando?  2 ¿Dónde? (gateways)  3 ¿Cuándo baja la TAA?
    4 ¿Por qué? (cuadrantes)  5 ¿Qué marca?
  El detalle (motivos por cuadrante, marca, producto, bancos, BINes) queda en
  secciones colapsadas: no consultan Athena hasta que se abren.
- "Estado General" (4 stats, 4 consultas) pasa a un solo panel con una consulta.
- Indicadores NOC: se suman TAA de los ultimos 15 min y su diferencia contra D-1.
- Clic para filtrar: en un gateway (graficos y tabla) o en una marca, el tablero
  entero pasa a ese gateway / marca, conservando rango y demas filtros.
- Links arriba: limpiar filtros y saltar a "One page live monitoring" con el
  mismo rango, CUIT, gateway, producto y modalidad.
- uid estable (payway-desvios) para que links y favoritos no cambien por version.

    python3 build_desvios_v18.py
"""

import copy
import json
import sys

ENTRADA = "desvios_v17.1_noc.json"
SALIDA = "desvios_v18.json"

UID = "payway-desvios"
UID_ONE_PAGE = "payway-tx-v21"
TITULO = "Desvíos · NOC · Payway"
TAGS = ["payway", "noc", "desvios", "abt"]

DS_DASH = {"type": "datasource", "uid": "-- Dashboard --"}

ORDEN_VARIABLES = ["plataforma", "marca", "producto", "modalidad", "cuit",
                   "establecimiento", "banco", "motivo_emisor", "motivo_adqte",
                   "resolucion"]
ETIQUETAS = {
    "plataforma": ("Gateway", "Plataforma de pago (sufijo de 4 dígitos de programaperiferiaorigen)."),
    "marca": ("Marca", "Una o varias marcas."),
    "producto": ("Producto", "Tipo de producto según INSTRMTYP."),
    "modalidad": ("Modalidad", "Presente, E-commerce o Débito Automático."),
    "cuit": ("CUIT", "CUIT del comercio, con o sin guiones. Vacío (o -1) = todos."),
    "establecimiento": ("Establecimiento", "Número de establecimiento. Vacío = todos."),
    "banco": ("Banco emisor", "Uno o varios bancos emisores."),
    "motivo_emisor": ("Motivo emisor", "Código de respuesta del emisor (2 dígitos)."),
    "motivo_adqte": ("Motivo adquirente", "Rechazo interno del adquirente."),
    "resolucion": ("Resolución", "Tamaño del bucket de los gráficos. Durante un incidente, 1 min."),
}

# Secciones en orden: (id de fila, titulo, colapsada)
SECCIONES = [
    (900, "1 · ¿Está procesando?  —  volumen, frescura del dato y latencia", False),
    (1100, "2 · ¿Dónde?  —  gateways  (clic en un gateway para filtrar el tablero)", False),
    (200, "3 · ¿Cuándo baja la TAA?", False),
    (300, "4 · ¿Por qué?  —  cuadrantes y motivos", False),
    (310, "Detalle · motivos de cada cuadrante", True),
    (400, "5 · ¿Qué marca?  —  clic en una marca para filtrar el tablero", False),
    (420, "Detalle · TAA y volumen por marca", True),
    (430, "Detalle · TAA y volumen por producto (Crédito / Débito / Prepago)", True),
    (720, "Detalle · top 10 bancos emisores por rechazos", True),
    (710, "Detalle · top 10 BINes por rechazos  (lee CTX: más lento)", True),
]

TITULOS = {
    10: "TAA actual vs D-1 y volumen (Total / Aprobadas / Denegadas)",
    11: "Desvío de TAA vs D-1 (pp)",
    20: "Denegadas por cuadrante",
    21: "Fondos insuficientes + Do Not Honor",
    22: "Otros motivos (sin Fondos / DNH)",
    23: "Operativo Emisor · top 20 motivos",
    24: "Riesgo Emisor · motivos",
    25: "Operativo Adquirente · motivos",
    26: "Riesgo Adquirente · motivos",
    30: "TAA por marca",
    120: "Top 10 bancos emisores · rechazos",
    38: "Top 10 BINes (8 dígitos) · rechazos",
}

DESCRIPCIONES = {
    11: "TAA actual menos TAA del mismo bucket de ayer, en puntos porcentuales. "
        "Negativo = hoy se aprueba menos que ayer.",
    20: "Denegadas de cada bucket, separadas en los cuatro cuadrantes: Operativo / "
        "Riesgo, de Emisor / Adquirente. Muestra de qué lado viene la caída de TAA.",
    21: "Las dos causas de rechazo más frecuentes del emisor. Suelen explicar el "
        "grueso de las denegadas y no son un problema de procesamiento.",
    22: "Denegadas sin contar Fondos insuficientes ni Do Not Honor: acá aparecen "
        "los problemas operativos.",
    23: "Motivos individuales del cuadrante Operativo Emisor (top 20 del rango).",
    24: "Motivos individuales del cuadrante Riesgo Emisor (59, 63, 83).",
    25: "Rechazos internos del adquirente que no son de riesgo.",
    26: "Rechazos internos de riesgo del adquirente (TA00x, TCADQ, LMT/LIM).",
    30: "TAA de cada marca por bucket. Clic en una línea para filtrar todo el "
        "tablero por esa marca.",
    120: "Los 10 bancos emisores con más rechazos en el rango, por bucket.",
}
for _id, _marca in ((721, "Visa"), (101, "Mastercard"), (102, "Cabal"), (103, "Amex"), (104, "UPI")):
    DESCRIPCIONES[_id] = ("TAA de %s (eje izquierdo) con aprobadas y denegadas "
                          "(eje derecho) por bucket." % _marca)
for _id, _prod in ((110, "Crédito"), (111, "Débito"), (112, "Prepago")):
    DESCRIPCIONES[_id] = ("TAA de %s (eje izquierdo) con aprobadas y denegadas "
                          "(eje derecho) por bucket. Clasificación original por "
                          "INSTRMTYP, no la del filtro Producto." % _prod)

CUIT_ALL_VIEJO = "COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1'"
CUIT_ALL_NUEVO = "COALESCE(NULLIF('${cuit:raw}',''),'-1') IN ('-1','$__all')"


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def url_filtrar(variable, valor):
    """Este tablero con `variable`=`valor`, conservando rango y demas filtros."""
    otros = "&".join("${%s:queryparam}" % v for v in ORDEN_VARIABLES if v != variable)
    return "/d/%s/?${__url_time_range}&var-%s=%s&%s" % (UID, variable, valor, otros)


def url_one_page(plataforma="${plataforma:queryparam}", con_rango=True):
    """con_rango=False para los links de arriba: keepTime ya agrega el rango."""
    if not plataforma.startswith("${plataforma"):
        plataforma = "var-plataforma=" + plataforma
    rango = "${__url_time_range}&" if con_rango else ""
    return ("/d/%s/?%svar-cuit=${cuit:raw}&%s"
            "&${producto:queryparam}&${modalidad:queryparam}" % (UID_ONE_PAGE, rango, plataforma))


def data_link(titulo, url):
    return {"title": titulo, "url": url, "targetBlank": False}


# ---------------------------------------------------------------------------

def todos(panels):
    for p in panels:
        yield p
        for c in p.get("panels") or []:
            yield c


def secciones_v17(panels):
    """Agrupa los paneles de la v17.1 por la fila que los precede."""
    orden = sorted(panels, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
    grupos, actual = {}, None
    for p in orden:
        if p["type"] == "row":
            actual = p["id"]
            grupos[actual] = []
        else:
            grupos[actual].append(p)
    return grupos


def kpis(grupo):
    """Fila de indicadores NOC: 8 stats de ancho 3, con TAA 15 min y su delta."""
    por_id = {p["id"]: p for p in grupo}
    base = por_id[1003]

    taa = copy.deepcopy(base)
    taa.update(id=1006, title="TAA", description=(
        "Tasa de aceptación de los últimos 15 minutos cerrados. Respeta todos los "
        "filtros. Con volumen normal y TAA baja, el problema es de rechazos; con "
        "volumen bajo, es de procesamiento."))
    taa["options"]["reduceOptions"]["fields"] = "/^taa_15$/"
    taa["fieldConfig"]["defaults"].update(unit="percent", decimals=2, thresholds={
        "mode": "absolute", "steps": [{"color": "red", "value": None},
                                      {"color": "orange", "value": 80},
                                      {"color": "green", "value": 85}]})

    dtaa = copy.deepcopy(base)
    dtaa.update(id=1007, title="Δ TAA D-1", description=(
        "TAA de los últimos 15 minutos cerrados menos la de los mismos 15 minutos "
        "de ayer, en puntos porcentuales."))
    dtaa["options"]["reduceOptions"]["fields"] = "/^dtaa_pp$/"
    dtaa["fieldConfig"]["defaults"].update(unit="suffix: pp", decimals=1, thresholds={
        "mode": "absolute", "steps": [{"color": "red", "value": None},
                                      {"color": "orange", "value": -5},
                                      {"color": "green", "value": -2}]})

    titulos = {1000: "Datos hasta", 1001: "Atraso", 1002: "Resolución",
               1003: "TRX/min", 1004: "Vol. vs D-1", 1005: "Silencio"}
    for pid, t in titulos.items():
        por_id[pid]["title"] = t
    orden = [por_id[1000], por_id[1001], por_id[1002], por_id[1003], por_id[1004],
             taa, dtaa, por_id[1005]]
    y = por_id[1000]["gridPos"]["y"]
    for i, p in enumerate(orden):
        p["gridPos"] = {"h": 4, "w": 3, "x": 3 * i, "y": y}
    resto = [p for p in grupo if p["id"] not in titulos]
    return orden + resto


def resumen_rango(stat_taa):
    """Los 4 stats de 'Estado General' en un solo panel y una sola consulta."""
    sql = stat_taa["targets"][0]["rawSQL"].rsplit("\nSELECT ", 1)[0] + (
        "\nSELECT ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)"
        "/NULLIF(COUNT(*),0),2) AS \"TAA\","
        "COUNT(*) AS \"Total TRX\","
        "SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS \"Denegadas\","
        "ROUND(100.0*SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END)"
        "/NULLIF(COUNT(*),0),2) AS \"Tasa de rechazo\" "
        "FROM filt WHERE ts>=$__timeFrom() AND ts<$__timeTo();")
    tgt = copy.deepcopy(stat_taa["targets"][0])
    tgt["rawSQL"] = sql

    def unidad(nombre, unit, decimals, color):
        return {"matcher": {"id": "byName", "options": nombre},
                "properties": [{"id": "unit", "value": unit},
                               {"id": "decimals", "value": decimals},
                               {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}}]}

    return {
        "datasource": copy.deepcopy(stat_taa["datasource"]),
        "description": ("Totales de todo el rango seleccionado (lo que antes era "
                        "'Estado General'). Total excluye reversos."),
        "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "#5794F2"},
                                     "mappings": [], "unit": "short",
                                     "thresholds": {"mode": "absolute",
                                                    "steps": [{"color": "#5794F2", "value": None}]}},
                        "overrides": [unidad("TAA", "percent", 2, "#73BF69"),
                                      unidad("Total TRX", "short", 0, "#B877D9"),
                                      unidad("Denegadas", "short", 0, "#F2495C"),
                                      unidad("Tasa de rechazo", "percent", 2, "#F2495C")]},
        "gridPos": {"h": 3, "w": 24, "x": 0, "y": 0},
        "id": 5,
        "options": {"colorMode": "value", "graphMode": "none", "justifyMode": "center",
                    "orientation": "vertical",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "showPercentChange": False, "textMode": "value_and_name",
                    "wideLayout": True},
        "pluginVersion": "10.4.7",
        "targets": [tgt],
        "title": "Resumen del rango seleccionado",
        "type": "stat",
    }


def links_gateway(panel, campo_cod, campo_nombre):
    links = [data_link("Filtrar el tablero por " + campo_nombre,
                       url_filtrar("plataforma", campo_cod)),
             data_link("Ver " + campo_nombre + " en One page live monitoring",
                       url_one_page(campo_cod))]
    panel["fieldConfig"]["defaults"]["links"] = links


def main():
    dash = json.load(open(ENTRADA, encoding="utf-8"))
    grupos = secciones_v17(dash["panels"])
    filas = {p["id"]: p for p in dash["panels"] if p["type"] == "row"}

    # Estado General -> un panel dentro de "¿Cuándo baja la TAA?"
    estado = {p["id"]: p for p in grupos.pop(100)}
    resumen = resumen_rango(estado[1])
    resumen["gridPos"]["y"] = min(p["gridPos"]["y"] for p in grupos[200])
    for p in grupos[200]:
        p["gridPos"]["y"] += 3
    grupos[200].insert(0, resumen)

    grupos[900] = kpis(grupos[900])

    # Clic para filtrar
    por_id = {p["id"]: p for g in grupos.values() for p in g}
    links_gateway(por_id[1101], "${__field.labels.cod}", "${__field.labels.metric}")
    links_gateway(por_id[1102], "${__field.labels.cod}", "${__field.labels.metric}")
    tabla = por_id[1103]
    cortos = {"TRX últ. 15 min": "TRX 15'", "TRX D-1 mismo horario": "TRX D-1 15'",
              "TAA últ. 15 min": "TAA 15'", "TAA D-1": "TAA D-1 15'", "Δ TAA (pp)": "Δ TAA pp",
              "Último tráfico (hs)": "Último tráfico", "Silencio (min)": "Silencio"}
    for campo, nombre in cortos.items():
        tabla["fieldConfig"]["overrides"].append(
            {"matcher": {"id": "byName", "options": campo},
             "properties": [{"id": "displayName", "value": nombre}]})
    tabla["fieldConfig"]["defaults"]["custom"]["minWidth"] = 70
    tabla["fieldConfig"]["overrides"].append(
        {"matcher": {"id": "byName", "options": "TRX últ. 6 h"},
         "properties": [{"id": "custom.hidden", "value": True}]})
    tabla["fieldConfig"]["overrides"].append(
        {"matcher": {"id": "byName", "options": "Silencio (min)"},
         "properties": [{"id": "unit", "value": "m"}]})
    for ov in tabla["fieldConfig"]["overrides"]:
        if ov["matcher"]["options"] == "Gateway":
            ov["properties"].append({"id": "links", "value": [
                data_link("Filtrar el tablero por ${__data.fields.Gateway}",
                          url_filtrar("plataforma", "${__data.fields.cod}")),
                data_link("Ver ${__data.fields.Gateway} en One page live monitoring",
                          url_one_page("${__data.fields.cod}"))]})
    por_id[30]["fieldConfig"]["defaults"]["links"] = [
        data_link("Filtrar el tablero por ${__field.name}",
                  url_filtrar("marca", "${__field.name}"))]

    # Panel 10: Baseline D-1 comparte el eje de la TAA (antes abria un tercer eje)
    # y la leyenda muestra solo el ultimo valor para que entren las 6 series.
    p10 = por_id[10]
    for ov in p10["fieldConfig"]["overrides"]:
        if ov["matcher"]["options"] == "Baseline D-1":
            ov["properties"].append({"id": "custom.axisPlacement", "value": "left"})
    p10["options"]["legend"]["calcs"] = ["lastNotNull"]

    # Titulos y descripciones
    for pid, p in por_id.items():
        if pid in TITULOS:
            p["title"] = TITULOS[pid]
        p["title"] = p.get("title", "").replace(" · ${resolucion:text}", "")
        if pid in DESCRIPCIONES and not (p.get("description") or "").strip():
            p["description"] = DESCRIPCIONES[pid]

    # CUIT: aceptar '$__all' (llega asi desde el link de One page live monitoring)
    n_cuit = 0
    for p in por_id.values():
        for t in p.get("targets") or []:
            if t.get("rawSQL"):
                n_cuit += t["rawSQL"].count(CUIT_ALL_VIEJO)
                t["rawSQL"] = t["rawSQL"].replace(CUIT_ALL_VIEJO, CUIT_ALL_NUEVO)

    # Layout: secciones en orden, las colapsadas guardan sus paneles adentro
    panels, y = [], 0
    for fid, titulo, colapsada in SECCIONES:
        fila = filas[fid]
        fila.update(title=titulo, collapsed=colapsada, panels=[])
        fila["gridPos"] = {"h": 1, "w": 24, "x": 0, "y": y}
        panels.append(fila)
        hijos = grupos[fid]
        y0 = min(h["gridPos"]["y"] for h in hijos)
        alto = max(h["gridPos"]["y"] + h["gridPos"]["h"] for h in hijos) - y0
        for h in hijos:
            h["gridPos"]["y"] = h["gridPos"]["y"] - y0 + y + 1
        if colapsada:
            fila["panels"] = hijos
            y += 1
        else:
            panels.extend(hijos)
            y += 1 + alto
    dash["panels"] = panels

    # Variables: filtros mas usados primero, resolucion al final
    lista = {v["name"]: v for v in dash["templating"]["list"]}
    for nombre, (label, desc) in ETIQUETAS.items():
        lista[nombre]["label"] = label
        lista[nombre]["description"] = desc
    dash["templating"]["list"] = [lista[n] for n in ORDEN_VARIABLES]

    dash["links"] = [
        {"asDropdown": False, "icon": "dashboard", "includeVars": False, "keepTime": True,
         "tags": [], "targetBlank": False, "title": "One page live monitoring",
         "tooltip": "Mismo rango, CUIT, gateway, producto y modalidad",
         "type": "link", "url": url_one_page(con_rango=False)},
        {"asDropdown": False, "icon": "sync", "includeVars": False, "keepTime": True,
         "tags": [], "targetBlank": False, "title": "Limpiar filtros",
         "tooltip": "Vuelve a este tablero sin filtros, con el mismo rango",
         "type": "link", "url": "/d/%s/" % UID},
    ]

    dash.update(uid=UID, title=TITULO, tags=TAGS, version=1)
    dash.pop("id", None)
    dash["description"] = (
        "v18. Recorrido de arriba hacia abajo: 1 ¿Está procesando? 2 ¿Dónde? "
        "3 ¿Cuándo baja la TAA? 4 ¿Por qué? 5 ¿Qué marca? El detalle está en "
        "secciones colapsadas.")

    salida = json.dumps(dash, ensure_ascii=False, indent=2)
    json.loads(salida)
    open(SALIDA, "w", encoding="utf-8").write(salida)

    visibles = sum(1 for p in panels for t in (p.get("targets") or []) if t.get("rawSQL"))
    total = sum(1 for p in todos(panels) for t in (p.get("targets") or []) if t.get("rawSQL"))
    print("OK")
    print("  consultas al abrir:   %d (de %d en total)" % (visibles, total))
    print("  predicados CUIT:      %d aceptan '$__all'" % n_cuit)
    print("  salida:               %s" % SALIDA)


if __name__ == "__main__":
    sys.exit(main())
