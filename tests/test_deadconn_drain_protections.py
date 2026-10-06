"""Regression tests for the Shape-2 unprotected-drain() audit findings
fixed in this round: ansi_ui.write_menu_art(), wall.py's Graffiti Wall
(7 call sites), anetirc2._IRC._tx(), and mrc_irc_bridge's upstream
_send(). See the deadconn-freeze-audit skill -- each of these wrote
raw bytes then called .drain() directly, unprotected, the same hang
shape already fixed elsewhere in session.py.
"""
import asyncio

from tests._deadconn_fixtures import FakeWriter, make_session, run

import anetbbs.core.session as session_mod
from anetbbs.core.session import CarrierLost
from anetbbs.features import ansi_ui, wall


async def _hung_drain():
    await asyncio.sleep(60)


def test_write_menu_art_drain_is_protected(monkeypatch):
    """write_menu_art() must time out (CarrierLost) rather than hang
    forever when the client stops relieving write backpressure."""
    monkeypatch.setattr(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 0.2)
    monkeypatch.setattr(ansi_ui, 'load_menu_ansi', lambda slot, mode: b'ART')
    session, writer = make_session(
        reader=None, writer=FakeWriter(drain_coro=_hung_drain))
    assert session.term_mode == 'ansi'

    try:
        run(ansi_ui.write_menu_art(session, 'chat'), timeout=3)
        assert False, 'expected CarrierLost from a hung drain(), got a clean return'
    except CarrierLost:
        pass


def test_wall_show_wall_drain_is_protected(monkeypatch):
    """The Graffiti Wall's render path (session.writer.drain() after
    painting the board) must not hang forever on a stuck client."""
    import anetbbs.features.bbs_ui as bbs_ui
    import anetbbs.models as models

    class _FakeQuery:
        def filter_by(self, **kw):
            return self

        def count(self):
            return 0

        def order_by(self, *a):
            return self

        def offset(self, *a):
            return self

        def limit(self, *a):
            return self

        def all(self):
            return []

    class _FakeColumn:
        def desc(self):
            return self

    class _FakeWallPost:
        query = _FakeQuery()
        created_at = _FakeColumn()

    monkeypatch.setattr(models, 'WallPost', _FakeWallPost)
    monkeypatch.setattr(session_mod, 'WRITE_DRAIN_TIMEOUT_SECONDS', 0.2)

    session, writer = make_session(
        reader=None, writer=FakeWriter(drain_coro=_hung_drain))
    session.user = {'id': 1, 'is_admin': False}

    class _NullCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeFlaskApp:
        def app_context(self):
            return _NullCtx()

    monkeypatch.setattr(bbs_ui, '_app', lambda: _FakeFlaskApp())

    try:
        run(wall.show_wall(session, allow_post=False), timeout=3)
        assert False, 'expected CarrierLost from a hung drain(), got a clean return'
    except CarrierLost:
        pass


def test_anetirc_tx_drain_is_protected(monkeypatch):
    """_IRC._tx() must give up on a hung drain() rather than block the
    whole read/write loop forever."""
    import anetbbs.features.anetirc2 as anetirc2

    monkeypatch.setattr(anetirc2, '_DRAIN_TIMEOUT', 0.2)
    irc = anetirc2._IRC.__new__(anetirc2._IRC)
    irc.writer = FakeWriter(drain_coro=_hung_drain)
    irc.reader = None

    run(irc._tx('PING :x'), timeout=3)


def test_mrc_bridge_send_drain_is_protected(monkeypatch):
    """The MRC<->IRC bridge's upstream _send() must give up on a hung
    drain() -- this is a shared multiplexer connection, so a true hang
    here would silently stall every bridged user, not just one."""
    import anetbbs.features.mrc_irc_bridge as mrc_irc_bridge

    monkeypatch.setattr(mrc_irc_bridge, '_DRAIN_TIMEOUT', 0.2)
    leg = mrc_irc_bridge._IrcLeg.__new__(mrc_irc_bridge._IrcLeg)
    leg.writer = FakeWriter(drain_coro=_hung_drain)
    leg.connected = True

    run(leg._send('PRIVMSG #x :hi'), timeout=3)
    assert leg.connected is False
