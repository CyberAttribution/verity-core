import subprocess
from types import SimpleNamespace
from swarmtracker_node_ui import memory_preflight as m

def test_fixed_bounded_pressure_probe_and_warning_guidance(monkeypatch):
    monkeypatch.setattr(m.sys,"platform","darwin")
    def probe(argv,**kw):
        assert argv==["/usr/sbin/sysctl","-n","kern.memorystatus_vm_pressure_level"]
        assert kw["timeout"]==.5 and "shell" not in kw
        return SimpleNamespace(stdout="2\n")
    monkeypatch.setattr(m.subprocess,"run",probe)
    pressure=m.current()
    assert pressure["state"]=="warning"
    assert "smaller model" in m.advice(pressure)

def test_failed_probe_is_unknown_and_normal_is_not_a_fit_claim(monkeypatch):
    monkeypatch.setattr(m.sys,"platform","darwin")
    monkeypatch.setattr(m.subprocess,"run",lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired("sysctl",.5)))
    assert m.current()["state"]=="unavailable"
    assert "does not prove" in m.advice({"state":"normal"})

def test_model_weight_measurement_has_no_memory_fit_assertion():
    assert m.weight_gib({"size":4683087332})==4.36
    assert m.weight_gib({"size":True}) is None
    assert m.weight_gib({"size":-1}) is None
    assert m.weight_gib(None) is None
