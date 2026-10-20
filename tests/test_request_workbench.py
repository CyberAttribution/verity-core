import copy
import hashlib
import json
from html.parser import HTMLParser
import pytest
from fastapi.testclient import TestClient
from verity_core.trust import inference_receipt


class RequestForm(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.values={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form':self.active=a.get('action')=='/workbench/requests'
        if self.active and tag=='input' and 'name' in a:self.values[a['name']]=a.get('value','')
    def handle_endtag(self,tag):
        if tag=='form':self.active=False


def environment(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui,store,customer_routes,local_inference,workbench_routes
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    cfg=customer_routes.database()
    cfg.save('resource','report',dict(description='Synthetic disposable report',provenance='operator-entered',status='unknown'))
    cfg.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:8090',model='fixture-model',revision='fixture-revision',scope='local',credential_ref='none'))
    cfg.save('agent','fixture-agent',dict(connection_id='local',instructions='PRIVATE_OPERATOR_INSTRUCTION',allowed_tools=['generate_text'],resources=['report'],external_sharing='none',fallback_id='',require_fresh_connection_observation=True))
    cfg.observe('connection','local','reachable','Synthetic HTTP fixture')
    calls=[]
    def dispatch(c,prompt,purpose):
        calls.append(prompt)
        return dict(model=c['model'],response='<script>unchecked model text</script>',event_id='fixture-request-event',decision='allowed',done=True,done_reason='stop')
    monkeypatch.setattr(local_inference.LocalInference,'_dispatch',staticmethod(dispatch))
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    parser=RequestForm();parser.feed(client.get('/workbench').text)
    form=dict(parser.values,task_id='workbench-request',session_id='',purpose='generate_text',prompt='PRIVATE_SPECIMEN')
    return client,cfg,workbench_routes.runs(),form,calls


def test_workbench_select_submit_restart_export_and_no_duplicate_dispatch(tmp_path,monkeypatch):
    client,cfg,ledger,form,calls=environment(tmp_path,monkeypatch)
    response=client.post('/workbench/requests',data=form,follow_redirects=False)
    assert response.status_code==303 and len(calls)==1
    url=response.headers['location'];page=client.get(url)
    assert page.status_code==200 and 'allowed / completed' in page.text
    assert '&lt;script&gt;' in page.text and '<script>unchecked model text</script>' not in page.text
    assert 'independently unchecked' in page.text
    bundle=client.get(url+'/export').json();anchor=client.get(url+'/checkpoint').json()
    assert inference_receipt.verify(bundle,anchor)
    encoded=json.dumps(bundle)
    assert 'PRIVATE_SPECIMEN' not in encoded and 'PRIVATE_OPERATOR_INSTRUCTION' not in encoded
    assert hashlib.sha256(b'PRIVATE_SPECIMEN').hexdigest() in encoded
    reopened=type(ledger)(ledger.path,cfg)
    run_id=url.rsplit('/',1)[1]
    assert reopened.request_export(run_id)==bundle
    assert client.post('/workbench/requests',data=form).status_code==409 and len(calls)==1
    assert page.headers['cache-control']=='no-store'


def test_stale_reviewed_settings_and_missing_health_deny_without_dispatch(tmp_path,monkeypatch):
    client,cfg,ledger,form,calls=environment(tmp_path,monkeypatch)
    body=cfg.latest('agent','fixture-agent')['body'];cfg.save('agent','fixture-agent',dict(body,instructions='Changed since review'))
    response=client.post('/workbench/requests',data=form,follow_redirects=False)
    assert response.status_code==303 and not calls
    page=client.get(response.headers['location']).text
    assert 'denied / denied' in page and 'Configuration changed since request review' in page
    bundle=client.get(response.headers['location']+'/export').json()
    assert inference_receipt.verify(bundle,bundle['checkpoint'])
    parser=RequestForm();parser.feed(client.get('/workbench').text)
    new=dict(form,**parser.values);new['task_id']='health-denied'
    with cfg._db() as db:db.execute('DELETE FROM observations')
    response=client.post('/workbench/requests',data=new,follow_redirects=False)
    assert response.status_code==303 and not calls
    assert 'Fresh local connection observation required' in client.get(response.headers['location']).text


def test_workbench_refuses_cross_origin_remote_peer_and_injected_fields(tmp_path,monkeypatch):
    client,cfg,ledger,form,calls=environment(tmp_path,monkeypatch)
    assert client.post('/workbench/requests',data={k:v for k,v in form.items() if k!='operator_token'}).status_code==403
    assert client.post('/workbench/requests',data=form,headers={'Origin':'https://evil.invalid'}).status_code==403
    from swarmtracker_node_ui import app as ui
    remote=TestClient(ui.app,base_url='http://127.0.0.1',client=('203.0.113.4',1234))
    assert remote.get('/workbench').status_code==403
    assert remote.post('/workbench/requests',data=form).status_code==403
    assert client.post('/workbench/requests',data=dict(form,dispatch='arbitrary')).status_code==422
    assert not calls and not ledger.recent()


def test_unobserved_crash_stays_reserved_without_receipt_or_redispatch(tmp_path,monkeypatch):
    client,cfg,ledger,form,calls=environment(tmp_path,monkeypatch)
    def crash(*args):raise SystemExit('Synthetic interrupted process')
    with pytest.raises(SystemExit):ledger.run(agent_id='fixture-agent',task_id='crashed-request',resource_id='report',purpose='generate_text',prompt='Synthetic specimen',dispatch=crash)
    run=ledger.recent()[0];assert run['state']=='reserved'
    page=client.get('/workbench/requests/'+run['id'])
    assert page.status_code==200 and 'Outcome unconfirmed' in page.text
    assert client.get('/workbench/requests/'+run['id']+'/export').status_code==404
    assert not calls


def test_receipt_retained_anchor_rejects_tampering_and_rehashed_replacement(tmp_path,monkeypatch):
    client,cfg,ledger,form,calls=environment(tmp_path,monkeypatch)
    response=client.post('/workbench/requests',data=form,follow_redirects=False)
    url=response.headers['location'];bundle=client.get(url+'/export').json();anchor=client.get(url+'/checkpoint').json()
    for field,value in [('task_id','different-task'),('model','different-model'),('state','failed')]:
        changed=copy.deepcopy(bundle);changed['record']['entries'][0]['payload'][field]=value
        assert not inference_receipt.verify(changed,anchor)
    run=ledger.recent()[0];run['result']['response']='Rewritten result';run['result']['response_sha256']=hashlib.sha256(b'Rewritten result').hexdigest()
    replacement=inference_receipt.export(run)
    assert inference_receipt.verify(replacement,replacement['checkpoint'])
    assert not inference_receipt.verify(replacement,anchor)
    bad=copy.deepcopy(bundle);bad['record']['entries'][0]['payload']['configuration']['agent_version']=True
    assert not inference_receipt.verify(bad,anchor)
