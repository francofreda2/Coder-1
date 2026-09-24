import json,re,sys,os,shutil
src,out,tstype=sys.argv[1],sys.argv[2],sys.argv[3]
shutil.rmtree(out,ignore_errors=True); os.makedirs(out)
d=json.load(open(src,encoding="utf-8"))
def interp(s,vals):
    s=s.replace("$__timeFrom()","TIMESTAMP '2026-09-24 10:00:00'").replace("$__timeTo()","TIMESTAMP '2026-09-24 16:00:00'")
    def rep(m):
        name,fmt=m.group(1),m.group(2)
        if fmt=="text": return vals.get(name+":text","All")
        if fmt=="sqlstring": return "'x'"
        if fmt=="csv": return "x"
        return vals.get(name,"")
    return re.sub(r"\$\{(\w+):(\w+)\}",rep,s)
escenarios={"all":{"resolucion":"0","producto":"$__all","plataforma":"$__all","modalidad":"$__all"},
            "filtrado":{"resolucion":"1","cuit":"30111111111","establecimiento":"111","producto":"Crédito","plataforma":"8002","modalidad":"Presente","marca:text":"Visa"}}
def tabla(cols):
    return "(SELECT " + ", ".join("CAST(NULL AS %s) AS %s" % (ty, c) for c, ty in cols) + ")"
V="varchar"
ABT0=[("minuto_utc",tstype),("fecha_particion",V),("cuit",V),("nro_establecimiento",V),("marca",V),("instrmtyp",V),("cod_respuesta",V),("cod_tipo_mensaje",V),("cod_proceso_autorizacion",V),("codrespuestaiss",V),("nrorechazointerno",V),("codbancoemisor",V),("programaperiferiaorigen",V),("coddebitoautomatico",V),("codidentpresenciacliente",V),("codmetodoingresomovimiento",V),("latencia_ms","bigint"),("fill9",V),("flg_aprobada","integer"),("flg_denegada","integer"),("flg_reverso","integer")]
CTX=[(c,V) for c in "fechalocalautorizacion horalocalautorizacion fecha codrespuestaautorizacion codtipomensajeautorizacion codprocesoautorizacion1 nroestablecimiento INSTRMTYP codrespuestaiss nrorechazointerno codbancoemisor nrotarjeta programaperiferiaorigen coddebitoautomatico codidentpresenciacliente codmetodoingresomovimiento".split()]
EST=[("nro_establecimiento","integer"),("cuit_establecimiento_host",V),("cod_debito_automatico_estab",V)]
def tablas(q):
    q=q.replace("payway_poststage_prod_db.abt0_ctx_livemonitoring",tabla(ABT0))
    q=q.replace("payway_poststage_prod_db.ctx_autorizaciones_eps",tabla(CTX))
    return q.replace("prisma_ab_analytics_prod_db.establecimiento",tabla(EST))
n=0
for esc,v in escenarios.items():
    for p in d["panels"]:
        for i,t in enumerate(p.get("targets") or []):
            if t.get("rawSQL"):
                open(f"{out}/{esc}_p{p['id']:05d}_{i}.sql","w",encoding="utf-8").write(tablas(interp(t["rawSQL"],v))); n+=1
print("consultas generadas:",n)
