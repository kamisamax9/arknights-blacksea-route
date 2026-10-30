#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_solver.py — 黑流树海 逐层路线穷举 / 校验器（v2，带剪枝）

设计原则：**边只从构型表读，禁止手推邻接**。任何路线先过 --check，再谈优化。

用法
----
  python route_solver.py scenario.json                 # 求最优路线
  python route_solver.py scenario.json --alts 6        # 附带 6 条同样好的替代（按落点集合去重）
  python route_solver.py scenario.json --ban 9,3       # 禁掉若干格子再求
  python route_solver.py --check "0,1;0,0" scenario.json   # 只校验徒步段每条边是否存在
  python route_solver.py scenario.json --no-tie-break  # 只要分数，不做同分比价（更快）

目标函数是**两段的**：先最大化毛分，再在「毛分相同」的解法里**最小化道具消耗价**。
第二段（同分比价）只用第一段剩下的时间预算；跑不完会明确标注
「可能还有更省的同分解」，不会把贵的方案说成最省。

优化点（相对 v1）
--------------
1. 访问集用 **bitmask**（只记有价值的格），状态 key 更小、hash 更快；
2. 可达域 **按 (轨道, 位置) 预算并缓存**，不再每步重建 20×8 的集合；
3. 剪枝下界从 `ap×最大值` 换成 **“未访问价值里最大的 ap 个之和”**（含未来 grant 的乐观加成），
   这是可采纳上界，剪得干净得多；
4. **跳过“跳过去还不如走过去”的目标**：目标是邻接格时，跳跃与徒步同价却白烧一件，
   直接剪掉（免 AP 件除外）；
5. 走法按“目标价值降序”排序，先找到好解，让上界剪枝尽早生效。

注意
----
* **曲折密道 = 强制传送**（2026-10-24 玩家确认）：踩上去 1 AP，直接出现在配对的另一端。
  scenario 里用 `"portals": [["2,3","0,0"]]` 声明；求解器会把「走进 A」自动改写成「落在 B」。
  传送后**不再二次触发**（否则会无限弹），并且 A、B 两端都按 0 收益计。
* 跳跃件也在传送范围内：跳到密道上同样会被传走。
* 跳跃件只跳**未访问**的格子。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules as R                   # noqa: E402  道具定义/分层定价的唯一真源

DEFAULT_VALUES = {
    # 树根/理想源 15 是「文明开化」口径；普通分队模式的 scenario 由
    # route.py / build_scenario.py 写入 mode + 本层紧急作战价（9/5），合并时覆盖这里。
    # 手写 scenario 若要跑普通分队，必须自己带 "mode": "普通分队" 和对应 values。
    # 2026-10-29 修复（#18 漂移）：本表曾与 rules.py 脱节——紧急作战 5（rules 9）、
    # 误入奇境 16（rules 10）、右区作战 12（rules 16），且缺右区一整档。
    # 工具产出的 scenario 会覆盖它们，但**手写/旧 scenario 漏写 values 时会静默按旧价算**。
    # 现在与 rules._COMMON / _RIGHT 对齐；selftest 的「DEFAULT_VALUES 与 rules 对齐」钉住。
    "树根": 15, "理想源": 15, "误入奇境": 10,
    "右区作战": 16, "右区紧急作战": 21, "右区凶戾": 18.5, "右区诡秘": 8,
    "右区不期而遇": 9, "右区得偿所愿": 8, "右区安全的角落": 3,
    "紧急作战": 9, "作战": 3,
    # 凶戾 = 未知的凶戾，权重按层改（见 references/03「按层的期望值」）：
    #   二层已知树根 → 全是普通作战，记 3
    #   三层/四层 → 剩余紧急名额 ÷ 凶戾格数 × 9（层内由 expect_by_distance 逐格覆盖，
    #               不再是旧公式 2/k —— 2026-10-29 勘误）
    #   五层     → 9m/k（m = 理想源之外还有几个紧急作战）
    "凶戾": 3,
    # 2026-10-31 QA：这两项与 LAYER_PRICES 对不上（4/5 → 应为 10/10）。
    # 它们在所有出现层上的定价都是 10（狭路相逢 三/四/五层、居民据点 二~五层），
    # 所以兜底值只能是 10；旧值会让**手写/漏写 values 的 scenario 静默少算 6/5 分**。
    # selftest 的「DEFAULT_VALUES 与 rules 对齐」现在按「层间恒定值必须相等」全表比对。
    "居民据点": 10, "狭路相逢": 10,
    "不期而遇": 2, "得偿所愿": 2, "秘境行商": 11, "诡意行商": 4, "先行一步": 2,
    # 三结局前置藏品已获取 → 每层一个不期而遇变「泪之聚落」：0 分、默认不进，可作 0 分中继
    # （2026-10-30 玩家口径；带徽章的失与得/无效验尸不改价，不单列）
    "泪之聚落": 0,
    "羽瞰点": 0, "安全的角落": 0, "失与得": 0, "应急助力": 0,
    "险路尽头": -99, "曲折密道": -99, "命运所指": 0, "流窜居民": 0,
    "险路小径": 0, "险路恶敌": -6,
    "空地": -99, "已访问": 0, "起点": 0,
}

# 三条硬性排除（2026-10-24 玩家口径）已在上面默认值里体现：
#   1) 三层起左区不走普通作战 → 在 scenario 的 values 里把 "作战" 覆盖为 0 / -99（"凶戾" 保留）
#   2) 除一层外不走林间空地   → "空地": -99；一层 scenario 里覆盖为 0
#   3) 不走曲折密道           → "曲折密道": -99，且不要写 portals（强制传送机制对规划已无意义）
# 注："险路尽头" 也设为 -99；二层若要拿四结局道具，需在 scenario 里把它覆盖成正值。


# 控制台编码兜底（GBK 终端下打印中文/符号不崩）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _cov_adj8(p):
    return [(p[0] + dx, p[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]


def _cov_adj12(p):
    return [(p[0] + dx, p[1] + dy) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
            if 0 < abs(dx) + abs(dy) <= 2]


def _cov_line2(p):
    # 「直线 2 格**内**」= 直线方向 1~2 格（不是恰好 2 格）。2026-10-28 玩家裁定：
    # 试作外骨骼「直线 3 格内」能落距离 2，所以「内」一律按 ≤N 建模。
    return [(p[0] + d, p[1]) for d in (-2, -1, 1, 2)] + [(p[0], p[1] + d) for d in (-2, -1, 1, 2)]


def _cov_line3(p):
    return ([(p[0] + d, p[1]) for d in (-3, -2, -1, 1, 2, 3)]
            + [(p[0], p[1] + d) for d in (-3, -2, -1, 1, 2, 3)])


def _cov_row_col(p, xs, ys):
    # 同行或同列任意节点，**不含自身**（含自身只会白烧一件换 0 收益，还多出状态）
    return ([(x, p[1]) for x in xs if x != p[0]]
            + [(p[0], y) for y in ys if y != p[1]])


def _cov_any(p, xs, ys):
    # 任意节点，**不含自身**
    return [(x, y) for x in xs for y in ys if (x, y) != p]


COVER = {"adj8": _cov_adj8, "adj12": _cov_adj12, "line2": _cov_line2,
         "line3": _cov_line3, "row_col": _cov_row_col, "any": _cov_any}
FREE_COV = {"adj8", "adj12", "line2", "line3"}

# 分数是浮点（诡秘 5.7 / 6.2 这种），判「同分」必须带容差：
# 两条数学上同分的路线，加法顺序不同，浮点末位会差 1e-15 左右。
# 用 `==` 会让「同分更省道具」的判据在浮点场景下静默失效（2026-10-28）。
EPS = 1e-9

# 迷藏（橡果徽章）的收益：**已经并进节点价值**，求解器不再另做「同分优先」。
#   2026-10-30 玩家新口径：橡果 = `rules.ACORN_VALUE`（12 分），
#   建场景时按「所在节点收益 + 12」写进 scenario 的 `cell_values`（逐格覆盖），
#   带橡果的林间空地同时从 `block` 里豁免（`block_except`）→ 地基 0 + 12 = 12 分。
#   求解器只认 `cell_value(q)`：**pval / 走法 / 打印全部同源**，
#   所以「经过一个橡果格多拿 12 分」与「总分」永远对得上。
#   ⛔ 旧的 `ACORN_EPS=1e-4` 微额加成已删除：那是「不改价、只压过省加工品」的实现，
#      现在橡果有真实价值，再加 ε 会让「同分是否经过橡果」压过真实取舍。


def load(path):
    # utf-8-sig：Windows 记事本/旧 PowerShell 存出的 UTF-8 BOM 也能读
    with open(path, encoding="utf-8-sig") as f:
        sc = json.load(f)
    sc["values"] = {**DEFAULT_VALUES, **sc.get("values", {})}
    return sc


def _xy(v):
    """把坐标统一成 (int, int)。

    **`tuple("2,3")` 会得到 `('2', ',', '3')`** —— 2026-10-28 审计 bug 1：
    portals 文档写的是 `[["2,3","0,0"]]`（字符串），代码直接 tuple()，
    portal 字典的键变成三字符元组，永远匹配不到任何节点 → 传送静默失效。
    坐标解析只走这一个函数，字符串/列表/元组都吃。
    """
    if isinstance(v, str):
        parts = [p for p in v.replace(" ", "").split(",") if p != ""]
        if len(parts) < 2:
            raise ValueError("坐标格式不对：%r（要 \"x,y\"）" % (v,))
        return (int(parts[0]), int(parts[1]))
    return (int(v[0]), int(v[1]))


# 公开别名：route.py / build_scenario.py 的建场·校验层也走它（2026-10-28 审查第 1 条）。
xy = _xy


def key_xy(q):
    """(x, y) 元组 → scenario 的字符串键 "x,y"（`_xy` 的逆运算）。

    **坐标表示约定**（2026-10-28 审查：转换散落 20+ 处，只准走这一对）：
    scenario JSON 的 nodes / grants / final 键一律是**字符串 "x,y"**（JSON 限制）；
    几何运算一律**元组 (x, y)**。两边的转换禁止再手写
    `int(k.split(",")[0])`（遇空格就碎）或 `"%d,%d" % q` 这类散装形式。
    """
    return "%d,%d" % _xy(q)


def build_adj(edges):
    adj = {}
    for a, b in edges:
        a, b = _xy(a), _xy(b)
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def check_path(sc, pts):
    adj = build_adj(sc["edges"])
    # 曲折密道：`portals` 是**无向对**，只写了 [[A,B]] 也要能从 B 传到 A
    portal = {}
    for a, b in sc.get("portals") or []:
        a, b = _xy(a), _xy(b)
        portal[a] = b
        portal[b] = a
    nodes = {tuple(map(int, k.split(","))): v for k, v in sc.get("nodes", {}).items()}
    log, ok = [], True
    if pts[0] != tuple(sc["start"]):
        ok = False
        log.append("起点不符: %s != %s" % (pts[0], tuple(sc["start"])))
    for a, b in zip(pts, pts[1:]):
        if portal.get(a) == b:
            log.append("%s -> %s OK  [强制传送：踩上曲折密道即到另一端]" % (a, b))
        elif (abs(a[0] - b[0]) + abs(a[1] - b[1])) != 1:
            ok = False
            log.append("%s -> %s 不是正交相邻" % (a, b))
        elif b not in adj.get(a, ()):
            ok = False
            log.append("%s -> %s **没有边**（构型表里不存在）" % (a, b))
        else:
            log.append("%s -> %s OK  [%s]" % (a, b, nodes.get(b, "?")))
    return ok, log


# 加工品「消耗权重」表 —— 数值真源在 rules.py，这里只补两张**编号副本**。
# 现行口径（2026-10-28 玩家定稿）：1 重弹簧/小八界；2 假肢/轮子/鸣笛；
# 4 引擎/气垫/外骨骼/背包/逃生座椅/触须；8 融雪；12 方向盘；16 原理/遥控器/特快。
# 这个文件**不再维护任何一件的价格**：要改就改 rules.py 的 ITEM_DEFS["price"]。
PRICE = {**R.PRICE, "气垫底座A": 4, "气垫底座B": 4}


class Solver(object):
    def __init__(self, sc, ban=()):
        self.adj = build_adj(sc["edges"])
        self.nodes = {tuple(map(int, k.split(","))): v for k, v in sc.get("nodes", {}).items()}
        for q in ban:
            self.nodes.pop(tuple(q), None)
        self.vals = sc["values"]
        self.grants = {_xy(k): v for k, v in sc.get("grants", {}).items()}
        self.items = []
        for it in sc.get("items", []):
            try:
                self.items.append(R.normalize_item(it))
            except R.UnknownItem as exc:
                raise SystemExit(
                    "!! 道具「%s」认不出来，scenario 里也没写 cov。\n"
                    "   已知 16 件：%s\n"
                    "   自己加的新道具必须在 assets 里写明 cov（%s）。"
                    % (exc, "、".join(R.available_items()), "/".join(R.COVS)))
        for it in self.items:
            if it["cov"] not in COVER:
                raise SystemExit("!! 道具「%s」的 cov「%s」未知（可选：%s）"
                                 % (it["name"], it["cov"], "/".join(sorted(COVER))))
            if it.get("random"):
                print("!! 道具「%s」落点**不可控**（随机传送）：路线把它当「最好的那格」算，"
                      "实际是上界，不是承诺" % it["name"])
        self.start = _xy(sc["start"])
        self.ap0 = int(sc["ap"])
        self.visited0 = frozenset(_xy(v) for v in sc.get("visited", []))
        # ── 逐格价值覆盖（2026-10-30）──────────────────────────────
        # `cell_values` = {"x,y": 分}：**这一格**的实际收益，优先于类型价。
        # 目前唯一的来源是迷藏（橡果）：建场景时写「所在节点收益 + rules.ACORN_VALUE」。
        # 为什么不用「改类型名」的老办法（右区是那么做的）：橡果能压在**任何**类型上，
        # 逐类型改名要 19×N 条，而且会把 `base_type()` / 道具 only·exclude 的匹配搅乱。
        self.cval = {}
        for k, v in (sc.get("cell_values") or {}).items():
            try:
                self.cval[_xy(k)] = float(v)
            except (TypeError, ValueError):
                print("!! cell_values 的 (%s)=%r 不是数字，已忽略" % (k, v))
        # `block_except` = [[x,y] …]：类型在 block 里、但**这一格**保留可进。
        # 唯一用途：带橡果的林间空地（三层起空地是硬墙，带迷藏就要能走进去）。
        self.block_except = frozenset(_xy(v) for v in sc.get("block_except", []))
        # ── 不可达类型（硬墙）──────────────────────────────────────
        # 2026-10-28 玩家口径：三/四/五层的**林间空地按不可达算**，
        # 只求「每一点行动力都落在有效节点上」的解。
        # 以前空地只是 -99 分（走上去等于自杀），求解器仍然会把它当**中继**试一遍，
        # 白占状态；现在直接从图里删掉：走不进、跳不进、也不占 mask。
        # 一层例外（空地合法中继），由入口按层写 `block`。
        self.blocked = []
        for t in sc.get("block", []):
            kill = [q for q, t2 in self.nodes.items()
                    if t2 == t and q not in self.block_except]
            keep = [q for q, t2 in self.nodes.items()
                    if t2 == t and q in self.block_except]
            for q in kill:
                self.nodes.pop(q, None)
            if kill:
                self.blocked.append((t, len(kill)))
            if keep:
                print("!! 「%s」%d 格在 block 里，但带橡果（迷藏）→ 保留可进：" %
                      (t, len(keep)) + "、".join("(%d,%d)" % q for q in sorted(keep)))
        for t, n in self.blocked:
            print("!! 「%s」%d 格按**不可达**处理（不走进、不跳进、不记分）" % (t, n))
        if self.start not in self.nodes:
            raise SystemExit("!! 起点 %s 被 block 列表删掉了（类型是「%s」？）——检查 scenario 的 block"
                             % (self.start, sc.get("nodes", {}).get(key_xy(self.start), "?")))
        # 强制传送对（曲折密道）
        self.portal = {}
        for a, b in sc.get("portals", []):
            a, b = _xy(a), _xy(b)
            self.portal[a] = b
            self.portal[b] = a
        for q in self.portal:
            if q not in self.nodes:
                print("!! portals 里的 (%d,%d) 不在该构型格子里（拼写错？）" % q)
        # 有价值的格子 → bit 位
        # **给「回复行动力」的格子（羽瞰点 +1 / 树根 +2）也分配 bit**，但价值记 0：
        # 这样 mask 能记住它已经被踩过，回复只结算一次。
        # 2026-10-27 二层：没这条时求解器把羽瞰点当提款机，来回踩它刷 +1 AP，
        # 9 步的路由刷出了 10 点行动力。回复类节点必须 only-once。
        # 节点类型自带行动力效果的，**必须在算出 grant_cells/pcells/bit 之前**补进 grants。
        # 否则补进来的格子没有 bit，会被当成「可以反复踩、反复拿 AP」
        # （2026-10-27 QA：我第一版放在后面，正好踩了这个坑）。
        # 补多少**必须走 rules.grant_ap()**：普通分队模式（sc["mode"]="普通分队"）
        # 树根不回复 AP，直接读 GRANT_AP 会凭空多 +2（2026-10-29 新增模式）。
        # 同一句里恢复终点模式（sc["end_mode"]="险路尽头" → final_type 收终点）。
        R.set_mode(sc.get("mode"))
        R.set_end_mode(sc.get("end_mode"))
        # 二层四结局问询（2026-10-30）：旧 scenario 没有这两个键 → None（旧口径兜底）；
        # 定价与 final_type 已由建场景工具写死，这里只恢复状态供打印/复核。
        R.set_four_ending(sc.get("four_ending"))
        R.set_four_ending_pre(sc.get("four_ending_pre"))
        for q, t in self.nodes.items():
            g = R.grant_ap(t)
            if g > 0 and q not in self.grants:
                self.grants[q] = g
                print(f"!! ({q[0]},{q[1]}) 是「{t}」但 grants 没配，已自动补 +{g} AP")

        # ── 必走节点（2026-10-28 玩家口径）──────────────────────────────
        # 先把「这一层一定要走到的坐标」锁死，再在「必须覆盖它们」的解里穷举。
        # 直接收益：分支被砍掉一大半（二层尤其明显——树根就是唯一的紧急作战）。
        #
        # 语义是**硬约束**：没走齐这些点不记为解。所以：
        #   · 走不到时**不能**安静地给一条漏掉它们的路线，必须报告（见 run 的回落逻辑）；
        #   · 坐标写错（不在图上）直接报错退出，否则会变成「永远无解」的假象。
        # ⚠️ 这段必须在 pcells **之前**：0 价值（或价值缺失）的必走点也要分 bit。
        # 2026-10-29 修复：以前 pcells 只收「正值格 + grant 格」，0 分必走点没有 bit →
        # must_mask 永远盖不住它 → 约束静默失效（求解器绕开它照样收解），
        # 而 must_uncovered 又按 must 全集算 → route.py 独立复核误报「必走点点不到 → ❌」。
        self.must = frozenset(_xy(v) for v in sc.get("must", []))
        bad_must = sorted(q for q in self.must if q not in self.nodes)
        if bad_must:
            raise SystemExit(
                "!! must 里有不在该构型格子里的坐标 %s（拼写错？或右区没拼进来）。\n"
                "   must 是可选的：不写就只按价值最优求解。" % bad_must)
        self.must_dropped = False        # True = 必走约束下无解，已回落到无约束
        self.must_uncovered = ()         # 回落时：没走到的必走点
        self.must_cuts = 0               # 「必走点超出剩余步数」这条剪枝命中次数（诊断用）

        # ── 加工品次数钳制格（2026-10-31：五层二结局·右区命运所指）──────────
        # 玩家口径：五层右区「命运所指」进入后，**零件箱里所有加工品的剩余次数强制变成 1**
        # （含之后获取的）。所以它是「越晚进越值钱」的节点——早进去会把后面的跳跃预算砍掉。
        # 实现：落在这格时把状态里的 items 全部 `min(1)`（求解器状态本来就带 items，
        # 所以搜索会自动把「先扫右边再进它」排到前面，不需要额外排序约束）。
        self.clamp_at = frozenset(_xy(v) for v in sc.get("clamp_items_at", []))
        _bad_clamp = sorted(q for q in self.clamp_at if q not in self.nodes)
        if _bad_clamp:
            print("!! clamp_items_at 里的 %s 不在该构型格子里（拼写错？）——已忽略" % _bad_clamp)
        self.clamp_at = frozenset(q for q in self.clamp_at if q in self.nodes)
        if self.clamp_at:
            print("!! 「%s」%d 格：进入后所有加工品剩余次数强制→1（二结局）"
                  % ("/".join(sorted({self.nodes[q] for q in self.clamp_at})), len(self.clamp_at)))

        # ── 迷藏（橡果徽章）：收益已并进 `cell_values`（2026-10-30 新口径）──────
        # 求解器**不再**给橡果另设偏好：它拿的 12 分是真实的、与节点收益相加的。
        # `acorn` 列表只剩两个用途：① 报告「本条路线经过几个橡果」；
        # ② 兜底：手写 scenario 只写了 `acorn` 没写 `cell_values` 时，
        #    在这里按 rules.acorn_value() 补一次（否则那份数据会被静默当成 0 分）。
        self.acorn = frozenset(q for q in (_xy(v) for v in sc.get("acorn", []))
                               if q in self.nodes)
        for q in self.acorn:
            if q not in self.cval:
                _t = self.nodes.get(q, "")
                if _t in ("起点", "已访问"):
                    continue      # 记账类型（0 分）：已走格不再发一次橡果收益
                base = R.acorn_base_value(self.vals.get(_t, 0))
                self.cval[q] = round(base + R.acorn_value(), 4)
            # 曲折密道：结算格是**落点**（踩 A 落 B），所以对端也要同价，
            # 否则手写场景只写 acorn 时，从 A 进入会按 B 的类型价（-99）结算。
            o = self.portal.get(q)
            if (o is not None and o != q and o not in self.cval
                    and self.nodes.get(o) not in ("起点", "已访问")):
                self.cval[o] = self.cval[q]
        self.acorn_hits = 0

        _grant_cells = {q for q in self.grants if q in self.nodes}
        # 橡果格也要 bit：它现在多半是正分，但**带橡果的空地/普通作战**在旧场景里
        # 可能仍是 0 分地基；给它 bit 才能计分与计数。
        self.pcells = [q for q, t in self.nodes.items()
                       if self.cell_value(q) > 0 or q in _grant_cells or q in self.must
                       or q in self.acorn]
        self.bit = {q: i for i, q in enumerate(self.pcells)}
        # 先左后右（玩家 2026-10-28：三/四层右区作战高一档，先清左区养等级再进右区；
        # 五层右区同档不受此限）。sc 里给 "left_first": true + "right_from_x": <列号> 启用：
        # 一旦踩过右区格，就不再踩左区的有价值格（含羽瞰点等 0 分 grant 格）。
        # 纯过境格（无 bit）不限制。默认关闭 → 语义与旧版完全一致。
        self.left_first = bool(sc.get("left_first"))
        self.right_mask = 0
        self.bit_is_right = ()
        if self.left_first and sc.get("right_from_x") is not None:
            _rfx = int(sc["right_from_x"])
            _ir = []
            for q, i in self.bit.items():
                if q[0] >= _rfx:
                    self.right_mask |= 1 << i
                _ir.append(q[0] >= _rfx)
            self.bit_is_right = tuple(_ir)
        else:
            self.left_first = False
        # .get：0 价值必走点的类型可能不在 values 里（手写 scenario），别 KeyError
        self.pval = [max(0, self.cell_value(q)) for q in self.pcells]
        # ub 每次都要「剩余价值里最大的 k 个」——预排序后按序取，免去每节点 sort
        self._order = sorted(range(len(self.pval)), key=lambda t: -self.pval[t])
        self.grant_cells = list(_grant_cells)
        # 道具「贵贱」：默认 1，可用 scenario 的 "cost" 覆盖（结构性原理这类通用件设 5）。
        # 2026-10-27 二层：只按「件数」记次级目标时，烧 1 次原理和烧 1 次报废假肢等价，
        # 求解器于是拿原理去跳一个假肢就能到的斜邻。必须按代价加权。
        # 价目表真源在 rules.ITEM_DEFS[...]["price"]（玩家 2026-10-28 定稿）：
        # 原理/遥控器/特快 16；融雪 8；方向盘 12；引擎/气垫/外骨骼/背包/逃生座椅/触须 4；
        # 假肢/轮子/鸣笛 2；重弹簧/小八界 1。**这里不要另抄一份**。
        self.cost_of = {it["name"]: it.get("cost", PRICE.get(it["name"], 2))
                        for it in self.items}
        xs = sorted({q[0] for q in self.nodes})
        ys = sorted({q[1] for q in self.nodes})
        # 放到循环外：items 为空时（或全部带 only 时）也要有值，否则 targets() 会 AttributeError
        self._xs, self._ys = xs, ys
        # 预计算每格信息，避免 moves() 里对每个邻居做 3 次 dict.get
        self.info = {}
        for q, t in self.nodes.items():
            self.info[q] = (self.bit.get(q, -1), self.cell_value(q), self.grants.get(q, 0))
        # 「最后一格必须是 X」：五层的命运所指占掉最后 1 点行动力（2026-10-26 玩家口径）。
        _fn = sc.get("final")
        self.final = _xy(_fn) if _fn else None
        # 险路尽头模式（2026-10-29）：按**类型**收终点——走到任何一个该类节点即收解并停止。
        # 「碰到 = 结束」天然保证「最后一步之前的行动力不进该类节点」，
        # 多个候选尽头/恶敌由搜索自动择优，不需要逐候选各解一遍。
        # 与坐标型 final 互斥：同时设置时 final_type 优先（终点模式是更强的硬约束）。
        # 2026-10-30 玩家新规：一/四层的**险路小径**也是终点候选（与险路尽头同分）；
        # 二层的两种四结局分支（不要 / 要且已获取前置藏品）同样把小径列为候选；
        # 所以 `final_type` 允许是**列表**（字符串仍兼容）。终点因此分两档：
        #   · 硬终点（HARD_ENDING：尽头/恶敌）= 碰到即结束，之后不许再走；
        #   · 软终点（险路小径）= 停这里算合法收尾，**继续走也不算违规**
        #     （小径本来就可进可出、常作一层中继；做成硬终点会砍掉「必经小径」的路线）。
        _ft = sc.get("final_type") or None
        if isinstance(_ft, str):
            self.final_types = (_ft,)
        elif isinstance(_ft, (list, tuple)):
            self.final_types = tuple(str(t) for t in _ft if t)
        else:
            self.final_types = ()
        self.final_type = self.final_types[0] if self.final_types else None   # 主类型（打印用）
        # 硬终点：默认按 rules.HARD_ENDING（尽头/恶敌）；二层「小径=可选终点」时建场景会写
        # `hard_final_types=["险路尽头","险路小径"]`——小径进入即结束，避免和尽头各计一次 8。
        _hard = tuple(t for t in (sc.get("hard_final_types") or ()) if t in self.final_types)
        if not _hard:
            _hard = tuple(t for t in self.final_types if t in R.HARD_ENDING)
        # `final_soft_types`（2026-10-30）：带橡果的终点节点可**当中继**（软终点）。
        # 只对「基础价为 0」的类型生效（建场景工具算好才写），所以二层 8 分的
        # 尽头/小径不会被放开（那会把「尽头 8 + 小径 8」重复计）。
        _soft = tuple(t for t in (sc.get("final_soft_types") or ()) if t in self.final_types)
        if _soft:
            self.final_hard = tuple(t for t in _hard if t not in _soft)       # 可能全软（=空）
        else:
            self.final_hard = _hard if _hard else self.final_types           # 未收录类型按硬终点
        if self.final_types:
            if self.final is not None:
                print("!! final 与 final_type 同时设置：以 final_type（险路终点模式）为准")
                self.final = None
            if not any(t in self.final_types for t in self.nodes.values()):
                print("!! final_type=%s：图上没有任何可作为终点的节点 → 终点约束无法满足"
                      "（会判无可行解；终点坐标可用 --final c,r 手报）"
                      % "/".join(self.final_types))
            for _t in self.final_types:
                _vt = self.vals.get(_t, 0)
                if _vt < 0:
                    print("!! final_type=「%s」的价值为负(%.0f)。险路终点模式的终点应记 ≥0"
                          "（值源 rules.END_FINAL_VALUE）；已临时按 0 处理。" % (_t, _vt))
                    self.vals = dict(self.vals)
                    self.vals[_t] = 0
        # 终点约束会占掉 1 点行动力（落在 0 价值的命运所指上），
        # 上界相应少算一个节点，否则 ub 相对「受约束的最优解」太松，剪枝几乎失效。
        # 故意保留但**不参与剪枝**：曾试过用它把上界收一格，但「最后一步」可能靠
        # 免行动力道具走（不扣 AP），减 1 后不再是可采纳上界，会剪掉最优解。
        # 留着这个字段只是记录「终点占 1 点」这个事实，别再往 ub() 里接。
        self._ub_cut = 1 if (self.final or self.final_types) else 0
        if self.final is not None:
            _t = self.nodes.get(self.final, "")
            if self.vals.get(_t, 0) < 0:
                print("!! final=%s 的类型「%s」价值为负(%.0f)。五层的命运所指是终点、应记 0；"
                      "三/四层才是 -99 的墙。已临时按 0 处理。" % (self.final, _t, self.vals.get(_t, 0)))
                self.vals = dict(self.vals); self.vals[_t] = 0
        self._has_portal = bool(self.portal)
        self._cov_cache = {}
        self._adj_pre = {}      # pos -> [(land, bit, val, grant)]，省掉 moves() 里每次的 dict.get
        self._ti = {}           # (cov,pos) -> [(q0, land, bit, val, grant)]，同上（跳跃分支）

        # ── 右区改名 vs 道具的 only 前缀 ────────────────────────────
        # 右区格子会被改名成「右区凶戾 / 右区诡秘 …」，而道具的 only 写的是
        # 「凶戾 / 诡秘」这类**基础名**，startswith 就匹配不上了
        # （2026-10-28 踩过：融雪只能落「诡秘」，右区诡秘一个都落不了 → 右区进不去）。
        # 展开逻辑在 `rules.expand_prefixes`，**入口 route.py 也调同一个函数**
        # （否则会出现「入口说进得去、求解器说进不去」）。
        for it in self.items:
            for f in ("only", "exclude"):
                if it.get(f):
                    it[f] = R.expand_prefixes(it[f])
        self.expanded = 0
        self.alts = {}

        # must_mask：所有必走点的 bit 合集（解析在 pcells 之前，见那边的注释）。
        # 0 价值的必走点已在 pcells 里分到 bit（2026-10-29 修复），
        # 这里查不到 bit 就是结构性错误，必须炸出来——
        # 静默 continue 等于把硬约束悄悄丢掉（正是这次修掉的 bug）。
        self.must_mask = 0
        for q in self.must:
            b = self.bit.get(q, -1)
            if b < 0:
                raise SystemExit(
                    "!! 必走点 (%d,%d) 在图上却没有 bit（pcells 漏收了它）——"
                    "must 约束无法表达，属求解器内部错误" % q)
            self.must_mask |= 1 << b
        # 走法预计算：把「只依赖位置/类型」的判断从热循环里搬出来（见 _precompute_moves）
        self._precompute_moves()
        self._ub_cache = {}              # (mask, k) -> 上界；命中率很高（同一个 mask 会被不同顺序重复访问）
        self._ub_hit = 0
        self._ub_miss = 0

    # ---- 单格收益 ----
    def cell_value(self, q):
        """**这一格**的收益：逐格覆盖（`cell_values`）优先，否则按节点类型价。

        唯一用处是迷藏（橡果）：建场景时把「所在节点收益 + 12」写进 `cell_values`，
        于是橡果格在 pval / 走法枚举 / fmt 打印里处处一致（不会出现
        「打印得分与总分对不上」那种旧账）。其余格子没有覆盖项，行为与旧版完全一致。
        """
        v = self.cval.get(q)
        if v is None:
            v = self.vals.get(self.nodes.get(q, ""), 0)
        return v

    # ---- 可达域（带缓存） ----
    def targets(self, cov, pos):
        key = (cov, pos)
        hit = self._cov_cache.get(key)
        if hit is None:
            fn = COVER[cov]
            raw = fn(pos) if cov in FREE_COV else fn(pos, self._xs, self._ys)
            hit = [q for q in raw if q in self.nodes]
            self._cov_cache[key] = hit
        return hit

    def _grant_extra(self, mask):
        """未踩到的 grant 格一共还能回多少 AP（乐观口径）。"""
        extra = 0
        for q in self.grant_cells:
            b = self.bit.get(q, -1)
            if b < 0 or not (mask >> b) & 1:      # 0 价值的 grant 格（如羽瞰点）永远算作未访问
                extra += self.grants[q]
        return extra

    def budget(self, mask, ap, bonus=0):
        """乐观可达**步数** = 当前 AP + 未踩 grant + （免AP次数 / 道具给的 AP）。

        必走点的可行性判据要用它：只要「还能走几步」都不够覆盖剩下的必走点，就可以剪。
        用乐观口径所以这剪枝是**安全**的（不会剪掉真正走得完的路）。
        """
        return ap + self._grant_extra(mask) + bonus

    def ub_k(self, mask, k):
        """给定「还能走的步数 k」，返回未访问价值里前 k 大之和（可采纳上界）。

        按 (mask, k) 缓存：同一个 mask 会被**不同顺序**反复到达，
        而这条上界只取决于「哪些格已访问 + 还能走几步」，与顺序无关。
        实测命中率 96–99%（见 CHANGELOG 2026-10-28），是纯赚。

        注意：**不能因为「最后一步要给 final」就把 k 减 1**。那一步可能靠免行动力道具走
        （不扣 AP），减了就不再是可采纳上界，会把真正的最优解剪掉（2026-10-27 QA）。
        """
        if k <= 0:
            return 0
        key = (mask, k)
        hit = self._ub_cache.get(key)
        if hit is not None:
            self._ub_hit += 1
            return hit
        self._ub_miss += 1
        total = cnt = 0
        for idx in self._order:
            if not (mask >> idx) & 1:
                total += self.pval[idx]
                cnt += 1
                if cnt >= k:
                    break
        if len(self._ub_cache) > 4000000:      # 内存上限，防重层把机器吃穿
            self._ub_cache.clear()
        self._ub_cache[key] = total
        return total

    def ub(self, mask, ap, bonus=0):
        """可采纳上界：未访问价值里最大的 (ap + 乐观未来AP) 个之和

        `bonus` = 剩余**免AP件次数** + 剩余**道具给AP的总和**（见 dfs 里的 _extra_moves）。
        2026-10-28 修：原实现只加 `1 if 有免AP件`（布尔），
        当身上有两件以上免AP件、AP 又恰好为 0 时，真实可再拿 2 格而界只给 1 格 →
        **上界不再可采纳，会把最优解剪掉**（审计 bug 3 的连带问题）。
        """
        return self.ub_k(mask, self.budget(mask, ap, bonus))

    def _precompute_moves(self):
        """把「徒步邻居」和「每个道具在每个位置的可落点」预计算好。

        为什么：`moves()` 是最热的函数（15M 状态 × 每状态十几次调用）。
        原来每步都要做 `portal.get / bit.get / vals.get / grants.get` 和
        `any(t.startswith(o) ...)` 的字符串前缀匹配 —— 这些**只依赖位置和节点类型，
        与 mask/ap 无关**，可以一次性算完。实测（73 分用例，整体求解）见 CHANGELOG。
        """
        self._adj_pre = {}
        for pos, nbs in self.adj.items():
            row = []
            for q in nbs:
                if q not in self.nodes:
                    continue
                land = self.portal.get(q, q)              # 曲折密道：强制传送到另一端
                if land not in self.nodes:
                    # 分阶段求解时，密道可能**跨左右区**（另一端不在本段图里）→ 本段不放行，
                    # 否则路线会走到图外的格子上（安全但错）。
                    continue
                i = self.bit.get(land, -1)
                v = self.cell_value(land)
                # i_in = **被踩上的那一格**的 bit（带橡果的密道：两端都算「已探索」）
                row.append((land, i, v, self.grants.get(land, 0), self.bit.get(q, -1)))
            self._adj_pre[pos] = tuple(row)

        # 每个道具：pos -> ((land, bit, value), …)，only/exclude/「走过去更省」都已过滤
        self._item_tg = []
        for it in self.items:
            per = {}
            for pos in self.nodes:
                tg = []
                for q0 in self.targets(it["cov"], pos):
                    land = self.portal.get(q0, q0)
                    if land not in self.nodes:
                        continue        # 密道另一端在本段图外（分阶段）：本段不放行
                    i = self.bit.get(land, -1)
                    t = self.nodes.get(land, "")
                    if it["only"] and not any(t.startswith(o) for o in it["only"]):
                        continue
                    if it.get("exclude") and any(t.startswith(x) for x in it["exclude"]):
                        continue
                    # 跳过"走过去更省"的目标：邻接格 + 要花 AP
                    if not it["free"] and q0 in self.adj.get(pos, ()):
                        continue
                    v = self.cell_value(land)
                    tg.append((land, i, v, self.bit.get(q0, -1)))
                per[pos] = tuple(tg)
            self._item_tg.append(per)

    # ---- 加工品次数钳制（五层二结局·右区命运所指）----
    def _clamp_after(self, land, items):
        """落在钳制格（右区命运所指）→ 所有加工品剩余次数强制变成 1。

        语义来自玩家 2026-10-31：这一格进入后「零件箱里所有加工品的剩余次数强制变为 1，
        包括之后获取的加工品」。求解器没有「之后获取」的概念（库存在入口就定了），
        所以对已有库存做 `min(1)`；新获得的件在建场景时已经不可能预支，不用额外处理。

        这不只是「尽量排最后」的偏好：它真的砍掉后续跳跃次数，所以 dfs 会自己把
        「先扫完右边、再进它、最后只跳一步」排在前面。
        """
        if land not in self.clamp_at:
            return items
        return tuple(1 if u > 0 else 0 for u in items)

    def moves(self, pos, mask, ap, items, adjset, unit_cost=None):
        """枚举这一步的所有走法。

        每个元素 = (得分, 落点, 道具名, 落点后AP, 新mask, 新items, 新增道具价, 非必走标记)。
        `dcost` = 这次移动的道具价（徒步 0、跳跃 = 该件单价）：dfs 靠它累积代价，
        **不用每步遍历 items 重算**（那正是 2026-10-27「14s→60s」的原因）。
        `mr` = 1 表示不是必走点：排序时必走点优先，可行解更早出现。
        """
        out = []
        mm = self.must_mask
        lf = self.left_first
        rm = self.right_mask
        bir = self.bit_is_right
        # 徒步**必须有 1 点行动力**：ap=0 时只剩免AP件可动。
        # 2026-10-28 审计连带修：以前这里不看 ap，靠 dfs 的「放宽 1 格」间接挡；
        # 放宽量改成「剩余免AP次数」后就漏了——ap=0 还能徒步，AP 被走到 -3 都不停。
        if ap > 0:
            for land, i, v, gr, i_in in self._adj_pre.get(pos, ()):
                seen = i >= 0 and (mask >> i) & 1
                if lf and i >= 0 and not seen and not bir[i] and (mask & rm):
                    continue                # 先左后右：进过右区就不再回左区拿收益
                nm = mask if (i < 0 or seen) else (mask | (1 << i))
                # 曲折密道：踩上去那一刻两端都算探索过（落地那格才是结算格），
                # 所以**两端的 bit 一起置位**——否则回头再从另一端进一次会重复结算。
                if i_in >= 0 and i_in != i and not (mask >> i_in) & 1:
                    nm |= (1 << i_in)
                out.append((0 if seen else v, land, None,
                            ap - 1 + (0 if seen else gr),
                            nm, self._clamp_after(land, items), 0,
                            1 if (i < 0 or not (mm >> i) & 1) else 0))
        for k, it in enumerate(self.items):
            left = items[k]
            if left <= 0:
                continue
            free = it["free"]
            # 付费件要 1 点行动力；免AP件 ap=0 也能用（这就是「免AP」的定义）
            step = 0 if free else 1
            if ap < step:
                continue
            bonus = it.get("bonus", 0)
            name = it["name"]
            dc = unit_cost[k] if unit_cost else 0
            for land, i, v, i_in in self._item_tg[k].get(pos, ()):
                if i >= 0 and (mask >> i) & 1:
                    continue
                if lf and i >= 0 and not bir[i] and (mask & rm):
                    continue                # 先左后右：同上
                ni = list(items)
                ni[k] = left - 1
                nm = (mask | (1 << i)) if i >= 0 else mask
                # 落到曲折密道上同样会强制传送：两端一起置位（同徒步分支）
                if i_in >= 0 and i_in != i and not (mask >> i_in) & 1:
                    nm |= (1 << i_in)
                out.append((v, land, name,
                            ap - step + self.grants.get(land, 0) + bonus,
                            nm,
                            self._clamp_after(land, tuple(ni)), dc,
                            1 if (i < 0 or not (mm >> i) & 1) else 0))
        # 同分时**先走便宜的那步**：这样「贵而先到」的解不会先占住 best、
        # 把后面同分的廉价解挡在剪枝外面（2026-10-28）。
        # 必走格排最前：可行解越早出现，上界剪枝越早收紧（2026-10-28 必走点约束）。
        out.sort(key=lambda z: (-z[0], z[7], z[6]))
        return out

    def _prune_cycles(self, path):
        """删掉「回到已访问节点」的绕路（只删循环内全是**更早**已访问节点的段）。

        2026-10-30 六层源流：5 AP 但只有 3 格可走时，求解器会靠 1,2→0,2→1,2 来回踱步
        把 AP 填满 —— 分数一样，但输出可避免的回头步会被判 ❌。这里机械剪掉：
        被删段里不能有**首次**访问的节点（否则会丢分）；start 视为 index -1。
        """
        p = list(path)
        changed = True
        while changed:
            changed = False
            first = {self.start: -1}
            for j, (q, _n, _g) in enumerate(p):
                if q in first:
                    i = first[q]
                    seg = p[i + 1:j]
                    if all(first.get(x[0], 10 ** 9) < i for x in seg):
                        del p[i + 1:j + 1]
                        changed = True
                        break
                else:
                    first[q] = j
        return tuple(p)

    def run(self, slack=0, max_alts=0, node_limit=20000000, max_time=0.0, tie_break=True):
        """求最优路线。返回 (毛分, 路径, 是否撞上限)。

        两阶段（2026-10-28，玩家要求保留「同分但更省道具」的路径）：
          阶段 A：只最大化毛分，剪枝用 `_ub <= 最优`（**快路径**，与旧行为一致）。
          阶段 B：在「毛分必须追平 A 的结论」前提下最小化道具价，
                  剪枝用 `_ub < S` 或 `已花道具价 >= 当前最省`。
        A 已证明 S 是最大毛分 → B 只可能在 S 上更便宜，不会更差。

        **B 有自己的预算**（默认 max_time 的 35% / 300 万状态）：
        「证明不存在更省的同分解」在最坏情况下是指数级（实测 73 分那局
        壳层有 120 万状态、20M 都跑不完）。所以：
          * B 跑完 → `cost_proven=True`，代价就是**最小值**；
          * B 被预算截断 → `cost_proven=False`，代价是「已找到的最省」，
            报告里要写明「可能还有更省的」，**不能当定论**。
        分数最优性不受 B 影响：A 已经证明，B 只是锦上添花。
        """
        start_mask = 0
        for q in self.visited0:
            if q in self.bit:
                start_mask |= 1 << self.bit[q]
        items0 = tuple(it["uses"] for it in self.items)
        unit_cost = [self.cost_of.get(it["name"], 2) for it in self.items]

        if self.final is not None and self.final not in self.nodes:
            print("!! final=%s 不在该构型的格子里，已忽略该约束" % (self.final,))
            self.final = None

        def search(tie=False, seed=None, t_end=0.0, node_cap=None):
            """tie=False → 阶段A；tie=True → 阶段B（seed = 阶段A 的 (分, 路径, 价)）"""
            st = {"best": [-1, ()], "cost": 1 << 30, "hit": False,
                  "tie": 0, "expanded": 0, "cut": False}
            if seed is not None:
                st["best"] = [seed[0], seed[1]]
                st["cost"] = seed[2]
            memo = {}
            stack = []      # 路径用栈：每步 append/pop，只在更新最优解时复制一次（性能建议 3）

            def _better(tot, cost, nsteps):
                """同分/同价时**步数少者优先**：避免靠回头步把 AP 填满（2026-10-30）。"""
                bs, bc = st["best"][0], st["cost"]
                if tot > bs + EPS:
                    return True
                if abs(tot - bs) <= EPS:
                    if cost < bc:
                        return True
                    if cost == bc and nsteps < len(st["best"][1]):
                        return True
                return False

            def dfs(pos, mask, ap, items, tot, cost):
                """单次 DFS（阶段 A/B 共用）。cost = 已消耗道具价，沿路径累加。

                几个**容易改回去**的形态（教训全文在 CHANGELOG，动手前先读）：
                · **final 约束**：最后一格是 final 就收解并停止。旧写法「非终点就不更新
                  best」既不强制走到终点，还会配合上界剪枝把通往终点的分支砍掉（2026-10-27）。
                · **必走点是硬约束**：mask 没凑齐不收解；「还没踩的必走点数 > 还能走的
                  步数（含未踩 grant、免AP件）」直接剪——每步最多覆盖 1 个新格（2026-10-28）。
                · **免AP件放宽**：ap=0 时仍可能靠免AP件/bonus 再走。放宽量必须按剩余次数
                  算——只给布尔 1 会把「两件免AP件 + AP=0」的最优剪掉（不可采纳的界）。
                · **剪枝与 EPS**：阶段A `_ub <= bs` 就剪；阶段B `_ub < bs` 才剪、追平时比
                  道具价（已花的钱只增不减 ⇒ 「已花 ≥ 当前最省」的追平不可能更省）。
                  分数是**浮点**（诡秘 5.7 这种）：「本路累加」与「ub 前 k 大之和」的加法
                  顺序不同，数学追平也会差 1e-15，所以判追平/更新最优都要带 EPS——
                  否则同分更省在浮点场景**静默失效**（2026-10-28 复查发现）。
                · **memo 按毛分支配**：key 含 items，同一 key 的道具价必然相同，
                  不会丢掉更便宜的等价状态。
                """
                st["expanded"] += 1
                if node_cap and st["expanded"] > node_cap:
                    st["cut"] = True
                    return
                if t_end and time.time() > t_end:
                    st["cut"] = True
                    return
                # final / final_type：最后一格即收解并停止；必走点：mask 不齐不收解
                # （final_type = 险路终点模式：走到任何一个该类节点即可收解，见 __init__）
                _miss = self.must_mask & ~mask
                if self.final is not None:
                    if pos == self.final:
                        if not _miss and _better(tot, cost, len(stack)):
                            st["best"] = [tot, tuple(stack)]
                            st["cost"] = cost
                        return               # 终点即止
                elif self.final_types:
                    _t = self.nodes.get(pos)
                    _ok_better = (not _miss and _better(tot, cost, len(stack)))
                    if _t in self.final_hard:
                        if _ok_better:
                            st["best"] = [tot, tuple(stack)]
                            st["cost"] = cost
                        return               # 硬终点即止（碰到该类节点 = 路线到此为止）
                    if _t in self.final_types and _ok_better:
                        # 软终点（险路小径）：可以**停在这里**作为最后一步，
                        # 但**不 return**——继续走也不违规（它本来就可进可出）
                        st["best"] = [tot, tuple(stack)]
                        st["cost"] = cost
                elif not _miss and _better(tot, cost, len(stack)):
                    st["best"] = [tot, tuple(stack)]
                    st["cost"] = cost
                if max_alts:
                    if tot >= st["best"][0] - slack:
                        key = mask
                        if key not in self.alts or self.alts[key][0] < tot:
                            self.alts[key] = (tot, tuple(stack))
                            if len(self.alts) > 4000:
                                for k in list(self.alts)[:2000]:
                                    self.alts.pop(k, None)
                # 免AP件/bonus 在 ap=0 时仍可能再走：放宽量必须按剩余次数给（见 docstring）
                _extra_moves = 0
                for _k in range(len(items)):
                    if items[_k] > 0:
                        _it = self.items[_k]
                        if _it["free"]:
                            _extra_moves += items[_k]        # 每次免AP件 = 白拿一格
                        if _it.get("bonus"):
                            _extra_moves += items[_k] * _it["bonus"]
                if ap + _extra_moves <= 0:
                    return
                _k = self.budget(mask, ap, _extra_moves)    # 还能走几步（乐观：含未踩 grant）
                # 必走点剪枝：一步最多覆盖 1 个新格 → 差得太多就不可能凑齐
                if _miss and bin(_miss).count("1") > _k:
                    self.must_cuts += 1
                    return
                _ub = tot + self.ub_k(mask, _k)
                bs, bc = st["best"][0], st["cost"]
                # 剪枝：阶段A 追平即剪；阶段B 追平时还要比道具价。EPS 不可省（见 docstring）
                if _ub < bs - EPS:
                    return
                if _ub <= bs + EPS:
                    if not tie:
                        return
                    if bs >= 0 and cost >= bc:
                        return
                    st["tie"] += 1
                key = (pos, mask, ap, items)   # memo 按毛分支配：key 含 items ⇒ 同 key 同价
                prev = memo.get(key)
                if prev is not None and prev >= tot:
                    return
                memo[key] = tot
                adjset = self.adj.get(pos, ())
                for g, q, name, nap, nm, ni, dcost, _mr in self.moves(
                        pos, mask, ap, items, adjset, unit_cost):
                    stack.append((q, name, g))
                    dfs(q, nm, nap, ni, tot + g, cost + dcost)
                    stack.pop()

            dfs(self.start, start_mask, self.ap0, items0, 0, 0)
            self.expanded += st["expanded"]
            return st

        t0 = time.time()
        deadline = (t0 + max_time) if max_time and max_time > 0 else 0.0
        a = search(t_end=deadline, node_cap=node_limit)

        # 必走点走不到 → 清掉约束回落重解，漏了哪些点由调用方显式告警（不装作没事）
        if a["best"][0] < 0 and self.must_mask:
            self.must_dropped = True
            keep = self.must_mask
            self.must_mask = 0
            remain = (deadline - time.time()) if deadline else 0.0
            # 回落也要有预算：至少给一点，否则「约束无解」会变成「工具无输出」
            fallback_end = time.time() + max(remain, min(max_time * 0.5, 20.0) if max_time else 5.0)
            a = search(t_end=fallback_end, node_cap=node_limit)
            self.must_mask = keep
        best = a["best"]
        cost = a["cost"]
        hit = a["cut"]
        proven = (cost == 0)    # 没花道具时「最省」是平凡的，无需阶段B
        tried_b = False
        # 阶段B：只在有道具可省、A 已证明最大毛分时跑，且**只用 A 剩下的预算**
        if tie_break and not hit and self.items and cost > 0:
            remain = (deadline - time.time()) if deadline else 3.0
            if remain > 0.5:
                tried_b = True
                b = search(tie=True, seed=(best[0], best[1], cost),
                           t_end=time.time() + remain,
                           node_cap=min(node_limit, 3000000))
                proven = not b["cut"]
                # 同分比较必须带 EPS（浮点），否则更省解换不上来（见 docstring）
                if (b["best"][0] > best[0] + EPS
                        or (abs(b["best"][0] - best[0]) <= EPS and b["cost"] < cost)):
                    best = b["best"]
                    cost = b["cost"]
        # 剪掉可避免的回头步（不丢分）：输出更短的同分路线，过路线校验
        best = [best[0], self._prune_cycles(best[1])]
        # 必走点覆盖情况（回落时用来告警）
        covered = {q for q, _it, _g in best[1]}
        self.must_uncovered = tuple(sorted(self.must - covered))
        # 迷藏不再有 ε：橡果的 12 分是**真实分**，已经并进 cell_values（见文件头注释）。
        # 命中数按「橡果格或其传送对端被踩过」算（带橡果的密道：踩任一端都拿到了）。
        _hit = set(covered & self.acorn)
        for q in self.acorn:
            o = self.portal.get(q)
            if o is not None and o != q and o in covered:
                _hit.add(q)
        self.acorn_hits = len(_hit)
        clean = round(best[0], 6)
        self.last_cost = {"score": clean, "cost": cost,
                          "tie_phase": tried_b, "cost_proven": proven}
        return clean, best[1], hit

    def fmt(self, path):
        """重建路径并打印。免行动力道具（喷气背包类）不扣 AP——早先版本一律减 1，
        导致结尾出现「余AP=-1」这种误导数字（搜索本身是对的）。

        2026-10-28：遥控器这类「移动后 +N AP」的道具，打印也要把 bonus 记上，
        否则路线看着像在透支行动力。

        2026-10-30：带橡果的**曲折密道**会被放行，而路径里存的是**落点**（另一端）。
        所以这里要倒推「这一跳其实是踩了哪一格」——否则玩家看到「从 (6,3) 走到 (0,0)」
        完全无法执行。判据：落点 q 是密道端点、且上一格挨着它的**另一端**，
        说明这一跳踩的是另一端（强制传送）。"""
        free_names = {it["name"] for it in self.items if it.get("free")}
        bonus_of = {it["name"]: int(it.get("bonus", 0)) for it in self.items}
        ap = self.ap0
        granted_seen = set()
        prev = self.start
        lines = []
        for q, name, g in path:
            cost = 0 if (name in free_names) else 1
            gain = 0 if q in granted_seen else self.grants.get(q, 0)
            granted_seen.add(q)
            ap = ap - cost + gain + bonus_of.get(name, 0)
            tag = name or "走"
            if name:
                marks = []
                if cost == 0:
                    marks.append("免AP")
                if bonus_of.get(name):
                    marks.append("+%dAP" % bonus_of[name])
                if marks:
                    tag = "<%s>(%s)" % (name, "、".join(marks))
                else:
                    tag = "<%s>" % name
            # 强制传送：落点 q 的另一端才是我实际踩上去的格
            portal_src = None
            other = self.portal.get(q)
            if other is not None and other != q and prev in self.adj.get(other, ()):
                portal_src = other
            # %4g：分值可能是 4.8 / 18.5，以前 %3d 会截断成 4 / 18（和总分对不上）
            lines.append("  (%d,%d) %-8s %-18s 得%4g 余AP=%d"
                         % (q[0], q[1], self.nodes.get(q, "?"), tag, g, ap)
                         + ("   ← 踩 (%d,%d) 强制传送" % portal_src if portal_src else "")
                         + ("   ← 二结局：进入后加工品次数强制→1" if q in self.clamp_at else ""))
            prev = q
        return "\n".join(lines)


def resolve_items(path, items):
    """把路径里的道具名落实成「具体是哪一件」。

    历史上求解器曾把同效果道具并成「等价类」（为了压状态空间），归一化后会把
    「用掉的引擎A」写成「没用过的引擎B」，打印出来就成了「同一件用了 5 次」——
    那条优化已删除（2026-10-27，风险大于收益），现在按逐件余量记录。

    本函数仍然保留：**同名多件**（如 2 件标准引擎）在路径里只记名字，
    这里按真实余量把它们落实成「具体哪一件」，保证输出与库存对得上。
    """
    rem = [it["uses"] for it in items]
    out = []
    for pos, name, g in path:
        if name is None:
            out.append((pos, None, g))
            continue
        idx = next((i for i, it in enumerate(items)
                    if it["name"] == name and rem[i] > 0), None)
        if idx is None:
            base = next((it for it in items if it["name"] == name), None)
            if base is not None:
                idx = next((i for i, it in enumerate(items)
                            if it["cov"] == base["cov"] and rem[i] > 0
                            and it.get("cost", 0) == base.get("cost", 0)), None)
        if idx is None:
            out.append((pos, name, g))
            continue
        rem[idx] -= 1
        out.append((pos, items[idx]["name"], g))
    return out


def _load_or_die(path):
    """读 scenario；文件不在 / JSON 坏掉时给判词，而不是 Python traceback。

    2026-10-31 QA：`python route_solver.py --help` 和「路径写错」都会抛
    `FileNotFoundError` traceback（exit 1）——这是给人直接调的低层入口，
    报错要说清「该传什么」，不能让人以为工具坏了。
    """
    if not os.path.isfile(path):
        print("!! 找不到 scenario 文件：%s" % path)
        print("   用法：python route_solver.py <scenario.json> [--check \"x,y;x,y\"]"
              " [--alts 6] [--ban x,y] [--max-time 60]")
        print("   场景由 tools/route.py / build_scenario.py 生成；"
              "手写的先跑 tools/check_scenario.py 核对边表。")
        raise SystemExit(2)
    try:
        return load(path)
    except json.JSONDecodeError as exc:
        print("!! scenario JSON 解析失败：%s" % exc)
        print("   先跑 tools/check_scenario.py 核对；手改 JSON 时注意逗号/引号。")
        raise SystemExit(2)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--check":
        if len(args) < 3:
            print("用法：python route_solver.py --check \"0,1;0,0\" scenario.json")
            raise SystemExit(2)
        sc = _load_or_die(args[2])
        pts = [tuple(map(int, s.split(","))) for s in args[1].split(";")]
        ok, log = check_path(sc, pts)
        print("路径校验:", "通过" if ok else "**不通过**")
        for line in log:
            print("  " + line)
        return
    sc = _load_or_die(args[0])

    # [临时停用前置校验做对照]
    ban = ()
    if "--ban" in args:
        ban = [tuple(map(int, s.split(","))) for s in args[args.index("--ban") + 1].split(";")]
    slack = int(args[args.index("--slack") + 1]) if "--slack" in args else 0
    alts = int(args[args.index("--alts") + 1]) if "--alts" in args else 0
    max_time = float(args[args.index("--max-time") + 1]) if "--max-time" in args else 30.0   # 默认 30s 封顶，撞到就交已找到的最好解
    node_limit = int(args[args.index("--node-limit") + 1]) if "--node-limit" in args else 20000000
    # 只要分数、不做同分比价（省掉第二阶段；重层快 10s 左右）
    tie_break = "--no-tie-break" not in args
    # 忽略 scenario 里的必走点（对照实验用：看「不管必走点」的最优解长什么样）
    if "--no-must" in args:
        sc["must"] = []
    t0 = time.time()
    s = Solver(sc, ban)
    total, path, hit = s.run(slack=slack, max_alts=alts, max_time=max_time,
                             node_limit=node_limit, tie_break=tie_break)
    path = resolve_items(path, list(s.items))
    dt = time.time() - t0
    print("== %s ==" % sc.get("name", ""))
    print(s.fmt(path))
    print("总计价值 %g，走了 %d 步 ｜ 展开 %d 个状态，用时 %.2fs%s"
          % (total, len(path), s.expanded, dt,
             "（撞到节点上限/时间上限，结果可能非最优）" if hit else ""))
    # 同分比价：报出这条路线烧掉的**道具价**，以及「最省」是否已被证明。
    # 未证明时必须写清楚——否则会被当成「这就是最省的」。
    lc = getattr(s, "last_cost", None)
    # 无解时 cost 是 1<<30 哨兵，别把它当真实道具价打印（2026-10-29 修）
    if lc and 0 < lc["cost"] < (1 << 29):
        used = [(q, it) for q, it, _g in path if it]
        detail = "、".join("%s(%d)" % (it, s.cost_of.get(it, 2)) for _q, it in used)
        if lc["cost_proven"]:
            print("道具价 %d（%s）｜同分更省的已搜完，**这就是最省**" % (lc["cost"], detail))
        elif lc["tie_phase"]:
            print("道具价 %d（%s）｜⚠️ 同分更省的搜索被预算截断，**可能还有更省的同分解**"
                  % (lc["cost"], detail))
        else:
            print("道具价 %d（%s）｜未做同分比价（--no-tie-break 或第一阶段已用完预算）"
                  % (lc["cost"], detail))

    # ── 回头步检测 ────────────────────────────────────────────
    # 求解器不知道「哪一层的地图长什么样」，重踩一个已访问的格子（尤其是起点）
    # 在它眼里完全合法：值为 0，不违反任何约束。但如果这一步不必要，就是纯浪费。
    # 2026-10-27：一层路线里出现 (2,0)→(2,1) 重踩起点，被当成「同分替代」放过了，
    # 还写了「剩下 2 点只能浪费」——那 2 点本来能换别的。
    # 起点算「已访问」——重踩起点也是一次回头（2026-10-27 就是这种没被算出来）
    seen_nodes = {tuple(sc["start"])} if sc.get("start") else set()
    backs = []
    for i, (q, _it, _cv) in enumerate(path):
        if q in seen_nodes:
            backs.append((i + 1, q, i))
        seen_nodes.add(q)
    if backs:
        print("↺ 回头步 %d 处（重踩已访问格）：" % len(backs))
        for step, q, prev in backs:
            t = s.nodes.get(q, "")
            print(f"   第 {step} 步重踩 ({q[0]},{q[1]})「{t}」——值为 {s.vals.get(t, 0)}")
        print("   → **必须逐条确认这一步是否必要**：能不回头就换条路，"
              "回头了却没换到更值的东西就是纯浪费。")

    # ── 行动力收支校验 ────────────────────────────────────────
    # 「安全的角落 +2 AP」这类**选项收益**一旦写进 grants，就等于白送行动力，
    # 求解器会算出根本走不完的长路线（2026-10-27：5 点行动力跑了 8 个节点）。
    # grants 只允许来自**必定给行动力**的节点。
    # 按模式取合法来源（rules.grant_ok_types）：普通分队模式下树根不再回复 AP，
    # 谁往 grants 里写树根 +2 就是灌水，照样拦。
    GRANT_OK = set(R.grant_ok_types())
    triggered = {}
    # 每步的实际消耗：免行动力道具（喷气背包/重弹簧）那一步不扣 AP；
    # 遥控器这类「移动后 +N AP」要记回收入（2026-10-28）
    free_names = {it["name"] for it in s.items if it.get("free")}
    bonus_of = {it["name"]: int(it.get("bonus", 0)) for it in s.items}
    spent = 0
    for q, item, _cov in path:
        if q in s.grants:
            triggered[q] = s.grants[q]
        spent += 0 if (item in free_names) else 1
    granted = sum(triggered.values())
    bonus = sum(bonus_of.get(it, 0) for _q, it, _c in path if it)
    budget = s.ap0 + granted + bonus
    print(f"AP 收支：初始 {s.ap0} + 授予 {granted}"
          + (f" + 道具 {bonus}" if bonus else "")
          + f" = {budget}，实走 {len(path)} 步（消耗 {spent} 点）→ "
          + ("平衡 ✅" if spent <= budget else "**超支**"))
    bad = []
    for q, v in s.grants.items():
        t = s.nodes.get(q, "")
        if t not in GRANT_OK:
            bad.append(f"({q[0]},{q[1]}) = {t} 给了 +{v} AP，但「{t}」不是必定给行动力的节点")
    if bad:
        print("❌ grants 非法（选项收益不能当确定收益）：")
        for b in bad:
            print("   - " + b)
        _src = "羽瞰点(+1)" if R.is_normal_squad() else "树根(+2)、羽瞰点(+1)"
        print("   → 正常 +AP 来源只有：" + _src)
        print("   → 这条路线**不可用**：AP 预算被灌水，步数不代表真的走得完。")
        print("      把该项从 grants 删掉或改 0，再重跑。")
        return 3

    if alts:
        rows = sorted(s.alts.values(), key=lambda t: -t[0])[:alts]
        print("-- 同样好的替代（按落点集合去重）--")
        for v, p in rows:
            print("  val=%g  %s" % (v, " > ".join("(%d,%d)" % q for q, _, _ in p)))


if __name__ == "__main__":
    sys.exit(main())
