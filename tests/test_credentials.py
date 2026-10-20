import json
import subprocess
from types import SimpleNamespace
import pytest
from swarmtracker_node_ui.credentials import CredentialStore,CredentialUnavailable

PROFILE='11111111-1111-4111-8111-111111111111'

class NativeDouble:
    def __init__(self):self.items={};self.calls=[]
    def __call__(self,args,**kwargs):
        assert args==['/trusted/helper']
        assert kwargs['stderr']==subprocess.DEVNULL and kwargs['timeout']==10
        assert 'env' not in kwargs
        q=json.loads(kwargs['input']);self.calls.append(q);key=(q['profile'],q['reference'])
        if q['operation']=='create':self.items[key]=q['value'];response={'ok':True}
        elif q['operation']=='read':response={'ok':True,'value':self.items[key]} if key in self.items else {'ok':False}
        else:self.items.pop(key,None);response={'ok':True}
        return SimpleNamespace(returncode=0,stdout=json.dumps(response).encode())


def test_secret_only_in_pipe_and_bound_to_profile_provider_origin():
    native=NativeDouble();store=CredentialStore('/trusted/helper',PROFILE,runner=native)
    ref=store.create(provider='openai',endpoint='https://api.openai.com',secret='synthetic-token')
    assert store.read(ref,provider='openai',endpoint='https://api.openai.com/')=='synthetic-token'
    for provider,endpoint in [('anthropic','https://api.openai.com'),('openai','https://other.example')]:
        with pytest.raises(CredentialUnavailable):store.read(ref,provider=provider,endpoint=endpoint)
    other=CredentialStore('/trusted/helper','22222222-2222-4222-8222-222222222222',runner=native)
    with pytest.raises(CredentialUnavailable):other.read(ref,provider='openai',endpoint='https://api.openai.com')
    store.delete(ref)
    with pytest.raises(CredentialUnavailable):store.read(ref,provider='openai',endpoint='https://api.openai.com')


@pytest.mark.parametrize('endpoint',['http://api.openai.com','https://key@api.openai.com','https://api.openai.com?key=x','https://api.openai.com/path','https://api.openai.com:8443'])
def test_bad_binding_never_reaches_native(endpoint):
    native=NativeDouble();store=CredentialStore('/trusted/helper',PROFILE,runner=native)
    with pytest.raises(ValueError):store.create(provider='openai',endpoint=endpoint,secret='synthetic')
    assert not native.calls


@pytest.mark.parametrize('failure',[b'not json',b'{"ok":false,"error":"synthetic-secret"}',b'[]'])
def test_failure_messages_do_not_echo_native_output(failure):
    store=CredentialStore('/trusted/helper',PROFILE,runner=lambda *a,**k:SimpleNamespace(returncode=0,stdout=failure))
    with pytest.raises(CredentialUnavailable) as error:store.create(provider='openai',endpoint='https://api.openai.com',secret='synthetic-secret')
    assert str(error.value)=='Credential storage unavailable'


def test_timeout_does_not_echo_secret_or_retry():
    calls=[]
    def timeout(*a,**k):
        calls.append(1);raise subprocess.TimeoutExpired(a,10,output=b'synthetic-secret')
    store=CredentialStore('/trusted/helper',PROFILE,runner=timeout)
    with pytest.raises(CredentialUnavailable) as error:store.create(provider='openai',endpoint='https://api.openai.com',secret='synthetic-secret')
    assert len(calls)==1 and 'synthetic' not in str(error.value)
