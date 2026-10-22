"""Complete deterministic AML workflow, orchestrated by LangGraph."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from kyc_poc import DEFAULT_JSON, DEFAULT_RULES, case_from_json
from policy_engine import (
    evaluate_rf, evaluate_dimensions, evaluate_safeguards, integrate,
    load_catalogue, prepare_workflow_evidence, format_workflow_result,
)


class WorkflowInput(TypedDict):
    case: dict[str, Any]


class WorkflowOutput(TypedDict):
    result: dict[str, Any]


class WorkflowState(WorkflowInput, WorkflowOutput, total=False):
    evidence: dict[str, Any]
    ownership_analysis: dict[str, Any] | None
    risk_assessment: dict[str, Any]
    dimensions: dict[str, Any]
    final_appetite: str
    appetite_rule_ids: list[str]
    safeguards: dict[str, Any]
    final_decision: dict[str, Any]


def build_workflow_graph(rules_dir: Path = DEFAULT_RULES, *, checkpointer=None):
    """Run every assessment stage; integration alone applies decision priorities.

    HardGate/DocGate and scoring are one existing evaluate_rf operation.
    Even blocked cases receive full diagnostic assessments, as in assess_workflow.
    Existing signed approvals are consumed; this graph does not collect approvals.
    """
    cat = load_catalogue(Path(rules_dir))

    def prepare(state):
        evidence, ownership = prepare_workflow_evidence(case_from_json(state['case']))
        return {'evidence': evidence, 'ownership_analysis': ownership}

    def risk(state):
        return {'risk_assessment': evaluate_rf(state['evidence'], cat)}

    def appetite(state):
        dimensions, level, rules = evaluate_dimensions(state['evidence'], cat, state['risk_assessment'])
        return {'dimensions': dimensions, 'final_appetite': level, 'appetite_rule_ids': rules}

    def safeguards(state):
        return {'safeguards': evaluate_safeguards(state['dimensions'], state['risk_assessment'],
                                                state['final_appetite'], state['evidence'], cat)}

    def integration(state):
        return {'final_decision': integrate(state['risk_assessment'], state['final_appetite'],
                                            state['safeguards'], state['evidence'], cat)}

    def report(state):
        return {'result': format_workflow_result(
            case_from_json(state['case']), cat, state['evidence'], state['ownership_analysis'],
            state['risk_assessment'], state['dimensions'], state['final_appetite'],
            state['appetite_rule_ids'], state['safeguards'], state['final_decision'])}

    builder = StateGraph(WorkflowState, input_schema=WorkflowInput, output_schema=WorkflowOutput)
    stages = [('prepare_evidence', prepare), ('assess_risk_factors', risk),
              ('assess_appetite', appetite), ('assess_safeguards', safeguards),
              ('policy_integration', integration), ('build_report', report)]
    previous = START
    for name, node in stages:
        builder.add_node(name, node)
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    return builder.compile(checkpointer=checkpointer)


graph = build_workflow_graph()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, default=DEFAULT_JSON)
    parser.add_argument('--case', help='Exact test_case_id; omitted runs all cases')
    parser.add_argument('--rules-dir', type=Path, default=DEFAULT_RULES)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--trace', action='store_true', help='Print node updates to stderr')
    parser.add_argument('--check-expected', action='store_true')
    args = parser.parse_args()
    items = json.loads(args.json.read_text(encoding='utf-8'))['test_cases']
    if args.case:
        items = [item for item in items if item['test_case_id'] == args.case]
        if not items:
            parser.error(f'Unknown case: {args.case}')
    runner = build_workflow_graph(args.rules_dir)
    results, failures = [], []
    for item in items:
        result = None
        for event in runner.stream({'case': item}, stream_mode='updates'):
            if args.trace:
                print(json.dumps({'case': item['test_case_id'], 'update': event}, ensure_ascii=False), file=sys.stderr)
            if 'build_report' in event:
                result = event['build_report']['result']
        if result is None:
            raise RuntimeError('Workflow ended without a report')
        results.append(result)
        if args.check_expected:
            observed = {'final_rule_id': result['final_decision']['rule_id'],
                        'final_result': result['final_decision']['result'],
                        'final_appetite': result['final_appetite'],
                        'total_score': result['risk_assessment']['total_score']}
            expected = item.get('expected', {})
            for key, value in observed.items():
                if key not in expected or expected[key] != value:
                    failures.append(f"{item['test_case_id']} {key}: expected={expected.get(key)!r}, actual={value!r}")
    payload = json.dumps(results, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(payload, encoding='utf-8')
    else:
        print(payload, end='')
    if args.check_expected:
        print('\n'.join(failures) if failures else f'预期结果核对通过：{len(results)} / {len(results)}', file=sys.stderr)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
