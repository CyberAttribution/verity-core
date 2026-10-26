"""Disposable loopback-only Qwen experiment server. No task authority."""
import hashlib,json,threading,time,traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import torch,transformers
from transformers import Qwen3_5ForConditionalGeneration,AutoTokenizer,TextIteratorStreamer

ROOT=Path('/home/ubuntu/verity-qwen35')
REV='851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'
MODEL='Qwen/Qwen3.5-4B'
lock=threading.Lock(); log_lock=threading.Lock()
seen={json.loads(x)['request_id'] for x in (ROOT/'requests.jsonl').read_text().splitlines() if json.loads(x).get('kind')=='start'} if (ROOT/'requests.jsonl').exists() else set()
def event(kind,**kw):
    with log_lock,(ROOT/'requests.jsonl').open('a') as f:
        f.write(json.dumps(dict(at=time.time(),kind=kind,**kw),sort_keys=True)+'\n');f.flush()
tok=AutoTokenizer.from_pretrained(ROOT/'model',local_files_only=True)
model=Qwen3_5ForConditionalGeneration.from_pretrained(ROOT/'model',local_files_only=True,dtype=torch.bfloat16,attn_implementation='eager').to('cuda').eval()
assert all(p.device.type=='cuda' for p in model.parameters())
identity=dict(model=MODEL,revision=REV,torch=torch.__version__,transformers=transformers.__version__,precision='bfloat16',device=str(model.device),template_sha256=hashlib.sha256(tok.chat_template.encode()).hexdigest(),gpu=torch.cuda.get_device_name(),weights={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'model').glob('*.safetensors')})
(ROOT/'identity.json').write_text(json.dumps(identity,indent=2));event('ready',**identity)
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        raw=json.dumps(identity).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        rid=self.headers.get('X-Request-ID',''); n=int(self.headers.get('Content-Length','0'))
        if not rid or not 0<n<=100000:self.send_error(400);return
        body=json.loads(self.rfile.read(n))
        if body.get('model')!=MODEL or body.get('chat_template_kwargs')!={'enable_thinking':False} or 'reasoning_effort' in body or not 1<=body.get('max_tokens',0)<=768:self.send_error(400);return
        with log_lock:
            if rid in seen:self.send_error(409);return
            seen.add(rid)
        with lock:
            started=time.time();event('start',request_id=rid,payload=body,payload_sha256=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest())
            ids=tok.apply_chat_template(body['messages'],add_generation_prompt=True,enable_thinking=False,return_tensors='pt',return_dict=True).to('cuda')
            stream=TextIteratorStreamer(tok,skip_prompt=True,skip_special_tokens=True,timeout=120)
            result={}
            def generate():
                try:
                    with torch.inference_mode():result['ids']=model.generate(**ids,max_new_tokens=body['max_tokens'],do_sample=False,streamer=stream)
                except BaseException as e:result['error']=repr(e);stream.end()
            thread=threading.Thread(target=generate);thread.start()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers();self.close_connection=True
            text=[]; connected=True
            def send(chunk):
                nonlocal connected
                try:self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode());self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):connected=False
            for s in stream:
                text.append(s);event('token_chunk',request_id=rid,content=s)
                if connected:send(dict(id='qwen35-'+rid,model=MODEL,choices=[dict(index=0,delta={'content':s},finish_reason=None)]))
            thread.join();torch.cuda.synchronize()
            if 'error' in result:event('error',request_id=rid,error=result['error']);return
            generated=result['ids'][0,ids['input_ids'].shape[-1]:].tolist()
            eos=model.generation_config.eos_token_id
            finish='stop' if generated and generated[-1] in ([eos] if isinstance(eos,int) else eos) else 'length'
            usage=dict(prompt_tokens=ids['input_ids'].shape[-1],completion_tokens=len(generated),total_tokens=ids['input_ids'].shape[-1]+len(generated))
            output=''.join(text);record=dict(request_id=rid,finish_reason=finish,usage=usage,content=output,output_sha256=hashlib.sha256(output.encode()).hexdigest(),elapsed=time.time()-started,generated_ids=generated,identity=identity)
            (ROOT/('result-'+rid+'.json')).write_text(json.dumps(record,indent=2));event('completed',**record)
            # Explicit disposable transport fault after retaining real GPU result.
            if '[TEST_LOST_ACK]' in body['messages'][-1]['content']:event('intentional_lost_ack',request_id=rid);return
            if connected:
                send(dict(id='qwen35-'+rid,model=MODEL,choices=[dict(index=0,delta={},finish_reason=finish)],usage=usage))
                try:self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
