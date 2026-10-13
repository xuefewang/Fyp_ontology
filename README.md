# IS4920 本体与 AML 规则评估

流程：HardGate / DocGate → RF 评分 → D1–D3 Appetite → Safeguards → Policy Integration。
三个原始工作簿保存在 `rules/`，运行时读取规则编号、分值、风险带和最终结果文本，并记录文件 SHA-256。Excel 单元格是规则数据，不执行其中的文本。

## 运行

```bash
cd /workspace/Fyp_ontology
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python kyc_poc.py --json test_cases_data.json
.venv/bin/python kyc_poc.py --json test_cases_data.json --detail case11
.venv/bin/python kyc_poc.py --json test_cases_data.json --output /tmp/kyc-results.json
.venv/bin/python -m unittest discover -s tests -v
```

也可使用已配置的 `/workspace/.venvs/fyp-ontology/bin/python`。`--rules-dir` 指定三个工作簿的其他目录。所有默认路径相对于脚本目录，不再依赖 Windows 下载目录。

`test_cases_data.json` 尚未提供。程序保留原来的 `test_cases` 数组／根数组，以及 `data.Application/Industry/party1/party2/Document/WatchList` 格式。以下为新增输入协议，不声称未提供的 JSON 已包含这些字段。旧输入仍能读取，但新增事实缺失时保持 Pending。`--builtin` 加载原来的 15 个案例，它们没有核验记录，结果会暂停；内置输入不是完整的新版验证案例。

## 新增输入：`data.WorkflowEvidence`

以下各类输入都是有证据支撑的核验结论。程序不连接监管机构验证真实性，`verified: true` 不能代替实际核验。缺失、未知或缺少证据编号的结论不推定为 Clear / Low / Within。

```json
{
  "risk_factors": {
    "RF-02": {
      "verified": true,
      "evidence_refs": ["screening-2026-001", "false-positive-review-001"],
      "facts": {
        "ubo_sanctions_match": true,
        "screening_review_complete": true,
        "false_positive_excluded": true
      }
    }
  },
  "D1": {"evidence_complete": false, "evidence_refs": [], "facts": {}},
  "D2": {"evidence_complete": false, "evidence_refs": [], "facts": {}},
  "D3": {"evidence_complete": false, "evidence_refs": [], "facts": {}},
  "signals": {},
  "safeguards": {},
  "policy": {
    "version": "approved-policy-version",
    "approval_matrix_approved": true,
    "required_approver_roles": ["Compliance Officer"]
  },
  "approvals": []
}
```

### 风险因子

每项 RF 要单独评估。支持两种输入：

- 提供 `facts`，由程序执行下表谓词；每项记录还必须有 `verified: true` 和非空 `evidence_refs`。
- 提供已由核验人员按该编号完整判定的 `status: "Hit" / "Clear" / "Pending"`，也必须有核验标记及证据编号。这里的 Hit 表示工作簿全部触发条件已确认，不是初步风险信号。若同时提供 facts 和 status，两者矛盾则报错。

未提供 RF 项保持 Pending。HardGate 和 DocGate 不计分；Score 只累计确认命中的分值，不把失效身份核查、待补件或疑似制裁转换为高分。

| RF | facts 谓词（true 为已核实条件，不是推测） |
| --- | --- |
| 01 | `prohibited_jurisdiction` AND `official_verification_complete` |
| 02 | `ubo_sanctions_match` AND `screening_review_complete` AND `false_positive_excluded` |
| 03 | `regulated_activity` AND `licence_missing_revoked_or_invalid` AND `official_verification_complete` AND `policy_prohibits_onboarding` |
| 04 | `ubo_verification_status = "Failed"`；只有 `"Verified"` 明确不命中，其余 Pending |
| 05 | `control_verification_status = "Failed"`；同上 |
| 06 | `prohibited_or_illegal_activity` AND `reliable_evidence_verified` AND `approved_prohibition_met` |
| 07 | `illegal_subscription_funds` AND `reliable_evidence_verified` AND `approved_prohibition_met` |
| 08 | `fraudulent_identity_or_key_documents` AND `independent_evidence_verified` AND `approved_prohibition_met` |
| 09 | `mandatory_document_missing` |
| 10 | `articles_shareholder_register_shareholding_conflict` |
| 11 | `ubo_unidentified_or_ownership_evidence_insufficient` AND `reasonable_measures_completed` |
| 12 | `registration_or_ubo_nationality_greylisted` |
| 13 | `ubo_confirmed_pep` |
| 14 | `ubo_pep_family_or_close_associate` |
| 15 | `ownership_layers >= 4`，整数，完整股权图最大链深度 |
| 16 | `nominee_or_trust_indirect_shareholding` |
| 17 | `principal_business_in_approved_high_risk_category`；含 VASP/MSO、博彩、现金密集、贵金属、国防／两用 |
| 18 | `third_party_funding` OR `non_own_name_account` OR `borrowed_or_leveraged_funding` |
| 19 | `incorporated_under_one_year` AND `no_substantive_history_or_audited_accounts` |
| 20 | `policy_defined_high_leverage` OR `negative_cash_flow` OR `assets_or_cash_insufficient_for_subscription` |
| 21 | `jurisdiction_count >= 3` OR `complex_cross_border_or_offshore_arrangement` |

阈值必须来自已批准政策；程序不自行创造杠杆、财务、重大性或行业门槛。因子分值从工作簿读取。Low 0–4、Medium 5–9、High ≥10。`score_band` 是已确认分值的数值带；`risk_band` 在闸口命中／未清除或存在未核验 RF 时为 Pending，确认禁止单独保存在 HardGate，绝不强制改成 High。

### D1–D3

每个维度有 `evidence_complete: true`、非空 `evidence_refs` 和以下 `facts`。布尔值只能是真正的 JSON true/false；缺失不等于 false。政策目录、重大性和材料充分性由核验人员依据批准政策评估。命中规则保留全部编号，维度内部取 MAX，再跨维度取 MAX；任何维度 Pending 优先于 High。

| 规则 | facts 条件 |
| --- | --- |
| D1 前置 | `legal_entity_verified`, `ownership_links_verified`, `ubo_verified`, `control_verified` 均 true；下列六个风险标记均须明确 true/false |
| D1-01 | `complex_ownership`, `nominee_or_trust`, `cross_entity_or_border_complexity`, `control_rights_discrepancy`, `unusual_structure`, `material_unresolved_contradiction` 均 false |
| D1-02 | `complex_ownership` true（最大股权深度至少4层）、`rationale_sufficient` true、`material_unresolved_contradiction` false |
| D1-03 | `nominee_or_trust`, `relevant_parties_verified`, `legal_documents_sufficient`, `rationale_sufficient` true |
| D1-04 | `cross_entity_or_border_complexity`, `materiality_policy_approved`, `rationale_sufficient` true；`material_unresolved_contradiction`, `control_rights_discrepancy`, `unusual_structure` false |
| D1-05 | `control_rights_discrepancy` true、`discrepancy_explained` false |
| D1-06 | `unusual_structure`, `review_completed` true、`rationale_sufficient` false |
| D2 前置 | `activity_verified`, `taxonomy_approved` true；`material_discrepancy`, `material_high_risk_activity`, `regulated` 均明确布尔；受监管时 `licence_verified` true |
| D2-01 | `category = "Standard"`，没有重大分类矛盾或重大高风险活动 |
| D2-02 | `category = "Sensitive"`，没有重大高风险活动 |
| D2-03 | `category = "HighRisk"`、`activity_not_prohibited` true |
| D2-04 | `multiple_activities`, `material_high_risk_activity`, `materiality_policy_approved`, `high_risk_activity_verified`, `activity_not_prohibited` true |
| D2-05 | 分类证据不足或 `material_discrepancy` true，Pending |
| D3 前置 | `mandatory_evidence_authenticated` true；`material_anomaly`, `unresolved_discrepancy`, `complex_funding`, `unusual_subscription` 均明确布尔 |
| D3 对账 | `source_event_identified`, `owner_identified`, `amount_dates_verified`, `funds_sufficient`, `records_reconciled` true；`unresolved_discrepancy` false |
| D3-01 | 对账通过、`own_account`, `records_verified` true；`complex_funding`, `unusual_subscription`, `material_anomaly` false |
| D3-02 | 对账通过；`complex_funding`, `material_parties_verified`, `legal_basis_verified`, `flows_verified`, `arrangement_policy_permitted` true |
| D3-03 | `material_anomaly`, `records_verified`, `review_completed` true、`anomaly_explained` false |
| D3-04 | 必需证据缺失／无法认证，Pending |
| D3-05 | 对账通过；`unusual_subscription`, `materiality_policy_approved`, `exceptional_event_verified` true |

FA-01：全 Low → Within。FA-02：至少一项 Medium、无 High → Conditional。FA-03：任一 High → Restricted。FA-04：任一缺失／Pending → Pending。FA-05：记录全部命中规则，以 MAX 聚合，不累加等级。

### Safeguards

SG-01 EDD、SG-14、SG-15 被最终工作簿明确排除，输入这些编号会报错。

仅为实际风险生成适用性审核候选：SG-02（PEP／资金异常／高风险业务）、SG-03（PEP／高风险关系）、SG-04（Restricted／PEP／高风险关系）、SG-05（复杂或异常资金及第三方／账户不匹配）、SG-06（限额确能减轻敞口）、SG-07（服务限制风险）、SG-08（Conditional／Restricted）、SG-09（PEP／灰名单／重筛暴露）、SG-10（Restricted／高风险关系）、SG-11（复杂股权／代持／信托）、SG-12（受监管业务）、SG-13（PEP／双签要求）。

额外风险标记放在 `signals`：`high_risk_relationship`, `third_party_or_account_mismatch`, `exposure_limit_reduces_risk`, `service_restriction_exposure`, `rescreening_exposure`, `regulated_activity`, `dual_approval_required`。非 RF／D 输入可以通过附加、有政策及证据支持的 safeguard 记录提交。

候选不代表自动适用。每个候选需要 `safeguards.SG-xx` 记录：

```json
{
  "applicable": true,
  "policy_approved": true,
  "evidence_refs": ["approved-control-policy", "case-specific-trigger"],
  "status": "Complete",
  "owner": "control-owner-id",
  "completion_evidence_refs": ["verified-completion-record"]
}
```

`applicable: false` 也须批准政策和证据；不会默认要求全部 SG。适用性未明确为 Pending。适用项状态为 Complete／Incomplete／Pending；全部适用项完成才为 Complete；没有适用项才为 Not Applicable。SG-04 还必须有 Senior Compliance 批准；SG-13 必须有 Compliance Director 和 Risk Director 的两份、不同审批人的批准。完成控制不会降低原 Appetite。

### 审批与最终判断

`approvals` 每项示例：

```json
{
  "decision": "Approved",
  "authorized": true,
  "role": "Compliance Officer",
  "reviewer": "reviewer-id",
  "authority": "approved-authority-matrix",
  "timestamp": "2026-10-13T00:00:00Z",
  "rationale": "signed decision rationale",
  "evidence_refs": ["signed-approval-record"]
}
```

只有 Approved／Rejected 且授权、签署证据、审批人、权限、时间和理由完整的记录有效。`policy.required_approver_roles` 列出必要审批角色；`policy.approval_matrix_approved` 和 `policy.version` 是审批权限的前提。Restricted 还需 `policy.restricted_acceptance_permitted` 明确为 true／false，未设置进入政策确认，不推定允许。权限与阈值须由政策负责人批准，程序只检查结构化结论，不验证签名的真实性。

PI 优先级：确认禁止 PI-01 → 待核验 PI-02 → 文件待处理 PI-03 → RF／Appetite 待完成 PI-04 → 必要保障未完成 PI-05 → 授权拒绝 PI-06 → 待审批 PI-07 → Within 批准 PI-08／Conditional 批准 PI-09／政策允许的 Restricted 批准 PI-10。缺失、矛盾或未明确政策通过 PI-11 兜底，不能成为批准。Policy Integration 仅读取上游输出，不重算其评分或等级。

完整输出保留各阶段状态、因子命中、未核验规则、维度命中、保障措施、证据引用、审批记录、政策版本、工作簿哈希和评估时间。评估时间与人工签署时间分别保存。

## 工作簿冲突的处理

- 用户已明确要求按实际表格保留 RF-21（3分）。工作簿标题／备注仍说删除 RF-21、保留 RF-22；程序记录警告，不创造没有表格定义的 RF-22。
- AML 表中的 RF-06／RF-08 是旧编号引用，维度判定使用其实际语义（股权复杂度／高风险行业），新版对应 RF-15／RF-17。
- D 表仍有 EDD 动作描述，但最终工作簿明确排除 EDD；本版不执行 SG-01，`edd_required` 恒为 false。
- FA 全 Low 对应 Within，但 SG 旧目录备注提到 PEP 的 Conditional 下限。PEP 命中且 FA 为 Within 时转 PI-11，保留原 FA 输出，等待政策确认，不能通过普通批准绕过这项歧义。

## 验证范围

`tests/test_workflow.py` 使用显式标记的合成核验证据和真实上传工作簿，覆盖规则、边界、优先级、缺失证据、审批及外部 JSON 命令行。并未执行尚未提供的真实39个案例，也不生成或冒充 `test_cases_data.json`。
