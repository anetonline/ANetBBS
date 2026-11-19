# Internet Mail

Internet Mail gives opted-in ANetBBS users an address at
`mailbox-name@your-mail-domain`. Users can receive mail addressed to the
BBS and can send mail to external addresses from the WebUI or terminal
clients. This feature is independent of Echomail, FidoNet Netmail, local
message boards, private messages, and account-recovery email.

Internet Mail is deliberately gated at two levels. A sysop enables the
feature and chooses its domain and access policy. Each eligible user then
opts in and chooses a mailbox name. The mail listener can be installed and
running while the application feature remains disabled.

## Where to find it

### Sysop settings

- WebUI: sign in as a sysop, open **Admin (gear) → Settings → Internet
  Mail**. The existing **SMTP / Email** page configures the outbound relay;
  it is a separate setting. The Internet Mail settings page is also at
  `/admin/mail`.
- Terminal: run `anetbbs-cfg` and choose **Internet Mail**. This edits the
  same database settings as the WebUI page. Listener bind settings remain
  under **System / Network Settings → Internet Mail (SMTP receiver)**,
  because those values come from `.env` and are read when a service starts.

### User mail

After the sysop enables the feature, eligible signed-in users see
**Messaging → Set up Internet Mail** until they opt in. That entry opens
**Profile → Edit**. Once opted in, the entry becomes **Messaging → Internet
Mail** and opens the inbox; an unread badge is shown when mail is waiting.
The mailbox provides **Inbox**, **Sent**, and **Compose** actions. Users can
also use `/mail`, `/mail/sent`, and `/mail/compose` directly. Non-admin
accounts below the sysop's minimum access level cannot newly opt in;
administrator/Sysop accounts are always eligible regardless of their
separate numeric access level. An account that had already opted in remains
usable if the sysop later raises that threshold;
the user can still turn their mailbox off from their profile.

## How delivery works

- **Inbound:** `anetbbs-mail.service` runs a separate SMTP receiver. It
  listens on the configured SMTP host and port (normally port 25) and
  stores accepted messages in the addressed user's Internet Mail inbox.
  No Postfix or Exim service is required in front of ANetBBS.
- **Outbound:** Internet Mail reuses the existing relay configured on
  **Admin → Settings → SMTP / Email**. It does not have a second relay
  configuration. Verification and password-reset email use that relay too.
- **Account recovery:** existing account-verification and password-reset
  messages are separate from a user's Internet Mail mailbox and are not
  controlled by the Internet Mail opt-in.

## Sysop setup, in order

1. **Choose a domain you control.** Every enabled mailbox becomes
   `mailbox-name@domain`. Do not use a private hostname such as
   `raspberrypi.local` for public Internet Mail; other mail servers cannot
   deliver to it.
2. **Publish DNS for inbound delivery.** Create an MX record for the chosen
   domain pointing to a public mail host, and make that host resolve to the
   BBS public IP with an A record (and AAAA only if IPv6 delivery is
   actually available). The MX target must not be a CNAME. Allow DNS time to
   propagate.
3. **Check inbound network access.** Permit inbound TCP port 25 through the
   host firewall, router/NAT, and ISP or hosting provider. A local test from
   the Pi does not prove that another mail server on the Internet can
   connect. Some residential ISPs and cloud providers block port 25.
4. **Set up the outbound relay.** In **Admin → Settings → SMTP / Email**,
   configure the relay host, port, sender name/address, and TLS mode. Add
   relay credentials only if the provider requires them. Authenticated
   SMTP must use verified STARTTLS or implicit TLS, except for a literal
   loopback host used for local testing. Certificate and hostname checks
   remain enabled.
5. **Authenticate the outbound sender domain.** The visible `From:` address
   and the SMTP envelope sender determine which domains receivers check. Ask
   the relay administrator which envelope-sender domain it uses and which
   public IP addresses or provider SPF mechanisms it uses to deliver mail to
   the Internet. The relevant IP is the relay's public egress IP (or the
   provider's documented sending infrastructure), not automatically the BBS
   host's LAN or public IP. Confirm it from a delivery bounce or the receiving
   provider's `Authentication-Results` header where possible.

   Publish SPF as a TXT record at the exact envelope-sender domain reported
   by the receiving provider. SPF does not inherit from a parent domain to a
   subdomain: a check for `bbs.example.org` needs an SPF record at
   `bbs.example.org`, even if `example.org` has its own SPF record. If the
   exact domain has no SPF record, a simple single-server record has this
   shape (replace the placeholder with the relay's real public sending IP):

   ```text
   v=spf1 ip4:<PUBLIC_RELAY_SENDING_IP> -all
   ```

   If using a mail provider, use the SPF `include:` value that provider
   documents instead of guessing its IP ranges. If an SPF TXT record already
   exists for that exact name, edit and merge the required `ip4:`, `ip6:`, or
   `include:` mechanisms into that one record; do not publish a second
   `v=spf1` record. Include every legitimate sender for the domain before
   using `-all`. Check DNS with `dig +short TXT <envelope-sender-domain>`.

   Configure the outbound relay to DKIM-sign messages using a domain aligned
   with the visible `From:` domain, then publish the public key at the exact
   selector name supplied by the relay (usually
   `<selector>._domainkey.<signing-domain>`). Keep the private key on the
   signing relay; only the public key belongs in DNS. Add a DMARC record for
   the visible sender domain, initially with a monitoring policy if desired,
   and review its reports before enforcing a stricter policy. ANetBBS creates
   a `Message-ID` but does not itself perform SPF authorization or DKIM
   signing. Verify a test message's `Authentication-Results` for `spf=pass`
   and `dkim=pass`; SPF or DKIM must authenticate the sender for Gmail, and
   aligned authentication is needed for DMARC.
6. **Install and start the listener.** The package updater installs the
   `anetbbs-mail.service` unit. On an existing system, inspect the updater
   output and service status. The package may leave the unit stopped when
   `MAIL_ENABLED` is not set; an operator can enable it after reviewing the
   configuration. Listener details (`SMTP_LISTEN_HOST`,
   `SMTP_LISTEN_PORT`, `SMTP_MAX_CONNECTIONS`, and `SMTP_DNSBL_ZONES`) are
   in `.env` and are editable in `anetbbs-cfg` under the system/network
   settings. Restart `anetbbs-mail` after changing those values.
7. **Set application policy.** In **Admin → Settings → Internet Mail** or
   `anetbbs-cfg` → **Internet Mail**, set the domain, minimum access level,
   message and mailbox limits, and outbound rate limits. Keep **Internet
   Mail Enabled** off until the DNS, relay, and listener are ready. Save the
   settings, then enable the feature.
8. **Opt in a test account.** Sign in with an account whose access level
   meets the configured minimum. Open **Profile → Edit**, enable Internet
   Mail, and optionally set a unique mailbox name. A blank name uses a
   normalized version of the username. Duplicate names are rejected.
9. **Test sending and receiving separately.** For outbound, use
   **Messaging → Internet Mail → Compose** to send one message to an address
   you control, then check the relay result and recipient mailbox. For inbound,
   send from an external mailbox to the user's displayed
   `mailbox-name@domain` address and confirm it appears in **Inbox**. A
   successful outbound test does not prove inbound DNS/port-25 delivery,
   and a successful inbound test does not prove the relay can send.

The **Sent** list records that the configured outbound relay accepted the
message. The recipient's server can still reject it later and generate a
delivery-status notification (bounce). Check the relay's mail log/queue and the
configured return path if the recipient does not receive it; the BBS does not
currently synchronize asynchronous remote bounces back into the Sent status.

`anetbbs-cfg` changes the application policy row in the database; those
settings are shared immediately with the WebUI and mail application.
Changing the listener settings in `.env` is different: restart
`anetbbs-mail.service` for them to take effect. Neither settings interface
restarts services automatically.

## Settings and limits

The application settings page and `anetbbs-cfg` edit the same `mail_config`
row:

| Setting | Default / behavior |
| --- | --- |
| Internet Mail Enabled | Off until the sysop enables it |
| Domain | Empty until configured |
| Minimum access level for opt-in | 100 (sysop tier) |
| Maximum message size | 25 MiB |
| Unique inbound recipients per SMTP transaction | 5 by default; hard maximum 100 |
| Outbound messages per user | 10 per hour and 50 per day; 0 blocks outbound mail |
| Stored mailbox data per user | 100 MiB finite quota |
| Attachments per inbound message | 20 by default; finite hard ceiling |
| Decoded size per attachment | 10 MiB by default |

The limits are enforced by the application, not by hiding UI fields. The
SMTP listener also bounds concurrent connections and session work. At the
hard maximum of 100 accepted recipients and the default 25 MiB message
size, raw per-recipient message copies can require about 2.44 GiB for one
transaction, plus decoded message content. Keep the recipient limit at its
default unless the system has been sized for the larger storage impact.
Repeated transactions and concurrent sessions are separate from that
single-transaction bound.

## User workflow

1. The sysop enables the global feature and sets its domain.
2. An eligible user chooses **Messaging → Set up Internet Mail** (or opens
   **Profile → Edit**) and opts in. The user can set a mailbox name or use
   the username-based default.
3. **Messaging → Internet Mail** opens the inbox. The mailbox navigation
   also provides **Sent** and **Compose**.
   Each newly accepted inbound message also creates a user-scoped item in the
   top navigation notification bell. Opening the message or its bell item
   clears that notification. Users can control the bell item under
   **Notification Settings → New Internet Mail messages**; the mailbox unread
   badge remains available in Messaging.
4. Compose uses an external email address in the `To` field. The send is
   subject to per-user rate limits and the configured outbound relay. A
   successful submission is labelled **Accepted by relay**; recipient-side
   delivery may complete later or produce a bounce.
5. A user can disable their mailbox from Profile → Edit. Disabling the
   account's mailbox prevents further delivery and sending through the
   feature; it does not disable Internet Mail for other users.

Treat inbound sender names, addresses, subjects, message bodies, and
attachments as untrusted external content. The WebUI escapes message text;
terminal clients render inbound text through the sanitized plain-text path.
At terminal login, the **Scan for new messages?** prompt includes the unread
Internet Mail count. Answer yes and choose **M** to open the Internet Mail
inbox. The scan is separate from the notification bell preference, so opting
out of the bell notification does not hide unread mail from an explicitly
requested login scan.

The WebUI and ANSI terminal reader show the same received message text.
Quoted reply lines (the conventional `>` prefix and its “wrote:” introduction)
are visually muted in both views, and repeated blank lines are reduced for
readability. These are display-only changes: the stored message and quoted
text used by replies are preserved. Telnet and SSH sessions wrap lines to the
active terminal width; the WebUI wraps to the browser width. Untrusted terminal
control sequences are stripped before display.

## Troubleshooting

### There is no Internet Mail link

- Confirm the global **Internet Mail Enabled** switch is on.
- The user must be signed in. A not-yet-opted-in user must meet the minimum
  access level to see **Set up Internet Mail**. An opted-in user retains the
  **Internet Mail** link if the threshold later changes.
- Confirm the user is opening the **Messaging** dropdown in the main WebUI.
  The sysop page is at `/admin/mail`.
- Open the user's profile editor. If there is no opt-in control, the global
  feature may still be off or a non-admin account may be below the access
  bar. Administrator/Sysop accounts are exempt from the numeric threshold.

### Inbound messages do not arrive

- Check the listener: `sudo systemctl status anetbbs-mail.service`.
- Inspect recent logs: `sudo journalctl -u anetbbs-mail.service -n 100
  --no-pager`.
- Verify the message is addressed to the exact configured domain and the
  recipient has opted in with that mailbox name.
- Check the domain's MX and target A/AAAA records from outside the BBS
  network. Confirm external TCP port 25 reaches the Pi. The service being
  active only proves it started locally.
- Check that the sending server was not rejected for a full mailbox,
  message-size/attachment limits, DNSBL, or unknown recipient. SMTP `452`
  quota/recipient-limit responses are temporary; `552` indicates a message
  or attachment size limit.

### Outbound messages fail

- Verify the relay is enabled and correct at **Admin → Settings → SMTP /
  Email**. Internet Mail uses this relay; it has no separate host setting.
- Check its TLS mode, certificate/hostname validity, sender address, and
  whether authentication is required. Do not send relay passwords in
  screenshots or logs. Authenticated SMTP without verified TLS is refused
  for non-loopback relays.
- Check relay rate limits and the Internet Mail user's hourly/daily quotas.
- Verify SPF/DKIM/DMARC for the relay's actual sending domain and provider.
- Inspect `anetbbs-web.service` logs for the outbound send result. Subjects
  and message bodies should not be copied into diagnostic reports.
- A **Sent** / **Accepted by relay** row means the relay accepted the
  submission; it does not confirm final delivery. Check the relay log for a
  remote `status=sent`, `status=deferred`, or `status=bounced` result and make
  sure delivery-status notifications can return to a valid sender mailbox.
- If Gmail reports `550 5.7.1` for a missing `Message-ID`, use a package that
  generates a valid RFC 5322 `Message-ID` in the shared outbound mailer.

### Settings do not appear or do not take effect

- The settings pages require a sysop/admin account. Try the direct route
  `/admin/mail` or the `anetbbs-cfg` → **Internet Mail** section.
- WebUI and `anetbbs-cfg` change application policy in the same database.
- Listener bind/concurrency/DNSBL settings are in `.env`; restart
  `anetbbs-mail.service` after editing those settings.
- The global application switch, the systemd listener state, and each user's
  opt-in are independent gates. All three must be in the intended state for
  Internet Mail delivery and access.

## Inbound SMTP listener capabilities

- The BBS listener receives server-to-server SMTP for the domain's MX
  records on the configured listener port (normally 25). It does not offer
  STARTTLS or SMTP AUTH. With this listener, the SMTP hop from a sending mail
  server to the BBS is plaintext; message content and headers are not
  transport-encrypted on that hop. Internet SMTP servers commonly use
  opportunistic STARTTLS when the receiving server advertises it, but this
  listener does not currently advertise or negotiate it.
- SMTP AUTH is for authenticated message submission, commonly from a mail
  client or a trusted relay. Remote delivery MTAs normally connect to the
  destination MX without authenticating. Do not configure a relay to log in
  to this listener or require STARTTLS from it; it will not complete that
  handshake. A relay that forwards unauthenticated SMTP to the BBS can work,
  but that relay-to-BBS network hop is still plaintext unless protected by a
  separately managed private network or tunnel.
- Outbound mail is a separate path: ANetBBS submits through the sysop's
  configured outbound relay, and that connection can use verified TLS and
  relay authentication. These outbound settings do not enable TLS or AUTH on
  the inbound listener.
- ANetBBS does not independently verify SPF or DKIM on inbound messages.
  DNSBL checks are configurable and fail open on lookup timeouts/errors so
  resolver problems do not silently block all legitimate mail.
- Null-sender bounce/DSN messages are not accepted.
- The Internet Mail feature does not provide a generic mailbox for
  `Sysop@raspberrypi.local`; user addresses use the domain configured in
  Internet Mail Settings.
