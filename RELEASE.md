# ANetBBS v1.1.61 — Follow-up audit pass (October 2026)

Following the string of sudo-escalation and auto-update fixes shipped over the past several releases (v1.1.54 through v1.1.60), ran a focused audit over everything that changed in that window to catch anything those fixes missed or introduced. Six issues found and fixed, each with new regression test coverage:

- The pre-launch system check (Admin → Preflight) could report the auto-update sudo grant as healthy while the real upgrade path stayed broken — it was testing the wrong one of two sudoers grants. Fixed by having both the real upgrade code and the preflight check build the exact same command from one shared source, so they can't drift apart again.
- The pre-login anti-bot challenge could, in a narrow case, be satisfied by ordinary terminal-negotiation traffic alone, with no real keystroke involved. Fixed by routing it through the same telnet-negotiation-aware read path already used elsewhere in the terminal stack.
- A scheduled stale-session cleanup task could, if one specific row hit an unexpected error, skip cleaning up every other unrelated stale row for that run too. Fixed by isolating each row's handling.
- The admin BinkP-node creation form's duplicate-address check could miss that two differently-formatted address strings name the same real FTN address, allowing an accidental duplicate node entry. Fixed to use the same address normalization already applied elsewhere.
- A reference systemd unit file (a hand-maintained example, not used directly by the installer/updater) still showed an older setting without the newer explanatory guidance added elsewhere — corrected to match.
- A new automated test added alongside last release's auto-update fix only checked that the right pieces existed, not that they were in the right order. Tightened so it actually catches the class of mistake it was meant to catch.

Also corrected two documentation pages that still described the old, now-removed `--scope`-based upgrade mechanism from before v1.1.59.

No sysop action is required beyond the normal update — this release doesn't change the recommended deploy path established in v1.1.58/v1.1.59.
