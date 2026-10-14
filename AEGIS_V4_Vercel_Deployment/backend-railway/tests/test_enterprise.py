"""Enterprise delivery controls: all Gmail responses mocked; no actual mail."""
import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs,urlparse
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server,storage,security,gmail_oauth

class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=patch.object(storage,'DB',Path(self.tmp.name)/'aegis.db');self.store.start();self.addCleanup(self.store.stop)
        self.enterprise=patch.dict('os.environ',{'AEGIS_ENTERPRISE_MODE':'1'});self.enterprise.start();self.addCleanup(self.enterprise.stop)
        self.const=patch.object(gmail_oauth,'ENTERPRISE_MODE',True);self.const.start();self.addCleanup(self.const.stop)
        storage.initialize()
        self.cli=TestClient(server.app)
    def test_default_off_and_oauth_scopes(self):
        g=gmail_oauth.GmailConnection();g.client_id='client.apps.googleusercontent.com';g.client_secret='dev-test'
        info=g.status()
        self.assertTrue(info['enterprise_mode']);self.assertFalse(info['sending_enabled'])
        qs=parse_qs(urlparse(g.authorization_url()).query)
        self.assertIn(gmail_oauth.READ_SCOPE,qs['scope'][0])
        self.assertIn(gmail_oauth.SEND_SCOPE,qs['scope'][0])
    def test_toggle_requires_admin_auth_and_gmail_scope(self):
        with patch.object(security,'PASSWORD','test-password'):
            security.SESSIONS.clear()
            self.assertEqual(self.cli.post('/api/gmail/delivery',json={'enabled':True}).status_code,401)
            self.assertEqual(self.cli.post('/api/login',json={'password':'test-password'}).status_code,200)
            self.assertEqual(self.cli.post('/api/gmail/delivery',json={'enabled':True}).status_code,409)
            with patch.object(server.gmail,'status',return_value={'connected':True,'send_permission_granted':False}):
                self.assertEqual(self.cli.post('/api/gmail/delivery',json={'enabled':True}).status_code,409)
            with patch.object(server.gmail,'status',return_value={'connected':True,'send_permission_granted':True}):
                allowed=self.cli.post('/api/gmail/delivery',json={'enabled':True})
                self.assertEqual(allowed.status_code,200)
                self.assertTrue(storage.delivery_setting())
                self.assertEqual(self.cli.post('/api/gmail/delivery',json={'enabled':False}).status_code,200)
                self.assertFalse(storage.delivery_setting())
            security.SESSIONS.clear()
        self.assertIn('delivery_setting_changed',[r['action'] for r in storage.list_audit()])
    def test_cannot_send_without_admin_even_when_connected(self):
        with patch.object(server.gmail,'status',return_value={'connected':True,'sending_enabled':True}):
            with patch.object(server.gmail,'send') as called:
                res=self.cli.post('/api/cases/E001/send',json={'confirmation':'SEND'})
                self.assertEqual(res.status_code,403)
                called.assert_not_called()
    def test_granted_scope_and_persisted_enable_required(self):
        g=gmail_oauth.GmailConnection();g.access_token='not-a-real-token';g.expires_at=9999999999;g.account='operator@example.test'
        g.granted_scopes={gmail_oauth.READ_SCOPE}
        with self.assertRaisesRegex(ValueError,'Enable Live'):
            g.send('recipient@example.test','Hello','Body')
        storage.set_delivery_setting(True)
        with self.assertRaisesRegex(ValueError,'send permission'):
            g.send('recipient@example.test','Hello','Body')
        g.granted_scopes.add(gmail_oauth.SEND_SCOPE)
        with patch.object(g,'api',return_value={'id':'mock-gmail-message-id'}) as call:
            result=g.send('recipient@example.test','Hello','Body')
        self.assertEqual(result['id'],'mock-gmail-message-id')
        self.assertEqual(call.call_args.args[0],'POST')
        self.assertEqual(call.call_args.args[1],'/messages/send')

if __name__=='__main__': unittest.main()
