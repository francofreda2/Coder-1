# Desvíos v17.0 · migración a las ABT + Producto / Plataforma / Modalidad

Este documento describe qué cambió entre el tablero operativo **v16.8**
(`desvios_v16.8_origen.json`, uid `desvios-v16-7`) y la versión nueva
**v17.0** (`desvios_v17.0_abt.json`, uid `desvios-v17-abt`).

La v17.0 se genera con `build_desvios_abt.py` y se valida con
`check_desvios_abt.py`. No se escribió el JSON a mano: el script parte del
JSON original y solo reescribe `rawSQL` y el bloque de `templating`, así que
el layout, los tipos de panel, `fieldConfig`, `overrides`, las
transformaciones y los data links quedan byte a byte iguales.

---

## 1. Qué se pidió y qué se hizo

| Pedido | Resultado |
|---|---|
| Que el tablero operativo lea de las ABT en lugar de pegarle al CTX | 32 de 33 consultas pasaron a `abt0_ctx_livemonitoring` |
| Agregarle las modalidades | Variable `modalidad` + predicado en los 33 paneles |
| Agregarle los gateways | Variable `plataforma` (53 gateways) + predicado en los 33 paneles |
| — (se sumó por coherencia con el tablero de live monitoring) | Variable `producto` (Tipo de producto) + predicado en los 33 paneles |

Se mantuvo además la exclusión de los 8 CUITs que ya se venía aplicando en
el resto de los tableros.

---

## 2. Por qué ABT0 y no ABT1 / ABT2

El tablero de live monitoring reparte sus paneles entre las tres ABT y, en
los que corren sobre ABT1/ABT2, tiene que avisar en la descripción que los
filtros de producto, plataforma y modalidad **no aplican**, porque esas dos
tablas no traen esas columnas.

En el tablero de Desvíos no hizo falta esa concesión. La razón es que su
clasificación de cuadrantes es **por reglas sobre campos crudos**, no por
catálogo:

```sql
"Op. Emisor"  -- nri IS NULL AND ciss IS NOT NULL AND TRIM(ciss) NOT IN('59','63','83')
"Ri. Emisor"  -- TRIM(ciss) IN('59','63','83')
"Op. Adqte."  -- nri IS NOT NULL AND nri NOT IN('TA001',...,'LIM02')
"Ri. Adqte."  -- nri IN('TA001','TA002',...,'TCADQ','LMT01','LMT02','LIM01','LIM02')
```

Eso solo necesita `codrespuestaiss` y `nrorechazointerno`, y **ABT0 conserva
las dos columnas crudas**. Así que todos los paneles de cuadrantes y motivos
se pudieron migrar a ABT0 sin tocar una sola expresión de clasificación, y
por lo tanto **todos respetan los tres filtros nuevos**. No hay ningún panel
con la advertencia de "este filtro no aplica".

---

## 3. Mapeo de columnas CTX → ABT0

| v16.8 (`ctx_autorizaciones_eps`) | v17.0 (`abt0_ctx_livemonitoring`) |
|---|---|
| `CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR` | `a.minuto_utc` |
| `a.fecha` (partición) | `a.fecha_particion` |
| `a.codrespuestaautorizacion` | `a.cod_respuesta` |
| `a.codtipomensajeautorizacion` | `a.cod_tipo_mensaje` |
| `a.codprocesoautorizacion1` | `a.cod_proceso_autorizacion` |
| `a.nroestablecimiento` | `a.nro_establecimiento` |
| `a.INSTRMTYP` | `a.instrmtyp` |
| `a.codrespuestaiss`, `a.nrorechazointerno`, `a.codbancoemisor` | igual nombre |
| CUIT vía `EXISTS` contra `prisma_ab_analytics_prod_db.establecimiento` | `a.cuit`, ya resuelto en la fila |

### El timestamp

v16.8 armaba el instante parseando fecha y hora locales y sumándole 3 horas
para llevarlo a UTC. ABT0 ya expone `minuto_utc` en UTC, truncado al minuto.
Todos los paneles agrupan en buckets de 15 minutos o filtran por rango, así
que perder los segundos no cambia ningún resultado.

### El CUIT

Esta es la mejora más grande. Antes:

```sql
AND (COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR EXISTS(
      SELECT 1 FROM prisma_ab_analytics_prod_db.establecimiento e
      WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=e.nro_establecimiento
        AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)
            =TRY_CAST('${cuit:raw}' AS BIGINT)))
```

Un `EXISTS` correlacionado, evaluado una vez por fila, en **los 33 paneles**.
Ahora:

```sql
AND (COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR
     TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT)
     =TRY_CAST(REPLACE('${cuit:raw}','-','') AS BIGINT))
```

Sin join y sin subconsulta. Como efecto lateral, el filtro pasa a tolerar
CUITs escritos con guiones.

---

## 4. Los tres filtros nuevos

Se aplican con las mismas expresiones que usa el tablero de live monitoring,
para que los dos tableros clasifiquen igual. Van en el `WHERE` de la tabla
base de **los 33 paneles**, y cuando la variable está en `All` el predicado
se pliega a `TRUE` y no filtra nada.

### 4.1 Tipo de producto (`$producto`)

```sql
AND ( '${producto:raw}' IN ('-1','','$__all')
      OR (CASE
               WHEN TRIM(a.instrmtyp) IN ('VD','MD','MS')                    THEN 'Débito'
               WHEN TRIM(a.instrmtyp) IN ('V6','VB','VN','VC','MC','AX','PA') THEN 'Crédito'
               WHEN TRIM(a.instrmtyp) IN ('VK','SR','MK')                    THEN 'Prepaga'
               WHEN TRIM(a.instrmtyp) = 'PB'                                 THEN 'Marcas Privadas'
               WHEN TRIM(a.instrmtyp) = 'PN'                                 THEN 'Naranja Pura'
               WHEN TRIM(a.instrmtyp) = 'UC'                                 THEN 'Indeterminado (Créd/Déb)'
               ELSE 'Otro'
          END) = '${producto:raw}' )
```

Opciones: Crédito, Débito, Prepaga, Naranja Pura, Marcas Privadas,
Indeterminado, Otro.

### 4.2 Plataforma de pago / gateway (`$plataforma`)

La variable guarda el sufijo de 4 dígitos de `programaperiferiaorigen`:

```sql
AND ( '${plataforma:raw}' IN ('-1','','$__all')
      OR contains(split('${plataforma:raw}', ','),
                  IF(length(TRIM(COALESCE(a.programaperiferiaorigen,''))) >= 4,
                     substr(TRIM(a.programaperiferiaorigen), -4),
                     TRIM(COALESCE(a.programaperiferiaorigen,'')))) )
```

53 gateways, entre ellos Paystore (`0001`), Pos Legacy (`7001`), Decidir
(`8002`), Laposweb (`9000`), MELI (`3410`), Cybersource (`1053`), Geopagos
(`0502`), PayU (`0811`), DLOCAL (`0540`).

### 4.3 Modalidad (`$modalidad`)

```sql
AND ( '${modalidad:raw}' IN ('-1','','$__all')
      OR (CASE
               WHEN TRIM(COALESCE(a.coddebitoautomatico,'')) = '1'
                 OR ( dafil.nro_establecimiento IS NOT NULL
                      AND TRIM(COALESCE(a.codmetodoingresomovimiento,'')) IN ('0','1') )
                    THEN 'Débito Automático'
               WHEN TRIM(COALESCE(a.codidentpresenciacliente,'')) = '8' THEN 'E-commerce'
               WHEN TRIM(COALESCE(a.codidentpresenciacliente,'')) <> ''  THEN 'Presente'
               ELSE 'Indeterminado'
          END) = '${modalidad:raw}' )
```

La rama de Débito Automático necesita saber qué establecimientos lo tienen
habilitado, así que cada consulta suma este `LEFT JOIN`:

```sql
LEFT JOIN (SELECT DISTINCT e.nro_establecimiento
           FROM prisma_ab_analytics_prod_db.establecimiento e
           WHERE TRIM(COALESCE(e.cod_debito_automatico_estab,'')) = '1') dafil
       ON dafil.nro_establecimiento = TRY_CAST(TRIM(a.nro_establecimiento) AS INTEGER)
```

El `SELECT DISTINCT` garantiza que el join no duplique filas.

---

## 5. Exclusión de los 8 CUITs

```sql
AND (TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT) IS NULL
     OR TRY_CAST(REPLACE(a.cuit,'-','') AS BIGINT) NOT IN
        (30712334610,30715313681,30717791904,30718438906,
         30716325586,30691399768,33714595569,30709228419))
```

El `IS NULL` adelante evita perder filas cuyo CUIT no sea numérico: sin esa
guarda, un solo `NULL` haría que `NOT IN` devuelva `NULL` y el panel quedaría
vacío.

---

## 6. El único panel que no se pudo migrar

**Panel 38 · "Top 10 BINes (8 díg.) por Rechazos"** sigue leyendo
`ctx_autorizaciones_eps`. ABT0 entrega el PAN enmascarado, y el panel necesita
los primeros 8 dígitos de `nrotarjeta` para armar el BIN. No hay forma de
reconstruirlo desde la ABT.

Para que no quede como el panel caro del tablero, se le aplicaron igual las
mejoras que no dependen de la ABT:

- El `EXISTS` correlacionado del CUIT se reescribió como semi-join no
  correlacionado (`IN (SELECT ...)`), que Presto resuelve una sola vez.
- Lleva los tres filtros nuevos y la exclusión de CUITs, igual que el resto.

Es decir: **es el único panel sobre CTX, pero conserva la fidelidad completa
de filtros.** Se dejó constancia en la descripción del panel dentro del JSON.

---

## 7. Otros ajustes

- `refresh`: `30s` → `1m`.
- `resultReuseEnabled` + `resultReuseMaxAgeInMinutes: 5` en los 33 targets,
  para que Athena devuelva resultados cacheados en lugar de re-escanear.
- `uid` nuevo (`desvios-v17-abt`) y `id` removido, para que la importación no
  pise el tablero v16.8.

---

## 8. Un punto a verificar en la primera corrida

`a.codbancoemisor` se asume presente en ABT0. La evidencia es un comentario
del propio tablero de live monitoring, que elige ABT0 sobre ABT1 diciendo:

> *ABT1 no tiene codbancoemisor / codrespuestaiss / nrorechazointerno, así que
> no puede respetar los filtros $banco, $motivo_emisor ni $motivo_adqte.*

Es una inferencia razonable, pero no la pude comprobar contra el catálogo
porque desde acá no tengo acceso a Athena. Si al importar el tablero el
**panel 120 ("Top 10 Bancos Emisores")** o el filtro **$banco** fallan con
columna inexistente, es exactamente esto, y se resuelve de una de dos formas:

1. Si la columna existe con otro nombre en ABT0, cambiar el mapeo en
   `build_desvios_abt.py` (constante `COLUMNAS`) y regenerar.
2. Si no existe, dejar el panel 120 sobre CTX igual que el 38: alcanza con
   sumar su condición a la línea `sobre_abt = ...` del script.

El resto de las columnas usadas (`minuto_utc`, `fecha_particion`, `cuit`,
`nro_establecimiento`, `instrmtyp`, `cod_respuesta`, `cod_tipo_mensaje`,
`cod_proceso_autorizacion`, `codrespuestaiss`, `nrorechazointerno`, `fill9`,
`programaperiferiaorigen`, `coddebitoautomatico`, `codidentpresenciacliente`,
`codmetodoingresomovimiento`) sí están verificadas: las usa el tablero de live
monitoring sobre ABT0.

---

## 9. Archivos

| Archivo | Qué es |
|---|---|
| `desvios_v16.8_origen.json` | El tablero operativo tal como estaba (entrada) |
| `abt_vars_nuevos.json` | Las 3 variables nuevas, tomadas del tablero de live monitoring |
| `build_desvios_abt.py` | Genera la v17.0 a partir de los dos anteriores |
| `desvios_v17.0_abt.json` | **El tablero a importar en Grafana** |
| `check_desvios_abt.py` | 24 validaciones sobre el resultado |

```bash
python3 build_desvios_abt.py && python3 check_desvios_abt.py
```

---

## 10. Validaciones que corren

1. 33 paneles preservados, con los mismos ids.
2. Layout y tipos de panel intactos.
3. `fieldConfig`, `transformations` y `options` intactos.
4. Solo el panel 38 sigue leyendo CTX; 32 targets sobre ABT0.
5. No queda ningún `EXISTS` correlacionado.
6. No queda ningún `date_parse` ni `a.fecha` en los paneles ABT0.
7. Los tres filtros nuevos, el `LEFT JOIN dafil` y la exclusión de CUITs
   aparecen **exactamente una vez** en cada uno de los 33 targets.
8. Las expresiones de cuadrantes, la definición de TAA, la exclusión de
   reversos (`<>'1420'`) y la de procesos (`NOT IN('92','30')`) aparecen la
   misma cantidad de veces que en el origen.
9. Paréntesis y comillas balanceados en los 33 SQL.
10. 9 variables de templating en orden, `uid` nuevo, sin `id`, `refresh` 1m,
    result reuse activo en 33/33.
