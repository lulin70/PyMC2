# PyMC2 路线图

- 立项日期：2026-11-18
- 依据：[MC2_RESEARCH_20261118.md](../PyCC2/docs/MC2_RESEARCH_20261118.md) §4.4
- 原则：每个里程碑带可执行验收门禁，不带时间承诺；门禁不过不进下一里程碑。

## M1 垂直切片

- 范围：单任务场景渲染 + 单台机甲移动 + 直射武器开火 + 命中判定
- 验收：e2e 脚本「选中 → 移动 → 开火 → 掉血」全链路截图留痕（沿用 PyCC2 uat 脚本模式）
- 门禁：全量回归 0 failed + ruff/mypy 全绿

## M2 指令层

- 范围：TacticalOrder DSL（类型化数据对象 + origin 三来源，MC2 tacordr.h 同款设计）+ 框选/右键指令/火力姿态
- 验收：同一指令序列重放两次结果一致；真实窗口 UAT 点击链路 PASS
- 门禁：同 M1

## M3 机甲实体

- 范围：部位装甲/内部结构/武器槽/热量累积与散热
- 验收：单测覆盖部位摧毁顺序与 WeaponBolt 判定路径
- 门禁：同 M1

## M4 AI

- 范围：驾驶员技能模型（use/success 双计数成长）+ 指挥官 goal 分解（GOAL_OBJECT/GOAL_REGION）
- 验收：AI 对战压力场景无死锁；技能 rank 单调性单测
- 门禁：同 M1

## M5 战役

- 范围：任务链 + 整备闭环（salvage/refit/hire，对应 MC2 logistics 层）
- 验收：e2e「整备变更带入下一任务」
- 门禁：同 M1

## M6 脚本化

- 范围：Trigger 条件-动作 DSL + ABL 式任务脚本（参考 MC2 ABLT/ 结构，自建语法）
- 验收：示例任务脚本全事件触发覆盖
- 门禁：同 M1
