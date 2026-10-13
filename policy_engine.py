"""Evidence-first implementation of the three IS4920 rule workbooks.

Workbook cells are policy data, never executable instructions. Missing facts are
unknown, not false. Review conclusions must identify their supporting evidence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = {'Low': 0, 'Medium': 1, 'High': 2}
PENDING = 'Pending'


@dataclass
class Rule:
    rule_id: str
    description: str
    condition: str
    outcome: str
    source: str
    points: int = 0
    kind: str = ''
    priority: int = 0
    evidence: str = ''


@dataclass
class Catalogue:
    rf: dict[str, Rule]
    dimensions: dict[str, Rule]
    appetite: dict[str, Rule]
    safeguards: dict[str, Rule]
    integration: dict[str, Rule]
    bands: list[tuple[str, int, int | None]]
    versions: dict[str, str]
    warnings: list[str] = field(default_factory=list)


def load_catalogue(directory: Path, rf21_mode: str = 'include') -> Catalogue:
    import openpyxl
    names = {
        'rf': 'IS4920_Integrated_HardGate_RiskFactors_RiskBand.xlsx',
        'aml': 'IS4920_AML_Appetite_Safeguards.xlsx',
        'pi': 'IS4920_Policy_Integration_Final_Step.xlsx',
    }
    books, versions = {}, {}
    for key, name in names.items():
        path = directory / name
        versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        books[key] = openpyxl.load_workbook(path, data_only=True)
    def rows(key, sheet, prefix):
        return [r for r in books[key][sheet].iter_rows(values_only=True)
                if isinstance(r[0], str) and re.fullmatch(prefix + r'-\d{2}', r[0])]
    try:
        rf = {}
        for r in rows('rf', 'Risk_Factors', 'RF'):
            if r[6] not in {'HardGate', 'DocGate', 'Score'}:
                raise ValueError(f'Unsupported factor type: {r[0]}')
            if r[6] == 'Score' and (not isinstance(r[4], (int, float)) or r[4] < 0):
                raise ValueError(f'Invalid score: {r[0]}')
            rf[r[0]] = Rule(r[0], r[2], r[3], str(r[4]), names['rf'],
                            int(r[4]) if r[6] == 'Score' else 0, r[6])
        warnings = []
        if 'RF-21' in rf and 'RF-22' not in rf:
            warnings.append('Workbook conflict: notes remove RF-21 and retain RF-22, '
                            f'but table contains RF-21 only. RF-21 mode={rf21_mode}; RF-22 not invented.')
        if rf21_mode == 'exclude':
            rf.pop('RF-21', None)
        elif rf21_mode != 'include':
            raise ValueError('rf21_mode must be exclude or include')
        dims, appetite, sg, pi = {}, {}, {}, {}
        for prefix, sheet in [('D1', 'D1_Ownership'), ('D2', 'D2_Industry'), ('D3', 'D3_Source_of_Funds')]:
            for r in rows('aml', sheet, prefix):
                dims[r[0]] = Rule(r[0], r[1], r[2], r[5].split('\n')[0], names['aml'], evidence=r[3])
        for r in rows('aml', 'Final_Appetite', 'FA'):
            appetite[r[0]] = Rule(r[0], r[1], r[2], r[5].split('\n')[0], names['aml'], evidence=r[3])
        for r in rows('aml', 'Safeguards', 'SG'):
            sg[r[0]] = Rule(r[0], r[1], r[4], r[7], names['aml'], evidence=r[6])
        warnings.append('User scope override: include SG-01 EDD from the AML workbook despite legacy final-workbook exclusion notes; SG-14/15 have no catalogue entries.')
        for r in rows('pi', 'Policy_Integration', 'PI'):
            pi[r[0]] = Rule(r[0], r[2], r[2], r[5].split('\n')[0], names['pi'], priority=int(r[1]), evidence=r[3])
        bands = []
        for r in books['rf']['Risk_Band'].iter_rows(values_only=True):
            if isinstance(r[0], str) and r[0].split(' / ')[0] in LEVELS:
                band = r[0].split(' / ')[0]
                nums = [int(n) for n in re.findall(r'\d+', r[1])]
                bands.append((band, nums[0], nums[1] if len(nums) == 2 else None))
        expected = {f'RF-{i:02}' for i in range(1, 21)}
        if not expected <= rf.keys() or len(dims) != 16 or len(appetite) != 5 or len(sg) != 13 or len(pi) != 11:
            raise ValueError('Workbook rule set is incomplete or incompatible with this engine')
        if bands != [('Low', 0, 4), ('Medium', 5, 9), ('High', 10, None)]:
            raise ValueError('Unexpected risk-band definitions; review engine compatibility')
        return Catalogue(rf, dims, appetite, sg, pi, bands, versions, warnings)
    finally:
        for book in books.values():
            book.close()


def yes(facts, *keys):
    return all(facts.get(key) is True for key in keys)


def no(facts, *keys):
    return all(facts.get(key) is False for key in keys)


def refs(record):
    value = record.get('evidence_refs', [])
    return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value)


def dimension_hits(prefix, record):
    """Facts represent documented reviewer conclusions, not guessed thresholds."""
    f = record.get('facts', {})
    if not isinstance(f, dict) or not yes(record, 'evidence_complete') or not refs(record):
        return [], PENDING
    hits = []
    if prefix == 'D1':
        indicators = ('complex_ownership', 'nominee_or_trust', 'cross_entity_or_border_complexity',
                      'control_rights_discrepancy', 'unusual_structure', 'material_unresolved_contradiction')
        if any(type(f.get(k)) is not bool for k in indicators):
            return [], PENDING
        if not yes(f, 'legal_entity_verified', 'ownership_links_verified', 'ubo_verified', 'control_verified'):
            return [], PENDING
        required_details = []
        if f['complex_ownership'] or f['nominee_or_trust'] or f['cross_entity_or_border_complexity'] or f['unusual_structure']:
            required_details.append('rationale_sufficient')
        if f['control_rights_discrepancy']: required_details.append('discrepancy_explained')
        if any(type(f.get(k)) is not bool for k in required_details):
            return [], PENDING
        if yes(f, 'complex_ownership', 'rationale_sufficient') and no(f, 'material_unresolved_contradiction'):
            hits.append('D1-02')
        if yes(f, 'nominee_or_trust', 'relevant_parties_verified', 'legal_documents_sufficient', 'rationale_sufficient'):
            hits.append('D1-03')
        if yes(f, 'cross_entity_or_border_complexity', 'materiality_policy_approved', 'rationale_sufficient') and no(f, 'material_unresolved_contradiction', 'control_rights_discrepancy', 'unusual_structure'):
            hits.append('D1-04')
        if yes(f, 'control_rights_discrepancy') and no(f, 'discrepancy_explained'):
            hits.append('D1-05')
        if yes(f, 'unusual_structure', 'review_completed') and no(f, 'rationale_sufficient'):
            hits.append('D1-06')
        if no(f, 'complex_ownership', 'nominee_or_trust', 'cross_entity_or_border_complexity', 'control_rights_discrepancy', 'unusual_structure', 'material_unresolved_contradiction'):
            hits.append('D1-01')
    elif prefix == 'D2':
        if any(type(f.get(k)) is not bool for k in ('regulated', 'material_discrepancy', 'material_high_risk_activity')):
            return ['D2-05'], PENDING
        if yes(f, 'material_discrepancy') or not yes(f, 'activity_verified', 'taxonomy_approved'):
            return ['D2-05'], PENDING
        licensed = f.get('regulated') is False or yes(f, 'licence_verified')
        if f.get('category') == 'Standard' and no(f, 'material_discrepancy', 'material_high_risk_activity') and licensed:
            hits.append('D2-01')
        if f.get('category') == 'Sensitive' and licensed and no(f, 'material_high_risk_activity'):
            hits.append('D2-02')
        if f.get('category') == 'HighRisk' and yes(f, 'activity_not_prohibited') and licensed:
            hits.append('D2-03')
        if yes(f, 'multiple_activities', 'material_high_risk_activity', 'materiality_policy_approved', 'high_risk_activity_verified', 'activity_not_prohibited') and licensed:
            hits.append('D2-04')
    else:
        if not yes(f, 'mandatory_evidence_authenticated') or any(type(f.get(k)) is not bool for k in ('material_anomaly', 'unresolved_discrepancy', 'complex_funding', 'unusual_subscription')):
            return ['D3-04'], PENDING
        if f['material_anomaly'] and (type(f.get('anomaly_explained')) is not bool or not yes(f, 'records_verified', 'review_completed')):
            return [], PENDING
        if yes(f, 'material_anomaly', 'records_verified', 'review_completed') and no(f, 'anomaly_explained'):
            hits.append('D3-03')
        reconciled = yes(f, 'source_event_identified', 'owner_identified', 'amount_dates_verified', 'funds_sufficient', 'records_reconciled') and no(f, 'unresolved_discrepancy')
        if reconciled and yes(f, 'own_account', 'records_verified') and no(f, 'complex_funding', 'unusual_subscription', 'material_anomaly'):
            hits.append('D3-01')
        if reconciled and yes(f, 'complex_funding', 'material_parties_verified', 'legal_basis_verified', 'flows_verified', 'arrangement_policy_permitted'):
            hits.append('D3-02')
        if reconciled and yes(f, 'unusual_subscription', 'materiality_policy_approved', 'exceptional_event_verified'):
            hits.append('D3-05')
    return hits, None if hits else PENDING


def evaluate_dimensions(evidence, cat):
    results = {}
    for prefix in ('D1', 'D2', 'D3'):
        record = evidence.get(prefix, {})
        hits, status = dimension_hits(prefix, record)
        level = PENDING if status == PENDING else max((cat.dimensions[x].outcome for x in hits), key=LEVELS.__getitem__)
        results[prefix] = {'level': level, 'rule_ids': hits, 'evidence_refs': record.get('evidence_refs', [])}
    levels = [d['level'] for d in results.values()]
    if PENDING in levels:
        appetite, fid = PENDING, 'FA-04'
    else:
        highest = max(levels, key=LEVELS.__getitem__)
        appetite, fid = {'Low': ('Within', 'FA-01'), 'Medium': ('Conditional', 'FA-02'), 'High': ('Restricted', 'FA-03')}[highest]
    return results, appetite, [fid, 'FA-05']


RF_CRITERIA = {
    'RF-01': ('all', ('prohibited_jurisdiction', 'official_verification_complete')),
    'RF-02': ('all', ('ubo_sanctions_match', 'screening_review_complete', 'false_positive_excluded')),
    'RF-03': ('all', ('regulated_activity', 'licence_missing_revoked_or_invalid', 'official_verification_complete', 'policy_prohibits_onboarding')),
    'RF-04': ('failed', ('ubo_verification_status',)),
    'RF-05': ('failed', ('control_verification_status',)),
    'RF-06': ('all', ('prohibited_or_illegal_activity', 'reliable_evidence_verified', 'approved_prohibition_met')),
    'RF-07': ('all', ('illegal_subscription_funds', 'reliable_evidence_verified', 'approved_prohibition_met')),
    'RF-08': ('all', ('fraudulent_identity_or_key_documents', 'independent_evidence_verified', 'approved_prohibition_met')),
    'RF-09': ('all', ('mandatory_document_missing',)),
    'RF-10': ('all', ('articles_shareholder_register_shareholding_conflict',)),
    'RF-11': ('all', ('ubo_unidentified_or_ownership_evidence_insufficient', 'reasonable_measures_completed')),
    'RF-12': ('all', ('registration_or_ubo_nationality_greylisted',)),
    'RF-13': ('all', ('ubo_confirmed_pep',)),
    'RF-14': ('all', ('ubo_pep_family_or_close_associate',)),
    'RF-15': ('layers', ('ownership_layers',)),
    'RF-16': ('all', ('nominee_or_trust_indirect_shareholding',)),
    'RF-17': ('all', ('principal_business_in_approved_high_risk_category',)),
    'RF-18': ('any', ('third_party_funding', 'non_own_name_account', 'borrowed_or_leveraged_funding')),
    'RF-19': ('all', ('incorporated_under_one_year', 'no_substantive_history_or_audited_accounts')),
    'RF-20': ('any', ('policy_defined_high_leverage', 'negative_cash_flow', 'assets_or_cash_insufficient_for_subscription')),
    'RF-21': ('jurisdictions', ('jurisdiction_count', 'complex_cross_border_or_offshore_arrangement')),
}


def rf_condition(fid, facts):
    """Three-valued predicates: incomplete facts never imply Clear."""
    method, keys = RF_CRITERIA[fid]
    values = [facts.get(k) for k in keys]
    if method == 'failed':
        return True if values[0] == 'Failed' else False if values[0] == 'Verified' else None
    if method == 'layers':
        depth = values[0]
        return depth >= 4 if type(depth) is int and depth >= 1 else None
    if method == 'jurisdictions':
        count, complex_structure = values
        if (type(count) is int and count >= 3) or complex_structure is True:
            return True
        return False if type(count) is int and count >= 1 and complex_structure is False else None
    if method == 'all':
        if any(v is False for v in values): return False
        return True if all(v is True for v in values) else None
    if any(v is True for v in values): return True
    return False if all(v is False for v in values) else None


def evaluate_rf(evidence, cat):
    records = evidence.get('risk_factors', {})
    unknown = set(records) - set(cat.rf)
    if unknown:
        raise ValueError(f'Unknown or excluded risk factor IDs: {sorted(unknown)}')
    hits, pending, assessments = [], [], []
    hard_pending, doc_pending = [], []
    for fid, rule in cat.rf.items():
        record = records.get(fid, {})
        if 'facts' in record:
            condition = rf_condition(fid, record['facts'])
            status = 'Hit' if condition is True else 'Clear' if condition is False else PENDING
            supplied = record.get('status')
            if supplied is not None and supplied != status:
                raise ValueError(f'{fid}: declared status contradicts predicate facts')
        else:
            status = record.get('status', PENDING)
        if status not in {'Hit', 'Clear', PENDING}:
            raise ValueError(f'{fid}: status must be Hit, Clear or Pending')
        if status != PENDING and (not refs(record) or record.get('verified') is not True):
            status = PENDING
        assessments.append({'rule_id': fid, 'status': status, 'evidence_refs': record.get('evidence_refs', [])})
        if status == PENDING:
            pending.append(fid)
            if rule.kind == 'HardGate': hard_pending.append(fid)
            if rule.kind == 'DocGate': doc_pending.append(fid)
        elif status == 'Hit':
            hits.append({'factor_id': fid, 'factor_type': rule.kind, 'points_applied': rule.points,
                         'action_type': rule.outcome, 'evidence_refs': record['evidence_refs']})
    hard = 'Confirmed Hit' if any(h['factor_type'] == 'HardGate' for h in hits) else PENDING if hard_pending else 'Clear'
    doc_hits = {h['factor_id'] for h in hits if h['factor_type'] == 'DocGate'}
    doc = 'Human Review' if 'RF-10' in doc_hits else 'Awaiting Information' if doc_hits else PENDING if doc_pending else 'Clear'
    score = sum(h['points_applied'] for h in hits)
    band = next(b for b, low, high in cat.bands if score >= low and (high is None or score <= high))
    # A confirmed prohibition is reported separately, never converted to High.
    final_band = PENDING if hard != 'Clear' or doc != 'Clear' or pending else band
    return {'hard_gate': hard, 'doc_gate': doc, 'total_score': score, 'score_band': band,
            'risk_band': final_band, 'hits': hits, 'pending_rule_ids': pending, 'rule_assessments': assessments}


def valid_approval(record, roles=None):
    return (record.get('decision') in {'Approved', 'Rejected'}
            and yes(record, 'authorized') and refs(record)
            and all(isinstance(record.get(k), str) and record[k].strip()
                    for k in ('reviewer', 'authority', 'timestamp', 'rationale'))
            and (roles is None or record.get('role') in roles))


def safeguard_candidates(dimensions, rf, appetite, evidence):
    hits = {h['factor_id'] for h in rf['hits']}
    dims = {r for d in dimensions.values() for r in d['rule_ids']}
    signals = evidence.get('signals', {})
    result = set()
    if appetite not in {'Conditional', 'Restricted'}:
        return result
    if (appetite == 'Restricted' or dims & {'D1-02', 'D1-05', 'D1-06', 'D2-03', 'D2-04', 'D3-03'}
            or len([h for h in rf['hits'] if h.get('factor_type') == 'Score']) >= 2
            or yes(signals, 'edd_required')):
        result.add('SG-01')
    if dims & {'D3-02', 'D3-03', 'D3-05', 'D2-03', 'D2-04'} or hits & {'RF-13', 'RF-17'}: result.add('SG-02')
    if 'RF-13' in hits or yes(signals, 'high_risk_relationship'): result.add('SG-03')
    if appetite == 'Restricted' or 'RF-13' in hits or yes(signals, 'high_risk_relationship'): result.add('SG-04')
    if dims & {'D3-02', 'D3-03'} and yes(signals, 'third_party_or_account_mismatch'): result.add('SG-05')
    if yes(signals, 'exposure_limit_reduces_risk'): result.add('SG-06')
    if yes(signals, 'service_restriction_exposure'): result.add('SG-07')
    result.add('SG-08')
    if 'RF-13' in hits or 'RF-12' in hits or yes(signals, 'rescreening_exposure'): result.add('SG-09')
    if appetite == 'Restricted' or yes(signals, 'high_risk_relationship'): result.add('SG-10')
    if dims & {'D1-02', 'D1-03', 'D1-04'} or hits & {'RF-15', 'RF-16'}: result.add('SG-11')
    if yes(signals, 'regulated_activity') or yes(evidence.get('D2', {}).get('facts', {}), 'regulated'): result.add('SG-12')
    if 'RF-13' in hits or yes(signals, 'dual_approval_required'): result.add('SG-13')
    return result


def evaluate_safeguards(dimensions, rf, appetite, evidence, cat):
    records = evidence.get('safeguards', {})
    unknown = set(records) - set(cat.safeguards)
    if unknown:
        raise ValueError(f'Unknown safeguard IDs: {sorted(unknown)}')
    candidates = safeguard_candidates(dimensions, rf, appetite, evidence)
    selected, unresolved = [], []
    for sid in sorted(candidates | set(records)):
        record = records.get(sid, {})
        # Applicability requires an approved policy and a documented trigger review.
        if type(record.get('applicable')) is not bool or record.get('policy_approved') is not True or not refs(record):
            unresolved.append(sid)
            continue
        if not record['applicable']:
            continue
        status = record.get('status', PENDING)
        if status not in {'Complete', 'Incomplete', PENDING}:
            raise ValueError(f'{sid}: invalid safeguard completion status')
        if status == 'Complete' and (not isinstance(record.get('owner'), str) or not record['owner'].strip() or not refs({'evidence_refs': record.get('completion_evidence_refs')})):
            status = PENDING
        approvals = evidence.get('approvals', [])
        if sid == 'SG-04' and status == 'Complete' and not any(valid_approval(a, {'Senior Compliance'}) and a['decision'] == 'Approved' for a in approvals):
            status = PENDING
        if sid == 'SG-13' and status == 'Complete':
            directors = {a['role']: a['reviewer'] for a in approvals
                         if valid_approval(a, {'Compliance Director', 'Risk Director'}) and a['decision'] == 'Approved'}
            if len(directors) != 2 or len(set(directors.values())) != 2:
                status = PENDING
        selected.append({'safeguard_id': sid, 'status': status, 'owner': record.get('owner'),
                         'evidence_refs': record['evidence_refs'],
                         'completion_evidence_refs': record.get('completion_evidence_refs', [])})
    if unresolved or any(x['status'] == PENDING for x in selected):
        status = PENDING
    elif any(x['status'] == 'Incomplete' for x in selected):
        status = 'Incomplete'
    elif selected:
        status = 'Complete'
    elif appetite == PENDING:
        status = PENDING
    else:
        status = 'Not Applicable'
    return {'status': status, 'selected': selected, 'unresolved_applicability': unresolved,
            'supported_rule_ids': sorted(cat.safeguards)}


def integrate(rf, appetite, sg, evidence, cat):
    """Final step consumes upstream statuses only; no recalculation."""
    policy = evidence.get('policy', {})
    approvals = evidence.get('approvals', [])
    required = policy.get('required_approver_roles')
    policy_defined = (yes(policy, 'approval_matrix_approved') and bool(policy.get('version'))
                      and isinstance(required, list) and bool(required)
                      and all(isinstance(r, str) and r for r in required))
    valid = [a for a in approvals if valid_approval(a)]
    relevant = [a for a in valid if isinstance(required, list) and a.get('role') in required]
    rejected = any(a['decision'] == 'Rejected' for a in relevant)
    approved = policy_defined and all(any(a.get('role') == role and a['decision'] == 'Approved' for a in relevant) for role in required)
    # Conflicting approvals need policy-owner resolution; do not pick the last record.
    inconsistent = any({a['decision'] for a in relevant if a.get('role') == role} == {'Approved', 'Rejected'} for role in (required or []))
    invalid_inputs = (rf.get('hard_gate') not in {'Confirmed Hit', 'Clear', PENDING}
                      or rf.get('doc_gate') not in {'Clear', 'Awaiting Information', 'Human Review', PENDING}
                      or rf.get('risk_band') not in {*LEVELS, PENDING}
                      or appetite not in {'Within', 'Conditional', 'Restricted', PENDING}
                      or sg.get('status') not in {'Not Applicable', 'Complete', 'Incomplete', PENDING})
    if rf.get('hard_gate') == 'Confirmed Hit': fid = 'PI-01'
    elif invalid_inputs: fid = 'PI-11'
    elif rf['hard_gate'] == PENDING: fid = 'PI-02'
    elif rf['doc_gate'] != 'Clear': fid = 'PI-03'
    elif rf['risk_band'] == PENDING or appetite == PENDING: fid = 'PI-04'
    elif appetite in {'Conditional', 'Restricted'} and sg['status'] in {'Incomplete', PENDING}: fid = 'PI-05'
    elif not policy_defined or inconsistent: fid = 'PI-11'
    elif appetite == 'Within' and (sg.get('selected') or any(h['factor_id'] == 'RF-13' for h in rf.get('hits', []))): fid = 'PI-11'
    elif rejected: fid = 'PI-06'
    elif sg['status'] not in {'Complete', 'Not Applicable'}: fid = 'PI-11'
    elif appetite == 'Restricted' and policy.get('restricted_acceptance_permitted') not in (True, False): fid = 'PI-11'
    elif appetite == 'Restricted' and policy.get('restricted_acceptance_permitted') is False:
        return {'rule_id': 'PI-10', 'result': 'REJECT — Policy Disallows Restricted Acceptance',
                'next_action': 'Record the applicable prohibition and policy basis.'}
    elif not approved: fid = 'PI-07'
    elif appetite == 'Within': fid = 'PI-08'
    elif appetite == 'Conditional': fid = 'PI-09'
    elif appetite == 'Restricted': fid = 'PI-10'
    else: fid = 'PI-11'
    rule = cat.integration[fid]
    return {'rule_id': fid, 'result': rule.outcome, 'rationale': rule.condition}


def assess_workflow(case, cat):
    evidence = case.workflow_evidence
    rf = evaluate_rf(evidence, cat)
    dimensions, appetite, appetite_rules = evaluate_dimensions(evidence, cat)
    sg = evaluate_safeguards(dimensions, rf, appetite, evidence, cat)
    final = integrate(rf, appetite, sg, evidence, cat)
    ids = [h['factor_id'] for h in rf['hits']] + rf['pending_rule_ids']
    ids += [r for d in dimensions.values() for r in d['rule_ids']] + appetite_rules
    ids += [s['safeguard_id'] for s in sg['selected']] + sg['unresolved_applicability'] + [final['rule_id']]
    return {'case': case.name, 'assessment_id': 'RA-' + case.application.application_id,
            'assessment_date': case.assessment_date or case.application.submission_date,
            'risk_assessment': rf, 'dimensions': dimensions, 'final_appetite': appetite,
            'appetite_rule_ids': appetite_rules, 'safeguards': sg, 'final_decision': final,
            'edd_required': (True if any(x['safeguard_id'] == 'SG-01' for x in sg['selected'])
                             else None if 'SG-01' in sg['unresolved_applicability'] or appetite == PENDING else False),
            'edd_status': next((x['status'] for x in sg['selected'] if x['safeguard_id'] == 'SG-01'),
                               PENDING if 'SG-01' in sg['unresolved_applicability'] or appetite == PENDING else 'Not Applicable'),
            'audit': {'source_rule_ids': list(dict.fromkeys(ids)), 'workbook_sha256': cat.versions,
                      'policy_version': evidence.get('policy', {}).get('version'),
                      'evaluated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                      'input_evidence': evidence,
                      'approval_records': evidence.get('approvals', []), 'warnings': cat.warnings}}
