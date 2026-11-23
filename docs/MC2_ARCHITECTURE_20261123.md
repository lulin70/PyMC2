# MechCommander 2 系统架构文档

**版本**: 1.0  
**日期**: 2026-11-23  
**来源**: MC2 源码逆向分析  
**范围**: 核心模块、数据流、扩展点

---

## 1. 架构概览

### 1.1 整体架构风格
MechCommander 2 采用**分层架构 + 数据驱动**模式：

```
┌─────────────────────────────────────────────────────────┐
│          表现层 (Presentation Layer)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ 3D渲染器 │  │ UI管理器 │  │ 音频引擎 │             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
└───────┼─────────────┼─────────────┼────────────────────┘
        │             │             │
┌───────┼─────────────┼─────────────┼────────────────────┐
│       │   逻辑层 (Logic Layer)     │                    │
│  ┌────▼─────┐  ┌───▼──────┐  ┌───▼──────┐            │
│  │ 战斗系统 │  │ AI引擎   │  │ 任务系统 │            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘            │
└───────┼─────────────┼─────────────┼────────────────────┘
        │             │             │
┌───────┼─────────────┼─────────────┼────────────────────┐
│       │   数据层 (Data Layer)      │                    │
│  ┌────▼─────┐  ┌───▼──────┐  ┌───▼──────┐            │
│  │ 对象管理 │  │ 资源缓存 │  │ 脚本引擎 │            │
│  └──────────┘  └──────────┘  └──────────┘            │
└──────────────────────────────────────────────────────-─┘
```

### 1.2 核心设计原则
1. **数据驱动内容**：所有游戏参数（武器/机甲/地形）通过 .fit 配置文件定义
2. **实体-组件分离**：`ObjectType`（类型定义）与 `GameObject`（实例）分离
3. **块状管理**：地形/对象使用 20×20 块状结构优化内存与剔除
4. **脚本驱动任务**：ABL 脚本控制任务逻辑，无需重编译

---

## 2. 模块设计

### 2.1 渲染模块 (Renderer)

#### 文件结构
```
MCLib/
├── Camera.h/cpp         # 相机与投影
├── Terrain.h/cpp        # 地形渲染管线
├── mapdata.h/cpp        # 顶点数据与光照
├── appearance.h/cpp     # 外观系统（推断）
└── MLR/                 # Microsoft Layered Rendering
    ├── mlr_i_*.cpp      # 索引几何体
    └── mlr_*.cpp        # 顶点/纹理管理
```

#### 关键类

**Camera** (`Camera.h:69-880`)
```cpp
class Camera {
public:
    // 投影设置
    bool usePerspective;              // 透视/正交模式
    float projectionAngle;            // FOV (28-60°)
    float tiltFactor;                 // 俯仰角
    
    // 视口变换
    void projectZ(Vector3D& world, Vector4D& screen);
    bool inView(Vector3D& position, float radius);
    
    // 移动控制
    void moveLeft(float amount);      // 相机平移
    void moveRight/Up/Down(...);
    void ZoomIn/Out(float delta);     // 缩放
    
private:
    Vector3D cameraPosition;
    Quaternion cameraRotation;
    float viewMulX, viewMulY;         // 屏幕空间缩放系数
};
```

**Terrain** (`Terrain.h:62-328`)
```cpp
class Terrain {
public:
    // 网格常量
    static const long verticesBlockSide = 20;  // 20×20顶点块
    static const long realVerticesMapSide;     // 总顶点数(60-120)
    static const float worldUnitsPerVertex;    // 128.0f
    
    // 渲染管线
    void render(long &actualVertsDrawn, long &actualIndicesDrawn);
    void update();                             // 每帧更新可见性
    
    // 高程查询
    float getTerrainElevation(Vector3D& pos);
    bool isVisible(Vector3D& from, Vector3D& to); // LOS检测
    
private:
    PostcompVertex* mapData;                   // 顶点数组
    bool* terrainVisibleFlags;                 // 可见性标记
    void recalcLight();                        // 重算光照
    void recalcShadows();                      // 重算阴影
};
```

#### 渲染流程
```
每帧更新:
1. Camera::update() → 更新视锥体
2. Terrain::update() → 视锥裁剪标记可见块
3. ObjectManager::update() → 更新对象动画状态
4. Terrain::render() → 绘制地形块
5. ObjectManager::render() → 绘制对象（深度排序）
6. UI::render() → 绘制HUD
```

#### 性能优化
- **视锥裁剪**：球体常量 `verticalSphereClipConstant = -TAN(projAngle/2)`
- **LOD 系统**：远距离对象降低多边形数（`MLR` 层负责）
- **延迟光照**：仅可见顶点参与光照计算

---

### 2.2 对象系统 (Object System)

#### 文件结构
```
MCLib/
├── objtype.h/cpp        # ObjectType工厂
├── objmgr.h/cpp         # ObjectManager单例
├── Objblck.h            # 对象块管理
├── GameObject.h/cpp     # 实例基类（推断）
└── mission.h            # Part结构（任务实体）
```

#### 类图
```
                  ┌──────────────┐
                  │  ObjectType  │
                  │  (工厂类)    │
                  └───────┬──────┘
                          │ creates
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   ┌────▼────┐      ┌─────▼──────┐   ┌─────▼─────┐
   │  Mech   │      │  Vehicle   │   │  Turret   │
   │  Type   │      │   Type     │   │   Type    │
   └────┬────┘      └─────┬──────┘   └─────┬─────┘
        │                 │                 │
        │ instantiate     │                 │
        ▼                 ▼                 ▼
   ┌────────────┐    ┌────────────┐  ┌────────────┐
   │ GameObject │◄───│ GameObject │◄─│ GameObject │
   │ (instance) │    │ (instance) │  │ (instance) │
   └────────────┘    └────────────┘  └────────────┘
```

**ObjectType** (`objtype.cpp:143-385`)
```cpp
class ObjectType {
public:
    long objectTypeNum;              // 全局唯一ID
    char name[40];                   // 类型名称
    long explosionObject;            // 爆炸特效ID
    long destroyedObject;            // 残骸模型ID
    float extentRadius;              // 碰撞半径
    char appearanceName[48];         // .ase文件路径
    
    virtual GameObject* create();    // 工厂方法
    virtual void handleCollision(...);
    void createExplosion(Vector3D& pos, float dmg, float rad);
    
    // 子类
    static BattleMechType* create();
    static VehicleType* create();
    static TurretType* create();
};
```

**ObjectManager** (`objmgr.cpp` - 推断接口)
```cpp
class ObjectManager {
public:
    static ObjectManager* getInstance();
    
    // 对象生命周期
    GameObject* createObject(long typeNum, Team* team, Vector3D& pos);
    void destroyObject(GameObjectPtr obj);
    void update(float deltaTime);
    
    // 查询接口
    GameObject* findObject(long id);
    std::vector<GameObject*> objectsInRadius(Vector3D& center, float radius);
    
private:
    ObjectBlock blocks[MAX_BLOCKS];  // 块状存储
    long numObjects;
};
```

**Part 结构** (`mission.h:57-85`) - 任务中的对象实例
```cpp
struct Part {
    GameObjectWatchID objectWID;     // 物理对象引用（弱指针）
    unsigned long objNumber;         // 类型编号
    
    // 视觉属性
    DWORD baseColor;                 // RGB基础色
    DWORD highlightColor1/2;         // 高光色
    
    // 状态标志
    bool active;                     // 激活中
    bool exists;                     // 存在于世界
    bool destroyed;                  // 已被摧毁
    
    // 物理属性
    Vector3D position;               // 世界坐标
    float velocity;                  // 速度(world units/s)
    float rotation;                  // 朝向(弧度)
    
    // 玩法属性
    char alignment;                  // 阵营(-1=敌, 0=中立, 1=友)
    char teamId, squadId;            // 队伍/小队ID
    unsigned long controlType;       // 控制类型(AI/玩家)
    char profileName[9];             // 配置文件名
    unsigned long pilot;             // 驾驶员ID
    bool captureable;                // 可捕获
    unsigned long gestureId;         // 动画姿态ID
    DWORD variantNum;                // 变体编号
};
```

#### 对象块管理
```cpp
// Objblck.h:53-186
struct ObjectBlock {
    GameObject objects[MAX_OBJECTS_PER_BLOCK];  // 200个对象槽位
    ObjectQueueNode* activeList;                 // 活跃对象链表
    long numActive;
};
```

**优势**：
- 连续内存布局 → CPU 缓存友好
- 块级激活/停用 → 视锥外对象零更新
- 固定槽位 → 无动态分配/释放

---

### 2.3 战斗系统 (Combat System)

#### 文件结构（推断）
```
GameLogic/
├── Mech.h/cpp           # 机甲类
├── Weapon.h/cpp         # 武器系统
├── Ballistic.h/cpp      # 弹道计算
├── Damage.h/cpp         # 伤害结算
└── Team.h/cpp           # 队伍管理
```

#### 核心流程

**射击判定** (推断自 `gameLogic.cpp`)
```cpp
class WeaponSystem {
public:
    bool canFire(Mech* attacker, GameObject* target) {
        // 1. 冷却检查
        if (currentTime < lastFireTime + cooldown) return false;
        
        // 2. 距离检查
        float dist = distance(attacker->pos, target->pos);
        if (dist > weapon->range) return false;
        
        // 3. LOS检查
        if (!Terrain::isVisible(attacker->pos, target->pos)) return false;
        
        // 4. 热量检查
        if (attacker->heatLevel > 90) return false; // 临近关机
        
        return true;
    }
    
    void fire(Mech* attacker, GameObject* target) {
        // 1. 命中判定
        float hitChance = calculateHitChance(attacker, target);
        bool hit = random() < hitChance;
        
        // 2. 创建投射物
        Vector3D impact = hit ? target->pos : scatterPoint(target->pos);
        Projectile* proj = createProjectile(
            attacker->pos, impact, weapon->speed, weapon->damage
        );
        
        // 3. 累加热量
        attacker->heatLevel += weapon->heat;
        
        // 4. 消耗弹药
        if (weapon->ammoType != UNLIMITED) {
            attacker->ammo[weapon->ammoType]--;
        }
    }
    
private:
    float calculateHitChance(Mech* attacker, GameObject* target) {
        float dist = distance(attacker->pos, target->pos);
        float distFactor = 1.0 - 0.75 * (dist / weapon->range);
        float skillFactor = 0.85 + 0.06 * attacker->pilot->gunneryRank;
        float heatPenalty = attacker->heatLevel > 50 ? 
            (100 - attacker->heatLevel) / 100 : 1.0;
        return distFactor * skillFactor * heatPenalty;
    }
};
```

**伤害结算** (推断)
```cpp
class DamageSystem {
public:
    void applyDamage(GameObject* target, float damage, MechLocation location) {
        if (target->type != BATTLEMECH) {
            target->health -= damage;
            if (target->health <= 0) destroy(target);
            return;
        }
        
        Mech* mech = static_cast<Mech*>(target);
        
        // 1. 护甲吸收
        float armorDamage = min(damage, mech->armor[location]);
        mech->armor[location] -= armorDamage;
        float overflow = damage - armorDamage;
        
        // 2. 内部结构伤害
        if (overflow > 0) {
            mech->internalStructure[location] -= overflow;
            
            // 3. 临界命中判定（15%概率）
            if (random() < 0.15) {
                destroyComponent(mech, location); // 武器/设备瘫痪
            }
            
            // 4. 击毁判定
            if (location == CT && mech->internalStructure[CT] <= 0) {
                destroyMech(mech);
            } else if (mech->internalStructure[location] <= 0) {
                disableLocation(mech, location); // 部位失能
            }
        }
    }
};
```

#### 武器类型差异化

| 武器类型 | 弹道特性 | 热量 | 伤害模式 |
|----------|----------|------|----------|
| BALLISTIC | 快速直射（1.5× 基础弹速） | 低 | 单发高伤 + 部位权重判定 |
| ENERGY | 瞬时命中（1.0× 基础弹速） | 高 | 无视护甲加成（直伤 CT） |
| MISSILE | 齐射散布（0.7× 基础弹速 + 末端加速） | 中 | 多发小伤均摊（3 发） |

---

### 2.4 AI 系统 (AI System)

#### 文件结构（推断）
```
AI/
├── Brain.h/cpp          # AI决策树
├── ABLModule.h/cpp      # ABL脚本引擎
├── FSM.h/cpp            # 有限状态机
└── Pathfinding.h/cpp    # A*寻路
```

#### ABL 脚本引擎

**ABLModule** (`mission.h:150-380` 引用)
```cpp
class ABLModule {
public:
    void init(const char* scriptPath);
    void execute();                      // 每帧执行脚本主循环
    void setParam(const char* name, float value);
    float getParam(const char* name);
    
    // 回调接口
    void registerCallback(const char* eventName, SymTableNodePtr func);
    void triggerEvent(const char* eventName);
    
private:
    SymTable symbolTable;                // 符号表
    CodeBuffer bytecode;                 // 字节码
};
```

**ABL 脚本示例** (伪代码)
```abl
FSM MechBrain {
    state Idle {
        if (enemyInRange()) {
            setState(Engage);
        } else if (hasOrders()) {
            setState(MoveToObjective);
        }
    }
    
    state Engage {
        target = nearestEnemy();
        if (canFire(target)) {
            fire(target);
        } else {
            moveTo(firePosition(target));
        }
        
        if (health < 30%) {
            setState(Retreat);
        }
    }
    
    state Retreat {
        moveTo(repairBay);
        if (arrived(repairBay)) {
            setState(Idle);
        }
    }
}
```

#### AI 决策周期
```
每 500ms 执行一次:
1. 感知阶段 → 扫描视野内敌单位
2. 决策阶段 → FSM 状态转移
3. 执行阶段 → 发布移动/攻击指令
4. 评估阶段 → 检查健康状态/弹药/热量
```

---

### 2.5 任务系统 (Mission System)

#### 文件结构
```
Data/
├── mission.h/cpp        # Mission类
├── Objective.h/cpp      # 目标管理
├── packet.h/cpp         # .pak解析器
└── missions/
    ├── mc2_01.pak       # 任务1数据包
    └── mc2_02.pak       # 任务2...
```

**Mission 类** (`mission.h:150-380`)
```cpp
class Mission {
public:
    // 初始化
    void load(const char* pakPath);
    void init();
    
    // 目标系统
    void addObjective(const char* name, ObjectiveType type, ...);
    void updateObjective(const char* name, ObjectiveStatus status);
    Objective* getObjective(const char* name);
    
    // 实体管理
    void addPart(Part& part);                // 添加对象
    Part* getPart(long id);
    std::vector<Part*> getPartsInTeam(char teamId);
    
    // 脚本驱动
    ABLModulePtr missionBrain;               // 任务脚本
    void executeBrain(float deltaTime);
    
    // 状态查询
    unsigned long scenarioResult;            // 任务结果(0=进行中,1=失败,5=胜利)
    float timeLeft;                          // 任务倒计时
    
private:
    Part parts[MAX_PARTS];                   // 对象数组
    Objective objectives[MAX_OBJECTIVES];
};
```

#### .pak 文件解析

**PacketFile** (`packet.h:56-131`)
```cpp
class PacketFile {
public:
    long open(const char* fName);
    long readPacket(long packet, unsigned char* buffer);
    long seekPacket(long packet);
    long getPacketSize(long packet = -1);
    
private:
    struct PacketEntry {
        long offset;                         // 文件偏移
        long size;                           // 压缩后大小
        long realSize;                       // 解压后大小
        long type;                           // 压缩类型
    };
    
    PacketEntry* seekTable;                  // 索引表
    long numPackets;                         // 包数量
    
    void decompress(unsigned char* src, unsigned char* dest, long type);
};
```

**任务数据包结构**
```
mc2_01.pak:
├── Packet 0: 地形高度图 (100×100 float数组)
├── Packet 1: 纹理索引 (100×100 byte数组)
├── Packet 2: 对象列表 (Part结构数组)
├── Packet 3: 目标定义 (Objective数组)
├── Packet 4: ABL脚本字节码
└── Packet N: 额外资源...
```

---

### 2.6 资源管理 (Resource Management)

#### 文件结构
```
MCLib/
├── mc2res.h/cpp         # 资源加载器
├── fastfile.h/cpp       # FastFile容器
├── ffent.h              # 文件入口
└── lzcomp.h/cpp         # LZ压缩/解压
```

**FastFile** (`fastfile.h` - 资源容器)
```cpp
class FastFile {
public:
    long open(const char* fname);
    long openFast(long fastNum, unsigned char** data);
    long sizeFast(long fastNum);
    void close();
    
private:
    struct FileEntry {
        char name[12];                       // 8.3文件名
        long offset;
        long size;
    };
    
    FileEntry* directory;                    // 文件目录
    long numFiles;
};
```

#### 缓存策略
```cpp
class ResourceCache {
public:
    // 三级缓存
    Texture* getTexture(const char* name) {
        // L1: 热缓存(最近16个)
        if (hotCache.contains(name)) return hotCache[name];
        
        // L2: 冷缓存(最近256个)
        if (coldCache.contains(name)) {
            promoteToHot(name);
            return coldCache[name];
        }
        
        // L3: 从磁盘加载
        Texture* tex = loadFromDisk(name);
        coldCache.insert(name, tex);
        return tex;
    }
    
private:
    LRUCache<Texture*> hotCache;             // 16个槽位
    LRUCache<Texture*> coldCache;            // 256个槽位
};
```

---

## 3. 数据流图

### 3.1 渲染数据流
```
磁盘资源 (.pak/.fit/.ase)
    │
    ▼
ResourceCache (三级缓存)
    │
    ├─► Terrain::mapData (顶点/纹理索引)
    ├─► ObjectType (外观/碰撞数据)
    └─► Texture (DDS/TGA位图)
         │
         ▼
      Camera (投影变换)
         │
         ▼
      MLR Layer (几何体生成)
         │
         ▼
      Direct3D 7.0 / OpenGL (光栅化)
         │
         ▼
      帧缓冲 (1280×768 RGBA)
```

### 3.2 玩法数据流
```
玩家输入 (鼠标/键盘)
    │
    ▼
UI Manager (按钮/热键映射)
    │
    ▼
Command Queue (指令队列)
    │
    ├─► 移动指令 → Pathfinding → GameObject::moveTo()
    ├─► 攻击指令 → WeaponSystem::fire()
    └─► 编队指令 → Team::setFormation()
         │
         ▼
      Mission::update(deltaTime)
         │
         ├─► AI::executeBrain() (AI单位)
         ├─► Ballistic::advanceProjectiles()
         └─► Damage::applyCollisions()
              │
              ▼
           ObjectManager::update()
              │
              ▼
           GameObject::updateAnimation()
```

### 3.3 任务脚本数据流
```
.pak文件 (任务定义)
    │
    ▼
PacketFile::readPacket()
    │
    ├─► 解析Part数组 → Mission::addPart()
    ├─► 解析Objective → Mission::addObjective()
    └─► 加载ABL字节码 → ABLModule::init()
         │
         ▼
      Mission::executeBrain(每帧)
         │
         ├─► 检查触发条件 (IF/ELSE逻辑)
         ├─► 生成增援 → ObjectManager::createObject()
         ├─► 更新目标状态 → Objective::status = Success
         └─► 检查胜利条件 → Mission::scenarioResult = WIN
              │
              ▼
           触发结算画面
```

---

## 4. 扩展点设计

### 4.1 Mod 支持

#### 自定义机甲
```ini
# 新机甲配置: CustomMech.fit
[ObjectType]
l ObjectTypeNum = 9999              # 自定义ID范围: 9000-9999
st Name = "custom_assault"
st AppearanceName = "custom_assault.ase"
f ExtentRadius = 15
l ExplosionObject = 41

[MechProfile]
st ProfileName = "custom_assault"
f tonnage = 100
f maxSpeed = 48
f armor_CT = 90
f armor_LT = 70
...
```

#### 自定义武器
```ini
# 新武器: Gauss_Rifle.fit
[WeaponType]
l WeaponTypeNum = 8888
st Name = "gauss_rifle"
l Category = 0                      # 0=BALLISTIC, 1=ENERGY, 2=MISSILE
f Damage = 15
f Range = 660
f Heat = 1
f Cooldown = 7.5
f ProjectileSpeed = 720.0
l CriticalSlots = 7
```

#### Mod 加载流程
```cpp
// 启动时扫描 Mods/ 目录
void ModManager::loadMods() {
    for (auto& modDir : scanDirectory("Mods/")) {
        // 1. 读取 mod.json 元数据
        ModManifest manifest = parseManifest(modDir + "/mod.json");
        
        // 2. 加载 .fit 配置(覆盖原版)
        for (auto& fitFile : manifest.configs) {
            FitIniFile fit(modDir + "/" + fitFile);
            ObjectTypeRegistry::registerOverride(fit);
        }
        
        // 3. 挂载资源包
        for (auto& pakFile : manifest.assets) {
            ResourceCache::mount(modDir + "/" + pakFile);
        }
    }
}
```

### 4.2 多人对战扩展

#### 网络同步架构（新增模块）
```cpp
class NetworkManager {
public:
    // 服务端
    void hostGame(long maxPlayers);
    void broadcastState(GameState& state);
    
    // 客户端
    void joinGame(const char* serverIP);
    void sendCommand(Command& cmd);
    
private:
    // 锁步同步(每100ms一帧)
    void lockstepUpdate() {
        // 1. 收集所有玩家指令
        std::vector<Command> commands = collectCommands();
        
        // 2. 确定性排序(按玩家ID)
        sortCommands(commands);
        
        // 3. 执行指令
        for (auto& cmd : commands) {
            executeCommand(cmd);
        }
        
        // 4. 推进物理模拟
        Mission::update(0.1f);
    }
};
```

**关键设计点**：
- **确定性模拟**：相同输入 → 相同输出（固定时间步、固定随机种子）
- **指令同步**：仅同步玩家指令，不同步游戏状态
- **延迟补偿**：预测性移动 + 服务端回滚校正

### 4.3 编辑器接口

#### 任务编辑器 API
```cpp
class MissionEditor {
public:
    // 地形编辑
    void setElevation(long x, long y, float height);
    void paintTexture(long x, long y, long textureID);
    void addForest(Rect area, float density);
    
    // 对象放置
    Part* spawnMech(const char* chassis, Vector3D pos, char team);
    Part* spawnBuilding(const char* type, Vector3D pos);
    
    // 目标编辑
    Objective* addObjective(ObjectiveType type, const char* desc);
    void setTrigger(Objective* obj, const char* condition);
    
    // 保存为 .pak
    void exportMission(const char* outPath);
};
```

---

## 5. 性能优化策略

### 5.1 内存优化

#### 对象池
```cpp
template<typename T, size_t POOL_SIZE>
class ObjectPool {
public:
    T* allocate() {
        for (size_t i = 0; i < POOL_SIZE; ++i) {
            if (!inUse[i]) {
                inUse[i] = true;
                return &pool[i];
            }
        }
        return nullptr; // 池满，返回空
    }
    
    void free(T* obj) {
        size_t index = obj - pool;
        inUse[index] = false;
    }
    
private:
    T pool[POOL_SIZE];
    bool inUse[POOL_SIZE];
};

// 使用示例
ObjectPool<Projectile, 500> projectilePool; // 500发弹丸复用
```

#### 块状地形缓存
```cpp
struct TerrainBlock {
    PostcompVertex vertices[20*20];         // 400个顶点
    IndexBuffer indices;                    // 三角形索引
    bool dirty;                             // 需要重新上传GPU
};

// 仅重建脏块
void Terrain::rebuildDirtyBlocks() {
    for (auto& block : blocks) {
        if (block.dirty) {
            uploadToGPU(block.vertices, block.indices);
            block.dirty = false;
        }
    }
}
```

### 5.2 渲染优化

#### 批量绘制
```cpp
class BatchRenderer {
public:
    void addQuad(Vector3D pos, Texture* tex) {
        batches[tex].vertices.push_back(pos);
    }
    
    void flush() {
        for (auto& [tex, batch] : batches) {
            bindTexture(tex);
            drawVertices(batch.vertices); // 单次Draw Call
        }
        batches.clear();
    }
    
private:
    std::map<Texture*, VertexBatch> batches;
};
```

#### 视锥裁剪优化
```cpp
// 粗粒度裁剪(块级)
bool Camera::isBlockVisible(BoundingBox& blockBox) {
    return frustum.intersects(blockBox);
}

// 细粒度裁剪(对象级)
bool Camera::isObjectVisible(Vector3D& pos, float radius) {
    return frustum.containsSphere(pos, radius);
}
```

### 5.3 AI 优化

#### 分帧更新
```cpp
class AIScheduler {
public:
    void update(float deltaTime) {
        // 每帧更新 1/5 的AI单位
        int start = (frameIndex % 5) * (numUnits / 5);
        int end = start + numUnits / 5;
        
        for (int i = start; i < end; ++i) {
            aiUnits[i]->updateBrain(deltaTime * 5);
        }
        
        frameIndex++;
    }
};
```

---

## 6. 技术债务与重构建议

### 6.1 原版技术债务
| 问题 | 影响 | 重构方案 |
|------|------|----------|
| **全局对象管理器** | 单例模式限制测试 | 依赖注入（构造函数传递） |
| **深层继承体系** | ObjectType 7层继承 | 组合优于继承（ECS架构） |
| **硬编码常量** | 分辨率/单位数量写死 | 配置文件驱动 |
| **单线程架构** | AI/物理/渲染阻塞 | 分离线程（AI/物理异步） |

### 6.2 现代化改造路径

#### Phase 1: 核心解耦
- 抽离渲染层接口（支持 OpenGL/Vulkan/Metal）
- 对象系统重写为 ECS（Entity-Component-System）
- 资源管理改用异步加载

#### Phase 2: 多线程改造
```cpp
// 线程分离
Thread renderThread;    // 渲染（60 FPS）
Thread physicsThread;   // 物理模拟（120 FPS固定步长）
Thread aiThread;        // AI决策（2 Hz）

// 线程安全队列
ConcurrentQueue<Command> commandQueue;
```

#### Phase 3: 脚本语言替换
- **选项 A**：保留 ABL（兼容原版任务）
- **选项 B**：Lua 重写（社区 Mod 友好）
- **推荐**：双引擎共存（ABL 执行 + Lua 扩展 API）

---

## 7. 部署架构

### 7.1 单机版
```
游戏可执行文件 (MechCmd2.exe)
├── Data/
│   ├── missions/*.pak           # 任务包
│   ├── textures/*.dds           # 纹理
│   ├── sounds/*.wav             # 音效
│   └── configs/*.fit            # 配置
├── Mods/                        # Mod目录
└── Saves/                       # 存档
```

### 7.2 多人版（新增）
```
服务端 (MechCmd2_Server.exe)
├── 权威游戏状态
├── 反作弊验证
└── 日志记录

客户端 (MechCmd2_Client.exe)
├── 渲染 + UI
├── 预测性模拟
└── 指令上传
```

---

## 8. 总结

### 核心架构优势
1. **数据驱动**：游戏参数全部外部化，无需重编译
2. **模块化**：清晰的层次分离，易于维护
3. **内存高效**：块状管理 + 对象池，适合低端硬件

### 现代化升级建议
1. **ECS 架构**：替换深层继承，提升灵活性
2. **多线程**：分离 AI/物理/渲染，提升帧率
3. **脚本扩展**：Lua 或 Python 绑定，支持热重载

### 扩展潜力
- **Mod 生态**：通过 .fit 配置 + ABL 脚本，社区可创作无限内容
- **多人对战**：锁步同步架构易于实现（确定性模拟已具备）
- **跨平台**：抽象渲染层后可移植到 macOS/Linux/移动端

---

**文档状态**: ✅ 完成  
**相关文档**: MC2_PRD_20261123.md（产品需求）  
**下一步**: 生成详细设计文档（MC2_DESIGN_20261123.md）
