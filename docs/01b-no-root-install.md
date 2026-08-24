# No-Root Quick Start

This guide is for anyone who wants to run ANetBBS without root/sudo
access at all — because they don't have it on the box they're using,
or simply don't want to hand a script `sudo bash install.sh` on a
2,000+ line file they haven't audited line by line. Both are
reasonable positions, and this is a genuinely complete, first-class
way to run ANetBBS, not a crippled fallback.

Everything in this guide has been verified end to end on a real,
unprivileged account: install, boot, and serve real HTTP traffic,
with `sudo` never invoked once.

## What "no root" actually gets you — and what it doesn't

Be clear-eyed about the boundary before you start, so nothing here
surprises you later.

**Fully available with zero root, no exceptions:**

- The web UI (default port 5000)
- Telnet (default port 2233)
- SSH (default port 2234)
- Echomail (FidoNet-style BinkP/QWK networks), file areas, doors, the
  wiki, RSS reader, and everything else that isn't a network listener
  on a privileged port

None of ANetBBS's default ports are privileged (below 1024) — that's
a deliberate design choice this guide's rootless path depends on, not
an accident.

**Genuinely requires root or an administrator's one-time help:**

- **MSP / SYSTAT** (inter-BBS instant messaging, ports 18 and 11) —
  both are below 1024. The no-root installer below disables them by
  default so you don't see a permission-denied error on every boot;
  turn them on later if you get root.
- **Finger** (RFC 1288, port 79) — also privileged, not offered by
  this installer at all.
- **rlogin at its conventional port 513** — privileged. You *can*
  still run rlogin without root by picking a port above 1024 (the
  wizard will warn you and let you choose one); you just can't use
  the traditional port 513 without root.
- **FTP at its conventional port 21** — same story; not something
  this wizard prompts for at all (see `docs/INSTALL.md` §9 if you
  want it later).
- **nginx / TLS in front of the web UI** — putting a reverse proxy in
  front requires writing to `/etc/nginx/`, which needs root by
  definition. Without it you're running plain HTTP directly on
  whatever `WEB_PORT` you chose. If you later get a domain and TLS,
  or someone with root, `docs/INSTALL.md` §7 covers the nginx side.
- **Installing OS-level packages you don't already have** (see
  Prerequisites below) — this is the one place a truly bare system
  might need someone else's help, and it's a small, one-time ask, not
  an ongoing dependency on root.

If none of the "requires root" items above matter to you — and for a
lot of people trying ANetBBS out, none of them do — everything else in
this document gets you a fully working BBS, permanently, on your own
account.

## Prerequisites

You need Python 3.10 or newer with the standard `venv` and `pip`
modules available. On most modern Linux systems (and inside most
shared hosting / VPS environments from the last several years) this
is already true — check before assuming you need to ask anyone for
anything:

```bash
python3 --version          # need 3.10 or newer
python3 -m venv --help      # confirms the venv module is present
python3 -m pip --version    # confirms pip is present
```

If all three succeed, you have everything you need and can skip
straight to Step 1.

If `python3 -m venv` fails (common on Debian/Ubuntu, where the venv
module is sometimes split into a separate `python3-venv` package from
the base `python3` install) or `pip` is missing, this is the one
legitimate exception where you may need to ask whoever administers
the box to run something for you, once:

```bash
sudo apt install python3 python3-venv python3-pip
```

(or the equivalent for your distro — `dnf install python3 python3-pip`
on Fedora/RHEL, `apk add python3 py3-pip` on Alpine, and so on).

You do **not** need to pre-install `build-essential`/`libffi-dev` or
any other compiler toolchain up front. Modern `pip` pulls prebuilt
wheels for every dependency this project uses on common platforms
(x86_64/aarch64 Linux); a compiler is only needed if `pip install`
ends up trying to build something from source, which is the exception,
not the rule. If you do hit a build failure, the error will name the
specific package that needs compiling — ask for `build-essential`
(Debian/Ubuntu) at that point, not before.

## Step 1: Get the code

Clone the repository, or extract a release tarball, into a directory
**you own** — your home directory, not `/opt` (which typically isn't
writable by a non-root account, by design):

```bash
git clone <repo-url> ~/anetbbs
cd ~/anetbbs
```

or:

```bash
tar xzf ANetBBS-vX.Y.Z.tar.gz -C ~/
cd ~/ANetBBS-vX.Y.Z
```

## Step 2: Run the installer

```bash
python3 -m anetbbs.installer.wizard
```

This works straight out of the checkout — the wizard itself has zero
external dependencies (it only uses the Python standard library), so
there's no `pip install` step needed just to *launch* it. The wizard
is what creates a dedicated virtual environment and installs the
application's real dependencies into it, entirely under the directory
you choose in the next step — nothing is ever installed system-wide.

You'll be walked through a short series of prompts. Defaults (shown
in `[brackets]`) are sensible for a first install — press Enter to
accept any of them. A few worth knowing about ahead of time:

- **Install directory** — defaults to the checkout you're running
  from. Anywhere you own is fine.
- **Public domain (or IP)** — leave this blank if you're just testing
  locally, or don't have one yet. You can add it to `.env` later.
- **Sysop password** — minimum 8 characters, entered twice to confirm.
  This becomes your admin login.
- **Enable rlogin?** — defaults to *off*. If you turn it on, the
  wizard will warn you that its conventional port (513) is privileged
  and let you pick a port above 1024 instead.

The wizard then, without any further prompts, creates the virtual
environment, installs Python dependencies into it, initializes the
SQLite database, and creates your admin account. This step (dependency
installation) is the slowest part — expect it to take roughly a
minute.

## Step 3: Choose how to keep it running

Near the end, the wizard asks how you want the BBS to run
persistently, with three real choices:

```
1) System-wide systemd service — needs root once, to write
   /etc/systemd/system/*. Starts on boot for everyone.
2) User systemd service — no root at all. Runs under your own
   account (systemctl --user); survives logout via loginctl
   linger, which most systems let you enable for yourself.
3) Skip — run it yourself (a terminal, tmux, screen, or your
   own supervisor).
```

If you're following this guide, you don't have root, so option **1**
is off the table by definition (it will fail with a clear "not
writable — re-run with sudo" error if you try it anyway — no harm
done, just skips that step). That leaves two real choices:

### Option 2: systemd user service (recommended)

This is the closest thing to "a real background service" you can get
without root. `systemd --user` is a per-account instance of systemd
that manages services scoped to your own login, entirely independent
of the system-wide instance in `/etc/systemd/system` — no special
permissions needed to use it.

The wizard writes unit files to `~/.config/systemd/user/` and enables
them for you. To actually start the BBS and keep it running:

```bash
systemctl --user start anetbbs-web anetbbs
```

(`anetbbs` here is the combined telnet/SSH/rlogin process — it only
starts if you enabled at least one of those protocols.)

By default, a systemd `--user` instance stops when you log out —
`loginctl enable-linger` is what keeps it running (and lets it start
on boot) independent of any active login session:

```bash
loginctl enable-linger $USER
```

On most systems this needs no root either — many distributions ship a
default policy (via `polkit`) that lets an account enable linger for
*itself*. If it fails with a permission error, that's the one
genuinely system-dependent exception in this whole guide: ask whoever
administers the box to run that exact command for your account, once.
Nothing else in this workflow depends on it succeeding — without
linger, the BBS still runs fine for as long as you stay logged in
(including over SSH, as long as the session stays open), it just won't
survive a logout or reboot on its own.

Useful commands once it's running:

```bash
systemctl --user status anetbbs-web anetbbs   # is it up?
journalctl --user -u anetbbs-web -f            # tail the web log live
systemctl --user restart anetbbs-web anetbbs   # after a config change
systemctl --user stop anetbbs-web anetbbs      # stop it
```

### Option 3: run it yourself

If you'd rather not deal with systemd at all — inside a container, on
a system where `systemd --user` genuinely isn't available, or you just
want to watch the logs directly — start the two processes by hand.
`tmux` or `screen` are the natural tools here, since closing your
terminal would otherwise kill them:

```bash
tmux new -s anetbbs-web
~/anetbbs/venv/bin/python ~/anetbbs/deploy/serve.py
# Ctrl-b d to detach

tmux new -s anetbbs-terminal
~/anetbbs/venv/bin/anetbbs
# Ctrl-b d to detach
```

(Skip the second one if you didn't enable telnet, SSH, or rlogin.)

The wizard's own final summary prints the exact commands for your
install directory, so you don't need to remember the paths above —
just copy what it prints.

## Step 4: Log in and verify

Visit `http://localhost:5000/` (or whichever host/port you chose) and
log in with the sysop username and password you set during the
wizard. From there:

- `/admin/checklist` — a live health check confirming everything is
  actually configured and reachable.
- `/admin/control` — day-to-day operations (restart services, view
  logs, manage the queue) from the web UI itself.

If you enabled telnet or SSH, test those too:

```bash
telnet localhost 2233
ssh -p 2234 <your-sysop-username>@localhost
```

## Upgrading later

`anetbbs-upgrade` (`python3 -m anetbbs.installer.upgrade`, or the
`venv/bin/anetbbs-upgrade` shortcut once your venv exists) is the
rootless counterpart to `update.sh` — point it at a new release
tarball or extracted directory, and it backs up your `data/`, `.env`,
and `logs/`, syncs the new code in, reinstalls dependencies, runs
schema migrations, and restarts whichever service mode you're running
(system, user, or none — it detects which). No root required for any
of that either, for exactly the same reasons the install itself
doesn't need it.

## If you get root access later

Nothing about starting rootless locks you out of the full feature set
later. If you (or an administrator) later want MSP/SYSTAT/Finger/FTP,
a system-wide service, or nginx+TLS in front, none of your existing
setup needs to be redone — `docs/INSTALL.md` covers each of those as
additive steps on top of exactly what you already have. Your data,
`.env`, and install directory all carry forward unchanged; nothing
here is a dead end.

## Troubleshooting

- **`python3 -m venv` fails with "ensurepip is not available"** —
  confirms the `python3-venv` (or distro equivalent) package is
  missing; see Prerequisites above.
- **`pip install` fails trying to compile something** — see the note
  at the end of Prerequisites; the error names the specific package,
  ask for `build-essential` (or your distro's compiler toolchain) at
  that point.
- **`systemctl --user` says `Failed to connect to bus`** — your
  system may not have a running user systemd instance (some minimal
  containers and older init setups don't). Use option 3 (run it
  yourself) instead; this isn't something you can fix without a
  different base system.
- **`loginctl enable-linger $USER` fails with a permission error** —
  see the note under Option 2 above; ask a box administrator to run
  it once, or just accept that the service stops when you log out
  (still fully functional while you're logged in).
- **MSP/SYSTAT/rlogin(:513)/FTP(:21) fail to bind** — expected; these
  are privileged ports and this installer deliberately leaves them off
  by default (MSP/SYSTAT) or warns you about the port choice (rlogin).
  See "What this doesn't give you" above.
- **Symlink step at the end shows `failed:need sudo`** — expected and
  harmless; this only affects the convenience shortcuts in
  `/usr/local/bin` (typing `anetbbs-web` instead of
  `venv/bin/anetbbs-web`). Everything else in the install is
  unaffected. Add `~/anetbbs/venv/bin` to your own `$PATH` if you want
  the short names without needing root for the shared location.
