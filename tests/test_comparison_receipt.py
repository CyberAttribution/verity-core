"""Frozen requested-configuration receipt acceptance."""
from verity_core.trust.inference_receipt import configuration_snapshot

def test_requested_runtime_options_survive_receipt_snapshot():
    original={'model':'fixture','runtime_options':{'keep_alive_seconds':90},'instructions':'PRIVATE','credential':'PRIVATE'}
    snapshot=configuration_snapshot(original)
    assert snapshot['runtime_options']=={'keep_alive_seconds':90}
    assert 'instructions' not in snapshot and 'credential' not in snapshot
    original['runtime_options']['keep_alive_seconds']=0
    assert snapshot['runtime_options']['keep_alive_seconds']==90
    assert 'runtime_options' not in configuration_snapshot({'model':'legacy'})


