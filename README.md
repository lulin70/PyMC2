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

## 运行

```
python src/pymc2/main.py                        # 默认：交互模式（真人可玩）
python src/pymc2/main.py --demo                 # 双侧 AI 自动演示（M5 任务链 + 结算/整备摘要）
python src/pymc2/main.py --mc2-assets <本地路径>  # 交互模式 + 加载本地 MC2 Shared Source 资产
```

- **交互模式（默认）**：玩家手操己方机甲——左键拖拽框选 / 单击选中，右键地面移动、右键敌机甲开火，
  F 键循环火力姿态（FREE/HOLD，HUD 状态行可见）；指令以 TacticalOrder（origin=PLAYER）经
  OrderDispatcher 下发，与 AI 同源同路径。敌方保留 AiCommander goal 分解（后撤/集火/占区）。
  玩家侧不挂指挥官，AI 决策只作用于敌方；战役链（任务脚本/结算/整备摘要）两模式共用。
- **演示模式（`--demo`）**：玩家侧也由 AiCommander 指挥，双侧 AI 自动攻防（原 main 行为）。
- **`--mc2-assets <路径>`**：指向本机自有 MechCommander 2 Shared Source checkout（含 `Data/` 目录）。
  加载 `Data/TGL/{256,128}/*.tga` 与 `Data/Art/*.tga` 建精灵索引；机甲命中精灵时按格子尺寸缩放绘制
  （血条/选中框/HUD 仍程序化），未命中或路径无效时自动降级程序化色块，渲染行为不变。
  **许可红线：MC2 原版资产仅限本机学习加载，不打包进仓库、不再分发、不用于商业用途；仓库内只有测试用 PIL 自造 TGA fixture。**

## 状态

- M1 垂直切片：已交付（2026-11-18）。场景渲染 / 单机移动 / 直射开火 / 命中判定，e2e 截图存 docs/assets/uat_m1/。
- M2 指令层：已交付（2026-11-18）。TacticalOrder DSL（7 类指令 + origin 三来源，可序列化）、OrderDispatcher 单一入口（环形指令日志 maxlen=2000）、框选 / 右键指令 / F 键火力姿态、AiCommander 决策入口占位；回放一致性集成测试通过，e2e 截图存 docs/assets/uat_m2/。注：F 键自 M2 起为火力姿态循环，开火统一改为右键敌方目标。
- M3 机甲实体：已交付（2026-11-18）。八部位底盘 MechChassis（BodyPart armor→structure 摧毁顺序、溢出传导链、CT/HEAD 全毁、LA/RA 武器失效、LL/RL/侧躯 mobility 惩罚、中型底盘工厂）、Weapon.mounted_location 武器槽、HeatSystem 热量系统（0-100 钳制 / 0.5s⁻¹ 散热 / ≥80 精度惩罚 ×0.7 / 100 强制 shutdown 2s）；hull 改为底盘总余量派生视图（M1/M2 兼容）；e2e「选中 → 开火 → CT 摧毁爆炸」截图存 docs/assets/uat_m3/。
- M4 AI：已交付（2026-11-18）。驾驶员技能模型 Pilot（PilotSkill gunnery 起步、SkillStats use/success 双计数、kills 计数、gunnery_rank=min(5.0, kills*0.25 + hit_rate*2.0) 纯派生）挂载 Mech.pilot（可选缺省）；技能系数接入弹道消费点 hit_probability（×(0.85+rank*0.06) 钳 [0.7,1.2]，无驾驶员中性 1.0，M1/M2 存量零改动）；开火/击杀在 BattleService 记账（record_shot/record_kill）；AiCommander 重写为 goal 分解（PRESERVE_FORCE > DESTROY_OBJECT > SECURE_REGION 优先级 + 完成条件谓词，最弱优先集火、超射程先接近、残机后撤、决策间隔 N 可注入、回退路径可注入）；main() 接线 AI 决策循环；e2e「AI 指挥 vs 玩家单机自动摧毁 + 后撤场景」截图 4 张存 docs/assets/uat_m4/。
- M5 战役：已交付（2026-11-18）。CampaignState（RP 账本/名册/任务指针）+ 四档序列化（Pilot to_dict/from_dict 含双计数与 kills——M4 遗留项补齐、chassis 部位余量、mech 整备快照含 max_hull、campaign 存档往返）；MissionChain 三关递进（敌方编制 1/2/3、出生位置互异、奖励 25/30/40）；SalvageService（摧毁机甲按剩余完好度 ×45 折算 RP）；RefitService（repair 半价满修 / hire 40 RP 购新机甲 / carryover 幸存者回写阵亡移除）；名册快照复制参战、结算回写携带；e2e「任务 1 真战斗 → 敌全灭结算 → repair+hire → 任务 2 开局逐台断言 pilot rank/装甲状态/新机甲在列」截图 3 张存 docs/assets/uat_m5/。整备走 service 调用 + main 文本摘要（无 GUI 整备屏）。
- M6 脚本化：已交付（2026-11-18）。Trigger DSL（条件 tick_reached/enemy_destroyed/unit_below_hull/unit_in_region/allies_destroyed + 动作 spawn_reinforcement/victory/defeat/grant_rp/show_message，declarative dataclass、JSON 可加载）；MissionScript + ScriptEngine（PyCC2 TriggerSystem 精神：once/cooldown 门控、fire_count 可观测），经 BattleService.tick_hook 由 battle_service.tick 驱动（默认 None 零回归）；load_mission_script 错误统一 MissionScriptError（JSON 行号/缺字段/未知 kind/缺参数）；示例脚本 data/missions/m1_river_crossing.json 全 5 动作+全 5 条件；验收「示例脚本全事件覆盖（每动作均有用例执行）+ PRESERVE_FORCE 与脚本 defeat 共存 + e2e 增援→交战→RP→victory」截图 3 张存 docs/assets/uat_m6/；main() 重写为 M5+M6 自动演示（双侧 AI + 首关脚本 + 结算/整备文本摘要）。
- 交互化修补：已交付（2026-11-18）。main() 默认改为**交互模式**（真人可玩：玩家侧不挂 AiCommander，框选/右键移动/右键开火/F 火力姿态走既有输入链路，敌方保留 AI；`--demo` 保留原双侧 AI 自动演示）；新增 `--mc2-assets` 本地 MC2 资产加载管线（infrastructure/mc2_assets.py：扫描 Data/TGL/{256,128}+Data/Art 的 TGA 按小写 stem 建索引，机甲映射 px-01→MadCat / px-02→Thor / ex→UrbanMech，匹配链 精确→包含→程序化回退，未加载/未命中渲染逐像素不变）；e2e 交互全链路「框选 → 移动到位 → 开火 → 掉血」截图 5 张存 docs/assets/uat_interactive/，资产降级路径渲染一致性集成测试通过。
