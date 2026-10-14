"""Local FastAPI control room for the autonomous Student Helpdesk Triage Agent.

Binds to 127.0.0.1; Gmail sending requires enterprise login, explicit enablement,
an approved draft and a confirmed Google API send response.
API key stays in memory and is only forwarded to Google's Gemini API.
"""
from __future__ import annotations

import json
import os
import re
import threading
import shutil
import secrets
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent
import security
import storage
import quality
import delivery
from gmail_oauth import gmail
from fastapi.responses import RedirectResponse

ROOT = Path(__file__).resolve().parent
# Mutable files go on the Railway persistent volume when AEGIS_DATA_DIR is set.
DATA_HOME = Path(os.environ['AEGIS_DATA_DIR']).expanduser().resolve() if os.environ.get('AEGIS_DATA_DIR') else ROOT
INBOX = DATA_HOME / 'data' / 'inbox'
POLICY = DATA_HOME / 'data' / 'policy.md'
OUTBOX = DATA_HOME / 'outbox.json'
LOGS = DATA_HOME / 'agent_log.jsonl'
if DATA_HOME != ROOT:
    INBOX.mkdir(parents=True, exist_ok=True)
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    if not POLICY.exists() and (ROOT / 'data' / 'policy.md').exists():
        shutil.copy2(ROOT / 'data' / 'policy.md', POLICY)
    if os.environ.get('AEGIS_SEED_SAMPLE_DATA', '0') == '1' and not list(INBOX.glob('*.json')):
        for sample in (ROOT / 'data' / 'inbox').glob('*.json'):
            shutil.copy2(sample, INBOX / sample.name)
# Public deployments must have authentication enabled at startup.
if os.environ.get('AEGIS_PUBLIC_URL') and len(os.environ.get('AEGIS_ADMIN_PASSWORD', '')) < 12:
    raise RuntimeError('Set AEGIS_ADMIN_PASSWORD to a strong password (12+ chars) before public deployment.')
STATIC = ROOT / 'static'
storage.initialize()
storage.recover_incomplete_sends()
if not storage.current_policy_version() and POLICY.exists():
    storage.add_policy(POLICY.read_text(encoding='utf-8'), 'initial_setup', 'Competition starter policy')
SENDING_NOW = set()
GMAIL_SYNC_LOCK = threading.Lock()
GMAIL_AUTO_WORKER = None

app = FastAPI(title='Aegis — Student Helpdesk Intelligence', version='1.0.0', docs_url='/api/docs', redoc_url=None)
app.mount('/static', StaticFiles(directory=str(STATIC)), name='static')
mutex = threading.RLock()
state = {
    'running': False, 'progress': 0, 'total': 0, 'processed': 0,
    'current': '', 'stage':'idle', 'last_error': '', 'started_at': None, 'finished_at': None,
    'model': os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash'),
    'last_run_count': 0
}


@app.middleware('http')
async def safe_origin(request: Request, call_next):
    # Read-only GET is public only to the local computer; mutating browser requests
    # must originate from our localhost web app, not a random website.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin', '')
        allowed_origins = {'http://127.0.0.1:8765', 'http://localhost:8765'}
        if os.environ.get('AEGIS_PUBLIC_URL'):
            allowed_origins.add(os.environ['AEGIS_PUBLIC_URL'].rstrip('/'))
        if origin and origin not in allowed_origins:
            return JSONResponse({'detail': 'Request blocked: cross-origin write'}, status_code=403)
    if request.url.path.startswith('/api/') and request.url.path not in ('/api/session','/api/login','/api/gmail/callback','/api/health') and not security.authenticated(request):
        return JSONResponse({'detail':'Administrator sign-in required.'}, status_code=401)
    admin_actions = ((request.url.path.startswith('/api/policy') and request.method not in ('GET','HEAD')) or
                     (request.url.path.startswith('/api/gmail') and (request.method not in ('GET','HEAD') or request.url.path=='/api/gmail/authorize')) or
                     request.url.path in ('/api/credentials','/api/model') or
                     ((request.url.path.endswith('/send') or request.url.path.endswith('/retry-send')) and request.method=='POST'))
    if security.protected() and admin_actions and security.role(request)!='admin':
        return JSONResponse({'detail':'Only an administrator can perform this action.'},status_code=403)
    return await call_next(request)


class Credential(BaseModel):
    api_key: str = Field(min_length=10, max_length=250)
    model: str = 'gemini-2.5-flash'


class ModelRequest(BaseModel):
    model: str = Field(min_length=3, max_length=80)


class RunRequest(BaseModel):
    only: Optional[str] = None


class AddEmail(BaseModel):
    email_id: Optional[str] = Field(default=None, max_length=80)
    from_name: str = Field(default='Student', max_length=120)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=15000)
    from_email: str = Field(default='', max_length=320)
    attachments: list[dict] = Field(default_factory=list, max_length=20)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_outbox():
    try:
        return json.loads(OUTBOX.read_text(encoding='utf-8')) if OUTBOX.is_file() else []
    except (OSError, ValueError):
        return []


def read_emails():
    if not INBOX.is_dir():
        return []
    messages = agent.load_emails(INBOX)
    # Output provides normalized shape so imported files work like the supplied set.
    for email in messages:
        email.pop('source_file', None)
    return messages


def status_snapshot():
    with mutex:
        result = dict(state)
        result['has_key'] = bool(os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'))
    result['inbox_count'] = len(list(INBOX.glob('*.json'))) if INBOX.exists() else 0
    result['output_count'] = len(read_outbox())
    result['policy_version'] = storage.current_policy_version()
    result['admin_password_required'] = security.protected()
    result['gmail'] = gmail.status()
    result['policy_count'] = len(agent.extract_policy_codes(POLICY.read_text(encoding='utf-8'))) if POLICY.exists() else 0
    return result


def on_event(event):
    # Persist concise tool/event summaries in SQLite across multiple agent runs.
    details={k:v for k,v in event.items() if k not in ('event','timestamp','email_id','body','reply_draft','api_key')}
    storage.audit('agent_'+str(event.get('event','unknown')), event.get('email_id'), **details)
    with mutex:
        if event['event'] == 'progress':
            state['current'] = event.get('email_id', '')
            state['stage'] = 'reading email'
        elif event['event'] in ('plan','policy_lookup','decision','validated'):
            state['stage'] = {'plan':'planning response','policy_lookup':'checking policy','decision':'composing draft','validated':'validating policy and safety'}[event['event']]
        elif event['event'] == 'email_saved':
            state['stage'] = 'draft saved'
            state['processed'] += 1
            state['progress'] = round(100 * state['processed'] / max(1, state['total']))
            for result in read_outbox():
                if result.get('email_id') == event.get('email_id'):
                    storage.save_ai_result(result, storage.current_policy_version())
                    break
        elif event['event'] == 'processing_error':
            state['last_error'] = event.get('message', '')


def background_run(only):
    try:
        previous_results = read_outbox() if only else []
        # Flush previous output before a new run; don't display stale results.
        if OUTBOX.exists():
            OUTBOX.unlink()
        agent.emit.callback = on_event
        result = agent.run(INBOX, POLICY, OUTBOX, LOGS, state['model'], only=only)
        if only and previous_results:
            combined = {r['email_id']:r for r in previous_results}
            combined.update({r['email_id']:r for r in result})
            temp = OUTBOX.with_suffix('.json.tmp')
            temp.write_text(json.dumps(list(combined.values()), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(OUTBOX)
        storage.audit('agent_run_finished',count=len(result),model=state['model'],filter_email=only)
        with mutex:
            state['last_run_count'] = len(result)
            state['progress'] = 100
    except Exception as exc:
        with mutex:
            state['last_error'] = str(exc)[:600]
        agent.LOG.exception('Run failed')
        storage.audit('agent_run_failed',error=str(exc)[:200])
    finally:
        agent.emit.callback = None
        agent.emit.log_file = None
        with mutex:
            state['running'] = False
            state['current'] = ''
            state['stage'] = 'finished'
            state['finished_at'] = stamp()


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/status')
def get_status():
    return status_snapshot()


@app.get('/api/overview')
def overview():
    emails = read_emails()
    results = read_outbox()
    priorities = Counter(r.get('priority', 'other') for r in results)
    categories = Counter(r.get('category', 'other') for r in results)
    return {
        'emails': emails, 'results': results,
        'cases': storage.all_cases(), 'seen':storage.seen_emails(),
        'stats': {
            'inbox': len(emails), 'processed': len(results),
            'escalations': sum(1 for r in results if r.get('escalate')),
            'high_priority': priorities.get('high', 0),
            'average_confidence': round(sum(float(r.get('confidence', 0)) for r in results) / max(1, len(results)) * 100),
            'priorities': dict(priorities), 'categories': dict(categories),
            'provider_accepted': sum(c['status'] in ('sent','provider_accepted') for c in storage.all_cases().values()),
            'send_failed': sum(c['status']=='send_failed' for c in storage.all_cases().values()),
            'delivery_uncertain': sum(c['status']=='send_uncertain' for c in storage.all_cases().values()),
        },
        'status': status_snapshot()
    }


@app.get('/api/policy')
def policy():
    if not POLICY.exists():
        raise HTTPException(404, 'Policy file missing')
    text = POLICY.read_text(encoding='utf-8')
    sections = []
    heading = 'Introduction'
    for block in re.split(r'(?=^## )', text, flags=re.MULTILINE):
        if not block.strip():
            continue
        lines = block.splitlines()
        if lines[0].startswith('## '):
            heading = lines[0][3:].strip()
            content = '\n'.join(lines[1:])
        else:
            content = block
        rules = []
        for m in re.finditer(r'^\s*-\s*((?:FEE|HOSTEL|ACAD|TECH|ESC)-\d+):\s*(.*?)(?=\n\s*-\s*(?:FEE|HOSTEL|ACAD|TECH|ESC)-\d+:|\Z)', content, re.M | re.S):
            rules.append({'code': m.group(1), 'text': ' '.join(m.group(2).strip().split())})
        sections.append({'name': heading, 'rules': rules, 'content': content.strip()})
    return {'sections': sections, 'raw': text, 'count': len(agent.extract_policy_codes(text)),
            'version': storage.current_policy_version(), 'history': storage.policy_history()}


@app.get('/api/logs')
def logs():
    if not LOGS.exists():
        return {'events': []}
    result = []
    # Small log files; bound output to most recent 350 events.
    for line in LOGS.read_text(encoding='utf-8', errors='replace').splitlines()[-350:]:
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return {'events': result}


@app.post('/api/credentials')
def credentials(data: Credential):
    if not re.fullmatch(r'[a-zA-Z0-9._-]{3,80}', data.model):
        raise HTTPException(400, 'Model name must use letters, numbers, dots, dashes or underscores')
    with mutex:
        if state['running']:
            raise HTTPException(409, 'Please wait until current run finishes')
        os.environ['GEMINI_API_KEY'] = data.api_key.strip()
        state['model'] = data.model
    return {'ok': True, 'message': 'Key loaded into memory (not saved on disk).'}


@app.post('/api/model')
def model(data: ModelRequest):
    if not re.fullmatch(r'[a-zA-Z0-9._-]{3,80}', data.model):
        raise HTTPException(400, 'Invalid model name')
    with mutex:
        if state['running']:
            raise HTTPException(409, 'Cannot change model during run')
        state['model'] = data.model
    return {'ok': True}


@app.post('/api/run')
def start(data: RunRequest):
    with mutex:
        if state['running']:
            raise HTTPException(409, 'Agent is already running')
        if not (os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')):
            raise HTTPException(400, 'Connect Gemini API key in Settings first')
        try:
            messages = read_emails()
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        if data.only:
            messages = [x for x in messages if x['email_id'] == data.only]
            if not messages:
                raise HTTPException(404, 'Email ID not found')
        if not messages or not POLICY.is_file():
            raise HTTPException(400, 'Inbox or policy missing')
        state.update(running=True, progress=0, total=len(messages), processed=0, stage='preparing',
                     current='', last_error='', last_run_count=0,
                     started_at=stamp(), finished_at=None)
        thread = threading.Thread(target=background_run, args=(data.only,), daemon=True)
        thread.start()
    return {'ok': True, 'message': 'Autonomous triage started', 'total': len(messages)}


@app.post('/api/emails')
def add_email(item: AddEmail):
    with mutex:
        if state['running']:
            raise HTTPException(409, 'Cannot change inbox while an agent run is active')
        INBOX.mkdir(parents=True, exist_ok=True)
        eid = (item.email_id or f'E{max([int(m.group(1)) for p in INBOX.glob("E*.json") if (m := re.fullmatch(r"E(\d+)", p.stem))] or [0])+1:03d}').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', eid):
            raise HTTPException(400, 'Invalid email ID')
        path = INBOX / f'{eid}.json'
        if path.exists():
            raise HTTPException(409, f'Email {eid} already exists')
        payload = {'id': eid, 'from_name': item.from_name, 'from_email': item.from_email,
                   'subject': item.subject, 'body': item.body, 'received_at': stamp(),
                   'attachments': [{'name':str(a.get('name','Attachment'))[:140], 'size':max(0,int(a.get('size',0))) if str(a.get('size',0)).isdigit() else 0} for a in item.attachments if isinstance(a,dict)]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    storage.audit('email_imported',eid,actor='operator',source='manual',subject=item.subject[:100])
    return {'ok': True, 'email_id': eid}


@app.post('/api/import')
async def import_emails(request: Request):
    # Accept raw JSON body (single item or array), no multipart dependency needed.
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid JSON')
    items = body if isinstance(body, list) else [body]
    if not 1 <= len(items) <= 100:
        raise HTTPException(400, 'Import must contain 1-100 emails')
    added, errors = [], []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f'Entry {idx+1}: not an object')
            continue
        try:
            parsed = AddEmail(email_id=str(item.get('email_id') or item.get('id') or '') or None,
                              from_name=str(item.get('from_name') or item.get('sender_name') or 'Student'),
                              subject=str(item.get('subject') or ''),
                              body=str(item.get('body') or item.get('message') or ''),
                              from_email=str(item.get('from_email') or ''),
                              attachments=item.get('attachments') if isinstance(item.get('attachments'),list) else [])
            result = add_email(parsed)
            added.append(result['email_id'])
        except Exception as exc:
            errors.append(f'Entry {idx+1}: {str(exc)[:130]}')
    return {'added': added, 'errors': errors}


@app.get('/api/download/outbox')
def download_outbox():
    if not OUTBOX.exists():
        raise HTTPException(404, 'Run the agent to generate outbox.json')
    return FileResponse(OUTBOX, filename='outbox.json', media_type='application/json')


@app.get('/api/download/logs')
def download_logs():
    if not LOGS.exists():
        raise HTTPException(404, 'Run the agent to generate logs')
    return FileResponse(LOGS, filename='agent_log.jsonl', media_type='application/x-ndjson')


@app.get('/api/health')
def health():
    cases=storage.all_cases()
    return {'ok': True, 'service': 'Aegis Local Helpdesk', 'time': stamp(),
            'emails_sent': sum(c['status']=='sent' for c in cases.values())}


# --- Operator workflow, policy governance, Gmail OAuth ---
class DraftChange(BaseModel):
    draft: str = Field(min_length=1, max_length=15000)


class PolicyChange(BaseModel):
    content: str = Field(min_length=60, max_length=120000)
    notes: str = Field(min_length=4, max_length=600)


class GmailSettings(BaseModel):
    client_id: str = Field(min_length=20, max_length=256)
    client_secret: str = Field(min_length=5, max_length=256)


class GmailFetch(BaseModel):
    limit: int = Field(default=10, ge=1, le=30)


class GmailSendConfirmation(BaseModel):
    confirmation: str


class GmailDeliveryToggle(BaseModel):
    enabled: bool


class SendRetry(BaseModel):
    reason: str = Field(min_length=8,max_length=400)


class GmailSyncRequest(BaseModel):
    limit: int = Field(default=30, ge=1, le=100)


def permitted_id(eid):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', eid):
        raise HTTPException(400, 'Invalid email identifier.')
    return eid


def known_email(eid):
    permitted_id(eid)
    for email in read_emails():
        if email['email_id'] == eid:
            return email
    raise HTTPException(404, 'Email not found in inbox.')


def operational_error(exc):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(503, 'External provider unavailable. Check the audit trail and retry.') from exc


@app.get('/api/audit')
def audit_all(email_id: Optional[str] = None):
    if email_id:
        permitted_id(email_id)
    return {'events': storage.list_audit(700, email_id)}


@app.post('/api/cases/{email_id}/open')
def case_open(email_id: str):
    email = known_email(email_id)
    storage.mark_opened(email_id)
    return {'case': storage.case(email_id), 'ok': True}


@app.put('/api/cases/{email_id}/draft')
def case_draft(email_id: str, payload: DraftChange):
    known_email(email_id)
    try:
        storage.edit_draft(email_id, payload.draft.strip())
    except Exception as exc:
        operational_error(exc)
    return {'ok': True, 'case': storage.case(email_id), 'message': 'Draft saved; approval is required again.'}


@app.post('/api/cases/{email_id}/approve')
def case_approve(email_id: str):
    known_email(email_id)
    record=storage.case(email_id)
    if record and record['policy_version'] < storage.current_policy_version():
        raise HTTPException(409, 'Policy has changed since this draft was created. Re-analyze this email before approval.')
    try:
        storage.approve(email_id)
    except Exception as exc:
        operational_error(exc)
    return {'ok': True, 'case': storage.case(email_id), 'message': 'Approved for manual review/delivery; not sent.'}


@app.post('/api/policy')
def publish_policy(change: PolicyChange):
    with mutex:
        if state['running']:
            raise HTTPException(409, 'Wait for the current agent run to finish before changing its policy.')
        content=change.content.strip()+'\n'
        matches=re.findall(r'^\s*-\s*((?:FEE|HOSTEL|ACAD|TECH|ESC)-\d+):',content,re.MULTILINE)
        if len(matches)<5 or len(matches)!=len(set(matches)):
            raise HTTPException(400, 'Policy must contain at least 5 uniquely coded rules (e.g. FEE-1, ESC-1). No duplicate codes.')
        if not all(code in matches for code in ('ESC-1','ESC-2','ESC-3','ESC-6')):
            raise HTTPException(400, 'Critical emergency/security rules ESC-1, ESC-2, ESC-3 and ESC-6 cannot be removed.')
        temp=POLICY.with_suffix('.md.tmp')
        temp.write_text(content,encoding='utf-8')
        temp.replace(POLICY)
        version,changed=storage.add_policy(content, 'operator', change.notes)
    return {'ok':True,'version':version,'changed':changed,'count':len(matches),
            'message':'New policy published. Existing drafts require re-analysis before approval.'}


@app.get('/api/policy/versions/{version}')
def policy_by_version(version: int):
    record=storage.policy_version(version)
    if not record:
        raise HTTPException(404, 'Policy version does not exist.')
    return record


@app.get('/api/gmail/status')
def gmail_status():
    return gmail.status()


@app.post('/api/gmail/delivery')
def gmail_delivery_toggle(payload: GmailDeliveryToggle):
    if os.environ.get('AEGIS_ENTERPRISE_MODE')!='1':
        raise HTTPException(403,'Launch RUN.bat in enterprise mode to configure live delivery.')
    if not security.protected():
        raise HTTPException(403,'Administrator authentication is required to change delivery permissions.')
    if payload.enabled and not gmail.status()['connected']:
        raise HTTPException(409,'Connect Gmail with the requested permissions before enabling delivery.')
    if payload.enabled and not gmail.status()['send_permission_granted']:
        raise HTTPException(409,'Reconnect Gmail and grant gmail.send permission before enabling delivery.')
    storage.set_delivery_setting(payload.enabled,actor='admin')
    return {'ok':True,'gmail':gmail.status()}


@app.post('/api/gmail/configure')
def gmail_configure(config: GmailSettings):
    try:
        gmail.configure(config.client_id,config.client_secret)
        storage.audit('gmail_oauth_configured',actor='operator')
    except Exception as exc:
        operational_error(exc)
    return {'ok':True,'status':gmail.status()}


@app.get('/api/gmail/authorize')
def gmail_authorize():
    try:
        return {'url':gmail.authorization_url()}
    except Exception as exc:
        operational_error(exc)


@app.get('/api/gmail/callback')
def gmail_callback(code: str = '', state: str = '', error: str = ''):
    if error:
        storage.audit('gmail_oauth_denied',error=error[:100])
        return RedirectResponse('/?gmail=denied',status_code=303)
    try:
        account=gmail.callback(code,state)
        start_gmail_auto_worker()
        storage.audit('gmail_oauth_connected',actor='operator',account=account)
        return RedirectResponse('/?gmail=connected',status_code=303)
    except Exception as exc:
        storage.audit('gmail_oauth_failed',error=str(exc)[:200])
        return RedirectResponse('/?gmail=failed',status_code=303)


@app.post('/api/gmail/disconnect')
def gmail_disconnect():
    account=gmail.account
    gmail.disconnect()
    storage.audit('gmail_disconnected',actor='operator',account=account)
    return {'ok':True}


def save_imported_gmail(emails, *, actor='operator'):
    imported=[];skipped=[]
    INBOX.mkdir(parents=True, exist_ok=True)
    for email in emails:
        mid=email.get('gmail_message_id','')
        eid=email.get('id','')
        if not mid or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',eid):
            skipped.append(eid);continue
        if (INBOX/(eid+'.json')).exists() or storage.imported('gmail',mid):
            skipped.append(eid)
            storage.register_thread_message(email)
            continue
        tmp=INBOX/(eid+'.json.tmp')
        tmp.write_text(json.dumps(email,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(INBOX/(eid+'.json'))
        storage.register_import('gmail',mid,eid)
        storage.register_thread_message(email)
        storage.audit('gmail_email_imported',eid,actor=actor,source_message_id=mid,thread_id=email.get('thread_id',''))
        imported.append(eid)
    return {'ok':True,'imported':imported,'skipped':skipped}


@app.post('/api/gmail/sync')
def gmail_sync(payload: GmailSyncRequest):
    if not GMAIL_SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409,'Another Gmail sync is in progress. Try again shortly.')
    try:
        with mutex:
            if state['running']:
                raise HTTPException(409,'Wait for active agent triage to finish before syncing Gmail.')
        if not gmail.status()['connected']:
            raise HTTPException(409,'Connect Gmail before syncing.')
        account=gmail.account or ''
        cursor=storage.sync_cursor(account)
        storage.audit('gmail_sync_started',actor='operator',has_cursor=bool(cursor),limit=payload.limit)
        try:
            fetched=gmail.sync_changes(cursor or '',payload.limit)
            result=save_imported_gmail(fetched['emails'])
            if fetched.get('cursor'):
                storage.update_sync_cursor(account,fetched['cursor'])
            storage.audit('gmail_sync_completed',actor='operator',mode=fetched['mode'],imported_count=len(result['imported']),skipped_count=len(result['skipped']))
            return {**result,'mode':fetched['mode'],'cursor_saved':bool(fetched.get('cursor'))}
        except Exception as exc:
            storage.audit('gmail_sync_failed',actor='operator',error_type=type(exc).__name__,message=str(exc)[:170])
            operational_error(exc)
    finally:
        GMAIL_SYNC_LOCK.release()


def start_gmail_auto_worker():
    """Poll the connected authorized Gmail inbox at 120-second intervals, not fake UI timers."""
    global GMAIL_AUTO_WORKER
    if GMAIL_AUTO_WORKER is not None and GMAIL_AUTO_WORKER.is_alive():return
    def worker():
        import time
        while True:
            time.sleep(120)
            if not gmail.status().get('connected'):continue
            try:
                gmail_sync(GmailSyncRequest(limit=40))
            except HTTPException as exc:
                if exc.status_code!=409:
                    storage.audit('gmail_autosync_skipped',actor='system',error=str(exc.detail)[:120])
            except Exception as exc:
                storage.audit('gmail_autosync_failed',actor='system',error_type=type(exc).__name__)
    GMAIL_AUTO_WORKER=threading.Thread(target=worker,name='aegis-gmail-inbox-sync',daemon=True)
    GMAIL_AUTO_WORKER.start()


@app.get('/api/gmail/threads/{thread_id}')
def gmail_thread(thread_id: str):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,150}',thread_id):
        raise HTTPException(400,'Invalid Gmail thread ID.')
    if not gmail.status()['connected']:
        return {'source':'cached','messages':storage.thread_messages(thread_id)}
    try:
        messages=gmail.fetch_thread(thread_id)
        return {'source':'gmail','messages':messages}
    except Exception as exc:
        storage.audit('gmail_thread_lookup_failed',error_type=type(exc).__name__,message=str(exc)[:150])
        return {'source':'cached','messages':storage.thread_messages(thread_id),'warning':'Gmail unavailable; showing cached thread metadata.'}


@app.get('/api/cases/{email_id}/delivery-history')
def delivery_history(email_id: str):
    known_email(email_id)
    return {'attempts':storage.send_history(email_id),'case':storage.case(email_id)}


@app.post('/api/cases/{email_id}/retry-send')
def retry_send(email_id:str,payload:SendRetry):
    known_email(email_id)
    try:storage.delivery_retry_authorize(email_id,payload.reason)
    except Exception as exc:operational_error(exc)
    return {'ok':True,'case':storage.case(email_id),
            'message':'Definite failure reset to approved. Review recipient and send again manually.'}


@app.get('/api/cases/{email_id}/quality')
def check_quality(email_id:str):
    email=known_email(email_id)
    record=storage.case(email_id)
    result=next((r for r in read_outbox() if r.get('email_id')==email_id),None)
    if not record or not result:raise HTTPException(409,'Analyze the email before quality review.')
    gate=quality.validate_reply(record,result,email,POLICY.read_text(encoding='utf-8'))
    storage.audit('quality_preview',email_id,actor='operator',passed=gate['passed'],issues=gate['errors'][:6])
    return gate


@app.post('/api/gmail/import')
def gmail_import(payload: GmailFetch):
    with mutex:
        if state['running']:
            raise HTTPException(409,'Wait until agent finishes before importing emails.')
    try:
        emails=gmail.fetch_messages(payload.limit)
    except Exception as exc:
        operational_error(exc)
    return save_imported_gmail(emails)


@app.post('/api/cases/{email_id}/send')
def send_case(email_id: str, payload: GmailSendConfirmation):
    if not security.protected():
        raise HTTPException(403,'Enterprise sending requires an authenticated administrator.')
    email=known_email(email_id)
    if payload.confirmation!='SEND':
        raise HTTPException(400,'Type SEND to explicitly confirm delivery.')
    if not gmail.status()['sending_enabled']:
        raise HTTPException(409,'Enable Live Email Delivery and reconnect Gmail with gmail.send permission.')
    with mutex:
        if state['running']:raise HTTPException(409,'Wait for agent triage to finish before sending.')
        if email_id in SENDING_NOW:raise HTTPException(409,'A send is already in progress.')
        record=storage.case(email_id)
        if not record or record['status']!='approved':
            raise HTTPException(409,'Approve the draft first. Failed or uncertain sends need explicit review.')
        if record['policy_version'] < storage.current_policy_version():
            raise HTTPException(409,'Policy changed; re-analyze and reapprove the draft before sending.')
        SENDING_NOW.add(email_id)
    attempt=None
    try:
        result=next((r for r in read_outbox() if r.get('email_id')==email_id),None)
        if not result:raise HTTPException(409,'Missing AI analysis; re-analyze before sending.')
        # Mandatory deterministic + separate LLM semantic check (fail closed).
        try: reviewer=agent.GeminiReasoner(state['model'])
        except Exception as exc:
            storage.audit('quality_gate_blocked',email_id,reason='Gemini quality reviewer unavailable')
            raise HTTPException(409,'Gemini quality reviewer unavailable. Configure a working key before live delivery.') from exc
        verdict=quality.validate_reply(record,result,email,POLICY.read_text(encoding='utf-8'),model=reviewer)
        storage.audit('quality_gate_checked',email_id,passed=verdict['passed'],method=verdict['method'],issues=verdict['errors'][:6])
        if not verdict['passed']:
            raise HTTPException(409,{'message':'Pre-send quality check did not pass. Review and correct draft before sending.',
                                     'issues':verdict['errors']})
        attempt=storage.begin_send(email_id,email.get('from_email',''))
        try:
            provider=gmail.send(email['from_email'],email['subject'],record['draft'],
                                email.get('thread_id',''),email.get('rfc_message_id',''),email.get('references',''))
            if not provider.get('id'):
                raise RuntimeError('Gmail returned no message ID; delivery outcome uncertain.')
        except Exception as exc:
            status,explanation=delivery.classify_delivery_exception(exc)
            storage.finish_send(email_id,attempt,state=status,error=explanation+' '+str(exc)[:150])
            if status=='send_uncertain':
                raise HTTPException(502,'Gmail outcome uncertain. Check Sent folder before trying anything else. Automated resend is blocked.') from exc
            raise HTTPException(502,'Gmail rejected the send. Check Delivery History for the reason; retry requires authorization.') from exc
        storage.finish_send(email_id,attempt,state='provider_accepted',message_id=provider['id'])
        return {'ok':True,'provider_message_id':provider['id'],'case':storage.case(email_id),
                'delivery_status':'provider_accepted','message':'Google accepted the message; recipient inbox delivery is not yet verified.'}
    finally:
        with mutex:SENDING_NOW.discard(email_id)


@app.get('/api/download/audit')
def download_audit():
    # Event export contains operational metadata, never raw message/draft bodies.
    return JSONResponse({'exported_at':stamp(),'events':storage.list_audit(2000)})

# --- V3 operator intelligence, streaming, authentication and history ---
from fastapi.responses import StreamingResponse, Response
from contextlib import suppress
import asyncio
import difflib
import io
import csv
import security


class LoginForm(BaseModel):
    password: str = Field(min_length=1, max_length=250)


class Assignment(BaseModel):
    department: str = Field(min_length=2, max_length=100)
    explanation: str = Field(default='', max_length=500)


class InternalNote(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ReviewAction(BaseModel):
    action: str
    reason: str = Field(min_length=4, max_length=500)


class ImportPolicy(BaseModel):
    filename: str = Field(min_length=3, max_length=140)
    content: str = Field(default='', max_length=120000)
    content_base64: str = Field(default='', max_length=210000)
    notes: str = Field(min_length=4, max_length=600)


@app.get('/api/session')
def session(request: Request):
    return {'required':security.protected(), 'authenticated':security.authenticated(request),'role':security.role(request),
            'local_only':not bool(os.environ.get('AEGIS_PUBLIC_URL')), 'sending_enabled':gmail.status()['sending_enabled']}


@app.post('/api/login')
def login(payload: LoginForm, request: Request, response: Response):
    if not security.protected():
        return {'ok':True, 'message':'Local demonstration mode: password not required.'}
    try:
        token=security.login(payload.password, request.client.host if request.client else 'local')
    except ValueError as exc:
        raise HTTPException(401,str(exc))
    response.set_cookie(security.COOKIE,token,httponly=True,samesite='strict',secure=os.environ.get('AEGIS_PUBLIC_URL','').startswith('https://'),max_age=28800,path='/')
    storage.audit('operator_logged_in',actor='operator',outcome='success')
    return {'ok':True}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    security.logout(request.cookies.get(security.COOKIE,''))
    response.delete_cookie(security.COOKIE,path='/')
    return {'ok':True}


@app.get('/api/cases/{email_id}/notes')
def case_notes(email_id:str):
    known_email(email_id)
    return {'notes':storage.notes(email_id)}


@app.post('/api/cases/{email_id}/notes')
def post_note(email_id:str, data:InternalNote):
    known_email(email_id)
    try:storage.add_note(email_id,data.text)
    except Exception as exc: operational_error(exc)
    return {'ok':True,'notes':storage.notes(email_id)}


@app.put('/api/cases/{email_id}/assignment')
def assign_case(email_id:str, data:Assignment):
    known_email(email_id)
    try:storage.update_assignment(email_id,data.department,data.explanation)
    except Exception as exc: operational_error(exc)
    return {'ok':True,'case':storage.case(email_id)}


@app.post('/api/cases/{email_id}/review')
def review_case(email_id:str, data:ReviewAction):
    known_email(email_id)
    try:storage.review_decision(email_id,data.action,data.reason)
    except Exception as exc: operational_error(exc)
    return {'ok':True,'case':storage.case(email_id)}


@app.get('/api/analytics')
def analytics():
    results=read_outbox()
    cases=storage.all_cases()
    stats=storage.operational_stats()
    escalations=sum(bool(r.get('escalate')) for r in results)
    statuses=stats['case_statuses']
    timestamps=storage.list_audit(2000)
    run_starts=[r for r in timestamps if r['action']=='agent_run_started']
    return {'processed':len(results), 'total_cases':len(cases), 'high_priority':sum(r.get('priority')=='high' for r in results),
            'escalations':escalations, 'escalation_rate':round(escalations / max(1,len(results))*100,1),
            'categories':dict(Counter(r.get('category','other') for r in results)),
            'priorities':dict(Counter(r.get('priority','other') for r in results)),
            'status':statuses, 'approved':statuses.get('approved',0),'sent':statuses.get('sent',0),
            'human_review':statuses.get('needs_review',0),'rejected':statuses.get('rejected',0),
            'failures':stats['failures'], 'retries':stats['retries'],
            'daily_activity':stats['daily_activity'],'assignments':stats['assignments'],
            'avg_confidence':round(sum(float(r.get('confidence',0)) for r in results)/max(1,len(results)),3),
            'average_process_ms':round(sum(c.get('last_processing_ms',0) for c in cases.values()) /max(1,sum(1 for c in cases.values() if c.get('last_processing_ms')))),
            'provider_deliveries':sum(bool(c.get('sent_message_id')) for c in cases.values())}


@app.get('/api/events')
async def events(request: Request, after: int=0):
    """Real server-originated operational events. No invented stages or timers."""
    cursor=max(0,after)
    last_header=request.headers.get('last-event-id','')
    if last_header.isdigit(): cursor=max(cursor,int(last_header))
    async def generate():
        nonlocal cursor
        yield ': connected to persistent event stream\n\n'
        for tick in range(300):  # reconnect naturally after 5 minutes
            if await request.is_disconnected():
                break
            rows=await asyncio.to_thread(storage.list_audit_since,cursor,100)
            for row in rows:
                cursor=row['id']
                yield f"id: {cursor}\nevent: audit\ndata: {json.dumps(row,ensure_ascii=False)}\n\n"
            if tick%15==0:
                yield ': keepalive\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={
        'Cache-Control':'no-cache,no-transform','X-Accel-Buffering':'no'})


@app.get('/api/policy/diff')
def policy_diff(old:int,new:int):
    before=storage.policy_version(old);after=storage.policy_version(new)
    if not before or not after:raise HTTPException(404,'Both policy versions must exist.')
    diff='\n'.join(difflib.unified_diff(before['content'].splitlines(),after['content'].splitlines(),
                     fromfile=f'policy v{old}',tofile=f'policy v{new}',lineterm=''))
    return {'from':old,'to':new,'diff':diff,'changed':bool(diff)}


@app.post('/api/policy/import-text')
def policy_import(data:ImportPolicy):
    suffix=Path(data.filename).suffix.lower()
    if suffix in ('.md','.txt'):
        content=data.content
    elif suffix=='.docx':
        import base64, zipfile, xml.etree.ElementTree as ET
        from io import BytesIO
        try:
            raw=base64.b64decode(data.content_base64,validate=True)
            if len(raw)>120000:raise ValueError('Word file exceeds 120 KB')
            with zipfile.ZipFile(BytesIO(raw)) as z:
                root=ET.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            lines=[]
            for para in root.findall('.//w:p',ns):
                value=''.join(t.text or '' for t in para.findall('.//w:t',ns)).strip()
                if not value:continue
                if re.match(r'^(?:FEE|HOSTEL|ACAD|TECH|ESC)-\d+:',value):value='- '+value
                elif value.lower() in ('fees','hostel','academics','technical','escalation rules','reply rules'):
                    value='## '+value
                lines.append(value)
            content='\n'.join(lines)
        except Exception as exc:
            raise HTTPException(400,'Cannot read Word document. Upload an unencrypted .docx with selectable text.') from exc
    else:
        raise HTTPException(400,'Supported policy formats: .md, .txt, .docx. Convert PDF to editable text first.')
    return publish_policy(PolicyChange(content=content,notes=data.notes))


@app.get('/api/download/audit.csv')
def audit_csv():
    buffer=io.StringIO()
    writer=csv.writer(buffer)
    writer.writerow(['event_id','timestamp','case_id','action','actor','detail_json'])
    for r in reversed(storage.list_audit(5000)):
        writer.writerow([r['id'],r['happened_at'],r['email_id'],r['action'],r['actor'],json.dumps(r['detail'],ensure_ascii=False)])
    return Response(content=buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="aegis_audit.csv"'})
