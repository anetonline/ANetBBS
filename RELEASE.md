# ANetBBS v1.1.72 — terminal message scan and user-delete guidance

Private test candidate. Fixes terminal session message scanning so the
read-only scan runs within a Flask application context and can find unread
private/instant mail, Internet Mail, message-board posts, and accessible
subscribed echomail. The terminal user can jump into the relevant reader.

The Admin → Users confirmation previews directly linked row counts and offers
a separate permanent purge only after the operator types the exact username.
The purge runs in one transaction and rolls back if the database finds a
reference it cannot safely remove. Bulk, Pending Users, and Inactive Users
deletion paths provide the schema-aware CLI dry-run instructions when history
blocks an ordinary deletion. No user data is deleted automatically by this
change.
