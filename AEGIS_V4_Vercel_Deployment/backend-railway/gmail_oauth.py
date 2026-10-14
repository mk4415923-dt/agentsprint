"""Optional, in-memory OAuth 2 Gmail integration using only Python stdlib.
No email is sent automatically. Use OAuth with the operator's own TEST Gmail account.
"""
import base64
import json
import os
import secrets
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timezone
from email.utils import parseaddr

READ_SCOPE='https://www.googleapis.com/auth/gmail.readonly'
SEND_SCOPE='https://www.googleapis.com/auth/gmail.send'
ENTERPRISE_MODE=os.environ.get('AEGIS_ENTERPRISE_MODE')=='1'
REDIRECT=os.environ.get('AEGIS_GOOGLE_REDIRECT_URI', 'http://127.0.0.1:8765/api/gmail/callback')

class GmailConnection:
    def __init__(self):
        self.client_id=os.environ.get('AEGIS_GOOGLE_CLIENT_ID','')
        self.client_secret=os.environ.get('AEGIS_GOOGLE_CLIENT_SECRET','')
        self.oauth_state=None
        self.access_token=None
        self.refresh_token=None
        self.expires_at=0
        self.account=None
        self.granted_scopes=set()

    def configure(self, client_id, secret):
        if not client_id.endswith('.apps.googleusercontent.com') or not secret:
            raise ValueError('Provide a valid Google OAuth client ID and secret from Google Cloud Console.')
        self.client_id=client_id.strip()
        self.client_secret=secret.strip()
        self.disconnect()

    def disconnect(self):
        self.access_token=None; self.refresh_token=None; self.expires_at=0; self.account=None; self.oauth_state=None
        self.granted_scopes=set()

    def status(self):
        import storage
        enabled=bool(ENTERPRISE_MODE and storage.delivery_setting())
        allowed=SEND_SCOPE in self.granted_scopes
        return {'configured':bool(self.client_id and self.client_secret),
                'connected':bool(self.access_token or self.refresh_token),
                'account':self.account or '',
                'enterprise_mode':ENTERPRISE_MODE,
                'delivery_requested':storage.delivery_setting() if ENTERPRISE_MODE else False,
                'send_permission_granted':allowed,
                'sending_enabled':enabled and allowed and bool(self.access_token or self.refresh_token),
                'redirect_uri':REDIRECT}

    def authorization_url(self):
        if not self.client_id or not self.client_secret:
            raise ValueError('First configure Gmail OAuth credentials in Settings.')
        self.oauth_state=secrets.token_urlsafe(32)
        query=urllib.parse.urlencode({'client_id':self.client_id,'redirect_uri':REDIRECT,
          'response_type':'code','access_type':'offline','prompt':'consent',
          'scope':READ_SCOPE+((' '+SEND_SCOPE) if ENTERPRISE_MODE else ''),'state':self.oauth_state})
        return 'https://accounts.google.com/o/oauth2/v2/auth?'+query

    def post_token(self,data):
        req=urllib.request.Request('https://oauth2.googleapis.com/token',
          data=urllib.parse.urlencode(data).encode(),
          headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
        return self.call(req)

    def call(self,req):
        try:
            with urllib.request.urlopen(req,timeout=22) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raw=e.read(1000).decode('utf-8','replace')
            try: desc=json.loads(raw).get('error_description') or json.loads(raw).get('error',{}).get('message') or raw
            except (ValueError,AttributeError): desc=raw
            raise ValueError(f'Google API returned HTTP {e.code}: {desc}') from e
        except urllib.error.URLError as e:
            raise ValueError('Could not reach Google API. Check internet and firewall.') from e

    def callback(self,code,state):
        if not self.oauth_state or not secrets.compare_digest(str(state),self.oauth_state):
            raise ValueError('Login session expired or security state did not match. Try Connect again.')
        self.oauth_state=None
        d=self.post_token({'code':code,'client_id':self.client_id,
          'client_secret':self.client_secret,'redirect_uri':REDIRECT,'grant_type':'authorization_code'})
        self.access_token=d['access_token']; self.refresh_token=d.get('refresh_token')
        self.granted_scopes=set(d.get('scope','').split())
        self.expires_at=time.time()+int(d.get('expires_in',3500))-90
        profile=self.api('GET','/profile')
        self.account=profile.get('emailAddress','')
        return self.account

    def token(self):
        if self.access_token and time.time()<self.expires_at: return self.access_token
        if self.refresh_token:
            d=self.post_token({'client_id':self.client_id,'client_secret':self.client_secret,
                               'refresh_token':self.refresh_token,'grant_type':'refresh_token'})
            self.access_token=d['access_token'];self.expires_at=time.time()+int(d.get('expires_in',3500))-90
            return self.access_token
        raise ValueError('Gmail is not connected. Connect in Settings first.')

    def api(self,method,path,payload=None):
        url='https://gmail.googleapis.com/gmail/v1/users/me'+path
        body=None if payload is None else json.dumps(payload).encode()
        req=urllib.request.Request(url,data=body,method=method,headers={
            'Authorization':'Bearer '+self.token(), 'Content-Type':'application/json'})
        if method != 'GET':
            # Gmail SEND is deliberately never automatically retried: a timeout
            # could occur after Google accepted the message.
            return self.call(req)
        for attempt in range(3):
            try:
                return self.call(req)
            except ValueError as exc:
                reason=str(exc).lower()
                temporary=any(token in reason for token in ('http 429','http 500','http 502','http 503','http 504',
                                                               'could not reach google'))
                if not temporary or attempt==2:raise
                import storage
                storage.audit('gmail_read_retry',actor='system',attempt=attempt+1,request='GET',
                              error_type='temporary_google_response')
                time.sleep(0.75*(2**attempt))

    def parse_message(self, msg):
        headers={h['name'].lower():h['value'] for h in msg.get('payload',{}).get('headers',[])}
        sender_name,sender_address=parseaddr(headers.get('from',''))
        def text_part(part):
            if part.get('mimeType')=='text/plain' and part.get('body',{}).get('data'):
                try:return base64.urlsafe_b64decode(part['body']['data']+'===').decode('utf-8','replace')
                except (ValueError,TypeError):return ''
            for item in part.get('parts',[]):
                value=text_part(item)
                if value:return value
            return ''
        def attachments(payload):
            found=[]
            if payload.get('filename'):
                found.append({'name':payload['filename'][:140], 'mime_type':payload.get('mimeType',''),
                              'size':payload.get('body',{}).get('size',0)})
            for part in payload.get('parts',[]):found.extend(attachments(part))
            return found[:20]
        return {'id':'G'+msg['id'], 'from_name':sender_name or sender_address or 'Gmail Sender',
                'from_email':sender_address, 'subject':headers.get('subject','(No subject)'),
                'body':text_part(msg.get('payload',{})) or msg.get('snippet',''),
                'gmail_message_id':msg['id'],'thread_id':msg.get('threadId',''),
                'rfc_message_id':headers.get('message-id',''),
                'references':headers.get('references',''),
                'received_at':datetime.fromtimestamp(int(msg.get('internalDate',0))/1000,timezone.utc).isoformat() if msg.get('internalDate') else '',
                'attachments':attachments(msg.get('payload',{}))}

    def get_message(self, message_id):
        msg=self.api('GET','/messages/'+urllib.parse.quote(message_id,safe='')+'?format=full')
        return self.parse_message(msg)

    def fetch_thread(self, thread_id):
        if not thread_id:return []
        doc=self.api('GET','/threads/'+urllib.parse.quote(thread_id,safe='')+'?format=full')
        return [self.parse_message(m) for m in doc.get('messages',[])]

    def sync_changes(self, cursor='', limit=30):
        """Gmail history-based inbox updates with full-scan fallback on expired cursor."""
        limit=min(100,max(1,int(limit)))
        profile=self.api('GET','/profile')
        next_cursor=str(profile.get('historyId',''))
        ids=[]
        if cursor:
            query=urllib.parse.urlencode({'startHistoryId':cursor,'historyTypes':'messageAdded','maxResults':100})
            try:
                page=self.api('GET','/history?'+query)
                while True:
                    for evt in page.get('history',[]):
                        for added in evt.get('messagesAdded',[]):
                            m=added.get('message') or {}
                            if 'INBOX' in m.get('labelIds',[]):ids.append(m.get('id',''))
                    page_token=page.get('nextPageToken')
                    if not page_token:break
                    page=self.api('GET','/history?'+query+'&pageToken='+urllib.parse.quote(page_token))
                if page.get('historyId'):next_cursor=str(page['historyId'])
            except ValueError as exc:
                if 'HTTP 404' not in str(exc):raise
                cursor=''  # Gmail's history window expired; scan the recent inbox safely
        if not cursor:
            q=urllib.parse.urlencode({'maxResults':limit,'q':'in:inbox'})
            listing=self.api('GET','/messages?'+q)
            ids=[m['id'] for m in listing.get('messages',[])]
        messages=[]
        for mid in list(dict.fromkeys(x for x in ids if x))[:limit]:
            try:
                full=self.get_message(mid)
                messages.append(full)
            except ValueError as exc:
                if 'HTTP 404' not in str(exc):raise
        return {'emails':messages,'cursor':next_cursor,'mode':'history' if cursor else 'recent_inbox_scan'}

    def fetch_messages(self,limit=10):
        listing=self.api('GET','/messages?'+urllib.parse.urlencode({'maxResults':limit,'q':'in:inbox'}))
        return [self.get_message(row['id']) for row in listing.get('messages',[])]

    def send(self,recipient,subject,body,thread_id='',reply_to_message_id='',references=''):
        status=self.status()
        if not status['enterprise_mode']:
            raise ValueError('Launch RUN.bat in enterprise mode to use Gmail delivery.')
        if not status['delivery_requested']:
            raise ValueError('Enable Live Email Delivery in Settings before sending.')
        if not status['send_permission_granted']:
            raise ValueError('Gmail send permission is missing. Reconnect Gmail and grant the requested send permission.')
        if not status['connected']:
            raise ValueError('Connect your Google account before sending.')
        name,addr=parseaddr(recipient)
        if not addr or '@' not in addr or any(c in addr for c in '\r\n'):
            raise ValueError('Recipient email address is missing or invalid.')
        m=EmailMessage();m['To']=addr;m['Subject']='Re: '+subject.removeprefix('Re: ').replace('\r',' ').replace('\n',' ')
        if reply_to_message_id and re.match(r'^<[^<>\r\n]{1,250}>$',reply_to_message_id):
            m['In-Reply-To']=reply_to_message_id
            m['References']=(references.strip()+' '+reply_to_message_id).strip()[-800:]
        m.set_content(body)
        encoded=base64.urlsafe_b64encode(m.as_bytes()).decode().rstrip('=')
        payload={'raw':encoded}
        if thread_id:payload['threadId']=thread_id
        result=self.api('POST','/messages/send',payload)
        if not result.get('id'):
            raise ValueError('Gmail did not accept the send request. Do not mark the message sent.')
        return result


gmail=GmailConnection()
