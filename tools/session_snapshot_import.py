"""Read a bounded trusted collector file into local Sessions and warning history.

No SSH credentials, network requests, task dispatch, retries of inference,
provider actions, secret values, or conversation resumption.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"apps"/"node"))
from swarmtracker_node_ui.sessions import SessionStore
from swarmtracker_node_ui.session_snapshot import import_snapshot
from swarmtracker_node_ui.session_alerts import AlertJournal
from swarmtracker_node_ui.sessions import derive


def once(database,session_id,path,now=None):
    now=time.time() if now is None else now
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1048576:
        raise ValueError("Trusted regular bounded snapshot file required")
    with path.open("rb") as file:
        raw=file.read(1048577)
    if len(raw)>1048576:
        raise ValueError("Snapshot grew beyond bound")
    snapshot=json.loads(raw)
    result=import_snapshot(database,session_id,snapshot,now=now)
    view=derive(database.get(session_id),now=now)
    AlertJournal(database.path.with_name("session-alerts.db")).evaluate(session_id,view["alerts"],now)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--session",required=True)
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--interval",type=int,default=30)
    parser.add_argument("--duration",type=int,default=7200)
    args=parser.parse_args()
    if not 5<=args.interval<=3600 or not 5<=args.duration<=28800:
        parser.error("Bounded interval/duration required")
    database=SessionStore(args.database)
    deadline=time.monotonic()+args.duration
    while time.monotonic()<deadline:
        try:
            print(json.dumps(once(database,args.session,args.snapshot)),flush=True)
        except (ValueError,KeyError,OSError,sqlite3.Error) as error:
            print(json.dumps(dict(state="observation import unavailable",error_type=type(error).__name__)),flush=True)
            if args.once:return 1
        if args.once:return 0
        time.sleep(args.interval)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
