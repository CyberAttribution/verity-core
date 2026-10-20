import io
import json
from swarmtracker_node_ui.replica_status import collect_service, service_view


class Response(io.BytesIO):
    status=200


class Opener:
    def __init__(self,metrics=b'vllm:num_requests_running{model_name="glm"} 1\nvllm:num_requests_waiting{model_name="glm"} 0\n',health=True):
        self.metrics=metrics;self.health=health;self.seen=[]
    def open(self,request,timeout):
        self.seen.append(request.full_url)
        if not self.health:raise OSError('Synthetic unavailable')
        if request.full_url.endswith('/health'):return Response(b'')
        if request.full_url.endswith('/v1/models'):return Response(b'{"data":[{"id":"glm-5.3-flash"}]}')
        return Response(self.metrics)


def test_independent_probe_reads_only_fixed_loopback_observation_endpoints():
    opener=Opener();s=collect_service(1,opener=opener,clock=lambda:1000)
    assert opener.seen==['http://127.0.0.1:8001/health','http://127.0.0.1:8001/v1/models','http://127.0.0.1:8001/metrics']
    assert s['state']=='ready' and s['running_requests']==1 and s['waiting_requests']==0
    assert s['activity']=='inference reported by server'


def test_missing_or_bad_metrics_never_implies_idle():
    for metrics in [b'# no counts',b'vllm:num_requests_running -1\n',b'vllm:num_requests_running nan\n']:
        s=collect_service(0,opener=Opener(metrics),clock=lambda:1000)
        assert s['state']=='ready' and s['running_requests'] is None and s['activity']=='unknown'
    opener=Opener(health=False);s=collect_service(0,opener=opener,clock=lambda:1000)
    assert s['state']=='unknown' and len(opener.seen)==1


def test_stale_and_malformed_service_samples_do_not_claim_live_readiness(tmp_path):
    p=tmp_path/'replica0-service.json';s=collect_service(0,opener=Opener(),clock=lambda:1000);p.write_text(json.dumps(s))
    assert service_view(tmp_path,0,now=1010)['state']=='ready'
    assert service_view(tmp_path,0,now=1201)['state']=='unknown'
    for altered in [dict(s,models=[]),dict(s,running_requests=True),dict(s,replica=1),dict(s,at=1100)]:
        p.write_text(json.dumps(altered));assert service_view(tmp_path,0,now=1010)['state']=='unknown'


def test_forged_activity_text_cannot_override_observed_count(tmp_path):
    s=collect_service(0,opener=Opener(b'vllm:num_requests_running 0\n'),clock=lambda:1000)
    s['activity']='inference reported by server';(tmp_path/'replica0-service.json').write_text(json.dumps(s))
    assert service_view(tmp_path,0,now=1010)['activity']=='no inference reported by server'
