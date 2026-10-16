"""Controlled offline activation lifecycle; uses only private fixture code files."""
import argparse,pathlib,json,re,urllib.request,urllib.error,urllib.parse,hashlib

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a):return None

def main():
    p=argparse.ArgumentParser();p.add_argument("--origin",required=True);p.add_argument("--private-dir",required=True);p.add_argument("--state-file",required=True);p.add_argument("--output",required=True)
    a=p.parse_args();private=pathlib.Path(a.private_dir);statefile=pathlib.Path(a.state_file);results=[]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path,data=None):
        req=urllib.request.Request(a.origin+path,data=urllib.parse.urlencode(data).encode() if data else None,headers={"Origin":a.origin,"Content-Type":"application/x-www-form-urlencoded"})
        try:r=opener.open(req,timeout=15)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,r.read().decode()
    def action(kind,name,expected,label):
        page=request("/setup")[1];token=re.search(r'name="operator_token" value="([^"]+)"',page).group(1)
        code=(private/(name+".code")).read_text().strip()
        before=statefile.read_bytes()
        status,page=request("/setup",{"operator_token":token,"action":kind,"code":code})
        assert status==expected,label
        if expected==422:assert statefile.read_bytes()==before,label+" mutated state"
        results.append({"case":label,"status":status,"state_preserved_on_rejection":expected==422})
    action("activate","mini-2",303,"replacement code activates")
    action("activate","mini-1",303,"old code remains usable before revocation import")
    action("revoke","revocation-1",303,"signed revocation list revision 1 imports")
    assert request("/")[0]==303
    results.append({"case":"imported revocation blocks current old code","status":303})
    action("activate","mini-1",422,"revoked old code cannot reactivate")
    action("activate","mini-2",303,"unlisted replacement remains usable")
    action("revoke","revocation-2",303,"cumulative revision 2 imports")
    assert request("/")[0]==303
    results.append({"case":"revision 2 blocks current replacement","status":303})
    action("activate","mini-2",422,"revoked replacement cannot reactivate")
    action("activate","mini-3",303,"new successor restores activation")
    action("revoke","revocation-1",422,"revocation revision rollback refused")
    action("revoke","revocation-3",422,"non-cumulative signed revocation refused")
    action("activate","expired",422,"expired signed code preserves current activation")
    action("activate","wrong-install",422,"wrong-installation signed code preserves current activation")
    assert request("/")[0]==200
    page=request("/setup")[1]
    assert "pilot-mini-enrolled-20261015-3" in page and "Activation active until" in page
    state=json.loads(statefile.read_text())
    report={"passed":True,"checks":results,"final_license_id":"pilot-mini-enrolled-20261015-3",
            "revocation_state_sha256":hashlib.sha256(state["revocations"].encode()).hexdigest(),
            "installation_id":state["installation_id"],"settings":state["settings"],"limits":"Only this isolated pilot profile was changed; no online revocation is claimed."}
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2))
    print(json.dumps({"passed":True,"checks":len(results),"final_license_id":report["final_license_id"]}))
if __name__=="__main__":main()
