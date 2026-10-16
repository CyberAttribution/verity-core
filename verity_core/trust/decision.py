"""Portable verification for governed_decision/v0.1 (standard library only).

This is external application policy enforcement over a local simulator. The
operator label is an unauthenticated local intent assertion. A chain/checkpoint
bundled with its own record is not an independent anchor; retain checkpoints
separately to detect full rewrites or suffix removal. No evidence truth,
identity authentication, cloud authorization, or model intervention is implied.
"""
import copy
import hashlib
from . import record as trust

SCHEMA = 'governed_decision/v0.1'
LIMITS = ('Integrity is relative to the retained checkpoint. This does not prove '
          'evidence truth, actor identity, complete event capture, or immutable storage. '
          'Approval records local operator intent, not authenticated human identity. '
          'The only controlled action is a local simulation; no sender is blocked '
          'and no evidence is sent to a cloud provider.')


def digest(value):
    return hashlib.sha256(trust.canonical_json(value).encode('utf-8')).hexdigest()


def text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} must contain 1–{maximum} characters')
    value.encode('utf-8')
    return value


def validate_manifest(m):
    if not isinstance(m, dict) or m.get('schema') != SCHEMA:
        raise ValueError('Unknown decision schema')
    text(m.get('title'), 'title', 160)
    if m.get('source_kind') not in ('synthetic', 'analysis_snapshot'):
        raise ValueError('Unsupported source kind')
    participants = m.get('participants')
    if not isinstance(participants, list) or not 1 <= len(participants) <= 8:
        raise ValueError('One to eight participants required')
    ids = set()
    for p in participants:
        if not isinstance(p, dict):
            raise ValueError('Participant must be an object')
        pid = text(p.get('id'), 'participant ID', 120)
        if pid in ids:
            raise ValueError('Duplicate participant ID')
        parent = p.get('delegated_by')
        if parent is not None and parent not in ids:
            raise ValueError('Delegation must reference an earlier participant')
        ids.add(pid)
        for key in ('name', 'framework', 'model', 'identity_provenance'):
            text(p.get(key), key, 300)
    evidence = m.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise ValueError('One to eight evidence items required')
    eids = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError('Evidence item must be an object')
        eid = text(item.get('id'), 'evidence ID', 120)
        if eid in eids:
            raise ValueError('Duplicate evidence ID')
        eids.add(eid)
        text(item.get('label'), 'evidence label', 300)
        content = text(item.get('text'), 'evidence', 65536)
        if item.get('sha256') != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError('Evidence hash mismatch')
    findings = m.get('findings')
    if not isinstance(findings, list) or not 1 <= len(findings) <= 16:
        raise ValueError('One to sixteen findings required')
    for f in findings:
        if not isinstance(f, dict):
            raise ValueError('Finding must be an object')
        refs = f.get('evidence_ids')
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(x, str) or x not in eids for x in refs)
                or len(refs) != len(set(refs)) or f.get('participant_id') not in ids):
            raise ValueError('Finding has an unknown participant or evidence reference')
        text(f.get('statement'), 'finding', 4000)
        text(f.get('provenance'), 'finding provenance', 300)
        if type(f.get('conflicting')) is not bool:
            raise ValueError('Finding conflict flag must be boolean')
    text(m.get('recommendation'), 'recommendation')
    action = m.get('action')
    if action != {'kind': 'simulate_sender_block', 'target': 'sender@example.invalid'}:
        raise ValueError('Only the fixed local simulator is supported')
    policy = m.get('policy')
    if not isinstance(policy, dict):
        raise ValueError('Policy must be an object')
    mode = policy.get('mode')
    if mode not in ('require_approval', 'deny') or policy != policy_snapshot(mode):
        raise ValueError('Unsupported or altered policy snapshot')
    return m


def policy_snapshot(mode):
    if mode == 'require_approval':
        return {'id': 'controlled-simulation', 'version': '1', 'mode': mode,
                'reason': 'Explicit local operator approval is required before this simulated action.'}
    if mode == 'deny':
        return {'id': 'controlled-simulation', 'version': '2', 'mode': mode,
                'reason': 'This policy denies the simulated action, including after operator approval.'}
    raise ValueError('Unknown policy mode')


def new_run(manifest):
    validate_manifest(manifest)
    r = trust.new_record({'kind': 'governed_decision', 'identifier': manifest['title']})
    manifest = copy.deepcopy(manifest)
    manifest['authorization_scope'] = r['record_id']
    trust.append_entry(r, 'note', payload={'event': 'run_created', 'manifest': manifest})
    return r


def binding(manifest):
    scope = ({'authorization_scope':manifest['authorization_scope']} if 'authorization_scope' in manifest else {})
    return digest({**scope, 'action': manifest['action'], 'policy': manifest['policy'],
                   'evidence': manifest['evidence'], 'findings': manifest['findings'],
                   'participants': manifest['participants'], 'recommendation': manifest['recommendation']})


def inspect(r):
    """Validate chain AND replay the supported state machine; fail closed."""
    if not trust.chain_valid(r) or not r['entries']:
        raise ValueError('Trust Record integrity failed')
    first = r['entries'][0]
    if first['entry_type'] != 'note' or first['payload'].get('event') != 'run_created':
        raise ValueError('Missing decision manifest')
    m = validate_manifest(first['payload']['manifest'])
    if 'authorization_scope' in m and m['authorization_scope'] != r['record_id']:
        raise ValueError('Authorization scope does not match this record')
    if r['subject'] != {'kind': 'governed_decision', 'identifier': m['title']} or r['refs'] != {}:
        raise ValueError('Record metadata does not match the manifest')
    decision = 'pending'
    executed = False
    pending_attempt = None
    attempts = 0
    execution_seen = False
    for entry in r['entries'][1:]:
        p = entry['payload']
        event = p.get('event')
        if pending_attempt is not None and event not in ('action_executed', 'action_result'):
            raise ValueError('Action attempt is missing its outcome')
        if event == 'authorization_decided':
            if entry['entry_type'] not in ('approval_granted', 'note') or decision != 'pending' or executed:
                raise ValueError('Invalid authorization transition')
            decision = p.get('decision')
            if decision not in ('approved', 'rejected') or p.get('binding') != binding(m):
                raise ValueError('Authorization is not bound to this decision')
            if (entry['entry_type'] == 'approval_granted') != (decision == 'approved'):
                raise ValueError('Authorization entry type mismatch')
            text(p.get('reason'), 'authorization reason', 1000)
            if p.get('identity_provenance') != 'local operator intent; unauthenticated':
                raise ValueError('Unsupported operator identity provenance')
        elif event == 'action_attempted':
            if entry['entry_type'] != 'action_attempted':
                raise ValueError('Invalid attempt entry type')
            allowed = decision == 'approved' and m['policy']['mode'] == 'require_approval' and not executed
            if type(p.get('allowed')) is not bool or p['allowed'] != allowed or p.get('binding') != binding(m):
                raise ValueError('Action boundary state mismatch')
            pending_attempt = entry
            execution_seen = False
            attempts += 1
        elif event == 'action_executed':
            if (entry['entry_type'] != 'note' or pending_attempt is None or execution_seen
                    or not pending_attempt['payload']['allowed']):
                raise ValueError('Execution has no authorized attempt')
            expected = {'event':'action_executed', 'attempt_hash':pending_attempt['entry_hash'],
                        'binding':binding(m), 'executor':'fixed_local_simulator',
                        'external_action_executed':False}
            if p != expected:raise ValueError('Execution does not match the authorized action')
            execution_seen = True
        elif event == 'action_result':
            if entry['entry_type'] != 'action_result' or pending_attempt is None:
                raise ValueError('Outcome has no attempt')
            allowed = pending_attempt['payload']['allowed']
            if allowed and 'authorization_scope' in m and not execution_seen:
                raise ValueError('Authorized attempt has no execution event')
            expected = {'event': 'action_result', 'attempt_hash': pending_attempt['entry_hash'],
                        'outcome': 'simulated' if allowed else 'denied',
                        'external_action_executed': False}
            if p != expected:
                raise ValueError('Unexpected simulator outcome')
            executed = executed or allowed
            pending_attempt = None
        else:
            raise ValueError('Unknown decision event')
    if pending_attempt is not None:
        raise ValueError('Incomplete action transaction')
    return {'id': r['record_id'], 'manifest': copy.deepcopy(m), 'decision': decision,
            'executed': executed, 'attempts': attempts,
            'can_execute': decision == 'approved' and m['policy']['mode'] == 'require_approval' and not executed,
            'state': 'simulated' if executed else ('policy_denied' if m['policy']['mode'] == 'deny' else decision),
            'record': copy.deepcopy(r), 'head_hash': r['entries'][-1]['entry_hash'],
            'entry_count': len(r['entries']), 'limits': LIMITS}


def checkpoint(r):
    view = inspect(r)
    return {'schema': SCHEMA, 'record_id': r['record_id'],
            'entry_count': view['entry_count'], 'head_hash': view['head_hash'],
            'record_sha256': digest(r)}


def export(r):
    return {'schema': SCHEMA, 'record': copy.deepcopy(r), 'checkpoint': checkpoint(r), 'limits': LIMITS}


def verify(bundle, expected_checkpoint=None):
    """An independently retained checkpoint is optional, never implied."""
    try:
        if bundle.get('schema') != SCHEMA:
            raise ValueError('Unsupported export schema')
        actual = checkpoint(bundle['record'])
        if bundle.get('checkpoint') != actual:
            raise ValueError('Export checkpoint mismatch')
        if expected_checkpoint is not None and expected_checkpoint != actual:
            raise ValueError('Independently retained checkpoint mismatch')
        return {'verified': True, 'anchored': expected_checkpoint is not None, 'issues': [], 'limits': LIMITS}
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as e:
        return {'verified': False, 'anchored': False, 'issues': [str(e)], 'limits': LIMITS}
