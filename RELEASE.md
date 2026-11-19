# ANetBBS v1.1.70 — Code-scan CI fixes

Follow-up to v1.1.69. The Internet Mail body renderer now relies on
markupsafe's own escaping (`Markup.format()` / `Markup.join()`) to build its
HTML, clearing a bandit B704 finding that failed the `code-scan` CI workflow.
A duplicated, identical `send_email()` definition in `anetbbs/mailer.py` was
also removed, clearing the pyflakes failure in the same workflow.
Rendered output is byte-identical to v1.1.69; no behavior or configuration
changes. Includes all v1.1.69 Internet Mail delivery and notification work.
