# ANetBBS on Windows

ANetBBS itself is pure Python with no Windows-specific code anywhere —
`install.sh`, the systemd units, and every path in the codebase assume
a Linux userspace. Rather than maintain a second, parallel native-
Windows install/service-management path, the supported route on
Windows is to give ANetBBS a real Linux environment to run in. Two
ways to do that, both well-trodden and both free:

- **WSL2 + Ubuntu** — a real Linux kernel running alongside Windows.
  `install.sh` runs completely unmodified inside it, exactly like a
  native Ubuntu box. This is the closer-to-native option and the one
  to reach for if you want systemd units, `ufw`, and the full
  `install.sh` wizard experience.
- **Docker Desktop** — if you'd rather not manage a Linux install at
  all. See [`docs/22-containers.md`](22-containers.md) for the full
  Docker walkthrough; this page only covers what's different running
  it under Docker Desktop specifically rather than Docker-on-Linux.

**A transparency note on how tested this page is:** everything below
is written from how WSL2/Docker Desktop are documented to behave and
how the existing Linux install path already works — this sandbox has
no way to run an actual Windows box, so the WSL2 path in particular
hasn't been confirmed end-to-end against real WSL2 the way the Docker
single-container path has against a real Linux Docker daemon (see the
transparency note at the top of `docs/22-containers.md`). If you try
this on a real Windows machine and something doesn't match what's
written here, please report it.

## Option 1 — WSL2 + Ubuntu

### Install WSL2

From an elevated (Administrator) PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

This enables the WSL2 feature, downloads Ubuntu, and asks you to set a
Unix username/password on first launch. Requires Windows 10 version
2004+ or Windows 11 — recent installs already meet this.

### Run the normal Linux install inside it

Open the "Ubuntu" app from the Start menu — you're now at a real Bash
prompt in a real (if virtualized) Linux kernel. From here, everything
in [`docs/INSTALL.md`](INSTALL.md) applies exactly as written: same
`apt install` prerequisites, same `install.sh`, same systemd units
(WSL2 supports systemd as of Windows 11 22H2 / recent Windows 10
updates — Ubuntu 24.04's default WSL image has it enabled already; if
`systemctl` doesn't work, add `systemd=true` under `[boot]` in
`/etc/wsl.conf` and run `wsl --shutdown` from PowerShell, then reopen
Ubuntu).

```bash
tar xzf ANetBBS-vX.Y.Z.tar.gz
cd ANetBBS-vX.Y.Z
sudo bash install.sh
```

Pick **test** mode when the wizard asks for install mode unless you've
actually got a domain pointed at this machine — WSL2's networking sits
behind Windows' own NAT, so treat it the same as any other NAT/behind-
a-router install (`docs/01-installing.md`'s **test**/**behind** mode
guidance applies).

### Reaching it from Windows and the rest of your network

WSL2 gets its own internal IP, separate from Windows' — `localhost`
from a Windows browser or terminal client generally reaches WSL2's
services automatically on recent WSL2 builds (`localhost` forwarding),
so `telnet localhost 2233` / `http://localhost:8080` from Windows
itself should just work. Reaching it from a **different** machine on
your LAN (or the internet) needs a port-forward from Windows to the
WSL2 VM's IP, since WSL2's networking is NAT'd behind Windows the same
way a VM's would be:

```powershell
# Find WSL2's current internal IP (changes on reboot unless pinned)
wsl hostname -I

# Forward each port you want reachable from outside Windows
netsh interface portproxy add v4tov4 listenport=2233 listenaddress=0.0.0.0 connectport=2233 connectaddress=<wsl-ip>
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=<wsl-ip>
```

Repeat per port (telnet 2233, SSH 2234, web 5000/8080, etc. — see
[`docs/PORTS.md`](PORTS.md) for the full list), and open the matching
Windows Firewall rule. This mirrors exactly what a router's port-
forward table does for a normal home-network install — WSL2 is just
one more layer of NAT to punch through.

### Filesystem performance note

Keep the ANetBBS install directory (and `data/`) inside WSL2's own
filesystem (`~/anetbbs` or similar, under `/home/<user>/`), not on a
Windows drive mounted at `/mnt/c/...`. SQLite's frequent small writes
are notably slower across the WSL2↔Windows filesystem boundary — same
underlying reason the Pi install guide recommends an SSD over a slow
SD card for `data/` (see
[`docs/INSTALL-PI.md`](INSTALL-PI.md#why-ssd-matters)).

## Option 2 — Docker Desktop

Follow [`docs/22-containers.md`](22-containers.md) — the build steps,
`docker run`/`docker compose` commands, and troubleshooting are
identical. Three things differ specifically under Docker Desktop on
Windows:

- **Enable the WSL2 backend.** Docker Desktop → Settings → General →
  "Use the WSL 2 based engine" — this is the default on current
  Docker Desktop versions, but worth confirming; the older Hyper-V
  backend has real networking/volume-mount quirks that WSL2 mode
  avoids.
- **Bind-mount paths use Windows drive syntax** if you're running
  `docker`/`docker compose` from PowerShell rather than from inside a
  WSL2 shell — e.g. `-v C:\Users\you\anetbbs-data:/app/data` instead
  of a Linux-style path. Simplest fix: run the `docker compose`
  commands from inside your WSL2 Ubuntu shell instead (Docker Desktop
  integrates with WSL2 distros automatically, sharing the same Docker
  engine) — then every path in `docs/22-containers.md` works exactly
  as written, no translation needed.
- **Privileged low ports (18, 11, 79)** for MSP/SYSTAT/Finger need the
  same `docker-compose.yml` capability grants `docs/22-containers.md`
  already documents — nothing Windows-specific there, just easy to
  miss if you're skimming.

## Which option to pick

WSL2 if you want the full sysop experience (systemd units, `anetbbs-
cfg`/`anetbbs-monitor` on the console, `ufw`, the complete
`install.sh` wizard) — closest to how `bbs.a-net.fyi` itself actually
runs. Docker Desktop if you'd rather not manage a Linux environment at
all and are fine with container-level service management instead of
systemd. Both are free, both are well-supported upstream (WSL2 by
Microsoft, Docker Desktop by Docker Inc.), and both point at the exact
same ANetBBS codebase with no fork or Windows-specific branch to keep
in sync.
