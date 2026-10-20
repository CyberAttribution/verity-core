"""Secret-free lifecycle records for version-bound customer credentials.

Native storage is supplied by the packager. No model provider is contacted and
no paid-inference authorization is granted by storing a credential.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import uuid
from .credentials import CredentialUnavailable,binding,identity


class CredentialConflict(ValueError):pass


class CredentialManager:
    def __init__(self,path,config,native=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.config=config;self.native=native
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS credential_profile (singleton INTEGER PRIMARY KEY CHECK(singleton=1),profile TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO credential_profile VALUES (1,?)',(str(uuid.uuid4()),))
            self.profile=db.execute('SELECT profile FROM credential_profile WHERE singleton=1').fetchone()[0]
            db.execute('CREATE TABLE IF NOT EXISTS credential_records (reference TEXT PRIMARY KEY,connection_id TEXT NOT NULL,config_digest TEXT NOT NULL,profile TEXT NOT NULL,provider TEXT NOT NULL,endpoint TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS credential_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,reference TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    @contextmanager
    def connection(self,identifier,digest,*,cleanup=False):
        with self.config._db() as db:
            db.execute('BEGIN IMMEDIATE')
            entry=self.config.latest('connection',identifier)
            if not entry or entry['digest']!=digest or (not cleanup and self.config.connection_disabled(identifier)):
                raise CredentialConflict('Connection changed or disabled; reload before changing credentials')
            if cleanup:bound=None
            else:
                if entry['body']['scope']!='external':raise CredentialConflict('External connection required')
                bound=binding(entry['body']['provider'],entry['body']['endpoint'])
            yield entry,bound

    def _transition(self,reference,state):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE credential_records SET state=?,at=? WHERE reference=?',(state,time.time(),reference))
            db.execute('INSERT INTO credential_events(reference,state,at) VALUES (?,?,?)',(reference,state,time.time()))

    def records(self):
        with self.db() as db:return [dict(row) for row in db.execute('SELECT * FROM credential_records ORDER BY at DESC,reference')]

    def history(self,reference):
        identity(reference)
        with self.db() as db:return [dict(row) for row in db.execute('SELECT * FROM credential_events WHERE reference=? ORDER BY seq',(reference,))]

    def create(self,connection_id,*,expected_digest,secret):
        if self.native is None:raise CredentialUnavailable('Protected credential storage is not configured')
        if not isinstance(secret,str) or not 1<=len(secret.encode())<=8192 or any(ord(c)<33 or ord(c)>126 for c in secret):
            raise ValueError('Bounded nonempty ASCII API credential required')
        with self.connection(connection_id,expected_digest) as (entry,bound):
            reference=str(uuid.uuid4())
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM credential_records WHERE connection_id=? AND state!='removed'",(connection_id,)).fetchone():
                    raise CredentialConflict('Remove or recover the existing credential before saving another')
                db.execute('INSERT INTO credential_records VALUES (?,?,?,?,?,?,?,?)',(reference,connection_id,entry['digest'],self.native.profile,bound['provider'],bound['endpoint'],'pending',time.time()))
                db.execute('INSERT INTO credential_events(reference,state,at) VALUES (?,?,?)',(reference,'pending',time.time()))
            try:
                actual=self.native.create(**bound,secret=secret,reference=reference)
                if actual!=reference:raise CredentialUnavailable('Credential reference mismatch')
            except (CredentialUnavailable,ValueError):
                self._transition(reference,'unknown')
                raise CredentialUnavailable('Credential save not confirmed; recovery reference retained') from None
            self._transition(reference,'stored')
            return reference

    def remove(self,reference,*,expected_digest):
        identity(reference)
        if self.native is None:raise CredentialUnavailable('Protected credential storage is not configured')
        row=next((row for row in self.records() if row['reference']==reference),None)
        if not row:raise CredentialConflict('Unknown credential reference')
        with self.connection(row['connection_id'],expected_digest,cleanup=True):
            # The initial routing lookup may precede another completed removal.
            # Re-read after the shared configuration lock serializes operations.
            with self.db() as db:
                row=dict(db.execute('SELECT * FROM credential_records WHERE reference=?',(reference,)).fetchone())
            if row['profile']!=self.native.profile:raise CredentialConflict('Credential belongs to a different local profile')
            if row['state']=='removed':return
            self._transition(reference,'removing')
            try:self.native.delete(reference)
            except CredentialUnavailable:
                self._transition(reference,'unknown')
                raise CredentialUnavailable('Credential removal not confirmed; recovery reference retained') from None
            self._transition(reference,'removed')

    def resolve(self,connection_id,*,expected_digest):
        """Internal adapter only. Never expose a credential through an HTTP route."""
        if self.native is None:raise CredentialUnavailable('Protected credential storage is not configured')
        with self.connection(connection_id,expected_digest) as (entry,bound):
            rows=[r for r in self.records() if r['connection_id']==connection_id and r['state']!='removed']
            if len(rows)!=1 or rows[0]['state']!='stored' or rows[0]['config_digest']!=entry['digest'] or rows[0]['profile']!=self.native.profile:
                raise CredentialConflict('Current connection has no confirmed credential binding')
            return self.native.read(rows[0]['reference'],**bound)
