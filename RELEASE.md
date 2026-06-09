# ANetBBS v1.0a2.97 — Fix: native Linux door games (RMDoor/DDPlus/FPC) — carrier drop on launch

## What's new

### Fix: `door_native` games instantly drop carrier on launch

All doors built with the RMDoor / DDPlus Free Pascal door toolkit (the
dominant toolkit for new Linux BBS door development) produced an immediate
"Carrier Dropped" exit as soon as the game launched.

**Root cause:** RMDoor on Linux compiles with `{$DEFINE COMM_SOCKET}` and
routes all I/O through `fpSend(fd, ...)` / `fpRecv(fd, ...)` — POSIX socket
syscalls. ANetBBS previously wrote DOOR32.SYS with `CommType=1` (FOSSIL) and
`CommNum=0` (stdin fd). When the door called `CommOpen(0)`, it issued
`fpSend(0, …)` on the PTY slave fd, which is not a socket — this returned
`ENOTSOCK`, set `FCarrier = false`, and triggered the hangup handler within
milliseconds of startup.

**Fix:** For `door_native` games, DOOR32.SYS is now written with
`CommType=2, CommNum=-1` — the Mystic BBS Linux STDIO convention. RMDoor's
`door.pas` explicitly checks for `ComNum == -1` on UNIX and switches to
`Write(StdOut, …)` / `ReadKey` (stdin), which are PTY-safe. This is the same
mechanism Mystic, Synchronet, and ENiGMA½ use when running RMDoor-based doors
via a PTY/pipe rather than a raw socket.

This fix applies automatically to all `door_native` games — no per-game
reconfiguration needed. DOS doors (`door_dos`) are unaffected; they continue
to use CommType=1 / FOSSIL via the TCP nullmodem bridge.

**Game admin config for RMDoor/DDPlus doors:**
- Game Type: `door_native`
- Drop File Type: `door32.sys`
- Drop File Path: `%P` (per-node temp dir, appends DOOR32.SYS automatically)
- Command Line Args: `-D%f` (`%f` expands to full DOOR32.SYS path)
