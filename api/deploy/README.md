# Installing the `vaultos-api` systemd `--user` service

Runs the spine as a persistent, boot-surviving service instead of a
foreground `uvicorn --reload`. `Restart=on-failure` brings it back after a
crash; no sandboxing directives yet (deferred until the service has run
stably for a while).

Both unit files use `/path/to/Vault-Os-Api` as a placeholder — replace it
with the absolute path of your clone before installing (systemd units need
absolute paths; `~` is not expanded there). The same goes for the
placeholder paths in the commands below.

## 1. Create the environment file

`EnvironmentFile=` points at `~/.config/vaultos-api/env` — a file dedicated
to this service, separate from `~/.claude/.env` (which `runner.js` already
owns).

```sh
mkdir -p ~/.config/vaultos-api
cat > ~/.config/vaultos-api/env <<'EOF'
VAULT_ROOT=/path/to/your/vault
# Optional overrides (defaults shown in vaultos/config.py):
# VAULTOS_DB=/path/to/Vault-Os-Api/data/vaultos.db
# TOKEN_BUDGET_5H_USD=100
# HUD_TZ=America/Chicago
EOF
```

Note: `VAULTOS_PORT` is **not** a usable override here — `deploy/vaultos-api.service`'s
`ExecStart` pins `--port 3109` directly on the uvicorn command line, which always wins
over the app's own (unused-in-this-path) `VAULTOS_PORT` reading. To run this service on
a different port, edit the `--port` flag in the unit file itself.

## 2. Install the unit

```sh
mkdir -p ~/.config/systemd/user
ln -sf /path/to/Vault-Os-Api/deploy/vaultos-api.service \
  ~/.config/systemd/user/vaultos-api.service
```

## 3. Enable and start

```sh
systemctl --user daemon-reload
systemctl --user enable --now vaultos-api.service
```

## 4. Verify

```sh
curl -s http://127.0.0.1:3109/health
curl -s http://127.0.0.1:3109/state | head -c 200
systemctl --user restart vaultos-api.service
curl -s http://127.0.0.1:3109/health   # confirms it comes back up after a restart
```

## Updating after a code change

```sh
systemctl --user restart vaultos-api.service
```

# Installing the `vaultos-calendar-pull` timer

Fetches a single iCal (`.ics`) feed URL periodically and writes today's events to
`system/metrics/calendar-today.json`, which `GET /calendar` reads. Entirely separate from
`vaultos-api.service` — a calendar-pull failure can never affect the spine's own uptime.

## 1. Add the secret feed URL

`vaultos-calendar-pull.service`'s `EnvironmentFile=` points at `~/.claude/.env` —
deliberately *not* the spine's own dedicated `~/.config/vaultos-api/env` (see
`docs/adr/0009-calendar-data-via-periodic-puller-not-live-fetch.md`): `CALENDAR_ICAL_URL` is
a secret in the same class as `ANTHROPIC_API_KEY`, already conventionally stored there, and
`~/.claude/.env` already carries the `VAULT_ROOT` this service also needs.

```sh
# add to ~/.claude/.env (alongside the secrets already there):
# CALENDAR_ICAL_URL=https://calendar.google.com/calendar/ical/<id>/private-<secret>/basic.ics
```

Leaving `CALENDAR_ICAL_URL` unset is a supported, no-op state — the puller exits 0 with a
stderr note and the calendar simply never populates.

## 2. Install the unit + timer

```sh
mkdir -p ~/.config/systemd/user
ln -sf /path/to/Vault-Os-Api/deploy/vaultos-calendar-pull.service \
  ~/.config/systemd/user/vaultos-calendar-pull.service
ln -sf /path/to/Vault-Os-Api/deploy/vaultos-calendar-pull.timer \
  ~/.config/systemd/user/vaultos-calendar-pull.timer
```

## 3. Enable and start

```sh
systemctl --user daemon-reload
systemctl --user enable --now vaultos-calendar-pull.timer
```

## 4. Verify

```sh
systemctl --user start vaultos-calendar-pull.service   # run once immediately, don't wait for the timer
journalctl --user -u vaultos-calendar-pull.service -n 20
curl -s http://127.0.0.1:3109/calendar
systemctl --user list-timers vaultos-calendar-pull.timer
```
