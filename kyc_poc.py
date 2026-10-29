#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IS4920: HardGate/DocGate → RF → Appetite → Safeguards → Policy Integration.

The three bundled workbooks supply rule IDs, points, bands and policy outcomes.
External JSON preserves the original entity layout and adds data.WorkflowEvidence.
Inputs without verification facts remain Pending. All workbook safeguards SG-01..SG-13 supported, including EDD.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from policy_engine import assess_workflow, load_catalogue
from case_migration import workflow_from_case

ROOT = Path(__file__).resolve().parent
DEFAULT_JSON = ROOT / "test_cases_data.json"
DEFAULT_RULES = ROOT / "rules"

@dataclass
class WatchList:
    """案例自带的名单条目，表达 listed-in 关系（该主体被列入了哪些名单）。"""

    watchlist_id: str
    list_name: str
    list_version_date: str = ""


@dataclass
class Industry:
    industry_id: str
    industry_name: str
    industry_tier: str  # I1..I4，输入直接给定


@dataclass
class Application:
    application_id: str
    submission_date: str
    application_status: str = "Submitted"


@dataclass
class Document:
    document_id: str
    document_type: str
    status: str  # Provided / Missing / Expired / Conflicting
    fact_verified: str
    is_mandatory: bool


@dataclass
class Party:
    """party1 / party2 共用结构。party1 为法人（多 full_name），party2 为股东链上的当事方。"""

    party_id: str
    person_id: str = ""
    full_name: str = ""
    is_ubo: bool = False
    is_nominee: bool = False
    is_trust: bool = False
    sanctions_flag: bool = False
    shareholding_percentage: float = 0.0
    pep_status: str = "None"
    nationality: str = ""
    registration_jurisdiction: str = ""


@dataclass
class Case:
    name: str
    application: Application
    industry: Industry
    party1: Party
    party2_chain: list[Party] = field(default_factory=list)  # 股东链，按层级顺序
    documents: list[Document] = field(default_factory=list)
    watchlists: list[WatchList] = field(default_factory=list)
    assessment_date: str = ""
    workflow_evidence: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)

    def all_parties(self) -> list[Party]:
        return [self.party1] + self.party2_chain

    def ubos(self) -> list[Party]:
        return [p for p in self.all_parties() if p.is_ubo]

    def ownership_layers(self) -> int:
        """旧输入总节点数，含两端；不用于新版RF-15的中间法人计数。"""
        return 1 + len(self.party2_chain)


def _party(d: dict) -> Party:
    return Party(
        party_id=d.get("party_id", ""),
        person_id=d.get("person_id", ""),
        full_name=d.get("full_name", ""),
        is_ubo=bool(d.get("is_ubo", False)),
        is_nominee=bool(d.get("is_nominee", False)),
        is_trust=bool(d.get("is_trust", False)),
        sanctions_flag=bool(d.get("sanctions_flag", False)),
        shareholding_percentage=d.get("shareholding_percentage", 0.0),
        pep_status=d.get("pep_status", "None"),
        nationality=d.get("nationality", ""),
        registration_jurisdiction=d.get("registration_jurisdiction", ""),
    )


def case_from_json(item: dict) -> Case:
    data = item["data"]
    app = data["Application"]
    ind = data["Industry"]
    return Case(
        name=f"{item.get('test_case_id', '')} {item.get('company', '')}".strip(),
        application=Application(app["application_id"], app["submission_date"],
                                app.get("application_status", "")),
        industry=Industry(ind["industry_id"], ind["industry_name"], ind["industry_tier"]),
        party1=_party(data["party1"]),
        party2_chain=[_party(p) for p in data.get("party2", [])],
        documents=[Document(d["document_id"], d["document_type"], d["status"],
                            d["fact_verified"], bool(d.get("is_mandatory", False)))
                   for d in data.get("Document", [])],
        watchlists=[WatchList(w["watchlist_id"], w["list_name"], w.get("list_version_date", ""))
                    for w in data.get("WatchList", [])],
        assessment_date=app["submission_date"],
        expected=item.get("expected", {}),
        workflow_evidence=(data["WorkflowEvidence"] if "WorkflowEvidence" in data
                           else workflow_from_case(item) if "SourceOfFunds" in data or "ubo_verification" in data
                           else {}),
    )


def load_cases(path: Path) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw["test_cases"] if isinstance(raw, dict) else raw
    return [case_from_json(it) for it in items]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON, help="外部案例 JSON（默认仓库目录）")
    ap.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES, help="三个规则工作簿的目录")
    ap.add_argument("--detail", nargs="?", const="all", help="输出完整流程 JSON，可指定案例号")
    ap.add_argument("--check-expected", action="store_true", help="核对每个案例的预期规则、最终结果、Appetite和分值；不匹配则返回非零")
    ap.add_argument("--output", type=Path, help="保存完整流程及审计 JSON")
    args = ap.parse_args()
    try:
        catalogue = load_catalogue(args.rules_dir)
        cases = load_cases(args.json)
        if not cases:
            raise ValueError("没有输入案例，不能视为已完成验证")
        results = [assess_workflow(case, catalogue) for case in cases]
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 2
    if args.check_expected:
        fields = {'final_rule_id': lambda r: r['final_decision']['rule_id'],
                  'final_result': lambda r: r['final_decision']['result'],
                  'final_appetite': lambda r: r['final_appetite'],
                  'total_score': lambda r: r['risk_assessment']['total_score']}
        mismatches = []
        for case, result in zip(cases, results):
            if not all(k in case.expected for k in fields):
                mismatches.append(f'{case.name}: 缺少完整预期结果')
            for key, getter in fields.items():
                if key in case.expected and case.expected[key] != getter(result):
                    mismatches.append(f'{case.name}: {key} expected={case.expected[key]!r}, actual={getter(result)!r}')
        if mismatches:
            print('\n'.join(mismatches), file=sys.stderr)
            return 1
        print(f'预期结果核对通过：{len(results)} / {len(results)}')
    for warning in catalogue.warnings:
        print(f"规则说明：{warning}", file=sys.stderr)
    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.detail:
        selected = [r for r in results if args.detail == "all" or args.detail in r["case"]]
        if not selected:
            print("未找到指定案例", file=sys.stderr)
            return 2
        print(json.dumps(selected, ensure_ascii=False, indent=2))
    else:
        for r in results:
            rf = r["risk_assessment"]
            print(f'{r["case"]}: HardGate={rf["hard_gate"]}; DocGate={rf["doc_gate"]}; '
                  f'score={rf["total_score"]}; RiskBand={rf["risk_band"]}; '
                  f'Appetite={r["final_appetite"]}; Safeguards={r["safeguards"]["status"]}; '
                  f'{r["final_decision"]["rule_id"]} {r["final_decision"]["result"]}')
    print(f"合计 {len(results)} 个案例；保障措施支持 SG-01～SG-13。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
