"""Prototype report projection and SQL persistence; existing AML rules are unchanged."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((Path(__file__).parent/'requirements.json').read_text(encoding='utf-8'))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def pointer(value, path):
    for part in path.strip('/').split('/'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return copy.deepcopy(value)


def reconcile_funding(data):
    sf = data.get('SourceOfFunds', {})
    if not isinstance(sf, dict):
        raise ValueError('SourceOfFunds must be an object')
    currency = sf.get('currency')
    target_currency = sf.get('subscription_currency', currency)
    if sf.get('funding_structure') in {'ThirdParty','MultipleSources','Intercompany','Loan','Leveraged','Borrowed'} or sf.get('payer_is_investor') is False:
        return {'status':'OutOfPrototypeScope','reason':'Third-party or complex funding requires manual review'}
    if not currency or not target_currency:
        return {'status':'Missing','reason':'Explicit source and target currency required'}
    if currency != target_currency:
        return {'status':'OutOfPrototypeScope','reason':'Cross-currency reconciliation is excluded; no FX inferred'}
    try:
        # Net availability must be explicit; gross source_amount is not assumed to be net.
        net, allocated = (Decimal(str(sf[k])) for k in ('net_available_amount','subscription_amount'))
        if not net.is_finite() or not allocated.is_finite() or net < 0 or allocated < 0:
            raise ValueError('Amounts must be finite and nonnegative')
    except (KeyError, ValueError, InvalidOperation):
        return {'status':'Missing','reason':'Explicit valid net_available_amount and subscription_amount required'}
    return {'status':'Calculated','currency':currency,'net_available_amount':str(net),'subscription_amount':str(allocated),
            'difference':str(net-allocated),'funds_sufficient':net>=allocated,
            'formula':'net_available_amount - subscription_amount',
            'evidence_refs':sf.get('evidence_refs',[]),'verification':'CalculationOnly — input authenticity is not certified'}


def build_projection(item, result):
    data=copy.deepcopy(item['data']);extra=data.get('ReportInput',{})
    if not isinstance(extra,dict):raise ValueError('ReportInput must be an object')
    for key,value in extra.items():
        if not isinstance(value,dict):raise ValueError(f'ReportInput.{key} must be an object')
    wf=copy.deepcopy(result['audit']['input_evidence']);app=data['Application'];now=result['audit']['evaluated_at']
    case_id=extra.get('context',{}).get('case_id') or app['application_id']
    parties=[dict(data['party1'],role='party1',entity_type='LegalEntity')]+[dict(p,role='party2') for p in data.get('party2',[])]
    ubos=[p for p in parties if p.get('is_ubo') is True]
    sg=result['safeguards']
    explanations=[]
    hit_map={h['factor_id']:h for h in result['risk_assessment']['hits']}
    for record in result['risk_assessment']['rule_assessments']:
        rid=record['rule_id']
        facts=wf.get('risk_factors',{}).get(rid,{})
        explanations.append({'rule_id':rid,'origin':'DeterministicRule','status':record['status'],
                             'facts':facts.get('facts') if isinstance(facts,dict) else None,
                             'evidence_refs':record['evidence_refs'],'points_applied':hit_map.get(rid,{}).get('points_applied',0),
                             'gap': 'Missing or unverified predicate facts; not assumed clear' if record['status']=='Pending' else None})
    for prefix,dim in result['dimensions'].items():
        explanations.append({'rule_ids':dim['rule_ids'],'dimension':prefix,'origin':'DeterministicRule',
                             'level':dim['level'],'facts':wf.get(prefix,{}).get('facts'), 'evidence_refs':dim['evidence_refs']})
    explanations.append({'rule_ids':result['appetite_rule_ids'],'origin':'DeterministicRule','final_appetite':result['final_appetite']})
    explanations.append(dict(result['final_decision'],origin='DeterministicRule'))
    missing=[]
    def gap(kind,reason,reference=None):missing.append({'type':kind,'reason':reason,'related_id':reference,'owner':None,'due_at':None})
    for rid in result['risk_assessment']['pending_rule_ids']:gap('RiskEvidence','Risk factor unresolved',rid)
    for key,dim in result['dimensions'].items():
        if dim['level']=='Pending':gap('DimensionEvidence','Dimension assessment pending',key)
    for message in result['final_decision']['input_errors']:gap('Input',message)
    if result['final_decision']['authorization_status']=='Pending':gap('Approval','Original-policy approval pending')
    for row in sg['selected']:
        if row['status']!='Complete':gap('Safeguard','Original safeguard incomplete',row['safeguard_id'])
    for rid in sg['unresolved_applicability']:gap('Safeguard','Original applicability unresolved',rid)
    screening=extra.get('screening',{});requirements=screening.get('requirements');runs=screening.get('execution')
    screening_gaps=None
    if requirements is not None:
        if not isinstance(requirements,list) or not all(isinstance(x,dict) for x in requirements):raise ValueError('screening.requirements must contain objects')
        if runs is not None and (not isinstance(runs,list) or not all(isinstance(x,dict) for x in runs)):raise ValueError('screening.execution must contain objects')
        screening_gaps=[]
        for req in requirements:
            matches=[r for r in (runs or []) if r.get('party_id')==req.get('party_id') and r.get('screening_type')==req.get('screening_type')]
            # Input order is not a trusted version order: explicit latest status must be supplied.
            if len(matches)!=1 or matches[0].get('status')!='Completed' or matches[0].get('unresolved_candidates'):
                screening_gaps.append(dict(req,reason='Missing, failed, ambiguous or unresolved screening execution'))
        if not requirements:
            screening_gaps=[{'reason':'Screening scope not established'}]
        for row in screening_gaps:gap('Screening',row['reason'],row.get('party_id'))
    else:gap('Screening','No imported screening scope/results; service not connected')
    graph=wf.get('ownership_structure',{})
    edges=extra.get('ownership',{}).get('edges')
    if edges is None and graph.get('edges'):
        edges=graph['edges'] # Keep entity_id/owner_id direction; do not invent equity percentages.
    funds=reconcile_funding(data)
    if funds['status'] in {'Missing','OutOfPrototypeScope'}:gap('Funding',funds['reason'])
    if funds.get('funds_sufficient') is False:gap('Funding','Net available amount is insufficient')
    if any(p.get('is_nominee') is True or p.get('is_trust') is True for p in parties):gap('OutOfPrototypeScope','Nominee/trust structure requires manual handling')
    ubo_state={'identified_party_ids':[p.get('party_id') for p in ubos],
               'verification_status':data.get('ubo_verification'),
               'control_status':data.get('control_verification'),
               'source':'Supplied input; not inferred from layer count'}
    derived={'case_id':case_id,'ownership_edges':edges,
             'net_funding_amounts':{'received_amount':data.get('SourceOfFunds',{}).get('received_amount'),
                                   'net_available_amount':data.get('SourceOfFunds',{}).get('net_available_amount'),
                                   'currency':data.get('SourceOfFunds',{}).get('currency')},'parties':parties,'ubos':ubos,
             'organization_chart':{'nodes':graph.get('nodes') or parties,'edges':edges,'unknown_relations':edges is None,
                                   'ownership_analysis':result['ownership_analysis'],'direction_note':'Original graph edges point entity_id → owner_id; ReportInput edges must state their direction'},
             'ubo_verification':ubo_state,'pep_parties':[p for p in parties if p.get('pep_status') not in (None,'None','')],
             'jurisdictions':{'party1_registration':data['party1'].get('registration_jurisdiction'),
                              'party2':[{'party_id':p.get('party_id'),'nationality':p.get('nationality'),'registration_jurisdiction':p.get('registration_jurisdiction')} for p in data.get('party2',[])]},
             'policy_pack':{'version':result['audit']['policy_version'],'workbook_sha256':result['audit']['workbook_sha256'],
                            'ontology_code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('policy_engine.py','ownership.py','kyc_poc.py')},
                            'output_contract_version':CONTRACT['contract_version'],
                            'bands':{'Low':'0–4','Medium':'5–9','High':'10+'},'provenance':'Example workbook policy; institutional approval not certified'},
             'document_applicability':[{'document_id':d['document_id'],'is_mandatory':d.get('is_mandatory')} for d in data.get('Document',[])],
             'document_verification':[{'document_id':d['document_id'],'status':d.get('status'),'fact_verified':d.get('fact_verified'),
                                       'note':'fact_verified identifies the fact; it is not an independent verification result'} for d in data.get('Document',[])],
             'screening_gaps':screening_gaps,'funding_reconciliation':funds,
             'funding_amount_currency':{'source_amount':data.get('SourceOfFunds',{}).get('source_amount'),
                                       'subscription_amount':data.get('SourceOfFunds',{}).get('subscription_amount'),
                                       'currency':data.get('SourceOfFunds',{}).get('currency')},
             'score_calculation':{'total_score':result['risk_assessment']['total_score'],
                                  'confirmed_score_subtotal':result['risk_assessment']['confirmed_score_subtotal'],
                                  'terms':result['risk_assessment']['hits'],'scoring_status':result['risk_assessment']['scoring_status']},
             'appetite_status':'Pending' if result['final_appetite']=='Pending' else 'Assessed',
             'edd_sow':{'edd_required':result['edd_required'],'edd_status':result['edd_status'],'sow':extra.get('sow')},
             'rule_explanations':explanations,'outstanding_items':missing,
             'content_origin':'DeterministicRule / ImportedInput; no LLM executed by ontology exporter',
             'release_disclaimer':'Prototype only — no account, fund acceptance, product or settlement permission granted',
             'included_material':{'documents':data.get('Document',[]),'workflow_evidence':wf},
             'run':{'run_id':str(uuid.uuid4()),'evaluated_at':now},
             'snapshot_binding':{'input_hash':sha(data),'engine_result_hash':sha(result),'contract_hash':sha(CONTRACT)},
             'reproducibility':'Original input and rule result saved; a rerun is a separate run, not a historical overwrite'}
    canonical={'data':data,'engine':copy.deepcopy(result),'derived':derived}
    fields=[]
    for requirement in CONTRACT['requirements']:
        value=pointer(canonical,requirement['canonical_path'])
        if isinstance(value,dict) and value and all(v is None for v in value.values()):value=None
        if isinstance(value,str) and not value.strip():value=None
        if requirement['owner']=='OutOfScope':status='OutOfPrototypeScope';value=None
        elif requirement['owner']=='External':status='ExternalProvided' if value is not None else 'ExternalPending'
        else:status='Provided' if value is not None else 'Missing'
        if status=='Missing':gap('ReportField','Required ontology output has no supplied source',requirement['requirement_id'])
        fields.append({'requirement_id':requirement['requirement_id'],'canonical_path':requirement['canonical_path'],
                       'owner':requirement['owner'],'status':status,'value':value})
    # Shared objects are copied after assembly so missing-field tasks are visible consistently.
    for field in fields:
        if field['canonical_path']=='/derived/outstanding_items':field['value']=copy.deepcopy(missing)
    return {'contract_version':CONTRACT['contract_version'],'run_id':derived['run']['run_id'],'case_id':case_id,
            'assessment_id':result['assessment_id'],'canonical':canonical,'fields':fields,
            'summary':{'total_requirements':len(fields),'statuses':dict(Counter(f['status'] for f in fields)),
                       'ontology_field_statuses':dict(Counter(f['status'] for f in fields if f['owner'] in ('Ontology','Shared'))),
                       'ready_for_report':not missing,'note':'Provided means present, not verified; missing facts are never fabricated'}}


DDL='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS report_requirement (
 contract_hash TEXT NOT NULL, requirement_id TEXT NOT NULL, definition_json TEXT NOT NULL,
 PRIMARY KEY(contract_hash,requirement_id));
CREATE TABLE IF NOT EXISTS ontology_run (
 run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
 contract_hash TEXT NOT NULL, canonical_json TEXT NOT NULL, summary_json TEXT NOT NULL,
 UNIQUE(run_id,contract_hash));
CREATE TABLE IF NOT EXISTS report_field (
 run_id TEXT NOT NULL, contract_hash TEXT NOT NULL, requirement_id TEXT NOT NULL,
 canonical_path TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL, value_json TEXT NOT NULL,
 PRIMARY KEY(run_id,requirement_id),
 FOREIGN KEY(run_id,contract_hash) REFERENCES ontology_run(run_id,contract_hash),
 FOREIGN KEY(contract_hash,requirement_id) REFERENCES report_requirement(contract_hash,requirement_id));
'''


def save_sql(connection, projection):
    expected={r['requirement_id']:r for r in CONTRACT['requirements']}
    supplied=projection['fields']
    if len(supplied)!=len(expected) or {f['requirement_id'] for f in supplied}!=set(expected):
        raise ValueError('A SQL run must include every requirement exactly once')
    for field in supplied:
        definition=expected[field['requirement_id']]
        if field['canonical_path']!=definition['canonical_path'] or field['owner']!=definition['owner']:
            raise ValueError('Field mapping differs from the contract')
    if projection['run_id']!=projection['canonical']['derived']['run']['run_id']:
        raise ValueError('Run identity mismatch')
    # Serialize before any writes, so invalid JSON cannot leave a partial run.
    encoded(projection)
    connection.executescript(DDL)
    contract_hash=sha(CONTRACT);run_id=projection['run_id']
    with connection:
        previous=connection.execute('SELECT canonical_json,summary_json FROM ontology_run WHERE run_id=?',(run_id,)).fetchone()
        if previous:
            if previous!=(encoded(projection['canonical']),encoded(projection['summary'])):
                raise ValueError('Same run_id with different content')
            saved=connection.execute('SELECT requirement_id,canonical_path,owner,status,value_json FROM report_field WHERE run_id=? ORDER BY requirement_id',(run_id,)).fetchall()
            wanted=sorted((f['requirement_id'],f['canonical_path'],f['owner'],f['status'],encoded(f['value'])) for f in projection['fields'])
            if saved!=wanted:raise ValueError('Same run_id with different fields')
            return
        for r in CONTRACT['requirements']:
            connection.execute('INSERT OR IGNORE INTO report_requirement VALUES(?,?,?)',(contract_hash,r['requirement_id'],encoded(r)))
        connection.execute('INSERT INTO ontology_run VALUES(?,?,?,?,?,?)',(run_id,projection['case_id'],projection['assessment_id'],contract_hash,encoded(projection['canonical']),encoded(projection['summary'])))
        for f in projection['fields']:
            connection.execute('INSERT INTO report_field VALUES(?,?,?,?,?,?,?)',(run_id,contract_hash,f['requirement_id'],f['canonical_path'],f['owner'],f['status'],encoded(f['value'])))


def read_sql(connection, run_id):
    """Read one complete persisted run. Caller/API owns access authorization."""
    row=connection.execute(
        'SELECT case_id,assessment_id,contract_hash,canonical_json,summary_json FROM ontology_run WHERE run_id=?',
        (run_id,)).fetchone()
    if row is None:
        raise KeyError(f'Unknown run_id: {run_id}')
    case_id,assessment_id,contract_hash,canonical_json,summary_json=row
    definitions=connection.execute(
        'SELECT definition_json FROM report_requirement WHERE contract_hash=?',(contract_hash,)).fetchall()
    fields=connection.execute(
        'SELECT requirement_id,canonical_path,owner,status,value_json FROM report_field WHERE run_id=?',
        (run_id,)).fetchall()
    if len(fields)!=len(definitions):
        raise ValueError('Incomplete SQL run; do not construct a partial report')
    field_rows=[{'requirement_id':r[0],'canonical_path':r[1],'owner':r[2],'status':r[3],'value':json.loads(r[4])} for r in fields]
    field_rows.sort(key=lambda f:tuple(int(part) for part in f['requirement_id'].split('.')))
    canonical=json.loads(canonical_json)
    return {'contract_version':canonical['derived']['policy_pack']['output_contract_version'],
            'run_id':run_id,'case_id':case_id,'assessment_id':assessment_id,
            'canonical':canonical,'summary':json.loads(summary_json),'fields':field_rows}


def main():
    from kyc_poc import case_from_json, DEFAULT_JSON, DEFAULT_RULES
    from policy_engine import assess_workflow,load_catalogue
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',type=Path,default=DEFAULT_JSON)
    parser.add_argument('--case',required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    items=json.loads(args.json.read_text(encoding='utf-8'))['test_cases']
    chosen=[x for x in items if x['test_case_id']==args.case]
    if len(chosen)!=1:parser.error('Select exactly one test_case_id')
    item=chosen[0];result=assess_workflow(case_from_json(item),load_catalogue(DEFAULT_RULES))
    projection=build_projection(item,result)
    args.db.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(args.db) as conn:save_sql(conn,projection)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(projection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(encoded({'run_id':projection['run_id'],'sql_database':str(args.db),**projection['summary']}))


if __name__=='__main__':main()
