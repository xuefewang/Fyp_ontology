"""Compare the real LangGraph execution against the independent static cases."""
import copy
import json
import unittest

from appetite import build_appetite_graph
from kyc_poc import DEFAULT_JSON, case_from_json
from policy_engine import assess_workflow, load_catalogue
from kyc_poc import DEFAULT_RULES


class AppetiteGraphTests(unittest.TestCase):
    def test_all_cases_and_input_isolation(self):
        graph = build_appetite_graph()
        catalogue = load_catalogue(DEFAULT_RULES)
        items = json.loads(DEFAULT_JSON.read_text())['test_cases']
        before = copy.deepcopy(items)
        routes = set()
        for item in items:
            with self.subTest(case=item['test_case_id']):
                actual = graph.invoke({'case': item})
                baseline = assess_workflow(case_from_json(item), catalogue)
                self.assertEqual(actual['final_appetite'], item['expected']['final_appetite'])
                for key in ('dimensions', 'risk_assessment', 'ownership_analysis', 'appetite_rule_ids'):
                    self.assertEqual(actual[key], baseline[key])
                self.assertEqual(actual['audit']['input_evidence'], baseline['audit']['input_evidence'])
                routes.add(actual['next_step'])
                if actual['next_step'] == 'safeguards':
                    self.assertEqual(actual['risk_assessment']['hard_gate'], 'Clear')
                    self.assertEqual(actual['risk_assessment']['doc_gate'], 'Clear')
                    self.assertNotEqual(actual['final_appetite'], 'Pending')
        self.assertEqual(items, before)
        self.assertEqual(routes, {'safeguards', 'review_required'})

    def test_reuse_does_not_retain_previous_case(self):
        graph = build_appetite_graph()
        items = json.loads(DEFAULT_JSON.read_text())['test_cases']
        graph.invoke({'case': items[-1]})
        result = graph.invoke({'case': items[0]})
        self.assertEqual(result['final_appetite'], items[0]['expected']['final_appetite'])
        self.assertNotIn('evidence', result)
