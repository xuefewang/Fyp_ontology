"""Read every scenario and its independent oracle from one static JSON file."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from case_migration import migrate_dataset
from kyc_poc import DEFAULT_RULES, case_from_json
from policy_engine import assess_workflow, load_catalogue

ROOT = DEFAULT_RULES.parent


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalogue(DEFAULT_RULES)
        cls.dataset = json.loads((ROOT/'test_cases_data.json').read_text(encoding='utf-8'))
        cls.results = [assess_workflow(case_from_json(item),cls.cat) for item in cls.dataset['test_cases']]

    def test_catalogue_and_priority(self):
        self.assertEqual(set(self.cat.rf),{f'RF-{i:02}' for i in range(1,22)})
        self.assertEqual(set(self.cat.appetite),{f'FA-{i:02}' for i in range(1,8)})
        self.assertEqual(len(self.cat.dimensions),17)
        self.assertEqual(set(self.cat.safeguards),{f'SG-{i:02}' for i in range(1,14)})
        self.assertFalse(self.cat.warnings)
        self.assertEqual([r.rule_id for r in sorted(self.cat.integration.values(),key=lambda r:r.priority)],
            ['PI-01','PI-03','PI-02','PI-11','PI-06','PI-04','PI-05','PI-07','PI-08','PI-09','PI-10'])

    def test_all_static_cases_match_independent_expectations(self):
        self.assertEqual(len(self.dataset['test_cases']),174)
        for item,r in zip(self.dataset['test_cases'],self.results):
            with self.subTest(case=item['test_case_id'],description=item.get('description')):
                exp=item['expected']
                self.assertEqual(r['final_decision']['rule_id'],exp['final_rule_id'])
                self.assertEqual(r['final_decision']['result'],exp['final_result'])
                self.assertEqual(r['final_appetite'],exp['final_appetite'])
                self.assertEqual(r['risk_assessment']['total_score'],exp['total_score'])
                if 'risk_factor_hit' in exp:self.assertIn(exp['risk_factor_hit'],{h['factor_id'] for h in r['risk_assessment']['hits']})
                if 'risk_band' in exp:self.assertEqual(r['risk_assessment']['risk_band'],exp['risk_band'])
                if 'doc_gate' in exp:self.assertEqual(r['risk_assessment']['doc_gate'],exp['doc_gate'])
                if 'doc_gate_reason_ids' in exp:self.assertTrue(set(exp['doc_gate_reason_ids']) <= {x['rule_id'] for x in r['risk_assessment']['doc_gate_reasons']})
                if 'appetite_rule' in exp:self.assertIn(exp['appetite_rule'],r['appetite_rule_ids'])
                if 'absent_appetite_rule' in exp:self.assertNotIn(exp['absent_appetite_rule'],r['appetite_rule_ids'])
                if 'intermediate_legal_entity_count' in exp:self.assertEqual(r['ownership_analysis']['intermediate_legal_entity_count'],exp['intermediate_legal_entity_count'])
                if 'dimension_rule_hit' in exp:self.assertIn(exp['dimension_rule_hit'],r['dimensions'][exp['dimension_rule_hit'][:2]]['rule_ids'])
                for rid in exp.get('dimension_rule_hits',[]):self.assertIn(rid,r['dimensions'][rid[:2]]['rule_ids'])
                for prefix,level in exp.get('dimension_level',{}).items():self.assertEqual(r['dimensions'][prefix]['level'],level)
                if 'safeguard_status' in exp:self.assertEqual(r['safeguards']['status'],exp['safeguard_status'])

    def test_case_ids_evidence_and_rule_coverage(self):
        cases=self.dataset['test_cases'];ids=[c['test_case_id'] for c in cases]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(self.dataset['original_case_count'],28)
        self.assertEqual(self.dataset['synthetic_extension_count'],146)
        self.assertTrue(all(c['synthetic'] for c in cases[28:]))
        for c in cases:
            self.assertTrue(all(x.get('evidence_refs') for x in c['data']['WorkflowEvidence']['risk_factors'].values()))
        self.assertEqual({h['factor_id'] for r in self.results for h in r['risk_assessment']['hits']},set(self.cat.rf))
        self.assertEqual({r['final_decision']['rule_id'] for r in self.results},set(self.cat.integration))
        self.assertEqual({s['safeguard_id'] for r in self.results for s in r['safeguards']['selected']},set(self.cat.safeguards))

    def test_original_snapshot_and_migration(self):
        original=self.dataset['original_source']
        checksum=hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(checksum,self.dataset['original_source_content_sha256'])
        migrated=migrate_dataset(original);self.assertEqual(migrate_dataset(migrated),migrated)
        for old,new in zip(original['test_cases'],self.dataset['test_cases'][:28]):
            for k,v in old.items():
                self.assertEqual({key:value for key,value in new[k].items() if key!='WorkflowEvidence'} if k=='data' else new[k],v)
            self.assertEqual(case_from_json(old).workflow_evidence,case_from_json(new).workflow_evidence)
            self.assertFalse(new['data']['WorkflowEvidence']['approvals'])

    def test_cli_runs_all_cases_and_exports_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'results.json'
            process=subprocess.run([sys.executable,str(ROOT/'kyc_poc.py'),'--check-expected','--output',str(output)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertIn('174 / 174',process.stdout)
            results=json.loads(output.read_text());self.assertEqual(len(results),174)
            self.assertTrue(all(len(r['audit']['workbook_sha256'])==3 for r in results))

    def test_wrong_expectation_fails_cli(self):
        dataset=copy.deepcopy(self.dataset);dataset['test_cases']=dataset['test_cases'][:1]
        dataset['test_cases'][0]['expected']['final_rule_id']='PI-08'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'wrong.json';source.write_text(json.dumps(dataset))
            process=subprocess.run([sys.executable,str(ROOT/'kyc_poc.py'),'--json',str(source),'--check-expected'],capture_output=True,text=True)
            self.assertEqual(process.returncode,1);self.assertIn('final_rule_id',process.stderr)


if __name__=='__main__':unittest.main()
