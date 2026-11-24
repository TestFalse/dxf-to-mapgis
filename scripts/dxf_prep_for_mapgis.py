# -*- coding: utf-8 -*-
"""
DXF 预处理 -> 供 Section 完美转换为 MapGIS  (v2)

v2 改进（相对 v1）：
  - 保留图层：INSERT explode 时块内 layer "0" 继承父 INSERT 图层；其余图层原样保留。
  - 保留旋转：TEXT 旋转原样保留；MTEXT 多行按原文字方向排布。
  - 划线分式：%%u上(下划线配下方) / %%o下(上划线配上方) -> MapGIS 分式 /上/下/，
    消除 MapGIS 中显示的 "%/u / %/o" 字面量并恢复“文字在横线上下”。
  - 输出 R12 (AC1009)：兼容性更好，转换效果更佳。
  - 全部复合实体爆破为 LINE/ARC/CIRCLE/TEXT/POINT，Section 不丢要素。
  - MTEXT 堆叠 \\S上/下; -> /上/下/。
  - 虚线保留：Section 靠线型名查 cad_map.lin（CAD名->MapGIS线型号），原图的
    HIDDEN2/ACAD_ISO02W100 等及随层虚线查不到映射会变实线。这里不改几何，
    只把虚线实体线型名统一改为对照表已有的 421302（->MapGIS 线型2 虚线）。

保留原文件，输出到「MapGIS转换用」子目录。
"""
import os, re, logging, argparse
from collections import Counter
import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Vec3, Matrix44

logging.getLogger("ezdxf").setLevel(logging.ERROR)

SRC_DIR = r"C:\Users\TC\Desktop\铜山岭资料更新\爆破"
OUT_DIR = os.path.join(SRC_DIR, "MapGIS转换用")

STACK_RE = re.compile(r"\\S([^;]*?)/([^;]*?);")
FRAC_RE = re.compile(r"/[^/]+/[^/]+/")
UU_RE = re.compile(r"%%[uU]")   # 下划线（上排文字，配下方）
OO_RE = re.compile(r"%%[oO]")   # 上划线（下排文字，配上方）——钻孔样品标注横线
UNI_RE = re.compile(r"\\[uU]\+([0-9A-Fa-f]{4,6})")   # AutoCAD \U+XXXX Unicode 转义（中文）
# 罗马数字：Section 既把 GBK 的 Ⅱ 误渲染成两个 Ⅰ，也不认 \U+2161 转义（显示字面），
# 统一改写成 ASCII 大写字母（Ⅱ→II），保证任何转换器都能正确显示。
ROMAN_MAP = dict(zip("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ",
                     ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII"]))

EXPLODE_TYPES = {
    "INSERT", "DIMENSION", "ARC_DIMENSION", "LWPOLYLINE", "POLYLINE",
    "MULTILEADER", "LEADER", "MTEXT", "SPLINE", "HATCH",
}
DROP_TYPES = {
    "VIEWPORT", "OLE2FRAME", "IMAGE", "WIPEOUT", "TABLESTYLE",
    "VERTEX", "SEQEND", "ATTRIB", "ATTDEF",
}
KEEP = {"LINE", "ARC", "CIRCLE", "TEXT", "POINT", "SOLID", "3DFACE", "TRACE"}


def clean_format(text):
    s = text or ""
    # AutoCAD \U+XXXX Unicode 转义 -> 真实字符（如 \U+8BBE -> 设），MapGIS 不认该转义
    s = UNI_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    def repl(m):
        return f"/{m.group(1).strip()}/{m.group(2).strip()}/ "
    s = STACK_RE.sub(repl, s)
    s = re.sub(r"\\[A-Za-z][^;\\]*;", "", s)
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"%%[cC]", "Φ", s)
    s = re.sub(r"%%[dD]", "°", s)
    s = re.sub(r"%%[pP]", "±", s)
    # 注意：%%u/%%o 划线码不能在此删除，须留给 merge_overline_fractions 配对成 /上/下/
    # 罗马数字 -> ASCII 字母：Section 不认 \U+ 转义（显示字面），改 II/III 最稳
    for ch, asc in ROMAN_MAP.items():
        s = s.replace(ch, asc)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def set_layer_from_parent(e, parent_layer):
    """块内 layer '0' 继承父 INSERT 图层（AutoCAD 规则）"""
    if e.dxf.get("layer", "0") in ("0", "", "Defpoints"):
        try:
            e.dxf.layer = parent_layer
        except Exception:
            pass


def mtext_rotation(e):
    """取 MTEXT 方向角：优先 text_direction 向量，其次 rotation 属性。
    ezdxf explode(DIMENSION) 生成的 MTEXT 方向写在 text_direction 里。"""
    import math
    try:
        d = e.dxf.get("text_direction")
        if d is not None and (abs(d.x) > 1e-9 or abs(d.y) > 1e-9):
            return math.degrees(math.atan2(d.y, d.x)) % 360
    except Exception:
        pass
    return e.dxf.get("rotation", 0.0) or 0.0


def fix_color_weight(e):
    """把 BYBLOCK(color=0/lineweight=-2) 改为 BYLAYER，避免 MapGIS 变黑/线宽错乱"""
    try:
        if e.dxf.get("color", 256) == 0:
            e.dxf.color = 256
    except Exception:
        pass
    try:
        if e.dxf.get("lineweight", -1) == -2:
            e.dxf.lineweight = -1
    except Exception:
        pass


def _text_width_est(text, height):
    """估算文字宽度用于右/居中对齐补偿：中文/全角≈1.0h，ASCII≈0.6h。"""
    w = 0.0
    for ch in text:
        w += height * (1.0 if ord(ch) > 127 else 0.6)
    return w


def mtext_to_text(msp, e):
    """MTEXT -> 多个 TEXT。关键：按 attachment_point 还原对齐，否则文字错位/多行散开。"""
    raw = e.dxf.text
    parts = [p for p in re.split(r"\\P|\n", raw)]
    hp = e.dxf.char_height
    ins = Vec3(e.dxf.insert)
    rot = mtext_rotation(e)          # 关键：保留尺寸标注文字的旋转方向
    layer = e.dxf.layer
    attach = e.dxf.get("attachment_point", 1) or 1
    # MTextEntityAlignment: 1左上 2上中 3右上 4左中 5正中 6右中 7左下 8下中 9右下
    h_group = (attach - 1) % 3        # 0左 1中 2右
    v_group = (attach - 1) // 3       # 0顶 1中 2底
    valid = [p for p in parts if clean_format(p)]
    n = max(len(valid), 1)
    line_gap = hp * 1.0               # AutoCAD MTEXT 默认行距≈1倍字高
    # 首行基线 y：顶=ins.y；中=ins.y+(n-1)*gap/2；底=ins.y+(n-1)*gap
    if v_group == 0:
        y0 = ins.y
    elif v_group == 1:
        y0 = ins.y + (n - 1) * line_gap / 2.0
    else:
        y0 = ins.y + (n - 1) * line_gap
    added = 0
    for part in parts:
        txt = clean_format(part)
        if not txt:
            continue
        y = y0 - added * line_gap
        # R12 不持久化 group 72/73，无法用 TEXT 对齐点；用几何补偿模拟右/居中对齐
        x = ins.x
        if h_group:
            w = _text_width_est(txt, hp)
            x = ins.x - w if h_group == 2 else ins.x - w / 2.0
        te = msp.add_text(txt, height=hp, rotation=rot)
        te.set_placement((x, y, ins.z))
        te.dxf.layer = layer
        try:
            te.dxf.color = e.dxf.color
        except Exception:
            pass
        fix_color_weight(te)
        added += 1
    msp.delete_entity(e)
    return added


def spline_to_lines(msp, e):
    pts = list(e.flattening(0.5))
    layer = e.dxf.layer
    for a, b in zip(pts, pts[1:]):
        ln = msp.add_line((a.x, a.y, a.z), (b.x, b.y, b.z))
        ln.dxf.layer = layer
    msp.delete_entity(e)


def hatch_to_lines(msp, e):
    """HATCH -> 边界图元。关键：实心填充圆(EdgePath/ArcEdge)必须还原为
    闭合 CIRCLE，Section 才能造区填充；否则见矿/未见矿钻孔都只剩同心圆，
    实心/空心无法区分。"""
    layer = e.dxf.layer
    color = e.dxf.get("color", 256)
    # 实心 HATCH(SOLID) 的闭合边界放到独立图层，供 Section 造区填充 ->
    # 区分「见矿(实心)」与「未见矿(空心)」钻孔；两者原有同心圆完全相同。
    try:
        solid_fill = int(e.dxf.get("solid_fill", 0))
    except Exception:
        solid_fill = 0
    fill_layer = "见矿实心区" if (solid_fill and
                              str(e.dxf.get("pattern_name", "")).upper() == "SOLID") else layer
    for path in e.paths:
        # EdgePath: 圆弧/直线边界（实心圆多为 ArcEdge 0->360）
        edges = getattr(path, "edges", None)
        if edges:
            for edge in edges:
                et = type(edge).__name__
                try:
                    if et == "ArcEdge":
                        c = edge.center
                        cz = getattr(c, "z", 0.0)  # ArcEdge.center 是 Vec2，无 z
                        a0, a1 = edge.start_angle, edge.end_angle
                        if abs((a1 - a0) % 360.0) < 1e-6:
                            cire = msp.add_circle((c.x, c.y, cz), edge.radius)
                            cire.dxf.layer = fill_layer
                            cire.dxf.color = color
                        else:
                            arc = msp.add_arc((c.x, c.y, cz), edge.radius, a0, a1)
                            arc.dxf.layer = fill_layer
                            arc.dxf.color = color
                    elif et == "LineEdge":
                        s, t = edge.start_point, edge.end_point
                        ln = msp.add_line((s.x, s.y, getattr(s, "z", 0.0)),
                                          (t.x, t.y, getattr(t, "z", 0.0)))
                        ln.dxf.layer = fill_layer
                        ln.dxf.color = color
                    else:
                        # 其他边（Spline/Ellipse）尝试通用接口
                        subs = getattr(edge, "virtual_entities", None)
                        if callable(subs):
                            for sub in subs():
                                sub.dxf.layer = fill_layer
                                msp.add_entity(sub)
                except Exception:
                    pass
            continue
        # PolylinePath / 顶点路径
        vs = getattr(path, "vertices", None)
        if vs is not None:
            vs = list(vs)
            for a, b in zip(vs, vs[1:]):
                ln = msp.add_line((a[0], a[1]), (b[0], b[1]))
                ln.dxf.layer = fill_layer
                ln.dxf.color = color
    msp.delete_entity(e)


def explode_all_keep_layer(msp, max_rounds=30, keep_hatch=False):
    """多轮爆破，INSERT 子实体继承父图层；返回新增 TEXT 行数。
    keep_hatch=True 时保留 HATCH 实体（R2004 供 Section 生成区文件）。"""
    n_text = 0
    for _ in range(max_rounds):
        changed = False
        for e in list(msp):
            t = e.dxftype()
            if t in DROP_TYPES:
                msp.delete_entity(e)
                changed = True
            elif t == "MTEXT":
                n_text += mtext_to_text(msp, e)
                changed = True
            elif t == "SPLINE":
                spline_to_lines(msp, e)
                changed = True
            elif t == "HATCH":
                if keep_hatch:
                    # R2004 保留 HATCH，Section 才能生成区文件；只继承父图层
                    continue
                hatch_to_lines(msp, e)
                changed = True
            elif t in EXPLODE_TYPES:
                parent_layer = e.dxf.get("layer", "0")
                is_dim = t in ("DIMENSION", "ARC_DIMENSION")
                try:
                    # ezdxf explode() 对负缩放(xscale<0)的 INSERT 有 bug：圆心 x 被
                    # 错算成 -insert.x（飞到负坐标，与主体不同版面）。临时改为正缩放
                    # 再 explode，之后对所有子实体施加 x 镜像修正回正确位置。
                    mirror_x = False
                    ins_x = 0.0
                    if t == "INSERT":
                        xs = e.dxf.get("xscale", 1.0)
                        if xs is not None and xs < 0:
                            mirror_x = True
                            ins_x = e.dxf.insert.x
                            e.dxf.xscale = abs(xs)
                    subs = e.explode()
                    # ezdxf 的 explode 已自动应用父 INSERT 的旋转/缩放/插入点，
                    # 不要再手动叠加，否则旋转会翻倍。
                    if mirror_x:
                        # 关于 x=ins_x 镜像：x' = 2*ins_x - x（把负坐标翻回图幅）
                        # 不用 Matrix44.chain（易顺序错），直接改关键点坐标。
                        def mir(v):
                            return Vec3(2 * ins_x - v.x, v.y, getattr(v, "z", 0.0))
                        for s in subs:
                            st = s.dxftype()
                            try:
                                if st in ("CIRCLE", "ARC"):
                                    s.dxf.center = mir(Vec3(s.dxf.center))
                                elif st == "POINT":
                                    s.dxf.location = mir(Vec3(s.dxf.location))
                                elif st == "LINE":
                                    s.dxf.start = mir(Vec3(s.dxf.start))
                                    s.dxf.end = mir(Vec3(s.dxf.end))
                                elif st in ("TEXT", "MTEXT", "ATTDEF"):
                                    s.dxf.insert = mir(Vec3(s.dxf.insert))
                                elif st == "HATCH":
                                    for path in s.paths:
                                        for ed in getattr(path, "edges", []) or []:
                                            if hasattr(ed, "center"):
                                                c = ed.center
                                                c.x = 2 * ins_x - c.x
                            except Exception:
                                pass
                    for s in subs:
                        # 尺寸标注爆破会带出 Defpoints 图层的 POINT（标注定义点），
                        # MapGIS 会把 POINT 渲染成实心圆点，导致标注端点多出绿点——丢弃。
                        if is_dim and s.dxftype() == "POINT":
                            try:
                                msp.delete_entity(s)
                            except Exception:
                                pass
                            continue
                        set_layer_from_parent(s, parent_layer)
                        fix_color_weight(s)   # BYBLOCK -> BYLAYER，修正颜色/线宽
                    changed = True
                except Exception:
                    msp.delete_entity(e)
                    changed = True
            elif t == "TEXT":
                raw = e.dxf.text
                cleaned = clean_format(raw)
                if cleaned != raw:
                    e.dxf.text = cleaned
        if not changed:
            break
    return n_text


def merge_overline_fractions(msp, tol_x=3.0, dy_min=1.0, dy_max=8.0):
    """把带 AutoCAD 划线控制码的 TEXT 与紧邻文字合并为 MapGIS 分式 /上/下/。
      - %%u 下划线：标在上排文字 -> 从下方找配对（上=本，下=配）
      - %%o 上划线：标在下排文字 -> 从上方找配对（上=配，下=本）
    返回合并数。"""
    texts = [e for e in msp if e.dxftype() == "TEXT"]
    used = set()
    merged = 0

    def find_pair(base, direction, exclude_re):
        """direction=-1 找下方(%%u)，+1 找上方(%%o)；exclude_re=同类划线码，避免重复配对"""
        bx, by = base.dxf.insert[0], base.dxf.insert[1]
        br = base.dxf.get("rotation", 0.0)
        cand = None
        best = None
        for t in texts:
            if t is base or id(t) in used or not hasattr(t, "dxf"):
                continue
            ttxt = t.dxf.text.strip()
            # 跳过空文字（控制点空属性 explode 出空 TEXT，会抢占配对）
            if not ttxt:
                continue
            # 跳过已成型分式（如 /5ca1/316.140/，避免把两个分式串成一个）
            if FRAC_RE.search(ttxt):
                continue
            if exclude_re.search(t.dxf.text):
                continue
            dx = t.dxf.insert[0] - bx
            dy = (t.dxf.insert[1] - by) * direction
            if abs(dx) > tol_x or not (dy_min < dy < dy_max):
                continue
            if abs(t.dxf.get("rotation", 0.0) - br) > 5:
                continue
            d = dx * dx + dy * dy
            if best is None or d < best:
                best = d
                cand = t
        return cand

    # 先处理 %%u（下划线=上排）
    for u in [e for e in texts if hasattr(e, "dxf") and UU_RE.search(e.dxf.text)]:
        if id(u) in used:
            continue
        cand = find_pair(u, -1, UU_RE)
        if cand is None:
            u.dxf.text = UU_RE.sub("", u.dxf.text).strip()
            used.add(id(u))
            continue
        up = UU_RE.sub("", u.dxf.text).strip()
        dn = clean_format(cand.dxf.text).strip()
        u.dxf.text = f"/{up}/{dn}/"
        used.add(id(u))
        used.add(id(cand))
        msp.delete_entity(cand)
        merged += 1

    # 再处理 %%o（上划线=下排，钻孔样品标注）
    for o in [e for e in texts if hasattr(e, "dxf") and OO_RE.search(e.dxf.text) and id(e) not in used]:
        if id(o) in used or not hasattr(o, "dxf"):
            continue
        cand = find_pair(o, +1, OO_RE)
        if cand is None:
            o.dxf.text = OO_RE.sub("", o.dxf.text).strip()
            used.add(id(o))
            continue
        up = clean_format(cand.dxf.text).strip()
        dn = OO_RE.sub("", o.dxf.text).strip()
        o.dxf.text = f"/{up}/{dn}/"
        used.add(id(o))
        used.add(id(cand))
        msp.delete_entity(cand)
        merged += 1
    return merged


# Section 的线型对照表 cad_map.lin 中：421302 -> MapGIS 线型 2（虚线），
# CONTINUOUS -> 1（实线）。把 DXF 中的虚线统一改成对照表已有的名字，
# Section 即可直接映射为虚线，无需用户改动任何系统配置、也不改任何几何。
SECTION_DASH_NAME = "421302"


SOLID_LAYER_KEYS = ("采场", "巷道")   # 这些图层的虚线在 MapGIS 中间距变大像断线，改实线


def explicit_linetype(msp, doc):
    """把所有虚线几何实体的线型统一改为 Section 对照表可识别的虚线名。

    背景：Section 转 MapGIS 不用 DXF 的线型图案，而是取实体「线型名称」去
    cad_map.lin 查 MapGIS 线型号。用户的对照表只有 CONTINUOUS->1(实线) 和
    421302/421202->2(虚线) 等，没有 HIDDEN2/ACAD_ISO02W100 这些 AutoCAD 线型名，
    所以虚线全部变实线。这里：
      1) 随层(BYLAYER)实体 -> 先按图层解析出它真实的虚线线型名；
      2) 任何非实线的虚线（显式或随层）-> 统一改名为 SECTION_DASH_NAME(421302)。
    不改变任何几何，只改实体 group 6 线型名。
    """
    layer_lt = {}
    for ly in doc.layers:
        try:
            lname = ly.dxf.name
            lt = ly.dxf.get("linetype", "CONTINUOUS")
            layer_lt[lname] = lt
            # 采场/巷道图层：Section 会按「图层线型」渲染（不只实体线型），
            # 图层仍是虚线名则实体改成 CONTINUOUS 也会显示成断续短线 → 同步改图层定义。
            if any(k in lname for k in SOLID_LAYER_KEYS) and str(lt).upper() not in (
                    "CONTINUOUS", "BYLAYER", "BYBLOCK", ""):
                ly.dxf.linetype = "CONTINUOUS"
                layer_lt[lname] = "CONTINUOUS"
        except Exception:
            pass

    def is_dash(name):
        return name and str(name).upper() not in (
            "CONTINUOUS", "BYLAYER", "BYBLOCK", "",
        )

    n = 0
    for e in msp:
        if e.dxftype() not in ("LINE", "ARC", "CIRCLE"):
            continue
        try:
            lt = e.dxf.get("linetype", "BYLAYER")
        except Exception:
            continue
        if lt is None:
            lt = "BYLAYER"
        upper = str(lt).upper()
        if upper in ("BYLAYER", "BYBLOCK"):
            lay = e.dxf.get("layer", "0")
            lt = layer_lt.get(lay, "CONTINUOUS")
        if is_dash(lt):
            try:
                lay = e.dxf.get("layer", "0") or ""
                # 采场/巷道图层：Section 虚线2 间距过大看着像断线，改实线
                if any(k in lay for k in SOLID_LAYER_KEYS):
                    e.dxf.linetype = "CONTINUOUS"
                else:
                    e.dxf.linetype = SECTION_DASH_NAME
                n += 1
            except Exception:
                pass
    return n


def process_file(src_path, dst_path, version="R12", keep_hatch=False):
    src = ezdxf.readfile(src_path)
    doc = ezdxf.new(version, setup=True)   # R12=AC1009 / R2004=AC1018
    doc.encoding = "gbk"
    imp = Importer(src, doc)
    imp.import_modelspace()
    imp.finalize()
    msp = doc.modelspace()

    n_text = explode_all_keep_layer(msp, keep_hatch=keep_hatch)
    n_uu = merge_overline_fractions(msp)
    n_lt = explicit_linetype(msp, doc)   # 虚线 -> Section 对照表可识别的 421302（不改几何）
    if n_lt and SECTION_DASH_NAME not in doc.linetypes:
        # 补一个同名线型表定义（虚线 pattern），保证 dxf 结构合法、绘图软件不报错
        try:
            src_lt = None
            for nm in ("HIDDEN2", "ACAD_ISO02W100", "DASHED2"):
                if nm in doc.linetypes:
                    src_lt = doc.linetypes.get(nm)
                    break
            new_lt = doc.linetypes.new(SECTION_DASH_NAME, dxfattribs={
                "description": "Dashed for Section cad_map.lin -> MapGIS linetype 2",
            })
            pat = ([t.value for t in src_lt.pattern_tags.tags if t.code == 49]
                   if src_lt is not None else [3.0, -1.5])
            new_lt.setup_pattern(pat, length=sum(abs(p) for p in pat))
        except Exception:
            pass

    keep_types = KEEP | ({"HATCH"} if keep_hatch else set())
    for e in list(msp):
        if e.dxftype() not in keep_types:
            msp.delete_entity(e)
        else:
            fix_color_weight(e)   # 全量兜底：所有基础图元 BYBLOCK -> BYLAYER
            # 显式写线型：explode 后线型常为 None(ByLayer 未展开)，Section 读取时
            # 会按默认渲染，钻孔圆圈被误显示成虚线。实线统一写 CONTINUOUS；
            # 虚线(421302)保持不动。
            if e.dxftype() in ("LINE", "ARC", "CIRCLE", "POINT"):
                try:
                    lt = e.dxf.get("linetype", None)
                    if lt is None or str(lt).upper() in ("BYLAYER", "BYBLOCK"):
                        e.dxf.linetype = "CONTINUOUS"
                except Exception:
                    pass

    # R2004 模式保留源图 lineweight（用户选择保留线粗细），仅 R12 因
    # 规格不支持线宽而自动丢失。HATCH 保留供 Section 生成区文件。

    # 旋转规范化到 [0,360)：避免 -50 这类负值在 MapGIS 中方向异常
    for e in msp:
        if e.dxftype() == "TEXT":
            try:
                r = e.dxf.get("rotation", 0.0)
                if r is not None and (r < 0 or r >= 360):
                    e.dxf.rotation = r % 360
            except Exception:
                pass

    doc.saveas(dst_path, encoding="gbk")

    cnt = Counter(e.dxftype() for e in msp)
    frac = sum(1 for e in msp if e.dxftype() == "TEXT" and FRAC_RE.search(e.dxf.text))
    l0 = sum(1 for e in msp if e.dxf.get("layer", "0") == "0")
    return len(list(src.modelspace())), len(list(msp)), dict(cnt), frac, n_text, n_uu, l0


def main():
    global SRC_DIR, OUT_DIR
    ap = argparse.ArgumentParser(description="DXF 预处理 -> 供 Section 转 MapGIS")
    ap.add_argument("--src", default=SRC_DIR, help="源 DXF 目录")
    ap.add_argument("--out", default=None, help="输出目录（默认 <src>/MapGIS转换用）")
    ap.add_argument("--dxf-version", default="R12", choices=["R12", "R2004"],
                    help="输出版本：R12(AC1009，默认，线/点转换最佳) 或 R2004(AC1018，保留 HATCH 供生成区文件)")
    ap.add_argument("--dash-name", default="421302", help="虚线改名用的线型名")
    ap.add_argument("--both", action="store_true",
                    help="一次生成 R12 和 R2004 两版（输出到 <out> 和 <out>_R2004）")
    args = ap.parse_args()
    SRC_DIR = os.path.abspath(args.src)
    OUT_DIR = os.path.abspath(args.out or os.path.join(SRC_DIR, "MapGIS转换用"))
    global SECTION_DASH_NAME
    SECTION_DASH_NAME = args.dash_name
    version = args.dxf_version
    keep_hatch = (version == "R2004")

    # --both: run two passes (R12 + R2004) into <out> and <out>_R2004
    versions = [("R12", OUT_DIR, False), ("R2004", OUT_DIR + "_R2004", True)] \
        if args.both else [(version, OUT_DIR, keep_hatch)]

    report = []
    for ver, out_dir, kh in versions:
        os.makedirs(out_dir, exist_ok=True)
        files = sorted(f for f in os.listdir(SRC_DIR)
                       if f.lower().endswith(".dxf") and
                       os.path.abspath(os.path.join(SRC_DIR, f)) != out_dir)
        for f in files:
            try:
                report.append((f"[{ver}] {f}",) + process_file(
                    os.path.join(SRC_DIR, f), os.path.join(out_dir, f),
                    version=ver, keep_hatch=kh) + ("OK",))
            except Exception as ex:
                report.append((f"[{ver}] {f}", 0, 0, {}, 0, 0, 0, 0, "ERR:" + repr(ex)))
    print("文件 | 原始→输出 | 分式 | 文本行 | 划线合并 | layer0 | 结果")
    for r in report:
        f, a, b, cnt, frac, ntext, nuu, l0, st = r
        print(f"\n{f}\n  {a}→{b} | 分式={frac} | 文本行={ntext} | 划线合并={nuu} | layer0={l0} | {st}")
        print("  图元:", dict(sorted(cnt.items(), key=lambda x: -x[1])))
    with open(os.path.join(OUT_DIR, "_处理报告.txt"), "w", encoding="utf-8") as fh:
        fh.write("DXF 预处理报告 v2（爆破 + 分式 + 图层/旋转保留 + R12）\n\n")
        for r in report:
            f, a, b, cnt, frac, ntext, nuu, l0, st = r
            fh.write(f"{f}\n  原始 {a} → 输出 {b} | 分式 {frac} | 文本行 {ntext} | 划线合并 {nuu} | layer0 {l0} | {st}\n  图元 {cnt}\n\n")


if __name__ == "__main__":
    main()
