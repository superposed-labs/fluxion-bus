# LINE Adapter

This directory contains the LINE Messaging API `ChannelAdapter`
implementation.

To initialize it:

1. Create a Messaging API channel in the LINE Developers Console.
2. Set `FLUXION_LINE_ENABLED=true`, `LINE_CHANNEL_SECRET`, and
   `LINE_CHANNEL_ACCESS_TOKEN`.
3. Expose the local port `8766` webhook server with a public tunnel.
4. Register `https://<your-tunnel-domain>/line/webhook` as the webhook URL.
5. Optionally set `FLUXION_LINE_ALLOWED_USERS` to comma-separated LINE user IDs.
6. Start `fluxion-gateway`.

If you use `setup_tunnel.sh`, pass your own Cloudflare hostname:

```bash
FLUXION_LINE_TUNNEL_DOMAIN=line.example.com ./src/fluxion/channels/line/setup_tunnel.sh
```

The LINE channel secret comes from the channel's basic settings. The channel
access token is issued from the Messaging API settings. Add the bot as a friend
from the channel QR code before sending tasks.

See `docs/configuration.md#line` for the full setup notes.
