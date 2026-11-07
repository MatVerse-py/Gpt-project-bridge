from __future__ import annotations

import copy
import unittest
from app.reactor_bridge import assess_reactor, verify_reactor_result, BRIDGE_SCHEMA
from matverse_reactors.core import ReactorGraph, digest

GRAPH = {'schema': 'matverse.reactor-graph.v1', 'nodes': [
    {'node_id': 'sensor', 'kind': 'TRANSDUCER', 'params': {'field': 'signal', 'scale': 0.5}},
    {'node_id': 'motor', 'kind': 'MOTOR', 'source': 'sensor', 'params': {'gain': 1.4}},
    {'node_id': 'reactor', 'kind': 'REACTOR', 'source': 'motor', 'params': {'feedback': 0.25}},
    {'node_id': 'dissipator', 'kind': 'DISSIPATOR', 'source': 'reactor', 'params': {'minimum': 0, 'maximum': 10}},
    {'node_id': 'diffuser', 'kind': 'DIFFUSER', 'source': 'dissipator', 'params': {'ratio': .2}},
]}


class FakeJournal:
    def __init__(self):
        self.saved={}
    def append(self, **kwargs):
        key=kwargs['event_id']
        if key in self.saved and self.saved[key]['payload'] != kwargs['payload']:
            raise ValueError('collision')
        self.saved[key]=kwargs
        return type('E', (), {'event_id': key, 'seq': 1})()


class BridgeTests(unittest.TestCase):
    def test_assessment_has_no_privileged_authority(self):
        report=assess_reactor(graph_spec=GRAPH, inputs={'signal': 10})
        self.assertEqual(report['schema'], BRIDGE_SCHEMA)
        self.assertEqual(report['decision'], 'HOLD')
        self.assertFalse(report['external_execution_allowed'])
        self.assertEqual(report['external_effects'], [])
        self.assertEqual(report['gate_decision'],'BLOCK')
    def test_idempotent_result(self):
        a=assess_reactor(graph_spec=GRAPH,inputs={'signal': 10})
        b=assess_reactor(graph_spec=GRAPH,inputs={'signal': 10})
        self.assertEqual(a,b)
    def test_validating_legacy_self_hash_is_insufficient(self):
        out=ReactorGraph.from_dict(GRAPH).run({'signal':10})
        self.assertTrue(verify_reactor_result(graph_spec=GRAPH,inputs={'signal':10},candidate=out))
        bad=copy.deepcopy(out)
        bad['values']['motor']=999
        payload={k:v for k,v in bad.items() if k!='result_hash'}
        bad['result_hash']=digest(payload)
        self.assertFalse(verify_reactor_result(graph_spec=GRAPH,inputs={'signal':10},candidate=bad))
    def test_result_rejects_wrong_inputs(self):
        out=ReactorGraph.from_dict(GRAPH).run({'signal':10})
        self.assertFalse(verify_reactor_result(graph_spec=GRAPH,inputs={'signal':11},candidate=out))
    def test_human_confidential_is_blocked(self):
        report=assess_reactor(graph_spec=GRAPH,inputs={'signal':1},human={'sensitivity':'SECRET'})
        self.assertEqual(report['decision'],'BLOCK')
        self.assertEqual(report['gate_decision'],'BLOCK')
    def test_simulated_not_upgraded(self):
        report=assess_reactor(graph_spec=GRAPH,inputs={'signal':1},evidence_class='SIMULATED')
        self.assertEqual(report['evidence_class'],'SIMULATED')
        self.assertEqual(report['decision'],'HOLD')
    def test_measured_requires_source(self):
        with self.assertRaises(ValueError):assess_reactor(graph_spec=GRAPH,inputs={'signal':1},evidence_class='MEASURED_LOCAL')
    def test_untrusted_claim_of_measured_does_not_verify(self):
        out=ReactorGraph.from_dict(GRAPH).run({'signal':1},evidence_class='MEASURED_LOCAL')
        self.assertFalse(verify_reactor_result(graph_spec=GRAPH,inputs={'signal':1},candidate=out))
    def test_journal_receipt_commit_only(self):
        j=FakeJournal()
        report=assess_reactor(graph_spec=GRAPH,inputs={'signal':1},journal=j)
        record=next(iter(j.saved.values()))
        self.assertEqual(record['payload']['receipt_hash'],report['receipt']['receipt_hash'])
        self.assertNotIn('signal',str(record))
        self.assertEqual(report['journal_seq'],1)
    def test_journal_idempotent_event_identifier(self):
        j=FakeJournal()
        a=assess_reactor(graph_spec=GRAPH,inputs={'signal':1},journal=j)
        b=assess_reactor(graph_spec=GRAPH,inputs={'signal':1},journal=j)
        self.assertEqual(a['journal_event_id'],b['journal_event_id'])
        self.assertEqual(len(j.saved),1)
    def test_journal_exception_not_hidden(self):
        class Broken:
            def append(self,**kwargs): raise OSError('readonly')
        with self.assertRaises(OSError):assess_reactor(graph_spec=GRAPH,inputs={'signal':1},journal=Broken())
    def test_bad_graph_blocks(self):
        bad=copy.deepcopy(GRAPH)
        bad['nodes'][0]['params']['field']='../../secret'
        with self.assertRaises(ValueError):assess_reactor(graph_spec=bad,inputs={'signal':1})
    def test_nonfinite_blocks(self):
        with self.assertRaises(ValueError):assess_reactor(graph_spec=GRAPH,inputs={'signal':float('nan')})
    def test_inputs_untouched(self):
        v={'signal':10}
        original=copy.deepcopy(v)
        assess_reactor(graph_spec=GRAPH,inputs=v)
        self.assertEqual(v,original)
    def test_graph_fingerprint_stable(self):
        report=assess_reactor(graph_spec=GRAPH,inputs={'signal':1})
        self.assertEqual(report['graph_fingerprint'],ReactorGraph.from_dict(GRAPH).fingerprint)
    def test_previous_state_affects_reactor(self):
        a=assess_reactor(graph_spec=GRAPH,inputs={'signal':10})
        b=assess_reactor(graph_spec=GRAPH,inputs={'signal':10},previous={'reactor':9})
        self.assertNotEqual(a['result_hash'],b['result_hash'])

if __name__=='__main__':unittest.main()
