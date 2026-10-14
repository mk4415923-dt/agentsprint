"""SQLite persistence: case lifecycle, versioned policies and immutable audit trail.
Single-device demo store. Switch this module for Postgres in a multi-user deployment.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ['AEGIS_DATA_DIR']).expanduser().resolve() / 'state' / 'aegis.db' if os.environ.get('AEGIS_DATA_DIR') else Path(__file__).resolve().parent / 'state' / 'aegis.db'
LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=15)
    try:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA busy_timeout=15000')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize():
    with LOCK, connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS cases (
          email_id TEXT PRIMARY KEY, status TEXT NOT NULL,
          draft TEXT NOT NULL DEFAULT '', revised_at TEXT NOT NULL,
          policy_version INTEGER NOT NULL DEFAULT 0,
          approved_at TEXT, sent_at TEXT, sent_message_id TEXT, sent_to TEXT,
          last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
          email_id TEXT, action TEXT NOT NULL, actor TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS audit_email_idx ON audit(email_id,id);
        CREATE TABLE IF NOT EXISTS policy_versions (
          version INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          actor TEXT NOT NULL, content TEXT NOT NULL, notes TEXT NOT NULL,
          checksum TEXT NOT NULL
        );
        ''')
    migrate_v3()


def audit(action, email_id=None, actor='system', **detail):
    safe = {k: v for k, v in detail.items() if k not in ('body', 'draft', 'api_key', 'token', 'secret')}
    with LOCK, connect() as con:
        con.execute('INSERT INTO audit(happened_at,email_id,action,actor,detail) VALUES(?,?,?,?,?)',
                    (now(), email_id, action, actor, json.dumps(safe, ensure_ascii=False)))


def list_audit(limit=400, email_id=None):
    with LOCK, connect() as con:
        if email_id:
            rows = con.execute('SELECT * FROM audit WHERE email_id=? ORDER BY id DESC LIMIT ?', (email_id, limit)).fetchall()
        else:
            rows = con.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [{**dict(r), 'detail': json.loads(r['detail'])} for r in rows]


def all_cases():
    with LOCK, connect() as con:
        rows = con.execute('SELECT * FROM cases ORDER BY email_id').fetchall()
    return {r['email_id']: dict(r) for r in rows}


def case(email_id):
    with LOCK, connect() as con:
        row = con.execute('SELECT * FROM cases WHERE email_id=?', (email_id,)).fetchone()
    return dict(row) if row else None


def save_ai_result(result, version):
    eid = result['email_id']
    draft = result['reply_draft']
    with LOCK, connect() as con:
        before = con.execute('SELECT status FROM cases WHERE email_id=?', (eid,)).fetchone()
        if before and before['status'] in ('sent','provider_accepted','sending','send_uncertain'):
            audit('reanalyzed_sent_case_not_overwritten', eid, rationale='Existing sent record preserved')
            return False
        status = 'needs_review' if result.get('escalate') else 'draft_ready'
        con.execute('''INSERT INTO cases(email_id,status,draft,revised_at,policy_version,approved_at)
                       VALUES(?,?,?,?,?,NULL)
                       ON CONFLICT(email_id) DO UPDATE SET
                       status=excluded.status,draft=excluded.draft,revised_at=excluded.revised_at,
                       policy_version=excluded.policy_version,approved_at=NULL,last_error='' ''',
                    (eid,status,draft,now(),version))
    if result.get('escalate') or result.get('priority') == 'high':
        import quality
        suggested=quality.route_case(result)
        if suggested:
            with LOCK, connect() as con:
                con.execute("UPDATE cases SET assigned_to=? WHERE email_id=? AND (assigned_to='' OR assigned_to IS NULL)", (suggested,eid))
            audit('escalation_routed',eid,department=suggested,reason=str(result.get('escalation_reason',''))[:160])
    audit('ai_draft_created', eid, policy_version=version, status=status, priority=result.get('priority'),
          policy_refs=result.get('policy_refs',[]), confidence=result.get('confidence'))
    return True


def edit_draft(email_id, text):
    with LOCK, connect() as con:
        row = con.execute('SELECT status FROM cases WHERE email_id=?', (email_id,)).fetchone()
        if not row: raise ValueError('Analyze this email before editing a reply.')
        if row['status'] in ('sent','provider_accepted','sending','send_uncertain'): raise ValueError('This email may have been sent; edit is blocked until status is resolved.')
        con.execute('UPDATE cases SET draft=?,status=?,revised_at=?,approved_at=NULL,last_error=? WHERE email_id=?',
                    (text, 'needs_review', now(), '', email_id))
    audit('draft_edited', email_id, actor='operator', new_word_count=len(text.split()), reason='Manual edits require re-approval')


def approve(email_id):
    with LOCK, connect() as con:
        row = con.execute('SELECT * FROM cases WHERE email_id=?',(email_id,)).fetchone()
        if not row: raise ValueError('No AI draft exists for this case.')
        if row['status'] in ('sent','provider_accepted','sending','send_uncertain'): raise ValueError('Case is already submitted or has an uncertain delivery state.')
        if row['status']=='rejected': raise ValueError('This draft was rejected. Edit it or reopen the case before approving.')
        if not row['draft'].strip(): raise ValueError('Reply text is empty.')
        con.execute('UPDATE cases SET status=?, approved_at=?, revised_at=? WHERE email_id=?',
                    ('approved', now(), now(), email_id))
    audit('operator_approved', email_id, actor='operator', policy_version=row['policy_version'])


def begin_send(email_id, recipient):
    """Atomic transition before network I/O; survives crashes without duplicate submission."""
    with LOCK, connect() as con:
        row=con.execute('SELECT status FROM cases WHERE email_id=?',(email_id,)).fetchone()
        if not row or row['status']!='approved':
            raise ValueError('Case must be approved and must not have a prior pending send.')
        con.execute('UPDATE cases SET status=?,revised_at=? WHERE email_id=?',('sending',now(),email_id))
        cur=con.execute('INSERT INTO send_attempts(email_id,started_at,status,recipient) VALUES(?,?,?,?)',
                        (email_id,now(),'sending',recipient))
        attempt=cur.lastrowid
    audit('gmail_send_started',email_id,actor='operator',attempt_id=attempt,recipient=recipient)
    return attempt


def finish_send(email_id, attempt_id, *, state, message_id='', error=''):
    if state not in ('provider_accepted','send_failed','send_uncertain'):
        raise ValueError('Invalid Gmail outcome state.')
    if state=='provider_accepted' and not message_id:
        raise ValueError('A provider-accepted send must contain a Google message ID.')
    with LOCK, connect() as con:
        attempt=con.execute('SELECT * FROM send_attempts WHERE id=? AND email_id=?',(attempt_id,email_id)).fetchone()
        case=con.execute('SELECT status FROM cases WHERE email_id=?',(email_id,)).fetchone()
        if not attempt or attempt['status']!='sending' or not case or case['status']!='sending':
            raise ValueError('Send state conflict; check audit before retrying.')
        con.execute('UPDATE send_attempts SET status=?,completed_at=?,provider_message_id=?,error=? WHERE id=?',
                    (state,now(),message_id,error[:500],attempt_id))
        con.execute("""UPDATE cases SET status=?,sent_at=?,sent_message_id=?,sent_to=?,last_error=?,revised_at=?
                       WHERE email_id=?""",
                    (state,now() if state=='provider_accepted' else None,message_id,
                     attempt['recipient'],error[:400],now(),email_id))
    action={'provider_accepted':'gmail_provider_accepted','send_failed':'gmail_send_failed',
            'send_uncertain':'gmail_delivery_uncertain'}[state]
    audit(action,email_id,actor='operator',attempt_id=attempt_id,provider_message_id=message_id or None,
          recipient=attempt['recipient'],reason=error[:180])


def recover_incomplete_sends():
    """A crash might occur after Gmail accepts a message; never silently resend."""
    with LOCK, connect() as con:
        rows=con.execute("SELECT email_id FROM cases WHERE status='sending'").fetchall()
        con.execute("UPDATE cases SET status='send_uncertain',last_error='Server restarted during Gmail send; verify in Gmail Sent', revised_at=? WHERE status='sending'",(now(),))
        con.execute("UPDATE send_attempts SET status='send_uncertain',completed_at=? WHERE status='sending'",(now(),))
    for row in rows:
        audit('gmail_send_recovered_as_uncertain',row['email_id'],reason='Previous process ended before provider outcome could be saved')
    return len(rows)


def mark_send_result(email_id, *, recipient, success, message_id='', error=''):
    """Compatibility wrapper for offline tests; live API uses begin_send / finish_send."""
    attempt=begin_send(email_id,recipient)
    finish_send(email_id,attempt,state='provider_accepted' if success else 'send_failed',message_id=message_id,error=error)


def send_history(email_id):
    with LOCK, connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM send_attempts WHERE email_id=? ORDER BY id DESC',(email_id,))]


def delivery_retry_authorize(email_id,reason):
    if len(reason.strip())<8:raise ValueError('Provide a retry justification (8 characters minimum).')
    with LOCK, connect() as con:
        row=con.execute('SELECT status FROM cases WHERE email_id=?',(email_id,)).fetchone()
        if not row or row['status']!='send_failed':
            raise ValueError('Only definite send failures can be retried. Uncertain sends require manual Gmail verification.')
        con.execute("UPDATE cases SET status='approved',last_error='',revised_at=? WHERE email_id=?",(now(),email_id))
    audit('gmail_retry_authorized',email_id,actor='operator',reason=reason[:180])


def update_sync_cursor(account, cursor):
    import hashlib
    key='gmail_sync_cursor_'+hashlib.sha256(account.encode()).hexdigest()[:16]
    with LOCK, connect() as con:
        con.execute("INSERT INTO app_settings(name,value,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(key,str(cursor),now()))


def sync_cursor(account):
    import hashlib
    key='gmail_sync_cursor_'+hashlib.sha256(account.encode()).hexdigest()[:16]
    with LOCK, connect() as con:
        row=con.execute('SELECT value FROM app_settings WHERE name=?',(key,)).fetchone()
    return row['value'] if row else None


def register_thread_message(message):
    with LOCK, connect() as con:
        con.execute("""INSERT INTO gmail_threads(gmail_message_id,thread_id,email_id,sender,subject,received_at,snippet,provider_status)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(gmail_message_id) DO UPDATE SET provider_status=excluded.provider_status""",
          (message['gmail_message_id'],message.get('thread_id',''),message.get('id'),message.get('from_email',''),
           message.get('subject',''),message.get('received_at',''),(message.get('body','') or '')[:250],'inbox'))


def thread_messages(thread_id):
    with LOCK, connect() as con:
        rows=con.execute('SELECT * FROM gmail_threads WHERE thread_id=? ORDER BY received_at',(thread_id,)).fetchall()
    return [dict(row) for row in rows]


def add_policy(content, actor, notes):
    import hashlib
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    with LOCK, connect() as con:
        existing=con.execute('SELECT * FROM policy_versions ORDER BY version DESC LIMIT 1').fetchone()
        if existing and existing['checksum'] == digest:
            return existing['version'],False
        cur=con.execute('INSERT INTO policy_versions(created_at,actor,content,notes,checksum) VALUES(?,?,?,?,?)',
                        (now(),actor,content,notes,digest))
        version=cur.lastrowid
    audit('policy_version_published',actor=actor,version=version,summary=notes,checksum=digest[:12])
    return version,True


def policy_history():
    with LOCK, connect() as con:
        rows=con.execute('SELECT version,created_at,actor,notes,checksum FROM policy_versions ORDER BY version DESC').fetchall()
    return [dict(r) for r in rows]


def policy_version(version):
    with LOCK, connect() as con:
        row=con.execute('SELECT * FROM policy_versions WHERE version=?',(version,)).fetchone()
    return dict(row) if row else None


def current_policy_version():
    with LOCK, connect() as con:
        row=con.execute('SELECT MAX(version) AS version FROM policy_versions').fetchone()
    return row['version'] or 0

# V3 additive migrations preserve existing cases, policies and audit entries.
def migrate_v3():
    with LOCK, connect() as con:
        fields = {r['name'] for r in con.execute('PRAGMA table_info(cases)')}
        for column, ddl in {
            'seen_at': 'TEXT', 'assigned_to': "TEXT NOT NULL DEFAULT ''",
            'internal_notes': "TEXT NOT NULL DEFAULT ''", 'review_reason': "TEXT NOT NULL DEFAULT ''",
            'last_processing_ms': 'INTEGER NOT NULL DEFAULT 0',
        }.items():
            if column not in fields:
                con.execute(f'ALTER TABLE cases ADD COLUMN {column} {ddl}')
        con.executescript('''
          CREATE TABLE IF NOT EXISTS case_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL,
            created_at TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS case_notes_email_idx ON case_notes(email_id,id);
          CREATE TABLE IF NOT EXISTS read_receipts (email_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS import_registry (
            provider TEXT NOT NULL, provider_id TEXT NOT NULL, email_id TEXT NOT NULL,
            imported_at TEXT NOT NULL, PRIMARY KEY(provider,provider_id)
          );
          CREATE TABLE IF NOT EXISTS app_settings (
            name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS send_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
            recipient TEXT NOT NULL, provider_message_id TEXT, error TEXT
          );
          CREATE INDEX IF NOT EXISTS send_attempts_case_idx ON send_attempts(email_id,id);
          CREATE TABLE IF NOT EXISTS gmail_threads (
            gmail_message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            email_id TEXT, sender TEXT, subject TEXT, received_at TEXT,
            snippet TEXT NOT NULL DEFAULT '', provider_status TEXT NOT NULL DEFAULT 'inbox'
          );
          CREATE INDEX IF NOT EXISTS gmail_threads_thread_idx ON gmail_threads(thread_id);
        ''')


def mark_opened(email_id):
    with LOCK, connect() as con:
        row = con.execute('SELECT seen_at FROM cases WHERE email_id=?', (email_id,)).fetchone()
        if row and not row['seen_at']:
            con.execute('UPDATE cases SET seen_at=? WHERE email_id=?', (now(), email_id))
        con.execute('INSERT OR IGNORE INTO read_receipts(email_id,seen_at) VALUES (?,?)', (email_id,now()))
    audit('operator_opened_email', email_id, actor='operator', result='read')


def update_assignment(email_id, department, explanation=''):
    if len(department) > 100 or not department.strip():
        raise ValueError('Choose a department (maximum 100 characters).')
    with LOCK, connect() as con:
        row = con.execute('SELECT status FROM cases WHERE email_id=?', (email_id,)).fetchone()
        if not row: raise ValueError('Analyze the email before assigning it.')
        con.execute('UPDATE cases SET assigned_to=?, revised_at=? WHERE email_id=?',
                    (department.strip(), now(), email_id))
    audit('case_assigned', email_id, actor='operator', department=department, explanation=explanation[:160])


def add_note(email_id, body):
    if not body.strip() or len(body) > 2000:
        raise ValueError('Note must be between 1 and 2000 characters.')
    with LOCK, connect() as con:
        if not con.execute('SELECT 1 FROM cases WHERE email_id=?', (email_id,)).fetchone():
            raise ValueError('Analyze the email before adding a note.')
        con.execute('INSERT INTO case_notes(email_id, created_at, author, text) VALUES (?,?,?,?)',
                    (email_id, now(), 'operator', body.strip()))
    audit('internal_note_added', email_id, actor='operator', note_length=len(body))


def notes(email_id):
    with LOCK, connect() as con:
        rows = con.execute('SELECT * FROM case_notes WHERE email_id=? ORDER BY id DESC',(email_id,)).fetchall()
    return [dict(row) for row in rows]


def review_decision(email_id, action, reason):
    if action not in ('reject', 'reopen'):
        raise ValueError('Unsupported review action')
    if len(reason.strip()) < 4:
        raise ValueError('Please explain your decision (at least four characters).')
    with LOCK, connect() as con:
        row = con.execute('SELECT status FROM cases WHERE email_id=?', (email_id,)).fetchone()
        if not row: raise ValueError('No analyzed case exists.')
        if row['status'] in ('sent','provider_accepted','sending','send_uncertain'): raise ValueError('A submitted or uncertain email cannot be reopened or rejected.')
        if action == 'reject' and row['status'] not in ('draft_ready','needs_review','approved'):
            raise ValueError('Only existing drafts can be rejected.')
        next_status = 'rejected' if action == 'reject' else 'needs_review'
        con.execute('UPDATE cases SET status=?, review_reason=?, approved_at=NULL,revised_at=? WHERE email_id=?',
                    (next_status, reason[:500], now(), email_id))
    audit('case_'+action, email_id, actor='operator', reason=reason[:180])


def list_audit_since(after=0, limit=100):
    with LOCK, connect() as con:
        rows = con.execute('SELECT * FROM audit WHERE id>? ORDER BY id LIMIT ?', (after,limit)).fetchall()
    return [{**dict(r), 'detail': json.loads(r['detail'])} for r in rows]


def operational_stats():
    with LOCK, connect() as con:
        statuses = con.execute('SELECT status, COUNT(*) n FROM cases GROUP BY status').fetchall()
        days = con.execute('''SELECT substr(happened_at,1,10) as day, COUNT(*) n FROM audit
                              WHERE action='ai_draft_created' GROUP BY day ORDER BY day DESC LIMIT 14''').fetchall()
        failures = con.execute('''SELECT COUNT(*) FROM audit WHERE action LIKE '%failed%' OR action LIKE '%error%' ''').fetchone()[0]
        retries = con.execute("SELECT COUNT(*) FROM audit WHERE action='agent_api_retry'").fetchone()[0]
        assignments = con.execute("SELECT assigned_to, count(*) n FROM cases WHERE assigned_to!='' GROUP BY assigned_to").fetchall()
    return {'case_statuses': {r['status']:r['n'] for r in statuses},
            'daily_activity': list(reversed([dict(r) for r in days])),
            'failures': failures, 'retries': retries,
            'assignments': [dict(r) for r in assignments]}


def imported(provider, provider_id):
    with LOCK, connect() as con:
        return con.execute('SELECT 1 FROM import_registry WHERE provider=? AND provider_id=?',
                           (provider,provider_id)).fetchone() is not None


def register_import(provider, provider_id, email_id):
    with LOCK, connect() as con:
        con.execute('INSERT OR IGNORE INTO import_registry VALUES (?,?,?,?)',
                    (provider,provider_id,email_id,now()))


def seen_emails():
    with LOCK, connect() as con:
        rows=con.execute('SELECT email_id,seen_at FROM read_receipts').fetchall()
    return {row['email_id']:row['seen_at'] for row in rows}


def delivery_setting():
    """Opt-in operator setting; no OAuth credentials are persisted."""
    with LOCK, connect() as con:
        row=con.execute("SELECT value FROM app_settings WHERE name='live_delivery'").fetchone()
    return bool(row and row['value']=='1')


def set_delivery_setting(enabled, actor='admin'):
    with LOCK, connect() as con:
        con.execute("INSERT INTO app_settings(name,value,updated_at) VALUES('live_delivery',?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    ('1' if enabled else '0',now()))
    audit('delivery_setting_changed',actor=actor,enabled=bool(enabled))
