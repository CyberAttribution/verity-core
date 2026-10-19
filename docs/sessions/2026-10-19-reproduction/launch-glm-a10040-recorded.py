from pathlib import Path
import json,subprocess,time,datetime
root=Path('/home/ubuntu/verity-session-20261019')
image='lazymio/vllm-backport@sha256:2627d2a97e7f539b99ac98f511c589f5f520d3fc5630b195aca7f6ef865ead4e'
started=time.monotonic()
while time.monotonic()-started < 600:
 ready=(root/'glm-download-result.json').exists()
 available=subprocess.run(['sudo','-n','docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
 if ready and available:break
 time.sleep(5)
else:raise SystemExit('GLM download/image deadline exceeded; no load attempted')
used=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True)
assert all(int(x.strip())<100 for x in used.splitlines()),'GPUs already occupied; refusing collision'
args=['sudo','-n','docker','run','-d','--name','verity-glm-20261019','--gpus','all','--cap-drop','ALL','--security-opt','no-new-privileges','--shm-size','32g','-p','127.0.0.1:8001:8000','-v','/home/ubuntu/models/GLM-5.3-Flash-AWQ-W4A16:/model:ro','-e','NCCL_ALGO=Ring','-e','NCCL_PROTO=Simple',image,'/model','--served-model-name','glm-5.3-flash','--tensor-parallel-size','8','--max-model-len','8192','--max-num-seqs','2','--gpu-memory-utilization','0.85','--max-num-batched-tokens','8192','--disable-custom-all-reduce','--reasoning-parser','glm45','--enforce-eager','--host','0.0.0.0','--port','8000']
(root/'glm-launch-args.json').write_text(json.dumps(args,indent=2))
result=subprocess.run(args,text=True,capture_output=True,timeout=60)
(root/'glm-launch-result.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},indent=2))
print(result.stdout,result.stderr,flush=True);result.check_returncode()
