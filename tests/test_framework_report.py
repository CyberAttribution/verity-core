import copy,uuid
import pytest
from test_research_journey import setup
from swarmtracker_node_ui.framework_report import ReportJourney
from swarmtracker_node_ui import report_actions as ra


@pytest.fixture
def report(setup,tmp_path):
    cfg,j,calls,c,form=setup
    cfg.save('resource',ra.TARGET,dict(description='Disposable report',provenance='operator-entered',status='unknown'))
    a=cfg.latest('agent','researcher')['body'];a['resources']=[ra.TARGET];a['allowed_tools']=['draft_text'];cfg.save('agent','researcher',a)
    actions=ra.Store(tmp_path/'reports.db',tmp_path/'output',cfg)
    journey=ReportJourney(j.runs,actions)
    call=dict(agent_id='researcher',task_id='report-1',request_id=str(uuid.uuid4()),session_id='conformance',prompt='Harmless report specimen',configuration=cfg.effective('researcher',ra.TARGET),source_name='disposable report')
    return cfg,j.runs,calls,actions,journey,call


def test_exact_recovery_approval_and_independent_file(report):
    cfg,runs,calls,actions,j,call=report
    proposed=j.propose(**call);original=actions.get(proposed['proposal_id'])
    assert j.propose(**call,recover=True)==proposed and len(calls)==1
    assert actions.get(proposed['proposal_id'])==original
    approved=actions.decide(original['id'],'approved','Harmless disposable report only','local operator',original['head_hash'])
    written=actions.execute(approved['id'],'broker',approved['head_hash'])
    assert written['state']=='written' and written['attempts']==1
    assert actions.observed_file()['sha256']==original['manifest']['action']['sha256']
    assert (actions.output_dir/ra.TARGET).read_text()==runs.get(call['request_id'])['result']['response']
    with pytest.raises(ra.Conflict):actions.execute(written['id'],'broker',written['head_hash'])
    assert len(calls)==1


@pytest.mark.parametrize('field,value',[('agent_id','foreign'),('task_id','foreign'),('session_id','foreign'),('prompt','Changed specimen')])
def test_wrong_recovery_never_dispatches(report,field,value):
    cfg,runs,calls,actions,j,call=report;j.propose(**call)
    changed=dict(call);changed[field]=value
    with pytest.raises(ValueError):j.propose(**changed,recover=True)
    assert len(calls)==1 and len(actions.recent())==1


def test_denied_request_no_proposal_and_no_call(report):
    cfg,runs,calls,actions,j,call=report
    a=cfg.latest('agent','researcher');cfg.set_routing('researcher',False,a['digest'])
    call['configuration']=cfg.effective('researcher',ra.TARGET)
    assert j.propose(**call)['state']=='denied'
    assert calls==[] and actions.recent()==[] and not actions.observed_file()['present']


def test_ambiguous_proposals_and_modified_response_refused(report):
    cfg,runs,calls,actions,j,call=report;out=j.propose(**call);m=actions.get(out['proposal_id'])['manifest']
    changed=copy.deepcopy(m);changed['proposal']='Different text';changed['action']['sha256']=ra.digest(b'Different text')
    with pytest.raises(ValueError):actions.create_bound(changed,runs)
    actions.create(m)
    with pytest.raises(ra.Conflict,match='Ambiguous'):j.propose(**call,recover=True)
    assert len(calls)==1


def test_uncertain_request_never_recovers_or_redispatches(report):
    cfg,runs,calls,actions,j,call=report
    def lost(*args,**kwargs):raise KeyboardInterrupt('simulated process interruption')
    original=runs._dispatch;runs._dispatch=lost
    with pytest.raises(KeyboardInterrupt):j.propose(**call)
    runs._dispatch=original
    with pytest.raises(ValueError,match='uncertain'):j.propose(**call,recover=True)
    assert runs.get(call['request_id'])['state']=='reserved' and actions.recent()==[] and calls==[]
