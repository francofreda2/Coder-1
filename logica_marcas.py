# -*- coding: utf-8 -*-
"""
Marca y tipo de producto por tipo de tarjeta (instrmtyp): lista comun del
02/10/2026 (8-LOGICA-COMPARTIDA/marcas/MARCAS_por_instrmtyp.sql).

Unica fuente para los generadores de este repo: el filtro y el desglose tienen
que usar la misma lista. Si no, filtrar "Marcas Privadas" trae una cosa y el
grafico muestra otra.
"""

PRIVADAS = "('PB','PC','PM','PP')"

# (valor de la variable, texto que ve el usuario). Los valores son los mismos
# que usa One page live monitoring, asi la marca puede viajar entre tableros.
MARCAS = [
    ("VISA", "Visa"),
    ("MASTERCARD", "Mastercard"),
    ("AMEX", "Amex"),
    ("CABAL_DISCOVER", "Cabal / Discover"),
    ("UPI", "UPI"),
    ("NARANJA", "Naranja"),
    ("PRIVADAS", "Marcas Privadas"),
    ("OTRAS", "Otras"),
]

PRODUCTOS = ["Crédito", "Débito", "Prepaga", "Marcas Privadas", "Naranja Pura",
             "Indeterminado (Créd/Déb)", "Otro"]


def _t(col):
    return "TRIM(COALESCE(%s,''))" % col


def marca_ctx(col="a.instrmtyp"):
    """Bloque 2: marca sobre el CTX, desde instrmtyp crudo."""
    t = _t(col)
    return ("CASE"
            " WHEN %s IN ('VD','VK','V6','VB','VN','SR','VC') THEN 'VISA'"
            " WHEN %s IN ('MD','MC','MA','MS','MX','MK') THEN 'MASTERCARD'"
            " WHEN %s IN ('AX','AE') THEN 'AMEX'"
            " WHEN %s IN ('PA','CB','CABAL','DISCOVER') THEN 'CABAL_DISCOVER'"
            " WHEN %s IN ('UC','UP') THEN 'UPI'"
            " WHEN %s = 'PN' THEN 'NARANJA'"
            " WHEN %s IN %s THEN 'PRIVADAS'"
            " ELSE 'OTRAS' END" % (t, t, t, t, t, t, t, PRIVADAS))


def marca_abt0(alias="a"):
    """Bloque 3: marca sobre ABT0. La columna marca viene normalizada por el ETL,
    pero manda Naranja y las privadas como OTRAS: se corrigen por instrmtyp."""
    t = _t(alias + ".instrmtyp")
    return ("CASE"
            " WHEN %s = 'PN' THEN 'NARANJA'"
            " WHEN %s IN %s THEN 'PRIVADAS'"
            " ELSE COALESCE(NULLIF(TRIM(%s.marca),''), 'OTRAS') END" % (t, t, PRIVADAS, alias))


def producto(col="a.instrmtyp"):
    """Bloque 4: tipo de producto (sin BIN; el Cabal debito queda en credito)."""
    t = _t(col)
    return ("CASE"
            " WHEN %s IN ('VD','MD','MS') THEN 'Débito'"
            " WHEN %s IN ('V6','VB','VN','VC','MC','AX','PA') THEN 'Crédito'"
            " WHEN %s IN ('VK','SR','MK') THEN 'Prepaga'"
            " WHEN %s IN %s THEN 'Marcas Privadas'"
            " WHEN %s = 'PN' THEN 'Naranja Pura'"
            " WHEN %s = 'UC' THEN 'Indeterminado (Créd/Déb)'"
            " ELSE 'Otro' END" % (t, t, t, t, PRIVADAS, t, t))


def variable_marca(base):
    """Variable de templating 'marca' con la lista comun. Conserva el resto de la
    configuracion de la variable existente (label, multi, includeAll...)."""
    v = dict(base)
    v["query"] = ",".join("%s : %s" % (texto, valor) for valor, texto in MARCAS)
    v["options"] = [{"selected": True, "text": "All", "value": "$__all"}] + [
        {"selected": False, "text": texto, "value": valor} for valor, texto in MARCAS]
    v["current"] = {"selected": True, "text": ["All"], "value": ["$__all"]}
    return v
