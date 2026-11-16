#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compara el estado que calcula el SQL de logica_desvios.estado_ctes() contra la logica de las
alertas (evaluar() del onRender de «Salud de los Clientes» v0.2, portada linea por linea a
Python), sobre miles de casos al azar. Requiere: pip install duckdb sqlglot

    python3 tests_logica/test_estado_vs_alertas.py
"""
import math, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import duckdb, sqlglot
import logica_desvios as ld

LIM_VOL, LIM_TAA, DEN_X, REV_X = ld.LIM_VOL, ld.LIM_TAA, ld.DEN_X, ld.REV_X
PISO, Z_MIN, Z_DEN = ld.PISO, ld.Z_MIN, ld.Z_DEN

def mediana(a):
    v = sorted(x for x in a if x is not None)
    if not v: return None
    return v[(len(v)-1)//2] if len(v) % 2 else (v[len(v)//2-1] + v[len(v)//2]) / 2

def significancia(act, esp, ta, te):
    zv = (act-esp)/math.sqrt(esp) if (esp is not None and esp > 0) else None
    zt = None
    if ta is not None and te is not None and act > 0:
        p = min(max(te/100, 0.005), 0.995); zt = ((ta-te)/100)/math.sqrt(p*(1-p)/act)
    return dict(baja=zv is None or zv <= -Z_MIN, taa=zt is None or zt <= -Z_MIN, suba=zv is None or zv >= Z_MIN)

def exceso(act, esp, mn):
    if esp is None: return dict(exc=None, x=None, real=False)
    exc = act-esp; x = act/max(esp, 1); z = exc/math.sqrt(2*max(esp, 1))
    return dict(exc=exc, x=x, real=exc >= mn and z >= Z_DEN)

def evaluar(c):
    """evaluar(k) de las alertas. dias = los dias con dato y no feriados (los None)."""
    vent = c["vent"]; MIN_EXC, MIN_REV = 2*vent, vent
    dias = [i for i in (1, 2, 3) if c["r%d_t" % i] is not None]
    act_t, act_a, act_r = c["act_t"], c["act_a"], c["act_r"]; act_d = act_t-act_a
    esp_t = mediana([c["r%d_t" % i] for i in dias]); esp_d = mediana([c["r%d_t" % i]-c["r%d_a" % i] for i in dias])
    esp_r = mediana([c["r%d_r" % i] for i in dias])
    taa = lambda a, t: 100*a/t if t > 0 else None
    ta, te = taa(act_a, act_t), mediana([taa(c["r%d_a" % i], c["r%d_t" % i]) for i in dias])
    D, R = exceso(act_d, esp_d, MIN_EXC), exceso(act_r, esp_r, MIN_REV)
    st, causa = "ok", None
    if not (esp_t is not None and esp_t >= PISO) and act_t < PISO: st = "nd"
    else:
        dvol = 100*act_t/esp_t-100 if esp_t else None
        dtaa = ta-te if ta is not None and te is not None else None
        sig = significancia(act_t, esp_t, ta, te)
        excT = None if te is None else act_d-act_t*(1-te/100)
        bV = lambda i: dvol is not None and dvol <= -LIM_VOL[i] and sig["baja"]
        bT = lambda i: dtaa is not None and dtaa <= -LIM_TAA[i] and sig["taa"] and excT is not None and excT >= MIN_EXC
        bD = lambda i: D["real"] and D["x"] >= DEN_X[i]
        bR = lambda i: R["real"] and R["x"] >= REV_X[i]
        if esp_t is None or esp_t == 0: st = "info" if act_t >= PISO else "nd"
        elif bV(1) or bT(1) or bD(1) or bR(1): st = "crit"
        elif bV(0) or bT(0) or bD(0) or bR(0): st = "warn"
        elif dvol >= LIM_VOL[0] and sig["suba"]: st = "info"
        if st in ("crit", "warn"):
            i = 1 if st == "crit" else 0; v, r, rv = bV(i), bT(i) or bD(i), bR(i)
            causa = ("volumen" if v and not r else "rechazo" if r and not v else
                     ("volumen" if dvol is not None and dvol <= -LIM_VOL[1] else "rechazo") if v and r else
                     "reversos" if rv else "rechazo")
    # chica30 (dias = los mismos)
    if st == "nd" and dias:
        e30 = mediana([c["r%d_30" % i] for i in dias])
        if e30 is not None and e30 >= 8:
            a30 = c["act30"]; z = (a30-e30)/math.sqrt(e30)
            if a30 == 0 or (a30 <= e30/3 and z <= -3):
                st = "crit" if a30 == 0 else "warn"; causa = "volumen"
    return st, causa

def caso(rnd):
    vent = rnd.choice([5, 15]); base = rnd.choice([0, 5, 20, 60, 300, 3000])
    taa_h = rnd.uniform(0.6, 0.99); c = {"vent": vent}
    for i in (1, 2, 3):
        if rnd.random() < 0.15: c.update({"r%d_t" % i: None, "r%d_a" % i: None, "r%d_r" % i: None, "r%d_30" % i: None}); continue
        t = max(0, int(rnd.gauss(base, math.sqrt(base)+1)))
        c.update({"r%d_t" % i: t, "r%d_a" % i: int(t*taa_h), "r%d_r" % i: int(t*rnd.uniform(0, 0.03)),
                  "r%d_30" % i: int(t*30/vent)})
    fv = rnd.choice([1, 1, 0.9, 0.6, 0.3, 0, 1.5]); ft = rnd.choice([1, 1, 0.97, 0.9, 0.7])
    t = max(0, int(rnd.gauss(base*fv, math.sqrt(base)+1))); a = int(t*taa_h*ft)
    r = int(t*rnd.choice([0.01, 0.01, 0.1, 0.3]))
    c.update(act_t=t, act_a=min(a, t), act_r=r, act30=int(t*30/vent*rnd.choice([1, 1, 0])))
    return c

def main():
    rnd = random.Random(20261116); casos = [caso(rnd) for _ in range(5000)]
    con = duckdb.connect()
    cols = ["id", "vent", "act_t", "act_a", "act_r", "act30"] + ["r%d_%s" % (i, m) for i in (1, 2, 3) for m in ("t", "a", "r", "30")]
    con.execute("CREATE TABLE src (%s)" % ", ".join("%s BIGINT" % c for c in cols))
    con.executemany("INSERT INTO src VALUES (%s)" % ",".join("?"*len(cols)), [[n]+[c[k] for k in cols[1:]] for n, c in enumerate(casos)])
    sql = "WITH " + ld.estado_ctes("src") + " SELECT id, st, causa FROM est ORDER BY id"
    duck = sqlglot.transpile(sql, read="trino", write="duckdb")[0]
    filas = con.execute(duck).fetchall()
    malos = [(n, casos[n], (st, causa), evaluar(casos[n])) for n, st, causa in filas if (st, causa) != evaluar(casos[n])]
    dist = {}
    for n, st, causa in filas: dist[(st, causa)] = dist.get((st, causa), 0)+1
    print("casos: %d · distintos: %d" % (len(filas), len(malos)))
    print("estados cubiertos:", sorted(dist.items(), key=lambda x: -x[1]))
    for m in malos[:5]: print("  ", m)
    sys.exit(1 if malos else 0)

if __name__ == "__main__":
    main()
