"""Restart persistence and concurrent retry checks against a native-owned worker."""
import argparse,json,pathlib,re,urllib.request,urllib.error,urllib.parse
from concurrent.futures import ThreadPoolExecutor

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None
def main():
    p=argparse.ArgumentParser();p.add_argument("--origin",required=True);p.add_argument("--prior",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();prior=pathlib.Path(a.prior);out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
    results={"checks":[]}
    def check(name,ok):
        results["checks"].append({"name":name,"passed":bool(ok)})
        if not ok:raise AssertionError(name)
    def req(path,data=None):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        r=urllib.request.Request(a.origin+path,data=urllib.parse.urlencode(data).encode() if data is not None else None,
                                headers={"Content-Type":"application/x-www-form-urlencoded"})
        try:r=opener.open(r,timeout=20)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,dict(r.headers),r.read().decode()
    def field(page,name):return re.search('name="'+name+'" value="([^"]+)"',page).group(1)
    def form(path):
        s,h,page=req(path);return {"operator_token":field(page,"operator_token"),"expected_head":field(page,"expected_head")}
    try:
        check("active after native restart",req("/")[0]==200)
        for name in ["approved","rejected"]:
            old=json.loads((prior/(name+".decision.json")).read_text());rid=old["record"]["record_id"];path="/decisions/"+rid
            s,h,body=req(path+"/export.json")
            check(name+" exact pre-restart history retained",s==200 and json.loads(body)==old)
            check(name+" retry denied after restart",req(path+"/execute",form(path))[0]==403)
            s,h,body=req(path+"/export.json");new=json.loads(body)
            count=sum(e["payload"]["event"]=="action_executed" for e in new["record"]["entries"])
            check(name+" no duplicate execution",count==(1 if name=="approved" else 0))
            (out/(name+".decision.json")).write_text(body)
            (out/(name+".checkpoint.json")).write_text(req(path+"/checkpoint.json")[2])
        s,h,index=req("/decisions");intent=field(index,"operator_token")
        def create():
            s,h,body=req("/decisions/demo",{"operator_token":intent,"policy_mode":"require_approval"})
            check("new controlled run",s==303);return h["location"]
        first=create();second=create()
        first_form=form(first);second_form=form(second)
        check("approval from another record rejected",req(second+"/authorization",{**first_form,"decision":"approved","reason":"cross-record attempt"})[0]==409)
        check("second record remains pending", "Authorization: <strong>pending" in req(second)[2])
        check("first record approved",req(first+"/authorization",{**first_form,"decision":"approved","reason":"Concurrent controlled retry"})[0]==303)
        approved_form=form(first)
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes=list(pool.map(lambda _:req(first+"/execute",approved_form)[0],range(6)))
        check("one concurrent execution succeeds",codes.count(200)==1 and codes.count(409)==5)
        s,h,body=req(first+"/export.json");bundle=json.loads(body)
        check("one durable execution entry",sum(e["payload"]["event"]=="action_executed" for e in bundle["record"]["entries"])==1)
        (out/"concurrent.decision.json").write_text(body)
        (out/"concurrent.checkpoint.json").write_text(req(first+"/checkpoint.json")[2])
        check("missing operator intent refused",req(second+"/execute",{})[0]==403)
        check("unknown run fails closed",req("/decisions/unknown-test-run")[0]==404)
        results.update(passed=True,concurrent_codes=codes)
    except Exception as e:
        results.update(passed=False,error_class=type(e).__name__,error=str(e));raise
    finally:
        (out/"recovery.json").write_text(json.dumps(results,indent=2));print(json.dumps(results))
if __name__=="__main__":main()
