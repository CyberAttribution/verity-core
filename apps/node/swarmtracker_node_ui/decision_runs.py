"""Durable governed decisions. No network, shell, or external action executor.

Each operation uses its own SQLite connection and BEGIN IMMEDIATE. An allowed
simulation and its audit outcome commit atomically, so retries/concurrent
requests cannot execute the simulator twice. Failed writes roll back all state.
This does not offer exactly-once execution for future external side effects.
"""
import copy
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from verity_core.trust import decision, record as trust
from verity_core.json_safety import bounded_json_loads
from . import store


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def evidence(eid, label, content):
    return {'id': eid, 'label': label, 'text': content,
            'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}


def synthetic_manifest(mode='require_approval'):
    """Deterministic fixtures, NOT model responses or live agent delegation."""
    return {'schema': decision.SCHEMA, 'title': 'Synthetic suspicious-message review',
            'source_kind': 'synthetic',
            'participants': [
                {'id': 'demo-intake', 'name': 'Intake participant (simulation)',
                 'framework': 'Deterministic demo fixture', 'model': 'None — fixture',
                 'identity_provenance': 'simulated participant', 'delegated_by': None},
                {'id': 'demo-validator', 'name': 'Delegated validator (simulation)',
                 'framework': 'Deterministic demo fixture', 'model': 'None — fixture',
                 'identity_provenance': 'simulated participant', 'delegated_by': 'demo-intake'}],
            'evidence': [
                evidence('message', 'Synthetic message', 'SYNTHETIC: Urgent payment change requested by sender@example.invalid.'),
                evidence('directory', 'Synthetic conflicting directory', 'SYNTHETIC: The vendor directory lists a different payment contact; the request is unconfirmed.')],
            'findings': [
                {'participant_id': 'demo-intake', 'evidence_ids': ['message'],
                 'statement': 'The urgent payment change is suspicious; recommend a simulated sender block.',
                 'provenance': 'deterministic synthetic fixture', 'conflicting': False},
                {'participant_id': 'demo-validator', 'evidence_ids': ['message', 'directory'],
                 'statement': 'The directory conflicts with the claimed contact. The discrepancy does not establish malicious authorship; confirm out of band.',
                 'provenance': 'deterministic synthetic fixture', 'conflicting': True}],
            'recommendation': 'Preserve the disagreement and request operator review before a simulated sender block.',
            'action': {'kind': 'simulate_sender_block', 'target': 'sender@example.invalid'},
            'policy': decision.policy_snapshot(mode)}


def analysis_manifest(job, mode='require_approval'):
    """Snapshot a successful existing single analysis, preserving its provenance.

    No second participant, delegation, or conflicting finding is invented.
    The proposed controlled action still targets only the synthetic address.
    """
    participant = job['decision_participant']
    report = job['report']
    items = job['items']
    return {'schema': decision.SCHEMA, 'title': 'Review analysis: ' + str(job['report_id'])[:120],
            'source_kind': 'analysis_snapshot', 'participants': [copy.deepcopy(participant)],
            'evidence': [evidence(f'input-{i}', item.get('label') or 'Evidence', item['text']) for i, item in enumerate(items)],
            'findings': [{'participant_id': participant['id'],
                          'evidence_ids': [f'input-{i}' for i in range(len(items))],
                          'statement': str(report.get('fused_verdict') or 'No verdict recorded')[:4000],
                          'provenance': 'snapshot of successful local analysis; runtime identity self-reported',
                          'conflicting': False}],
            'recommendation': 'Operator-requested simulator review linked to this analysis. This does not imply the model recommended blocking.',
            'action': {'kind': 'simulate_sender_block', 'target': 'sender@example.invalid'},
            'policy': decision.policy_snapshot(mode)}


class RunStore:
    def __init__(self, path=None):
        self.path = Path(path or store.DB_PATH.with_name('governed-decisions.db'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE IF NOT EXISTS decision_runs (id TEXT PRIMARY KEY, record TEXT NOT NULL)')

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            con.execute('PRAGMA synchronous=FULL')
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _load(self, con, rid):
        row = con.execute('SELECT record FROM decision_runs WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise NotFound(rid)
        try:
            record = bounded_json_loads(row[0])
            view = decision.inspect(record)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as e:
            raise ValueError('Stored decision failed verification') from e
        if view['id'] != rid:
            raise ValueError('Stored record ID mismatch')
        return record, view

    def _save(self, con, record):
        decision.inspect(record)
        con.execute('UPDATE decision_runs SET record=? WHERE id=?',
                    (trust.canonical_json(record), record['record_id']))

    def create(self, manifest):
        record = decision.new_run(manifest)
        with self._connection() as con:
            con.execute('INSERT INTO decision_runs VALUES (?,?)',
                        (record['record_id'], trust.canonical_json(record)))
        return decision.inspect(record)

    def get(self, rid):
        with self._connection() as con:
            return self._load(con, rid)[1]

    def recent(self, limit=50):
        with self._connection() as con:
            ids = con.execute('SELECT id FROM decision_runs ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
            views = []
            for (rid,) in ids:
                try:
                    views.append(self._load(con, rid)[1])
                except (ValueError, TypeError, KeyError, AttributeError):
                    views.append({'id': rid, 'integrity_error': True})
            return views

    def authorize(self, rid, choice, reason, actor, expected_head):
        if choice not in ('approved', 'rejected'):
            raise ValueError('Approval or rejection required')
        decision.text(reason, 'operator reason', 1000)
        decision.text(actor, 'operator label', 120)
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            record, view = self._load(con, rid)
            if view['head_hash'] != expected_head:
                raise Conflict('This run changed. Reload before deciding.')
            if view['decision'] != 'pending':
                raise Conflict('Authorization is already final for this run.')
            trust.append_entry(record, 'approval_granted' if choice == 'approved' else 'note', actor=actor,
                               payload={'event': 'authorization_decided', 'decision': choice,
                                        'reason': reason, 'binding': decision.binding(view['manifest']),
                                        'identity_provenance': 'local operator intent; unauthenticated'})
            self._save(con, record)
        return decision.inspect(record)

    def execute(self, rid, actor, expected_head):
        """Only execution entry point; UI state is never authority."""
        decision.text(actor, 'operator label', 120)
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            record, view = self._load(con, rid)
            if view['head_hash'] != expected_head:
                raise Conflict('This run changed. Reload before executing.')
            attempt = trust.append_entry(record, 'action_attempted', actor=actor,
                                         payload={'event': 'action_attempted', 'allowed': view['can_execute'],
                                                  'binding': decision.binding(view['manifest'])})
            if view['can_execute']:
                trust.append_entry(record, 'note', actor=actor, payload={
                    'event':'action_executed', 'attempt_hash':attempt['entry_hash'],
                    'binding':decision.binding(view['manifest']),
                    'executor':'fixed_local_simulator', 'external_action_executed':False})
            # No externally callable executor: the simulator IS this recorded
            # outcome. It cannot send messages, change files, or block a sender.
            trust.append_entry(record, 'action_result', payload={
                'event': 'action_result', 'attempt_hash': attempt['entry_hash'],
                'outcome': 'simulated' if view['can_execute'] else 'denied',
                'external_action_executed': False})
            self._save(con, record)
        return decision.inspect(record), view['can_execute']
