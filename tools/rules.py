#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py — 道具定义 / 分层定价 / 授予值的**唯一真源**

为什么单独一个文件
------------------
2026-10-28 审计查出三处「文档对、代码错」，根因是同一份规则在三个地方各抄了一遍：

| # | 现象 | 原因 |
|---|---|---|
| 1 | 鸣笛只跳相邻 8 格 | `route.py` 的 `ITEM_COV` 写 `adj8`（文档：周围 **12** 格） |
| 2 | 免AP件真跑时白扣 1 点行动力 | `route.py` / `build_scenario.py` 造 item 时不写 `free` |
| 3 | `build_scenario.py` 产出的 json 求解器读不了 | 造 item 时不写 `cov`，求解器 `it["cov"]` 直接 KeyError |

只要「一件道具的定义」还散在三处，改一处忘两处就是时间问题。
现在：**`ITEM_DEFS` 是唯一来源**，`route.py` / `build_scenario.py` / `route_solver.py`
都从这里取，`references/01-加工品.md` 末尾有一张同样内容的对照表（改这里要同步改那里）。

口径来源
--------
`references/01-加工品.md`（原文逐字）、`references/08-零件表.md`（价格/来源）。
数值单位是「消耗权重」（玩家 2026-10-27 口径），**不是节点的源石锭当量**。
"""
import re

# 覆盖域代号 → 求解器 COVER 函数（见 route_solver.py）
#   adj8   = 相邻 8 格（切比雪夫距离 1）
#   adj12  = 周围 12 格（曼哈顿距离 ≤ 2，含 4 个正交 2 格 + 4 个斜角）
#   line2  = 正前方/左右直线 2 格**内**（距离 1~2，共 8 个点）
#   line3  = 直线 3 格**内**（距离 1~3，共 12 个点）
#   ↑ 2026-10-28 玩家裁定：原文「直线3格内任意节点」含 1/2 格，不是恰好 3 格
#   row_col= 同行或同列任意
#   any    = 任意节点（可再用 only / exclude 收窄）
COVS = ("adj8", "adj12", "line2", "line3", "row_col", "any")

# 「无需行动力」的 4 件（01-加工品 原文标注）**不在这里手写**：
# 2026-11-02 P0 审计——这行原来是「第二份真源」，全项目**零引用**，
# 却随时可能与 ITEM_DEFS[...]["free"] 分家（改一处忘一处就是下一个漂移）。
# 现在 FREE_ITEMS 由 ITEM_DEFS 派生，见下方 PRICE 之后。

# 「作战家族」的前缀：地图上显示成「未知的凶戾」的格子，落地一定是 作战/紧急作战；
# 树根也显示成紧急作战（所以鸣笛能直飞树根、融雪不能落树根）。
# 这几条**必须一起改**：鸣笛的 only 用它，融雪/小八界的 exclude 也用它，
# 否则会出现「融雪落凶戾」这种非法路线（2026-10-28 实测踩到）。
COMBAT_PREFIXES = ["作战", "紧急作战", "普通作战", "凶戾", "未知的凶戾",
                   "树根", "理想源"]

ITEM_DEFS = {
    # ── N（7） ──────────────────────────────────────────────
    "报废轮子": dict(dura=1, cov="line2", price=2),
    "报废假肢": dict(dura=1, cov="adj8", price=2),
    "标准引擎": dict(dura=3, cov="adj8", price=4),
    "气垫底座": dict(dura=2, cov="adj12", price=4),
    "试作外骨骼": dict(dura=2, cov="line3", price=4),
    "小八界": dict(dura=1, cov="any", free=True, price=1,
                exclude=list(COMBAT_PREFIXES), random=True,
                note="随机传送至任意**非作战**节点。落点不可控，求解器按最好情况估=上界"),
    "鸣笛": dict(dura=1, cov="adj12", price=2,
               only=list(COMBAT_PREFIXES),
               note="周围 12 格内任意**作战**节点（2026-10-28 修正：原写 adj8）。"
                    "凶戾/树根也算作战节点——树根在地图上就是紧急作战，"
                    "所以鸣笛能直飞树根（03-节点收益）"),
    # ── R（5） ──────────────────────────────────────────────
    "重弹簧": dict(dura=1, cov="adj12", free=True, price=1),
    "一次性喷气背包": dict(dura=1, cov="any", free=True, price=4),
    "老妈妈的融雪": dict(dura=1, cov="any", price=8, exclude=list(COMBAT_PREFIXES),
                  note="任意**非作战**节点；每次移动后希望 +2（未建模）。"
                       "exclude 必须覆盖 凶戾/树根这一整族，否则会落进战区（2026-10-28 实测踩到）。"
                       "价格 8（玩家 2026-10-28 补充；01-加工品 的购买价表同值）"),
    "坎诺特的触须": dict(dura=2, cov="any", price=4, only=["诡意行商", "秘境行商"],
                  note="任意行商（不含应急助力）；每次移动后源石锭 +4（未建模）。"
                       "**价格 4 = 净成本**：12 源石锭买入、2 次移动自返 8（玩家 2026-10-28 口径）。"
                       "**只有「诡意行商」已探明时**才能在五层当进区件："
                       "五层右区虽然固定有一个行商，但它不一定被直接点亮，"
                       "没点亮时那格显示成「未知的诡秘」，限行商的件语义上落不了"),
    "紧急逃生座椅": dict(dura=1, cov="any", price=4,
                  only=["险路尽头", "险路小径", "险路恶敌"]),
    # ── SR（4） ─────────────────────────────────────────────
    "结构性原理": dict(dura=3, cov="any", price=16,
                 note="任意跳 3 次。价格 16（玩家 2026-10-28 上调，原 12）"),
    "简易遥控器": dict(dura=1, cov="adj8", free=True, bonus=3, price=16,
                 note="相邻 8 格、免AP、**移动后行动力 +3**。价格 16（玩家 2026-10-28 上调，原 12）"),
    "力反馈方向盘": dict(dura=2, cov="row_col", price=12),
    "坎诺特特快": dict(dura=1, cov="any", price=16, only=["不期而遇"],
                 note="落到任意不期而遇并**转成诡意行商**；转换收益未建模"
                      "（按不期而遇计价，偏保守）。价格 16 = 文档标价（玩家 2026-10-28 口径）"),
}

# 消耗权重表（玩家口径，单位=源石锭购买价）。缺省的按 2 处理。
PRICE = {n: d["price"] for n, d in ITEM_DEFS.items() if "price" in d}

# 「无需行动力」的 4 件 —— **派生值，不许手写**。
# `rules.py --check` 会独立再算一遍并与它比对（防止有人把常量抄回来）。
FREE_ITEMS = tuple(n for n, d in ITEM_DEFS.items() if d.get("free"))

# 行动力授予（节点自带效果）——不要散落在各脚本里
GRANT_AP = {"树根": 2, "理想源": 2, "羽瞰点": 1}
GRANT_OK = tuple(GRANT_AP)          # 允许出现在 grants 里的节点类型

# ---------------------------------------------------------------------------
# 分队模式（2026-10-29 玩家新增； SKILL.md「〇·〇」是权威描述）
#
#   **N16文明开化模式**（2026-10-30 改名；旧名「文明开化模式」）：
#              **通过追猎进入二~六层时，大概率出现理想源**（玩家 2026-10-30 口径，
#              原文写「必定出现」）。**实际代码不变**：仍按「本层有 1 个理想源」求解，
#              玩家必须报坐标；打掉 **+2 行动力 + 4 件收藏品** → 理想源/树根 15 分。
#              **推荐搭配「全程追猎」终点模式使用**（追猎才容易撞到理想源）。
#   普通分队 = 二~六层**不保证**出理想源：玩家报了 --root 才算有，**没报 = 本层无理想源**；
#              打掉**不再 +2 行动力、不再给 4 藏品**，价值 = 本层「紧急作战」的分值
#              （一~五层 9、六层 5）——它仍是紧急作战：占紧急配额、鸣笛能飞、融雪落不了。
#
# 一层在两种模式下都默认无理想源（20-一层特殊规则），不受本开关影响。
# 模式沿调用链传播：route.py / build_scenario.py 的 --mode → set_mode()；
# scenario JSON 落盘时写 `"mode"`（**内部仍是短名** 文明开化/普通分队，旧场景照样读），
# route_solver 直读文件时按它恢复。
# ---------------------------------------------------------------------------
MODES = ("文明开化", "普通分队")
DEFAULT_MODE = "文明开化"
_MODE = None

# 显示名（判词/转述文案用）：内部短名不变，只有给人看的名字带 N16
MODE_LABELS = {"文明开化": "N16文明开化模式", "普通分队": "普通分队模式"}

# 输入侧别名 → 内部短名（玩家/命令行写「N16文明开化」「N16文明开化模式」都收）
MODE_ALIASES = {
    "N16文明开化": "文明开化",
    "N16文明开化模式": "文明开化",
    "文明开化模式": "文明开化",
    "普通分队模式": "普通分队",
}


def normalize_mode(m):
    """把玩家/命令行写的模式名归一到内部短名（None 原样返回，未知名原样返回）。"""
    if m is None:
        return None
    return MODE_ALIASES.get(str(m).strip(), str(m).strip())


def mode_choices():
    """argparse 的 --mode 可选值（内部短名 + 别名，玩家怎么写都收）。"""
    return list(MODES) + sorted(MODE_ALIASES)


def set_mode(m):
    """设定本进程的分队模式（None = 恢复默认）。未知模式名直接抛错。"""
    global _MODE
    if m is None:
        _MODE = None
        return
    m = normalize_mode(m)
    if m not in MODES:
        raise ValueError(f"未知分队模式「{m}」；可选：{'、'.join(mode_choices())}")
    _MODE = m


def mode():
    """当前分队模式名（内部短名；未设定 = 默认「文明开化」）。"""
    return _MODE or DEFAULT_MODE


def mode_label(m=None):
    """分队模式**显示名**：「N16文明开化模式」/「普通分队模式」（判词与转述文案用）。"""
    key = normalize_mode(m) if m is not None else mode()
    return MODE_LABELS.get(key, str(key))


def is_normal_squad():
    """是否「普通分队模式」（理想源降级为普通紧急作战）。"""
    return mode() == "普通分队"


def root_value(layer):
    """理想源/树根在**当前模式**下的分值。

    N16文明开化 = 15（含 4 藏品的敏感性验证值，CHANGELOG 2026-10-25）；
    普通分队 = 本层「紧急作战」的分值（值真源：LAYER_PRICES，一~五层 9、六层 5）。
    """
    layer = int(layer)
    if is_normal_squad():
        return float(LAYER_PRICES[layer].get("紧急作战", 5 if layer == 6 else 9))
    return 15.0


def grant_ap(node_type):
    """节点自带的**确定**行动力授予（含模式开关）。

    普通分队模式：树根/理想源不再回复 AP（羽瞰点 +1 不变）。
    route.py 的 grants 自动补、route_solver 的 AUTO_AP 补都**必须**走这里，
    不要直接读 GRANT_AP——那是文明开化的口径。
    """
    if node_type in ("树根", "理想源") and is_normal_squad():
        return 0
    return int(GRANT_AP.get(node_type, 0))


def grant_ok_types():
    """grants 里允许出现的节点类型（按模式）。

    普通分队模式下树根/理想源不该再出现在 grants 里——
    谁写进去了就是 AP 灌水，校验要拦（route.py / route_solver 各查一遍）。
    """
    if is_normal_squad():
        return ("羽瞰点",)
    return tuple(GRANT_AP)


# ---------------------------------------------------------------------------
# 终点模式（2026-10-29 玩家新增；与上面的「分队模式」**相互独立**，SKILL.md 〇·〇）
#
#   全程追猎 = 旧默认：一/二/四层最后一步**不**进险路尽头（一/四层尽头 block、
#              二层值 8 走 1 次）；三/五/六层险路恶敌照旧 block；层在行动力耗尽处自然结束。
#   险路尽头 = 一/二/四层**最后进入的节点必须是险路尽头**（多个只进其一；
#              二层进尽头固定为最后一步），且**最后一步之前不进任何尽头**；
#              三/五/六层**最后进入的节点必须是险路恶敌**，且之前不进恶敌。
#              终点类型重新定价（不再 block、不再 -99/-6）——见 END_FINAL_VALUE。
#
# 2026-10-30 玩家新增（含同日修正）：**一/四层**的**险路小径**与险路尽头同待遇——不再排除、
# **同分值**、且**是最后一步的候选点之一**。**二层是小径的唯一例外**：小径拿不到「开启四结局的
# 藏品」，所以二层小径 = 0 分、**不能当最后一步**，二层终点只能是险路尽头（8 分）。
# 注意小径**不是**「碰到即结束」的节点（它本来就可进可出、常作一层中继），所以求解器里它是
# **软终点**：停在这里算合法收尾，继续走也不算违规。尽头/恶敌仍是**硬终点**（进层就结束，之前不许进）。
#
# 两轴正交：N16文明开化/普通分队 × 全程追猎/险路尽头 任选组合。
# 场景落盘为 `"end_mode"` 字段；求解器按 `sc["final_type"]`（本层终点类型，**字符串或列表**）
# 收终点——硬终点碰到即结束，「最后一步之前不进该类节点」由结构自动成立。
# ---------------------------------------------------------------------------
END_MODES = ("全程追猎", "险路尽头")
DEFAULT_END_MODE = "全程追猎"
_END_MODE = None


def set_end_mode(m):
    """设定本进程的终点模式（None = 恢复默认「全程追猎」）。未知模式名直接抛错。"""
    global _END_MODE
    if m is None:
        _END_MODE = None
        return
    m = str(m).strip()
    if m not in END_MODES:
        raise ValueError(f"未知终点模式「{m}」；可选：{'、'.join(END_MODES)}")
    _END_MODE = m


def end_mode():
    """当前终点模式名（未设定 = 默认「全程追猎」）。"""
    return _END_MODE or DEFAULT_END_MODE


def is_end_jintou():
    """是否「险路尽头」终点模式（最后一步必须落在尽头/恶敌上）。"""
    return end_mode() == "险路尽头"


# ---------------------------------------------------------------------------
# 二层「四结局」问询（2026-10-30 玩家新增）
#
# **只要本局要规划二层**（玩家手报「二层」或截图识别到二层，都算），
# 必须先问玩家，再求解：
#
#   全程追猎：问一个问题
#       本局是否要打四结局？
#         要   → 旧口径：二层险路尽头 = 8（四结局道具），走 1 次
#         不要 → 二层险路尽头 = 0，**不再必走**（可当 0 分中继）
#
#   险路尽头：问两个问题
#       Q1 本局是否要打四结局？
#         不要 → 二层险路小径 = 险路尽头（8），**可作二层可选终点**
#         要   → Q2 是否已获取四结局前置藏品？
#                  已获取 → 二层险路小径 = 险路尽头（8），**可作二层可选终点**
#                  未获取 → 旧口径：小径 0、不作终点；二层终点只能是尽头
#
# 状态只在本进程内。CLI（route/build_scenario/apply_config/observe）在二层
# 必须显式传 `--four-ending`；险路尽头+要 还要 `--four-ending-pre`，
# 否则 `layer2_answer_errors()` 报错，逼执行方先把问题问完。
# **未回答时按旧口径兜底**：兼容旧 scenario 和直接读文件的 route_solver。
# ---------------------------------------------------------------------------
FOUR_ENDING_CHOICES = ("要", "不要")
FOUR_PRE_CHOICES = ("已获取", "未获取")
_FOUR_ENDING = None          # None = 还没问；True = 要；False = 不要
_FOUR_ENDING_PRE = None      # None = 还没问；True = 已获取；False = 未获取

_FOUR_TRUE = ("要", "打", "是", "yes", "y", "true", "1")
_FOUR_FALSE = ("不要", "不打", "否", "no", "n", "false", "0")
_PRE_TRUE = ("已获取", "已获得", "已拥有", "已拿", "有", "是", "yes", "y", "true", "1")
_PRE_FALSE = ("未获取", "未获得", "未拥有", "没拿", "没有", "没", "否", "no", "n", "false", "0")


def normalize_four_ending(v):
    """把玩家回答归一到 True/False/None（None = 未问）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _FOUR_TRUE:
        return True
    if s in _FOUR_FALSE:
        return False
    raise ValueError(f"未知四结局回答「{v}」；可选：要 / 不要")


def normalize_four_ending_pre(v):
    """把「是否已获取四结局前置藏品」归一到 True/False/None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _PRE_TRUE:
        return True
    if s in _PRE_FALSE:
        return False
    raise ValueError(f"未知前置藏品回答「{v}」；可选：已获取 / 未获取")


def set_four_ending(v):
    """设定本局「是否要打四结局」（None = 未问）。"""
    global _FOUR_ENDING
    _FOUR_ENDING = normalize_four_ending(v)


def set_four_ending_pre(v):
    """设定「是否已获取四结局前置藏品」（None = 未问）。"""
    global _FOUR_ENDING_PRE
    _FOUR_ENDING_PRE = normalize_four_ending_pre(v)


def four_ending():
    """本局是否打四结局（True/False；None = 还没问）。"""
    return _FOUR_ENDING


def four_ending_pre():
    """是否已获取四结局前置藏品（True/False；None = 还没问/不适用）。"""
    return _FOUR_ENDING_PRE


def layer2_jintou_value():
    """二层「险路尽头」在**全程追猎**下的基础分（四结局=不要 → 0）。"""
    return 0.0 if _FOUR_ENDING is False else 8.0


def layer2_trail_equals_jintou():
    """二层「险路小径」是否与险路尽头同分（且可作可选终点）。

    只在**险路尽头**终点模式下有意义：
      · 四结局 = 不要 → 是（小径顶替尽头）
      · 四结局 = 要 + 前置藏品已获取 → 是
      · 四结局 = 要 + 前置藏品未获取 → 否（旧口径：小径 0、不作终点）
    未回答（None）按旧口径（否）兜底。
    """
    if _FOUR_ENDING is False:
        return True
    return _FOUR_ENDING is True and _FOUR_ENDING_PRE is True


def layer2_answer_errors(layer):
    """二层问询是否问全。返回错误列表（空 = 可以求解）。

    CLI 在**任何**读图/建场景之前调用；非二层传了四结局参数也算错
    （防止把上一层的回答带到别的层）。
    """
    layer = int(layer)
    if layer != 2:
        if _FOUR_ENDING is not None or _FOUR_ENDING_PRE is not None:
            return ["--four-ending / --four-ending-pre 只在二层有意义"]
        return []
    if _FOUR_ENDING is None:
        return ["二层必须先问玩家「本局是否要打四结局？」"
                "（答「要」或「不要」，再传 --four-ending）"]
    errs = []
    if is_end_jintou():
        if _FOUR_ENDING and _FOUR_ENDING_PRE is None:
            errs.append("险路尽头模式 + 四结局=要：还必须问问题二"
                        "「是否已获取四结局前置藏品？」（--four-ending-pre 已获取|未获取）")
        if (not _FOUR_ENDING) and _FOUR_ENDING_PRE is not None:
            errs.append("四结局=不要：问题二不适用，--four-ending-pre 不该传")
    elif _FOUR_ENDING_PRE is not None:
        errs.append("全程追猎模式只问一个问题：--four-ending-pre 不该传")
    return errs


def layer2_plan_text(layer):
    """给人看的二层四结局口径（判定/场景备注用；非二层返回空串）。"""
    if int(layer) != 2:
        return ""
    if _FOUR_ENDING is None:
        return "四结局=未问（必须先问玩家，不许先算）"
    if _FOUR_ENDING is False:
        if is_end_jintou():
            return ("四结局=不要 → 二层险路小径 = 险路尽头（8 分）、可作可选终点；"
                    "终点候选 = 险路尽头 / 险路小径")
        return "四结局=不要 → 二层险路尽头 = 0、不再必走（可作 0 分中继）"
    if is_end_jintou():
        if _FOUR_ENDING_PRE is True:
            return ("四结局=要 + 前置藏品=已获取 → 二层险路小径 = 险路尽头（8 分）、"
                    "可作可选终点；终点候选 = 险路尽头 / 险路小径")
        return ("四结局=要 + 前置藏品=未获取 → 二层险路小径 = 0、不作终点；"
                "二层终点只能是险路尽头")
    return "四结局=要 → 二层险路尽头 = 8（四结局道具，必走 1 次）"


# ---------------------------------------------------------------------------
# 三层「三结局」问询（2026-10-30 玩家新增）
#
# **只在「三层 + 本层没有右区（熄日余径）」时问**：有右区 = 本局走的是四结局线
# （右区只在四结局前置藏品已获取时出现，见 25-右区有无），三结局问询不适用。
#
#   Q1 本局是否打三结局？
#     不要 → 普通逻辑（先行一步仍是 2 分、不必走）
#     要   → Q2 是否已获取三结局前置收藏品？
#              已获取 → 普通逻辑（已经拿到，不必再找）
#              未获取 → 三层把「先行一步」按 **15 分必走点**处理：
#                       · 已探明 → 写进 must，强制走到；
#                       · 藏在「未知的诡秘」里 → 列出距离带 2–4 的所有候选坐标，
#                         提示玩家去这些格子找它（进去就拿到前置收藏品）。
#
# 15 分 = 玩家口径（与树根的 4 件藏品同档）。它仍是普通非作战节点：
# 占 1 步、不回复 AP，鸣笛/融雪的 only/exclude 口径都不变。
# 状态只在本进程；CLI 在三层无右区时必须显式传 `--three-ending`，没问全由
# `layer3_answer_errors()` 拦住（与二层四结局同一套纪律）。
# **未回答按旧口径兜底**（先行一步 2 分、不必走），旧 scenario 照样能跑。
# ---------------------------------------------------------------------------
THREE_ENDING_CHOICES = ("要", "不要")
THREE_PRE_CHOICES = ("已获取", "未获取")
THREE_XIANXING_NODE = "先行一步"
THREE_XIANXING_VALUE = 15.0
_THREE_ENDING = None          # None = 还没问；True = 要；False = 不要
_THREE_ENDING_PRE = None      # None = 还没问；True = 已获取；False = 未获取


def normalize_three_ending(v):
    """把玩家回答归一到 True/False/None（None = 未问）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _FOUR_TRUE:
        return True
    if s in _FOUR_FALSE:
        return False
    raise ValueError(f"未知三结局回答「{v}」；可选：要 / 不要")


def normalize_three_ending_pre(v):
    """把「是否已获取三结局前置收藏品」归一到 True/False/None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _PRE_TRUE:
        return True
    if s in _PRE_FALSE:
        return False
    raise ValueError(f"未知前置收藏品回答「{v}」；可选：已获取 / 未获取")


def set_three_ending(v):
    """设定本局「是否打三结局」（None = 未问）。"""
    global _THREE_ENDING
    _THREE_ENDING = normalize_three_ending(v)


def set_three_ending_pre(v):
    """设定「是否已获取三结局前置收藏品」（None = 未问）。"""
    global _THREE_ENDING_PRE
    _THREE_ENDING_PRE = normalize_three_ending_pre(v)


def three_ending():
    """本局是否打三结局（True/False；None = 还没问）。"""
    return _THREE_ENDING


def three_ending_pre():
    """是否已获取三结局前置收藏品（True/False；None = 还没问/不适用）。"""
    return _THREE_ENDING_PRE


def three_ending_need_xianxing():
    """三层是否要把「先行一步」按 15 分必走点处理。

    只有 **三结局=要 且 前置收藏品=未获取** 才成立；其余（不要 / 已获取 /
    还没问）都按普通逻辑（先行一步 2 分、不必走）。
    """
    return _THREE_ENDING is True and _THREE_ENDING_PRE is False


def layer3_scope_errors(layer):
    """三结局参数是否用错层（可在**读图之前**调用，与右区无关）。"""
    layer = int(layer)
    if layer != 3 and (_THREE_ENDING is not None or _THREE_ENDING_PRE is not None):
        return ["--three-ending / --three-ending-pre 只在三层（且本层无右区）有意义"]
    return []


def layer3_answer_errors(layer, has_right=None):
    """三层「三结局」问询是否问全。返回错误列表（空 = 可以求解）。

    只在**无右区**时问：`has_right=True` 表示本层有熄日余径（四结局线）——
    这时传了三结局参数算错（防止把不适用的题硬套上去）。
    `has_right=None`（还没判出右区）按「无右区」严格要求。
    """
    errs = layer3_scope_errors(layer)
    if errs or int(layer) != 3:
        return errs
    if has_right is True:
        if _THREE_ENDING is not None or _THREE_ENDING_PRE is not None:
            return ["本层有右区（熄日余径）→ 三结局问询不适用；"
                    "--three-ending / --three-ending-pre 不该传"]
        return []
    if _THREE_ENDING is None:
        return ["三层无右区（熄日余径不存在）→ 必须先问玩家「本局是否打三结局？」"
                "（答「要」或「不要」，再传 --three-ending）"]
    errs = []
    if _THREE_ENDING and _THREE_ENDING_PRE is None:
        errs.append("三结局=要：还必须问问题二「是否已获取三结局前置收藏品？」"
                    "（--three-ending-pre 已获取|未获取）")
    if (not _THREE_ENDING) and _THREE_ENDING_PRE is not None:
        errs.append("三结局=不要：问题二不适用，--three-ending-pre 不该传")
    return errs


def layer3_error_headline(errs, action="规划"):
    """给 CLI 的一句话标题：区分「还没问」（要停下来问玩家）与「有右区/参数不适用」。"""
    if any("有右区" in str(e) for e in errs or ()):
        return "本层有右区（熄日余径）→ 三结局问询不适用（有右区 = 走四结局线）"
    return f"三层的「三结局」问询还没完成——**先问玩家，再{action}**"


def layer3_plan_text(layer):
    """给人看的三层三结局口径（判词/场景备注用；非三层返回空串）。

    文案里**不要再用圆括号**：它会被 `route.py --explain` 的【结论】整段括起来，
    套两层括号读起来很乱（2026-10-30 实机输出复查）。
    """
    if int(layer) != 3:
        return ""
    if _THREE_ENDING is None:
        return "三结局=未问：三层无右区时必须先问玩家，不许先算"
    if _THREE_ENDING is False:
        return "三结局=不要 → 普通逻辑，先行一步仍 2 分、不必走"
    if _THREE_ENDING_PRE is True:
        return "三结局=要 + 前置收藏品=已获取 → 普通逻辑，不必再找先行一步"
    if _THREE_ENDING_PRE is False:
        return ("三结局=要 + 前置收藏品=未获取 → 先行一步 = 15 分必走点"
                "；已探明就锁坐标，藏着就列候选坐标去找")
    return "三结局=要；问题二还没问：是否已获取三结局前置收藏品？"


# ---------------------------------------------------------------------------
# 五层「二结局」问询（2026-10-31 玩家新增，**强制**）
#
# **只要层数=五层（卡德霍之颅）且左区读到 3 个「命运所指」**，就先问玩家：
#
#   Q 本局是否打二结局？
#
#   不要 → 按常规逻辑规划：**终点 = 右区「命运所指」**（五层右区固定 1 个）；
#          左区另外两个**无徽章**「命运所指」按「得偿所愿 − 1」计价（见下）。
#   要   → ① 右区「命运所指」变**必走点**，而且**尽量排在最后几步**——
#            进入后零件箱里**所有加工品的剩余次数强制变成 1**（含之后获取的），
#            早进去会把后面的跳跃预算砍掉（scenario 用 `clamp_items_at` 表达，
#            求解器落在那格时把 items 全部 min(1)）；
#          ② **最后一步 = 左区带徽章的「命运所指」**（进入二结局关卡）。
#            它是**非作战节点**，所以「任意非作战」的全图移动件（老妈妈的融雪）能落；
#            结构性原理（任意节点）也能落。没有全图移动件 → 这一步走不到，工具如实 ❌。
#
# 三个「命运所指」的来历：拿了二结局相关藏品时，五层左区会多出这三个。
# 右上角**无徽章**的两个 = 进入后给 1 件随机收藏品（价值 = 得偿所愿 − 1）：
# 得偿所愿是「两件收藏品里自选 1 件」，命运所指没有选择权，所以少 1 分。
# 右上角**有徽章**的那个 = 二结局关卡入口（本层终点）。
#
# ⚠️ 二结局问询**优先于终点模式**：三层起「险路尽头」模式要求最后一步落在险路恶敌，
# 但五层出现这 3 个命运所指时，本层终点由二结局问询决定（见 references/28）。
# 状态只在本进程；CLI 在「五层 + 左区 3 个命运所指」时必须显式传 `--second-ending`，
# 没答由 `layer5_answer_errors()` 拦住（exit 2），不许先算一版。
# ---------------------------------------------------------------------------
SECOND_ENDING_CHOICES = ("要", "不要")
_SECOND_ENDING = None         # None = 还没问；True = 要；False = 不要

SECOND_FATE_NODE = "命运所指"
SECOND_FATE_MARKER_COUNT = 3  # 五层左区出现 3 个「命运所指」= 拿了二结局相关藏品
SECOND_FATE_VALUE_DELTA = 1.0  # 无徽章命运所指 = 本层「得偿所愿」− 1


def normalize_second_ending(v):
    """把玩家回答归一到 True/False/None（None = 未问）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _FOUR_TRUE:
        return True
    if s in _FOUR_FALSE:
        return False
    raise ValueError(f"未知二结局回答「{v}」；可选：要 / 不要")


def set_second_ending(v):
    """设定本局「是否打二结局」（None = 未问）。"""
    global _SECOND_ENDING
    _SECOND_ENDING = normalize_second_ending(v)


def second_ending():
    """本局是否打二结局（True/False；None = 还没问）。"""
    return _SECOND_ENDING


def second_fate_random_value(layer=5):
    """无徽章「命运所指」的收益 = 本层「得偿所愿」− 1（玩家 2026-10-31 口径）。

    五层得偿所愿 = 8 → **7**。用本层价表算，别在别处写死 7：
    得偿所愿的价格若按层调整，这里自动跟着走。
    """
    layer = int(layer)
    return float(LAYER_PRICES[layer].get("得偿所愿", 8)) - SECOND_FATE_VALUE_DELTA


def layer5_scope_errors(layer):
    """二结局参数是否用错层（可在**读图之前**调用）。"""
    if int(layer) != 5 and _SECOND_ENDING is not None:
        return ["--second-ending 只在五层（且左区出现 3 个「命运所指」）时有意义"]
    return []


def layer5_answer_errors(layer, has_marker=None):
    """五层二结局问询是否问全。返回错误列表（空 = 可以求解）。

    `has_marker` = 左区是否读到 3 个「命运所指」（None = 还没判，先不拦）。
    """
    errs = layer5_scope_errors(layer)
    if errs or int(layer) != 5:
        return errs
    if has_marker is None:
        return []
    if has_marker and _SECOND_ENDING is None:
        return ["五层左区出现 3 个「命运所指」→ 拿了二结局相关藏品："
                "必须先问玩家「本局是否打二结局？」（答「要」或「不要」，"
                "再传 --second-ending 要|不要）"]
    if (not has_marker) and _SECOND_ENDING is not None:
        return ["本层左区没有 3 个「命运所指」→ 不是二结局强化局（或截图漏检）："
                "--second-ending 不该传"]
    return []


def layer5_plan_text(layer):
    """给人看的五层二结局口径（判词/场景备注用；非五层返回空串）。"""
    if int(layer) != 5:
        return ""
    if _SECOND_ENDING is None:
        return "二结局=未问：五层左区有 3 个「命运所指」时必须先问玩家，不许先算"
    if _SECOND_ENDING is False:
        return ("二结局=不要 → 常规逻辑：终点 = 右区「命运所指」；"
                "左区两个无徽章命运所指按 得偿所愿−1 计价，带徽章的 (3,1) 不进")
    return ("二结局=要 → 右区「命运所指」= 必走点（进入后加工品次数强制→1，尽量排最后）；"
            "最后一步 = 左区带徽章的「命运所指」（需要全图移动件）")


# ---------------------------------------------------------------------------
# 规则状态的**快照 / 恢复 / 清空**（2026-11-02 P0）
#
# 上面七个开关（分队模式、终点模式、四结局×2、三结局×2、二结局）都是模块级全局，
# 一改全进程生效。单局 CLI 天生没问题，但：
#   · `solve_scan.py` 在同一进程里连续建多个 Solver（每建一次就重放一遍 sc）；
#   · `selftest_solver.py` 以前靠**手写 22 处** `set_*(None)` 复位；
#   · 以后任何「一个进程跑多份 scenario」的脚本（扫描器换模式、批量复盘），
# 漏复位 = 把上一份的答案带进下一份，而且**不报错，只是算错**。
#
# 这一层不改变任何调用签名（121 处模块级调用照旧），只把「拍现场 / 还原现场」
# 变成一条语句，让测试和批处理不必再手抄复位清单。
# `route_solver.Solver.__init__` 现在从 scenario 重放全部七项（见那里），
# 所以单份求解是自洽的；这里服务的是「同一进程里的相邻两份」。
# ---------------------------------------------------------------------------
_STATE_KEYS = ("mode", "end_mode", "four_ending", "four_ending_pre",
               "three_ending", "three_ending_pre", "second_ending")


def snapshot():
    """把当前七项规则状态拍成 dict（可直接 json 化，便于写进日志/回归用例）。

    值域：模式是内部短名，问询是 True/False/None（None = 还没问）。
    """
    return {"mode": _MODE, "end_mode": _END_MODE,
            "four_ending": _FOUR_ENDING, "four_ending_pre": _FOUR_ENDING_PRE,
            "three_ending": _THREE_ENDING, "three_ending_pre": _THREE_ENDING_PRE,
            "second_ending": _SECOND_ENDING}


def restore(snap):
    """按 snapshot() 的结果还原；缺键 = 还原成 None（未设定）。

    走各自的 setter，所以非法值仍会抛错（不会把脏状态塞回全局）。
    """
    snap = snap or {}
    set_mode(snap.get("mode"))
    set_end_mode(snap.get("end_mode"))
    set_four_ending(snap.get("four_ending"))
    set_four_ending_pre(snap.get("four_ending_pre"))
    set_three_ending(snap.get("three_ending"))
    set_three_ending_pre(snap.get("three_ending_pre"))
    set_second_ending(snap.get("second_ending"))


def reset_all():
    """七项全部回到「未设定」= 默认分队/终点模式 + 所有问询未答。"""
    restore(None)


# 险路尽头模式下，各层「最后一步可以落进」的节点类型（**元组：任一即可，第一个是主类型**）。
# 2026-10-30 玩家口径（含同日修正）：
#   **一/四层**：险路小径与险路尽头同待遇——不再排除（永不 block）、同分值（都 0）、
#              且是最后一步的候选点之一。
#   **二层**：默认只有险路尽头能当最后一步；小径**不是候选**（拿不到「开启四结局的藏品」）。
#              **但二层有两种例外**（2026-10-30 玩家新增，见上方四结局问询）：
#              四结局=不要，或 四结局=要且前置藏品已获取 → 小径与尽头同分、可作可选终点。
#   三/五/六层：险路恶敌。
LAYER_ENDING = {1: ("险路尽头", "险路小径"), 2: ("险路尽头",),
                4: ("险路尽头", "险路小径"),
                3: ("险路恶敌",), 5: ("险路恶敌",), 6: ("险路恶敌",)}

# 「一进去这层就结束」的终点类型（硬终点）。险路小径是**软终点**（可停可继续，见 route_solver），
# 所以不在这里——把它塞进来会让一层「必经小径」的中继路线全部提前结束。
HARD_ENDING = ("险路尽头", "险路恶敌")

# 险路尽头模式下终点节点的权重（2026-10-29 暂定 0：终点是**硬约束**不是收益，
# 不影响最优解的选取，只影响报分——玩家给出实际结算分就直接改这几个数）。
# 2026-10-30：险路小径与险路尽头同分（一/四层两者都 0）；
# 二层**只有尽头**计入（8 分 = 四结局道具），小径在二层仍是 0（见 ending_final_value）。
END_FINAL_VALUE = {"险路尽头": 0, "险路小径": 0, "险路恶敌": 0}


def ending_types(layer):
    """险路尽头模式下该层**可作终点**的全部类型（元组；第一个是主类型）。

    一/四层 = (`险路尽头`, `险路小径`)；
    二层 = (`险路尽头`,)，**例外**：四结局=不要 或 已获取前置藏品 时
           追加 `险路小径`（与尽头同分、可作可选终点）；
    三/五/六层 = (`险路恶敌`,)。
    """
    layer = int(layer)
    if layer == 2 and is_end_jintou() and layer2_trail_equals_jintou():
        return ("险路尽头", "险路小径")
    return tuple(LAYER_ENDING[layer])


def layer_ending(layer):
    """主终点类型（字符串；兼容旧场景/旧代码里「LAYER_ENDING[x] 是字符串」的用法）。"""
    return ending_types(layer)[0]


def ending_hard_types(layer):
    """该层「碰到即结束」的终点类型。

    一/四层：险路尽头（小径是软终点，可停可续）。
    二层：**当小径被列为可选终点时，它也是硬终点**——进入即结束，避免「小径 8 + 尽头 8」
          被重复计入（两个终点只能选一个）；未获取前置藏品时小径不是终点，不在此列。
    三/五/六层：险路恶敌。
    """
    layer = int(layer)
    types = ending_types(layer)
    out = [t for t in types if t in HARD_ENDING]
    if layer == 2 and "险路小径" in types:
        out.append("险路小径")
    return tuple(out)


def ending_final_value(layer, node_type=None):
    """险路尽头模式下，该层终点节点的权重。

    二层：
      · 险路尽头恒 8（四结局道具，进 1 次即得）；
      · 险路小径 = 8 **仅当**它被 `ending_types(2)` 收为可选终点
        （四结局=不要，或 四结局=要且已获取前置藏品）；否则 0（旧口径）。
    一/四层：尽头与小径都按 `END_FINAL_VALUE`（暂定 0）。
    """
    layer = int(layer)
    t = node_type or layer_ending(layer)
    if layer == 2:
        if t == "险路尽头":
            return 8
        if t == "险路小径" and layer2_trail_equals_jintou():
            return 8
        return 0
    return END_FINAL_VALUE.get(t, 0)


def ending_value_map(layer):
    """本层各终点类型的权重（给建场景/手工管线批量铺 values 用）。"""
    return {t: float(ending_final_value(layer, t)) for t in ending_types(layer)}


# ---------------------------------------------------------------------------
# 迷藏（橡果徽章）的收益：**并入所在节点**（2026-10-30 玩家新口径）
#
# 旧口径（2026-10-30 上午）：橡果**不改价**，只在「毛分相同」时优先经过
# （`ACORN_EPS` 微额加成）。玩家同日改口：**要把橡果的收益量化出来，与所在节点的
# 收益相加后再算路线**，因此「带橡果的林间空地」不再是 0 分、要能进路线。
#
# 量化过程（数据是玩家给的，四件等概率）：
#
#   | 自然物 | 估值 | 动态 |
#   |---|---|---|
#   | 光彩松露 | 32 源石锭 | 无 |
#   | 恋家果   | 32 源石锭 | 每次移动 −2（拿到就跌，越早卖越好） |
#   | 板藤     | 8 源石锭  | 每次获得零件 +4（打得越多越值） |
#   | 旅行绒   | 1 源石锭  | 每次移动 **+移动的曼哈顿距离**；**全图跳一次就 +10~17** |
#
# ① **裸期望** = (8 + 32 + 32 + 1) / 4 = **18.25 源石锭**。
# ② **动态修正**（拿到后到卖给行商，典型 m≈6–8 步、p≈3–5 个零件、总位移 D≈8–12 格）：
#      板藤 8+4p ≈ 20–28；恋家果 32−2m ≈ 16–20；光彩松露 32；
#      旅行绒 1+D：**结构性原理/喷气背包/触须这类全图跳一次就是 +10~17**
#      → 实际不低于 32（玩家 2026-10-30 口径），取 **32–40**
#    → 期望 **≈25–30（中枢 26.5）**，比裸值高约 45%。板藤/旅行绒要「后面还有得打/
#      还有得走」，恋家果要「早点卖」，所以**不额外加价**，取 25–30 这一档。
# ③ **折成求解器的「分」不是 1:1**：本模型对大额奖励是**压平定价**的——
#      树根 = 4 藏品（32 当量）+ 2 AP 只记 15（≈0.47 倍）；一层定点行商 +12 源石锭只记 4（0.33 倍）；
#      1:1 只出现在小额段（1 件藏品 8 当量 = 得偿所愿 8；右区多 1 件高级藏品 = +16）。
#    按**树根锚点**（32 当量 ↔ 15 分）线性折：25–30 当量 → **11.7–14.1**（中枢 12.4）。
#    这正是 12 的落点，不需要再「取中间值」——**12 = 把 SR 自然物按 4 件藏品同一把尺子量**。
# ④ **实现折损**：估值必须到行商才变现（五层 2–3 个必进、三/四层秘境行商必进，
#    风险小）；但它要占 1 个背包格 —— 与③的向上取整基本对冲，所以 12（不上调到 13/14）。
# ⑤ **不变量校验**（这条最要紧，改数前先过一遍）：
#      9  < 12 < 15 < 16/21
#      紧急作战(9) < 橡果(12) < 树根(15) < 右区作战(16)/右区紧急(21)
#    · 一件 SR 自然物**比一件随机藏品值钱** → 橡果 > 紧急作战 9；
#    · **4 件藏品 + 2 AP 仍是全场最高** → 橡果 < 树根 15；
#    · **右区多一件高级藏品** → 橡果 < 右区作战。
#    所以「带橡果的空地 = 12 分」会排在不期而遇(9)/得偿所愿(8)之上、
#    紧急作战(9)之上、树根(15)之下——这是玩家要的「加进路线规划」的正确位置。
#
# 用法（**只在这一处改数**）：
#   · `build_scenario.acorn_cell_values()` / `route.py` 把 `ACORN_VALUE` **加在**
#     该格原有收益上，写进 scenario 的 `cell_values`（逐格覆盖，不是改类型价）；
#   · 带橡果的**林间空地**从 `block` 里豁免（`block_except`），地基按 0 起算 → 12 分；
#   · 求解器按 `Solver.cell_value()` 读（pval / 走法 / fmt 同源）。
#   ⛔ 不要再退回「不改价 + 同分优先」：那会变成「愿意为它少拿 1 分」。
# ⑥ **性质（2026-11-02 补）**：12 是**模型估值**，不是实测固定收益——
#    上面 ①–④ 每一步都是假设（m 步数 / p 零件 / D 位移 / 折算系数 k=0.47）。
#    假设一动 12 就该动；**断点表**见 `references/27-三结局徽章.md`「性质：模型估值」一节，
#    最危险的一条是：k 换成行商锚点 0.33 时分值 ≈8.7，直接跌破「紧急作战 9」。
#    代码只负责给一个可复现的数，**合理性靠人复核**（`--check` 只钉排序锚点）。
# ---------------------------------------------------------------------------
ACORN_VALUE = 12.0

# 带橡果时「放行 / 照旧禁踩」的类型（玩家 2026-10-30 逐条口径，**唯一真源**）：
#   放行 = 该格从 block 里豁免，收益按「地基 + 12」计：
#     · 空地        —— 0 分地基 → 12；三层起的硬墙对它豁免
#     · 曲折密道    —— 0 → 12；**必须配对成功**（写 portals，进入即传送到另一端）
#     · 险路尽头    —— 0 → 12；全程追猎可当中继，险路尽头模式建议留给最后一步
#                       （险路小径本来就不 block，带橡果自动 12）
#   照旧禁踩（加 12 分也不改变，打 ⚠️ 如实告知）：
#     · 险路恶敌    —— 全程追猎仍 block；险路尽头模式下它本来就是终点
#     · 命运所指    —— 三/四层是「不可进入」的墙（玩家明确：带橡果也不进）
ACORN_PASS_TYPES = ("空地", "曲折密道", "险路尽头")
ACORN_STAY_BLOCKED = ("险路恶敌", "命运所指")


def acorn_value(layer=None):
    """一格橡果（迷藏）的收益（**加到所在节点收益之上**，不是替换）。

    `layer` 目前不影响取值：四件自然物等概率、动态项互抵，所以全层同价 12。
    以后若要按层微调（例：六层板藤拿不到零件、旅行绒没几步可走 → 10），
    只在这里按层返回，**别在 route/build_solver 里另写一份**。
    """
    return ACORN_VALUE


def acorn_base_value(type_value):
    """橡果格的**地基收益**：节点本身的分（墙标记 -99 视作 0）。

    空地/密道这类「-99」是「不可达」的编码，不是收益；带橡果时它重新变成
    「0 分地基 + 橡果 12 分 = 12 分」的可进格，而不是 -99+12=-87 的坑。
    """
    return 0.0 if float(type_value) < -90 else float(type_value)


# ---------------------------------------------------------------------------
# 分层定价
#
# 硬性排除（2026-10-24 玩家口径，先于收益计算）：
#   1) 三层起左区不走普通作战 → "作战": 0
#   2) 除一层外不走林间空地   → "空地": -99（一层 0，合法中继）
#   3) 不走曲折密道           → "曲折密道": -99（且不写 portals）
#   4) 除二层外不走险路尽头   → "险路尽头": -99；二层 8（四结局道具，走 1 次）
#   5) 险路小径               → 0（可进可出、等同空节点，**永不 block**）。
#      **险路尽头模式**下**一/四层**它与尽头同待遇：同分值 + 最后一步候选（2026-10-30）；
#      二层例外：小径 0 分、不当终点（拿不到四结局藏品）
#
# 2026-10-28 修正的三处漂移：
#   * 三/四/五层「空地」原为 0（允许踩）→ 现 -99，与 1) 2) 一致
#   * 一层「险路尽头」原为 0 → 现 -99（一层尽头是负收益）
#   * 一层「凶戾」原为 9（当它是紧急作战）→ 现 3.0 只作**兜底**；
#     入口建场景时会按 03-节点收益 一·附 覆盖：无理想源 = 3+6/k、有理想源 = 3.0
#     （2026-10-29 修：此前恒按 3.0，默认局少算 2–6 分/格）
#   * 补进缺的类型（曲折密道 / 险路恶敌 / 右区作战 / 命运所指），
#     以前缺键会让求解器 `vals.get(t, 0)` 静默按 0 分算
#     —— 三层实测路线真的从曲折密道走过去了（见 CHANGELOG 2026-10-28）
# ---------------------------------------------------------------------------

_COMMON = {
    "树根": 15, "理想源": 15,
    "起点": 0, "已访问": 0,
    "曲折密道": -99, "险路尽头": -99, "险路恶敌": -6, "险路小径": 0,
    "命运所指": 0, "流窜居民": 0,
    # 三结局前置藏品已获取后，每层一个「不期而遇」会变成带徽章的**泪之聚落**
    # （2026-10-30 玩家口径）：收益 0，默认不进，但**不 block**——可作 0 分过路中继。
    # 带徽章的失与得（同普通失与得）与「无效验尸」（同普通作战）**不改价**，不单列。
    "泪之聚落": 0.0,
    "羽瞰点": 0, "安全的角落": 0, "失与得": 0, "应急助力": 0,
    "紧急作战": 9,
}

# 右区（**正式名「熄日余径」**，三/四/五层缝之后那 3 列）——**节点构成固定、只有位置随机**，
# 所以价格按「这一格在这一层能拿到什么」定，不按左区同类定。
#
# 口径来源 `03-节点收益` + 玩家 2026-10-28 修正（**作战配额是固定且独立的**）：
#   右区作战 = **三层 1 紧急 + 1 普通；四层 1 紧急 + 2 普通；五层 2 紧急 + 1 普通**
#   → 未点亮的「右区凶戾」期望按这个配比算（不是拍一个 18）：
#     三层 (21+16)/2 = **18.5**；四层 (21+16+16)/3 ≈ **17.7**；五层 (21×2+16)/3 ≈ **19.3**
#   **右区紧急配额独立于左区**：左区已探明 2 个紧急，右区照样有 1 个（玩家 2026-10-28）。
#   满扫 5 格（三/四层）= 紧急 21 + 普通 16 + 不期 9 + 得偿 8 + 安全 3 = **57**
#   右区诡秘盲踩期望：**三/四/五层统一 8**
_RIGHT = {
    "右区作战": 16, "右区紧急作战": 21, "右区凶戾": 18.5, "右区诡秘": 8,
    "右区不期而遇": 9, "右区得偿所愿": 8, "右区安全的角落": 3,
}
# 唯一的分层差别：三/四层的「命运所指」是墙，五层是终点（可作最后一步）
_RIGHT_34 = {**_RIGHT, "命运所指": -99}
_RIGHT_5 = {**_RIGHT, "命运所指": 0}

# 右区作战的**固定配额**（玩家 2026-10-28）：用来推「未点亮的右区凶戾」到底是紧急还是普通。
# 与左区的「紧急作战总数」**完全独立**——左区配额已经满了，右区该有的紧急一个不少。
RIGHT_COMBAT_QUOTA = {
    3: {"紧急作战": 1, "作战": 1},
    4: {"紧急作战": 1, "作战": 2},
    5: {"紧急作战": 2, "作战": 1},
}
# 各层「右区凶戾」的平均期望（配额均衡时的取值，供无推断场合兜底）
RIGHT_HAZARD_EXPECT = {3: 18.5, 4: 17.7, 5: 19.3}

LAYER_PRICES = {
    1: {**_COMMON,
        "空地": 0, "作战": 3, "凶戾": 3.0, "诡秘": 2,
        "诡意行商": 4, "不期而遇": 2, "得偿所愿": 2,
        "险路尽头": -99},
    2: {**_COMMON,
        # 2026-10-28 玩家修正：**一/二层的林间空地可以当合法中继**（一层、二层地图稀疏，
        # 连通常常必须借空地）。三层起才是墙。一层、二层也不会出现曲折密道。
        "空地": 0, "作战": 3, "凶戾": 3.0, "诡秘": 2,
        "诡意行商": 0, "秘境行商": 11, "不期而遇": 2, "不期而遇线人": 4,
        "得偿所愿": 2, "安全的角落": 3, "应急助力": 3, "失与得": 3,
        "居民据点": 10, "险路尽头": 8},
    3: {**_COMMON, **_RIGHT_34,
        "右区凶戾": RIGHT_HAZARD_EXPECT[3],      # 1 紧急 + 1 普通 → 18.5
        "空地": -99, "作战": 0, "凶戾": 1.0, "诡秘": 5,
        "不期而遇": 9, "得偿所愿": 8, "不期而遇线人": 10,
        "狭路相逢": 10, "误入奇境": 10,
        "失与得": 3, "安全的角落": 3, "应急助力": 3,
        "秘境行商": 11, "诡意行商": 0, "居民据点": 10, "险路尽头": -99},
    4: {**_COMMON, **_RIGHT_34,
        "右区凶戾": RIGHT_HAZARD_EXPECT[4],      # 1 紧急 + 2 普通 → 17.7
        "空地": -99, "作战": 0, "凶戾": 1.0, "诡秘": 5,
        "不期而遇": 9, "得偿所愿": 8, "不期而遇线人": 10,
        "狭路相逢": 10, "误入奇境": 10,
        "失与得": 3, "安全的角落": 3, "应急助力": 3,
        "秘境行商": 11, "诡意行商": 0, "居民据点": 10, "险路尽头": -99},
    5: {**_COMMON, **_RIGHT_5,
        "右区凶戾": RIGHT_HAZARD_EXPECT[5],      # 2 紧急 + 1 普通 → 19.3
        "空地": -99, "作战": 0, "凶戾": 1.5, "诡秘": 5,
        "不期而遇": 9, "得偿所愿": 8,
        "狭路相逢": 10, "误入奇境": 10,
        "失与得": 3, "安全的角落": 3, "应急助力": 3,
        "秘境行商": 11, "诡意行商": 12, "居民据点": 10, "险路尽头": -99},
    # 六层：**两张图**（2026-10-30）：四结局「盲目者所见」5×4 单构型，无右区；
    # 三结局「源流交汇处」6×5，起点 (0,2)（本 dict 是两张图共用的兜底价）。
    # 四结局：基础 AP 追猎 4 / 险路尽头 5；树根 +2 → 6 / 7 步（文明开化）；
    # 普通分队模式树根不回复 AP → 全层只有 4 / 5 步。
    # 定点节点见 15-六层构型（(0,2)/(4,3) 紧急作战、 (3,3) 行商、(0,3) 不期、(1,3) 得偿）。
    # 加工品次数：**只有四结局那张**强制每件 1 次；三结局源流按默认耐久（2026-10-31 玩家裁定，
    # 见 `clamp_uses` / `parse_items_spec`）。
    6: {**_COMMON,
        "空地": -99, "作战": 3, "凶戾": 3.0, "诡秘": 2,
        "不期而遇": 2, "得偿所愿": 2,
        # 2026-10-29 玩家裁定：六层诡意行商 = 12（必去），与 03-节点收益「五、六层 12」对齐
        # （旧值 4 是漂移；定点 (3,3)，15-六层构型）
        "诡意行商": 12, "紧急作战": 5,
        "险路尽头": -99},
}

# 一层诡秘/凶戾的逐格推断用不到距离带（一层信息少），三/四/五层见 route.py 的 BANDS


# ── 右区命名 ────────────────────────────────────────────────────────────
# 右区节点的**名字**要能自己说明「我在右区」，否则求解器会按左区同类定价
# （三层右区普通作战会被当成 0 分、五层右区行商会被当成 12 分）。
# 2026-10-28 前只有五层做了半套改名（凶戾/诡秘），三/四层完全没改。
RIGHT_RENAME = {
    "作战": "右区作战",
    "紧急作战": "右区紧急作战",
    "凶戾": "右区凶戾", "未知的凶戾": "右区凶戾",
    "诡秘": "右区诡秘", "未知的诡秘": "右区诡秘",
    "不期而遇": "右区不期而遇",
    "得偿所愿": "右区得偿所愿",
    "安全的角落": "右区安全的角落",
}


def right_zone_type(t):
    """右区格子的节点类型名（其余类型原样返回，如命运所指/狭路相逢/诡意行商）。"""
    return RIGHT_RENAME.get(t, t)


# 入口/推断给节点起的「派生名」→ 比对用的基名（2026-10-29 系统性修复 #5）：
# build_scenario / route.py 会把节点改写成 `诡秘@2,3`（逐格期望）、`右区凶戾`（右区改名）、
# `普通作战`（配额满后的定性）——配套工具若只认原始名，就会假阴性
# （pool_candidates 说「没有诡秘」、expect_by_distance 空表、resident_infer 找不到凶戾）。
_ALIAS_BASE = {
    "未知的诡秘": "诡秘", "未知的凶戾": "凶戾",
    "普通作战": "作战", "普通凶戾": "凶戾",
}


def base_type(t):
    """把派生/改名过的节点类型还原成基名（工具链比对的**统一口径**）。

    - `诡秘@2,3` / `右区凶戾@9,1` → 去掉 `@x,y` 逐格期望后缀；
    - `右区*` → 去掉「右区」前缀（右区诡秘本来就是诡秘）；
    - `未知的诡秘 / 普通作战` 等别名 → 映射到基名。
    其余原样返回。**所有按类型名匹配的工具都该走它**，不要再自己写 startswith。
    """
    if not isinstance(t, str):
        return t
    if "@" in t:
        t = t.split("@", 1)[0]
    if t.startswith("右区"):
        t = t[len("右区"):]
    return _ALIAS_BASE.get(t, t)


def expand_prefixes(names):
    """把 only/exclude 的前缀补上「右区」变体。

    右区格子会被改名成「右区凶戾 / 右区诡秘 / 右区作战 …」，而道具的 only/exclude
    写的是**基础名**（作战 / 凶戾 / 诡秘），`startswith` 就匹配不上：
    - only 写「凶戾」→ 落不了「右区凶戾」（反向漏判，少走）
    - exclude 写「作战」→ 会把「右区作战」当成合法落点（**正向误判**，最危险）

    **只有这里能改这个规则**：`route_solver` 和 `route.py` 都必须调它，
    否则「入口说进得去、求解器说进不去」（2026-10-28 踩过：融雪被判能进右区，实际 0 分）。
    """
    out = list(names or [])
    out += ["右区" + o for o in out if not o.startswith("右区")]
    return out


def land_ok(it, t):
    """道具 `it` 能不能落在类型 `t` 上（only / exclude 都按展开后的前缀判）。"""
    only = expand_prefixes(it.get("only"))
    exc = expand_prefixes(it.get("exclude"))
    if only and not any(t.startswith(o) for o in only):
        return False
    if exc and any(t.startswith(x) for x in exc):
        return False
    return True


# 六层有两张完全不同的图（2026-10-30）：四结局「盲目者所见」5×4 / 三结局「源流交汇处」6×5。
# **只有四结局那张**「所有加工品一律剩 1 次」（15-六层构型，玩家口径「以后所有对局都如此」）；
# 三结局源流**不夹次数**、按各件默认耐久（2026-10-31 玩家裁定：源流没有「用完即毁」规则）。
# 判定口径与工具链其它地方一致：`cfg` 里含「源流」= 三结局六层。
# ⚠️ 三条边界（2026-10-28 复查 / 2026-10-31 补）：
#   · 只夹**正的次数**：`剩0次` = 这件已经用完了，抬成 1 次会凭空多出一件；
#   · `layer=None` = 「不按层夹」（`parse_items_spec` 的默认）——以前 `int(None)` 直接 TypeError；
#   · `cfg=None`（没传构型）= 按四结局六层夹，**老行为**：旧调用不会因少传参数而静默放宽预算。
def is_source_six(layer, cfg=None):
    """本层是不是三结局六层「源流交汇处」（6×5）。"""
    return (layer is not None and int(layer) == 6
            and "源流" in str(cfg or ""))


def clamp_uses(layer, uses, cfg=None):
    uses = int(uses)
    if layer is None or int(layer) != 6 or is_source_six(layer, cfg):
        return uses
    return 1 if uses > 0 else uses


class UnknownItem(Exception):
    """名字不在 ITEM_DEFS 里，且调用方也没给 cov。"""


def _clean(name):
    """去掉玩家可能带的引号/空白：「"简易遥控器"」→ 简易遥控器。"""
    return str(name).strip().strip('"“”\' ').strip()


# ---------------------------------------------------------------------------
# 库存条目校验（2026-11-02 P0）——**所有入口共用一把尺子**
#
# 为什么必须有：`parse_items_spec`（命令行）早就防住了负数/畸形数量，
# 但 `normalize_item`（手写 scenario、其它脚本直接调）以前只有一句
# `int(out.get("uses", 1))`，于是三类**不报错的错**能一路进求解器：
#
# | 输入 | 旧行为 | 后果 |
# |---|---|---|
# | `"uses": -3` | 直接吃下 | 负预算，路线整体偏保守 |
# | `"only": "作战"` | `list("作战")` → `['作','战']` | **逐字前缀匹配，静默错配**（融雪能落作战、鸣笛飞不到凶戾） |
# | `"bonus": -3` | 直接吃下 | 少给 3 点 AP，判词还报 ✅ |
#
# 上限只设**宽松哨兵**（99），抓「12 写成 1200」这类笔误，**不按耐久卡**：
# 合法次数本就能到 6（2 件 × 3），六层夹到 1，`solve_scan` 的 k 也可能更大。
# ---------------------------------------------------------------------------
MAX_USES = 99
MAX_BONUS = 99


class BadItem(Exception):
    """库存条目字段非法（负数次数 / 字符串 only / 负 bonus / 超范围）。"""


def _as_int(v, what, lo=0, hi=None):
    """把字段读成整数：只收 int / 纯数字字符串，拒 bool、float、脏字符串。"""
    if isinstance(v, bool):                    # bool 是 int 的子类，必须先拦
        raise BadItem("%s 不能是布尔值（%r）" % (what, v))
    if isinstance(v, int):
        n = v
    elif isinstance(v, float):
        if not v.is_integer():
            raise BadItem("%s 必须是整数（%r）" % (what, v))
        n = int(v)
    elif isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-\d+", s):
            raise BadItem("%s 不能为负（%r）：0 = 已用完" % (what, v))
        if not re.fullmatch(r"\d+", s):
            raise BadItem("%s 必须是非负整数（%r）" % (what, v))
        n = int(s)
    else:
        raise BadItem("%s 必须是整数（%r）" % (what, v))
    if n < lo:
        raise BadItem("%s 不能小于 %d（%r）" % (what, lo, v))
    if hi is not None and n > hi:
        raise BadItem("%s 超过合理上限 %d（%r）：是不是多写了一位？" % (what, hi, v))
    return n


def _as_prefix_list(v, what):
    """only / exclude 必须是非空字符串的列表。

    **字符串是这里最危险的输入**：`list("作战")` 会变成 `['作','战']`，
    随后 `startswith` 按单字匹配——不抛错、只是整条路线按错的道具语义算。
    """
    if v is None:
        return None
    if isinstance(v, str):
        raise BadItem('%s 要写成列表（如 ["作战"]），不能是字符串「%s」：'
                      "字符串会被逐字拆成前缀，静默错配" % (what, v))
    if not isinstance(v, (list, tuple)):
        raise BadItem("%s 必须是列表（%r）" % (what, v))
    out = []
    for x in v:
        if not isinstance(x, str) or not x.strip():
            raise BadItem("%s 里只能放非空字符串（%r）" % (what, x))
        out.append(x.strip())
    return out


def validate_item(ent):
    """校验一条库存条目（**规范化之前**调用）；非法抛 `BadItem`，由入口统一判 ❌。

    只查**写了的**字段：缺省值仍由 `normalize_item` 按 ITEM_DEFS 补。
    名字不要求在 ITEM_DEFS 里——`solve_scan` 会造「标准引擎(扫描3)」这类派生名，
    只要它自带 cov 就合法。
    """
    if not isinstance(ent, dict):
        raise BadItem("库存条目必须是对象（dict），拿到 %r" % (ent,))
    name = ent.get("name")
    if not isinstance(name, str) or not _clean(name):
        raise BadItem("库存条目的 name 必须是非空字符串（%r）" % (name,))
    nm = _clean(name)
    if "uses" in ent:
        _as_int(ent["uses"], "「%s」的 uses（剩余次数）" % nm, 0, MAX_USES)
    if "bonus" in ent:
        _as_int(ent["bonus"], "「%s」的 bonus（额外行动力）" % nm, 0, MAX_BONUS)
    for f, label in (("only", "only（只能落的类型）"),
                     ("exclude", "exclude（不能落的类型）")):
        if f in ent:
            _as_prefix_list(ent[f], "「%s」的 %s" % (nm, label))
    for f, label in (("free", "free（免行动力）"), ("random", "random（随机落点）")):
        if f in ent and not isinstance(ent[f], (bool, int)):
            raise BadItem("「%s」的 %s 必须是 true/false（%r）" % (nm, label, ent[f]))
    if "cov" in ent and ent["cov"] not in COVS:
        raise BadItem("「%s」的 cov「%s」不在 %s 里" % (nm, ent["cov"], "/".join(COVS)))
    return ent


def item_def(name):
    return ITEM_DEFS.get(_clean(name))


def available_items():
    return sorted(ITEM_DEFS)


# 耐久 = 满次数（`01-加工品` 原始条目表）。玩家口语「1 个标准引擎」= **1 份**，
# 次数按耐久给满（3 次）。2026-10-28 玩家纠正：以前把「1 个」读成「1 次」，
# 四件全被压成 1 次，路线系统性偏保守（平白少掉 5 次位移）。
DURA = {n: int(d.get("dura", 1)) for n, d in ITEM_DEFS.items()}


def default_uses(name):
    """一件**全新**加工品的可用次数（耐久）。"""
    return DURA.get(_clean(name), 1)


def make_item(name, uses=None):
    """按名字造一件 item（给 route.py / build_scenario.py 的 --items 用）。

    产出的字段与 scenario 里的 items[] 完全一致，**求解器可直接吃**：
        {"name", "cov", "uses"} + 可选 only / exclude / free / bonus / random

    `uses` 省略时 = 该件的耐久（满次数），**不是 1**。
    """
    d = item_def(name)
    if d is None:
        raise UnknownItem(name)
    if uses is None:
        uses = default_uses(name)
    ent = {"name": _clean(name), "cov": d["cov"], "uses": int(uses)}
    for f in ("only", "exclude"):
        if d.get(f):
            ent[f] = list(d[f])
    if d.get("free"):
        ent["free"] = True
    if d.get("bonus"):
        ent["bonus"] = int(d["bonus"])
    if d.get("random"):
        ent["random"] = True
    return ent


def parse_items_spec(spec, layer=None, cfg=None):
    """解析 `--items` 规格（用 `;` 分隔），返回 (items, notes)。

    **默认口径：数字 = 件数**（玩家 2026-10-28 明确）：
    「如果没在后面写剩余几次，就按满次数算，除非是**四结局**六层强制剩余 1 次」。

    所以 `标准引擎:1` 与 `标准引擎:1个` 都 = **1 件 = 3 次**，
    `标准引擎:2` = 2 件 = 6 次。要表达「只剩 N 次」必须**写出「次」**。

    | 写法 | 含义 |
    |---|---|
    | `标准引擎` | 1 件 → 耐久 3 次 |
    | `标准引擎:1` / `:1个` / `:1件` / `:1份` | 1 件 → 3 次 |
    | `标准引擎:2` | 2 件 → 6 次 |
    | `标准引擎:剩2次` / `:2次` | **总共 2 次**（不管几件） |
    | `标准引擎:2*1` | 2 件、每件只剩 1 次 → 2 次 |
    | 四结局六层（`layer=6` 且 cfg 不含「源流」） | 每件强制 1 次：`标准引擎:2` → 2 次；写「次」也强制（`剩3次` → 1 次） |
    | 四结局六层且 `剩0次` | **仍记 0**（这件已经用完了，不凭空抬成 1 次）|
    | **三结局六层（`cfg` 含「源流」）** | **不夹次数**，按默认耐久（2026-10-31 玩家裁定） |

    `layer` 只影响六层的 clamp（`cfg` 用来区分六层两张图），其余层原样；
    `layer=None` = 不按层夹（不传也不会炸）；`cfg=None` = 按四结局六层夹（老行为）。
    notes 是给人看的逐条解析，入口请打出来（玩家要能一眼看出「我报的 1 件=3 次」）。
    """
    items, notes = [], []
    for part in filter(None, (spec or "").split(";")):
        part = part.strip()
        if not part:
            continue
        name, num = (part.rsplit(":", 1) if ":" in part else (part, ""))
        name = _clean(name)
        num = str(num).strip()
        dura = default_uses(name)

        def _clamp(n):
            return clamp_uses(layer, n, cfg)

        def _num(s, raw=None):
            """数量写错时给中文说明，而不是 float() 的英文 traceback。

            2026-10-31 QA：旧实现 `int(float(s))` 太宽松，把两类垃圾输入静默吞掉——
              · `标准引擎:-1` → **-3 次**（负数库存进 scenario，后续预算/小结全歪）；
              · `标准引擎:1次2` → 去掉「次」得到 "12" → **12 次**（玩家笔误被当成合法值）。
            现在只收「非负数字」；负号/夹带字符一律报错，由入口统一判 ❌。
            """
            text = str(s).strip()
            shown = raw if raw is not None else text
            if text.startswith("-"):
                raise ValueError(f"「{name}」的数量「{shown}」不能为负（0 = 已用完）")
            if not re.fullmatch(r"\d+", text or ""):
                raise ValueError(
                    f"「{name}」的数量「{shown}」不是数字（只能写非负整数：2 / 剩1次 / 2*1）")
            return int(text)

        if num == "":
            uses = _clamp(dura)
            notes.append(f"{name} → 1 件 → {uses} 次（耐久 {dura}）")
        elif "*" in num:                       # 2*1 = 2 件、每件剩 1 次
            a, b = num.split("*", 1)
            copies, each = _num(a), _num(b, num)
            uses = copies * _clamp(each)
            notes.append(f"{name} → {copies} 件 × 每件 {each} 次 → {uses} 次")
        elif "次" in num:                      # 剩2次 / 2次 = 总次数
            # 先校验「次」的写法：`1次2` / `2次3` 这类笔误不能靠 replace 变成 12/23
            core = num[1:] if num.startswith("剩") else num
            if not core.endswith("次") or core.count("次") != 1:
                raise ValueError(
                    f"「{name}」的数量「{num}」看不懂（要写 剩2次 / 2次 / 2*1）")
            uses = _num(core[:-1], num)
            clamped = _clamp(uses)
            if clamped != uses:
                # 2026-10-28 审计：这是唯一没过 clamp_uses 的分支——
                # 四结局六层每件强制 1 次，`标准引擎:剩3次` 却仍是 3 次
                notes.append(f"{name} → 共 {uses} 次（按次数）→ 四结局六层强制 1 次")
                uses = clamped
            else:
                notes.append(f"{name} → 共 {uses} 次（按次数）")
        elif num.endswith(("个", "份", "件", "把", "张", "支", "枚")):
            copies = _num(num[:-1] or 1)
            uses = copies * _clamp(dura)
            notes.append(f"{name} → {copies} 件 → {uses} 次（每件 {_clamp(dura)} 次）")
        else:                                  # 裸数字 = 件数（玩家默认口径）
            copies = _num(num)
            uses = copies * _clamp(dura)
            notes.append(f"{name} → {copies} 件 → {uses} 次（每件 {_clamp(dura)} 次）")
        items.append(make_item(name, uses))
    if items and is_source_six(layer, cfg):
        # 入口打出来，玩家一眼能看出「本层没被夹」——别让默认值和四结局六层混淆
        notes.append("三结局六层「源流交汇处」：不夹次数，按各件默认耐久"
                     "（只有四结局「盲目者所见」才强制每件 1 次）")
    return items, notes


def normalize_item(ent):
    """把 scenario 里省略字段的 item 补全成求解器要的形状。

    手写 json 常常只写 {"name": "一次性喷气背包", "uses": 1}：
    缺 cov 会让求解器 KeyError，缺 free 会让它白扣行动力，
    缺 bonus 会让遥控器少 3 点 AP。这里统一补齐，**不改显式写明的值**。

    2026-11-02 P0：补齐之前先过 `validate_item()`——命令行与手写 JSON
    必须是**同一把尺子**（负数次数、字符串 only/exclude、负 bonus 全部抛 BadItem）。
    `uses` 只收非负整数（`0` = 已用完，合法）；`bonus` 同理。
    """
    if not isinstance(ent, dict) or "name" not in ent:
        raise UnknownItem(str(ent))
    validate_item(ent)
    d = item_def(ent["name"]) or {}
    out = dict(ent)
    out["name"] = _clean(ent["name"])
    if not out.get("cov"):
        if not d.get("cov"):
            raise UnknownItem(out["name"])
        out["cov"] = d["cov"]
    if out["cov"] not in COVS:
        raise UnknownItem("%s 的 cov「%s」不在 %s 里" % (out["name"], out["cov"], COVS))
    out["uses"] = _as_int(out.get("uses", 1),
                          "「%s」的 uses（剩余次数）" % out["name"], 0, MAX_USES)
    for f in ("only", "exclude"):
        if out.get(f) is None and d.get(f):
            out[f] = list(d[f])
    # 形状固定：调用方会直接 `it["only"]` / `it["free"]`，缺键就是 KeyError
    out.setdefault("only", None)
    out["exclude"] = list(out.get("exclude") or [])
    out["free"] = bool(out["free"]) if "free" in out else bool(d.get("free", False))
    out["bonus"] = (_as_int(out["bonus"], "「%s」的 bonus（额外行动力）" % out["name"],
                            0, MAX_BONUS)
                    if "bonus" in out else int(d.get("bonus", 0)))
    if "random" not in out and d.get("random"):
        out["random"] = True
    return out


def layer_values(layer):
    """该层的基础定价（求解器会再 merge 一层 DEFAULT_VALUES 兜底）。

    普通分队模式：树根/理想源按「紧急作战」计价（`root_value`），
    覆盖掉 `_COMMON` 里的 15——调用方（route.py / build_scenario.py）拿这份去铺 values。
    险路尽头模式：本层**所有**终点类型（尽头/小径/恶敌）按 `ending_final_value` 重新定价
    （不再 -99/-6；调用方也不要再把它 block 掉）。
    二层：全程追猎下「险路尽头」按 `layer2_jintou_value()`（四结局=不要 → 0）；
    险路尽头下再由 `ending_value_map` 覆盖（尽头 8；小径见 `ending_final_value`）。
    三层（无右区）：三结局=要且前置收藏品未获取 → 「先行一步」= 15 分
    （`three_ending_need_xianxing()`；必走点由 build_scenario.derive_must 加）。
    """
    layer = int(layer)
    vals = dict(LAYER_PRICES[layer])
    if is_normal_squad():
        rv = root_value(layer)
        for t in ("树根", "理想源"):
            vals[t] = rv
    if layer == 3 and three_ending_need_xianxing():
        vals[THREE_XIANXING_NODE] = THREE_XIANXING_VALUE
    if layer == 2:
        vals["险路尽头"] = layer2_jintou_value()
    if is_end_jintou():
        vals.update(ending_value_map(layer))
    return vals


# ---------------------------------------------------------------------------
# 规则不变式（`python tools/rules.py --check`，2026-11-02 P1）
#
# 注释拦不住漂移。这文件里每段「口径来源」都是人在 10 月一条条补的，
# 补完就再没有东西验它——于是出现「FREE_ITEMS 定义后零引用」「缺键被
# `vals.get(t,0)` 静默按 0 算」这类事故。这里把**能机械验的**关系钉死，
# 并按 分队 2 × 终点 2 × 四结局 3 × 前置 3 共 36 组开关组合遍历（跑完还原现场）。
#
# 只验**结构关系**，不验数值偏好：改价格不会让它变红（那是 regress 的定价指纹管），
# 改出矛盾才会。
# ---------------------------------------------------------------------------
def _check_free_items():
    bad = []
    derived = tuple(n for n, d in ITEM_DEFS.items() if d.get("free"))
    if tuple(FREE_ITEMS) != derived:
        bad.append("FREE_ITEMS 与 ITEM_DEFS 的 free 标记不一致：%s ≠ %s"
                   % (list(FREE_ITEMS), list(derived)))
    return bad


def _check_item_roundtrip():
    bad = []
    for n in available_items():
        try:
            out = normalize_item(make_item(n))
        except Exception as exc:                       # noqa: BLE001 - 报给人看
            bad.append("「%s」make_item→normalize_item 失败：%s" % (n, exc))
            continue
        d = ITEM_DEFS[n]
        if out["cov"] != d["cov"]:
            bad.append("「%s」cov 往返变了：%s → %s" % (n, d["cov"], out["cov"]))
        if out["uses"] != default_uses(n):
            bad.append("「%s」uses 往返变了：%s → %s" % (n, default_uses(n), out["uses"]))
        if not isinstance(out["exclude"], list):
            bad.append("「%s」exclude 不是 list（%r）" % (n, out["exclude"]))
        if out["free"] != bool(d.get("free")):
            bad.append("「%s」free 往返变了：%r" % (n, out["free"]))
        if out["bonus"] != int(d.get("bonus", 0)):
            bad.append("「%s」bonus 往返变了：%r" % (n, out["bonus"]))
    return bad


def _combat_like(t):
    """这个类型名是否属于「作战家族」（鸣笛能飞、融雪/小八界落不了的那族）。"""
    b = base_type(t)
    return ("作战" in b) or ("凶戾" in b) or b in ("树根", "理想源")


def _check_combat_prefixes():
    bad = []
    pre = expand_prefixes(COMBAT_PREFIXES)
    names = set()
    for tab in LAYER_PRICES.values():
        names.update(tab)
    names.update(RIGHT_RENAME.values())
    names.update(ACORN_PASS_TYPES)
    names.update(ACORN_STAY_BLOCKED)
    for t in sorted(names):
        if _combat_like(t) and not any(t.startswith(p) for p in pre):
            bad.append("作战家族前缀漏了「%s」（鸣笛 only / 融雪 exclude 会静默错配）" % t)
    return bad


def _check_layer_tables():
    bad = []
    for layer in sorted(LAYER_PRICES):
        tab = LAYER_PRICES[layer]
        for t in _COMMON:
            if t not in tab:
                bad.append("层 %d 价表缺「%s」（会被 values.get(t,0) 静默按 0 算）" % (layer, t))
        # 每层都该显式定价的基础类型。右区 / 线人 / 居民据点 / 秘境行商 / 狭路相逢
        # 不是每层都有，不进这张必备表（它们在别的层缺失是设计，不是漏）。
        for t in ("空地", "作战", "凶戾", "诡秘", "不期而遇", "得偿所愿", "诡意行商"):
            if t not in tab:
                bad.append("层 %d 价表缺基础类型「%s」（会被静默按 0 算）" % (layer, t))
        if layer in (3, 4, 5):
            for t in _RIGHT:
                if t not in tab:
                    bad.append("层 %d 价表缺右区类型「%s」" % (layer, t))
        for t in ending_types(layer):
            if t not in tab:
                bad.append("层 %d 价表缺终点类型「%s」" % (layer, t))
        vals = layer_values(layer)
        for t, v in vals.items():
            if not isinstance(v, (int, float)):
                bad.append("层 %d 的 layer_values[%s]=%r 不是数字" % (layer, t, v))
        if is_end_jintou():
            for t in ending_types(layer):
                v = vals.get(t)
                if isinstance(v, (int, float)) and v < 0:
                    bad.append("险路尽头模式下 层 %d 的终点「%s」被定价为负（%r）："
                               "终点是硬约束，负分会让求解器宁可不收尾" % (layer, t, v))
    return bad


def _check_ending_consistency():
    bad = []
    for layer in sorted(LAYER_ENDING):
        types = ending_types(layer)
        hard = ending_hard_types(layer)
        for t in hard:
            if t not in types:
                bad.append("层 %d 硬终点「%s」不在 ending_types 里" % (layer, t))
        extra = set(hard) - set(HARD_ENDING)
        if extra and not (layer == 2 and extra == {"险路小径"}):
            bad.append("层 %d 出现非 HARD_ENDING 的硬终点 %s" % (layer, sorted(extra)))
        # 一层/四层的小径是**软终点**：进了硬终点表会把「必经小径」的中继提前截断
        if layer in (1, 4) and "险路小径" in hard:
            bad.append("层 %d 的「险路小径」被当成硬终点（它必须可停可续）" % layer)
    return bad


def _check_anchors():
    bad = []
    t3 = LAYER_PRICES[3]
    chain = [("紧急作战", float(t3.get("紧急作战", 9))),
             ("橡果", float(ACORN_VALUE)),
             ("树根", float(t3.get("树根", 15))),
             ("右区作战", float(t3.get("右区作战", 16)))]
    for (n1, v1), (n2, v2) in zip(chain, chain[1:]):
        if not v1 < v2:
            bad.append("价格锚点不单调：%s=%g 应小于 %s=%g" % (n1, v1, n2, v2))
    dex = float(LAYER_PRICES[5].get("得偿所愿", 8))
    if not dex < ACORN_VALUE:
        bad.append("价格锚点：得偿所愿=%g 应小于橡果=%g" % (dex, ACORN_VALUE))
    return bad


def _check_derived_rules():
    bad = []
    if SECOND_FATE_VALUE_DELTA < 0:
        bad.append("SECOND_FATE_VALUE_DELTA=%r 不能为负（命运所指收益要低于得偿所愿，但不该更高）"
                   % SECOND_FATE_VALUE_DELTA)
    want = float(LAYER_PRICES[5]["得偿所愿"]) - SECOND_FATE_VALUE_DELTA
    got = second_fate_random_value(5)
    if abs(got - want) > 1e-9:
        bad.append("second_fate_random_value(5)=%g 与价表脱钩（应为 %g）" % (got, want))
    if not 0 < got < float(LAYER_PRICES[5]["得偿所愿"]):
        bad.append("无徽章命运所指=%g 应落在 (0, 本层得偿所愿 %g) 之间"
                   % (got, LAYER_PRICES[5]["得偿所愿"]))
    for layer in sorted(LAYER_PRICES):
        if "险路小径" not in LAYER_PRICES[layer]:
            bad.append("层 %d 价表缺「险路小径」（它可进可出，缺键会被按 0 算）" % layer)
        for t in grant_ok_types():
            if t not in GRANT_AP:
                bad.append("grant_ok_types() 里的「%s」不在 GRANT_AP" % t)
    return bad


_CHECKS = (
    ("FREE_ITEMS 与 ITEM_DEFS 同源", _check_free_items),
    ("道具定义往返", _check_item_roundtrip),
    ("作战家族前缀覆盖", _check_combat_prefixes),
    ("分层价表覆盖", _check_layer_tables),
    ("终点类型一致性", _check_ending_consistency),
    ("价格锚点单调", _check_anchors),
    ("派生规则不脱钩", _check_derived_rules),
)

# 36 = 2 分队 × 2 终点 × 3 四结局 × 3 前置
_CHECK_COMBOS = len(MODES) * len(END_MODES) * 3 * 3


def check_invariants(verbose=False):
    """跑全部不变式，返回问题列表（空 = 通过）。跑完**一定**还原原来的开关状态。"""
    problems = []
    snap = snapshot()
    try:
        for mode_name in MODES:
            for end_name in END_MODES:
                for four in (None, True, False):
                    for pre in (None, True, False):
                        set_mode(mode_name)
                        set_end_mode(end_name)
                        set_four_ending(four)
                        set_four_ending_pre(pre)
                        tag = "[%s+%s 四结局=%s 前置=%s]" % (
                            mode_label(mode_name), end_name,
                            {None: "未问", True: "要", False: "不要"}[four],
                            {None: "未问", True: "已获取", False: "未获取"}[pre])
                        for title, fn in _CHECKS:
                            if verbose:
                                print("  · %s %s" % (tag, title))
                            for p in fn():
                                problems.append("%s %s：%s" % (tag, title, p))
    finally:
        restore(snap)
    return problems


def describe_items():
    """给人看的对照表（tools/rules.py 与 references/01 的同一份内容）。"""
    rows = []
    for n in sorted(ITEM_DEFS):
        d = ITEM_DEFS[n]
        flags = []
        if d.get("free"):
            flags.append("免AP")
        if d.get("bonus"):
            flags.append("+%dAP" % d["bonus"])
        if d.get("only"):
            flags.append("仅" + "/".join(d["only"]))
        if d.get("exclude"):
            flags.append("排除" + "/".join(d["exclude"]))
        if d.get("random"):
            flags.append("随机")
        rows.append("%-8s %-7s 价%-3s %s" % (n, d["cov"], d.get("price", "-"),
                                             " ".join(flags)))
    return "\n".join(rows)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--check" in sys.argv[1:]:
        print("== 规则不变式（%d 组开关组合）==" % _CHECK_COMBOS)
        bad = check_invariants(verbose="--verbose" in sys.argv[1:])
        if bad:
            print("❌ 未通过：%d 条" % len(bad))
            for b in bad:
                print("   - " + b)
            sys.exit(1)
        print("✅ 全部通过")
        sys.exit(0)
    print(describe_items())
