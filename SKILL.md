---
name: videochannel-commerce-agent
display_name: "视频号带货助手"
display_name_en: WeChat Channels Commerce Agent
description: 面向微信视频号带货的确定性生产流水线。当用户要求在视频号带货、做视频号商品分享视频、搭建视频号自动化智能体、判断视频号能不能挂车、检查视频号 AI 标注合规、或提到"视频号带货""微信小店""短视频挂车""AI 标注""数字人带货""带货权限"时使用。4 个 Agent + 6 个确定性闸门，含视频号专属 AI 标注合规与带货门槛推算。
description_zh: 4 个 Agent 只做理解与创意，6 个脚本闸门做全部判定。视频号专属：AI 标注三要素校验、数字人冒充真人检测、带货权限门槛推算、未上架商品状态处理。
category: operations
author: WorkBuddy
agent_created: true
version: "1.0.2"
updated: "2026-11-03"
license: MIT
---
# 视频号带货智能体（v1.0）

> 架构继承 `ecommerce-agent-orchestrator` v2.0，叠加**视频号专属合规闸门**。
> 全部脚本、词库与规则快照随本技能包一起分发（`scripts/`、`references/`、`templates/`），不依赖任何本机绝对路径。

---

## 何时使用

- 老板要在视频号带货，需要选题/文案/分镜/字幕一套产出
- 要判断「现在能不能挂商品链接」「卡在哪一条门槛」
- 视频含 AI 生成内容（数字人/AI 口播/AI 画面），需要判断怎么标注才不被限流
- 货源线下未上架，需要判断内容怎么做才不违规
- 要把视频号内容生产做成可复现的流水线

---

## 铁律（在通用 12 条之上追加 2 条）

| # | 铁律 |
|---|---|
| R1–R12 | 同 `ecommerce-agent-orchestrator`：只有 4 个 Agent；判定类一律脚本化；机器 PASS 即 PASS；真并行；单一事实源；超 SLA 判 FAIL |
| **R13** | **带货视频含 AI 内容必须添加 AI 标注**（平台硬性要求：发布页「添加标注 → 勾选含 AI 生成内容」）；画面叠加文字标识是**建议做法，非平台强制**。数字人不得冒充真人实测。依据：视频号 2026-08-10 治理公告，违者限制分发 |
| **R14** | **未上架商品禁止任何挂车声明**，且 `product_page_fields` 全部标 BLOCKED；禁止任何 Agent 自补价格/库存/规格 |

---

## 4 个 Agent

| ID | 载体 | 只做 |
|---|---|---|
| RESEARCH | general-purpose | 产品事实、竞品、痛点 → `PRODUCT_FACTS.json` + `BLOCKED_FIELDS.json` |
| COMMERCE-COPY | + `ecommerce-operations` + `ad-copywriter` | 定位/卖点/Hook/标题/口播/SRT/CTA |
| CREATIVE | + `video-script-dissect` + 图像视频工具 | 视觉方向/Storyboard/AI 标注位设计 |
| CRITIC | general-purpose（独立实例） | 只审冠军 7 项（含 AI 标注可见性、无冒充），≤30 帧 |

详细角色卡见 `references/agents.md`。

---

## 6 个闸门

```bash
python scripts/channel_gates.py --run runs/<SKU> --sku <SKU> --gate all
```

| 闸门 | 检查 | 预算 |
|---|---|---|
| GATE-0 IDENTITY_LOCK | 昵称/头像/简介 vs 主体；非官方主体禁「官方/旗舰/专卖」；空模板拦截 | <1s |
| GATE-1 SKU_GUARD | sku 一致性；未上架→商品页字段全 BLOCKED；账号模式 vs 粉丝门槛 | <1s |
| GATE-2 COMPLIANCE | 两层词库（广告法基础层 + 视频号扩展层）+ BLOCKED 泄漏 | <3s |
| GATE-3 AI_DISCLOSURE | AI 标注三要素；数字人冒充真人检测；实物一致性 | <1s |
| GATE-4 MEDIA_TECH | 9:16 / ≤180s / 帧率 / 音轨 / 字幕条数==分镜数 | <5s |
| GATE-5 FRAME_EXTRACT | 每镜≤3、整片≤30 帧 + contact sheet | <120s |

---

## 发布计划器

```bash
python scripts/planner.py --facts runs/<SKU>/PRODUCT_FACTS.json --sku <SKU>
```

回答：**能不能挂车** / **卡在哪条门槛** / **解锁路径** / **AI 要不要标注、怎么标**。

带货门槛（2026-11-03 对照官方 20260612 版规则复核）：

| 账号类型 | 直播带货 | 短视频挂车 | 橱窗 |
|---|---|---|---|
| 店铺自营 · 账号**可对外展示** | 无关注数限制 | **无关注数限制** | 无关注数限制 |
| 店铺自营 · 账号**不可对外展示** | 无关注数限制 | **≥1000 有效关注** | 无关注数限制 |
| 带货者（只带联盟货） | ≥100 有效关注 | **≥1000 有效关注** | 无关注数限制（100 元保证金） |

⚠️ 门槛按**有效关注**算，主页总粉丝数不作数。

⚠️ **易错点**：不是「只要是小店自营就 0 粉可挂短视频」。官方原文是
「短视频带货：对外展示账号无关注数限制，其余账号需满足 ≥1000 有效关注」。
无法从规则推断你的账号属于哪一类，**必须去带货权限页确认**，
并把结果填进 `store_displayable`（true/false）；不填就按「无法判定」处理，不给挂车结论。

---

## 三种账号模式

| mode | 含义 | 内容策略 |
|---|---|---|
| `pre_launch` | 未开通带货 | 只能发不带链接内容；同时攒「已发视频 ≥10 条」 |
| `self_operated` | 店铺自营 | 可对外展示则短视频免门槛；否则需 1000 有效关注 |
| `talent` | 带货者 | 短视频需 1000 有效关注，能力受限 |

---

## 关键实操提醒

- **只能在发表时添加商品，无法对已发表内容补加商品** —— 发布前必须确认挂车意图
- 平台对**画面 + 话术 + 文案**做全维度 AI 语义筛查
- 内容审核不通过会私信通知，可在私信内申诉
- 漏标 AI 可在作品后台补标，通过后解除限流
- 具体规则以 `references/videochannel-rules.md`（**2026-11-03 复核版**）为准，
  文件里 `[官方]` 标记的条款附了官方链接可直接核；`[未核实]` 的不要当硬性要求对外讲。
  **超过 1 个月必须重新核对**

---

## 完整用法

见 `README.md`。

## 复用关系

| 复用 | 来源 |
|---|---|
| 违禁词基础库 | `ad-redline-guardian/references/banned-words.md` |
| 4-Agent 架构 | `ecommerce-agent-orchestrator` |
| 视频结构方法 | `video-script-dissect` |
| 文案 / 运营 | `ad-copywriter` + `ecommerce-operations` |
