# Running ANetBBS in Docker

ANetBBS ships two Docker deployment options alongside the traditional
`install.sh`/systemd path (which is still the primary, most-tested way
to run a production BBS). Neither replaces the other:

- **Single-container quick start** — one image, one container, every
  service (web, terminal, MRC bridge, finger, binkp) managed inside it
  by `supervisord`. Fastest way to try ANetBBS, and the best place to
  start if this is your first time with any of this.
- **docker-compose — the "correct" way** — one container per service,
  matching the 5 systemd units a traditional install runs. Proper
  per-service health checks, logs, and restart control. This is the
  recommended option for anything beyond kicking the tires — do the
  single-container test first to confirm the basics work, then move
  to this.

Both share one image (`docker/Dockerfile`) — the only difference is
which entrypoint/command each container runs.

**No pre-built image is published anywhere yet.** Every command below
builds the image from your own source checkout first. `docker pull` /
a registry reference only becomes real once a release is actually
tagged and pushed — don't run `docker pull ghcr.io/...` yet, it won't
find anything.

**A transparency note on how tested this path is:** the Dockerfile,
entrypoints, and both compose layouts described below have been built
and unit-tested with mocked Docker/subprocess calls, but have **not**
yet been run end-to-end against a real Docker daemon — no actual
`docker build` or `docker compose up` has been done on this yet. The
steps below are believed correct and are written from a real reading
of the files in `docker/`, but if you hit something that doesn't match
what's documented here, that's exactly the kind of gap this note is
warning you about — please report it.

## If you're new to Docker — a few concepts first

- An **image** is a packaged snapshot of the application (code +
  dependencies). A **container** is a running instance of an image —
  like the difference between a program's installer and the program
  actually running.
- `docker build` turns source code into an image. `docker run` (or
  `docker compose up`) starts a container from an image.
- Anything a container writes to its own filesystem is thrown away
  when the container is removed, *unless* it's inside a **volume** —
  a piece of storage that lives outside the container and survives
  `docker stop`/`docker rm`. This is why the commands below always
  mount a volume for `data/` (your database, uploads, etc.) — deleting
  the *container* never deletes your *data*, but deleting the *volume*
  does.
- `-p 5000:5000` means "make port 5000 on my machine forward to port
  5000 inside the container." The first number is your machine, the
  second is inside the container.

## One-time setup: run `docker` without `sudo`

If `docker ps` gives you a permission error, your user isn't in the
`docker` group yet:

```bash
sudo usermod -aG docker $USER
```

**Close your terminal and open a new one** — group membership changes
don't apply to already-open sessions. Confirm it worked:

```bash
docker ps
```

An empty table (just column headers, no permission error) means
you're good.

## Step 1 — build the image

From the root of your ANetBBS source checkout:

```bash
docker build -f docker/Dockerfile -t anetbbs:local .
```

The first build takes a few minutes (compiling a few Python
dependencies that don't have prebuilt wheels for every platform). If
it fails partway through, that's useful — it means something in the
Dockerfile needs adjusting; save the full output.

## Step 2 — try the single-container quick start

```bash
cp .env.docker.example .env
```

Edit `.env` in a text editor and fill in at least `SECRET_KEY` (any
random string), `BBS_NAME`, `SYSOP_NAME`, `BBS_EMAIL`.

```bash
docker run -d --name anetbbs \
    --env-file .env \
    -e ANETBBS_RUNTIME=docker-single \
    -e ANETBBS_DB_URL=sqlite:////app/data/anetbbs.db \
    -e DATABASE_URL=sqlite:////app/data/anetbbs.db \
    --cap-add NET_BIND_SERVICE \
    -p 5000:5000 -p 2233:2233 -p 2234:2234 \
    -p 18:18 -p 11:11/udp -p 79:79 -p 8080:8080 -p 24554:24554 \
    -v anetbbs_data:/app/data \
    anetbbs:local \
    /usr/local/bin/entrypoint-single.sh
```

Watch it start:

```bash
docker logs -f anetbbs
```

`Ctrl-C` to stop watching (the container keeps running in the
background either way). Confirm all 5 services actually came up:

```bash
docker exec anetbbs supervisorctl -c /app/docker/single/supervisord.conf status
```

You should see all 5 as `RUNNING`:

```
web           RUNNING   pid 8, uptime 0:02:15
terminal      RUNNING   pid 9, uptime 0:02:15
mrc-bridge    RUNNING   pid 10, uptime 0:02:15
finger        RUNNING   pid 11, uptime 0:02:15
binkp         RUNNING   pid 12, uptime 0:02:15
```

If any show `FATAL`/`BACKOFF` instead, check that specific service's
output:

```bash
docker exec anetbbs supervisorctl -c /app/docker/single/supervisord.conf tail web
```

Now actually use it:
- **Web admin**: open `http://localhost:5000` in a browser.
- **Telnet**: `telnet localhost 2233`
- **SSH**: `ssh -p 2234 anyuser@localhost`

### Cleaning up

```bash
docker stop anetbbs && docker rm anetbbs
```

Your data survives this — it's in the `anetbbs_data` volume, not the
container. Only delete the volume if you want a completely fresh
start (this DOES delete your database/uploads):

```bash
docker volume rm anetbbs_data
```

## Step 3 — the docker-compose setup (recommended once the above works)

```bash
mkdir ~/anetbbs-docker-test && cd ~/anetbbs-docker-test
cp /path/to/anetbbs-source/docker/compose/docker-compose.yml .
cp /path/to/anetbbs-source/.env.docker.example .env
cp /path/to/anetbbs-source/docker/compose/mrc-bridge-config.json.example mrc-bridge-config.json
```

Edit `.env`:
- Fill in `SECRET_KEY`, `BBS_NAME`, `SYSOP_NAME`, `BBS_EMAIL` as before.
- Since nothing's published to a registry yet, point at the image you
  built locally in Step 1:
  ```
  ANETBBS_IMAGE=anetbbs
  ANETBBS_IMAGE_TAG=local
  ```
- See the `MRC_BRIDGE_HOST` warning below before you skip past it.

```bash
docker compose up -d
docker compose ps
```

Give it 15-30 seconds, then `docker compose ps` should show every
service as `healthy`. Useful commands from here:

```bash
docker compose logs -f web       # tail one service's logs
docker compose logs -f           # tail all of them
docker compose restart web       # restart one service
docker compose down              # stop + remove containers (data volume survives)
```

### Enabling rlogin / FTP

Both default off (matching `install.sh`'s own default-off posture).
Uncomment their `ports:` lines in `docker-compose.yml`'s `terminal`
service and set `RLOGIN_ENABLED=true` / `FTP_ENABLED=true` in `.env`.
**FTP passive mode doesn't yet support NAT traversal** in this
codebase (no `masquerade_address` support) — under Docker's default
bridge networking, passive FTP transfers will likely fail. If you need
FTP working today, run the `terminal` container with `network_mode:
host` instead, which sidesteps Docker's NAT entirely.

## The docker.sock trust boundary (read this before enabling)

The `web` service's compose entry mounts `/var/run/docker.sock` into
the container so the Sysop Control Panel can restart/upgrade sibling
containers via the Docker API instead of `systemctl` (which doesn't
exist inside a container at all). **This grants root-equivalent
control of the Docker host to anything that can reach the panel's
restart/upgrade endpoints.** It's the same tradeoff tools like
Portainer and Watchtower make, and it's why those endpoints are
already gated behind `@login_required`/admin-only — but it's worth
understanding plainly before you opt in.

If you'd rather not accept that: delete the `/var/run/docker.sock`
volume line from the `web` service. Everything else keeps working —
the panel just won't be able to restart/upgrade sibling containers for
you; run `docker compose restart <service>` / `docker compose pull &&
up -d` by hand instead.

A future hardening option (not implemented, documented for later): put
a scoped proxy like `tecnativa/docker-socket-proxy` between the web
container and the real socket, exposing only the specific API
operations the panel needs (containers list/start/stop/restart,
images pull) instead of the full Docker API.

## `MRC_BRIDGE_HOST` — a real gotcha

Set this in `.env` to **your public domain**, not the compose service
name (`mrc-bridge`). The browser connects to the MRC bridge's
WebSocket **directly** — it isn't proxied through Flask — so setting
this to a Docker-internal service name (unreachable from outside the
Docker network) will silently break MRC chat for web users even though
everything looks fine in `docker compose ps`.

## Why this is safe with SQLite (no Postgres required)

ANetBBS today runs as 5 separate OS processes (one per systemd unit)
that all open their own connection to the same `data/anetbbs.db`
SQLite file. That's exactly the same shape as 5 separate containers
doing the same thing — and it's safe under Docker for the identical
reason it's safe on bare metal: every container runs as the **same
fixed user (UID 1000)** and shares **one local Docker volume** for
`data/`. WAL mode + a busy-timeout are enabled at the SQLAlchemy engine
level (`anetbbs/models.py`) so concurrent writers retry instead of
immediately failing under brief contention.

**Do not** back the `anetbbs_data` volume with NFS, CIFS, or any other
network filesystem — that's the one case where SQLite's file-locking
guarantees genuinely break down. A plain local Docker volume (the
default `local` driver, which is what the commands above use) is the
only supported backing store.

Postgres is mentioned in some docstrings as theoretically supported,
but there's no driver dependency and no tested migration path in this
codebase today — don't treat it as a real option for a container
deployment yet.

## What's not supported inside containers yet

These all degrade gracefully (the existing code already logs a clear
"skip" message rather than crashing) — none of them break the rest of
the BBS, they just won't work:

- **DOSBox / Mystic BBS / Node.js door games** — none of these runtimes
  are bundled in the image. `GAMES_ENABLED` stays on; DOS/Mystic/Node
  door types specifically report themselves unavailable.
- **ClamAV virus scanning** — shells out to a local `clamscan` binary,
  not bundled in the image, and there's no ClamAV-daemon-over-TCP
  client implemented yet for a sidecar-container setup.
- **`.lzh` archive extraction (`lhasa`)** and **sixel images in the
  terminal RSS reader (`libsixel-bin`)** — same story, not bundled.
- **FTP passive-mode NAT traversal** — see above.

## Self-upgrade

`docker-compose` mode supports one-click self-upgrade from the admin
panel (Admin → Upgrades), same UI as bare metal. Behind the scenes it
launches a detached, ephemeral sibling container (from the same image,
which bundles the `docker` CLI + compose plugin) that runs `docker
compose pull && up -d --remove-orphans` against your own
`docker-compose.yml`/`.env` — the container analogue of the
`systemd-run --scope` cgroup-escape trick bare-metal installs use so
the upgrader survives the web container being recreated mid-upgrade.

This needs `ANETBBS_COMPOSE_DIR` set in `.env` to the absolute host
path containing your `docker-compose.yml` (defaults to wherever you
ran `docker compose up` from, which is usually right — only override
if you launch compose from a script/cron job with a different working
directory each time). This only matters once real images are being
published somewhere pullable — not relevant while you're pointing
`.env` at a locally built `anetbbs:local` image.

**Single-container mode has no in-place upgrade button** — clicking
"Install Update" there returns a clear message instead of attempting
anything, since a container replacing its own single running image is
a meaningfully harder problem than recreating a sibling container.
Pull the new tag and recreate the container by hand instead (same
`docker stop && docker rm` + re-run the `docker run` command from
Step 2 above, with a new image tag).

## Multi-arch

Images are meant to be built for both `linux/amd64` and `linux/arm64`
(Raspberry Pi and other ARM hosts) via `docker buildx`, once there's a
registry to push to:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
    -f docker/Dockerfile -t your-registry/anetbbs:yourtag --push .
```

For local-only testing (Step 1 above), a plain `docker build` targets
just your own machine's architecture, which is all you need to test on
one box.

## Ports reference

See `docs/PORTS.md` for the full port list and privileged-port
background. Container-specific notes:

- **Finger (79)** binds to an unprivileged port (`1079`) inside its
  container and gets remapped to 79 via Docker's port publish — no
  `cap_add` needed for that container at all.
- **MSP (18)** and **SYSTAT (11, UDP)** stay bound to their real ports
  inside the `web` container, which needs `cap_add: [NET_BIND_SERVICE]`
  in compose (mirrors the bare-metal systemd unit's
  `AmbientCapabilities=CAP_NET_BIND_SERVICE`). If your platform doesn't
  allow `cap_add`, override `MSP_PORT`/`SYSTAT_PORT` in `.env` to
  unprivileged values and remap them the same way Finger does.

## Troubleshooting

- **`docker: permission denied`** — you're not in the `docker` group
  yet, or you added yourself but didn't open a new terminal. See "One-
  time setup" above.
- **Build fails on a specific pip package** — save the full output and
  report it; some dependency may need a from-source build on your
  specific CPU architecture, which can need an extra system package.
- **A service shows `FATAL`/`BACKOFF` in `supervisorctl status`** —
  run `supervisorctl ... tail <program>` (single-container) or `docker
  compose logs <service>` (compose) to see why it's crash-looping.
- **Port already in use** — something else on your machine (maybe a
  bare-metal ANetBBS install!) is already using that port. Either stop
  the other thing or change the *host* side of the `-p host:container`
  mapping (e.g. `-p 5001:5000` to use 5001 on your machine instead).
- **Can't reach the web UI / telnet from another device on your
  network** — confirm the port mappings are actually published (`docker
  ps` shows them in the "PORTS" column) and that your machine's
  firewall allows those ports.
