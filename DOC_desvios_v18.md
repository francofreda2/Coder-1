# Desvíos · NOC · v18

La v18 tiene la **misma lógica y el mismo SQL** que la v17.1. Lo que cambia es
cómo se recorre y cómo se interactúa con el tablero.

## El recorrido

El tablero se lee de arriba hacia abajo como cinco preguntas:

| # | Sección | Para qué |
|---|---|---|
| 1 | **¿Está procesando?** | Hasta qué minuto hay dato, atraso de la ABT, TRX/min, volumen y TAA contra D-1, latencia |
| 2 | **¿Dónde?** | Gateways: volumen, TAA y tabla de estado con los caídos arriba |
| 3 | **¿Cuándo baja la TAA?** | Resumen del rango, TAA vs D-1 con volumen, desvío en pp |
| 4 | **¿Por qué?** | Cuadrantes, Fondos/DNH y otros motivos |
| 5 | **¿Qué marca?** | TAA por marca |

El **detalle** (motivos de cada cuadrante, marca por marca, producto, bancos y
BINes) está en secciones **colapsadas** debajo de la pregunta que profundizan.
Se abren con un clic.

## Interacción

- **Clic en un gateway** (en cualquiera de los dos gráficos o en la tabla) →
  *Filtrar el tablero por ese gateway*: todo el tablero pasa a ese gateway,
  con el mismo rango y los demás filtros. También está *Ver en One page live
  monitoring*.
- **Clic en una marca** (gráfico "TAA por marca") → *Filtrar el tablero por esta
  marca*.
- **Arriba a la derecha**:
  - *One page live monitoring* abre ese tablero con el mismo rango, CUIT,
    gateway, marca, producto y modalidad.
  - *Limpiar filtros* vuelve a este tablero sin filtros.
- En **One page live monitoring** hay un link de vuelta: *Desvíos · NOC
  (análisis de la caída)*.

La marca viaja entre los dos tableros porque los dos usan la lista común
(`VISA`, `MASTERCARD`, … `PRIVADAS`, `OTRAS`). Si en One page está "All" en
Cliente, en Desvíos el CUIT llega como `-1`, que también significa "todos".

## Marcas y tipo de producto (lista común del 02/10/2026)

Marca y tipo de producto salen de `logica_marcas.py`, copia de los bloques 2, 3 y
4 de `8-LOGICA-COMPARTIDA/marcas/MARCAS_por_instrmtyp.sql`. El filtro y todos los
desgloses usan la misma lista.

| Marca | `instrmtyp` |
|---|---|
| Visa | VD, VK, V6, VB, VN, SR, VC |
| Mastercard | MD, MC, MA, MS, MX, MK |
| Amex | AX, AE |
| Cabal / Discover | PA, CB |
| UPI | UC, UP |
| Naranja | PN |
| **Marcas Privadas** | **PB, PC, PM, PP** |
| Otras | el resto (hoy, solo el tipo vacío) |

Sobre ABT0 se usa la columna `marca` del ETL, corregida por `instrmtyp` para
Naranja y las privadas (bloque 3). El panel de BINes, que lee el CTX, usa el
bloque 2.

Qué cambió respecto de la v18 anterior:

- **Filtro Marca**: valores de la lista común. Antes eran Visa, Mastercard,
  Cabal, Amex, UPI y "Otra", donde caían Naranja, las privadas y MA, MX, AE, CB,
  UP.
- **Filtro Producto**: Marcas Privadas incluye PC, PM y PP (antes solo PB).
- **TAA por marca**: suma las series Naranja y Marcas Privadas.
- **Detalle por marca**: cada panel elige su marca con la lista común (antes
  Mastercard no tenía MA ni MX, Amex no tenía AE, etc.). Se suman Naranja y
  Marcas Privadas.
- **Detalle por tipo de producto**: los paneles Crédito, Débito y Prepago usaban
  una clasificación propia, distinta del filtro Producto (por ejemplo, `MS` era
  Crédito en el panel y Débito en el filtro; `PA`, al revés). Ahora usan la del
  filtro. **Sus números cambian.** "Prepago" pasa a llamarse "Prepaga", como en
  el filtro, y se suman Marcas Privadas, Naranja Pura e Indeterminado.

## Qué cambió respecto de la v17.1

| Antes | Ahora |
|---|---|
| 37 consultas a Athena al abrir | **12**. Las 32 del detalle corren recién al abrir su sección |
| "Estado General": 4 stats, 4 consultas | "Resumen del rango": 1 panel, 1 consulta, mismos 4 números |
| 6 indicadores NOC | 8: se suman **TAA** y **Δ TAA D-1** de los últimos 15 min |
| Secciones sin orden de lectura | Preguntas numeradas 1 a 5, detalle colapsado |
| Títulos con "· auto (según rango)" | Títulos cortos; la resolución está en su indicador |
| 22 paneles sin descripción | Todos con descripción (ícono ⓘ) |
| Variables sin ayuda, Gateway al final | Gateway, Marca, Producto, Modalidad primero; Resolución al final; todas con ayuda |
| Tabla de gateways más ancha que la pantalla | Encabezados cortos; entra entera |
| "TAA actual vs D-1" con 3 ejes y leyenda cortada | 2 ejes (TAA y volumen) y leyenda completa |
| Sin links | Clic para filtrar, links a One page y Limpiar filtros |
| Marca y producto con listas propias | Lista común del 02/10/2026 (ver arriba) |
| uid distinto en cada versión | uid estable `payway-desvios` |

El único cambio en el SQL: el filtro de CUIT ahora acepta también `$__all`, para
que el link desde One page no deje el tablero vacío.

## Cómo se validó

- `check_desvios_v18.py`: 61 chequeos. Entre ellos: sin paneles superpuestos,
  ningún panel que reusa datos depende de otro colapsado, toda variable
  referenciada existe, cada link filtra conservando rango y filtros, el SQL es
  idéntico al de la v17.1 salvo el CUIT, y cada detalle por marca o producto
  filtra su segmento con la lista común.
- `check_desvios_abt.py`: no queda la clasificación de marca vieja, las 33
  consultas usan la lista común y el filtro Producto tiene PB, PC, PM y PP.
- `test_desvios_noc.py desvios_v18.json`: 31 chequeos ejecutando el SQL real
  sobre una ABT0 sintética con un incidente conocido. Los datos incluyen tarjetas
  **PC con `marca = 'OTRAS'`**, como las manda hoy el ETL: el filtro Marca =
  Marcas Privadas las trae, Marca = Otras ya no, Producto = Marcas Privadas las
  trae, y aparecen en "TAA por marca" y en los detalles de Marcas Privadas.
- `trino_check/run.sh desvios_v18.json`: 88/88 consultas OK en Trino 446.
- **Grafana 10.4.7 real** (la misma versión que usan), con el resultado del SQL
  sobre los datos sintéticos cargado en cada panel. Se probaron con clics: el
  filtro por gateway desde la tabla y desde los gráficos (incluido que `0001`
  no pierda los ceros), el filtro por marca (el link lleva el código, p. ej.
  `VISA`, y al llegar con `PRIVADAS` el filtro muestra "Marcas Privadas"), los
  links de ida y vuelta con One page, *Limpiar filtros* y la apertura de las
  secciones colapsadas.

No se probó contra Athena con datos reales: tiempos y volúmenes se confirman al
importarlo.
