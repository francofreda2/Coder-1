#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valida desvios_v17.0_abt.json contra su origen desvios_v16.8_origen.json.

Comprueba que la migracion a ABT0 no altero el tablero mas alla de lo previsto:
layout intacto, logica de cuadrantes/TAA intacta, filtros nuevos presentes en
todos los paneles y SQL sintacticamente balanceado.

    python3 check_desvios_abt.py
"""
import json, re, sys

SRC = "desvios_v16.8_origen.json"
OUT = "desvios_v17.0_abt.json"

src = json.load(open(SRC, encoding="utf-8"))
out = json.load(open(OUT, encoding="utf-8"))
err, ok = [], []
def chk(c, m): (ok if c else err).append(m)

sp = {p["id"]: p for p in src["panels"]}
op = {p["id"]: p for p in out["panels"]}

chk(len(src["panels"]) == len(out["panels"]) == 33, "33 paneles preservados")
chk(set(sp) == set(op), "ids de panel identicos")
chk(all(sp[i].get("gridPos") == op[i].get("gridPos") and sp[i].get("type") == op[i].get("type")
        for i in sp), "layout y tipos de panel intactos")
chk(all(json.dumps(sp[i].get(k), sort_keys=True) == json.dumps(op[i].get(k), sort_keys=True)
        for i in sp for k in ("fieldConfig", "transformations", "options")),
    "fieldConfig / transformations / options intactos")

tg = [(p["id"], t["rawSQL"]) for p in out["panels"]
      for t in (p.get("targets") or []) if t.get("rawSQL")]
chk(len(tg) == 33, "33 targets con SQL")
chk(sorted({i for i, s in tg if "ctx_autorizaciones_eps" in s}) == [38],
    "solo el panel 38 (BINes) sigue leyendo CTX")
chk(len([1 for i, s in tg if "abt0_ctx_livemonitoring" in s]) == 32,
    "32 targets migrados a ABT0")
chk(not [i for i, s in tg if "EXISTS(SELECT 1 FROM prisma_ab_analytics" in s],
    "sin EXISTS correlacionado contra la dimension de establecimientos")
chk(not [i for i, s in tg if i != 38 and
         ("fechalocalautorizacion" in s or re.search(r"\ba\.fecha\b(?!_particion)", s))],
    "sin date_parse ni a.fecha en los paneles ABT0")

for lbl, pat in [("Tipo de producto", "FILTRO Tipo de producto"),
                 ("Plataforma de pago", "FILTRO Plataforma de pago"),
                 ("Modalidad", "FILTRO Modalidad"),
                 ("LEFT JOIN dafil", "dafil.nro_establecimiento IS NOT NULL"),
                 ("exclusion de 8 CUITs", "30712334610,30715313681")]:
    chk(all(s.count(pat) == 1 for i, s in tg),
        "%s: exactamente una vez en los 33 targets" % lbl)

sv = [t["rawSQL"] for p in src["panels"] for t in (p.get("targets") or []) if t.get("rawSQL")]
for c in ["nri IS NULL AND ciss IS NOT NULL AND TRIM(ciss) NOT IN('59','63','83')",
          "TRIM(ciss) IN('59','63','83')",
          "'TA001','TA002','TA003','TA004','TA005','TA006','TA007','TA008','TA009',"
          "'TCADQ','LMT01','LMT02','LIM01','LIM02'",
          "cr IN('0000','0400','0900')",
          "cmsg<>'1420'",
          "NOT IN('92','30')"]:
    a = sum(x.count(c) for x in sv)
    b = sum(s.count(c) for i, s in tg)
    chk(a == b and a > 0, "logica preservada (%d = %d): %s" % (a, b, c[:44]))

def balanceado(s):
    d = 0; en_str = False; esc = False
    for ch in s:
        if en_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == "'": en_str = False
            continue
        if ch == "'": en_str = True
        elif ch == "(": d += 1
        elif ch == ")": d -= 1
        if d < 0: return False
    return d == 0 and not en_str

chk(all(balanceado(s) for i, s in tg), "parentesis y comillas balanceadas en 33/33")

nombres = [v["name"] for v in out["templating"]["list"]]
chk(nombres == ["cuit", "establecimiento", "marca", "banco", "motivo_emisor",
                "motivo_adqte", "producto", "plataforma", "modalidad"],
    "9 variables de templating en orden")
chk(out["uid"] == "desvios-v17-abt" and "id" not in out and out["refresh"] == "1m",
    "metadatos: uid nuevo, sin id, refresh 1m")
chk(all(t.get("connectionArgs", {}).get("resultReuseEnabled")
        for p in out["panels"] for t in (p.get("targets") or []) if t.get("rawSQL")),
    "result reuse de Athena activo en 33/33")

print("\n".join("  OK  " + m for m in ok))
if err:
    print("\nFALLAS:\n" + "\n".join("  XX  " + m for m in err))
    sys.exit(1)
print("\n%d/%d checks OK" % (len(ok), len(ok)))
