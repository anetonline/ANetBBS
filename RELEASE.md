# ANetBBS v1.1.69 — Internet Mail delivery and notifications

Private test candidate for Jerry's live ANetBBS test. This package is not
approved for GitHub push or public publication.

Outbound Internet Mail and system mail now include an RFC 5322 `Message-ID`,
so Gmail and other receiving systems can accept the message. The UI reports
relay acceptance distinctly from final recipient delivery, which can be delayed
or bounced asynchronously. Incoming Internet Mail creates a user-scoped WebUI
notification and appears in terminal login scanning, with a direct shortcut to
the inbox. Notification read state follows message read/delete actions.

The WebUI and ANSI terminal reader use matching display formatting for
quoted replies and repeated blank lines. The display is normalized without
changing stored messages or the content included in replies.

The release includes detailed Internet Mail setup, relay, DNS, notification,
troubleshooting, and rollback guidance in `docs/19-email.md`. The sysop can
configure the same database-backed Internet Mail policy through the WebUI or
`anetbbs-cfg`; SMTP listener settings remain separately configured in `.env`.
