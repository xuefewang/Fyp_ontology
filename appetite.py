"""LangGraph appetite subgraph using the existing evidence-first policy engine."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from kyc_poc import DEFAULT_JSON, DEFAULT_RULES, case_from_json
from policy_engine import (
    evaluate_dimensions, evaluate_rf, load_catalogue, prepare_workflow_evidence,
)


class AppetiteInput(TypedDict):
    case: dict[str, Any]


class AppetiteOutput(TypedDict):
    assessment_id: str
    ownership_analysis: dict[str, Any] | None
    risk_assessment: dict[str, Any]
    dimensions: dict[str, Any]
    final_appetite: str
    appetite_rule_ids: list[str]
    next_step: str
    audit: dict[str, Any]


class AppetiteState(AppetiteInput, AppetiteOutput, total=False):
    evidence: dict[str, Any]


def build_appetite_graph(rules_dir: Path = DEFAULT_RULES, *, checkpointer=None):
    """Compile a reusable subgraph. Input is one existing test-case shaped object.

    next_step is a handoff hint, never an onboarding acceptance decision.
    Pass the full case on every invocation, including after evidence remediation.
    """
    catalogue = load_catalogue(Path(rules_dir))

    def prepare(state):
        case = case_from_json(state['case'])
        evidence, ownership = prepare_workflow_evidence(case)
        return {'evidence': evidence, 'ownership_analysis': ownership,
                'assessment_id': 'RA-' + case.application.application_id}

    def assess_rf(state):
        return {'risk_assessment': evaluate_rf(state['evidence'], catalogue)}

    def assess_appetite(state):
        dimensions, appetite, rules = evaluate_dimensions(
            state['evidence'], catalogue, state['risk_assessment'])
        return {'dimensions': dimensions, 'final_appetite': appetite,
                'appetite_rule_ids': rules}

    def route(state):
        rf = state['risk_assessment']
        if state['evidence'].get('input_errors') or rf['input_errors']:
            return 'review'
        if rf['hard_gate'] != 'Clear' or rf['doc_gate'] != 'Clear':
            return 'review'
        return 'review' if state['final_appetite'] == 'Pending' or rf['risk_band'] == 'Pending' else 'ready'

    def finish(next_step):
        def node(state):
            return {'next_step': next_step, 'audit': {
                'workbook_sha256': dict(catalogue.versions),
                'policy_version': state['evidence'].get('policy', {}).get('version'),
                'evaluated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'input_evidence': state['evidence'],
                'input_errors': state['evidence'].get('input_errors', []) + state['risk_assessment']['input_errors'],
                'warnings': list(catalogue.warnings),
            }}
        return node

    builder = StateGraph(AppetiteState, input_schema=AppetiteInput, output_schema=AppetiteOutput)
    builder.add_node('prepare_evidence', prepare)
    builder.add_node('assess_risk_factors', assess_rf)
    builder.add_node('assess_appetite', assess_appetite)
    builder.add_node('review_required', finish('review_required'))
    builder.add_node('ready_for_safeguards', finish('safeguards'))
    builder.add_edge(START, 'prepare_evidence')
    builder.add_edge('prepare_evidence', 'assess_risk_factors')
    builder.add_edge('assess_risk_factors', 'assess_appetite')
    builder.add_conditional_edges('assess_appetite', route, {
        'review': 'review_required', 'ready': 'ready_for_safeguards',
    })
    builder.add_edge('review_required', END)
    builder.add_edge('ready_for_safeguards', END)
    return builder.compile(checkpointer=checkpointer)


# Discovery entry point for LangGraph CLI / Studio.
graph = build_appetite_graph()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, default=DEFAULT_JSON)
    parser.add_argument('--case', help='Exact test_case_id; omitted means all cases')
    parser.add_argument('--rules-dir', type=Path, default=DEFAULT_RULES)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    items = json.loads(args.json.read_text(encoding='utf-8'))['test_cases']
    if args.case:
        items = [item for item in items if item['test_case_id'] == args.case]
        if not items:
            parser.error(f'Unknown case: {args.case}')
    runner = build_appetite_graph(args.rules_dir)
    results = [dict(case=item['test_case_id'], **runner.invoke({'case': item})) for item in items]
    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)


if __name__ == '__main__':
    main()
