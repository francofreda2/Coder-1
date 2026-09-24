#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de ejecucion de desvios_v17.1_noc.json sobre datos sinteticos.

Arma una ABT0 falsa en DuckDB con un incidente conocido y corre el SQL real de
los paneles (traducido de Trino a DuckDB con sqlglot):

  - 3 gateways: Paystore (0001) 20 TRX/min, Decidir (8002) 10, Pos Legacy (7001) 5.
  - 24/09 13:05-13:10 UTC: caida total (cero transacciones).
  - 24/09 13:00-13:40 UTC: Decidir sin trafico.
  - 24/09 desde 15:00 UTC: Paystore con TAA degradada (60%).
  - La ABT llega hasta 15:52 UTC; "ahora" = 16:00 UTC (8 min de atraso).

    pip install duckdb sqlglot
    python3 test_desvios_noc.py

El panel 38 (lee ctx_autorizaciones_eps) no se ejecuta: esa tabla no existe aca.
"""
import os
import tempfile
import json,re,sys,random,csv,datetime as dt
import duckdb,sqlglot
random.seed(7)
SP=tempfile.mkdtemp()+os.sep
con=duckdb.connect()
con.execute("CREATE SCHEMA payway_poststage_prod_db; CREATE SCHEMA prisma_ab_analytics_prod_db;")
con.execute("CREATE TABLE prisma_ab_analytics_prod_db.establecimiento(nro_establecimiento INTEGER, cuit_establecimiento_host VARCHAR, cod_debito_automatico_estab VARCHAR)")
con.execute("INSERT INTO prisma_ab_analytics_prod_db.establecimiento VALUES (111,'30-11111111-1','0'),(222,'30-22222222-2','1')")
COLS=["minuto_utc TIMESTAMP","fecha_particion VARCHAR","cuit VARCHAR","nro_establecimiento VARCHAR","marca VARCHAR","instrmtyp VARCHAR",
 "cod_respuesta VARCHAR","cod_tipo_mensaje VARCHAR","cod_proceso_autorizacion VARCHAR","codrespuestaiss VARCHAR","nrorechazointerno VARCHAR",
 "codbancoemisor VARCHAR","programaperiferiaorigen VARCHAR","coddebitoautomatico VARCHAR","codidentpresenciacliente VARCHAR",
 "codmetodoingresomovimiento VARCHAR","latencia_ms BIGINT","fill9 VARCHAR"]
GM=dt.datetime(2026,9,24,15,52)          # ultimo minuto cargado en la ABT
rate={"0001":20,"8002":10,"7001":5}
with open(SP+"abt0.csv","w",newline="") as f:
    w=csv.writer(f); n=0
    t=dt.datetime(2026,9,22,20,0)
    while t<=GM:
        today=t.date()==dt.date(2026,9,24)
        fp=(t-dt.timedelta(hours=3)).strftime("%Y-%m-%d")
        for gw,r in rate.items():
            k=r
            if today and dt.datetime(2026,9,24,13,5)<=t<dt.datetime(2026,9,24,13,10): k=0            # caida total 5'
            if today and gw=="8002" and dt.datetime(2026,9,24,13,0)<=t<dt.datetime(2026,9,24,13,40): k=0  # Decidir caido 40'
            for i in range(k):
                p=0.6 if (today and gw=="0001" and t>=dt.datetime(2026,9,24,15,0)) else 0.9
                ok=random.random()<p
                w.writerow([t,fp,"30111111111","111","VISA","VC","0000" if ok else "0051","1100","00","" if ok else "51","","7","APP"+gw,"0","8" if gw=="8002" else "1","1",random.randint(200,900),"x"]); n+=1
        if not (today and dt.datetime(2026,9,24,13,5)<=t<dt.datetime(2026,9,24,13,10)):
            w.writerow([t,fp,"30111111111","111","VISA","VC","0000","1420","00","","","7","APP0001","0","1","1",300,"x"]); n+=1
        t+=dt.timedelta(minutes=1)
con.execute("CREATE TABLE payway_poststage_prod_db.abt0_ctx_livemonitoring(%s)"%",".join(COLS))
con.execute("INSERT INTO payway_poststage_prod_db.abt0_ctx_livemonitoring SELECT * FROM read_csv('%sabt0.csv',header=false,all_varchar=true)"%SP)
con.execute("UPDATE payway_poststage_prod_db.abt0_ctx_livemonitoring SET codrespuestaiss=NULLIF(codrespuestaiss,''), nrorechazointerno=NULLIF(nrorechazointerno,'')")


d=json.load(open("desvios_v17.1_noc.json",encoding="utf-8"))
P={p["id"]:p for p in d["panels"]}
def interp(s,frm,to,vals):
    s=s.replace("$__timeFrom()","TIMESTAMP '%s'"%frm).replace("$__timeTo()","TIMESTAMP '%s'"%to)
    def rep(m):
        name,fmt=m.group(1),m.group(2)
        if fmt=="text": return "All"
        if fmt=="sqlstring": return "'x'"
        if fmt=="csv": return "x"
        return vals.get(name,"")
    return re.sub(r"\$\{(\w+):(\w+)\}",rep,s)
def run(pid,ti=0,res="0",frm="2026-09-24 10:00:00",to="2026-09-24 16:00:00",**vals):
    v={"resolucion":res,"cuit":"","establecimiento":"","producto":"$__all","plataforma":"$__all","modalidad":"$__all"}; v.update(vals)
    q=interp(P[pid]["targets"][ti]["rawSQL"],frm,to,v)
    q=q.replace("CAST(current_timestamp AT TIME ZONE 'UTC' AS timestamp)","TIMESTAMP '2026-09-24 16:00:00'")
    duck=sqlglot.transpile(q.rstrip().rstrip(";"),read="trino",write="duckdb")[0]
    cur=con.execute(duck)
    cols=[c[0] for c in cur.description]
    return cols,cur.fetchall()
def show(cols,rows,lim=None):
    print(" | ".join(cols))
    for r in rows[:lim]: print(" | ".join("" if x is None else str(x) for x in r))

fallas = []


def check(cond, msg):
    print(("  OK  " if cond else "  XX  ") + msg)
    if not cond:
        fallas.append(msg)


def fila(cols, row):
    return dict(zip(cols, row))


print("KPIs")
k = fila(*[(c, r[0]) for c, r in [run(1000)]][0])
check(k["ultimo_minuto"] == "24/09 12:51 hs", "Datos hasta = 12:51 hs AR (ABT 15:52 UTC menos el minuto en carga)")
check(k["atraso_min"] == 8, "atraso de la ABT = 8 min")
check(k["resolucion"] == "5 min", "rango de 6 h en auto = 5 min")
check(k["trx_min"] == 35.0, "35 TRX/min (sin reversos)")
check(k["pct_vs_d1"] == 100.0, "100% vs D-1 fuera del incidente")
check(k["silencio_min"] == 0, "silencio 0 sin filtros")
c, r = run(1000, to="2026-09-24 13:20:00", plataforma="8002")
k = fila(c, r[0])
check(k["trx_min"] == 0.0 and k["pct_vs_d1"] == 0.0, "Decidir durante el incidente: 0 TRX/min y 0% vs D-1")
check(k["silencio_min"] == 20, "Decidir: 20 min de silencio a las 13:20")

print("Procesamiento (1010)")
c, r = run(1010, res="1", frm="2026-09-24 13:00:00", to="2026-09-24 13:14:00")
serie = {str(x[0])[11:16]: fila(c, x) for x in r}
check(len(r) == 14, "14 buckets de 1 min: los minutos sin trafico existen")
check(all(serie["13:0%d" % m]["Total TRX/min"] == 0.0 for m in range(5, 10)),
      "caida total 13:05-13:09 = 0, no hueco")
check(serie["13:00"]["% vs D-1"] == 71.4, "Decidir caido: 71,4% vs D-1")
c, r = run(1010)
check(fila(c, r[-1])["Total TRX/min"] == 35.0, "ultimo bucket parcial no dibuja caida falsa")

print("Gateways (1101 / 1103)")
c, r = run(1101, res="5", frm="2026-09-24 12:30:00", to="2026-09-24 14:00:00")
dec = {str(x[0])[11:16]: x[2] for x in r if x[1] == "Decidir (8002)"}
check(dec["13:00"] == 0.0 and dec["13:35"] == 0.0 and dec["13:40"] == 10.0,
      "Decidir baja a 0 a las 13:00 y vuelve a las 13:40")
c, r = run(1103, to="2026-09-24 13:20:00")
top = fila(c, r[0])
check(top["Gateway"] == "Decidir (8002)" and top["% vs D-1"] == 0.0 and top["Silencio (min)"] == 20,
      "tabla: Decidir arriba, 0% vs D-1, 20 min de silencio")
c, r = run(1103)
pay = [fila(c, x) for x in r if x[0] == "Paystore (0001)"][0]
check(pay["Δ TAA (pp)"] < -20, "tabla: Paystore con TAA degradada (Δ < -20 pp)")

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
                fallas.append("panel %s res %s: %s" % (p["id"], res, str(e)[:200]))
check(n == 144, "%d ejecuciones sin error (36 consultas x 4 resoluciones)" % n)

print("\n%s" % ("TODO OK" if not fallas else "FALLAS: %d" % len(fallas)))
sys.exit(1 if fallas else 0)
