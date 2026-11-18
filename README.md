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

脚手架立项（2026-11-18）。M1 未开始。
