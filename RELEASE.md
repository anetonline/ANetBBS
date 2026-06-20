# ANetBBS v1.0a2.139 — Fix 500 on BBS Directory

## Changes

### Hotfix: BBS Directory 500 error

`PeerBbs` model was missing the new columns introduced in v1.0a2.138
(`is_approved`, `telnet_port`, `web_url`, `location`, `software`,
`submitted_by_user_id`). `_ensure_column` adds the columns to the SQLite
DB on startup, but SQLAlchemy also requires them to be defined as
`db.Column` attributes on the model class — otherwise `filter_by()` raises
`InvalidRequestError: Entity namespace for "peer_bbses" has no property
"is_approved"`. Fixed in `models.py`.
