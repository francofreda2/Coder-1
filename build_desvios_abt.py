#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera "Desvios · v17.0 · ABT" a partir del dashboard operativo v16.8.

Que hace
--------
1) Migra las consultas de payway_poststage_prod_db.ctx_autorizaciones_eps a la
   ABT abt0_ctx_livemonitoring (la misma capa que usa el tablero "One page live
   monitoring"). ABT0 ya trae minuto_utc, cuit y nro_establecimiento resueltos,
   asi que desaparecen el date_parse(concat(...)) por fila y el EXISTS
   correlacionado contra prisma_ab_analytics_prod_db.establecimiento.

2) Agrega los tres atributos que el tablero operativo no tenia y el de live
   monitoring si: Tipo de producto, Plataforma de pago (gateway) y Modalidad.
   Se suman como variables de templating y como predicados en TODOS los paneles.

3) Agrega la exclusion de los 8 CUITs que ya se aplicaba en el resto de los
   tableros.

Que NO toca
-----------
- La clasificacion de cuadrantes / rechazos. En v16.8 es por reglas sobre
  codrespuestaiss y nrorechazointerno (no por catalogo), y ABT0 conserva las dos
  columnas crudas, asi que las expresiones viajan intactas.
- El layout, los tipos de panel, fieldConfig, overrides, transformaciones ni los
  data links: solo se reescribe rawSQL y se agregan variables.

Uso
---
    python3 build_desvios_abt.py
    python3 build_desvios_abt.py --entrada otro.json --salida otro_abt.json
"""

import argparse
import json
import re
import sys

ENTRADA_DEFAULT = "desvios_v16.8_origen.json"
VARS_DEFAULT = "abt_vars_nuevos.json"
SALIDA_DEFAULT = "desvios_v17.0_abt.json"

UID_NUEVO = "desvios-v17-abt"
TITULO_NUEVO = "Desvíos · v17.0 · ABT · CUIT/Establecimiento + Producto/Plataforma/Modalidad"
TAGS_NUEVOS = ["payway", "noc", "desvios", "abt", "v17-0"]
REFRESH_NUEVO = "1m"

CTX = "payway_poststage_prod_db.ctx_autorizaciones_eps"
ABT0 = "payway_poststage_prod_db.abt0_ctx_livemonitoring"

# ---------------------------------------------------------------------------
# 1. Mapeo de columnas CTX -> ABT0
# ---------------------------------------------------------------------------

# El timestamp: v16.8 arma el instante parseando fecha+hora locales y le suma 3
# horas para llevarlo a UTC. ABT0 ya expone minuto_utc en UTC, truncado al
# minuto. Todos los paneles agrupan por bucket de 15' o filtran por rango, asi
# que la perdida de los segundos no cambia ningun resultado.
TS_CTX = ("CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),"
          "'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR")
TS_ABT = "a.minuto_utc"

COLUMNAS = [
    ("a.fecha ", "a.fecha_particion "),                       # columna de particion
    ("a.nroestablecimiento", "a.nro_establecimiento"),
    ("a.codrespuestaautorizacion", "a.cod_respuesta"),
    ("a.codtipomensajeautorizacion", "a.cod_tipo_mensaje"),
    ("a.codprocesoautorizacion1", "a.cod_proceso_autorizacion"),
    ("a.INSTRMTYP", "a.instrmtyp"),
    # codrespuestaiss, nrorechazointerno, codbancoemisor y fill9 se llaman igual
    # en ABT0, no hace falta renombrarlas.
]

# ---------------------------------------------------------------------------
# 2. Filtro por CUIT
# ---------------------------------------------------------------------------
# v16.8 resuelve el CUIT con un EXISTS correlacionado contra la dimension de
# establecimientos, una vez por fila. ABT0 ya trae el CUIT en la propia fila.
CUIT_EXISTS = (
    "(COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR EXISTS(SELECT 1 FROM "
    "prisma_ab_analytics_prod_db.establecimiento e WHERE "
    "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=e.nro_establecimiento AND "
    "TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST('${cuit:raw}' AS BIGINT)))"
)
CUIT_ABT = (
    "(COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR "
    "TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT)"
    "=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT))"
)

# Para el unico panel que se queda en CTX, el EXISTS correlacionado se reescribe
# como semi-join no correlacionado: Presto lo resuelve una sola vez en lugar de
# por fila. Resultado identico, y ademas el CUIT pasa a tolerar guiones.
CUIT_SEMIJOIN = (
    "(COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR "
    "TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN "
    "(SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e "
    "WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)"
    "=TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT)))"
)

# ---------------------------------------------------------------------------
# 3. Ancla de insercion de los filtros nuevos
# ---------------------------------------------------------------------------
# Aparece exactamente una vez en cada uno de los 33 targets, siempre dentro del
# WHERE de la tabla base. Es el punto de insercion.
ANCLA = ("('${establecimiento:raw}'='' OR TRIM(a.nro_establecimiento)="
         "'${establecimiento:raw}')")
ANCLA_CTX = ("('${establecimiento:raw}'='' OR TRIM(a.nroestablecimiento)="
             "'${establecimiento:raw}')")

CUITS_EXCLUIDOS = ("30712334610,30715313681,30717791904,30718438906,"
                   "30716325586,30691399768,33714595569,30709228419")

# Anti-join sobre ABT0: el CUIT viene en la fila.
EXCL_ABT = (
    "\n    AND (TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT) IS NULL"
    " OR TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT) NOT IN (" + CUITS_EXCLUIDOS + "))"
)

# Anti-join sobre CTX: hay que resolver el CUIT contra la dimension. El
# TRY_CAST(...) IS NULL adelante evita perder filas con establecimiento no
# numerico, y el IS NOT NULL adentro evita que un solo NULL vacie el NOT IN.
EXCL_CTX = (
    "\n    AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL"
    " OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN"
    " (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e"
    " WHERE e.nro_establecimiento IS NOT NULL AND"
    " TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN ("
    + CUITS_EXCLUIDOS + ")))"
)

# ---------------------------------------------------------------------------
# 4. Los tres filtros nuevos (mismas expresiones que el tablero de live monitoring)
# ---------------------------------------------------------------------------

def filtros_nuevos(col_estab, col_instrmtyp):
    """Bloque de predicados para Tipo de producto / Plataforma / Modalidad."""
    return (
        "\n    -- FILTRO Tipo de producto"
        "\n    AND ( '${producto:raw}' IN ('-1','','$__all')"
        "\n          OR (CASE"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") IN ('VD','MD','MS') THEN 'Débito'"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") IN ('V6','VB','VN','VC','MC','AX','PA') THEN 'Crédito'"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") IN ('VK','SR','MK') THEN 'Prepaga'"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") = 'PB' THEN 'Marcas Privadas'"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") = 'PN' THEN 'Naranja Pura'"
        "\n                   WHEN TRIM(" + col_instrmtyp + ") = 'UC' THEN 'Indeterminado (Créd/Déb)'"
        "\n                   ELSE 'Otro'"
        "\n              END) = '${producto:raw}' )"
        "\n    -- FILTRO Plataforma de pago (la variable trae el sufijo de 4 digitos)"
        "\n    AND ( '${plataforma:raw}' IN ('-1','','$__all')"
        "\n          OR contains(split('${plataforma:raw}', ','),"
        "\n                      IF(length(TRIM(COALESCE(a.programaperiferiaorigen,''))) >= 4,"
        "\n                         substr(TRIM(a.programaperiferiaorigen), -4),"
        "\n                         TRIM(COALESCE(a.programaperiferiaorigen,'')))) )"
        "\n    -- FILTRO Modalidad"
        "\n    AND ( '${modalidad:raw}' IN ('-1','','$__all')"
        "\n          OR (CASE"
        "\n                   WHEN TRIM(COALESCE(a.coddebitoautomatico,'')) = '1'"
        "\n                     OR ( dafil.nro_establecimiento IS NOT NULL"
        "\n                          AND TRIM(COALESCE(a.codmetodoingresomovimiento,'')) IN ('0','1') )"
        "\n                        THEN 'Débito Automático'"
        "\n                   WHEN TRIM(COALESCE(a.codidentpresenciacliente,'')) = '8'"
        "\n                        THEN 'E-commerce'"
        "\n                   WHEN TRIM(COALESCE(a.codidentpresenciacliente,'')) <> ''"
        "\n                        THEN 'Presente'"
        "\n                   ELSE 'Indeterminado'"
        "\n              END) = '${modalidad:raw}' )"
    ).replace("__COL_ESTAB__", col_estab)


# El LEFT JOIN que marca los establecimientos con debito automatico habilitado.
# Se necesita para la rama 'Débito Automático' de la modalidad.
def join_dafil(col_estab):
    return (
        " LEFT JOIN (SELECT DISTINCT e.nro_establecimiento FROM "
        "prisma_ab_analytics_prod_db.establecimiento e WHERE "
        "TRIM(COALESCE(e.cod_debito_automatico_estab,'')) = '1') dafil"
        " ON dafil.nro_establecimiento = TRY_CAST(TRIM(" + col_estab + ") AS INTEGER)"
    )


# ---------------------------------------------------------------------------
# Transformacion de un rawSQL
# ---------------------------------------------------------------------------

def migrar_a_abt(sql):
    """CTX -> ABT0. Devuelve (sql, cambios) o (sql, None) si no aplica."""
    if CTX not in sql:
        return sql, None
    n = {}
    n["ts"] = sql.count(TS_CTX)
    sql = sql.replace(TS_CTX, TS_ABT)

    n["cuit"] = sql.count(CUIT_EXISTS)
    sql = sql.replace(CUIT_EXISTS, CUIT_ABT)

    n["cols"] = 0
    for viejo, nuevo in COLUMNAS:
        n["cols"] += sql.count(viejo)
        sql = sql.replace(viejo, nuevo)

    n["tabla"] = sql.count(CTX)
    sql = sql.replace(CTX, ABT0)
    return sql, n


def insertar_join(sql, tabla, col_estab):
    """Mete el LEFT JOIN dafil justo despues de la tabla base y su alias."""
    pat = re.compile(re.escape(tabla) + r"(\s+)a\b")
    m = pat.search(sql)
    if not m:
        return sql, 0
    ins = join_dafil(col_estab)
    return sql[:m.end()] + ins + sql[m.end():], 1


def transformar(sql, sobre_abt):
    """Aplica migracion (si corresponde), filtros nuevos y exclusion de CUITs."""
    info = {}
    if sobre_abt:
        sql, info["migracion"] = migrar_a_abt(sql)
        tabla, col_estab, col_instr = ABT0, "a.nro_establecimiento", "a.instrmtyp"
        ancla, excl = ANCLA, EXCL_ABT
    else:
        info["migracion"] = None
        tabla, col_estab, col_instr = CTX, "a.nroestablecimiento", "a.INSTRMTYP"
        ancla, excl = ANCLA_CTX, EXCL_CTX
        info["semijoin"] = sql.count(CUIT_EXISTS)
        sql = sql.replace(CUIT_EXISTS, CUIT_SEMIJOIN)

    sql, info["join"] = insertar_join(sql, tabla, col_estab)

    if sql.count(ancla) != 1:
        raise RuntimeError(
            "ancla de insercion encontrada %d veces (se esperaba 1)" % sql.count(ancla))
    sql = sql.replace(ancla, ancla + excl + filtros_nuevos(col_estab, col_instr))
    info["filtros"] = 1
    return sql, info


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", default=ENTRADA_DEFAULT)
    ap.add_argument("--vars", default=VARS_DEFAULT)
    ap.add_argument("--salida", default=SALIDA_DEFAULT)
    args = ap.parse_args()

    with open(args.entrada, encoding="utf-8") as f:
        dash = json.load(f)
    with open(args.vars, encoding="utf-8") as f:
        vars_nuevas = json.load(f)

    # --- paneles ---------------------------------------------------------
    n_abt = n_ctx = 0
    for panel in dash.get("panels", []):
        for tgt in panel.get("targets", []) or []:
            sql = tgt.get("rawSQL")
            if not sql:
                continue
            # El unico panel que no puede viajar a ABT0 es el de BINes: necesita
            # el PAN y ABT0 lo entrega enmascarado. Se queda en CTX y conserva
            # todos los filtros, incluidos los tres nuevos.
            sobre_abt = "a.nrotarjeta" not in sql
            nuevo, info = transformar(sql, sobre_abt)
            tgt["rawSQL"] = nuevo
            if sobre_abt:
                n_abt += 1
            else:
                n_ctx += 1
                panel["description"] = (
                    (panel.get("description") or "").rstrip() + "\n\n"
                    "NOTA: este panel sigue leyendo ctx_autorizaciones_eps porque "
                    "necesita el PAN para armar el BIN de 8 digitos, y ABT0 lo "
                    "entrega enmascarado. Respeta todos los filtros, incluidos "
                    "Tipo de producto, Plataforma de pago y Modalidad."
                ).strip()

    # --- templating ------------------------------------------------------
    lista = dash.setdefault("templating", {}).setdefault("list", [])
    existentes = {v.get("name") for v in lista}
    agregadas = []
    for v in vars_nuevas:
        if v["name"] not in existentes:
            lista.append(v)
            agregadas.append(v["name"])

    # --- metadatos -------------------------------------------------------
    dash["uid"] = UID_NUEVO
    dash["title"] = TITULO_NUEVO
    dash["tags"] = TAGS_NUEVOS
    dash["refresh"] = REFRESH_NUEVO
    dash["version"] = 1
    dash.pop("id", None)

    # Reuso de resultados de Athena: si dos paneles piden lo mismo dentro de la
    # ventana, Athena devuelve el resultado cacheado en lugar de re-escanear.
    n_reuse = 0
    for panel in dash.get("panels", []):
        for tgt in panel.get("targets", []) or []:
            ca = tgt.get("connectionArgs")
            if isinstance(ca, dict):
                ca["resultReuseEnabled"] = True
                ca["resultReuseMaxAgeInMinutes"] = 5
                n_reuse += 1

    salida = json.dumps(dash, ensure_ascii=False, indent=2)
    json.loads(salida)
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(salida)

    print("OK")
    print("  entrada:                %s" % args.entrada)
    print("  targets migrados a ABT0: %d" % n_abt)
    print("  targets que siguen en CTX: %d" % n_ctx)
    print("  variables agregadas:     %s" % (", ".join(agregadas) or "ninguna"))
    print("  targets con result reuse: %d" % n_reuse)
    print("  salida:                 %s" % args.salida)


if __name__ == "__main__":
    sys.exit(main())
