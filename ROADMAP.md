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
- 交付（2026-11-18）：7 类指令 DSL（含 to_dict/from_dict 序列化）+ OrderDispatcher 单一入口（_order_log 环形队列 maxlen=2000）+ AiCommander 占位（origin=COMMANDER）；集成测试验证「同一指令序列两次重放 → 位置/伤害/日志完全一致」且 AI 与玩家指令同源；e2e「框选两台 → 右键移动 → F 切火力姿态 → 开火 → 掉血」截图 5 张存 docs/assets/uat_m2/。注：F 键自 M2 起为火力姿态循环，开火改为右键敌方目标，M1 e2e 开火入口随此演进。

## M3 机甲实体

- 范围：部位装甲/内部结构/武器槽/热量累积与散热
- 验收：单测覆盖部位摧毁顺序与 WeaponBolt 判定路径
- 门禁：同 M1
- 交付（2026-11-18）：MechChassis 八部位（BodyPart 先 armor 后 structure、溢出沿传导链、CT/HEAD 全毁 → alive 派生、LA/RA 摧毁武器失效、LL/RL/侧躯 mobility 惩罚折算移速）+ Weapon.mounted_location 武器槽 + HeatSystem（0-100 钳制、0.5/s 散热、环境加成接口预留、≥80 命中率 ×0.7、100 强制 shutdown 2s 只散热）；fire_at 结算改走部位命中（权重 CT 0.25 / 侧躯各 0.15 / 臂各 0.10 / 腿各 0.09 / HEAD 0.07），Projectile 携带 location 落地按部位结算；hull 改为底盘总余量派生视图保持 M1/M2 存量 80 例零回归；e2e「选中 → 开火 → CT 摧毁爆炸残骸」截图 3 张存 docs/assets/uat_m3/。

## M4 AI

- 范围：驾驶员技能模型（use/success 双计数成长）+ 指挥官 goal 分解（GOAL_OBJECT/GOAL_REGION）
- 验收：AI 对战压力场景无死锁；技能 rank 单调性单测
- 门禁：同 M1
- 交付（2026-11-18）：Pilot 技能模型（PilotSkill/SkillStats 双计数参照 PyCC2 Wave 2，gunnery_rank 派生封顶 5.0）+ 弹道消费点（hit_probability × (0.85+rank*0.06) 钳 [0.7,1.2]，无驾驶员中性 1.0 保存量 148 例零回归）+ AiCommander goal 分解（PRESERVE_FORCE（hull<30% 后撤）/ DESTROY_OBJECT（最弱优先、超射程先接近 standoff）/ SECURE_REGION（敌全灭转中央区域）带完成条件谓词，决策间隔 N 与回退工厂可注入，update 每帧驱动）+ BattleService 开火/击杀记账；单测+集成覆盖 rank 单调成长、goal 切换、shutdown/部位摧毁健壮性、N=1 与 N=30 可重放；e2e「AI 指挥 vs 玩家单机自动摧毁 + 后撤触发」截图 4 张存 docs/assets/uat_m4/。

## M5 战役

- 范围：任务链 + 整备闭环（salvage/refit/hire，对应 MC2 logistics 层）
- 验收：e2e「整备变更带入下一任务」
- 门禁：同 M1
- 交付（2026-11-18）：services 层四件套——CampaignState（campaign_id/mission_index/RP/roster）+ 四档序列化（Pilot to_dict/from_dict 含 kills 与 use/success 双计数补齐 M4 遗留、chassis 各部位 armor/structure 余量、mech 整备快照含 max_hull 保 hull_ratio 分母口径、campaign 存档往返）；MissionChain 三关编制/位置/奖励递进（1/2/3 敌）+ is_victory/is_defeat 谓词 + complete_mission（胜利=奖励+salvage+指针推进，战败=仅 salvage 重试，同归于尽按目标达成计胜利）；SalvageService（摧毁机甲按剩余完好度比例 ×45 RP 折算，打空残骸 0）；RefitService（repair 缺口×0.5 RP/点满修、hire 固定 40 RP 购新机甲+rookie、carryover_survivors 存活回写/阵亡移除）；名册快照复制参战保证战斗损伤不直接污染名册（结算才回写）；e2e「任务 1 真战斗（seed 固定）→ 敌全灭结算 → repair px-02 + hire px-03 → 任务 2 开局逐台断言 rank/装甲/新机甲携带」截图 3 张存 docs/assets/uat_m5/。注：整备为 service 调用 + main 文本摘要，无 GUI 整备屏（本里程碑范围外）。

## M6 脚本化

- 范围：Trigger 条件-动作 DSL + ABL 式任务脚本（参考 MC2 ABLT/ 结构，自建语法）
- 验收：示例任务脚本全事件触发覆盖
- 门禁：同 M1
- 交付（2026-11-18）：domain/scripting/ 两件套——Trigger DSL（declarative frozen dataclass：条件五种 tick_reached/enemy_destroyed/unit_below_hull/unit_in_region/allies_destroyed + 动作五种 spawn_reinforcement/victory/defeat/grant_rp/show_message，JSON 可加载）+ MissionScript/ScriptEngine（照 PyCC2 TriggerSystem 精神：登记序评估 + once/cooldown 门控 + fire_count 可观测；经 BattleService.tick_hook 由 battle_service.tick 驱动，钩子默认 None 保存量零回归）；load_mission_script 统一包装错误（JSON 语法含行号/缺字段/未知 kind/缺必需参数均 MissionScriptError）；示例脚本 data/missions/m1_river_crossing.json 全 5 动作 + 全 5 条件覆盖；unit/integration/e2e 三层验收——示例脚本全事件覆盖（每动作至少一个用例执行 + 真战斗循环开场/渡口/增援/首杀/胜利全触发）、PRESERVE_FORCE 与脚本 defeat 共存不冲突、M6 e2e「增援空投 → dispatcher 交战 → RP 入账 → victory 结算」截图 3 张存 docs/assets/uat_m6/；main() 重写为 M5+M6 演示（双侧 AI 自动攻防 + 首关挂脚本 + 结算/整备文本摘要，QUIT 即退、同任务 3 连败熔断防演示死循环）。

## 交付后注记（2026-11-18）：默认入口交互化 + 本地 MC2 资产管线

- **main() 默认改为交互模式**（用户验收反馈：双侧 AI 演示"选择后不能移动不能开火"，不算真实可玩）：
  玩家侧机甲不挂 AiCommander，框选/右键移动/右键开火/F 火力姿态复用 M2 既有输入链路
  （TacticalOrder origin=PLAYER 经 OrderDispatcher 下发，与 AI 同源同路径）；敌方保留 AI。
  `--demo` 保留原双侧 AI 自动演示；战役链/脚本/结算/整备两模式共用。
  验收：e2e 交互全链路「框选 → 移动到位 → 右键开火 → 掉血」+ AI 边界断言（玩家机甲只收
  PLAYER 源指令、敌方指挥官持续产出 COMMANDER 源指令），截图 5 张存 docs/assets/uat_interactive/。
- **本地 MC2 资产加载管线**（infrastructure/mc2_assets.py + `--mc2-assets <路径>`）：
  扫描 Shared Source checkout 的 Data/TGL/{256,128}/*.tga（256 优先）与 Data/Art/*.tga，
  PIL 读取 → convert_alpha，按小写文件名 stem 建索引；机甲映射表 px-01→MadCat / px-02→Thor /
  ex→UrbanMech（px 前缀回退，可调）；匹配链 精确 stem → 包含关系（"awesome" 命中 awesome1、
  "madcat" 命中 mcl_mc_madcat_a，大小写不敏感）→ None；渲染层注入可选 asset_pack，命中精灵按
  格子尺寸缩放绘制（血条/选中框/HUD 仍程序化），root 无效/无 Data/未命中一律优雅降级，
  未加载时渲染输出与现状逐像素一致（集成测试全图字节比对）。许可红线：真实资产仅本机学习
  加载，零入库、不再分发；测试 fixture 为 PIL 现场生成 TGA。

## 交付后注记（2026-11-18）：开火自动化 + MC2 资产免配置（用户反馈修补）

- **自动交战**（用户反馈：开火应自动，不要右键点敌人）：
  BattleService.tick 内新增自动交战步骤（冷却推进后、弹丸推进前）：对每个存活、
  FireMode.FREE 的机甲——attack_target_id 仍有效（存活且在射程内，口径同 fire_at）则保持；
  否则清锁并重锁射程内最近敌机甲，构造 TacticalOrder（order_type=ATTACK_OBJECT、
  origin=SELF）经 dispatcher 下发，与玩家/指挥官指令同源同路径。优先级语义：
  **显式指定目标（玩家/指挥官）优先，SELF 只在无有效目标时接管、不覆盖**；
  **HOLD 火力纪律不自动锁定**；目标死亡/出射程 → 清除锁定，下个评估周期自动重锁。
  仅在武器就绪/可用且非强制关机时下发，防指令日志刷屏；未注入 dispatcher 的直构战局
  行为与存量完全一致（dispatcher 由 main.create_game 接线，交互与 demo 模式均生效）。
  右键语义简化：一律 MOVE_TO_POINT（点到敌方机甲也移动过去，攻击交给自动交战）。
  验收：单测 7 例（FREE 锁最近/超射程不锁/HOLD 不锁/显式目标优先/死亡重锁/出射程清锁/
  未接线零行为）+ 集成 1 例（敌入射程 → 自动开火 → 掉血，日志全 SELF 源）+ e2e 交互链路
  改写（右键敌机甲=移动、自动交战击毁断言），截图 4 张存 docs/assets/uat_interactive/。
- **MC2 资产默认自动发现**（用户反馈：路径门槛是自己设的，去掉）：
  mc2_assets 新增 default_pack_root()：env `PYMC2_MC2_ASSETS` → `~/.pymc2/mc2-assets`
  （存在才用）→ None，其余不做；main() 交互与 demo 模式均自动尝试，`--mc2-assets`
  显式路径优先；加载成功打印 `[PyMC2] MC2 资产已加载：<root>（<N> 张，机甲精灵已启用）`，
  失败维持降级提示、无候选静默。缓存目录若为 git 仓库（含 .git）无碍——load 仅扫
  Data/TGL、Data/Art 三个子目录。验收：default_pack_root 三级探测 + main 接线
  （显式优先/自动发现/降级/静默）共 9 例，HOME/env 全程 monkeypatch，不读真实资产仓库。

## 交付后注记（2026-11-19）：单位占位与让路协商（用户反馈：敌我机甲画面重叠互穿）

- **让路协商**（用户反馈：两台机甲可站同一格互相穿过；参照 PyCC2 Wave 3 MC2-05，直线插值版）：
  BattleService.tick 每帧先构建占位快照（`_occupancy_snapshot`：static_tiles=无移动意图
  存活机甲 tile；movers=移动机甲的当前/本帧将进入/目标 tile，O(n) 重建无全局占位表），
  逐机经 `_advance_with_yield` 推进：本帧将进入新 tile 且被静态存活机甲占据、或与另一
  移动机甲互换对冲（互以对方当前 tile 为目标 tile）→ 本帧停走（yield：位置与目标保持、
  武器冷却/散热照常，连续被阻帧数 +1）；tile 内移动不阻挡（只在 tile 进入瞬间硬阻挡，
  防「已在遮挡 tile 内卡死」）；两个移动机甲非互换不互阻（穿越语义）。连续让路超过
  `YIELD_TIMEOUT_TICKS=90` 强制通行（防死锁逃生阀，通行至进入新 tile 才清计数）。
  yield 状态存 service 内部 `_yield_ticks` dict（mech.id → 连续被阻帧数），实体零新字段；
  计数清零时机：被阻帧以外进入新 tile / 到达目标 / 停止指令或无意图 / 阵亡。
  渲染层 tie-break：机甲按 y 升序绘制（y 大者后画），消除同格叠压随机感。
  验收：unit 15 例（快照分流/静态阻挡冻结+计数/仅 tile 进入瞬间/互换双方 yield/
  非互换不阻/对穿到达/超时边界 90 仍阻+>90 通行/计数清零四时机/阵亡不阻/y 排序）
  + integration 3 例（对冲互换 ~100 帧双双到达不死锁/静态挡路边界外等待→超时穿过到达/
  自动交战与让路共存——让路期间持续开火掉血）；存量适配 2 处：test_m2_slice 与
  test_interactive_slice 组移动循环 80→200（双机同目标 tile，后到者经超时阀通过）。
  门禁：全量 352 passed / 0 failed + ruff check/format + mypy（27 文件）全绿。

## 交付后注记（2026-11-19）：窗口内结算呈现 + 名册全灭终局（用户反馈：战斗结束后不会结束和结算）

- **名册全灭终局**（软锁修复）：旧逻辑全灭战败后重试当前关，以 0 台己方机甲开局——
  `is_defeat` 要求「至少曾有己方机甲」永不触发，只能靠连续战败 3 次兜底。新增
  `_defeat_is_terminal`：战败且名册空 → 直接「战役失败：名册全灭，战役终局」，不再重试。
- **窗口内结算画面**：旧结算只打终端文本，窗口无任何提示（用户视觉上「打完继续打」）。
  新增 Renderer.draw_settlement_overlay（半透明黑遮罩 + 居中标题/摘要，字体缺失静默
  降级为纯遮罩）+ main._show_settlement（重绘战场帧 + 遮罩，回车/空格/点击继续，
  SETTLEMENT_CONFIRM_TIMEOUT_S=6 超时自动继续，QUIT 与战斗循环同一退出语义）。
  单任务结算（MISSION ACCOMPLISHED/FAILED + RP 构成 + 损失）与战役完成
  （CAMPAIGN COMPLETE）均走画面。文案 ASCII 对齐 HUD 提示行惯例。
  验收：unit 9 例（终局判定 3/文案 2/遮罩像素 1/等待循环事件驱动 3）；
  门禁全量 412 passed + ruff check/format + mypy（27 文件）全绿。

## 交付后注记（2026-11-19）：Wave V2 机甲/物件状态（.fit 解析 + 受损变体渲染）

- **设计文档**：[V2_DESIGN_20261119.md](docs/V2_DESIGN_20261119.md)（远端探测/解析器
  设计/阈值决策/偏离与裁剪/实现记录全留痕）。
- **远端探测结论**（videogamepreservation/mechcommander2 @ master，Trees API 全量
  17753 项）：官方 Data/TGL/{128,256} **无机甲受损静态 TGA**（256 目录远端不存在；
  128 仅 4 张 RGB 卡），受损外观走 .ase 动画（FallBackwardDam 等 3DSMAX 场景）——
  静态 blit 管线不解析；Data/Objects 四机甲 .fit 为 FITini 文本（ASCII+CRLF），
  `AppearanceName` 即精灵引用字段。
- **.fit 最小解析器**（infrastructure/fit_parser.py）：只解析渲染相关六字段
  （ObjectTypeNum/Name/AppearanceName 小写化/ExtentRadius/DestroyedObject/
  ExplosionObject），[MechProfile] 战斗侧数值裁剪不解析；对真实四文件人工验证
  输出正确；与 pack 零耦合（id 体系不同名，V3 候选做映射校准源）。
- **受损变体渲染**：MC2AssetPack.resolve_mech_sprite/scaled_mech_sprite 增加
  `damaged` 参数（dam/x 变体词派生 → 未命中回退基名，缓存 (kind,damaged[,size])
  分键）；Renderer 新增模块常量 `MECH_DAMAGED_HULL_RATIO = 0.5`，hull_ratio ≤ 阈值
  且存活切受损精灵，无资产包/无变体路径输出与现状逐像素一致；环境装饰不接受损态
  （纯装饰无 HP 状态源，偏离记录于设计文档 §5）。
- **下载范围**（仅本机缓存 ~/.pymc2/mc2-assets/，零入库）：Data/Objects/ 四机甲
  .fit（urbanmech/thor/BMadCat/BJagermech）；TGA 零新增。
- 验收：单测 18 例（fit_parser 11 + 受损变体索引 7）+ 集成 5 例（阈值 0.5 边界/
  回退/无包程序化）+ e2e 1 条（真实战局满血基名 → 压血切变体 → 击毁残骸，截图
  3 张存 docs/assets/uat_v2/）。
  门禁：全量 436 passed / 0 failed + ruff format/check + mypy（28 文件）全绿。
## 后续优化待办（2026-11-20）：个体自动战术层（用户提出，暂缓待议）

用户提出三类自动战术走位，经确认暂缓，作为将来「整体战术优化」的一部分继续讨论：

1. **残血后撤**：hull ≤ 50% 的 FREE 机甲自动产生「远离最近敌 N 格」撤退意图
   （origin=SELF 与自动交战同管线；HOLD 纪律不受影响；撤退中照常还击）；
2. **厚甲前置**：编队移动目标按装甲值排序分配，厚甲列居前、薄甲列居后；
3. **射程停走**：向敌移动进入自身射程即停（不贴脸），交火交给自动开火。

MC2 对齐说明：MC2 原版无真闪避（弹道即时命中），战术靠站位与火力纪律（.abl 脚本 +
姿态系统）；上述三项均为超 MC2 的 RTS 常见行为，实现前需讨论默认开关（auto 默认 or
按键切换）与 HOLD/托管语义交互。

## 交付后注记（2026-11-20）：Wave V3 UI 面板（buttonlayout 布局 + MCUI 图集 + 动态文本槽位对齐）

- **设计文档**：[V3_DESIGN_20261120.md](docs/V3_DESIGN_20261120.md)（远端探测/布局分区
  分析/槽位对齐设计/偏离裁剪/实现记录全留痕）。
- **远端探测结论**：buttonlayout 系列是 **.fit 布局定义文件而非整版贴图**（简报预期
  偏差，5 个分辨率各一份 FITini 定位表）；面板画面拆在 MCUI_High* 图集（UV 取块）。
  `buttonlayout1280.fit` 3412 行：[Statics] 23 部件（画布 1024×768 + HiresOffsets
  居中/底锚）+ [Button*]/[MechIcon*]/[ResourcePoints]/[HelpText] 等。
- **下载范围**（仅本机缓存 ~/.pymc2/mc2-assets/Data/Art/，零入库）：buttonlayout1280.fit
  + MCUI_High1/High3/High9/High11 + A_Compass_Small，共 6 文件（PIL 验证尺寸/alpha）。
- **UI 面板渲染**：mc2_assets 新增 `parse_fit_sections`（最小 FITini 段解析）+
  `MC2AssetPack.button_layout`（lazy 缓存，[Statics]+[ResourcePoints]+[HelpText] →
  `ButtonLayout`：部件 UV 裁切 + 画布 bbox + 文本槽位锚点；零尺寸/缺图集/越界逐件
  跳过，fit 缺失 → None）；Renderer 按布局 bbox 预合成单张面板（每帧 1 blit），
  `panel_base_offset` 对齐原版 HiresOffsets 语义（1280×720 → (128,−48) 底边锚定）。
- **动态文本槽位**（程序化 _font，ASCII）：RP 槽（官方 [ResourcePoints] 锚点）、
  任务/托管条（compalette top：`M:<id> | AUTO: ON | +N`）、dropteam 选中机甲前 4 槽
  （id/FIRE 姿态/HULL+HEAT，官方 Static10 锚点 stride=160）、键位提示（[HelpText]
  底对齐——面板模式下提示行收进面板）；`render()/create_game/Game` 新增可选
  mission_id/requisition_points（默认 None 存量调用零改动）。
- **回退**：无资产包/包无 fit/零部件零解析 → 完全走现有 _draw_hud（e2e 全图字节
  比对与无包渲染一致）。
- **裁剪**（详见设计文档 §5）：命令按钮/机甲图标/托盘/光标图集不下载不绘制（无
  点击交互语义，留后续）；移动/攻击标记图标替换不做（Cursors 系列是屏幕空间鼠标
  光标，非世界空间标记）；选中 >4 台只显示 4 dropteam 槽 + 计数并入任务条。
- 验收：单测 13 例（fit 解析/布局构建/pack lazy/偏移数学/面板像素与回退字节一致）
  + e2e 1 文件 2 用例（真实战局 4 步槽位切片 + 回退字节比对），截图 5 张存
  docs/assets/uat_v3/。
  门禁：全量 454 passed / 0 failed + ruff format/check + mypy（28 文件）全绿。

## 交付后注记（2026-11-21）：P1-A 高程切片（数据模型 + terrace 渲染 + 移动代价）

- **设计文档**：[P1A_DESIGN_20261121.md](docs/P1A_DESIGN_20261121.md)（数据模型/生成
  策略/代价与爬升语义/渲染分层/偏离裁剪/实现记录全留痕）。P1-B 视线遮挡不在本切片。
- **ElevationGrid**（domain/elevation.py，新模块）：per-tile int 高程 0..ELEVATION_MAX=3
  （钳制存取、越界视为 0、O(1) is_flat 快路径）；三种构造——全零平面（默认）、显式
  40×40 行数组、确定性丘陵 `generate_hills`（md5 哈希 value noise 双线性插值，同 seed
  同网格；道路行/列与 10 个出生点 Chebyshev ±2 保护区归零，默认战局走廊全零可用，
  实测四档高程均有分布）；`climb_allowed`：目标高于出发 >1 级不可爬（下坡不限）。
- **移动代价**：service 恒持网格（缺省 flat，移动数学存量零差异）；上坡耗时每 +1 级
  ×1.5 复利（以步长速度倒数 1/1.5^Δ 实现，下坡 ×1.0 不加速），跨入 tile 帧判定、
  `_frame_next_tile` 同步加成防让路相位漂移；爬升限制（无寻路简化）：MOVE_TO 目标
  tile 高于当前 >1 级直接拒绝——回执沿用 dispatcher 失败惯例（指令入 order_log、
  dispatch 返回 False）；让路占位/互换判定与高程解耦（语义不变）。
- **terrace 渲染**：`--terrain {flat,hills}`（默认 flat）+ 任务脚本可选 `elevation` 键
  （"hills" 关键字或显式行数组；缺失/非法 → 不覆盖，CLI hills 不被清零）；注入丘陵时
  地形改铺 terrace 版面（顶部加高 30px 防裁切，行序顶面上移 e×10px 自然遮挡 + 南侧
  深色崖壁与南邻顶面精确衔接，北/东/西高差为零宽接缝不画侧面）；机甲/装饰/弹丸/
  移动标记/攻击高亮按所在 tile 高程同步上移，机甲 y 排序键改 (elevation, y)；V3 面板
  blit 序不变；HUD 状态行选中机甲追加 ` | ELEV: n`（未选中不追加）。
- **行为保持实证**：flat 下渲染/移动路径走原分支——stash 对照实验显示 v1/v3 存量 e2e
  截图与改动前代码逐字节一致（净变化 None）；存量 UAT 截图的 git diff 为 4dd18c9 改
  键位提示行以来未重存的历史脏状态，本次全量运行一并刷新。
- 验收：单测 51 例（数据模型 21 + 移动代价与爬升 13 + terrace 数学/HUD/CLI 17）+
  e2e 2 条（hills 真实战局：顶面上移进北邻区/崖壁色像素断言、爬 1 级落位且实体同步
  上移、爬升 >1 被拒且指令入日志；flat 回归：无高程注入 vs flat 网格全帧字节一致 +
  移动公式逐帧 4.8px 不变 + 道路像素），截图 4 张存 docs/assets/uat_p1a/。
  门禁：全量 511 passed / 0 failed + ruff format/check + mypy（29 文件）全绿。
