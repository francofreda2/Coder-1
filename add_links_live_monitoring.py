#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agrega a "One page live monitoring" (uid payway-tx-v21) los links para ir y
volver del tablero de Desvíos con el mismo rango y filtros.

Solo toca el bloque "links" del tablero: no cambia paneles, SQL ni variables.
Se aplica sobre el JSON exportado de Grafana, asi sirve para cualquier version
futura de ese tablero.

    python3 add_links_live_monitoring.py exportado.json salida.json
"""
import json
import sys

UID_DESVIOS = "payway-desvios"

# Variables con la misma definicion en los dos tableros. 'marca' no se pasa:
# One page usa VISA / CABAL_DISCOVER / OTRAS y Desvios Visa / Cabal / Otra.
URL_DESVIOS = ("/d/%s/?var-cuit=${cuit:raw}"
               "&${plataforma:queryparam}&${producto:queryparam}&${modalidad:queryparam}"
               % UID_DESVIOS)

LINKS = [
    {"asDropdown": False, "icon": "dashboard", "includeVars": False, "keepTime": True,
     "tags": [], "targetBlank": False, "title": "Desvíos · NOC (análisis de la caída)",
     "tooltip": "Mismo rango, CUIT, gateway, producto y modalidad",
     "type": "link", "url": URL_DESVIOS},
    {"asDropdown": False, "icon": "sync", "includeVars": False, "keepTime": True,
     "tags": [], "targetBlank": False, "title": "Limpiar filtros",
     "tooltip": "Vuelve a este tablero sin filtros, con el mismo rango",
     "type": "link", "url": "/d/payway-tx-v21/"},
]


def main():
    entrada, salida = sys.argv[1], sys.argv[2]
    d = json.load(open(entrada, encoding="utf-8"))
    titulos = {l["title"] for l in LINKS}
    d["links"] = [l for l in d.get("links") or [] if l.get("title") not in titulos] + LINKS
    d.pop("id", None)
    json.dump(d, open(salida, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("OK: %d links en %s" % (len(d["links"]), salida))


if __name__ == "__main__":
    main()
