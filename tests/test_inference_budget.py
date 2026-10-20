import threading
import pytest
from verity_core.inference_budget import BudgetLedger,BudgetDenied


@pytest.fixture
def env(tmp_path):
    ledger=BudgetLedger(tmp_path/'budget.db',clock=lambda:1792530000)
    policy=ledger.set_policy('agent',daily_microusd=100,session_microusd=80)
    return ledger,policy


def reserve(env,id='request',amount=60,session='session'):
    ledger,policy=env
    return ledger.reserve(id,'agent',session_id=session,estimate_microusd=amount,expected_policy_digest=policy['digest'])


def test_reservation_dispatch_unknown_restart_and_reconciliation(env):
    l,p=env;reserve(env);l.mark_dispatched('request');l.mark_unknown('request')
    reopened=BudgetLedger(l.path,clock=l.clock)
    with pytest.raises(BudgetDenied):reserve((reopened,p),'second',30)
    with pytest.raises(BudgetDenied):reopened.release_undispatched('request',reason='Cannot treat timeout as free')
    with pytest.raises(BudgetDenied):reopened.mark_dispatched('request')
    assert reopened.reconcile('request',actual_microusd=40)['actual']==40
    assert reserve((reopened,p),'second',40)['state']=='reserved'
    assert reopened.reconcile('request',actual_microusd=40)['actual']==40
    with pytest.raises(BudgetDenied):reopened.reconcile('request',actual_microusd=41)


def test_concurrent_reservations_serialize_before_dispatch(env):
    l,p=env;other=BudgetLedger(l.path,clock=l.clock);barrier=threading.Barrier(2);accepted=[];denied=[]
    def attempt(ledger,id):
        barrier.wait()
        try:reserve((ledger,p),id,60);accepted.append(id)
        except BudgetDenied:denied.append(id)
    threads=[threading.Thread(target=attempt,args=(ledger,str(i))) for i,ledger in enumerate((l,other))]
    for t in threads:t.start()
    for t in threads:t.join(5)
    assert len(accepted)==len(denied)==1


def test_same_request_id_is_never_a_new_dispatch_authorization(env):
    l,p=env;reserve(env)
    with pytest.raises(BudgetDenied):reserve(env)
    l.mark_dispatched('request')
    with pytest.raises(BudgetDenied):l.mark_dispatched('request')
    with pytest.raises(BudgetDenied):reserve(env)


def test_policy_change_and_utc_rollover_block_pending_dispatch(env):
    l,p=env;reserve(env);new=l.set_policy('agent',daily_microusd=200,session_microusd=200)
    with pytest.raises(BudgetDenied):l.mark_dispatched('request')
    with pytest.raises(BudgetDenied):reserve(env,'old-policy',1)
    l.release_undispatched('request',reason='Policy changed before dispatch')
    reserve((l,new),'next-day',1);l.clock=lambda:1792530000+86400
    with pytest.raises(BudgetDenied):l.mark_dispatched('next-day')


def test_actual_overrun_is_retained_and_blocks_further_reservations(env):
    l,p=env;reserve(env,amount=20);l.mark_dispatched('request')
    outcome=l.reconcile('request',actual_microusd=120)
    assert outcome['estimate_overrun'] is True and outcome['actual']==120
    with pytest.raises(BudgetDenied):reserve(env,'later',1)


def test_known_undispatched_release_restores_capacity_without_reusing_id(env):
    l,p=env;reserve(env);l.release_undispatched('request',reason='Operator canceled before dispatch')
    reserve(env,'new',80)
    with pytest.raises(BudgetDenied):reserve(env)
    with pytest.raises(BudgetDenied):l.reconcile('request',actual_microusd=0)


def test_daily_and_session_caps_are_distinct(env):
    l,p=env;reserve(env,'one',60,'session-A')
    with pytest.raises(BudgetDenied):reserve(env,'session-denied',30,'session-A')
    reserve(env,'two',40,'session-B')
    with pytest.raises(BudgetDenied):reserve(env,'daily-denied',1,'session-C')


def test_zero_cap_is_not_unlimited_and_none_is_explicitly_unlimited(env):
    l,p=env;zero=l.set_policy('agent',daily_microusd=0,session_microusd=None)
    with pytest.raises(BudgetDenied):reserve((l,zero),'paid',1,'')
    assert reserve((l,zero),'free',0,'')['state']=='reserved'
    unlimited=l.set_policy('agent',daily_microusd=None,session_microusd=None)
    assert reserve((l,unlimited),'uncapped',1000,'')['state']=='reserved'


@pytest.mark.parametrize('amount',[True,-1,1.5,float('nan'),'10',10**12+1])
def test_money_is_bounded_integer_microusd(env,amount):
    with pytest.raises(ValueError):reserve(env,amount=amount)


def test_no_policy_or_missing_required_session_fails_closed(env):
    l,p=env
    with pytest.raises(BudgetDenied):l.reserve('missing','other',session_id='session',estimate_microusd=1,expected_policy_digest=p['digest'])
    with pytest.raises(BudgetDenied):reserve(env,session='')


def test_budget_state_history_is_retained_and_reconciliation_is_idempotent(env):
    l,p=env;reserve(env);l.mark_dispatched('request');l.mark_unknown('request');l.mark_unknown('request');l.reconcile('request',actual_microusd=45);l.reconcile('request',actual_microusd=45)
    history=BudgetLedger(l.path,clock=l.clock).history('request')
    assert [e['state'] for e in history]==['reserved','dispatched','unknown','reconciled']
    assert history[0]['data']['estimate_microusd']==60 and history[-1]['data']['actual_microusd']==45


def test_unknown_before_dispatch_cannot_attribute_an_authorized_charge(env):
    l,p=env;reserve(env);l.mark_unknown('request')
    with pytest.raises(BudgetDenied):l.reconcile('request',actual_microusd=50)
    assert l.get('request')['state']=='unknown' and l.get('request')['actual'] is None
    with pytest.raises(BudgetDenied):reserve(env,'next',30)


def test_rollover_after_reserve_day_sample_cannot_authorize_dispatch(env):
    l,p=env
    old=l.clock();calls=[]
    def rollover():
        calls.append(1)
        return old if len(calls)==1 else old+86400
    l.clock=rollover
    row=reserve(env)
    assert row['day']==int(old//86400)
    with pytest.raises(BudgetDenied):l.mark_dispatched('request')
    assert [e['state'] for e in l.history('request')]==['reserved']
