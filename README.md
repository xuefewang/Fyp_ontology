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

`test_cases_data.json` 包含 **174 个案例**：28 个上传原始案例（全部原字段保留）+ 146 个明确标为合成数据的扩展场景。覆盖全部 RF-01～21、PI-01～11、SG-01～13，另有计分边界、优先级竞争、PEP下限、跨评估／政策版本、股权分支／循环及持续控制配置案例。每项都有 `WorkflowEvidence` 和预先独立填写的 `expected`，包括最终规则、最终结果、Appetite及分值。合成核验材料与批准政策仅为测试断言，不是实际客户核验。

所有测试输入仅保存在 `test_cases_data.json`，不需要案例生成脚本。原始28个案例的输入快照保存在同一文件的 `original_source`，并记录内容SHA-256；实际运行只读取 `test_cases` 数组。修改或增加场景时直接编辑JSON中的输入及独立预期结果。

```bash
python kyc_poc.py --check-expected
python kyc_poc.py --detail new001 --output /tmp/kyc-results.json
python -m unittest discover -s tests -v
```

`--check-expected` 核对全部案例的规则、最终结果、Appetite与分值；失败或缺失预期字段返回非零。程序可直接读取原始文件并在运行时迁移；显式 `WorkflowEvidence` 优先。旧版Python内置案例已移除，所有场景统一从JSON读取。

## 最终判断顺序（新版工作簿）

| 顺序 | PI | 处理 |
| --- | --- | --- |
| 1 | PI-01 | 确认HardGate拒绝 |
| 2 | PI-03 | RF补件／人工复核（保留同时存在的全部原因） |
| 3 | PI-02 | 待核实HardGate |
| 4 | PI-11 | 输入／政策／权限及同次评估、版本检查 |
| 5 | PI-06 | 必要授权审批人已签署拒绝，优先于等待评估及SG |
| 6 | PI-04 | 待完成RiskBand／Appetite评估 |
| 7 | PI-05 | 任何Appetite的必要SG未完成／待定 |
| 8 | PI-07 | 取得全部必要授权签署 |
| 9 | PI-08／09／10 | 按Within／Conditional／Restricted接受，共同前提全部成立 |

顺序从更新后的 PI 工作簿读取，并检查其与支持的执行顺序一致。Final Integration只读取上游结果，不重新计算评分、Appetite或SG。确认HardGate不能被SG覆盖；High RiskBand不自动拒绝；新版明确允许复核后接受Restricted，仍保留Restricted档位及限制，不再要求旧版额外的 `restricted_acceptance_permitted` 开关。

## 原始字段的迁移

迁移将原文件中明确给出的测试断言转换为规则输入，引用形如 `original-test-case:case01#/data/ubo_verification` 的 JSON 字段。引用表示测试输入来源，不代表真实监管材料或独立核验。`hardgate_pending` true 时不确认未解决的 HardGate；已有 Failed／confirmed 等测试结论按原值保留。

- WatchList／司法管辖区、制裁及 PEP 标记、牌照／UBO／控制核验、非法活动／资金／欺诈声明映射到 RF-01～08、12～14。
- Document 映射文件缺失／持股比例冲突，代持映射相应规则。RF-15改按中间法人计数；旧列表未明确法人类型时，不把列表长度或两端算作中间法人。
- `SourceOfFunds` 映射付款账户、资金结构、对账、异常及材料充分性。涉及款项日期、法律依据、第三方许可及重大性政策时，原文件未给出的事实保持 null。
- 新设公司按申请日与成立日期比较一周年；不会仅凭“成立不到一年”补造“无经营历史／无经审计报表”的结论。
- 杠杆、现金流、净资产、跨境结构按原有字段保留为测试断言。
- `safeguards_completed` 只表示原案例声称完成：迁移保留 `reported_status: Complete`，不凭此补造措施负责人、批准政策、完成证据或高级签署；正式状态仍为 Pending。
- `officer_decision` 原值保存在 `reported_officer_decision`，不会凭一个 Approved／Rejected 字符串伪造授权签署记录。

当前28个案例中缺少批准行业目录、部分资金核验事实、权限矩阵及有签署证据的审批记录，因此部分案例进入 HOLD。这是资料／政策缺口，不是已批准的客户判断。补齐 `WorkflowEvidence` 或原始数据中的 `PolicyEvidence`、`ApprovalEvidence`、`SafeguardEvidence` 后可继续。

### 新增输入：`data.WorkflowEvidence`

结构包括 `risk_factors`、`D1`、`D2`、`D3`、`signals`、`safeguards`、`policy`、`approvals`。各维度有 `evidence_complete`、`evidence_refs` 和 `facts`；缺失事实不推定为 false。正式核验记录仍需完整事实与证据。

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
| 11 | `ubo_verification_status` 为 Pending／AwaitingInformation，或 `ownership_evidence_insufficient`／`remediation_outstanding` true；最终 Failed 不属于RF-11 |
| 12 | `registration_or_ubo_nationality_greylisted` |
| 13 | `ubo_confirmed_pep` |
| 14 | `ubo_pep_family_or_close_associate` |
| 15 | `intermediate_legal_entity_count >= 4`；不含投资者与自然人两端，多分支取最大值，循环转人工复核 |
| 16 | `nominee_or_trust_indirect_shareholding` |
| 17 | `principal_business_in_approved_high_risk_category`；含 VASP/MSO、博彩、现金密集、贵金属、国防／两用 |
| 18 | `third_party_funding` OR `non_own_name_account` OR `borrowed_or_leveraged_funding` |
| 19 | `incorporated_under_one_year` AND `no_substantive_history_or_audited_accounts` |
| 20 | `policy_defined_high_leverage` OR `negative_cash_flow` OR `assets_or_cash_insufficient_for_subscription` |
| 21 | `jurisdiction_count >= 3` OR `complex_cross_border_or_offshore_arrangement` |

阈值必须来自已批准政策；程序不自行创造杠杆、财务、重大性或行业门槛。因子分值从工作簿读取。Low 0–4、Medium 5–9、High ≥10。`score_band` 是已确认分值的数值带；`risk_band` 在闸口命中／未清除或存在未核验 RF 时为 Pending，确认禁止单独保存在 HardGate，绝不强制改成 High。未清除的DocGate在正式评分前暂停：`total_score` 为 null，`scoring_status` 为 Held，`confirmed_score_subtotal`仅供审计；保留缺件、复核和待核实全部原因。

### D1–D3

每个维度有 `evidence_complete: true`、非空 `evidence_refs` 和以下 `facts`。布尔值只能是真正的 JSON true/false；缺失不等于 false。政策目录、重大性和材料充分性由核验人员依据批准政策评估。命中规则保留全部编号，维度内部取 MAX，再跨维度取 MAX；任何维度 Pending 优先于 High。

| 规则 | facts 条件 |
| --- | --- |
| D1 前置 | `intermediate_legal_entity_count` 必须明确并与 `complex_ownership` 一致； `legal_entity_verified`, `ownership_links_verified`, `ubo_verified`, `control_verified` 均 true；下列六个风险标记均须明确 true/false |
| D1-01 | `complex_ownership`, `nominee_or_trust`, `cross_entity_or_border_complexity`, `control_rights_discrepancy`, `unusual_structure`, `material_unresolved_contradiction` 均 false |
| D1-02 | `complex_ownership` true（至少4个中间法人，不含两端）、`rationale_sufficient` true、`material_unresolved_contradiction` false |
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

FA-01：全 Low → Within。FA-02：至少一项 Medium、无 High → Conditional。FA-03：任一 High → Restricted。FA-04：任一缺失／Pending → Pending。FA-05：记录全部命中规则，以 MAX 聚合，不累加等级。FA-06：RF-13／RF-14确认命中且基础评估完成时，最终Appetite最低Conditional，不降低Restricted，不绕过Pending。FA-07：没有可分级规则或政策门槛未批准时保持Pending。新增D1-07为所有权证据充分性闸口，返回适用RF补件任务；最终Failed仍由HardGate处理。

### Safeguards

采纳更新工作簿实际列出的全部SG-01～SG-13，包含SG-01 EDD。SG-14／SG-15在新版模块中没有条目，按用户后续要求不添加。不存在硬编码排除名单。

按实际触发条件为任何Appetite选择措施，**Within也可能需要SG**，有必要控制未完成就不能接受。SG-01候选包括Restricted／High、复杂或异常结构、高风险业务、资金异常及多重计分风险，最终适用性仍需批准政策和证据。输出 `edd_required`（true／false／未知null）及 `edd_status`；完成措施不降低Appetite。

仅为实际风险生成适用性审核候选：SG-02（PEP／PEP紧密关联人／资金异常／高风险业务）、SG-03（PEP／PEP紧密关联人／高风险关系）、SG-04（Restricted／PEP及关联人／高风险关系）、SG-05（复杂或异常资金及第三方／账户不匹配）、SG-06（限额确能减轻敞口）、SG-07（服务限制风险）、SG-08（Conditional／Restricted）、SG-09（PEP及关联人／灰名单／重筛暴露）、SG-10（Restricted／高风险关系）、SG-11（复杂股权／代持／信托）、SG-12（受监管业务）、SG-13（PEP及关联人／双签要求）。

额外风险标记放在 `signals`：`high_risk_relationship`, `third_party_or_account_mismatch`, `exposure_limit_reduces_risk`, `service_restriction_exposure`, `rescreening_exposure`, `regulated_activity`, `dual_approval_required`。非 RF／D 输入可以通过附加、有政策及证据支持的 safeguard 记录提交。

候选不代表自动适用。每个候选需要 `safeguards.SG-xx` 记录：

```json
{
  "applicable": true,
  "policy_approved": true,
  "evidence_refs": ["approved-control-policy", "case-specific-trigger"],
  "status": "Complete",
  "owner": "control-owner-id",
  "completion_evidence_refs": ["verified-completion-record"],
  "completion_verified": true,
  "verifier": "independent-verifier-id",
  "verified_at": "2026-10-13T09:00:00+08:00"
}
```

`applicable: false` 也须批准政策和证据；不会默认要求全部 SG。`safeguard_selection` 要有 `reviewed: true` 和非空 `evidence_refs`；适用性选择未知不能当成Not Applicable。适用性未明确为 Pending。适用项状态为 Complete／Incomplete／Pending；全部适用项完成才为 Complete；没有适用项才为 Not Applicable。SG-04 还必须有 Senior Compliance 批准；SG-13 必须有 Compliance Director 和 Risk Director 的两份、不同审批人的批准。完成控制不会降低原 Appetite。SG-08／09／10的Complete仅表示准入前配置就绪，须提供 `ongoing_setup`：`cadence_approved`、`setup_verified`、`signed_off` true，以及 `cadence`、`first_due_date`、`executable_plan`、`verifier`、`verified_at`、`evidence_refs`。缺配置为Incomplete，未明确批准频率为Pending；不声称未来义务已执行。

### 审批与最终判断

`approvals` 每项示例：

```json
{
  "assessment_id": "RA-APP-2026-0001",
  "policy_version": "approved-policy-version",
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

只有Approved／Rejected且授权、签署证据、审批人、权限、时间和理由完整的记录有效。签署的 `assessment_id` 须匹配 `RA-<application_id>`，`policy_version` 须匹配当前 `policy.version`。不属于当前评估／政策的签署进入PI-11，不能计为批准或拒绝。

`policy.required_approver_roles`、`approval_matrix_approved`和`version`定义必要权限。所选SG-04／SG-13还会加入其必要高级／双签角色。任一必要授权拒绝→Rejected；否则全部必要角色有效批准→Approved；否则Pending。`upstream_contexts` 可提供各阶段的 `assessment_id` 和 `policy_version`，不一致进入PI-11。

完整输出保留各阶段状态、因子命中、未核验规则、维度命中、保障措施、证据引用、审批记录、政策版本、工作簿哈希和评估时间。评估时间与人工签署时间分别保存。

## 股权图输入

`WorkflowEvidence.ownership_structure` 提供 `investor_id`、`nodes`（每项`id`及`entity_type: LegalEntity / NaturalPerson`）和`edges`（每项`entity_id`、`owner_id`，从被持股实体指向持有人）。程序统计到自然人路径的最大中间法人数量，更新RF-15及D1复杂度。直接投资者→自然人是0，3个中间法人不命中，4个命中；循环保留审计并转Human Review，不无限递归。不支持用旧的“投资者＋party2列表长度”代替新计数。

## 版本与验证

三个更新工作簿原样保存在`rules/`。旧RF-21备注矛盾和PEP下限歧义已在新版消除，不再输出旧警告。运行结果记录工作簿SHA-256、规则编号、评估及政策版本、签署、证据和控制就绪信息。原始上传案例缺失的政策／授权材料仍保持未知。

测试逐项核对174个独立预期结果，检查RF-01～21、PI-01～11、SG-01～13覆盖，并验证原28个案例保留、最终顺序、计分边界、PEP下限、版本隔离、全部必要签署、Within控制、持续配置以及1200层图不会递归溢出。合成扩展明确标记为测试材料；测试代码仅读取静态JSON并验证输出，不生成案例。

## Appetite 的 LangGraph 入口

已提供 `appetite.py` 子图与 `langgraph.json`。安装、调用方式和范围见 [APPETITE_LANGGRAPH.md](APPETITE_LANGGRAPH.md)。

## 完整 AML LangGraph 工作流

`workflow_graph.py` 将证据准备、RF闸口与评分、Appetite、Safeguards、Policy Integration 和审计报告连接为总图。运行方式：

```bash
python -m pip install -r requirements-langgraph.txt
python workflow_graph.py --case case01 --trace --output case01-result.json
python workflow_graph.py --check-expected --output workflow-results.json
```

节点连接、共享状态与验证范围见 [WORKFLOW_LANGGRAPH.md](WORKFLOW_LANGGRAPH.md)。`langgraph.json` 中完整图名为 `aml_workflow`。
