"""Reproducible test cases with independently specified expected outcomes.

Original records are preserved. Supplemental records and evidence are synthetic.
This builder never calls the assessment engine to obtain expected decisions.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from case_migration import migrate_dataset

ROOT = Path(__file__).resolve().parents[1]
RESULTS = {'PI-01':'REJECT','PI-02':'HOLD — Verification','PI-03':'HOLD — Information / Review',
    'PI-04':'HOLD — Assessment','PI-05':'HOLD — Safeguards','PI-06':'REJECT — Officer Decision',
    'PI-07':'HOLD — Approval','PI-08':'APPROVE — Within Appetite','PI-09':'CONDITIONAL APPROVAL',
    'PI-10':'RESTRICTED APPROVAL','PI-11':'HOLD — Policy / Input Clarification'}
POINTS = {12:3,13:5,14:4,15:4,16:3,17:4,18:4,19:3,20:4,21:3}


def signature(aid, role='Compliance Officer', decision='Approved', reviewer=None):
    return {'decision': decision, 'authorized': True, 'role': role,
            'reviewer': reviewer or 'Synthetic '+role, 'authority': 'synthetic-approved-matrix',
            'timestamp': '2026-10-13T09:00:00+08:00', 'rationale': 'Synthetic signed case review',
            'assessment_id': aid, 'policy_version': 'synthetic-policy-v3',
            'evidence_refs': ['synthetic-signature:'+aid+':'+role]}


def baseline(aid):
    return {'assessment_id': aid,
        'risk_factors': {f'RF-{i:02}': {'status':'Clear','verified':True,'evidence_refs':[f'synthetic:{aid}:RF-{i:02}']} for i in range(1,22)},
        'D1': {'evidence_complete':True,'evidence_refs':['synthetic-ownership'],'facts':{
            'legal_entity_verified':True,'ownership_links_verified':True,'ubo_verified':True,'control_verified':True,
            'intermediate_legal_entity_count':0,'complex_ownership':False,'nominee_or_trust':False,
            'cross_entity_or_border_complexity':False,'control_rights_discrepancy':False,'unusual_structure':False,
            'material_unresolved_contradiction':False}},
        'D2': {'evidence_complete':True,'evidence_refs':['synthetic-business'],'facts':{
            'activity_verified':True,'taxonomy_approved':True,'category':'Standard','regulated':False,
            'material_discrepancy':False,'material_high_risk_activity':False}},
        'D3': {'evidence_complete':True,'evidence_refs':['synthetic-funds'],'facts':{
            'mandatory_evidence_authenticated':True,'source_event_identified':True,'owner_identified':True,
            'amount_dates_verified':True,'funds_sufficient':True,'records_reconciled':True,'records_verified':True,
            'unresolved_discrepancy':False,'own_account':True,'complex_funding':False,'unusual_subscription':False,'material_anomaly':False}},
        'policy': {'version':'synthetic-policy-v3','approval_matrix_approved':True,'required_approver_roles':['Compliance Officer']},
        'approvals':[signature(aid)], 'signals':{},
        'safeguard_selection': {'reviewed':True,'evidence_refs':['synthetic-control-selection']},
        'safeguards': {f'SG-{i:02}':{'applicable':False,'policy_approved':True,'evidence_refs':[f'synthetic-policy-selection:SG-{i:02}']} for i in range(1,14)}}


def hit(e, *numbers):
    for n in numbers: e['risk_factors'][f'RF-{n:02}']['status']='Hit'


def pending(e, n): e['risk_factors'][f'RF-{n:02}']['status']='Pending'


def complex_ownership(e):
    e['D1']['facts'].update(intermediate_legal_entity_count=4,complex_ownership=True,rationale_sufficient=True)


def high_industry(e): e['D2']['facts'].update(category='HighRisk',activity_not_prohibited=True)


def control(e, sid, status='Complete'):
    record={'applicable':True,'policy_approved':True,'evidence_refs':['synthetic-control-trigger:'+sid],
            'status':status,'owner':'Synthetic '+sid+' owner','completion_verified':True,
            'verifier':'Synthetic independent verifier','verified_at':'2026-10-13T09:00:00+08:00',
            'completion_evidence_refs':['synthetic-control-completion:'+sid]}
    if sid in {'SG-08','SG-09','SG-10'}:
        record['ongoing_setup']={'cadence_approved':True,'setup_verified':True,'signed_off':True,
            'cadence':'Monthly','first_due_date':'2026-11-13','executable_plan':'Synthetic scheduled task and runbook',
            'verifier':'Synthetic schedule verifier','verified_at':'2026-10-13T09:00:00+08:00',
            'evidence_refs':['synthetic-schedule-configuration:'+sid]}
    e['safeguards'][sid]=record
    roles = ['Senior Compliance'] if sid=='SG-04' else ['Compliance Director','Risk Director'] if sid=='SG-13' else []
    for role in roles:
        if role not in e['policy']['required_approver_roles']:
            e['policy']['required_approver_roles'].append(role)
            e['approvals'].append(signature(e['assessment_id'],role))


def controls(e, ids):
    for sid in ids: control(e,sid)


def graph(depths, cycle=False):
    nodes=[{'id':'investor','entity_type':'LegalEntity'}]; edges=[]
    for branch,depth in enumerate(depths):
        previous='investor'
        for i in range(depth):
            current=f'b{branch}-legal{i}'
            nodes.append({'id':current,'entity_type':'LegalEntity'});edges.append({'entity_id':previous,'owner_id':current});previous=current
        person=f'b{branch}-person';nodes.append({'id':person,'entity_type':'NaturalPerson'});edges.append({'entity_id':previous,'owner_id':person})
    if cycle: edges.append({'entity_id':'b0-legal0','owner_id':'investor'})
    return {'investor_id':'investor','nodes':nodes,'edges':edges}


def build_dataset():
    source_path=ROOT/'tests/fixtures/original_test_cases.json'
    original=json.loads(source_path.read_text())
    data=migrate_dataset(original)
    data['schema_version']=3
    data['dataset_type']='original_cases_with_synthetic_extensions'
    data['source_sha256']=hashlib.sha256(source_path.read_bytes()).hexdigest()
    data['notice']='28 original records preserved; supplemental cases, evidence and approved policies are explicitly synthetic. Expected outcomes are specified independently of the engine.'
    originals={'case03':'PI-01','case04':'PI-01','case10':'PI-03','case11':'PI-03','case13':'PI-02',
               'case14':'PI-01','case15':'PI-01','case16':'PI-01','case17':'PI-01','case18':'PI-01',
               'case19':'PI-03','case26':'PI-02'}
    original_scores={1:0,2:3,3:0,4:0,5:5,6:4,7:0,8:3,9:4,10:None,11:None,12:10,13:0,
                     14:0,15:0,16:0,17:0,18:0,19:None,20:4,21:0,22:4,23:0,24:3,25:0,26:0,27:0,28:3}
    for item in data['test_cases']:
        item['source_type']='original_uploaded_case'
        item['expected']={'final_rule_id':originals.get(item['test_case_id'],'PI-11'),
                          'total_score':original_scores[item['case_no']], 'final_appetite':'Pending'}
    def add(name, edit=None, pi='PI-08', appetite='Within', score=0, extra=None):
        cid=f'new{len(data["test_cases"])-27:03}'
        aid='RA-SYNTHETIC-'+cid
        e=baseline(aid)
        if edit: edit(e)
        item=copy.deepcopy(original['test_cases'][0])
        item.update(test_case_id=cid,case_no=len(data['test_cases'])+1,company='SYNTHETIC '+name,
                    synthetic=True,source_type='synthetic_extension',description=name)
        item['data']['Application']['application_id']='SYNTHETIC-'+cid
        item['data']['WorkflowEvidence']=e
        # These entities are fixture context, not original customer records.
        item['data']['party1']['full_name']=item['company']
        item['data']['party1']['party_id']=cid+'-investor'
        for i,p in enumerate(item['data']['party2']):
            p['full_name']=item['company']+' fixture UBO';p['party_id']=cid+f'-party{i}'
        item['expected']={'final_rule_id':pi,'total_score':score,'final_appetite':appetite,**(extra or {})}
        data['test_cases'].append(item)
    for n in range(1,22):
        def edit(e,n=n):
            hit(e,n)
            if n==4: e['D1']['facts']['ubo_verified']=False
            if n==5: e['D1']['facts']['control_verified']=False
            if n==11: pending(e,4)
            if n in {13,14}: controls(e,['SG-02','SG-03','SG-04','SG-08','SG-09','SG-13'])
            if n==15: complex_ownership(e);controls(e,['SG-01','SG-08','SG-11'])
            if n==16:
                e['D1']['facts'].update(nominee_or_trust=True,relevant_parties_verified=True,legal_documents_sufficient=True,rationale_sufficient=True)
                controls(e,['SG-08','SG-11'])
            if n==17: high_industry(e);controls(e,['SG-01','SG-02','SG-04','SG-08','SG-10'])
            if n==18:
                e['D3']['facts'].update(complex_funding=True,own_account=False,material_parties_verified=True,legal_basis_verified=True,flows_verified=True,arrangement_policy_permitted=True)
                e['signals']['third_party_or_account_mismatch']=True;controls(e,['SG-02','SG-05','SG-08'])
            if n==21:
                e['D1']['facts'].update(cross_entity_or_border_complexity=True,materiality_policy_approved=True,rationale_sufficient=True)
                controls(e,['SG-08','SG-11'])
            if n==12: controls(e,['SG-09'])
        app='Pending' if n in {4,5} else 'Restricted' if n==17 else 'Conditional' if n in {13,14,15,16,18,21} else 'Within'
        pi='PI-01' if n<=8 else 'PI-03' if n<=11 else 'PI-10' if n==17 else 'PI-09' if app=='Conditional' else 'PI-08'
        add(f'RF-{n:02} confirmed factor',edit,pi,app,None if n in {9,10,11} else POINTS.get(n,0),{'risk_factor_hit':f'RF-{n:02}'})
    for n in range(1,12):
        def edit(e,n=n):
            if n==1: hit(e,2)
            elif n==2: pending(e,2)
            elif n==3: hit(e,9)
            elif n==4: e['D3']['evidence_complete']=False
            elif n==5: control(e,'SG-06','Incomplete')
            elif n==6: e['approvals'][0]['decision']='Rejected'
            elif n==7: e['approvals']=[]
            elif n==9: complex_ownership(e);hit(e,15);controls(e,['SG-01','SG-08','SG-11'])
            elif n==10: high_industry(e);hit(e,17);controls(e,['SG-01','SG-02','SG-04','SG-08','SG-10'])
            elif n==11: e['policy']['approval_matrix_approved']=False
        add(f'PI-{n:02} final route',edit,f'PI-{n:02}', 'Pending' if n==4 else 'Conditional' if n==9 else 'Restricted' if n==10 else 'Within',None if n==3 else 4 if n in {9,10} else 0)
    # Competing signals exercise the complete precedence chain.
    def conflict(e): hit(e,9,10,11);pending(e,2)
    add('confirmed gate overrides all holds',lambda e:(conflict(e),hit(e,1),e['policy'].update(approval_matrix_approved=False)),'PI-01',score=None)
    add('document review precedes pending HardGate',conflict,'PI-03',score=None,extra={'doc_gate':'Human Review','doc_gate_reason_ids':['RF-09','RF-10','RF-11']})
    add('pending HardGate precedes invalid policy',lambda e:(pending(e,2),e['policy'].update(approval_matrix_approved=False)),'PI-02')
    add('invalid input precedes signed rejection',lambda e:(e['policy'].update(approval_matrix_approved=False),e['approvals'][0].update(decision='Rejected')),'PI-11')
    add('rejection precedes Pending assessment',lambda e:(e['approvals'][0].update(decision='Rejected'),e['D3'].update(evidence_complete=False)),'PI-06','Pending')
    add('rejection precedes incomplete Within controls',lambda e:(e['approvals'][0].update(decision='Rejected'),control(e,'SG-06','Incomplete')),'PI-06')
    add('assessment precedes control hold',lambda e:(e['D3'].update(evidence_complete=False),control(e,'SG-06','Incomplete')),'PI-04','Pending')
    add('controls precede Pending authorization',lambda e:(e['approvals'].clear(),control(e,'SG-06','Incomplete')),'PI-05')
    add('different assessment signature',lambda e:e['approvals'][0].update(assessment_id='RA-OTHER'),'PI-11')
    add('different policy signature',lambda e:e['approvals'][0].update(policy_version='old-version'),'PI-11')
    add('mixed upstream versions',lambda e:e.update(upstream_contexts={'RiskBand':{'assessment_id':e['assessment_id'],'policy_version':'old-version'}}),'PI-11')
    add('one required rejection vetoes approval',lambda e:(e['policy']['required_approver_roles'].append('Risk Director'),e['approvals'].append(signature(e['assessment_id'],'Risk Director','Rejected'))),'PI-06')
    # Scalar boundaries and no automatic rejection from a High RF band.
    for numbers,score,band in [([],0,'Low'),([20],4,'Low'),([13],5,'Medium'),([13,20],9,'Medium'),([12,19,20],10,'High')]:
        def edit(e,numbers=numbers):
            hit(e,*numbers)
            if 13 in numbers: controls(e,['SG-02','SG-03','SG-04','SG-08','SG-09','SG-13'])
        add(f'score boundary {score}',edit,'PI-09' if 13 in numbers else 'PI-08','Conditional' if 13 in numbers else 'Within',score,{'risk_band':band})
    # Within controls, readiness, current authority, PEP floor and pending semantics.
    for n in range(1,14):
        sid=f'SG-{n:02}'
        add(sid+' Within applicable incomplete',lambda e,sid=sid:control(e,sid,'Incomplete'),'PI-05')
        add(sid+' Within configured complete',lambda e,sid=sid:control(e,sid),'PI-08')
    add('unknown safeguard selection',lambda e:(e['safeguards'].clear(),e['safeguard_selection'].update(reviewed=False)),'PI-05')
    add('ongoing control missing first due date',lambda e:(control(e,'SG-08'),e['safeguards']['SG-08']['ongoing_setup'].pop('first_due_date')),'PI-05')
    add('ongoing configured, future execution not yet due',lambda e:control(e,'SG-09'),'PI-08')
    add('unauthorized required signature',lambda e:e['approvals'][0].update(authorized=False),'PI-11')
    add('unsigned required approval',lambda e:e['approvals'][0].update(evidence_refs=[]),'PI-07')
    add('missing required second signature',lambda e:e['policy']['required_approver_roles'].append('Risk Director'),'PI-07')
    add('PEP floor cannot bypass incomplete D3',lambda e:(hit(e,13),e['D3'].update(evidence_complete=False)),'PI-04','Pending',5,{'absent_appetite_rule':'FA-06'})
    add('PEP floor retains Restricted',lambda e:(hit(e,14),high_industry(e),controls(e,['SG-01','SG-02','SG-03','SG-04','SG-08','SG-09','SG-10','SG-13'])),'PI-10','Restricted',4,{'appetite_rule':'FA-06'})
    # Four intermediate companies; endpoint count is irrelevant; max branch and cycles.
    for depths,score,app in [([0],0,'Within'),([3],0,'Within'),([4],4,'Conditional'),([1,4],4,'Conditional')]:
        def edit(e,depths=depths):
            e['ownership_structure']=graph(depths)
            e['risk_factors']['RF-15'].pop('status')
            e['risk_factors']['RF-15']['facts']={'intermediate_legal_entity_count':max(depths)}
            e['D1']['facts']['rationale_sufficient']=True
            if max(depths)>=4: controls(e,['SG-01','SG-08','SG-11'])
        add('ownership intermediates '+str(depths),edit,'PI-09' if app=='Conditional' else 'PI-08',app,score,{'intermediate_legal_entity_count':max(depths)})
    add('ownership cycle human review',lambda e:e.update(ownership_structure=graph([1],cycle=True)),'PI-03','Pending',None,{'doc_gate':'Human Review'})
    # Failed UBO is distinct from remediable pending evidence.
    add('RF11 pending UBO not final failure',lambda e:(pending(e,4),e['risk_factors']['RF-11'].update(status='Hit')),'PI-03',score=None)
    add('final Failed UBO is HardGate not RF11',lambda e:(hit(e,4),e['D1']['facts'].update(ubo_verified=False)),'PI-01','Pending')
    for item in data['test_cases']:
        item['expected']['final_result'] = RESULTS[item['expected']['final_rule_id']]
    data['original_case_count']=28
    data['synthetic_extension_count']=len(data['test_cases'])-28
    return data


if __name__=='__main__':
    output=build_dataset()
    (ROOT/'test_cases_data.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(f'Wrote {len(output["test_cases"])} cases (28 original + {output["synthetic_extension_count"]} synthetic).')
