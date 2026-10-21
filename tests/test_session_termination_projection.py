import pytest
from swarmtracker_node_ui.sessions import derive

NOW=1792542000.


def event(eid,kind,data,at,source='provider_console'):
    return dict(id=eid,kind=kind,data=data,at=at,received=at,source=source)


def record(*entries):
    return dict(identity=dict(id='termination'),events=list(entries))


def test_provider_confirmed_end_stops_active_setup_failure_and_budget_warnings():
    snapshot=record(
        event('running','provider',dict(state='running'),NOW-7200),
        event('billing','billing',dict(rate=22.32,billing_start=NOW-7200,ceiling=20),NOW-7200),
        event('failure','model',dict(state='failed'),NOW-7100,source='model_server'),
        event('end','shutdown',dict(confirmed=True),NOW-3600),
        event('volume','resource',dict(name='retained-volume',state='attached',billable=True),NOW-3599))
    view=derive(snapshot,now=NOW)
    assert view['termination_confirmed'] and view['provider']=='terminated'
    assert not any('setup' in w or 'Model failure' in w or 'Approaching' in w for w in view['alerts'])
    assert view['retained_resources'][0]['name']=='retained-volume'
    assert view['reported_charge'] is None and view['estimated_charge']==22.32


def test_later_provider_end_correction_updates_estimate_without_rewriting_history():
    entries=[
        event('billing','billing',dict(rate=22.32,billing_start=NOW-3600),NOW-3600),
        event('initial-end','shutdown',dict(confirmed=True),NOW-300),
        event('corrected-end','provider',dict(state='terminated'),NOW-100)]
    view=derive(record(*entries),now=NOW)
    assert view['estimated_charge']==pytest.approx(21.70,abs=.001)
    assert view['termination_confirmed'] and entries[1]['at']==NOW-300
    assert view['reported_charge'] is None


def test_operator_claim_cannot_extend_provider_confirmed_end():
    entries=[
        event('billing','billing',dict(rate=22.32,billing_start=NOW-3600),NOW-3600),
        event('initial-end','shutdown',dict(confirmed=True),NOW-300),
        event('corrected-end','provider',dict(state='terminated'),NOW-100),
        event('operator-claim','provider',dict(state='terminated'),NOW-10,source='operator')]
    view=derive(record(*entries),now=NOW)
    assert view['estimated_charge']==pytest.approx(21.70,abs=.001)
    assert view['termination_confirmed']
