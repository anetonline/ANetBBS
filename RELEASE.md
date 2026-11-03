# ANetBBS v1.1.66 — CI security-scan fix (November 2026)

A security scan (bandit) caught a real finding in v1.1.65's own fix, after that release had already shipped: the new helper that gives door games a reliable writable storage location built its fallback path from a hardcoded `/tmp` literal inline, rather than through the same pattern already used (and already annotated) for the equivalent fallback a few lines above in the same file.

Not a functional bug — the fallback path is only ever used when application config isn't loaded yet (effectively test-only), and the resulting path was always correct. This release simply consolidates the fallback through the existing, already-reviewed pattern so the scanner's static analysis (and anyone reading the code) can see that clearly, with no behavior change for live installs.
