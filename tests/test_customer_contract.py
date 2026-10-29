"""Customer report contract, SQL projections and fail-closed input regressions."""
import copy
import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from customer_ontology import digest, snapshot_id, organization_chart
from database_io import export_graph, export_rows
from kyc_poc import case_from_json, DEFAULT_RULES
from policy_engine import assess_workflow, load_catalogue
from workflow_graph import build_workflow_graph

ROOT = DEFAULT_RULES.parent


class CustomerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalogue(DEFAULT_RULES)
        cls.template = json.loads((ROOT/'examples/customer-input.json').read_text())['test_cases'][0]
        cls.validator = Draft202012Validator(json.loads((ROOT/'schemas/report-payload.schema.json').read_text()), format_checker=FormatChecker())

    def assess(self, item=None):
        return assess_workflow(case_from_json(item or self.template), self.cat)

    def changed(self):
        return copy.deepcopy(self.template)

    def codes(self, item):
        return {x['code'] for x in self.assess(item)['report_payload']['quality']['issues']}

    def test_complete_synthetic_case_schema_and_preserved_roles(self):
        item = self.changed();before = copy.deepcopy(item)
        r = self.assess(item);p=r['report_payload']
        self.validator.validate(p)
        self.assertEqual(item,before)
        self.assertEqual(p['quality']['status'],'ReadyForReview')
        self.assertEqual(p['customer_summary']['party1'],item['data']['party1'])
        self.assertEqual(p['customer_summary']['party2'],item['data']['party2'])
        self.assertEqual(p['customer_summary']['party1_entity_type'],'LegalEntity')
        self.assertEqual(p['customer_decision']['scope'],'CustomerAMLOnly')
        self.assertEqual(p['organization_chart']['indirect_interests'][0]['percentage'],'100')
        self.assertEqual(p['screening']['status'],'Complete')
        self.assertEqual(r['safeguards'],p['safeguards'])

    def test_graph_and_batch_business_outputs_match(self):
        a=build_workflow_graph().invoke({'case':self.template})['result'];b=self.assess()
        for r in (a,b):
            r['audit'].pop('evaluated_at')
            r['report_payload']['run_manifest'].pop('evaluated_at')
            r['report_payload']['run_manifest'].pop('run_id')
        self.assertEqual(a,b)

    def test_unique_runs_and_stable_input_hash(self):
        a=self.assess()['report_payload'];b=self.assess()['report_payload']
        self.assertNotEqual(a['run_manifest']['run_id'],b['run_manifest']['run_id'])
        self.assertEqual(a['run_manifest']['input_snapshot_hash'],b['run_manifest']['input_snapshot_hash'])

    def test_new_snapshot_invalidates_old_approval(self):
        item=self.changed();item['data']['party1']['full_name']='Changed legal name'
        r=self.assess(item)
        self.assertIn('APPROVAL_BINDING',self.codes(item))
        self.assertEqual(r['final_decision']['rule_id'],'PI-11')
        self.assertEqual(r['report_payload']['quality']['status'],'NeedsReview')

    def test_invalid_date_and_expired_approval(self):
        for field,value in [('timestamp','not-a-date'),('valid_until','2026-01-01T00:00:00Z')]:
            item=self.changed();item['data']['WorkflowEvidence']['approvals'][0][field]=value
            self.assertIn('APPROVAL_BINDING',self.codes(item))

    def test_different_rule_pack_scope_or_version(self):
        for field,value in [('rule_pack_hash','0'*64),('organization_id','OTHER'),('assessment_version',2),('withdrawn',True)]:
            item=self.changed();item['data']['WorkflowEvidence']['approvals'][0][field]=value
            self.assertIn('APPROVAL_BINDING',self.codes(item))

    def test_evidence_ref_must_resolve(self):
        item=self.changed();item['data']['WorkflowEvidence']['D1']['evidence_refs']=['missing://never-registered']
        self.assertIn('UNRESOLVED_EVIDENCE',self.codes(item))
        self.assertEqual(self.assess(item)['final_decision']['rule_id'],'PI-11')

    def test_unverified_expired_and_conflicted_evidence(self):
        for patch,code in [({'verification_status':'Received'},'UNVERIFIED_EVIDENCE'),
                           ({'valid_until':'2020-01-01'},'EXPIRED_EVIDENCE'),
                           ({'conflicts_with':['other-evidence']},'EVIDENCE_CONFLICT')]:
            item=self.changed();item['data']['CustomerAssessment']['evidence_records'][0].update(patch)
            self.assertIn(code,self.codes(item))

    def test_invalid_schema_produces_blocked_report(self):
        item=self.changed();item['data']['CustomerAssessment']['context']['as_of']='not-a-date'
        r=self.assess(item)
        self.assertIn('SCHEMA',self.codes(item))
        self.validator.validate(r['report_payload'])
        self.assertEqual(r['final_decision']['rule_id'],'PI-11')

    def test_screening_failure_unrun_and_unresolved_candidate(self):
        for status,code in [('Failed','SCREENING_INCOMPLETE'),('NotRun','SCREENING_INCOMPLETE')]:
            item=self.changed();item['data']['CustomerAssessment']['screening_runs'][0]['execution_status']=status
            self.assertIn(code,self.codes(item))
        item=self.changed();item['data']['CustomerAssessment']['screening_runs'][0]['candidates']=[{'candidate_id':'MATCH-1','disposition':'Pending'}]
        self.assertIn('SCREENING_INCOMPLETE',self.codes(item))

    def test_later_failed_attempt_does_not_reuse_old_clear(self):
        item=self.changed();runs=item['data']['CustomerAssessment']['screening_runs']
        attempt=copy.deepcopy(runs[0]);attempt.update(screening_id='RETRY',attempted_at='2026-10-29T11:00:00Z',execution_status='Failed')
        attempt.pop('screened_at');runs.append(attempt)
        p=self.assess(item)['report_payload']
        self.assertEqual(p['screening']['subjects'][0]['status'],'Failed')

    def test_confirmed_match_requires_policy_review(self):
        item=self.changed();item['data']['CustomerAssessment']['screening_runs'][0]['candidates']=[{
            'candidate_id':'MATCH-1','disposition':'Confirmed','reviewer':'demo','reviewed_at':'2026-10-29T10:00:00Z','evidence_refs':['demo-evidence']}]
        self.assertIn('SCREENING_REVIEW',self.codes(item))
        self.assertEqual(self.assess(item)['final_decision']['rule_id'],'PI-11')

    def test_missing_subject_and_unknown_subject(self):
        item=self.changed();item['data']['CustomerAssessment']['screening_requirements'].pop()
        self.assertIn('SCREENING_SCOPE_GAP',self.codes(item))
        item=self.changed();item['data']['CustomerAssessment']['screening_runs'][0]['party_id']='UNKNOWN'
        self.assertIn('SCREENING_SUBJECT',self.codes(item))

    def test_relationship_validation_and_unknown_branches(self):
        for pct,code in [(150,'SCHEMA'),(50,'OWNERSHIP_GAP')]:
            item=self.changed();item['data']['CustomerAssessment']['party_relationships'][0]['shareholding_percentage']=pct
            self.assertIn(code,self.codes(item))
        item=self.changed();item['data']['CustomerAssessment']['party_relationships'][0]['from_party_id']='UNKNOWN'
        self.assertIn('RELATIONSHIP_ENDPOINT',self.codes(item))

    def test_multiple_paths_sum_and_cycles_are_not_certified(self):
        parties=[{'party_id':'A','entity_type':'LegalEntity'},{'party_id':'B','entity_type':'LegalEntity'},{'party_id':'N','entity_type':'NaturalPerson'}]
        def edge(a,b,p):return {'from_party_id':a,'to_party_id':b,'relationship_type':'ownership','shareholding_percentage':p}
        chart=organization_chart(parties,[edge('N','A',40),edge('B','A',60),edge('N','B',100)],'A')
        self.assertEqual(chart['indirect_interests'],[{'party_id':'N','percentage':'100.0'}])
        self.assertEqual(len(chart['calculation_paths']),2)
        chart=organization_chart(parties,[edge('B','A',100),edge('A','B',100)],'A')
        self.assertEqual(chart['status'],'Pending')
        self.assertTrue(any('cycle' in x for x in chart['gaps']))

    def test_cross_module_funding_conflict(self):
        item=self.changed();w=item['data']['WorkflowEvidence'];w['D3']['facts']['own_account']=True
        w['signals']['third_party_or_account_mismatch']=True
        self.assertIn('FUNDING_CONFLICT',self.codes(item))

    def test_sql_and_graph_share_run_identity(self):
        r=self.assess();tables=export_rows(r);graph=export_graph(r)
        run_id=r['report_payload']['run_manifest']['run_id']
        for table in tables.values():
            for row in table:
                self.assertEqual(row['run_id'],run_id)
                self.assertEqual(row['case_id'],'DEMO-CASE')
        self.assertEqual(graph['run_id'],run_id)
        self.assertEqual(graph['edges'],r['report_payload']['organization_chart']['edges'])
        json.dumps(tables,allow_nan=False)

    def test_legacy_output_is_not_claimed_ready(self):
        item=json.loads((ROOT/'test_cases_data.json').read_text())['test_cases'][0]
        r=self.assess(item);p=r['report_payload'];self.validator.validate(p)
        self.assertEqual(p['quality']['mode'],'Legacy')
        self.assertEqual(p['quality']['status'],'NeedsReview')
        self.assertEqual(p['screening']['status'],'NotAssessed')
        self.assertEqual(r['final_decision']['rule_id'],item['expected']['final_rule_id'])

    def test_changed_rule_pack_requires_compatibility_review(self):
        changed = copy.deepcopy(self.cat)
        changed.versions['IS4920_AML_Appetite_Safeguards.xlsx'] = '0'*64
        r = assess_workflow(case_from_json(self.template), changed)
        self.assertIn('RULE_PACK_COMPATIBILITY', {x['code'] for x in r['report_payload']['quality']['issues']})

    def test_string_booleans_are_rejected_in_customer_input(self):
        item=self.changed();item['data']['party1']['is_ubo']='false'
        self.assertIn('BOOLEAN_TYPE',self.codes(item))

    def test_missing_customer_d3_facts_remain_pending(self):
        item=self.changed();item['data']['WorkflowEvidence']['D3']['facts'].pop('funds_sufficient',None)
        r=self.assess(item)
        self.assertEqual(r['dimensions']['D3']['level'],'Pending')
        self.assertEqual(r['final_appetite'],'Pending')
        self.assertEqual(r['report_payload']['funding_applicability']['product_suitability'],'OutOfScope')
        self.assertTrue(any(x['related_id']=='D3' for x in r['report_payload']['outstanding_items']))

    def test_null_customer_contract_cannot_silently_select_legacy(self):
        item=self.changed();item['data']['CustomerAssessment']=None
        r=self.assess(item)
        self.assertEqual(r['report_payload']['quality']['mode'],'Customer')
        self.assertIn('SCHEMA',{x['code'] for x in r['report_payload']['quality']['issues']})

    def test_malformed_fact_record_blocks_without_claiming_verified(self):
        item=self.changed();item['data']['WorkflowEvidence']['D2']['facts']=None
        r=self.assess(item)
        self.assertNotIn(r['final_decision']['rule_id'],{'PI-08','PI-09','PI-10'})
        self.assertEqual(r['report_payload']['quality']['status'],'NeedsReview')

    def test_confirmed_match_can_be_bound_to_reviewed_factor(self):
        item=self.changed();c=item['data']['CustomerAssessment'];w=item['data']['WorkflowEvidence']
        run=c['screening_runs'][1]
        run['screening_type']='PEP';c['screening_requirements'][1]['screening_type']='PEP'
        run['candidates']=[{'candidate_id':'PEP-1','disposition':'Confirmed','reviewer':'demo','reviewed_at':'2026-10-29T10:00:00Z','evidence_refs':['demo-evidence']}]
        run['policy_review']={'reviewer':'demo','reviewed_at':'2026-10-29T10:30:00Z','policy_version':w['policy']['version'],'factor_ids':['RF-13'],'evidence_refs':['demo-evidence']}
        w['risk_factors']['RF-13']={'facts':{'ubo_confirmed_pep':True},'status':'Hit','verified':True,'evidence_refs':['demo-evidence']}
        self.assertNotIn('SCREENING_REVIEW',self.codes(item))
        self.assertIn('RF-13',{x['factor_id'] for x in self.assess(item)['risk_assessment']['hits']})
