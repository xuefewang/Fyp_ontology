import copy
import json
import sqlite3
import unittest
from report_contract.export import CONTRACT,build_projection,save_sql,reconcile_funding,read_sql
from kyc_poc import DEFAULT_JSON,DEFAULT_RULES,case_from_json
from policy_engine import assess_workflow,load_catalogue

class ReportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.item=json.loads(DEFAULT_JSON.read_text())['test_cases'][0]
        cls.cat=load_catalogue(DEFAULT_RULES)

    def projection(self,item=None):
        item=copy.deepcopy(item or self.item)
        return build_projection(item,assess_workflow(case_from_json(item),self.cat))

    def test_all_second_sheet_rows_mapped_exactly_once(self):
        rows=CONTRACT['requirements']
        self.assertEqual(len(rows),289)
        self.assertEqual(len({r['requirement_id'] for r in rows}),289)
        self.assertEqual(sum(r['owner']=='OutOfScope' for r in rows),65)
        self.assertTrue(all(r['canonical_path'] for r in rows))
        self.assertEqual(CONTRACT['worksheet_index'],2)

    def test_inputs_unchanged_and_engine_output_exactly_preserved(self):
        item=copy.deepcopy(self.item);original=copy.deepcopy(item)
        result=assess_workflow(case_from_json(item),self.cat);before=copy.deepcopy(result)
        p=build_projection(item,result)
        self.assertEqual(item,original);self.assertEqual(result,before)
        self.assertEqual(p['canonical']['engine'],result)
        self.assertEqual(p['canonical']['engine']['safeguards'],result['safeguards'])

    def test_no_duplicate_application_customer_identity(self):
        p=self.projection();d=p['canonical']['data']
        self.assertEqual(p['case_id'],d['Application']['application_id'])
        fields={f['requirement_id']:f for f in p['fields']}
        self.assertEqual(fields['1.1.3']['canonical_path'],'/data/party1/party_id')
        self.assertNotEqual(fields['1.1.3']['value'],d['Application']['application_id'])
        self.assertNotIn('customer_id',p['canonical']['data'])

    def test_sql_save_read_idempotency_and_reject_changed_run(self):
        p=self.projection()
        with sqlite3.connect(':memory:') as db:
            save_sql(db,p);save_sql(db,p)
            self.assertEqual(db.execute('SELECT count(*) FROM ontology_run').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM report_field').fetchone()[0],289)
            saved=json.loads(db.execute('SELECT canonical_json FROM ontology_run WHERE run_id=?',(p['run_id'],)).fetchone()[0])
            self.assertEqual(saved,p['canonical'])
            changed=copy.deepcopy(p);changed['fields'][0]['value']='tampered'
            with self.assertRaises(ValueError):save_sql(db,changed)
            changed=copy.deepcopy(p);changed['canonical']['derived']['case_id']='tampered'
            with self.assertRaises(ValueError):save_sql(db,changed)

    def test_unique_run_for_each_export(self):
        a,b=self.projection(),self.projection()
        self.assertNotEqual(a['run_id'],b['run_id'])
        self.assertEqual(a['canonical']['derived']['snapshot_binding']['input_hash'],b['canonical']['derived']['snapshot_binding']['input_hash'])

    def test_missing_inputs_are_explicit_not_fabricated(self):
        p=self.projection();fields={f['requirement_id']:f for f in p['fields']}
        self.assertEqual(fields['1.2.3']['status'],'Missing')
        self.assertIsNone(fields['1.2.3']['value'])
        self.assertEqual(fields['6.5.1']['status'],'OutOfPrototypeScope')
        self.assertEqual(fields['6.4.2']['status'],'ExternalPending')
        self.assertFalse(p['summary']['ready_for_report'])

    def test_new_canonical_input_is_preserved_and_exported(self):
        item=copy.deepcopy(self.item)
        item['data']['ReportInput']={'company':{'registration':{'number':'DEMO-001','registry':'Synthetic'}}}
        p=self.projection(item);field=next(f for f in p['fields'] if f['requirement_id']=='1.2.3')
        self.assertEqual(field['status'],'Provided');self.assertEqual(field['value']['number'],'DEMO-001')

    def test_single_currency_decimal_reconciliation(self):
        d={'SourceOfFunds':{'net_available_amount':'0.30','subscription_amount':'0.20','currency':'HKD'}}
        r=reconcile_funding(d);self.assertEqual(r['difference'],'0.10');self.assertTrue(r['funds_sufficient'])
        d['SourceOfFunds']['subscription_amount']='1.00'
        self.assertFalse(reconcile_funding(d)['funds_sufficient'])

    def test_gross_fx_and_third_party_cannot_be_auto_passed(self):
        for sf,expected in [({'source_amount':100,'subscription_amount':50,'currency':'HKD'},'Missing'),
                            ({'net_available_amount':100,'subscription_amount':50,'currency':'HKD','subscription_currency':'USD'},'OutOfPrototypeScope'),
                            ({'net_available_amount':100,'subscription_amount':50,'currency':'HKD','payer_is_investor':False},'OutOfPrototypeScope'),
                            ({'net_available_amount':'NaN','subscription_amount':50,'currency':'HKD'},'Missing')]:
            self.assertEqual(reconcile_funding({'SourceOfFunds':sf})['status'],expected)

    def test_screening_is_not_inferred_from_watchlist_flag(self):
        p=self.projection()
        self.assertIsNone(p['canonical']['derived']['screening_gaps'])
        self.assertTrue(any(x['type']=='Screening' for x in p['canonical']['derived']['outstanding_items']))
        item=copy.deepcopy(self.item);pid=item['data']['party1']['party_id']
        item['data']['ReportInput']={'screening':{'requirements':[{'party_id':pid,'screening_type':'Sanctions'}],
                                                'execution':[{'party_id':pid,'screening_type':'Sanctions','status':'Failed'}]}}
        p=self.projection(item)
        self.assertTrue(p['canonical']['derived']['screening_gaps'])

    def test_missing_rows_remain_in_summary_and_sql(self):
        p=self.projection()
        with sqlite3.connect(':memory:') as db:
            save_sql(db,p)
            statuses=dict(db.execute('SELECT status,count(*) FROM report_field WHERE run_id=? GROUP BY status',(p['run_id'],)).fetchall())
            self.assertEqual(statuses,p['summary']['statuses'])
            self.assertGreater(statuses['Missing'],0)

    def test_projection_supports_all_174_existing_cases(self):
        items=json.loads(DEFAULT_JSON.read_text())['test_cases']
        for item in items:
            with self.subTest(case=item['test_case_id']):
                result=assess_workflow(case_from_json(item),self.cat)
                projection=build_projection(item,result)
                self.assertEqual(len(projection['fields']),289)
                self.assertEqual(projection['canonical']['engine']['final_decision']['rule_id'],item['expected']['final_rule_id'])

    def test_projection_is_independent_from_original_rule_result(self):
        item=copy.deepcopy(self.item)
        result=assess_workflow(case_from_json(item),self.cat)
        before=copy.deepcopy(result)
        projection=build_projection(item,result)
        projection['canonical']['derived']['included_material']['workflow_evidence']['new_field']='changed'
        self.assertEqual(result,before)

    def test_sql_reader_returns_exact_persisted_run(self):
        projection=self.projection()
        with sqlite3.connect(':memory:') as db:
            save_sql(db,projection)
            self.assertEqual(read_sql(db,projection['run_id']),projection)
            with self.assertRaises(KeyError):read_sql(db,'missing-run')

    def test_sql_rejects_incomplete_or_wrong_contract_fields(self):
        with sqlite3.connect(':memory:') as db:
            projection=self.projection();projection['fields'].pop()
            with self.assertRaises(ValueError):save_sql(db,projection)
            projection=self.projection();projection['fields'][0]['canonical_path']='/wrong'
            with self.assertRaises(ValueError):save_sql(db,projection)
