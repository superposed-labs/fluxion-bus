# Deployment units

Keep-alive service templates for the long-running Fluxion surfaces. The
**scheduler is the only service you need to run continuously** for time- and
quota-triggered tasks — it hosts its own executor, so it does not depend on the
Slack gateway or the web UI. Run the UI too if you want to manage schedules and
watch history in the browser.

Every template uses a `__FLUXION_REPO__` placeholder. Replace it with the
absolute path to your checkout (the `sed` recipes below do this from the repo
root).

## macOS (launchd, per-user)

```bash
# from the repo root, with the .venv created and `pip install -e .` done:
sed "s#__FLUXION_REPO__#$PWD#g" deploy/launchd/com.fluxion.scheduler.plist \
  > ~/Library/LaunchAgents/com.fluxion.scheduler.plist
launchctl load -w ~/Library/LaunchAgents/com.fluxion.scheduler.plist

# optional web console:
sed "s#__FLUXION_REPO__#$PWD#g" deploy/launchd/com.fluxion.web.plist \
  > ~/Library/LaunchAgents/com.fluxion.web.plist
launchctl load -w ~/Library/LaunchAgents/com.fluxion.web.plist

# optional provider gateway — see the warning below before loading this one:
sed "s#__FLUXION_REPO__#$PWD#g" deploy/launchd/com.fluxion.provider.plist \
  > ~/Library/LaunchAgents/com.fluxion.provider.plist
launchctl load -w ~/Library/LaunchAgents/com.fluxion.provider.plist
```

Logs land in `data/logs/scheduler.{out,err}.log`. Unload with
`launchctl unload ~/Library/LaunchAgents/com.fluxion.scheduler.plist`.

### The provider gateway needs exactly one supervisor

The macOS app starts a gateway of its own when Preferences → Services →
Provider Gateway is on. Set `FLUXION_PROVIDER_ENABLED=false` in `.env` before
loading the launchd unit, or the two race for port 8787: the one that loses the
bind exits immediately, and `KeepAlive` restarts it every ten seconds for as
long as the machine is up. Nothing is visibly broken while that happens — the
winner serves every request — so the symptom is a log full of
`address already in use` and a `launchctl kickstart` that deploys nothing.

## Linux (systemd, per-user)

```bash
mkdir -p ~/.config/systemd/user
sed "s#__FLUXION_REPO__#$PWD#g" deploy/systemd/fluxion-scheduler.service \
  > ~/.config/systemd/user/fluxion-scheduler.service
systemctl --user daemon-reload
systemctl --user enable --now fluxion-scheduler
loginctl enable-linger "$USER"   # keep running after logout

# follow logs:
journalctl --user -u fluxion-scheduler -f
```

## Notes

- Every template loads the rest of your config from `.env` via
  `FLUXION_ENV_FILE`; `FLUXION_WORKSPACE_ROOT` / `FLUXION_DATA_DIR` are set
  explicitly so the service always agrees with the CLI on where state lives.
- The provider gateway template also sets `PATH`. It serves a turn by running a
  local agent CLI (`agy`, `claude`, `codex`), and a launchd job inherits a
  nearly empty `PATH` — without it the gateway starts fine and every turn fails
  at exec time.
- Set `FLUXION_SCHEDULER_ENABLED=true` in `.env` to document intent (the daemon
  runs regardless when launched explicitly, but the flag is the canonical
  on/off switch).
- See [docs/scheduler.md](../docs/scheduler.md) for rule schema, trigger types,
  and the data files the daemon reads/writes.
