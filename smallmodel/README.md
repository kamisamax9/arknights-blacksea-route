# smallmodel/ — 黑流树海 × 本地小模型适配档（2026-10-29）

给 llama.cpp（llama-server `--jinja`）这类本地部署的小模型用的"执行卡"，
配合 `route.py --explain` 使用。**设计原则：接口越死，越笨的模型越能用。**

实测背景（gemma-4-12B，2026-10-29）：
- ✅ 结构化 tool call、多模态读图、先问模式开关——这些交给它；
- ❌ 规则引用（会把「全程追猎」的尽头规则说反）、领域知识（构型/定点/期望值）、
  长输出耐力——这些**全部收走**，只允许它转述工具输出。

## 文件

| 文件 | 何时加载 |
|---|---|
| `card_open.md` | 新对局 / 进新层（含模式开关提问协议） |
| `card_roll.md` | 层内滚动：每步 / 每次探明 / 每次道具变动 |
| `card_end.md` | AP 见底、离层前 |

## 接线（部署方要做的事）

1. llama-server 必须带 `--jinja`（tool calling 的前提）；小模型建议再加
   `--reasoning off`（实测 reasoning 会吃光 token 预算且多为英文）。
2. 把 skill 工具包成 OpenAI function 挂给模型：

   | 工具名 | 绑定 | 说明 |
   |---|---|---|
   | `ask_user_question` | 透传给玩家 | 模式开关、理想源坐标、库存都从这里来 |
   | `arknights_grab` | `tools/grab.py --scene`（需 adb+MuMu） | 取节点表；层数自动认 |
   | `bs_route_plan` | `tools/observe.py … --explain`（或 `route.py --explain`） | **必须带 --explain**；observe/route 输出同一份文案，真源只有 route.py 一处 |

   > 2026-10-29 起，普通 DSH 会话（完整 SKILL.md）与执行器走**同一份输出合同**：
   > 判词 + 转述文案逐字转达。SKILL.md 〇·六 输出门禁 = 本卡铁律的主路径版。

3. 执行循环（模型只出 JSON，你负责执行）：

   ```
   msgs = [system=当前相位的卡, user=玩家输入]
   loop ≤ 4 次:
       resp = POST /v1/chat/completions (tools=[上面三个])
       if 无 tool_calls: 把 resp.content 发给玩家; break
       对每个 tool_call: 执行绑定命令 → 把 stdout 原文作为 role=tool 消息回喂
   ```

4. 相位切换：进新层 → card_open；层内每步 → card_roll；AP≤2 或玩家说离层 →
   card_end。换卡 = 换 system 消息重开会话（小模型别做长会话）。

## 延迟

12B Q4 + MTP 投机解码实测 4–5 tok/s（12 线程 CPU）。转述文案约 200 token，
单轮 ≈ 1 分钟。滚动对局里可接受；更快的做法是只在「判词 ❌」「玩家点名」时唤醒模型。

## 回归

`evals/triggers.json`（5 例触发）与 `evals/evals.json` 的协议子集（4 例）
由跑分器对真实模型执行：会话根目录 `llama_test/run_model_evals.py`。
其余 4 个 eval（L1-fixed-merchant / infer-cross-type / L2-merchant-priority /
jintou-end-layer2）依赖真实截图与求解器，属**工具侧回归**，由
`evals/run_evals.py` 与 `tools/regress.py` 覆盖，不对模型跑。
