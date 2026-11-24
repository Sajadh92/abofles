#!/usr/bin/env python3
"""Abo Fles session status: where the spend went, what is still open.

    status.py                       newest session ledger
    status.py --all                 every ledger
    status.py --resolve <agent_id> --trace <evidence>  mark one follow-up resolved (the whole id, or a prefix of 6+ characters)
    status.py --unresolve <agent_id>  reopen a follow-up marked resolved
      both take --session <sid> and --trace [text]: one "resolved/reopened by hand" line in the memo Journal; --resolve needs the text (the evidence)
      the id names one agent: a follow-up row of the list below (--unresolve: a resolved one), never a done/high row or a second agent
      exit codes: 0 changed · 1 no match / no ledger / error · 2 usage (missing or empty value, --resolve without evidence, a prefix under
                  6 characters, a prefix of several agents) · 3 nothing to change (already resolved / not resolved / no follow-up) — no trace line is written
    status.py --baseline opus·high  compare against this model·effort (default opus·high; opus.medium, opus-medium too)

Follow-ups = ledger lines with status partial/blocked, confidence low, an
ATTENTION note, or a FAIL verdict not followed by a PASS on the same topic —
and not yet marked resolved.
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
try:
    from abofles_hook import label, context_tokens, context_marks, newest_transcript, newest_state, newest_session_id, usd, fmt_tokens, fmt_usd, PRICE_USD, session_arg
    from abofles_hook import PRICE, EFFORT, units as cost, saving_text, base_label_of, reprice_rows, load_state  # the hook's tables, wording and re-price rule: never a copy here
except Exception:  # pragma: no cover
    context_tokens = context_marks = newest_transcript = None
    newest_state = lambda *a, **k: {}
    newest_session_id = lambda *a, **k: ""
    session_arg = lambda argv, keep=False: None
    label = lambda m, e=None: f"{m} · {e}" if e else m
    usd = lambda m, u: 0.0
    fmt_tokens = str
    fmt_usd = lambda x: f"${x:.2f}"
    PRICE_USD = {}
    PRICE, EFFORT = {}, {}
    cost = lambda model, effort: 0.0
    saving_text = lambda c, b, header=False, money=True, pct=None: f"saved {pct if pct is not None else 0}%"
    base_label_of = lambda rows, fallback="opus": label(fallback)
    reprice_rows = lambda rows, st, sid: rows
    load_state = lambda sid: {}
try:
    from abofles_hook import memo_trace, memo_path, state_path, ledger_path, state_lock, replace_file, last_resolved_note, script_cmd
except Exception:  # pragma: no cover
    import contextlib
    memo_trace = last_resolved_note = script_cmd = None
    ledger_path = lambda sid: Path(os.environ.get("ABOFLES_LEDGER_DIR", Path.home() / ".abofles" / "ledgers")) / f"{re.sub(r'[^A-Za-z0-9_-]', '', sid or '') or 'unknown'}.jsonl"
    state_lock = lambda sid: contextlib.nullcontext()
    replace_file = os.replace

LEDGER_DIR = Path(os.environ.get("ABOFLES_LEDGER_DIR", Path.home() / ".abofles" / "ledgers"))
SIDE_FULL = {"R": "Read", "A": "Analyse", "P": "Plan", "W": "Work", "V": "Verify"}


def resolve_ledger(sess=None):
    """The one ledger of the session a command belongs to: ABOFLES_SESSION, else the command's `--session`, else the newest
    prompt among this project's sessions (state root == cwd). Never the ledger of another project. None when there is none."""
    cands = [os.environ.get("ABOFLES_SESSION"), sess, newest_session_id(os.getcwd(), sess)]
    for c in cands:
        c = "" if "${" in (c or "") else re.sub(r"[^A-Za-z0-9_-]", "", c or "")
        if c and ledger_path(c).exists():
            return ledger_path(c)
    return None


def ledger_files(all_sessions, sess=None):
    if all_sessions:
        return sorted(LEDGER_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    f = resolve_ledger(sess)
    return [f] if f else []


def load(files, reprice=True):
    """(file, rows) per ledger; `reprice`: rows priced again from their transcripts as bill.py does (the hook's reprice_due rule; not written back)."""
    out = []
    for f in files:
        rows = []
        for line in f.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        if reprice:
            rows = reprice_rows(rows, load_state(f.stem), f.stem)
        out.append((f, rows))
    return out


def follow_ups(rows):
    open_fail = {}
    items = []
    for i, r in enumerate(rows):
        if r.get("resolved"):
            continue
        why = []
        if r.get("status") in ("partial", "blocked"):
            why.append(f"status {r['status']}")
        if r.get("confidence") == "low":
            why.append("confidence low")
        if r.get("attention"):
            why.append("ATTENTION note")
        if r.get("verdict") == "FAIL":
            open_fail[r.get("topic")] = i
            why.append("VERDICT FAIL")
        if r.get("verdict") == "PASS" and r.get("topic") in open_fail:
            j = open_fail.pop(r.get("topic"))
            items = [it for it in items if it[0] != j]
        if r.get("status") == "pending":
            why.append("resumed, running" if r.get("resumed") else "still pending (background reply never arrived?)")
        if why:
            items.append((i, r, why))
    return items


def bar(n, width=20, total=None):
    total = total or n or 1
    return "█" * max(1, round(width * n / total)) if n else ""


USAGE = "usage: status.py --resolve <agent_id> --trace <evidence> | --unresolve <agent_id> [--session <sid>] [--trace [text]] | [--all] [--baseline <model·effort>]"
MIN_PREFIX = 6  # a shorter prefix may settle rows of agents the user never meant (the list shows 8 characters)
NO_EVIDENCE = 'abofles: resolve needs evidence — add a note, e.g. --trace "re-checked: <evidence>" or "user answered: <answer>"'


def flag_value(args, flag):
    """The value after `flag`: (found, value). A missing, empty or `--`-led value is a usage error (value None)."""
    if flag not in args:
        return False, None
    i = args.index(flag) + 1
    v = args[i] if i < len(args) else ""
    return True, (v if v.strip() and not v.startswith("--") else None)


def split_baseline(v):
    """`opus·high`, `opus.medium`, `Opus-Low`, `opus high`, `opus` -> (model, effort or ""), lower case (bill.py reads it the same way)."""
    bm, _, be = re.sub(r"[\s.\-·]+", "·", v.strip().lower()).strip("·").partition("·")
    return bm, be


def settle(args, sess=None):
    """--resolve / --unresolve. Exit code: 0 changed, 1 no match / no ledger / error, 2 usage, 3 nothing to change."""
    if "--resolve" in args and "--unresolve" in args:
        print(USAGE)
        return 2
    undo = "--unresolve" in args
    _, key = flag_value(args, "--unresolve" if undo else "--resolve")
    has_sid, sid = flag_value(args, "--session")
    if key is None or (has_sid and sid is None):
        print(USAGE)
        return 2
    if not undo:  # nothing is settled on a bare word: --resolve takes the evidence as the trace text
        _, ev0 = flag_value(args, "--trace")
        if not (ev0 or "").strip():
            print(NO_EVIDENCE)
            return 2
    f = resolve_ledger(sess)  # the same ledger the display shows, so a row listed there can be settled here
    if f is None:
        print(f"abofles: no ledger for session {re.sub(r'[^A-Za-z0-9_-]', '', sid)}" if has_sid else "abofles: no ledger yet — nothing to " + ("reopen." if undo else "resolve."))
        return 1
    sid = f.stem
    with state_lock(sid) as lk:  # the hook appends and rewrites this file under the same lock
        if getattr(lk, "busy", False):  # another writer held it past the wait: never rewritten unlocked
            print("abofles: ledger busy, try again")
            return 1
        try:
            lines = f.read_text().splitlines()
        except Exception as e:
            print(f"abofles: cannot read {f.name}: {e}")
            return 1
        parsed = []  # (raw line, row or None): a half-written line is kept verbatim, never matched
        for l in lines:
            try:
                r = json.loads(l)
            except Exception:
                r = None
            parsed.append((l, r if isinstance(r, dict) else None))
        rows = [r for _, r in parsed if r is not None]
        hit = [r for r in rows if (r.get("agent_id") or "").startswith(key)]
        if not hit:
            print(f"abofles: no follow-up with agent id starting '{key}' in {f.name}")
            return 1
        exact = [r for r in hit if r.get("agent_id") == key]
        if exact:  # the whole id (any length) names its own rows, never a longer id it is a prefix of
            hit = exact
        elif len(key) < MIN_PREFIX:
            print(f"abofles: agent id '{key}' is too short — give the whole id or at least {MIN_PREFIX} characters (the list shows 8)")
            return 2
        ids = sorted({r.get("agent_id") for r in hit})
        if len(ids) > 1:
            print(f"abofles: '{key}' starts {len(ids)} agent ids ({', '.join(i[:12] for i in ids[:5])}) — give a longer one")
            return 2
        if undo:
            hit = [r for r in hit if r.get("resolved")]
            if not hit:
                print(f"abofles: not resolved ({key}…)")
                return 3
            for r in hit:
                r.pop("resolved", None)
        else:
            if all(r.get("resolved") for r in hit):
                print(f"abofles: already resolved ({key}…)")
                return 3
            listed = {id(r) for _, r, _ in follow_ups([r for r in rows if r.get("side") != "Session"])}  # the rows the display lists as open
            hit = [r for r in hit if id(r) in listed]
            if not hit:
                print(f"abofles: {key}… is no open follow-up (no partial/blocked/low/ATTENTION/open FAIL/pending row) — nothing to resolve")
                return 3
            for r in hit:
                r["resolved"] = True
        changed = {id(r) for r in hit}
        out = "".join((json.dumps(r, ensure_ascii=False) if r is not None and id(r) in changed else l) + "\n" for l, r in parsed)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(out)
            replace_file(tmp, f)
        except Exception as e:
            print(f"abofles: cannot write {f.name}: {e}")
            try:
                tmp.unlink()
            except Exception:
                pass
            return 1
    print(f"abofles: {'reopened' if undo else 'resolved'} {len(hit)} entr{'y' if len(hit) == 1 else 'ies'} ({key}…)")
    r0 = hit[0]
    kind = "open FAIL" if r0.get("verdict") == "FAIL" else "ATTENTION" if r0.get("attention") else "escalate"
    what = f"{kind} · {r0.get('topic') or r0.get('side') or '?'}"
    proj = ""  # the session's memo project: only from its state (a folder a `cd` left us in is no project of this session)
    try:
        stj = json.loads(state_path(sid).read_text())
        proj = stj.get("project") or stj.get("root") or ""
    except Exception:
        pass
    ev = ""
    if "--trace" in args and memo_trace:
        i = args.index("--trace") + 1
        ev = args[i] if i < len(args) and not args[i].startswith("--") else ""
        if proj:  # the session's memo project (the work root its edits moved it to, else its launch root); memo_trace creates the memo when this is its first journal line
            memo_trace(proj, sid, f"{'reopened' if undo else 'resolved'} by hand: {what}" + (f" · {ev}" if ev and ev not in what else ""))
        else:
            print("abofles: no session root known — the trace is not written to any memo")
    if last_resolved_note:  # the band's Undo: Claude's last resolve, until the turn after it ends
        if undo:
            last_resolved_note(proj or os.getcwd(), sid)
        else:
            last_resolved_note(proj or os.getcwd(), sid, what, script_cmd("status.py", "--unresolve", key, "--session", sid, "--trace", what))
    return 0


def main():
    args = sys.argv[1:]
    if args[-2:] == ["--session", ""]:  # a command whose ${CLAUDE_SESSION_ID} expanded to nothing
        del args[-2:]
    sess = session_arg(args, keep=True)  # --session stays in args: --resolve / --unresolve read it themselves
    all_sessions = "--all" in args
    baseline = "opus·high"
    if "--baseline" in args:  # a flag with no value (last arg, empty, `--`-led) is a usage error, never an IndexError
        i = args.index("--baseline") + 1
        if i >= len(args) or not args[i].strip() or args[i].startswith("--"):
            print(USAGE)
            sys.exit(2)
        baseline = args[i]
    bm, be = split_baseline(baseline)
    if not be or "--assumed-effort" in args:
        be = newest_state(os.getcwd(), sess).get("effort") or be
    baseline = label(bm, be or "high")
    if "--resolve" in args or "--unresolve" in args:
        sys.exit(settle(args, sess))

    files = ledger_files(all_sessions, sess)
    if not files:
        print("abofles: no agents logged yet." if all_sessions else "abofles: no ledger for this session yet")
        return

    rows = [r for _, rs in load(files) for r in rs if r.get("side") != "Session"]  # a sub session's row is no agent: no count, no cost, no bar
    if not rows:
        print("abofles: no agents in this session yet (only sub sessions; `/abofles:history` lists them).")
        return
    by_model = Counter((r.get("model") or "?") for r in rows)
    by_side = Counter(SIDE_FULL.get(r.get("side") or "?", r.get("side") or "?") for r in rows)
    conf = Counter((r.get("confidence") or "?") for r in rows)
    total = sum(cost(r.get("model"), r.get("effort")) for r in rows)
    base = len(rows) * cost(bm, be or "high")
    scope = "all sessions" if all_sessions else "this session"

    units = f"{total:.1f}" if total < 10 else f"{total:,.0f}"
    real = [r for r in rows if r.get("tokens")]
    if real:
        tokens = sum(r["tokens"] for r in real); rcost = sum(r.get("usd") or 0 for r in real); rbase = sum(r.get("base_usd") or 0 for r in real)
        sv = f" · {saving_text(rcost, rbase)} vs all {base_label_of(real, bm)}" if rbase else ""
        unpriced = f" ({len(real)} of {len(rows)} priced — {len(rows) - len(real)} had no transcript)" if len(real) < len(rows) else ""  # the $ and the saving are of the priced agents only
        print(f"Abo Fles (ابو فليس) — status, {scope}: {len(rows)} agent{'' if len(rows) == 1 else 's'} · {fmt_tokens(tokens)} tokens · {fmt_usd(rcost)}{unpriced}{sv}")
    else:
        print(f"Abo Fles (ابو فليس) — status, {scope}: {len(rows)} agent{'' if len(rows) == 1 else 's'} · {units} u · {saving_text(total, base, money=False)} vs all {baseline}")
    if context_tokens and not all_sessions:
        st0 = load_state(files[0].stem)  # the session of the ledger shown (`--session`), never the newest state of another session
        tp = st0.get("transcript_path") or ""
        tk = context_tokens(tp) if tp and os.path.exists(tp) else None
        if tk is not None:
            marks = context_marks(st0.get("root") or os.getcwd())
            nxt = next((m for m in marks if tk < m * 1000), None)
            print(f"Context:    {tk // 1000}K tokens · " + (f"next compact warning at {nxt}K" if nxt else "past the last mark — run /compact at a task boundary"))
    print()
    print("Agents per model")
    mx = max(by_model.values()) if by_model else 1
    for m in ("haiku", "sonnet", "opus", "fable"):
        if by_model.get(m):
            print(f"  {label(m):<11}{bar(by_model[m], 24, mx)} {by_model[m]}")
    print()
    print("Sides:      " + "  ".join(f"{s} {by_side[s]}" for s in ("Read", "Analyse", "Plan", "Work", "Git", "Verify", "Judge", "Explain") if by_side.get(s)))
    print("Confidence: " + "  ".join(f"{c} {n}" for c, n in conf.most_common()))
    pend = sum(1 for r in rows if r.get("status") == "pending")
    if pend:
        print(f"Pending:    {pend} background agent(s) not yet filled")
    print()
    fu = follow_ups(rows)
    if fu:
        print(f"Open follow-ups ({len(fu)}) — resolve with: /abofles:status --resolve <id> --trace \"re-checked: <evidence>\"")
        for i, r, why in fu:
            aid = (r.get("agent_id") or f"row{i}")[:8]
            print(f"  ⚠ {aid}  {r.get('topic') or '?'} / {SIDE_FULL.get(r.get('side') or '?', r.get('side') or '?')} — {', '.join(why)}")
    else:
        print("Open follow-ups: none")


if __name__ == "__main__":
    main()
