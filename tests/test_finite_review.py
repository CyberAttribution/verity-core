import copy
import hashlib
import io
import json

import pytest
from apps.node.swarmtracker_node_ui import finite_review as review
from apps.node.swarmtracker_node_ui.session_coordinator import Coordinator


def manifest():
    source='Read-only fixture source packet.'
    return dict(id='BatchFixture',session_id='FixtureSession',authorization='explicit-finite-qwen-review',
        model=review.MODEL,revision=review.REVISION,max_tokens=100,max_seconds=300,
        tasks=[dict(id='Review'+str(i),purpose='Review the supplied fixture.',source=source,
            sha256=hashlib.sha256(source.encode()).hexdigest(),acceptance='Concrete finding or no supported finding.') for i in range(3)])


class Opener:
    def __init__(self,fail=False,model=review.MODEL,finish='stop'):
        self.calls=[];self.fail=fail;self.model=model;self.finish=finish
    def open(self,request,timeout):
        self.calls.append((request,timeout))
        if self.fail:raise TimeoutError('uncertain submission')
        return io.BytesIO(json.dumps(dict(id='remote-fixture',model=self.model,
            choices=[dict(finish_reason=self.finish,message=dict(content='No supported finding.'))],
            usage=dict(prompt_tokens=10,completion_tokens=4))).encode())


def test_finite_reviews_advance_automatically_without_acceptance_or_replay(tmp_path):
    c=Coordinator(tmp_path/'queue.db');opener=Opener();out=tmp_path/'output'
    results=review.run_batch(manifest(),c,out,opener=opener)
    assert len(opener.calls)==3
    assert all(r['state']=='awaiting_review' for r in results)
    assert all(r[0].full_url==review.ENDPOINT for r in opener.calls)
    assert all(json.loads(r[0].data)['max_tokens']==100 for r in opener.calls)
    assert all('reasoning_effort' not in json.loads(r[0].data) for r in opener.calls)
    assert len({r['request_id'] for r in results})==3
    snapshot=c.snapshot()
    assert snapshot['control'] is None  # No invented rental ceiling.
    assert [w['state'] for w in snapshot['work_items']]==['awaiting_review']*3
    assert json.loads((out/'status.json').read_text())['automatic_acceptance'] is False
    assert len(list(out.glob('*.raw.json')))==3
    with pytest.raises(ValueError,match='replayed'):review.run_batch(manifest(),c,tmp_path/'retry',opener=opener)
    assert len(opener.calls)==3


@pytest.mark.parametrize('failure',['timeout','wrong_model'])
def test_uncertain_or_wrong_model_stops_remaining_batch_without_retry(tmp_path,failure):
    c=Coordinator(tmp_path/'queue.db');opener=Opener(fail=failure=='timeout',model='wrong' if failure=='wrong_model' else review.MODEL)
    results=review.run_batch(manifest(),c,tmp_path/'out',opener=opener)
    assert len(opener.calls)==1
    assert [r['state'] for r in results]==['unknown','paused','paused']
    assert results[0]['retry'] is False


def test_incomplete_result_is_failed_and_never_accepted(tmp_path):
    c=Coordinator(tmp_path/'queue.db');opener=Opener(finish='length')
    results=review.run_batch(manifest(),c,tmp_path/'out',opener=opener)
    assert [r['state'] for r in results]==['failed']*3
    assert len(opener.calls)==3


@pytest.mark.parametrize('field',['source','model','revision','authorization','count','tokens','duplicate'])
def test_unapproved_packets_fail_before_any_queued_work_or_network(tmp_path,field):
    m=copy.deepcopy(manifest())
    if field=='source':m['tasks'][0]['source']='Changed after hashing'
    elif field=='count':m['tasks']=m['tasks']*3
    elif field=='tokens':m['max_tokens']=1025
    elif field=='duplicate':m['tasks'][1]['id']=m['tasks'][0]['id']
    else:m[field]='wrong'
    c=Coordinator(tmp_path/'queue.db');opener=Opener()
    with pytest.raises(ValueError):review.run_batch(m,c,tmp_path/'out',opener=opener)
    assert not opener.calls and not c.snapshot()['work_items']


def test_expired_dispatch_deadline_pauses_without_network(tmp_path):
    c=Coordinator(tmp_path/'queue.db');opener=Opener();ticks=iter([0,301,302,303])
    results=review.run_batch(manifest(),c,tmp_path/'out',opener=opener,clock=lambda:next(ticks))
    assert not opener.calls and [r['state'] for r in results]==['paused']*3


def test_glm_profile_preserves_explicit_low_in_both_request_locations(tmp_path):
    m=manifest();m.update(model=review.GLM_MODEL,revision=review.GLM_REVISION,authorization='explicit-finite-glm-review',max_tokens=2048)
    c=Coordinator(tmp_path/'queue.db');opener=Opener(model=review.GLM_MODEL)
    results=review.run_batch(m,c,tmp_path/'out',opener=opener)
    assert [r['state'] for r in results]==['awaiting_review']*3
    for request,timeout in opener.calls:
        payload=json.loads(request.data)
        assert payload['model']==review.GLM_MODEL
        assert payload['max_tokens']==2048
        assert payload['reasoning_effort']=='low'
        assert payload['chat_template_kwargs']=={'reasoning_effort':'low'}
    assert all(w['spec'].find('glm-review')>=0 for w in c.snapshot()['work_items'])
    m['authorization']='explicit-finite-qwen-review'
    with pytest.raises(ValueError):review.validate(m)


def test_unknown_profile_with_null_authorization_is_not_an_allowlist_match():
    m=manifest();m.update(model='unknown',revision='unknown',authorization=None)
    with pytest.raises(ValueError):review.validate(m)
