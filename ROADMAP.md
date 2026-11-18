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
