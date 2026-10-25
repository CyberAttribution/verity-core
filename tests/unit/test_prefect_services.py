import plistlib
import pytest
from tools.prefect_services import jobs,installed

def config(t):return dict(root=str(t/'private'),source=str(t/'source'),venv=str(t/'venv'),home=str(t/'home'),revision='a'*40)

def test_services_only_start_existing_loopback_prefect(tmp_path):
    c=config(tmp_path);v=jobs(c)
    assert v['server']['ProgramArguments'][1:]==['server','start','--host','127.0.0.1','--port','4200']
    assert v['worker']['ProgramArguments'][1:]==['worker','start','--pool','verity-local-process','--type','process','--limit','1']
    for job in v.values():
        assert job['EnvironmentVariables']['PREFECT_API_URL']=='http://127.0.0.1:4200/api'
        assert job['EnvironmentVariables']['DO_NOT_TRACK']=='1'
        assert job['KeepAlive']=={'SuccessfulExit':False}
        assert job['WorkingDirectory']==c['source']
        assert 'prefect_session.py' not in ' '.join(job['ProgramArguments'])

def test_foreign_service_binding_refused_without_replacement(tmp_path):
    c=config(tmp_path);p,value=installed(c)['server'];p.parent.mkdir(parents=True)
    p.write_bytes(plistlib.dumps(dict(value,ProgramArguments=['foreign-server'])))
    with pytest.raises(ValueError,match='another binding'):installed(c)
    assert plistlib.loads(p.read_bytes())['ProgramArguments']==['foreign-server']
