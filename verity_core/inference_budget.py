"""USD reservation ledger for a future governed paid-inference adapter.

Amounts are integer microUSD. Estimates are reservations, not provider-enforced
maximum charges. Unknown completion holds its reservation. This foundation does
not enable external inference or constrain direct calls outside the adapter.
"""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time


class BudgetDenied(ValueError):pass


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',value):
        raise ValueError('Invalid budget identity')
    return value


def money(value,*,optional=False):
    if optional and value is None:return None
    if type(value) is not int or not 0<=value<=10**12:raise ValueError('Integer microUSD required')
    return value


class BudgetLedger:
    def __init__(self,path,*,clock=time.time):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS inference_budget_policies (agent TEXT NOT NULL,version INTEGER NOT NULL,digest TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(agent,version))')
            db.execute('CREATE TABLE IF NOT EXISTS inference_budget_entries (request_id TEXT PRIMARY KEY,agent TEXT NOT NULL,session_id TEXT NOT NULL,day INTEGER NOT NULL,estimate INTEGER NOT NULL,actual INTEGER,state TEXT NOT NULL,policy_digest TEXT NOT NULL,at REAL NOT NULL,detail TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS inference_budget_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS inference_cost_estimates (id TEXT PRIMARY KEY,agent TEXT NOT NULL,at REAL NOT NULL,body TEXT NOT NULL)')

    def record_estimate(self,agent,*,input_tokens,output_tokens,input_price,output_price,pricing_source,pricing_as_of,basis):
        """Append an offline scenario; never reserve, dispatch or reconcile usage."""
        from .offline_cost import estimate_cost
        import uuid
        identifier(agent)
        for text in (pricing_source,pricing_as_of,basis):
            if not isinstance(text,str) or not text.strip() or len(text)>500 or any(ord(c)<32 for c in text):
                raise ValueError('Pricing source, checked date and calculation basis required')
            if re.search(r'(?i)(bearer\s+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=])',text):
                raise ValueError('Credentials cannot be entered in estimate records')
        body=estimate_cost(input_tokens,output_tokens,input_price=input_price,output_price=output_price)
        body.update(input_tokens=input_tokens,output_tokens=output_tokens,input_price=input_price,output_price=output_price,
                    pricing_source=pricing_source,pricing_as_of=pricing_as_of,basis=basis,usage_source='operator assumptions')
        record=dict(id=uuid.uuid4().hex,agent=agent,at=time.time(),body=body)
        with self.db() as db:
            db.execute('INSERT INTO inference_cost_estimates VALUES (?,?,?,?)',
                       (record['id'],agent,record['at'],json.dumps(body,sort_keys=True,allow_nan=False)))
        return record

    def estimates(self,agent):
        identifier(agent)
        with self.db() as db:
            return [dict(row,body=json.loads(row['body'])) for row in db.execute(
                'SELECT * FROM inference_cost_estimates WHERE agent=? ORDER BY rowid DESC LIMIT 20',(agent,))]

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def _day(self):
        now=self.clock()
        if type(now) not in (int,float) or not 0<now<4102444800:raise ValueError('Invalid ledger clock')
        return int(now//86400)

    def set_policy(self,agent,*,daily_microusd,session_microusd,expected_policy_digest=None):
        identifier(agent);money(daily_microusd,optional=True);money(session_microusd,optional=True)
        if expected_policy_digest is not None and (not isinstance(expected_policy_digest,str) or (expected_policy_digest!='' and not re.fullmatch('[a-f0-9]{64}',expected_policy_digest))):
            raise ValueError('Reviewed monetary policy digest required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if expected_policy_digest is not None:
                previous=db.execute('SELECT digest FROM inference_budget_policies WHERE agent=? ORDER BY version DESC LIMIT 1',(agent,)).fetchone()
                if (previous['digest'] if previous else '')!=expected_policy_digest:raise BudgetDenied('Monetary policy changed since review')
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM inference_budget_policies WHERE agent=?',(agent,)).fetchone()[0]
            body=dict(agent=agent,version=version,currency='USD',unit='microUSD',daily_microusd=daily_microusd,session_microusd=session_microusd)
            raw=json.dumps(body,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(raw.encode()).hexdigest()
            db.execute('INSERT INTO inference_budget_policies VALUES (?,?,?,?)',(agent,version,digest,raw))
        return dict(body,digest=digest)

    def policy(self,agent):
        identifier(agent)
        with self.db() as db:row=self._policy(db,agent)
        return dict(json.loads(row['body']),digest=row['digest'])

    @staticmethod
    def _policy(db,agent):
        row=db.execute('SELECT * FROM inference_budget_policies WHERE agent=? ORDER BY version DESC LIMIT 1',(agent,)).fetchone()
        if row is None:raise BudgetDenied('Explicit monetary policy required')
        return row

    @staticmethod
    def _spent(db,agent,*,day=None,session_id=None):
        sql="SELECT COALESCE(SUM(CASE WHEN state='released' THEN 0 WHEN actual IS NOT NULL THEN actual ELSE estimate END),0) FROM inference_budget_entries WHERE agent=?"
        args=[agent]
        if day is not None:sql+=' AND day=?';args.append(day)
        if session_id is not None:sql+=' AND session_id=?';args.append(session_id)
        return db.execute(sql,args).fetchone()[0]

    def reserve(self,request_id,agent,*,session_id='',estimate_microusd,expected_policy_digest):
        identifier(request_id);identifier(agent);money(estimate_microusd)
        if not isinstance(session_id,str):raise ValueError('Session ID must be text')
        if session_id:identifier(session_id)
        if not isinstance(expected_policy_digest,str) or not re.fullmatch('[a-f0-9]{64}',expected_policy_digest):
            raise ValueError('Reviewed monetary policy digest required')
        day=self._day()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=self._policy(db,agent);policy=json.loads(row['body'])
            if row['digest']!=expected_policy_digest:raise BudgetDenied('Monetary policy changed since review')
            if db.execute('SELECT 1 FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone():
                raise BudgetDenied('Request already has a reservation; reconcile instead of retrying')
            daily=policy['daily_microusd'];session=policy['session_microusd']
            if session is not None and not session_id:raise BudgetDenied('Session ID required by monetary policy')
            if daily is not None and self._spent(db,agent,day=day)+estimate_microusd>daily:
                raise BudgetDenied('Daily monetary reservation limit reached')
            if session is not None and self._spent(db,agent,session_id=session_id)+estimate_microusd>session:
                raise BudgetDenied('Session monetary reservation limit reached')
            db.execute('INSERT INTO inference_budget_entries VALUES (?,?,?,?,?,NULL,?,?,?,?)',
                       (request_id,agent,session_id,day,estimate_microusd,'reserved',row['digest'],self.clock(),'Awaiting dispatch; estimate held'))
            self._event(db,request_id,'reserved',dict(estimate_microusd=estimate_microusd,policy_digest=row['digest']))
        return self.get(request_id)

    def get(self,request_id):
        identifier(request_id)
        with self.db() as db:
            row=db.execute('SELECT * FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone()
        if row is None:raise KeyError(request_id)
        result=dict(row);result['estimate_overrun']=result['actual'] is not None and result['actual']>result['estimate']
        return result

    def _event(self,db,request_id,state,data):
        at=self.clock()
        if type(at) not in (int,float) or not 0<at<4102444800:raise ValueError('Invalid ledger clock')
        db.execute('INSERT INTO inference_budget_events(request_id,state,at,data) VALUES (?,?,?,?)',
                   (request_id,state,at,json.dumps(data,sort_keys=True,separators=(',',':'))))

    def history(self,request_id):
        identifier(request_id)
        with self.db() as db:
            return [dict(row,data=json.loads(row['data'])) for row in db.execute('SELECT * FROM inference_budget_events WHERE request_id=? ORDER BY seq',(request_id,))]

    def mark_dispatched(self,request_id):
        """Single transition only; caller may dispatch ONLY after this succeeds."""
        identifier(request_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone()
            if row is None or row['state']!='reserved':raise BudgetDenied('Fresh undispatched reservation required')
            if row['policy_digest']!=self._policy(db,row['agent'])['digest'] or row['day']!=self._day():
                raise BudgetDenied('Reservation policy or UTC day changed before dispatch')
            db.execute("UPDATE inference_budget_entries SET state='dispatched',detail='Dispatch permitted once; charge not yet reconciled' WHERE request_id=?",(request_id,))
            self._event(db,request_id,'dispatched',dict(policy_digest=row['policy_digest']))
        return self.get(request_id)

    def mark_unknown(self,request_id):
        identifier(request_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT state FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone()
            if row is None or row['state'] not in ('reserved','dispatched','unknown'):raise BudgetDenied('Unknown outcome cannot replace terminal reconciliation')
            if row['state']!='unknown':
                db.execute("UPDATE inference_budget_entries SET state='unknown',detail='Completion unknown; estimate remains held; no automatic retry' WHERE request_id=?",(request_id,))
                self._event(db,request_id,'unknown',dict(reservation_held=True))
        return self.get(request_id)

    def reconcile(self,request_id,*,actual_microusd):
        identifier(request_id);money(actual_microusd)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone()
            if row is None or row['state'] not in ('dispatched','unknown','reconciled'):raise BudgetDenied('Dispatched or unknown request required')
            if not db.execute("SELECT 1 FROM inference_budget_events WHERE request_id=? AND state='dispatched'",(request_id,)).fetchone():
                raise BudgetDenied('Recorded dispatch authorization required before charge reconciliation')
            if row['state']=='reconciled' and row['actual']!=actual_microusd:raise BudgetDenied('Conflicting charge requires explicit correction workflow')
            if row['state']!='reconciled':
                db.execute("UPDATE inference_budget_entries SET state='reconciled',actual=?,detail='Attributed actual charge; provider provenance must be retained by adapter' WHERE request_id=?",(actual_microusd,request_id))
                self._event(db,request_id,'reconciled',dict(actual_microusd=actual_microusd,estimate_microusd=row['estimate'],estimate_overrun=actual_microusd>row['estimate']))
        return self.get(request_id)

    def release_undispatched(self,request_id,*,reason):
        identifier(request_id)
        if not isinstance(reason,str) or not 0<len(reason)<=240 or any(ord(c)<32 for c in reason):raise ValueError('Bounded pre-dispatch reason required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT state FROM inference_budget_entries WHERE request_id=?',(request_id,)).fetchone()
            if row is None or row['state']!='reserved':raise BudgetDenied('Only a known undispatched reservation may be released')
            db.execute("UPDATE inference_budget_entries SET state='released',detail=? WHERE request_id=?",(reason,request_id))
            self._event(db,request_id,'released',dict(reason=reason))
        return self.get(request_id)
