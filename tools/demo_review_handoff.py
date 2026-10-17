"""Copy a verified fixed GLM review into an explicitly disposable demo profile.

No model commands or credentials are imported. The source DB stays read-only.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from swarmtracker_node_ui.session_coordinator import Coordinator
from swarmtracker_node_ui import product_demo


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-db",required=True);p.add_argument("--demo-root",required=True)
    p.add_argument("--demo-db",required=True);p.add_argument("--workflow",required=True)
    a=p.parse_args()
    root=Path(a.demo_root).resolve();target=Path(a.demo_db).resolve();source=Path(a.source_db).resolve()
    if target.parent!=root or source==target or source in root.parents or source.parent==root:
        raise ValueError("Source and disposable coordinator must be separate")
    os.environ["NODE_UI_DEMO_PROFILE"]="1";os.environ["NODE_UI_DEMO_ROOT"]=str(root);os.environ["NODE_UI_PRODUCT_DB"]=str(target)
    uri="file:"+str(source)+"?mode=ro"
    with sqlite3.connect(uri,uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute("SELECT * FROM tasks WHERE id='demoConflictReview'").fetchone()
    if not row or row["state"]!="independently_verified" or row["finish_reason"]!="stop" or not row["verifier"] or not row["evidence"]:
        raise ValueError("Source request needs independent verification and complete response")
    spec=json.loads(row["spec"]);response=json.loads(row["response"])
    if response.get("finish_reason")!="stop" or response.get("request_id")!=row["request_id"] or response.get("model")!=spec["model_settings"]["model"]:
        raise ValueError("Source response and request do not agree")
    review=dict(schema="verity_demo_review/v1",task_id=row["id"],request_id=row["request_id"],
        model=response["model"],finish_reason=row["finish_reason"],content=response["content"],
        settings=spec["model_settings"],verifier=row["verifier"],evidence=row["evidence"],usage=response.get("usage") or {})
    path=product_demo.attach_review(Coordinator(target),a.workflow,review)
    print(json.dumps({"workflow":a.workflow,"request_id":row["request_id"],"artifact":str(path),"verified":True}))


if __name__=="__main__":main()
