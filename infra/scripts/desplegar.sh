#!/usr/bin/env bash
# Despliega un ambiente con una versión (etiqueta de imagen) concreta.
# Uso:  ./desplegar.sh desarrollo sha-abc1234
# Deja la etiqueta desplegada anotada en el kustomization (queda registrada en Git).
set -euo pipefail
AMB="${1:?Ambiente: desarrollo | produccion}"
TAG="${2:?Etiqueta de imagen, por ejemplo sha-abc1234}"
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
OVERLAY="$RAIZ/infra/$AMB"
NS=canchas

[ -d "$OVERLAY" ] || { echo "No existe $OVERLAY"; exit 1; }

sed -i "s|newTag: .*|newTag: $TAG|" "$OVERLAY/kustomization.yaml"

echo ">> Validando manifiestos"
kubectl kustomize "$OVERLAY" >/dev/null

# Los Job no se pueden modificar: se recrea para correr las migraciones de esta versión
kubectl -n "$NS" delete job migrar --ignore-not-found

echo ">> Aplicando $AMB con imagen $TAG"
kubectl apply -k "$OVERLAY"

echo ">> Esperando bases de datos"
kubectl -n "$NS" rollout status statefulset/postgres --timeout=300s
kubectl -n "$NS" rollout status statefulset/mongo --timeout=300s

echo ">> Esperando migraciones"
kubectl -n "$NS" wait --for=condition=complete job/migrar --timeout=300s

echo ">> Esperando backend"
kubectl -n "$NS" rollout status deployment/canchas-backend --timeout=300s

kubectl -n "$NS" get pods -o wide
