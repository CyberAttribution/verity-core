"""macOS launchd ownership for the existing local Prefect server/process worker.

Install leaves jobs inactive. This does not schedule tasks, change Verity queue
state, retry flows, adjudicate findings, or transfer coordinator ownership.
"""
import argparse,json,os,plistlib,re,socket,subprocess,time,urllib.request
from pathlib import Path

LABELS={n:'ai.swarmtracker.verity.prefect-'+n for n in ('server','worker')}

def specification(root,source,venv,home):
    root,source,venv,home=[Path(p).resolve() for p in (root,source,venv,home)]
    if not (source/'tools/prefect_session.py').is_file() or not (venv/'bin/prefect').is_file():raise ValueError('Existing adapter and pinned environment required')
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True):raise ValueError('Clean source checkpoint required')
    return dict(root=str(root),source=str(source),venv=str(venv),home=str(home),revision=revision,schema='verity_prefect_user_services/v1')

def jobs(c):
    root=Path(c['root']);binary=str(Path(c['venv'])/'bin/prefect')
    env=dict(HOME=c['home'],PATH=str(Path(c['venv'])/'bin')+':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',PREFECT_HOME=str(root/'prefect-home'),PREFECT_API_URL='http://127.0.0.1:4200/api',PREFECT_SERVER_ANALYTICS_ENABLED='false',DO_NOT_TRACK='1',VERITY_PREFECT_DEPLOYMENT='1',PYTHONUNBUFFERED='1')
    if Path('/opt/homebrew/opt/expat/lib').is_dir():env['DYLD_LIBRARY_PATH']='/opt/homebrew/opt/expat/lib'
    args=dict(server=['server','start','--host','127.0.0.1','--port','4200'],worker=['worker','start','--pool','verity-local-process','--type','process','--limit','1'])
    return {n:dict(Label=LABELS[n],ProgramArguments=[binary,*v],EnvironmentVariables=env,WorkingDirectory=c['source'],RunAtLoad=True,KeepAlive={'SuccessfulExit':False},ThrottleInterval=10,ProcessType='Background',Umask=63,StandardOutPath=str(root/'logs'/f'{n}.stdout.log'),StandardErrorPath=str(root/'logs'/f'{n}.stderr.log')) for n,v in args.items()}

def installed(c):
    result={}
    for n,value in jobs(c).items():
        p=Path(c['home'])/'Library/LaunchAgents'/f'{LABELS[n]}.plist'
        if p.exists() and plistlib.loads(p.read_bytes())!=value:raise ValueError('Existing service has another binding; preserve and reconcile it: '+str(p))
        result[n]=(p,value)
    return result

def health():
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:4200/api/health',timeout=2) as r:return r.status==200 and json.loads(r.read(100)) is True
    except (OSError,ValueError):return False

def status(n):
    p=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{LABELS[n]}'],capture_output=True,text=True)
    pid=re.search(r'\bpid = (\d+)',p.stdout);state=re.search(r'\bstate = ([^\n]+)',p.stdout)
    return dict(loaded=p.returncode==0,pid=int(pid[1]) if pid else None,state=state[1] if state else 'not loaded')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['install','status','start','stop']);p.add_argument('--root',required=True);p.add_argument('--source');p.add_argument('--venv');p.add_argument('--db');p.add_argument('--service',choices=['server','worker','all'],default='all');a=p.parse_args()
    root=Path(a.root).resolve();manifest=root/'user-services.json'
    if a.action=='install':
        if not a.source or not a.venv:raise ValueError('Explicit source and venv required')
        c=specification(root,a.source,a.venv,Path.home())
        if manifest.exists() and json.loads(manifest.read_text())!=c:raise ValueError('Installation binding differs; preserve and reconcile it first')
        values=installed(c)
        if any(status(n)["loaded"] for n in LABELS):raise RuntimeError("Stop owned services before installing or rebinding them")
        root.mkdir(parents=True,exist_ok=True);(root/'logs').mkdir(exist_ok=True);(root/'prefect-home').mkdir(exist_ok=True)
        for path,value in values.values():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(plistlib.dumps(value));path.chmod(0o600)
        manifest.write_text(json.dumps(c,indent=2));manifest.chmod(0o600)
        for n in LABELS:subprocess.run(["launchctl","disable",f"gui/{os.getuid()}/{LABELS[n]}"],check=True)
        print(json.dumps(dict(installed=True,started=False,manifest=str(manifest),source=c['revision']),indent=2));return
    c=json.loads(manifest.read_text());values=installed(c);names=list(LABELS) if a.service=='all' else [a.service]
    if a.action=='status':print(json.dumps(dict(host=socket.gethostname(),source=c['revision'],services={n:status(n) for n in LABELS},api_healthy=health(),manifest=str(manifest),logs=str(root/'logs')),indent=2));return
    if a.action=='start':
        if specification(root,c['source'],c['venv'],c['home'])!=c:raise ValueError('Source binding changed; reconcile installation first')
        for n in names:
            if status(n)['loaded']:continue
            if n=='server':
                with socket.socket() as s:
                    try:s.bind(('127.0.0.1',4200))
                    except OSError:raise RuntimeError('Listener occupied; refusing to adopt or terminate another server') from None
            else:
                end=time.monotonic()+20
                while time.monotonic()<end and status('server')['pid'] and not health():time.sleep(.2)
                if not status('server')['pid'] or not health():raise RuntimeError('Start and verify owned loopback server before worker')
            subprocess.run(['launchctl','enable',f'gui/{os.getuid()}/{LABELS[n]}'],check=True)
            subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(values[n][0])],check=True)
    else:
        if not a.db:raise ValueError('Explicit queue required for safe stop; pause and reconcile it first')
        import sys
        sys.path[:0]=[c['source'],str(Path(c['source'])/'apps/node')]
        from swarmtracker_node_ui.session_coordinator import Coordinator
        q=Coordinator(a.db);v=q.execution_status()
        if not v['control'] or v['control']['mode']=='running' or v['worker']['state']=='active' or any(x['state'] in {'submitted','acknowledged'} for x in v['assignments']):raise RuntimeError('Pause and quiesce the owned queue before stopping services')
        for n in reversed(names):
            if status(n)['loaded']:subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABELS[n]}'],check=True)
            subprocess.run(['launchctl','disable',f'gui/{os.getuid()}/{LABELS[n]}'],check=True)
    print(json.dumps({n:status(n) for n in LABELS},indent=2))

if __name__=='__main__':main()
