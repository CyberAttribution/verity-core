"""Article intake and recovery through the existing configured inference ledger."""
import hashlib
import json
import sqlite3
from .customer_config import canonical, label
from .company_work import CompanyWorkStore
from verity_core.trust.research_record import prompt




class ResearchJourney:
    def __init__(self,path,runs):
        self.path=path;self.runs=runs;self.work=CompanyWorkStore(path)
        with self.work._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_intake (task_id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def submit(self,*,task_id,article,source_url,source_claim,agent_id,resource_id,session_id,purpose,expected_configuration_sha256,requested_connection_id=''):
        import datetime
        from urllib.parse import urlsplit
        label(task_id)
        if not isinstance(article,dict) or set(article)!={'title','date','text','synthetic'}:raise ValueError('Article required')
        if not isinstance(article['title'],str) or not 0<len(article['title'])<=200:raise ValueError('Article title required')
        datetime.date.fromisoformat(article['date'])
        if not isinstance(article['text'],str) or not 0<len(article['text'])<=2000 or type(article['synthetic']) is not bool:raise ValueError('Bounded article required')
        url=urlsplit(source_url)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment or len(source_url)>1000:raise ValueError('HTTPS source required')
        if not isinstance(source_claim,str) or not 0<len(source_claim.strip())<=2000:raise ValueError('Source claim required')
        if not expected_configuration_sha256:raise ValueError('Review configuration first')
        article=dict(article,sha256=hashlib.sha256(article['text'].encode()).hexdigest())
        body=dict(task_id=task_id,article=article,source_url=source_url,source_claim=source_claim)
        # Reserve intake before inference. A crash cannot automatically resubmit.
        with self.work._db() as db:
            db.execute('INSERT INTO research_intake VALUES (?,?)',(task_id,canonical(body)))
        run=self.runs.run(agent_id=agent_id,task_id=task_id,resource_id=resource_id,session_id=session_id,purpose=purpose,
            prompt=prompt(article,source_url),expected_configuration_sha256=expected_configuration_sha256,
            requested_connection_id=requested_connection_id)
        if run['state']=='completed':self.recover(task_id)
        return run

    def recover(self,task_id):
        label(task_id)
        with self.work._db() as db:
            row=db.execute('SELECT body FROM research_intake WHERE task_id=?',(task_id,)).fetchone()
        if not row:raise KeyError(task_id)
        body=json.loads(row[0])
        # Read the unique durable task, never call inference from recovery.
        with self.runs._db() as db:
            row=db.execute('SELECT record FROM runs WHERE task_id=?',(task_id,)).fetchone()
        if not row:raise ValueError('Intake interrupted before request reservation; no automatic retry')
        run=json.loads(row[0])
        if run['state']!='completed':raise ValueError('Request unfinished, denied or failed; inspect request evidence')
        if run['prompt_sha256']!=hashlib.sha256(prompt(body['article'],body['source_url']).encode()).hexdigest():raise ValueError('Article/request prompt binding differs')
        bundle=self.runs.request_export(run['id'])
        try:return self.work.create(task_id,body['source_url'],body['source_claim'],run,article=body['article'],request_bundle=bundle)
        except sqlite3.IntegrityError:
            existing=self.work.get(task_id)
            if existing.get('request_id')!=run['id'] or existing.get('article')!=body['article']:raise ValueError('Research identity conflict')
            self.work.receipt(task_id)
            return existing

    def intakes(self):
        with self.work._db() as db:return [json.loads(r[0]) for r in db.execute('SELECT body FROM research_intake ORDER BY rowid DESC LIMIT 20')]
