# doc-audit：路线图

状态：grill-with-docs 与 to-spec 已完成，用户批准的 7 张垂直切片票据已发布到 `.scratch/doc-audit/issues/`，均为 ready-for-agent；实施尚未开始。规格位于 `.scratch/doc-audit/spec.md`，正式 skill 名为 `doc-audit`，目标位置 `engineering/doc-audit/`。

## 已确认的方向调整

动态证据采用本次有实战意义的只读调查试跑，替代既往会话回溯扫描。主 agent 临时适配工具轨迹，固定脚本统一校验、计数、排序及报告组装；子代理回答不参与访问证据解析，也不要求特定格式。具体职责和任务来源见 [architecture.md](architecture.md)。

旧的最近 100 个历史会话、时间窗口、历史 fork/resume 及多 harness 历史解析器计划已退出当前设计。前期调查保留在 `.scratch/doc-navigation-audit/`，拒绝理由见 [dead-ends.md](dead-ends.md)。

## 已确认目标与 v0.1 范围

- 核心产物是 Documentation Maintenance Priority：为文档审查及后续重构、改进迭代排出有依据的优先级。图是解释工具。
- `doc-audit` 仅由用户主动调用，不因普通对话提及文档而自动触发。动态证据来自当前环境可观测的试跑工具轨迹；静态文档指针为另一种证据来源。
- v0.1 只做分层有向图，不做桑基图。
- 只找问题并给出有证据的修订建议，不修改、移动或删除目标仓库文件。
- 审查覆盖引用有效性、内容准确性、导航有效性及文档组织问题；纯审美润色不单独构成发现。
- 隐私与证据约束见 [architecture.md](architecture.md)。
- v0.1 动态分析只做文件访问统计，不重建读取序列；具体证据边界见 [architecture.md](architecture.md)。
- 静态模式独立可用，默认有界审查与实验缺口处理见 [features.md](features.md)。
- 分开提供待审文档队列与问题改进队列，职责见 [features.md](features.md)。

## Backlog

- 读取顺序片段重建与桑基图：展示有足够顺序证据的读取片段；不将静态引用热度冒充真实访问流量。列语义及片段口径届时再决定。
- `.md`、`.markdown` 以外的文档格式；外部 URL 联网检查不属于 v0.1。
- 跨会话续审与报告对比。
- 同任务重复试跑与稳定性分析。

## 收口状态

Q1–Q35 的产品选择及中途替换已记录，用户已批准整体规格与七票拆分。CLI/JSON、静态指标、非默认预算取整、稳定排序、证据有效性及状态转换以 `.scratch/doc-audit/spec.md` 为实现基准；票据各自声明交付边界、依赖与验收，不恢复旧历史扫描方案。

首发实证风险见 [testing.md](testing.md)。运行能力、实验代表性与性能尚未实测，不能将设计确认表述为功能验证完成。

## 实施票据与依赖

票据一票一文件，位于本地 `.scratch/doc-audit/issues/`；本表仅作导航，不代替票内验收和状态。

| 编号 | 交付 | Blocked by |
| --- | --- | --- |
| 01 | 限定范围与第一份静态报告 | 无 |
| 02 | 导航结构、分层图与静态待审队列 | 01 |
| 03 | 一次真实 Exploration Trial 的行为证据进入报告 | 01 |
| 04 | 有效实验触达与动态待审队列 | 02、03 |
| 05 | 有证据的深审与问题改进队列 | 02 |
| 06 | 中断、证据变化与可信最终报告 | 03、05 |
| 07 | 用户主动调用的完整工作流与集成验收 | 04、06 |

当前可领取票为 01。依赖允许的并行不代表共享文件可并行编辑：实施时为每个编译单元指定唯一编辑者，重叠时顺序执行。各票随功能交付针对性测试，07 核对 T01–T19 的累计覆盖并执行所需集成门禁。

## 开发文档导航

- [architecture.md](architecture.md)：已确定的职责与证据约束；实现接口确定后再记录。
- [features.md](features.md)：已确定能力和审查范围。
- [testing.md](testing.md)：研究缺口与后续验证。
- [dead-ends.md](dead-ends.md)：已排除的推断与设计方向；延期功能归 backlog。

研究依据位于本地 `.scratch/doc-navigation-audit/research-design.md`、`research-sessions.md`、`research-visualization.md`；这些是研究记录，不是已批准的规格。
