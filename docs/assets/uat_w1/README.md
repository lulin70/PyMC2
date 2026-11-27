# W1 E2E 验收截图清单

**Wave**: W1 (官方 pak 纹理映射 + Objective 系统)  
**阶段**: P9 Test Execution  
**日期**: 2026-11-27  
**验收标准**: 5 张截图 + 视觉差异 >50% 验证

---

## 截图清单（5 张）

### 1. pak_texture_baseline.png
**场景**：V1.1 JPG pool 基线（pak 纹理映射关闭）  
**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
# 临时注释 renderer.py 中 ground_tile_by_index 调用，回退 JPG pool
# 或直接使用 V1.1 git checkout 渲染
.venv/bin/python -m pymc2.main --mission mc2_01 --headless --screenshot docs/assets/uat_w1/pak_texture_baseline.png --max-ticks 60
```
**预期内容**：地形使用程序化 JPG 池纹理（`Random_Maps/*.jpg` 派生 4×4 网格）

### 2. pak_texture_enabled.png
**场景**：pak 纹理映射开启（TGA 查表优先）  
**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
# 确保 renderer.py ground_tile_by_index 调用启用
.venv/bin/python -m pymc2.main --mission mc2_01 --headless --screenshot docs/assets/uat_w1/pak_texture_enabled.png --max-ticks 60
```
**预期内容**：地形使用官方 `Textures/64/*.tga` 纹理（视觉差异 >50%）

**视觉差异验证**：
```bash
# 使用 ImageMagick 计算差异百分比
compare -metric RMSE docs/assets/uat_w1/pak_texture_baseline.png docs/assets/uat_w1/pak_texture_enabled.png docs/assets/uat_w1/pak_texture_diff.png 2>&1 | awk '{print $1}'
# 预期输出：差异度 >0.5（50% 阈值）
```

### 3. objective_panel_pending.png
**场景**：HUD 目标面板 PENDING 状态  
**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
# 创建测试任务脚本（3 PRIMARY + 2 SECONDARY，全 PENDING）
cat > /tmp/test_objective_pending.json <<'EOF'
{
  "script_id": "test_pending",
  "triggers": []
}
EOF
# 注入 ObjectiveSet 到 Renderer（需修改 main.py 或创建测试入口）
.venv/bin/python scripts/test_objective_hud.py --status pending --screenshot docs/assets/uat_w1/objective_panel_pending.png
```
**预期内容**：
- 左上角半透明黑背景面板（alpha=180）
- 标题 "MISSION OBJECTIVES" 白色粗体 20px
- PRIMARY 分组标题 + 3 个白色空框（☐）+ 目标描述
- SECONDARY 分组标题 + 2 个白色空框（☐）+ 目标描述

### 4. objective_panel_completed.png
**场景**：HUD 目标面板 COMPLETED 状态（部分完成）  
**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
.venv/bin/python scripts/test_objective_hud.py --status completed --screenshot docs/assets/uat_w1/objective_panel_completed.png
```
**预期内容**：
- PRIMARY 目标 1-2 显示绿色对勾（✓）
- PRIMARY 目标 3 显示白色空框（☐，未完成）
- SECONDARY 全显示绿色对勾（✓）

### 5. objective_panel_failed.png
**场景**：HUD 目标面板 FAILED 状态  
**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
.venv/bin/python scripts/test_objective_hud.py --status failed --screenshot docs/assets/uat_w1/objective_panel_failed.png
```
**预期内容**：
- PRIMARY 目标 1 显示红色叉号（✗，失败）
- PRIMARY 目标 2-3 显示白色空框（☐）
- SECONDARY 显示混合状态（部分完成 ✓ + 部分失败 ✗）

---

## 验收标准验证

### 1. 视觉差异 >50%
```bash
compare -metric RMSE docs/assets/uat_w1/pak_texture_baseline.png docs/assets/uat_w1/pak_texture_enabled.png null: 2>&1 | awk -F'[()]' '{print $2}' | awk '{if ($1 > 0.5) print "PASS: "$1; else print "FAIL: "$1}'
```

### 2. undefined.tga 回退不崩溃
```bash
# 临时重命名部分 TGA 文件模拟缺失
mv ~/.pymc2/mc2-assets/Data/Textures/64/Detail_10000.tga ~/.pymc2/mc2-assets/Data/Textures/64/Detail_10000.tga.bak
.venv/bin/python -m pymc2.main --mission mc2_01 --headless --max-ticks 60 2>&1 | grep -i "crash\|error" || echo "PASS: undefined.tga fallback works"
mv ~/.pymc2/mc2-assets/Data/Textures/64/Detail_10000.tga.bak ~/.pymc2/mc2-assets/Data/Textures/64/Detail_10000.tga
```

### 3. HUD 面板 3 状态可复现
```bash
# 验证 3 张截图文件存在且非空
for status in pending completed failed; do
  img="docs/assets/uat_w1/objective_panel_${status}.png"
  if [[ -f "$img" && -s "$img" ]]; then
    echo "PASS: $img exists"
  else
    echo "FAIL: $img missing or empty"
  fi
done
```

---

## 自动化脚本（待创建）

由于 pymc2.main 当前不支持 `--screenshot` 参数和 Objective 注入，需要创建测试脚本：

### scripts/test_objective_hud.py
```python
"""W1 E2E 测试：HUD 目标面板截图生成器。"""
import argparse
import sys
from pathlib import Path

import pygame

from pymc2.domain.scripting.objective import (
    Objective,
    ObjectiveSet,
    ObjectiveStatus,
    ObjectiveType,
)
from pymc2.infrastructure.mc2_assets import load_mc2_assets
from pymc2.presentation.renderer import Renderer
from pymc2.services.battle_service import BattleService


def generate_test_objectives(status_scenario: str) -> ObjectiveSet:
    """生成测试 ObjectiveSet（3 PRIMARY + 2 SECONDARY）。"""
    if status_scenario == "pending":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.PENDING),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.PENDING),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.PENDING),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.PENDING),
        ]
    elif status_scenario == "completed":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.COMPLETED),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.COMPLETED),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.COMPLETED),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.COMPLETED),
        ]
    elif status_scenario == "failed":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.FAILED),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.PENDING),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.COMPLETED),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.FAILED),
        ]
    else:
        raise ValueError(f"Unknown status scenario: {status_scenario}")
    
    return ObjectiveSet(objectives=objectives)


def main():
    parser = argparse.ArgumentParser(description="W1 HUD 目标面板截图生成器")
    parser.add_argument("--status", choices=["pending", "completed", "failed"], required=True,
                        help="目标状态场景")
    parser.add_argument("--screenshot", type=Path, required=True,
                        help="截图保存路径")
    args = parser.parse_args()
    
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption(f"W1 E2E: Objective HUD ({args.status})")
    
    # 加载资产
    assets = load_mc2_assets(Path.home() / ".pymc2" / "mc2-assets")
    
    # 创建测试 ObjectiveSet
    objectives = generate_test_objectives(args.status)
    
    # 创建 Renderer（注入 objectives）
    renderer = Renderer(screen, asset_pack=assets, objectives=objectives)
    
    # 创建空 BattleService（HUD 面板不依赖战局状态）
    battle = BattleService()
    
    # 渲染一帧
    renderer.render(battle)
    pygame.display.flip()
    
    # 保存截图
    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(screen, str(args.screenshot))
    print(f"Screenshot saved: {args.screenshot}")
    
    pygame.quit()


if __name__ == "__main__":
    main()
```

**生成命令**：
```bash
cd /Users/lin/trae_projects/PyMC2
.venv/bin/python scripts/test_objective_hud.py --status pending --screenshot docs/assets/uat_w1/objective_panel_pending.png
.venv/bin/python scripts/test_objective_hud.py --status completed --screenshot docs/assets/uat_w1/objective_panel_completed.png
.venv/bin/python scripts/test_objective_hud.py --status failed --screenshot docs/assets/uat_w1/objective_panel_failed.png
```

---

## 验收结论模板

验收完成后填写：

| 项 | 状态 | 证据 |
|----|------|------|
| 截图 1 (baseline) | ☐ PASS / ☐ FAIL | 文件路径 + 文件大小 |
| 截图 2 (enabled) | ☐ PASS / ☐ FAIL | 文件路径 + 文件大小 |
| 截图 3 (pending) | ☐ PASS / ☐ FAIL | 文件路径 + 文件大小 |
| 截图 4 (completed) | ☐ PASS / ☐ FAIL | 文件路径 + 文件大小 |
| 截图 5 (failed) | ☐ PASS / ☐ FAIL | 文件路径 + 文件大小 |
| 视觉差异 >50% | ☐ PASS / ☐ FAIL | RMSE 值 / 百分比 |
| undefined.tga 回退 | ☐ PASS / ☐ FAIL | 执行日志无 crash |
| HUD 面板可见性 | ☐ PASS / ☐ FAIL | 人工目视确认 |

**W1-S3 验收签字**：________________  
**日期**：________________
