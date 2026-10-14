"""AEGIS V4 offline safety tests. Never contacts Gmail or Gemini."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server, storage, agent, quality, delivery, gmail_oauth

class QAReviewer:
    def __init__(self,model): pass
    def ask(self,*args,**kwargs):return {'pass':True,'issues':[]}

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        d=Path(self.tmp.name)
        self.patches=[patch.object(storage,'DB',d/'db.sqlite'),patch.object(server,'INBOX',d/'inbox'),
                      patch.object(server,'OUTBOX',d/'outbox.json'),patch.object(server,'POLICY',d/'policy.md'),
                      patch.object(server.security,'PASSWORD','testpass123')]
        for patcher in self.patches:patcher.start();self.addCleanup(patcher.stop)
        storage.initialize()
        server.INBOX.mkdir()
        server.POLICY.write_text((Path(__file__).resolve().parents[1]/'data/policy.md').read_text())
        storage.add_policy(server.POLICY.read_text(),'test','policy')
        self.email={'id':'T555','from_name':'Demo','from_email':'authorised.receiver@gmail.com','subject':'Voucher',
                    'body':'Voucher not visible','thread_id':'test-thread','rfc_message_id':'<rfc-123@uni.edu>'}
        (server.INBOX/'T555.json').write_text(json.dumps(self.email))
        self.result={'email_id':'T555','category':'fee','priority':'low','escalate':False,
                     'policy_refs':['FEE-1'],'reply_draft':'Dear Demo, FEE-1 says contact the Accounts Office. Regards, Student Helpdesk',
                     'confidence':.8,'agent_notes':'Policy cited'}
        server.OUTBOX.write_text(json.dumps([self.result]))
        storage.save_ai_result(self.result,storage.current_policy_version())
        server.state['running']=False
        self.client=TestClient(server.app)
        self.assertEqual(self.client.post('/api/login',json={'password':'testpass123'}).status_code,200)

    def test_policy_gate_blocks_example_recipient(self):
        email=dict(self.email,from_email='fake@example.com')
        gate=quality.validate_reply(storage.case('T555'),self.result,email,server.POLICY.read_text())
        self.assertFalse(gate['passed']);self.assertTrue(any('fictional' in v for v in gate['errors']))

    def test_semantic_quality_rejection_blocks_provider(self):
        storage.approve('T555')
        class BadReviewer:
            def __init__(self,model):pass
            def ask(self,*a,**kw):return {'pass':False,'issues':['The draft makes an unsupported promise.']}
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}),patch.object(agent,'GeminiReasoner',BadReviewer),patch.object(server.gmail,'send') as gmail_send:
            response=self.client.post('/api/cases/T555/send',json={'confirmation':'SEND'})
        self.assertEqual(response.status_code,409);gmail_send.assert_not_called()
        self.assertEqual(storage.case('T555')['status'],'approved')
        self.assertTrue(any(e['action']=='quality_gate_checked' for e in storage.list_audit()))

    def test_known_rejection_requires_explicit_retry(self):
        storage.approve('T555')
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}),patch.object(agent,'GeminiReasoner',QAReviewer),patch.object(server.gmail,'send',side_effect=ValueError('Google API returned HTTP 403: scope blocked')):
            res=self.client.post('/api/cases/T555/send',json={'confirmation':'SEND'})
        self.assertEqual(res.status_code,502)
        self.assertEqual(storage.case('T555')['status'],'send_failed')
        self.assertEqual(self.client.post('/api/cases/T555/send',json={'confirmation':'SEND'}).status_code,409)
        retry=self.client.post('/api/cases/T555/retry-send',json={'reason':'Fixed OAuth permission and verified error'})
        self.assertEqual(retry.status_code,200)
        self.assertEqual(storage.case('T555')['status'],'approved')

    def test_timeout_never_automatically_resends(self):
        storage.approve('T555')
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}),patch.object(agent,'GeminiReasoner',QAReviewer),patch.object(server.gmail,'send',side_effect=TimeoutError('connection closed')) as call:
            res=self.client.post('/api/cases/T555/send',json={'confirmation':'SEND'})
        self.assertEqual(res.status_code,502)
        self.assertEqual(storage.case('T555')['status'],'send_uncertain')
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.client.post('/api/cases/T555/retry-send',json={'reason':'Just try it again please'}).status_code,400)

    def test_crash_recovery_marks_sending_uncertain(self):
        storage.approve('T555');storage.begin_send('T555','authorised.receiver@gmail.com')
        self.assertEqual(storage.recover_incomplete_sends(),1)
        self.assertEqual(storage.case('T555')['status'],'send_uncertain')
        self.assertEqual(storage.recover_incomplete_sends(),0)

    def test_automatic_high_risk_department_routing(self):
        r=dict(self.result,escalate=True,priority='high',policy_refs=['ESC-1','ESC-3'])
        storage.save_ai_result(r,storage.current_policy_version())
        dept=storage.case('T555')['assigned_to']
        self.assertIn('Security',dept);self.assertIn('Campus Clinic',dept)
        self.assertIn('escalation_routed',[e['action'] for e in storage.list_audit()])

    def test_gmail_thread_parsed_with_rfc_id(self):
        msg={'id':'123abc','threadId':'456def','payload':{'headers':[{'name':'From','value':'Jane <jane@gmail.com>'},
             {'name':'Subject','value':'Help'}, {'name':'Message-ID','value':'<rfc-9@domain.test>'}],
             'mimeType':'text/plain','body':{'data':'SGVsbG8='}}}
        g=gmail_oauth.GmailConnection()
        parsed=g.parse_message(msg)
        self.assertEqual(parsed['rfc_message_id'],'<rfc-9@domain.test>')
        self.assertEqual(parsed['body'],'Hello')
        self.assertEqual(parsed['thread_id'],'456def')

    def test_incremental_history_sync_and_deduplication(self):
        g=gmail_oauth.GmailConnection()
        g.account='operator@gmail.com';g.access_token='temporary'
        def api(method,path,payload=None):
            if path=='/profile':return {'historyId':'1200'}
            if path.startswith('/history?'):return {'history':[{'messagesAdded':[{'message':{'id':'abc','labelIds':['INBOX']}},
                                                                            {'message':{'id':'abc','labelIds':['INBOX']}}]}], 'historyId':'1200'}
            raise AssertionError(path)
        with patch.object(g,'api',side_effect=api),patch.object(g,'get_message',return_value={'id':'Gabc','gmail_message_id':'abc','thread_id':'t1'}) as fetch:
            result=g.sync_changes('1195',limit=30)
        self.assertEqual(result['mode'],'history');self.assertEqual(len(result['emails']),1);fetch.assert_called_once_with('abc')

    def test_cached_thread_and_audit(self):
        storage.register_thread_message({'gmail_message_id':'xyz','thread_id':'thread-123','id':'Gxyz','subject':'Request','body':'Hello'})
        with patch.object(server.gmail,'status',return_value={'connected':False}):
            res=self.client.get('/api/gmail/threads/thread-123')
        self.assertEqual(res.status_code,200)
        self.assertEqual(res.json()['source'],'cached')
        self.assertEqual(len(res.json()['messages']),1)

    def test_provider_accepted_not_delivery_confirmed(self):
        storage.approve('T555')
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}),patch.object(agent,'GeminiReasoner',QAReviewer),patch.object(server.gmail,'send',return_value={'id':'provider-test-id'}):
            resp=self.client.post('/api/cases/T555/send',json={'confirmation':'SEND'})
        self.assertEqual(resp.status_code,200)
        self.assertEqual(resp.json()['delivery_status'],'provider_accepted')
        self.assertNotIn('delivered',resp.json()['delivery_status'])
        self.assertEqual(len(storage.send_history('T555')),1)

if __name__=='__main__':unittest.main()
