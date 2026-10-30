#!/usr/bin/env bash
# Crea (o reutiliza) una CA local del laboratorio y emite un certificado para el host indicado.
# La CA y las llaves quedan en ~/pki (FUERA del repositorio). Importa ca.crt en Windows para que
# el navegador confíe en el certificado.
# Uso:  ./crear-tls.sh canchas-dev.lab
set -euo pipefail
HOST="${1:?Indica el nombre de host, por ejemplo canchas-dev.lab}"
NS="${2:-canchas}"
PKI="$HOME/pki"
mkdir -p "$PKI" && chmod 700 "$PKI"

if [ ! -f "$PKI/ca.crt" ]; then
  openssl req -x509 -newkey rsa:4096 -nodes -days 365 \
    -keyout "$PKI/ca.key" -out "$PKI/ca.crt" \
    -subj "/CN=Canchas Lab CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"
  chmod 600 "$PKI/ca.key"
fi

openssl req -newkey rsa:2048 -nodes -keyout "$PKI/$HOST.key" -out "$PKI/$HOST.csr" -subj "/CN=$HOST"
openssl x509 -req -in "$PKI/$HOST.csr" -CA "$PKI/ca.crt" -CAkey "$PKI/ca.key" -CAcreateserial \
  -out "$PKI/$HOST.crt" -days 90 \
  -extfile <(printf "subjectAltName=DNS:%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n" "$HOST")
chmod 600 "$PKI/$HOST.key"

kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"
kubectl -n "$NS" create secret tls canchas-tls --cert="$PKI/$HOST.crt" --key="$PKI/$HOST.key" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Certificado emitido para $HOST. CA para importar en Windows: $PKI/ca.crt"
