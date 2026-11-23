---
name: dxf-to-mapgis
description: Preprocess AutoCAD DXF files so Section converts them to MapGIS 6.7 with no missing features, correct fraction text, preserved layers/rotations/colors, and visible dashed lines. Use when a user has DXF drawings to convert to MapGIS via Section, or reports that conversion drops entities, mangles text/fractions, changes layer/color/lineweight, or turns dashed lines into solid lines.
agent_created: true
---

# DXF → MapGIS 预处理

## Overview

Section 将 DXF 转入 MapGIS 6.7 时会丢复合实体、破坏分式文字、改图层/方向/颜色，并把虚线变成实线。本 Skill 把 DXF 预处理为「Section 友好」格式：递归爆破为图元、还原分式与中文、保留图层/旋转/颜色、让虚线可被 Section 正确映射。输出 R12(AC1009)+GBK 到独立子目录，原文件不动。

## When to use

- 用户要把一张或多张 DXF 用 Section 转 MapGIS。
- 用户反馈转换后：要素缺失、分式横线丢失/文字上下分离、中文乱码、图层变 0 层、标注方向/颜色/线宽变了、端点多圆点、虚线变实线。

## Workflow

### Step 1 — Inspect and confirm the linetype mapping (critical for dashed lines)

Section **does not parse DXF linetype patterns**. It reads each entity's linetype **name** and looks it up in `C:\mapgis67\program\section\cad_map.lin` to get a MapGIS linetype number (1=solid, 2=dashed, 3=dot). Names absent from that table fall back to solid.

- Read the user's actual `cad_map.lin` and find a name mapped to `2` (dashed). In the reference project this was `421302`.
- Pass that name via `--dash-name`. If unknown, default `421302` but warn the user to verify against their table.
- An example table is in `assets/cad_map.lin.example`.

### Step 2 — Run the preprocessing script

```bash
python scripts/dxf_prep_for_mapgis.py --src "<源DXF目录>" [--out "<输出目录>"] [--dash-name 421302] [--dxf-version R12|R2004]
```

- `--src` (required): directory containing source `.dxf` files.
- `--out` (optional): defaults to `<src>/MapGIS转换用`.
- `--dxf-version` (optional): `R12` (default, AC1009) — best for point/line conversion; `R2004` (AC1018) — keeps HATCH entities so Section can generate MapGIS region (.wp) files. Run twice to two output dirs if you need both point/line and region output.
- Requires `ezdxf` (`pip install ezdxf`; if blocked, use `-i https://pypi.tuna.tsinghua.edu.cn/simple`).
- The script prints per-file stats and writes `_处理报告.txt` to the output directory.

### Step 3 — Verify the output

Load a produced DXF with ezdxf and confirm:
- DXF version `AC1009`, encoding `gbk`.
- No composite entity types remain (INSERT/DIMENSION/MTEXT/SPLINE/HATCH/LWPOLYLINE/POLYLINE).
- Dashed entities carry the `--dash-name` linetype; the linetype table contains it; old AutoCAD names (HIDDEN2/ACAD_ISO02W100 etc.) are gone.
- No `\U+XXXX` escapes, no `%%u`/`%%o` control codes, no garbled characters in TEXT.
- Fractions are `/上/下/` format; layers preserved (layer "0" count unchanged).

### Step 4 — Hand off

Tell the user to open Section → 文件\新建文件 → 辅助工具\打开外部数据\读取DXF文件, select the output DXF, then save point/line/region files. Dashed lines should now appear dashed.

## Customization

- Different dashed linetype name: use `--dash-name` (must exist in the user's cad_map.lin as a dashed mapping).
- Preserve AutoCAD native linetype names instead: append `HIDDEN2 2` etc. to cad_map.lin (back it up first) and skip the rename — see `references/processing_rules.md`.
- Any structural tweak must keep the ordering: explode → fraction merge → linetype rename → cleanup → normalize rotation → save.

## Resources

### scripts/
- `dxf_prep_for_mapgis.py` — the full preprocessing pipeline (CLI). Run directly; it does not need to be read into context unless patching.

### references/
- `processing_rules.md` — root cause of every processing step and a pitfall cheat-sheet. Load when adjusting the script or diagnosing a new conversion defect.

### assets/
- `cad_map.lin.example` — sample Section linetype mapping table showing solid/dashed mappings and both linetype strategies.
