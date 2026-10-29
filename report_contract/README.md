# 第二张完整内容清单 → Ontology → SQL → Report

本目录只以用户上传的2026-10-30工作簿第二张「完整內容清單」为需求来源；没有读取或使用第一张作为需求。原表289行、P0 50／P1 78／P2 85／P3 76。原型确认：保留80、需调整144、本轮不适用65。源文件SHA-256与工作表名称记录在requirements.json。

## 先看逐项对照表

[comparison.csv](comparison.csv) 可用Excel打开，共289条，没有按大标题省略子项。每行保留原编号、字段、优先级、适用性、确认和备注，并增加owner、ontology_output、canonical_path、comparison、sql_path、identity_note。

| 责任 | 数量 | 含义 |
| --- | --- | --- |
| Ontology | 170 | 现有规则结果、客户事实、关系及输入资料的结构化输出 |
| Shared | 26 | Ontology提供规则／证据，人工／Report／工作流补审阅或交接记录 |
| External | 28 | AI建议、人工演示覆核、报告版本、执行／修订等外部模块数据 |
| OutOfScope | 65 | 按Excel本轮“不适用”保留范围提示，绝不当作案件政策豁免 |

原始内容所有“需调整”均以备注为准：不开发真实授权、作业许可、汇率或复杂持股计算系统。P2／P3接口被列齐，不代表其后续功能已经实现。

## 统一名称与复用

- party1保持legal person，party2保持原结构。
- 客户编号展示使用原party1.party_id；现有case01-P1是案件内编号，未保证跨案件稳定，不能假称已建立客户主档。按表内备注跨案件customer master放P2。
- application_id复用Application.application_id；case_id首轮在“一次申请一个示例案件”约定下可取同一值，ReportInput.context.case_id可显式指定。二者不同业务含义，不与客户编号混用。
- assessment_id复用原结果；独立run_id仅用于此次SQL输出及重试。没有创建另一份risk_score、appetite_level、customer_decision。
- 原risk_assessment、dimensions、final_appetite、safeguards、final_decision、audit原样放入canonical.engine。
- 身份、Industry、Document、WatchList及SourceOfFunds原字段从canonical.data读取，不要求另起名字重复填。
- 新的事实／人工背景字段在data.ReportInput中按业务分组接收；所有Report字段都有唯一canonical_path。多个报告子项可引用同一来源，不独立维护多个副本。

## 新补的输出

canonical包含data（输入原样）、engine（原引擎输出）、derived（输出组装）。补了公司／业务背景接收接口、主体与组织图、文档核实和差异接口、逐主体筛查样本、同币别对账、SoW背景、逐RF／D／FA／PI解释、缺口及版本追溯。SourceOfFunds保留source_amount、subscription_amount等名称，只另接currency、received_amount、net_available_amount、subscription_currency。

[input-supplement-template.json](input-supplement-template.json) 列出新字段位置，null是待补资料，不是默认合格。将其中ReportInput合并到既有case.data；模板本身不是完整可运行案件。筛查、授权、证据和AI数据由实际来源提供，原型不查询资料商或银行。

report_field覆盖所有289项；status含Provided、Missing、ExternalProvided、ExternalPending、OutOfPrototypeScope。Provided只表示输出值存在，不表示真实、已核实或已获批准。筛查结果、反证、时间及核实记录仍需原始来源，不能仅根据WatchList标记推断筛查已完成。

现有case01实测196个Ontology/Shared项中68个有值、128个缺少源资料；缺失项是实际资料缺口，不是遗漏输出接口。它们均保存到SQL并能逐项查询。筛查或资料不足时ready_for_report=false；该字段仅表示完整输出材料是否齐全，不是准入授权，也不是P0演示里程碑。

## SQL实际读写

已提供Python标准库SQLite适配器，完成实际落库／读回测试；没有外部SQL地址或凭据，因此没有连接已有外部数据库。核心评估代码没有改动，使用新的出口：

```bash
python -m pip install -r requirements-langgraph.txt
python -m report_contract.export --case case01 --db work/report.sqlite3 --output work/case01-report.json
```

三张表：

| 表 | 内容 |
| --- | --- |
| report_requirement | 工作簿289项、责任、统一路径及缺口对照；按contract_hash版本化 |
| ontology_run | run_id、case_id、assessment_id、完整原始输入、原评估及派生输出、汇总 |
| report_field | 每次run的289个报告字段、责任、状态及物化值 |

报告端按run_id读取，不分别拼接“最新客户”“最新评分”。源值在canonical中维护一次，report_field是同一次运行的只读派生快照。相同run_id、相同内容重试不重复插入，内容改变时拒绝覆盖；修改后重跑创建新run_id。数据库伙伴可将这套表与参数化save_sql语义移植到PostgreSQL等SQL服务；SQLite原型不提供企业权限、租户隔离或服务部署。

查询缺少的ontology字段：

```sql
SELECT f.requirement_id, json_extract(q.definition_json, '$.label') AS field_name,
       f.canonical_path, f.status
FROM report_field f
JOIN report_requirement q
 ON q.contract_hash=f.contract_hash AND q.requirement_id=f.requirement_id
WHERE f.run_id=? AND f.owner IN ('Ontology','Shared') AND f.status='Missing';
```

## 只增加最少计算，不改政策

单币别对账使用Decimal和显式net_available_amount，输出净可用额－拟投入额，不把合同总额／source_amount当可用净额。币别缺失、无明确净额、跨币别、第三方或复杂资金转Missing／超原型范围，并列人工事项。这个计算是报告解释，不改现有D3判定；若和原D3结论不一致，必须阅读待办，不将原引擎结果当真实放行。

组织图复用原主体／ownership_structure；无关系不从party2列表猜股权边，无持股比例不猜比例。不新增复杂权益或UBO计算引擎。现有层数／循环结果直接复用；需要人工核对的计算、UBO门槛和判定依据由新输入接口提供。

人工覆核结论6.4.3来自外部演示记录，绝不由final_decision代替。7.4报告编号、文稿版本和AI原文仍由对应模块提供。原Safeguards、规则优先级和174个案例保持不变；新表标不实施的真实双签／签署平台未新增，原SG已有逻辑也没有删除。

## 验证及未完成事项

运行 `python -m unittest discover -s tests -v`。新增测试覆盖289项唯一映射、身份复用、SQL读回／幂等／不可覆盖、缺失资料、同币别精度、跨币别／第三方范围及筛查未知。原12项测试仍校验174例及图／批次一致性。

输出端已补齐契约和缺口状态；真实字段值需要原始输入／人工提供，不能以“都补齐”为由编造。Report排版、模型调用、真实身份核验和外部SQL配置不在本轮实现。任何APPROVE文本都只是原型规则结果，不授予开户／收款／认购／交收权限。

## 按run_id读取完整SQL结果

```python
import sqlite3
from report_contract.export import read_sql

with sqlite3.connect('work/report.sqlite3') as connection:
    report_data = read_sql(connection, run_id)
```

读取未知run_id会报错，记录不完整时拒绝拼装报告。写入前检查289项齐全、映射一致及run_id一致；同一个run_id的不同内容禁止覆盖。所有canonical输出均与原引擎结果隔离，不会因报告端修改引用而改变原评估对象。
