# PyMC2 — Python MechCommander 2 战术复刻

Python + pygame-ce 复刻 MechCommander 2（FASA Studio, 2001）的实时战术玩法。本机单机运行，不含联机。

## 定位与许可声明（重要）

- **学习项目，非商业用途**。MechCommander 2 官方源码来自微软 2006 Shared Source 发布（[源码镜像](https://github.com/videogamepreservation/mechcommander2)），该许可仅限非商业参考。
- **MC2 原版美术/音频资产仅限本地学习使用**：可在本机加载官方 Shared Source 发布中的资产用于开发验证，不打包进仓库、不再分发、不用于任何商业用途。仓库内只保留程序化生成或自建占位资产。
- 机制参考与里程碑依据：姊妹项目 PyCC2 的研究文档 `../PyCC2/docs/MC2_RESEARCH_20261118.md` §4。

## 架构

沿用 PyCC2 验证过的四层结构 + 门禁体系：

```
src/pymc2/
  domain/           # 实体、值对象、系统（mech / warrior / ballistic / goal）
  services/         # 战术指令层（TacticalOrder DSL）、AI、触发器、战役整备
  presentation/     # pygame-ce 渲染与输入
  infrastructure/   # 窗口、事件总线、资源加载
```

工程门禁：ruff + mypy + radon + unit/integration/e2e + FPS 基线 + 远端 CI 强制前置（推送 ≠ 完成）。

## 里程碑（详见 ROADMAP.md，每项带验收门禁，不带时间承诺）

| 里程碑 | 内容 |
|--------|------|
| M1 垂直切片 | 场景渲染 + 单机移动 + 直射开火 |
| M2 指令层 | TacticalOrder DSL + 框选/右键指令/火力姿态 |
| M3 机甲实体 | 部位装甲/内部结构/武器槽/热量 |
| M4 AI | 驾驶员技能模型 + 指挥官 goal 分解 |
| M5 战役 | 任务链 + 整备闭环（salvage/refit/hire） |
| M6 脚本化 | Trigger DSL + ABL 式任务脚本 |

## 状态

- M1 垂直切片：已交付（2026-11-18）。场景渲染 / 单机移动 / 直射开火 / 命中判定，e2e 截图存 docs/assets/uat_m1/。
- M2 指令层：已交付（2026-11-18）。TacticalOrder DSL（7 类指令 + origin 三来源，可序列化）、OrderDispatcher 单一入口（环形指令日志 maxlen=2000）、框选 / 右键指令 / F 键火力姿态、AiCommander 决策入口占位；回放一致性集成测试通过，e2e 截图存 docs/assets/uat_m2/。注：F 键自 M2 起为火力姿态循环，开火统一改为右键敌方目标。
- M3 机甲实体：已交付（2026-11-18）。八部位底盘 MechChassis（BodyPart armor→structure 摧毁顺序、溢出传导链、CT/HEAD 全毁、LA/RA 武器失效、LL/RL/侧躯 mobility 惩罚、中型底盘工厂）、Weapon.mounted_location 武器槽、HeatSystem 热量系统（0-100 钳制 / 0.5s⁻¹ 散热 / ≥80 精度惩罚 ×0.7 / 100 强制 shutdown 2s）；hull 改为底盘总余量派生视图（M1/M2 兼容）；e2e「选中 → 开火 → CT 摧毁爆炸」截图存 docs/assets/uat_m3/。
- M4 AI：已交付（2026-11-18）。驾驶员技能模型 Pilot（PilotSkill gunnery 起步、SkillStats use/success 双计数、kills 计数、gunnery_rank=min(5.0, kills*0.25 + hit_rate*2.0) 纯派生）挂载 Mech.pilot（可选缺省）；技能系数接入弹道消费点 hit_probability（×(0.85+rank*0.06) 钳 [0.7,1.2]，无驾驶员中性 1.0，M1/M2 存量零改动）；开火/击杀在 BattleService 记账（record_shot/record_kill）；AiCommander 重写为 goal 分解（PRESERVE_FORCE > DESTROY_OBJECT > SECURE_REGION 优先级 + 完成条件谓词，最弱优先集火、超射程先接近、残机后撤、决策间隔 N 可注入、回退路径可注入）；main() 接线 AI 决策循环；e2e「AI 指挥 vs 玩家单机自动摧毁 + 后撤场景」截图 4 张存 docs/assets/uat_m4/。
