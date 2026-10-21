"""Persistent local warning episodes. Availability is not verified delivery."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3


class AlertJournal:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS alert_evaluations (session_id TEXT PRIMARY KEY,at REAL NOT NULL,digest TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS alert_episodes (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,warning TEXT NOT NULL,opened_at REAL NOT NULL,last_seen REAL NOT NULL,cleared_at REAL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_warning ON alert_episodes(session_id,warning) WHERE cleared_at IS NULL")

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def evaluate(self,session_id,warnings,at):
        if not isinstance(session_id,str) or not session_id or len(session_id)>240:
            raise ValueError("Invalid session identity")
        if type(at) not in (int,float) or not math.isfinite(at) or not 0<at<4102444800:
            raise ValueError("Invalid warning evaluation time")
        if (not isinstance(warnings,list) or len(warnings)>100 or any(
            not isinstance(w,str) or not w or len(w)>500 or any(ord(c)<32 for c in w) for w in warnings)):
            raise ValueError("Invalid warning snapshot")
        warnings=sorted(set(warnings))
        digest=hashlib.sha256(json.dumps(warnings).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior=db.execute("SELECT at,digest FROM alert_evaluations WHERE session_id=?",(session_id,)).fetchone()
            if prior and at<prior["at"]:
                raise ValueError("Older warning evaluation cannot replace current state")
            if prior and at==prior["at"]:
                if prior["digest"]!=digest:
                    raise ValueError("Conflicting warning evaluation at the same time")
                return False
            active={row["warning"]:row["id"] for row in db.execute(
                "SELECT id,warning FROM alert_episodes WHERE session_id=? AND cleared_at IS NULL",(session_id,))}
            for warning in warnings:
                if warning in active:
                    db.execute("UPDATE alert_episodes SET last_seen=? WHERE id=?",(at,active[warning]))
                else:
                    db.execute("INSERT INTO alert_episodes(session_id,warning,opened_at,last_seen) VALUES (?,?,?,?)",(session_id,warning,at,at))
            for warning,identifier in active.items():
                if warning not in warnings:
                    db.execute("UPDATE alert_episodes SET cleared_at=? WHERE id=?",(at,identifier))
            db.execute("INSERT OR REPLACE INTO alert_evaluations VALUES (?,?,?)",(session_id,at,digest))
        return True

    def history(self,session_id):
        with self.db() as db:
            return [dict(row,availability="local record only; delivery not verified") for row in db.execute(
                "SELECT * FROM alert_episodes WHERE session_id=? ORDER BY id DESC LIMIT 100",(session_id,))]
