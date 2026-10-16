"""Read this Mac's memory pressure without loading a model or changing services."""
import subprocess,sys

def current():
    if sys.platform!="darwin":return {"state":"unavailable","level":None}
    try:
        value=subprocess.run(["/usr/sbin/sysctl","-n","kern.memorystatus_vm_pressure_level"],
                             capture_output=True,text=True,check=True,timeout=.5)
        level=int(value.stdout.strip())
        label="normal" if level==1 else "warning" if level==2 else "elevated" if level>2 else "unavailable"
        return {"state":label,"level":level}
    except (OSError,ValueError,subprocess.SubprocessError):return {"state":"unavailable","level":None}

def weight_gib(entry):
    size=entry.get("size") if isinstance(entry,dict) else None
    return round(size/2**30,2) if type(size) is int and 0<size<2**40 else None

def advice(pressure):
    state=pressure["state"]
    if state in ("warning","elevated"):
        return "Close unneeded applications or choose a smaller model before analysis; this Mac currently has "+state+" memory pressure."
    return "Allow memory for model context, runtime overhead and other applications. Normal memory pressure does not prove the selected model will fit."
