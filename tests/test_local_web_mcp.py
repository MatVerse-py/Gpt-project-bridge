import json, os, subprocess, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from app.local_web_mcp import NAME, HTTP_NAME, digest, install, origin

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location', 'http://127.0.0.1:1/'); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
        self.wfile.write(b'<title>MatVerse real</title><body><h1>PHOENIX</h1><script>document.body.append(" RENDER_OK")</script><a href="/next">next</a></body>')
    def log_message(self, *args): pass

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
    def rpc(self, url, name=NAME, standalone=True):
        requests = [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':name,'arguments':{'url':url}}}]
        env = dict(os.environ, MATVERSE_WEB_ORIGINS=self.url)
        p = subprocess.run([sys.executable,'-m','app.local_web_mcp']+(['--standalone'] if standalone else []), input='\n'.join(map(json.dumps,requests))+'\n', text=True, capture_output=True, env=env, timeout=40, check=True)
        return list(map(json.loads,p.stdout.splitlines()))
    @unittest.skipUnless(os.environ.get('MATVERSE_TEST_RENDER') == '1', 'requires explicitly installed Chromium/Playwright')
    def test_rendered_mcp(self):
        responses = self.rpc(self.url)
        self.assertEqual(responses[1]['result']['tools'][0]['name'],NAME)
        result = responses[2]['result']; self.assertFalse(result['isError'])
        data = json.loads(result['content'][0]['text']); receipt = data.pop('receipt_sha256')
        self.assertEqual(receipt,digest(data)); self.assertIn('RENDER_OK',data['text']); self.assertIn(self.url+'/next',data['links'])
        print('REAL_MCP_RECEIPT',json.dumps({**data,'receipt_sha256':receipt}))
    def test_http_mcp(self):
        result = self.rpc(self.url, HTTP_NAME)[2]['result']; self.assertFalse(result['isError'])
        data = json.loads(result['content'][0]['text']); receipt=data.pop('receipt_sha256'); self.assertEqual(receipt,digest(data)); self.assertIn('PHOENIX',data['text']); self.assertNotIn('RENDER_OK',data['text']); print('REAL_HTTP_MCP_RECEIPT',json.dumps({**data,'receipt_sha256':receipt}))
    def test_real_bridge_http(self):
        responses = self.rpc(self.url, HTTP_NAME, standalone=False)
        names = {t['name'] for t in responses[1]['result']['tools']}
        self.assertTrue({'bridge_agents', NAME, HTTP_NAME}.issubset(names))
        self.assertFalse(responses[2]['result']['isError'])
        data=json.loads(responses[2]['result']['content'][0]['text']); receipt=data.pop('receipt_sha256'); self.assertEqual(receipt,digest(data))
    def test_http_redirect_denied(self): self.assertTrue(self.rpc(self.url+'/redirect', HTTP_NAME)[2]['result']['isError'])
    def test_http_denied_origin(self): self.assertTrue(self.rpc('https://example.org', HTTP_NAME)[2]['result']['isError'])
    def test_denied_origin(self): self.assertTrue(self.rpc('https://example.org')[2]['result']['isError'])
    @unittest.skipUnless(os.environ.get('MATVERSE_TEST_RENDER') == '1', 'requires explicitly installed Chromium/Playwright')
    def test_redirect_denied(self): self.assertTrue(self.rpc(self.url+'/redirect')[2]['result']['isError'])
    def test_url_validation(self):
        for url in ['file:///etc/passwd','https://a:b@example.org','http://example.org','https://example.org\\@bad']:
            with self.assertRaises(ValueError): origin(url)
    def test_bridge_install_preserves_dispatch(self):
        class Bridge:
            def call(self,name,args): return 'original'
        module = SimpleNamespace(TOOLS=[],BridgeMCP=Bridge)
        install(module); install(module)
        self.assertEqual(len(module.TOOLS),2); self.assertEqual(Bridge().call('old',{}),'original')
        with self.assertRaises(ValueError): Bridge().call(NAME,{})

if __name__ == '__main__': unittest.main()
