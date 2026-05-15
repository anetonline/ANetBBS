# Ports used by ANetBBS

| Port  | Proto | Default | Service        | Purpose / required for                           | Config var          |
| ----- | ----- | ------- | -------------- | ------------------------------------------------ | ------------------- |
| 5000  | TCP   | yes     | Web (Flask)    | Browser interface — every web feature            | `WEB_PORT`          |
| 2233  | TCP   | yes     | Telnet         | Classic terminal login                           | `TELNET_PORT`       |
| 2234  | TCP   | yes     | SSH            | SSH terminal login                               | `SSH_PORT`          |
| 513   | TCP   | off     | rlogin         | Legacy rlogin (disable unless you need it)       | `RLOGIN_PORT`       |
| 21    | TCP   | off     | FTP            | FTP file-area access (anon + authenticated)      | `FTP_PORT`          |
| 40000-40050 | TCP | off  | FTP (passive)  | Data channels for the FTP control session above  | `FTP_PASV_PORTS`    |
| 24554 | TCP   | yes     | BinkP          | FidoNet echomail/netmail mailer                  | per-network in DB   |
| 18    | TCP   | yes     | MSP            | Inter-BBS Instant Message inbound (RFC 1312)     | `MSP_PORT`          |
| 11    | UDP   | yes     | SYSTAT         | "Who's online" lookup from peer BBSes (Finger-style) | `SYSTAT_PORT`   |
| 79    | TCP   | yes     | Finger         | RFC 1288 Finger — per-user info queries          | `FINGER_LISTEN_PORT`|
| 8080  | TCP   | yes     | MRC web bridge | Standalone service in `mrc/bridge/`              | bridge config       |

## Listening interface

All defaults bind `0.0.0.0`. Override with `WEB_HOST`, `MSP_BIND_HOST`,
`SYSTAT_BIND_HOST`, `TELNET_HOST`, `SSH_HOST`.

## Privileged ports

Ports < 1024 (Finger/79, MSP/18, SYSTAT/11, FTP/21, rlogin/513) require
root or the `CAP_NET_BIND_SERVICE` capability. See `docs/INSTALL.md` §6
for the three fix options. The MSP/SYSTAT/Finger services log a clear
`Permission denied` error and exit cleanly if the bind fails — the rest
of the BBS keeps running.

For the FTP server specifically, the cleanest fix is a systemd drop-in:
```bash
sudo systemctl edit anetbbs.service
# Add:
# [Service]
# AmbientCapabilities=CAP_NET_BIND_SERVICE
# CapabilityBoundingSet=CAP_NET_BIND_SERVICE
sudo systemctl daemon-reload && sudo systemctl restart anetbbs.service
```

Or run FTP on `FTP_PORT=2121` and put an iptables redirect / nginx-stream
proxy in front.

## Outbound ports the BBS uses

| Port  | Proto | When                                              |
| ----- | ----- | ------------------------------------------------- |
| 18    | TCP   | Sending an MSP message to another BBS             |
| 11    | UDP   | Querying SYSTAT on another BBS                    |
| 24554 | TCP   | BinkP poll to a FidoNet hub                       |
| 21    | TCP   | FTP fetch of `sbbsimsg.lst` from `vert.synchro.net` (daily) |
| 80/443| TCP   | QWK download from a hub that uses HTTP            |
| 53    | UDP   | DNS lookups for any hostname-based config         |

## Firewall rules (iptables)

Open the inbound services your BBS publishes. Example:

```bash
sudo iptables -A INPUT -p tcp --dport 5000  -j ACCEPT   # web (or use nginx 80/443)
sudo iptables -A INPUT -p tcp --dport 2233  -j ACCEPT   # telnet
sudo iptables -A INPUT -p tcp --dport 2234  -j ACCEPT   # ssh
sudo iptables -A INPUT -p tcp --dport 21    -j ACCEPT   # ftp control
sudo iptables -A INPUT -p tcp --dport 40000:40050 -j ACCEPT  # ftp passive data
sudo iptables -A INPUT -p tcp --dport 24554 -j ACCEPT   # binkp
sudo iptables -A INPUT -p tcp --dport 18    -j ACCEPT   # msp
sudo iptables -A INPUT -p udp --dport 11    -j ACCEPT   # systat
sudo iptables -A INPUT -p tcp --dport 79    -j ACCEPT   # finger
```

To get listed in the official `sbbsimsg.lst` directory on Vertrauen,
ports **18/TCP** and **11/UDP** must be reachable from the internet.
Test with:

```bash
nc -zv your-bbs-host 18           # TCP
echo | nc -uvw 2 your-bbs-host 11 # UDP
```
