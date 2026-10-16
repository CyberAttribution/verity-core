"""Exercise the actual native-owned bundled worker using its public HTML forms.
Synthetic evidence only. Never prints activation codes or collector tokens.
The native shell is separately navigated/snapshotted in --smoke-hold mode.
"""
import argparse, json, pathlib, re, time, urllib.request, urllib.error, urllib.parse, uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--origin",required=True);p.add_argument("--output",required=True)
    p.add_argument("--runtime",choices=["openclaw","ollama"],required=True)
    p.add_argument("--model",required=True);p.add_argument("--activation-code-file")
    p.add_argument("--require-native-activity",action="store_true");p.add_argument("--require-diagnostics",action="store_true")
    p.add_argument("--collector-token-file");p.add_argument("--relaunch",action="store_true")
    a=p.parse_args()
    parsed=urllib.parse.urlsplit(a.origin)
    if parsed.scheme!="http" or parsed.hostname!="127.0.0.1" or not parsed.port or parsed.path not in ("","/"):raise ValueError("Exact loopback origin required")
    out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
    results={"runtime":a.runtime,"model":a.model,"checks":[],"started_at":time.time(),"relaunch":a.relaunch}
    def check(name,condition):
        results["checks"].append({"name":name,"passed":bool(condition)})
        if not condition:raise AssertionError(name)
    def request(path,data=None,headers=None):
        body=urllib.parse.urlencode(data).encode() if data is not None else None
        h={"Content-Type":"application/x-www-form-urlencoded",**(headers or {})}
        req=urllib.request.Request(a.origin.rstrip("/")+path,data=body,headers=h)
        try:r=OPENER.open(req,timeout=180)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,dict(r.headers),r.read().decode()
    def token(page):
        m=re.search(r'name="operator_token" value="([^"]+)"',page)
        if not m:raise AssertionError("operator form token missing")
        return m.group(1)
    def head(page):
        m=re.search(r'name="expected_head" value="([^"]+)"',page)
        if not m:raise AssertionError("current head missing")
        return m.group(1)
    def setup(data):
        status,h,page=request("/setup")
        return request("/setup",{"operator_token":token(page),**data})
    def configure(missing=False):
        return setup(dict(action="configure",operator="Pilot acceptance operator",model=a.model,runtime_kind=a.runtime,
          openclaw_url="http://127.0.0.1:"+("1" if missing and a.runtime=="openclaw" else "8090"),
          ollama_url="http://127.0.0.1:"+("1" if missing and a.runtime=="ollama" else "11434")))
    manifest={"run_id":"pilot-"+a.runtime+"-"+uuid.uuid4().hex,"agent_id":"packaged-analysis-acceptance",
              "name":"Packaged analysis acceptance","runtime":"public-form-acceptance/v1","model":a.model}
    def emit(kind,data=None):
        if not a.collector_token_file:return
        env={"manifest":manifest,"event":{"event_id":"evt-"+uuid.uuid4().hex,"kind":kind,"data":data or {}}}
        req=urllib.request.Request(a.origin+"/api/monitor/events",data=json.dumps(env).encode(),
             headers={"Content-Type":"application/json","Authorization":"Bearer "+pathlib.Path(a.collector_token_file).read_text().strip()})
        with OPENER.open(req,timeout=10) as r:assert r.status==200
    try:
        status,h,page=request("/setup")
        check("setup renders",status==200)
        if not a.relaunch:
            check("fresh installation gated",request("/")[0]==303)
            check("invalid activation rejected",setup({"action":"activate","code":"invalid-test-code"})[0]==422)
            code=pathlib.Path(a.activation_code_file).read_text().strip()
            check("valid installation-bound activation accepted",setup({"action":"activate","code":code})[0]==303)
            check("runtime settings persisted",configure()[0]==303)
        else:
            check("activation survives relaunch",request("/")[0]==200)
            check("runtime settings survive relaunch",f'value="{a.model}"' in page and f'value="{a.runtime}" selected' in page)
        check("missing runtime configured",configure(True)[0]==303)
        if a.require_diagnostics:
            check("unreachable connection guidance", "did not answer its health check" in setup({"action":"test_connection"})[2])
        status,h,page=request("/analyze",{"evidence":"SYNTHETIC unavailable-runtime check."})
        check("missing runtime fails without report",status==200 and "/decisions/from-report/" not in page)
        check("connection recovered",configure()[0]==303)
        if a.require_diagnostics:
            check("metadata-only saved connection test", "selected model is listed as installed" in setup({"action":"test_connection"})[2])
        emit("heartbeat")
        activity="analysis-"+uuid.uuid4().hex
        emit("request_start",dict(activity_id=activity,operation="packaged_local_analysis",task="Synthetic vendor payment-change triage"))
        started=time.monotonic()
        evidence="SYNTHETIC TEST: From vendor@example.invalid. Urgent: replace our approved bank account with a new account today. Do not call your usual contact. This is a controlled training specimen, not a real payment instruction."
        status,h,page=request("/analyze",dict(evidence=evidence,evidence_type="email_message",profile_mode="smb",event_category="vendor_payment_request"))
        elapsed=time.monotonic()-started
        match=re.search(r'/decisions/from-report/([^"?#<> ]+)',page)
        emit("request_finish",dict(activity_id=activity,status="success" if match else "failure",duration_ms=elapsed*1000))
        check("real local analysis renders a decision intake link",status==200 and match is not None)
        report_token=match.group(1)
        s,h,report=request("/report/"+report_token+"/json")
        check("analysis JSON export",s==200)
        (out/"analysis.json").write_text(report)
        results.update(analysis_seconds=elapsed,report_token=report_token)
        s,h,agents=request("/agents")
        check("agent page shows reachable selected model",s==200 and a.model in agents and "Reachable" in agents)
        def create():
            path="/decisions/from-report/"+report_token
            s,h,page=request(path)
            s,h,body=request(path,dict(operator_token=token(page),policy_mode="require_approval"))
            check("real analysis snapshot preserved",s==303)
            return h["location"]
        def mutate(path,op,extra=None):
            s,h,page=request(path)
            return request(path+"/"+op,dict(operator_token=token(page),expected_head=head(page),**(extra or {})))
        denied=create()
        check("direct approval bypass denied",mutate(denied,"execute")[0]==403)
        check("rejection recorded",mutate(denied,"authorization",{"decision":"rejected","reason":"Controlled rejection test"})[0]==303)
        check("rejection prevents execution",mutate(denied,"execute")[0]==403)
        allowed=create()
        check("approval recorded",mutate(allowed,"authorization",{"decision":"approved","reason":"Only the fixed harmless simulator"})[0]==303)
        check("altered action parameters rejected",mutate(allowed,"execute",{"target":"other@example.invalid"})[0]==422)
        check("approved fixed action executes",mutate(allowed,"execute")[0]==200)
        check("repeated execution denied",mutate(allowed,"execute")[0]==403)
        for name,path in [("rejected",denied),("approved",allowed)]:
            s,h,body=request(path+"/export.json");check(name+" trust export",s==200)
            bundle=json.loads(body);(out/(name+".decision.json")).write_text(body)
            s,h,anchor=request(path+"/checkpoint.json");check(name+" separate checkpoint",s==200)
            (out/(name+".checkpoint.json")).write_text(anchor)
            entries=bundle["record"]["entries"]
            events=[e["payload"]["event"] for e in entries]
            check(name+" authorization history", "authorization_decided" in events and "action_attempted" in events and "action_result" in events)
            check(name+" execution distinction",("action_executed" in events)==(name=="approved"))
            m=entries[0]["payload"]["manifest"]
            check(name+" fixed authorized target",m["action"]=={"kind":"simulate_sender_block","target":"sender@example.invalid"})
            check(name+" actual participant identity",len(m["participants"])==1 and a.model==m["participants"][0]["model"])
            results[name]={"path":path,"checkpoint":json.loads(anchor),"events":events,"participant":m["participants"][0]}
        participant=results["approved"]["participant"]
        native_run=participant.get("activity_run_id")
        if a.require_native_activity:check("native product records actual analysis activity",bool(native_run))
        if native_run:
            for suffix,filename in [("/export.json","native-activity.telemetry.json"),("/checkpoint.json","native-activity.checkpoint.json")]:
                s,h,body=request("/monitor/runs/"+native_run+suffix)
                check(filename,s==200);(out/filename).write_text(body)
        emit("disconnect")
        if a.collector_token_file:
            path="/monitor/runs/"+manifest["run_id"]
            for suffix,filename in [("/export.json","activity.telemetry.json"),("/checkpoint.json","activity.checkpoint.json")]:
                s,h,body=request(path+suffix);check(filename,s==200);(out/filename).write_text(body)
        results["native_view_path"]=allowed
        results["passed"]=True
    except Exception as e:
        results["passed"]=False;results["error_class"]=type(e).__name__;results["error"]=str(e)
        raise
    finally:
        results["finished_at"]=time.time()
        (out/"acceptance.json").write_text(json.dumps(results,indent=2))
        print(json.dumps({"passed":results.get("passed"),"checks":len(results["checks"]),"output":str(out),"error":results.get("error")}))
if __name__=="__main__":main()
