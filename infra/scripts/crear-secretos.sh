#!/usr/bin/env bash
# Crea los Secret del ambiente con valores aleatorios. NUNCA se suben a Git ni a las imágenes.
# Es idempotente: si un Secret ya existe, NO lo toca (evita desincronizar contraseñas de las BD).
# Uso:  ./crear-secretos.sh            (usa el namespace "canchas")
set -euo pipefail
NS="${1:-canchas}"

kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

gen() { openssl rand -hex 24; }   # solo [0-9a-f]: seguro dentro de URLs de conexión

crear() {
  local nombre="$1"; shift
  if kubectl -n "$NS" get secret "$nombre" >/dev/null 2>&1; then
    echo "Secret/$nombre ya existe: no se modifica"
  else
    kubectl -n "$NS" create secret generic "$nombre" "$@"
  fi
}

crear postgres-secret \
  --from-literal=POSTGRES_PASSWORD="$(gen)" \
  --from-literal=MIGRADOR_PASSWORD="$(gen)" \
  --from-literal=APP_PASSWORD="$(gen)" \
  --from-literal=MONITOR_PASSWORD="$(gen)"

crear mongo-secret \
  --from-literal=MONGO_ROOT_PASSWORD="$(gen)" \
  --from-literal=MONGO_APP_PASSWORD="$(gen)" \
  --from-literal=MONGO_MONITOR_PASSWORD="$(gen)"

crear app-secret \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)"

# Contraseña de las cuentas sintéticas de demostración (admin@ejemplo.test, jugadorNN@ejemplo.test)
crear semilla-secret \
  --from-literal=SEED_PASSWORD="$(openssl rand -hex 8)"

echo "Secretos listos en el namespace $NS (valores no mostrados)."
