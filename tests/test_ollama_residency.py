import pytest
from swarmtracker_node_ui.customer_config import CustomerConfig
from swarmtracker_node_ui.local_inference import LocalInference


def test_connection_specific_validation_and_exact_dispatch_value(tmp_path,monkeypatch):
    cfg=CustomerConfig(tmp_path/'config.db')
    base=dict(provider='ollama_local',endpoint='http://127.0.0.1:11434',model='m',revision='abc',scope='local',credential_ref='none')
    for bad in [True,-1,601,1.5,'0']:
        with pytest.raises(ValueError):cfg.save('connection','invalid',dict(base,runtime_options={'keep_alive_seconds':bad}))
    with pytest.raises(ValueError):cfg.save('connection','invalid',dict(base,provider='openclaw',runtime_options={'keep_alive_seconds':0}))
    sent=[]
    def request(endpoint,method,path,payload=None,**kwargs):
        if path=='/api/show':return {'model_info':{'x':1},'details':{'format':'gguf'}}
        if path=='/api/tags':return {'models':[{'name':'m','digest':'abc'}]}
        sent.append(payload);return {'model':'m','done':True,'response':'ok'}
    monkeypatch.setattr('swarmtracker_node_ui.bounded_loopback.request_json',request)
    for options,wanted in [({},300),({'runtime_options':{'keep_alive_seconds':0}},0)]:
        entry=cfg.save('connection','local',dict(base,**options))
        LocalInference._ollama_dispatch(dict(entry['body'],model_revision='abc',instructions=''), 'specimen')
        assert sent[-1]['keep_alive']==wanted
