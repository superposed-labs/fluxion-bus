#!/bin/bash
set -e

echo "=== Fluxion LINE Webhook Cloudflare Tunnel Setup ==="

# Check if logged in
if [ ! -f ~/.cloudflared/cert.pem ]; then
  echo "Error: Cloudflare credentials not found. Please run 'cloudflared tunnel login' first."
  exit 1
fi

TUNNEL_NAME="${FLUXION_LINE_TUNNEL_NAME:-fluxion-line}"
DOMAIN="${FLUXION_LINE_TUNNEL_DOMAIN:-}"
PORT="${PORT:-8766}"

if [ -z "$DOMAIN" ]; then
  echo "Error: FLUXION_LINE_TUNNEL_DOMAIN is not set."
  echo "Example: FLUXION_LINE_TUNNEL_DOMAIN=line.example.com $0"
  exit 1
fi

echo "Creating tunnel: $TUNNEL_NAME..."
# Check if tunnel already exists
EXISTING_TUNNEL=$(cloudflared tunnel list | grep "$TUNNEL_NAME" || true)

if [ -n "$EXISTING_TUNNEL" ]; then
  echo "Tunnel $TUNNEL_NAME already exists."
  TUNNEL_ID=$(echo "$EXISTING_TUNNEL" | awk '{print $1}')
else
  TUNNEL_INFO=$(cloudflared tunnel create "$TUNNEL_NAME")
  echo "$TUNNEL_INFO"
  # Try to extract UUID
  TUNNEL_ID=$(echo "$TUNNEL_INFO" | grep -oE "Created tunnel $TUNNEL_NAME with id [a-f0-9-]+" | awk '{print $NF}')
  # Fallback if grep matches differently
  if [ -z "$TUNNEL_ID" ]; then
    TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
  fi
fi

if [ -z "$TUNNEL_ID" ]; then
  echo "Could not retrieve Tunnel ID. Please check the output above."
  exit 1
fi

echo "Tunnel ID: $TUNNEL_ID"

echo "Routing DNS subdomain $DOMAIN to tunnel..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN"

echo "Writing configuration file ~/.cloudflared/config.yml..."
mkdir -p ~/.cloudflared

cat <<EOF > ~/.cloudflared/config.yml
tunnel: $TUNNEL_NAME
credentials-file: /Users/$(whoami)/.cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $DOMAIN
    service: http://localhost:$PORT
  - service: http_status:404
EOF

echo "Configuration written successfully!"
echo "--------------------------------------------------"
echo "You can now run the tunnel using:"
echo "  cloudflared tunnel run $TUNNEL_NAME"
echo ""
echo "And run your webhook server in another terminal:"
echo "  python3 src/fluxion/channels/line/dev_line_webhook.py"
echo "--------------------------------------------------"
