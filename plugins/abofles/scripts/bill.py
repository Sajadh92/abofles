#!/usr/bin/env python3
"""Abo Fles bill: what the subagents of a session cost, in relative units.

    bill.py                 newest session ledger
    bill.py --all           every ledger
    bill.py --baseline opus·high   compare against this model·effort (default opus·high; opus.medium, opus-medium too)
    bill.py --assumed-effort       mark the baseline effort as assumed (session effort unknown)
    bill.py --history       one line per agent (time, side, status, confidence, model·effort, tokens, $, saved) and the totals line

Cost index = output-price ratio x effort factor. haiku 1, sonnet 20, opus 40,
fable 80 (list price per output token, Haiku = 1). Effort: low 0.5, medium 1,
high 1.5, xhigh 2.5, max 4. Reply length is not known, so one agent = one unit
of work. These are relative numbers for comparing routes, not dollars.
"""
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
try:
    from abofles_hook import label, newest_state, newest_session_id, usd, fmt_tokens, fmt_usd, PRICE_USD, session_arg, history_totals, load_state, reprice_rows
    from abofles_hook import PRICE, EFFORT, units as cost, saving_text, base_label_of  # the hook's tables and wording: never a copy here
except Exception:  # pragma: no cover
    newest_state = lambda *a, **k: {}
    newest_session_id = lambda *a, **k: ""
    session_arg = lambda argv, keep=False: None
    label = lambda m, e=None: f"{m} · {e}" if e else m
    usd = lambda m, u: 0.0
    fmt_tokens = str
    fmt_usd = lambda x: f"${x:.2f}"
    PRICE_USD = {}
    history_totals = lambda rows, bm="opus", be="high": {"spawns": len(rows), "tokens": 0, "usd": 0.0, "base_usd": 0.0, "saved_pct": 0, "open_fail": 0}
    load_state = lambda sid: {}
    reprice_rows = lambda rows, st, sid: rows
    PRICE, EFFORT = {}, {}
    cost = lambda model, effort: 0.0
    saving_text = lambda c, b, header=False, money=True, pct=None: f"saved {pct if pct is not None else 0}%"
    base_label_of = lambda rows, fallback="opus": label(fallback)

LEDGER_DIR = Path(os.environ.get("ABOFLES_LEDGER_DIR", Path.home() / ".abofles" / "ledgers"))
SIDE_FULL = {"R": "Read", "A": "Analyse", "P": "Plan", "W": "Work", "V": "Verify"}
SIDE_ORDER = ["Read", "Analyse", "Plan", "Work", "Verify"]


def load(all_sessions, sess=None):
    if all_sessions:
        files = sorted(LEDGER_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    else:  # this project's session only (state root == cwd), never the newest ledger of another project
        files = []
        for c in (os.environ.get("ABOFLES_SESSION"), sess, newest_session_id(os.getcwd(), sess)):
            c = "" if "${" in (c or "") else re.sub(r"[^A-Za-z0-9_-]", "", c or "")
            if c and (LEDGER_DIR / f"{c}.jsonl").exists():
                files = [LEDGER_DIR / f"{c}.jsonl"]
                break
    rows = []
    for f in files:
        st = load_state(f.stem)
        mine = []
        for line in f.read_text().splitlines():
            try:
                mine.append(json.loads(line))
            except Exception:
                continue
        # a row priced at SubagentStop before its last message was written: priced from the transcript as it is now (not written back),
        # by the hook's own rule (reprice_due), so bill, status and the receipt agree on which rows are read again
        rows += reprice_rows(mine, st, f.stem)
    return rows


USAGE = "usage: bill.py [--all] [--history] [--baseline <model·effort>] [--assumed-effort] [--session <sid>]"


def split_baseline(v):
    """`opus·high`, `opus.medium`, `Opus-Low`, `opus high`, `opus` -> (model, effort or ""), lower case: every separator the docs and the
    overrides use (`Main session = opus.medium`), so a dot never leaves the effort to the session or High."""
    bm, _, be = re.sub(r"[\s.\-·]+", "·", v.strip().lower()).strip("·").partition("·")
    return bm, be


def main():
    args = sys.argv[1:]
    if args[-2:] == ["--session", ""]:  # a command whose ${CLAUDE_SESSION_ID} expanded to nothing
        del args[-2:]
    sess = session_arg(args)
    all_sessions = "--all" in args
    baseline = "opus·high"
    if "--baseline" in args:  # a flag with no value (last arg, empty, `--`-led) is a usage error, never an IndexError
        i = args.index("--baseline") + 1
        if i >= len(args) or not args[i].strip() or args[i].startswith("--"):
            print(USAGE)
            sys.exit(2)
        baseline = args[i]
    assumed = "--assumed-effort" in args
    bm, be = split_baseline(baseline)
    from_session = False
    if not be or assumed:
        se = newest_state(os.getcwd(), sess).get("effort")
        if se:
            be, assumed, from_session = se, False, True
    baseline = label(bm, be or "high")
    rows = load(all_sessions, sess)
    if not rows:
        print("abofles: no agents logged yet." if all_sessions else "abofles: no ledger for this session yet")
        return

    if "--history" in args:
        return print_history(rows, bm, be or "high")
    rows = [r for r in rows if r.get("side") != "Session"]  # a sub session's row is no agent: no count, no cost, no bar (its cost is phase 2)
    if not rows:
        print("abofles: no agents logged yet." if all_sessions else "abofles: no agents in this session yet")
        return

    by_model = Counter()
    by_side = Counter()
    by_topic = Counter()
    conf = Counter()
    status = Counter()
    verdict = Counter()
    open_fail = {}
    total = 0.0
    for r in rows:
        key = label(r.get("model") or "?", r.get("effort") or "?")
        by_model[key] += 1
        by_side[SIDE_FULL.get(r.get("side") or "?", r.get("side") or "?")] += 1
        by_topic[r.get("topic") or "(no header)"] += 1
        conf[r.get("confidence") or "?"] += 1
        status[r.get("status") or "?"] += 1
        total += cost(r.get("model"), r.get("effort"))  # before the verdict block: an open FAIL row's `continue` must not drop its cost (status.py counts it)
        if r.get("verdict"):
            key = r["verdict"] + (" +attention" if r.get("attention") else "")
            if r["verdict"] == "PASS" and open_fail.get(r.get("topic")):
                key = f"PASS (after {open_fail.pop(r.get('topic'))} fix)"
            elif r["verdict"] == "FAIL" and r.get("resolved"):  # closed by hand with evidence: not open
                key = "FAIL (resolved)"
            elif r["verdict"] == "FAIL":
                open_fail[r.get("topic")] = open_fail.get(r.get("topic"), 0) + 1
                continue
            verdict[key] += 1
    base_total = len(rows) * cost(bm, be or "high")

    scope = "all sessions" if all_sessions else "this session"
    print(f"Abo Fles (ابو فليس) — bill for {scope}: {len(rows)} agent{'' if len(rows) == 1 else 's'}")
    print()
    print("By model·effort:")
    mx = max(by_model.values()) if by_model else 1
    for k, n in by_model.most_common():
        print(f"  {k:<20} {n:>3}  {'█' * max(1, round(20 * n / mx))}")
    print()
    print("By side: " + "  ".join(f"{s} {by_side[s]}" for s in SIDE_ORDER + sorted(k for k in by_side if k not in SIDE_ORDER) if s in by_side))
    print("Status: " + "  ".join(f"{c} {n}" for c, n in status.most_common()))
    print("Confidence: " + "  ".join(f"{c} {n}" for c, n in conf.most_common()))
    for t, n in open_fail.items():
        verdict[f"FAIL (open, {t or 'no topic'})"] += n
    if verdict:
        print("Verdicts: " + "  ".join(f"{c} {n}" for c, n in verdict.most_common()))
    bg = [(r.get("topic") or "?", r.get("side") or "?", w) for r in rows if r.get("completed_ts") and r.get("warnings") for w in r["warnings"]]
    if bg:
        print()
        print("Background agent warnings (not shown live — the hook stays silent on SubagentStop):")
        for t, sd, w in bg[:10]:
            print(f"  {t} / {sd}: {w}")
    print()
    print("Top topics:")
    for t, n in by_topic.most_common(8):
        print(f"  {n:>3}  {t}")
    print()
    print(f"Cost index: {total:.1f} units" if total < 10 else f"Cost index: {total:,.0f} units")
    print(f"Same agents all at {baseline}{' (baseline effort assumed — the session effort is not visible)' if assumed else ''}{' (effort read from the session)' if from_session else ''}: {base_total:,.0f} units")
    low_tier = any((r.get("model") in ("haiku", "sonnet")) for r in rows)
    if total > base_total:
        note = f" (agents ran above {baseline}: a higher cell — Extra effort or Fable on 🟠/🔴 plans and work — or an escalation; the saving is in the cheaper reads and checks around them)"
    elif total < base_total or low_tier:
        note = ""
    else:
        note = " (every agent ran on Opus or Fable — under Matrix = quality Opus writes at every tier, and the saving comes from the cheaper Read and Verify cells)"
    print(f"Versus {baseline}: {saving_text(total, base_total, money=False)}{note}")
    print_real_spend(rows, bm)


def print_history(rows, bm, be):
    """One line per agent, oldest first, then the totals (the sums of the pane's Agents tab)."""
    for r in rows:
        t = time.strftime("%m-%d %H:%M", time.localtime(r["ts"])) if r.get("ts") else "--:--"
        real = r.get("tokens") and r.get("base_usd")
        saved = saving_text(r.get("usd") or 0, r["base_usd"], money=False) if real else "saved -"
        side = "🪟 Session" if r.get("side") == "Session" else (r.get("side") or "?")  # the pane's label
        print(f"{t}  {side:<8} {(r.get('status') or '?'):<8} {(r.get('confidence') or '?'):<6} "
              f"{label(r.get('model') or '?', r.get('effort')):<22} {fmt_tokens(r['tokens']) if r.get('tokens') else '-':>6} tok  "
              f"{fmt_usd(r['usd']) if r.get('usd') is not None else '-':>7}  {saved}")
    t = history_totals(rows, bm, be)
    print(f"Total: {t['spawns']} agent{'' if t['spawns'] == 1 else 's'} · {fmt_tokens(t['tokens'])} tokens · {fmt_usd(t['usd'])} "
          + (f"(same tokens on {t['base_label']}: {fmt_usd(t['base_usd'])}) · " if t["base_usd"] else "")
          + (saving_text(t["usd"], t["base_usd"], money=False) if t["base_usd"] else saving_text(0, 0, money=False, pct=t["saved_pct"]))
          + (f" · {t['open_fail']} open FAIL" if t["open_fail"] else ""))


def print_real_spend(rows, base_model):
    """Real tokens and dollars from the rows that carry them (subagent transcripts), priced at list prices."""
    real = [r for r in rows if r.get("tokens")]
    if not real:
        return
    tokens = sum(r["tokens"] for r in real)
    cost = sum(r.get("usd") or 0 for r in real)
    base = sum(r.get("base_usd") or 0 for r in real)
    print()
    print(f"Real spend ({len(real)} of {len(rows)} agents had a transcript): {fmt_tokens(tokens)} tokens · {fmt_usd(cost)}")
    print(f"Same tokens on {base_label_of(real, base_model)}: {fmt_usd(base)} · {saving_text(cost, base)}" if base else "")


if __name__ == "__main__":
    main()
