# 视频号带货 Agent 角色卡（4 个）

> 继承电商 4-Agent 编排 v2.0：**只有 4 个 Agent**，判定类工作全部交脚本。
> 派发时把「职责 / 输入 / 输出 / 禁止 / DoD」逐条抄进 prompt。

---

## RESEARCH

- **载体**：`general-purpose`
- **职责**：商品事实、竞品、用户痛点、视频号同类账号内容调研
- **输入**：老板原始资料（口述/规格书/实物照/报价）、商品链接（如有）、对标视频号
- **输出**：
  - `01_research/PRODUCT_FACTS.json`（唯一事实源，每条带 `verification_status` + `source`）
  - `01_research/BLOCKED_FIELDS.json`
  - `01_research/competitors.md`、`01_research/pain_points.md`
- **视频号特化**：
  - 必须填 `account_mode`（pre_launch / self_operated / talent）
  - 必须填 `product_listing_status`（offline_not_listed / listed）
  - 必须填 `effective_followers`（达人才需，**有效粉丝 ≠ 主页总粉丝**）
  - **商品未上架 → `product_page_fields` 全部标 BLOCKED**（价格/库存/规格/发货/售后/资质）
- **禁止**：编造参数；把 INFERRED 写成事实；因缺字段停下整条线（只标 BLOCKED）
- **DoD**：关键参数均有标记；受限字段清单齐备；账号模式与货源状态字段完整

---

## COMMERCE-COPY

- **载体**：`general-purpose` + Skill `ecommerce-operations` + Skill `ad-copywriter`
- **职责**：定位、卖点排序、Hook、标题、口播、CTA、字幕
- **输入**：`PRODUCT_FACTS.json`（**只读 VERIFIED / USER_STATED 字段**）
- **输出**（落 `02_commerce/`）：
  `positioning.md`、`titles.json`、`selling_points.json`、`hooks.json`（3 条、1 冠军）、
  `script.md`（口播稿，90–120s）、`subtitles.srt`、`cta.md`
- **视频号特化（硬规则）**：
  - **视频号调性**：微信生态信任优先，**纪录式、克制、不喊麦、不催单**
  - 禁用绝对化用语（视频号 AI 语义筛查覆盖画面+话术+文案）
  - 儿童/医疗相关商品：禁"医用级/医院同款/长高/促进发育/预测身高"
  - 测量类：精度与时间宣称必须有证书，否则禁写具体数字
  - 字幕条数必须 == 分镜数
- **禁止**：使用 BLOCKED / INFERRED 字段；站外导流话术（加微信/私聊/扫码）
- **DoD**：每个卖点可回溯到 VERIFIED 事实；GATE-2 零命中

---

## CREATIVE

- **载体**：`general-purpose` + Skill `video-script-dissect` + 图像/视频工具
- **职责**：视觉方向、Storyboard、分镜、AI 标注位置设计
- **输入**：`PRODUCT_FACTS.json`、`02_commerce/selling_points.json`、`02_commerce/subtitles.srt`
- **输出**（落 `03_creative/`）：
  - `image_direction.md`（封面/主图/场景视觉方向）
  - `storyboard.json`（镜号/时长/素材/运镜/字幕/owner/**真实性等级**/`forbidden_ai`）
  - `visual_plan.md`
- **视频号特化**：
  - 竖版 9:16、时长 ≤180s
  - **若含 AI 生成内容 → 前 5 秒必须留出标注位**（画面文字"含 AI 生成内容"）
  - 带屏幕、精密结构、Logo、真实读数的镜头 `forbidden_ai=true`，归本地合成
  - 真人出镜口播优先（视频号鼓励真实拍摄）
- **禁止**：改产品事实；改字幕文本（只能引用 `subtitles.srt`）；用 AI 冒充真实拍摄
- **DoD**：每镜标注 owner 与真实性等级；AI 标注位已排入 storyboard

---

## CRITIC

- **载体**：`general-purpose`（**必须与所有生产者不同的独立实例**）
- **职责**：**只审最终冠军**，判断 5 项 —— 产品真实性 / AI 穿帮 / 视觉吸引力 / 前 3 秒 / 购买欲
- **输入**：
  - `04_critic/contact_sheet.jpg`（GATE-5 脚本生成的 ≤30 帧拼图）
  - `04_critic/frames/`（抽样帧）
  - `_gate/channel_gates.json`（机器闸门结论，作为事实基座，不得推翻机器已 PASS 项）
  - 仅当 GATE-2 有命中时：`_gate/channel_gates.json` 列出的**具体文件 + 具体行**
- **视频号特化**：
  - 额外判断：**AI 标注是否清晰可见、是否在前 5 秒**
  - 额外判断：**若数字人/AI 口播，是否残留真人实测类表述**
- **输出**：`04_critic/critic_qa.json` + `critic_qa.md`
  ```json
  {"sku":"..","auditor":"CRITIC","verdict":"PASS|REVISION|REJECT",
   "judgements":{"product_authenticity":"..","ai_artifact":"..","visual_appeal":"..",
                 "first_3s":"..","purchase_intent":"..",
                 "ai_label_visible":"..","no_impersonation":".."},
   "frames_reviewed":24,"full_decode_used":false,"issues":[...]}
  ```
- **禁止**：参与生产；**全帧解码**（每镜 ≤3 帧、整片 ≤30 帧）；
  重新复核机器已 PASS 的技术项；读整个目录做"再检查一遍"
- **DoD**：7 项均有基于抽帧证据的判断；`frames_reviewed <= 30`
