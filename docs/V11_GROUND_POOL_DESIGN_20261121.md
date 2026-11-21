# V11 设计：官方地表纹理池扩充与分类（修橙青马赛克观感）

- 日期：2026-11-21
- 状态：设计定稿 → 实现中（实现记录见文末 §7）
- 上游：Wave V1 纹理地形（记录见 ROADMAP.md 2026-11-19 注记）；P1-A 高程 hills；
- 用户反馈：真机 hills 模式地形呈**橙/青马赛克拼花 + 十字细亮线**，不像 MC2 草地战场。
- 许可红线不变：真实 MC2 资产仅本机缓存（`~/.pymc2/mc2-assets`），零入库、不再分发。

## 1. 根因

V1 grass 池 = `Data/Textures/64` 下 `Detail_*` 前缀全集。实测官方色彩（PIL 均值）：

| stem | 尺寸 | avg RGB | 观感 |
| --- | --- | --- | --- |
| Detail_10000 | 128² | (111, 83, 55) | 棕 |
| Detail_20000 | 128² | (145, 97, 45) | **橙** |
| Detail_30000 | 128² | (75, 107, 89) | **青灰** |
| Detail_40000 | 128² | (91, 110, 53) | 橄榄 |

官方管线里 Detail 系列是**乘算 detail 噪声叠加层**（textures.fit `[DetailType*]`
TilingFactor 115/200），不是地面底色。V1 把 4 张异色叠加层当独立 tile 逐格随机铺
→ 橙/青拼花；道路 overlay 直道帧叠出十字细亮线（跨 tile 接缝帧选帧观感，本切片不动）。

## 2. 远端纹理勘测（Trees API，master=801a705d，2026-11-21）

`Data/Textures` 全树 757 项（5 子树 + 752 根级散文件）：

| 目录 | 文件数 | 命名族 | 推断用途 | 判定 |
| --- | --- | --- | --- | --- |
| Textures/64 | 11 | Cement_1~5、Detail_10000~40000、Water_ocean、undefined | 64px 基础 tile：混凝土/detail 噪声/水 | **与本地缓存完全一致（全集，非稀疏）**；无草/泥地表色，undefined 为品红占位 (255,0,255) |
| Textures/64Mask | 126 | Mask0X0000~0013（9 族 ×14 帧） | 地形过渡灰度蒙版（64×64，L 模式） | 非地表色，不选型 |
| Textures/64Overlays | 475 | 2lanedirt、DirtRoad、PavedRoad、Bridge、runway、rough、Scorch、X_* 交叉变体 | 道路/焦痕覆盖序列（alpha 蒙版） | V1 已接 2lanedirt；其余未来道路语义 |
| Textures/Defaults | 27 | Water_{Ocean,Lake,Desert,Stormy}、default_detail、Mine*、Wake*、feet* | 缺省水面/水花/脚印 | 水族代表 4 张选型；default_detail 近全黑噪声不选 |
| Textures/Random_Maps | 5 | {Forest,Desert,Tundra,Volcanic,Lunar}.jpg | 随机地图**生物群系整图**（1536² JPEG） | **真·地表纹理来源，全选** |
| Textures 根 | 752 | mc2_XX.detail.tga（~30）、mc2_XX.water0000~0015、*.burnin.jpg、*.lst、textures.fit、100 个数值 .txm | 任务专属 detail 噪声/水动画帧/烘焙图/地形定义 | mc2_XX.detail 实测近全黑（avg 22~29）不选；.txm 为二进制压缩容器未解析；textures.fit 为地形定义金矿 |

textures.fit 官方地形定义（74 个 TerrainType）关键证据：`TerrainTacRGB` 即官方地形
色系真值——草地绿族 0x003F4429~0x005C6648（≈ Forest.jpg avg (63,61,29)）、泥土卡其族
0x0097885c~0x00E4BD7E（≈ Desert.jpg avg (155,132,87)）、水泥灰 0x00828282（= Cement_1
avg 148,146,138）、水蓝 0x002B5673。Random_Maps 五图即官方地形色的整图载体。

## 3. 选型表（先于下载定稿）

| 族 | 选中文件（保持远端相对路径与命名） | 尺寸（PIL 实测） | 依据 |
| --- | --- | --- | --- |
| grass 草地 | `Random_Maps/Forest.jpg` | 1536² JPEG | avg (63,61,29) ≈ 官方草地 TacRGB 族；预览/4×4 网格 tile 蒙太奇均为连贯有机草地纹理 |
| dirt 泥地 | `Random_Maps/Desert.jpg` | 1536² JPEG | avg (155,132,87) ≈ 官方泥土 TacRGB 族 |
| rock 岩地 | `Random_Maps/{Volcanic,Lunar,Tundra}.jpg` | 1536² JPEG | Volcanic (98,85,74) 暗岩 / Lunar (140,140,140) 灰岩 / Tundra (199,197,204) 灰白荒原 |
| water 水 | `Defaults/Water_{Ocean,Lake,Desert,Stormy}.tga` | 128² TGA | 官方缺省水面族；water 池本切片不接渲染（Water_ocean 既有语义保留） |

共 9 张 ≤ 25 上限；MC2 资产零入库（仅 `~/.pymc2/mc2-assets/Data/Textures/` 下缓存）。
**偏离决策**：
1. 任务预期「草/泥/岩各 3-8 张到 `Textures/64/`」——勘测证实远端 64/ 已是全集且无
   草/泥地表色，改为下载 Random_Maps 生物群系整图 + Defaults 水面，**保持远端目录
   结构与命名**（落在 `Random_Maps/`、`Defaults/`，不在 `64/`）；
2. 每族仅 1~3 张整图 → 运行时按固定网格派生 tile 帧（每图 4×4=16 帧 128px）补足
   池内多样性（草 16 / 泥 16 / 岩 48 帧），派生确定性（无随机数），不落盘派生产物。

## 4. 分类规则（代码关键词表，首命中归族）

```python
GROUND_FAMILY_KEYWORDS = (  # (族, 小写 stem 子串)——按官方命名族写死，首命中归族
    ("water", "water"),  # Water_ocean / Defaults Water_{Ocean,Lake,Desert,Stormy}
    #   （water 首位：Water_Desert 官方沙漠水面不得被 dirt 误收）
    ("grass", "forest"),  # Random_Forest.jpg → grass
    ("dirt", "desert"),  # Random_Desert.jpg → dirt
    ("rock", "volcanic"),  # Random_Volcanic.jpg → rock
    ("rock", "lunar"),  # Random_Lunar.jpg → rock
    ("rock", "tundra"),  # Random_Tundra.jpg → rock（灰白荒原归岩地）
    ("cement", "cement"),  # Cement_1~5 → cement（城镇地形，不暴露）
)
# 旧缓存回退族：Detail_10000~40000（V1 起 grass 池成员；startswith 精确前缀，
# "default_detail" 等含 detail 子串的杂项不误入）
LEGACY_GROUND_PREFIX = "detail"
```

- 未命中任何关键词且非 detail 前缀 → `None`：不入任何池（`undefined.tga` 品红占位、
  `Mine/Wake/feet/default_detail` 杂项天然排除）。
- 生物群系 JPG 派生帧参数：`BIOME_FRAME_GRID=4`、`BIOME_FRAME_MAX_PX=128`；
  帧边长 `min(128, w//4, h//4)`，取样点在图内均匀分布（端点含边），行主序排序。
- 池构成：族池 = 该族 JPG 派生帧（stem 排序 + 帧行主序）+ 该族 TGA（stem 排序）；
  `ground_pool(kind)`：grass/dirt 缺族时回退 detail 族池；rock 无回退；water 按族；
  road 仍为 2lanedirt overlay 序列；cement 与未登记 kind 恒空池（城镇地形 V2+ 再暴露）。

### 降级矩阵（行为兼容铁律）

| 缓存形态 | grass 池 | dirt 池（道路底色） | rock/water/cement 池 | 渲染输出 |
| --- | --- | --- | --- | --- |
| 旧缓存（仅 Detail/Cement/Water_ocean，V1 现状） | detail 族（= V1 现状逐像素同） | detail 族（= V1 道路底=草地池同源同序） | rock 空 / water Water_ocean / cement 空 | 与 V1 完全一致 |
| V11 全量缓存（Random_Maps + Defaults + 64） | Forest 派生帧 | Desert 派生帧 | rock Volcanic/Lunar/Tundra / water 5 张 / cement 不暴露 | 草地战场观感 |
| 仅生物群系（无 64 TGA） | Forest 帧 | Desert 帧；无 Desert 时 grass 池兜底（渲染层） | 按族 | 纯 JPG 派生 |
| 无地面资产 | 空 → 程序化色块（现状） | 同左 | 空 | 未注入状态 |

## 5. 渲染接线（renderer）

- `_fill_grass_tile`：`scaled_ground_tile("grass", seed)`（池内已分类），缺池回退
  dirt 池再回退程序化草色（tile 哈希确定性不变）；
- `_fill_road_tile`：底色改 `scaled_ground_tile("dirt", seed)`（道路=泥径语义），
  缺 dirt 池回退 grass 池（旧缓存行为不变）；2lanedirt overlay 蒙版逻辑零改动；
- cement/water/rock 不接任何渲染路径（本切片口径）。

## 6. 测试计划

- 单测（tests/unit/test_mc2_assets.py）：族分类真值表（7 关键词 + detail 前缀 +
  未命中排除）、JPG 派生确定性（重载同帧字节相等）、派生帧数/边长参数化、
  旧缓存降级（grass/dirt 池=detail、cement 恒 None、undefined 不入池）、
  Defaults 水族入池、损坏 JPG 跳过不阻断。
- e2e 切片（tests/e2e/test_v11_ground_pool_slice.py）：fixture 多族纹理
  （Forest/Desert JPG + overlay 蒙版）实战——草地 tile 绿主导（g>r）、道路蒙版内
  overlay 色、蒙版外泥底色（r>g）；旧单族缓存回归（草地/路底=Detail 色逐像素）。
  截图存 docs/assets/uat_v11/。
- 真机验收：hills 模式冒烟（本机 V11 全量缓存），截图存 docs/assets/uat_v11/real_*.png。

## 7. 实现记录（2026-11-21 回写）

- mc2_assets：`DEFAULTS_DIR`/`BIOME_DIR` 扫描扩展、`_ground_sequences` 生物群系帧
  索引、`GROUND_FAMILY_KEYWORDS`/`LEGACY_GROUND_PREFIX`/`ground_family()`、
  `_derive_biome_frames()`（PIL→pygame，损坏跳过）、`ground_pool()` 族池改选 +
  detail 回退、`has_ground_assets` 计入生物群系帧、cement 保持不暴露。
  实测真缓存池：grass 16 / dirt 16 / rock 48 / water 4 / cement 0（不暴露）/ road 6。
- renderer：`_fill_grass_tile` grass→dirt→程序化三级回退；`_fill_road_tile` 底色
  dirt 池（缺族回退 grass），overlay 逻辑零改动。
- 下载入库缓存（9 张，零入库仓库）：Random_Maps 五图（1536² JPEG）+ Defaults 四水
  （128² TGA），全部 PIL 验证。
- 分类序缺陷修正（实现中发现）：初版关键词表 dirt(desert) 先于 water——
  Defaults/Water_Desert.tga（官方沙漠水面）被误收 dirt 池；water 关键词移至首位。
- 验收：单测 +9（族分类真值表/JPG 派生确定性/帧尺寸参数化/族池断言/旧缓存 detail
  回退/undefined 排除/Defaults 杂项排除/损坏 JPG 跳过/纯 JPG pack）+ e2e +2
  （多族实战像素断言 + 旧单族缓存逐像素回归），全量 **686 passed / 0 failed**
  （675 存量零语义改动 + 11 新增）+ ruff format/check + mypy（37 文件）全绿。
- 真机 hills 冒烟截图 3 张（docs/assets/uat_v11/real_01_hills_opening.png、
  real_02_hills_midframe.png、real_03_hills_engagement.png）观感自评：草地由橙青
  拼花改为深橄榄绿连续草皮（Forest 派生帧 16 变体，tile 间深浅斑块自然）、道路为
  土黄泥径底 + 2lanedirt 蒙版（贴近 MC2 泥路语义）、丘陵 terrace 崖壁正常叠现；
  十字交叉处细亮线为 overlay 直道帧接缝观感，属 V1 道路接缝遗留（本切片不动，
  已留痕）。与 MC2 官方草地战场（textures.fit 草地 TacRGB 族）观感方向一致。
