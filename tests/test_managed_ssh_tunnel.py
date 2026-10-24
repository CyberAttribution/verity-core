import copy,hashlib,socket
import pytest
from swarmtracker_node_ui.customer_config import CustomerConfig
from swarmtracker_node_ui.managed_ssh_tunnel import ManagedSSHTunnel,TunnelError,validate_transport,require_connection


def fixture(tmp_path):
    known=tmp_path/'known_hosts';known.write_text('Pinned public SSH trust fixture\n')
    t=dict(kind='ssh_local_forward',host='192.168.1.203',login='swarmtracker',ssh_port=22,remote_port=11434,local_port=19434,known_hosts_path=str(known),known_hosts_sha256=hashlib.sha256(known.read_bytes()).hexdigest(),identity_file='')
    cfg=CustomerConfig(tmp_path/'config.db');entry=cfg.save('connection','mini',dict(provider='ollama_local',endpoint='http://127.0.0.1:19434',model='qwen2.5:7b-instruct',revision='',scope='local',credential_ref='none',transport=t))
    return cfg,entry,t,known


def test_version_trust_and_unowned_listener_refusal(tmp_path):
    cfg,entry,t,known=fixture(tmp_path);manager=ManagedSSHTunnel(cfg,'mini')
    with pytest.raises(TunnelError):manager.start('0'*64)
    known.write_text('Changed trust')
    with pytest.raises(TunnelError,match='trust changed'):manager.start(entry['digest'])
    with pytest.raises(TunnelError):require_connection('mini',entry['digest'])
    body=dict(entry['body']);body.pop('transport')
    with pytest.raises(ValueError,match='silently removed'):cfg.save('connection','mini',body)


@pytest.mark.parametrize('key,value',[('host','-oProxyCommand=bad'),('login','user@other'),('local_port',80),('known_hosts_sha256','bad'),('kind','reverse_shell')])
def test_unsafe_transport_rejected(tmp_path,key,value):
    cfg,entry,t,known=fixture(tmp_path);t[key]=value
    with pytest.raises(ValueError):validate_transport(t,entry['body']['endpoint'])


def test_finite_handshake_kills_only_owned_process_and_forces_loopback(tmp_path,monkeypatch):
    cfg,entry,t,known=fixture(tmp_path);manager=ManagedSSHTunnel(cfg,'mini');seen=[]
    class Process:
        pid=12345
        def poll(self):return None
        def terminate(self):seen.append('terminate-owned')
        def wait(self,timeout):return 0
    def spawn(argv,**kwargs):seen.append(argv);return Process()
    monkeypatch.setattr('swarmtracker_node_ui.managed_ssh_tunnel.subprocess.Popen',spawn)
    monkeypatch.setattr(manager,'healthy',lambda:False)
    with pytest.raises(TunnelError,match='finite deadline'):manager.start(entry['digest'],timeout=.05)
    argv=seen[0]
    assert '127.0.0.1:19434:127.0.0.1:11434' in argv and 'StrictHostKeyChecking=yes' in argv
    assert argv[1:3]==['-F','/dev/null'] and 'ForwardAgent=no' in argv and 'PermitLocalCommand=no' in argv
    assert seen[-1]=='terminate-owned' and manager.process is None
