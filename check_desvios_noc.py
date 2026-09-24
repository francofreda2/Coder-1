#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valida la estructura de desvios_v17.1_noc.json.  python3 check_desvios_noc.py"""
import json, sys

d = json.load(open("desvios_v17.1_noc.json", encoding="utf-8"))
src = json.load(open("desvios_v17.0_abt.json", encoding="utf-8"))
err, ok = [], []
def chk(c, m): (ok if c else err).append(m)

P = d["panels"]
ids = [p["id"] for p in P]
chk(len(ids) == len(set(ids)) == 47, "47 paneles con id unico")
chk(set(p["id"] for p in src["panels"]) <= set(ids), "los 33 paneles de la v17.0 siguen presentes")

celdas = {}
choques = []
for p in P:
    g = p["gridPos"]
    for x in range(g["x"], g["x"] + g["w"]):
        for y in range(g["y"], g["y"] + g["h"]):
            if (x, y) in celdas:
                choques.append((celdas[(x, y)], p["id"]))
            celdas[(x, y)] = p["id"]
    if g["x"] + g["w"] > 24:
        choques.append(("ancho", p["id"]))
chk(not choques, "sin paneles superpuestos ni fuera de las 24 columnas")

primero = min(P, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
chk(primero["id"] == 900, "la seccion NOC queda arriba de todo")

refs = [(p["id"], t["panelId"]) for p in P for t in (p.get("targets") or []) if "panelId" in t]
fuentes = {p["id"]: p for p in P}
chk(refs and all(r in fuentes and any(t.get("rawSQL") for t in fuentes[r]["targets"])
                 for _, r in refs),
    "%d paneles reusan la consulta de otro panel valido (-- Dashboard --)" % len(refs))

nuevos = [t["rawSQL"] for p in P if p["id"] >= 1000 for t in (p.get("targets") or []) if t.get("rawSQL")]
chk(len(nuevos) == 4, "4 consultas nuevas a Athena para los 12 paneles NOC")
for lbl, pat in [("Tipo de producto", "FILTRO Tipo de producto"),
                 ("Plataforma", "FILTRO Plataforma de pago"),
                 ("Modalidad", "FILTRO Modalidad"),
                 ("marca/banco/motivos", "${motivo_adqte:text}"),
                 ("CUIT", "${cuit:raw}"),
                 ("exclusion de CUITs", "30712334610,30715313681")]:
    chk(all(pat in s for s in nuevos), "consultas NOC respetan filtro %s" % lbl)

todos = [t["rawSQL"] for p in P for t in (p.get("targets") or []) if t.get("rawSQL")]
chk(not any("%15)" in s for s in todos), "no quedan buckets fijos de 15 min")
chk(not any(p.get("title", "").endswith("· 15m") for p in P), "ningun titulo dice '15m' fijo")

p10 = fuentes[10]
chk('"Total TRX"' in p10["targets"][0]["rawSQL"] and '"Total D-1"' in p10["targets"][1]["rawSQL"],
    "panel 10 trae Total TRX y Total D-1")

v = [x["name"] for x in d["templating"]["list"]]
chk(v[0] == "resolucion" and len(v) == 10, "variable Resolucion agregada (10 variables)")
chk(d["timezone"] == "America/Argentina/Buenos_Aires", "zona horaria del tablero = Argentina")
chk(d["uid"] == "desvios-v17-noc" and "id" not in d, "uid nuevo y sin id")

print("\n".join("  OK  " + m for m in ok))
if err:
    print("\nFALLAS:\n" + "\n".join("  XX  " + m for m in err)); sys.exit(1)
print("\n%d/%d checks OK" % (len(ok), len(ok)))
