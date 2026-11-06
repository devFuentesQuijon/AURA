#!/bin/sh
# Túnel SSH hacia el API del sitio (el mismo que corre en el host hacia la
# VM de QA), pero viviendo DENTRO de la red aura: así el backend lo alcanza
# por DNS (http://aura-live-tunnel:5200) sin depender del loopback del host
# ni de reglas de firewall hacia la máquina anfitriona.
set -e

DEST=${TUNNEL_DEST:-desarrollo@10.121.105.51}
REMOTE=${TUNNEL_REMOTE:-localhost:5200}
PUERTO=${TUNNEL_PORT:-5200}

KEY_SRC=""
for k in id_rsa id_ed25519; do
  if [ -f "/ssh/$k" ]; then KEY_SRC="/ssh/$k"; break; fi
done
if [ -z "$KEY_SRC" ]; then
  echo "aura-live-tunnel: no encontré key SSH en /ssh (monta ~/.ssh del host)"
  exit 1
fi

# ssh exige que la key sea propia y 600; en el mount viene con dueño del
# uid del host, así que se copia a /tmp con dueño root.
cp "$KEY_SRC" /tmp/tunnel_key
chmod 600 /tmp/tunnel_key

echo "aura-live-tunnel: $DEST -L 0.0.0.0:${PUERTO}:${REMOTE} (key $(basename "$KEY_SRC"))"
exec ssh -N \
  -i /tmp/tunnel_key \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -L "0.0.0.0:${PUERTO}:${REMOTE}" \
  -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile=/tmp/known_hosts \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  "$DEST"
