"""Customer-only ontology/report contract. No database or verification service calls.

party1 is always a LegalEntity; party2 IDs and raw fields are retained.
A supplied Verified record is an assertion by the caller, not proof of trust.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid
import sys
from importlib.metadata import version, PackageNotFoundError
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = 'customer-report/1.0'
ROOT = Path(__file__).resolve().parent
INPUT_SCHEMA = json.loads((ROOT/'schemas/customer-assessment.schema.json').read_text())
FORMATS = FormatChecker()
VALIDATOR = Draft202012Validator(INPUT_SCHEMA, format_checker=FORMATS)


def supported_rule_pack():
    return json.loads((ROOT/'schemas/supported-rule-pack.json').read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def snapshot_material(data):
    """Approval envelopes and caller-assigned context are not self-hashed.

    Business data, evidence records, facts and policy ARE bound. Approval hashes
    are verified against this material and rule-pack hash separately.
    """
    value = copy.deepcopy(data)
    value.pop('ApprovalEvidence', None)
    if isinstance(value.get('WorkflowEvidence'), dict):
        value['WorkflowEvidence'].pop('approvals', None)
    if isinstance(value.get('CustomerAssessment'), dict):
        value['CustomerAssessment'].pop('context', None)
    return value


def snapshot_id(data):
    return digest(snapshot_material(data))


def _date(value):
    return dt.date.fromisoformat(value)


def _time(value):
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone required')
    return value


@FORMATS.checks('date-time', raises=(ValueError, TypeError, AttributeError))
def _valid_timestamp(value):
    if not isinstance(value, str):
        return True  # JSON Schema type checking owns non-string values.
    return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value)) and bool(_time(value))


@FORMATS.checks('date', raises=(ValueError, TypeError))
def _valid_date(value):
    if not isinstance(value, str):
        return True
    return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)) and bool(_date(value))


def _issue(issues, code, path, message):
    issues.append({'code': code, 'path': path, 'message': message})


def _refs(value, path='$'):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'evidence_refs' and isinstance(child, list):
                for ref in child:
                    yield path+'.'+key, ref
            else:
                yield from _refs(child, path+'.'+key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _refs(child, f'{path}[{i}]')


def _parties(case):
    data = case.source_data
    parties = []
    for role, records in [('party1', [data.get('party1', {})]), ('party2', data.get('party2', []))]:
        for raw in records:
            parties.append(dict(raw, role=role, entity_type='LegalEntity' if role == 'party1'
                                else raw.get('entity_type', 'Unknown')))
    return parties


def inspect_customer(case, evidence):
    """Validate optional new input and return report material without mutating SG."""
    data = case.source_data
    supplied = data.get('CustomerAssessment')
    parties = _parties(case)
    issues = []
    result = {'mode': 'Legacy' if 'CustomerAssessment' not in data else 'Customer', 'parties': parties,
              'issues': issues, 'snapshot_hash': snapshot_id(data),
              'customer': supplied if isinstance(supplied, dict) else {}}
    if 'CustomerAssessment' not in data:
        return result
    for error in sorted(VALIDATOR.iter_errors(supplied), key=lambda x: str(x.path)):
        _issue(issues, 'SCHEMA', '$.CustomerAssessment.'+'.'.join(map(str, error.path)), error.message)
    if issues:
        return result
    if 'WorkflowEvidence' not in data:
        _issue(issues, 'EXPLICIT_FACTS_REQUIRED', '$.WorkflowEvidence',
               'Customer mode requires explicit evidence; legacy test migration is not a production fact source')
    for role, records in [('party1', [data.get('party1', {})]), ('party2', data.get('party2', []))]:
        for record in records:
            for key in ('is_ubo', 'is_nominee', 'is_trust', 'sanctions_flag'):
                if key in record and type(record[key]) is not bool:
                    _issue(issues, 'BOOLEAN_TYPE', '$.'+role+'.'+key, 'Use JSON true/false, not strings or numbers')
    ids = [p.get('party_id') for p in parties]
    known = set(ids)
    if None in known or '' in known or len(ids) != len(known):
        _issue(issues, 'PARTY_ID', '$.parties', 'Party IDs must be nonempty and unique')
    if data.get('party1', {}).get('entity_type', 'LegalEntity') != 'LegalEntity':
        _issue(issues, 'PARTY1_TYPE', '$.party1', 'party1 must be a legal person')
    for p in parties:
        if p['entity_type'] not in {'LegalEntity', 'NaturalPerson'}:
            _issue(issues, 'PARTY_TYPE', '$.parties', f"Explicit entity_type required for {p.get('party_id')}")
    now = _time(supplied['context']['as_of'])
    records = supplied['evidence_records']
    registry = {r['evidence_id']: r for r in records}
    if len(registry) != len(records):
        _issue(issues, 'DUPLICATE_EVIDENCE', '$.evidence_records', 'Duplicate evidence ID')
    # Existing SG inputs and applicability/completion semantics are excluded here.
    referenced = {k: v for k, v in evidence.items() if k not in {'safeguards', 'safeguard_selection'}}
    for path, ref in _refs([referenced, {k:v for k,v in supplied.items() if k != 'evidence_records'}]):
        r = registry.get(ref) if isinstance(ref, str) else None
        if not r:
            _issue(issues, 'UNRESOLVED_EVIDENCE', path, f'Unregistered evidence reference: {ref}')
        elif r['verification_status'] != 'Verified':
            _issue(issues, 'UNVERIFIED_EVIDENCE', path, f'Evidence is not verified: {ref}')
        elif 'valid_until' in r and _date(r['valid_until']) < now.date():
            _issue(issues, 'EXPIRED_EVIDENCE', path, f'Evidence expired: {ref}')
        elif _time(r['verified_at']) > now:
            _issue(issues, 'FUTURE_VERIFICATION', path, f'Verification is after assessment time: {ref}')
        elif r.get('conflicts_with'):
            _issue(issues, 'EVIDENCE_CONFLICT', path, f'Unresolved evidence conflict: {ref}')
    relationships = supplied['party_relationships']
    seen, totals, children = set(), {}, {}
    for r in relationships:
        rid = r['relationship_id']
        if rid in seen:
            _issue(issues, 'DUPLICATE_RELATIONSHIP', rid, 'Duplicate relationship ID')
        seen.add(rid)
        owner, entity = r['from_party_id'], r['to_party_id']
        if owner not in known or entity not in known or owner == entity:
            _issue(issues, 'RELATIONSHIP_ENDPOINT', rid, 'Unknown or identical endpoints')
        if 'valid_from' in r and _date(r['valid_from']) > now.date():
            _issue(issues, 'RELATIONSHIP_NOT_EFFECTIVE', rid, 'Relationship is not effective as of assessment')
        if 'valid_to' in r and _date(r['valid_to']) < now.date():
            _issue(issues, 'RELATIONSHIP_EXPIRED', rid, 'Relationship expired')
        if 'valid_from' in r and 'valid_to' in r and r['valid_from'] > r['valid_to']:
            _issue(issues, 'RELATIONSHIP_DATES', rid, 'Invalid validity interval')
        if r['relationship_type'] == 'ownership':
            if next((p['entity_type'] for p in parties if p.get('party_id') == entity), None) != 'LegalEntity':
                _issue(issues, 'OWNERSHIP_TARGET', rid, 'Owned endpoint must be a legal person')
            totals[entity] = totals.get(entity, Decimal(0)) + Decimal(str(r['shareholding_percentage']))
            edge = (owner, entity)
            if edge in children:
                _issue(issues, 'DUPLICATE_OWNERSHIP', rid, 'Duplicate ownership pair')
            children[edge] = rid
    for entity, total in totals.items():
        if total > 100:
            _issue(issues, 'OWNERSHIP_TOTAL', entity, f'Ownership total exceeds 100%: {total}')
    # No incomplete graph is silently promoted into a verified ownership chain.
    graph = organization_chart(parties, relationships, data.get('party1', {}).get('party_id'))
    for message in graph['gaps']:
        _issue(issues, 'OWNERSHIP_GAP', '$.party_relationships', message)
    requirements = supplied['screening_requirements']
    keys = [(r['party_id'], r['screening_type']) for r in requirements]
    if len(keys) != len(set(keys)):
        _issue(issues, 'DUPLICATE_REQUIREMENT', '$.screening_requirements', 'Duplicate subject/type')
    for party_id in known - {r['party_id'] for r in requirements}:
        _issue(issues, 'SCREENING_SCOPE_GAP', '$.screening_requirements', f'No screening requirement for {party_id}')
    run_ids = set()
    for run in supplied['screening_runs']:
        if run['screening_id'] in run_ids:
            _issue(issues, 'DUPLICATE_SCREENING', run['screening_id'], 'Duplicate screening ID')
        run_ids.add(run['screening_id'])
        if run['party_id'] not in known:
            _issue(issues, 'SCREENING_SUBJECT', run['screening_id'], 'Unknown screening subject')
        if _time(run['attempted_at']) > now or ('screened_at' in run and _time(run['screened_at']) > now):
            _issue(issues, 'SCREENING_TIME', run['screening_id'], 'Screening is after assessment time')
        if 'valid_until' in run and _date(run['valid_until']) < now.date():
            _issue(issues, 'SCREENING_EXPIRED', run['screening_id'], 'Screening result expired')
        for candidate in run['candidates']:
            if 'reviewed_at' in candidate and _time(candidate['reviewed_at']) > now:
                _issue(issues, 'SCREENING_REVIEW_TIME', run['screening_id'], 'Candidate review is after assessment time')
    for req in requirements:
        if req['party_id'] not in known:
            _issue(issues, 'SCREENING_SUBJECT', '$.screening_requirements', 'Unknown screening subject')
    coverage = screening_coverage(supplied)
    for row in coverage['subjects']:
        if row['status'] not in {'Clear', 'ConfirmedMatch'}:
            _issue(issues, 'SCREENING_INCOMPLETE', row['party_id'], f"{row['screening_type']}: {row['status']}")
    # A screening confirmation cannot be lost in disconnected rule inputs.
    for row in coverage['subjects']:
        if row['status'] == 'ConfirmedMatch':
            run = next(r for r in supplied['screening_runs'] if r['screening_id'] == row['screening_id'])
            review = run.get('policy_review', {})
            mapped = review.get('factor_ids', [])
            valid_review = (bool(mapped) and review.get('policy_version') == evidence.get('policy', {}).get('version')
                            and _time(review['reviewed_at']) <= now)
            for fid in mapped:
                factor = evidence.get('risk_factors', {}).get(fid, {})
                valid_review = valid_review and isinstance(factor, dict) and factor.get('status') == 'Hit' and factor.get('verified') is True
            if not valid_review:
                _issue(issues, 'SCREENING_REVIEW', row['party_id'],
                       f'Confirmed {row["screening_type"]} requires a reviewed, policy-version-bound mapping to verified RF Hit records')
    record = evidence.get('risk_factors', {}).get('RF-18', {})
    rf = record.get('facts', {}) if isinstance(record, dict) else {}
    rf = rf if isinstance(rf, dict) else {}
    own = evidence.get('D3', {}).get('facts', {}).get('own_account')
    if own is True and (rf.get('third_party_funding') is True or rf.get('non_own_name_account') is True
                        or evidence.get('signals', {}).get('third_party_or_account_mismatch') is True):
        _issue(issues, 'FUNDING_CONFLICT', '$.WorkflowEvidence', 'RF/signals contradict D3 own-account conclusion')
    if own is False and rf.get('non_own_name_account') is False:
        _issue(issues, 'FUNDING_CONFLICT', '$.WorkflowEvidence', 'RF non-own-name fact contradicts D3')
    # Monetary background is retained as data, not equated to transaction reconciliation.
    for event in supplied.get('funding_background', {}).get('source_events', []):
        if event['owner_party_id'] not in known:
            _issue(issues, 'FUNDING_OWNER', event['event_id'], 'Unknown funding owner')
        if _date(event['event_date']) > now.date():
            _issue(issues, 'FUNDING_DATE', event['event_id'], 'Funding event is after assessment time')
    return result


def screening_coverage(customer):
    rows = []
    runs = customer.get('screening_runs', [])
    for req in customer.get('screening_requirements', []):
        matches = [r for r in runs if r['party_id'] == req['party_id'] and r['screening_type'] == req['screening_type']]
        if not matches:
            status, chosen = 'NotRun', None
        else:
            # Latest attempt governs coverage; a later failure is never hidden by an old success.
            chosen = max(enumerate(matches), key=lambda pair: (_time(pair[1]['attempted_at']), pair[0]))[1]
            status = chosen['execution_status']
            if status == 'Completed':
                dispositions = {x['disposition'] for x in chosen['candidates']}
                status = 'PendingReview' if 'Pending' in dispositions else 'ConfirmedMatch' if 'Confirmed' in dispositions else 'Clear'
        rows.append(dict(req, status=status, screening_id=chosen['screening_id'] if chosen else None))
    return {'status': 'NotAssessed' if not rows else 'Complete' if all(r['status'] in {'Clear','ConfirmedMatch'} for r in rows) else 'Incomplete',
            'subjects': rows}


def organization_chart(parties, relationships, root):
    """Bounded path traversal for acyclic disclosed equity; not a UBO certification."""
    owners, gaps = {}, []
    kinds = {p.get('party_id'): p.get('entity_type') for p in parties}
    for r in relationships:
        if r.get('relationship_type') == 'ownership':
            owners.setdefault(r.get('to_party_id'), []).append(r)
    interests, paths = {}, []
    stack = [(root, Decimal(1), [root])]
    count = 0
    while stack:
        node, fraction, path = stack.pop()
        count += 1
        if count > 10000:
            gaps.append('Ownership path limit exceeded; manual review required')
            break
        if kinds.get(node) == 'NaturalPerson':
            interests[node] = interests.get(node, Decimal(0)) + fraction*100
            paths.append({'party_id': node, 'path': path, 'percentage': str(fraction*100)})
            continue
        links = owners.get(node, [])
        if not links:
            gaps.append(f'Unresolved ownership branch: {node}')
        elif sum(Decimal(str(r.get('shareholding_percentage', 0))) for r in links) != 100:
            gaps.append(f'Ownership coverage is not 100%: {node}')
        for r in links:
            owner = r.get('from_party_id')
            if owner in path:
                gaps.append(f'Ownership cycle at {owner}')
                continue
            stack.append((owner, fraction*Decimal(str(r.get('shareholding_percentage', 0)))/100, path+[owner]))
    return {'nodes': parties, 'edges': relationships, 'root_party_id': root,
            'status': 'Pending' if gaps else 'DisclosedGraphComplete', 'gaps': list(dict.fromkeys(gaps)),
            'indirect_interests': [{'party_id': key, 'percentage': str(value)} for key,value in sorted(interests.items())],
            'calculation_paths': paths, 'ubo_identification': 'NotCalculated — policy thresholds and control review required'}


def approval_binding_issues(case, evidence, rule_hash):
    if not isinstance(case.source_data.get('CustomerAssessment'), dict):
        return []
    context = case.source_data['CustomerAssessment'].get('context', {})
    issues = []
    try:
        as_of = _time(context['as_of'])
    except (KeyError, ValueError, TypeError):
        return issues
    for i, approval in enumerate(evidence.get('approvals', [])):
        path = f'$.approvals[{i}]'
        try:
            if _time(approval['timestamp']) > as_of or _time(approval['valid_until']) < as_of:
                raise ValueError('Approval outside validity interval')
            if approval.get('withdrawn') is True:
                raise ValueError('Approval withdrawn')
            if approval.get('input_snapshot_hash') != snapshot_id(case.source_data):
                raise ValueError('Approval belongs to a different input snapshot')
            if approval.get('rule_pack_hash') != rule_hash:
                raise ValueError('Approval belongs to a different rule pack')
            if approval.get('organization_id') != context.get('organization_id') or approval.get('case_id') != context.get('case_id'):
                raise ValueError('Approval scope mismatch')
            if approval.get('assessment_version') != context.get('assessment_version'):
                raise ValueError('Approval assessment version mismatch')
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            _issue(issues, 'APPROVAL_BINDING', path, str(exc))
    return issues


def runtime_versions():
    values = {'python': sys.version.split()[0]}
    for package in ('openpyxl', 'jsonschema', 'langgraph'):
        try:
            values[package] = version(package)
        except PackageNotFoundError:
            values[package] = 'NotInstalled'
    return values


def build_report_payload(case, cat, evidence, result):
    inspection = evidence.get('_customer_inspection') or inspect_customer(case, evidence)
    customer = inspection['customer']
    # Invalid schemas are reported but not traversed as trusted structured data.
    if any(x['code'] == 'SCHEMA' for x in inspection['issues']):
        customer = {}
    context = customer.get('context', {})
    sid = inspection['snapshot_hash']
    run_id = str(uuid.uuid4())
    scope = {'organization_id': context.get('organization_id'), 'case_id': context.get('case_id', case.application.application_id),
             'assessment_id': result['assessment_id'], 'assessment_version': context.get('assessment_version'),
             'customer_party_id': case.party1.party_id, 'application_id': case.application.application_id,
             'as_of': context.get('as_of', result['assessment_date']), 'purpose': 'CustomerAML'}
    checks = []
    for row in result['risk_assessment']['rule_assessments']:
        rid = row['rule_id'];rule = cat.rf[rid]
        checks.append({'evaluation_id': rid, 'stage': 'RiskFactors', 'rule_id': rid,
                       'status': row['status'], 'description': rule.description, 'policy_condition': rule.condition,
                       'evidence_refs': row['evidence_refs'],
                       'facts': (evidence.get('risk_factors', {}).get(rid, {}).get('facts')
                                 if isinstance(evidence.get('risk_factors', {}).get(rid, {}), dict) else None),
                       'points_applied': next((h['points_applied'] for h in result['risk_assessment']['hits'] if h['factor_id']==rid),0)})
    for prefix, dim in result['dimensions'].items():
        checks.append({'evaluation_id': prefix, 'stage': 'Appetite', 'rule_ids': dim['rule_ids'],
                       'status': 'Pending' if dim['level']=='Pending' else 'Assessed',
                       'level': None if dim['level']=='Pending' else dim['level'],
                       'facts': evidence.get(prefix, {}).get('facts', {}), 'evidence_refs': dim['evidence_refs']})
    checks.append({'evaluation_id': 'FA', 'stage': 'Appetite', 'rule_ids': result['appetite_rule_ids'],
                   'status': 'Pending' if result['final_appetite']=='Pending' else 'Assessed',
                   'level': None if result['final_appetite']=='Pending' else result['final_appetite'],
                   'evidence_refs': list(dict.fromkeys(ref for d in result['dimensions'].values() for ref in d['evidence_refs']))})
    checks.append({'evaluation_id':'PI','stage':'PolicyIntegration', **copy.deepcopy(result['final_decision'])})
    outstanding = []
    def todo(code, reason, target=None):
        outstanding.append({'item_id': f'ITEM-{len(outstanding)+1:04}', 'type':code, 'reason':reason,
                            'related_id':target, 'status':'Open', 'owner':None, 'due_at':None})
    for issue in inspection['issues']:
        todo(issue['code'], issue['message'], issue['path'])
    for rid in result['risk_assessment']['pending_rule_ids']:
        todo('RiskEvidence', 'Complete verified risk-factor assessment', rid)
    for prefix, dim in result['dimensions'].items():
        if dim['level']=='Pending': todo('DimensionEvidence','Complete customer dimension evidence under applicable policy',prefix)
    if inspection['mode']=='Legacy':
        todo('LegacyInput','Provide CustomerAssessment context, registered evidence and screening records')
    for message in result['final_decision']['input_errors']:
        if message not in {x['message'] for x in inspection['issues']}:
            todo('DecisionInput', message)
    if not customer.get('company_profile'):
        todo('CompanyProfile','Provide customer registration/business profile with evidence')
    if result['final_decision']['authorization_status']=='Pending':
        todo('Approval','Obtain existing-policy required approvals')
    # Safeguards output is copied exactly, with no changes to candidate/applicability logic.
    for r in result['safeguards']['selected']:
        if r['status']!='Complete': todo('Safeguard', 'Resolve existing safeguard requirement', r['safeguard_id'])
    for rid in result['safeguards']['unresolved_applicability']:
        todo('Safeguard', 'Resolve existing safeguard applicability', rid)
    chart = organization_chart(inspection['parties'], customer.get('party_relationships', []), case.party1.party_id)
    ready = inspection['mode']=='Customer' and not outstanding and not result['final_decision']['input_errors']
    return {'schema_version': SCHEMA_VERSION, 'scope':scope,
            'run_manifest': {'run_id':run_id,'input_snapshot_id':sid,'input_snapshot_hash':sid,
                             'rule_pack_hash':digest(cat.versions),'workbook_sha256':copy.deepcopy(cat.versions),
                             'engine_version':'customer-contract-1','runtime_versions':runtime_versions(),
                             'schema_sha256':{name:hashlib.sha256((ROOT/'schemas'/name).read_bytes()).hexdigest()
                                              for name in ('customer-assessment.schema.json','report-payload.schema.json','supported-rule-pack.json')},
                             'engine_sha256':{
                                 name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                 for name in ('policy_engine.py','customer_ontology.py','ownership.py','kyc_poc.py','workflow_graph.py')},
                             'evaluated_at':result['audit']['evaluated_at'],
                             'supersedes_snapshot_id':context.get('supersedes_snapshot_id')},
            'input_snapshot':copy.deepcopy(case.source_data),
            'customer_summary':{'party1':copy.deepcopy(case.source_data.get('party1', {})),
                                'party2':copy.deepcopy(case.source_data.get('party2', [])),
                                'party1_entity_type':'LegalEntity', 'industry':copy.deepcopy(case.source_data.get('Industry', {})),
                                'company_profile':copy.deepcopy(customer.get('company_profile', {}))},
            'organization_chart':chart, 'screening':dict(screening_coverage(customer), runs=copy.deepcopy(customer.get('screening_runs',[]))),
            'documents':copy.deepcopy(case.source_data.get('Document', [])),
            'evidence_records':copy.deepcopy(customer.get('evidence_records', [])),
            'funding_background':copy.deepcopy(customer.get('funding_background', {})),
            'funding_applicability':{'customer_background':'InScope','product_suitability':'OutOfScope','subscription_settlement':'OutOfScope',
                                     'd3_policy_note':'Existing D3/FA rules retained; missing customer-basis policy facts remain Pending, not auto-passed'},
            'rule_evaluations':checks, 'safeguards':copy.deepcopy(result['safeguards']),
            'approvals':copy.deepcopy(evidence.get('approvals', [])),
            'customer_decision':dict(copy.deepcopy(result['final_decision']), scope='CustomerAMLOnly'),
            'outstanding_items':outstanding,
            'claims':[{'claim_id':'CLAIM-'+r['evaluation_id'],'kind':'RuleConclusion',
                       'rule_evaluation_id':r['evaluation_id'],'evidence_refs':r.get('evidence_refs',[]),
                       'status':r.get('status', 'Assessed'),'statement':r.get('description') or r.get('result') or f"{r['evaluation_id']}: {r.get('level') or r.get('status')}"} for r in checks],
            'quality':{'mode':inspection['mode'],'status':'ReadyForReview' if ready else 'NeedsReview',
                       'issues':copy.deepcopy(inspection['issues']), 'verification_trust':'CallerSuppliedAssertions — trusted service integration required',
                       'audience':'ComplianceInternal','external_permissions_enforced':False}}
