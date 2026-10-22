"""Run the actual graph against all independent cases and batch audit output."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kyc_poc import DEFAULT_JSON, DEFAULT_RULES, case_from_json
from policy_engine import assess_workflow, load_catalogue
from workflow_graph import build_workflow_graph


class FullGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = json.loads(DEFAULT_JSON.read_text(encoding='utf-8'))['test_cases']
        cls.cat = load_catalogue(DEFAULT_RULES)
        cls.graph = build_workflow_graph()

    def test_all_174_cases_match_oracles_and_entire_batch_result(self):
        before = copy.deepcopy(self.items)
        final_rules = set()
        self.assertEqual(len(self.items), 174)
        for item in self.items:
            with self.subTest(case=item['test_case_id']):
                result = self.graph.invoke({'case': item})['result']
                expected = item['expected']
                self.assertEqual(result['final_decision']['rule_id'], expected['final_rule_id'])
                self.assertEqual(result['final_decision']['result'], expected['final_result'])
                self.assertEqual(result['final_appetite'], expected['final_appetite'])
                self.assertEqual(result['risk_assessment']['total_score'], expected['total_score'])
                baseline = assess_workflow(case_from_json(item), self.cat)
                self.assertTrue(result['audit'].pop('evaluated_at'))
                baseline['audit'].pop('evaluated_at')
                self.assertEqual(result, baseline)
                final_rules.add(result['final_decision']['rule_id'])
        self.assertEqual(final_rules, set(self.cat.integration))
        self.assertEqual(self.items, before)

    def test_node_order_and_repeated_case_isolation(self):
        self.graph.invoke({'case': self.items[-1]})
        events = list(self.graph.stream({'case': self.items[0]}, stream_mode='updates'))
        self.assertEqual([next(iter(event)) for event in events], [
            'prepare_evidence', 'assess_risk_factors', 'assess_appetite',
            'assess_safeguards', 'policy_integration', 'build_report'])
        result = events[-1]['build_report']['result']
        self.assertEqual(result['assessment_id'], 'RA-' + case_from_json(self.items[0]).application.application_id)
        self.assertEqual(result['final_decision']['rule_id'], self.items[0]['expected']['final_rule_id'])

    def test_cli_trace_and_all_expected(self):
        root = DEFAULT_JSON.parent
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'results.json'
            run = subprocess.run([sys.executable, str(root/'workflow_graph.py'), '--check-expected',
                                  '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(json.loads(output.read_text())), 174)
            run = subprocess.run([sys.executable, str(root/'workflow_graph.py'), '--case',
                                  self.items[0]['test_case_id'], '--trace', '--output', str(output)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(run.stderr.splitlines()), 6)
            self.assertIn('build_report', run.stderr)

    def test_cli_rejects_wrong_expectations(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = copy.deepcopy(self.items[0])
            item['expected']['final_rule_id'] = 'WRONG'
            source = Path(tmp)/'wrong.json'
            source.write_text(json.dumps({'test_cases': [item]}))
            run = subprocess.run([sys.executable, str(DEFAULT_JSON.parent/'workflow_graph.py'),
                                  '--json', str(source), '--check-expected', '--output', str(Path(tmp)/'out.json')],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn('final_rule_id', run.stderr)
