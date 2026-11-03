# 视频号带货自动化智能体（channel-agent v1.0）

面向**微信视频号带货**的确定性生产流水线。4 个 Agent 负责理解与创意，
**所有判定类工作由脚本完成**——机器说 PASS 就是 PASS，机器没说 PASS 谁都不许过。

> 架构继承 `ecommerce-agent-orchestrator` v2.0，叠加视频号专属的
> **AI 标注合规闸门** 与 **带货权限门槛推算**。

---

## 一、它解决什么问题

| 老板的问题 | 这套东西的回答 |
|---|---|
| "我现在能不能挂车？" | `planner.py` 逐条判定，卡在哪一条写清楚，并给解锁路径 |
| "这条视频会不会被判违规？" | `channel_gates.py` 用两层词库（广告法基础层 + 视频号扩展层，装第三方词库时 198 词）+ AI 标注规则机器筛查 |
| "AI 数字人能不能用？" | 能用。带货视频必须**在发布页勾选「含 AI 生成内容」**（官方硬性要求）；画面叠加文字是建议做法。数字人不得冒充真人实测 |
| "商品还没上架能发吗？" | 能发，但不能带链接；且商品页字段必须全标 BLOCKED，防止编造 |
| "这套跑多久？" | 6 个闸门全跑 **< 20 毫秒**（无成片时）；含抽帧 < 120s |

---

## 二、快速开始

```bash
cd <本技能根目录>

# 1. 建一个运行（--account-mode: pre_launch | self_operated | talent）
python scripts/state.py init --sku VC-Q2-001 --account-mode pre_launch

# 2. 建事实源（唯一事实源，缺的字段只能标 BLOCKED）
cp templates/PRODUCT_FACTS.template.json runs/VC-Q2-001/PRODUCT_FACTS.json
#    → 编辑填入真实信息；商品未上架时 product_page_fields 全部保持 BLOCKED

# 3. 算发布计划（能不能挂车 / AI 要不要标注）
python scripts/planner.py --facts runs/VC-Q2-001/PRODUCT_FACTS.json --sku VC-Q2-001

# 4. 跑闸门（生产完成后）
python scripts/channel_gates.py --run runs/VC-Q2-001 --sku VC-Q2-001 --gate all

# 5. 出结果页（老板只看这个）
python scripts/state.py render --sku VC-Q2-001
```

---

## 三、6 个机器闸门

| 闸门 | 检查 | 预算 | 触发条件 |
|---|---|---|---|
| **GATE-0** IDENTITY_LOCK | 昵称/头像/简介 vs 主体类型；非官方主体不得用「官方/旗舰/专卖」 | <1s | pre_launch 跳过 |
| **GATE-1** SKU_GUARD | sku 一致性；**未上架时商品页字段必须全 BLOCKED**；账号模式 vs 粉丝门槛 | <1s | 始终 |
| **GATE-2** COMPLIANCE | 两层词库（广告法基础层 + 视频号扩展层）+ BLOCKED 字段泄漏 | <3s | 有对外物料时 |
| **GATE-3** AI_DISCLOSURE | 发布页 AI 标注勾选（硬性，阻断项）；画面/前5秒标识只给建议不阻断；数字人冒充真人检测 | <1s | 始终 |
| **GATE-4** MEDIA_TECH | 可解码/9:16/时长≤180s/帧率/音轨/字幕条数==分镜数 | <5s | 有 mp4 时 |
| **GATE-5** FRAME_EXTRACT | 每镜≤3帧、整片≤30帧 + contact sheet（供 CRITIC） | <120s | 有 mp4 时 |

结果落 `_gate/channel_gates.json` 与 `.log`（含耗时与 `within_budget`）。
`_gate/` 为闸门独占，**生产 Agent 禁止修改**。

---

## 四、并行拓扑

```
波1（一条消息同批派发，max_concurrency ≥3）
  ├─ RESEARCH       → 01_research/PRODUCT_FACTS.json + BLOCKED_FIELDS.json + competitors.md + pain_points.md
  ├─ COMMERCE-COPY  → 02_commerce/positioning.md + titles.json + hooks.json + script.md + subtitles.srt + cta.md
  └─ CREATIVE       → 03_creative/image_direction.md + storyboard.json + visual_plan.md

波2（生产工具并行；有既有素材直接复用，不重新生成）
  └─ 图像/视频落地（视频默认先出首帧 + 参数待批注，不默认烧积分）

波3（纯脚本并行）
  └─ GATE-0 ∥ GATE-1 ∥ GATE-2 ∥ GATE-3   （发布计划 planner.py 同波次）

波4（成片就绪后）
  └─ GATE-4 → GATE-5 抽帧 → CRITIC 终审（只审冠军，≤30 帧）
```

---

## 五、铁律

| # | 铁律 |
|---|---|
| R1 | 只有 4 个 Agent，不得再拆分 |
| R2 | 判定类工作一律脚本化，**Agent 自检不作为依据** |
| R3 | 机器 PASS → 直接 PASS，禁止 Agent 复核 |
| R4 | 机器命中才叫人：只把**具体文件+具体行**交给 Critic |
| R5 | 真并行：同波次必须一条消息同批派发，`max_concurrency ≥3`，否则 `ORCHESTRATION_FAIL` |
| R6 | CRITIC 独立且只审冠军，禁止全帧解码（每镜≤3、整片≤30） |
| R7 | 单一事实源 `PRODUCT_FACTS.json`；缺字段进 BLOCKED，**任何 Agent 不得自补参数** |
| R8 | 同路线首次 + 最多 1 次修正，再失败换路线 |
| R9 | 媒体成本闸：视频默认先出首帧 + 参数待批注 |
| R10 | 超 SLA 直接 `PERFORMANCE_FAIL`，不用"最后全 PASS"掩盖 |
| R11 | 后台子 Agent 会被中止 → 必须前台同批调用 |
| R12 | 老板只看结果页，禁止倒过程日志 |
| **R13** | **视频号专属**：带货视频含 AI 内容必须双标注；数字人不得冒充真人实测 |
| **R14** | **视频号专属**：未上架商品禁止任何挂车声明；商品页字段全 BLOCKED |

---

## 六、目录约定

```
channel-agent/
├─ scripts/
│  ├─ channel_gates.py      # 6 个确定性闸门
│  ├─ state.py              # 状态机 + 结果页生成
│  └─ planner.py            # 发布计划器（能不能挂车 / AI 要不要标）
├─ references/
│  ├─ agents.md             # 4 个 Agent 角色卡
│  ├─ videochannel-rules.md # 平台规则快照（2026-11-03 复核，条款带官方链接）
│  ├─ banned-words.md       # 广告法基础词库（包内自带，法条来源）
│  └─ banned-words-videochannel.md  # 视频号违禁词扩展层
├─ templates/
│  ├─ PRODUCT_FACTS.template.json
│  └─ IDENTITY_LOCK.template.json
└─ runs/<SKU>/
   ├─ run_state.json        # 状态 + metrics
   ├─ PRODUCT_FACTS.json    # 唯一事实源
   ├─ IDENTITY_LOCK.json    # 账号身份锁
   ├─ 01_research/          # RESEARCH
   ├─ 02_commerce/          # COMMERCE-COPY
   ├─ 03_creative/          # CREATIVE
   ├─ 04_critic/            # CRITIC（frames/ + contact_sheet.jpg + critic_qa.json）
   ├─ 05_publish/           # planner 输出：publish_plan.json / .md
   ├─ 07_media/composited/  # 成片
   ├─ _gate/                # 闸门独占
   └─ result_panel.html     # ★ 老板看这个
```

---

## 七、复用关系（不重复造轮子）

| 复用了什么 | 来源 | 用途 |
|---|---|---|
| 违禁词库基础层 | `~/.workbuddy/skills/ad-redline-guardian/references/banned-words.md` | 广告法 6 大类 |
| 违禁词引擎思路 | `ad-redline-guardian/scripts/check_banned.py` | 21 行 md 解析器（本项目内联重写为支持多层词库） |
| 4-Agent 架构 / 铁律 | `~/.workbuddy/skills/ecommerce-agent-orchestrator` | 编排范式 |
| 视频结构 / 分镜方法 | `~/.workbuddy/skills/video-script-dissect` | CREATIVE 用 |
| 文案 / 运营 | `ad-copywriter` + `ecommerce-operations` | COMMERCE-COPY 用 |

**本项目的增量**：视频号平台规则、AI 标注合规闸门、带货权限门槛推算、未上架状态处理。

---

## 八、当前状态与下一步

**已跑通（2026-11-03 复测，19 项自动化测试）**：
- ✅ 负向测试：故意埋 4 类坑（价格未上架却标 VERIFIED / 数字人真人实测话术 / 绝对化用语 / 精度泄漏），闸门 100% 命中
- ✅ 三种账号模式判定正确（未开通→不能挂 / 自营已上架→可挂 / 达人粉丝不足→拦截）
- ✅ 漏抓修复：词库按最小短语切分 + 归一化去空格，解决了「3 秒」「0.1 毫米」漏抓
- ✅ 全链路 18ms 跑完 6 闸门 + 结果页生成

**待办**：
1. 老板提供真实商品事实（精度/规格/证书状态）→ 回填 PRODUCT_FACTS
2. 填写 IDENTITY_LOCK（昵称/头像/简介/主体类型）→ GATE-0 才能真跑
3. 决定解锁路径：开小店并把视频号设为可对外展示（推荐），或攒 1000 有效关注
4. 首条内容跑通后，验证「公开发表视频 ≥10 条」进度

## 与其他技能的关系

本技能的骨架（4 个 Agent + 确定性机器闸门、fail-closed 判定、SLA 预算）
继承自 [`ecommerce-agent-orchestrator`](../ecommerce-agent-orchestrator)，
在其之上叠加了**视频号专属**的部分：账号主体校验（GATE-0）、
带货身份与关注数门槛、AI 标注合规（GATE-3）、视频号违禁词扩展层。

**为什么分成两个技能而不是一个**：通用编排讲的是「怎么不让 Agent 自己给自己打分」，
视频号讲的是「具体平台的资格与合规红线」。拆开写，两个场景都能独立安装、
独立演进；合在一起会让只想用通用闸门的人被迫读一堆平台细节。
两者共用同一套判定哲学的代码结构，接口一致，不冲突。

## 运行环境

| 项 | 状态 |
|---|---|
| Windows 11 + Python 3.12 / 3.13 | ✅ 实测通过 |
| Linux（含 WSL） | ⚠️ **未实测**。代码用 `pathlib`，没有硬编码分隔符，理论上可跑；但测试只在 Windows 跑过，不声称支持 |
| macOS | ⚠️ 未实测 |

诚实标注：不要在没有实跑过的平台上对外声称「已支持」。
