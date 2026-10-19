from pathlib import Path
import time,json
from huggingface_hub import snapshot_download
root=Path('/home/ubuntu/verity-session-20261019')
start=time.monotonic()
path=snapshot_download(repo_id='wtdcode/GLM-5.3-Flash-AWQ-W4A16',revision='abd7b07719111f137e1de8a0c1b7e01c11b74d1a',local_dir='/home/ubuntu/models/GLM-5.3-Flash-AWQ-W4A16',allow_patterns=['*.json','*.safetensors','*.jinja','*.model','tokenizer*','LICENSE*','README.md'],max_workers=8)
files=[p for p in Path(path).rglob('*') if p.is_file() and '.cache' not in p.parts]
result={'model':'wtdcode/GLM-5.3-Flash-AWQ-W4A16','revision':'abd7b07719111f137e1de8a0c1b7e01c11b74d1a','seconds':time.monotonic()-start,'bytes':sum(p.stat().st_size for p in files),'files':len(files)}
(root/'glm-download-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
