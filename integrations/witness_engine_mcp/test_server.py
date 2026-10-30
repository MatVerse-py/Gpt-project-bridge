import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).with_name('server.py')
spec = importlib.util.spec_from_file_location('witness_server', PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

class WitnessMcpTests(unittest.TestCase):
    def test_tools_exposed(self):
        self.assertEqual(len(module.TOOLS), 5)
        self.assertTrue(all(t['name'] in module.ROUTES for t in module.TOOLS))

    def test_initialize(self):
        r = module.result_for({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        self.assertEqual(r['result']['serverInfo']['name'], 'matverse-witness-mcp')
        self.assertIn('tools', r['result']['capabilities'])

    def test_no_arbitrary_endpoint(self):
        self.assertNotIn('http://', module.API_BASE)
        self.assertEqual(module.API_BASE, 'https://matverse-witness-engine-iu685x.v2.appdeploy.ai')
        self.assertNotIn('custom_request', module.ROUTES)

    def test_unknown_tool_fails_closed(self):
        r = module.result_for({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'secret_write'}})
        self.assertTrue(r['result']['isError'])
        self.assertIn('HOLD', r['result']['content'][0]['text'])

if __name__ == '__main__':
    unittest.main()
