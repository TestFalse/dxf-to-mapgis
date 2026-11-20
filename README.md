# dxf-to-mapgis

一个 [WorkBuddy](https://www.workbuddy.cn) Skill：把 AutoCAD DXF 预处理为「Section 友好」格式，供 Section 完美转换为 MapGIS 6.7。

实测解决 8 类转换缺陷：要素缺失、分式横线丢失/文字上下分离、中文乱码、图层变 0 层、标注方向/颜色/线宽改变、标注端点多余圆点、多行文字散开、虚线变实线。

## 安装

将本仓库作为 Skill 安装到 WorkBuddy：

- 用户级：把本仓库内容放到 `~/.workbuddy/skills/dxf-to-mapgis/`
- 项目级：放到 `<项目>/.workbuddy/skills/dxf-to-mapgis/`

或下载 `dist/dxf-to-mapgis.zip`（若发布）解压到上述目录。

安装后，对 WorkBuddy 说「把这些 DXF 转换成 MapGIS 能用的」即可自动触发。

## 快速使用

```bash
# 安装依赖
pip install ezdxf
# 若安装受限，走国内镜像
pip install ezdxf -i https://pypi.tuna.tsinghua.edu.cn/simple

# 预处理（输出到 <源目录>/MapGIS转换用）
python scripts/dxf_prep_for_mapgis.py --src "<源DXF目录>"

# 自定义输出目录与虚线线型名
python scripts/dxf_prep_for_mapgis.py --src "<源DXF目录>" --out "<输出目录>" --dash-name 421302
```

参数：

| 参数 | 说明 |
|---|---|
| `--src` | 必填，含源 `.dxf` 的目录 |
| `--out` | 可选，默认 `<src>/MapGIS转换用` |
| `--dash-name` | 可选，虚线改名用的线型名，默认 `421302`（须为 Section `cad_map.lin` 中映射到 MapGIS 虚线代号 2 的名字） |

脚本会打印每个文件的处理统计，并在输出目录生成 `_处理报告.txt`。原文件不会被修改。

## 关于虚线（重要）

Section 转 DXF→MapGIS 时**不解析线型图案**，而是取实体的「线型名称」去对照表 `C:\mapgis67\program\section\cad_map.lin` 查 MapGIS 线型号（1=实线、2=虚线、3=点线）。原图的 `HIDDEN2`/`ACAD_ISO02W100` 等名字及随层继承查不到映射，就会变成实线。

本脚本不改几何，只把虚线实体的线型名统一改为对照表中已有的虚线名（`--dash-name`，默认 `421302`）。使用前请确认你自己的 `cad_map.lin` 里该名字确实映射到 `2`；参见 [`assets/cad_map.lin.example`](assets/cad_map.lin.example)。

备选方案：不改 DXF，而是在 `cad_map.lin` 追加 `HIDDEN2 2` 等映射（改前先备份）。

## 目录结构

```
dxf-to-mapgis/
├── SKILL.md                         # Skill 定义（WorkBuddy 用）
├── scripts/
│   └── dxf_prep_for_mapgis.py       # 预处理主脚本（CLI）
├── references/
│   └── processing_rules.md          # 各步骤根因与踩坑速查
└── assets/
    └── cad_map.lin.example          # Section 线型对照表示例
```

详见 [`references/processing_rules.md`](references/processing_rules.md) 了解每步处理的原因与调整方法。

## License

MIT
