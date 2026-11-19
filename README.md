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
python src/pymc2/main.py                        # 默认：交互模式（真人可玩，MC2 资产自动发现）
python src/pymc2/main.py --demo                 # 双侧 AI 自动演示（M5 任务链 + 结算/整备摘要）
python src/pymc2/main.py --mc2-assets <本地路径>  # 显式指定资产路径（覆盖自动发现）
```

- **交互模式（默认）**：玩家手操己方机甲——左键拖拽框选 / 单击选中，右键移动（点到敌方
  机甲同样移动过去），F 键循环火力姿态（FREE/HOLD，HUD 状态行可见）；开火由**自动交战**
  接管：FREE 姿态机甲对射程内最近敌机甲自动锁定开火（origin=SELF 经 OrderDispatcher
  下发，与玩家/指挥官同源同路径；显式指定的目标优先、HOLD 火力纪律不自动开火）。
  键盘操控：方向键 / W S D 单次按键选中组沿方向移动 5 格；A 键托管开关（挂上/解除
  玩家侧 AiCommander，托管激活时 HUD 显示 `AUTO: ON`）。敌方保留 AiCommander goal
  分解（后撤/集火/占区）。托管未激活时玩家侧不挂指挥官，AI 决策只作用于敌方；
  战役链（任务脚本/结算/整备摘要）两模式共用。
- **演示模式（`--demo`）**：玩家侧也由 AiCommander 指挥，双侧 AI 自动攻防（原 main 行为），
  A 键托管无效。
- **键位表（交互模式）**：

  | 键 | 操作 |
  | --- | --- |
  | ← ↑ ↓ → | 选中组沿方向移动 5 格（四方向全支持，左移用 ←） |
  | W / S / D | 上 / 下 / 右移 5 格（A 键已划归托管，字母区无左移键） |
  | F | 循环火力姿态 FREE→HOLD（作用于全部选中单位） |
  | A | 托管开关：玩家侧 AiCommander 接管/解除（`--demo` 下无效） |
  | 左键拖拽 / 单击 | 框选 / 单选己方机甲 |
  | 右键 | 移动到目标点（tile 中心；开火交给自动交战） |

  键盘移动与右键移动同路径（MOVE_TO_POINT origin=PLAYER），与让路协商天然共存；
  托管为辅助决策语义——托管期间手动指令仍可下达（指令覆盖 AI 决策，下一决策周期
  AI 可能重新接管）。
- **MC2 资产自动发现**：默认按 `PYMC2_MC2_ASSETS` 环境变量 → `~/.pymc2/mc2-assets` 缓存目录
  顺序探测；`--mc2-assets <路径>` 显式覆盖。加载 `Data/TGL/{256,128}/*.tga` 与 `Data/Art/*.tga`
  建精灵索引（机甲 + `A_*` 环境物件），`Data/Textures/64/*.tga` 地面基础纹理与
  `Data/Textures/64Overlays/*.tga` 道路覆盖序列建地形索引；机甲命中精灵时按格子尺寸缩放
  绘制（血条/选中框/HUD 仍程序化），未发现或路径无效时自动降级程序化色块，渲染行为不变。
  **许可红线：MC2 原版资产仅限本机学习加载，不打包进仓库、不再分发、不用于商业用途；仓库内只有测试用 PIL 自造 TGA fixture。**

## 状态

- M1 垂直切片：已交付（2026-11-18）。场景渲染 / 单机移动 / 直射开火 / 命中判定，e2e 截图存 docs/assets/uat_m1/。
- M2 指令层：已交付（2026-11-18）。TacticalOrder DSL（7 类指令 + origin 三来源，可序列化）、OrderDispatcher 单一入口（环形指令日志 maxlen=2000）、框选 / 右键指令 / F 键火力姿态、AiCommander 决策入口占位；回放一致性集成测试通过，e2e 截图存 docs/assets/uat_m2/。注：F 键自 M2 起为火力姿态循环，开火统一改为右键敌方目标。
- M3 机甲实体：已交付（2026-11-18）。八部位底盘 MechChassis（BodyPart armor→structure 摧毁顺序、溢出传导链、CT/HEAD 全毁、LA/RA 武器失效、LL/RL/侧躯 mobility 惩罚、中型底盘工厂）、Weapon.mounted_location 武器槽、HeatSystem 热量系统（0-100 钳制 / 0.5s⁻¹ 散热 / ≥80 精度惩罚 ×0.7 / 100 强制 shutdown 2s）；hull 改为底盘总余量派生视图（M1/M2 兼容）；e2e「选中 → 开火 → CT 摧毁爆炸」截图存 docs/assets/uat_m3/。
- M4 AI：已交付（2026-11-18）。驾驶员技能模型 Pilot（PilotSkill gunnery 起步、SkillStats use/success 双计数、kills 计数、gunnery_rank=min(5.0, kills*0.25 + hit_rate*2.0) 纯派生）挂载 Mech.pilot（可选缺省）；技能系数接入弹道消费点 hit_probability（×(0.85+rank*0.06) 钳 [0.7,1.2]，无驾驶员中性 1.0，M1/M2 存量零改动）；开火/击杀在 BattleService 记账（record_shot/record_kill）；AiCommander 重写为 goal 分解（PRESERVE_FORCE > DESTROY_OBJECT > SECURE_REGION 优先级 + 完成条件谓词，最弱优先集火、超射程先接近、残机后撤、决策间隔 N 可注入、回退路径可注入）；main() 接线 AI 决策循环；e2e「AI 指挥 vs 玩家单机自动摧毁 + 后撤场景」截图 4 张存 docs/assets/uat_m4/。
- M5 战役：已交付（2026-11-18）。CampaignState（RP 账本/名册/任务指针）+ 四档序列化（Pilot to_dict/from_dict 含双计数与 kills——M4 遗留项补齐、chassis 部位余量、mech 整备快照含 max_hull、campaign 存档往返）；MissionChain 三关递进（敌方编制 1/2/3、出生位置互异、奖励 25/30/40）；SalvageService（摧毁机甲按剩余完好度 ×45 折算 RP）；RefitService（repair 半价满修 / hire 40 RP 购新机甲 / carryover 幸存者回写阵亡移除）；名册快照复制参战、结算回写携带；e2e「任务 1 真战斗 → 敌全灭结算 → repair+hire → 任务 2 开局逐台断言 pilot rank/装甲状态/新机甲在列」截图 3 张存 docs/assets/uat_m5/。整备走 service 调用 + main 文本摘要（无 GUI 整备屏）。
- M6 脚本化：已交付（2026-11-18）。Trigger DSL（条件 tick_reached/enemy_destroyed/unit_below_hull/unit_in_region/allies_destroyed + 动作 spawn_reinforcement/victory/defeat/grant_rp/show_message，declarative dataclass、JSON 可加载）；MissionScript + ScriptEngine（PyCC2 TriggerSystem 精神：once/cooldown 门控、fire_count 可观测），经 BattleService.tick_hook 由 battle_service.tick 驱动（默认 None 零回归）；load_mission_script 错误统一 MissionScriptError（JSON 行号/缺字段/未知 kind/缺参数）；示例脚本 data/missions/m1_river_crossing.json 全 5 动作+全 5 条件；验收「示例脚本全事件覆盖（每动作均有用例执行）+ PRESERVE_FORCE 与脚本 defeat 共存 + e2e 增援→交战→RP→victory」截图 3 张存 docs/assets/uat_m6/；main() 重写为 M5+M6 自动演示（双侧 AI + 首关脚本 + 结算/整备文本摘要）。
- 交互化修补：已交付（2026-11-18）。main() 默认改为**交互模式**（真人可玩：玩家侧不挂 AiCommander，框选/右键移动/右键开火/F 火力姿态走既有输入链路，敌方保留 AI；`--demo` 保留原双侧 AI 自动演示）；新增 `--mc2-assets` 本地 MC2 资产加载管线（infrastructure/mc2_assets.py：扫描 Data/TGL/{256,128}+Data/Art 的 TGA 按小写 stem 建索引，机甲映射 px-01→MadCat / px-02→Thor / ex→UrbanMech，匹配链 精确→包含→程序化回退，未加载/未命中渲染逐像素不变）；e2e 交互全链路「框选 → 移动到位 → 开火 → 掉血」截图 5 张存 docs/assets/uat_interactive/，资产降级路径渲染一致性集成测试通过。
- 用户反馈修补（开火自动化 + 资产免配置）：已交付（2026-11-18）。①**自动交战**（反馈：开火应自动，不要右键点敌人）——BattleService.tick 新增自动交战步骤：FREE 姿态存活机甲对射程内最近敌机甲自动锁定，构造 TacticalOrder（origin=SELF）经 dispatcher 下发（与玩家/指挥官同路径）；显式指定目标（attack_target_id 仍有效）优先、SELF 不覆盖，目标死亡/出射程清锁下 tick 重锁，HOLD 火力纪律不自动锁定，冷却/关机/武器损毁期间不下发（防指令日志刷屏）；右键语义简化为一律 MOVE_TO_POINT；②**MC2 资产默认自动发现**（反馈：去掉路径门槛）——default_pack_root() 按 env `PYMC2_MC2_ASSETS` → `~/.pymc2/mc2-assets` 探测，`--mc2-assets` 显式路径优先，加载成功打印资产摘要、失败维持降级提示；e2e 交互链路改写为「框选 → 右键敌机甲=移动 → 自动交战击毁（零玩家攻击指令）」截图 4 张存 docs/assets/uat_interactive/，新增自动交战单测/集成测试与资产探测单测（HOME/env monkeypatch 隔离，不读真实资产仓库）。
- 用户反馈修补（键盘操控 + A 键托管）：已交付（2026-11-19）。①**键盘移动**（反馈：Mac 上鼠标移动操作不便）——方向键四方向 + 字母区 W/S/D 单次按键选中组沿方向移动 KEYBOARD_MOVE_TILES=5 格（模块常量可调；A 归托管开关故字母区无左移键，左移用 ←），每机以自身 tile 为基点 + 方向×N **编队平行偏移**（队形保持、目标天然互异），堆叠同 tile 撞点时按移动方向横向逐格交替错开（防全挤同一点触发让路超时逃生阀），越界钳制；HUD 状态行追加键位提示（pygame 内置字体无 CJK 字形，提示行用 ASCII：`MOVE: ARROWS/W S D | FIRE: F | AUTO: A`，中文键位说明见上方键位表）。②**A 键托管**——Game 新增 player_commander/demo_mode 字段，handle_event KEYDOWN A 挂/解除 AiCommander(Faction.PLAYER)（run_frame 决策循环与敌方 AI 同源同路径，goal 分解：后撤>集火>占区），解除不清除已下发指令（移动/攻击执行完自然停止）；托管为辅助决策语义——托管期间手动指令仍可下达，下一决策周期 AI 可能重新接管；`--demo` 下 A 无效（双侧 AI 已就位，防双重决策）；托管激活时 HUD 显示 `AUTO: ON`。e2e「框选 → F 转 HOLD 冻结火力 → K_d 键盘编队移动到位 → A 托管 COMMANDER 源指令入账（ATTACK_OBJECT 目标 ex-01）→ F 恢复 FREE 自主交战落地 → A 解除后零新玩家侧 COMMANDER 指令」截图 3 张存 docs/assets/uat_keyboard/；新增单测 15 例（键位表/编队移动/堆叠错开/越界钳制/托管切换/demo 门禁/HUD 文本）+ e2e 2 例，存量 352 例零回归。
- MC2 画面复现 Wave V1（地形合成）：已交付（2026-11-19）。①**资产下载扩展**（本机 `~/.pymc2/mc2-assets`，零入库）：`Data/Textures/64/` 全 11 张（Cement×5 留作城镇地形 / Detail×4 草地 / Water_ocean / undefined；注意 Detail_10000 为 .TGA 大写扩展名且 Detail 系列实为 128×128）、`Data/Textures/64Overlays/2lanedirt0000-0005.TGA` 6 张道路序列、`Data/TGL/128/` 环境精灵 8 张（A_Pine&Palm / A_JungletreeA/B / A_DeadTreesL1 / A_GreenHouseDam（A_GreenHouse 官方缺卡片用受损变体）/ A_MechDam + X 摧毁变体 A_Pine&PalmX / A_JungletreeAX），逐张 PIL 验证可读（29/29 OK）。②**MC2AssetPack 扩展**：ground_tiles / overlays（按帧号排序序列）/ environment 三类索引；`resolve_ground(kind, seed)`（grass→Detail 系列按 seed 取模确定性选张、road→2lanedirt 序列、water→Water_ocean；Cement 不暴露）、`resolve_environment(kind)`（别名表 + `_x`/`_dam` 后缀按 MC2 命名直拼派生摧毁变体：tree_x→a_pine&palmx）、`scaled_ground_tile`/`scaled_environment_sprite` 缩放缓存；机甲 `resolve_mech_sprite` 零改动。**资产 alpha 勘测**：Textures/64 的 alpha 是高光/细节通道（Water 全 0、Detail≈48）非透明度，加载强制不透明；64Overlays 的 alpha 是道路形状蒙版（≤140 软边）按原生合成；TGL 环境精灵是真抠图。③**渲染器接入**：pack 含地面资产时地形改铺 MC2 纹理（草地 tile 哈希确定性选 Detail 张、道路 tile 草皮底 + 2lanedirt overlay——直道段用对应直帧（0001 水平/0000 竖直，按边缘连通性勘测）、十字交点叠两直帧保四向连通，接缝观感优先），单 tile 解析失败逐格回退哈希色块/虚线；**无 pack 或 pack 无地面资产时地形逐字节保持现状**（存量 e2e 截图语义不变）。④**环境点缀**：`plan_decorations` 确定性 seed 撒 ~15 个装饰（非道路空 tile、避开出生格，kind 循环取用）经 create_game 注入，纯装饰不阻挡移动不参与命中（对齐让路协商语义，机甲可自由穿过）；摧毁变体（Dam/X）本波只暴露接口不接渲染（V2 做）。e2e「纹理地形开局 → 装饰像素断言 → 机甲移入装饰格落位 → 自动交战照常击毁」截图 3 张存 docs/assets/uat_v1/；新增单测 25 例（ground/overlay/environment 解析、确定性选张、alpha 语义、变体派生、撒点纯函数）+ 集成 8 例（纹理像素断言、无包字节一致、装饰层级）+ e2e 1 例，存量 369 例零回归（共 403）。
