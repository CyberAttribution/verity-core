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


def test_actual_unrelated_listener_is_never_adopted(tmp_path):
    cfg,entry,t,known=fixture(tmp_path)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0));listener.listen()
        t['local_port']=listener.getsockname()[1]
        body=dict(entry['body'],endpoint='http://127.0.0.1:'+str(t['local_port']),transport=t)
        entry=cfg.save('connection','mini',body)
        manager=ManagedSSHTunnel(cfg,'mini')
        with pytest.raises(TunnelError,match='occupied'):manager.start(entry['digest'])
        assert listener.fileno()>=0 and manager.process is None


def test_socket_permission_is_reported_separately(tmp_path,monkeypatch):
    import errno
    cfg,entry,t,known=fixture(tmp_path)
    class Refused:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def setsockopt(self,*args):pass
        def bind(self,*args):raise PermissionError(errno.EACCES,'restricted')
    monkeypatch.setattr('swarmtracker_node_ui.managed_ssh_tunnel.socket.socket',Refused)
    with pytest.raises(TunnelError,match='permission'):ManagedSSHTunnel(cfg,'mini').start(entry['digest'])


def test_unconfirmed_owned_exit_retains_handle_and_refuses_dispatch(tmp_path,monkeypatch):
    import subprocess
    from swarmtracker_node_ui import managed_ssh_tunnel as transport
    cfg,entry,t,known=fixture(tmp_path);manager=ManagedSSHTunnel(cfg,'mini')
    class Stuck:
        pid=99999
        def poll(self):return None
        def terminate(self):pass
        def kill(self):pass
        def wait(self,timeout):raise subprocess.TimeoutExpired('owned fixture',timeout)
    manager.process=Stuck();manager.binding=entry;transport.MANAGED['mini']=manager
    try:
        with pytest.raises(TunnelError,match='exit unconfirmed'):transport.stop_connection('mini')
        assert transport.MANAGED['mini'] is manager and manager.process is not None
        assert not manager.healthy()
        with pytest.raises(TunnelError):transport.require_connection('mini',entry['digest'])
    finally:transport.MANAGED.pop('mini',None)


def test_healthy_control_timeout_is_unknown(tmp_path,monkeypatch):
    import subprocess
    cfg,entry,t,known=fixture(tmp_path);manager=ManagedSSHTunnel(cfg,'mini')
    class Alive:
        pid=99999
        def poll(self):return None
    manager.process=Alive();manager.binding=entry;manager.transport=t;manager.control='/private/tmp/unused-control'
    def unavailable(*args,**kwargs):raise subprocess.TimeoutExpired('owned-control',1)
    monkeypatch.setattr('swarmtracker_node_ui.managed_ssh_tunnel.subprocess.run',unavailable)
    assert not manager.healthy() and manager.observation()['state']=='unknown'


def test_failed_start_retains_unconfirmed_owned_child(tmp_path,monkeypatch):
    from swarmtracker_node_ui import managed_ssh_tunnel as transport
    cfg,entry,t,known=fixture(tmp_path)
    def failed(self,*args,**kwargs):
        self.process=object();self.cancelled.set()
        raise TunnelError('Owned SSH exit unconfirmed')
    monkeypatch.setattr(ManagedSSHTunnel,'start',failed)
    try:
        with pytest.raises(TunnelError):transport.start_connection(cfg,'mini',entry['digest'])
        assert transport.MANAGED['mini'].process is not None
        assert transport.MANAGED['mini'].cancelled.is_set()
    finally:transport.MANAGED.pop('mini',None)
