"""Database partner boundary. Pure exports; no credentials, connections or SQL execution."""
from __future__ import annotations

import copy
from typing import Protocol


class AssessmentRepository(Protocol):
    def save_bundle(self, bundle: dict) -> None:
        """Atomically append by run_id; identical retry is idempotent, changed retry fails."""
        ...

    def get_bundle(self, organization_id: str, case_id: str,
                   assessment_version: int, run_id: str) -> dict:
        """Retrieve an exact immutable run after authorization; never mix latest rows."""
        ...


def export_rows(result):
    """JSON-serializable row sets for SQL; every child is qualified by run_id."""
    payload = result['report_payload']
    scope = payload['scope']
    run_id = payload['run_manifest']['run_id']
    keys = dict(organization_id=scope['organization_id'], case_id=scope['case_id'],
                assessment_version=scope['assessment_version'], run_id=run_id)
    def rows(items):
        return [dict(keys, row_index=i, data=copy.deepcopy(item)) for i,item in enumerate(items)]
    return {'assessment_run': [dict(keys, schema_version=payload['schema_version'],
                                    input_snapshot_hash=payload['run_manifest']['input_snapshot_hash'],
                                    quality_status=payload['quality']['status'], data=copy.deepcopy(payload))],
            'party_snapshot': rows(payload['organization_chart']['nodes']),
            'party_relationship': rows(payload['organization_chart']['edges']),
            'screening_requirement': rows(payload['screening']['subjects']),
            'screening_run': rows(payload['screening']['runs']),
            'evidence_record': rows(payload['evidence_records']),
            'rule_evaluation': rows(payload['rule_evaluations']),
            'approval': rows(payload['approvals']),
            'outstanding_item': rows(payload['outstanding_items']),
            'report_claim': rows(payload['claims'])}


def export_graph(result):
    """Versioned graph projection. Raw party1/party2 roles are kept on each node."""
    p = result['report_payload']
    return {'scope':copy.deepcopy(p['scope']), 'run_id':p['run_manifest']['run_id'],
            'nodes':copy.deepcopy(p['organization_chart']['nodes']),
            'edges':copy.deepcopy(p['organization_chart']['edges'])}
