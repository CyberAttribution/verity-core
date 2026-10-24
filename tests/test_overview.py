import time
from swarmtracker_node_ui.overview import workflow, store_inventory, context_inventory
from test_research_journey import setup


def test_overview_separates_optional_connection_and_execution_evidence(setup):
    cfg,j,calls,c,form=setup
    cfg.save('connection','optional',dict(cfg.latest('connection','local')['body']))
    cfg.observe('connection','optional','unavailable','Optional service is not running')
    current=cfg.latest('agent','researcher')['body']['connection_id']
    cfg.observe('connection',current,'reachable','Health only')
    first=workflow(cfg,j.runs,'researcher','research')
    assert first['state']=='execution unconfirmed'
    assert c.post('/company-work/research',data=form,follow_redirects=False).status_code==303
    ready=workflow(cfg,j.runs,'researcher','research')
    assert ready['state']=='observed available' and ready['request_id']
    assert next(x for x in ready['connections'] if x['id']=='optional')['state']=='unavailable'
    with cfg._db() as db:db.execute('UPDATE observations SET at=?',(time.time()-301,))
    stale=workflow(cfg,j.runs,'researcher','research')
    assert stale['state']=='stale' and 'unknown' in stale['detail']
    assert all(x['state']=='stale' for x in stale['connections'])


def test_configured_names_or_prior_configuration_do_not_prove_readiness(setup):
    cfg,j,calls,c,form=setup
    current=cfg.latest('agent','researcher')['body']['connection_id']
    assert workflow(cfg,j.runs,'researcher','research')['state']=='unknown'
    cfg.observe('connection',current,'reachable','Health only')
    conn=cfg.latest('connection',current)['body'];conn['model']='different-model';cfg.save('connection',current,conn)
    assert workflow(cfg,j.runs,'researcher','research')['state']=='unknown'
    agent=cfg.latest('agent','researcher');cfg.set_routing('researcher',False,agent['digest'])
    assert workflow(cfg,j.runs,'researcher','research')['state']=='paused'
    assert workflow(cfg,j.runs,'missing','research')['state']=='unavailable'


def test_missing_profile_stores_are_not_initialized(tmp_path):
    p=tmp_path/'missing.db'
    assert store_inventory(p)['state']=='not created'
    assert context_inventory(p)['profiles']==0 and not p.exists()
    p.write_bytes(b'broken database')
    assert store_inventory(p)['state']=='unknown'


def test_native_overview_scope_branding_and_no_cache(setup,monkeypatch):
    cfg,j,calls,c,form=setup
    from swarmtracker_node_ui import app as app_module
    monkeypatch.setattr(app_module,'node_status',lambda:dict(runtime_online=False,model_online=False))
    page=c.get('/?agent=researcher&resource=research')
    assert page.status_code==200 and page.headers['cache-control']=='no-store'
    for text in ('Verity by SwarmTracker','Overview','Application health','Selected workflow','Individual connection observations','Optional analysis runtime setup','not governed requests or company decisions','Organization context in this profile'):
        assert text in page.text
    assert 'Not connected yet' not in page.text and 'Node Dashboard' not in page.text
