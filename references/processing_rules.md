# DXF → MapGIS 预处理：问题根因与处理规则

本文件记录脚本 `dxf_prep_for_mapgis.py` 每一步背后的根因，供需要调整或排查时参考。
这些规则来自对多张工程图纸、多轮转换问题反馈的实测总结。

## 环境前提
- Python 库：ezdxf（实测 1.4.5）。pip 装不上时走国内镜像：
  `python -m pip install ezdxf -i https://pypi.tuna.tsinghua.edu.cn/simple`
- MapGIS 6.7 常装于 `C:\mapgis67`；Section 程序目录 `C:\mapgis67\program\section`。
- 源 DXF 多为 GBK/ANSI_936、AC1018。

## 核心机制：Section 如何转换线型
**Section 转 DXF→MapGIS 不解析 DXF 的线型图案（LTYPE pattern），而是取实体的「线型名称」（group 6）去对照表 `cad_map.lin` 查 MapGIS 线型号。**
- 对照表路径：`C:\mapgis67\program\section\cad_map.lin`
- 格式：`CAD线型名 空格 MapGIS线型号`（如 `421302  2`）
- MapGIS 线型代号：1=实线、2=虚线、3=点线（其他 4/5/6 为点划线等）
- 常见对照表只映射 `CONTINUOUS→1` 和少数代号（421302→2 虚线等），**没有** AutoCAD 的 `HIDDEN2`/`ACAD_ISO02W100`/`DASHED2` 等名字 → 查不到就按实线处理。
- 且原图虚线大多是「随层 BYLAYER」继承（实体自身无线型名）→ Section 更读不到。

处理：`explicit_linetype()` 不改几何，把虚线几何实体（LINE/ARC/CIRCLE）的线型名统一改为 `--dash-name`（默认 `421302`，对照表中映射到虚线2）。
- 随层实体先按图层解析出真实虚线名再判定；CONTINUOUS/BYLAYER 实线保持不动。
- 同时补一个同名线型表定义（复制 HIDDEN2 pattern，`setup_pattern`），保证 DXF 结构合法。
- **重要**：`--dash-name` 必须换成用户实际对照表里映射到虚线的名字；先读用户的 cad_map.lin 确认。
- 备选方案（不改 DXF）：在 cad_map.lin 追加 `HIDDEN2 2`、`ACAD_ISO02W100 2` 等映射（改前先备份）。

## 各处理步骤的根因

### 1. 复合实体爆破
Section 不识别 INSERT/DIMENSION/HATCH/SPLINE/LWPOLYLINE/POLYLINE/MTEXT 等复合对象 → 要素缺失。
多轮 `explode()` 递归爆成 LINE/ARC/CIRCLE/TEXT/POINT。SPLINE 用 `flattening(0.5)` 离散；HATCH 取边界 path 顶点连 LINE。

### 2. 图层保留
`explode(INSERT)` 后，块内处于 layer "0" 的子实体不会自动继承父 INSERT 图层 → 全跑到 0 层。
`set_layer_from_parent()`：layer 0/空/Defpoints 的子实体改为父图层。（AutoCAD 规则：块内 0 层继承插入层）

### 3. 旋转保留
- **不要手动叠加父 INSERT 旋转**：ezdxf 的 `explode()` 已自动把父 INSERT 的旋转/缩放/插入点应用到子实体，再叠加会翻倍（50°→100°）。
- DIMENSION 爆破出的文字方向写在 MTEXT 的 `text_direction` 向量里，`rotation` 属性缺失（取到 0）→ 文字不随标注线倾斜。`mtext_rotation()` 用 atan2(direction.y, direction.x) 取角度。
- TEXT 旋转规范化到 [0,360)：负角（如 -50°）在 MapGIS 中方向异常，%360 转正。

### 4. 分式文字
MapGIS 分式格式是 `/上/下/`。三类来源：
- MTEXT 堆叠 `\\S上/下;`：clean_format 用 STACK_RE 替换（尾注加空格分隔，避免 MapGIS 把尾注吞进分母）。
- `%%u`（下划线）：画在上排文字上方 → 与**下方**紧邻文字配对。
- `%%o`（上划线）：画在下排文字上方 → 与**上方**紧邻文字配对（例如：`元素含量` 上 + `%%o下限值` 下）。
- `merge_overline_fractions()`：几何配对（dx<3，dy 1~8，旋转差<5°）。关键约束：
  - 跳过空字符串 TEXT（控制点空属性 explode 出空 TEXT，距离更近会抢占配对 → 畸形分式 `/数值//`）
  - 跳过已成型分式（如 `/编号/数值/` 距离更近会把两个分式串成一个）
  - `%%u` 从下方配、`%%o` 从上方配，各自排除同类码避免重复
  - **不能在 explode 的 clean_format 里提前删 %%u/%%o**，否则 merge 无法识别

### 5. 中文 \\U+XXXX 转义
源 DXF 部分中文以 AutoCAD 转义存储（`\U+8BBE\U+8BA1` = "设计"），ezdxf 读出后原样保留，MapGIS 显示字面乱码。
`clean_format()` 开头用 `UNI_RE` + `chr(int(hex,16))` 解码。MTEXT→TEXT 与 TEXT 兜底都经 clean_format，全覆盖。

### 6. 尺寸标注颜色/线宽
`explode(DIMENSION)` 子实体 `color=0`(BYBLOCK)、`lineweight=-2`(BYBLOCK)，MapGIS 当黑色/默认线宽 → 线变黑、粗细变。
`fix_color_weight()`：color 0→256、lineweight -2→-1（BYLAYER，随图层色）。

### 7. 标注端点多余圆点
每个 DIMENSION 爆破带出 3 个 `Defpoints` 图层 POINT（标注定义点，CAD 中隐藏）。脚本改到父图层后 MapGIS 把 POINT 渲染成实心圆点 → 端点多绿点。
爆破时识别父为 DIMENSION 且子为 POINT → 丢弃。原图真实 POINT（控制点等）保留。

### 8. 多行 MTEXT 对齐
MTEXT 的 `attachment_point`（1左上/2上中/3右上/4左中/5正中/6右中/7左下/8下中/9右下）被忽略、一律按 TEXT 左下角放 → 文字错位/多行散开。
`mtext_to_text()`：水平组 `(attach-1)%3`、垂直组 `(attach-1)//3` 还原对齐；行距 hp*1.0。
**R12 不持久化 group 72/73**：`set_placement(align=)` 与直接设 `dxf.halign` 都无效 → 右/居中对齐用字宽估算（`_text_width_est`：中文/全角≈1.0h，ASCII≈0.6h）做几何补偿 `x = insert.x - width`。

### 9. 实心/空心钻孔区分 + 圆圈变虚线
见矿/未见矿钻孔块各自只有两个同心 CIRCLE（半径1、2），**唯一差别是见矿块多一个 SOLID 填充 HATCH（`solid_fill=1`，ArcEdge 0→360 整圆，与内圆重合）**。原脚本 `hatch_to_lines()` 只处理有 `vertices` 的路径，ArcEdge 没有 `virtual_entities()` → 异常被吞，填充圆完全丢失 → MapGIS 里两者都是空心双圆，分不出。
处理：
- `hatch_to_lines()` 支持 EdgePath：`ArcEdge` 整圆(0→360) 生成闭合 CIRCLE，非整圆生成 ARC，`LineEdge` 生成 LINE。**实心 HATCH 的边界圆放到独立图层 `见矿实心区`**，未见矿钻孔无此层圆 → Section 里按该图层造区填充即得实心，一眼区分（用户选「保留区文件填充」方案）。
- 坑：`ArcEdge.center` / `LineEdge.start_point` 是 **Vec2**，没有 `.z`，须用 `getattr(v,'z',0.0)` 兜底，否则 add_circle 抛异常被吞、圆丢失。
- 圆圈变虚线：explode 后实体线型常为 `None`（ByLayer 未展开写不出），Section 读不到就按默认渲染成虚线。清理循环里给 LINE/ARC/CIRCLE/POINT 显式写 `CONTINUOUS`（已是虚线 421302 的保持不动）。

### 10. 飞线（与主体不同版面）
源图部分块 INSERT 用负缩放 `xscale=-2`（镜像），ezdxf `explode()` 有 bug：把圆心 x 从 `+549271` 错算成 `-549271`（直接对 insert.x 取负、漏平移），圆飞到负坐标、与主体分离。
处理：explode 前若 `xscale<0`，临时改为 `abs(xscale)` 再 explode，之后对子实体施加 `x' = 2*insert.x - x` 镜像（CIRCLE/ARC center、POINT location、LINE 两端、TEXT insert、HATCH edge.center）。勿用 `Matrix44.chain`（顺序易错，直接改坐标值）。

### 11. 区文件（MapGIS .wp）
R12/AC1009 规格不含 HATCH 实体 → Section 从 R12 DXF 读不到填充 → 无区文件。Section 自带示例 `dxf2004.dxf` 为 AC1018，支持 HATCH→区。
处理：`--dxf-version R2004` 输出 AC1018 并保留 HATCH（`keep_hatch=True`，explode 时跳过 HATCH 拆解、KEEP 集合加入 HATCH），供 Section 生成区文件。可与 R12 版本分别输出到两个目录，点/线用 R12、区用 R2004。

## 关键坑速查
- 新文档必须 `doc.encoding='gbk'`（只设 header 的 `$DWGCODEPAGE` 无效），否则中文乱码。
- `add_text` 无 `location/insert` 参数，用 `te.set_placement()`。
- R12 新文档线型表无 HIDDEN2，但 `Importer` 导入后会自动带入源的全部线型定义。
- `setup_pattern(pattern, length)`（不是 set_pattern）。
- 删除的实体 `.dxf` 属性失效，遍历列表须 `hasattr(e,'dxf')` 保护。
- 分母含斜杠如 `编号/子编号(序号)` 是合法复合编号，不是串接，勿误判。
