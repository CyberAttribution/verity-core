import time
import pytest
from test_research_journey import setup
from swarmtracker_node_ui.overview import workflow


@pytest.mark.parametrize('timestamp',[float('inf'),float('-inf'),'invalid',time.time()+3600])
def test_invalid_observation_unknown_in_overview_and_denies_without_dispatch(setup,timestamp):
    cfg,j,calls,c,form=setup
    first=j.runs.run(agent_id='researcher',task_id='historical',resource_id='research',purpose='generate_text',prompt='Historical fixture')
    receipt=j.runs.request_export(first['id'])
    cfg.observe('connection','local','reachable','Health fixture')
    with cfg._db() as db:db.execute("UPDATE observations SET at=? WHERE id='local'",(timestamp,))
    assert cfg.observations()[('connection','local')]['state']=='unknown'
    assert workflow(cfg,j.runs,'researcher','research')['state']=='unknown'
    result=j.runs.run(agent_id='researcher',task_id='clock-denied',resource_id='research',purpose='generate_text',prompt='Do not dispatch')
    assert result['state']=='denied' and len(calls)==1
    assert j.runs.request_export(first['id'])==receipt
