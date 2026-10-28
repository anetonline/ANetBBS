# ANetBBS v1.1.64 — CI security-scan fix (October 2026)

A security scan (bandit) caught a real finding in v1.1.63's own fix, after that release had already shipped: the code that lets both the web process and the detached root upgrade unit write to the same progress-log file loosened that file's permissions to world-writable (`0o666`) to guarantee both sides could always write to it.

That's unnecessary — root already bypasses standard file permission checks regardless of mode, so the only direction that genuinely needed defending (the web process needing to reclaim a file a past root-owned write left behind) was already covered by a separate remove-and-recreate fallback. A world-writable progress log is needless exposure on a box with other local accounts, so the loosening step was simply removed rather than suppressed.

No sysop-visible behavior changes; applying this release corrects the file's permissions going forward. The exposure window was a transient progress log (install-step text only, no credentials or secrets ever written to it), not a credential or data leak.
