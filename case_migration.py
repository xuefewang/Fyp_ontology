"""Migrate uploaded case assertions without inventing policy or signed approvals.

JSON-pointer references identify the supplied test inputs, not independently
verified regulatory records. Original data remains unchanged.
"""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path


def flag(record, key):
    v = record.get(key)
    return v if type(v) is bool else None


def all_of(*values):
    if any(v is False for v in values): return False
    return True if all(v is True for v in values) else None


def any_of(*values):
    if any(v is True for v in values): return True
    return False if all(v is False for v in values) else None


def inverse(v): return not v if type(v) is bool else None


def workflow_from_case(item):
    d = item['data']
    sf, ind = d.get('SourceOfFunds', {}), d.get('Industry', {})
    parties = [d.get('party1', {})] + d.get('party2', [])
    ubos = [p for p in parties if p.get('is_ubo') is True]
    docs, watches = d.get('Document', []), d.get('WatchList', [])
    pointer = f"original-test-case:{item['test_case_id']}#/data"
    def reference(*keys): return [pointer + '/' + k for k in keys]
    gate_review = inverse(flag(d, 'hardgate_pending'))
    def wl(kind):
        return any(kind in str(w.get('watchlist_id', '')).upper().split('-') or
                   (('blacklist' if kind == 'BL' else 'grey' if kind == 'GL' else 'united nations') in str(w.get('list_name', '')).lower()) for w in watches)
    countries = {p.get('registration_jurisdiction') for p in parties if p.get('registration_jurisdiction')}
    ubo_countries = {p.get('nationality') for p in ubos if p.get('nationality')}
    black = bool((countries | ubo_countries) & {'KP', 'IR', 'MM'}) or wl('BL')
    sanctioned = any(p.get('sanctions_flag') is True for p in ubos) or wl('UN')
    nominee = any(p.get('is_nominee') is True or p.get('is_trust') is True for p in parties)
    pep = any(p.get('pep_status') in {'Confirmed', 'PEP'} for p in ubos)
    associate = any(any(t in str(p.get('pep_status', '')).lower() for t in ('associate', 'spouse', 'partner', 'child', 'parent')) for p in ubos)
    high = ind.get('industry_tier') == 'I3'
    depth = 1 + len(d.get('party2', []))
    ownership = flag(d, 'ownership_evidence_complete')
    ubo, control = d.get('ubo_verification'), d.get('control_verification')
    regulated = flag(d, 'regulated_activity')
    licence = d.get('licence_status')
    own = flag(sf, 'payer_is_investor')
    funding = sf.get('funding_structure')
    complex_funds = funding in {'ThirdParty', 'MultipleSources', 'Intercompany', 'Loan', 'Leveraged'} or own is False
    reconciled = flag(sf, 'reconciled')
    source_complete = flag(sf, 'evidence_complete')
    anomaly = flag(sf, 'material_anomaly_unexplained')
    unusual = flag(sf, 'unusual_size_explained')
    amount, subscription = sf.get('source_amount'), sf.get('subscription_amount')
    funds_sufficient = amount >= subscription if type(amount) in (int, float) and type(subscription) in (int, float) else None
    try:
        formed = dt.date.fromisoformat(d['incorporation_date'])
        submitted = dt.date.fromisoformat(d['Application']['submission_date'])
        try: anniversary = formed.replace(year=formed.year+1)
        except ValueError: anniversary = formed.replace(year=formed.year+1, day=28)
        young = formed <= submitted < anniversary if formed <= submitted else None
    except (KeyError, ValueError): young = None
    no_history = flag(d, 'no_substantive_history_or_audited_accounts')
    missing = any(doc.get('is_mandatory') is True and doc.get('status') == 'Missing' for doc in docs)
    conflict = any(doc.get('status') == 'Conflicting' and doc.get('fact_verified') in {'shareholding_pct', 'shareholding_percentage'} for doc in docs)
    values = {
        'RF-01': black, 'RF-02': sanctioned,
        'RF-03': all_of(regulated, licence in {'Missing', 'Revoked', 'Invalid'} if licence is not None else None,
                         flag(d, 'licence_failure_officially_verified'), flag(d, 'unlicensed_onboarding_prohibited')),
        'RF-04': True if ubo == 'Failed' else False if ubo == 'Verified' else None,
        'RF-05': True if control == 'Failed' else False if control == 'Verified' else None,
        'RF-06': flag(d, 'illicit_activity_confirmed'), 'RF-07': flag(sf, 'illicit_source_confirmed'),
        'RF-08': flag(d, 'fraud_confirmed'), 'RF-09': missing, 'RF-10': conflict,
        'RF-11': True if ownership is False or ubo in {'Failed', 'Pending'} else False if ownership is True and ubo == 'Verified' else None,
        'RF-12': wl('GL'), 'RF-13': pep, 'RF-14': associate, 'RF-15': depth >= 4,
        'RF-16': nominee, 'RF-17': high,
        'RF-18': any_of(inverse(own), funding in {'Loan', 'Leveraged', 'Borrowed'} if funding is not None else None),
        'RF-19': all_of(young, no_history),
        'RF-20': any_of(flag(d, 'high_leverage'), flag(d, 'negative_cash_flow'), inverse(flag(d, 'net_assets_sufficient'))),
        'RF-21': len(countries) >= 3 or d.get('cross_entity_structure') is True,
    }
    rf = {}
    for fid, value in values.items():
        confirmed = type(value) is bool and (gate_review is True if int(fid[-2:]) <= 8 else True)
        rf[fid] = {'status': 'Hit' if value is True and confirmed else 'Clear' if value is False and confirmed else 'Pending',
                   'verified': confirmed, 'evidence_refs': reference('Application', 'party1', 'party2', 'Document', 'WatchList', 'SourceOfFunds'),
                   'source_type': 'uploaded_test_case_assertion'}
    rationale = flag(d, 'structural_explanation_adequate')
    discrepancy = flag(d, 'control_rights_discrepancy_unexplained')
    unusual_structure = all_of(depth >= 4 or nominee, inverse(rationale))
    d1 = {'legal_entity_verified': all_of(ownership, inverse(flag(d, 'fraud_confirmed'))),
          'ownership_links_verified': ownership, 'ubo_verified': ubo == 'Verified', 'control_verified': control == 'Verified',
          'complex_ownership': depth >= 4, 'nominee_or_trust': nominee,
          'cross_entity_or_border_complexity': flag(d, 'cross_entity_structure'),
          'control_rights_discrepancy': discrepancy, 'discrepancy_explained': inverse(discrepancy),
          'unusual_structure': unusual_structure, 'material_unresolved_contradiction': conflict,
          'rationale_sufficient': rationale, 'relevant_parties_verified': ownership,
          'legal_documents_sufficient': all_of(ownership, not missing), 'review_completed': ownership,
          'materiality_policy_approved': flag(d, 'ownership_materiality_policy_approved')}
    category = {'I1': 'Standard', 'I2': 'Sensitive', 'I3': 'HighRisk'}.get(ind.get('industry_tier'))
    d2 = {'activity_verified': flag(d, 'industry_data_sufficient'),
          'taxonomy_approved': flag(d, 'industry_taxonomy_approved'), 'category': category,
          'material_discrepancy': inverse(flag(d, 'industry_data_sufficient')), 'regulated': regulated,
          'licence_verified': licence == 'Valid', 'material_high_risk_activity': flag(d, 'multiple_high_risk_activities'),
          'multiple_activities': flag(d, 'multiple_high_risk_activities'),
          'high_risk_activity_verified': all_of(flag(d, 'multiple_high_risk_activities'), flag(d, 'industry_data_sufficient')),
          'materiality_policy_approved': flag(d, 'industry_materiality_policy_approved'),
          'activity_not_prohibited': inverse(flag(d, 'illicit_activity_confirmed'))}
    d3 = {'mandatory_evidence_authenticated': source_complete, 'source_event_identified': bool(sf.get('source_event')),
          'owner_identified': bool(sf.get('source_owner')), 'amount_dates_verified': flag(sf, 'amount_dates_verified'),
          'funds_sufficient': funds_sufficient, 'records_reconciled': reconciled, 'records_verified': source_complete,
          'unresolved_discrepancy': inverse(reconciled), 'own_account': own, 'complex_funding': complex_funds,
          'unusual_subscription': unusual, 'material_anomaly': anomaly, 'review_completed': source_complete,
          'anomaly_explained': inverse(anomaly), 'material_parties_verified': flag(sf, 'material_parties_verified'),
          'legal_basis_verified': flag(sf, 'legal_basis_verified'), 'flows_verified': flag(sf, 'flows_verified'),
          'arrangement_policy_permitted': flag(sf, 'third_party_policy_permitted'),
          'exceptional_event_verified': flag(sf, 'exceptional_event_verified'),
          'materiality_policy_approved': flag(sf, 'subscription_materiality_policy_approved')}
    safeguards = copy.deepcopy(d.get('SafeguardEvidence', {}))
    for sid in d.get('safeguards_completed', []):
        safeguards.setdefault(sid, {'reported_status': 'Complete', 'status': 'Pending', 'applicable': None,
            'policy_approved': None, 'evidence_refs': reference('safeguards_completed'),
            'owner': None, 'completion_evidence_refs': []})
    return {'risk_factors': rf,
            'D1': {'evidence_complete': ownership is True, 'evidence_refs': reference('ownership_evidence_complete','ubo_verification','control_verification'), 'facts': d1},
            'D2': {'evidence_complete': d.get('industry_data_sufficient') is True, 'evidence_refs': reference('Industry','industry_data_sufficient'), 'facts': d2},
            'D3': {'evidence_complete': source_complete is True, 'evidence_refs': reference('SourceOfFunds'), 'facts': d3},
            'signals': {'regulated_activity': regulated, 'third_party_or_account_mismatch': inverse(own), 'high_risk_relationship': pep},
            'safeguards': safeguards, 'policy': copy.deepcopy(d.get('PolicyEvidence', {})),
            'approvals': copy.deepcopy(d.get('ApprovalEvidence', [])),
            'reported_officer_decision': d.get('officer_decision'),
            'migration_notes': ['Evidence references identify uploaded test assertions, not independent regulatory verification.',
                                'Missing policy approvals, transaction dates, signed decisions and control completion evidence remain unknown.']}


def migrate_dataset(source):
    result = copy.deepcopy(source)
    result['schema_version'] = 2
    result['dataset_type'] = 'user_supplied_test_cases'
    result['notice'] = 'Preserves all original cases and data; only adds WorkflowEvidence. Unknown policy and authorization evidence stays Pending.'
    for item in result['test_cases']:
        if 'WorkflowEvidence' not in item['data']:
            item['data']['WorkflowEvidence'] = workflow_from_case(item)
    return result


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    source = json.loads(args.source.read_text(encoding='utf-8'))
    result = migrate_dataset(source)
    result['source_sha256'] = hashlib.sha256(args.source.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
