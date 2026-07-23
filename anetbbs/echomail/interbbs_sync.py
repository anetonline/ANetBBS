# anetbbs/echomail/interbbs_sync.py
"""
InterBBS Wall / Last Callers -- opt-in sharing of local Graffiti Wall
posts and recent-caller entries with other ANetBBS installs, riding the
existing QWK/BinkP echomail transport (special-purpose EchoAreas) rather
than a bespoke sync protocol. Each feature is scoped to exactly one
EchomailNetwork, picked by the sysop in the admin UI (config keys
WALL_INTERBBS_NETWORK_ID / LASTCALLERS_INTERBBS_NETWORK_ID) -- never
"every active network", since that would silently create these areas on
unrelated third-party FTN networks too.

Two directions per feature:
  - outbound (post_wall_to_interbbs / post_lastcaller_to_interbbs):
    called right after a LOCAL WallPost/CallerLog row is created. Composes
    a normal EchomailMessage in the feature's special area and tosses it
    to any downstream subscribers.
  - inbound (sync_wall_inbound / sync_lastcallers_inbound): ScheduledEvent
    handlers (see anetbbs/events/handlers.py) that periodically scan for
    new inbound EchomailMessage rows in the special area and materialize
    them into WallPost/CallerLog rows.

CRITICAL loop-prevention invariant: a materialized row always gets
`origin_bbs` set (non-NULL). The outbound functions refuse to relay any
row that already has `origin_bbs` set. This is the ONLY thing preventing
an infinite bounce -- a re-composed message gets a brand-new msg_id
(anetbbs/echomail/binkp.py's packer only reuses msg_id if already set on
the ORM object, never regenerates deterministically from content), so no
downstream MSGID dedup could ever catch a re-relayed loop on its own.
Never remove the `origin_bbs is not None: return` checks below.
"""
import logging

logger = logging.getLogger(__name__)

WALL_AREA_TAG = 'ANET_WALL'
LASTCALLERS_AREA_TAG = 'ANET_LASTCALLERS'
GAMES_AREA_TAG = 'ANET_GAMESCORES'

# InterBBS score sharing fairness lock: different installs can configure
# different CASINO_*_START values, so a "$50,000 peak" isn't earned under
# identical odds on two different BBSes. While score sharing is enabled,
# these four settings are locked to these standard values (current
# defaults) -- anetbbs/web/games_interbbs_admin.py force-writes them on
# enable, and anetbbs/web/admin.py's general settings route auto-disables
# the feature if a sysop changes any of them afterward.
CASINO_INTERBBS_STANDARD_STARTS = {
    'CASINO_BLACKJACK_START': '500',
    'CASINO_SLOTS_START': '200',
    'CASINO_VIDEOPOKER_START': '200',
    'CASINO_HOLDEM_START': '1000',
}


def ensure_special_area(network, tag):
    """Get-or-create the special-purpose EchoArea for `tag` on `network`.
    Idempotent -- re-asserts is_active/is_subscribed/is_sysop_only on
    every call in case a sysop's own DB edit ever flips one off.
    is_sysop_only=True hides it from normal users' terminal/web echomail
    browsing (confirmed: this is a real content gate on both surfaces,
    not just a listing filter) -- it's a sync channel, not a discussion
    area meant to be read directly.

    If we're a spoke of this network (hub_address set) on a BinkP
    network, also request the hub subscribe us via AreaFix. QWK networks
    have no AreaFix protocol at all -- send_areafix_request() itself
    doesn't gate on network_type, so we must.
    """
    from ..models import db, EchoArea

    area = EchoArea.query.filter_by(network_id=network.id, tag=tag).first()
    needs_areafix_request = False
    if area is None:
        area = EchoArea(
            network_id=network.id, tag=tag, name=tag,
            is_active=True, is_subscribed=True, is_sysop_only=True,
        )
        db.session.add(area)
        db.session.commit()
        needs_areafix_request = True
    else:
        changed = False
        if not area.is_active:
            area.is_active = True
            changed = True
        if not area.is_subscribed:
            area.is_subscribed = True
            changed = True
        if not area.is_sysop_only:
            area.is_sysop_only = True
            changed = True
        if changed:
            db.session.commit()
            needs_areafix_request = True

    # Real bug found in a full echomail-subsystem audit: this AreaFix
    # request used to run unconditionally on EVERY call to
    # ensure_special_area(), not just when the area was actually just
    # created or repaired above -- and this function is called from
    # post_wall_to_interbbs/post_lastcaller_to_interbbs/
    # post_score_to_interbbs, i.e. on every single Wall post, logged
    # caller, and new personal-best game score. A moderately active
    # install flooded its upstream hub's AreaFix bot with a redundant
    # subscribe request (plus its own AreafixLog row and a synchronous
    # DB commit) every time any of those happened, even though we're
    # almost certainly already subscribed after the very first one.
    # Now only fires when this call actually changed something.
    if needs_areafix_request and network.hub_address and network.network_type == 'binkp':
        try:
            from .areafix import send_areafix_request
            send_areafix_request(network, plus_tags=[tag])
        except Exception:
            logger.exception('interbbs_sync: areafix request failed for %s', tag)

    return area


def _configured_network(network_id):
    """Resolve the one EchomailNetwork a feature is scoped to. Returns
    None if unconfigured, inactive, or (defensively) QWK -- callers must
    no-op in that case. QWK areas are identified by numeric conference
    number end to end (qwk.py/qwk_hub_ftp.py), so a symbolic tag like
    ANET_WALL can never receive real QWK traffic no matter how the
    EchoArea row is created -- the admin UI already only offers BinkP
    networks, this is just a second guard in case an old .env still has
    a QWK network id saved from before that restriction existed."""
    if not network_id:
        return None
    from ..models import EchomailNetwork
    network = EchomailNetwork.query.get(int(network_id))
    if network is None or not network.is_active:
        return None
    if network.network_type != 'binkp':
        logger.warning(
            'interbbs_sync: configured network %s is %r, not binkp -- '
            'refusing to use it (QWK areas cannot carry a symbolic tag)',
            network.name, network.network_type)
        return None
    return network


def post_wall_to_interbbs(wall_post):
    """Relay a freshly-created local WallPost out to the configured
    ANET_WALL area. No-op unless WALL_INTERBBS_ENABLED and a network is
    configured, and -- critically -- no-op if this post itself came from
    another BBS (origin_bbs set). That check is the loop-prevention gate;
    never remove it."""
    from flask import current_app
    if not current_app.config.get('WALL_INTERBBS_ENABLED'):
        return
    if wall_post.origin_bbs is not None:
        return
    network = _configured_network(current_app.config.get('WALL_INTERBBS_NETWORK_ID'))
    if network is None:
        return

    from ..models import db, EchomailMessage
    from . import tosser

    line1 = wall_post.line1 or ''
    line2 = wall_post.line2 or ''
    body = line1 if not line2 else f'{line1}\n{line2}'
    try:
        area = ensure_special_area(network, WALL_AREA_TAG)
        msg = EchomailMessage(
            area_id=area.id, network_id=network.id,
            from_name=(wall_post.display_name or wall_post.username or '?')[:100],
            from_address=(network.our_address or '')[:60],
            to_name='All', subject='ANET-WALL-POST',
            body=body, direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        tosser.toss_message(msg.id)
    except Exception:
        db.session.rollback()
        logger.exception('interbbs_sync: failed to relay wall post %s', wall_post.id)


def post_lastcaller_to_interbbs(caller_log_row):
    """Same shape as post_wall_to_interbbs, for Last Callers. Body carries
    ONLY service + started_at -- never ip_address. There is no precedent
    anywhere in this codebase for sharing caller IPs across BBS
    boundaries, and there shouldn't be one.

    Real report: a sysop who tests heavily (many logins/day) was
    flooding every other BBS's Last Callers area on the shared network
    with their own account. LASTCALLERS_HIDE_SYSOP already existed to
    hide sysop logins from the LOCAL Last Callers displays
    (features/lastcallers.py's recent_callers_query), but this outbound
    relay never checked it at all -- a sysop login got relayed to every
    downstream BBS regardless of that setting. Same toggle now also
    gates the relay, so "hidden from Last Callers" means hidden from
    every BBS's copy, not just this one's."""
    from flask import current_app
    if not current_app.config.get('LASTCALLERS_INTERBBS_ENABLED'):
        return
    if caller_log_row.origin_bbs is not None:
        return
    if current_app.config.get('LASTCALLERS_HIDE_SYSOP'):
        from ..models import User
        caller_user = User.query.get(caller_log_row.user_id) if caller_log_row.user_id else None
        if caller_user is not None and caller_user.is_admin:
            return
    network = _configured_network(current_app.config.get('LASTCALLERS_INTERBBS_NETWORK_ID'))
    if network is None:
        return

    from ..models import db, EchomailMessage
    from . import tosser

    started = caller_log_row.started_at.isoformat() if caller_log_row.started_at else ''
    body = f'{caller_log_row.service or "?"}\n{started}'
    try:
        area = ensure_special_area(network, LASTCALLERS_AREA_TAG)
        msg = EchomailMessage(
            area_id=area.id, network_id=network.id,
            from_name=(caller_log_row.username or '?')[:100],
            from_address=(network.our_address or '')[:60],
            to_name='All', subject='ANET-LASTCALLER',
            body=body, direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        tosser.toss_message(msg.id)
    except Exception:
        db.session.rollback()
        logger.exception('interbbs_sync: failed to relay caller log %s', caller_log_row.id)


def _origin_from_message(msg):
    """Best-effort human-readable origin BBS name for an inbound message
    -- prefers the FTN '* Origin: ...' line, falls back to from_address,
    falls back to a fixed placeholder (never blank/None -- that would
    collide with the "local post" sentinel)."""
    if msg.origin_line:
        return msg.origin_line.strip()[:100] or 'unknown'
    if msg.from_address:
        return msg.from_address[:100]
    return 'unknown'


def sync_wall_inbound(app, params):
    """ScheduledEvent handler: materialize new inbound ANET_WALL messages
    into local WallPost rows. Signature per events/handlers.py convention:
    f(app, params) -> (ok, output).

    Dedup is by EchomailMessage.msg_id against WallPost.remote_msg_id,
    queried GLOBALLY (not scoped to one area/network) -- the transport
    layer's own dedup (poller.py) is scoped to (msg_id, area_id), so the
    same message arriving via two areas/networks would not be caught
    there. Rows with msg_id IS NULL (malformed/legacy packets) are
    skipped, not materialized -- they can never be deduped on a later
    scan and would re-import forever otherwise.

    Inserts WallPost directly via db.session.add() -- NEVER through
    wall.py's _post_to_wall(), since that's the function carrying the
    outbound-relay hook, and calling it here would defeat the
    loop-prevention gate entirely.
    """
    from ..models import db, EchoArea, EchomailMessage, WallPost
    from ..features.word_filter import apply as _wf_apply

    imported = 0
    skipped_no_msgid = 0
    try:
        area_ids = [a.id for a in EchoArea.query.filter_by(tag=WALL_AREA_TAG).all()]
        if not area_ids:
            return True, 'no ANET_WALL areas configured'

        known_ids = {
            r[0] for r in db.session.query(WallPost.remote_msg_id)
            .filter(WallPost.remote_msg_id.isnot(None)).all()
        }
        rows = (EchomailMessage.query
                .filter(EchomailMessage.area_id.in_(area_ids),
                        EchomailMessage.direction == 'inbound')
                .all())
        for msg in rows:
            if not msg.msg_id:
                skipped_no_msgid += 1
                continue
            if msg.msg_id in known_ids:
                continue

            body = msg.body or ''
            lines = body.split('\n', 1)
            line1 = _wf_apply(lines[0])[:200]
            line2 = _wf_apply(lines[1])[:200] if len(lines) > 1 else None
            wp = WallPost(
                username=(msg.from_name or '?')[:80],
                display_name=(msg.from_name or '?')[:100],
                line1=line1, line2=line2,
                origin_bbs=_origin_from_message(msg),
                remote_msg_id=msg.msg_id,
            )
            db.session.add(wp)
            db.session.commit()
            known_ids.add(msg.msg_id)
            imported += 1
        return True, f'imported {imported}, skipped {skipped_no_msgid} (no msg_id)'
    except Exception as exc:
        db.session.rollback()
        logger.exception('sync_wall_inbound failed')
        return False, str(exc)


def sync_lastcallers_inbound(app, params):
    """Same shape as sync_wall_inbound, for Last Callers -- see that
    function's docstring for the dedup/loop-prevention reasoning, which
    applies identically here."""
    from ..models import db, EchoArea, EchomailMessage, CallerLog
    from datetime import datetime as _dt

    imported = 0
    skipped_no_msgid = 0
    try:
        area_ids = [a.id for a in EchoArea.query.filter_by(tag=LASTCALLERS_AREA_TAG).all()]
        if not area_ids:
            return True, 'no ANET_LASTCALLERS areas configured'

        known_ids = {
            r[0] for r in db.session.query(CallerLog.remote_msg_id)
            .filter(CallerLog.remote_msg_id.isnot(None)).all()
        }
        rows = (EchomailMessage.query
                .filter(EchomailMessage.area_id.in_(area_ids),
                        EchomailMessage.direction == 'inbound')
                .all())
        for msg in rows:
            if not msg.msg_id:
                skipped_no_msgid += 1
                continue
            if msg.msg_id in known_ids:
                continue

            body_lines = (msg.body or '').split('\n')
            service = (body_lines[0].strip() if body_lines else '')[:20] or 'unknown'
            started_at = None
            if len(body_lines) > 1 and body_lines[1].strip():
                try:
                    started_at = _dt.fromisoformat(body_lines[1].strip())
                except Exception:
                    started_at = None
            cl = CallerLog(
                username=(msg.from_name or '?')[:80],
                service=service,
                started_at=started_at or _dt.utcnow(),
                origin_bbs=_origin_from_message(msg),
                remote_msg_id=msg.msg_id,
                ip_address=None,
            )
            db.session.add(cl)
            db.session.commit()
            known_ids.add(msg.msg_id)
            imported += 1
        return True, f'imported {imported}, skipped {skipped_no_msgid} (no msg_id)'
    except Exception as exc:
        db.session.rollback()
        logger.exception('sync_lastcallers_inbound failed')
        return False, str(exc)


def _get_or_create_interbbs_score_user():
    """Lazily get-or-create the ghost placeholder User every imported
    GameScore.user_id points at. GameScore.user_id is a hard NOT NULL
    FK (unlike WallPost.username, a plain denormalized string), and
    making it nullable isn't viable -- SQLite can't relax an existing
    NOT NULL via ALTER TABLE, and this codebase's migration helper
    only ever adds columns, never loosens constraints. The real remote
    player name lives in GameScore.remote_username instead."""
    from ..models import db, User

    user = User.query.filter_by(username='__interbbs_import__').first()
    if user is None:
        user = User(
            username='__interbbs_import__',
            email='__interbbs_import__@localhost.invalid',
            password_hash='!',
            is_active=False,
        )
        db.session.add(user)
        db.session.commit()
    return user


def post_score_to_interbbs(game_score_row):
    """Relay a freshly-created local GameScore out to the configured
    ANET_GAMESCORES area -- but ONLY if it's a new personal best for
    this user+game, not every submission (the first score for any
    user+game is trivially a personal best, since there's nothing
    prior to beat). No-op unless GAMES_INTERBBS_ENABLED, a network is
    configured, the specific game has opted in
    (Game.share_scores_interbbs) -- and, critically, no-op if this
    score itself came from another BBS (origin_bbs set). That last
    check is the loop-prevention gate; never remove it."""
    from flask import current_app
    if not current_app.config.get('GAMES_INTERBBS_ENABLED'):
        return
    if game_score_row.origin_bbs is not None:
        return

    from ..models import db, Game, GameScore

    game = game_score_row.game or Game.query.get(game_score_row.game_id)
    if game is None or not game.share_scores_interbbs:
        return

    network = _configured_network(current_app.config.get('GAMES_INTERBBS_NETWORK_ID'))
    if network is None:
        return

    # Only relay new personal bests. Excludes imported rows (origin_bbs
    # IS NOT NULL) from the comparison -- defensive/documentation, since
    # imported rows carry the ghost user's id, not the real submitter's,
    # so they'd never match this user_id anyway.
    prior_best = (db.session.query(db.func.max(GameScore.score))
                  .filter(GameScore.user_id == game_score_row.user_id,
                          GameScore.game_id == game_score_row.game_id,
                          GameScore.id != game_score_row.id,
                          GameScore.origin_bbs.is_(None))
                  .scalar())
    if prior_best is not None and game_score_row.score <= prior_best:
        return

    from ..models import EchomailMessage
    from . import tosser

    display_name = (game_score_row.display_username or '?')[:100]
    body = f'{game.slug}\n{game_score_row.score}\n{display_name}'
    try:
        area = ensure_special_area(network, GAMES_AREA_TAG)
        msg = EchomailMessage(
            area_id=area.id, network_id=network.id,
            from_name=display_name,
            from_address=(network.our_address or '')[:60],
            to_name='All', subject='ANET-GAMESCORE',
            body=body, direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        tosser.toss_message(msg.id)
    except Exception:
        db.session.rollback()
        logger.exception('interbbs_sync: failed to relay game score %s', game_score_row.id)


def sync_scores_inbound(app, params):
    """ScheduledEvent handler: materialize new inbound ANET_GAMESCORES
    messages into local GameScore rows. See sync_wall_inbound's
    docstring for the shared dedup/loop-prevention reasoning.

    Cross-install game identity is a double gate: the sender already
    only relays for a game it has opted in (share_scores_interbbs); the
    RECEIVER separately only materializes a score if it ALSO has a
    local Game with the same slug AND that local game is ALSO opted
    in -- a receiving install with no matching game, or one where that
    specific game is opted out, silently skips (never gets a
    remote_msg_id, so it's correctly re-evaluated -- not re-imported
    -- on every later scan, same as a NULL-msg_id row: if the sysop
    later adds/opts in that game, a future scan will pick it up).

    Inserts GameScore directly via db.session.add() -- NEVER through
    games.py's submit_score(), since that carries both the
    outbound-relay hook and the local play_count increment; routing an
    imported score through it would defeat the loop-prevention gate
    AND misrepresent local play activity (materializing a remote score
    is not a local play -- play_count must never be touched here).
    """
    from ..models import db, EchoArea, EchomailMessage, Game, GameScore

    imported = 0
    skipped_no_msgid = 0
    skipped_no_local_game = 0
    try:
        area_ids = [a.id for a in EchoArea.query.filter_by(tag=GAMES_AREA_TAG).all()]
        if not area_ids:
            return True, 'no ANET_GAMESCORES areas configured'

        known_ids = {
            r[0] for r in db.session.query(GameScore.remote_msg_id)
            .filter(GameScore.remote_msg_id.isnot(None)).all()
        }
        rows = (EchomailMessage.query
                .filter(EchomailMessage.area_id.in_(area_ids),
                        EchomailMessage.direction == 'inbound')
                .all())
        for msg in rows:
            if not msg.msg_id:
                skipped_no_msgid += 1
                continue
            if msg.msg_id in known_ids:
                continue

            body_lines = (msg.body or '').split('\n')
            if len(body_lines) < 2:
                continue
            slug = body_lines[0].strip()
            try:
                score = int(body_lines[1].strip())
            except (ValueError, IndexError):
                continue
            remote_username = (body_lines[2].strip()[:100] if len(body_lines) > 2
                               else (msg.from_name or '?')[:100])

            game = Game.query.filter_by(slug=slug).first()
            if game is None or not game.share_scores_interbbs:
                skipped_no_local_game += 1
                continue

            ghost = _get_or_create_interbbs_score_user()
            gs = GameScore(
                game_id=game.id,
                user_id=ghost.id,
                score=score,
                origin_bbs=_origin_from_message(msg),
                remote_msg_id=msg.msg_id,
                remote_username=remote_username,
            )
            db.session.add(gs)
            db.session.commit()
            known_ids.add(msg.msg_id)
            imported += 1
        return True, (f'imported {imported}, skipped {skipped_no_msgid} (no msg_id), '
                      f'skipped {skipped_no_local_game} (no local game / opted out)')
    except Exception as exc:
        db.session.rollback()
        logger.exception('sync_scores_inbound failed')
        return False, str(exc)
