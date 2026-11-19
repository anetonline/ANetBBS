# ANetBBS development and release workflow

## Canonical checkout

`/home/jerry/AIANETBBS/anetbbs-rebuilt` is the canonical working checkout for ANetBBS development on ClaudeDELL. Make source, documentation, and release-preparation changes here. Other extracted trees, test installations, and generated archives are snapshots or test artifacts; do not treat them as competing source checkouts.

Before importing work from a snapshot, compare it with this checkout and preserve changes already present here. Never copy runtime state such as `.env`, logs, databases, caches, or user mailboxes into the source tree or release archive.

## Build and verification

Run the repository's `build-release.sh` from this checkout to produce a private test archive. Review its output and checksum, inspect the archive contents, and test the archive on the intended test installation before considering a public release. Update `VERSION`, `RELEASE.md`, `docs/CHANGELOG.md`, and the user documentation together when preparing a versioned candidate.

The release builder includes tracked files and non-ignored untracked files, plus the explicitly bundled game assets. This allows a local private test build before committing, but also means every non-ignored untracked file can enter the archive. Review `git status --short` and the archive contents before distributing it.

## GitHub sync script

`/home/jerry/AIANETBBS/sync_to_github.sh` operates on this canonical checkout. It stages all changes, creates a commit, and creates a version tag. Inspect the full diff and `git status` first, run the relevant checks, and ensure the version tag does not already exist. The script does not publish the changes by itself; pushing the branch and tag is a separate action. Do not run the script as a substitute for review or as part of a private test build.

## ClaudeVM access

ClaudeVM should work from a separate checkout or a read-only snapshot of the canonical source. Avoid simultaneous edits to the same working tree. Until a verified ClaudeVM host and an approved sharing path are configured, transfer reviewed patches or snapshots explicitly and reconcile them back into the canonical checkout before a release build.
