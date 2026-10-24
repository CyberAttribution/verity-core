import pytest
from fastapi.testclient import TestClient


def environment(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui,store,decision_routes,customer_routes
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    form=dict(operator_token=decision_routes.TOKEN,connection_id='guided-model',endpoint='http://127.0.0.1:8090',model='fixture-model',revision='declared-revision')
    return client,customer_routes.database(),form


def test_guided_local_connection_continues_to_preselected_agent_without_readiness_claim(tmp_path,monkeypatch):
    client,cfg,form=environment(tmp_path,monkeypatch)
    cfg.save('resource','report',dict(description='Synthetic resource',provenance='operator-entered',status='unknown'))
    assert 'Connect an existing local model' in client.get('/models/connect').text
    response=client.post('/models/connect',data=form,follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/agents/add?connection_id=guided-model'
    page=client.get(response.headers['location']);assert page.status_code==200
    assert 'value="guided-model" selected' in page.text
    saved=cfg.latest('connection','guided-model');assert saved['body']['provider']=='openclaw' and saved['body']['scope']=='local'
    assert not cfg.observations()
    assert client.post('/models/connect',data=form).status_code==422
    assert cfg.latest('connection','guided-model')['version']==1


@pytest.mark.parametrize('endpoint',['https://api.example.invalid','http://user:password@127.0.0.1:8090','http://127.0.0.1:8090/?key=secret'])
def test_guided_connection_refuses_external_and_embedded_credentials(tmp_path,monkeypatch,endpoint):
    client,cfg,form=environment(tmp_path,monkeypatch)
    assert client.post('/models/connect',data=dict(form,endpoint=endpoint)).status_code==422
    assert cfg.latest('connection','guided-model') is None


def test_guided_connection_csrf_peer_and_injected_provider_are_refused(tmp_path,monkeypatch):
    client,cfg,form=environment(tmp_path,monkeypatch)
    assert client.post('/models/connect',data={k:v for k,v in form.items() if k!='operator_token'}).status_code==403
    assert client.post('/models/connect',data=form,headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.post('/models/connect',data=dict(form,provider='external')).status_code==422
    from swarmtracker_node_ui import app as ui
    remote=TestClient(ui.app,base_url='http://127.0.0.1',client=('203.0.113.2',2222))
    assert remote.get('/models/connect').status_code==403 and remote.post('/models/connect',data=form).status_code==403
    assert cfg.latest('connection','guided-model') is None
