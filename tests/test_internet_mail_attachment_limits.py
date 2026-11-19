"""Per-message attachment limits for inbound Internet Mail
(MailConfig.max_attachments, MailConfig.max_attachment_bytes).

Covers finite defaults + migration, count and decoded-size boundaries for
base64 / quoted-printable / 8bit parts, nested multiparts and attached
messages, parts identified by filename or non-text type, malformed
transfer encodings (invalid base64 characters, misplaced padding,
unknown encodings, broken headers), and that a refusal happens with a
5xx after DATA and stores nothing -- never a 250 first.
"""
import base64

import pytest
from sqlalchemy import inspect

from tests.internet_mail_limits_harness import (
    addr, clean_mail, inbound_rows, mail_app, make_user, run_with_server,
    set_cfg, transaction)

__all__ = ['mail_app', 'clean_mail']

B = 'BOUNDARY-limits'


def _b64_lines(data):
    enc = base64.b64encode(data).decode()
    return '\r\n'.join(enc[i:i + 76] for i in range(0, len(enc), 76))


def _part(payload, *, ctype='application/octet-stream', cte='base64',
          filename='file.bin', disposition='attachment'):
    hdrs = [f'Content-Type: {ctype}']
    if disposition:
        d = disposition + (f'; filename="{filename}"' if filename else '')
        hdrs.append(f'Content-Disposition: {d}')
    if cte:
        hdrs.append(f'Content-Transfer-Encoding: {cte}')
    return '\r\n'.join(hdrs) + '\r\n\r\n' + payload


def _mixed(*parts, text='see attached', boundary=B):
    body = [f'--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{text}']
    body += [f'--{boundary}\r\n{p}' for p in parts]
    return ('From: sender@remote.test\r\nSubject: attachments\r\nMIME-Version: 1.0\r\n'
            f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
            + '\r\n'.join(body) + f'\r\n--{boundary}--\r\n').encode()


def _b64_attachment(size, **kw):
    return _part(_b64_lines(b'\x01' * size), **kw)


def _deliver(app, raw, user='lim_att'):
    return run_with_server(lambda c: transaction(c, [addr(user)], raw))


@pytest.fixture
def att(clean_mail):
    make_user(clean_mail, 'lim_att')
    set_cfg(clean_mail, max_attachments=3, max_attachment_bytes=1000)
    return clean_mail


def _accepted(out):
    return out['data'] is not None and out['data'].startswith('250')


def _refused_552(out):
    return out['data'] is not None and out['data'].startswith('552 5.3.4')


# -- defaults / schema ---------------------------------------------------------

def test_defaults_are_finite_and_migrated(mail_app):
    from anetbbs.models import (db, MailConfig, MAX_ATTACHMENTS_DEFAULT,
                                MAX_ATTACHMENT_BYTES_DEFAULT)
    assert MAX_ATTACHMENTS_DEFAULT == 20
    assert MAX_ATTACHMENT_BYTES_DEFAULT == 10 * 1024 * 1024
    with mail_app.app_context():
        cols = {c['name']: c for c in inspect(db.engine).get_columns('mail_config')}
        for name in ('max_attachments', 'max_attachment_bytes'):
            assert name in cols and not cols[name]['nullable']
        fresh = MailConfig()
        db.session.add(fresh)
        db.session.flush()
        assert (fresh.max_attachments, fresh.max_attachment_bytes) == (
            MAX_ATTACHMENTS_DEFAULT, MAX_ATTACHMENT_BYTES_DEFAULT)
        db.session.rollback()


# -- count ---------------------------------------------------------------------

def test_count_at_limit_accepted_one_over_refused(att):
    ok = _deliver(att, _mixed(*[_b64_attachment(10, filename=f'f{i}') for i in range(3)]))
    assert _accepted(ok), ok
    over = _deliver(att, _mixed(*[_b64_attachment(10, filename=f'f{i}') for i in range(4)]))
    assert _refused_552(over) and 'more than 3' in over['data'], over
    assert len(inbound_rows(att)) == 1


def test_zero_attachments_allowed_refuses_any_attachment_but_not_plain_mail(att):
    set_cfg(att, max_attachments=0)
    plain = ('From: s@remote.test\r\nSubject: hi\r\n\r\njust text\r\n').encode()
    assert _accepted(_deliver(att, plain))
    assert _refused_552(_deliver(att, _mixed(_b64_attachment(1))))


def test_inline_image_and_named_text_part_count_as_attachments(att):
    set_cfg(att, max_attachments=1)
    inline_img = _part(_b64_lines(b'\x89PNG' * 2), ctype='image/png', filename=None,
                       disposition='inline')
    named_text = _part('hello', ctype='text/plain; name="notes.txt"', cte='7bit',
                       filename=None, disposition=None)
    assert _refused_552(_deliver(att, _mixed(inline_img, named_text)))


def test_html_alternative_body_is_not_an_attachment(att):
    set_cfg(att, max_attachments=0)
    alt = ('From: s@remote.test\r\nSubject: alt\r\nMIME-Version: 1.0\r\n'
           'Content-Type: multipart/alternative; boundary="ALT"\r\n\r\n'
           '--ALT\r\nContent-Type: text/plain\r\n\r\nplain\r\n'
           '--ALT\r\nContent-Type: text/html\r\n\r\n<p>html</p>\r\n--ALT--\r\n').encode()
    assert _accepted(_deliver(att, alt))


def _attached_message(inner, *, disposition='attachment; filename="fwd.eml"'):
    hdr = 'Content-Type: message/rfc822\r\n'
    if disposition:
        hdr += f'Content-Disposition: {disposition}\r\n'
    return hdr + '\r\n' + inner


FWD_WITH_TWO = ('From: x@remote.test\r\nSubject: fwd\r\nMIME-Version: 1.0\r\n'
                'Content-Type: multipart/mixed; boundary="IN"\r\n\r\n'
                '--IN\r\nContent-Type: text/plain\r\n\r\ninner text\r\n'
                '--IN\r\n' + _b64_attachment(5, filename='a') + '\r\n'
                '--IN\r\n' + _b64_attachment(5, filename='b') + '\r\n--IN--')


@pytest.mark.parametrize('limit,accepted', [(2, True), (1, False)])
def test_attached_message_is_one_unit_and_nested_multipart_is_descended(att, limit, accepted):
    """An attached message counts once (its two inner attachments are part
    of its serialized size, not counted again); an ordinary nested
    multipart wrapper is descended into and its leaf counted."""
    set_cfg(att, max_attachments=limit)
    nested = ('Content-Type: multipart/mixed; boundary="NEST"\r\n\r\n'
              '--NEST\r\n' + _b64_attachment(5, filename='c') + '\r\n--NEST--')
    out = _deliver(att, _mixed(_attached_message(FWD_WITH_TWO), nested))
    assert (_accepted(out) if accepted else _refused_552(out)), out


@pytest.mark.parametrize('disposition', ['attachment; filename="fwd.eml"', 'inline', None])
def test_attached_message_with_text_only_body_counts_even_with_zero_limit(att, disposition):
    """Codex repro: max_attachments=0 accepted an attached message/rfc822
    because its only leaf was plain text with no disposition."""
    set_cfg(att, max_attachments=0)
    inner = 'From: x@remote.test\r\nSubject: fwd\r\n\r\n' + 'A' * 200
    out = _deliver(att, _mixed(_attached_message(inner, disposition=disposition)))
    assert _refused_552(out) and 'more than 0' in out['data'], out
    assert inbound_rows(att) == []


@pytest.mark.parametrize('size,accepted', [(100, True), (400, False)])
def test_attached_message_size_is_its_serialized_content(att, size, accepted):
    set_cfg(att, max_attachment_bytes=300)
    inner = 'Subject: fwd\r\n\r\n' + 'A' * size
    out = _deliver(att, _mixed(_attached_message(inner)))
    assert (_accepted(out) if accepted else _refused_552(out)), out


def test_message_nested_in_attached_message_counted_once(att):
    set_cfg(att, max_attachments=1, max_attachment_bytes=100000)
    deeper = ('From: y@remote.test\r\nSubject: inner\r\nMIME-Version: 1.0\r\n'
              'Content-Type: multipart/mixed; boundary="D"\r\n\r\n'
              '--D\r\n' + _attached_message('Subject: deepest\r\n\r\nz') + '\r\n--D--')
    assert _accepted(_deliver(att, _mixed(_attached_message(deeper))))


def test_empty_attached_message_counts(att):
    set_cfg(att, max_attachments=0)
    assert _refused_552(_deliver(att, _mixed(_attached_message(''))))
    set_cfg(att, max_attachments=1)
    assert _accepted(_deliver(att, _mixed(_attached_message(''))))


def test_malformed_attached_message_is_bounded(att):
    """Broken inner headers / unterminated inner multipart: still one unit,
    still size-checked (never skipped)."""
    set_cfg(att, max_attachment_bytes=300)
    broken = ('Content-Type: multipart/mixed; boundary="NEVER-CLOSED"\r\n'
              'From: <<<\r\n\r\n--NEVER-CLOSED\r\nContent-Type: text/plain\r\n\r\n' + 'B' * 500)
    assert _refused_552(_deliver(att, _mixed(_attached_message(broken))))
    set_cfg(att, max_attachments=0, max_attachment_bytes=100000)
    assert _deliver(att, _mixed(_attached_message(broken)))['data'][:1] == '5'


def test_attached_multipart_container_with_filename_is_one_unit(att):
    set_cfg(att, max_attachments=1, max_attachment_bytes=100000)
    bundle = ('Content-Type: multipart/mixed; boundary="BUN"\r\n'
              'Content-Disposition: attachment; filename="bundle"\r\n\r\n'
              + ''.join(f'--BUN\r\n{_b64_attachment(5, filename=f"x{i}")}\r\n' for i in range(4))
              + '--BUN--')
    assert _accepted(_deliver(att, _mixed(bundle)))
    set_cfg(att, max_attachment_bytes=50)
    assert _refused_552(_deliver(att, _mixed(bundle)))


def test_alternative_and_related_wrappers_are_not_attachments(att):
    set_cfg(att, max_attachments=1)
    wrapper = ('Content-Type: multipart/alternative; boundary="ALT2"\r\n\r\n'
               '--ALT2\r\nContent-Type: text/plain\r\n\r\nplain\r\n'
               '--ALT2\r\nContent-Type: multipart/related; boundary="REL"\r\n\r\n'
               '--REL\r\nContent-Type: text/html\r\n\r\n<p>x</p>\r\n--REL--\r\n--ALT2--')
    assert _accepted(_deliver(att, _mixed(wrapper, _b64_attachment(5))))


@pytest.mark.parametrize('leaf,expected', [
    ('Content-Type: text/plain\r\n\r\nleaf', 0),
    ('Content-Type: application/octet-stream\r\n\r\nzz', 1)])
def test_deep_nesting_is_walked_without_refusal(leaf, expected):
    """300 nested body wrappers (audit MIME-08) stay deliverable, and a leaf
    attachment at the bottom is still counted."""
    from email import message_from_bytes, policy
    from types import SimpleNamespace
    from anetbbs.mail import limits
    raw = leaf
    for i in range(300):
        raw = (f'Content-Type: multipart/mixed; boundary="N{i}"\r\n\r\n'
               f'--N{i}\r\n{raw}\r\n--N{i}--')
    msg = message_from_bytes(('Subject: deep\r\n' + raw).encode(), policy=policy.default)
    cfg = SimpleNamespace(max_attachments=20, max_attachment_bytes=10 ** 6)
    assert limits.check_attachment_limits(msg, cfg) == expected
    if expected:
        with pytest.raises(limits.AttachmentLimitExceeded):
            limits.check_attachment_limits(
                msg, SimpleNamespace(max_attachments=0, max_attachment_bytes=10 ** 6))


# -- decoded size ----------------------------------------------------------------

@pytest.mark.parametrize('size,accepted', [(1000, True), (1001, False)])
def test_base64_decoded_size_boundary(att, size, accepted):
    out = _deliver(att, _mixed(_b64_attachment(size)))
    assert (_accepted(out) if accepted else _refused_552(out)), out


def test_size_limit_uses_decoded_not_encoded_bytes(att):
    # 1000 decoded bytes are ~1352 base64 characters on the wire: accepted
    raw = _mixed(_b64_attachment(1000))
    assert len(raw) > 1300
    assert _accepted(_deliver(att, raw))


@pytest.mark.parametrize('size,accepted', [(1000, True), (1001, False)])
def test_quoted_printable_decoded_size_boundary(att, size, accepted):
    # '=41' decodes to one byte: encoded length is 3x the decoded length
    qp = '=\r\n'.join('=41' * 25 for _ in range(size // 25)) + '=41' * (size % 25)
    out = _deliver(att, _mixed(_part(qp, cte='quoted-printable', filename='q.txt',
                                     ctype='application/octet-stream')))
    assert (_accepted(out) if accepted else _refused_552(out)), out


@pytest.mark.parametrize('size,accepted', [(1000, True), (1001, False)])
def test_8bit_attachment_size_boundary(att, size, accepted):
    out = _deliver(att, _mixed(_part('z' * size, cte='8bit', filename='z.txt')))
    assert (_accepted(out) if accepted else _refused_552(out)), out


# -- malformed encodings -----------------------------------------------------------

def test_base64_with_invalid_characters_counts_every_alphabet_char(att):
    # 1600 alphabet chars (=1200 bytes if decoded by a skip-garbage decoder)
    # with '*' noise between them; must not slip under a 1000-byte limit.
    data = ''.join('QUFB*' for _ in range(400))
    lines = '\r\n'.join(data[i:i + 76] for i in range(0, len(data), 76))
    assert _refused_552(_deliver(att, _mixed(_part(lines))))


def test_base64_with_misplaced_padding_cannot_hide_trailing_data(att):
    # 'QQ==' then 1600 more alphabet chars: a decoder that stops at the first
    # padding sees 1 byte; one that keeps going sees ~1200.
    hidden = 'QQ==' + 'QUFB' * 400
    lines = '\r\n'.join(hidden[i:i + 76] for i in range(0, len(hidden), 76))
    assert _refused_552(_deliver(att, _mixed(_part(lines))))


def test_truncated_base64_under_limit_is_accepted(att):
    # malformed (length not a multiple of 4) but genuinely small
    assert _accepted(_deliver(att, _mixed(_part('QUFBQUFBQ'))))


def test_unknown_transfer_encoding_counts_raw_bytes(att):
    out = _deliver(att, _mixed(_part('y' * 1500, cte='x-made-up-encoding')))
    assert _refused_552(out), out


def test_malformed_content_type_header_fails_closed_as_attachment(att):
    set_cfg(att, max_attachments=0)
    broken = 'Content-Type: ;;;=\r\nContent-Disposition: attachment; filename*=bad\'\'%ZZ\r\n\r\nxyz'
    out = _deliver(att, _mixed(broken))
    assert out['data'] is not None and out['data'][:1] == '5', out
    assert inbound_rows(att) == []


def test_check_runs_before_any_row_is_written(att):
    """Direct call: the refusal is raised by _persist itself and no row,
    even for other recipients, exists afterwards."""
    from anetbbs.mail import smtp_server, limits
    other = make_user(att, 'lim_att2')
    raw = _mixed(*[_b64_attachment(10, filename=f'f{i}') for i in range(5)])
    with att.app_context():
        with pytest.raises(limits.AttachmentLimitExceeded) as exc:
            smtp_server._persist('s@remote.test', [addr('lim_att'), addr('lim_att2')], raw)
    assert exc.value.reply.startswith('552 5.3.4')
    assert inbound_rows(att) == []
    assert other


def test_session_continues_normally_after_a_refusal(att):
    big = _mixed(_b64_attachment(5000))
    small = _mixed(_b64_attachment(10))

    async def scenario(connect):
        c = await connect()
        await c.send('EHLO client.test')
        out = []
        for raw in (big, small):
            await c.send('MAIL FROM:<s@remote.test>')
            await c.send(f'RCPT TO:<{addr("lim_att")}>')
            await c.send('DATA')
            out.append(await c.send_data(raw))
        await c.send('QUIT')
        await c.close()
        return out

    first, second = run_with_server(scenario)
    assert first.startswith('552 5.3.4') and second.startswith('250'), (first, second)
    assert len(inbound_rows(att)) == 1
