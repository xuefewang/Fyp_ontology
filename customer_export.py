"""Export existing engine results plus customer report/SQL/graph contracts."""
import argparse
import json
from pathlib import Path

from database_io import export_graph, export_rows
from kyc_poc import DEFAULT_JSON, DEFAULT_RULES, case_from_json
from policy_engine import assess_workflow, load_catalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, default=DEFAULT_JSON)
    parser.add_argument('--case')
    parser.add_argument('--rules-dir', type=Path, default=DEFAULT_RULES)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    items = json.loads(args.json.read_text(encoding='utf-8'))['test_cases']
    if args.case:
        items = [x for x in items if x['test_case_id'] == args.case]
    if not items:
        parser.error('No matching cases')
    cat = load_catalogue(args.rules_dir)
    results = [assess_workflow(case_from_json(x), cat) for x in items]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, values in [('results.json',results), ('report-payloads.json',[x['report_payload'] for x in results]),
                         ('sql-rows.json',[export_rows(x) for x in results]), ('graph-projection.json',[export_graph(x) for x in results])]:
        (args.output_dir/name).write_text(json.dumps(values, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Exported {len(results)} assessment(s); no database connection was made.')


if __name__ == '__main__':
    main()
