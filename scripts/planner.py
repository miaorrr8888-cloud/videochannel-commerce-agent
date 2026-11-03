#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频号带货 · 发布计划器（publish planner）

回答三个老板真正关心的问题：
  1. 现在能不能挂车？不能的话卡在哪一条？
  2. 这条视频该带 AI 标注吗？怎么标？
  3. 发布时要在页面上勾什么、填什么？

不联网、不调 API，纯本地推算。

用法：
  python planner.py --facts runs/<SKU>/PRODUCT_FACTS.json --sku VC-Q2 [--out runs/<SKU>/05_publish]
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

# 视频号带货权限门槛（2026-11-03 对照《微信小店商家和带货者账号体系管理规则-20260612版本》复核）
#
# 重要修正：自营账号的「短视频挂车」**不是一律 0 粉**。官方原文是
#   「短视频带货：对外展示账号无关注数限制，其余账号需满足 ≥1000 有效关注」
# 也就是说只有「微信小店可对外展示」的自营账号才免门槛，其他自营账号仍需 1000 粉。
# 是否可对外展示无法从规则推断，必须由使用者用 store_displayable 明确告知；
# 未告知时按「未知」处理，不给出「可挂车」的结论。
THRESHOLD = {
    "self_operated": {"short_video": 0, "live": 0, "showcase": 0,
                      "requires_public_store": True,
                      "desc": "小店自营：直播/橱窗无关注数限制；短视频挂车仅「可对外展示账号」免门槛，"
                              "其余需≥1000 有效关注"},
    "talent": {"short_video": 1000, "live": 100, "showcase": 0,
               "desc": "带货者：直播≥100有效关注，短视频挂车≥1000有效关注，橱窗无关注数限制（需100元保证金）"},
    "pre_launch": {"short_video": 0, "live": 0, "showcase": 0,
                   "desc": "未开通带货：无挂车权限"},
}

AI_TYPES = {
    "ai_product_visual": "AI 生成的商品展示画面",
    "ai_usage_effect": "AI 生成的使用效果",
    "ai_scene": "AI 生成的场景演绎",
    "digital_human": "数字人出镜",
    "ai_voiceover": "AI 生成的人物口播",
    "ai_poster": "AI 生成的商品海报/宣传图",
    "other_ai_video": "其他 AI 生成视频画面",
}


def load(p):
    p = Path(p)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def plan(facts):
    mode = str(facts.get("account_mode", "")).strip() or "pre_launch"
    listing = str(facts.get("product_listing_status", "")).strip()
    follows = facts.get("effective_followers")
    ai_used = facts.get("ai_generated_content")
    ai_types = facts.get("ai_content_types") or []
    binding = str(facts.get("video_channel_binding_status", "")).strip()
    displayable = facts.get("store_displayable")

    gates = []

    # --- 1. 权限门槛 ---
    th = THRESHOLD.get(mode, THRESHOLD["pre_launch"])
    if mode == "pre_launch":
        gates.append({"check": "带货权限", "pass": False, "level": "hard",
                      "detail": "账号未开通带货权限 → 视频不能挂商品链接",
                      "action": "二选一：①开通微信小店并确认视频号为「可对外展示」的店铺自营账号"
                                "（该类账号短视频挂车免关注数门槛）"
                                "②攒够 1000 有效关注后申请带货者短视频带货权益"})
    elif mode == "talent":
        if follows is None:
            gates.append({"check": "带货权限", "pass": False, "level": "hard",
                          "detail": "有效关注数未填，无法判定",
                          "action": "填入 effective_followers（注意：只算有效关注，主页总粉丝数不作数）"})
        elif int(follows) < th["short_video"]:
            gates.append({"check": "带货权限", "pass": False, "level": "hard",
                          "detail": f"带货者短视频挂车需 ≥{th['short_video']} 有效关注，当前 {follows}",
                          "action": f"还差 {th['short_video'] - int(follows)} 有效关注；"
                                    "或改走店铺自营路线（需账号可对外展示）"})
        else:
            gates.append({"check": "带货权限", "pass": True, "level": "info",
                          "detail": f"带货者短视频带货门槛已满足（{follows}≥{th['short_video']}）",
                          "action": ""})
    else:  # self_operated
        if displayable is True:
            gates.append({"check": "带货权限", "pass": True, "level": "info",
                          "detail": "店铺自营且账号可对外展示 → 短视频挂车无关注数限制",
                          "action": ""})
        elif displayable is False:
            fallback = th.get("fallback_short_video", 1000)
            if follows is not None and int(follows) >= fallback:
                gates.append({"check": "带货权限", "pass": True, "level": "info",
                              "detail": f"店铺自营但账号不可对外展示，按 ≥{fallback} 有效关注门槛判定，"
                                        f"当前 {follows} 已满足",
                              "action": ""})
            else:
                gates.append({"check": "带货权限", "pass": False, "level": "hard",
                              "detail": f"店铺自营但账号不可对外展示 → 短视频挂车仍需 "
                                        f"≥{fallback} 有效关注，当前 {follows if follows is not None else '未填'}",
                              "action": "把微信小店设为可对外展示，或补足有效关注数"})
        else:
            gates.append({"check": "带货权限", "pass": False, "level": "hard",
                          "detail": "店铺自营，但「账号是否可对外展示」未确认 → 无法判定短视频挂车是否免门槛",
                          "action": "到视频号「创作者中心 → 带货中心 → 带货权限」确认账号类型，"
                                    "并把结果填进 store_displayable（true/false）"})

    # --- 2. 商品上架状态 ---
    if listing == "offline_not_listed":
        gates.append({"check": "商品挂载", "pass": False, "level": "hard",
                      "detail": "货源为线下、未上架微信小店 → 无商品链接可挂",
                      "action": "先把商品上架微信小店并加入橱窗；"
                                "在此之前只能发布『不带商品链接』的内容"})
    elif listing == "listed":
        gates.append({"check": "商品挂载", "pass": True, "level": "info",
                      "detail": "商品已上架，可添加至橱窗后挂载", "action": ""})
    else:
        gates.append({"check": "商品挂载", "pass": False, "level": "hard",
                      "detail": f"商品上架状态未知（product_listing_status={listing or '未填'}）",
                      "action": "补充 product_listing_status"})

    # --- 3. 账号绑定 ---
    if mode == "self_operated":
        if binding != "bound":
            gates.append({"check": "账号绑定", "pass": False, "level": "hard",
                          "detail": f"小店自营需视频号与微信小店完成绑定，当前={binding or '未填'}",
                          "action": "在微信小店后台把该视频号设为关联账号"})
        else:
            gates.append({"check": "账号绑定", "pass": True, "level": "info",
                          "detail": "视频号已绑定小店", "action": ""})

    # --- 4. AI 标注 ---
    mandatory_ai = bool(ai_used) and mode in ("self_operated", "talent")
    ai_plan = None
    if ai_used:
        ai_plan = {
            "ai_label_on_screen": True,
            "ai_label_within_first_5s": True,
            "ai_label_page_checked": True,
            "label_text": "含 AI 生成内容",
            "mandatory": mandatory_ai,
            "types": [AI_TYPES.get(t, t) for t in ai_types],
            "note": ("视频号 2026-08-10 新规：带货短视频含 AI 生成内容必须双标注"
                     "（前 5 秒画面文字 + 发布页勾选），否则限制分发；"
                     "标注后若 AI 内容与实物不符，同样限制分发")
            if mandatory_ai else
            ("当前未挂车，AI 标注为倡导非强制；仍建议标注以降低后续挂车审查风险"),
        }
    else:
        ai_plan = {
            "ai_label_on_screen": False, "ai_label_page_checked": False,
            "ai_label_within_first_5s": False, "mandatory": False,
            "types": [], "label_text": "",
            "note": "全真实拍摄，无需 AI 标注。视频号平台鼓励真实拍摄的带货短视频",
        }

    # --- 5. 总判定 ---
    hard_fails = [g for g in gates if not g["pass"] and g["level"] == "hard"]
    if hard_fails:
        can_attach = False
        status = "READY_NO_LINK" if any(
            g["check"] != "商品挂载" or True for g in hard_fails) else "BLOCKED"
        # 未上架/未开权限 → 仍可发不带链接内容
        status = "READY_NO_LINK"
    else:
        can_attach = True
        status = "READY_WITH_LINK"

    # 🔴 硬红线：未上架时禁止任何"挂车"声明
    if listing == "offline_not_listed":
        can_attach = False
        status = "READY_NO_LINK"

    return {
        "sku": facts.get("sku_id", ""),
        "generated_at": datetime.now().isoformat(),
        "account_mode": mode,
        "account_mode_desc": THRESHOLD.get(mode, {}).get("desc", ""),
        "can_attach_product_link": can_attach,
        "publish_status": status,
        "checks": gates,
        "blockers": [g["detail"] for g in hard_fails],
        "next_actions": [g["action"] for g in hard_fails if g["action"]],
        "ai_declaration": ai_plan,
    }


def main():
    ap = argparse.ArgumentParser(description="视频号带货发布计划器")
    ap.add_argument("--facts", required=True)
    ap.add_argument("--sku", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    facts = load(a.facts)
    if not facts:
        print(f"错误：事实源为空或不存在 {a.facts}")
        return 2
    facts.setdefault("sku_id", a.sku)

    p = plan(facts)

    out = Path(a.out) if a.out else Path(a.facts).resolve().parent / "05_publish"
    out.mkdir(parents=True, exist_ok=True)
    (out / "publish_plan.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人读版
    L = [f"# 视频号发布计划 · {a.sku}", f"生成时间：{p['generated_at']}", "",
         f"**账号模式**：{p['account_mode_desc']}", "",
         f"**能否挂商品链接**：{'能' if p['can_attach_product_link'] else '不能'}", "",
         f"**发布状态**：{p['publish_status']}", "", "## 逐条检查", ""]
    for g in p["checks"]:
        mark = "PASS" if g["pass"] else "BLOCK"
        L.append(f"- [{mark}] **{g['check']}**：{g['detail']}")
        if g["action"]:
            L.append(f"  - → {g['action']}")
    L += ["", "## AI 标注", ""]
    ai = p["ai_declaration"]
    L.append(f"- 是否含 AI 内容：{'是' if ai['types'] or ai.get('mandatory') else '否'}")
    if ai["types"]:
        L.append(f"- AI 类型：{'、'.join(ai['types'])}")
    L.append(f"- 标注是否强制：{'强制（带货场景）' if ai['mandatory'] else '倡导'}")
    if ai.get("label_text"):
        L.append(f"- 画面标注文字：{ai['label_text']}（须置于前 5 秒）")
        L.append(f"- 发布页需勾选：AI 创作声明")
    L += ["", f"> {ai['note']}"]
    if p["blockers"]:
        L += ["", "## 当前阻塞项", ""] + [f"- {b}" for b in p["blockers"]]
    (out / "publish_plan.md").write_text("\n".join(L), encoding="utf-8")

    print(json.dumps({
        "can_attach_product_link": p["can_attach_product_link"],
        "publish_status": p["publish_status"],
        "blockers": p["blockers"],
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
