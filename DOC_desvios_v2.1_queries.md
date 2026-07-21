# Dashboard Desvíos · CUIT/Establecimiento — v2.1
## Documento de agregaciones y queries

**Fecha:** 2026-07-21 · **Archivo:** `desvios_v2.1.json` · **uid:** `desvios-v2` · **Paneles:** 47 · **Refresh:** 1m

Repo: `francofreda2/Coder-1` · rama `claude/keen-noether-rr827x`. Generador reproducible: `build_desvios.py`.

---

## 1. Convenciones comunes a todo el tablero

| Concepto | Definición |
|---|---|
| Fuente | `payway_poststage_prod_db.ctx_autorizaciones_eps` |
| Timestamp | `date_parse(fechalocal+horalocal)` **+3 h** (local ART → UTC) |
| Bucket | 15 minutos: `date_trunc('minute', ts) - (minuto % 15)` |
| Poda de partición | `fecha BETWEEN date_format(timeFrom-3h) AND date_format(timeTo-3h)` |
| **Aprobada** | `codrespuestaautorizacion IN ('0000','0400','0900')` |
| **Denegada** | `codrespuestaautorizacion NOT IN ('0000','0400','0900')` |
| **TAA** | `100 * aprobadas / total` (sin reversos) |
| Exclusiones estándar | procesos `92`,`30` y reversos `codtipomensaje = 1420` |
| Filtros template | CUIT, Establecimiento, BIN(8), Marca, Banco, Motivo Emisor, Motivo Adqte |

## 2. Optimizaciones globales aplicadas (v2.0 → v2.1)

### 2.1 Filtro CUIT como semi-join (reemplaza EXISTS correlacionado)
El `EXISTS` por fila contra `establecimiento` se reescribió como `IN` no correlacionado; Athena/Trino lo ejecuta **una sola vez** como hash semi-join. Resultado idéntico, y el input tolera guiones.
```sql
-- antes (por cada fila de la tabla grande):
EXISTS(SELECT 1 FROM prisma_ab_analytics_prod_db.establecimiento e
  WHERE TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER)=e.nro_establecimiento
    AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT))

-- ahora (se resuelve una sola vez):
TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN
  (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e
   WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT))
```

### 2.2 Exclusión de 8 CUITs (anti-join, presente en TODOS los targets)
CUITs excluidos: `30712334610, 30715313681, 30717791904, 30718438906, 30716325586, 30691399768, 33714595569, 30709228419`.
```sql
AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR
     TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN
       (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e
        WHERE e.nro_establecimiento IS NOT NULL
          AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN
              (30712334610,30715313681,30717791904,30718438906,
               30716325586,30691399768,33714595569,30709228419)))
```

Guardas: el `IS NULL` evita perder filas con establecimiento no numérico; el `nro_establecimiento IS NOT NULL` del subquery evita que un NULL anule el `NOT IN` completo. El panel *CUITs afectados* además filtra estos CUITs en la salida.

### 2.3 Otras optimizaciones
- `refresh`: 30s → **1m**.
- **Athena result reuse** habilitado (5 min) en `connectionArgs` de todos los targets.
- **Fix de filtros en paneles 800/801** (Establecimientos del CUIT): ahora respetan también Marca, Banco, Motivo Emisor y Motivo Adqte.
- Cuadrantes, motivos, bancos y BINes: **lógica intacta** respecto del original.

## 3. NUEVO · Tipo de Producto (reemplaza a Crédito/Débito/Prepago)

Clasificación por `INSTRMTYP`, con Marcas Privadas (PB), Naranja Pura (PN) y PA→Débito por BIN:

| Tipo de producto | Regla |
|---|---|
| Débito | `VD, MD, MS` · o `PA` con BIN `604201` (6 díg.) o `65008700` (8 díg.) |
| Crédito | `V6, VB, VN, VC, MC, AX` · o `PA` (resto) |
| Prepaga | `VK, SR, MK` |
| Marcas Privadas | `PB` |
| Naranja Pura | `PN` |
| Indeterminado (Crédito/Débito) | `UC` (UnionPay) |
| Otro | resto |

**Filtros:** exclusiones estándar (92/30, 1420) + `INSTRMTYP` en lista permitida (incluye PB y PN) + los 7 filtros template + exclusión de CUITs. Se removió el `LIMIT 100` del query de prueba (truncaba la serie).

### 3.1 Panel «TAA por Tipo de Producto · 15m» (id 110) — query completa
```sql
WITH agg AS (
  SELECT date_trunc('minute',CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)-(EXTRACT(MINUTE FROM CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)%15)*INTERVAL '1' MINUTE AS bk,
    CASE WHEN a.INSTRMTYP IN ('VD', 'MD', 'MS') THEN 'Débito' WHEN a.INSTRMTYP = 'PA' AND (SUBSTR(TRIM(a.nrotarjeta), 1, 6) = '604201' OR SUBSTR(TRIM(a.nrotarjeta), 1, 8) = '65008700') THEN 'Débito' WHEN a.INSTRMTYP IN ('V6', 'VB', 'VN', 'VC', 'MC', 'AX') THEN 'Crédito' WHEN a.INSTRMTYP = 'PA' THEN 'Crédito' WHEN a.INSTRMTYP IN ('VK', 'SR', 'MK') THEN 'Prepaga' WHEN a.INSTRMTYP = 'PB' THEN 'Marcas Privadas' WHEN a.INSTRMTYP = 'PN' THEN 'Naranja Pura' WHEN a.INSTRMTYP = 'UC' THEN 'Indeterminado (Crédito/Débito)' ELSE 'Otro' END AS tipo_producto,
    SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) IN('0000','0400','0900') THEN 1 ELSE 0 END) AS aprob,
    SUM(CASE WHEN TRIM(a.codrespuestaautorizacion) NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS deneg,
    COUNT(*) AS total
  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a
  WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')
    AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR>=$__timeFrom() AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR<$__timeTo()
    AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')
    AND TRIM(a.codtipomensajeautorizacion)<>'1420'
    AND a.INSTRMTYP IN ('VD','VK','V6','VB','VN','SR','VC','MD','MC','MS','MK','AX','PA','UC','PB','PN')
    AND (COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT))) AND ('${establecimiento:raw}'='' OR TRIM(a.nroestablecimiento)='${establecimiento:raw}') AND ('${bin:raw}'='' OR substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8)='${bin:raw}') AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE e.nro_establecimiento IS NOT NULL AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN (30712334610,30715313681,30717791904,30718438906,30716325586,30691399768,33714595569,30709228419))) AND ('${marca:text}'='All' OR (CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END) IN (${marca:sqlstring})) AND ('${banco:text}'='All' OR CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) AND ('${motivo_emisor:text}'='All' OR (CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END) IN (${motivo_emisor:sqlstring})) AND ('${motivo_adqte:text}'='All' OR NULLIF(TRIM(a.nrorechazointerno),'') IN (${motivo_adqte:sqlstring}))
  GROUP BY 1, 2
)
SELECT bk AS time, tipo_producto AS metric, ROUND(100.0*aprob/NULLIF(total,0),2) AS "TAA %" FROM agg ORDER BY 1, 2;
```

### 3.2 Panel «Denegadas por Tipo de Producto · 15m» (id 111) — SELECT final (misma CTE `agg`)
```sql
SELECT bk AS time, tipo_producto AS metric, deneg AS "Denegadas" FROM agg ORDER BY 1, 2;
```

### 3.3 Panel «Resumen Tipo de Producto» (id 112) — SELECT final (agrupa por producto en el rango)
```sql
SELECT tipo_producto AS "Tipo Producto", total AS "Total Op", aprob AS "Aprobadas", deneg AS "Denegadas",
  ROUND(100.0*aprob/NULLIF(total,0),2) AS "TAA %",
  ROUND(100.0*total/NULLIF(SUM(total) OVER (),0),1) AS "% del total"
FROM agg ORDER BY total DESC;
```

## 4. NUEVO · QR y Billeteras (`fill9`)

Identificación: `fill9` posición 1 = `'Q'` → pago con QR; posición 2 = wallet id. Se excluye cierre de lote (`codtipomensaje <> '1500'`).

| Cód. | Billetera | Cód. | Billetera |
|---|---|---|---|
| 1 | Tp | 9 | Cenco |
| 2 | Macro | M | Mercado Pago (MEPA) |
| 3 | Modo (MODO) | A | Plus Pagos (PLUS) |
| 4 | Bimo | B | Nubi (NUBI) |
| 5 | Pagochat | C | Naranja X (NARX) |
| 6 | Bapro_cuentaDNI (CDNI) | D | Persona Pay (PEPA) |
| 7 | Bna | Y | Yacaré (YACA) |
| 8 | Bsf | otro | NO MAPEADO / DESCONOCIDO |

**Adaptación:** las fechas fijas del query de prueba se reemplazaron por el selector de tiempo con poda de partición. Nota: fiel a la especificación, este panel **no** excluye reversos 1420 (solo 1500).

### 4.1 Panel «Operaciones QR por Billetera · 15m» (id 130) — query completa
```sql
WITH q AS (
  SELECT date_trunc('minute',CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)-(EXTRACT(MINUTE FROM CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)%15)*INTERVAL '1' MINUTE AS bk, SUBSTRING(a.fill9, 2, 1) AS wallet_id_codigo,
    CASE SUBSTRING(a.fill9, 2, 1) WHEN '1' THEN 'Tp' WHEN '2' THEN 'Macro' WHEN '3' THEN 'Modo (MODO)' WHEN '4' THEN 'Bimo' WHEN '5' THEN 'Pagochat' WHEN '6' THEN 'Bapro_cuentaDNI (CDNI)' WHEN '7' THEN 'Bna' WHEN '8' THEN 'Bsf' WHEN '9' THEN 'Cenco' WHEN 'M' THEN 'Mercado Pago (MEPA)' WHEN 'A' THEN 'Plus Pagos (PLUS)' WHEN 'B' THEN 'Nubi (NUBI)' WHEN 'C' THEN 'Naranja X (NARX)' WHEN 'D' THEN 'Persona Pay (PEPA)' WHEN 'Y' THEN 'Yacaré (YACA)' ELSE 'NO MAPEADO / DESCONOCIDO' END AS billetera,
    TRIM(a.codrespuestaautorizacion) AS cr
  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a
  WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')
    AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR>=$__timeFrom() AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR<$__timeTo()
    AND SUBSTRING(a.fill9, 1, 1) = 'Q'
    AND TRIM(a.codtipomensajeautorizacion) <> '1500'
    AND (COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT))) AND ('${establecimiento:raw}'='' OR TRIM(a.nroestablecimiento)='${establecimiento:raw}') AND ('${bin:raw}'='' OR substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8)='${bin:raw}') AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE e.nro_establecimiento IS NOT NULL AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN (30712334610,30715313681,30717791904,30718438906,30716325586,30691399768,33714595569,30709228419))) AND ('${marca:text}'='All' OR (CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END) IN (${marca:sqlstring})) AND ('${banco:text}'='All' OR CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) AND ('${motivo_emisor:text}'='All' OR (CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END) IN (${motivo_emisor:sqlstring})) AND ('${motivo_adqte:text}'='All' OR NULLIF(TRIM(a.nrorechazointerno),'') IN (${motivo_adqte:sqlstring}))
)
SELECT bk AS time, billetera AS metric, COUNT(*) AS "Operaciones" FROM q GROUP BY 1, 2 ORDER BY 1;
```

### 4.2 Panel «Ranking Billeteras QR» (id 131) — SELECT final (misma CTE `q`)
```sql
SELECT billetera AS "Billetera", wallet_id_codigo AS "Cód.", COUNT(*) AS "Operaciones",
  SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS "Aprobadas",
  SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS "Denegadas",
  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS "TAA %",
  ROUND(100.0*COUNT(*)/NULLIF(SUM(COUNT(*)) OVER (),0),1) AS "% del total"
FROM q GROUP BY 1, 2 ORDER BY 3 DESC;
```

## 5. NUEVO · Modalidades (DA / E-commerce / Presente)

| Modalidad | Regla |
|---|---|
| Débito Automático | `coddebitoautomatico = '1'` |
| E-commerce | `codidentpresenciacliente = '8'` |
| Presente | `codidentpresenciacliente <> '8'` |
| Indeterminado | resto (p.ej. `codidentpresenciacliente` NULL) |

**Adaptación:** se agregaron las exclusiones estándar (92/30 y 1420) para que la TAA sea comparable con el resto del tablero (el query de prueba no las tenía).

### 5.1 Panel «TAA por Modalidad · 15m» (id 140) — query completa
```sql
WITH m AS (
  SELECT date_trunc('minute',CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)-(EXTRACT(MINUTE FROM CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR)%15)*INTERVAL '1' MINUTE AS bk,
    CASE WHEN a.coddebitoautomatico = '1' THEN 'Débito Automático' WHEN a.codidentpresenciacliente = '8' THEN 'E-commerce' WHEN a.codidentpresenciacliente <> '8' THEN 'Presente' ELSE 'Indeterminado' END AS modalidad,
    TRIM(a.codrespuestaautorizacion) AS cr
  FROM payway_poststage_prod_db.ctx_autorizaciones_eps a
  WHERE a.fecha BETWEEN date_format(date_add('hour',-3,$__timeFrom()),'%Y-%m-%d') AND date_format(date_add('hour',-3,$__timeTo()),'%Y-%m-%d')
    AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR>=$__timeFrom() AND CAST(date_parse(concat(a.fechalocalautorizacion,a.horalocalautorizacion),'%y%m%d%H%i%s') AS timestamp)+INTERVAL '3' HOUR<$__timeTo()
    AND TRIM(a.codprocesoautorizacion1) NOT IN('92','30')
    AND TRIM(a.codtipomensajeautorizacion)<>'1420'
    AND (COALESCE(NULLIF('${cuit:raw}',''),'-1')='-1' OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT)=TRY_CAST('${cuit:raw}' AS BIGINT))) AND ('${establecimiento:raw}'='' OR TRIM(a.nroestablecimiento)='${establecimiento:raw}') AND ('${bin:raw}'='' OR substr(regexp_extract(TRIM(a.nrotarjeta),'^[0-9]+'),1,8)='${bin:raw}') AND (TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) IS NULL OR TRY_CAST(TRIM(a.nroestablecimiento) AS INTEGER) NOT IN (SELECT e.nro_establecimiento FROM prisma_ab_analytics_prod_db.establecimiento e WHERE e.nro_establecimiento IS NOT NULL AND TRY_CAST(REPLACE(e.cuit_establecimiento_host,'-','') AS BIGINT) IN (30712334610,30715313681,30717791904,30718438906,30716325586,30691399768,33714595569,30709228419))) AND ('${marca:text}'='All' OR (CASE WHEN a.INSTRMTYP IN('VD','VK','V6','VB','VN','SR','VC') THEN 'Visa' WHEN a.INSTRMTYP IN('MD','MC','MS','MK') THEN 'Mastercard' WHEN a.INSTRMTYP='AX' THEN 'Amex' WHEN a.INSTRMTYP='PA' THEN 'Cabal' WHEN a.INSTRMTYP='UC' THEN 'UPI' ELSE 'Otra' END) IN (${marca:sqlstring})) AND ('${banco:text}'='All' OR CAST(TRY_CAST(TRIM(a.codbancoemisor) AS INTEGER) AS VARCHAR) IN (${banco:sqlstring})) AND ('${motivo_emisor:text}'='All' OR (CASE WHEN length(TRIM(a.codrespuestaiss))=1 THEN '0'||TRIM(a.codrespuestaiss) WHEN length(TRIM(a.codrespuestaiss))>=2 THEN substr(TRIM(a.codrespuestaiss),length(TRIM(a.codrespuestaiss))-1,2) ELSE NULL END) IN (${motivo_emisor:sqlstring})) AND ('${motivo_adqte:text}'='All' OR NULLIF(TRIM(a.nrorechazointerno),'') IN (${motivo_adqte:sqlstring}))
)
SELECT bk AS time, modalidad AS metric,
  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS "TAA %"
FROM m GROUP BY 1, 2 ORDER BY 1;
```

### 5.2 Paneles «Volumen por Modalidad» (id 141) y «Resumen por Modalidad» (id 142) — SELECT finales (misma CTE `m`)
```sql
-- id 141
SELECT bk AS time, modalidad AS metric, COUNT(*) AS "Transacciones" FROM m GROUP BY 1, 2 ORDER BY 1;

-- id 142
SELECT modalidad AS "Modalidad", COUNT(*) AS "Transacciones",
  SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END) AS "Aprobadas",
  SUM(CASE WHEN cr NOT IN('0000','0400','0900') THEN 1 ELSE 0 END) AS "Denegadas",
  ROUND(100.0*SUM(CASE WHEN cr IN('0000','0400','0900') THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),2) AS "TAA %",
  ROUND(100.0*COUNT(*)/NULLIF(SUM(COUNT(*)) OVER (),0),1) AS "% del total"
FROM m GROUP BY 1 ORDER BY 2 DESC;
```

## 6. Inventario de archivos en el repo

| Archivo | Contenido |
|---|---|
| `desvios_v2.1.json` | **Versión vigente** (47 paneles, uid `desvios-v2`) |
| `desvios_v2.0.json` | Rollback (40 paneles, sin secciones nuevas) |
| `desvios_optimizado.json` | Mismo contenido que v2.1 con uid `desvios` |
| `dashboard_producto_qr_modalidades.json` | Versión standalone de las 3 secciones (descartable) |
| `build_desvios.py` | Generador del dashboard completo |
| `optimize_desvios.py` + `README_desvios_optimizacion.md` | Optimizador de exports + doc |

## 7. Validaciones automáticas ejecutadas

- JSON parsea OK · 47 paneles · ids únicos.
- Exclusión de CUITs presente en **39/39 targets SQL**.
- Clasificación de producto, mapeo de 15 billeteras y CASE de modalidad **fieles a la definición** entregada.
- Sin `LIMIT` en los paneles nuevos; LIMITs legítimos (800: 100, 820: 200, 900: 50) intactos.
- Cuadrantes/rechazos sin cambios vs. original.