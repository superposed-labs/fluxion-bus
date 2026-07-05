# Telegram Adapter

This directory contains the Telegram `ChannelAdapter` implementation.

To initialize it:

1. Create a bot with Telegram's `@BotFather`.
2. Set `FLUXION_TELEGRAM_ENABLED=true` and `TELEGRAM_BOT_TOKEN`.
3. Optionally set `FLUXION_TELEGRAM_ALLOWED_USERS` to comma-separated numeric
   Telegram user IDs.
4. Start `fluxion-gateway`.

See `docs/configuration.md#telegram` for the full setup notes.
