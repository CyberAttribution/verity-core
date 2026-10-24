"""Managed authenticated forward using existing OS SSH login and pinned trust.

No key provisioning, authorized_keys edits, password prompts, proxy commands or
remote shell. SSH authenticates the server key/login; machine/profile claims
still require their own verification. Unowned listeners/PIDs are never adopted.
"""
import errno,hashlib,os,re,socket,subprocess,tempfile,time,threading
from pathlib import Path
from urllib.parse import urlsplit


def validate_transport(value,endpoint):
    required={'kind','host','login','ssh_port','remote_port','local_port','known_hosts_path','known_hosts_sha256','identity_file'}
    if not isinstance(value,dict) or set(value)!=required or value['kind']!='ssh_local_forward':raise ValueError('Exact SSH transport fields required')
    for field in ('host','login'):
        if not isinstance(value[field],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}',value[field]):raise ValueError('Invalid SSH host/login')
    for field in ('ssh_port','remote_port','local_port'):
        if type(value[field]) is not int or not 1024<=value[field]<=65535:
            if field!='ssh_port' or value[field]!=22:raise ValueError('Invalid SSH/forward port')
    for field in ('known_hosts_path','identity_file'):
        p=value[field]
        if field=='identity_file' and p=='':continue
        if not isinstance(p,str) or not Path(p).is_absolute() or any(c in p for c in '\x00\r\n') or len(p)>1000:raise ValueError('Absolute existing SSH file reference required')
    if not isinstance(value['known_hosts_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['known_hosts_sha256']):raise ValueError('Pinned known_hosts file digest required')
    origin=urlsplit(endpoint)
    if origin.scheme!='http' or origin.hostname!='127.0.0.1' or origin.port!=value['local_port'] or origin.path not in ('','/') or origin.query or origin.fragment or origin.username:raise ValueError('Connection must use the exact loopback forward')
    return dict(value)


class TunnelError(ValueError):pass


class ManagedSSHTunnel:
    def __init__(self,configuration,connection_id):
        self.configuration=configuration;self.connection_id=connection_id;self.process=None;self.stage=None;self.error_file=None;self.binding=None
        self.cancelled=threading.Event();self.lock=threading.RLock()

    def _trust(self,transport):
        path=Path(transport['known_hosts_path']);info=path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_mode & 0o022 or info.st_size>65536:raise TunnelError('Pinned known_hosts must be a bounded non-writable regular file')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=transport['known_hosts_sha256']:raise TunnelError('Pinned SSH server trust changed; explicit review required')
        key=transport['identity_file']
        if key and (not Path(key).is_file() or Path(key).is_symlink()):raise TunnelError('Existing SSH identity file unavailable')

    def start(self,expected_digest,*,timeout=15):
        if not 0<timeout<=30:raise ValueError('Bounded SSH handshake required')
        if self.process is not None:raise TunnelError('Managed tunnel already exists; stop it before restart')
        entry=self.configuration.latest('connection',self.connection_id)
        if not entry or entry['digest']!=expected_digest or self.configuration.connection_disabled(self.connection_id):raise TunnelError('Connection changed or disabled')
        t=validate_transport(entry['body'].get('transport'),entry['body']['endpoint']);self._trust(t)
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:probe.bind(('127.0.0.1',t['local_port']))
            except OSError as exc:
                if exc.errno==errno.EADDRINUSE:
                    raise TunnelError('Local port already occupied; existing listener is not adopted') from None
                raise TunnelError('Local listener permission or socket unavailable; use permitted access') from None
        self.stage=tempfile.TemporaryDirectory(prefix='verity-ssh-');os.chmod(self.stage.name,0o700)
        self.control=str(Path(self.stage.name)/'control');self.error_file=open(Path(self.stage.name)/'stderr.log','w+b')
        self.binding=entry;self.transport=t
        argv=['/usr/bin/ssh','-F','/dev/null','-N','-T','-p',str(t['ssh_port']),'-S',self.control,
              '-L',f"127.0.0.1:{t['local_port']}:127.0.0.1:{t['remote_port']}"]
        options={'BatchMode':'yes','StrictHostKeyChecking':'yes','UserKnownHostsFile':t['known_hosts_path'],'GlobalKnownHostsFile':'/dev/null','UpdateHostKeys':'no','ForwardAgent':'no','ForwardX11':'no','PermitLocalCommand':'no','ProxyCommand':'none','ProxyJump':'none','CanonicalizeHostname':'no','GatewayPorts':'no','ExitOnForwardFailure':'yes','IdentitiesOnly':'yes','ConnectTimeout':'5','ServerAliveInterval':'10','ServerAliveCountMax':'2','ControlMaster':'yes','ControlPersist':'no','PasswordAuthentication':'no','KbdInteractiveAuthentication':'no'}
        for k,v in options.items():argv.extend(['-o',k+'='+v])
        if t['identity_file']:argv.extend(['-i',t['identity_file']])
        argv.append(t['login']+'@'+t['host'])
        try:
            with self.lock:
                if self.cancelled.is_set():raise TunnelError('SSH start cancelled before launch')
                self.process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=self.error_file)
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                process=self.process
                if self.cancelled.is_set() or process is None:raise TunnelError('SSH start cancelled by operator')
                if process.poll() is not None:raise TunnelError('SSH authentication or forward failed; existing trust/keys preserved')
                if self.healthy():
                    self.configuration.observe('connection',self.connection_id,'unknown','Authenticated SSH forward established; model execution and installation identity unconfirmed',expected_digest=entry['digest'],expected_version=entry['version'])
                    return self
                time.sleep(.1)
            raise TunnelError('SSH handshake exceeded its finite deadline')
        except BaseException:
            self.stop();raise

    def healthy(self):
        process=self.process
        if process is None or process.poll() is not None:return False
        entry=self.configuration.latest('connection',self.connection_id)
        if not entry or entry['digest']!=self.binding['digest'] or self.configuration.connection_disabled(self.connection_id):return False
        try:self._trust(self.transport)
        except (OSError,ValueError):return False
        result=subprocess.run(['/usr/bin/ssh','-F','/dev/null','-S',self.control,'-O','check',self.transport['login']+'@'+self.transport['host']],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1)
        return result.returncode==0 and ('pid='+str(process.pid)+')').encode() in result.stderr

    def stop(self):
        with self.lock:
            self.cancelled.set();process=self.process;self.process=None
            error_file=self.error_file;self.error_file=None
            stage=self.stage;self.stage=None
        if process:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        if error_file:error_file.close()
        if stage:stage.cleanup()

    def observation(self):
        return dict(state='authenticated forward' if self.healthy() else 'unknown',connection_id=self.connection_id,
                    configuration_digest=self.binding['digest'] if self.binding else None,
                    meaning='SSH server-key and account authentication only; model and installation identity require separate checks')


MANAGED={}
REGISTRY_LOCK=threading.RLock()


def start_connection(configuration,identifier,expected_digest):
    with REGISTRY_LOCK:
        if identifier in MANAGED:raise TunnelError('Tunnel already managed; stop before restart')
        tunnel=ManagedSSHTunnel(configuration,identifier);MANAGED[identifier]=tunnel
    try:tunnel.start(expected_digest)
    except BaseException:
        with REGISTRY_LOCK:
            if MANAGED.get(identifier) is tunnel:MANAGED.pop(identifier)
        raise
    return tunnel


def require_connection(identifier,digest):
    tunnel=MANAGED.get(identifier)
    if not tunnel or not tunnel.binding or tunnel.binding['digest']!=digest or not tunnel.healthy():
        raise TunnelError('Authenticated transport unavailable or changed; explicitly start/recheck this connection')
    return tunnel


def stop_connection(identifier):
    with REGISTRY_LOCK:tunnel=MANAGED.pop(identifier,None)
    if tunnel:tunnel.stop()


def _close_owned():
    for identifier in list(MANAGED):stop_connection(identifier)


import atexit
atexit.register(_close_owned)
