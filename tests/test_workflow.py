"""Synthetic verified evidence; never a substitute for the missing production JSON."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kyc_poc import DEFAULT_RULES, builtin_cases, case_from_json
from policy_engine import (RF_CRITERIA, assess_workflow, evaluate_dimensions, evaluate_rf,
                           integrate, load_catalogue, safeguard_candidates)


def baseline(cat):
    return {
        'risk_factors': {fid: {'status': 'Clear', 'verified': True, 'evidence_refs': ['synthetic-reviewed-record']}
                         for fid in cat.rf},
        'D1': {'evidence_complete': True, 'evidence_refs': ['synthetic-ownership'], 'facts': {
            'legal_entity_verified': True, 'ownership_links_verified': True, 'ubo_verified': True,
            'control_verified': True, 'complex_ownership': False, 'nominee_or_trust': False,
            'cross_entity_or_border_complexity': False, 'control_rights_discrepancy': False,
            'unusual_structure': False, 'material_unresolved_contradiction': False}},
        'D2': {'evidence_complete': True, 'evidence_refs': ['synthetic-business'], 'facts': {
            'activity_verified': True, 'taxonomy_approved': True, 'category': 'Standard',
            'material_discrepancy': False, 'material_high_risk_activity': False, 'regulated': False}},
        'D3': {'evidence_complete': True, 'evidence_refs': ['synthetic-funds'], 'facts': {
            'mandatory_evidence_authenticated': True, 'source_event_identified': True,
            'owner_identified': True, 'amount_dates_verified': True, 'funds_sufficient': True,
            'records_reconciled': True, 'records_verified': True, 'unresolved_discrepancy': False,
            'own_account': True, 'complex_funding': False, 'unusual_subscription': False, 'material_anomaly': False}},
        'policy': {'version': 'synthetic-policy-v1', 'approval_matrix_approved': True,
                   'required_approver_roles': ['Compliance Officer']},
        'approvals': [], 'safeguards': {}, 'signals': {},
    }


def approval(role='Compliance Officer', decision='Approved', name='Reviewer A'):
    return {'decision': decision, 'authorized': True, 'role': role, 'reviewer': name,
            'authority': 'synthetic-approved-matrix', 'timestamp': '2026-10-13T00:00:00Z',
            'rationale': 'Synthetic documented review', 'evidence_refs': ['synthetic-signoff']}


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.cat = load_catalogue(DEFAULT_RULES)

    def setUp(self): self.e = baseline(self.cat)

    def run_case(self):
        case = builtin_cases()[0]
        case.workflow_evidence = self.e
        return assess_workflow(case, self.cat)

    def hit(self, fid): self.e['risk_factors'][fid]['status'] = 'Hit'

    def test_workbook_catalogues_and_user_resolution(self):
        self.assertEqual(len(self.cat.rf), 21)
        self.assertEqual(self.cat.rf['RF-21'].points, 3)
        self.assertNotIn('RF-22', self.cat.rf)
        self.assertEqual(len(self.cat.dimensions), 16)
        self.assertEqual(set(self.cat.safeguards), {f'SG-{i:02}' for i in range(2, 14)})
        self.assertEqual(self.cat.integration['PI-10'].priority, 10)
        self.assertTrue(self.cat.warnings)

    def test_original_inputs_do_not_become_approved(self):
        for case in builtin_cases():
            r = assess_workflow(case, self.cat)
            self.assertEqual(r['final_decision']['rule_id'], 'PI-02')
            self.assertEqual(r['final_appetite'], 'Pending')

    def test_all_hard_gates_override_every_signal_without_points(self):
        for i in range(1, 9):
            with self.subTest(factor=i):
                self.e = baseline(self.cat)
                self.hit(f'RF-{i:02}')
                self.hit('RF-13')
                r = self.run_case()
                self.assertEqual(r['risk_assessment']['total_score'], 5)
                self.assertEqual(r['final_decision']['result'], 'REJECT')
                self.assertEqual(r['risk_assessment']['risk_band'], 'Pending')

    def test_unverified_hit_holds_instead_of_rejecting(self):
        self.hit('RF-02')
        self.e['risk_factors']['RF-02']['verified'] = False
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-02')
        self.e['risk_factors']['RF-02']['verified'] = True
        self.e['risk_factors']['RF-02']['evidence_refs'] = []
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-02')

    def test_document_gates_do_not_score(self):
        for fid, expected in [('RF-09', 'Awaiting Information'), ('RF-10', 'Human Review'), ('RF-11', 'Awaiting Information')]:
            with self.subTest(factor=fid):
                self.e = baseline(self.cat); self.hit(fid)
                r = self.run_case()
                self.assertEqual(r['risk_assessment']['doc_gate'], expected)
                self.assertEqual(r['risk_assessment']['total_score'], 0)
                self.assertEqual(r['final_decision']['rule_id'], 'PI-03')

    def test_score_boundaries_and_high_does_not_reject(self):
        combinations = [([], 0, 'Low'), (['RF-14'], 4, 'Low'), (['RF-13'], 5, 'Medium'),
                        (['RF-13', 'RF-14'], 9, 'Medium'), (['RF-12', 'RF-16', 'RF-17'], 10, 'High')]
        for ids, score, band in combinations:
            with self.subTest(score=score):
                self.e = baseline(self.cat)
                for fid in ids: self.hit(fid)
                r = self.run_case()
                self.assertEqual(r['risk_assessment']['total_score'], score)
                self.assertEqual(r['risk_assessment']['risk_band'], band)
                self.assertEqual(r['final_appetite'], 'Within')
                self.assertEqual(r['final_decision']['rule_id'], 'PI-11' if 'RF-13' in ids else 'PI-07')
                self.e['approvals'] = [approval()]
                self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-11' if 'RF-13' in ids else 'PI-08')

    def test_unknown_score_is_pending_not_low(self):
        del self.e['risk_factors']['RF-20']
        r = self.run_case()
        self.assertEqual(r['risk_assessment']['risk_band'], 'Pending')
        self.assertEqual(r['final_decision']['rule_id'], 'PI-04')

    def test_each_dimension_rule(self):
        updates = {
            'D1-01': {},
            'D1-02': {'complex_ownership': True, 'rationale_sufficient': True},
            'D1-03': {'nominee_or_trust': True, 'relevant_parties_verified': True, 'legal_documents_sufficient': True, 'rationale_sufficient': True},
            'D1-04': {'cross_entity_or_border_complexity': True, 'materiality_policy_approved': True, 'rationale_sufficient': True},
            'D1-05': {'control_rights_discrepancy': True, 'discrepancy_explained': False},
            'D1-06': {'unusual_structure': True, 'review_completed': True, 'rationale_sufficient': False},
            'D2-01': {},
            'D2-02': {'category': 'Sensitive', 'regulated': True, 'licence_verified': True},
            'D2-03': {'category': 'HighRisk', 'activity_not_prohibited': True},
            'D2-04': {'multiple_activities': True, 'material_high_risk_activity': True, 'materiality_policy_approved': True, 'high_risk_activity_verified': True, 'activity_not_prohibited': True},
            'D2-05': {'material_discrepancy': True},
            'D3-01': {},
            'D3-02': {'complex_funding': True, 'own_account': False, 'material_parties_verified': True, 'legal_basis_verified': True, 'flows_verified': True, 'arrangement_policy_permitted': True},
            'D3-03': {'material_anomaly': True, 'review_completed': True, 'anomaly_explained': False},
            'D3-04': {'mandatory_evidence_authenticated': False},
            'D3-05': {'unusual_subscription': True, 'materiality_policy_approved': True, 'exceptional_event_verified': True},
        }
        for rid, facts in updates.items():
            with self.subTest(rule=rid):
                e = baseline(self.cat); prefix = rid[:2]; e[prefix]['facts'].update(facts)
                dimensions, _, _ = evaluate_dimensions(e, self.cat)
                self.assertIn(rid, dimensions[prefix]['rule_ids'])
                expected = self.cat.dimensions[rid].outcome
                self.assertEqual(dimensions[prefix]['level'], 'Pending' if expected.startswith('Pending') else expected)

    def test_pending_precedes_high_and_max_not_sum(self):
        self.e['D1']['facts'].update(complex_ownership=True, nominee_or_trust=True, rationale_sufficient=True,
                                    relevant_parties_verified=True, legal_documents_sufficient=True)
        r = self.run_case()
        self.assertEqual(r['dimensions']['D1']['level'], 'Medium')
        self.assertEqual(r['final_appetite'], 'Conditional')
        self.e['D1']['facts'].update(control_rights_discrepancy=True, discrepancy_explained=False)
        self.assertEqual(self.run_case()['final_appetite'], 'Restricted')
        self.e['D3']['evidence_complete'] = False
        self.assertEqual(self.run_case()['final_appetite'], 'Pending')
        del self.e['D1']['facts']['unusual_structure']
        self.assertEqual(self.run_case()['dimensions']['D1']['level'], 'Pending')

    def conditional(self):
        self.e['D1']['facts'].update(complex_ownership=True, rationale_sufficient=True)

    def complete_controls(self, ids):
        for sid in ids:
            self.e['safeguards'][sid] = {'applicable': True, 'policy_approved': True,
                'evidence_refs': ['synthetic-trigger-policy'], 'status': 'Complete', 'owner': 'Control owner',
                'completion_evidence_refs': ['synthetic-completion']}

    def test_only_applicable_controls_hold_and_never_lower_appetite(self):
        self.conditional()
        r = self.run_case()
        self.assertEqual(r['final_decision']['rule_id'], 'PI-05')
        self.assertEqual(set(r['safeguards']['unresolved_applicability']), {'SG-08', 'SG-11'})
        self.complete_controls(['SG-08', 'SG-11'])
        self.e['approvals'] = [approval()]
        r = self.run_case()
        self.assertEqual(r['final_appetite'], 'Conditional')
        self.assertEqual(r['final_decision']['rule_id'], 'PI-09')
        self.assertEqual(len(r['safeguards']['selected']), 2)
        self.assertFalse(r['edd_required'])
        self.e['safeguards']['SG-11']['status'] = 'Incomplete'
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-05')

    def test_approved_not_applicable_controls(self):
        self.conditional()
        for sid in ['SG-08', 'SG-11']:
            self.e['safeguards'][sid] = {'applicable': False, 'policy_approved': True, 'evidence_refs': ['synthetic-nonapplicability']}
        self.e['approvals'] = [approval()]
        r = self.run_case()
        self.assertEqual(r['safeguards']['status'], 'Not Applicable')
        self.assertEqual(r['final_decision']['rule_id'], 'PI-09')

    def test_restricted_route_requires_explicit_policy_and_senior_approval(self):
        self.e['D2']['facts'].update(category='HighRisk', activity_not_prohibited=True)
        self.complete_controls(['SG-02', 'SG-04', 'SG-08', 'SG-10'])
        self.e['approvals'] = [approval(), approval('Senior Compliance', name='Senior Reviewer')]
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-11')
        self.e['policy']['restricted_acceptance_permitted'] = True
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-10')
        self.e['policy']['restricted_acceptance_permitted'] = False
        self.assertTrue(self.run_case()['final_decision']['result'].startswith('REJECT'))
        self.e['approvals'] = [approval()]
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-05')

    def test_dual_signature_must_have_two_distinct_directors(self):
        self.conditional(); self.hit('RF-13')
        self.complete_controls(['SG-02', 'SG-03', 'SG-04', 'SG-08', 'SG-09', 'SG-11', 'SG-13'])
        self.e['approvals'] = [approval(), approval('Senior Compliance'), approval('Compliance Director'), approval('Risk Director')]
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-05')
        self.e['approvals'][-1]['reviewer'] = 'Reviewer B'
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-09')

    def test_officer_rejection_and_inconsistent_or_unsigned_approval(self):
        self.e['approvals'] = [approval(decision='Rejected')]
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-06')
        self.e['approvals'].append(approval())
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-11')
        self.e['approvals'] = [approval()]; self.e['approvals'][0]['authorized'] = False
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-07')
        del self.e['policy']['approval_matrix_approved']
        self.assertEqual(self.run_case()['final_decision']['rule_id'], 'PI-11')

    def test_excluded_edd_and_unknown_rule_fail_loudly(self):
        self.e['safeguards']['SG-01'] = {}
        with self.assertRaises(ValueError): self.run_case()
        self.e['safeguards'].clear(); self.e['risk_factors']['RF-22'] = {}
        with self.assertRaises(ValueError): self.run_case()

    def test_all_rf_predicates_from_verified_facts(self):
        for fid, (method, keys) in RF_CRITERIA.items():
            with self.subTest(rule=fid):
                self.e = baseline(self.cat)
                facts = {key: True for key in keys}
                if method == 'failed': facts[keys[0]] = 'Failed'
                if method == 'layers': facts[keys[0]] = 4
                if method == 'jurisdictions': facts[keys[0]] = 3
                self.e['risk_factors'][fid] = {'facts': facts, 'verified': True, 'evidence_refs': ['synthetic-rule-evidence']}
                r = evaluate_rf(self.e, self.cat)
                self.assertIn(fid, {h['factor_id'] for h in r['hits']})
                self.e['risk_factors'][fid]['facts'] = {}
                self.assertIn(fid, evaluate_rf(self.e, self.cat)['pending_rule_ids'])

    def test_numeric_complexity_boundaries(self):
        for fid, cases in [('RF-15', [(3, False), (4, True)]), ('RF-21', [(2, False), (3, True)])]:
            for value, hit in cases:
                method, keys = RF_CRITERIA[fid]
                facts = {keys[0]: value}
                if fid == 'RF-21': facts[keys[1]] = False
                self.e['risk_factors'][fid] = {'facts': facts, 'verified': True, 'evidence_refs': ['synthetic-depth']}
                ids = {h['factor_id'] for h in evaluate_rf(self.e, self.cat)['hits']}
                self.assertEqual(fid in ids, hit)

    def test_contradictory_rf_conclusion_is_rejected(self):
        self.e['risk_factors']['RF-02'].update(facts={'ubo_sanctions_match': True, 'screening_review_complete': True, 'false_positive_excluded': True})
        with self.assertRaises(ValueError): self.run_case()

    def test_integration_reads_upstream_without_recalculating(self):
        rf = {'hard_gate': 'Clear', 'doc_gate': 'Clear', 'risk_band': 'High'}
        self.e['approvals'] = [approval()]
        final = integrate(rf, 'Within', {'status': 'Not Applicable'}, self.e, self.cat)
        self.assertEqual(final['rule_id'], 'PI-08')

    def test_all_safeguard_trigger_routes(self):
        scenarios = {
            'SG-02': ({'D3-03'}, set(), {}),
            'SG-03': (set(), {'RF-13'}, {}),
            'SG-04': (set(), {'RF-13'}, {}),
            'SG-05': ({'D3-02'}, set(), {'third_party_or_account_mismatch': True}),
            'SG-06': (set(), set(), {'exposure_limit_reduces_risk': True}),
            'SG-07': (set(), set(), {'service_restriction_exposure': True}),
            'SG-08': (set(), set(), {}),
            'SG-09': (set(), {'RF-12'}, {}),
            'SG-10': (set(), set(), {'high_risk_relationship': True}),
            'SG-11': ({'D1-03'}, set(), {}),
            'SG-12': (set(), set(), {'regulated_activity': True}),
            'SG-13': (set(), set(), {'dual_approval_required': True}),
        }
        for sid, (dims, hits, signals) in scenarios.items():
            with self.subTest(safeguard=sid):
                candidates = safeguard_candidates({'test': {'rule_ids': list(dims)}},
                    {'hits': [{'factor_id': fid} for fid in hits]}, 'Conditional', {'signals': signals})
                self.assertIn(sid, candidates)
                self.assertFalse({'SG-01', 'SG-14', 'SG-15'} & candidates)
        self.assertFalse(safeguard_candidates({}, {'hits': []}, 'Within', {}))

    def test_invalid_upstream_status_cannot_approve(self):
        self.e['approvals'] = [approval()]
        for invalid in ['Unknown', '', None]:
            final = integrate({'hard_gate': 'Clear', 'doc_gate': 'Clear', 'risk_band': invalid},
                              'Within', {'status': 'Not Applicable'}, self.e, self.cat)
            self.assertEqual(final['rule_id'], 'PI-11')

    def test_new_39_case_dataset_matches_independent_expectations(self):
        path = DEFAULT_RULES.parent / 'test_cases_data.json'
        dataset = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(dataset['dataset_type'], 'synthetic')
        self.assertEqual(len(dataset['test_cases']), 39)
        seen_ids, seen_factors, seen_final_rules = set(), set(), set()
        for item in dataset['test_cases']:
            with self.subTest(case=item['test_case_id']):
                self.assertTrue(item['synthetic'])
                self.assertNotIn(item['test_case_id'], seen_ids)
                seen_ids.add(item['test_case_id'])
                result = assess_workflow(case_from_json(item), self.cat)
                expected = item['expected']
                self.assertEqual(result['final_decision']['rule_id'], expected['final_rule_id'])
                self.assertEqual(result['final_appetite'], expected['final_appetite'])
                self.assertEqual(result['risk_assessment']['total_score'], expected['total_score'])
                seen_final_rules.add(result['final_decision']['rule_id'])
                hits = {h['factor_id'] for h in result['risk_assessment']['hits']}
                seen_factors.update(hits)
                if 'risk_factor_hit' in expected:
                    self.assertIn(expected['risk_factor_hit'], hits)
                if 'dimension_rule_hit' in expected:
                    rid = expected['dimension_rule_hit']
                    self.assertIn(rid, result['dimensions'][rid[:2]]['rule_ids'])
        self.assertEqual(seen_factors, set(self.cat.rf))
        self.assertEqual(seen_final_rules, set(self.cat.integration))

    def test_cli_external_json_and_audit(self):
        case = {'test_case_id': 'synthetic-case', 'company': 'Fixture', 'data': {
            'Application': {'application_id': 'fixture-1', 'submission_date': '2026-10-13'},
            'Industry': {'industry_id': 'fixture-ind', 'industry_name': 'Fixture', 'industry_tier': 'I1'},
            'party1': {'party_id': 'fixture-party'}, 'WorkflowEvidence': self.e}}
        self.e['approvals'] = [approval()]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'cases.json', Path(tmp)/'result.json'
            src.write_text(json.dumps({'test_cases': [case]}))
            process = subprocess.run([sys.executable, 'kyc_poc.py', '--json', str(src), '--output', str(out)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            r = json.loads(out.read_text())[0]
            self.assertEqual(r['final_decision']['rule_id'], 'PI-08')
            self.assertEqual(len(r['audit']['workbook_sha256']), 3)
            self.assertIn('RF-21', self.cat.rf)
            self.assertIn('PI-08', r['audit']['source_rule_ids'])


if __name__ == '__main__': unittest.main()
