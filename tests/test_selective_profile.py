import copy
from test_evidence_profile import make_profile
from test_research_journey import setup
from verity_core.trust import selective_profile as reduced


def test_child_integrity_does_not_claim_withheld_parent_proof(setup):
    parent,*_=make_profile(setup);child=reduced.create(parent)
    result=reduced.verify(child,child['checkpoint'])
    assert result['child_integrity'] and result['correspondence'] is None
    assert result['parent_check']=='not supplied'
    assert reduced.verify(child,child['checkpoint'],parent=parent)['correspondence'] is True
    payload=child['record']['entries'][0]['payload']
    assert 'request' not in payload and 'decision' not in payload
    assert payload['decision_qualification']=='Qualified lead only; exposure unknown. No execution authority.'
    assert payload['mapping']['source_dependence']==parent['record']['entries'][0]['payload']['mapping']['source_dependence']


def test_retained_anchor_refuses_mutation_and_wrong_parent(setup):
    parent,*_=make_profile(setup);child=reduced.create(parent)
    damaged=copy.deepcopy(child);damaged['record']['entries'][0]['payload']['decision_qualification']='Unqualified claim'
    assert not reduced.verify(damaged,child['checkpoint'])['child_integrity']
    wrong=copy.deepcopy(parent);wrong['checkpoint']['head_hash']='0'*64
    result=reduced.verify(child,child['checkpoint'],parent=wrong)
    assert result['child_integrity'] and result['correspondence'] is False


def test_reviewed_route_export_restart_and_stale_parent(setup):
    from test_evidence_profile import accepted,denial
    from swarmtracker_node_ui.company_work import CompanyWorkStore
    cfg,j,calls,c,form=accepted(setup)
    denied=denial(cfg,j)
    work=j.work.get('article-1')
    correction=dict(operator_token=form['operator_token'],text='Correction retains uncertainty.',kind='correction',expected_binding=work['proposal_binding'],expected_head='')
    assert c.post('/company-work/article-1/correction',data=correction,follow_redirects=False).status_code==303
    assert c.post('/company-work/article-1/profile',data=dict(operator_token=form['operator_token'],denied_request_id=denied['record']['entries'][0]['payload']['run_id']),follow_redirects=False).status_code==303
    parent=j.work.profiles('article-1')[0];head=parent['checkpoint']['head_hash']
    preview=c.get('/company-work/article-1/profile/'+head+'/reduced')
    assert preview.status_code==200 and 'Withheld' in preview.text
    data=dict(operator_token=form['operator_token'],parent_head=head,review_ack='off')
    assert c.post('/company-work/article-1/reduced',data=data).status_code==422
    data['review_ack']='on'
    assert c.post('/company-work/article-1/reduced',data=data,follow_redirects=False).status_code==303
    child=j.work.disclosures('article-1')[0]
    assert c.post('/company-work/article-1/reduced',data=data,follow_redirects=False).status_code==303
    assert CompanyWorkStore(j.path).disclosures('article-1')==[child]
    assert c.get('/company-work/article-1/reduced/'+child['checkpoint']['head_hash']+'/bundle.json').json()==child
    correction['expected_head']=j.work.corrections('article-1')[-1]['checkpoint']['head_hash'];correction['text']='Later correction'
    assert c.post('/company-work/article-1/correction',data=correction,follow_redirects=False).status_code==303
    assert c.post('/company-work/article-1/reduced',data=data).status_code==409
    assert j.work.disclosures('article-1')==[child] and len(calls)==1
