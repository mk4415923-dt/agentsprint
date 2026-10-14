"""Local-only integration tests. No Gmail/Gemini network calls or real deliveries."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server, storage, agent


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d=Path(self.tmp.name)
        self.patches=[patch.object(server,'INBOX',d/'inbox'),patch.object(server,'POLICY',d/'policy.md'),
                      patch.object(server,'OUTBOX',d/'outbox.json'),patch.object(server,'LOGS',d/'log.jsonl'),
                      patch.object(storage,'DB',d/'app.db'), patch.object(server.security,'PASSWORD','testing-admin')]
        for pp in self.patches: pp.start();self.addCleanup(pp.stop)
        server.INBOX.mkdir(parents=True)
        (server.INBOX/'E999.json').write_text(json.dumps({'id':'E999','from_name':'Test Student',
                 'from_email':'qa.operator.receiving@gmail.com','subject':'Voucher question','body':'My voucher is missing.'}))
        policy=Path(__file__).resolve().parents[1]/'data'/'policy.md'
        server.POLICY.write_text(policy.read_text(encoding='utf-8'),encoding='utf-8')
        storage.initialize();storage.add_policy(server.POLICY.read_text(encoding='utf-8'),'initial','Initial policy')
        self.client=TestClient(server.app)
        self.assertEqual(self.client.post('/api/login',json={'password':'testing-admin'}).status_code,200)
        self.orig_running=server.state['running']; server.state['running']=False
        self.addCleanup(lambda:server.state.update(running=self.orig_running))

    def ai_result(self):
        return {'email_id':'E999','category':'fee','priority':'medium','escalate':False,
           'escalation_reason':'','policy_refs':['FEE-1'],'reply_draft':'Dear Test Student, please see FEE-1. Regards, Student Helpdesk',
           'confidence':.82,'agent_notes':'Student asked about a missing voucher.'}

    def seed(self):
        result=self.ai_result();server.OUTBOX.write_text(json.dumps([result]));storage.save_ai_result(result,storage.current_policy_version())

    def test_real_case_lifecycle_with_mocked_provider(self):
        self.seed()
        opened=self.client.post('/api/cases/E999/open');self.assertEqual(opened.status_code,200)
        edited=self.client.put('/api/cases/E999/draft',json={'draft':'Dear Test Student, FEE-1 says contact Accounts Office. Regards, Student Helpdesk'})
        self.assertEqual(edited.status_code,200)
        self.assertEqual(edited.json()['case']['status'],'needs_review')
        approved=self.client.post('/api/cases/E999/approve')
        self.assertEqual(approved.status_code,200)
        self.assertEqual(approved.json()['case']['status'],'approved')
        forbidden=self.client.post('/api/cases/E999/send',json={'confirmation':'SEND'})
        self.assertEqual(forbidden.status_code,409)
        class QAReviewer:
            def __init__(self,model):pass
            def ask(self,*a,**kw):return {'pass':True,'issues':[]}
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}),patch.object(server.gmail,'send',return_value={'id':'mock-provider-ID'}),patch.object(agent,'GeminiReasoner',QAReviewer):
            sent=self.client.post('/api/cases/E999/send',json={'confirmation':'SEND'})
        self.assertEqual(sent.status_code,200)
        self.assertEqual(sent.json()['case']['status'],'provider_accepted')
        self.assertEqual(sent.json()['case']['sent_message_id'],'mock-provider-ID')
        self.assertEqual(self.client.post('/api/cases/E999/send',json={'confirmation':'SEND'}).status_code,409)
        audit=self.client.get('/api/audit?email_id=E999').json()['events']
        actions=[e['action'] for e in audit]
        self.assertIn('gmail_provider_accepted',actions)
        self.assertIn('quality_gate_checked',actions)
        self.assertIn('gmail_send_started',actions)
        self.assertIn('operator_approved',actions)
        self.assertIn('draft_edited',actions)

    def test_policy_change_blocks_stale_approval(self):
        self.seed()
        updated=self.client.post('/api/policy',json={'content':server.POLICY.read_text()+'\n## Additional student help\n- TECH-4: New rule for example policy testing.\n','notes':'Add technical response route'})
        self.assertEqual(updated.status_code,200)
        self.assertEqual(self.client.post('/api/cases/E999/approve').status_code,409)
        self.assertGreater(storage.current_policy_version(),1)
        self.assertEqual(self.client.get('/api/policy/versions/1').status_code,200)

    def test_draft_must_be_approved_before_send(self):
        self.seed()
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}):
            res=self.client.post('/api/cases/E999/send',json={'confirmation':'SEND'})
        self.assertEqual(res.status_code,409)
        self.assertEqual(storage.case('E999')['status'],'draft_ready')

    def test_agent_batch_to_persistent_case(self):
        class FakeBrain:
            def __init__(self,model):pass
            def ask(self,task,payload,attempts=3):
                if 'STAGE 1' in task:
                    return {'category':'fee','priority':'medium','policy_topics':['Fees'],'search_terms':['voucher'],'notes':'Voucher issue'}
                item=self_result.copy()
                item.pop('email_id')
                return item
        self_result=self.ai_result()
        with patch.object(agent,'GeminiReasoner',FakeBrain),patch.dict(os.environ,{'GEMINI_API_KEY':'unit-test-not-live'}):
            result=self.client.post('/api/run',json={'only':'E999'})
            self.assertEqual(result.status_code,200)
            for _ in range(100):
                if not server.state['running']:break
                time.sleep(.03)
        self.assertFalse(server.state['running'])
        self.assertEqual(storage.case('E999')['status'],'draft_ready')
        self.assertEqual(server.OUTBOX.exists(),True)
        self.assertTrue(any(e['action']=='ai_draft_created' for e in storage.list_audit()))


if __name__=='__main__':unittest.main()
