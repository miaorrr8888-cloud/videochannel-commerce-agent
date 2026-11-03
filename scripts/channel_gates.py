#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频号带货 · 确定性闸门引擎（channel-guard v1.0）

设计原则（继承电商 4-Agent 编排 v2.0）：
  R2  判定类工作一律脚本化，禁止 Agent 自检作为依据
  R3  机器 PASS → 直接 PASS，禁止 Agent 复核
  R7  单一事实源 PRODUCT_FACTS.json
  R9  媒体成本闸：默认不烧积分

5 个闸门：
  GATE-0 IDENTITY_LOCK        <1s   账号身份一致性（昵称/头像/简介 vs 主体）
  GATE-1 SKU_GUARD            <1s   商品字段完整性 + 货源状态一致性
  GATE-2 COMPLIANCE           <3s   违禁词（基础层+视频号扩展层）+ BLOCKED 字段泄漏
  GATE-3 AI_DISCLOSURE        <1s   AI 标注合规（视频号 2026-08-10 新规）+ 数字人冒充检测
  GATE-4 MEDIA_TECH           <5s   可解码/分辨率/时长/帧率/音轨/字幕条数（ffmpeg）
  GATE-5 抽帧（供 CRITIC）      <120s 每镜≤3帧、整片≤30帧 + contact sheet

用法:
  python channel_gates.py --run runs/<SKU> --facts runs/<SKU>/PRODUCT_FACTS.json \\
                          --sku <SKU> [--gate 0|1|2|3|4|5|all] [--video <mp4>]

退出码: 0=全 PASS, 1=有 FAIL, 2=错误
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent

GATE_BUDGET_S = {0: 1.0, 1: 1.0, 2: 3.0, 3: 1.0, 4: 5.0, 5: 120.0}
GATE_NAMES = {
    0: "IDENTITY_LOCK", 1: "SKU_GUARD", 2: "COMPLIANCE",
    3: "AI_DISCLOSURE", 4: "MEDIA_TECH", 5: "FRAME_EXTRACT",
}

# 视频号带货权限门槛（2026-11-03 对照官方 20260612 版规则复核；未开通时 mode=pre_launch）
# 注意：自营账号的短视频挂车**不是一律 0 粉**——只有「微信小店可对外展示」的账号免门槛，
# 其余自营账号仍需 ≥1000 有效关注。是否可对外展示必须由 store_displayable 明确告知。
VC_THRESHOLD = {
    "self_operated": {"short_video": 0, "live": 0, "showcase": 0,
                      "requires_public_store": True,
                      "fallback_short_video": 1000},
    "talent": {"short_video": 1000, "live": 100, "showcase": 0},
    "pre_launch": {"short_video": 0, "live": 0, "showcase": 0},
}

def _resolve_base_wordlist() -> Path:
    """定位「广告法基础词库」。

    该词库来自 ad-redline-guardian 技能（可能是第三方安装），路径因机器而异。
    候选顺序：环境变量 > 同级 skills 目录 > 用户工作区 skills 目录 > 包内自带副本。
    任何情况下都不写死本机绝对路径。
    """
    import os
    env_dir = os.environ.get("AD_REDLINE_SKILL_DIR")
    cands = []
    if env_dir:
        cands.append(Path(env_dir) / "references" / "banned-words.md")
    cands.append(SKILL_ROOT.parent / "ad-redline-guardian" / "references" / "banned-words.md")
    home = Path.home()
    for base in (".workbuddy/skills", ".codebuddy/skills", ".claude/skills"):
        cands.append(home / base / "ad-redline-guardian" / "references" / "banned-words.md")
    # 包内自带（若使用者选择随包分发基础词库）
    cands.append(SKILL_ROOT / "references" / "banned-words.md")
    for c in cands:
        if c.is_file():
            return c
    return cands[0]  # 不存在时返回首个候选，由 gate2 判 FAIL，不崩溃


BASE_WORDLIST = _resolve_base_wordlist()
VC_WORDLIST = SKILL_ROOT / "references" / "banned-words-videochannel.md"


def base_wordlist_source() -> str:
    """说明基础词库到底来自哪里。

    装了 ad-redline-guardian 就用它（词更多），否则用包内 references/banned-words.md
    （依据《广告法》条文整理的基线）。两种来源检出力不同，判定报告里必须写清楚，
    不能让人以为「PASS」的强度是一样的。
    """
    p = Path(BASE_WORDLIST)
    if p.parent.parent.name == "ad-redline-guardian":
        return "ad-redline-guardian"
    if p == SKILL_ROOT / "references" / "banned-words.md":
        return "builtin-statutory"
    return "custom:%s" % p


WORDLIST_SOURCE = base_wordlist_source()


# ---------------------------------------------------------------- utils
def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"__parse_error__": str(e)}


def norm(text: str) -> str:
    """NFKC 归一化 + 去零宽字符 + 去空白，提高命中率。

    去空白是关键：中文文案里「3 秒」「0.1 毫米」「全网 最低」这类
    带空格的写法会导致关键词漏抓（实测踩过）。
    """
    text = unicodedata.normalize("NFKC", str(text))
    for z in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(z, "")
    return re.sub(r"[\s\u3000]+", "", text)


def parse_wordlist(md_path: Path):
    """解析词库 md：## 类别标题下表格第一列命中词 → {category: [words]}"""
    cats = {}
    if not md_path.exists():
        return cats
    cur = None
    for line in md_path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            cur = re.sub(r"^[\d一二三四五六七八九十]+[\.、]\s*", "", m.group(1)).strip()
            cur = re.sub(r"（.*?）$", "", cur).strip()
            cats.setdefault(cur, [])
            continue
        if cur and line.startswith("|") and not re.match(r"^\|[\s:\-|]+\|$", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and cells[0] and cells[0] not in ("命中词",):
                for tok in re.split(r"[、，,]", cells[0]):
                    tok = re.sub(r"（[^）]*）", "", tok).strip()
                    if tok and len(tok) >= 2:
                        cats[cur].append(tok)
    return cats


def scan_text(text: str, wordlists: dict):
    """返回命中列表 [{category, word, context, layer}]"""
    hits = []
    t = norm(text)
    for layer, cats in wordlists.items():
        for cat, words in cats.items():
            for w in words:
                if norm(w) in t:
                    idx = t.find(norm(w))
                    ctx = t[max(0, idx - 20): idx + len(norm(w)) + 20]
                    hits.append({
                        "layer": layer, "category": cat,
                        "word": w, "context": ctx.strip(),
                    })
    return hits


# 对外物料文件（会被扫描的范围）
AD_FACING_GLOBS = [
    ("02_commerce", ["*.json", "*.md", "*.srt", "*.txt"]),
    ("03_creative", ["*.json", "*.md"]),
    ("05_publish", ["*.json", "*.md", "*.txt"]),
]
# 明确排除的内部文件（含方法论/自检记录，引述违禁词不算违规）
INTERNAL_PAT = re.compile(
    r"(自检|合规|红线|禁用|受限|排除|方法论|证据|来源|复核|修订|更正|"
    r"CHECK|REPORT|_gate|BLOCKED|README|TEMPLATE)",
    re.I,
)


def collect_ad_facing(run: Path):
    """按目录通配收集对外物料，不枚举文件名 → 新增文件自动纳入。"""
    out = []
    for sub, pats in AD_FACING_GLOBS:
        d = run / sub
        if not d.exists():
            continue
        for pat in pats:
            for f in sorted(d.glob(pat)):
                if INTERNAL_PAT.search(f.name):
                    continue
                out.append(f)
    return out


def extract_text(path: Path) -> str:
    """从文件抽取可扫描文本。SRT 只取字幕正文，JSON 取叶子字符串值。"""
    if path.suffix.lower() == ".srt":
        lines = []
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if not s or s.isdigit() or "-->" in s:
                continue
            lines.append(s)
        return "\n".join(lines)
    if path.suffix.lower() == ".json":
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return path.read_text(encoding="utf-8", errors="replace")
        vals = []

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            elif isinstance(o, str):
                vals.append(o)
        walk(obj)
        return "\n".join(vals)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- GATE 0
def gate0_identity(run: Path, facts: dict):
    """账号身份一致性：昵称/头像/简介 不得与店铺主体冲突、不得冒充官方。

    注意：pre_launch 跳过的是"身份一致性判定"，不是"整条闸门"。
    若事实源本身为空/占位未填，仍必须报错，否则空跑会被误读为"检查通过"。
    """
    chk, issues = {}, []
    lock_p = run / "IDENTITY_LOCK.json"
    lock = load_json(lock_p)

    # --- 前置：事实源不得为占位空模板 ---
    placeholder = {"", "示例字段_请替换"}
    real = facts.get("facts", {}) or {}
    has_real_fact = any(k not in placeholder for k in real.keys())
    if not facts.get("product_name") and not has_real_fact:
        chk["identity_lock"] = "FAIL"
        chk["reason"] = "事实源为占位空模板（product_name 与 facts 均未填）"
        issues.append(
            "PRODUCT_FACTS.json 仍是模板占位内容：product_name 与 facts 均为空 → "
            "禁止进入生产流程，先回填真实事实"
        )
        return chk, issues, []

    if lock is None:
        mode = str(facts.get("account_mode", "")).strip()
        if mode == "pre_launch":
            chk["identity_lock"] = "SKIP"
            chk["reason"] = "pre_launch 模式，账号未开通带货，不做身份一致性判定"
            return chk, issues, []
        chk["identity_lock"] = "FAIL"
        issues.append("缺少 IDENTITY_LOCK.json（未开通带货时 account_mode 须为 pre_launch）")
        return chk, issues, []

    # 身份字段
    nickname = str(lock.get("nickname", "")).strip()
    bio = str(lock.get("bio", "")).strip()
    subject = str(lock.get("subject_type", "")).strip()

    if not nickname:
        issues.append("IDENTITY_LOCK.json 缺 nickname")

    # 冒充官方检查（非官方主体不得用官方/旗舰/专卖/总店）
    FORBID = {
        "official": r"(官方|旗舰|专卖|总店|直营|微信官方|腾讯官方)",
    }
    combined = norm(nickname + " " + bio)
    if subject not in ("official_flagship", "official"):
        if re.search(FORBID["official"], combined):
            issues.append(
                "昵称/简介含『官方/旗舰/专卖/总店/直营』字样，"
                f"但 subject_type={subject or '未填'} 非官方旗舰主体 → 冒充平台/官方，重度违规"
            )

    # 头像存在性
    avatar = str(lock.get("avatar", "")).strip()
    if avatar:
        ap = Path(avatar)
        if not ap.is_absolute():
            ap = run / avatar
        if not ap.exists():
            issues.append(f"头像文件不存在：{avatar}")
    else:
        issues.append("IDENTITY_LOCK.json 缺 avatar 路径")

    chk["identity_lock"] = "PASS" if not issues else "FAIL"
    chk["nickname"] = nickname
    chk["subject_type"] = subject or "未填"
    chk["fields_filled"] = f"{sum(1 for k in ('nickname','avatar','bio','subject_type') if lock.get(k))}/4"
    return chk, issues, []


# ---------------------------------------------------------------- GATE 1
def gate1_sku_guard(run: Path, facts: dict, sku: str):
    """商品字段完整性 + 货源状态一致性 + 带货模式与门槛匹配。"""
    chk, issues = {}, []
    facts = facts or {}

    # --- 1. 核心标识字段 ---
    sku_id = str(facts.get("sku_id", "")).strip()
    if not sku_id:
        issues.append("PRODUCT_FACTS.json 缺 sku_id")
    elif sku.lower() not in sku_id.lower() and sku_id.lower() not in sku.lower():
        issues.append(f"sku_id({sku_id}) 与调用入参 --sku({sku}) 不一致")

    # --- 2. 各产物 sku 一致性 ---
    arts = {}
    for rel in ("02_commerce/titles.json", "02_commerce/hooks.json",
                "02_commerce/selling_points.json", "03_creative/storyboard.json"):
        p = run / rel
        if not p.exists():
            continue
        o = load_json(p, {})
        if not isinstance(o, dict):
            continue
        v = str(next((o[k] for k in ("sku_id", "sku") if k in o), "")).strip()
        if v:
            arts[rel] = v
    mism = {k: v for k, v in arts.items() if sku_id and v != sku_id}
    if mism:
        for k, v in mism.items():
            issues.append(f"{k} 的 sku({v}) != 事实源 sku_id({sku_id})")
    chk["sku_consistency"] = f"{len(arts) - len(mism)}/{len(arts)} 一致" if arts else "无产物"

    # --- 3. 货源状态一致性（老板场景：线下货源未上架） ---
    product_status = str(facts.get("product_listing_status", "")).strip()
    if product_status == "offline_not_listed":
        # 未上架 → 商品页字段必须全部 BLOCKED，且禁止生成挂车产物
        leaked = []
        pages = facts.get("product_page_fields", {}) or {}
        for k, v in pages.items():
            st = str(v.get("verification_status", "")).upper() if isinstance(v, dict) else ""
            if st != "BLOCKED":
                leaked.append(f"{k}({st or '无标记'})")
        if leaked:
            issues.append(
                "货源状态=offline_not_listed（商品未上架），但以下商品页字段未标记 BLOCKED，"
                f"存在编造风险：{', '.join(leaked)}"
            )
        chk["product_listing_status"] = "offline_not_listed ✓ 商品页字段应全 BLOCKED"
        # 未上架时若存在挂车声明 → 违规
        publish_p = run / "05_publish" / "publish_plan.json"
        if publish_p.exists():
            pp = load_json(publish_p, {})
            if isinstance(pp, dict) and pp.get("attach_product_link") is True:
                issues.append(
                    "商品尚未上架小店，但 publish_plan.attach_product_link=true → "
                    "视频无法挂车，属无效声明"
                )
    else:
        chk["product_listing_status"] = product_status or "未填"

    # --- 4. 带货模式 vs 权限门槛 ---
    mode = str(facts.get("account_mode", "")).strip()
    follows = facts.get("effective_followers")
    if mode in VC_THRESHOLD:
        th = VC_THRESHOLD[mode]
        need = th.get("short_video", 0)
        if mode == "talent":
            if follows is None:
                issues.append("account_mode=talent 但缺 effective_followers（有效关注数）")
            elif int(follows) < need:
                issues.append(
                    f"account_mode=talent 短视频挂车需 ≥{need} 有效关注，"
                    f"当前 {follows} → 权限不足"
                )
        elif mode == "self_operated":
            # 官方：只有「可对外展示」的自营账号免关注数门槛，其余仍需 1000
            displayable = facts.get("store_displayable")
            fallback = th.get("fallback_short_video", 1000)
            if displayable is True:
                need = 0
            elif displayable is False:
                need = fallback
                if follows is None:
                    issues.append(
                        "account_mode=self_operated 且账号不可对外展示，"
                        f"短视频挂车需 ≥{fallback} 有效关注，但缺 effective_followers"
                    )
                elif int(follows) < fallback:
                    issues.append(
                        f"账号不可对外展示，短视频挂车需 ≥{fallback} 有效关注，"
                        f"当前 {follows} → 权限不足"
                    )
            else:
                issues.append(
                    "account_mode=self_operated 但缺 store_displayable（是否可对外展示）："
                    "官方规则下这两类账号门槛不同，不能默认按 0 粉放行"
                )
                need = None
        chk["account_mode"] = mode
        if need is None:
            chk["threshold_check"] = "无法判定（账号是否可对外展示未告知）"
        else:
            chk["threshold_check"] = (f"short_video 需 {need} 有效关注 / "
                                      f"实有 {follows if follows is not None else '未填'}")
    else:
        chk["account_mode"] = mode or "未填"
        if not mode:
            issues.append("缺 account_mode（pre_launch / self_operated / talent 三选一）")

    # --- 5. 产物文件存在性 ---
    required = ["01_research/BLOCKED_FIELDS.json"]
    missing = [r for r in required if not (run / r).exists()]
    if missing:
        issues.append(f"缺必需产物：{', '.join(missing)}")

    chk["gate"] = "PASS" if not issues else "FAIL"
    return chk, issues, []


# ---------------------------------------------------------------- GATE 2
def gate2_compliance(run: Path, facts: dict):
    """违禁词（基础层+视频号扩展层）+ BLOCKED 字段泄漏检测。"""
    chk, issues, detail = {}, [], []
    wordlists = {
        "基础层(广告法)": parse_wordlist(BASE_WORDLIST),
        "视频号扩展层": parse_wordlist(VC_WORDLIST),
    }
    n_words = sum(len(v) for cats in wordlists.values() for v in cats.values())
    chk["wordlist_size"] = n_words
    chk["base_wordlist_source"] = WORDLIST_SOURCE

    # 词库缺失不得静默放行：空词库 = 零检出力，必须判 FAIL 并说明修复方式
    if not wordlists["视频号扩展层"]:
        issues.append(
            f"视频号扩展词库为空或不可读：{VC_WORDLIST}（应为包内自带，检查安装完整性）"
        )
    if not wordlists["基础层(广告法)"]:
        issues.append(
            f"广告法基础词库不可用：{BASE_WORDLIST} —— "
            "装 ad-redline-guardian 技能或设 AD_REDLINE_SKILL_DIR，否则 GATE-2 不放行"
        )
    if n_words == 0:
        chk["gate"] = "FAIL"
        return chk, issues, detail

    files = collect_ad_facing(run)
    chk["scanned_files"] = len(files)
    if not files:
        chk["compliance"] = "SKIP"
        chk["reason"] = "无对外物料文件可扫描（02_commerce / 03_creative / 05_publish）"
        return chk, issues, detail

    total_hits = 0
    for f in files:
        text = extract_text(f)
        hits = scan_text(text, wordlists)
        if hits:
            total_hits += len(hits)
            rel = f.relative_to(run).as_posix()
            for h in hits:
                detail.append({
                    "file": rel, "layer": h["layer"], "category": h["category"],
                    "word": h["word"], "context": h["context"],
                })

    # BLOCKED 字段泄漏：对外文案不得出现 BLOCKED 字段的取值
    blocked = load_json(run / "BLOCKED_FIELDS.json", {}) or {}
    bnames = []
    if isinstance(blocked, dict):
        bnames = [str(k) for k in (blocked.get("blocked_fields") or blocked).keys()]
    elif isinstance(blocked, list):
        bnames = [str(b.get("field", "")) for b in blocked if isinstance(b, dict)]
    bnames = [b for b in bnames if b]

    leak = []
    for f in files:
        t = norm(extract_text(f))
        for b in bnames:
            if norm(b) in t and not INTERNAL_PAT.search(f.name):
                leak.append({"file": f.relative_to(run).as_posix(), "blocked_field": b})

    chk["banned_hits"] = total_hits
    chk["banned_hit_files"] = len({d["file"] for d in detail})
    chk["blocked_fields"] = len(bnames)
    chk["blocked_leaks"] = len(leak)

    if total_hits:
        issues.append(f"违禁词命中 {total_hits} 处，涉及 {chk['banned_hit_files']} 个文件")
    if leak:
        issues.append(f"BLOCKED 字段泄漏 {len(leak)} 处（商品未上架，参数不得出现在对外文案）")

    chk["gate"] = "PASS" if not issues else "FAIL"
    return chk, issues, detail + [{"leak": x} for x in leak]


# ---------------------------------------------------------------- GATE 3
def gate3_ai_disclosure(run: Path, facts: dict):
    """视频号 AI 标注合规（2026-08-10 治理公告，2026-11-03 复核）+ 数字人冒充真人检测。

    规则要点（以官方公告为准）：
      - 带货视频含 AI 生成内容 → **必须**通过平台功能添加 AI 标注
        （发表页 → 添加标注 → 勾选「含 AI 生成内容」）。
      - 非带货普通视频 → 倡导标注，未标注不影响分发。
      - 不得用 AI 虚构真实使用体验；AI 生成内容须与实物一致，否则限制分发。

    判定口径（重要，别把建议当规则）：
      - **阻断项**：带货场景下 publish_plan.ai_label_page_checked ≠ true。
      - **非阻断项**：画面叠加文字标识、标识是否在前 5 秒 —— 这是本项目的操作建议，
        官方公告未作强制要求，只进 detail 提示，不判 FAIL。
    """
    chk, issues, detail = {}, [], []
    facts = facts or {}

    ai_used = facts.get("ai_generated_content")
    if ai_used is None:
        chk["ai_declaration"] = "SKIP"
        chk["reason"] = "PRODUCT_FACTS 未声明 ai_generated_content，无法判定"
        # 未声明不算 PASS，标待补
        issues.append("缺 ai_generated_content 字段：必须声明本片是否含 AI 生成内容")
        chk["gate"] = "FAIL"
        return chk, issues, detail

    account_mode = str(facts.get("account_mode", "")).strip()
    is_carrying = account_mode in ("self_operated", "talent")

    if not ai_used:
        chk["ai_declaration"] = "NOT_REQUIRED"
        chk["reason"] = "本片全真实拍摄，无需 AI 标注"
        chk["gate"] = "PASS"
        return chk, issues, detail

    # 含 AI 内容 → 检查状态
    ai_types = facts.get("ai_content_types", []) or []
    chk["ai_types"] = ai_types
    # 带货场景即需标注；pre_launch 走"倡导标注"，但仍建议标
    chk["scenario"] = "带货挂车（强制标注）" if is_carrying else "未挂车（倡导标注）"

    # 检查发布计划里的标注声明
    pp = load_json(run / "05_publish" / "publish_plan.json", {}) or {}
    if not isinstance(pp, dict):
        pp = {}
    on_screen = pp.get("ai_label_on_screen") is True
    page_decl = pp.get("ai_label_page_checked") is True
    first5s = pp.get("ai_label_within_first_5s") is True

    chk["label_on_screen"] = on_screen
    chk["label_page_checked"] = page_decl
    chk["label_within_first_5s"] = first5s

    if is_carrying:
        # 只有平台勾选这一条是硬性要求
        if not page_decl:
            issues.append(
                "带货视频含 AI 内容但未勾选发布页 AI 标注（publish_plan.ai_label_page_checked≠true）"
                "—— 官方要求：发表页 → 添加标注 → 勾选「含 AI 生成内容」"
            )
        # 以下仅为建议，不阻断
        if not on_screen:
            detail.append({"advisory":
                           "建议在画面叠加「含 AI 生成内容」字样：官方未强制，但能降低用户误解与投诉"})
        if not first5s:
            detail.append({"advisory":
                           "建议把 AI 标识放在前 5 秒：官方未规定具体秒数，越早越稳妥"})
    else:
        if not on_screen:
            detail.append({"info": "未挂车场景 AI 标注为倡导非强制，建议补标以降低后续挂车审查风险"})

    # 数字人冒充真人的文案检测（视频号零容忍）
    IMPERSONATE = [
        r"我(亲自|本人)(试过|用过|测过|体验过)",
        r"真人实测", r"亲测", r"实拍", r"原相机", r"无滤镜", r"无美颜",
        r"我的真实(使用)?(感受|体验|反馈)",
    ]
    if "digital_human" in ai_types or "ai_voiceover" in ai_types:
        files = collect_ad_facing(run)
        for f in files:
            t = norm(extract_text(f))
            for pat in IMPERSONATE:
                if re.search(pat, t):
                    detail.append({
                        "file": f.relative_to(run).as_posix(),
                        "pattern": pat,
                        "issue": "AI 口播/数字人场景出现真人实测类表述 → 虚构使用体验",
                    })
        if any("issue" in d for d in detail):
            issues.append(
                "AI 口播/数字人场景存在真人实测类表述（虚构使用体验），"
                "视频号判定为最重违规类型"
            )

    # 商品一致性声明
    if facts.get("ai_content_matches_product") is not True:
        issues.append(
            "缺/未确认 ai_content_matches_product：标注后若 AI 生成内容与实物不符，"
            "视频号同样限制分发"
        )

    chk["gate"] = "PASS" if not issues else "FAIL"
    return chk, issues, detail


# ---------------------------------------------------------------- GATE 4
def ffprobe_json(path: Path):
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "ffprobe 未安装"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None, (r.stderr or "ffprobe 失败").strip()[:200]
        return json.loads(r.stdout), None
    except Exception as e:
        return None, str(e)[:200]


def count_srt_entries(p: Path):
    if not p.exists():
        return None
    n = 0
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "-->" in ln:
            n += 1
    return n


def gate4_media_tech(run: Path, video: str = ""):
    """可解码/分辨率/时长/帧率/音轨/字幕条数/镜头文件完整性。"""
    chk, issues = {}, []
    vp = Path(video) if video else None
    if vp and not vp.is_absolute():
        vp = run / vp
    if vp is None or not vp.exists():
        cands = []
        for d in ("07_media/composited", "07_media", "03_creative"):
            dd = run / d
            if dd.exists():
                cands += sorted(dd.glob("*.mp4"))
        if not cands:
            chk["media_tech"] = "SKIP"
            chk["reason"] = "未找到成片 mp4（生产阶段未开始时可 SKIP）"
            return chk, issues, None
        vp = cands[0]

    chk["video"] = vp.relative_to(run).as_posix() if run in vp.parents or str(vp).startswith(str(run)) else str(vp)
    meta, err = ffprobe_json(vp)
    if err:
        chk["media_tech"] = "SKIP_NO_FFPROBE"
        chk["reason"] = f"ffprobe 不可用（{err}）→ 技术闸门跳过，需在剪辑机复核"
        return chk, issues, None

    fmt = meta.get("format", {}) or {}
    streams = meta.get("streams", []) or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    dur = float(fmt.get("duration") or 0)
    w = int(vstream.get("width") or 0) if vstream else 0
    h = int(vstream.get("height") or 0) if vstream else 0
    fr = 0.0
    if vstream:
        try:
            num, den = str(vstream.get("avg_frame_rate") or "0/1").split("/")
            fr = round(float(num) / float(den), 2) if float(den) else 0.0
        except Exception:
            fr = 0.0

    chk.update({"duration_s": round(dur, 2), "resolution": f"{w}x{h}",
                "fps": fr, "has_audio": bool(astream),
                "vcodec": (vstream or {}).get("codec_name", "")})

    # 规则：视频号竖版 9:16 优先
    if w and h:
        if h <= w:
            issues.append(f"非竖版（{w}x{h}）：视频号带货短视频建议竖版 9:16")
        elif abs((h / w) - (16 / 9)) > 0.06:
            issues.append(f"非标准 9:16（{w}x{h}），实际比例 {round(h/w, 3)}")
    if dur <= 0:
        issues.append("无法读取时长")
    elif dur > 180:
        issues.append(f"时长 {dur:.1f}s 超过 180s，视频号带货短视频建议 ≤180s")
    if not astream:
        issues.append("无音轨")
    if fr and not (20 <= fr <= 61):
        issues.append(f"帧率 {fr} 异常")

    # 字幕条数 vs 分镜数
    srt_n = count_srt_entries(run / "02_commerce" / "subtitles.srt")
    sb = load_json(run / "03_creative" / "storyboard.json", {}) or {}
    shots = None
    if isinstance(sb, dict):
        shots = sb.get("shots") or sb.get("storyboard")
    if isinstance(shots, dict):
        shots = list(shots.values())
    shot_n = len(shots) if isinstance(shots, list) else None
    chk["srt_entries"] = srt_n
    chk["storyboard_shots"] = shot_n
    if srt_n is not None and shot_n is not None and srt_n != shot_n:
        issues.append(f"字幕条数({srt_n}) != 分镜数({shot_n})")

    # 镜头文件完整性
    if isinstance(shots, list) and shots:
        missing = []
        for s in shots:
            if not isinstance(s, dict):
                continue
            f = s.get("file") or s.get("output") or ""
            if f:
                fp = Path(f)
                if not fp.is_absolute():
                    fp = run / f
                if not fp.exists():
                    missing.append(f)
        if missing:
            issues.append(f"分镜引用文件缺失 {len(missing)} 个：{', '.join(missing[:5])}")

    chk["gate"] = "PASS" if not issues else "FAIL"
    return chk, issues, vp


def gate5_frames(run: Path, video: Path, per_shot=3, cap=30):
    """抽帧：每镜 ≤3 帧、整片 ≤30 帧 + contact sheet。"""
    chk, issues = {}, []
    out = run / "04_critic" / "frames"
    out.mkdir(parents=True, exist_ok=True)
    if video is None or not Path(video).exists():
        chk["frame_extract"] = "SKIP"
        chk["reason"] = "无成片可抽帧"
        return chk, issues
    exe = shutil.which("ffmpeg")
    if not exe:
        chk["frame_extract"] = "SKIP"
        chk["reason"] = "ffmpeg 未安装"
        return chk, issues
    meta, err = ffprobe_json(Path(video))
    if err:
        chk["frame_extract"] = "SKIP"
        chk["reason"] = f"ffprobe 不可用：{err}"
        return chk, issues
    dur = float((meta.get("format") or {}).get("duration") or 0)
    sb = load_json(run / "03_creative" / "storyboard.json", {}) or {}
    shots = sb.get("shots") if isinstance(sb, dict) else None
    n_shot = len(shots) if isinstance(shots, list) and shots else 6
    n = min(n_shot * per_shot, cap)
    ts = [round(dur * (i + 0.5) / n, 3) for i in range(n)] if dur and n else []
    made = 0
    for i, t in enumerate(ts):
        fp = out / f"f{i+1:02d}_{t}s.jpg"
        try:
            r = subprocess.run(
                [exe, "-y", "-ss", str(t), "-i", str(video),
                 "-frames:v", "1", "-q:v", "3", str(fp)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and fp.exists():
                made += 1
        except Exception:
            pass
    # contact sheet
    sheet = run / "04_critic" / "contact_sheet.jpg"
    try:
        subprocess.run(
            [exe, "-y", "-pattern_type", "glob", "-i", str(out / "*.jpg"),
             "-filter_complex", "scale=320:-1,tile=5x6", "-frames:v", "1", str(sheet)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        pass
    chk.update({"frame_extract": "DONE" if made else "FAIL",
                "frames_made": made, "planned": n,
                "cap": cap, "contact_sheet": sheet.exists()})
    if not made:
        issues.append("抽帧失败")
    return chk, issues


# ---------------------------------------------------------------- runner
def run_gate(num, run: Path, facts: dict, sku: str, video: str):
    t0 = time.time()
    if num == 0:
        chk, issues, detail = gate0_identity(run, facts)
    elif num == 1:
        chk, issues, detail = gate1_sku_guard(run, facts, sku)
    elif num == 2:
        chk, issues, detail = gate2_compliance(run, facts)
    elif num == 3:
        chk, issues, detail = gate3_ai_disclosure(run, facts)
    elif num == 4:
        chk, issues, vp = gate4_media_tech(run, video)
        detail = []
    elif num == 5:
        vp = None
        if video:
            vp = Path(video)
            if not vp.is_absolute():
                vp = run / vp
        if vp is None or not vp.exists():
            cands = []
            for d in ("07_media/composited", "07_media"):
                dd = run / d
                if dd.exists():
                    cands += sorted(dd.glob("*.mp4"))
            vp = cands[0] if cands else None
        chk, issues = gate5_frames(run, vp)
        detail = []
    else:
        raise ValueError(f"未知闸门 {num}")

    el = round(time.time() - t0, 3)
    verdict = "PASS" if not issues else "FAIL"
    if chk.get("gate") == "SKIP" or str(chk.get(chk.get("gate", ""), "")).startswith("SKIP"):
        verdict = "SKIP"
    return {
        "gate": num, "name": GATE_NAMES[num], "verdict": verdict,
        "issues": issues, "detail": detail, "check": chk,
        "elapsed_s": el, "within_budget": el <= GATE_BUDGET_S[num],
        "budget_s": GATE_BUDGET_S[num],
    }


def main():
    ap = argparse.ArgumentParser(description="视频号带货 · 确定性闸门引擎 v1.0")
    ap.add_argument("--run", required=True, help="运行目录 runs/<SKU>")
    ap.add_argument("--facts", default="", help="PRODUCT_FACTS.json 路径")
    ap.add_argument("--sku", required=True)
    ap.add_argument("--gate", default="all", help="0|1|2|3|4|5|all")
    ap.add_argument("--video", default="", help="成片 mp4 路径（可选）")
    a = ap.parse_args()

    run = Path(a.run).resolve()
    if not run.exists():
        print(f"错误：运行目录不存在 {run}")
        return 2
    facts_p = Path(a.facts).resolve() if a.facts else run / "PRODUCT_FACTS.json"
    facts = load_json(facts_p, {}) or {}
    if "__parse_error__" in facts:
        print(f"错误：PRODUCT_FACTS.json 解析失败：{facts['__parse_error__']}")
        return 2
    if not facts:
        print(f"警告：事实源不存在或为空 {facts_p} → 依赖事实源的闸门将 FAIL")

    nums = list(GATE_BUDGET_S.keys()) if a.gate == "all" else [int(x) for x in a.gate.split(",")]

    results, t_all = [], time.time()
    for n in nums:
        try:
            r = run_gate(n, run, facts, a.sku, a.video)
        except Exception as e:
            r = {"gate": n, "name": GATE_NAMES[n], "verdict": "ERROR",
                 "issues": [f"闸门异常：{e}"], "detail": [], "check": {},
                 "elapsed_s": 0, "within_budget": False, "budget_s": GATE_BUDGET_S[n]}
        results.append(r)
        mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "ERROR": "ERR "}[r["verdict"]]
        print(f"[{mark}] GATE-{n} {r['name']:<16} {r['elapsed_s']:>7.3f}s / {r['budget_s']}s")
        for i in r["issues"]:
            print(f"        └─ {i}")

    total = round(time.time() - t_all, 3)
    n_fail = sum(1 for r in results if r["verdict"] in ("FAIL", "ERROR"))
    n_over = sum(1 for r in results if not r["within_budget"])
    overall = "PASS" if n_fail == 0 else "FAIL"

    gd = run / "_gate"
    gd.mkdir(parents=True, exist_ok=True)
    payload = {
        "sku": a.sku, "overall": overall, "checked_at": datetime.now().isoformat(),
        "total_elapsed_s": total, "gates": results,
        "summary": {"fail": n_fail, "over_budget": n_over,
                    "pass": sum(1 for r in results if r["verdict"] == "PASS"),
                    "skip": sum(1 for r in results if r["verdict"] == "SKIP")},
    }
    (gd / "channel_gates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log = [f"视频号带货闸门 · SKU={a.sku} · {datetime.now().isoformat()}",
           f"总体：{overall}  总耗时 {total}s  失败 {n_fail}  超预算 {n_over}", ""]
    for r in results:
        log.append(f"GATE-{r['gate']} {r['name']} [{r['verdict']}] {r['elapsed_s']}s")
        for i in r["issues"]:
            log.append(f"    - {i}")
        for d in r["detail"][:20]:
            log.append(f"    · {json.dumps(d, ensure_ascii=False)}")
    (gd / "channel_gates.log").write_text("\n".join(log), encoding="utf-8")

    print(f"\n总体：{overall}  总耗时 {total}s  → _gate/channel_gates.json")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
