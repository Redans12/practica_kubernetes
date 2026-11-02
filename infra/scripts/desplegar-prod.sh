#!/usr/bin/env bash
# Despliega PRODUCCIÓN con la imagen fijada por DIGEST: es exactamente la misma imagen (mismos bytes)
# que se probó en desarrollo; una etiqueta puede moverse, un digest no.
# Uso:  ./desplegar-prod.sh sha256:<64 caracteres hexadecimales>
set -euo pipefail
DIGEST="${1:?Digest de la imagen, por ejemplo sha256:abc...}"
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Digest inválido: debe ser sha256: seguido de 64 caracteres hexadecimales"; exit 1; }
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
OVERLAY="$RAIZ/infra/produccion"
NS=canchas

sed -i "s|digest: sha256:[^ ]*|digest: $DIGEST|" "$OVERLAY/kustomization.yaml"

echo ">> Validando manifiestos"
kubectl kustomize "$OVERLAY" >/dev/null

# Los Job no se pueden modificar: se recrea para correr las migraciones de esta versión
kubectl -n "$NS" delete job migrar --ignore-not-found

echo ">> Aplicando producción con imagen $DIGEST"
kubectl apply -k "$OVERLAY"

echo ">> Esperando bases de datos"
kubectl -n "$NS" rollout status statefulset/postgres --timeout=300s
kubectl -n "$NS" rollout status statefulset/mongo --timeout=300s

echo ">> Esperando migraciones"
kubectl -n "$NS" wait --for=condition=complete job/migrar --timeout=300s

echo ">> Esperando backend (el HPA lo lleva a 2 réplicas como mínimo)"
kubectl -n "$NS" wait --for=jsonpath='{.status.readyReplicas}'=2 deployment/canchas-backend --timeout=300s

kubectl -n "$NS" get pods -o wide
kubectl -n "$NS" get hpa,pdb,networkpolicy
