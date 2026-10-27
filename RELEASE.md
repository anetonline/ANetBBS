# ANetBBS v1.1.60 — CI-only test fix (October 2026)

v1.1.59's new test coverage for the auto-update button fix cross-references `deploy/sudoers.anetbbs` against the upgrade command the web button builds. That file is deliberately excluded from the Docker image (see `.dockerignore` — it's a bare-metal-only concern, never baked into the container), so the Docker-build CI workflow's in-container test run failed with a missing-file error even though the same test passes everywhere else.

Fixed by skipping that specific check when the file isn't present, consistent with the exclusion `.dockerignore` already documents. No functional or runtime code changed — this release exists solely to keep the Docker-build CI check green. No action needed from sysops beyond the normal update if they want it; v1.1.59's actual fix (the auto-update button itself) is unaffected either way.
