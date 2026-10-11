"""SQLite pipeline, review state transitions, audit trail and approved ledger."""
import csv
import hashlib
import io
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from parser import FIELDS, extract, fingerprint, validate

def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

class Conflict(ValueError): pass

class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
              CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_kind TEXT NOT NULL,
                source_hash TEXT UNIQUE NOT NULL, source_text TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('needs_review','approved','rejected','duplicate','failed')),
                fields TEXT NOT NULL, original_fields TEXT NOT NULL, evidence TEXT NOT NULL,
                ambiguity TEXT NOT NULL, issues TEXT NOT NULL, fingerprint TEXT,
                duplicate_of TEXT, version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS docs_fingerprint ON documents(fingerprint);
              CREATE TABLE IF NOT EXISTS source_files (
                document_id TEXT PRIMARY KEY REFERENCES documents(id), data BLOB NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                status TEXT NOT NULL, duration_ms REAL NOT NULL, error TEXT,
                created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                action TEXT NOT NULL, actor TEXT NOT NULL, details TEXT NOT NULL,
                created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS ledger (
                id TEXT PRIMARY KEY, document_id TEXT UNIQUE NOT NULL REFERENCES documents(id),
                fingerprint TEXT UNIQUE NOT NULL, fund TEXT NOT NULL, investor TEXT NOT NULL,
                notice_type TEXT NOT NULL, currency TEXT NOT NULL, amount_minor INTEGER NOT NULL,
                signed_amount_minor INTEGER NOT NULL, recallable_minor INTEGER NOT NULL,
                notice_date TEXT NOT NULL, effective_date TEXT NOT NULL, approved_at TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try: yield c
        finally:
            if c.in_transaction: c.rollback()
            c.close()

    def audit(self, c, doc_id, action, actor, details):
        c.execute('INSERT INTO audit(document_id,action,actor,details,created_at) VALUES(?,?,?,?,?)',
                  (doc_id, action, actor, json.dumps(details), now()))

    def decode(self, row):
        if row is None: raise KeyError('Notice not found.')
        d = dict(row)
        for key in ('fields', 'original_fields', 'evidence', 'ambiguity', 'issues'):
            d[key] = json.loads(d[key])
        return d

    def get(self, doc_id):
        with self.db() as c:
            doc = self.decode(c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone())
            doc['audit'] = [{**dict(r), 'details': json.loads(r['details'])}
                            for r in c.execute('SELECT * FROM audit WHERE document_id=? ORDER BY id DESC', (doc_id,))]
            return doc

    def documents(self):
        with self.db() as c:
            return [self.decode(r) for r in c.execute('SELECT * FROM documents ORDER BY created_at DESC, id')]

    def import_notice(self, name, text, kind='text', extraction_error=None, source_bytes=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160: raise ValueError('Source name must contain 1–160 characters.')
        if not isinstance(text, str) or len(text.encode()) > 100000: raise ValueError('Source text exceeds 100 KB.')
        if not text.strip() and extraction_error is None: raise ValueError('Provide readable source text.')
        started = time.perf_counter()
        source_hash = hashlib.sha256(source_bytes if source_bytes is not None else text.encode()).hexdigest()
        fields, evidence, ambiguity = extract(text)
        issues, normalized = validate(fields, ambiguity)
        fp = fingerprint(normalized) if not issues else None
        stamp, doc_id = now(), uuid.uuid4().hex
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT id FROM documents WHERE source_hash=?', (source_hash,)).fetchone()
            if existing:
                self.audit(c, existing['id'], 'duplicate_import', 'pipeline', {'source_name': name})
                c.commit()
                return {'id': existing['id'], 'duplicate': True, 'message': 'Identical source already imported.'}
            duplicate = c.execute("SELECT id FROM documents WHERE fingerprint=? AND status IN ('needs_review','approved') LIMIT 1", (fp,)).fetchone() if fp else None
            status = 'failed' if extraction_error else 'duplicate' if duplicate else 'needs_review'
            if extraction_error:
                issues = [{'field': 'source', 'code': 'unreadable', 'message': extraction_error}]
            c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (doc_id, name.strip(), kind, source_hash, text, status, json.dumps(fields), json.dumps(fields),
                 json.dumps(evidence), json.dumps(ambiguity), json.dumps(issues), fp,
                 duplicate['id'] if duplicate else None, 1, stamp, stamp))
            if source_bytes is not None:
                c.execute('INSERT INTO source_files VALUES(?,?)', (doc_id, source_bytes))
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                      (uuid.uuid4().hex, doc_id, 'failed' if extraction_error else 'completed',
                       round((time.perf_counter()-started)*1000, 3), extraction_error, stamp))
            self.audit(c, doc_id, 'imported', 'pipeline', {'source_kind': kind, 'checks': len(issues), 'duplicate_of': duplicate['id'] if duplicate else None})
            c.commit()
        return {'id': doc_id, 'duplicate': bool(duplicate), 'message': 'Imported for review.'}

    def review(self, doc_id, action, version, actor, reason='', updates=None):
        if action not in ('correct', 'approve', 'reject'): raise ValueError('Unknown review action.')
        if type(version) is not int or version < 1: raise ValueError('Provide a valid expected version.')
        if not isinstance(actor, str) or not 2 <= len(actor.strip()) <= 80: raise ValueError('Enter your reviewer name (2–80 characters).')
        if not isinstance(reason, str) or len(reason) > 1000: raise ValueError('Reason must contain at most 1,000 characters.')
        if action in ('correct', 'reject') and not reason.strip(): raise ValueError('Explain the correction or rejection.')
        if updates is not None and (not isinstance(updates, dict) or any(k not in FIELDS or not isinstance(v, str) or len(v)>250 for k,v in updates.items())):
            raise ValueError('Unsupported field or value in corrections.')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            d = self.decode(c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone())
            if action == 'approve' and d['status'] == 'approved':
                c.commit()  # Approval retries cannot create a second cash flow.
                return self.get(doc_id)
            if d['version'] != version: raise Conflict('This notice changed. Reload and review the latest values.')
            if d['status'] not in ('needs_review', 'duplicate'): raise Conflict('Only unapproved, readable notices can be reviewed.')
            fields, ambiguity = dict(d['fields']), list(d['ambiguity'])
            changes = {}
            if action == 'correct':
                if not updates: raise ValueError('Provide at least one field to confirm or correct.')
                for k,v in updates.items():
                    if v != fields.get(k, '') or k in ambiguity:
                        changes[k] = {'before': fields.get(k, ''), 'after': v}
                    fields[k] = v.strip()
                    if k in ambiguity: ambiguity.remove(k)
                if not changes: raise ValueError('No field values changed or ambiguities were confirmed.')
            issues, normalized = validate(fields, ambiguity)
            fp = fingerprint(normalized) if not issues else None
            status = d['status']
            dup = c.execute("SELECT id FROM documents WHERE fingerprint=? AND id<>? AND status IN ('needs_review','approved') LIMIT 1", (fp, doc_id)).fetchone() if fp else None
            if action == 'approve':
                if issues: raise Conflict('Resolve every validation issue before approval.')
                if dup: raise Conflict('An equivalent notice already exists. Investigate the duplicate before approval.')
                amount = normalized['amount_minor']
                c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (uuid.uuid4().hex, doc_id, fp, normalized['fund'], normalized['investor'], normalized['notice_type'],
                           normalized['currency'], amount, -amount if normalized['notice_type']=='capital_call' else amount,
                           normalized.get('recallable_amount_minor') or 0, normalized['notice_date'], normalized['effective_date'], now()))
                status = 'approved'
            elif action == 'reject': status = 'rejected'
            else: status = 'duplicate' if dup else 'needs_review'
            c.execute('UPDATE documents SET fields=?,ambiguity=?,issues=?,fingerprint=?,duplicate_of=?,status=?,version=version+1,updated_at=? WHERE id=?',
                      (json.dumps(fields),json.dumps(ambiguity),json.dumps(issues),fp,dup['id'] if dup else None,status,now(),doc_id))
            self.audit(c, doc_id, {'correct':'corrected','approve':'approved','reject':'rejected'}[action], actor.strip(), {'reason': reason.strip(), 'changes': changes, 'version': version+1})
            c.commit()
        return self.get(doc_id)

    def snapshot(self):
        with self.db() as c:
            counts = {r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM documents GROUP BY status')}
            jobs = [dict(r) for r in c.execute('SELECT j.*,d.source_name FROM jobs j JOIN documents d ON d.id=j.document_id ORDER BY j.created_at DESC LIMIT 100')]
            ledger = [dict(r) for r in c.execute('SELECT l.*,d.source_name FROM ledger l JOIN documents d ON d.id=l.document_id ORDER BY approved_at DESC')]
            totals = [dict(r) for r in c.execute('SELECT currency,SUM(signed_amount_minor) net_minor,COUNT(*) notices FROM ledger GROUP BY currency')]
            processing = dict(c.execute("SELECT COUNT(*) total,COALESCE(SUM(status='failed'),0) failed,COALESCE(AVG(duration_ms),0) mean_ms FROM jobs").fetchone())
            return {'counts':counts,'jobs':jobs,'ledger':ledger,'totals':totals,'processing':processing}

    def export(self):
        out = io.StringIO(); writer = csv.writer(out)
        writer.writerow(['notice_id','fund','investor','notice_type','currency','amount','signed_cash_flow','recallable_amount','notice_date','due_or_payment_date','approved_at'])
        for row in self.snapshot()['ledger']:
            # Prevent spreadsheet formulas in imported entity names.
            def safe(v): return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v
            def amount(v): return f'{v // 100}.{v % 100:02}' if v>=0 else '-'+f'{abs(v)//100}.{abs(v)%100:02}'
            writer.writerow([row['document_id'],safe(row['fund']),safe(row['investor']),row['notice_type'],row['currency'],amount(row['amount_minor']),amount(row['signed_amount_minor']),amount(row['recallable_minor']),row['notice_date'],row['effective_date'],row['approved_at']])
        return out.getvalue()
