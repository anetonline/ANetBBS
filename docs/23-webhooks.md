# Webhooks

**Admin → Settings → Webhooks** (`/admin/webhooks`). Outbound-only:
when a supported event happens on the BBS, ANetBBS POSTs a JSON body
to a URL you configure — useful for mirroring activity into Discord,
Slack (via an incoming-webhook-compatible endpoint), or your own
logging service. There is no reverse direction — nothing here lets an
external service push data *into* ANetBBS.

> **Not the same thing as "firing" a Scheduled Event.** Admin →
> Scheduled Events also has a `fire()` function (used by its "Run
> now" button) — same word, completely unrelated code. See
> [`21-scheduled-events.md`](21-scheduled-events.md) if that's what
> you're actually looking for.

## Supported events

All eight event types in the New Webhook dropdown fire for real:

| Event | Fires when | Fires from |
|---|---|---|
| `shout` | A public shoutbox post is made | Web only (shoutbox is web-only) |
| `post` | A new board topic or reply is posted | Web and terminal |
| `bulletin` | A sysop creates a new bulletin | Web (admin-only action) |
| `login` | A user logs in | Web, telnet, SSH, and rlogin |
| `achievement` | A user earns a new badge | Wherever the triggering action happens (web) |
| `broadcast` | A multinode chat / "Local Chat" line goes out | Terminal only |
| `sysop_page` | A user pages the sysop | Terminal (and web, if paging is exposed there) |
| `echomail` | A new inbound echomail message is imported (BinkP or QWK) | Background poller |

There is **no per-event enable/disable beyond the webhook row
itself** — if you create a webhook for `login`, it fires on every
login, from every service, with no way to scope it to "web logins
only" short of checking `service` in your own receiving code (see the
payload table below).

## Creating a webhook

Fields on the Add form:

- **Name** — free text, admin-list display only.
- **Event** — one of the eight above.
- **URL** — where the POST goes. The placeholder in the form is a
  Discord incoming-webhook URL, since that's the most common target.
- **On** — checkbox, enabled by default. Unchecking disables delivery
  without deleting the row.
- **Body template** (optional) — see [Custom body
  templates](#custom-body-templates) below. Leave blank for the
  default JSON shape.
- **Bearer secret** (optional) — see [Security](#security) below.

There is **no edit action** — to change a webhook's URL, event,
template, or secret, delete it and add a new one. There is also **no
manual "send test" button** — the only way to see a webhook actually
fire is to trigger the real event.

## Payload format

There's no shared envelope, no `event` name, no timestamp, and no
version field baked into the body automatically — it's exactly the
payload dict the triggering feature built, JSON-encoded, and **the
available keys are different for every event.** There's no universal
placeholder set — check the row for your specific event before
writing a custom template.

| Event | Payload keys |
|---|---|
| `shout` | `user`, `text`, `content` (`"{user}: {text}"`, convenience field for single-string targets like Discord) |
| `post` | `user`, `board_id`, `subject`, `content` |
| `bulletin` | `user`, `title`, `content` |
| `login` | `user`, `service` (`web` / `telnet` / `ssh` / `rlogin`) |
| `achievement` | `user`, `code` (e.g. `first_post`), `name` (friendly label, e.g. "Getting Started") |
| `broadcast` | `user`, `text`, `kind` (`msg` / `join` / `part` / `sysop`) |
| `sysop_page` | `user`, `service`, `text` |
| `echomail` | `from_name`, `subject`, `area_tag`, `content` |

`shout`'s `text` and `post`/`bulletin`/`echomail`'s content fields
have already passed through word-filtering (where applicable) by the
time the webhook fires — you're seeing what real users would see, not
raw unfiltered input.

Default rendering (no template) is just `json.dumps(payload)` — e.g.
for `login`:
```json
{"user": "jerry", "service": "telnet"}
```

## Custom body templates

If you fill in **Body template**, `{key}` placeholders get replaced
with values from the event's payload dict via plain string
substitution — not a JSON-aware merge. Only keys that exist for that
specific event (see the table above) will substitute; anything else
is left as a literal `{whatever}` in the output.

**Discord example** (works for any event with a `content`-shaped
field — `shout`, `post`, `bulletin`, `echomail`):
```json
{"content": "{content}"}
```

**For events without a `content` key** (`login`, `achievement`,
`broadcast`, `sysop_page`), build your own string from the fields
that exist, e.g. for `achievement`:
```json
{"content": "{user} just earned: {name}"}
```

**Be careful with quotes and backslashes in free-text fields**
(`text`, `content`). Substitution is a literal string replace with no
escaping — if the underlying text contains a `"` or `\`, it goes
straight into your template unescaped and can break the resulting
JSON. Leaving the template blank avoids this entirely (the default
path uses `json.dumps`, which escapes correctly); only use a custom
template if you specifically need a non-default shape like Discord's,
and treat any user-supplied text field as untrusted when you do.

## Security

There is **no HMAC/signature verification** on outbound requests —
don't design a receiver expecting something like an
`X-ANetBBS-Signature` header, because none is sent. The only
authentication is a plain shared secret: if you set **Bearer secret**,
every request carries:

```
Authorization: Bearer <your-secret>
```

Your receiving endpoint's job is to compare that header against the
value you configured, out of band, the same way you'd check an API
key. There's no per-request signing and no replay protection — anyone
who has the secret (or intercepts an unencrypted request) can replay
or forge a request. Use HTTPS URLs, and treat the secret like a
password. If you leave it blank, requests go out with no
`Authorization` header at all.

**Example receiver (Flask):**
```python
from flask import Flask, request, abort

app = Flask(__name__)
EXPECTED_SECRET = "the-secret-you-set-in-anetbbs-admin"

@app.route("/anetbbs-hook", methods=["POST"])
def anetbbs_hook():
    if request.headers.get("Authorization") != f"Bearer {EXPECTED_SECRET}":
        abort(401)
    data = request.get_json(force=True, silent=True) or {}
    # data's keys depend on which event this webhook was created for --
    # see the payload table above.
    return "", 204
```

## Delivery mechanics

- **Always POST**, regardless of what's stored — the model has a
  `method` field but nothing reads it.
- **Fire-and-forget, one attempt, no retries.** Delivery runs in a
  background thread so a slow or dead endpoint never blocks the
  action that triggered it (posting, logging in, etc.), but there's
  no queue and no re-attempt if it fails — a delivery that fails is
  simply lost.
- **8-second timeout**, fixed, not configurable.
- **No rate limiting** at the webhook layer itself. Some triggering
  features have their own unrelated limits that indirectly cap
  frequency (e.g. the shoutbox's 5-posts/60s anti-spam limit for
  non-admins), but nothing throttles webhook delivery specifically —
  a busy multinode chat session firing `broadcast` rapidly will fire
  a webhook (and spin up a background thread) just as rapidly.
- **HTTP error responses from your endpoint are not treated as
  failures.** If your receiver returns a 404 or 500, that status code
  is recorded as `Last status` with no error badge — the admin list
  only shows a red "err" badge for network-level failures (timeout,
  DNS failure, connection refused). A silently-broken receiving
  endpoint that returns 500 will look "fine" in the ANetBBS admin
  list; check your own endpoint's logs, not just this page, if
  messages aren't showing up on the other end.
- **No delivery history beyond the single most recent attempt** —
  `Last status` and the error badge reflect only the last time this
  webhook fired, not a log of every delivery.

## Worked example: mirroring the shoutbox to Discord

1. In Discord: Server Settings → Integrations → Webhooks → New
   Webhook, copy its URL (`https://discord.com/api/webhooks/...`).
2. Admin → Webhooks → Add:
   - **Name**: `Discord shoutbox mirror`
   - **Event**: `shout`
   - **URL**: the Discord webhook URL from step 1
   - **Body template**: `{"content": "{content}"}`
   - Leave **Bearer secret** blank — Discord's webhook URL is itself
     the secret; it doesn't check an `Authorization` header.
3. Save, then post something in the BBS's shoutbox. It should show up
   in the Discord channel within a few seconds.

The same pattern works for `post`, `bulletin`, and `echomail` — all
three have a `content` key, so the same `{"content": "{content}"}`
template works unchanged; just pick a different **Event** and,
usually, a different Discord channel webhook URL.

## Troubleshooting

- **`Last status` stays "—" forever** — the webhook has never
  actually been triggered. Confirm the underlying action has actually
  happened (posted, logged in, etc.) since the webhook was created;
  it won't retroactively fire for anything that already happened
  before it was added.
- **`Last status` shows a real HTTP code (200, 404, 500...) but
  nothing shows up on the receiving end** — that's not an ANetBBS
  problem; the request reached your server, and your server responded
  with that code. Check your receiver's own logs/config.
- **Red "err" badge** — a network-level failure (timeout, DNS,
  connection refused) on the last attempt. Check the URL is correct
  and reachable from the ANetBBS server specifically (not just from
  your own machine).
- **JSON looks malformed on the receiving end** — likely a custom
  **Body template** getting broken by an unescaped `"` or `\` in a
  free-text field (see [Custom body
  templates](#custom-body-templates)). Switch to a blank template
  (default JSON encoding) if you don't need a specific non-default
  shape.
- **`{something}` shows up literally, unsubstituted, in the delivered
  body** — that key doesn't exist in this event's payload. Check the
  [payload table](#payload-format) above for the exact keys available
  per event; there's no universal placeholder set.
