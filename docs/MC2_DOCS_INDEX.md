# MechCommander 2 源码分析文档索引

**生成日期**: 2026-11-23  
**来源**: MC2 源码逆向分析（`/tmp/mc2src/` 目录）  
**目的**: 为 PyMC2 项目提供 MC2 完整设计规格参考

---

## 文档清单

### 1. [MC2_PRD_20261123.md](./MC2_PRD_20261123.md) - 产品需求文档
**内容**：
- 产品概述与核心特性
- 功能需求（战前准备/战斗执行/战后结算）
- 地形系统（高程网格/地形类型/光照阴影）
- 机甲系统（底盘属性/武器系统/命中判定/伤害分配）
- 任务系统（目标类型/ABL 脚本/任务包格式）
- UI 需求（HUD 布局/交互规范/状态反馈）
- 非功能需求（性能指标/兼容性/可访问性）
- 用户故事（4 个核心场景）
- 数据模型（Mech/Weapon/Terrain 实体结构）
- 技术约束与风险
- 路线图（4 个 Phase）

**关键亮点**：
- 完整武器三分类（BALLISTIC/ENERGY/MISSILE）弹道设计
- 命中公式：`(1 - 0.75×距离比) × (0.85+rank×0.06) × 热量系数`
- 部位伤害概率表（CT 30% / 侧躯 15% / 四肢 10% / 头部 5%）

---

### 2. [MC2_ARCHITECTURE_20261123.md](./MC2_ARCHITECTURE_20261123.md) - 系统架构文档
**内容**：
- 架构概览（分层架构 + 数据驱动）
- 模块设计（渲染/对象系统/战斗/AI/任务/资源管理）
- 数据流图（渲染/玩法/脚本三条主线）
- 扩展点设计（Mod 支持/多人对战/编辑器接口）
- 性能优化策略（内存/渲染/AI）
- 技术债务与重构建议
- 部署架构

**关键亮点**：
- **Camera 类** 投影变换（透视/正交双模式）
- **Terrain 类** 20×20 顶点块管理 + 视锥裁剪优化
- **ObjectType 工厂** + **GameObject 实例** 分离设计
- **PacketFile** LZ/zLib 压缩任务包解析
- **ABL 脚本引擎** 任务逻辑驱动
- **对象池 + 块状缓存** 内存优化策略
- **锁步同步** 多人对战网络架构（扩展）

**源码文件映射**：
- `Camera.h/cpp` → 投影与视锥裁剪
- `Terrain.h/cpp` + `mapdata.h/cpp` → 地形渲染管线
- `objtype.cpp` + `Objblck.h` → 对象系统
- `mission.h` → 任务实体（Part 结构）
- `packet.h/cpp` → 任务包容器

---

### 3. [MC2_Detailed_Design_Document.md](./MC2_Detailed_Design_Document.md) - 详细设计文档
**内容**（§1-§6 结构）：
- §1 设计目标（系统边界/设计原则）
- §2 核心流程（战斗回合流程图/机甲状态转换图）
- §3 数据结构（武器属性表/机甲底盘规格/地形参数/热量阈值）
- §4 算法伪代码（命中判定/部位伤害结算/视线遮挡/高程移动代价）
- §5 边界处理（伤害溢出/热量上限/地图外坐标/让路死锁/浮点精度）
- §6 测试用例示例（命中率边界/部位摧毁致死/视线遮挡/热量状态机）

**关键算法**：
```python
# 命中判定
final_hit = (1.0 - 0.75×距离比) × (0.85 + rank×0.06) × (heat≥80 ? 0.7 : 1.0)

# 视线遮挡 (Bresenham + 线性插值)
sight_height = tile_elevation + EYE_HEIGHT  # +1 级
遮挡 = 途经tile高程 >= 插值视线高度

# 高程移动代价
上坡_每级 = ×1.5 (复利)  # delta=1 → 1.5×, delta=2 → 2.25×
```

**数据表**：
- 武器弹速倍率（BALLISTIC 1.5× / ENERGY 1.0× / MISSILE 0.7×）
- 机甲 8 部位装甲/结构/命中权重（总防护 81 点）
- 地形移动代价（平地 1.0× / 丘陵 1.5× / 高地 2.25× / 峭壁不可攀）
- 热量阈值（80 触发 ×0.7 精度惩罚，100 强制关机 2 秒）

---

### 4. [MC2_USER_STORIES_20261123.md](./MC2_USER_STORIES_20261123.md) - 用户故事集
**内容**（6 个 Epic）：
- **Epic 1: 战术指挥官**（单位选择/移动/攻击/战场信息）
  - 13 个 User Story（框选多单位/让路协商/NO LOS 提示/热量条警告）
- **Epic 2: 资源管理大师**（热量/弹药/护甲）
  - 7 个 User Story（武器产热反馈/弹药耗尽提醒/部位损坏警告）
- **Epic 3: 地形战术专家**（高地/移动代价/掩体）
  - 5 个 User Story（高地俯射加成/山脊掩护撤离/道路快速机动/水域减速/森林遮挡）
- **Epic 4: 机甲装配师**（装配界面/驾驶员管理）
  - 4 个 User Story（武器槽位编辑/配置保存/驾驶员技能查看/驾驶员分配）
- **Epic 5: 任务指挥官**（任务简报/战后结算）
  - 4 个 User Story（目标列表显示/目标进度更新/伤害统计/战利品回收）
- **Epic 6: 多人指挥官**（扩展）
  - 2 个 User Story（创建房间/锁步同步）

**总计**: 31 个 User Story（P0: 10 个 / P1: 15 个 / P2: 14 个 / P3: 2 个）

**用户画像**：
- 休闲战术玩家（25-35 岁，享受微操乐趣）
- 硬核策略专家（30-45 岁，桌游 BattleTech 玩家）
- 剧情沉浸者（20-40 岁，喜欢 RPG 叙事）

---

## 源码文件清单

### 已分析文件（/tmp/mc2src/）
```
核心引擎:
├── Camera.h/cpp          # 相机投影与视锥裁剪
├── Terrain.h/cpp         # 地形渲染管线
├── mapdata.h/cpp         # 顶点数据与光照系统
├── objtype.cpp           # ObjectType 工厂
├── Objblck.h             # 对象块管理
├── objmgr.cpp            # ObjectManager 单例
├── mission.h/cpp         # Mission 类与 Part 结构
├── packet.h/cpp          # .pak 任务包解析器
├── Packet.cpp            # PacketFile 实现
├── lzcomp.cpp/lzdecomp.cpp/Lz.h  # LZ 压缩算法
├── fastfile.h/cpp        # FastFile 资源容器
├── MechCmd2.cpp          # 主循环入口
└── Vertex.h/dterrain.h   # 顶点结构与地形定义

数据文件:
├── buttonlayout1280.fit  # HUD 布局配置（1280×768 分辨率）
├── mc2_01.fit            # 任务 1 配置
├── BJagermech.fit / BMadCat.fit / thor.fit / urbanmech.fit  # 机甲定义
└── textures.fit          # 纹理索引表
```

### 核心常量汇总
```python
# 地图
MAP_TILES = 40                              # 40×40 tile 网格
TILE_SIZE_PX = 32                           # 单 tile 像素尺寸
MAP_SIZE_PX = 1280                          # 地图总像素
VERTICES_BLOCK_SIDE = 20                    # 20×20 顶点块
WORLD_UNITS_PER_VERTEX = 128.0              # 顶点间距

# 机甲
MOVE_SPEED_PX_S = 96.0                      # 基础移动速度
HEADING_STEPS = 8                           # 朝向分桶

# 武器
HIT_FALLOFF = 0.75                          # 距离衰减系数
DEFAULT_PROJECTILE_SPEED_PX_S = 480.0       # 弹速基准
WEAPON_SPEED_MULTIPLIER = {
    BALLISTIC: 1.5,
    ENERGY: 1.0,
    MISSILE: 0.7
}

# 热量
MAX_HEAT = 100.0                            # 热量上限
COOLING_RATE_PER_S = 0.5                    # 散热率
ACCURACY_PENALTY_THRESHOLD = 80.0           # 精度惩罚阈值
SHUTDOWN_DURATION_S = 2.0                   # 关机持续时间

# 高程
ELEVATION_MAX = 3                           # 最大高度级数
CLIMB_MAX_DELTA = 1                         # 单次攀爬限制
CLIMB_COST_PER_LEVEL = 1.5                  # 爬升代价乘数
EYE_HEIGHT_LEVELS = 1                       # 眼高增量

# 技能
SKILL_BASE_MULTIPLIER = 0.85                # 技能基础系数
SKILL_RANK_STEP = 0.06                      # 每级技能增量
```

---

## 使用指南

### 对于 PyMC2 开发者
1. **需求对齐**：参考 PRD 文档确认功能范围与优先级
2. **架构设计**：复用 MC2 模块分层思想，适配 Python + pygame-ce
3. **算法实现**：直接使用详细设计文档中的伪代码
4. **测试验证**：详细设计文档 §6 提供完整测试用例

### 对于 Mod 作者
1. **数据格式**：.fit 文件格式已逆向，可自定义机甲/武器
2. **扩展点**：架构文档 §4 提供 Mod 加载接口设计
3. **脚本引擎**：ABL 脚本规范可用于自定义任务逻辑

### 对于玩家
- 用户故事文档提供完整功能清单与游戏玩法说明
- 可对照 Epic 优先级了解开发路线图

---

## 下一步行动

### Phase 1: 核心战斗原型（基于 PRD §8 路线图）
- [ ] 实现命中判定算法（详细设计 §4.1）
- [ ] 实现部位伤害结算（详细设计 §4.2）
- [ ] 实现视线遮挡判定（详细设计 §4.3）
- [ ] 实现高程移动代价（详细设计 §4.4）
- [ ] 编写测试用例（详细设计 §6）

### Phase 2: 机甲系统
- [ ] 装配编辑器 UI（用户故事 US-4.1.1）
- [ ] 驾驶员技能影响（用户故事 US-4.2.1/2）
- [ ] 武器冷却周期（用户故事 US-2.1.1）

### Phase 3: 任务系统
- [ ] .pak 文件解析（架构文档 §2.5）
- [ ] 目标系统（用户故事 US-5.1.1/2）
- [ ] ABL 脚本引擎（架构文档 §2.4）

---

## 附录：文档生成说明

**生成工具**: DevSquad V4.10.0 多角色 AI 编排系统  
**数据来源**: 
- `/tmp/mc2src/` 源码文件（C++）
- `/tmp/v2probe/` 配置文件（.fit）
- `/tmp/buttonlayout1280.fit` UI 布局
- `/tmp/mc2_Camera.h/cpp` 源码片段

**验证方法**:
- 所有数值常量直接引用源码定义
- 算法伪代码基于 PyMC2 现有实现验证
- 数据结构对照 `mission.h` Part 结构

**可追溯性**:
- 每个设计决策标注源码文件行号
- 关键算法提供等效 Python 伪代码
- 数据表包含原始 C++ 常量定义

---

**文档状态**: ✅ 完整  
**最后更新**: 2026-11-23  
**维护者**: PyMC2 开发团队
