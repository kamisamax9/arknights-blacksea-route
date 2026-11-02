#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_scenario.py — 截图 → scenario.json，**中间不经过人手**

为什么要有这个
--------------
2026-10-27 三次事故的共同点，都不是「读错」：

1. 把 `label_ocr` 的输出**手抄**进表格时，`(1,2)` 抄成了 `(1,1)`
   → 占掉了原来 `(1,1)` 的凶戾 → 凶戾被挤到 `(0,1)` → 还漏了两个定点
2. 表格和同一段正文**自相矛盾**（正文写 (1,2)，表格写 (1,1)），但没有任何检查会发现
3. 抄边表时漏了 1 条（19 → 18），据此断言「(2,2) 是孤点」

**只要「识别结果」到「求解输入」之间有一次人工转录，就一定会有这类错误。**
本工具把这一步整段删掉：读图、覆盖、查边表、定价、建 json，一条命令完成。

用法
----
  python tools/build_scenario.py 截图 --layer 1 --config 3 --ap 6
  python tools/build_scenario.py 截图 --layer 2 --config 1 --ap 5 --grants "2,1:1"
  python tools/build_scenario.py 截图 --layer 4 --config L6 --ap 7 --items "标准引擎:2;气垫底座:1"

输出
----
默认写到 `runs/L{层}-c{构型}-{HHMM}.json`（符合文件纪律的命名）。
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import label_ocr as LO          # noqa: E402
import check_configs as CC      # noqa: E402
import apply_config as AC       # noqa: E402
import route_solver as RS       # noqa: E402
import rules as R               # noqa: E402

REF = os.path.join(HERE, "..", "references")
RUNS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "runs"))

# 每层的定价（口径见 references/03-节点收益.md）——**数值真源在 tools/rules.py**
# 2026-10-28 审计：这里原先自己抄了一份，与 references 的三条硬性排除不一致
# （三/四/五层「空地」写成 0 = 可以踩；一层「险路尽头」写成 0 = 等于白送），
# 且缺 曲折密道 / 险路恶敌 / 右区作战 / 命运所指 四个键 → 求解器静默按 0 分算。
PRICES = R.LAYER_PRICES


# 层的格子集合/边表来源
CFG_FILE = {1: ("17-一层构型.md", "构型{cfg}（"), 2: ("09-二层构型.md", "构型{cfg}（"),
            3: ("10-三层左区构型.md", "{cfg}（"), 4: ("11-四层左区构型.md", "{cfg}（"),
            5: ("13-五层左区构型.md", "{cfg}（")}
# 六层只有 1 个构型，且文件里没有「构型N」标题——它的边表就是「## 边表」那一节。
L6_FILE = "15-六层构型.md"

# 右区（熄日余径）**固定节点数**（真源：12-三层全图=6、16-四层全图=7、14-五层全图=9）。
# OCR 漏 1 个暗图标 = 安静地少算一整格（右区作战 21 分）→ right_zone() 按它校验格数。
EXPECTED_RIGHT_CELLS = {3: 6, 4: 7, 5: 9}


def config_edges(layer, cfg):
    if int(layer) == 6:
        txt = open(os.path.join(REF, L6_FILE), encoding="utf-8").read()
        secs = re.split(r"(?m)^## ", txt)
        # 六层两张图：默认「边表」（盲目者所见 5×4）；cfg=源流 走「边表（源流交汇处，6×5）」
        want_src = "源流" in str(cfg or "")
        sec = None
        for s in secs:
            if not s.startswith("边表"):
                continue
            head = s.splitlines()[0] if s.splitlines() else ""
            if want_src == ("源流" in head):
                sec = s
                break
        if sec is None:
            raise SystemExit(f"找不到六层的「## 边表{'(源流)' if want_src else ''}」小节（{L6_FILE}）")
        return parse_l6_edges(sec)
    fn, pat = CFG_FILE[layer]
    txt = open(os.path.join(REF, fn), encoding="utf-8").read()
    want = pat.replace("{cfg}", str(cfg))
    sec = next((s for s in re.split(r"(?m)^## ", txt) if s.startswith(want)), None)
    if sec is None:
        raise SystemExit(f"找不到构型 {cfg}（{fn} 里以「{want}」开头的段落）")
    return CC.parse_section(sec)[0]


def parse_l6_edges(seg):
    """六层边表是**对称邻接表**：`| (1,0) | (0,0)、(1,1)、(2,0) |`。
    按无序对去重（两边各写一次），与 check_configs 的表格解析口径一致。"""
    return CC.parse_section(seg)[0]


# ── 险路恶敌 / 险路尽头坐标（定构型的「第二把钥匙」）─────────────────────
# 玩家 2026-10-28：**起点固定但不唯一**，遇到多个构型共用同一个起点时，
# 用**已探明的险路尽头 / 险路恶敌坐标**判断，两个加起来一定能定出正确构型。
#
# 三层：险路恶敌（红图标，每构型 1 个）—— `10-三层左区构型` 的坐标表
# 四层：险路尽头（绿图标，每构型 3 个，开局只亮 1 个）—— `11-四层左区构型`
# 五层：险路恶敌（红图标，每构型 1 个）—— `13-五层左区构型`
# 一层：险路尽头（每构型 1–2 个）—— `17-一层构型` 头部总览表
# 二层：险路尽头（每构型 2 个）—— `09-二层构型` 头部总览表
# 注意：**每层只有其中一种**（三层无尽头、四层无恶敌、五层无尽头），所以合起来必然唯一。
LANDMARK_SRC = {
    1: ("17-一层构型.md", "险路尽头"),
    2: ("09-二层构型.md", "尽头"),
    3: ("10-三层左区构型.md", "险路恶敌"),
    4: ("11-四层左区构型.md", "险路尽头"),
    5: ("13-五层左区构型.md", "险路恶敌"),
}
_LANDMARK_CACHE = {}


def _landmark_table(layer):
    """解析该层的「构型 → 地标坐标集合」表，返回 {cfg: {(x,y), …}}。

    两条解析路径：
      A. `## …<key>…` 小节内的表（三/四/五层：地标坐标表各占一节）；
      B. 文件头部总览表（一/二层：`| 构型 | 起点 | 险路尽头/尽头 | … |`，2026-10-28 补）。
    """
    layer = int(layer)
    if layer in _LANDMARK_CACHE:
        return _LANDMARK_CACHE[layer]
    src = LANDMARK_SRC.get(layer)
    out = {}
    if src:
        fn, key = src
        txt = open(os.path.join(REF, fn), encoding="utf-8").read()
        lines = txt.splitlines()
        # 路径 A
        for i, ln in enumerate(lines):
            if ln.startswith("## ") and key in ln:
                head = None
                for j in range(i, min(i + 12, len(lines))):
                    row = lines[j]
                    if head is None and row.startswith("|") and "构型" in row:
                        head = [c.strip() for c in row.strip("|").split("|")]
                        continue
                    if head is not None and row.startswith("|") and key in row:
                        cells = [c.strip() for c in row.strip("|").split("|")]
                        for name, cell in zip(head[1:], cells[1:]):
                            out[re.sub(r"\*", "", name)] = {
                                (int(x), int(y)) for x, y in
                                re.findall(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", cell)}
                        break
                if out:
                    break
        # 路径 B：头部总览表（没有 `## <key>` 小节时）
        if not out:
            for i, ln in enumerate(lines):
                if ln.startswith("|") and "构型" in ln and key in ln:
                    head = [c.strip() for c in ln.strip("|").split("|")]
                    ki = [k for k, c in enumerate(head) if key in c][0]
                    for row in lines[i + 1:]:
                        if not row.startswith("|"):
                            break
                        cells = [c.strip() for c in row.strip("|").split("|")]
                        if len(cells) != len(head):
                            break
                        if re.fullmatch(r"[-:\s]*", cells[0]):
                            continue        # markdown 分隔行
                        out[re.sub(r"[^\w]", "", cells[0]) or cells[0]] = {
                            (int(x), int(y)) for x, y in
                            re.findall(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", cells[ki])}
                    break
    _LANDMARK_CACHE[layer] = out
    return out


def config_landmarks(layer, cfg):
    """该构型的 险路恶敌/险路尽头 坐标集合（与 `config_start` 同级的硬事实）。"""
    return set(_landmark_table(layer).get(str(cfg).upper(), set()))


def configs_by_landmark(layer, cell):
    """哪些构型的地标集合包含这个坐标（用来在「起点不唯一」时定构型）。"""
    cell = RS.xy(cell)
    return sorted(cfg for cfg, cells in _landmark_table(layer).items() if cell in cells)


def landmark_conflict(layer, cfg, cells):
    """检查观察到的地标格与构型声明是否冲突。返回 (ok, 说明)。

    三层/五层恶敌各只有 1 格，四层尽头有 3 格但**开局只亮 1 格**，
    所以判据是「观察到的必须**全部**落在声明集合里」（observed ⊆ want，2026-10-29 修复）；
    不在就是构型选错。
    旧实现用的是**交集**（`observed & want` 非空即通过）：观察 [(1,2),(0,0)] 里
    只要有一个碰上就放行，混进来的错坐标拦不住——三层 L1/L4 恶敌都在 (5,2)
    这类共用坐标会放行错构型。observe.detect_config 一直是子集判据，两处现在一致。
    """
    want = config_landmarks(layer, cfg)
    if not want or not cells:
        return True, ""
    obs = {tuple(c) for c in cells}
    if obs <= want:
        return True, ""
    out = sorted(obs - want)
    return False, (f"地标 {sorted(obs)} 里有 {out} 不在构型{cfg} 的 {sorted(want)} 里"
                   "（观察到的必须全部落在声明集合里）")


def configs_by_evidence(layer, starts=(), landmarks=()):
    """用**起点 + 地标**两把钥匙一起筛构型（玩家 2026-10-28 的判定法）。

    起点可能被多个构型共用（三层 L1/L2 都是 (0,2)），
    地标也可能被共用（三层 L1/L4 的恶敌都在 (5,2)），
    但**两者加起来**在所有实战里都能唯一确定构型。
    返回命中的构型列表（空 = 没有构型同时满足，说明读错了）。

    2026-10-28 审计（「起点表写得太早」连带条）：起点表是**逐步补满**的，
    没学过的构型 `config_start` 返回 None——它们**不能**因为「表里还没有起点」
    就被排除，否则 L1 已记住 (0,2)、L2 还没有时，查 (0,2)+L2 的恶敌会得到空列表，
    把还没学过的正确构型丢掉。未学起点的构型**放行**给地标筛选，
    交给调用方的格子集合求差去分辨。
    """
    cand = None
    if starts:
        s = {RS.xy(q) for q in starts}
        cand = {cfg for cfg in _landmark_table(layer)
                if config_start(layer, cfg) is None or config_start(layer, cfg) in s} or None
    if landmarks:
        lm = {RS.xy(q) for q in landmarks}
        # 子集判据（2026-10-29 修复，与 landmark_conflict 同口径）：
        # 观察到的地标必须**全部**落在构型声明集合里；旧交集写法会让
        # 「混进一个错坐标」或「共用坐标」的构型被错误放行
        hit = {cfg for cfg, cells in _landmark_table(layer).items() if lm <= cells}
        cand = hit if cand is None else (cand & hit)
    if cand is None:
        return []
    return sorted(cand)


START_FILE = os.path.join(REF, "起点表.json")


def _load_start_table():
    try:
        with open(START_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def config_start(layer, cfg):
    """构型声明的起点；三种来源按优先级查：

    1. **构型表标题/正文**（`## L1（起点 (0,2)）` 或 `起点 **(2,0)**`）——一层/二层/六层；
    2. **`起点表.json`**——三/四/五层的 10 个构型标题里没写起点（只在「起点 → 构型 索引」
       里列了已核实的几条），所以单独维护一张配置表；
    3. 都没有 → None（调用方按候选 + 判据兜底，**别猜**）。

    玩家 2026-10-28：**所有构型的起点都是固定的**——所以这张表是可以逐步补满的硬事实，
    不是启发式。`learn_start()` 会在一次成功识别后自动写入。
    """
    if int(layer) == 6:
        if "源流" in str(cfg or ""):
            return (0, 2)          # 三结局六层「源流交汇处」：起点固定 (0,2)
        txt = open(os.path.join(REF, L6_FILE), encoding="utf-8").read()
        m = re.search(r"起点\s*\**\s*\((\d+)\s*,\s*(\d+)\)", txt)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    else:
        fn, pat = CFG_FILE[layer]
        txt = open(os.path.join(REF, fn), encoding="utf-8").read()
        want = pat.replace("{cfg}", str(cfg))
        sec = next((s for s in re.split(r"(?m)^## ", txt) if s.startswith(want)), None)
        got = CC.parse_section(sec)[2] if sec else None
        if got:
            return got
    table = _load_start_table()
    v = table.get(str(int(layer)), {}).get(str(cfg).upper())
    return tuple(v) if v else None


def learn_start(layer, cfg, start, quiet=False):
    """把一次**已验证**的起点写进 `起点表.json`（构型 → 起点是硬事实，可累积）。

    只该在「起点落在该构型格子内、且是无标签格」之后调用；
    写之前会先和已有记录比对，不一致就**不覆盖**并告警（宁可让人看见冲突）。
    """
    layer, cfg = str(int(layer)), str(cfg).upper()
    start = [int(start[0]), int(start[1])]
    table = _load_start_table()
    old = table.get(layer, {}).get(cfg)
    if old is not None:
        if list(old) != start and not quiet:
            print(f"!! 起点表冲突：{layer}层 构型{cfg} 记的是 {tuple(old)}，"
                  f"这次识别是 {tuple(start)} —— 不覆盖，请人工确认")
        return False
    table.setdefault(layer, {})[cfg] = start
    with open(START_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(table, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    if not quiet:
        print(f"  记住起点：{layer}层 构型{cfg} → {tuple(start)}（写入 起点表.json）")
    return True


# 诡秘的「距离带」候选表（`06-距离表`）：某类节点在某层只可能出现在「距起点 N–M 步」的格子上。
# 2026-10-28 从 route.py 搬来——以前只有 route.py 会做这个推断，
# 于是同一个局面：route.py 给「诡秘@3,2 = 5.7 分」，build_scenario 给「诡秘 = 5 分」，
# 两个入口算出的路线不一样。
#
# ⚠️ **这张表必须与 06-距离表 逐类对齐**。2026-10-28 曾漏了
# 先行一步（三层 2–4）/ 诡意行商（三层 3–8）/ 应急助力（三层 4+），
# 结果近格期望被抬高（(2,2) 该是 6.0，工具给 10.0）——候选少一类，均值就巨大。
# `selftest` 的「距离带与 06 表一致」逐个核这三层。
# 曲折密道不放进候选：它**固定点亮**，永远不会伪装成「未知的诡秘」。
BANDS = {
    3: {"狭路相逢": (2, 4), "秘境行商": (3, 6), "误入奇境": (5, 6),
        "得偿所愿": (4, 99), "不期而遇": (1, 99), "失与得": (3, 6), "安全的角落": (3, 6),
        # 2026-10-28 补齐：这三类之前漏了，导致近格期望被抬高
        # （玩家实测 (2,2) 可能是「先行一步」，工具却只给「狭路相逢」）
        "先行一步": (2, 4), "诡意行商": (3, 8), "应急助力": (4, 99)},
    4: {"狭路相逢": (2, 99), "秘境行商": (2, 6), "误入奇境": (5, 8),
        "得偿所愿": (4, 99), "不期而遇": (1, 99), "失与得": (2, 4), "安全的角落": (2, 99),
        "先行一步": (2, 4), "诡意行商": (2, 99), "应急助力": (2, 3)},
    5: {"狭路相逢": (2, 4), "秘境行商": (2, 99), "误入奇境": (3, 8),
        "得偿所愿": (1, 99), "不期而遇": (1, 99), "失与得": (2, 99), "安全的角落": (2, 99),
        "诡意行商": (1, 99), "应急助力": (3, 5)},
}


def _infer_secret_bands(layer, nodes, values, edges, start):
    """距离带推断（2026-10-28 审查第 3 条拆出）：「未知的诡秘」逐格算期望。

    就地改写 nodes（逐格改名 `诡秘@x,y`）与 values，返回逐格说明列表。
    为什么必须逐格改名：求解器按「节点类型」取价，同一层所有诡秘共用一个 price
    就等于「统一给 5 分」，而 `06-距离表` 说不同格子候选不同（2026-10-27 踩过：全变 0 分）。
    """
    adj = {}
    for u, v in edges:
        u, v = RS.xy(u), RS.xy(v)
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    start = RS.xy(start)
    dist = {start: 0}
    dq = collections.deque([start])
    while dq:
        cur = dq.popleft()
        for nb in adj.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                dq.append(nb)
    revoked = {t for t in nodes.values() if t in BANDS[layer]}      # 已点亮过的类型
    notes = []
    for k, t in list(nodes.items()):
        if t not in ("诡秘", "未知的诡秘"):
            continue
        q = RS.xy(k)
        d = dist.get(q)
        if d is None:
            continue
        cand = [ty for ty, (lo, hi) in BANDS[layer].items()
                if lo <= d <= hi and ty not in revoked]
        if not cand:
            continue
        vals = [values.get(ty, 5) for ty in cand]
        exp = round(sum(vals) / len(vals), 1)
        uniq = f"诡秘@{k}"
        nodes[k] = uniq
        values[uniq] = exp
        notes.append(f"({k}) 距{d}步 候选{'/'.join(cand)} → {exp}")
    return notes


def _infer_left_quota(layer, nodes, values, lm, root, quota_notes):
    """左区配额推断（2026-10-28 审查第 3 条拆出）：三/四层左区紧急作战共 2 个。

    一层（2026-10-29 补）：**默认无理想源** → 唯一 1 个紧急落在凶戾里，按 `3 + 6/k`；
    玩家报了树根则它占掉名额，其余凶戾全是普通作战 3（03-节点收益 一·附）。
    N16文明开化模式（2026-10-30 描述修正：理想源是「追猎进入时大概率出现」，**代码口径不变**）：
    其中 1 个必是理想源——配额一满，剩余未知凶戾全是普通作战（0 分）；
    没满时按 `9 × 剩余名额 / 凶戾格数` 给期望。
    普通分队模式（2026-10-29）：理想源不保证出现——报了坐标才占名额，
    **没报 = 名额全部落在未知凶戾里**（三/四层公式不变，只是 used 里没有理想源那份）；
    二层也纳入：唯一紧急名额在未报理想源时按 `3 + 6/k` 期望铺（普通作战 3、紧急 9），
    报了理想源则其余凶戾全普通（3 分，与原「二层排除法」一致）。
    就地改写 nodes/values，说明追加到 quota_notes（列表，调用方负责逐句打印）。
    """
    if layer == 1:
        # 一层：紧急作战总数 ≤1，且**默认没有理想源**（20-一层特殊规则）——
        # 那唯一 1 个紧急就落在未知凶戾里：期望 = (9 + 3(k−1))/k = 3 + 6/k。
        # 玩家报了树根才轮到「它占掉唯一名额 → 其余凶戾全是普通作战 3」。
        # 2026-10-29 修复：以前一层凶戾恒按 3.0 计（只有「有理想源」这一支），
        # 默认局少算 2–6 分/格（实测 L1-config3 14→19、L1-config2 11→17）。
        root_cell = RS.xy(root) if root else None
        hazards = [k for k, t in nodes.items()
                   if t in ("未知的凶戾", "凶戾") and RS.xy(k)[0] <= lm]
        if not hazards:
            return
        if root_cell is not None:
            for k in hazards:
                nodes[k] = "普通作战"
            values["普通作战"] = 3       # 一层普通作战照旧 3 分（层表口径）
            quota_notes.append(
                f"一层：理想源已报、占掉唯一紧急名额 → "
                f"剩余 {len(hazards)} 个未知凶戾全按普通作战 3 分")
        else:
            k = len(hazards)
            exp = round(3.0 + 6.0 / k, 1)   # 1 个紧急(9) + (k−1) 个普通(3)
            values["未知的凶戾"] = exp
            quota_notes.append(
                f"一层：未报理想源 → 唯一紧急作战落在 {k} 个未知凶戾里，"
                f"每个期望 {exp} 分")
        return
    if layer == 2:
        # 二层：紧急共 1 个。N16文明开化下它按理想源处理（玩家必报坐标）→ 凶戾全普通
        # 是定值 3，层表已写死，无需推断；普通分队下才看玩家报没报。
        if not R.is_normal_squad():
            return
        root_cell = RS.xy(root) if root else None
        hazards = [k for k, t in nodes.items()
                   if t in ("未知的凶戾", "凶戾") and RS.xy(k)[0] <= lm]
        if not hazards:
            return
        if root_cell is not None:
            for k in hazards:
                nodes[k] = "普通作战"
            values["普通作战"] = 3       # 二层普通作战照旧 3 分（层表口径）
            quota_notes.append(
                "二层（普通分队）：理想源已报 → 唯一紧急名额被占，"
                f"剩余 {len(hazards)} 个未知凶戾全按普通作战 3 分")
        else:
            k = len(hazards)
            exp = round(3.0 + 6.0 / k, 1)   # 1 个紧急(9) 落在 k 个普通(3)里
            values["未知的凶戾"] = exp
            quota_notes.append(
                f"二层（普通分队）：未报理想源 → 唯一紧急作战落在 {k} 个未知凶戾里，"
                f"每个期望 {exp} 分")
        return
    if layer not in (3, 4):
        return
    root_cell = RS.xy(root) if root else None
    seen_emerg = [k for k, t in nodes.items()
                  if t == "紧急作战" and RS.xy(k)[0] <= lm]
    used = len([k for k in seen_emerg
                if root_cell is None or RS.xy(k) != root_cell])
    if root_cell is not None:
        used += 1
    hazards = [k for k, t in nodes.items()
               if t == "未知的凶戾" and RS.xy(k)[0] <= lm]
    if not hazards:
        return
    if used >= 2:
        for k in hazards:
            nodes[k] = "普通作战"
        values["普通作战"] = 0
        quota_notes.append(
            f"左区：紧急作战配额已满（已见 {len(seen_emerg)} 个 + 理想源占 {used - len(seen_emerg)}）"
            f"→ 剩余 {len(hazards)} 个未知凶戾 **全部按普通作战 0 分**")
    else:
        exp = round(9.0 * (2 - used) / len(hazards), 1)
        values["未知的凶戾"] = exp
        quota_notes.append(
            f"左区：紧急作战还差 {2 - used} 个，"
            f"{len(hazards)} 个未知凶戾平分 → 每个期望 {exp} 分")


def _infer_right_quota(layer, nodes, values, quota_notes):
    """右区（熄日余径）作战配额推断（2026-10-28 审查第 3 条拆出）。

    **与左区完全独立**（玩家 2026-10-28）：左区已探明 2 个紧急，右区照样有 1 个。
    每层固定：三层 1 紧急 + 1 普通、四层 1 紧急 + 2 普通、五层 2 紧急 + 1 普通。
    所以右区凶戾不是「盲踩 18 分」，而是按剩余配额算——**已探明一个，剩下全变确定**。

    2026-10-28 审计（右区期望不看「看见了几格」）：名额只够 min(未知数, 剩余名额)
    个格用，**多出来的未知格不可能是普通作战**（配额是定数；多出的一般是居民据点
    或误检），不按满额定价——逐格改名 `右区凶戾@x,y` 记 0，也不再进必走点
    （`derive_must` 只认精确类型名）。实测四层配额 1 紧急+2 普通：
      · 4 个未知全给 17.7 → 合计 70.8，而真实上限只有 53 ❌
      · 改后 3 个按 17.7 配（=53）+ 1 个记 0 ✅
    名额格与多余格不可区分，取排序靠前的 m 个当名额（总量守恒，期望不变）。
    就地改写 nodes/values，说明追加到 quota_notes。
    """
    quota = R.RIGHT_COMBAT_QUOTA.get(layer)
    if not quota:
        return
    right_cells = [k for k, t in nodes.items()
                   if t in ("右区凶戾", "右区作战", "右区紧急作战")]
    unknown = [k for k, t in nodes.items() if t == "右区凶戾"]
    if not unknown:
        return
    seen_e = sum(1 for k in right_cells if nodes[k] == "右区紧急作战")
    seen_p = sum(1 for k in right_cells if nodes[k] == "右区作战")
    need_e = max(0, quota["紧急作战"] - seen_e)
    need_p = max(0, quota["作战"] - seen_p)
    slots = need_e + need_p                     # 剩余名额
    n = len(unknown)
    if slots == 0:
        # 两个配额都满了 → 这 n 个未知格**不可能再是作战**（配额是定数）。
        # 必须单独判：直接落进下面的 `need_e == 0` 分支会打印
        # 「0/N 个右区凶戾定为右区普通作战 16」，读起来像还剩普通名额（2026-10-28 复查）。
        for k in unknown:
            uniq = f"右区凶戾@{k}"
            nodes[k] = uniq
            values[uniq] = 0
        quota_notes.append(
            f"右区：作战名额已满（已见 {seen_e} 紧急 / {seen_p} 普通）→ "
            f"剩余 {n} 个右区凶戾 **全部记 0**（不可能再是作战；可能是居民据点/误检）")
        return
    m = min(n, slots)                           # 能按名额定价的格数
    if need_e == 0:
        for k in unknown[:m]:
            nodes[k] = "右区作战"
        values["右区作战"] = 16
        quota_notes.append(
            f"右区：紧急配额已满（已见 {seen_e} 个）→ "
            + (f"{n} 个右区凶戾 **全是右区普通作战 16**" if m == n
               else f"{m}/{n} 个右区凶戾定为右区普通作战 16"))
    elif need_p == 0:
        for k in unknown[:m]:
            nodes[k] = "右区紧急作战"
        values["右区紧急作战"] = 21
        quota_notes.append(
            f"右区：普通配额已满（已见 {seen_p} 个）→ "
            + (f"{n} 个右区凶戾 **全是右区紧急作战 21**" if m == n
               else f"{m}/{n} 个右区凶戾定为右区紧急作战 21"))
    else:
        exp = round((21.0 * need_e + 16.0 * need_p) / slots, 1)
        values["右区凶戾"] = exp
        quota_notes.append(
            f"右区：还差 {need_e} 紧急 / {need_p} 普通 → "
            f"{m} 个右区凶戾每个期望 {exp} 分")
    for k in unknown[m:]:
        uniq = f"右区凶戾@{k}"
        nodes[k] = uniq
        values[uniq] = 0
    if n > m:
        quota_notes.append(
            f"右区：未知 {n} 个 > 剩余名额 {slots} 个 → 多出的 {n - m} 个"
            f"**不按满额定价（记 0）**——可能是居民据点/误检，"
            f"若有流窜居民请人工核对")


def apply_unknown_aliases(values):
    """把 OCR 别名（`未知的凶戾` / `未知的诡秘`）映射到定价表里的基名。

    **唯一正确时机：拼好 values（DEFAULT + 本层）之后、`infer_unknown` 之前。**
    顺序反了就是事故：`_infer_left_quota` 会把「未知的凶戾」改成逐格期望
    （9 × 剩余名额 / 凶戾格数），别名再套一次就把期望换成 `凶戾` 的层均值
    ——判词打印「每个期望 2.2 分」、实际按 1.0 分算（2026-10-28 复查发现，
    旧代码 route.py / build_scenario.py 各有一份同样的循环，都把推断结果冲掉了）。

    右区别名**不在这里**：右区格子已被 `rules.right_zone_type()` 改名成
    「右区凶戾 / 右区诡秘」，有自己一档价（16/21/8），不能被左区价覆盖。
    """
    for alias, base in (("未知的凶戾", "凶戾"), ("未知的诡秘", "诡秘")):
        if base in values:
            values[alias] = values[base]
    return values


def infer_unknown(layer, nodes, values, edges, start, lm, root=None):
    """按**距离带 + 数量约束**推「未知的诡秘 / 未知的凶戾」的期望值。

    就地改写 nodes（逐格改名 `诡秘@x,y` / `普通作战`）与 values。
    返回 (诡秘逐格说明, 配额说明**列表**)；没有推断就返回 ([], [])。
    配额说明是列表：**左区一句 + 右区一句都保留**，右区不许盖掉左区
    （2026-10-28 审计：三/四层经常两边都有未知凶戾，单变量会让左区那句消失）。

    结构（2026-10-28 审查第 3 条拆分，方便单独测）：
      `_infer_secret_bands`  — 距离带期望（诡秘改名 + 赋值）
      `_infer_left_quota`    — 左区紧急作战配额
      `_infer_right_quota`   — 右区作战配额（熄日余径）
    """
    if not start:
        return [], []
    # 距离带只覆盖 3/4/5 层；**配额推断必须对所有层生效**——
    # 2026-10-29 修复：以前这里写成 `layer not in BANDS` 直接 return，把
    # 二层「普通分队未报 root → 3+6/k」和一层「无理想源 → 3+6/k」整段变成死代码。
    secret_notes = (_infer_secret_bands(layer, nodes, values, edges, start)
                    if layer in BANDS else [])
    quota_notes = []
    _infer_left_quota(layer, nodes, values, lm, root, quota_notes)
    _infer_right_quota(layer, nodes, values, quota_notes)
    return secret_notes, quota_notes


# ── 必走节点（2026-10-28 玩家口径；理想源一条 2026-10-29 按分队模式修订）──
# 思路：**先把必须走到的坐标锁死，再在「必须覆盖它们」的解里穷举**。
# 锁死几个点，分支就少一大截（二层最明显：树根就是唯一的紧急作战）。
#
# | 节点 | 何时必走 |
# |---|---|
# | 理想源（树根） | N16文明开化：只要图上有，**任何层**都必走。普通分队：降级为「已探明 |
# |                | 紧急作战」同待遇——二~五层必走，一/六层不强制（不回AP、值=紧急） |
# | 秘境行商 | 二 / 三 / 四 / 五层，且已探明 |
# | 紧急作战 | 二 / 三 / 四 / 五层，且已探明 |
# | 右区的 作战 / 紧急作战 / 未知的凶戾 / 凶戾 | 必走（含改名后的「右区作战」等）|
# | 五层右区的 **诡意行商** | 必走（02-地图：五层右区固定 1 个；03：五层行商「全进」）|
# | 不算 | 左区的未知凶戾、右区的诡秘、命运所指、空地、安全的角落 |
# 一层不强制行商与紧急作战（玩家口径）。
MUST_ALWAYS = ("树根", "理想源")
MUST_REVEALED = ("秘境行商", "紧急作战")           # 只在二~五层算
MUST_RIGHT = ("作战", "紧急作战", "未知的凶戾", "凶戾",
              "右区作战", "右区紧急作战", "右区凶戾")


def derive_must(layer, nodes, right_cells=()):
    """从已铺好的节点表推「必走点」坐标。返回排序后的 [(x,y), …]。

    普通分队模式：理想源价值 = 紧急作战、不再 +2AP+4 藏品 →
    必走待遇跟着降级（与 MUST_REVEALED 同口径：二~五层必走，一/六层不强制）。

    三层·三结局（2026-10-30）：三结局=要 且 前置收藏品=未获取 时，
    **先行一步 = 15 分必走点**（前提是它已被探明；还藏在未知诡秘里时
    没有坐标可锁，改由 `xianxing_candidates()` 列候选坐标让玩家去找）。
    """
    right = set(right_cells)
    normal = R.is_normal_squad()
    out = []
    for k, t in nodes.items():
        q = RS.xy(k)
        if t in MUST_ALWAYS:
            if normal and layer not in (2, 3, 4, 5):
                continue
            out.append(q)
        elif layer in (2, 3, 4, 5) and t in MUST_REVEALED:
            out.append(q)
        elif (layer == 3 and R.three_ending_need_xianxing()
              and t == R.THREE_XIANXING_NODE):
            # 三层无右区 + 三结局=要 + 前置收藏品未获取：先行一步是必走点
            out.append(q)
        elif q in right and t in MUST_RIGHT:
            out.append(q)
        elif layer == 5 and q in right and t == "诡意行商":
            # 五层右区**固定 1 个诡意行商**（02-地图固定构成），且 03 的结算口径是
            # 「五层诡意行商 2–3 个全进」→ 它就是必走点（12 分，仅次于右区作战）。
            out.append(q)
    return sorted(out)


def xianxing_candidates(layer, nodes, edges, start, lm=None, visited=()):
    """三层「先行一步」还藏在哪些「未知的诡秘」里（2026-10-30 三结局问询用）。

    **只在** 三层 + 三结局=要 + 前置收藏品=未获取 + 图上还没探明「先行一步」时有意义；
    其余情况返回 []（调用方据此判断要不要提示玩家）。

    候选判据（`06-距离表` 先行一步 三层 2–4 + `04` 数量表 三层左区必出 1 个）：
      · 类型是「未知的诡秘」（含 `诡秘@x,y` 这类逐格期望派生名）；
      · 距**层起点** BFS 徒步 2–4 步（不是距当前位置！见 19-已知坑「距离基准」）；
      · 在左区（x ≤ lm；无右区局整层都是左区，lm 传 None 也安全）；
      · 不在 visited 里（走过的格早已探明，不该再找）。

    返回 [(坐标, 步数), …]，按 (步数, 坐标) 排序。
    """
    if int(layer) != 3 or not R.three_ending_need_xianxing():
        return []
    if not start or not edges:
        return []
    if any(str(t) == R.THREE_XIANXING_NODE for t in nodes.values()):
        return []                      # 已经探明，不再是「候选」
    adj = {}
    for u, v in edges:
        u, v = RS.xy(u), RS.xy(v)
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    start = RS.xy(start)
    dist = {start: 0}
    dq = collections.deque([start])
    while dq:
        cur = dq.popleft()
        for nb in adj.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                dq.append(nb)
    lo, hi = BANDS[3][R.THREE_XIANXING_NODE]
    skip = {RS.xy(v) for v in (visited or ())}
    out = []
    for k, t in nodes.items():
        if R.base_type(t) != "诡秘":
            continue
        q = RS.xy(k)
        if q in skip or (lm is not None and q[0] > lm):
            continue
        d = dist.get(q)
        if d is not None and lo <= d <= hi:
            out.append((q, d))
    return sorted(out, key=lambda z: (z[1], z[0]))


# ── 五层·二结局（2026-10-31 玩家新增）────────────────────────────────────
def layer5_fate_cells(nodes, lm):
    """五层：左区 / 右区（熄日余径）的「命运所指」坐标（各自排序的元组列表）。

    左区 3 个 = 拿了二结局相关藏品的标志（`rules.SECOND_FATE_MARKER_COUNT`）。
    右区固定 1 个（五层右区 9 格构成，见 14-五层全图）。
    """
    left, right = [], []
    for k, t in nodes.items():
        if t != R.SECOND_FATE_NODE:
            continue
        q = RS.xy(k)
        (left if q[0] <= lm else right).append(q)
    return sorted(left), sorted(right)


def second_ending_setup(layer, nodes, lm, fate_badge=(), answer=None, node_xy=None):
    """把五层二结局规则落成「定价 / 必走点 / 终点 / 加工品次数钳制」。

    玩家 2026-10-31 口径（全文见 references/28-五层二结局.md）：

      · 左区 3 个「命运所指」= 拿了二结局相关藏品才会出现；
      · 右上角**无徽章**的两个 = 随机收藏品，价值 = 本层「得偿所愿」− 1（五层 7 分）；
      · 右上角**有徽章**的那个 = 二结局关卡入口（本层最后一步）。

    问询分支（`answer` = rules.second_ending()）：
      · **要**   → 右区命运所指进 `must`（`clamp`：进入后所有加工品次数强制→1，尽量排最后）；
                   `final` = 左区带徽章那个（非作战节点，需要全图移动件才落得进去）；
      · **不要** → `final` = 右区命运所指（常规逻辑）；带徽章那个按 -99 硬避（不触发二结局）；
      · 未问     → 与「不要」同样的终点兜底（CLI 本该先拦下并提问）。

    `fate_badge` = `label_ocr.apply_badges()` 认出的「命运所指 + 徽章」坐标；
    认不出/认错时用 `node_xy`（`--second-ending-node`）手报。
    """
    out = {"marker": False, "left": [], "right": [], "badged": None, "plain": [],
           "cell_values": {}, "must": [], "final": None, "clamp": [],
           "notes": [], "error": None, "answer": answer}
    if int(layer) != 5:
        return out
    left, right = layer5_fate_cells(nodes, lm)
    out["left"], out["right"] = left, right
    out["marker"] = len(left) >= R.SECOND_FATE_MARKER_COUNT
    if not out["marker"]:
        # 常规五层（本局没拿二结局相关藏品）：左区没有命运所指，但**右区那 1 个仍是本层终点**
        # （最后 1 点行动力给它）。2026-11-02 修复：这里以前直接 return、final 留 None，
        # route.py 于是走「读到命运所指但没能定出终点」分支 → **普通五层在全程追猎下必然 ❌**。
        out["final"] = right[0] if right else None
        if right:
            out["notes"].append(
                "常规五层（左区无命运所指）：终点 = 右区命运所指 "
                + "、".join(f"({q[0]},{q[1]})" for q in right)
                + "（最后 1 点行动力给它）")
        return out
    out["clamp"] = list(right)

    badge = {tuple(q) for q in (fate_badge or [])}
    cand = [q for q in left if q in badge]
    if node_xy is not None:
        q = tuple(node_xy)
        if q not in left:
            out["error"] = (f"--second-ending-node {q} 不是本层左区的「命运所指」"
                            f"（左区读到：{left}）")
            return out
        out["badged"] = q
        if q not in cand:
            out["notes"].append(f"--second-ending-node 手报 ({q[0]},{q[1]}) 带徽章"
                                "（以人报为准；徽章检测没认出来）")
    elif len(cand) == 1:
        out["badged"] = cand[0]
    elif len(cand) > 1:
        out["badged"] = cand[0]
        out["notes"].append("⚠️ 左区有 %d 个命运所指带徽章 %s → 先按第一个算；"
                            "请用 --second-ending-node 手报真正的二结局入口"
                            % (len(cand), "、".join(f"({q[0]},{q[1]})" for q in cand)))

    b = out["badged"]
    out["plain"] = [q for q in left if q != b]
    val = R.second_fate_random_value(5)
    out["cell_values"] = {RS.key_xy(q): val for q in out["plain"]}
    if out["plain"]:
        out["notes"].append(
            "无徽章命运所指（随机收藏品）"
            + "、".join(f"({q[0]},{q[1]})" for q in out["plain"])
            + f" = 得偿所愿−1 = **{val:g} 分**")

    if answer is True:
        if b is None:
            out["error"] = ("二结局=要：左区没认出「带徽章」的命运所指（徽章检测失败？）——"
                            "本层最后一步只能落在它上面，请用 --second-ending-node c,r 手报"
                            "（右上角有徽章的那个命运所指）")
            return out
        out["cell_values"][RS.key_xy(b)] = 0.0
        out["final"] = b
        out["must"] = list(right)
        if right:
            out["notes"].append(
                "二结局=要：右区命运所指 "
                + "、".join(f"({q[0]},{q[1]})" for q in right)
                + " = **必走点**；进入后零件箱里所有加工品剩余次数强制→1"
                  "（含之后获取的），所以**尽量排在最后几步**")
            out["notes"].append(
                "最后一步 = 左区带徽章命运所指 "
                + "、".join(f"({q[0]},{q[1]})" for q in [b])
                + "（非作战节点 → 老妈妈的融雪可直落，结构性原理也行；"
                  "没有全图移动件就落不进去）")
        else:
            out["notes"].append("⚠️ 没读到右区的「命运所指」——二结局的右区必走点缺失，"
                                "先核对截图/右区是否真的存在")
    elif answer is False:
        out["final"] = right[0] if right else None
        if b is not None:
            out["cell_values"][RS.key_xy(b)] = -99.0
            out["notes"].append(f"二结局=不要：带徽章的 ({b[0]},{b[1]}) 是入口，"
                                "按 -99 硬避（进去就会触发二结局关卡）")
        if right:
            out["notes"].append("二结局=不要：常规逻辑，终点 = 右区命运所指 "
                                + "、".join(f"({q[0]},{q[1]})" for q in right))
        else:
            out["notes"].append("⚠️ 二结局=不要，但没读到右区命运所指 —— 先核对截图")
    else:
        out["final"] = right[0] if right else None
        out["notes"].append("二结局=未问（按常规终点兜底；CLI 本该先拦下提问）")
    return out


def nearest_cell(start, targets, edges):
    """按边表 BFS，返回离 start 最近的 target（不可达的排最后）。"""
    if not targets or start is None:
        return None
    adj = {}
    for u, v in edges:
        adj.setdefault(tuple(u), set()).add(tuple(v))
        adj.setdefault(tuple(v), set()).add(tuple(u))
    dist = {tuple(start): 0}
    q = collections.deque([tuple(start)])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return min((tuple(t) for t in targets),
               key=lambda c: (dist.get(c, 10 ** 9), c))


RIGHT_ZONE_SRC = {3: "12-三层全图.md", 4: "16-四层全图.md"}
_RZ_SHAPES = {}
# 三层边表写成 `(8,Y)-(9,Y)`（Y = 左上角 y，未知量）；四层写成 `(9,4)-(10,4)`（左下角=2 的绝对坐标）。
_RZ_PAIR_Y = re.compile(
    r"\(\s*(\d+)\s*,\s*Y(?:([+-])\s*(\d+))?\s*\)\s*-\s*\(\s*(\d+)\s*,\s*Y(?:([+-])\s*(\d+))?\s*\)")
_RZ_PAIR_XY = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*-\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")


def _parse_right_shapes(layer):
    """从 references 解析**右区形状表** → [{name, cells, edges, empty}]（坐标已归一化）。

    为什么需要它（2026-11-02，玩家第 2 条意见）：
    `right_zone()` 以前按**四邻接假设**连右区的边，而规则文档里其实有精确形状表，
    两者直接冲突——`12-三层全图` 明写「构型3 … `(9,Y)` 与 `(9,Y-1)` **不连**（玩家确认）」，
    四邻接必然把它连上。也就是说旧口径不是「未验证」，是**已知错边**。

    归一化：
      · 三层（12）：`(x,Y+d)` → `(x, d)`，d ∈ {0,-1,-2}；**空地数 = 格数 − 6**（6 类有效节点）。
      · 四层（16）：`(x,y)`（文档左下角 y=2）→ `(x, y-2)`；空地数取小节标题里的「+ N 空地」。
    返回空列表 = 解析失败（调用方回退四邻接，**不许**静默假装查过表）。
    """
    layer = int(layer)
    if layer in _RZ_SHAPES:
        return _RZ_SHAPES[layer]
    fn = RIGHT_ZONE_SRC.get(layer)
    out = []
    if fn:
        txt = open(os.path.join(REF, fn), encoding="utf-8").read()
        if layer == 3:
            parts = re.split(r"(?m)^###\s*构型(\d+)（(\d+)\s*节点）", txt)
            # parts = [前言, "1", "7", 段1, "2", "6", 段2, ...]
            for i in range(1, len(parts) - 2, 3):
                name, ncells, body = ("构型" + parts[i], int(parts[i + 1]), parts[i + 2])
                # 只认「边：」那一行（+ 紧随的续行）：下面的 `**（9,Y）与（9,Y-1）不连**`
                # 用全角括号，正则本来就匹配不到，但仍别把整段扫一遍。
                ln = [k for k, s in enumerate(body.splitlines()) if s.startswith("边：")]
                seg = ""
                if ln:
                    _ls = body.splitlines()
                    seg = _ls[ln[0]] + (_ls[ln[0] + 1] if ln[0] + 1 < len(_ls) else "")
                edges, cells = [], set()
                for ux, us, ud, vx, vs, vd in _RZ_PAIR_Y.findall(seg):
                    du = -int(ud) if us == "-" else int(ud or 0)
                    dv = -int(vd) if vs == "-" else int(vd or 0)
                    u, v = (int(ux), du), (int(vx), dv)
                    edges.append((u, v))
                    cells.update((u, v))
                if edges:
                    out.append({"name": name, "cells": cells, "edges": edges,
                                "empty": max(0, ncells - 6)})
        else:
            parts = re.split(
                r"(?m)^###\s*构型(\d+)（(\d+)\s*格：(\d+)\s*节点\s*\+\s*(\d+)\s*空地）", txt)
            # parts = [前言, "1", "8", "7", "1", 段1, "2", "9", "7", "2", 段2, ...]（4 个捕获组）
            for i in range(1, len(parts) - 4, 5):
                name, nempty, body = ("构型" + parts[i], int(parts[i + 3]), parts[i + 4])
                # 只取**第一段**：紧跟在标题后的边表行。下面「形状：…」那句散文里
                # 会把 `(11,3)-(11,2)` 再写一遍，整段扫会多数一条边（构型1 变 10 条）。
                seg = next((b for b in body.split("\n\n") if b.strip()), "")
                edges, cells = [], set()
                for x1, y1, x2, y2 in _RZ_PAIR_XY.findall(seg):
                    u, v = (int(x1), int(y1) - 2), (int(x2), int(y2) - 2)
                    edges.append((u, v))
                    cells.update((u, v))
                if edges:
                    out.append({"name": name, "cells": cells, "edges": edges, "empty": nempty})
    _RZ_SHAPES[layer] = out
    return out


EDGE_MIN = 4.5          # 「这条边画出来了」的灰度对比阈值（实拍：有线 8–21，无线 −2.5–0.2）
EDGE_MARGIN = 2         # 探针定案要求领先第二名多少分（把「两个形状都有这条边」的噪声挡住）


def match_right_shape(layer, observed, dots=(), probe=None):
    """把右区**读到的有标签格子**（+ 空地圆点）匹配到形状表。

    返回 `(how, name, cells, edges, dropped)`：
      · `table`     —— 唯一解，或「多种形状但**边集完全一致**」（同样安全）→ 用表里边；
      · `ambiguous` —— 多个形状给出**不同**边集（空地落在哪种格子看不出来）→ 不许猜；
      · `none`      —— 没有任何形状对得上（OCR 漏检/多检，或右区不在预期位置）。
    `dropped` = 形状表里有、但**这次接不进图**的边：它们的端点落在**未探知的格**上
    （右区空地不写字；没报 `--right-dots` 时光凭形状只能知道「有几个空地」，不知道「在哪」，
    那一格就没法作为图里的节点，连到它的边只能丢）。**不是**「空地是硬墙」丢的——
    空地是 0 分中继、可穿过。调用方要把条数写进说明/判词：这些边缺着，结论会**偏保守**
    （右区可能少一条通路）。

    `dots`（2026-11-02 新增）：右区空地圆点坐标。**给了 dots 就走精确匹配**——
    `observed | dots` 必须**正好等于**某个形状的格子集合；这样形状唯一锁定，
    三层/四层也能拿到 `table`（否则四层永远多解，只能 ⚠️）。
    空地按玩家 2026-11-02 澄清是**0 分中继（可穿过）**：形状表里穿过空地的边**保留**，
    所以**给了 dots 时 `dropped` 恒为空**（所有端点都已知）。`ambiguous` / `none` 时
    边根本不用，`dropped` 也返回空——别拿它当「缺几条边」的计数。
    """
    observed = {tuple(q) for q in observed}
    dots = {tuple(q) for q in (dots or ())} - observed
    layer = int(layer)
    if not observed:
        return "none", "", set(), [], []
    if layer == 5:
        # 五层右区 = 3×3 九格**四邻全连通**（14-五层全图），文档口径与四邻接一致。
        xs = sorted({q[0] for q in observed})
        ys = sorted({q[1] for q in observed})
        if (len(observed) == 9 and len(xs) == 3 and len(ys) == 3
                and xs[2] - xs[0] == 2 and ys[2] - ys[0] == 2):
            edges = []
            for x in xs:
                for y in ys:
                    if (x + 1, y) in observed:
                        edges.append(((x, y), (x + 1, y)))
                    if (x, y + 1) in observed:
                        edges.append(((x, y), (x, y + 1)))
            return "table", "五层3×3", observed, edges, []
        return "none", "", set(), [], []
    if dots:
        exact = []
        for sh in _parse_right_shapes(layer):
            for s in range(-3, 9):
                cells = {(x, s + dy) for (x, dy) in sh["cells"]}
                if (observed | dots) != cells:
                    continue
                eg = {frozenset(((x1, s + d1), (x2, s + d2)))
                      for (x1, d1), (x2, d2) in sh["edges"]}
                exact.append((sh["name"], s, eg))
        if exact:
            edge_sets = {frozenset(k) for _n, _s, k in exact}
            names = sorted({n for n, _s, _k in exact})
            if len(edge_sets) == 1:
                keep = exact[0][2]
                return ("table",
                        ("/".join(names) if len(names) == 1 else "/".join(names) + "（边集一致）"),
                        observed, [tuple(e) for e in keep], [])
            # 格集相同、边集不同（典型：四层构型2 vs 构型3）→ **像素连线探针**定案。
            # 这两张形状表只差「哪两条边存在」，而边画在屏幕上，能量（实拍：有线 8–21、
            # 无线 −2.5–0.2）。领先不足 EDGE_MARGIN 就照旧判多解。
            if probe is not None:
                union = set().union(*[set(k) for _n, _s, k in exact])
                pres = probe([tuple(sorted(e)) for e in union]) or {}

                def _hit(e):
                    return 1.0 if pres.get(tuple(sorted(e)), 0.0) > EDGE_MIN else 0.0

                scored = []
                for n, _s, k in exact:
                    hit = sum(_hit(e) for e in k)
                    bad = sum(_hit(e) for e in (union - set(k)))
                    scored.append((hit - bad, n, k))
                scored.sort(key=lambda z: -z[0])
                if len(scored) > 1 and scored[0][0] - scored[1][0] >= EDGE_MARGIN:
                    _sc, _nm, keep = scored[0]
                    return ("table", f"{_nm}（像素连线探针定案，{_sc:+g}）",
                            observed, [tuple(e) for e in keep], [])
            return "ambiguous", "/".join(names), observed, [], []
        # 圆点和形状对不上：可能是误检 → 落到下面的无圆点匹配（并如实报多解）
    cands = []
    for sh in _parse_right_shapes(layer):
        for s in range(-3, 9):
            cells = {(x, s + dy) for (x, dy) in sh["cells"]}
            if not observed <= cells:
                continue
            if len(cells) - len(observed) != sh["empty"]:
                continue
            eg_all = {frozenset((u, v)) for u, v in
                      (((x1, s + d1), (x2, s + d2))
                       for (x1, d1), (x2, d2) in sh["edges"])}
            eg = {e for e in eg_all if set(e) <= observed}
            # 端点落在**未探知格**（右区空地）的边：这一格还不知道坐标，进不了图 →
            # 这条边这次接不上。**必须报出去**（以前这里默默丢掉、dropped 永远为空，
            # 于是「已核验」的判词掩盖了「右区可能少一条通路」）。
            dropped = {tuple(sorted(e)) for e in (eg_all - eg)}
            cands.append((sh["name"], s, eg, dropped))
    if not cands:
        return "none", "", set(), [], []
    edge_sets = {frozenset(eg) for _n, _s, eg, _d in cands}
    names = sorted({n for n, _s, _eg, _d in cands})
    if len(edge_sets) == 1:
        eg = cands[0][2]
        # 多个候选形状的 dropped 取**并集**：宁可多报一条「没接进来的边」，不许漏报。
        # （边集一致时已知格之间的边是确定的；不确定的只有那些端点落在空地里的边。）
        dropped = sorted(set().union(*[d for _n, _s, _eg, d in cands]))
        return ("table",
                ("/".join(names) if len(names) == 1 else "/".join(names) + "（边集一致）"),
                observed, [(u, v) for u, v in (tuple(e) for e in eg)], dropped)
    return "ambiguous", "/".join(names), observed, [], []


def resolve_right_dots(image_or_im, grid, gx, gy, lm, manual="", auto=False):
    """右区**空地圆点**坐标：手报优先，其次自动检测（2026-11-02）。

    为什么需要它：三/四层右区里**空地不写字**，没有它 `match_right_shape()` 只能拿到
    有标签的节点 → 四层（1↔4、2↔3 格集互相包含）**永远多解**，判词只能 ⚠️。
    报出空地坐标后形状唯一锁定，边表才算「已核验」。

    `manual` = `--right-dots "c,r;c,r"`（玩家口报，**最可靠**）；
    `auto`   = `--auto-dots`（像素检测，**实验性**：目前的判据只能挑出「格心有一团
               灰白、整块不够亮」的格子，实拍标定还差一个样本，见 label_ocr.DOT_*）。
    手报格式错 → 抛 ValueError（调用方按「参数错」判 ❌）。
    """
    dots = []
    for part in filter(None, (manual or "").split(";")):
        try:
            dots.append(RS.xy(part))
        except ValueError as exc:
            raise ValueError(f"--right-dots 格式错误：{part!r}（{exc}；正确写法 \"c,r;c,r\"）")
    notes = []
    if dots:
        notes.append("右区空地圆点（**手报**，用于锁定形状）："
                     + "、".join(f"({q[0]},{q[1]})" for q in dots))
        return dots, notes
    if auto:
        try:
            dots, dn = LO.detect_empty_dots(image_or_im, gx, gy, grid, lm)
            notes += dn
            if not dots:
                notes.append("右区空地圆点：自动检测没找到（阈值保守、且未按实拍标定）"
                             "——需要就手报 --right-dots c,r;c,r")
        except Exception as exc:                       # noqa: BLE001  像素检测整体失败
            notes.append(f"右区空地圆点自动检测失败：{exc}（可手报 --right-dots）")
    return dots, notes


def left_max(layer, cols=None, cfg=None):
    """左区最右列 x（右区 = x > 它）。

    五层有两个变体：正常 14 列（左区 x 0–9）、L8 13 列（左区 x 0–8）。
    2026-10-30 又多了「无右区、左区居中」的截图：正常 10 列、L8 9 列。
    仅凭 cols 无法区分「正常无右区 10 列」和「L8 有右区 13 列」之外的个别组合，
    所以**能拿到构型名就优先用 cfg**；拿不到再按列数推。
    """
    lm = LO.LEFT_MAX.get(layer, 99)
    if layer != 5:
        return lm
    cfgl = str(cfg).upper() if cfg is not None else ""
    if cfgl == "L8":
        lm = 8
    elif cols is None:
        lm = 9
    elif cols >= 14:
        lm = 9                      # 正常完整宽度
    elif cols in (9, 13):
        lm = 8                      # L8 无右区 / L8 完整宽度
    else:
        lm = 9                      # 正常无右区 10/11 列
    return lm


def right_zone(layer, grid, cells, edges, lm=None, meta=None, dots=(), probe=None):
    """把**右区**（左区构型表之外的那几列）拼进 cells/edges。

    2026-11-02（玩家第 2 条意见）**口径变更**：右区连通**不再是四邻接假设**——
    `12-三层全图` / `16-四层全图` 里有精确形状表（三层 3 构型、四层 4 构型），
    五层的 3×3 田字也明写「四邻全连通」。现在按形状表连线，只在**匹配不上**时
    才回退四邻接，并把「未验证」写进 meta 让 route.py 降级判词（⚠️）。

    `dots`（2026-11-02 新增）= 右区**空地圆点**坐标。给了它就能精确锁定形状
    （四层 1↔4 的格集互相包含，没有圆点会多解）。
    空地按玩家 2026-11-02 澄清 = **0 分中继（可穿过）**：会作为「空地」格接入图里、
    并按 `cell_values` 记 0 分；穿过它的形状表边**保留**——但前提是**知道那一格的坐标**：
    没给 `dots` 时只能知道「有几个空地」，不知道「在哪」，连到它的边接不进图
    （如实记进 `meta["dropped"]`，判词降 ⚠️；不是「空地是墙」）。
    调用方要把这些格从 `block` 里豁免（写进 `block_except`），否则求解器会把它们删掉。

    `meta`（可选，dict）会回填：`topology` ∈ {table, ambiguous, none}、`shape`、`dots`、
    `empty_cells`、`dropped`（没接进图的形状表边；见 `match_right_shape`）。
    返回 (右区格子列表, 说明列表)——与旧签名一致（selftest 按 2 元组解包）。

    `lm` 不传时按 `left_max(layer)` 推。**调用方必须和分阶段求解用同一个 lm**，
    否则「哪些格算右区」两处不一致（L8 就踩过）。
    """
    dots = [tuple(q) for q in (dots or ())]
    if layer not in (3, 4, 5):
        return [], []
    if lm is None:
        lm = left_max(layer)
    extra = sorted({q for q in grid if q[0] > lm})
    if meta is not None:
        meta.setdefault("topology", "none")
        meta["dots"] = dots
    if not extra:
        return [], ["右区：图上没读到缝之后的格子（切一边截图或缺右区？）"]
    notes = []
    want_n = EXPECTED_RIGHT_CELLS.get(layer)
    if want_n and len(extra) != want_n:
        notes.append(
            f"⚠️ 右区格数 {len(extra)} ≠ 固定构成 {want_n}（{layer} 层应为 {want_n} 节点）"
            f"——多半是 OCR 漏了暗图标/多检了，**先核对截图再算**，否则会安静地少算一整格")
    if dots:
        _bad = [q for q in dots if q[0] <= lm]
        if _bad:
            notes.append("⚠️ 报来的空地圆点 " + "、".join(f"({q[0]},{q[1]})" for q in _bad)
                         + " 落在左区 → 右区匹配时忽略")
            dots = [q for q in dots if q[0] > lm]
        if meta is not None:
            meta["dots"] = dots
        _dup = [q for q in dots if q in set(extra)]
        if _dup:
            notes.append("⚠️ 报来的空地圆点 " + "、".join(f"({q[0]},{q[1]})" for q in _dup)
                         + " 其实读到了标签 → 按节点算，不当空地")
            dots = [q for q in dots if q not in set(extra)]
    labeled = list(extra)          # 只有**有标签**的格（空地圆点不算），形状匹配用它
    for q in extra:
        cells.add(q)
    # 空地格也要进图（0 分中继、可穿过）：形状表里的边会用到它们。
    # 只加进 cells/edges，不计入「右区节点数」校验（那是**有标签**节点的定数）。
    for q in dots:
        if q not in extra:
            extra = sorted(list(extra) + [q])
            cells.add(q)
    if meta is not None:
        meta["empty_cells"] = list(dots)
    how, name, _cells, eg, dropped = match_right_shape(layer, labeled, dots, probe=probe)
    if meta is not None:
        meta["topology"] = how
        meta["shape"] = name
        meta["dropped"] = dropped
    if how == "table":
        for u, v in eg:
            edges.append((u, v))
        _dot_tail = (f"；空地圆点 {len(dots)} 个按**0 分中继**接入（可穿过）" if dots else "")
        if dropped:
            # 边缺着不等于「地图上没这条边」：空地位置没报，那一格进不了图。
            # 判词必须降级（route.py 会把它算进 warnings），不能写「已核验」就完事。
            # 多个候选形状时 dropped 是并集（见 match_right_shape），说明里点明「最多」。
            _n_cand = len(name.split("（")[0].split("/"))
            _n_multi = ("（%d 个候选形状的并集，最多这么多）" % _n_cand if _n_cand > 1 else "")
            notes.append(
                f"右区边表：按 references 形状表「{name}」连线 {len(eg)} 条"
                f"（已知格之间的边已核验）{_dot_tail}。"
                f"⚠️ 另有 **{len(dropped)} 条边没接进来**{_n_multi}——端点落在**未探知的空地**上"
                "（没报 --right-dots 时只能知道「有几个空地」，不知道「在哪」）"
                "→ 右区可能少一条通路，结论会偏保守；报 --right-dots c,r 可锁定并补全")
        else:
            notes.append(f"右区边表：按 references 形状表「{name}」连线 {len(eg)} 条"
                         f"（**已核验**，非四邻接假设）{_dot_tail}")
    else:
        # 回退：四邻接（旧口径）。ambiguous = 空地位置看不出来，多种形状边集不同。
        for q in extra:
            for du, dv in ((1, 0), (0, 1)):
                nb = (q[0] + du, q[1] + dv)
                if nb in extra:
                    edges.append((q, nb))
        why = ("形状表解析失败/没有形状对得上" if how == "none"
               else f"形状多解（{name}）——空地落在哪一格看不出来，边集不一致")
        if dots:
            why += f"；已报空地 {len(dots)} 个但仍未唯一对上"
        notes.append(f"⚠️ 右区边表：**未验证**，按 4 邻接临时假设（{why}）")
        notes.append("若右区实际有暗线不连通、或有斜向连接，这条假设会让路线错"
                     "——按形状表人工核一遍（12-三层全图 / 16-四层全图），"
                     "或把右区空地小圆点的坐标用 --right-dots c,r;c,r 报出来")
    seam = 0
    for q in extra:
        for du, dv in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (q[0] + du, q[1] + dv)
            if nb in cells and nb not in extra:
                edges.append((q, nb))
                seam += 1
    if seam:
        notes.append(f"右区：与左区之间的缝边 {seam} 条（几何 4 邻接，左区最右列 ↔ 右区最左列）")
    return extra, notes


def source_wall_cells(nodes, acorn_cells):
    """六层源流：**不带橡果徽章的林间空地**全部按墙处理（玩家 2026-10-30 口径）。

    与盲目者所见同口径；带橡果（迷藏）的空地保留（可进，**收益 = 橡果 12 分**）。
    `nodes` 是 {“x,y”: 类型}；返回要删掉的格 key 集合。
    """
    acorn_keys = {RS.key_xy(tuple(q)) for q in (acorn_cells or [])}
    return {k for k, t in nodes.items() if t == "空地" and k not in acorn_keys}


def acorn_portal_pairs(nodes, acorn_cells):
    """带橡果的**曲折密道** → 强制传送对（scenario 的 `portals`）。

    玩家口径（2026-10-30）：带橡果的密道**放行**，收益 = 橡果 12 分，
    但**踩上去会被强制传到另一端** —— 所以必须写 `portals`，否则求解器会以为
    能从密道原地走出去（错得离谱）。

    密道**成对出现**，故只读到恰好 2 个才能确定配对；数量不对就不放行（宁可保守）。
    返回 `(pairs, notes)`：pairs = [((x1,y1),(x2,y2))]。
    """
    dungeons = sorted(RS.xy(k) for k, t in nodes.items() if t == "曲折密道")
    acorn = {tuple(q) for q in (acorn_cells or [])}
    hit = [q for q in dungeons if q in acorn]
    if not hit:
        return [], []
    if len(dungeons) != 2:
        return [], [f"⚠️ 带橡果的曲折密道在 {hit}，但本层读到 {len(dungeons)} 个密道格"
                    f"（应成对 = 2）→ 配对不确定，**暂不放行**（仍按禁区）；"
                    f"请报出另一端坐标后再算"]
    a, b = dungeons
    return [(a, b)], [f"迷藏（橡果）：曲折密道 {a}↔{b} **放行**（强制传送；两端各记 12 分、"
                      f"只结算一次）"]


def acorn_cell_values(nodes, values, acorn_cells, portal_pairs=()):
    """迷藏（橡果）：**把橡果收益并进所在节点**（玩家 2026-10-30 新口径）。

    返回 scenario 的 `cell_values`（{"x,y": 分}）：值 = 该格现有收益 + `rules.acorn_value()`。
    「-99」是**墙标记**不是收益，按 0 起算 —— 所以**带橡果的空地 / 密道 / 险路尽头 = 12 分**
    （`rules.acorn_base_value` 统一这条口子）。

    **密道配对的两端都要写 12**：求解器的结算格是**落点**（踩 A 会落在 B），
    两端同价才能保证「从哪头进都拿到 12」；重复结算由求解器把两端 bit 一起置位来挡
    （`moves()` 里 i_in）。

    ⚠️ **必须在 `infer_unknown` 推完期望之后再调**：未知的诡秘/凶戾会被改写成
    `诡秘@2,3` 这类逐格价类型名，早算的话会拿到层均值而不是这一格的真实价。
    """
    out = {}

    def _put(q):
        k = RS.key_xy(tuple(q))
        t = nodes.get(k)
        # 起点/已访问 是**记账类型**（0 分），不是「还没拿的节点」：
        # 带橡果的已走格不该再发一次 12 分（求解器也会把它 mask 掉）。
        if t is None or t in ("起点", "已访问"):
            return
        base = R.acorn_base_value(values.get(t, 0))
        out[k] = round(base + R.acorn_value(), 4)

    for q in acorn_cells or []:
        _put(q)
    for a, b in portal_pairs or []:
        _put(a)
        _put(b)
    return out


def acorn_block_except(nodes, block, acorn_cells, portal_pairs=()):
    """带橡果、本该被 `block` 却要**放行**的格子。

    玩家 2026-10-30 口径（逐条对着玩家的话）：

    | 类型 | 带橡果时 | 理由 |
    |---|---|---|
    | 林间空地 | **放行** | 0 分地基 + 12 = 12 分，可进路线 |
    | 曲折密道 | **放行**（仅配对成功时） | 收益 12；进入即强制传送（写 `portals`） |
    | 险路尽头 | **放行** | 12 分；全程追猎可当中继，险路尽头模式建议留作最后一步 |
    | 险路恶敌 | **不放行**（全程追猎） | 玩家口径：仍为 block。险路尽头模式下它本来就是终点 |
    | 命运所指（三/四层） | **不放行** | 玩家口径：即使带橡果也不进 |

    只有「空地 / 密道（且配对成功） / 险路尽头」这三类会进返回值。
    """
    out = []
    blocked = set(block or ())
    pair_cells = {tuple(q) for pr in (portal_pairs or ()) for q in pr}
    for q in acorn_cells or []:
        k = RS.key_xy(tuple(q))
        t = nodes.get(k)
        if t not in blocked or t not in R.ACORN_PASS_TYPES:
            continue
        if t == "曲折密道" and tuple(q) not in pair_cells:
            continue        # 配对不确定 → 不放行（否则会「从密道原地走出去」）
        out.append([q[0], q[1]])
    return out


def acorn_wasted_cells(nodes, block, acorn_cells, portal_pairs=()):
    """橡果落在**禁踩格**上 → 这一层迷藏吃不到（给玩家一句实话，别静默）。

    放行规则见 `acorn_block_except`：空地/密道/险路尽头会放行，
    **险路恶敌（全程追猎）与三·四层命运所指不放行** → 这两类上的橡果只能浪费。
    返回 [(坐标, 类型)]；空 = 全部都能拿。
    """
    except_keys = {RS.key_xy(tuple(q))
                   for q in acorn_block_except(nodes, block, acorn_cells, portal_pairs)}
    out = []
    blocked = set(block or ())
    for q in acorn_cells or []:
        k = RS.key_xy(tuple(q))
        t = nodes.get(k)
        if t in blocked and k not in except_keys:
            out.append((tuple(q), t))
    return out


def acorn_soft_final_types(nodes, values, acorn_cells, end_types):
    """带橡果的终点节点 → 该类型降级为**软终点**（可当中继），但只在基础价为 0 时。

    玩家 2026-10-30 口径：险路尽头模式下，带橡果的险路尽头/小径「可作为中继节点放行，
    收益 = 橡果 12」，但**更建议把它留给最后一步**（12 分 + 正好满足终点约束）。
    求解器里「可当中继」= 软终点（`final_soft_types`）：停这里算合法收尾，继续走也不算违规。

    ⚠️ **二层例外不放开**：那里尽头/小径是 8 分（四结局道具），放开会让
    「尽头 8 + 小径 8」被重复计 —— 判据是**该类型基础价必须为 0**。
    险路恶敌也不在此列（玩家口径：全程追猎仍 block、险路尽头模式按正常逻辑收尾）。
    """
    out = []
    ts = set(end_types or ())
    for q in acorn_cells or []:
        t = nodes.get(RS.key_xy(tuple(q)))
        if t in ts and t in ("险路尽头", "险路小径") and float(values.get(t, 0)) == 0.0:
            if t not in out:
                out.append(t)
    return out


def block_types(layer, cfg=None):
    """该层**按不可达处理**的节点类型（求解器会直接从图里删掉这些格）。

    原则：**有墙就硬删，别靠 -99 分挡**。分数是求和，
    只要整条路线总分压得过那个负数，求解器就会把它当 0 价中转走过去。

    | 类型 | 哪些层 | 理由 |
    |---|---|---|
    | 险路恶敌 | 全部层 | 负收益的强制战斗（-6），没有「值得踩」的场合 |
    | 险路尽头 | **除二层**（一/三/四/五/六） | 负收益；**二层是四结局道具（+8），必须可进** |
    | 空地 | 三层起（**六层源流除外**） | 一/二层地图稀疏，连通常常必须借空地（合法中继） |
    | 曲折密道 | 三层起 | 一/二层不出现；三层起是强制传送的 0 收益格 |
    | 命运所指 | 三 / 四 | 不可进入的墙；五层它是**终点**，绝不能删 |

    **2026-10-30 六层「源流交汇处」例外**：图上的无标签小圆点是**林间空地**
    （可通行、**0 分**），不是墙——player 口径。所以 `cfg=源流` 时**不 block 空地**，
    对应 `layer_values` 里把空地改成 0（否则按 -99 定价）。

    **险路尽头模式**改写：本层终点类型（一/四层 → 险路尽头**及险路小径**；二层默认只有险路尽头；
    三/五/六层 → 险路恶敌）不再 block——它是必进的最后一步，`final_type` 约束保证「碰到即结束」。
    **险路小径永不 block**（一/四层终点模式下它与尽头同分、是最后一步候选；二层默认 0 分、
    只有「四结局=不要 或 已获取前置」时才与尽头同分 8，见 `rules.ending_types(2)`）。
    """
    layer = int(layer)
    end_ts = R.ending_types(layer) if R.is_end_jintou() else ()
    out = []
    if "险路恶敌" not in end_ts:
        out.append("险路恶敌")          # 终点模式下本层恶敌是终点，不能删
    if "险路尽头" not in end_ts and layer != 2:
        out.append("险路尽头")          # 二层留着（四结局道具，+8）；终点模式下本层尽头不能删
    if layer >= 3:
        # 六层源流：无标签格是林间空地（0 分、可通行），不能按墙删
        if int(layer) == 6 and "源流" in str(cfg or ""):
            out.append("曲折密道")
        else:
            out += ["空地", "曲折密道"]
    if layer in (3, 4):
        out.append("命运所指")          # 五层是终点，不能删
    return [t for t in out if t != "险路小径"]


def read_grid(path, layer, cfg, rows):
    """读图 + 自标定 + 定点覆盖，返回 (grid, start, forced, gx, gy, cols, ginfo)。

    三/四/五层可能没有右区（2026-10-30）：按「完整宽度 vs 左区宽度」A/B 选列，
    不再无条件按 11/12/14 列硬套（左区居中时会把格点顶出地图区）。
    """
    labels = LO.read_image(path)
    # center：屏幕中心（噪声级平局判决，防「最左列无标签 → 整图偏一列」，2026-10-29b）
    _W, _H = Image.open(path).size
    if int(layer) == 6 and "源流" in str(cfg or ""):
        # 三结局六层「源流交汇处」：6 列 × 5 行，没有右区概念
        cols = LO.layer_dims(6, "源流")[0]
        gx = sorted(LO.choose_lattice([l[0] for l in labels], cols, center=_W / 2.0))
        ginfo = {"has_right": False, "bounds_ok": True}
    else:
        cols, gx, ginfo = LO.choose_layer_cols([l[0] for l in labels], layer, _W, cfg=cfg)
    gy = LO.choose_lattice([l[1] for l in labels], rows, center=0.49 * _H)
    gx, gy = sorted(gx), sorted(gy, reverse=True)
    # 小人锚点自证：最左列只有起点（无标签）时，标签聚类会把 5 列套在 4 列上、
    # 整体右移一列，且标签→图标残差仍为 0（2026-10-29 一层实测）。
    gx, gy, _align_note = LO.align_lattice(path, labels, gx, gy, cols, rows)
    if _align_note:
        print("  " + _align_note)
    grid = LO.assign(labels, gx, gy, cols, rows)
    im = Image.open(path).convert("RGB")
    # 白圈徽章（2026-10-30）：不期而遇+字形 = 泪之聚落/线人；失与得/作战不改价；
    # 橡果=迷藏（记坐标，稍后把 +12 并入所在节点）。必须在建 nodes 之前改写 grid。
    try:
        _bd = LO.apply_badges(im, grid, gx, gy, cols, rows, layer=layer)
        ginfo["badge_ok"] = True
        for _bn in _bd["notes"]:
            print("  徽章：" + _bn)
        ginfo["acorn"] = _bd["acorn"]
        # 五层二结局（2026-10-31）：命运所指 + 徽章 = 二结局关卡入口，坐标交给上层判定终点
        ginfo["fate_badge"] = _bd.get("fate_badge") or []
        if ginfo["fate_badge"]:
            print("  命运所指+徽章："
                  + "、".join(f"({q[0]},{q[1]})" for q in ginfo["fate_badge"])
                  + "（五层=二结局关卡入口）")
        if _bd["acorn"]:
            print("  迷藏（橡果）：" + "、".join(f"({q[0]},{q[1]})" for q in _bd["acorn"])
                  + f" → 收益 +{R.acorn_value():g} 并入所在节点（带橡果的空地放行）")
    except Exception as _be:
        ginfo["badge_ok"] = False
        print(f"  !! 徽章检测失败：{_be}（橡果/泪之聚落/二结局入口定价未验证 → 写入 validation_status）")
    # 起点用**候选 + 构型表核对**：小人画在节点左侧，天生有半格横偏差，
    # 2026-10-28 三层那次偏了整整一列（检测 (1,2)=曲折密道，真实 (0,2)）。
    cands = LO.detect_start_candidates(im, gx, list(reversed(gy)), labels)
    cand_cells = [(c, rows - 1 - r) for c, r in cands]
    st = cands[0] if cands else None
    start = (st[0], rows - 1 - st[1]) if st else None
    cfg_start = config_start(layer, cfg)
    if start and cfg_start and tuple(start) != cfg_start and cfg_start in cand_cells:
        print(f"  !! 起点检测偏到 {tuple(start)}，按构型表校正为 {cfg_start}"
              f"（候选里有它；构型{cfg} 的起点是硬事实）")
        start = cfg_start
    forced = {}
    # 二层也有险路尽头（2 个都能进；四结局=要时值 8、走 1 次；
    # 四结局=不要时值 0、不再必走）——2026-10-28 前这里漏了 layer 2，
    # 于是伪装成「未知的诡秘」的尽头会被按 2 分铺进去。
    # 六层也有定点节点（(0,2)/(4,3) 紧急作战、(3,3) 行商 …），同样要覆盖。
    if layer != 5:
        ends, fixed = AC.config_fixed(layer, cfg)
        for q in ends:
            forced[q] = "险路尽头"
        for q, t in fixed.items():
            forced[q] = t
    return grid, start, forced, gx, gy, cols, ginfo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--layer", type=int, required=True, choices=sorted(PRICES))
    ap.add_argument("--config", required=True)
    ap.add_argument("--ap", type=int, required=True)
    ap.add_argument("--start", default=None, help="手报起点，如 0,0")
    ap.add_argument("--mode", default=None, choices=R.mode_choices(),
                    help="分队模式（SKILL.md 〇·〇）：N16文明开化（内部名「文明开化」）=旧默认"
                         "（通过追猎进入二~六层时大概率出现理想源、+2AP+4藏品；推荐搭配全程追猎）；"
                         "普通分队=不保证有理想源（不传 --root 即按无理想源算，"
                         "理想源价值=紧急作战、无 +2AP）。不传 = N16文明开化")
    ap.add_argument("--end-mode", dest="end_mode", default=None, choices=list(R.END_MODES),
                    help="终点模式（与分队模式**独立**）：全程追猎=旧默认（尽头/恶敌照旧禁踩）；"
                         "险路尽头=一/四层最后一步落在险路尽头**或险路小径**（同分值）、"
                         "**二层默认只能落险路尽头**（四结局=不要，或 要且已获取前置藏品时，"
                         "小径与尽头同分、可作可选终点）、三/五/六层必须落在险路恶敌"
                         "（之前不进尽头/恶敌；终点不再 block）。不传 = 全程追猎")
    ap.add_argument("--final", default=None,
                    help="终点坐标手报（仅险路尽头终点模式），如 3,0。六层恶敌 OCR 漏检等"
                         "构型表覆盖不到的场合用；以人报的为准")
    ap.add_argument("--right-zone", dest="right_zone", default="自动",
                    choices=("自动", "有", "无"),
                    help="三/四/五层右区（熄日余径）有无：默认按格点宽度自动判；"
                         "拿不准时先问玩家「本局是否存在熄日余径？」再传 有/无")
    ap.add_argument("--right-dots", dest="right_dots", default="",
                    help='右区**空地小圆点**坐标（手报，如 "10,3;9,4"）。'
                         "有了它形状才能唯一锁定（四层 1↔4、2↔3 的格集互相包含，"
                         "没有空地位置永远多解 → 只能 ⚠️）；空地是 **0 分中继（可穿过）**，"
                         "报出来才能把穿过它的形状表边接进图里（不报就只能丢边、结论偏保守）")
    ap.add_argument("--auto-dots", dest="auto_dots", action="store_true",
                    help="自动检测右区空地圆点（**实验性**，实拍标定还差一个样本；"
                         "检测不到不报错，只是退化回「多解 → ⚠️」）")
    ap.add_argument("--four-ending", dest="four_ending", default=None,
                    metavar="要|不要",
                    help="**二层必答**：本局是否要打四结局？全程追猎：要=尽头 8；不要=尽头 0、"
                         "不再必走。险路尽头：要→再问 --four-ending-pre；"
                         "不要→小径=尽头、可作可选终点")
    ap.add_argument("--four-ending-pre", dest="four_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="二层·险路尽头模式·四结局=要 的必答问题二：是否已获取四结局前置藏品？"
                         "已获取→小径=尽头；未获取→小径 0、不作终点")
    ap.add_argument("--three-ending", dest="three_ending", default=None,
                    metavar="要|不要",
                    help="**三层必答（仅本层无右区/熄日余径时）**：本局是否打三结局？"
                         "不要→普通逻辑；要→再问 --three-ending-pre。有右区时这道题不适用")
    ap.add_argument("--three-ending-pre", dest="three_ending_pre", default=None,
                    metavar="已获取|未获取",
                    help="三层·三结局=要 的必答问题二：是否已获取三结局前置收藏品？"
                         "未获取→「先行一步」按 15 分必走点；藏着就列候选坐标去找")
    ap.add_argument("--second-ending", dest="second_ending", default=None,
                    metavar="要|不要",
                    help="**五层必答（仅左区出现 3 个「命运所指」时）**：本局是否打二结局？"
                         "不要→常规逻辑、终点=右区命运所指；要→右区命运所指必走（尽量排最后，"
                         "进入后加工品次数强制→1），最后一步=左区带徽章的命运所指")
    ap.add_argument("--second-ending-node", dest="second_ending_node", default=None,
                    metavar="c,r",
                    help="五层·二结局=要 时，左区**带徽章**那个「命运所指」的坐标（手报，如 3,1）")
    ap.add_argument("--root", default=None,
                    help="理想源（树根）坐标，如 3,0。树根**地图上永远没有标签**，只能玩家口报："
                         "N16文明开化模式二~六层**必传**（追猎进入时大概率出现）；"
                         "普通分队模式可不传（不传=无理想源）")
    ap.add_argument("--grants", default="", help='如 "3,0:2;1,3:1"')
    ap.add_argument("--items", default="",
                    help='默认按**件数**："标准引擎:1" = 1 件（耐久 3 次）；'
                         '只剩 N 次要写 "标准引擎:剩1次"，或 "标准引擎:2*1"（2 件各剩 1 次）')
    ap.add_argument("--out", default=None)
    ap.add_argument("--badge-none", dest="badge_none", action="store_true",
                    help="玩家口报**本局没有白圈徽章**：徽章检测崩溃时把 validation_status "
                         "从 INVALID 降为 WARNING（橡果/泪之聚落/二结局入口仍未验证）")
    ap.add_argument("--visited", default="",
                    help="**滚动重算**用的「已经走过/已经触发过的格子」，如 \"6,3;4,0\"。"
                         "这些格子不再计分、其中的起点格 grant 视为已领（见 route.py 同名参数）")
    a = ap.parse_args()

    # 两个模式开关都要在任何定价/推断/求解之前定下（树根价格、block、终点约束都按它们算）
    R.set_mode(a.mode)
    R.set_end_mode(a.end_mode)

    # 二层「四结局」问询（2026-10-30 玩家新增）：**先问玩家，再建场景**。
    try:
        R.set_four_ending(a.four_ending)
        R.set_four_ending_pre(a.four_ending_pre)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")
    _ask_errs = R.layer2_answer_errors(a.layer)
    if _ask_errs:
        # 返回码必须和 route.py 一致（exit 2 = 「先去问玩家」），不是普通的 exit 1。
        print("❌ 二层的「四结局」问询还没完成——**先问玩家，再建场景**：\n   - "
              + "\n   - ".join(_ask_errs)
              + "\n   · 全程追猎：只问「本局是否要打四结局？」→ --four-ending 要|不要"
              + "\n   · 险路尽头：先问 Q1；答「要」再问 Q2「是否已获取四结局前置藏品？」"
                "→ --four-ending-pre 已获取|未获取")
        return 2

    # 三层「三结局」问询（2026-10-30 玩家新增）：只在**本层无右区**时问。
    # 先挡「用错层」（不需要读图）；右区有无要等读图，见下面的 _ask3。
    try:
        R.set_three_ending(a.three_ending)
        R.set_three_ending_pre(a.three_ending_pre)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")
    _e3 = R.layer3_scope_errors(a.layer)
    if _e3:
        raise SystemExit("❌ " + "\n   - ".join(_e3))

    # 五层「二结局」问询（2026-10-31 玩家新增）：先挡「用错层」，是否真有 3 个命运所指
    # 要等建完节点表（见下面 lm 之后的 _ask5）。
    try:
        R.set_second_ending(a.second_ending)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")
    _e5 = R.layer5_scope_errors(a.layer)
    if _e5:
        raise SystemExit("❌ " + "\n   - ".join(_e5))

    # 2026-10-29 修复：全程追猎下 --final 以前被静默忽略（exit 0、JSON 里没有 final），
    # 与 route.py 的判词不一致。在建场景/读图**之前**就明确拒绝。
    if a.final and not R.is_end_jintou():
        raise SystemExit("❌ --final 只在「险路尽头」终点模式下有意义（本次是全程追猎，未写入）")

    # 坐标先校验：写错时给可读的 ❌，不再让 RS.xy 抛 ValueError traceback
    for _flag, _val in (("--start", a.start), ("--root", a.root), ("--final", a.final),
                        ("--second-ending-node", a.second_ending_node)):
        if not _val:
            continue
        try:
            RS.xy(_val)
        except ValueError as exc:
            raise SystemExit(f"❌ {_flag} 坐标格式错误：{exc}（正确写法如 3,0）")

    rows = LO.layer_dims(a.layer, a.config)[1]
    _six = (a.layer == 6 and "源流" in str(a.config or ""))
    edges = config_edges(a.layer, a.config)
    grid, start, forced, gx, gy, cols, ginfo = read_grid(a.image, a.layer, a.config, rows)
    acorn_cells = ginfo.get("acorn") or []
    has_right = LO.has_right_cols(a.layer, cols)
    if a.right_zone != "自动" and a.layer not in (3, 4, 5):
        raise SystemExit(f"❌ --right-zone {a.right_zone} 只对三/四/五层有意义")
    if a.right_zone == "有" and not has_right:
        raise SystemExit("❌ 你指定本层有右区，但截图按左区宽度拟合（可能只截了左区）——"
                         "重截完整宽度，或改 --right-zone 无")
    if a.right_zone == "无" and has_right:
        raise SystemExit("❌ 你指定本层无右区，但截图是完整宽度（含右区列）——"
                         "先确认是否已获取四结局前置藏品/截图是否完整")
    # 三层·无右区 → 三结局问询（2026-10-30 玩家新增，强制）。
    # 有右区 = 四结局线，这道题不适用；无右区没答 → 停在提问，不许先建场景。
    _ask3 = R.layer3_answer_errors(a.layer, has_right)
    if _ask3:
        print("❌ " + R.layer3_error_headline(_ask3, "建场景") + "：\n   - "
              + "\n   - ".join(_ask3)
              + "\n   · 本题只在**本层无右区（熄日余径）**时问；Q1=要 再问 "
                "Q2「是否已获取三结局前置收藏品？」"
              + "\n   · 参数：--three-ending 要|不要；要 再加 --three-ending-pre 已获取|未获取")
        return 2
    # 左右区边界**只认这一个 lm**（L8 时左区只到 x=8），右区拼装与推断都用它
    lm = left_max(a.layer, cols, a.config)
    if a.start:
        start = RS.xy(a.start)
    if start is None:
        raise SystemExit("!! 起点未检测到，请用 --start 手报（禁止猜：起点决定构型）")

    # 起点校验：构型表声明了起点就必须一致。
    # **滚动重算例外**（2026-10-29）：此时 --start 报的是玩家**当前所在格**，不是进层起点，
    # 拿它跟构型起点比会误判「构型选错」（三层实测：站在 (6,3) 打树根后被判 ❌）。
    cfg_start = config_start(a.layer, a.config)
    rolling = bool(a.visited)
    if cfg_start and tuple(start) != cfg_start and not rolling:
        raise SystemExit(f"❌ 起点不符：检测到 {tuple(start)}，但构型{a.config} 的起点是 {cfg_start}。"
                         f"\n   起点决定构型 → 要么构型选错，要么起点读错，先查清再建场景。"
                         f"\n   （若这是**滚动重算**、起点是当前所在格，传 --visited 即可按当前位置建场景）")
    elif cfg_start and tuple(start) != cfg_start and rolling:
        print(f"  滚动重算：起点按**当前所在格** {tuple(start)} 处理（进层起点 {cfg_start} 不参与校验）")

    cells = {p for e in edges for p in e}
    # 右区（三/四/五层）：构型表里没有，必须从图上补——没有这一步，
    # build_scenario 与 route.py 会给出**不同的格子集合**（2026-10-28 前就是这个状态）。
    # 2026-10-30：**没有右区**（未获取四结局前置藏品）时，左区居中；
    # 不拼右区、不预留 AP、右区必走点/收益全部失效。
    extra_cells, rz_notes = [], []
    _rz_meta = {}
    _right_dots, _dot_notes = [], []
    if a.layer in (3, 4, 5):
        if has_right:
            try:
                _right_dots, _dot_notes = resolve_right_dots(
                    a.image, grid, gx, gy, lm,
                    manual=a.right_dots, auto=a.auto_dots)
            except ValueError as exc:
                raise SystemExit(f"❌ {exc}")
            for _n in _dot_notes:
                print("  " + _n)
            _probe = None
            if _right_dots:
                # 格集相同、边集不同的形状（四层 2↔3）只能靠屏幕上的连线定案
                try:
                    _probe = LO.make_right_edge_probe(a.image, gx, gy)
                except Exception as _pe:               # noqa: BLE001  探针是加分项，不是必需
                    print(f"  ⚠️ 右区连线探针不可用：{_pe}")
            extra_cells, rz_notes = right_zone(a.layer, grid, cells, edges, lm,
                                               meta=_rz_meta, dots=_right_dots,
                                               probe=_probe)
            if not extra_cells:
                raise SystemExit("❌ 截图是完整宽度、本层应有右区，但右区一格都没读到——"
                                 "可能切边/OCR 漏检；先重截或核对，别生成缺右区的 scenario")
        else:
            rz_notes = ["右区：无（本层无熄日余径；左区居中）→ 不预留 AP、右区必走点/收益失效"]
    cells = sorted(cells)

    # 起点闸门（2026-10-29 修复）：起点必须落在构型（+右区）格子里。
    # 以前只核对 cfg_start（三/四/五层常常没有），构型外的起点会被
    # `nodes.setdefault(start_key, "起点")` 塞成**孤立节点**——
    # 安静地写出一份跑不出路线的 scenario（route.py 有 outside 闸门，这里补上）。
    if start not in cells:
        raise SystemExit(
            f"❌ 起点 {tuple(start)} 不在构型{a.config} 的格子里（构型选错或起点读错）。\n"
            f"   起点决定构型：先用「起点 + 尽头/恶敌」核对构型，再用 --start 手报。")

    nodes = {}
    start_key = RS.key_xy(start)
    for q in cells:
        t = forced.get(q) or (grid[q][0] if q in grid else "空地")
        # 六层「源流交汇处」：无标签格是**林间空地**（可通行、0 分），保持"空地"
        # 右区节点改名：右区作战 16 / 右区紧急 21 ≠ 左区 0 / 9
        if q in extra_cells:
            t = R.right_zone_type(t)
        nodes[RS.key_xy(q)] = t
    nodes.setdefault(start_key, "起点")
    if nodes[start_key] in ("空地",):
        nodes[start_key] = "起点"

    # ── 五层·二结局问询（2026-10-31 玩家新增）──────────────────────────
    # 判据：层数=5 且左区读到 3 个「命运所指」= 拿了二结局相关藏品 → 先问再建场景。
    _se_left, _se_right = layer5_fate_cells(nodes, lm) if a.layer == 5 else ([], [])
    _se_marker = bool(a.layer == 5 and len(_se_left) >= R.SECOND_FATE_MARKER_COUNT)
    _ask5 = R.layer5_answer_errors(a.layer, _se_marker)
    if _ask5:
        print("❌ 五层的「二结局」问询还没完成——**先问玩家，再建场景**：\n   - "
              + "\n   - ".join(_ask5)
              + "\n   · 参数：--second-ending 要|不要；"
                "要 时可用 --second-ending-node c,r 锁定带徽章那个")
        return 2
    if _se_marker:
        print("  二结局：左区读到 3 个「命运所指」"
              + "、".join(f"({q[0]},{q[1]})" for q in _se_left)
              + f"（右区 {len(_se_right)} 个）→ 本层是二结局强化局")

    # 树根：地图上显示成「未知的凶戾 / 紧急作战」，只能玩家口报。
    # 必须在「紧急作战配额」推断**之前**落格，否则它会多占一个配额。
    # 普通分队模式：不报 = 本层无理想源（正常路径，不是漏报）。
    if a.root:
        rk = RS.key_xy(a.root)
        if rk not in nodes:
            raise SystemExit(f"!! 理想源 {rk} 不在该构型的格子里（构型选错，或坐标写错）")
        nodes[rk] = "树根"
    elif R.is_normal_squad():
        print("  模式：普通分队 —— 未报 --root，本层按**无理想源**求解")
    elif a.layer in (2, 3, 4, 5, 6):
        # 2026-10-29 修复：二/六层漏报理想源原来也没有提示（route.py 只提示 3/4/5）。
        # 二层漏报 = 少 15 分；六层漏报 = 少 2 步（追猎 4→6 / 险路尽头 5→7）。
        print("  ⚠️ N16文明开化模式：通过追猎进入二~六层时**大概率**出现理想源，本次未传 --root c,r：\n"
              "     本层若有理想源，会漏它的分值与 +2 AP（二层少 15 分、六层少 2 步）——\n"
              "     确认这层真没有理想源再继续；有的话带上 --root 重跑")

    # 六层源流：block 所有**不带橡果徽章**的林间空地（与盲目者所见同口径）；
    # 带橡果的空地（迷藏）保留：可进、0 分、同分优先。玩家没加工品走不到就如实报 ❌。
    if _six:
        _wall = source_wall_cells(nodes, acorn_cells)
        if _wall:
            _wall_t = {RS.xy(k) for k in _wall}
            edges = [[x, y] for x, y in edges
                     if RS.xy(x) not in _wall_t and RS.xy(y) not in _wall_t]
            for _k in _wall:
                nodes.pop(_k, None)
            cells = {p for e in edges for p in e}
            print("  源流：block 无橡果的林间空地 " +
                  "、".join(f"({q[0]},{q[1]})" for q in sorted(_wall_t)))

    grants = {}
    for part in filter(None, a.grants.split(";")):
        # 2026-10-29 修复：`--grants "3,0"`（漏冒号/漏数字）原来直接 ValueError traceback，
        # 现在给出可读的格式说明再退出
        try:
            k, v = part.split(":")
            grants[RS.key_xy(k)] = int(v)
        except ValueError:
            raise SystemExit(
                f"❌ --grants 格式错误：{part!r}\n"
                f"   正确写法是 \"c,r:AP\"，多段用分号：--grants \"3,0:2;1,3:1\""
                f"（坐标:回复行动力）")
    items = []
    try:
        # cfg 传进去：六层两张图次数规则不同（源流不夹、盲目者所见每件 1 次）
        items, item_notes = R.parse_items_spec(a.items, a.layer, a.config)
    except R.UnknownItem as e:
        raise SystemExit(f"!! 未知道具「{e}」。可用：{'、'.join(R.available_items())}")
    except ValueError as e:
        raise SystemExit(
            f"❌ --items 无法解析（{e}）——写法：标准引擎:2 / 标准引擎:剩1次 / 标准引擎:2*1")
    for line in item_notes:
        print("  道具：" + line)
    # 道具形状由 rules.parse_items_spec / make_item 保证（cov/only/exclude/free/bonus 齐全），
    # 件数→次数、**四结局六层每件强制 1 次**（三结局「源流」不夹）也都在那里做
    # （2026-10-28 起不再在这里二次 clamp）。

    # 定价 = 通用兜底 + 本层口径（与 route.py 同一来源，见 rules.py）。
    # **必须走 R.layer_values()**（模式感知）：普通分队模式下它会把
    # 树根/理想源覆盖成本层紧急作战价——直接抄 LAYER_PRICES 会漏掉这一步。
    values = dict(RS.DEFAULT_VALUES)
    values.update(R.layer_values(a.layer))
    if _six:
        values["空地"] = 0       # 六层源流：无标签格是林间空地（可通行、0 分）
    # 别名必须在推期望**之前**套：推断会覆盖「未知的凶戾」的逐格期望（见函数 docstring）
    apply_unknown_aliases(values)

    # 未知节点的期望值：距离带 + 数量约束（与 route.py 走同一个函数）
    # 用上面算好的 lm（L8 时是 8），不要再从 LEFT_MAX 取一遍
    secret_notes, quota_notes = infer_unknown(a.layer, nodes, values, edges, start, lm, a.root)
    for n in secret_notes:
        print("  推断：" + n)
    for n in quota_notes:          # 左区一句 + 右区一句都打（右区不许盖掉左区）
        print("  推断：" + n)

    # ── 五层·二结局：定价 / 必走点 / 终点 / 加工品次数钳制（2026-10-31）──
    se = second_ending_setup(
        a.layer, nodes, lm,
        fate_badge=ginfo.get("fate_badge") or [],
        answer=R.second_ending(),
        node_xy=RS.xy(a.second_ending_node) if a.second_ending_node else None)
    _se_prefix = "二结局：" if se.get("marker") else "五层终点："
    for n in se["notes"]:
        print("  " + _se_prefix + n)
    if se.get("error"):
        raise SystemExit("❌ " + se["error"])

    # 终点落格兜底（**必须在 derive_must 之前**：must 看到的得是纠正后的类型，
    # 否则 OCR 把恶敌误读成紧急作战时会把它锁成必走点，与终点约束自相矛盾）：
    # 三/五层的恶敌是构型表声明的地标硬事实，OCR 漏检也要落格；
    # 一/二/四层尽头已由定点覆盖强制落格；六层恶敌无构型表，靠 OCR 或 --final 手报。
    if R.is_end_jintou():
        _end_ts = R.ending_types(a.layer)
        if "险路恶敌" in _end_ts and a.layer in (3, 5):
            for q in sorted(config_landmarks(a.layer, a.config)):
                k = RS.key_xy(q)
                old = nodes.get(k)
                if old != "险路恶敌":
                    nodes[k] = "险路恶敌"
                    print(f"  终点兜底：构型表声明的险路恶敌 ({q[0]},{q[1]})"
                          + (f" 由「{old}」覆盖" if old else "（构型外格子，按地标落格）"))
        if a.final:
            fk = RS.key_xy(RS.xy(a.final))
            if fk in nodes:
                old = nodes.get(fk)
                # 人报终点：类型已在候选里就保留（如一层小径），否则按主类型覆盖
                nodes[fk] = old if old in _end_ts else _end_ts[0]
                print(f"  --final：({fk[0]},{fk[1]}) 由玩家手报，按「{nodes[fk]}」处理")
            else:
                sys.exit(f"!! --final {fk} 不在该构型的格子里")

    # 必走点：锁死坐标后再求解（求解器里是硬约束）
    must = derive_must(a.layer, nodes, extra_cells)
    # 五层·二结局=要：右区「命运所指」是必走点（进入后加工品次数强制→1，所以排最后更值钱）
    if a.layer == 5 and R.second_ending() is True and se.get("must"):
        _add5 = [q for q in se["must"] if q not in must]
        if _add5:
            must = list(must) + _add5
            print("  二结局：右区命运所指 "
                  + "、".join(f"({q[0]},{q[1]})" for q in _add5)
                  + " 锁进 must（尽量排最后：进入后加工品次数强制→1）")
    # 二层·全程追猎·四结局=要：险路尽头是**必走点**（进入一次）。
    # 两个尽头只算一次收益，所以这里取**离起点最近**的那个进 must（route.py 会两个候选各解一次择优；
    # build_scenario 只建场景，先锁最近的一个，避免求解器为了 10 分居民据点跳过四结局道具）。
    if a.layer == 2 and not R.is_end_jintou() and R.four_ending() is True:
        _ends = [RS.xy(k) for k, t in nodes.items() if t == "险路尽头"]
        _pick = nearest_cell(start, _ends, edges)
        if _pick and _pick not in must:
            must.append(_pick)
            print(f"  四结局=要：二层险路尽头 {_pick} 按必走点处理（离起点最近的一个）")
        elif not _ends:
            print("  ⚠️ 四结局=要，但图上没有险路尽头——先核对构型/OCR，别跳过四结局道具")

    # 不可达类型：**三层起**才把林间空地当墙（一/二层空地是合法中继，且不出现曲折密道）。
    # 三/四层的命运所指也是墙（只靠 -99 分挡不住），五层它是终点不能删。
    block = block_types(a.layer, a.config)

    # ── 滚动重算（--visited）──────────────────────────────────────────
    # 已走格统一按「起点」登记：0 分、且不再是 grant 格。
    # 理由与 route.py 同名参数一致（2026-10-29）：玩家口报的 --ap 是**领过树根 +2 之后**
    # 的余额，grant 若还挂着，求解器会把起点格再发一次 +2，预算虚高 2 点。
    visited = []
    for part in filter(None, a.visited.split(";")):
        try:
            visited.append(RS.xy(part))
        except Exception:
            raise SystemExit(f"❌ --visited 格式错误：{part!r}（正确写法 \"c,r\"，多段用分号）")
    # ── 三层·三结局：先行一步 = 15 分必走点（2026-10-30 玩家新增）──
    # 只在**本层无右区**时存在；有右区 = 四结局线，不问也不定价。
    # ⚠️ 必须排在「visited 格改成起点」**之前**：否则已走过的先行一步会被抹掉类型，
    # 反被误报成「还没探明」并列出假的候选坐标。
    if a.layer == 3 and not has_right:
        print(f"  三结局：{R.layer3_plan_text(3)}")
        if R.three_ending_need_xianxing():
            _revealed = [RS.xy(k) for k, t in nodes.items()
                         if t == R.THREE_XIANXING_NODE]
            if _revealed:
                print("  先行一步已探明：" + "、".join(f"({q[0]},{q[1]})" for q in _revealed)
                      + " → 已按 15 分必走点写进 must")
            else:
                _cand = xianxing_candidates(a.layer, nodes, edges, start, lm, visited)
                if _cand:
                    print("  ⚠️ 先行一步还没探明，可能藏在这些「未知的诡秘」里（距离带 2–4）：")
                    print("     " + "、".join(f"({q[0]},{q[1]}) 距{d}步" for q, d in _cand))
                    if len(_cand) == 1:
                        print("     → 三层左区必出 1 个先行一步，只剩这一格 → 它就是先行一步"
                              "（仍建议进场确认）")
                    print(f"     → 请在这 {len(_cand)} 个坐标里找「先行一步」；"
                          "进去就拿到三结局前置收藏品（找到后按新截图重算）")
                else:
                    print("  ⚠️ 先行一步还没探明，且没有距离 2–4 步的未知诡秘 → "
                          "本层找不到它（核对截图漏检/已走格）")

    for q in visited:
        k = RS.key_xy(q)
        if k in nodes:
            nodes[k] = "起点"
        grants.pop(k, None)

    # 已走格不该再出现在 must 里（route.py 早就这么做；build_scenario 以前漏了，
    # 滚动重算时可能要求「再走一遍」已经打掉的树根/先行一步）。
    if visited:
        _vkeys = {RS.key_xy(q) for q in visited}
        _drop = [q for q in must if RS.key_xy(q) in _vkeys]
        if _drop:
            must = [q for q in must if RS.key_xy(q) not in _vkeys]
            print("  必走点：已走格 " + "、".join(f"({q[0]},{q[1]})" for q in _drop)
                  + " 从必走里摘掉（本轮已经打过）")

    # ── 迷藏（橡果）：收益并进所在节点 + 特定类型从硬墙里放行（2026-10-30 新口径）──
    # 必须排在这里：节点类型已被「树根 / 终点兜底 / 已走格→起点」改完，
    # 收益也过了 infer_unknown 的逐格定价 —— 早一步算会拿到旧类型或层均值。
    # 放行规则（玩家逐条口径）：空地 / 曲折密道（配对成功才放） / 险路尽头 放行；
    # **险路恶敌（全程追猎）与三·四层命运所指不放行**。
    acorn_pairs, acorn_pair_notes = acorn_portal_pairs(nodes, acorn_cells)
    acorn_values = acorn_cell_values(nodes, values, acorn_cells, acorn_pairs)
    block_except = acorn_block_except(nodes, block, acorn_cells, acorn_pairs)
    for _n in acorn_pair_notes:
        print("  " + _n)
    if acorn_values:
        print(f"  迷藏（橡果）：{len(acorn_values)} 格收益已并入节点（每格 +{R.acorn_value():g} 分）："
              + "、".join(f"({RS.xy(k)[0]},{RS.xy(k)[1]})={v:g}"
                          for k, v in sorted(acorn_values.items())))
    if block_except:
        print("  迷藏（橡果）：以下格原本按硬墙 block，带橡果后**放行**："
              + "、".join(f"({q[0]},{q[1]})" for q in block_except))
    _wasted = acorn_wasted_cells(nodes, block, acorn_cells, acorn_pairs)
    if _wasted:
        print("  ⚠️ 橡果落在禁踩格 " +
              "、".join(f"({q[0]},{q[1]})={t}" for q, t in _wasted) +
              f"：这两类不放行（玩家口径），+{R.acorn_value():g} 分也不改变"
              " → **这一层迷藏吃不到**")

    # 逐格价值覆盖 = 五层二结局命运所指（无徽章 7 分 / 不要二结局时带徽章 -99）+ 迷藏橡果（+12）
    _cell_vals = dict(acorn_values or {})
    _cell_vals.update(se.get("cell_values") or {})
    # 右区空地（2026-11-02 玩家澄清：**0 分中继、可穿过**）：
    #   ① 必须从 block 里豁免——否则求解器把「空地」当墙删掉，穿过它的边全断；
    #   ② 逐格价写 0——层表里的「空地」是 -99，那是**左区**硬墙的口径。
    if _right_dots:
        _ex = {tuple(x) for x in (block_except or [])} | {tuple(q) for q in _right_dots}
        block_except = [list(x) for x in sorted(_ex)]
        for _q in _right_dots:
            _cell_vals[RS.key_xy(_q)] = 0.0
        print("  右区空地：" + "、".join(f"({q[0]},{q[1]})" for q in _right_dots)
              + " 按 **0 分中继**接入（可穿过，已在 block_except 里豁免）")
    if se.get("cell_values"):
        print("  二结局：命运所指逐格定价 "
              + "、".join(f"({RS.xy(k)[0]},{RS.xy(k)[1]})={v:g}"
                          for k, v in sorted(se["cell_values"].items())))

    sc = {
        "name": f"{a.layer}层 构型{a.config} / 起点{start} / AP{a.ap}",
        "notes": f"由 build_scenario.py 从 {os.path.basename(a.image)} 直接生成，**无人工转录**。",
        "mode": R.mode(),      # 分队模式随场景落盘：route_solver 直读文件时按它恢复
        "end_mode": R.end_mode(),   # 终点模式（全程追猎/险路尽头），与分队模式独立
        "has_right": bool(has_right),   # 三/四/五层：本层是否有右区（熄日余径）
        # 二层四结局问询（2026-10-30）：落盘，滚动重算/直读 scenario 时不再重复问
        **({"four_ending": R.four_ending()} if a.layer == 2 else {}),
        **({"four_ending_pre": R.four_ending_pre()}
           if a.layer == 2 and R.four_ending_pre() is not None else {}),
        # 三层三结局问询（2026-10-30）：同上，只在三层且答案存在时落盘
        **({"three_ending": R.three_ending()}
           if a.layer == 3 and R.three_ending() is not None else {}),
        **({"three_ending_pre": R.three_ending_pre()}
           if a.layer == 3 and R.three_ending_pre() is not None else {}),
        # 五层二结局问询（2026-10-31）：同上，只在五层落盘
        **({"second_ending": R.second_ending()}
           if a.layer == 5 and R.second_ending() is not None else {}),
        **({"second_ending_node": list(se["badged"])}
           if a.layer == 5 and se.get("badged") else {}),
        **({"clamp_items_at": [[q[0], q[1]] for q in se["clamp"]]}
           if a.layer == 5 and se["clamp"] else {}),
        # 迷藏（橡果徽章）：收益已**并进所在节点**（cell_values），特定类型放行
        **( {"acorn": [[q[0], q[1]] for q in acorn_cells]} if acorn_cells else {}),
        **({"cell_values": _cell_vals} if _cell_vals else {}),
        **({"block_except": block_except} if block_except else {}),
        # 带橡果的曲折密道：强制传送对（求解器按「踩 A 落 B」处理，两端只结算一次）
        **({"portals": [[[a[0], a[1]], [b[0], b[1]]] for a, b in acorn_pairs]}
           if acorn_pairs else {}),
        "edges": [[list(u), list(v)] for u, v in edges],
        "nodes": nodes, "start": list(start), "ap": a.ap,
        "grants": grants, "items": items,
        "values": values,
        "must": [[q[0], q[1]] for q in must],
        "block": block,
    }
    if visited:
        sc["visited"] = [[q[0], q[1]] for q in visited]
    # 别名已在推期望**之前**套用（`apply_unknown_aliases`）：
    # 放在这里会把 `_infer_left_quota` 算出的「未知的凶戾」逐格期望冲掉（2026-10-28 复查）。

    # 终点模式（2026-10-29）：险路尽头模式下按**类型**收终点（solver 的 final_type：
    # 碰到任何一个该类节点即结束，多个候选自动择优）；
    # 全程追猎保持旧口径（五层 final = 命运所指坐标）。落格兜底已在 derive_must 之前做过。
    # 五层·二结局（2026-10-31）：本层终点由二结局问询决定，**优先于终点模式**
    #   （二结局=要 → 左区带徽章命运所指；不要 → 右区命运所指）。
    _se_override = bool(a.layer == 5 and se.get("final") is not None
                        and R.second_ending() is not None)
    if _se_override and R.is_end_jintou():
        print("  二结局：本层终点改由二结局决定"
              f"（五层 3 个命运所指优先于终点模式「{R.layer_ending(5)}」）")
    if R.is_end_jintou() and not _se_override:
        sc["final_type"] = list(R.ending_types(a.layer))
        # 二层「小径=8」时小径是**硬终点**：进入即结束，避免与尽头各计 8。
        _hard_end = R.ending_hard_types(a.layer)
        if "险路小径" in _hard_end:
            sc["hard_final_types"] = list(_hard_end)
        # 迷藏：带橡果的尽头/小径 → 软终点（可当中继；建议留给最后一步）。
        # 二层 8 分的类型不放开（会重复计分），判据在 acorn_soft_final_types 里。
        _soft_end = acorn_soft_final_types(nodes, values, acorn_cells,
                                           R.ending_types(a.layer))
        if _soft_end:
            sc["final_soft_types"] = list(_soft_end)
            if "hard_final_types" in sc:
                sc["hard_final_types"] = [t for t in sc["hard_final_types"]
                                          if t not in _soft_end]
            print("  迷藏（橡果）：带橡果的「" + "/".join(_soft_end) +
                  "」按**软终点**处理（可当中继，12 分）——建议留给最后一步")
    elif a.layer == 5 and se.get("final") is not None:
        sc["final"] = [se["final"][0], se["final"][1]]
        print(f"  终点：({se['final'][0]},{se['final'][1]})"
              f"「{nodes.get(RS.key_xy(se['final']), '?')}」"
              + ("（二结局=要：左区带徽章命运所指）"
                 if R.second_ending() else "（二结局=不要/常规：右区命运所指）"))
    elif a.layer == 5:
        # 没有 3 个命运所指的普通五层：终点仍取「命运所指」（优先右区那个）
        if se["right"]:
            sc["final"] = list(se["right"][0])
        elif se["left"]:
            sc["final"] = list(se["left"][0])
        if sc.get("final"):
            print(f"  终点：({sc['final'][0]},{sc['final'][1]})「{nodes.get(RS.key_xy(sc['final']), '?')}」")

    # ── validation_status（2026-11-02，玩家第 1 条意见）──────────────────
    # build_scenario 是**场景生成器**，出错时允许继续产出 JSON（诊断数据有用），
    # 但必须把可信度写进文件，不能让下游以为它「已经通过完整校验」：
    #   VALID   —— 识别与校验都过；
    #   WARNING —— 有明确标记的假设（右区边表未验证），可以用但要展示风险；
    #   INVALID —— 关键识别失败（徽章读崩 / 格点越界），不能据此判「可用」。
    _val_hard, _val_soft = [], []
    if not ginfo.get("badge_ok", True):
        _bt = "徽章检测失败：橡果 +12/格、泪之聚落 0 分、五层二结局入口均未验证"
        if a.badge_none:
            _val_soft.append(_bt + "（--badge-none：按「无白圈徽章」继续）")
        else:
            _val_hard.append(_bt)
    if not ginfo.get("bounds_ok", True):
        _val_hard.append("格点拟合越出地图区：坐标基准不可信")
    if _rz_meta.get("topology") in ("ambiguous", "none"):
        _val_soft.append("右区（熄日余径）边表未验证：按 4 邻接临时假设"
                         + (f"（形状多解 {_rz_meta['shape']}）" if _rz_meta.get("shape") else ""))
    if _rz_meta.get("dropped"):
        # 2026-11-02 修复：这条以前写的是「已按『空地=硬墙』丢弃」，但空地已改成
        # **0 分中继**（可穿过）——真正的丢边原因是「空地坐标没报，那一格进不了图」。
        # 也正因为措辞和口径对不上，这条分支之前永远不触发（dropped 恒为空）。
        _val_soft.append(
            f"右区形状表最多有 {len(_rz_meta['dropped'])} 条边的端点落在**未探知的空地**上，"
            f"没报 --right-dots 接不进图 → 右区可能少一条通路（结论偏保守）")
    sc["validation_status"] = ("INVALID" if _val_hard
                               else ("WARNING" if _val_soft else "VALID"))
    sc["validation_reasons"] = _val_hard + _val_soft
    # 右区拓扑的可复现记录（2026-11-02）：形状怎么定的、用了哪些空地圆点。
    if _right_dots:
        sc["right_dots"] = [[q[0], q[1]] for q in _right_dots]
    if _rz_meta:
        sc["right_topology"] = {"how": _rz_meta.get("topology"),
                                "shape": _rz_meta.get("shape") or ""}

    out = a.out or os.path.join(
        RUNS, f"L{a.layer}-c{a.config}-{datetime.datetime.now():%H%M}.json")
    # 2026-10-29 修复：新装环境没有 runs/ 目录时，以前会在最后一步才 FileNotFoundError
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(sc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for n in rz_notes:
        print("  " + n)
    if must:
        print("  必走节点：" + "，".join(f"({x},{y})={nodes.get(RS.key_xy((x, y)), '?')}" for x, y in must))
    if a.layer == 2:
        print(f"  四结局：{R.layer2_plan_text(2)}")
    if a.layer in (3, 4, 5):
        print(f"  右区：{'有（熄日余径）' if has_right else '无（本层无熄日余径；不预留 AP）'}")
    print(f"起点 {start}  格 {len(cells)}  节点 {sum(1 for v in nodes.values() if v != '空地')}")
    for v in sorted(set(nodes.values())):
        if v in ("空地",):
            continue
        qs = [k for k, t in nodes.items() if t == v]
        print(f"  {v:8s} {qs}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
