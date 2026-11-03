#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频号带货 · 状态机与结果页生成器 v1.0

总控专用，幂等文件操作，不派子 Agent。

用法：
  python state.py init     --sku VC-Q2 --account-mode pre_launch [--root runs]
  python state.py set      --sku VC-Q2 --agent RESEARCH --status DONE --artifact 01_research/PRODUCT_FACTS.json
  python state.py gate     --sku VC-Q2 --qa PASS --reason "5/5 通过"
  python state.py champion --sku VC-Q2 --hook "..." --video 07_media/composited/final.mp4
  python state.py publish  --sku VC-Q2 --status READY_NO_LINK --evidence 05_publish/
  python state.py blocked  --sku VC-Q2 --field 商品页价格 --needed-by COPY --severity hard
  python state.py metrics  --sku VC-Q2 --total-time 250 --max-concurrency 3
  python state.py status   --sku VC-Q2
  python state.py render   --sku VC-Q2
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROLES = ["ORCH", "RESEARCH", "COMMERCE-COPY", "CREATIVE", "CRITIC", "GATE", "PUB"]
ACCOUNT_MODES = {
    "pre_launch": "未开通带货（先出内容养号）",
    "self_operated": "小店自营（0 粉可挂车）",
    "talent": "纯达人带货（短视频需 1000 有效粉）",
}
PUBLISH_STATES = {
    "PENDING": "待发布", "READY_NO_LINK": "可发布·不带商品链接",
    "READY_WITH_LINK": "可发布·挂车", "BLOCKED": "不可发布",
    "PUBLISHED": "已发布",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_dir(root, sku):
    return Path(root).resolve() / sku


def load(root, sku):
    p = run_dir(root, sku) / "run_state.json"
    if not p.exists():
        print(f"错误：{p} 不存在，先执行 init")
        sys.exit(2)
    return json.loads(p.read_text(encoding="utf-8"))


def save(root, sku, st):
    d = run_dir(root, sku)
    for sub in ("01_research", "02_commerce", "03_creative", "04_critic",
                "05_publish", "07_media/composited", "_gate"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    st["updated_at"] = now()
    p = d / "run_state.json"
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------- commands
def cmd_init(a):
    d = run_dir(a.root, a.sku)
    if (d / "run_state.json").exists() and not a.force:
        print(f"已存在：{d/'run_state.json'}（加 --force 覆盖）")
        return 0
    st = {
        "sku": a.sku, "platform": "微信视频号", "account_mode": a.account_mode,
        "account_mode_label": ACCOUNT_MODES.get(a.account_mode, a.account_mode),
        "created_at": now(), "updated_at": now(),
        "agents": {r: {"status": "PENDING", "artifact": "", "note": ""} for r in ROLES},
        "gate": {"qa": "PENDING", "reason": ""},
        "champion": {}, "publish": {"status": "PENDING", "binding": "", "evidence": ""},
        "blocked": [], "metrics": {}, "history": [],
    }
    p = save(a.root, a.sku, st)
    print(f"初始化完成：{p}")
    print(f"账号模式：{a.account_mode} — {ACCOUNT_MODES.get(a.account_mode,'')}")
    return 0


def cmd_set(a):
    st = load(a.root, a.sku)
    if a.agent not in st["agents"]:
        print(f"错误：未知角色 {a.agent}，可选 {list(st['agents'])}")
        return 2
    st["agents"][a.agent] = {"status": a.status, "artifact": a.artifact,
                             "note": a.note, "at": now()}
    st["history"].append(f"{now()} {a.agent} → {a.status} {a.artifact}")
    save(a.root, a.sku, st)
    print(f"{a.agent} → {a.status}  {a.artifact}")
    return 0


def cmd_gate(a):
    st = load(a.root, a.sku)
    st["gate"] = {"qa": a.qa, "reason": a.reason, "at": now()}
    st["history"].append(f"{now()} GATE → {a.qa} {a.reason}")
    save(a.root, a.sku, st)
    print(f"闸门 → {a.qa}  {a.reason}")
    return 0


def cmd_champion(a):
    st = load(a.root, a.sku)
    st["champion"] = {k: getattr(a, k.replace("-", "_")) for k in st.get("champion", {}).keys()
                      or ["hook", "video", "cover", "title"]}
    for k in ("hook", "video", "cover", "title", "cta", "subtitle"):
        if getattr(a, k, ""):
            st["champion"][k] = getattr(a, k)
    st["history"].append(f"{now()} 冠军已定")
    save(a.root, a.sku, st)
    print(f"冠军：{st['champion']}")
    return 0


def cmd_publish(a):
    st = load(a.root, a.sku)
    st["publish"] = {"status": a.status, "binding": a.binding,
                     "evidence": a.evidence, "at": now()}
    st["history"].append(f"{now()} PUBLISH → {a.status}")
    save(a.root, a.sku, st)
    print(f"发布状态 → {a.status}")
    return 0


def cmd_blocked(a):
    st = load(a.root, a.sku)
    st["blocked"].append({"field": a.field, "needed_by": a.needed_by,
                          "severity": a.severity, "reason": a.reason, "at": now()})
    save(a.root, a.sku, st)
    print(f"BLOCKED 记录：{a.field}（{a.severity}）")
    return 0


def cmd_metrics(a):
    st = load(a.root, a.sku)
    m = st.setdefault("metrics", {})
    for k in ("total_time", "agent_time", "machine_gate_time", "wait_time",
              "max_concurrency", "critic_qa_time", "orchestration", "performance"):
        v = getattr(a, k, "")
        if v != "" and v is not None:
            m[k.upper()] = v
    # 自动判效率
    if a.max_concurrency != "":
        try:
            if int(a.max_concurrency) < 3:
                m["ORCHESTRATION"] = "ORCHESTRATION_FAIL"
        except Exception:
            pass
    save(a.root, a.sku, st)
    print(f"指标：{json.dumps(m, ensure_ascii=False)}")
    return 0


def cmd_status(a):
    st = load(a.root, a.sku)
    print(f"SKU {st['sku']} · {st['platform']} · {st.get('account_mode_label','')}")
    print(f"闸门：{st['gate']['qa']}  {st['gate'].get('reason','')}")
    print(f"发布：{st['publish']['status']} — {PUBLISH_STATES.get(st['publish']['status'],'')}")
    print("角色：")
    for r, v in st["agents"].items():
        print(f"  {r:<14} {v['status']:<10} {v['artifact']}")
    if st["blocked"]:
        print("BLOCKED：")
        for b in st["blocked"]:
            print(f"  [{b['severity']}] {b['field']} (需 {b['needed_by']})")
    if st.get("metrics"):
        print(f"指标：{json.dumps(st['metrics'], ensure_ascii=False)}")
    return 0


def cmd_render(a):
    st = load(a.root, a.sku)
    d = run_dir(a.root, a.sku)
    g = {}
    gp = d / "_gate" / "channel_gates.json"
    if gp.exists():
        try:
            g = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            g = {}

    qa = st["gate"]["qa"]
    badge_cls = {"PASS": "ok", "REVISION": "warn", "REJECT": "bad",
                 "FAIL": "bad", "PENDING": "idle"}.get(qa, "idle")

    # 闸门行
    gate_rows = ""
    for r in g.get("gates", []):
        v = r.get("verdict", "?")
        cls = {"PASS": "ok", "FAIL": "bad", "SKIP": "idle", "ERROR": "bad"}.get(v, "idle")
        iss = "<br>".join(esc(i) for i in r.get("issues", [])) or "—"
        gate_rows += (
            f"<tr><td>GATE-{r.get('gate')}<br><span class='dim'>{esc(r.get('name',''))}</span></td>"
            f"<td><span class='pill {cls}'>{esc(v)}</span></td>"
            f"<td class='num'>{r.get('elapsed_s','-')}s<span class='dim'> /{r.get('budget_s','-')}s</span></td>"
            f"<td class='iss'>{iss}</td></tr>"
        )
    if not gate_rows:
        gate_rows = "<tr><td colspan='4' class='dim'>尚未运行闸门 — 执行 scripts/channel_gates.py</td></tr>"

    # BLOCKED 行
    blocked_rows = "".join(
        f"<tr><td>{esc(b['field'])}</td><td><span class='pill warn'>{esc(b['severity'])}</span></td>"
        f"<td>{esc(b.get('needed_by',''))}</td></tr>" for b in st["blocked"]
    ) or "<tr><td colspan='3' class='dim'>无</td></tr>"

    # 角色行
    agent_rows = "".join(
        f"<tr><td>{esc(r)}</td><td><span class='pill "
        f"{'ok' if v['status']=='DONE' else 'idle'}'>{esc(v['status'])}</span></td>"
        f"<td class='dim'>{esc(v['artifact'])}</td></tr>"
        for r, v in st["agents"].items()
    )

    ch = st.get("champion", {}) or {}
    m = st.get("metrics", {}) or {}
    mrows = "".join(
        f"<div class='metric'><span class='k'>{esc(k)}</span>"
        f"<span class='v'>{esc(v)}</span></div>" for k, v in m.items()
    ) or "<div class='dim'>未写入</div>"

    am = st.get("account_mode", "")
    next_step = {
        "pre_launch": "账号未开通带货 → 当前只能发布不带商品链接的内容；"
                      "攒够 1000 有效粉 或 开通小店自营后切挂车",
        "self_operated": "小店自营 → 短视频可 0 粉挂车",
        "talent": "纯达人 → 短视频挂车需 ≥1000 有效粉丝",
    }.get(am, "")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>视频号带货结果面板 · {esc(st['sku'])}</title>
<style>
:root{{--bg:#f7f8fa;--card:#fff;--line:#e6e8eb;--tx:#1a1d21;--dim:#8a9099;
--ok:#0a7d3f;--okbg:#e8f6ed;--bad:#c0392b;--badbg:#fdecea;
--warn:#9a6b00;--warnbg:#fff6e0;--idle:#6b7280;--idlebg:#f0f1f3;--accent:#0a5fd4}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}}
.wrap{{max-width:1100px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin-bottom:14px}}
.card h2{{font-size:14px;margin:0 0 12px;font-weight:600;letter-spacing:.3px}}
.pill{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}}
.pill.ok{{background:var(--okbg);color:var(--ok)}}
.pill.bad{{background:var(--badbg);color:var(--bad)}}
.pill.warn{{background:var(--warnbg);color:var(--warn)}}
.pill.idle{{background:var(--idlebg);color:var(--idle)}}
.top{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.top .big{{font-size:24px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--dim);font-weight:500;font-size:12px}}
.dim{{color:var(--dim);font-size:12px}}
.num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.iss{{color:var(--bad);font-size:12px;max-width:520px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}}
.metric{{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px}}
.metric .k{{display:block;color:var(--dim);font-size:11px}}
.metric .v{{font-weight:600;font-variant-numeric:tabular-nums}}
.next{{background:#eef4ff;border-left:3px solid var(--accent);padding:10px 12px;
border-radius:0 6px 6px 0;font-size:13px}}
</style></head><body><div class="wrap">
<h1>视频号带货 · 结果面板</h1>
<div class="sub">SKU {esc(st['sku'])} · {esc(st['platform'])} · 更新 {esc(st['updated_at'])}</div>

<div class="card"><div class="top">
  <span class="big">{esc(qa)}</span>
  <span class="pill {badge_cls}">{esc(st['gate'].get('reason',''))}</span>
  <span class="dim">账号模式：{esc(st.get('account_mode_label',''))}</span>
  <span class="dim">发布：{esc(PUBLISH_STATES.get(st['publish']['status'],st['publish']['status']))}</span>
</div>
<div class="next" style="margin-top:12px">{esc(next_step)}</div>
</div>

<div class="card"><h2>机器闸门（确定性脚本，可复现）</h2>
<table><tr><th>闸门</th><th>结论</th><th>耗时</th><th>命中</th></tr>{gate_rows}</table>
{'<div class="dim" style="margin-top:8px">总体：'+esc(g.get("overall",""))+' · 总耗时 '+esc(g.get("total_elapsed_s",""))+'s</div>' if g else ''}
</div>

<div class="card"><h2>冠军素材</h2>
<table>
<tr><th>Hook</th><td>{esc(ch.get('hook','—'))}</td></tr>
<tr><th>标题</th><td>{esc(ch.get('title','—'))}</td></tr>
<tr><th>视频</th><td class="dim">{esc(ch.get('video','—'))}</td></tr>
</table></div>

<div class="card"><h2>Agent 产出</h2>
<table><tr><th>角色</th><th>状态</th><th>产物</th></tr>{agent_rows}</table></div>

<div class="card"><h2>BLOCKED（禁止任何 Agent 自行补参数）</h2>
<table><tr><th>字段</th><th>级别</th><th>需谁提供</th></tr>{blocked_rows}</table></div>

<div class="card"><h2>效率</h2><div class="metrics">{mrows}</div></div>

</div></body></html>"""

    out = d / "result_panel.html"
    out.write_text(html, encoding="utf-8")
    print(f"结果页：{out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="视频号带货状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--sku", required=True)
        p.add_argument("--root", default="runs")

    p = sub.add_parser("init"); common(p)
    p.add_argument("--account-mode", default="pre_launch", choices=list(ACCOUNT_MODES))
    p.add_argument("--force", action="store_true")
    p.set_defaults(f=cmd_init)

    p = sub.add_parser("set"); common(p)
    p.add_argument("--agent", required=True, choices=ROLES)
    p.add_argument("--status", required=True,
                   choices=["PENDING", "RUNNING", "DONE", "FAILED"])
    p.add_argument("--artifact", default=""); p.add_argument("--note", default="")
    p.set_defaults(f=cmd_set)

    p = sub.add_parser("gate"); common(p)
    p.add_argument("--qa", required=True, choices=["PASS", "REVISION", "REJECT", "PENDING"])
    p.add_argument("--reason", default=""); p.set_defaults(f=cmd_gate)

    p = sub.add_parser("champion"); common(p)
    for k in ("hook", "title", "cover", "video", "cta", "subtitle"):
        p.add_argument(f"--{k}", default="")
    p.set_defaults(f=cmd_champion)

    p = sub.add_parser("publish"); common(p)
    p.add_argument("--status", default="PENDING", choices=list(PUBLISH_STATES))
    p.add_argument("--binding", default=""); p.add_argument("--evidence", default="")
    p.set_defaults(f=cmd_publish)

    p = sub.add_parser("blocked"); common(p)
    p.add_argument("--field", required=True)
    p.add_argument("--needed-by", dest="needed_by", default="")
    p.add_argument("--severity", default="hard", choices=["hard", "soft"])
    p.add_argument("--reason", default=""); p.set_defaults(f=cmd_blocked)

    p = sub.add_parser("metrics"); common(p)
    for k in ("total-time", "agent-time", "machine-gate-time", "wait-time",
              "max-concurrency", "critic-qa-time", "orchestration", "performance"):
        p.add_argument(f"--{k}", dest=k.replace("-", "_"), default="")
    p.set_defaults(f=cmd_metrics)

    p = sub.add_parser("status"); common(p); p.set_defaults(f=cmd_status)
    p = sub.add_parser("render"); common(p); p.set_defaults(f=cmd_render)

    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
