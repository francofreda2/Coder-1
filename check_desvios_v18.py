#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valida la estructura e interaccion de desvios_v18.json.  python3 check_desvios_v18.py"""
import json
import re
import sys

d = json.load(open("desvios_v18.json", encoding="utf-8"))
v17 = json.load(open("desvios_v17.1_noc.json", encoding="utf-8"))
err, ok = [], []
def chk(c, m): (ok if c else err).append(m)

top = d["panels"]
todos = [x for p in top for x in [p] + (p.get("panels") or [])]
por_id = {p["id"]: p for p in todos}
filas = [p for p in top if p["type"] == "row"]

ids = [p["id"] for p in todos]
chk(len(ids) == len(set(ids)), "ids unicos (%d paneles)" % len(ids))
esperados = {p["id"] for p in v17["panels"]} - {100, 1, 2, 3, 4} | {5, 1006, 1007}
chk(set(ids) == esperados, "estan todos los paneles de la v17.1 (Estado General -> panel 5)")

# Layout
def solapados(panels):
    celdas, malos = {}, []
    for p in panels:
        g = p["gridPos"]
        if g["x"] + g["w"] > 24:
            malos.append(p["id"])
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                if (x, y) in celdas:
                    malos.append((celdas[(x, y)], p["id"]))
                celdas[(x, y)] = p["id"]
    return malos
chk(not solapados(top), "sin superposicion en lo visible")
chk(all(not solapados(f["panels"]) for f in filas if f.get("collapsed")),
    "sin superposicion dentro de las secciones colapsadas")
ys = [p["gridPos"]["y"] for p in top]
chk(ys == sorted(ys), "paneles en orden vertical")
for f in filas:
    if f.get("collapsed"):
        chk(all(h["gridPos"]["y"] > f["gridPos"]["y"] for h in f["panels"]),
            "hijos debajo de su fila colapsada: %s" % f["title"][:40])

titulos = [f["title"] for f in filas]
chk([t.split(" ")[0] for t in titulos if t[0].isdigit()] == ["1", "2", "3", "4", "5"],
    "preguntas numeradas 1 a 5 en orden")
chk(all(f.get("collapsed") == f["title"].startswith("Detalle") for f in filas),
    "abiertas las preguntas, colapsado el detalle")

visibles = sum(1 for p in top for t in (p.get("targets") or []) if t.get("rawSQL"))
total = sum(1 for p in todos for t in (p.get("targets") or []) if t.get("rawSQL"))
chk(visibles <= 12, "consultas a Athena al abrir: %d de %d" % (visibles, total))

# Paneles que reusan la consulta de otro: la fuente tiene que estar visible
visibles_ids = {p["id"] for p in top}
for p in todos:
    for t in p.get("targets") or []:
        if "panelId" in t:
            src = por_id.get(t["panelId"])
            chk(src is not None and t["panelId"] in visibles_ids
                and any(x.get("rawSQL") for x in src["targets"]),
                "panel %s toma datos del %s, que esta visible" % (p["id"], t["panelId"]))

# Campos que leen los stats existen en la consulta de KPIs
kpi_sql = por_id[1000]["targets"][0]["rawSQL"]
for p in todos:
    if p["type"] == "stat" and any("panelId" in t for t in p.get("targets") or []) or p["id"] == 1000:
        campo = p["options"]["reduceOptions"]["fields"].strip("/^$")
        chk(re.search(r"AS %s\b" % campo, kpi_sql) is not None, "stat %s lee '%s'" % (p["id"], campo))

# Variables: toda referencia ${x...} en SQL, links y data links existe
nombres = {v["name"] for v in d["templating"]["list"]}
texto = json.dumps(d, ensure_ascii=False)
refs = set(re.findall(r"\$\{(\w+)(?::\w+)?\}", texto))
builtin = {"__url_time_range", "__field", "__data", "__all"}
faltan = {r for r in refs if r not in nombres and not r.startswith("__")}
chk(not faltan, "todas las variables referenciadas existen (faltan: %s)" % sorted(faltan))
chk([v["name"] for v in d["templating"]["list"]][0] == "plataforma"
    and d["templating"]["list"][-1]["name"] == "resolucion",
    "variables: Gateway primero, Resolucion al final")
chk(all(v.get("label") and v.get("description") for v in d["templating"]["list"]),
    "todas las variables tienen etiqueta y ayuda")

# Interaccion
def links(p):
    ls = list(p["fieldConfig"]["defaults"].get("links") or [])
    for ov in p["fieldConfig"].get("overrides") or []:
        for pr in ov["properties"]:
            if pr["id"] == "links":
                ls += pr["value"]
    return ls
for pid, var in ((1101, "plataforma"), (1102, "plataforma"), (1103, "plataforma"), (30, "marca")):
    ls = links(por_id[pid])
    chk(any("/d/%s/" % d["uid"] in l["url"] and "var-%s=" % var in l["url"]
            and "${__url_time_range}" in l["url"] for l in ls),
        "panel %s: clic filtra por %s conservando el rango" % (pid, var))
    for l in ls:
        if "/d/%s/" % d["uid"] in l["url"]:
            chk(l["url"].count("var-%s=" % var) + l["url"].count("${%s:queryparam}" % var) == 1,
                "panel %s: '%s' aparece una sola vez en el link" % (pid, var))
chk({l["title"] for l in d["links"]} == {"One page live monitoring", "Limpiar filtros"},
    "links arriba: One page live monitoring y Limpiar filtros")

# SQL: igual a la v17.1 salvo el CUIT que acepta '$__all'
v17_ids = {p["id"]: p for p in v17["panels"]}
distintos = []
for pid, p in por_id.items():
    if pid in v17_ids:
        a = [t.get("rawSQL") for t in v17_ids[pid].get("targets") or []]
        b = [(t.get("rawSQL") or "").replace(" IN ('-1','$__all')", "='-1'") or None
             for t in p.get("targets") or []]
        if a != b:
            distintos.append(pid)
chk(not distintos, "SQL identico a la v17.1 salvo el CUIT (distintos: %s)" % distintos)
chk(all("${resolucion:text}" not in p.get("title", "") for p in todos), "titulos sin '· auto (según rango)'")
chk(all((p.get("description") or "").strip() for p in todos if p["type"] != "row"),
    "todos los paneles tienen descripcion")
chk(d["uid"] == "payway-desvios" and "id" not in d, "uid estable payway-desvios")

print("\n".join("  OK  " + m for m in ok))
if err:
    print("\nFALLAS:\n" + "\n".join("  XX  " + m for m in err))
    sys.exit(1)
print("\n%d/%d checks OK" % (len(ok), len(ok)))
