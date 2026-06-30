# MSP Loopback Test (ANetBBS ↔ ANetBBS)

This procedure verifies that two ANetBBS instances can exchange Inter-BBS
Instant Messages over MSP (RFC 1312) and that SYSTAT/ActiveUser query
also works between them. Use it after upgrading the MSP module to confirm
nothing regressed.

## What it tests

1. MSP message wire delivery (TCP/18-equivalent)
2. Inbound persistence in the recipient's `InstantMessage` table
3. SYSTAT/ActiveUser UDP query + Finger-style reply parsing
4. The web `/imsg/send` route's `user@host` paste-friendly parsing

## Run it

```bash
cd /opt/anetbbs
python3 tests/msp_loopback_test.py
```

The test:
- Creates two isolated SQLite DBs in `/tmp/anetbbs-loopback-a` and `-b`
- Runs instance B's MSP listener on port **12118** and SYSTAT on **12111**
  (chosen above the privileged-port boundary so no setcap is needed)
- Acts as instance A in-process — sends one direct MSP via `send_msp()`,
  one via the `/imsg/send` HTTP route with `admin@127.0.0.1` pasted into
  the recipient field

## Expected output

```
=== ANetBBS MSP loopback test ===

[Setup] Building instance B (listener) ...
  instance B: MSP 12118, SYSTAT 12111, dir /tmp/anetbbs-loopback-b

[1] Sending MSP from A to B (admin@127.0.0.1)
  ✓  send_msp returned True

[2] Verify B persisted the message
  ✓  InstantMessage row exists with correct body

[3] SYSTAT query 127.0.0.1:12111 (B)
  ✓  reply contains BBS name
  ✓  reply mentions "active"

[4] /imsg/send accepts "admin@127.0.0.1" pasted into recipient
  ✓  /imsg/send returned redirect (success)
  ✓  B received the second message
```

All six checks must pass.

## What to check on a real two-host setup

If you have two ANetBBS hosts on the network:

1. On **both** hosts, ensure the MSP listener is bound (not "permission
   denied" in the bbs.log). See INSTALL.md for the three options to bind
   to the privileged ports 11/UDP and 18/TCP.

2. Confirm with `nc` from either host:
   ```bash
   nc -zv other.bbs.example 18    # TCP MSP
   ```
   "Connection succeeded" = port is reachable.

3. From host A, log in as a user, open **Messaging → Inter-BBS IM (MSP)**,
   click **Send IM**, paste `username@other.bbs.example`, type a message,
   submit. Flash should say "Instant message sent to ...".

4. On host B, the recipient logs in and opens the same menu — the message
   appears in their IM inbox with sender label `username@A_BBS_NAME`.

## Troubleshooting

- "Connection refused" on port 18 → listener never bound. Check the bbs.log
  for `MSP: cannot bind ... Permission denied`. Apply one of the three
  privilege fixes in INSTALL.md.
- Sent OK but never received → check the recipient name on the remote BBS.
  The MSP server falls back from `username` to `display_name` but that's
  it — there's no fuzzy match.
- SYSTAT reply garbled → the responder is probably emitting CP437 box
  glyphs that need rendering. The `query_systat()` client decodes UTF-8
  first then falls back to latin-1, so high bytes survive but won't show
  pretty in a plain text view.
