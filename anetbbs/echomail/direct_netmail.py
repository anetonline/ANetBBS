"""Recipient-specific BinkP delivery for networked crash/direct netmail.

An upstream hub's credentials belong to that link, not to arbitrary
nodelisted recipients. Only the destination's own configured link may
supply credentials; an otherwise nodelisted recipient gets an anonymous
session and an empty packet password.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import ipaddress
import json
import logging
import re

from sqlalchemy import func, or_

from ..models import db, BinkPNode, EchomailNetwork, EchomailPollLog
from ..models import NetmailMessage, Nodelist, NodelistEntry
from .routing import parse_address, self_hub_binkp_network

logger = logging.getLogger(__name__)
_ATTEMPT_TIMEOUT = timedelta(minutes=30)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int = 24554
    password: str = ''
    use_tls: bool = False
    packet_password: str = ''
    source: str = 'nodelist'
    node_id: int = None


def address_key(address):
    """Compare bare and qualified addresses, including explicit .0."""
    return parse_address((address or '').strip().split('@', 1)[0])


def requires_direct(message, network):
    return bool(message.is_crash or network.default_crash or network.default_direct)


def _host(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    if '%' in value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    labels = value.rstrip('.').split('.')
    if len(value) > 253 or not all(re.fullmatch(
            r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
            for label in labels):
        return None
    return value


def nodelist_endpoint(entry):
    """Read FTS-5001 INA/IBN, including legacy IBN:host[:port].

    IBN must actually be advertised. Never infer BinkP from a telnet
    flag, invent DNS from an FTN address, or use an older removed entry.
    """
    if (entry.keyword_type or '').lower() in ('down', 'hold'):
        return None
    try:
        flags = json.loads(entry.flags or '{}')
    except (ValueError, TypeError):
        return None
    if not isinstance(flags, dict):
        return None
    flags = {str(k).upper(): v for k, v in flags.items()}
    if 'IBN' not in flags or 'DOWN' in flags or 'HOLD' in flags:
        return None
    host = flags.get('INA')
    port = 24554
    ibn = flags['IBN']
    if ibn is not True:
        if not isinstance(ibn, str):
            return None
        if ibn.isascii() and ibn.isdecimal():
            port = int(ibn)
        elif ibn:
            # Brackets disambiguate an IPv6 literal followed by a port.
            match = re.fullmatch(r'\[([^\]]+)\](?::([0-9]+))?', ibn)
            if match:
                host, custom_port = match.groups()
                if custom_port:
                    port = int(custom_port)
            elif ibn.count(':') == 1:
                host, custom_port = ibn.rsplit(':', 1)
                if not custom_port.isascii() or not custom_port.isdecimal():
                    return None
                port = int(custom_port)
            else:
                host = ibn
    if not host:
        # Legacy Internet address in the phone field (000-a-b-c-d).
        phone = entry.phone or ''
        if phone.startswith('000-'):
            host = phone[4:].replace('-', '.')
        elif '.' in phone or ':' in phone:
            host = phone
    host = _host(host)
    return Endpoint(host, port) if host and 1 <= port <= 65535 else None


def resolve_endpoint(network, address):
    """Resolve within this network only; return (endpoint, error)."""
    key = address_key(address)
    domain = (network.ftn_domain or network.name or '').strip().lower()
    suffix = (address or '').partition('@')[2].strip().lower()
    if key is None or (suffix and suffix != domain):
        return None, 'Invalid destination or destination domain does not match the network'

    is_hub = key == address_key(network.hub_address)
    if is_hub and (network.binkp_host or '').strip():
        return Endpoint(network.binkp_host.strip(), network.binkp_port or 24554,
                        network.binkp_password or '', bool(network.binkp_tls),
                        network.packet_password or '', 'configured hub'), None

    node = None
    candidates = BinkPNode.query.filter(
        BinkPNode.is_active.is_(True),
        or_(BinkPNode.network_id == network.id,
            BinkPNode.network_id.is_(None))).all()
    for candidate in candidates:
        if address_key(candidate.ftn_address) != key:
            continue
        candidate_suffix = candidate.ftn_address.partition('@')[2].strip().lower()
        if candidate_suffix and candidate_suffix != domain:
            continue
        owner = candidate.network or self_hub_binkp_network(candidate.hub_identity_id)
        if owner is not None and owner.id == network.id:
            node = candidate
            break
    if node is not None and (node.binkp_host or '').strip():
        return Endpoint(node.binkp_host.strip(), node.binkp_port or 24554,
                        node.password or '', bool(node.binkp_tls),
                        source='configured node', node_id=node.id), None

    nodelist = Nodelist.query.filter(func.lower(Nodelist.domain) == domain).order_by(
        Nodelist.release_date.desc(), Nodelist.imported_at.desc(), Nodelist.id.desc()).first()
    if nodelist is None:
        return None, f'No nodelist imported for {domain}'
    z, net, number, point = key
    entry = NodelistEntry.query.filter_by(
        nodelist_id=nodelist.id, zone=z, net=net, node=number, point=point).first()
    endpoint = nodelist_endpoint(entry) if entry is not None else None
    if endpoint is None:
        return None, f'No usable BinkP endpoint for {address} in the current {domain} nodelist'
    if is_hub:
        return Endpoint(endpoint.host, endpoint.port, network.binkp_password or '',
                        bool(network.binkp_tls), network.packet_password or '',
                        source='nodelist with configured hub credentials'), None
    if node is not None:
        endpoint = Endpoint(endpoint.host, endpoint.port, node.password or '',
                            bool(node.binkp_tls), node_id=node.id,
                            source='nodelist with configured node credentials')
    return endpoint, None


def recover_interrupted(network_id):
    """Release an abandoned claim without keeping a DB transaction open during I/O."""
    cutoff = datetime.utcnow() - _ATTEMPT_TIMEOUT
    count = NetmailMessage.query.filter(
        NetmailMessage.network_id == network_id,
        NetmailMessage.status == 'sending',
        NetmailMessage.delivery_started_at < cutoff,
    ).update({NetmailMessage.status: 'queued',
              NetmailMessage.delivery_started_at: None,
              NetmailMessage.error_message: 'Interrupted direct attempt; queued for retry'},
             synchronize_session=False)
    EchomailPollLog.query.filter(
        EchomailPollLog.network_id == network_id,
        EchomailPollLog.poll_type == 'send',
        EchomailPollLog.status == 'running',
        EchomailPollLog.started_at < cutoff,
    ).update({EchomailPollLog.status: 'error',
              EchomailPollLog.completed_at: datetime.utcnow(),
              EchomailPollLog.error_message: 'Interrupted direct attempt; lease expired'},
             synchronize_session=False)
    db.session.commit()
    return count


def retry_queued_netmail(app, network):
    """Retry on the network's poll schedule, including networks hosted here."""
    if network.network_type != 'binkp':
        return 0
    recover_interrupted(network.id)
    query = NetmailMessage.query.filter(
        NetmailMessage.network_id == network.id,
        NetmailMessage.direction == 'outbound',
        NetmailMessage.status == 'queued',
        NetmailMessage.is_hold.isnot(True))
    if not (network.default_crash or network.default_direct):
        query = query.filter(NetmailMessage.is_crash.is_(True))
    ids = [nm.id for nm in query.all()]
    for netmail_id in ids:
        send_queued_netmail(app, netmail_id)
    return len(ids)


def send_queued_netmail(app, netmail_id):
    """Send one crash message to its recipient; never silently relay to the hub."""
    from .binkp import BinkPClient
    from .poller import _NetmailAdapter, _format_transcript, _import_netmail

    with app.app_context():
        nm = db.session.get(NetmailMessage, netmail_id)
        if nm is None or nm.direction != 'outbound' or nm.status != 'queued' or nm.is_hold:
            return
        network = db.session.get(EchomailNetwork, nm.network_id) if nm.network_id else None
        if (network is None or not network.is_active or network.network_type != 'binkp'
                or not requires_direct(nm, network)):
            return
        # An atomic claim keeps an immediate send and a scheduled retry
        # from transmitting the same message in separate worker processes.
        claimed = NetmailMessage.query.filter_by(id=nm.id, status='queued').update(
            {NetmailMessage.status: 'sending',
             NetmailMessage.delivery_started_at: datetime.utcnow()},
            synchronize_session=False)
        db.session.commit()
        if not claimed:
            return
        db.session.refresh(nm)
        log = EchomailPollLog(network_id=network.id, poll_type='send', status='running')
        db.session.add(log)
        db.session.commit()
        transcript = []
        acknowledged = False
        client = None
        try:
            endpoint, error = resolve_endpoint(network, nm.to_address)
            if endpoint is None:
                raise ValueError(error)
            log.node_id = endpoint.node_id
            transcript.append(f'ROUTE DIRECT netmail={nm.id} to={nm.to_address} '
                              f'via={endpoint.source} endpoint={endpoint.host}:{endpoint.port}')
            logger.info('%s', transcript[-1])
            # Persist routing details even if the process is interrupted.
            log.transcript = _format_transcript(transcript)
            db.session.commit()
            client = BinkPClient(
                host=endpoint.host, port=endpoint.port,
                our_address=network.our_address,
                hub_address=nm.to_address.split('@', 1)[0],
                password=endpoint.password, packet_password=endpoint.packet_password,
                use_tls=endpoint.use_tls,
                domain=network.ftn_domain or network.name,
                default_crash=True, transcript=transcript,
                require_remote_address=True)
            result = client.poll(outbound_messages=[_NetmailAdapter(nm)],
                                 data_dir=app.config.get('ECHOMAIL_DATA_DIR', 'data'))
            if result.get('sent', 0) != 1:
                raise ConnectionError(f'Packet not acknowledged by {endpoint.host}:{endpoint.port}')
            acknowledged = True
            nm.status = 'sent'
            nm.is_sent = True
            nm.sent_at = datetime.utcnow()
            nm.error_message = None
            log.messages_sent = 1
            log.status = 'success'
            # Commit the acknowledgment before processing peer replies.
            db.session.commit()
            for message in result.get('received', []):
                if message.get('area_tag'):
                    logger.warning('Direct peer %s returned unexpected echomail; not imported',
                                   nm.to_address)
                    continue
                try:
                    with db.session.begin_nested():
                        imported = _import_netmail(network, message,
                            trust='secure' if endpoint.password else 'insecure')
                        log.messages_received = (log.messages_received or 0) + imported
                except Exception:
                    logger.exception('Could not import netmail returned by direct peer %s',
                                     nm.to_address)
        except Exception as exc:
            # Import/logging errors after an acknowledged packet must
            # never put the delivered message back on the send queue.
            db.session.rollback()
            nm = db.session.get(NetmailMessage, netmail_id)
            acknowledged = acknowledged or (client is not None
                                             and client.acknowledged_messages == 1)
            detail = f'{type(exc).__name__}: {exc}'
            log.status = 'partial' if acknowledged else 'error'
            log.error_message = detail
            if acknowledged:
                nm.status = 'sent'
                nm.is_sent = True
                nm.sent_at = nm.sent_at or datetime.utcnow()
                nm.error_message = None
                log.messages_sent = 1
            else:
                nm.status = 'queued'
                nm.error_message = f'Direct delivery failed: {detail}'
            transcript.append(detail)
            logger.warning('Direct netmail %s: %s', nm.id, detail)
        finally:
            nm.delivery_started_at = None
            log.completed_at = datetime.utcnow()
            log.transcript = _format_transcript(transcript)
            db.session.commit()
