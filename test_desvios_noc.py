#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de ejecucion del tablero de Desvios sobre datos sinteticos.

Arma una ABT0 falsa en DuckDB y corre el SQL real de los paneles (traducido de Trino a
DuckDB con sqlglot). Hay cuatro dias del mismo dia de la semana: hoy (24/09) y sus dias de
referencia D-7 (17/09), D-14 (10/09) y D-21 (03/09).

  - 3 gateways: Paystore (0001) 20 TRX/min, Decidir (8002) 10, Pos Legacy (7001) 5.
    Pos Legacy usa tarjetas PC (marca privada) que el ETL de ABT0 manda como OTRAS.
  - D-14 fue atipico: Paystore x3 todo el dia (la mediana lo tiene que ignorar).
  - Hoy, 13:05-13:10 UTC: caida total.
  - Hoy, 13:00-13:40 UTC: Decidir sin trafico.
  - Hoy, 14:00-14:40 UTC: Pos Legacy sin trafico (segmento chico: regla de 30').
  - Hoy desde 15:00 UTC: Paystore con TAA degradada (60 % contra 90 %).
  - Hoy desde 15:25 UTC: 6 reversos por minuto (lo habitual es 1).
  - La ABT llega hasta 15:52 UTC; "ahora" = 16:00 UTC (8 min de atraso). Con 15' de
    asentamiento, el ancla queda en 15:38 y la ventana de 5' es 15:30-15:35.

    pip install duckdb sqlglot pytz
    python3 test_desvios_noc.py [tablero.json]   (por defecto la v19)

El panel 38 (lee ctx_autorizaciones_eps) no se ejecuta: esa tabla no existe aca.
"""
import csv
import datetime as dt
import json
import os
import random
import re
import sys
import tempfile

import duckdb
import sqlglot

random.seed(7)
SP = tempfile.mkdtemp() + os.sep
con = duckdb.connect()
con.execute("SET TimeZone='UTC'")  # en ABT0 minuto_utc es timestamp(6) with time zone
con.execute("CREATE SCHEMA payway_poststage_prod_db; CREATE SCHEMA prisma_ab_analytics_prod_db;")
con.execute("CREATE TABLE prisma_ab_analytics_prod_db.establecimiento(nro_establecimiento INTEGER, "
            "cuit_establecimiento_host VARCHAR, cod_debito_automatico_estab VARCHAR)")
con.execute("INSERT INTO prisma_ab_analytics_prod_db.establecimiento VALUES "
            "(111,'30-11111111-1','0'),(222,'30-22222222-2','1')")
COLS = ["minuto_utc TIMESTAMPTZ", "fecha_particion VARCHAR", "cuit VARCHAR", "nro_establecimiento VARCHAR",
        "marca VARCHAR", "instrmtyp VARCHAR", "cod_respuesta VARCHAR", "cod_tipo_mensaje VARCHAR",
        "cod_proceso_autorizacion VARCHAR", "codrespuestaiss VARCHAR", "nrorechazointerno VARCHAR",
        "codbancoemisor VARCHAR", "programaperiferiaorigen VARCHAR", "coddebitoautomatico VARCHAR",
        "codidentpresenciacliente VARCHAR", "codmetodoingresomovimiento VARCHAR", "latencia_ms BIGINT",
        "fill9 VARCHAR"]
HOY = dt.date(2026, 9, 24)
GM = dt.datetime(2026, 9, 24, 15, 52)          # ultimo minuto cargado en la ABT
RATE = {"0001": 20, "8002": 10, "7001": 5}
DIAS = [HOY - dt.timedelta(days=d) for d in (21, 14, 7, 0)]


def entre(t, a, b):
    return dt.datetime.combine(HOY, a) <= t < dt.datetime.combine(HOY, b)


with open(SP + "abt0.csv", "w", newline="") as f:
    w = csv.writer(f)
    for dia in DIAS:
        hoy = dia == HOY
        t = dt.datetime.combine(dia, dt.time(8, 0))
        fin = GM if hoy else dt.datetime.combine(dia, dt.time(17, 0))
        while t <= fin:
            fp = (t - dt.timedelta(hours=3)).strftime("%Y-%m-%d")
            caida = hoy and entre(t, dt.time(13, 5), dt.time(13, 10))
            for gw, r in RATE.items():
                k = r * (3 if dia == HOY - dt.timedelta(days=14) and gw == "0001" else 1)
                if caida or (hoy and gw == "8002" and entre(t, dt.time(13, 0), dt.time(13, 40))) \
                        or (hoy and gw == "7001" and entre(t, dt.time(14, 0), dt.time(14, 40))):
                    k = 0
                for _ in range(k):
                    p = 0.6 if (hoy and gw == "0001" and t >= dt.datetime.combine(HOY, dt.time(15, 0))) else 0.9
                    ok = random.random() < p
                    marca, tipo = ("OTRAS", "PC") if gw == "7001" else ("VISA", "VC")
                    w.writerow([t, fp, "30111111111", "111", marca, tipo, "0000" if ok else "0051", "1100", "00",
                                "" if ok else "51", "", "7", "APP" + gw, "0", "8" if gw == "8002" else "1", "1",
                                random.randint(200, 900), "x"])
            if not caida:
                nrev = 6 if hoy and t >= dt.datetime.combine(HOY, dt.time(15, 25)) else 1
                for _ in range(nrev):
                    w.writerow([t, fp, "30111111111", "111", "VISA", "VC", "0000", "1420", "00", "", "", "7",
                                "APP0001", "0", "1", "1", 300, "x"])
            t += dt.timedelta(minutes=1)
con.execute("CREATE TABLE payway_poststage_prod_db.abt0_ctx_livemonitoring(%s)" % ",".join(COLS))
con.execute("INSERT INTO payway_poststage_prod_db.abt0_ctx_livemonitoring SELECT * FROM "
            "read_csv('%sabt0.csv',header=false,all_varchar=true)" % SP)
con.execute("UPDATE payway_poststage_prod_db.abt0_ctx_livemonitoring SET "
            "codrespuestaiss=NULLIF(codrespuestaiss,''), nrorechazointerno=NULLIF(nrorechazointerno,'')")

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "desvios_v19.json", encoding="utf-8"))
d["panels"] = [x for q in d["panels"] for x in [q] + (q.get("panels") or [])]
P = {p["id"]: p for p in d["panels"]}


def interp(s, frm, to, vals):
    s = s.replace("$__timeFrom()", "TIMESTAMP '%s'" % frm).replace("$__timeTo()", "TIMESTAMP '%s'" % to)

    def rep(m):
        name, fmt = m.group(1), m.group(2)
        if fmt == "text":
            return vals.get(name + ":text", "All")
        if fmt == "sqlstring":
            return vals.get(name + ":sqlstring", "'x'")
        if fmt == "csv":
            return "x"
        return vals.get(name, "")
    return re.sub(r"\$\{(\w+):(\w+)\}", rep, s)


def run_sql(sql, frm="2026-09-24 10:00:00", to="2026-09-24 16:00:00", res="0", **vals):
    v = {"resolucion": res, "cuit": "", "establecimiento": "", "producto": "$__all",
         "plataforma": "$__all", "modalidad": "$__all"}
    v.update(vals)
    q = interp(sql, frm, to, v)
    # "ahora" fijo: los datos son del 24/09
    q = (q.replace("CAST(current_timestamp AT TIME ZONE 'UTC' AS timestamp)", "TIMESTAMP '2026-09-24 16:00:00'")
          .replace("current_date", "DATE '2026-09-24'"))
    duck = sqlglot.transpile(q.rstrip().rstrip(";"), read="trino", write="duckdb")[0]
    cur = con.execute(duck)
    return [c[0] for c in cur.description], cur.fetchall()


def run(pid, ti=0, **kw):
    return run_sql(P[pid]["targets"][ti]["rawSQL"], **kw)


fallas = []


def check(cond, msg):
    print(("  OK  " if cond else "  XX  ") + msg)
    if not cond:
        fallas.append(msg)


def fila(cols, row):
    return dict(zip(cols, row))


def kpi(**kw):
    c, r = run(1000, **kw)
    return fila(c, r[0])


print("Indicadores: estado contra lo habitual (mediana D-7 · D-14 · D-21)")
k = kpi()
check(k["ultimo_minuto"] == "24/09 12:37 hs", "Datos hasta 12:37 hs AR: ABT a las 15:52 UTC menos 15' de asentamiento")
check(k["atraso_min"] == 8, "atraso de la ABT = 8 min")
check(k["vent"] == 5 and k["n_ref"] == 3, "ventana de 5' y tres días de referencia")
check(k["act_t"] == 175 and k["esp_t"] == 175 and k["dvol"] == 0,
      "volumen 175 contra 175: la mediana ignora el D-14 atípico (375)")
check(k["vol_txt"] == "N|35,0/min · 0%", "Volumen en banda: %s" % k["vol_txt"])
check(k["st"] == "crit" and k["causa"] == "rechazo" and k["estado_txt"] == "C|✖ Más rechazos",
      "estado crítico por rechazos (Paystore al 60 %%): %s" % k["estado_txt"])
check(k["dtaa"] < -10 and k["z_taa"] < -2 and k["taa_txt"].startswith("C|"),
      "aceptación %s %% vs %s %% (z %s): %s" % (k["taa_act"], k["taa_esp"], k["z_taa"], k["taa_txt"]))
check(k["rev_txt"] == "C|6,0/min · ×6,0", "reversos ×6 críticos: %s" % k["rev_txt"])

sql_fer = P[1000]["targets"][0]["rawSQL"].replace("IN ('2026-08-17'", "IN ('2026-09-17','2026-08-17'")
c, r = run_sql(sql_fer)
kf = fila(c, r[0])
check(kf["n_ref"] == 2 and kf["esp_t"] == 275 and round(kf["dvol"]) == -36,
      "si D-7 fue feriado: 2 días, habitual 275 y volumen −36 %% (%s)" % kf["dvol"])
check(kf["vol_txt"].startswith("W|35,0/min · -36%"), "volumen en atención: %s" % kf["vol_txt"])

k = kpi(to="2026-09-24 13:20:00", plataforma="8002")
check(k["estado_txt"] == "C|▼ Sin tráfico" and k["z_vol"] < -7,
      "Decidir caído a las 13:20: %s (z %s)" % (k["estado_txt"], k["z_vol"]))
k = kpi(to="2026-09-24 14:30:00", plataforma="7001")
check(k["estado_txt"] == "C|▼ Sin tráfico" and k["act_t"] == 0 and k["esp_t"] == 25,
      "Pos Legacy (25 tx en 5', bajo el piso de 50) detectado por la regla de 30': %s" % k["estado_txt"])
k = kpi(to="2026-09-24 11:00:00", ventana="15")
check(k["vent"] == 15 and k["act_t"] == 525 and k["estado_txt"] == "N|✔ En banda",
      "ventana de 15' sin incidente: %s" % k["estado_txt"])
check(kpi(to="2026-09-24 11:00:00", ventana="15", cuit="$__all")["act_t"] == 525,
      "CUIT '$__all' (link desde One page) = todos")


def marca(valor, texto):
    return {"marca:text": texto, "marca:sqlstring": "'%s'" % valor}


check(kpi(to="2026-09-24 11:00:00", **marca("PRIVADAS", "Marcas Privadas"))["act_t"] == 25,
      "filtro Marca = Marcas Privadas trae las PC")
check(kpi(to="2026-09-24 11:00:00", **marca("OTRAS", "Otras"))["act_t"] == 0, "filtro Marca = Otras ya no trae las PC")
check(kpi(to="2026-09-24 11:00:00", producto="Marcas Privadas")["act_t"] == 25,
      "filtro Producto = Marcas Privadas trae las PC")

print("Procesamiento (1010) contra lo habitual")
c, r = run(1010, res="1", frm="2026-09-24 13:00:00", to="2026-09-24 13:14:00")
s = {str(x[0])[11:16]: fila(c, x) for x in r}
check(len(r) == 14 and all(s["13:0%d" % m]["Total TRX/min"] == 0 for m in range(5, 10)),
      "caída total 13:05-13:09 en 0, no hueco")
check(s["13:00"]["Habitual TRX/min"] == 35 and s["13:00"]["Habitual mín"] == 35 and s["13:00"]["Habitual máx"] == 75,
      "habitual 35/min (mediana) con rango 35-75 (D-14 atípico solo en la banda)")
check(s["13:00"]["% vs habitual"] == 71.4, "Decidir caído: 71,4 % del habitual")
c, r = run(1010)
check(fila(c, r[-1])["Total TRX/min"] == 35.0 and str(r[-1][0])[11:16] == "15:35",
      "termina en el ancla (15:35-15:38) sin caída falsa")

print("Gateways (1103)")
c, r = run(1103, to="2026-09-24 13:20:00")
g = fila(c, r[0])
check(g["Gateway"] == "Decidir (8002)" and g["Estado"] == "C|▼ Sin tráfico" and g["Silencio (min)"] == 20,
      "a las 13:20 Decidir primero: %s, %s min de silencio" % (g["Estado"], g["Silencio (min)"]))
c, r = run(1103)
g = fila(c, r[0])
check(g["Gateway"] == "Paystore (0001)" and g["Estado"] == "C|✖ Más rechazos",
      "ahora Paystore primero: %s, aceptación %s vs %s" % (g["Estado"], g["Acept."], g["Acept. hab."]))
ok = [fila(c, x) for x in r if fila(c, x)["Gateway"] == "Decidir (8002)"][0]
check(ok["Estado"] == "N|✔ En banda" and abs(ok["z vol"]) < 2, "Decidir en banda (z vol %s)" % ok["z vol"])

print("Paneles del rango contra lo habitual (10, 11 y detalles)")
c, r = run(10, 1, res="15")
h = [fila(c, x) for x in r]
check(all(85 <= x["TAA habitual"] <= 95 for x in h) and all(x["Total habitual"] == 525 for x in h[:-1]),
      "panel 10: TAA habitual ~90 %% y total habitual 525 por bucket (%d buckets)" % len(h))
c, r = run(11, 0, res="15")
dp = {str(x[0])[11:16]: x[1] for x in r}
check(all(abs(v) < 6 for t, v in dp.items() if "10:00" <= t < "15:00" and v is not None)
      and all(v < -10 for t, v in dp.items() if "15:00" <= t < "15:30"),
      "panel 11: Δpp ~0 hasta las 15:00 y muy negativo después")
c, r = run(106, 1, res="15")
check(len(r) > 0 and all(85 <= x[1] <= 95 for x in r), "detalle Marcas Privadas: TAA habitual ~90 %")
check(len(run(105, 1)[1]) == 0, "detalle Naranja: sin habitual (no hay PN)")

print("Todas las consultas")
n = 0
for p in d["panels"]:
    for ti, t in enumerate(p.get("targets") or []):
        if not t.get("rawSQL") or "ctx_autorizaciones_eps" in t["rawSQL"]:
            continue
        for res in ("0", "1", "15", "60"):
            try:
                run(p["id"], ti, res=res)
                n += 1
            except Exception as e:
                fallas.append("panel %s res %s: %s" % (p["id"], res, str(e)[:300]))
esperadas = 4 * sum(1 for p in d["panels"] for t in (p.get("targets") or [])
                    if t.get("rawSQL") and "ctx_autorizaciones_eps" not in t["rawSQL"])
check(n == esperadas, "%d ejecuciones sin error (%d consultas x 4 resoluciones)" % (n, esperadas // 4))

print("\n%s" % ("TODO OK" if not fallas else "FALLAS: %d" % len(fallas)))
for x in fallas:
    print("  -", x)
sys.exit(1 if fallas else 0)
