#!/usr/bin/env bash
# Pasa todo el SQL de un tablero por el analizador de Trino 446 (motor de Athena v3).
# Detecta errores de tipos y de funciones (p. ej. FUNCTION_NOT_FOUND) antes de
# importar en Grafana. No ejecuta: analiza y planifica cada consulta.
#
#   trino_check/run.sh [tablero.json]      (requiere Java 21 y Maven)
set -euo pipefail
JSON="$(realpath "${1:-$(dirname "$0")/../desvios_v17.1_noc.json}")"
cd "$(dirname "$0")"
[ -f cp.txt ] || mvn -q -B dependency:build-classpath -Dmdep.outputFile=cp.txt
mvn -q -B compile
# minuto_utc en ABT0 es timestamp(6) with time zone; se prueba tambien sin zona.
for T in "timestamp(6) with time zone" "timestamp(3)"; do
  python3 gen_sql.py "$JSON" sql_out "$T" >/dev/null
  java -cp "target/classes:$(cat cp.txt)" Check sql_out "$T" 2>/dev/null | grep -E "^(ERROR|minuto_utc)"
done
