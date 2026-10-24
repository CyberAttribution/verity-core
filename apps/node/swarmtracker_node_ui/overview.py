"""Read-only capability observations from existing configuration and ledgers."""
import json
import sqlite3
import time
from pathlib import Path


def connection_rows(configuration, *, now=None):
    now=time.time() if now is None else now
    observed=configuration.observations();rows=[];items=configuration.all()
    for item in items:
        if item['kind']!='connection':continue
        observation=observed.get(('connection',item['id']))
        state=observation['state'] if observation else 'unknown'
        detail=observation['detail'] if observation else 'No observation for this configuration. Check this connection in Models & Connections.'
        if observation and not 0<=now-observation['at']<=300:
            state='stale';detail='Observation is older than five minutes; current availability is unknown. Recheck this connection.'
        if configuration.connection_disabled(item['id']):state='disabled';detail='Connection disabled. Select another permitted connection.'
        elif item['body'].get('transport'):
            from .managed_ssh_tunnel import MANAGED
            import subprocess
            tunnel=MANAGED.get(item['id'])
            try:authenticated=tunnel is not None and tunnel.healthy()
            except (OSError,ValueError,subprocess.SubprocessError):authenticated=False
            if not authenticated:state='unknown';detail='Authenticated SSH forward is not verified in this app process. Start/recheck this connection in Models & Connections; saved model health is insufficient.'
        hosts=[x['body']['from_id'] for x in items if x['kind']=='link' and x['body']['to_kind']=='connection' and x['body']['to_id']==item['id'] and x['body']['permission']=='hosts' and configuration.relationship(x)['binding_state']=='declared_current']
        machine=hosts[0]+' (current declaration; identity unverified)' if len(hosts)==1 else 'Machine unassigned or ambiguous; execution host unverified'
        rows.append(dict(id=item['id'],model=item['body']['model'],machine=machine,endpoint=item['body']['endpoint'],state=state,detail=detail,
                         at=observation['at'] if observation else None,version=item['version']))
    return rows


def workflow(configuration, runs, agent_id='', resource_id='', *, now=None):
    rows=connection_rows(configuration,now=now);connections={r['id']:r for r in rows}
    agents=[a for a in configuration.all() if a['kind']=='agent']
    choices=[dict(agent=a['id'],resource=r) for a in agents for r in a['body']['resources']]
    result=dict(choices=choices,connections=rows,state='not selected',detail='Select an existing agent and resource to inspect its primary workflow.',agent=agent_id,resource=resource_id)
    if not agent_id:return result
    try:effective=configuration.effective(agent_id,resource_id)
    except (ValueError,KeyError,TypeError):
        return dict(result,state='unavailable',detail='Agent/resource configuration is incomplete or no longer permitted. Review Active Agents.')
    connection=connections.get(effective['connection_id'])
    if connection is None:return dict(result,state='unknown',detail='Connection changed during observation. Reload and review Models & Connections.')
    result.update(model=effective['model'],connection=connection['id'],state=connection['state'],detail=connection['detail'])
    if effective.get('routing_enabled',True) is False:
        return dict(result,state='paused',detail='This agent is paused. Resume routing in Active Agents before a new request; existing evidence remains readable.')
    if connection['state']!='reachable':return result
    last=runs.latest_for_agent(agent_id,state='completed')
    observed_now=time.time() if now is None else now
    matching=last and 0<=observed_now-last['at']<=300 and last['configuration']['connection_hash']==effective['connection_hash'] and last.get('model')==effective['model']
    if matching:
        result.update(state='observed available',detail='Current connection health and a retained completed request match this configured model. Every new request rechecks authority; this is not OS containment.',request_id=last['id'],request_at=last['at'])
    else:
        result.update(state='execution unconfirmed',detail='Endpoint health responded, but no completed request for this agent confirms the current connection/model. Run a governed request; a configured model name is not readiness evidence.')
    return result


def store_inventory(path):
    """Missing and unreadable databases are distinct; never initialize for counts."""
    path=Path(path)
    if not path.exists():return dict(state='not created',count=None,path=str(path))
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts={t:db.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in ('runs','research','research_events','profiles') if t in tables}
        return dict(state='readable',counts=counts,path=str(path))
    except (sqlite3.Error,OSError):return dict(state='unknown',count=None,path=str(path))


def context_inventory(path):
    path=Path(path)
    if not path.exists():return dict(state='No organization context profiles retained in this app profile',profiles=0,active=0)
    try:
        from verity_core.organization_context import ContextStore
        profiles=ContextStore(path).profiles()
        return dict(state='Retained organization context profiles',profiles=len(profiles),active=sum(p['state']['active_version'] is not None for p in profiles))
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError):return dict(state='Organization context store unavailable; current context unknown',profiles=None,active=None)
