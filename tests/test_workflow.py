"""Validate the revised workbooks, 118 independent case oracles and priority edges."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_cases import ROOT, baseline, build_dataset, complex_ownership, control, controls, graph, high_industry, hit, pending, signature
from case_migration import migrate_dataset
from kyc_poc import DEFAULT_RULES, case_from_json
from ownership import analyze_ownership
from policy_engine import (RF_CRITERIA, assess_workflow, evaluate_dimensions, evaluate_rf,
                           integrate, load_catalogue, rf_condition)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalogue(DEFAULT_RULES)
        cls.dataset = json.loads((ROOT/'test_cases_data.json').read_text())

    def setUp(self):
        self.e = baseline('RA-TEST-1')

    def result(self):
        item=copy.deepcopy(self.dataset['test_cases'][0])
        item['data']['Application']['application_id']='TEST-1'
        item['data']['WorkflowEvidence']=self.e
        return assess_workflow(case_from_json(item),self.cat)

    def test_catalogue_matches_updated_workbooks(self):
        self.assertEqual(set(self.cat.rf),{f'RF-{i:02}' for i in range(1,22)})
        self.assertEqual(set(self.cat.appetite),{f'FA-{i:02}' for i in range(1,8)})
        self.assertEqual(len(self.cat.dimensions),17)
        self.assertIn('D1-07',self.cat.dimensions)
        self.assertEqual(set(self.cat.safeguards),{f'SG-{i:02}' for i in range(1,14)})
        self.assertFalse(self.cat.warnings)
        order=['PI-01','PI-03','PI-02','PI-11','PI-06','PI-04','PI-05','PI-07','PI-08','PI-09','PI-10']
        self.assertEqual([r.rule_id for r in sorted(self.cat.integration.values(),key=lambda r:r.priority)],order)

    def test_all_118_independent_expected_results_and_coverage(self):
        items=self.dataset['test_cases'];self.assertEqual(len(items),118)
        seen,rf_coverage,pi_coverage,sg_coverage=set(),set(),set(),set()
        for item in items:
            with self.subTest(case=item['test_case_id'],description=item.get('description')):
                self.assertNotIn(item['test_case_id'],seen);seen.add(item['test_case_id'])
                e=item['data']['WorkflowEvidence'];exp=item['expected']
                self.assertTrue(e['risk_factors'])
                self.assertTrue(all(v.get('evidence_refs') for v in e['risk_factors'].values()))
                r=assess_workflow(case_from_json(item),self.cat)
                self.assertEqual(r['final_decision']['rule_id'],exp['final_rule_id'])
                self.assertEqual(r['final_decision']['result'],exp['final_result'])
                self.assertEqual(r['final_appetite'],exp['final_appetite'])
                self.assertEqual(r['risk_assessment']['total_score'],exp['total_score'])
                pi_coverage.add(r['final_decision']['rule_id'])
                ids={h['factor_id'] for h in r['risk_assessment']['hits']};rf_coverage.update(ids)
                sg_coverage.update(s['safeguard_id'] for s in r['safeguards']['selected'])
                if 'risk_factor_hit' in exp:self.assertIn(exp['risk_factor_hit'],ids)
                if 'risk_band' in exp:self.assertEqual(r['risk_assessment']['risk_band'],exp['risk_band'])
                if 'doc_gate' in exp:self.assertEqual(r['risk_assessment']['doc_gate'],exp['doc_gate'])
                if 'doc_gate_reason_ids' in exp:self.assertTrue(set(exp['doc_gate_reason_ids']) <= {x['rule_id'] for x in r['risk_assessment']['doc_gate_reasons']})
                if 'appetite_rule' in exp:self.assertIn(exp['appetite_rule'],r['appetite_rule_ids'])
                if 'absent_appetite_rule' in exp:self.assertNotIn(exp['absent_appetite_rule'],r['appetite_rule_ids'])
                if 'intermediate_legal_entity_count' in exp:self.assertEqual(r['ownership_analysis']['intermediate_legal_entity_count'],exp['intermediate_legal_entity_count'])
        self.assertEqual(rf_coverage,set(self.cat.rf));self.assertEqual(pi_coverage,set(self.cat.integration))
        self.assertEqual(sg_coverage,set(self.cat.safeguards))

    def test_reproducible_builder_and_original_data_preserved(self):
        self.assertEqual(build_dataset(),self.dataset)
        source=ROOT/'tests/fixtures/original_test_cases.json';original=json.loads(source.read_text())
        self.assertEqual(self.dataset['source_sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
        for old,new in zip(original['test_cases'],self.dataset['test_cases'][:28]):
            for k,v in old.items():
                self.assertEqual({key:value for key,value in new[k].items() if key!='WorkflowEvidence'} if k=='data' else new[k],v)
        self.assertTrue(all(x['synthetic'] for x in self.dataset['test_cases'][28:]))

    def test_original_runtime_migration_matches_first_28_records(self):
        original=json.loads((ROOT/'tests/fixtures/original_test_cases.json').read_text())
        migrated=migrate_dataset(original)
        self.assertEqual(migrate_dataset(migrated),migrated)
        for old,new in zip(original['test_cases'],self.dataset['test_cases']):
            self.assertEqual(case_from_json(old).workflow_evidence,case_from_json(new).workflow_evidence)
            self.assertFalse(new['data']['WorkflowEvidence']['approvals'])

    def test_all_rf_predicates_are_three_valued(self):
        for fid,(method,keys) in RF_CRITERIA.items():
            facts={k:True for k in keys}
            if method=='failed':facts[keys[0]]='Failed'
            if method=='layers':facts[keys[0]]=4
            if method=='jurisdictions':facts[keys[0]]=3
            if method=='ubo_pending':facts[keys[0]]='Pending'
            with self.subTest(rule=fid):
                self.assertIs(rf_condition(fid,facts),True)
                self.assertIsNone(rf_condition(fid,{}))
                e=baseline('RA-TEST-1');e['risk_factors'][fid]={'facts':facts,'verified':True,'evidence_refs':['synthetic-proof']}
                self.assertIn(fid,{h['factor_id'] for h in evaluate_rf(e,self.cat)['hits']})

    def test_final_failed_ubo_is_not_remediable_rf11(self):
        self.assertIs(rf_condition('RF-11',{'ubo_verification_status':'Failed'}),False)
        for status in ('Pending','AwaitingInformation'):
            self.assertIs(rf_condition('RF-11',{'ubo_verification_status':status}),True)
        self.assertIs(rf_condition('RF-11',{'ubo_verification_status':'Verified','ownership_evidence_insufficient':False,'remediation_outstanding':False}),False)

    def test_hardgate_overrides_documents_and_invalid_inputs(self):
        hit(self.e,1,9,10);self.e['policy']={}
        r=self.result();self.assertEqual(r['final_decision']['rule_id'],'PI-01')
        self.assertEqual(r['risk_assessment']['doc_gate'],'Human Review')

    def test_documents_precede_pending_gate_and_hold_scoring(self):
        hit(self.e,9,10,11,12);pending(self.e,2)
        r=self.result();self.assertEqual(r['final_decision']['rule_id'],'PI-03')
        self.assertIsNone(r['risk_assessment']['total_score'])
        self.assertEqual(r['risk_assessment']['confirmed_score_subtotal'],3)
        self.assertEqual({x['rule_id'] for x in r['risk_assessment']['doc_gate_reasons']},{'RF-09','RF-10','RF-11'})

    def test_authorized_rejection_precedes_assessment_and_sg(self):
        self.e['approvals'][0]['decision']='Rejected';self.e['D3']['evidence_complete']=False
        control(self.e,'SG-06','Incomplete')
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-06')
        self.e['policy']['approval_matrix_approved']=False
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')

    def test_pep_and_close_associate_floor(self):
        for n in (13,14):
            self.e=baseline('RA-TEST-1');hit(self.e,n)
            r=self.result();self.assertEqual(r['final_appetite'],'Conditional')
            self.assertIn('FA-06',r['appetite_rule_ids'])
            self.e['D3']['evidence_complete']=False
            r=self.result();self.assertEqual(r['final_appetite'],'Pending');self.assertNotIn('FA-06',r['appetite_rule_ids'])

    def test_pep_never_downgrades_restricted(self):
        hit(self.e,14);high_industry(self.e)
        self.assertEqual(self.result()['final_appetite'],'Restricted')

    def test_appetite_max_aggregation_not_sum(self):
        complex_ownership(self.e)
        self.e['D1']['facts'].update(nominee_or_trust=True,relevant_parties_verified=True,legal_documents_sufficient=True)
        r=self.result();self.assertEqual(r['dimensions']['D1']['level'],'Medium')
        self.assertEqual(r['final_appetite'],'Conditional')
        self.e['D1']['facts'].update(control_rights_discrepancy=True,discrepancy_explained=False)
        self.assertEqual(self.result()['final_appetite'],'Restricted')

    def test_d1_evidence_gate_and_uncovered_grade_are_pending(self):
        self.e['D1']['evidence_complete']=False
        r=self.result();self.assertIn('D1-07',r['dimensions']['D1']['rule_ids'])
        self.assertIn('RF-11',r['dimensions']['D1']['remediation_rf_ids'])
        self.e=baseline('RA-TEST-1');self.e['D2']['facts']['category']='UnapprovedUnknown'
        r=self.result();self.assertIn('FA-07',r['appetite_rule_ids']);self.assertEqual(r['final_appetite'],'Pending')

    def test_within_controls_and_unknown_selection_block(self):
        control(self.e,'SG-06','Incomplete')
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-05')
        control(self.e,'SG-06');self.assertEqual(self.result()['final_decision']['rule_id'],'PI-08')
        self.e=baseline('RA-TEST-1');self.e['safeguards']={};self.e['safeguard_selection']['reviewed']=False
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-05')

    def test_edd_completion_does_not_lower_risk(self):
        high_industry(self.e);control(self.e,'SG-01','Incomplete')
        r=self.result();self.assertTrue(r['edd_required']);self.assertEqual(r['edd_status'],'Incomplete')
        self.assertEqual(r['final_decision']['rule_id'],'PI-05')
        control(self.e,'SG-01');r=self.result()
        self.assertEqual(r['final_appetite'],'Restricted');self.assertEqual(r['final_decision']['rule_id'],'PI-10')

    def test_ongoing_setup_complete_not_future_execution(self):
        for sid in ('SG-08','SG-09','SG-10'):
            self.e=baseline('RA-TEST-1');control(self.e,sid)
            self.assertEqual(self.result()['final_decision']['rule_id'],'PI-08')
            self.e['safeguards'][sid]['ongoing_setup'].pop('first_due_date')
            self.assertEqual(self.result()['safeguards']['status'],'Incomplete')
            self.e['safeguards'][sid]['ongoing_setup']['cadence_approved']=None
            self.assertEqual(self.result()['safeguards']['status'],'Pending')

    def test_all_required_current_signatures_and_veto(self):
        self.e['policy']['required_approver_roles'].append('Risk Director')
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-07')
        self.e['approvals'].append(signature('RA-TEST-1','Risk Director'))
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-08')
        self.e['approvals'][-1]['decision']='Rejected'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-06')

    def test_signature_context_and_authority_are_validated(self):
        for field,value in [('assessment_id','RA-OTHER'),('policy_version','OLD'),('authorized',False)]:
            self.e=baseline('RA-TEST-1');self.e['approvals'][0][field]=value
            self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')
        self.e=baseline('RA-TEST-1');self.e['approvals'][0]['evidence_refs']=[]
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-07')

    def test_dual_signoff_requires_two_distinct_reviewers(self):
        control(self.e,'SG-13')
        self.e['approvals'][1]['reviewer']=self.e['approvals'][2]['reviewer']='Same reviewer'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-05')
        self.e['approvals'][2]['reviewer']='Different reviewer'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-08')

    def test_high_band_not_rejection_and_restricted_acceptance(self):
        hit(self.e,12,19,20)
        r=self.result();self.assertEqual(r['risk_assessment']['risk_band'],'High')
        self.assertEqual(r['final_decision']['rule_id'],'PI-08')
        high_industry(self.e)
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-10')

    def test_four_intermediate_entities_and_maximum_branch(self):
        for depths in ([0],[3],[4],[1,4]):
            analysis=analyze_ownership(graph(depths))
            self.assertEqual(analysis['intermediate_legal_entity_count'],max(depths))
        self.assertIs(rf_condition('RF-15',{'intermediate_legal_entity_count':3}),False)
        self.assertIs(rf_condition('RF-15',{'intermediate_legal_entity_count':4}),True)
        self.assertIsNone(rf_condition('RF-15',{'ownership_layers':6}))

    def test_ownership_cycles_and_large_graph_do_not_recurse_forever(self):
        self.e['ownership_structure']=graph([1],cycle=True)
        r=self.result();self.assertEqual(r['final_decision']['rule_id'],'PI-03')
        self.assertTrue(r['ownership_analysis']['cycle_detected'])
        self.assertEqual(analyze_ownership(graph([1200]))['intermediate_legal_entity_count'],1200)

    def test_mixed_stage_versions_and_invalid_upstream_status(self):
        self.e['upstream_contexts']={'RiskBand':{'assessment_id':'RA-TEST-1','policy_version':'OLD'}}
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')
        final=integrate({'hard_gate':'Clear','doc_gate':'Clear','risk_band':''},'Within',{'status':'Not Applicable'},self.e,self.cat)
        self.assertEqual(final['rule_id'],'PI-11')

    def test_input_errors_do_not_override_confirmed_rejection(self):
        self.e['input_errors']=['Stale external data'];hit(self.e,1)
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-01')

    def test_cli_expectation_mismatch_returns_failure(self):
        dataset=copy.deepcopy(self.dataset)
        dataset['test_cases']=dataset['test_cases'][:1]
        dataset['test_cases'][0]['expected']['final_rule_id']='PI-08'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'wrong.json';source.write_text(json.dumps(dataset))
            process=subprocess.run([sys.executable,str(ROOT/'kyc_poc.py'),'--json',str(source),'--check-expected'],capture_output=True,text=True)
            self.assertEqual(process.returncode,1)
            self.assertIn('final_rule_id',process.stderr)

    def test_malformed_factor_returns_input_hold_without_losing_gate_priority(self):
        self.e['risk_factors']['RF-20']['status']='Invalid'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')
        hit(self.e,1)
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-01')

    def test_selected_approval_control_rejection_is_a_required_veto(self):
        control(self.e,'SG-04')
        self.e['policy']['required_approver_roles']=['Compliance Officer']
        self.e['approvals'][-1]['decision']='Rejected'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-06')

    def test_invalid_policy_shape_produces_input_hold(self):
        self.e['policy']='invalid'
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')
        self.e=baseline('RA-TEST-1');self.e['risk_factors']['RF-20']['status']=[]
        self.assertEqual(self.result()['final_decision']['rule_id'],'PI-11')

    def test_cli_default_dataset_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'results.json'
            process=subprocess.run([sys.executable,str(ROOT/'kyc_poc.py'),'--check-expected','--output',str(output)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            results=json.loads(output.read_text());self.assertEqual(len(results),118)
            self.assertEqual(len(results[0]['audit']['workbook_sha256']),3)
            self.assertIn('PI-11',results[0]['audit']['source_rule_ids'])
            self.assertTrue(all('authorization_status' in r['final_decision'] for r in results))


if __name__=='__main__':unittest.main()
