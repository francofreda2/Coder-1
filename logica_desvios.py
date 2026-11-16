# -*- coding: utf-8 -*-
"""
Logica de desvios de las alertas de Observabilidad: la misma que usan «Salud de los
Clientes» y «Salud de las Conexiones» (gen_salud_clientes.py v0.4, 02/10/2026).

Lo habitual es la MEDIANA de la misma franja horaria en D-7, D-14 y D-21 (el mismo dia de
las tres semanas anteriores), sacando los dias que fueron feriado. Un desvio cuenta solo si
supera el umbral Y es estadisticamente significativo (z), asi un segmento chico no se marca
por ruido:

  volumen      z = (act - esp) / sqrt(esp)                      (Poisson)
  aceptacion   z = (taa_act - taa_esp) / sqrt(p (1 - p) / act)  (binomial, p = taa_esp)
  denegadas    exceso = act - esp, x = act / esp, z = exceso / sqrt(2 esp)
  reversos     igual que denegadas

Unica fuente para los generadores de este repo.
"""

REF_HORAS = (168, 336, 504)            # D-7, D-14, D-21
REF_TXT = "D-7 · D-14 · D-21"
FERIADOS = ["2026-08-17", "2026-10-12", "2026-11-23", "2026-12-08", "2026-12-25",
            "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-24", "2027-03-25",
            "2027-03-26", "2027-04-02", "2027-05-01", "2027-05-25", "2027-06-20",
            "2027-06-21", "2027-07-09", "2027-08-16", "2027-10-11", "2027-11-20",
            "2027-12-08", "2027-12-25"]

LIM_VOL = (30, 60)      # % de caida de volumen: atencion, critico
LIM_TAA = (1.5, 4)      # pp de caida de aceptacion
DEN_X = (2, 4)          # veces lo habitual de denegadas
REV_X = (3, 6)          # veces lo habitual de reversos
PISO = 50               # tx en la ventana para evaluar
Z_MIN = 2               # volumen y aceptacion
Z_DEN = 3               # denegadas y reversos

ASENTAMIENTO_MIN = 15   # los ultimos minutos que carga el ETL de ABT0 llegan incompletos
ATRASO_MAX_MIN = 60     # mas atras que esto, se ancla en ahora - 60' y se avisa
LAG_ALERTA_MIN = 30     # atraso a partir del cual el estado dice DATO ATRASADO
CHICA_MIN = 30          # un segmento chico que deja de operar se mira en 30'
CHICA_ESP = 8           # ... si en 30' suele tener al menos esto

TIPOS = {"rech": ("✖", "Más rechazos"), "rev": ("↺", "Más reversos"),
         "vol": ("▼", "Menos tráfico"), "cero": ("▼", "Sin tráfico"),
         "info": ("▲", "Más tráfico"), "ok": ("✔", "En banda"), "nd": ("○", "Sin evaluar")}

# Color por severidad. Los textos que devuelve el SQL llevan adelante la letra de la
# severidad («C|…»); los mapeos de Grafana la sacan y pintan el valor.
COLORES = {"C": "#CF4360", "W": "#E7B432", "I": "#3B82F6", "N": "#22C55E", "D": "#8b93a7"}
LETRA = {"crit": "C", "warn": "W", "info": "I", "ok": "N", "nd": "D"}


def mapeos_severidad():
    return [{"type": "regex", "options": {"pattern": "^%s\\|(.*)$" % l,
                                          "result": {"text": "$1", "color": c, "index": i}}}
            for i, (l, c) in enumerate(COLORES.items())]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def es_feriado(ts):
    """El dia local (UTC-3) de `ts` fue feriado."""
    return "(date_format(%s - INTERVAL '3' HOUR, '%%Y-%%m-%%d') IN (%s))" % (
        ts, ",".join("'%s'" % f for f in FERIADOS))


def arr(*xs):
    """Arreglo ordenado de los valores no nulos (los dias excluidos van como NULL)."""
    return "array_sort(filter(ARRAY[%s], v -> v IS NOT NULL))" % ", ".join(xs)


def med(a):
    """Mediana de un arreglo ordenado de hasta 3 elementos."""
    return ("(CASE cardinality({a}) WHEN 0 THEN NULL WHEN 1 THEN element_at({a}, 1) "
            "WHEN 2 THEN (element_at({a}, 1) + element_at({a}, 2)) / 2.0 "
            "ELSE element_at({a}, 2) END)").format(a=a)


def num(x, dec=1):
    return "replace(CAST(CAST(ROUND(%s, %d) AS DECIMAL(18,%d)) AS VARCHAR), '.', ',')" % (x, dec, dec)


def con_signo(x, dec=1):
    return "(CASE WHEN %s > 0 THEN '+' ELSE '' END || %s)" % (x, num(x, dec))


def con_sev(sev, texto):
    letra = " ".join("WHEN '%s' THEN '%s'" % (k, v) for k, v in LETRA.items())
    return "((CASE %s %s ELSE 'D' END) || '|' || %s)" % (sev, letra, texto)


def estado_ctes(src):
    """CTEs que evaluan el estado de cada fila de `src`, con las reglas de las alertas.

    `src` trae: vent (minutos de la ventana), act_t, act_a, act_r (sin reversos, aprobadas,
    reversos) y act30; y por cada dia de referencia k = 1, 2, 3: r{k}_t, r{k}_a, r{k}_r, r{k}_30,
    en NULL si ese dia no cuenta (feriado o sin datos). La ultima CTE se llama `est`.
    """
    L, T, D, R = LIM_VOL, LIM_TAA, DEN_X, REV_X
    k = (1, 2, 3)
    p = "LEAST(GREATEST(taa_esp / 100, 0.005), 0.995)"
    return f"""ez1 AS (SELECT s.*, s.act_t - s.act_a AS act_d,
    {arr(*['s.r%d_t' % i for i in k])} AS ar_t,
    {arr(*['s.r%d_t - s.r%d_a' % (i, i) for i in k])} AS ar_d,
    {arr(*['s.r%d_r' % i for i in k])} AS ar_r,
    {arr(*['IF(s.r%d_t > 0, 100.0 * s.r%d_a / s.r%d_t)' % (i, i, i) for i in k])} AS ar_taa,
    {arr(*['s.r%d_30' % i for i in k])} AS ar_30,
    2 * s.vent AS min_exc, s.vent AS min_rev
  FROM {src} s),
ez2 AS (SELECT ez1.*, {med('ar_t')} AS esp_t, {med('ar_d')} AS esp_d, {med('ar_r')} AS esp_r,
    {med('ar_taa')} AS taa_esp, {med('ar_30')} AS esp_30, cardinality(ar_t) AS n_ref,
    IF(act_t > 0, 100.0 * act_a / act_t) AS taa_act
  FROM ez1),
ez3 AS (SELECT ez2.*,
    IF(esp_t > 0, 100.0 * act_t / esp_t - 100) AS dvol,
    taa_act - taa_esp AS dtaa,
    IF(esp_t > 0, (act_t - esp_t) / sqrt(esp_t)) AS z_vol,
    IF(taa_act IS NOT NULL AND taa_esp IS NOT NULL AND act_t > 0,
       ((taa_act - taa_esp) / 100) / sqrt({p} * (1 - {p}) / act_t)) AS z_taa,
    act_d - act_t * (1 - taa_esp / 100) AS exc_t,
    act_d - esp_d AS exc_d, act_d / GREATEST(esp_d, 1) AS x_d, (act_d - esp_d) / sqrt(2 * GREATEST(esp_d, 1)) AS z_d,
    act_r - esp_r AS exc_r, act_r / GREATEST(esp_r, 1) AS x_r, (act_r - esp_r) / sqrt(2 * GREATEST(esp_r, 1)) AS z_r
  FROM ez2),
ez4 AS (SELECT ez3.*,
    (z_vol IS NULL OR z_vol <= -{Z_MIN}) AS sig_baja,
    (z_taa IS NULL OR z_taa <= -{Z_MIN}) AS sig_taa,
    (z_vol IS NULL OR z_vol >= {Z_MIN}) AS sig_suba,
    COALESCE(exc_d >= min_exc AND z_d >= {Z_DEN}, false) AS den_real,
    COALESCE(exc_r >= min_rev AND z_r >= {Z_DEN}, false) AS rev_real
  FROM ez3),
ez5 AS (SELECT ez4.*,
    COALESCE(dvol <= -{L[1]} AND sig_baja, false) AS bv1, COALESCE(dvol <= -{L[0]} AND sig_baja, false) AS bv0,
    COALESCE(dtaa <= -{T[1]} AND sig_taa AND exc_t >= min_exc, false) AS bt1,
    COALESCE(dtaa <= -{T[0]} AND sig_taa AND exc_t >= min_exc, false) AS bt0,
    COALESCE(den_real AND x_d >= {D[1]}, false) AS bd1, COALESCE(den_real AND x_d >= {D[0]}, false) AS bd0,
    COALESCE(rev_real AND x_r >= {R[1]}, false) AS br1, COALESCE(rev_real AND x_r >= {R[0]}, false) AS br0
  FROM ez4),
ez6 AS (SELECT ez5.*,
    CASE WHEN COALESCE(esp_t < {PISO}, true) AND act_t < {PISO} THEN 'nd'
         WHEN esp_t IS NULL OR esp_t = 0 THEN IF(act_t >= {PISO}, 'info', 'nd')
         WHEN bv1 OR bt1 OR bd1 OR br1 THEN 'crit'
         WHEN bv0 OR bt0 OR bd0 OR br0 THEN 'warn'
         WHEN dvol >= {L[0]} AND sig_suba THEN 'info'
         ELSE 'ok' END AS st0
  FROM ez5),
ez7 AS (SELECT ez6.*,
    -- un segmento chico que deja de operar: se mira en {CHICA_MIN}'
    COALESCE(st0 = 'nd' AND esp_30 >= {CHICA_ESP}
             AND (act30 = 0 OR (act30 <= esp_30 / 3 AND (act30 - esp_30) / sqrt(esp_30) <= -3)), false) AS chica,
    IF(st0 = 'crit', bv1, bv0) AS ej_v, IF(st0 = 'crit', bt1 OR bd1, bt0 OR bd0) AS ej_r, IF(st0 = 'crit', br1, br0) AS ej_rv
  FROM ez6),
ez8 AS (SELECT ez7.*,
    CASE WHEN chica THEN IF(act30 = 0, 'crit', 'warn') ELSE st0 END AS st,
    CASE WHEN chica THEN 'volumen'
         WHEN st0 NOT IN ('crit', 'warn') THEN NULL
         WHEN ej_v AND NOT ej_r THEN 'volumen'
         WHEN ej_r AND NOT ej_v THEN 'rechazo'
         WHEN ej_v AND ej_r THEN IF(dvol <= -{L[1]}, 'volumen', 'rechazo')
         WHEN ej_rv THEN 'reversos'
         ELSE 'rechazo' END AS causa,
    IF(chica, 100.0 * act30 / esp_30 - 100, dvol) AS dvol_v
  FROM ez7),
est AS (SELECT ez8.*,
    CASE WHEN st IN ('nd', 'ok', 'info') THEN st
         WHEN causa = 'rechazo' THEN 'rech' WHEN causa = 'reversos' THEN 'rev'
         WHEN (chica AND act30 = 0) OR (NOT chica AND act_t = 0) THEN 'cero' ELSE 'vol' END AS tipo,
    CASE WHEN st = 'nd' AND NOT chica THEN 'nd' WHEN chica THEN st WHEN bv1 THEN 'crit' WHEN bv0 THEN 'warn'
         WHEN COALESCE(esp_t > 0 AND dvol >= {L[0]} AND sig_suba, false) OR ((esp_t IS NULL OR esp_t = 0) AND act_t >= {PISO}) THEN 'info'
         ELSE 'ok' END AS sev_vol,
    CASE WHEN st = 'nd' THEN 'nd' WHEN bt1 THEN 'crit' WHEN bt0 THEN 'warn' ELSE 'ok' END AS sev_taa,
    CASE WHEN st = 'nd' THEN 'nd' WHEN bd1 THEN 'crit' WHEN bd0 THEN 'warn' ELSE 'ok' END AS sev_den,
    CASE WHEN st = 'nd' THEN 'nd' WHEN br1 THEN 'crit' WHEN br0 THEN 'warn' ELSE 'ok' END AS sev_rev
  FROM ez8)"""


def texto_tipo(tipo):
    return "(CASE %s %s END)" % (tipo, " ".join(
        "WHEN '%s' THEN '%s %s'" % (t, i, x) for t, (i, x) in TIPOS.items()))
