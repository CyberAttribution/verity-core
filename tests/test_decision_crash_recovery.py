"""Process death before and after SQLite commit must not duplicate simulation."""
import os
from pathlib import Path
import subprocess
import sys
from swarmtracker_node_ui.decision_runs import RunStore, synthetic_manifest

def test_process_death_before_commit_rolls_back_and_after_commit_retry_is_denied(tmp_path):
    path=tmp_path/"runs.db";db=RunStore(path)
    view=db.create(synthetic_manifest())
    view=db.authorize(view["id"],"approved","Controlled crash recovery","operator",view["head_hash"])
    script="""import os,sys
from swarmtracker_node_ui.decision_runs import RunStore
db=RunStore(sys.argv[1]);v=db.get(sys.argv[2])
if sys.argv[3]=='before':
 original=db._save
 def fail(con,record):
  original(con,record)
  os._exit(91)
 db._save=fail
db.execute(v['id'],'operator',v['head_hash'])
os._exit(92)
"""
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTHONPATH=str(root)+os.pathsep+str(root/"apps/node"))
    before=subprocess.run([sys.executable,"-c",script,str(path),view["id"],"before"],env=env)
    assert before.returncode==91
    assert db.get(view["id"])==view
    after=subprocess.run([sys.executable,"-c",script,str(path),view["id"],"after"],env=env)
    assert after.returncode==92
    durable=db.get(view["id"])
    assert durable["executed"] and durable["attempts"]==1
    retried,allowed=db.execute(durable["id"],"operator",durable["head_hash"])
    assert not allowed and retried["attempts"]==2
    assert sum(e["payload"]["event"]=="action_executed" for e in retried["record"]["entries"])==1
