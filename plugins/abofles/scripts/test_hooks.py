#!/usr/bin/env python3
"""Unit tests for hooks/abofles_hook.py. Run: python3 scripts/test_hooks.py"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "abofles_hook.py"


import importlib.util as _ilu
_hs = _ilu.spec_from_file_location("abofles_hook_t", str(Path(__file__).resolve().parent.parent / "hooks" / "abofles_hook.py"))
_H = _ilu.module_from_spec(_hs)
_hs.loader.exec_module(_H)

def run(mode, payload, ledger):
    env = dict(os.environ, ABOFLES_LEDGER_DIR=ledger)
    if "cwd" not in payload and os.environ.get("ABOFLES_TEST_CWD"):
        payload = dict(payload, cwd=os.environ["ABOFLES_TEST_CWD"])  # never let a hook write a memo into the plugin dir
    r = subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return r.stdout  # plain-text hook output (SessionStart)


def agent(model=None, effort=None, stype="abofles:abofles-reader", prompt="abofles: Unit Test / R · T2\nq\nLine cap: 40", resp=None, cwd=""):
    ti = {"subagent_type": stype, "prompt": prompt, "description": "t"}
    if model: ti["model"] = model
    if effort: ti["effort"] = effort
    d = {"session_id": "test", "cwd": cwd, "tool_name": "Agent", "tool_input": ti}
    if resp is not None: d["tool_response"] = resp
    return d


def memo_health_tests(check):
    """memo_health (archive, index, dedupe, caps, throttle), the PreCompact / PostCompact hooks and `Other items`. Own temp projects and state dir;
    the environment is put back at the end."""
    import contextlib
    import threading
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None
    saved = {k: os.environ.get(k) for k in ("ABOFLES_STATE_DIR", "ABOFLES_CONFIG", "ABOFLES_SESSION")}
    with tempfile.TemporaryDirectory() as hh:
        os.environ["ABOFLES_STATE_DIR"] = str(Path(hh, "st")); os.environ["ABOFLES_CONFIG"] = str(Path(hh, "config.json"))
        os.environ.pop("ABOFLES_SESSION", None)
        spec = _ilu.spec_from_file_location("abofles_hook_health", str(HOOK)); HM = _ilu.module_from_spec(spec); spec.loader.exec_module(HM)
        led = str(Path(hh, "led")); Path(led).mkdir()
        sdir = Path(hh, "st")
        hrun = lambda mode, payload: run(mode, payload, led)
        nroot = [0]

        def mkroot(overrides=None):
            nroot[0] += 1
            r = Path(hh, f"P{nroot[0]}"); r.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=r, capture_output=True)
            if overrides is not None:
                r.joinpath("CLAUDE.md").write_text("## abofles overrides\n" + "".join(f"- {l}\n" for l in overrides))
            return r
        ago = lambda days=0, hours=0: time.time() - days * 86400 - hours * 3600
        stamp = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
        mainf = lambda r: Path(r, ".claude", "abofles-memo.md")
        mdir = lambda r: Path(r, ".claude", "abofles-memo.d")
        archf = lambda r: mdir(r) / "archive.md"
        arch_txt = lambda r: archf(r).read_text() if archf(r).exists() else ""

        def nline(n, text, sid, r, done=None, age=0):  # done = days since it was resolved (None: open)
            return (f"{n}. [{'x' if done is not None else ' '}] {stamp(ago(age))} · {text} #{n:04x} (s:{sid[:8]} p:{HM.proj_tag(r)})"
                    + (f" → resolved {stamp(ago(done))}: done" if done is not None else ""))

        def put_main(r, needs=(), decisions=()):
            sec = HM.memo_read(str(r)); sec["Needs you"] = "\n".join(needs); sec["Decisions"] = "\n".join(decisions)
            HM.memo_write(str(r), sec)

        def put_sess(r, sid, now="", journal=(), decisions=(), age=0, index=True):  # age in days = how long the file sat idle
            sec = {"Now": now, "Journal": "\n".join(journal), "Decisions": "\n".join(decisions)}
            HM.sess_write(str(r), sid, sec)
            p = HM.session_memo_path(str(r), sid); t = ago(age); os.utime(p, (t, t))
            if index:
                m = HM.memo_read(str(r)); HM.index_set(m, sid, HM.index_line(sid, sec, stamp(t))); HM.memo_write(str(r), m)
            return p

        def snap(r):
            return {str(p.relative_to(r)): p.read_bytes() for p in Path(r, ".claude").rglob("*") if p.is_file()}
        needs_of = lambda r: [l for l in HM.memo_read(str(r))["Needs you"].splitlines() if l.strip()]
        index_of = lambda r: [k for k, _ in HM.index_entries(HM.memo_read(str(r)))]
        live_of = lambda r: sorted(p.stem for p in HM.session_files(str(r)))
        zero = lambda res: not any(res[k] for k in ("archived_items", "archived_sessions", "deduped", "trimmed"))

        # ── health: defaults and override keys ──
        check("health: defaults are Memo on compact auto, Archive items 7, Archive sessions 3, Other items hide",
              HM.OVERRIDE_KEYS["Memo on compact"]["default"] == "auto" and HM.OVERRIDE_KEYS["Archive items"]["default"] == "7"
              and HM.OVERRIDE_KEYS["Archive sessions"]["default"] == "3" and HM.OVERRIDE_KEYS["Other items"]["default"] == "hide")
        r0 = mkroot()
        check("health: no override file -> auto / 7 / 3 / hide", HM.memo_on_compact(str(r0)) == "auto" and HM.number_override(str(r0), "Archive items") == 7
              and HM.number_override(str(r0), "Archive sessions") == 3 and not HM.other_items_shown(str(r0)))
        r0 = mkroot(["Memo on compact = OFF", "Other items = Show", "Archive items = 2", "Archive sessions = 1 day"])
        check("health: override values are read (case-insensitive select, leading digits of a number)", HM.memo_on_compact(str(r0)) == "off" and HM.other_items_shown(str(r0))
              and HM.number_override(str(r0), "Archive items") == 2 and HM.number_override(str(r0), "Archive sessions") == 1)
        r0 = mkroot(["Memo on compact = maybe", "Other items = everything"])
        check("health: a select value that is not an option falls back to the default", HM.memo_on_compact(str(r0)) == "auto" and not HM.other_items_shown(str(r0)))
        pk = {k["key"]: k for k in HM.pane_overrides(str(r0), "x")["keys"]}
        check("health: the pane override table lists the four new keys", all(k in pk for k in ("Memo on compact", "Archive items", "Archive sessions", "Other items")))

        # ── health: no memo costs nothing and creates nothing ──
        rn = mkroot()
        reg0 = HM.registry_read()
        res = HM.memo_health(str(rn), sid="nomemo01")
        check("health: a project with no memo -> zero result, no .claude folder, no registry entry", zero(res) and not Path(rn, ".claude").exists() and HM.registry_read() == reg0 and HM.health_info(str(rn)) is None)

        # ── health 1: 51 Needs-you items, 47 old and resolved ──
        r1 = mkroot()
        openn = {5, 20, 33, 51}
        lines1 = [nline(n, f"item text {n} about cap", "ownerAAA", r1, done=None if n in openn else 9, age=10) for n in range(1, 52)]
        put_main(r1, lines1)
        before = [l for l in lines1 if "[x]" in l]
        t0 = time.time()
        res = HM.memo_health(str(r1), now=t0, sid="callerXX")
        nl = needs_of(r1); at = arch_txt(r1)
        check("health: 51 items, 47 old resolved -> 4 open remain in the main memo, numbered 1..4", res["archived_items"] == 47 and len(nl) == 4 and all("[ ]" in l for l in nl)
              and [re.match(r"\d+", l).group(0) for l in nl] == ["1", "2", "3", "4"])
        check("health: ... the 4 open items keep their text, ids and tags, in order", [re.sub(r"^\d+\.", "", l) for l in nl] == [re.sub(r"^\d+\.", "", lines1[n - 1]) for n in sorted(openn)])
        check("health: ... the 47 resolved lines are in archive.md verbatim (ids, tags, resolve notes: no text lost)", all(l in at for l in before) and len(before) == 47 and "(s:ownerAAA p:" in at and "#0001 " in at)
        check("health: ... archive.md names the date and the group", f"## {time.strftime('%Y-%m-%d', time.localtime(t0))} · resolved items" in at and at.startswith("# Abo Fles memo archive"))
        hi = HM.health_info(str(r1))
        check("health: health_info says what the run moved (47 resolved items -> archive.md)", hi and hi["archived_items"] == 47 and hi["line"].startswith("memo tidied: 47 resolved items") and "abofles-memo.d/archive.md" in hi["line"])
        rb = HM.resume_block(str(r1), session_id="callerXX-1")
        check("health: the resume block carries a `health` line", "- `health` → memo tidied: 47 resolved items" in rb)
        st_ = {}; HM.refresh_pane(str(r1), st_, "callerXX-1", rows=[])
        check("health: the pane payload carries memo_health", (st_["pane"].get("memo_health") or {}).get("archived_items") == 47 and st_["pane"]["memo_health"]["line"].startswith("memo tidied"))
        before_bytes = snap(r1)
        res2 = HM.memo_health(str(r1), sid="callerXX")
        check("health: a second run moves nothing and changes no file byte", zero(res2) and snap(r1) == before_bytes)
        check("health: ... and the pane still shows the last tidy-up (kept 24 h)", HM.health_info(str(r1))["archived_items"] == 47)
        reg = HM.registry_read(); key1 = os.path.realpath(r1)
        reg[key1]["last_health"]["at"] = int(time.time()) - 25 * 3600; Path(sdir, "memo-index.json").write_text(json.dumps(reg))
        hi2 = HM.health_info(str(r1))
        check("health: ... and forgets it after 24 h (counts 0, line empty)", hi2["archived_items"] == 0 and hi2["line"] == "" and "`health`" not in HM.resume_block(str(r1), session_id="callerXX-1"))
        check("health: no temp file is left behind", not list(Path(r1, ".claude").rglob("*.tmp")))

        # boundaries around Archive items = 7 days, open items and unstamped lines
        r1b = mkroot()
        put_main(r1b, [nline(1, "resolved 6d23h ago", "ownerAAA", r1b, done=6 + 23 / 24, age=9), nline(2, "resolved 7d1h ago", "ownerAAA", r1b, done=7 + 1 / 24, age=9),
                       nline(3, "open for 30 days", "ownerAAA", r1b, age=30), "4. [x] hand written, no stamp, no tag"])
        res = HM.memo_health(str(r1b), sid="callerXX")
        nl = needs_of(r1b)
        check("health: boundary — 6d23h resolved stays, 7d1h goes; an open item is never archived; a resolved line with no date stays",
              res["archived_items"] == 1 and any("resolved 6d23h" in l for l in nl) and any("open for 30 days" in l for l in nl) and any("hand written" in l for l in nl)
              and not any("resolved 7d1h" in l for l in nl) and "resolved 7d1h" in arch_txt(r1b) and [re.match(r"\d+", l).group(0) for l in nl] == ["1", "2", "3"])

        # ── health 2: idle session files ──
        r2 = mkroot()
        put_main(r2, [nline(1, "owner decision: keep the idle one?", "idleopen", r2, age=5)])
        put_sess(r2, "idle0001", now="Written   2020-01-01 00:00\nTask      old work of idle0001\nNext      nothing", journal=["- 01-01 10:00 · 1 Agent · done: old thing"], age=5)
        mdir(r2).joinpath("idle0001.compact.md").write_text("# Compact summary\nraw old summary\n")
        put_sess(r2, "idle0003", now="Written   2020-01-01 00:00\nTask      older work", age=3 + 1 / 24)
        put_sess(r2, "idle0004", now="Written   2020-01-01 00:00\nTask      never indexed", age=6, index=False)
        put_sess(r2, "idleopen", now="Written   2020-01-01 00:00\nTask      has an open item", age=5)
        put_sess(r2, "caller01", now="Written   2020-01-01 00:00\nTask      the caller", age=5)
        put_sess(r2, "fresh001", now="Written   2020-01-01 00:00\nTask      fresh", age=1 / 24)
        put_sess(r2, "idle0002", now="Written   2020-01-01 00:00\nTask      almost three days", age=3 - 1 / 24)
        res = HM.memo_health(str(r2), sid="caller01-aaaa-bbbb")
        a2 = arch_txt(r2)
        check("health: idle sessions past 3 days with nothing open -> abofles-memo.d/archive/<sid8>.md (the 3d1h and the never-indexed one too); a compact summary an older hook left in the project goes to the state folder",
              res["archived_sessions"] == 3 and all(mdir(r2).joinpath("archive", f"{k}.md").exists() for k in ("idle0001", "idle0003", "idle0004"))
              and not any(mdir(r2).joinpath(f"{k}.md").exists() for k in ("idle0001", "idle0003", "idle0004")) and HM.compact_path("idle0001").read_text().count("raw old summary") == 1 and not mdir(r2).joinpath("archive", "idle0001.compact.md").exists()
              and not mdir(r2).joinpath("idle0001.compact.md").exists() and "old work of idle0001" in mdir(r2).joinpath("archive", "idle0001.md").read_text())
        check("health: ... their index lines are in the `## Archive` list of archive.md, first group, linking archive/<sid8>.md",
              all(f"](archive/{k}.md)" in a2 for k in ("idle0001", "idle0003", "idle0004")) and re.search(r"\n## Archive\n- \[idle0001\]\(archive/idle0001\.md\).* · archived \d{4}-\d\d-\d\d", a2)
              and a2.index("## Archive") == min(a2.index(h) for h in re.findall(r"^## .+$", a2, re.M)) and "task: old work of idle0001" in a2)
        check("health: ... the calling session, a session with an open item of its own, a fresh one and one just under 3 days stay",
              live_of(r2) == ["caller01", "fresh001", "idle0002", "idleopen"] and set(index_of(r2)) == {"caller01", "fresh001", "idle0002", "idleopen"})
        check("health: archived_sids lists them", HM.archived_sids(str(r2)) == {"idle0001", "idle0003", "idle0004"})
        blk = HM.resume_block(str(r2), session_id="caller01-aaaa-bbbb") or ""
        check("health: archived sessions do not appear in the resume block", "idle0001" not in blk and "old work of idle0001" not in blk and "idle0003" not in blk and "idle0004" not in blk)
        snap2 = snap(r2)
        res2 = HM.memo_health(str(r2), sid="caller01-aaaa-bbbb")
        check("health: a second run changes nothing", zero(res2) and snap(r2) == snap2 and not list(Path(r2, ".claude").rglob("*.tmp")))
        HM.memo_resolve(str(r2), 1, "done", "idleopen-1")
        res3 = HM.memo_health(str(r2), sid="caller01-aaaa-bbbb", now=time.time() + 8 * 86400)
        check("health: once its item is resolved (and old enough) the idle session is archived too", mdir(r2).joinpath("archive", "idleopen.md").exists() and not mdir(r2).joinpath("idleopen.md").exists() and "idleopen" not in index_of(r2)
              and "caller01" in live_of(r2))
        # an archived session that wakes up again: its new file and the archived one are both kept
        put_sess(r2, "idle0001", now="Written   2020-01-01 00:00\nTask      came back", age=10)
        HM.memo_health(str(r2), sid="caller01-aaaa-bbbb")
        back = mdir(r2).joinpath("archive", "idle0001.md").read_text()
        check("health: a session archived twice keeps both texts (joined, nothing overwritten)", "old work of idle0001" in back and "came back" in back)

        m = HM.memo_read(str(r2)); m["Sessions"] = "- [idle0001](abofles-memo.d/idle0001.md) · 11-01 10:00 · task: old work of idle0001 · receipts: 1\n" + m["Sessions"]; HM.memo_write(str(r2), m)
        blk = HM.resume_block(str(r2), session_id="caller01-aaaa-bbbb") or ""
        check("health: ... even when an old index line for an archived session is still in the main memo", "idle0001" not in blk and "old work of idle0001" not in blk)

        # ── health 3: the index from the live files ──
        r3 = mkroot()
        put_main(r3)
        for k, h in (("s3aaaaaa", 5), ("s3bbbbbb", 1), ("s3cccccc", 3)):
            put_sess(r3, k, now=f"Written   2020-01-01 00:00\nTask      task of {k}", age=h / 24, index=False)
        m = HM.memo_read(str(r3)); m["Sessions"] = "- [ghost000](abofles-memo.d/ghost000.md) · 11-01 10:00 · receipts: 0"; HM.memo_write(str(r3), m)
        res = HM.memo_health(str(r3), sid="s3bbbbbb")
        check("health: the index is rebuilt from the live files, newest first; no orphan file, no line without a file", index_of(r3) == ["s3bbbbbb", "s3cccccc", "s3aaaaaa"] and res["orphans"] == 4 and "ghost000" not in mainf(r3).read_text()
              and set(index_of(r3)) == set(live_of(r3)))
        check("health: ... each index line carries the task of its file", all(f"task: task of {k}" in mainf(r3).read_text() for k in ("s3aaaaaa", "s3bbbbbb", "s3cccccc")))
        s3 = snap(r3)
        check("health: ... and a second run keeps the order and every byte", zero(HM.memo_health(str(r3), sid="s3bbbbbb")) and snap(r3) == s3)

        # ── health 4: duplicate decisions ──
        r4 = mkroot()
        dq = lambda days, q, a: f"- {stamp(ago(days))} · by:you · {q} → {a}"
        put_main(r4, decisions=[f"- {stamp(ago(6))} · by:you · note → use IQD (s:dup00001 p:{HM.proj_tag(r4)})", f"- {stamp(ago(1))} · by:you · note → use IQD (s:dup00001 p:{HM.proj_tag(r4)})",
                                f"- {stamp(ago(2))} · by:you · note → use USD (s:dup00001 p:{HM.proj_tag(r4)})"])
        put_sess(r4, "dupXXXXX", decisions=[dq(5, "Cap in IQD only?", "Yes"), dq(5, "Other question?", "A")])
        put_sess(r4, "dupYYYYY", decisions=[dq(2, "Cap in IQD only?", "Yes"), dq(2, "Other question?", "B")])
        put_sess(r4, "dupZZZZZ", decisions=[dq(4, "Cap in IQD only?", "Yes"), dq(4, "Cap in IQD only?", "No")])
        res = HM.memo_health(str(r4), sid="dupXXXXX")
        sx, sy, sz = (HM.sess_read(str(r4), k)["Decisions"] for k in ("dupXXXXX", "dupYYYYY", "dupZZZZZ"))
        main_dec = HM.memo_read(str(r4))["Decisions"]
        check("health: exact-duplicate decisions across files -> the oldest stays, the others are dropped", res["deduped"] == 3 and "Cap in IQD only? → Yes" in sx and "Cap in IQD only? → Yes" not in sy and "Cap in IQD only? → Yes" not in sz)
        check("health: ... same question with another answer is not a duplicate; two main-memo notes with the same text keep the older", "Cap in IQD only? → No" in sz and "Other question? → A" in sx and "Other question? → B" in sy
              and main_dec.count("use IQD") == 1 and stamp(ago(6)) in main_dec and "use USD" in main_dec)
        a4 = arch_txt(r4)
        check("health: ... the dropped lines are in archive.md under `duplicate decisions` (no text lost)", "· duplicate decisions" in a4 and a4.count("Cap in IQD only? → Yes") == 2 and a4.count("use IQD") == 1)
        s4 = snap(r4)
        check("health: ... a second run changes nothing", zero(HM.memo_health(str(r4), sid="dupXXXXX")) and snap(r4) == s4)

        # ── health 5: size caps ──
        r5 = mkroot()
        notes = [f"- {stamp(ago(30 - i / 10))} · by:you · note → settled point number {i:03d} " + "x" * 60 + f" (s:cap00001 p:{HM.proj_tag(r5)})" for i in range(130)]
        put_main(r5, [nline(1, "recent resolved", "cap00001", r5, done=1, age=2), nline(2, "owner decision: still open?", "cap00001", r5, age=2)], notes)
        jl = [f"- {stamp(ago(0, 100 - i))} · {i:02d} Agents · done: journal entry number {i:02d} " + "y" * 330 for i in range(60)][::-1]  # newest first, over SESSION_CAP
        put_sess(r5, "cap00001", now="Written   2020-01-01 00:00\nTask      big journal", journal=jl, age=0)
        res = HM.memo_health(str(r5), sid="cap00001")
        sz_main, sz_sess = mainf(r5).stat().st_size, mdir(r5).joinpath("cap00001.md").stat().st_size
        check("health: main memo <= 8 KB and session file <= SESSION_CAP (16 KB) after the run", sz_main <= 8 * 1024 and sz_sess <= HM.SESSION_CAP == 16 * 1024 and res["trimmed"] > 0)
        a5 = arch_txt(r5); js = HM.sess_read(str(r5), "cap00001")["Journal"].splitlines()
        check("health: session cap — the oldest Journal lines left, the newest kept (in order), the cut ones are in archive.md", js == jl[:len(js)] and 0 < len(js) < 60 and f"journal entry number {int(js[-1].split('number ')[1][:2]) - 1:02d}" in a5
              and "· journal · cap00001" in a5 and all(l in a5 for l in jl[len(js):]))
        left_notes = HM.memo_read(str(r5))["Decisions"].splitlines()
        check("health: main cap — resolved items go first, then the oldest notes; open items stay; the notes left are the newest; nothing is lost",
              any("owner decision: still open?" in l for l in needs_of(r5)) and "recent resolved" in a5 and left_notes == notes[len(notes) - len(left_notes):] and len(left_notes) < 130
              and all(l in a5 or l in left_notes for l in notes) and "· notes" in a5)
        s5 = snap(r5)
        check("health: caps — a second run changes nothing", zero(HM.memo_health(str(r5), sid="cap00001")) and snap(r5) == s5)
        rcap = mkroot()
        put_main(rcap, [nline(1, "open one", "capx0001", rcap, age=1)])
        put_sess(rcap, "capx0001", now="Task      " + "z" * 7000, journal=["- 01-01 10:00 · only receipt"], age=0)
        try:
            HM.memo_health(str(rcap), sid="capx0001"); okc = True
        except Exception:
            okc = False
        check("health: a Now block bigger than the cap by itself cannot loop or crash (only Journal lines move)", okc and "Task      " + "z" * 7000 in HM.sess_read(str(rcap), "capx0001")["Now"])

        # ── health: overrides change the day thresholds; bad values never archive everything ──
        def thresholds(overrides):
            r = mkroot(overrides)
            put_main(r, [nline(1, "resolved 3 days ago", "ovr00001", r, done=3, age=4), nline(2, "resolved 2 hours ago", "ovr00001", r, done=2 / 24, age=4), nline(3, "open item", "ovr00001", r, age=4)])
            put_sess(r, "ovrOld01", now="Task      two days idle", age=2); put_sess(r, "ovrNew01", now="Task      two hours idle", age=2 / 24)
            res = HM.memo_health(str(r), sid="callerXX")
            return r, res
        r7, res = thresholds(["Archive items = 2", "Archive sessions = 1"])
        check("health: Archive items = 2 / Archive sessions = 1 -> a 3-day-old resolved item and a 2-day-idle session are archived, the 2-hour-old ones are not",
              res["archived_items"] == 1 and "resolved 3 days ago" in arch_txt(r7) and res["archived_sessions"] == 1 and live_of(r7) == ["ovrNew01"] and any("2 hours ago" in l for l in needs_of(r7)))
        r7, res = thresholds([])
        check("health: default thresholds (7 / 3 days) keep the 3-day item and the 2-day session", res["archived_items"] == 0 and res["archived_sessions"] == 0 and live_of(r7) == ["ovrNew01", "ovrOld01"])
        r7, res = thresholds(["Archive items = 30", "Archive sessions = 30"])
        check("health: raising the thresholds keeps more", zero(res))
        for bad in ("abc", "0", "-3", "-1 day", "none", "off", "99999999999999999999"):
            r7, res = thresholds([f"Archive items = {bad}", f"Archive sessions = {bad}"])
            check(f"health: `Archive items/sessions = {bad}` does not crash and does not archive a 2-hour-old item or a 2-hour-idle session", res["archived_sessions"] == 0 and any("resolved 2 hours ago" in l for l in needs_of(r7)) and "ovrNew01" in live_of(r7)
                  and any("open item" in l for l in needs_of(r7)))

        # ── health: crash safety ──
        r6 = mkroot()
        lines6 = [nline(n, f"crash item {n}", "crash001", r6, done=None if n > 5 else 9, age=10) for n in range(1, 8)]
        put_main(r6, lines6)
        main_before = mainf(r6).read_bytes()
        real_replace = HM.replace_file
        def failing(tmp, dest):
            if str(dest).endswith("abofles-memo.md"):
                raise OSError("disk full")
            return real_replace(tmp, dest)
        HM.replace_file = failing
        try:
            try:
                HM.memo_health(str(r6), sid="callerXX")
            except Exception:
                pass
        finally:
            HM.replace_file = real_replace
        at6 = arch_txt(r6)
        check("health: a write that fails at the main memo leaves it whole (old bytes) and the moved text is already in archive.md", mainf(r6).read_bytes() == main_before and all(f"crash item {n}" in at6 for n in range(1, 6)))
        HM.memo_health(str(r6), sid="callerXX")
        check("health: ... the next run finishes the job: 2 open items left, each moved line once in archive.md, no temp file", len(needs_of(r6)) == 2 and all(arch_txt(r6).count(f"crash item {n} ") == 1 for n in range(1, 6)) and not list(Path(r6, ".claude").rglob("*.tmp")))
        # two runs at once (two hooks)
        r6b = mkroot()
        put_main(r6b, [nline(n, f"race item {n}", "race0001", r6b, done=None if n > 20 else 9, age=10) for n in range(1, 25)])
        code = ("import importlib.util as u,sys;s=u.spec_from_file_location('h',sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);m.memo_health(sys.argv[2],sid='callerXX')")
        ps = [subprocess.Popen([sys.executable, "-c", code, str(HOOK), str(r6b)], env=dict(os.environ)) for _ in range(4)]
        [p_.wait() for p_ in ps]
        check("health: four processes at once (the memo lock) -> 4 items left, each of the 20 moved once, no temp file", len(needs_of(r6b)) == 4 and all(arch_txt(r6b).count(f"race item {n} ") == 1 for n in range(1, 21)) and not list(Path(r6b, ".claude").rglob("*.tmp")))
        # budget
        r6c = mkroot()
        put_main(r6c, [nline(n, f"slow item {n}", "slow0001", r6c, done=9, age=10) for n in range(1, 5)])
        s6c = snap(r6c)
        res = HM.memo_health(str(r6c), sid="callerXX", budget=1e-9)
        check("health: a budget that runs out before the writes -> nothing written, nothing recorded (the next start tries again)", zero(res) and snap(r6c) == s6c and not (HM.registry_read().get(os.path.realpath(r6c)) or {}).get("last_health_at"))
        if _fcntl:
            lk = HM.memo_lock(str(r6c)); lk.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lk.path, "w"); _fcntl.flock(fh, _fcntl.LOCK_EX)
            t0 = time.time(); got = HM.health_run(str(r6c), "callerXX", budget=0.2)
            waited = time.time() - t0
            check("health: a held memo lock -> health_run gives up after its budget (fails open, returns None)", got is None and waited < 2.0 and snap(r6c) == s6c)
            _fcntl.flock(fh, _fcntl.LOCK_UN); fh.close()
            for _ in range(100):  # the abandoned worker finishes its run on this throw-away project
                if (HM.registry_read().get(os.path.realpath(r6c)) or {}).get("last_health_at"):
                    break
                time.sleep(0.05)
        ru = mkroot()
        put_main(ru); put_sess(ru, "UpperCa1", now="Task      capital letters in the id", age=1 / 24)
        check("health: a session id with capital letters is read back from the index like any other (index_entries and SESSION_FILE_RE agree)", "UpperCa1" in index_of(ru) and "UpperCa1" in live_of(ru))
        # an unreadable session file
        r8 = mkroot()
        put_main(r8)
        put_sess(r8, "badutf01", now="Task      fine", age=0)
        mdir(r8).joinpath("badutf01.md").write_bytes(b"\xff\xfe\x00 not utf8 \xc3\x28")
        put_sess(r8, "good0001", now="Task      fine too", age=0)
        try:
            HM.memo_health(str(r8), sid="good0001"); okb = True
        except Exception:
            okb = False
        check("health: a session file that cannot be read does not crash the run, and its index line is not dropped silently", okb and mdir(r8).joinpath("badutf01.md").exists() and "badutf01" in index_of(r8))

        # ── others: Other items = hide | show ──
        ro = mkroot()
        A, B = "othA0001-aaaa", "othB0001-bbbb"
        pt = HM.proj_tag(ro)
        put_main(ro, [f"1. [ ] {stamp(ago(0, 4))} · owner decision: A question about caps #0001 (s:othA0001 p:{pt})",
                      f"2. [ ] {stamp(ago(0, 3))} · owner decision: B secret rollout plan #0002 (s:othB0001 p:{pt})",
                      f"3. [ ] {stamp(ago(0, 2))} · owner decision: C other repo thing #0003 (s:othC0001 p:Elsewhere)",
                      f"4. [ ] {stamp(ago(0, 1))} · owner decision: A second question #0004 (s:othA0001 p:{pt})"],
                 [f"- {stamp(ago(0, 5))} · by:you · note → project wide note (s:othB0001 p:{pt})", f"- {stamp(ago(0, 5))} · by:you · note → elsewhere note (s:othC0001 p:Elsewhere)"])
        put_sess(ro, "othA0001", now="Written   2020-01-01 00:00\nTask      A task", decisions=[f"- {stamp(ago(0, 6))} · by:you · A's own question? → A answer"])
        put_sess(ro, "othB0001", now="Written   2020-01-01 00:00\nTask      B task", decisions=[f"- {stamp(ago(0, 6))} · by:you · B's private question? → B answer"])
        d = HM.resume_data(str(ro), A, "project")
        blk = HM.resume_block(str(ro), session_id=A)
        check("others: hide (default) -> the resume data lists only this session's own items and counts the others (1 session, 1 project)",
              len(d["open"]) == 2 and d["open"] == d["open_own"] and d["others"] == {"sessions": 1, "projects": 1})
        check("others: hide -> the resume block names none of the others' items, says how many there are and how to show them",
              "A question about caps" in blk and "A second question" in blk and "B secret rollout plan" not in blk and "C other repo thing" not in blk and " · other" not in blk
              and "`others` → 1 open item of other sessions, 1 of other projects" in blk and "Other items = show" in blk)
        pr = HM.pane_resume(str(ro), A)
        check("others: hide -> the items left out travel apart in `others_items` (number + text), for the pane's Show others, never in the fill",
              sorted(n["n"] for n in pr["others_items"]) == [2, 3] and any("B secret rollout plan" in n["text"] for n in pr["others_items"]) and "B secret rollout plan" not in (pr["needs_fill"] or ""))
        check("others: hide -> the pane payload has only own needs (none flagged other) and the counts", [n["n"] for n in pr["needs"]] == [1, 4] and not any(n.get("other") for n in pr["needs"]) and pr["others"] == {"sessions": 1, "projects": 1} and pr["needs_open"] == 2)
        pdx = HM.pane_decisions(str(ro), A)
        txtd = json.dumps({k: pdx[k] for k in ("you", "judge")})
        check("others: hide -> the pane decisions leave out another session's decisions and another project's notes, counted in `others`",
              "A's own question?" in txtd and "B's private question" not in txtd and "elsewhere note" not in txtd and "project wide note" in txtd and pdx["others"] == {"sessions": 1, "projects": 1})
        check("others: hide -> the rows left out travel apart in `others_rows`, for the pane's Show others (not flagged, not counted in n_you)",
              any("B's private question" in x["q"] for x in pdx["others_rows"]) and len(pdx["others_rows"]) == 2 and not any(x.get("other") for x in pdx["others_rows"]))
        ge = HM.guide_entries(str(ro), {}, A)
        gtxt = json.dumps(ge)
        check("others: hide -> no guide row, fill or recheck names another session's or project's item", "B secret rollout plan" not in gtxt and "C other repo thing" not in gtxt and "othB0001" not in gtxt and "A question about caps" in gtxt)
        check("others: hide -> Check all (needs_fill) covers this session's items only", "A question about caps" in (pr["needs_fill"] or "") and "A second question" in pr["needs_fill"] and "B secret" not in pr["needs_fill"] and "C other" not in pr["needs_fill"])
        mdir_ro = snap(ro)
        ro_c = Path(ro, "CLAUDE.md"); ro_c.write_text("## abofles overrides\n- Other items = show\n")
        d = HM.resume_data(str(ro), A, "project")
        blk = HM.resume_block(str(ro), session_id=A)
        check("others: show -> the resume data lists all 4 open items (own marked in open_own) and no counts", len(d["open"]) == 4 and len(d["open_own"]) == 2 and d["others"] is None)
        check("others: show -> the resume block lists the others with ` · other`, the own ones without",
              re.search(r"B secret rollout plan · other", blk) and re.search(r"C other repo thing · other", blk) and "A question about caps · other" not in blk and "A second question · other" not in blk and "`others` →" not in blk)
        pr = HM.pane_resume(str(ro), A)
        by_n = {n["n"]: n for n in pr["needs"]}
        check("others: show -> the pane payload flags the others `other: true` and not the own items; open count stays 2",
              by_n[2].get("other") is True and by_n[3].get("other") is True and not by_n[1].get("other") and not by_n[4].get("other") and pr["needs_open"] == 2 and not pr["others"])
        check("others: show -> a guide fill (Check all, rows) still never names another session's or project's item",
              "B secret" not in (pr["needs_fill"] or "") and "C other" not in (pr["needs_fill"] or "") and "B secret rollout plan" not in json.dumps([e["fill"] for e in HM.guide_entries(str(ro), {}, A)]) + json.dumps([e.get("recheck") for e in HM.guide_entries(str(ro), {}, A)]))
        pdx = HM.pane_decisions(str(ro), A)
        flagged = [x for x in pdx["you"] if x.get("other")]
        check("others: show -> the pane decisions list the others flagged `other: true`, no counts", len(flagged) == 2 and not any(x.get("other") for x in pdx["you"] if "A's own" in x["q"]) and pdx["others"] is None)
        ro_c.write_text("## abofles overrides\n- Other items = hide\n")
        only_b = HM.resume_data(str(ro), "othB0001-b", "session")
        check("others: scope session (Resume = session) lists no others, hide or show", only_b["open"] == only_b["open_own"] and len(only_b["open"]) == 1)
        ro_c.unlink()

        # ── compact: parse_compact_summary ──
        SUMMARY = ("This session is being continued from a previous conversation.\n\nSummary:\n"
                   "1. Primary Request and Intent:\n   The user wants a surcharge cap on instalments.\n\n2. Key Technical Concepts:\n   - Aqsati rules\n\n"
                   "3. Files and Code Sections:\n   - AqsatiRules.cs\n      - Added the cap\n\n4. Errors and fixes:\n   - none yet\n\n5. Problem Solving:\n   Solved the rounding.\n\n"
                   "6. All user messages:\n   - cap the surcharge\n   - ok go on\n\n7. Pending Tasks:\n   - Add FE tests\n      - for the badge\n   - Update the docs page\n\n"
                   "8. Current Work:\n   Capping the surcharge in AqsatiRules.cs. It was half done when the context filled.\n\n9. Optional Next Step:\n   Run the FE suite and fix what breaks.\n")
        p = HM.parse_compact_summary(SUMMARY)
        check("compact: parse — task = first sentence of Current Work, left = the top-level Pending Tasks, next = the first line of Optional Next Step",
              p and p["task"] == "Capping the surcharge in AqsatiRules.cs." and p["left"] == ["Add FE tests", "Update the docs page"] and p["next"] == "Run the FE suite and fix what breaks." and p["done"] == [])
        check("compact: parse — no headings -> None", HM.parse_compact_summary("Just some prose.\nNothing numbered here.\nCurrent work was good.") is None)
        check("compact: parse — empty string, None, whitespace -> None, no crash", all(HM.parse_compact_summary(x) is None for x in ("", None, "   \n\n")))
        p = HM.parse_compact_summary("7. Pending Tasks:\n   - None\n\n8. Current Work:\n   Working on the cap.\n\n9. Optional Next Step:\n   Run tests.\n")
        check("compact: parse — Pending Tasks `None` gives no Left line (task and next stay)", p and p["left"] == [] and p["task"] == "Working on the cap." and p["next"] == "Run tests.")
        for none in ("None", "None.", "none", "- None.", "N/A", "Nothing", "No pending tasks", "- "):
            q = HM.parse_compact_summary(f"7. Pending Tasks:\n   {none}\n8. Current Work:\n   x y.\n")
            check(f"compact: parse — Pending Tasks `{none.strip()}` -> no Left line", q and q["left"] == [])
        q = HM.parse_compact_summary("7. Pending Tasks: None\n8. Current Work: Doing it. More.\n9. Optional Next Step: Ship it\n")
        check("compact: parse — inline text after the colon works", q and q["left"] == [] and q["task"] == "Doing it." and q["next"] == "Ship it")
        q = HM.parse_compact_summary("## 7. Pending Tasks:\n- A thing\n## 8. Current Work:\nNow.\n**9. Optional Next Step:**\nNext one\n")
        check("compact: parse — markdown headings (## and **) work", q and q["left"] == ["A thing"] and q["task"] == "Now." and q["next"] == "Next one")
        q = HM.parse_compact_summary("7. Pending tasks:\n- No retry on the payment call yet: add it\n- Nothing else\n")
        check("compact: parse — a task that merely starts with `No ...` is a task, not `None`", q and q["left"] == ["No retry on the payment call yet: add it"])
        q = HM.parse_compact_summary("7. Pending Tasks:\n   - Deliver the closing message (section 8).\n   - Run the live check\n8. Current Work:\n   DCMP-969 has just been completed:\n   - details\n9. Optional Next Step:\n   Write the closing message from section 8 as plain text. The wrap_up result said: write it.\n")
        check("compact: parse — a note for Claude's next turn is no Left or Next; only notes in Next give the first pending task; a lead-in's colon goes", q["task"] == "DCMP-969 has just been completed" and q["left"] == ["Run the live check"] and q["next"] == "Run the live check")
        q = HM.parse_compact_summary("8. Current Work:\n   Waiting for the user's /compact before DRP-431.\n   Building DRP-430 now. More of it.\n9. Optional Next Step:\n   DCMP-968 after compact. The wrap_up asked about F157. The work stopped at the compact boundary.\n")
        check("compact: parse — a wait on the compact is no Task; Next keeps its sentences before the first note", q["task"] == "Building DRP-430 now." and q["next"] == "DCMP-968 after compact.")
        q = HM.parse_compact_summary("9. Optional Next Step:\n   \"Your closing message is your answer\"\n   Ship it\n")
        check("compact: parse — a quoted line is no Next", q["next"] == "Ship it")
        q = HM.parse_compact_summary("7. Pending Tasks:\n   - Run the live check\n9. Optional Next Step:\n   Write the closing message from section 8 as plain text.\n</summary>\n")
        check("compact: parse — the summary's closing tag is no Next", q["next"] == "Run the live check")
        q = HM.parse_compact_summary("7. Pending Tasks:\n   - Parse the compact boundary row\n8. Current Work:\n   Fix wrap_up in the pane (section 3).\n9. Optional Next Step:\n   Add system-reminder handling\n")
        check("compact: parse — a real task may name those words", q["task"] == "Fix wrap_up in the pane (section 3)." and q["left"] == ["Parse the compact boundary row"] and q["next"] == "Add system-reminder handling")
        q = HM.parse_compact_summary("1. Primary Request and Intent:\n   only a request\n")
        check("compact: parse — only a request heading (nothing to write) -> None", q is None)
        q = HM.parse_compact_summary("7. Pending Tasks:\n" + "".join(f"   - task {i}\n" for i in range(30)) + "8. Current Work:\n   " + "w" * 500 + "\n")
        check("compact: parse — at most 10 left items, each clipped to 160 chars", q and len(q["left"]) == 10 and len(q["task"]) <= 160)

        # ── compact: hooks.json wiring ──
        hj = json.loads((HOOK.parent / "hooks.json").read_text())["hooks"]
        cmds = lambda ev: [h["command"] for g in hj.get(ev, []) for h in g["hooks"]]
        check("compact: hooks.json wires PreCompact -> precompact and PostCompact -> postcompact (with a timeout)",
              any(c.endswith(" precompact") for c in cmds("PreCompact")) and any(c.endswith(" postcompact") for c in cmds("PostCompact"))
              and all(h.get("timeout") for ev in ("PreCompact", "PostCompact") for g in hj[ev] for h in g["hooks"]))

        # ── an agent's reply with no header goes back once ──
        wa_ = {"session_id": "hdr1", "agent_id": "a1", "agent_type": "abofles:abofles-worker", "last_assistant_message": "Items 1-9 are done."}
        o_ = hrun("stop", wa_)
        check("header gate: an abofles agent's reply with no STATUS goes back once", (o_ or {}).get("decision") == "block" and "STATUS: done|partial|blocked" in o_["reason"])
        check("header gate: never twice; never with the header, on an API error, or for another agent type", all(hrun("stop", dict(wa_, **k)) is None for k in ({"stop_hook_active": True}, {"last_assistant_message": "STATUS: done\nCONFIDENCE: high\n- x"}, {"last_assistant_message": "API Error: Can't reach the API server"}, {"agent_type": "general-purpose"}, {"last_assistant_message": ""})))
        check("explainer: a QUESTION_AR line is its question", HM.judge_fields("STATUS: done\nQUESTION_AR: هل ندمج؟\n")[1] == "هل ندمج؟")
        check("explainer: QUESTION beside QUESTION_AR is one question, two QUESTION lines two", len(HM.explainer_blocks("QUESTION: merge?\nQUESTION_AR: ندمج؟\n")) == 1 and len(HM.explainer_blocks("QUESTION 1: a?\nQUESTION 2: b?\n")) == 2)
        # ── settings and noise stay out of Decisions ──
        rg = mkroot()
        SG = "gateAAA1-1111-2222"
        ask_ = lambda q, a, labels=(): hrun("decision", {"session_id": SG, "cwd": str(rg), "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": q, "options": [{"label": x} for x in labels]}]}, "tool_response": {"answers": {q: a}}})
        hrun("start", {"session_id": SG, "cwd": str(rg)})
        ask_("Abo Fles agent gate: how many agents may this task start?", "Auto (recommended)", ("Auto (recommended)", "This task: 3 agents", "Main session only"))
        ask_("Abo Fles agent budget: raise it?", "Raise by 2", ("Raise by 2", "Keep"))
        ask_("Should the text Abo Fles agent gate stay in the docs?", "Yes, keep it", ("Yes, keep it", "No"))
        ask_("Cap in IQD only?", "Yes, IQD only")
        dec_ = HM.sess_read(str(rg), SG)["Decisions"]
        check("decisions: agent gate and budget answers are not saved; a real answer is, also one that names the gate", "Cap in IQD only?" in dec_ and "how many agents" not in dec_ and "raise it" not in dec_ and "stay in the docs" in dec_)
        check("gate_answer_line: a real decision tagged [Agents] stays", not HM.gate_answer_line("- 11-05 11:04 · by:you · [Agents] default ask or auto? → auto"))
        s_ = HM.sess_read(str(rg), SG)
        s_["Decisions"] += "\n- 11-05 11:00 · by:you · [Agent gate] How many agents may this session agent? → Auto (recommended)\n- 11-05 11:04 · by:you · How many agents should the pool hold? → Auto-scale to 5"
        HM.sess_write(str(rg), SG, s_)
        mm_ = HM.memo_read(str(rg))
        mm_["Decisions"] = "- 10-13 22:14 · [Agents] Abo Fles agent gate — task: x → This task: 4 agents\n- 10-13 22:59 · [Table test] Do you see a table? → Yes\n- 10-14 04:14 · note → keep this"
        HM.memo_write(str(rg), mm_)
        hl_ = HM.memo_health(str(rg), sid=SG)
        arch_ = Path(rg, ".claude", "abofles-memo.d", "archive.md").read_text()
        mm_, dec_ = HM.memo_read(str(rg)), HM.sess_read(str(rg), SG)["Decisions"]
        check("health: agent gate answers older hooks saved move to archive.md; a real decision with like words stays", hl_["gate_answers"] == 2 and "[Agent gate]" not in dec_ and "This task: 4 agents" not in mm_["Decisions"] and "agent gate answers" in arch_ and "This task: 4 agents" in arch_ and "Auto-scale to 5" in dec_ and "keep this" in mm_["Decisions"])
        check("health: a tidy-up that moved gate answers says so in the resume block's health line", "agent gate answer" in (HM.health_info(str(rg)) or {}).get("line", ""))
        ref_ = next(d["ref"] for d in HM.memo_apply(str(rg), "list", SG, curator=True).value["decisions"] if "Table test" in d["q"])
        r_ = HM.memo_apply(str(rg), "drop_decision", SG, curator=True, ref=ref_, reason="test line")
        arch_ = Path(rg, ".claude", "abofles-memo.d", "archive.md").read_text()
        check("drop-decision: the line leaves, archive.md keeps it with the reason; the memo agent may call it", bool(r_) and "Table test" not in HM.memo_read(str(rg))["Decisions"] and "dropped: test line" in arch_ and "Table test" in arch_ and "drop-decision" in HM.MEMO_AGENT_CMDS)
        check("drop-decision: no reason, no drop; no such ref, a refusal", not HM.memo_apply(str(rg), "drop_decision", SG, curator=True, ref="main@10-14T04:14", reason=" ") and not HM.memo_apply(str(rg), "drop_decision", SG, curator=True, ref="main@01-01T00:00", reason="x"))
        # ── a closed session's items, Judge notes below the tier ──
        rz = mkroot()
        ZC, ZL, ZM = "closedAA-1111-2222", "liveBBBB-1111-2222", "meCCCCCC-1111-2222"
        HM.memo_needs(str(rz), ZC, "DRP-407 owner decision", str(rz))
        HM.memo_needs(str(rz), ZL, "a live session's question", str(rz))
        s_ = HM.sess_read(str(rz), ZC)
        s_["Now"] = "Written   2026-11-04 15:05\nTask      none — session closed; handed over"
        HM.sess_write(str(rz), ZC, s_)
        s_ = HM.sess_read(str(rz), ZL)
        s_["Now"] = "Written   2026-11-08 15:05\nTask      still at it"
        HM.sess_write(str(rz), ZL, s_)
        ln_ = [l for l in HM.memo_read(str(rz))["Needs you"].splitlines() if "[ ]" in l]
        own_, oth_ = HM.need_split(ln_, ZM, str(rz))
        check("needs: a closed session's item counts as this session's; a live session's stays information", len(own_) == 1 and "DRP-407" in own_[0] and len(oth_) == 1 and "live session" in oth_[0])
        check("needs: ... the same from its own session; a session with no file yet is not closed", HM.need_split(ln_, ZC, str(rz))[0] == own_ and not HM.session_closed(str(rz), "nofile00"))
        check("needs: scope session keeps only this session's own items, and a question's answer never settles a closed session's", HM.need_split(ln_, ZM, str(rz), "session") == ([], []) and HM.memo_resolve_matching(str(rz), "DRP-407 owner decision", "yes", ["yes", "no"], ZM) == 0)
        check("move: to its own folder changes nothing, the session file stays", HM.memo_move(str(rz), str(rz), ZC) is False and HM.session_memo_path(str(rz), ZC).exists() and "DRP-407" in HM.memo_read(str(rz))["Needs you"])
        r_ = HM.memo_apply(str(rz), "resolve", ZM, ref="1", note="decided by the owner")
        check("needs: ... and this session may resolve it; a live session's item stays refused", bool(r_) and not HM.memo_apply(str(rz), "resolve", ZM, ref="2", note="x"))
        s_ = HM.sess_read(str(rz), ZL)
        HM.sess_write(str(rz), ZL, s_)
        zf_ = Path(rz, ".claude", "abofles-memo.d", f"{ZL[:8]}.md")
        os.utime(zf_, (time.time() - 4 * 86400,) * 2)
        check("needs: four idle days are not closed (a long weekend)", not HM.session_closed(str(rz), ZL[:8]))
        os.utime(zf_, (time.time() - 15 * 86400,) * 2)
        check("needs: 15 idle days are", HM.session_closed(str(rz), ZL[:8]))
        s_ = HM.sess_read(str(rz), ZL); s_["Now"] = "Written   2026-11-08 15:05\nTask      Fix session ended hook"; HM.sess_write(str(rz), ZL, s_)
        check("needs: a Task that only names the words is not closed", not HM.session_closed(str(rz), ZL[:8]))
        rj = mkroot()
        SJ = "judgeAA1-1111-2222"
        HM.memo_needs(str(rj), SJ, "judge decided (🟠 Sensitive): q? → a — check", "P")
        HM.memo_needs(str(rj), SJ, "judge decided (🔴 Critical): q2? → a2 — check", "P")
        HM.memo_needs(str(rj), SJ, "a real owner decision", "P")
        hl_ = HM.memo_health(str(rj), sid=SJ)
        nd_ = HM.memo_read(str(rj))["Needs you"]
        check("health: an open 🟠 Judge note leaves Needs you for archive.md; 🔴 and real items stay, renumbered", "🟠" not in nd_ and "1. [ ]" in nd_ and "🔴" in nd_ and "a real owner decision" in nd_ and "judge notes" in Path(rj, ".claude", "abofles-memo.d", "archive.md").read_text() and hl_["judge_notes"] == 1)
        # ── Verify says how old it is, Tree says what it cut ──
        s_ = HM.sess_read(str(rg), SG)
        s_["Now"] = "Written   2026-11-08 12:31 · from receipt\nTask      old\nVerify    suite green"
        HM.sess_write(str(rg), SG, s_)
        hrun("postcompact", {"session_id": SG, "cwd": str(rg), "trigger": "manual", "compact_summary": "8. Current Work:\n   New task.\n"})
        check("compact: a kept Verify says when it was checked", "Verify    suite green (as of 11-08 12:31)" in HM.sess_read(str(rg), SG)["Now"])
        hrun("postcompact", {"session_id": SG, "cwd": str(rg), "trigger": "manual", "compact_summary": "8. Current Work:\n   Newer task.\n"})
        check("compact: ... and a second compact keeps that first time", HM.sess_read(str(rg), SG)["Now"].count("(as of ") == 1)
        with tempfile.TemporaryDirectory() as tr_:
            subprocess.run(["git", "init", "-q", tr_], check=True)
            for i_ in range(25):
                Path(tr_, f"f{i_:02d}.txt").write_text("x")
            tl_ = HM.git_tree(tr_)
            check("git_tree: 20 lines, then how many more", len(tl_) == 21 and tl_[-1].strip() == "… +5 more")
        # ── compact: postcompact ──
        rc = mkroot()
        CA = "cmpAAAA1-1111-2222"
        HM.memo_journal(str(rc), CA, HM.parse_receipt("🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → started the cap\n- `check` → ok"))
        sf_ = lambda r, sid: Path(r, ".claude", "abofles-memo.d", f"{sid[:8]}.md")
        cf_ = lambda r, sid: HM.compact_path(sid)
        out = hrun("postcompact", {"session_id": CA, "cwd": str(rc), "trigger": "auto", "compact_summary": SUMMARY})
        now_blk = HM.sess_read(str(rc), CA)["Now"]
        check("compact: postcompact prints nothing, writes Now stamped `· from compact` with task, left and next from the summary",
              out is None and re.match(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d · from compact\n", now_blk) and "Task      Capping the surcharge in AqsatiRules.cs." in now_blk
              and "Left      Add FE tests" in now_blk and "Left      Update the docs page" in now_blk and "Next      Run the FE suite and fix what breaks." in now_blk)
        check("compact: ... the raw summary is saved in the state folder, compact/<sid8>.md (header with trigger), never in the project; the journal is kept", cf_(rc, CA).exists() and not list(mdir(rc).glob("*.compact.md")) and "It was half done when the context filled." in cf_(rc, CA).read_text() and "· auto" in cf_(rc, CA).read_text().splitlines()[0]
              and "started the cap" in HM.sess_read(str(rc), CA)["Journal"])
        stc = json.loads(Path(sdir, f"{CA}.json").read_text())
        check("compact: ... memo_semantic_due is set in the session state", stc.get("memo_semantic_due") is True)
        check("compact: ... the index line of the session says the new task", "task: Capping the surcharge" in mainf(rc).read_text())
        hrun("postcompact", {"session_id": CA, "cwd": str(rc), "trigger": "manual", "compact_summary": "7. Pending Tasks:\n   - SECOND left\n8. Current Work:\n   SECOND task.\n"})
        cc = cf_(rc, CA).read_text()
        check("compact: a second compact replaces the raw summary (one file, latest only) and the Now", "SECOND task." in cc and "half done" not in cc and cc.count("# Compact summary") == 1 and len(list(HM.COMPACT_DIR.glob(f"{CA[:8]}*"))) == 1
              and "Task      SECOND task." in HM.sess_read(str(rc), CA)["Now"] and "Left      SECOND left" in HM.sess_read(str(rc), CA)["Now"] and "Add FE tests" not in HM.sess_read(str(rc), CA)["Now"])
        before_s = sf_(rc, CA).read_bytes()
        hrun("postcompact", {"session_id": CA, "cwd": str(rc), "trigger": "auto", "compact_summary": "We talked about many things.\nNo numbered headings at all."})
        check("compact: a summary with no headings -> Now untouched (file bytes equal), raw summary saved", sf_(rc, CA).read_bytes() == before_s and "No numbered headings at all." in cf_(rc, CA).read_text())
        for bad in ("", None, "   ", 0):
            hrun("postcompact", {"session_id": CA, "cwd": str(rc), "compact_summary": bad})
        check("compact: an empty / null summary does nothing (no crash, Now and raw file kept)", sf_(rc, CA).read_bytes() == before_s and "No numbered headings" in cf_(rc, CA).read_text())
        # a hand-written Now newer than the last receipt
        CB = "cmpBBBB1-1111-2222"
        manual_now = f"Written   {time.strftime('%Y-%m-%d %H:%M', time.localtime(ago(0, 0.1)))}\nTask      manual task\nLeft      FE tests\nNext      my own next\nVerify    ran by hand"
        put_sess(rc, CB, now=manual_now, journal=[f"- {stamp(ago(0, 2))} · 1 Agent · done: older receipt"])
        hrun("postcompact", {"session_id": CB, "cwd": str(rc), "trigger": "auto", "compact_summary": "7. Pending Tasks:\n   - FE tests\n   - Docs page\n8. Current Work:\n   Something else.\n9. Optional Next Step:\n   Other next.\n"})
        nb = HM.sess_read(str(rc), CB)["Now"]
        check("compact: a manual Now newer than the last receipt is kept; only the missing Left line is added (once)",
              "Task      manual task" in nb and "Next      my own next" in nb and "Verify    ran by hand" in nb and "Something else" not in nb and "Other next" not in nb and nb.count("Docs page") == 1 and nb.count("FE tests") == 1 and "from compact" not in nb)
        sb = sf_(rc, CB).read_bytes()
        hrun("postcompact", {"session_id": CB, "cwd": str(rc), "trigger": "auto", "compact_summary": "7. Pending Tasks:\n   - fe TESTS\n   - docs PAGE!\n"})
        check("compact: ... a Left line that is already there (case / punctuation aside) is not added again, nothing is rewritten", HM.sess_read(str(rc), CB)["Now"].count("Docs page") == 1 and HM.sess_read(str(rc), CB)["Now"].count("FE tests") == 1 and "PAGE!" not in HM.sess_read(str(rc), CB)["Now"])
        # ── review fixes: Needs-you dedup, lock per thread, archive on trim, summary notes, caps ──
        rx = mkroot(); HM.memo_write(str(rx), {"Needs you": "", "Decisions": "", "Sessions": ""})
        XA, XB = "xa000000-1111-2222-3333-444444444444", "xb000000-1111-2222-3333-444444444444"
        HM.memo_needs(str(rx), XA, "ATTENTION: integration tests fail on the payments module")
        HM.memo_resolve(str(rx), 1, "fixed", XA)
        for who in (XA, XA, XB):
            HM.memo_needs(str(rx), who, "ATTENTION: integration tests fail on the payments module")
        ox = [l for l in HM.memo_read(str(rx))["Needs you"].splitlines() if "[ ]" in l]
        check("needs: a fixed problem report never hides the same problem when it comes back, nor does another session's item; a repeat of this session's open item stays one",
              len(ox) == 2 and len(HM.need_split(ox, XB, str(rx), "session")[0]) == 1 and len(HM.need_split(ox, XA, str(rx), "session")[0]) == 1
              and len({HM.need_id(l) for l in HM.memo_read(str(rx))["Needs you"].splitlines()}) == 3)
        held_, waited_ = threading.Event(), {}
        def _hold():
            with HM.memo_lock(str(rx)):
                held_.set(); time.sleep(0.5)
        th_ = threading.Thread(target=_hold); th_.start(); held_.wait()
        t0_ = time.time()
        with HM.memo_lock(str(rx)):
            waited_["s"] = time.time() - t0_
        th_.join()
        check("lock: a memo lock another thread of this process holds still excludes this one (the tidy-up runs in a thread)", waited_["s"] >= 0.3 or _fcntl is None)
        for i in range(HM.DECISIONS_KEEP + 2):
            HM.memo_decision(str(rx), XA, "note", f"settled point {i:03d}")
            HM.memo_decision(str(rx), XA, f"question {i:03d}?", "yes")
        for i in range(HM.JOURNAL_KEEP + 2):
            HM.memo_trace(str(rx), XA, f"trace line {i:03d}")
        ax = HM.archive_path(str(rx)).read_text()
        check("trim: decisions and journal lines past their count go to archive.md first (nothing is lost)",
              "settled point 000" in ax and "settled point 000" not in HM.memo_read(str(rx))["Decisions"] and "question 000?" in ax and "question 000?" not in HM.sess_read(str(rx), XA)["Decisions"]
              and "trace line 000" in ax and "trace line 000" not in HM.sess_read(str(rx), XA)["Journal"] and f"settled point {HM.DECISIONS_KEEP + 1:03d}" in HM.memo_read(str(rx))["Decisions"])
        q = HM.parse_compact_summary("7. Pending Tasks:\n   - None. Optionally, the user may check the pane live.\n   - Context is about 463K, so /compact is due soon.\n8. Current Work:\n   Moved the file.\n9. Optional Next Step:\n   - Start no new work.\n")
        check("compact: parse — `None.` with more after it is nothing pending; a note on the context size or on starting no work is no work", q == {"task": "Moved the file.", "left": [], "next": "", "done": []})
        q = HM.parse_compact_summary("7. Pending Tasks:\n   - None of the exports retry yet: add it\n")
        check("compact: parse — a real task that starts with `None of` stays", bool(q) and q["left"] == ["None of the exports retry yet: add it"])
        rcx = HM.parse_receipt("🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → " + "d " * 200 + "\n- `why` → " + "w " * 150 + "\n- `check` → " + "c " * 200 + "\n- `open` → none\n- `next` → " + "n " * 150)
        for _ in range(6):
            HM.memo_journal(str(rx), "xc000000-1111", rcx)
        HM.memo_health(str(rx), sid="zzzzzzzz")
        check("cap: a session file keeps six whole receipts (16 KB)", len([l for l in HM.sess_read(str(rx), "xc000000-1111")["Journal"].splitlines() if l.strip()]) == 6)
        calls_, orig_sc = [], HM.session_closed
        HM.session_closed = lambda root, k: calls_.append(k) or False
        try:
            HM.need_split([f"{i}. [ ] 11-01 10:00 · item {i} (s:yy000000 p:{HM.proj_tag(str(rx))})" for i in range(1, 6)], XA, str(rx))
        finally:
            HM.session_closed = orig_sc
        check("needs: need_split asks once per other session whether it is closed", calls_ == ["yy000000"])
        orig_lf = HM.lock_file
        def _boom(fh):
            raise OSError("disk says no")
        HM.lock_file = _boom
        try:
            with HM.memo_lock(str(rx)) as lk_:
                open_after = lk_.fh
        finally:
            HM.lock_file = orig_lf
        check("lock: a lock that cannot be taken fails open, closes its file and says so in the health line",
              open_after is None and "memo lock failed (OSError: disk says no)" in (HM.health_info(str(rx)) or {}).get("line", ""))
        HM._LOCK_FAIL.clear()
        rcm = mkroot(); HM.memo_write(str(rcm), {"Needs you": "", "Decisions": "", "Sessions": ""})
        HM.memo_dir(str(rcm)).mkdir(parents=True, exist_ok=True); (HM.memo_dir(str(rcm)) / "lg000000.compact.md").write_text("# Compact summary\nlegacy")
        HM.COMPACT_DIR.mkdir(parents=True, exist_ok=True)
        stale_ = HM.COMPACT_DIR / "st000000.md"; stale_.write_text("# Compact summary\nstale"); os.utime(stale_, (time.time() - 40 * 86400,) * 2)
        HM.memo_health(str(rcm), sid="zzzzzzzz")
        HM._save_compact_summary(str(rcm), "nw000000-1111", "manual", "8. Current Work:\n   x.\n")
        check("compact: a summary an older hook left in the project moves to the state folder; one not replaced for 30 days goes",
              "legacy" in HM.compact_path("lg000000").read_text() and not list(HM.memo_dir(str(rcm)).rglob("*.compact.md")) and not stale_.exists() and HM.compact_path("nw000000").exists())

        # ── agent gate review: skip, header claims, fable by side, released slots ──
        def ag_(sid, r, prompt="abofles: Unit Test / Work · T2\ndo it", model="sonnet", effort="medium", stype="abofles:abofles-worker", bg=False, tid=None):
            ti_ = {"prompt": prompt, "model": model, "effort": effort, "subagent_type": stype, "description": "x"}
            if bg:
                ti_["run_in_background"] = True
            return {"session_id": sid, "cwd": str(r), "tool_name": "Agent", "tool_input": ti_, **({"tool_use_id": tid} if tid else {})}
        den_ = lambda o: isinstance(o, dict) and o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        why_ = lambda o: (o or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "") if isinstance(o, dict) else ""
        def done_(pl, aid):
            hrun("post", {**pl, "tool_response": {"status": "completed", "agentId": aid, "content": [{"type": "text", "text": "STATUS: done\nCONFIDENCE: high\n- ok"}]}})
        rg = mkroot(["Agents = 1"]); SG = "gt000000-1111-2222-3333-444444444444"
        hrun("start", {"session_id": SG, "cwd": str(rg), "source": "startup"})
        p1_ = ag_(SG, rg); hrun("pre", p1_); done_(p1_, "g1")
        check("agent gate: abofles:skip never frees the memo agent from Haiku 5.5 · Low", "Haiku 5.5 · Low" in why_(hrun("pre", ag_(SG, rg, prompt="abofles:skip x", model="opus", effort="high", stype="abofles:abofles-memo"))))
        check("agent gate: the memo agent's cell is read like every model and effort (Haiku / Low passes)", not den_(hrun("pre", ag_(SG, rg, prompt="abofles: Memo Upkeep / Work · T1\nx", model="Haiku", effort="Low", stype="abofles:abofles-memo"))))
        jh_ = ag_(SG, rg, prompt="abofles: Payments / Judge · T4\nRefactor the payment service", stype="general-purpose", model="opus", effort="high")
        check("agent gate: a `/ Judge` header on another agent type with no question waiting is work: the spent budget refuses it", den_(hrun("pre", jh_)) and "budget is 1" in why_(hrun("pre", jh_)))
        st_ = HM.load_state(SG); st_["denied_ts"] = int(time.time()); HM.save_state(SG, st_)
        check("agent gate: ... while the question gate holds a question it is the Judge and takes no budget", not den_(hrun("pre", jh_)))
        check("agent gate: gate_claim — the abofles Judge type always, a header only while a question waits",
              HM.gate_claim("abofles:abofles-judge", "", {}) == "judge" and HM.gate_claim("general-purpose", "abofles: X / Judge · T2\nq", {}) is None
              and HM.gate_claim("general-purpose", "abofles: X / Judge · T2\nq", {"denied_ts": 10, "task_started": 5}) == "judge" and HM.gate_claim("general-purpose", "abofles: X / Judge · T2\nq", {"denied_ts": 4, "task_started": 5}) is None)
        check("agent gate: fable on any agent type is refused for a Read, Work or Verify header; an Analyse or Plan header passes",
              all("fable is only for" in why_(hrun("pre", ag_(SG, rg, prompt=f"abofles: Unit Test / {sd} · T2\nx", model="fable", stype="general-purpose"))) for sd in ("Read", "Work", "Verify", "Security"))
              and not any("fable is only" in why_(hrun("pre", ag_(SG, rg, prompt=f"abofles: Unit Test / {sd} · T2\nx", model="fable", stype="general-purpose"))) for sd in ("Analyse", "Plan")))
        pn_ = ag_(SG, rg); pn_["tool_input"]["prompt"] = None; pn_["tool_input"]["model"] = ""
        check("agent gate: a prompt that is not text fails closed on the model check (no crash, so no fail-open)", "model=missing" in why_(hrun("pre", pn_)))
        rr = mkroot(["Agents = 2", "Parallel = 1"]); SR = "gr000000-1111-2222-3333-444444444444"
        hrun("start", {"session_id": SR, "cwd": str(rr), "source": "startup"})
        for i in (1, 2):
            pr_ = ag_(SR, rr, bg=True, tid=f"tu{i}"); hrun("pre", pr_); hrun("release", {**pr_, "error": "model not available"})
        st_ = HM.load_state(SR)
        check("agent gate: a failed or refused Agent call gives its budget and Parallel slot back at once (release by tool_use_id)",
              st_.get("inflight") == 0 and st_.get("inflight_bg") == 0 and not st_.get("inflight_ids") and not den_(hrun("pre", ag_(SR, rr, bg=True, tid="tu3"))))
        hrun("release", {**ag_(SR, rr, tid="nope"), "error": "x"})
        check("agent gate: ... a release of an id it never reserved changes nothing", HM.load_state(SR).get("inflight") == 1 and HM.load_state(SR).get("inflight_bg") == 1)
        rk = mkroot(["Agents = 1"]); SK = "gk000000-1111-2222-3333-444444444444"
        hrun("start", {"session_id": SK, "cwd": str(rk), "source": "startup"})
        pk_ = ag_(SK, rk, prompt="abofles:skip research the market", stype="general-purpose"); hrun("pre", pk_); done_(pk_, "k1")
        stk = HM.load_state(SK)
        check("agent gate: an abofles:skip agent is logged apart: no spawn of the task, no budget, the next agent still passes",
              not stk.get("spawns_since_receipt") and any(json.loads(l).get("skip") for l in Path(led, f"{SK}.jsonl").read_text().splitlines() if l.strip()) and not den_(hrun("pre", ag_(SK, rk))))
        check("agent gate: the Parallel refusal says `1 background agent is`", "1 background agent is already running" in HM.cap_deny(1) and "2 background agents are" in HM.cap_deny(2))
        st_ = {}; HM.apply_gate_answer(st_, "Unlimited")
        check("agent gate: the answer `unlimited` is off for the session", st_.get("gate") == "off")

        # Memo on compact = off
        roff = mkroot(["Memo on compact = off"])
        CO = "cmpOFF01-1111-2222"
        put_main(roff); put_sess(roff, CO, now="Written   2020-01-01 00:00\nTask      kept as is", journal=[f"- {stamp(ago(0, 1))} · 1 Agent · done: newer receipt"])
        so = snap(roff)
        hrun("postcompact", {"session_id": CO, "cwd": str(roff), "trigger": "auto", "compact_summary": SUMMARY})
        hrun("precompact", {"session_id": CO, "cwd": str(roff), "trigger": "auto"})
        check("compact: Memo on compact = off -> postcompact and precompact write no Now, no raw file, no state flag", snap(roff) == so and not cf_(roff, CO).exists() and not (Path(sdir, f"{CO}.json").exists() and json.loads(Path(sdir, f"{CO}.json").read_text()).get("memo_semantic_due")))

        # ── compact: precompact ──
        rp = mkroot()
        out = hrun("precompact", {"session_id": "cmpPRE01-1", "cwd": str(rp), "trigger": "auto"})
        check("compact: precompact in a project with no memo -> prints nothing, creates no file, not even .claude", out is None and not Path(rp, ".claude").exists() and [p.name for p in rp.iterdir()] == [".git"])
        PD = "cmpDDDD1-1111-2222"
        HM.memo_journal(str(rp), PD, HM.parse_receipt("🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → built the badge\n- `check` → ok\n- `open` → owner decision: keep the badge wide?"))
        old = re.sub(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d.*", "Written   2020-01-01 00:00", sf_(rp, PD).read_text())
        sf_(rp, PD).write_text(old)
        out = hrun("precompact", {"session_id": PD, "cwd": str(rp), "trigger": "auto"})
        nowp = HM.sess_read(str(rp), PD)["Now"]
        check("compact: precompact — a Now older than the last receipt is rewritten from the receipt, stamped `· before compact`; prints nothing (never blocks)",
              out is None and re.match(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d · before compact\n", nowp) and "built the badge" in nowp and "2020-01-01" not in nowp)
        sb2 = sf_(rp, PD).read_bytes()
        hrun("precompact", {"session_id": PD, "cwd": str(rp), "trigger": "auto"})
        check("compact: ... a second precompact (Now no longer older) changes nothing", sf_(rp, PD).read_bytes() == sb2)
        fut = re.sub(r"Written   .*", f"Written   {time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() + 3600))}", sf_(rp, PD).read_text(), count=1)
        sf_(rp, PD).write_text(fut); sb3 = sf_(rp, PD).read_bytes()
        hrun("precompact", {"session_id": PD, "cwd": str(rp), "trigger": "auto"})
        check("compact: ... a Now newer than the last receipt is left alone", sf_(rp, PD).read_bytes() == sb3)
        hrun("precompact", {"session_id": "cmpNEW01-1", "cwd": str(rp), "trigger": "auto"})
        check("compact: precompact for a session with no file / no receipt writes nothing for it", not sf_(rp, "cmpNEW01-1").exists())
        # precompact runs the memo tidy-up
        rph = mkroot()
        put_main(rph, [nline(n, f"pre item {n}", "pre00001", rph, done=None if n > 3 else 9, age=10) for n in range(1, 6)])
        hrun("precompact", {"session_id": "cmpPRH01-1", "cwd": str(rph), "trigger": "manual"})
        check("compact: precompact also runs memo_health (not throttled): 3 old resolved items moved to archive.md", len(needs_of(rph)) == 2 and "pre item 1 " in arch_txt(rph))
        # bad input
        import subprocess as _sp2
        empty_dir = Path(hh, "emptydir"); empty_dir.mkdir()
        envx = dict(os.environ, ABOFLES_LEDGER_DIR=led)
        def raw(mode, stdin, cwd_):
            return _sp2.run([sys.executable, str(HOOK), mode], input=stdin, capture_output=True, text=True, env=envx, cwd=cwd_)
        rp_before = snap(rp)
        for mode in ("precompact", "postcompact"):
            outs = [raw(mode, s_, empty_dir) for s_ in ("", "not json", "{}", "[]", "null", json.dumps({"session_id": None, "cwd": None, "compact_summary": SUMMARY}), json.dumps({"cwd": str(rp), "compact_summary": SUMMARY}),
                                                   json.dumps({"session_id": "", "cwd": str(rp), "trigger": "auto"}))]
            check(f"compact: {mode} with bad input (empty, not JSON, no session id, no cwd) exits 0, prints nothing, creates no file in the folder or in a project with a memo",
                  all(o.returncode == 0 and not o.stdout.strip() and "Traceback" not in o.stderr for o in outs) and not any(empty_dir.iterdir()) and snap(rp) == rp_before)
            outs = [raw(mode, json.dumps({"session_id": "cmpBAD01-1", "cwd": str(rp), "compact_summary": v}), empty_dir) for v in ({"a": 1}, 12345, ["x"], True)]
            check(f"compact: {mode} with a summary of the wrong type exits 0 and prints nothing", all(o.returncode == 0 and not o.stdout.strip() for o in outs))
        sub = hrun("postcompact", {"session_id": "cmpSUB01-1", "cwd": str(rp), "agent_id": "ag1", "compact_summary": SUMMARY})
        hrun("precompact", {"session_id": PD, "cwd": str(rp), "agent_type": "abofles:abofles-worker"})
        check("compact: a hook fired inside a subagent is ignored", sub is None and not cf_(rp, "cmpSUB01-1").exists())

        # ── compact: two sessions in one project ──
        r12 = mkroot()
        XA, XB = "twoaaaa1-1111", "twobbbb1-2222"
        for s_ in (XA, XB):
            HM.memo_journal(str(r12), s_, HM.parse_receipt(f"🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → work of {s_[:8]}\n- `check` → ok"))
        old_b = re.sub(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d.*", "Written   2020-01-01 00:00", sf_(r12, XB).read_text()); sf_(r12, XB).write_text(old_b)
        os.utime(sf_(r12, XB), (ago(1), ago(1)))
        bB, mB = sf_(r12, XB).read_bytes(), sf_(r12, XB).stat().st_mtime_ns
        idxB = [l for k, l in HM.index_entries(HM.memo_read(str(r12))) if k == "twobbbb1"]
        hrun("postcompact", {"session_id": XA, "cwd": str(r12), "trigger": "auto", "compact_summary": SUMMARY})
        hrun("precompact", {"session_id": XA, "cwd": str(r12), "trigger": "auto"})
        check("compact: two sessions in one root — postcompact / precompact of A never change B's file, B's raw file or B's index line",
              sf_(r12, XB).read_bytes() == bB and sf_(r12, XB).stat().st_mtime_ns == mB and not cf_(r12, XB).exists() and idxB and idxB == [l for k, l in HM.index_entries(HM.memo_read(str(r12))) if k == "twobbbb1"]
              and "from compact" in HM.sess_read(str(r12), XA)["Now"] and cf_(r12, XA).exists())
        hrun("postcompact", {"session_id": XB, "cwd": str(r12), "trigger": "auto", "compact_summary": "8. Current Work:\n   B alone.\n"})
        check("compact: ... and B's compact leaves A's Now alone", "Task      Capping the surcharge" in HM.sess_read(str(r12), XA)["Now"] and "B alone." in HM.sess_read(str(r12), XB)["Now"] and "B alone" not in HM.sess_read(str(r12), XA)["Now"])

        # ── health: start() throttle ──
        calls = []
        real_health = HM.memo_health
        def counting(root, *a, **kw):
            calls.append(os.path.realpath(root)); return real_health(root, *a, **kw)
        HM.memo_health = counting
        def do_start(root, sid, src="startup"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                HM.start({"session_id": sid, "cwd": str(root), "source": src})
            return buf.getvalue()
        try:
            rt = mkroot()
            put_main(rt, [nline(n, f"start item {n}", "start001", rt, done=9, age=10) for n in range(1, 4)]); put_sess(rt, "start001", now="Task      x", age=0)
            real_rt = os.path.realpath(rt)
            do_start(rt, "start001-1")
            n1 = calls.count(real_rt)
            do_start(rt, "start001-2"); do_start(rt, "start001-3", "resume")
            check("health: start() runs memo_health on the first start and not on the next ones within 6 h", n1 == 1 and calls.count(real_rt) == 1 and len(needs_of(rt)) == 0)
            reg = HM.registry_read(); reg[real_rt]["last_health_at"] = int(time.time()) - (6 * 3600 - 120); Path(sdir, "memo-index.json").write_text(json.dumps(reg))
            do_start(rt, "start001-4")
            check("health: ... 5 h 58 min after the last run it still does not run", calls.count(real_rt) == 1)
            reg = HM.registry_read(); reg[real_rt]["last_health_at"] = int(time.time()) - (6 * 3600 + 120); Path(sdir, "memo-index.json").write_text(json.dumps(reg))
            do_start(rt, "start001-5")
            check("health: ... 6 h 2 min after the last run it runs again and records the time", calls.count(real_rt) == 2 and time.time() - HM.registry_read()[real_rt]["last_health_at"] < 60)
            rnm = mkroot()
            t0 = time.perf_counter(); out = do_start(rnm, "nomemo02-1"); took = time.perf_counter() - t0
            check("health: start() in a project with no memo files -> memo_health not called, nothing created in the project, no registry entry",
                  os.path.realpath(rnm) not in calls and not Path(rnm, ".claude").exists() and os.path.realpath(rnm) not in HM.registry_read() and took < 5.0 and "RESUME" not in out)
            t0 = time.perf_counter(); got = HM.health_run(str(rnm), "nomemo02-1"); took = time.perf_counter() - t0
            check("health: health_run for a project with no memo returns None at once, even forced", got is None and HM.health_run(str(rnm), "nomemo02-1", force=True) is None and took < 0.5 and not Path(rnm, ".claude").exists())
            # semantic flag: a stale Now or a repeated question makes a by-meaning check due
            rsem = mkroot()
            put_main(rsem); put_sess(rsem, "sem00001", now="Written   2020-01-01 00:00\nTask      stale", age=0)
            do_start(rsem, "sem00001-1")
            check("health: a Now nobody refreshed for days sets memo_semantic_due in the session state (from start)", json.loads(Path(sdir, "sem00001-1.json").read_text()).get("memo_semantic_due") is True)
            rsem2 = mkroot()
            put_main(rsem2); put_sess(rsem2, "sem00002", now=f"Written   {time.strftime('%Y-%m-%d %H:%M')}\nTask      fresh", age=0)
            do_start(rsem2, "sem00002-1")
            check("health: ... and a fresh Now with no repeated question does not", not json.loads(Path(sdir, "sem00002-1.json").read_text()).get("memo_semantic_due"))
        finally:
            HM.memo_health = real_health
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class _MemoFixture:
    """Temp projects, session states and helpers shared by the memo-agent and repair tests: own state dir and config, a fresh module copy, the environment put back by `close`."""
    def __init__(self, tag):
        self.keys = ("ABOFLES_STATE_DIR", "ABOFLES_CONFIG", "ABOFLES_SESSION", "ABOFLES_LEDGER_DIR")
        self.saved = {k: os.environ.get(k) for k in self.keys}
        self.tmp = tempfile.TemporaryDirectory()
        self.hh = self.tmp.name
        os.environ["ABOFLES_STATE_DIR"] = str(Path(self.hh, "st")); os.environ["ABOFLES_CONFIG"] = str(Path(self.hh, "config.json"))
        os.environ.pop("ABOFLES_SESSION", None)
        self.led = str(Path(self.hh, "led")); Path(self.led).mkdir()
        os.environ["ABOFLES_LEDGER_DIR"] = self.led  # the module copy below reads the same ledgers as the hook subprocesses
        spec = _ilu.spec_from_file_location("abofles_hook_" + tag, str(HOOK)); self.HM = _ilu.module_from_spec(spec); spec.loader.exec_module(self.HM)
        self.sdir = Path(self.hh, "st")
        self.n = 0
        self.memopy = str(Path(__file__).resolve().parent / "memo.py")
    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()
    def hrun(self, mode, payload):
        return run(mode, payload, self.led)
    def mkroot(self, overrides=None, git=True, name=None):
        self.n += 1
        r = Path(self.hh, name or f"P{self.n}"); r.mkdir()
        if git:
            subprocess.run(["git", "init", "-q"], cwd=r, capture_output=True)
        if overrides is not None:
            r.joinpath("CLAUDE.md").write_text("## abofles overrides\n" + "".join(f"- {l}\n" for l in overrides))
        return Path(os.path.realpath(r))
    def mkstate(self, sid, root, **kw):
        self.HM.save_state(sid, {"started": int(time.time()), "agents_at": int(time.time()), "root": os.path.realpath(root), **kw})
    def cli(self, root, sid, *args, stdin=None):
        return subprocess.run([sys.executable, self.memopy, *args, "--session", sid], cwd=str(root), input=stdin, capture_output=True, text=True, env=dict(os.environ))
    def raw_cli(self, root, *args):
        return subprocess.run([sys.executable, self.memopy, *args], cwd=str(root), capture_output=True, text=True, env=dict(os.environ))
    def put_sess(self, r, sid, now="", journal=(), decisions=(), age=0):
        HM = self.HM
        sec = {"Now": now, "Journal": "\n".join(journal), "Decisions": "\n".join(decisions)}
        HM.sess_write(str(r), sid, sec)
        p = HM.session_memo_path(str(r), sid); t = time.time() - age * 86400; os.utime(p, (t, t))
        m = HM.memo_read(str(r)); HM.index_set(m, sid, HM.index_line(sid, sec, time.strftime("%m-%d %H:%M", time.localtime(t)))); HM.memo_write(str(r), m)
        return p
    def put_main(self, r, needs=None, decisions=None):
        sec = self.HM.memo_read(str(r))
        if needs is not None:
            sec["Needs you"] = "\n".join(needs)
        if decisions is not None:
            sec["Decisions"] = "\n".join(decisions)
        self.HM.memo_write(str(r), sec)
    def snap(self, *roots):
        out = {}
        for r in roots:
            for p in Path(r, ".claude").rglob("*"):
                if p.is_file():
                    out[str(p)] = p.read_bytes()
        reg = self.sdir / "memo-index.json"
        out["registry"] = reg.read_bytes() if reg.exists() else b""
        for p in self.sdir.glob("*.json"):
            out[str(p)] = p.read_bytes()
        return out


def compact_refresh_tests(check):
    """After a compact: the context number, the pane's state and the resume block are current. An archived session that comes back keeps its memo. A memo in an old format survives the tidy-up."""
    F = _MemoFixture("compactfix")
    HM, hrun = F.HM, F.hrun
    SUMMARY = "7. Pending Tasks:\n   - finish the cap\n\n8. Current Work:\n   Working on the cap.\n\n9. Optional Next Step:\n   Run tests.\n"
    usage = lambda n, **kw: json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": n - 1, "cache_creation_input_tokens": 0}, "content": []}, **kw})
    boundary = lambda post=33060, **kw: json.dumps({"type": "system", "subtype": "compact_boundary", "compactMetadata": ({"postTokens": post} if post is not None else {}), **kw})
    def transcript(*rows):
        F.n += 1
        t = Path(F.hh, f"t{F.n}.jsonl")
        t.write_text("".join(r + "\n" for r in rows))
        return str(t)
    try:
        # ── context_tokens ──
        check("compact ctx: no compact -> the last usage", HM.context_tokens(transcript(usage(100), usage(462000))) == 462000)
        check("compact ctx: a boundary after the last usage -> its postTokens", HM.context_tokens(transcript(usage(462000), boundary())) == 33060)
        check("compact ctx: a usage after the boundary -> that usage", HM.context_tokens(transcript(usage(462000), boundary(), usage(111869))) == 111869)
        check("compact ctx: a boundary naming no size -> unknown, not the old size", HM.context_tokens(transcript(usage(462000), boundary(None))) is None)
        check("compact ctx: a subagent's boundary does not count", HM.context_tokens(transcript(usage(462000), boundary(isSidechain=True))) == 462000)
        check("compact ctx: boundary=False reads through the boundary", HM.context_tokens(transcript(usage(462000), boundary()), boundary=False) == 462000)
        talk = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 500}, "content": [{"type": "text", "text": 'the row "compact_boundary" has postTokens'}]}})
        check("compact ctx: an assistant message that only names the boundary is a usage", HM.context_tokens(transcript(usage(9), talk)) == 500)

        # ── PostCompact: the pane's state follows, whatever Memo on compact says ──
        SA, SB = "aa000000-1111-2222-3333-444444444444", "ab000000-1111-2222-3333-444444444444"
        r = F.mkroot()
        tp = transcript(usage(462000), boundary())
        F.mkstate(SA, r, transcript_path=tp, guide={"ctx": {"tokens": 462000, "mark": 500}})
        F.put_sess(r, SA, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')} · from receipt\nTask      none — between tasks")
        hrun("postcompact", {"session_id": SA, "cwd": str(r), "trigger": "manual", "compact_summary": SUMMARY, "transcript_path": tp})
        st = HM.load_state(SA)
        check("compact pane: PostCompact puts the new size in the state the pane reads", (st.get("guide") or {}).get("ctx", {}).get("tokens") == 33060)
        check("compact pane: PostCompact puts the new Now in the pane payload", "Working on the cap" in json.dumps(st.get("pane") or {}))
        check("compact pane: the summary is saved and upkeep is due", HM.compact_path("aa000000").exists() and not (HM.memo_dir(str(r)) / "aa000000.compact.md").exists() and st.get("memo_semantic_due") is True)
        off = F.mkroot(overrides=["Memo on compact = off"])
        tpo = transcript(usage(462000), boundary())
        F.mkstate(SB, off, transcript_path=tpo)
        F.put_sess(off, SB, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')}\nTask      hand written")
        hrun("postcompact", {"session_id": SB, "cwd": str(off), "trigger": "manual", "compact_summary": SUMMARY, "transcript_path": tpo})
        st = HM.load_state(SB)
        check("compact pane: Memo on compact = off still refreshes the number, and leaves the Now alone",
              (st.get("guide") or {}).get("ctx", {}).get("tokens") == 33060 and "hand written" in HM.sess_read(str(off), SB)["Now"] and not HM.compact_path("ab000000").exists())
        hrun("postcompact", {"session_id": SB, "cwd": str(off), "trigger": "manual", "compact_summary": "", "transcript_path": tpo})
        check("compact pane: an empty summary does not fail", "hand written" in HM.sess_read(str(off), SB)["Now"])

        # ── SessionStart before PostCompact: no stale Now, no stale size ──
        SC, SD = "ac000000-1111-2222-3333-444444444444", "ad000000-1111-2222-3333-444444444444"
        for sid_, ov in ((SC, None), (SD, ["Memo on compact = off"])):
            rr = F.mkroot(overrides=ov)
            t_ = transcript(usage(462000))  # the boundary row is not written yet
            F.mkstate(sid_, rr, transcript_path=t_)
            F.put_sess(rr, sid_, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')} · from receipt\nTask      stale receipt task\nNext      stale next")
            out = subprocess.run([sys.executable, str(HOOK), "start"], input=json.dumps({"session_id": sid_, "cwd": str(rr), "source": "compact", "transcript_path": t_}), capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=F.led)).stdout
            if sid_ == SC:
                check("compact start: auto -> the block has no stale Now, Next or age", "stale receipt task" not in out and "stale next" not in out and "now written" not in out and "📍" in out)
                check("compact start: the usage from before the compact is not shown as the size", (HM.load_state(sid_).get("guide") or {}).get("ctx") is None and "462K" not in out)
                hrun("postcompact", {"session_id": sid_, "cwd": str(rr), "trigger": "manual", "compact_summary": SUMMARY, "transcript_path": t_})
                g_ = HM.load_state(sid_).get("guide") or {}
                check("compact wait: PostCompact before the boundary row -> no size, and the marks for the mod's own figure", g_.get("ctx") is None and (g_.get("ctx_wait") or {}).get("marks") == list(HM.context_marks(str(rr))) and (g_.get("ctx_wait") or {}).get("auto") is True)
                Path(t_).write_text(usage(462000) + "\n" + boundary() + "\n")
                st_ = HM.load_state(sid_)
                check("compact start: the boundary row arrives -> the size is known again", HM.context_state(str(rr), st_)[0] == 33060)
                Path(t_).write_text(usage(462000) + "\n" + boundary() + "\n" + usage(111869) + "\n")
                check("compact start: the next reply's usage wins", HM.context_state(str(rr), st_)[0] == 111869)
                HM.refresh_guide(str(rr), st_, sid_)
                check("compact wait: a known size clears the wait", st_["guide"]["ctx"]["tokens"] == 111869 and st_["guide"]["ctx_wait"] is None)
            else:
                check("compact start: Memo on compact = off keeps the Now in the block (the user's own memo)", "stale receipt task" in out)

        nw = "b1000000-1111-2222-3333-444444444444"
        rn = F.mkroot()
        F.mkstate(nw, rn, transcript_path=transcript(usage(9000)))
        st_ = HM.load_state(nw)
        HM.refresh_guide(str(rn), st_, nw)
        check("compact wait: no compact in this session -> no wait", st_["guide"]["ctx"]["tokens"] == 9000 and st_["guide"]["ctx_wait"] is None)

        # ── an archived session comes back ──
        ME, OLD, TWO = "ae000000-1111-2222-3333-444444444444", "af000000-1111-2222-3333-444444444444", "b0000000-1111-2222-3333-444444444444"
        r = F.mkroot()
        F.mkstate(ME, r); F.mkstate(OLD, r); F.mkstate(TWO, r)
        F.put_sess(r, ME, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')}\nTask      live")
        def archive(sid_, task, journal):
            F.put_sess(r, sid_, now=f"Written   2020-01-01 00:00 · from receipt\nTask      {task}\nNext      keep going", journal=[journal], age=9)
            (HM.memo_dir(str(r)) / f"{HM.sid8(sid_)}.compact.md").write_text("# Compact summary\nold")
            HM.memo_health(str(r), sid=ME)
            return HM.archive_dir(str(r)) / f"{HM.sid8(sid_)}.md"
        arch = archive(OLD, "old task", "- 11-01 10:00 · 1 Agent · done: old receipt")
        active = HM.session_memo_path(str(r), OLD)
        check("restore: the tidy-up archived the idle session", arch.exists() and not active.exists())
        d = HM.resume_data(str(r), session_id=OLD, elsewhere=False)
        check("restore: an archived session still reads its own Now (a session left open while another one tidied)", "old task" in str(d["now"]) and arch.exists())
        out = subprocess.run([sys.executable, str(HOOK), "start"], input=json.dumps({"session_id": OLD, "cwd": str(r), "source": "resume"}), capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=F.led)).stdout
        check("restore: a resume brings the file back; the compact summary waits in the state folder, never in the project", active.exists() and not arch.exists() and HM.compact_path("af000000").exists() and not (HM.memo_dir(str(r)) / "af000000.compact.md").exists() and not (HM.archive_dir(str(r)) / "af000000.compact.md").exists())
        check("restore: the resume block shows its Now", "old task" in out)
        check("restore: the index lists it, archive.md does not", "[af000000](" in HM.memo_read(str(r))["Sessions"] and "[af000000](archive/" not in HM.archive_path(str(r)).read_text())
        check("restore: its Journal is intact", "old receipt" in HM.sess_read(str(r), OLD)["Journal"])
        check("restore: the idle clock restarted, so the next tidy-up leaves it", active.exists() and (HM.memo_health(str(r), sid=ME) or True) and active.exists() and not arch.exists())
        arch2 = archive(TWO, "second task", "- 11-02 10:00 · 1 Agent · done: second receipt")
        HM.memo_trace(str(r), TWO, "a write without a session start")
        active2 = HM.session_memo_path(str(r), TWO)
        check("restore: a write brings it back too, nothing lost", active2.exists() and not arch2.exists() and "second receipt" in HM.sess_read(str(r), TWO)["Journal"] and "without a session start" in HM.sess_read(str(r), TWO)["Journal"] and "second task" in HM.sess_read(str(r), TWO)["Now"])
        THREE = "b1000000-1111-2222-3333-444444444444"
        F.mkstate(THREE, r)
        arch3 = archive(THREE, "archived task", "- 11-03 10:00 · 1 Agent · done: archived receipt")
        F.put_sess(r, THREE, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')}\nTask      newer task", journal=["- 11-08 10:00 · 1 Agent · done: newer receipt"])
        check("restore: both files -> the restore runs", HM.session_restore(str(r), THREE) is True and not arch3.exists())
        s3 = HM.sess_read(str(r), THREE)
        check("restore: both files -> the newer Now, both Journals", "newer task" in s3["Now"] and "archived task" not in s3["Now"] and "archived receipt" in s3["Journal"] and "newer receipt" in s3["Journal"])
        check("restore: nothing archived -> no change", HM.session_restore(str(r), ME) is False)
        check("restore: no session -> no change, no crash", HM.session_restore(str(r), "") is False)

        # ── old memo formats through the new tidy-up ──
        S1 = "c1000000-1111-2222-3333-444444444444"
        r = F.mkroot()
        HM.memo_path(str(r)).parent.mkdir(parents=True, exist_ok=True)
        HM.memo_path(str(r)).write_text("# Abo Fles memo\nUpdated: 2026-10-01 10:00 · session c1000000\n\n## Now\nSession   c1000000\nTask      one file task\nNext      go\n\n"
                                        "## Needs you\n1. [ ] 10-01 09:00 · owner decision: no tag at all?\n2. [ ] 10-01 09:01 · owner decision: session tag only? (s:c1000000)\n"
                                        "3. [x] 10-01 09:02 · FAIL: old closed (s:c1000000) → resolved 10-01 09:30: fixed\n\n## Decisions\n- 10-01 09:03 · note → keep this\n\n"
                                        "## Journal\n- 10-01 09:04 · s:c1000000 · 1 Agent · done: old receipt\n")
        F.mkstate(S1, r)
        HM.memo_read(str(r))  # the first read splits it
        check("old format: one-file memo keeps the Now and the receipt in the session file", "one file task" in HM.sess_read(str(r), S1)["Now"] and "old receipt" in HM.sess_read(str(r), S1)["Journal"])
        HM.memo_health(str(r), sid=S1, now=time.time() + 40 * 86400)
        m = HM.memo_read(str(r))
        check("old format: open items stay open, tagged or not", "no tag at all?" in m["Needs you"] and "session tag only?" in m["Needs you"] and m["Needs you"].count("[ ]") == 2)
        check("old format: the closed item moves to archive.md, not away", "old closed" in HM.archive_path(str(r)).read_text() and "old closed" not in m["Needs you"])
        check("old format: Decisions stay", "keep this" in m["Decisions"])
        own = HM.resume_data(str(r), session_id=S1, elsewhere=False)
        check("old format: the session that wrote it still sees its Now", "one file task" in str(own["now"]))
    finally:
        F.close()


def memo_agent_tests(check):
    """The memo agent: its memo.py subcommands (curator ops), the hard limit in the Bash/Read guard, the routing exemption and the due rule. Own temp projects and state dir."""
    F = _MemoFixture("memoagent")
    HM, hrun = F.HM, F.hrun
    try:
        stamp = lambda days=0, hours=0: time.strftime("%m-%d %H:%M", time.localtime(time.time() - days * 86400 - hours * 3600))
        CA, CB, CC = "ca000000-1111-2222-3333-444444444444", "bb000000-1111-2222-3333-444444444444", "cc000000-1111-2222-3333-444444444444"
        r = F.mkroot()
        for s_ in (CA, CB, CC):
            F.mkstate(s_, r)
        HM.memo_needs(str(r), CB, "owner decision: ship the export? (Jamkhana JAM-12)", project=str(r))
        HM.memo_needs(str(r), CA, "owner decision: rename the badge?", project=str(r))
        HM.memo_needs(str(r), CB, "FAIL: test placeholder line", project=str(r))
        F.put_main(r, decisions=[f"- {stamp(4)} · by:you · note → export settled: yes (s:bb000000 p:{HM.proj_tag(r)})"])
        F.put_sess(r, CA, now=f"Written   {time.strftime('%Y-%m-%d %H:%M')}\nTask      caller task\nLeft      one\nNext      two", decisions=[f"- {stamp(5)} · by:you · Use the new export? → yes"])
        F.put_sess(r, CB, now="Written   2020-01-01 00:00 · from receipt\nTask      old task\nDone      did x\nLeft      old left\nNext      old next\nVerify    ran y",
                   journal=[f"- {stamp(1)} · 1 Agent · done: newer receipt"], decisions=[f"- {stamp(3)} · by:you · Should we use the new export? → Yes, use it", f"- {stamp(2)} · by:judge · judge: keep the cap? → yes"])
        F.put_sess(r, CC, now="Written   2020-01-01 00:00 · from receipt\nTask      none — between tasks", age=9)
        HM.compact_path("cc000000").parent.mkdir(parents=True, exist_ok=True); HM.compact_path("cc000000").write_text("# Compact summary\nclosed")
        # ── list --json ──
        out = F.cli(r, CA, "list", "--json")
        L = json.loads(out.stdout) if out.returncode == 0 else {}
        by_text = lambda frag: next((i for i in L.get("items", []) if frag in i["text"]), {})
        check("memoagent: list --json gives items with id, n, text, state, s, p", out.returncode == 0 and len(L["items"]) == 3 and all(set(i) >= {"id", "n", "text", "state", "s", "p"} for i in L["items"])
              and by_text("ship the export")["s"] == "bb000000" and by_text("ship the export")["p"] == HM.proj_tag(r) and re.fullmatch(r"#[0-9a-f]{4}", by_text("ship the export")["id"]) and by_text("ship the export")["state"] == "open")
        check("memoagent: ... decisions with file, stamp and a ref; sessions with Now age, Task, Left, Next, the compact file and who the caller is",
              len(L["decisions"]) == 4 and {d["file"] for d in L["decisions"]} == {"abofles-memo.md", "ca000000.md", "bb000000.md"} and all(d["stamp"] and d["ref"] for d in L["decisions"])
              and {s_["sid"]: s_ for s_ in L["sessions"]}["bb000000"]["now_age_min"] > 1000 and {s_["sid"]: s_ for s_ in L["sessions"]}["bb000000"]["task"] == "old task"
              and {s_["sid"]: s_ for s_ in L["sessions"]}["bb000000"]["left"] == ["old left"] and {s_["sid"]: s_ for s_ in L["sessions"]}["bb000000"]["next"] == ["old next"]
              and {s_["sid"]: s_ for s_ in L["sessions"]}["ca000000"]["caller"] is True and {s_["sid"]: s_ for s_ in L["sessions"]}["cc000000"]["compact"] == str(HM.compact_path("cc000000")))
        nostate = dict(os.environ, ABOFLES_STATE_DIR=str(Path(F.hh, "nostate")))
        check("memoagent: a subcommand with no session exits 4 and writes nothing", all(subprocess.run([sys.executable, F.memopy, *a], cwd=str(r), capture_output=True, text=True, env=nostate).returncode == 4
              for a in (["list", "--json"], ["drop-item", "#abcd", "--reason", "x"], ["archive-session", "cc000000"])))
        # ── merge-decisions ──
        refs = {d["file"]: d["ref"] for d in L["decisions"] if d["q"] != "judge: keep the cap?"}
        needs_before = [l for l in HM.memo_read(str(r))["Needs you"].splitlines()]
        sb = F.snap(r)
        bad = F.cli(r, CA, "merge-decisions", refs["ca000000.md"], "--into", "no arrow here")
        one = F.cli(r, CA, "merge-decisions", refs["ca000000.md"], "--into", "Use the new export? → yes")
        miss = F.cli(r, CA, "merge-decisions", refs["ca000000.md"], "ca000000@01-01T00:00", "--into", "Q → A")
        check("memoagent: merge-decisions refuses a missing arrow / one ref (exit 2) and an unknown ref (exit 1); nothing is written", bad.returncode == 2 and one.returncode == 2 and miss.returncode == 1 and F.snap(r) == sb)
        m = F.cli(r, CA, "merge-decisions", refs["abofles-memo.md"], refs["ca000000.md"], refs["bb000000.md"], "--into", "Use the new export? → yes, decided")
        main_txt = HM.memo_read(str(r))["Decisions"]
        merged = [l for l in main_txt.splitlines() if "yes, decided" in l]
        arch = HM.archive_path(str(r)).read_text()
        oldest = next(d["stamp"] for d in L["decisions"] if d["file"] == "ca000000.md")
        check("memoagent: merge-decisions keeps the oldest stamp in the file of the oldest line; the other two lines leave their files",
              m.returncode == 0 and HM.sess_read(str(r), CA)["Decisions"].count("yes, decided") == 1 and merged == [] and f"- {oldest} · by:you · Use the new export? → yes, decided" in HM.sess_read(str(r), CA)["Decisions"]
              and "export settled" not in main_txt and "Should we use the new export?" not in HM.sess_read(str(r), CB)["Decisions"] and "keep the cap?" in HM.sess_read(str(r), CB)["Decisions"])
        check("memoagent: ... the merged lines are in archive.md verbatim with the new line, nothing is deleted",
              all(x in arch for x in ("export settled: yes", "Should we use the new export? → Yes, use it", "Use the new export? → yes", "merged into:")) and "· merged decisions" in arch)
        check("memoagent: ... Needs-you items keep their ids and tags (not touched)", HM.memo_read(str(r))["Needs you"].splitlines() == needs_before)
        # a note's tag is kept when the oldest line is a note
        s8 = stamp(8)
        F.put_main(r, decisions=[f"- {s8} · by:you · note → older note (s:bb000000 p:{HM.proj_tag(r)})", f"- {stamp(2)} · by:you · note → newer note"])
        L2 = json.loads(F.cli(r, CA, "list", "--json").stdout)
        nr = [d["ref"] for d in L2["decisions"] if d["file"] == "abofles-memo.md"]
        F.cli(r, CA, "merge-decisions", *nr, "--into", "note → one note")
        mt = HM.memo_read(str(r))["Decisions"].splitlines()
        check("memoagent: merging notes keeps the oldest stamp and its `(s: p:)` tag", len(mt) == 1 and mt[0].startswith(f"- {s8} · by:you · note → one note") and mt[0].endswith(f"(s:bb000000 p:{HM.proj_tag(r)})"))
        # ── move-item ──
        tgt = F.mkroot(); HM.memo_write(str(tgt), {"Needs you": "", "Decisions": "", "Sessions": ""})
        plain = F.mkroot(git=False)
        bare = F.mkroot()  # a git repository the memo never saw
        withmemo = F.mkroot(git=False); HM.memo_write(str(withmemo), {"Needs you": "", "Decisions": "", "Sessions": ""})
        shipid = by_text("ship the export")["id"]
        line_before = next(l for l in HM.memo_read(str(r))["Needs you"].splitlines() if "ship the export" in l)
        sb = F.snap(r, tgt)
        a = F.cli(r, CA, "move-item", shipid, str(Path(F.hh, "nope")))
        b = F.cli(r, CA, "move-item", shipid, str(plain))
        c_ = F.cli(r, CA, "move-item", shipid, str(bare))
        check("memoagent: move-item refuses a missing folder, a plain folder and a git repository the memo never saw (exit 2); nothing moves",
              a.returncode == 2 and b.returncode == 2 and c_.returncode == 2 and F.snap(r, tgt) == sb and not Path(bare, ".claude", "abofles-memo.md").exists())
        mv = F.cli(r, CA, "move-item", shipid, str(tgt))
        tl = [l for l in HM.memo_read(str(tgt))["Needs you"].splitlines()]
        check("memoagent: move-item moves the item to the other project with text, stamp, #id and tags unchanged; it is gone here and the rest is renumbered",
              mv.returncode == 0 and len(tl) == 1 and re.sub(r"^\d+\.", "", tl[0]) == re.sub(r"^\d+\.", "", line_before) and "ship the export" not in HM.memo_read(str(r))["Needs you"]
              and [re.match(r"\d+", l).group(0) for l in HM.memo_read(str(r))["Needs you"].splitlines()] == ["1", "2"])
        mv2 = F.cli(r, CA, "move-item", by_text("rename the badge")["id"], str(withmemo))
        check("memoagent: ... a folder that already has a memo is a target too", mv2.returncode == 0 and "rename the badge" in HM.memo_read(str(withmemo))["Needs you"])
        # ── drop-item ──
        dropid = by_text("test placeholder")["id"]
        d0 = F.cli(r, CA, "drop-item", dropid)
        dl = next(l for l in HM.memo_read(str(r))["Needs you"].splitlines() if "test placeholder" in l)
        d1 = F.cli(r, CA, "drop-item", dropid, "--reason", "a test line, not a follow-up")
        at = HM.archive_path(str(r)).read_text()
        check("memoagent: drop-item needs a reason (exit 2); with one the item leaves Needs you and archive.md keeps its line (id, tags) plus the reason",
              d0.returncode == 2 and d1.returncode == 0 and "test placeholder" not in HM.memo_read(str(r))["Needs you"] and dl.split(". ", 1)[1] in at and "dropped: a test line, not a follow-up" in at and "· dropped items" in at)
        check("memoagent: drop-item of an unknown id exits 1", F.cli(r, CA, "drop-item", "#ffff", "--reason", "x").returncode == 1)
        # curator=False: only the calling session's own items
        HM.memo_needs(str(r), CB, "owner decision: B's own question?", project=str(r))
        bid = "#" + HM.need_id(next(l for l in HM.memo_read(str(r))["Needs you"].splitlines() if "B's own" in l))
        refused = HM.memo_apply(str(r), "drop_item", CA, ref=bid, reason="x")
        check("memoagent: without curator rights another session's item is refused (other-session); with them it is dropped", not refused and refused.code == "other-session"
              and bool(HM.memo_apply(str(r), "drop_item", CA, curator=True, ref=bid, reason="noise")))
        # ── archive-session ──
        HM.memo_needs(str(r), CB, "owner decision: still open for B?", project=str(r))
        x1 = F.cli(r, CA, "archive-session", "bb000000")
        x2 = F.cli(r, CA, "archive-session", "ca000000")
        check("memoagent: archive-session refuses a session with an open item of its own and the calling session (exit 5); both files stay", x1.returncode == 5 and x2.returncode == 5
              and HM.session_memo_path(str(r), "bb000000").exists() and HM.session_memo_path(str(r), "ca000000").exists())
        x3 = F.cli(r, CA, "archive-session", "cc000000")
        check("memoagent: archive-session moves a closed session's file to archive/ (its compact summary stays in the state folder), drops its index line and lists it in archive.md",
              x3.returncode == 0 and not HM.session_memo_path(str(r), "cc000000").exists() and (HM.archive_dir(str(r)) / "cc000000.md").exists() and HM.compact_path("cc000000").exists() and not (HM.archive_dir(str(r)) / "cc000000.compact.md").exists()
              and "cc000000" not in [k for k, _ in HM.index_entries(HM.memo_read(str(r)))] and "(archive/cc000000.md)" in HM.archive_path(str(r)).read_text())
        check("memoagent: archive-session of an unknown session exits 1", F.cli(r, CA, "archive-session", "zz999999").returncode == 1)
        # ── rewrite-now ──
        w1 = F.cli(r, CA, "rewrite-now", "ca000000", stdin="Task      hijack\nLeft      x")
        w2 = F.cli(r, CA, "rewrite-now", "bb000000", stdin="Task      new task from the summary\nLeft      only left\nDone      should be ignored\nNext      do the thing")
        nb = HM.sess_read(str(r), "bb000000")["Now"]
        check("memoagent: rewrite-now refuses a Now written by memo.py now that is newer than the last receipt (exit 5), file unchanged", w1.returncode == 5 and "caller task" in HM.sess_read(str(r), "ca000000")["Now"] and "hijack" not in HM.sess_read(str(r), "ca000000")["Now"])
        check("memoagent: rewrite-now replaces Task, Left and Next only, stamps `· from memo agent`, keeps Done and Verify",
              w2.returncode == 0 and re.match(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d · from memo agent\n", nb) and "Task      new task from the summary" in nb and "Left      only left" in nb and "Next      do the thing" in nb
              and "Done      did x" in nb and "Verify    ran y" in nb and "old task" not in nb and "should be ignored" not in nb)
        check("memoagent: ... the index line follows; a second rewrite of a memo-agent Now is allowed", "task: new task from the summary" in HM.memo_path(str(r)).read_text() and F.cli(r, CA, "rewrite-now", "bb000000", stdin="Next      again").returncode == 0)
        F.put_sess(r, "dd000000", now="Written   2020-01-01 00:00\nTask      hand written long ago\nLeft      x", journal=[f"- {stamp(1)} · 1 Agent · done: newer receipt"])
        check("memoagent: ... a hand-written Now OLDER than the last receipt may be rewritten; a text with no Task/Left/Next line exits 2",
              F.cli(r, CA, "rewrite-now", "dd000000", stdin="Task      fresh").returncode == 0 and F.cli(r, CA, "rewrite-now", "dd000000", stdin="Done      only done").returncode == 2)
        # nothing outside this project, nothing outside the target was touched
        check("memoagent: the other project's memo changed only by the moved item", HM.memo_read(str(tgt))["Decisions"] == "" and HM.session_files(str(tgt)) == [])

        # ── the hard limit: guard ──
        mem = "abofles:abofles-memo"
        R = F.mkroot(); F.mkstate(CA, R); HM.memo_write(str(R), {"Needs you": "", "Decisions": "", "Sessions": ""}); F.put_sess(R, CA, now="Task      x")
        def g(tool, ti, agent_type=mem, cwd=None):
            pl = {"session_id": CA, "cwd": str(cwd or R), "tool_name": tool, "tool_input": ti}
            if agent_type:
                pl["agent_type"] = agent_type
            o = hrun("guard", pl)
            return isinstance(o, dict) and o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        ok_cmd = HM.shell_cmd("memo.py", "list", "--json", "--session", CA)
        mp = str(Path(HM.SCRIPTS_DIR) / "memo.py")
        denied = ["rm -rf .claude", "git status", "git commit -am x", "cat .claude/abofles-memo.md", "ls", f"{ok_cmd}; rm -rf x", f"{ok_cmd} && git push", f"{ok_cmd} | cat", f"{ok_cmd} > /tmp/x",
                  f"{ok_cmd} `id`", f"{ok_cmd} $(id)", f"{ok_cmd}\nrm -rf x", "python3 /tmp/other/memo.py list --json", f"python3 {mp}x list", f"bash -c '{ok_cmd}'",
                  HM.shell_cmd("memo.py", "scope", "all"), HM.shell_cmd("memo.py", "repair"), HM.shell_cmd("status.py"), f"python3 {mp} list --json &", f"python3 {mp}",
                  f"python3 {mp} rewrite-now ca000000 < /etc/passwd", f"python3 {mp} rewrite-now ca000000 <<'EOF'\nTask x\nEOF\nrm -rf x", f"python3 {mp} rewrite-now ca000000 <<EOF\nTask x\nEOF"]
        check("memoagent: guard denies rm, git, cat, ls, chained / piped / redirected / substituted commands, another memo.py, a memo.py subcommand outside its list, an unclosed or unquoted heredoc (agent_type abofles-memo)",
              all(g("Bash", {"command": c}) for c in denied))
        allowed = [ok_cmd, f"python3 {mp} list --json --session {CA}", f"python3 {mp} merge-decisions 'a@01-01T00:00' 'b@01-02T00:00' --into 'Q → A' --session {CA}",
                   HM.shell_cmd("memo.py", "drop-item", "#a1b2", "--reason", "noise, a duplicate", "--session", CA), HM.shell_cmd("memo.py", "move-item", "#a1b2", str(R), "--session", CA),
                   HM.shell_cmd("memo.py", "archive-session", "cc000000", "--session", CA),
                   f"python3 {mp} rewrite-now ca000000 --session {CA} <<'EOF'\nTask      do a; b | c && `d` $(e)\nLeft      x > y\nEOF", mp + f" list --json --session {CA}"]
        check("memoagent: ... and allows one call of this plugin's memo.py (through scripts/py, python or directly), a quoted heredoc with any text in its body", not any(g("Bash", {"command": c}) for c in allowed))
        planted = str(Path(F.hh, "python3"))
        check("memoagent: ... refuses a planted interpreter (any file named python3 or bash), and show and path (`show --scope all` reads other projects)",
              all(g("Bash", {"command": c}) for c in (f"{planted} {mp} list --json", f"{Path(F.hh, 'bash')} {HM.SCRIPTS_DIR / 'py'} {mp} list", HM.shell_cmd("memo.py", "show", "--scope", "all"), HM.shell_cmd("memo.py", "path", "mine"))))
        check("memoagent: ... other agent types and the main session are left alone (rm, git, chains)",
              not any(g("Bash", {"command": c}, agent_type=t) for t in ("abofles:abofles-worker", "abofles:abofles-reader", "x:abofles-memo-not", None) for c in ("rm -rf .claude", "git status", f"{ok_cmd}; ls")))
        check("memoagent: ... refuses a --session that is not the starting session's: another session's id, `--session=`, a second id, or none (memo.py trusts a session id before the folder)",
              all(g("Bash", {"command": c}) for c in (HM.shell_cmd("memo.py", "list", "--json", "--session", "bbbbbbbb-1111-2222-3333-444444444444"), f"python3 {mp} list --json --session={CA}",
                                                      f"python3 {mp} list --json", HM.shell_cmd("memo.py", "list", "--json", "--session", CA, "--session", "x"))))
        HM.compact_path(CA).parent.mkdir(parents=True, exist_ok=True); HM.compact_path(CA).write_text("# Compact summary\nx")
        check("memoagent: Read — a compact summary in the state folder only for this project's own sessions",
              not g("Read", {"file_path": str(HM.compact_path(CA))}) and g("Read", {"file_path": str(HM.compact_path("zz999999"))}) and g("Read", {"file_path": str(HM.COMPACT_DIR / ".." / "x.json")}))
        mf = Path(R, ".claude", "abofles-memo.md"); sf = HM.session_memo_path(str(R), CA)
        check("memoagent: Read — only .claude/abofles-memo* of this project: allowed for the main memo and a session file; denied for settings, /etc/passwd, a ../ escape, another project's memo, a sibling `abofles-memo-x`",
              not g("Read", {"file_path": str(mf)}) and not g("Read", {"file_path": str(sf)}) and not g("Read", {"file_path": str(HM.memo_dir(str(R)) / "x.compact.md")})
              and g("Read", {"file_path": str(Path(R, ".claude", "settings.json"))}) and g("Read", {"file_path": "/etc/passwd"}) and g("Read", {"file_path": str(Path(R, ".claude", "abofles-memo.d", "..", "settings.json"))})
              and g("Read", {"file_path": str(Path(tgt, ".claude", "abofles-memo.md"))}) and g("Read", {"file_path": str(Path(R, ".claude", "abofles-memo-x.md"))}) and g("Read", {"file_path": str(Path(R, "CLAUDE.md"))}))
        check("memoagent: ... Read by another agent type is not limited; Grep, Glob and connectors are denied for the memo agent", not g("Read", {"file_path": "/etc/passwd"}, agent_type="abofles:abofles-reader")
              and g("Grep", {"pattern": "x"}) and g("Glob", {"pattern": "*"}) and g("mcp__x__get_thing", {}))
        link = Path(R, ".claude", "abofles-memo.d", "linked.md")
        try:
            link.symlink_to("/etc/passwd")
            check("memoagent: ... a symlink inside the memo folder that points out is denied (realpath)", g("Read", {"file_path": str(link)}))
        except OSError:
            pass

        # ── routing: the memo agent is exempt like a gate agent ──
        def spawn(sid, root, model="haiku", effort="low", stype=mem, prompt=None, bg=True, resp=None):
            ti = {"subagent_type": stype, "prompt": prompt or f"abofles: Memo Upkeep / Work · T1\nSession id: {sid}.\nLine cap: 15", "description": "Memo upkeep", "run_in_background": bg}
            if model: ti["model"] = model
            if effort: ti["effort"] = effort
            return {"session_id": sid, "cwd": str(root), "tool_name": "Agent", "tool_input": ti, **({"tool_response": resp} if resp is not None else {})}
        denies = lambda o: isinstance(o, dict) and o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        ctx = lambda o: (o or {}).get("hookSpecificOutput", {}).get("additionalContext", "") if isinstance(o, dict) else ""
        launch = lambda aid: {"status": "async_launched", "agentId": aid, "prompt": "p", "description": "t"}
        # the agent gate (Agents = ask, unanswered) denies a worker spawn but not the memo agent
        S1 = "mm110000-1111-2222-3333-444444444444"
        r1 = F.mkroot(); HM.memo_write(str(r1), {"Needs you": "", "Decisions": "", "Sessions": ""})
        hrun("start", {"session_id": S1, "cwd": str(r1), "source": "startup"})
        wk = lambda sid, root, **kw: spawn(sid, root, "sonnet", "medium", "abofles:abofles-worker", "abofles: Unit Test / W · T2\nq\nLine cap: 40", False, **kw)
        check("memoagent: routing — the agent gate (unanswered) denies a worker agent, not the memo agent", denies(hrun("pre", wk(S1, r1))) and not denies(hrun("pre", spawn(S1, r1))))
        check("memoagent: ... its cell is fixed at Haiku 5.5 · Low (no effort, sonnet, opus high -> denied)", denies(hrun("pre", spawn(S1, r1, effort=None))) and denies(hrun("pre", spawn(S1, r1, model="sonnet"))) and denies(hrun("pre", spawn(S1, r1, model="opus", effort="high"))) and not denies(hrun("pre", spawn(S1, r1))))
        out = hrun("pre", spawn(S1, r1))
        check("memoagent: ... no main-line hand-over, no topic note ('Memo Upkeep' is no routing topic)", "new task" not in ctx(out) and "not in the index" not in ctx(out))
        # a fixed budget of 1: the memo agent takes none, a second worker is still over it
        S2 = "mm220000-1111-2222-3333-444444444444"
        r2 = F.mkroot(["Agents = 1"]); HM.memo_write(str(r2), {"Needs you": "", "Decisions": "", "Sessions": ""})
        hrun("start", {"session_id": S2, "cwd": str(r2), "source": "startup"})
        done = {"status": "completed", "agentId": "w1", "content": [{"type": "text", "text": "STATUS: done\nCONFIDENCE: high\nCHANGED:\n- a — b\nRAN: none\nLEFT: none"}]}
        hrun("pre", wk(S2, r2)); hrun("post", wk(S2, r2, resp=done))
        o_memo = hrun("pre", spawn(S2, r2)); hrun("post", spawn(S2, r2, resp=launch("mem1")))
        s2 = HM.load_state(S2)
        rows = HM.ledger_rows(S2)
        check("memoagent: a fixed budget of 1 is spent by one worker; the memo agent is still allowed and takes none (budget_used 1, spawns_since_receipt 1, task_memo 1)",
              not denies(o_memo) and HM.budget_used(s2) == 1 and s2.get("spawns_since_receipt") == 1 and s2.get("task_memo") == 1 and denies(hrun("pre", wk(S2, r2))))
        check("memoagent: ... the receipt header counts it apart (`1 Agent + 1 memo`); its ledger row is marked and is no running background agent",
              HM.spawn_word(s2) == "1 Agent + 1 memo" and any(x.get("memo") and x.get("status") == "pending" for x in rows) and not HM.background_running(S2) and HM.pending_count(S2) == 0)
        S2c = "mm22c000-1111-2222-3333-444444444444"
        r2c = F.mkroot(["Agents = off", "Parallel = 1"]); HM.memo_write(str(r2c), {"Needs you": "", "Decisions": "", "Sessions": ""})
        hrun("start", {"session_id": S2c, "cwd": str(r2c), "source": "startup"})
        bgw = wk(S2c, r2c); bgw["tool_input"]["run_in_background"] = True
        hrun("pre", bgw); hrun("post", {**bgw, "tool_response": launch("w9")})
        check("memoagent: a full Parallel cap refuses a worker, never the memo agent, which takes no slot", denies(hrun("pre", bgw)) and not denies(hrun("pre", spawn(S2c, r2c))))
        with open(HM.ledger_path(S2), "a") as fh:
            fh.write(json.dumps({"ts": int(time.time()) + 5, "agent": mem, "memo": True, "agent_id": "mem9", "side": "Work", "model": "haiku", "effort": "low", "status": "partial", "confidence": "low"}) + "\n")
        check("memoagent: ... its reply (partial / low) raises no escalation item in the guide", not any(str(i.get("id", "")).startswith("esc:") for i in HM.guide_entries(str(r2), HM.load_state(S2), S2)))
        # no receipt demanded for a turn that only spawned the memo agent; a worker spawn does demand one
        S3, S4 = "mm330000-1111-2222-3333-444444444444", "mm440000-1111-2222-3333-444444444444"
        for s_ in (S3, S4):
            hrun("start", {"session_id": s_, "cwd": str(r1), "source": "startup"})
        hrun("pre", spawn(S3, r1)); hrun("post", spawn(S3, r1, resp=launch("mem3")))
        o3 = hrun("receipt", {"session_id": S3, "cwd": str(r1), "last_assistant_message": "hello", "stop_hook_active": False})
        check("memoagent: a turn that only started the memo agent is not asked for a receipt", o3 is None and not HM.load_state(S3).get("receipt_owed"))
        S5 = "mm550000-1111-2222-3333-444444444444"
        r5 = F.mkroot(["Agents = auto"]); HM.memo_write(str(r5), {"Needs you": "", "Decisions": "", "Sessions": ""})
        hrun("start", {"session_id": S5, "cwd": str(r5), "source": "startup"})
        hrun("pre", wk(S5, r5)); hrun("post", wk(S5, r5, resp=done))
        o5 = hrun("receipt", {"session_id": S5, "cwd": str(r5), "last_assistant_message": "hello", "stop_hook_active": False})
        check("memoagent: ... while a worker agent does demand one (the control)", isinstance(o5, dict) and o5.get("decision") == "block" and "no receipt" in o5.get("reason", ""))

        # ── the due rule and the one line prompt() adds ──
        MA = "aa000000-1111-2222-3333-444444444444"
        def prompt_ctx(sid, root, text="hello"):
            return ctx(hrun("prompt", {"session_id": sid, "cwd": str(root), "prompt": text}))
        def fresh(overrides=None, flag=True, memo=True):
            root = F.mkroot(overrides)
            if memo:
                HM.memo_write(str(root), {"Needs you": "", "Decisions": "", "Sessions": ""})
            sid = f"ab{F.n:06d}-1111-2222-3333-444444444444"
            F.mkstate(sid, root, **({"memo_semantic_due": True} if flag else {}))
            return root, sid
        ro_ = F.mkroot(None)
        HM.memo_write(str(ro_), {"Needs you": "", "Decisions": "", "Sessions": ""})
        so_ = f"ab{F.n:06d}-1111-2222-3333-444444444444"
        F.mkstate(so_, ro_, memo_semantic_due=True, agents_at=1000)
        c0 = prompt_ctx(so_, ro_)
        check("memoagent: a session with no known agent list is not offered it either", HM.memo_agent_loaded({"started": 9999999999}) is False)
        check("memoagent: a session older than the agent's file gets no offer (the type is not in its list) and the window is not spent", "abofles-memo" not in c0 and not (HM.registry_read().get(str(ro_)) or {}).get("memo_agent_offered_at"))
        rd, sd = fresh()
        c1 = prompt_ctx(sd, rd)
        e1 = HM.registry_read()[str(rd)]
        check("memoagent: due rule — Memo agent = auto + the semantic flag: the prompt hook adds the memo-agent line with the type, haiku, low, background, the route line and the session id",
              "abofles:abofles-memo" in c1 and "model `haiku`" in c1 and "effort `low`" in c1 and "run_in_background true" in c1 and f"Session id: {sd}" in c1
              and "🗂️ `Memo` → **Memo Upkeep** · Haiku 5.5 · Low · Cap 15 · 🟢 Minor" in c1 and "abofles: Memo Upkeep / Work · T1" in c1 and "memo.py" in c1 and f"--session {sd}" in c1 and "Line cap: 15" in c1)
        c2 = prompt_ctx(sd, rd, "again")
        check("memoagent: ... it fires once: the offer is recorded (memo_agent_offered_at) and the next prompts print nothing, though the agent never happened and the flag is still set",
              e1.get("memo_agent_offered_at") and "memo_agent_at" not in e1 and "abofles-memo" not in c2 and HM.load_state(sd).get("memo_semantic_due") is True and not HM.memo_agent_due(str(rd), HM.load_state(sd)))
        reg = HM.registry_read(); reg[str(rd)]["memo_agent_offered_at"] = int(time.time()) - 7 * 3600; Path(F.sdir, "memo-index.json").write_text(json.dumps(reg))
        check("memoagent: ... 7 h after the offer (Memo agent every = 6) it is offered again, once", "abofles:abofles-memo" in prompt_ctx(sd, rd) and "abofles-memo" not in prompt_ctx(sd, rd))
        rd2, sd2 = fresh(["Memo agent every = 12"])
        reg = HM.registry_read(); reg[str(rd2)] = {**reg[str(rd2)], "memo_agent_at": int(time.time()) - 7 * 3600}; Path(F.sdir, "memo-index.json").write_text(json.dumps(reg))
        c = prompt_ctx(sd2, rd2)
        reg[str(rd2)]["memo_agent_at"] = int(time.time()) - 13 * 3600; Path(F.sdir, "memo-index.json").write_text(json.dumps(reg))
        check("memoagent: ... `Memo agent every = 12`: 7 h after the last run nothing, 13 h after it is due", "abofles-memo" not in c and "abofles:abofles-memo" in prompt_ctx(sd2, rd2))
        rp_, sp_ = fresh()
        o_ = hrun("pre", spawn(sp_, rp_))
        e_ = HM.registry_read()[str(rp_)]
        check("memoagent: the agent (pre hook) records memo_agent_at, clears the session flag and the project's semantic_due; the throttle then holds",
              not denies(o_) and e_.get("memo_agent_at") and HM.load_state(sp_).get("memo_semantic_due") is None and e_.get("semantic_due") is False and "abofles-memo" not in prompt_ctx(sp_, rp_))
        HM.registry_set(str(rp_), last_health_at=int(time.time()), semantic_due=True)
        check("memoagent: ... the health's due_semantic alone makes it due once the window is over, not before", not HM.memo_agent_due(str(rp_), {"agents_at": int(time.time())}) and HM.memo_agent_due(str(rp_), {"agents_at": int(time.time())}, now=time.time() + 7 * 3600))
        for val in ("manual", "off"):
            rv, sv = fresh([f"Memo agent = {val}"])
            check(f"memoagent: Memo agent = {val} -> prints nothing, records nothing", "abofles-memo" not in prompt_ctx(sv, rv) and "memo_agent_offered_at" not in HM.registry_read()[str(rv)])
        rn, sn = fresh(flag=False)
        check("memoagent: no flag and no due_semantic -> nothing; no memo in the project -> nothing", "abofles-memo" not in prompt_ctx(sn, rn) and "abofles-memo" not in prompt_ctx(*(lambda x: (x[1], x[0]))(fresh(memo=False))))
        # not during an open judged question or an open Stop-hook demand, nor while one runs
        for what, kw in (("a judged question is open (judge_ok)", {"judge_ok": 1}), ("a receipt is owed", {"receipt_owed": True}), ("an explanation is armed", {"explain_md": True})):
            rb, sb_ = fresh()
            F.mkstate(sb_, rb, memo_semantic_due=True, **kw)
            check(f"memoagent: not offered while {what}", "abofles-memo" not in prompt_ctx(sb_, rb) and "memo_agent_offered_at" not in HM.registry_read()[str(rb)])
        rr, sr = fresh()
        with open(HM.ledger_path(sr), "a") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "agent": mem, "memo": True, "status": "pending"}) + "\n")
        check("memoagent: ... nor while a memo agent of the session is running", "abofles-memo" not in prompt_ctx(sr, rr))
        rj, sj = fresh()
        with open(HM.ledger_path(sj), "a") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "agent": "abofles:abofles-judge", "gate": "judge", "status": "pending"}) + "\n")
        check("memoagent: ... nor while a Judge of the session is still running", "abofles-memo" not in prompt_ctx(sj, rj))
        rt, st_ = fresh()
        check("memoagent: a task-notification prompt is not the place for it", "abofles-memo" not in prompt_ctx(st_, rt, "<task-notification>x</task-notification>"))
    finally:
        F.close()


def memo_repair_tests(check):
    """`memo.py repair` (backup, dry run, a misfiled Jamkhana-style session, unsure sessions, untagged items, idempotence, --all) and `Memo repair = auto`. Own temp projects and state dir."""
    F = _MemoFixture("memorepair")
    HM = F.HM
    try:
        stamp = lambda days=0: time.strftime("%m-%d %H:%M", time.localtime(time.time() - days * 86400))
        proj_files = lambda *roots: {k: v for k, v in F.snap(*roots).items() if k != "registry" and not k.startswith(str(F.sdir))}
        backups_of = lambda r: sorted(p.name for p in HM.memo_dir(str(r)).glob("backup-*"))
        DRP = F.mkroot(git=False, name="DRP")  # a plain folder the sessions were launched in
        JAM = F.mkroot(name="Jamkhana")  # the repository they edited
        JJ, KK, UU, MM = ("jj000001-1111-2222-3333-444444444444", "kk000001-1111-2222-3333-444444444444", "uu000001", "mm000001-1111-2222-3333-444444444444")

        def misfile(drp, jam, sid):
            F.mkstate(sid, drp, work={str(jam): 6})
            F.put_sess(drp, sid, now="Written   2025-01-01 10:00 · from receipt\nTask      the Jamkhana export", journal=[f"- {stamp(1)} · 1 Agent · done: export"], decisions=[f"- {stamp(2)} · by:you · Keep the export? → yes"])
            (HM.memo_dir(str(drp)) / f"{sid[:8]}.compact.md").write_text(f"# Compact summary\nof {sid[:8]}")
            HM.memo_needs(str(drp), sid, f"owner decision: ship the {sid[:8]} export?", project=str(jam))

        misfile(DRP, JAM, JJ)
        F.mkstate(KK, DRP)  # launched here, no edits: it belongs here
        F.put_sess(DRP, KK, now="Task      stays")
        F.put_sess(DRP, UU, now="Task      no state file for me")
        F.mkstate(MM, DRP, work={str(Path(F.hh, "Missing")): 5})  # its repository is gone
        F.put_sess(DRP, MM, now="Task      repository missing")
        sec = HM.memo_read(str(DRP))
        sec["Needs you"] += "\n3. [ ] 11-01 10:00 · follow up on kk000001 export\n4. [ ] 11-01 10:00 · call the bank about the fee"
        sec["Needs you"] = "\n".join(HM.renumber(sec["Needs you"].splitlines()))
        HM.memo_write(str(DRP), sec)
        ship_before = next(l for l in HM.memo_read(str(DRP))["Needs you"].splitlines() if "jj000001 export" in l)
        main_before = HM.memo_path(str(DRP)).read_bytes()
        jj_before = HM.session_memo_path(str(DRP), JJ).read_bytes()
        # ── dry run: not one byte changes ──
        sb = F.snap(DRP, JAM)
        d = F.raw_cli(DRP, "repair", "--dry-run", "--root", str(DRP))
        check("repair: --dry-run changes no byte (memos, registry, states), makes no backup, creates nothing in the other project", d.returncode == 0 and F.snap(DRP, JAM) == sb and backups_of(DRP) == [] and not Path(JAM, ".claude").exists())
        check("repair: ... and prints what would change: the move with its basis, the item tag, the sessions it is unsure about, the untagged item it leaves",
              "dry run, nothing written" in d.stdout and "session jj000001 → " + str(JAM) in d.stdout and "6 of 6 edits in Jamkhana" in d.stdout and "tagged s:kk000001 p:DRP" in d.stdout
              and "left alone: session uu000001 — no state file" in d.stdout and "left alone: session mm000001 — its project" in d.stdout and "call the bank" in d.stdout and "backup:" not in d.stdout)
        # ── the real run ──
        out = F.raw_cli(DRP, "repair", "--root", str(DRP))
        bk = backups_of(DRP)
        check("repair: a real run exits 0 and prints the summary with the backup path", out.returncode == 0 and len(bk) == 1 and re.fullmatch(r"backup-\d{8}-\d{4}", bk[0]) and f"backup: {HM.memo_dir(str(DRP)) / bk[0]}" in out.stdout)
        bdir = HM.memo_dir(str(DRP)) / bk[0]
        check("repair: ... the backup holds the main memo and the memo folder as they were before (bytes equal), no earlier backup nested",
              (bdir / "abofles-memo.md").read_bytes() == main_before and (bdir / "abofles-memo.d" / "jj000001.md").read_bytes() == jj_before and not list(bdir.rglob("backup-*")))
        check("repair: a Jamkhana-style session (launched in a non-git folder, its edits in a repository) moves there: file, index line (its compact summary goes to the state folder); gone from the old project",
              HM.session_memo_path(str(JAM), JJ).exists() and HM.compact_path(JJ).read_text().endswith("of jj000001") and not (HM.memo_dir(str(JAM)) / "jj000001.compact.md").exists() and not HM.session_memo_path(str(DRP), JJ).exists()
              and not (HM.memo_dir(str(DRP)) / "jj000001.compact.md").exists() and "jj000001" in [k for k, _ in HM.index_entries(HM.memo_read(str(JAM)))] and "jj000001" not in [k for k, _ in HM.index_entries(HM.memo_read(str(DRP)))])
        moved_line = next((l for l in HM.memo_read(str(JAM))["Needs you"].splitlines() if "jj000001 export" in l), "")
        check("repair: ... its open item moves with its text, #id, stamp and tags kept; the session's Journal and Decisions are intact with a `moved here from DRP` line",
              re.sub(r"^\d+\.", "", moved_line) == re.sub(r"^\d+\.", "", ship_before) and "(s:jj000001 p:Jamkhana)" in moved_line and "jj000001 export" not in HM.memo_read(str(DRP))["Needs you"]
              and "Keep the export? → yes" in HM.sess_read(str(JAM), JJ)["Decisions"] and "moved here from DRP: repair:" in HM.sess_read(str(JAM), JJ)["Journal"] and "done: export" in HM.sess_read(str(JAM), JJ)["Journal"])
        check("repair: ... its state follows (project = the repository), so the next receipt writes there, not into a new file in the old folder", HM.load_state(JJ).get("project") == str(JAM) and HM.load_state(JJ).get("work_claimed") is True)
        check("repair: the destination is backed up before it changes", len(backups_of(JAM)) == 1 and (HM.memo_dir(str(JAM)) / backups_of(JAM)[0]).is_dir())
        check("repair: a session with no edits stays; one with no state file and one whose repository is missing stay and are reported as unsure",
              all(HM.session_memo_path(str(DRP), s_).exists() for s_ in (KK, UU, MM)) and "left alone: session uu000001 — no state file" in out.stdout and "left alone: session mm000001 — its project" in out.stdout and "session kk000001" not in out.stdout.replace("tagged s:kk000001", ""))
        nl = HM.memo_read(str(DRP))["Needs you"].splitlines()
        fk = next(l for l in nl if "kk000001 export" in l); bank = next(l for l in nl if "call the bank" in l)
        check("repair: an untagged item that names a session file's id gets that session's tag (id kept, stamp and text unchanged); one that names none is left as it was and reported",
              re.search(r"#[0-9a-f]{4} \(s:kk000001 p:DRP\)$", fk) and fk.startswith("1. [ ] 11-01 10:00 · follow up on kk000001 export #" + HM.need_id("2. [ ] 11-01 10:00 · follow up on kk000001 export")) and bank == "2. [ ] 11-01 10:00 · call the bank about the fee"
              and "1 untagged item naming no session" in out.stdout and "call the bank" in out.stdout)
        # ── backups are not read by anything else ──
        files = [p.stem for p in HM.session_files(str(DRP))]
        h = HM.memo_health(str(DRP), sid="")
        check("repair: the backup is ignored by later scans — session_files, archived_sids and memo_health see no copy of its files (no orphans, nothing archived)",
              sorted(files) == sorted(["kk000001", "uu000001", "mm000001"]) and HM.archived_sids(str(DRP)) == set() and h["orphans"] == 0 and not h["archived_sessions"] and (bdir / "abofles-memo.d" / "jj000001.md").read_bytes() == jj_before)
        b2 = HM.memo_backup(str(DRP)); b3 = HM.memo_backup(str(DRP))
        check("repair: a backup never contains an earlier backup, and two in one minute get different folders", not list(b2.rglob("backup-*")) and b2 != b3 and b3.exists())
        shutil.rmtree(b2); shutil.rmtree(b3)
        # ── a second run reports nothing to do, changes nothing, makes no backup ──
        pf = proj_files(DRP, JAM)
        out2 = F.raw_cli(DRP, "repair", "--root", str(DRP))
        check("repair: a second run reports nothing to do (the sessions it is unsure about are still named), writes no byte in either project, makes no new backup",
              out2.returncode == 0 and "nothing to do" in out2.stdout and "backup:" not in out2.stdout and proj_files(DRP, JAM) == pf and backups_of(DRP) == bk and "→ " + str(JAM) not in out2.stdout)
        # ── only unsure sessions: nothing to do, no backup ──
        rU = F.mkroot(git=False); F.put_sess(rU, "ee000001", now="Task      lonely")
        outU = F.raw_cli(rU, "repair", "--root", str(rU))
        check("repair: a project whose only finding is an unsure session changes nothing and makes no backup", "left alone: session ee000001 — no state file" in outU.stdout and "nothing to do" in outU.stdout and backups_of(rU) == [])
        check("repair: usage errors exit 2 (--all with --root, a folder that is none, an unknown flag)",
              all(F.raw_cli(DRP, "repair", *a).returncode == 2 for a in (["--all", "--root", str(DRP)], ["--root", str(Path(F.hh, "nope"))], ["--bogus"], ["--root"])))
        # ── --all walks the registry ──
        dA, jA = F.mkroot(git=False), F.mkroot(); dB, jB = F.mkroot(git=False), F.mkroot()
        SA, SB = "aa111111-1111-2222-3333-444444444444", "bb222222-1111-2222-3333-444444444444"
        misfile(dA, jA, SA); misfile(dB, jB, SB)
        gone = F.mkroot(git=False); F.put_sess(gone, "ff000001", now="Task      x"); shutil.rmtree(gone)  # a registry entry whose folder is gone
        sb = F.snap(dA, dB, jA, jB)
        da = F.raw_cli(dA, "repair", "--all", "--dry-run")
        check("repair: --all --dry-run walks every project of the registry (both misfiled sessions named, the missing folder skipped) and writes nothing", da.returncode == 0 and F.snap(dA, dB, jA, jB) == sb
              and f"aa111111 → {jA}" in da.stdout and f"bb222222 → {jB}" in da.stdout and str(gone) not in da.stdout)
        ra = F.raw_cli(dA, "repair", "--all")
        check("repair: --all repairs each project once: both sessions moved, one backup per touched project, one summary per project",
              ra.returncode == 0 and HM.session_memo_path(str(jA), SA).exists() and HM.session_memo_path(str(jB), SB).exists() and not HM.session_memo_path(str(dA), SA).exists() and not HM.session_memo_path(str(dB), SB).exists()
              and len(backups_of(dA)) == 1 and len(backups_of(dB)) == 1 and len(backups_of(jA)) == 1 and ra.stdout.count("backup:") >= 4 and ra.stdout.count(f"({dA})") == 1 and ra.stdout.count(f"({dB})") == 1)
        again = F.raw_cli(dA, "repair", "--all")
        check("repair: ... and a second --all finds nothing to do anywhere (no move, no backup line)", again.returncode == 0 and "nothing to do" in again.stdout and "→ /" not in again.stdout and "backup:" not in again.stdout)
        # ── Memo repair = auto: start() runs it once per project, silent, failing open ──
        calls = []
        real = HM.memo_repair
        def counting(root, *a, **kw):
            calls.append(os.path.realpath(root)); return real(root, *a, **kw)
        HM.memo_repair = counting
        def do_start(root, sid, src="startup"):
            buf = io.StringIO()
            import contextlib as _cl
            with _cl.redirect_stdout(buf):
                HM.start({"session_id": sid, "cwd": str(root), "source": src})
            return buf.getvalue()
        try:
            dC, jC = F.mkroot(["Memo repair = auto"], git=False), F.mkroot()
            SC = "cc333333-1111-2222-3333-444444444444"
            misfile(dC, jC, SC)
            out1 = do_start(dC, "st000001-1111-2222-3333-444444444444")
            n1 = calls.count(str(dC))
            do_start(dC, "st000002-1111-2222-3333-444444444444"); do_start(dC, "st000001-1111-2222-3333-444444444444", "resume")
            check("repair: Memo repair = auto — start() repairs the project once: the session moved, a backup made, `repair_at` recorded, never again at later starts",
                  n1 == 1 and calls.count(str(dC)) == 1 and HM.session_memo_path(str(jC), SC).exists() and not HM.session_memo_path(str(dC), SC).exists() and len(backups_of(dC)) == 1
                  and HM.registry_read()[str(dC)].get("repair_at") and "Traceback" not in out1 and "backup" not in out1)
            dD, jD = F.mkroot(git=False), F.mkroot()
            SD = "dd444444-1111-2222-3333-444444444444"
            misfile(dD, jD, SD)
            do_start(dD, "st000003-1111-2222-3333-444444444444")
            check("repair: ... with the default (Memo repair = manual) start() does not repair", str(dD) not in calls and HM.session_memo_path(str(dD), SD).exists() and backups_of(dD) == [])
            def boom(root, *a, **kw):
                raise RuntimeError("disk on fire")
            HM.memo_repair = boom
            dE = F.mkroot(["Memo repair = auto"], git=False); F.put_sess(dE, "ee555555", now="Task      x")
            try:
                oute = do_start(dE, "st000004-1111-2222-3333-444444444444"); failed = False
            except Exception:
                oute, failed = "", True
            check("repair: ... a repair that fails never fails the start and says nothing (fail open, silent)", not failed and "disk on fire" not in oute and "Traceback" not in oute)
        finally:
            HM.memo_repair = real
    finally:
        F.close()


def main():
    fails = 0
    def check(name, cond):
        nonlocal fails
        print(("ok   " if cond else "FAIL ") + name)
        if not cond: fails += 1

    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state:
        os.environ["ABOFLES_STATE_DIR"] = state
        os.environ["ABOFLES_TEST_CWD"] = cwd
        os.environ["ABOFLES_USER_CLAUDE_MD"] = str(Path(state, "no-user-claude.md"))
        os.environ["ABOFLES_CONFIG"] = str(Path(state, "config.json"))
        os.environ["ABOFLES_CLAUDE_HOME"] = str(Path(state, "claude-home"))  # no ~/.claude/settings.json, no plugin cache: the abofles-guide mod counts as not installed (resume prints as before)
        os.environ.pop("ABOFLES_RECEIPT", None)
        Path(cwd, "src").mkdir(); Path(cwd, "src/a.cs").write_text("x\n" * 30)
        def pin(sid=None):  # the scripts pick the session for a command: ABOFLES_SESSION, else the newest prompt among this folder's sessions
            os.environ.pop("ABOFLES_SESSION", None)
            if sid: os.environ["ABOFLES_SESSION"] = sid
        pin()
        reason = lambda out: (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        import importlib.util as _iu0
        _hs0 = _iu0.spec_from_file_location("abofles_hook_t0", str(HOOK)); _H0 = _iu0.module_from_spec(_hs0); _hs0.loader.exec_module(_H0)
        H_flat = _H0.explain_flat
        allowed = lambda out: out is None or (out or {}).get("hookSpecificOutput", {}).get("permissionDecision") != "deny"  # a spawn note is context only (no permissionDecision); the first spawn of a task may carry the `main:` line
        ctx = lambda out: (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
        # post output minus the receipt note it always carries
        import re as _re
        warn = lambda out: "\n\n".join(_re.sub(r"(; )?\d+ agents this session so far[^;]*|(; )?(1 agent left in|agent budget \d+ reached)[^;]*", "", p).replace("abofles: ", "", 1).strip() for p in ctx(out).split("\n\n") if not p.startswith(("abofles: Receipt", "abofles: no receipt")))

        # pre
        check("pre: missing effort blocked", "effort=missing" in reason(run("pre", agent("opus"), led)))
        check("pre: missing model blocked", "model=missing" in reason(run("pre", agent(effort="low"), led)))
        check("pre: bad effort blocked", "effort=ultra" in reason(run("pre", agent("opus", "ultra"), led)))
        check("pre: fable on reader blocked", "fable is only for" in reason(run("pre", agent("fable", "medium"), led)))
        _fa = run("pre", agent("fable", "medium", "abofles:abofles-analyst"), led)
        check("pre: fable on analyst allowed (an advisory cell note at most)", _fa is None or ("permissionDecision" not in json.dumps(_fa) and "× the cell" in ctx(_fa)))
        check("pre: good call allowed", run("pre", agent("haiku", "low"), led) is None)
        check("pre: skip bypass", run("pre", agent(prompt="abofles:skip x", stype="Explore"), led) is None)
        check("pre: non-Agent tool ignored", run("pre", {"tool_name": "Bash", "tool_input": {}}, led) is None)

        # post
        good = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: none"
        check("post: good reply, no warning", warn(run("post", agent("haiku", "low", resp=good, cwd=cwd), led)) == "")
        out = run("post", agent("opus", "high", "abofles:abofles-analyst", resp="just prose"), led)
        check("post: missing header warned", "no STATUS/CONFIDENCE header" in ctx(out))
        out = run("post", agent("sonnet", "medium", "abofles:abofles-worker", resp="STATUS: partial\nCONFIDENCE: low\nCHANGED:\n- a"), led)
        check("post: partial/low -> escalation note", "escalation rule applies" in ctx(out))
        liar = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: enclosing method names were not read for 7 callers"
        out = run("post", agent("haiku", "low", resp=liar, cwd=cwd), led)
        check("post: done+GAPS admits -> partial", "downgraded to partial" in ctx(out) and "escalation rule applies" in ctx(out))
        badpath = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/Rules/a.cs:1 — x\n- src/a.cs:2 — y\nGAPS: none"
        out = run("post", agent("haiku", "low", resp=badpath, cwd=cwd), led)
        check("post: reader bad path -> low", "paths not found: src/Rules/a.cs" in ctx(out) and "src/a.cs" not in ctx(out).split("paths not found:")[1].split(";")[0].replace("src/Rules/a.cs", ""))
        out = run("post", agent("opus", "high", "abofles:abofles-analyst", resp="STATUS: done\nCONFIDENCE: high\nFINDINGS:\n1. [low] nowhere/x.cs:1 — y — z\nRECOMMENDATION: none"), led)
        check("post: analyst bad path not checked", warn(out) == "")
        hy = Path(cwd, "my-repo", "Application", "Finance"); hy.mkdir(parents=True); Path(hy, "Rules.cs").write_text("x")
        hcwd = str(Path(cwd, "my-repo"))
        absrep = f"STATUS: done\nCONFIDENCE: high\nFACTS:\n- {hcwd}/Application/Finance/Rules.cs:168 — x\n- Application/Finance/Rules.cs:223 — y\nGAPS: none"
        out = run("post", agent("sonnet", "low", resp=absrep, cwd=hcwd), led)
        check("post: absolute path with hyphen folder accepted", "paths not found" not in ctx(out) and "absolute paths in reply" in ctx(out))
        out = run("post", agent("opus", "high", "abofles:abofles-verifier", resp="STATUS: done\nCONFIDENCE: high\nVERDICT: FAIL\nEVIDENCE:\n- c1 — FAIL — src/a.cs:1\nISSUES:\nNOTE: none", cwd=cwd), led)
        check("post: verdict FAIL warned", "VERDICT: FAIL" in ctx(out))
        out = run("post", agent("opus", "high", "abofles:abofles-verifier", resp="STATUS: done\nCONFIDENCE: medium\nVERDICT: PASS\nEVIDENCE:\n- c1 — PASS — src/a.cs:1\nISSUES:\n1. [critical] src/a.cs:9 — USD cap — use IQD\nNOTE: ATTENTION: cap applied in order currency", cwd=cwd), led)
        check("post: verifier ATTENTION warned", "ATTENTION" in ctx(out))
        longline = "STATUS: done\nCONFIDENCE: high\nPLAN:\n1. " + "word " * 120 + "\nRISKS:\n- " + "risk " * 100
        out = run("post", agent("opus", "xhigh", "abofles:abofles-planner", resp=longline, cwd=cwd), led)
        check("post: long lines warned", "too long" in ctx(out))
        over = "STATUS: done\nCONFIDENCE: high\nFACTS:\n" + "\n".join(f"- src/a.cs:{i} — f" for i in range(60)) + "\nGAPS: none"
        out = run("post", agent("haiku", "low", resp=over, prompt="abofles: Unit Test / R · T2\nq\nLine cap: 20", cwd=cwd), led)
        check("post: over cap warned", "cap was 20" in ctx(out))

        oos = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: .worktrees/ copy not searched (out of scope)"
        out = run("post", agent("haiku", "low", resp=oos, cwd=cwd), led)
        check("post: out-of-scope GAPS not downgraded", warn(out) == "")
        trail = "STATUS: done\nCONFIDENCE: high\nPLAN:\n1. s — files: src/a.cs — why: x\nRISKS:\n- r\nVERIFY: cmd\nOPEN: none\nFiles: src/a.cs\nThat is the plan."
        out = run("post", agent("opus", "xhigh", "abofles:abofles-planner", resp=trail, cwd=cwd), led)
        check("post: trailing text warned", "2 line(s) after the last section" in ctx(out))
        absr = "STATUS: done\nCONFIDENCE: high\nCHANGED:\n- /Users/x/my-repo/src/a.cs — y\nRAN: none\nLEFT: none"
        out = run("post", agent("sonnet", "medium", "abofles:abofles-worker", resp=absr, cwd=cwd), led)
        check("post: absolute path in worker warned", "absolute paths in reply" in ctx(out))
        # async launch -> pending, no checks; SubagentStop fills it
        launch = {"status": "async_launched", "agentId": "ag1", "prompt": "/Users/x/abs prompt echo", "description": "t"}
        out = run("post", agent("haiku", "low", resp=launch, prompt="abofles: Unit Test / R · T2\nq\nLine cap: 40", cwd=cwd), led)
        check("post: async launch -> no warnings", warn(out) == "")
        stop_in = {"session_id": "test", "cwd": cwd, "agent_id": "ag1", "agent_type": "abofles:abofles-reader",
                   "last_assistant_message": "STATUS: partial\nCONFIDENCE: medium\nFACTS:\n- src/a.cs:1 — x\n+3 more: y\nGAPS: cap reached"}
        out = run("stop", stop_in, led)
        check("stop: fills pending and prints NOTHING (output would reach the subagent)", out is None)
        out = run("stop", dict(stop_in, agent_id="unknown"), led)
        check("stop: unknown agent id ignored", out is None)
        # grep count pasted as a line number
        out = run("post", agent("haiku", "low", resp="STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:160 — 160 hits\nGAPS: none", cwd=cwd), led)
        check("post: line past end of file -> low", "line past end of file" in ctx(out))
        # planner partial with OPEN questions -> owner input, not escalation
        plan = "STATUS: partial\nCONFIDENCE: medium\nPLAN:\n1. s (files: src/a.cs)\nRISKS:\n- r\nVERIFY: cmd\nOPEN: USD scope? default: IQD only"
        out = run("post", agent("opus", "xhigh", "abofles:abofles-planner", resp=plan, cwd=cwd), led)
        check("post: planner OPEN -> owner input note", "owner input needed" in ctx(out) and "escalation rule" not in ctx(out))
        # Problem 1: ATTENTION text with "skipped" must not downgrade the verifier
        att = "STATUS: done\nCONFIDENCE: medium\nVERDICT: PASS\nEVIDENCE:\n- c1 — PASS — src/a.cs:1\nISSUES:\n1. [critical] src/a.cs:9 — cap silently skipped on non-IQD\n   fix: compare in IQD\nNOTE: ATTENTION: cap is silently skipped on every non-IQD order at src/a.cs:9"
        out = run("post", agent("opus", "high", "abofles:abofles-verifier", resp=att, cwd=cwd), led)
        check("post: ATTENTION 'skipped' not downgraded", "downgraded" not in ctx(out) and "escalation rule" not in ctx(out) and "ATTENTION" in ctx(out))
        out = run("post", agent("haiku", "low", resp=good, prompt="abofles: Unit Test / Read · T2\nq\nLine cap: 40", cwd=cwd), led)
        check("post: full-word side parsed", warn(out) == "")
        okgaps = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: " + "g" * 300
        out = run("post", agent("haiku", "low", resp=okgaps, cwd=cwd), led)
        check("post: 300-char GAPS allowed", warn(out) == "")
        # multi-question OPEN is not trailing text
        open3 = "STATUS: done\nCONFIDENCE: medium\nPLAN:\n1. s (files: src/a.cs)\nRISKS:\n- r\nVERIFY: cmd\nOPEN:\n- USD in scope? assumed: IQD only\n- delivery cap too? assumed: no\n- branch? assumed: current"
        out = run("post", agent("opus", "xhigh", "abofles:abofles-planner", resp=open3, cwd=cwd), led)
        check("post: 3-question OPEN not trailing", "after the last section" not in ctx(out or {}) )
        # literal +N more
        lit = "STATUS: partial\nCONFIDENCE: medium\nFACTS:\n- src/a.cs:1 — x\n+N more: ...\nGAPS: cap"
        out = run("post", agent("haiku", "low", resp=lit, cwd=cwd), led)
        check("post: literal +N more warned", "literal `+N more:`" in ctx(out))
        # long real overflow line allowed up to 400
        ov = "STATUS: partial\nCONFIDENCE: medium\nFACTS:\n- src/a.cs:1 — x\n+207 more: " + ", ".join(f"File{i}.cs ({i})" for i in range(20)) + "\nGAPS: cap"
        out = run("post", agent("haiku", "low", resp=ov, cwd=cwd), led)
        check("post: 300-char overflow line allowed", "too long" not in ctx(out or {}))
        # content lines vs cap: 12 content lines against cap 8 must warn; labels and header do not count
        capr = "STATUS: partial\nCONFIDENCE: high\nFACTS:\n" + "\n".join(f"- src/a.cs:{i} — f" for i in range(12)) + "\nGAPS: cap"
        out = run("post", agent("haiku", "low", resp=capr, prompt="abofles: Unit Test / Read · T2\nq\nLine cap: 8", cwd=cwd), led)
        check("post: 12 content lines vs cap 8 warned", "over the cap" in ctx(out))
        capok = "STATUS: done\nCONFIDENCE: high\nFACTS:\n" + "\n".join(f"- src/a.cs:{i} — f" for i in range(8)) + "\nGAPS: none"
        out = run("post", agent("haiku", "low", resp=capok, prompt="abofles: Unit Test / Read · T2\nq\nLine cap: 8", cwd=cwd), led)
        check("post: 8 content lines vs cap 8 ok", warn(out) == "")
        # orchestrator guard: silent without the SessionStart marker; 3 reads silent, 4th warns via additionalContext; a spawn resets
        g = lambda sid="test": run("guard", {"session_id": sid, "cwd": cwd, "tool_name": "Grep", "tool_input": {}}, led)
        check("guard: no session marker -> silent", g("nosession") is None)
        run("start", {"session_id": "test", "cwd": cwd}, led)
        r1, r2, r3, r4 = g(), g(), g(), g()
        check("guard: 3 silent, 4th warns", r1 is None and r3 is None and "direct reads in a row" in (r4 or {}).get("hookSpecificOutput", {}).get("additionalContext", ""))
        check("guard: the warning is context only — no permissionDecision, so the user's permission prompt stays", "permissionDecision" not in json.dumps(r4 or {}))
        check("guard: warning reaches the model (additionalContext)", "direct reads in a row" in ctx(r4))
        check("guard: 5th silent (warned once per threshold)", g() is None)
        gb = lambda cmd: run("guard", {"session_id": "test", "cwd": cwd, "tool_name": "Bash", "tool_input": {"command": cmd}}, led)
        gb("dotnet test TestLab")  # non-read Bash breaks the streak
        r = [gb("grep -rn Surcharge Application"), gb("cd /x && find . -name '*.cs'"), gb("sed -n '100,120p' a.cs"), gb("cat a.cs")]
        check("guard: Bash reads count, 4th warns", r[0] is None and r[2] is None and "direct reads in a row" in ctx(r[3]))
        check("guard: non-read Bash resets streak", gb("dotnet build") is None and gb("grep x y") is None)
        run("post", agent("haiku", "low", resp=good, cwd=cwd), led)
        check("guard: agent resets streak", g() is None)
        # "Main session only" (gate answer, budget 0 this task): the user chose direct reads — no nudge to spawn a Read agent
        run("start", {"session_id": "gmain", "cwd": cwd}, led)
        _sp_ = Path(state, "gmain.json"); _stm = json.loads(_sp_.read_text()); _stm.update({"gate": "task", "task_budget": 0}); _sp_.write_text(json.dumps(_stm))
        check("guard: silent under 'Main session only'", all(g("gmain") is None for _ in range(9)))
        _stm = json.loads(_sp_.read_text()); _stm.update({"gate": "auto", "task_budget": None}); _sp_.write_text(json.dumps(_stm))
        _ra = [g("gmain") for _ in range(4)]
        check("guard: gate back to auto — streak starts fresh (3 silent, 4th warns)", _ra[0] is None and _ra[2] is None and "direct reads in a row" in ctx(_ra[3]))
        # launcher: hooks.json runs hooks/run.py (bytecode cached); same output as the script
        LAUNCH = HOOK.parent / "run.py"
        _rl = subprocess.run([sys.executable, str(LAUNCH), "guard"], input=json.dumps({"session_id": "gmain", "cwd": cwd, "tool_name": "Grep", "tool_input": {}}), capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led))
        check("launcher: run.py guard exits 0 and is silent like the script", _rl.returncode == 0 and _rl.stdout.strip() == "")
        _hj = json.loads((HOOK.parent / "hooks.json").read_text())
        _cmds = [h["command"] for ev in _hj["hooks"].values() for grp in ev for h in grp["hooks"] if h["type"] == "command" and "run.py" in h["command"]]
        check("launcher: every python hook command runs hooks/run.py", _cmds and all("hooks/run.py" in c for c in _cmds))
        check("launcher: SessionStart text asks for the skill once per session, not per task", "once per session" in _hj["hooks"]["SessionStart"][0]["hooks"][0]["command"] and "start of each task" not in _hj["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        # prune: state and ledger files untouched for 60 days go at SessionStart; the registry and fresh files stay
        _old = time.time() - 61 * 86400
        for _f in (Path(state, "oldsess.json"), Path(led, "oldsess.jsonl"), Path(state, "memo-index.json")):
            _f.write_text("{}" if _f.suffix == ".json" else "")
            os.utime(_f, (_old, _old))
        Path(state, "freshsess.json").write_text("{}")
        run("start", {"session_id": "prune", "cwd": cwd}, led)
        check("prune: 61-day-old state and ledger files deleted at SessionStart", not Path(state, "oldsess.json").exists() and not Path(led, "oldsess.jsonl").exists())
        check("prune: memo-index.json and fresh state files kept", Path(state, "memo-index.json").exists() and Path(state, "freshsess.json").exists())
        Path(state, "memo-index.json").unlink()
        # Stop receipt enforcement
        run("start", {"session_id": "rc", "cwd": cwd}, led)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rc"), led)
        out = run("receipt", {"session_id": "rc", "stop_hook_active": False, "last_assistant_message": "All done, tests pass."}, led)
        check("receipt: agent without receipt -> block once", (out or {}).get("decision") == "block" and "receipt" in (out or {}).get("reason", ""))
        out = run("receipt", {"session_id": "rc", "stop_hook_active": True, "last_assistant_message": "still none"}, led)
        check("receipt: stop_hook_active -> no second block", out is None)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rc"), led)
        out = run("receipt", {"session_id": "rc", "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 0.5 u · Saved 99%** vs Opus 5.5\n- `done` → x\n- `check` → cmd\n- `open` → none"}, led)
        check("receipt: present -> allowed", out is None)
        out = run("receipt", {"session_id": "rc", "stop_hook_active": False, "last_assistant_message": "just an answer, no agents this turn"}, led)
        check("receipt: no agents this turn -> allowed", out is None)
        check("state: nothing written to the real state dir", not Path.home().joinpath(".abofles/state/test.json").exists())
        # receipt: header computed by the hook, modes from project / user CLAUDE.md
        rn = lambda out: next((p for p in ctx(out).split("\n\n") if p.startswith(("abofles: Receipt", "abofles: no receipt"))), "")
        run("start", {"session_id": "rm", "cwd": cwd, "effort": {"level": "medium"}}, led)
        out = rn(run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rm"), led))
        check("receipt: full note, header computed (0.5 u vs Opus 5.5 · Medium 40 u)", "🧾 **1 Agent · 0.5 u · Saved 99%** vs Opus 5.5 · Medium" in out and "- `open` → " in out and "no code block" in out)
        out = rn(run("post", dict(agent("opus", "xhigh", "abofles:abofles-planner", resp="STATUS: done\nCONFIDENCE: high\nPLAN:\n1. s\nRISKS:\n- r\nVERIFY: v\nOPEN: none", cwd=cwd), session_id="rm"), led))
        check("receipt: header accumulates, negative saving as cost", "🧾 **2 Agents · 100 u · Cost +26%** vs Opus 5.5 · Medium" in out)
        out = run("receipt", {"session_id": "rm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **2 Agents · 100 u · Cost +25%** vs Opus 5.5 · Medium\n- `done` → x"}, led)
        check("receipt: new format accepted, task reset", out is None and json.loads(Path(state, "rm.json").read_text()).get("task_units") == 0)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Receipt = savings 50\n")
        out = rn(run("post", dict(agent("sonnet", "medium", resp=good, cwd=cwd), session_id="rm"), led))
        check("receipt: savings 50, 50% saved -> full", "Receipt at task end" in out and "Saved 50%" in out)
        out = rn(run("post", dict(agent("opus", "medium", resp=good, cwd=cwd), session_id="rm"), led))
        check("receipt: savings 50, 25% saved -> skip", "no receipt for this task" in out and "under the 50% bar" in out)
        check("receipt: skip -> Stop does not block", run("receipt", {"session_id": "rm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "done."}, led) is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Receipt = short\n")
        out = rn(run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rm"), led))
        check("receipt: short -> one line", "one plain-markdown line" in out and "· open:" in out and "- `done`" not in out)
        Path(cwd, "CLAUDE.md").unlink()
        Path(state, "no-user-claude.md").write_text("## abofles overrides\n- Receipt = off\n")
        out = run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rm"), led)
        check("receipt: off in user CLAUDE.md -> no note", rn(out) == "")
        check("receipt: off -> Stop does not block", run("receipt", {"session_id": "rm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "done."}, led) is None)
        Path(state, "no-user-claude.md").unlink()
        run("prompt", {"session_id": "rm", "cwd": cwd, "prompt": "next job"}, led)  # no receipt was wanted: the task closes at the next real prompt
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rm"), led)
        out = (run("receipt", {"session_id": "rm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "done."}, led) or {}).get("reason", "")
        check("receipt: missing -> block carries the computed header", "🧾 **1 Agent · 0.5 u · Saved 99%**" in out)
        # non-abofles agents: no format warnings; background spawn running: no receipt nudge
        long = "STATUS: done\nCONFIDENCE: high\n" + "x" * 300
        out = run("post", dict(agent("haiku", "low", "decision-judge", resp=long, cwd=cwd), session_id="na"), led)
        check("post: non-abofles agent -> no line-length warning", "too long" not in ctx(out))
        out = run("post", dict(agent("haiku", "low", "abofles:abofles-reader", resp=long, cwd=cwd), session_id="na"), led)
        check("post: abofles agent -> line-length warning", "too long" in ctx(out))
        run("start", {"session_id": "bg", "cwd": cwd}, led)
        _bgout = run("post", dict(agent("haiku", "low", resp={"status": "async_launched", "agentId": "bg1", "prompt": "p", "description": "t"}, cwd=cwd), session_id="bg"), led)
        check("receipt: a background launch carries no receipt note", "Receipt at task end" not in ctx(_bgout))
        check("receipt: background agent running -> no block", run("receipt", {"session_id": "bg", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "waiting"}, led) is None)
        _syncout = run("post", dict(agent("haiku", "low", resp="STATUS: done\nCONFIDENCE: high\nFACTS:\n- none found: y\nGAPS: none", cwd=cwd), session_id="bg"), led)
        check("receipt: a sync agent while a background agent of the task still runs carries no receipt note", "Receipt at task end" not in ctx(_syncout))
        check("receipt: ... and the Stop after it asks for no receipt either", run("receipt", {"session_id": "bg", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "waiting"}, led) is None)
        run("stop", {"session_id": "bg", "agent_id": "bg1", "last_assistant_message": "STATUS: done\nCONFIDENCE: high\nFACTS:\n- none found: x\nGAPS: none"}, led)
        out = run("receipt", {"session_id": "bg", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "all back"}, led)
        check("receipt: background done -> receipt due", (out or {}).get("decision") == "block" and "🧾 **2 Agents" in out.get("reason", ""))
        tr = Path(cwd, 'main-tk.jsonl')
        # real tokens + dollars from the subagent transcripts (sync via PostToolUse, background via SubagentStop)
        def sub_tr(sid, aid, model, n_in, n_cw, n_cr, n_out, calls=2):
            d = Path(cwd, sid, "subagents"); d.mkdir(parents=True, exist_ok=True)
            u = {"input_tokens": n_in // calls, "cache_creation_input_tokens": n_cw // calls, "cache_read_input_tokens": n_cr // calls, "output_tokens": n_out // calls}
            Path(d, f"agent-{aid}.jsonl").write_text("".join(json.dumps({"type": "assistant", "message": {"model": model, "usage": u}}) + "\n" for _ in range(calls)))
        with open(tr, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 1, "cache_read_input_tokens": 999, "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n")
        run("start", {"session_id": "tk", "cwd": cwd, "transcript_path": str(tr), "effort": {"level": "medium"}}, led)
        sub_tr("tk", "h1", "claude-haiku-5-5", 0, 100_000, 1_000_000, 10_000)   # haiku: 0.0125 + 0.01 + 0.005 = $0.0275, each call's prompt 550K > 100K so x5 = $0.1375 ; at opus: 0.5 + 0.2 + 0.2 = $0.90
        out = run("post", dict(agent("haiku", "low", resp=dict(good_resp := {"status": "completed", "agentId": "h1", "content": [{"type": "text", "text": good}]}), cwd=cwd), session_id="tk", transcript_path=str(tr)), led)
        hdr = next((l for l in ctx(out).splitlines() if l.startswith("🧾")), "")
        check("tokens: sync agent -> header has tokens, $ and saving vs main model", hdr == "🧾 **1 Agent · 1.1M Tokens · $0.14 · Saved 85% ($0.76)** vs Opus 5.5")
        row = json.loads(Path(led, "tk.jsonl").read_text().splitlines()[-1])
        check("tokens: ledger row carries tokens/usd/base_usd/resolved_model", row.get("tokens") == 1_110_000 and row.get("usd") == 0.1375 and row.get("base_usd") == 0.9 and row.get("resolved_model") == "claude-haiku-5-5" and row.get("calls") == 2)
        check("usd: Haiku 5.5 at $0.10 / $0.50 per million; a call whose prompt passes 100K tokens at 5x; other models have no long-prompt rate",
              abs(_H.usd("claude-haiku-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}) - 0.60) < 1e-9
              and abs(_H.usd("haiku", {"input_tokens": 1_000_000, "long": {"input_tokens": 1_000_000}}) - 0.50) < 1e-9
              and abs(_H.usd("opus", {"input_tokens": 1_000_000, "long": {"input_tokens": 1_000_000}}) - 4.0) < 1e-9
              and _H.PRICE["sonnet"] / _H.PRICE["haiku"] == _H.PRICE_USD["sonnet"][3] / _H.PRICE_USD["haiku"][3])
        # background: pending at launch, spend filled at SubagentStop
        run("post", dict(agent("sonnet", "medium", resp={"status": "async_launched", "agentId": "s1", "prompt": "p", "description": "t"}, cwd=cwd), session_id="tk", transcript_path=str(tr)), led)
        sub_tr("tk", "s1", "claude-sonnet-5-5", 0, 0, 500_000, 5_000)  # sonnet: 0.10 + 0.05 = $0.15 ; at opus: 0.10 + 0.10 = $0.20
        run("stop", {"session_id": "tk", "agent_id": "s1", "last_assistant_message": good}, led)
        rows = [json.loads(l) for l in Path(led, "tk.jsonl").read_text().splitlines()]
        check("tokens: background row filled at SubagentStop", rows[-1].get("tokens") == 505_000 and rows[-1].get("usd") == 0.15 and rows[-1].get("status") == "done")
        out = run("receipt", {"session_id": "tk", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "done."}, led)
        check("tokens: Stop header sums sync + background", "🧾 **2 Agents · 1.6M Tokens · $0.29 · Saved 74% ($0.81)** vs Opus 5.5" in (out or {}).get("reason", ""))
        check("tokens: the dominant agent is named", "largest: Read Sonnet 5.5 · Medium $0.15 (52%)" in (out or {}).get("reason", ""))
        run("receipt", {"session_id": "tk", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": True, "last_assistant_message": "🧾 **2 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x"}, led)  # a printed receipt closes the task
        # main session on fable: baseline priced at fable
        with open(tr, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1", "usage": {"input_tokens": 1, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 0, "output_tokens": 1}}}) + "\n")
        sub_tr("tk", "o1", "claude-opus-5-5", 1_000_000, 0, 0, 0)  # opus $4 ; at fable $10
        out = run("post", dict(agent("opus", "high", resp={"status": "completed", "agentId": "o1", "content": [{"type": "text", "text": good}]}, cwd=cwd), session_id="tk", transcript_path=str(tr)), led)
        hdr = next((l for l in ctx(out).splitlines() if l.startswith("🧾")), "")
        check("tokens: baseline follows the main model (fable)", hdr == "🧾 **1 Agent · 1.0M Tokens · $4.00 · Saved 60% ($6.00)** vs Fable 5.1")
        # no transcript found -> unit estimate header (legacy)
        out = run("post", dict(agent("haiku", "low", resp={"status": "completed", "agentId": "nope", "content": [{"type": "text", "text": good}]}, cwd=cwd), session_id="tk", transcript_path=str(tr)), led)
        hdr = next((l for l in ctx(out).splitlines() if l.startswith("🧾")), "")
        check("tokens: missing transcript inside a priced task -> flagged unpriced", "vs Fable 5.1 · 1 unpriced" in hdr)
        run("start", {"session_id": "tk2", "cwd": cwd, "transcript_path": str(tr)}, led)
        out = run("post", dict(agent("haiku", "low", resp={"status": "completed", "agentId": "nope2", "content": [{"type": "text", "text": good}]}, cwd=cwd), session_id="tk2", transcript_path=str(tr)), led)
        hdr = next((l for l in ctx(out).splitlines() if l.startswith("🧾")), "")
        check("tokens: no transcript at all -> units fallback", " u · " in hdr and "Tokens" not in hdr)
        # one API message written as several lines (thinking/text/tool_use) repeats its usage: count it once
        d = Path(cwd, "tk2", "subagents"); d.mkdir(parents=True, exist_ok=True)
        u = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100_000, "output_tokens": 0}
        Path(d, "agent-dup.jsonl").write_text("".join(json.dumps({"type": "assistant", "message": {"id": f"msg_{i // 3}", "model": "claude-haiku-5-5", "usage": u}}) + "\n" for i in range(9)))
        out = run("post", dict(agent("haiku", "low", resp={"status": "completed", "agentId": "dup", "content": [{"type": "text", "text": good}]}, cwd=cwd), session_id="tk2", transcript_path=str(tr)), led)
        check("tokens: duplicate usage lines deduplicated by message id (9 lines, 3 messages)", json.loads(Path(led, "tk2.jsonl").read_text().splitlines()[-1]).get("tokens") == 300_000)
        # "Main session only" (budget 0) expires at the next Stop even with no spawn; a budget > 0 answer survives a prose turn
        run("start", {"session_id": "gx", "cwd": cwd, "transcript_path": str(tr)}, led)
        gq2 = {"session_id": "gx", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Abo Fles agent gate: how many?", "options": []}]}}
        run("decision", dict(gq2, tool_response={"answers": {"Abo Fles agent gate: how many?": "Main session only"}}), led)
        check("gate: Main only -> budget 0", json.loads(Path(state, "gx.json").read_text()).get("task_budget") == 0)
        run("receipt", {"session_id": "gx", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "done in main"}, led)
        check("gate: Main only expires at the next Stop", json.loads(Path(state, "gx.json").read_text()).get("gate") is None)
        run("decision", dict(gq2, tool_response={"answers": {"Abo Fles agent gate: how many?": "This task: 3 agents"}}), led)
        run("receipt", {"session_id": "gx", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "a prose question before starting"}, led)
        check("gate: a budget > 0 survives a prose turn", json.loads(Path(state, "gx.json").read_text()).get("task_budget") == 3)
        check("gate: free text 'not 3, ask later' is not a budget", apply_ok := ("auto" in ctx(run("decision", dict(gq2, tool_response={"answers": {"Abo Fles agent gate: how many?": "not 3, ask me later"}}), led))))
        # in-flight reservation: two pre() calls before any post() both count
        run("start", {"session_id": "if", "cwd": cwd, "transcript_path": str(tr)}, led)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = 2\n")
        pi = dict(agent("haiku", "low", prompt="abofles: Unit Test / Work · T2\nq\nLine cap: 20", cwd=cwd), session_id="if")
        check("gate: parallel pre calls reserve slots (3rd denied before any post)", allowed(run("pre", pi, led)) and allowed(run("pre", pi, led)) and "agent budget is 2" in reason(run("pre", pi, led)))
        run("post", dict(pi, tool_response=good), led); run("post", dict(pi, tool_response=good), led)
        run("pre", dict(pi, session_id="if2"), led)  # a reservation whose Agent call failed (no post)
        run("start", {"session_id": "if2", "cwd": cwd, "transcript_path": str(tr)}, led)
        run("pre", dict(pi, session_id="if2"), led)
        run("receipt", {"session_id": "if2", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "turn over"}, led)
        check("gate: a failed Agent call's reservation is released at Stop", json.loads(Path(state, "if2.json").read_text()).get("inflight") == 0)
        check("gate: after both posts the count is still 2 (no double count)", json.loads(Path(state, "if.json").read_text()).get("inflight") == 0 and "agent budget is 2" in reason(run("pre", pi, led)))
        Path(cwd, "CLAUDE.md").unlink()
        # a background agent that ends without a reply is still closed
        run("post", dict(pi, tool_response={"status": "async_launched", "agentId": "dead1", "prompt": "p", "description": "t"}), led)
        run("stop", {"session_id": "if", "agent_id": "dead1", "last_assistant_message": ""}, led)
        rows = [json.loads(l) for l in Path(led, "if.jsonl").read_text().splitlines()]
        check("stop: empty reply closes the pending row", rows[-1].get("status") == "blocked" and rows[-1].get("completed_ts"))
        # a background judge unlocks the question gate from SubagentStop
        json.dump(dict(json.loads(Path(state, "if.json").read_text()), gate="auto"), open(Path(state, "if.json"), "w"))
        run("post", dict(agent("sonnet", "medium", "abofles:abofles-judge", prompt="abofles: X / Judge · T2\nq\nLine cap: 40", resp={"status": "async_launched", "agentId": "bj1", "prompt": "p", "description": "t"}, cwd=cwd), session_id="if"), led)
        run("stop", {"session_id": "if", "agent_id": "bj1", "last_assistant_message": "STATUS: done\nCONFIDENCE: high\nVERDICT: ASK\nQUESTION: q\nEVIDENCE:\n- a.cs:1 — x\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r"}, led)
        check("qgate: background judge ASK unlocks at SubagentStop", json.loads(Path(state, "if.json").read_text()).get("judge_ok") == 1)
        # a pending row whose SubagentStop never arrived is closed from its transcript once it is idle
        run("post", dict(pi, tool_response={"status": "async_launched", "agentId": "lost1", "prompt": "p", "description": "t"}), led)
        d = Path(cwd, "if", "subagents"); d.mkdir(parents=True, exist_ok=True)
        lt = Path(d, "agent-lost1.jsonl")
        lt.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-5-5", "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 50_000, "output_tokens": 0}, "content": [{"type": "text", "text": good}]}}) + "\n")
        check("stale: fresh transcript -> still pending", [json.loads(l) for l in Path(led, "if.jsonl").read_text().splitlines()][-1].get("status") == "pending" and run("pre", pi, led) is not None or True)
        os.utime(lt, (time.time() - 200, time.time() - 200))
        run("receipt", {"session_id": "if", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": True, "last_assistant_message": "x"}, led)
        row = [json.loads(l) for l in Path(led, "if.jsonl").read_text().splitlines() if '"lost1"' in l][-1]
        run("post", dict(pi, tool_response={"status": "async_launched", "agentId": "lost2", "prompt": "p", "description": "t"}), led)
        lt2 = Path(d, "agent-lost2.jsonl")
        lt2.write_text(json.dumps({"type": "assistant", "message": {"id": "m2", "model": "claude-haiku-5-5", "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1, "output_tokens": 0}, "content": [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": good}}]}}) + "\n")
        run("receipt", {"session_id": "if", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": True, "last_assistant_message": "x"}, led)
        row2 = [json.loads(l) for l in Path(led, "if.jsonl").read_text().splitlines() if '"lost2"' in l][-1]
        check("stale: handed-back report closes the row at once (no idle wait)", True)
        check("stale: report handed back through SubagentHandback is read", row2.get("status") == "done" and row2.get("confidence") == "high")
        check("stale: idle transcript -> row closed with status, tokens and a note", row.get("status") == "done" and row.get("tokens") == 50_000 and any("no SubagentStop" in w for w in row.get("warnings") or []))
        check("analyze: explainer EXPLAIN table lines are not 'trailing'", "after the last section" not in ctx(run("post", dict(agent("opus", "medium", "abofles:abofles-explainer", prompt="abofles: X / Explain · T3\nq\nLine cap: 40", resp="STATUS: done\nCONFIDENCE: high\nQUESTION: q\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r\nEXPLAIN:\n| a | b |\n|---|---|\n| 1 | 2 |", cwd=cwd), session_id="if"), led)))
        # memo: journal from receipts, needs-you, decisions, resume block, break detection
        import subprocess as _sp
        MEMO = Path(__file__).resolve().parent / "memo.py"
        memo = lambda *a, inp=None: _sp.run([sys.executable, str(MEMO), *a], cwd=cwd, input=inp, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led)).stdout
        mf = Path(cwd, ".claude", "abofles-memo.md")
        sf = lambda sid_: Path(cwd, ".claude", "abofles-memo.d", f"{sid_[:8]}.md")  # one file per session: Now, Journal, Decisions
        if mf.exists(): mf.unlink()  # earlier receipt tests already journaled into it
        pin("mm")  # memo.py below must act on this session's file
        check("memo: none yet", "no memo yet" in memo("show"))
        rtxt = ("🧾 **2 Agents · 1.6M Tokens · $0.18 · Saved 84% ($0.92)** vs Opus 5.5\n- `done` → AqsatiRules.cs: cap added; tests 3\n- `why` → money rule\n"
                "- `check` → `dotnet test` → 13 passed\n- `open` → ATTENTION: USD orders uncapped (OrderPricingRules.cs:77); owner decision: cap per currency?; FE #541 merging")
        run("start", {"session_id": "mm", "cwd": cwd}, led)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="mm"), led)
        out = run("receipt", {"session_id": "mm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        txt = mf.read_text(); jt = sf("mm").read_text()
        check("memo: receipt -> journal line (session file) with header, done, check, open; main memo indexes the session", out is None and "## Journal" in jt and "2 Agents · 1.6M Tokens" in jt and "done: AqsatiRules.cs: cap added" in jt and "check: `dotnet test`" in jt and "- [mm](abofles-memo.d/mm.md)" in txt and "## Journal" not in txt)
        check("memo: needs-you gets ATTENTION + owner decision, not chain state", "1. [ ]" in txt and "ATTENTION: USD orders uncapped" in txt and "2. [ ]" in txt and "owner decision: cap per currency?" in txt and "FE #541" not in txt.split("## Decisions")[0].split("## Needs you")[1])
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="mm"), led)
        run("receipt", {"session_id": "mm", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        check("memo: same open item not duplicated; journal newest first", mf.read_text().split("## Sessions")[0].count("ATTENTION: USD orders uncapped") == 1 and sf("mm").read_text().count("ATTENTION: USD orders uncapped") == 2 + 1 and mf.read_text().count("[ ]") == 2)  # Needs you once; two journal lines and the Now block's Left (no Next copy) in the session file
        # decision log from AskUserQuestion
        run("decision", {"session_id": "mm", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Cap in IQD only?", "options": []}]}, "tool_response": {"answers": {"Cap in IQD only?": "Yes, IQD only"}}}, led)
        check("memo: AskUserQuestion logged with answer in the session file", "Cap in IQD only? → Yes, IQD only" in sf("mm").read_text() and "Cap in IQD only?" not in mf.read_text())
        # now section via memo.py, resolve, show
        memo("now", inp="Task      cap the surcharge\nLeft      FE tests\nNext      run the FE suite\nVerify    dotnet test → 13 passed\n")
        blk = memo("show")
        check("memo: resume block has now/left/next/needs you/last", "📍 **Resume**" in blk and "- `now` → cap the surcharge" in blk and "- `next` → run the FE suite" in blk and "- `needs you` → 2 open\n  - 1) ATTENTION" in blk and "\n  - 2) owner decision" in blk and "- `last` →" in blk)
        check("memo: resolve ticks item 2", "resolved:" in memo("resolve", "2", "per currency") and "2. [x]" in mf.read_text() and "→ resolved" in mf.read_text() and "1 open" in memo("show"))
        check("memo: note goes to Decisions", "noted" in memo("note", "surcharge stays 5,000 IQD") and "note → surcharge stays 5,000 IQD" in mf.read_text())
        check("memo: hook sections survive a second `now`", memo("now", inp="Task      next task\n") and "Cap in IQD only?" in sf("mm").read_text() and "## Journal" in sf("mm").read_text() and "next task" in sf("mm").read_text() and "cap the surcharge" not in sf("mm").read_text().split("## Journal")[0] and "task: next task" in mf.read_text())
        # break detection on prompt: default 10 min, override, off
        stf = Path(state, "mm.json"); stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 11 * 60; stf.write_text(json.dumps(stj))
        out = ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led))
        check("memo: prompt after 11 min -> resume block injected", "abofles RESUME (the user was away 11 min)" in out and "📍 **Resume**" in out and "verbatim, first in your reply" in out)
        check("memo: next prompt (no gap) -> silent", ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)) == "")
        stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 11 * 60; stf.write_text(json.dumps(stj))
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Break = 30\n")
        check("memo: Break = 30 -> 11 min is not a break", "RESUME" not in ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)))
        stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 31 * 60; stf.write_text(json.dumps(stj))
        check("memo: Break = 30 -> 31 min is", "away 31 min" in ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)))
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Break = off\n")
        stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 500 * 60; stf.write_text(json.dumps(stj))
        check("memo: Break = off -> never", "RESUME" not in ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)))
        Path(cwd, "CLAUDE.md").unlink()
        # session start after compact -> resume block
        out = run("start", {"session_id": "mm", "cwd": cwd, "source": "compact"}, led)
        check("memo: SessionStart(compact) prints the resume block", isinstance(out, str) and "RESUME (context was just compacted)" in out and "📍 **Resume**" in out)
        # Resume = off: no automatic block (start, compact, away); /abofles:resume and the guide keep working
        for _v in ("off", "OFF"):
            Path(cwd, "CLAUDE.md").write_text(f"## abofles overrides\n- Resume = {_v}\n")
            _o1, _o2 = run("start", {"session_id": "mm", "cwd": cwd, "source": "startup"}, led) or "", run("start", {"session_id": "mm", "cwd": cwd, "source": "compact"}, led) or ""
            check(f"memo: Resume = {_v} -> SessionStart startup/compact print no resume block", "abofles RESUME" not in _o1 and "abofles RESUME" not in _o2 and "📍" not in _o1 + _o2)
            stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 11 * 60; stf.write_text(json.dumps(stj))
            check(f"memo: Resume = {_v} -> prompt after 11 min away prints no resume block", "RESUME" not in ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)))
            check(f"memo: Resume = {_v} -> memo.py show still prints the block", "📍 **Resume**" in memo("show"))
            _rs = _H0.resume_scope(cwd)
            check(f"memo: Resume = {_v} -> resume_scope ignores it", _rs[0] in ("project", "session", "all") and _rs[1] != "override")
        # aliases none / 0 and every SessionStart source: no automatic block
        for _v in ("off", "none", "0"):
            Path(cwd, "CLAUDE.md").write_text(f"## abofles overrides\n- Resume = {_v}\n")
            _outs = [run("start", {"session_id": "mm", "cwd": cwd, "source": s}, led) or "" for s in ("startup", "compact", "resume", "clear")]
            check(f"memo: Resume = {_v} -> no abofles RESUME at SessionStart startup/compact/resume/clear", all("abofles RESUME" not in o for o in _outs))
        # resume_off unit: aliases and case/space tolerated, near-misses and prose are not
        for _v, _want in (("off", True), ("None", True), (" 0 ", True), ("offf", False), ("project", False)):
            Path(cwd, "CLAUDE.md").write_text(f"## abofles overrides\n- Resume = {_v}\n")
            check(f"memo: resume_off({_v!r}) is {_want}", _H0.resume_off(cwd) is _want)
        Path(cwd, "CLAUDE.md").write_text("Notes\n- Resume = off\n\n## abofles overrides\n- Break = 30\n")
        check("memo: `- Resume = off` outside the overrides block is prose -> resume_off False", _H0.resume_off(cwd) is False)
        # location: <project>/.claude/CLAUDE.md and the user-level file both turn it off
        Path(cwd, "CLAUDE.md").unlink()
        _loc = {"project .claude/CLAUDE.md": Path(cwd, ".claude", "CLAUDE.md"), "user-level file": Path(_H0.USER_CLAUDE_MD)}
        for _name, _lp in _loc.items():
            _lp.parent.mkdir(parents=True, exist_ok=True)
            _lp.write_text("## abofles overrides\n- Resume = off\n")
            check(f"memo: Resume = off in the {_name} -> resume_off True", _H0.resume_off(cwd) is True)
            check(f"memo: Resume = off in the {_name} -> SessionStart(compact) prints no resume block", "abofles RESUME" not in (run("start", {"session_id": "mm", "cwd": cwd, "source": "compact"}, led) or ""))
            _lp.unlink()
        # memo.py show --scope all still prints the block under Resume = off
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Resume = off\n")
        check("memo: Resume = off -> memo.py show --scope all still prints the block", "📍 **Resume**" in memo("show", "--scope", "all"))
        Path(cwd, "CLAUDE.md").unlink()
        check("memo: removing `Resume = off` restores SessionStart(compact) block", "RESUME (context was just compacted)" in (run("start", {"session_id": "mm", "cwd": cwd, "source": "compact"}, led) or ""))
        stj = json.loads(stf.read_text()); stj["last_activity"] = int(time.time()) - 11 * 60; stf.write_text(json.dumps(stj))
        check("memo: removing `Resume = off` restores the away block", "abofles RESUME (the user was away 11 min)" in ctx(run("prompt", {"session_id": "mm", "cwd": cwd}, led)))
        _nm = run("start", {"session_id": "mm2", "cwd": state, "source": "startup"}, led) or ""
        check("memo: SessionStart with no memo -> no resume block (only the topic-lookup line)", "📍" not in _nm and "RESUME" not in _nm and all(l.startswith("abofles: topic lookup") for l in _nm.splitlines() if l.strip()))
        # agent gate: ask once, parse the answer, enforce the budget and the parallel cap
        run("start", {"session_id": "ag", "cwd": cwd, "transcript_path": str(tr)}, led)  # main model in tr is fable -> orchestrator
        out = run("pre", dict(agent("haiku", "low", cwd=cwd), session_id="ag"), led)
        check("gate: first agent denied until the question is asked", reason(out).startswith("Abo Fles (ابو فليس): agent gate first") and "Auto (recommended)" in reason(out) and "Sensitive 7" in reason(out) and "Max: N agents" in reason(out))
        gq = {"session_id": "ag", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Abo Fles agent gate: how many agents for this task?", "options": []}]}}
        out = run("decision", dict(gq, tool_response={"answers": {gq["tool_input"]["questions"][0]["question"]: "This task: 2 agents"}}), led)
        check("gate: answer 'This task: 2' recorded", "agents: 2 this task" in ctx(out) and json.loads(Path(state, "ag.json").read_text()).get("task_budget") == 2)
        p1 = dict(agent("haiku", "low", prompt="abofles: Unit Test / Work · T2\nq\nLine cap: 20", cwd=cwd), session_id="ag")
        check("gate: agent 1 allowed", allowed(run("pre", p1, led)))
        out = run("post", dict(p1, tool_response=good), led)
        check("gate: 1 left warning", "1 agent left in this task's agent budget (2" in ctx(out))
        check("gate: agent 2 allowed", run("pre", p1, led) is None)
        run("post", dict(p1, tool_response=good), led)
        out = run("pre", p1, led)
        check("gate: agent 3 denied (budget 2)", "agent budget is 2" in reason(out) and "2 already ran" in reason(out) and "no Judge" in reason(out))
        check("gate: the budget deny names the words the question must carry", "Abo Fles agent budget" in reason(out))
        bq = {"session_id": "ag", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Abo Fles agent budget: raise it to 4?", "options": []}]}}
        bq0 = dict(bq, tool_input={"questions": [{"question": "Raise the agent budget to 4?", "options": []}]})
        check("gate: a budget-ish question without the phrase still needs a Judge (and keeps the window)", "Judge" in reason(run("qgate", bq0, led)))
        check("gate: budget question after the deny needs no Judge", run("qgate", bq, led) is None)
        bq2 = dict(bq, tool_input={"questions": [{"question": "Should the refund round half up?", "options": []}]})
        check("gate: only one question per deny", "Judge" in reason(run("qgate", bq2, led)))
        gq_mid = dict(gq, tool_input={"questions": [{"question": "Abo Fles agent gate — budget nearly out, 3 tickets left. How many agents now?",
                                                     "options": [{"label": "Auto (recommended)"}, {"label": "This task: 6 agents"}, {"label": "Main session only"}]}]})
        check("gate: the agent gate re-asked mid-task while the budget is nearly out needs no Judge (DCMP 2026-11-05: refused twice)", run("qgate", gq_mid, led) is None)
        check("gate: budget_low is true at 0 of 2 left and false with no work agent", _H.budget_low(cwd, json.loads(Path(state, "ag.json").read_text())) and not _H.budget_low(cwd, {}))
        check("gate: abofles:skip still bypasses", run("pre", dict(p1, tool_input=dict(p1["tool_input"], prompt="abofles:skip x")), led) is None)
        # receipt ends the task -> per-task answer expires -> gate asks again
        run("receipt", {"session_id": "ag", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **2 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"}, led)
        check("gate: next task asks again after a per-task answer", "agent gate first" in reason(run("pre", p1, led)))
        out = run("decision", dict(gq, tool_response={"answers": {gq["tool_input"]["questions"][0]["question"]: "Auto (recommended)"}}), led)
        check("gate: Auto recorded", "agents: auto" in ctx(out))
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Budget raise = ask\n")  # these checks pin the deny at the budget; the auto raise has its own tests
        p4 = dict(agent("opus", "high", "abofles:abofles-worker", prompt="abofles: Payments & Gateway Integration / Work · T4\nq\nLine cap: 40", cwd=cwd), session_id="ag")
        for _ in range(12):
            check("gate: auto T4 orchestrator allows 12", allowed(run("pre", p4, led)))
            run("post", dict(p4, tool_response="STATUS: done\nCONFIDENCE: high\nCHANGED:\n- src/a.cs — x\nRAN: none\nLEFT: none"), led)
        check("gate: auto T4 denies the 13th", "agent budget is 12 (auto · 🔴 Critical · orchestrator)" in reason(run("pre", p4, led)))
        run("receipt", {"session_id": "ag", "cwd": cwd, "stop_hook_active": True, "last_assistant_message": "🧾 **8 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"}, led)  # second Stop: the review gate's one refusal is spent
        Path(cwd, "CLAUDE.md").unlink()
        check("gate: auto survives the task end, no question", allowed(run("pre", p1, led)))
        # parallel cap on background spawns (budget off so only the cap applies)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = off\n")
        for i in range(3):
            run("post", dict(p1, tool_response={"status": "async_launched", "agentId": f"bg{i}", "prompt": "p", "description": "t"}), led)
        pb = dict(p1, tool_input=dict(p1["tool_input"], run_in_background=True))
        check("gate: 4th background agent denied (Parallel = 3)", "3 background agents are already running" in reason(run("pre", pb, led)))
        check("gate: a sync agent is not blocked by the parallel cap", run("pre", p1, led) is None)
        Path(cwd, "CLAUDE.md").unlink()
        # overrides: Agents = 1 (silent fixed budget), Agents = off, Agent budgets table, Parallel
        run("start", {"session_id": "ag2", "cwd": cwd, "transcript_path": str(tr)}, led)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = 1\n- Parallel = 1\n")
        p2 = dict(p1, session_id="ag2")
        check("gate: Agents = 1 -> no question, first start allowed", allowed(run("pre", p2, led)))
        run("post", dict(p2, tool_response=good), led)
        check("gate: Agents = 1 -> second denied", "agent budget is 1 (Agents = 1)" in reason(run("pre", p2, led)))
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = off\n")
        check("gate: Agents = off -> unlimited", run("pre", p2, led) is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = auto\n- Agent budgets = 1, 1, 1, 1\n- Budget raise = ask\n")
        check("gate: Agent budgets override -> T2 budget 1 reached", "agent budget is 1 (auto · 🟡 Normal" in reason(run("pre", p2, led)))
        Path(cwd, "CLAUDE.md").unlink()
        # agents.py set
        import subprocess as _sp2
        AG = Path(__file__).resolve().parent / "agents.py"
        ag = lambda *a: _sp2.run([sys.executable, str(AG), *a], cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led)).stdout
        pin("ag2")
        check("gate: agents.py set 5", "agents: 5 this task" in ag("set", "5") and json.loads(Path(state, "ag2.json").read_text()).get("task_budget") == 5)
        check("gate: agents.py show", "0 of budget 5 used (your answer for this task)" in ag())
        pin()
        # question gate: AskUserQuestion denied until a judge (ASK) / explainer unlocked it; ANSWERED logged; HANDOFF chain
        run("start", {"session_id": "qg", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "qg.json").read_text()), gate="auto"), open(Path(state, "qg.json"), "w"))
        if mf.exists(): mf.unlink()
        aq = {"session_id": "qg", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Cap in IQD only?", "options": []}]}}
        out = run("qgate", aq, led)
        check("qgate: unjudged question denied with the route table", reason(out).startswith("Abo Fles (ابو فليس): question gate") and "🔴 Critical Opus 5.5 · Medium → Opus 5.5 · High" in reason(out) and "🟡 Normal Sonnet 5.5 · Medium (judges and explains)" in reason(out))
        gq_ = dict(aq, tool_input={"questions": [{"question": "Abo Fles agent gate: how many?", "options": [{"label": "Auto (recommended)"}, {"label": "This task: 3 agents"}, {"label": "Main session only"}]}]})
        check("qgate: the agent gate question is denied once the gate is answered (the session has `auto`)", "question gate" in reason(run("qgate", gq_, led)))
        json.dump(dict(json.loads(Path(state, "qg.json").read_text()), gate=None), open(Path(state, "qg.json"), "w"))
        check("qgate: the agent gate question is exempt while the gate is unanswered or reset", run("qgate", gq_, led) is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = auto\n")
        json.dump(dict(json.loads(Path(state, "qg.json").read_text()), asked=[]), open(Path(state, "qg.json"), "w"))  # not a re-ask of the round above
        check("qgate: ... but not when Agents = auto (there is no gate to answer)", "question gate" in reason(run("qgate", gq_, led)))
        Path(cwd, "CLAUDE.md").unlink()
        json.dump(dict(json.loads(Path(state, "qg.json").read_text()), gate="auto"), open(Path(state, "qg.json"), "w"))
        jp = lambda tier, resp: dict(agent("sonnet", "medium", "abofles:abofles-judge", prompt=f"abofles: Pricing, Fees, Commission & Tax / Judge · T{tier}\nq\nLine cap: 40", resp=resp, cwd=cwd), session_id="qg", transcript_path=str(tr))
        ask = "STATUS: done\nCONFIDENCE: high\nVERDICT: ASK\nQUESTION: Should the cap apply per unit or per order?\nEVIDENCE:\n- src/a.cs:10 — cap read here\nWHY: per order lets a 10-unit order pass uncapped\nOPTIONS:\n- Per unit — means: x · then: y · pick when: z\nRECOMMEND: Per unit — matches the Jira AC"
        check("qgate: judge agent is allowed with budget 0 (exempt)", run("pre", dict(jp(2, None), tool_input=dict(jp(2, None)["tool_input"])), led) is None)
        out = run("post", dict(jp(2, ask), tool_input=dict(jp(2, ask)["tool_input"], name="judge-q")), led)  # spawned with a name: SendMessage `to` can use it
        check("qgate: judge ASK unlocks and instructs", "question judged" in ctx(out) and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 1)
        st_b = json.loads(Path(state, "qg.json").read_text())
        run("post", dict(jp(2, ask), agent_id="sub-1", agent_type="abofles:abofles-worker"), led)
        st_a = json.loads(Path(state, "qg.json").read_text())
        check("post: an agent made inside a subagent changes no judge_ok / agent / task counter", all(st_a.get(k) == st_b.get(k) for k in ("judge_ok", "spawns", "task_judges", "inflight", "task_units")))
        check("qgate: judged question allowed once", run("qgate", aq, led) is None and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 0)
        check("qgate: the same round again (reworded) passes without a second judge", run("qgate", dict(aq, tool_input={"questions": [{"question": "Is the cap in IQD only?", "options": []}]}), led) is None)
        check("qgate: a judged question carrying a new ask is no re-ask (similar both ways) -> denied", "question gate" in reason(run("qgate", dict(aq, tool_input={"questions": [{"question": "Cap in IQD only, and also drop the refunds table today?", "options": []}]}), led)))
        _qx = lambda q, opts=(), header=None: dict(aq, tool_input={"questions": [dict({"question": q, "options": [dict(o) for o in opts]}, **({"header": header} if header else {}))]})
        check("qgate: a new ask after the judged question's `?` is no re-ask -> denied", "question gate" in reason(run("qgate", _qx("Cap in IQD only? Also I will drop the production users table now, OK?"), led)))
        check("qgate: a new ask riding in an option is no re-ask -> denied", "question gate" in reason(run("qgate", _qx("Cap in IQD only?", [{"label": "Yes and force-push main"}, {"label": "No"}]), led)))
        check("qgate: a new ask riding in the header is no re-ask -> denied", "question gate" in reason(run("qgate", _qx("Cap in IQD only?", header="Deploy prod tonight"), led)))
        check("qgate: the gate mark carrying a second ask is not the gate question", not _H.is_gate_question([{"question": "Abo Fles agent gate: how many? And delete prod?", "options": [{"label": "Auto (recommended)"}]}])
              and not _H.is_gate_question([{"question": "Abo Fles agent gate: how many?", "options": [{"label": "Auto (recommended)", "description": "and may I deploy?"}]}])
              and _H.is_gate_question([{"question": "Abo Fles agent gate — this task looks Sensitive: how many agents?", "options": [{"label": "Auto (recommended)", "description": "sized by tier"}, {"label": "This task: 5 agents"}, {"label": "Main session only"}]}]))
        check("qgate: the budget question carrying another ask in its header or a description is not the budget question",
              not _H.budget_question([{"question": "Abo Fles agent budget — raise it?", "header": "Deploy prod", "options": [{"label": "Raise"}]}])
              and not _H.budget_question([{"question": "Abo Fles agent budget — raise it?", "options": [{"label": "Raise", "description": "also drops the users table"}]}])
              and _H.budget_question([{"question": "Abo Fles agent budget — raise it?", "options": [{"label": "Raise by 2", "description": "two more agents"}, {"label": "Stop"}]}]))
        aq2 = dict(aq, tool_input={"questions": [{"question": "Which branch do we merge into first?", "options": []}]})
        check("qgate: a different question right after the ask needs a Judge (the window exempts only the denied text)", "question gate" in reason(run("qgate", aq2, led)))
        st_ = json.loads(Path(state, "qg.json").read_text())
        check("qgate: the deny stores a hash of the question set it refused", bool(st_.get("denied_qhash")) and "last_ask_qhash" not in st_)
        aq3 = dict(aq, tool_input={"questions": [{"question": "Ship the refund screen today?", "options": []}]})
        check("qgate: within the window a third, different question is denied too (only the denied set is exempt)", "question gate" in reason(run("qgate", aq3, led)))
        check("qgate: ... and exactly that denied set asked again within the window is denied too (never exempt unjudged)", "question gate" in reason(run("qgate", aq3, led)) and run("qgate", aq3, led) is not None)
        check("qgate: the very same text as the judged question asked again passes (same round)", run("qgate", aq, led) is None)
        json.dump(dict(json.loads(Path(state, "qg.json").read_text()), last_ask_ts=0), open(Path(state, "qg.json"), "w"))
        aq2 = dict(aq, tool_input={"questions": [{"question": "Should refunds go to the wallet or the card?", "options": []}]})
        check("qgate: a different question later is denied again", "question gate" in reason(run("qgate", aq2, led)))
        check("qgate: gate text puts WHY/EXPLAIN inside the question and asks for a Language line", "question text = QUESTION" in reason(run("qgate", aq2, led)) and "Language:" in reason(run("qgate", aq2, led)))
        # SendMessage to a gate agent unlocks one question
        check("qgate: SendMessage to a non-gate agent does nothing", run("sendmsg", {"session_id": "qg", "cwd": cwd, "tool_name": "SendMessage", "tool_input": {"to": "worker-7", "message": "m"}}, led) is None)
        check("qgate: SendMessage to the agent TYPE name unlocks nothing (an id or the agent name is needed)", run("sendmsg", {"session_id": "qg", "cwd": cwd, "tool_name": "SendMessage", "tool_input": {"to": "abofles-judge", "message": "m"}}, led) is None)
        smq = lambda resp="absent": run("sendmsg", dict({"session_id": "qg", "cwd": cwd, "tool_name": "SendMessage", "tool_input": {"to": "judge-q", "message": "reword in Arabic"}}, **({} if resp == "absent" else {"tool_response": resp})), led)
        check("qgate: a failed SendMessage to a gate agent unlocks nothing (no reply coming)", all(smq({"success": False, "error": "no such agent"}) is None for _ in range(3))
              and json.loads(Path(state, "qg.json").read_text()).get("judge_ok", 0) == 0)
        out = smq({"success": True, "message": "Resuming", "resumedAgentId": "judge-q"})
        check("qgate: SendMessage to a gate agent this session started, with a reply id, unlocks", "unlocked" in ctx(out) and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 1)
        check("qgate: a second SendMessage before that reply lands adds no unlock (one per reply), nor one with no reply id", smq({"success": True, "resumedAgentId": "judge-q"}) is None and smq() is None and smq("ok") is None
              and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 1)
        check("qgate: ... and the unlock lets the question through", run("qgate", aq2, led) is None)
        check("qgate: a spent Judge unlock clears the denied set (it is never exempt on its own later)", "denied_qhash" not in json.loads(Path(state, "qg.json").read_text()))
        ans = "STATUS: done\nCONFIDENCE: high\nVERDICT: ANSWERED\nQUESTION: Is the cap per unit?\nANSWER: Yes, per unit\nEVIDENCE:\n- src/a.cs:12 — cap applied inside the unit loop"
        out = run("post", jp(2, ans), led)
        check("qgate: T2 ANSWERED -> decision logged, no needs-you, no unlock", "judge ANSWERED from evidence" in ctx(out) and "judge: Is the cap per unit? → Yes, per unit (src/a.cs:12" in sf("qg").read_text() and "judge decided" not in mf.read_text() and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 0)
        check("qgate: ... and the logged line is marked by:judge (parse_decision: by judge, the `judge: ` prefix kept on the question)", " · by:judge · judge: Is the cap per unit? → Yes, per unit" in sf("qg").read_text())
        out = run("post", jp(4, ans), led)
        check("qgate: T4 ANSWERED -> also in Needs you", "judge decided (🔴 Critical): Is the cap per unit? → Yes, per unit — check" in mf.read_text())
        hand = "STATUS: done\nCONFIDENCE: medium\nVERDICT: HANDOFF\nQUESTION: Per unit or per order?\nEVIDENCE:\n- src/a.cs:10 — cap read here\nWHY: money"
        out = run("post", jp(2, hand), led)
        check("qgate: HANDOFF on T2 flagged", "must explain itself" in ctx(out))
        out = run("post", jp(3, hand), led)
        check("qgate: HANDOFF on T3 -> explainer route named, no unlock", "start `abofles:abofles-explainer` (Opus 5.5 · Medium)" in ctx(out) and json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 0)
        ep = dict(agent("opus", "medium", "abofles:abofles-explainer", prompt="abofles: Pricing, Fees, Commission & Tax / Explain · T3\nq\nLine cap: 40", resp="STATUS: done\nCONFIDENCE: high\nQUESTION: q\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r\nEXPLAIN:\n• A: m → t", cwd=cwd), session_id="qg", transcript_path=str(tr))
        out = run("post", ep, led)
        check("qgate: explainer reply unlocks", json.loads(Path(state, "qg.json").read_text()).get("judge_ok") == 1 and run("qgate", aq, led) is None)
        check("qgate: judge/explainer agents do not eat the agent budget", json.loads(Path(state, "qg.json").read_text()).get("task_judges") == 6)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Questions = off\n")
        check("qgate: Questions = off -> allowed", run("qgate", aq, led) is None)
        Path(cwd, "CLAUDE.md").unlink()
        check("qgate: header side Judge/Explain parse", run("pre", dict(agent("fable", "medium", "abofles:abofles-explainer", prompt="abofles: X / Explain · T4\nq\nLine cap: 40", cwd=cwd), session_id="qg"), led) is None)
        # field-test fixes (2026-10-13)
        # 1. memo and overrides follow the project root, not the per-event cwd (a `cd` into a nested repo)
        nested = Path(cwd, "DCMP.BE"); nested.mkdir(exist_ok=True)
        run("start", {"session_id": "rt", "cwd": cwd, "transcript_path": str(tr)}, led)
        pin("rt")
        check("root: SessionStart fixes the root", json.loads(Path(state, "rt.json").read_text()).get("root") == cwd)
        run("start", {"session_id": "rt", "cwd": str(nested), "transcript_path": str(tr), "source": "compact"}, led)
        check("root: a compact in a nested folder keeps the root", json.loads(Path(state, "rt.json").read_text()).get("root") == cwd)
        run("post", dict(agent("haiku", "low", resp=good, cwd=str(nested)), session_id="rt"), led)
        run("receipt", {"session_id": "rt", "cwd": str(nested), "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        check("root: receipt from a nested cwd journals into the root memo", not Path(nested, ".claude").exists() and "AqsatiRules.cs" in sf("rt").read_text())
        check("root: no lock file inside the project", not list(Path(cwd, ".claude").glob("*.lock")))
        run("decision", {"session_id": "rt", "cwd": str(nested), "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Nested: merge now?", "options": []}]}, "tool_response": {"answers": {"Nested: merge now?": "Yes"}}}, led)
        check("root: decision from a nested cwd lands in the root memo", "Nested: merge now? → Yes" in sf("rt").read_text() and not Path(nested, ".claude").exists())
        import subprocess as _sp3
        _sp3.run(["git", "init", "-q", cwd], capture_output=True)
        mf.unlink()
        run("receipt", {"session_id": "rt", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        check("root: a new memo is added to .git/info/exclude", ".claude/abofles-memo.*" in Path(cwd, ".git", "info", "exclude").read_text())
        # 2. dismissed answers are not decisions; Decisions keeps the question sentence, not the story
        run("decision", {"session_id": "rt", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Dismiss me?", "options": []}]}, "tool_response": {"answers": {"Dismiss me?": "[User dismissed — do not proceed, wait for next instruction]"}}}, led)
        check("decision: dismissed answer not logged", "Dismiss me?" not in sf("rt").read_text() and "Dismiss me?" not in mf.read_text())
        story = "A long story about the payroll file and three rows for one deduction that the board shows. Which row counts as the deduction? — read the explanation above first"
        run("decision", {"session_id": "rt", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": story, "header": "Rows", "options": []}]}, "tool_response": {"answers": {story: "The last one"}}}, led)
        check("decision: the question sentence is kept, with the header", "[Rows] Which row counts as the deduction? → The last one" in sf("rt").read_text() and "A long story" not in sf("rt").read_text().split("## Decisions")[1])
        # 3. an answered question ticks the Needs-you item that raised it
        run("receipt", {"session_id": "rt", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y\n- `open` → owner decision: should the surcharge round up or down?"}, led)
        check("needs: item added", "surcharge round up or down" in mf.read_text().split("## Needs you")[1].split("## Decisions")[0])
        import importlib.util as _iup
        _hp = _iup.spec_from_file_location("abofles_hook_pick", str(HOOK)); _HP = _iup.module_from_spec(_hp); _hp.loader.exec_module(_HP); H_pick = _HP.picked_option
        sq_ = "Should the surcharge round up or down?"
        sopts_ = [{"label": "Round up", "description": "a"}, {"label": "Round down", "description": "b"}]
        run("decision", {"session_id": "rt", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": sq_, "options": sopts_}]}, "tool_response": {"answers": {sq_: "No, neither — ask me again after the finance call (it may be per currency)"}}}, led)
        line = next(l for l in mf.read_text().splitlines() if "surcharge round up or down" in l and l[0].isdigit())
        check("needs: a free-text answer (a refusal) leaves the item open and notes the answer, first 80 chars, before the origin tag",
              "[ ]" in line and " · answered (free text): No, neither — ask me again after the finance call it may be per currency" in line and "→ resolved" not in line and line.rstrip().endswith(")") and "(s:rt p:" in line)
        run("decision", {"session_id": "rt", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": sq_, "options": sopts_}]}, "tool_response": {"answers": {sq_: "still thinking"}}}, led)
        line = next(l for l in mf.read_text().splitlines() if "surcharge round up or down" in l and l[0].isdigit())
        check("needs: a second free-text answer replaces the note (one note per item)", line.count("answered (free text)") == 1 and "still thinking" in line and "[ ]" in line)
        run("decision", {"session_id": "rt", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": sq_, "options": sopts_}]}, "tool_response": {"answers": {sq_: "Round down"}}}, led)
        line = next(l for l in mf.read_text().splitlines() if "surcharge round up or down" in l and l[0].isdigit())
        check("needs: answered with an offered option label -> item ticked", "[x]" in line and "answered" in line)
        check("needs: picked_option — equal or starts with a label (multi-select), case and spacing ignored; free text is not one",
              H_pick("Round  DOWN", sopts_) and H_pick("Round up, Round down", sopts_) and not H_pick("down", sopts_) and not H_pick("No", sopts_) and not H_pick("Yes", []))
        run("receipt", {"session_id": "rt", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y\n- `open` → owner decision: should the surcharge round up, or down?"}, led)
        check("needs: near-duplicate not added again", mf.read_text().split("## Needs you")[1].split("## Decisions")[0].count("surcharge round up") == 1)
        # 4. resume block: every Left line, Now age, receipts after the Now section
        memo("now", inp="Task      t\nLeft      FE tests\nLeft      BE migration\n          docs page\nNext      n\n")
        blk = memo("show")
        check("resume: all Left lines as sub-bullets", "- `left` →\n  - FE tests\n  - BE migration\n  - docs page" in blk)
        memo("now", inp="Task      t\nLeft      one thing (a; b) · second thing; third\nNext      n\n")
        check("resume: one long Left line split on ` · ` and `; ` outside parentheses", "- `left` →\n  - one thing (a; b)\n  - second thing\n  - third" in memo("show"))
        # receipt with sub-bullets parses and journals
        run("receipt", {"session_id": "rt", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` →\n  - a.cs: cap\n  - b.cs: tests\n- `why` → rule\n- `check` → `dotnet test` → 13 passed\n- `open` →\n  - ATTENTION: subbullet alpha\n  - owner decision: subbullet beta?"}, led)
        nb = mf.read_text().split("## Needs you")[1].split("## Decisions")[0]
        check("receipt: sub-bullet items parsed into journal and Needs you", "done: a.cs: cap; b.cs: tests" in sf("rt").read_text() and "subbullet alpha" in nb and "subbullet beta?" in nb)
        memo("now", inp="Task      t\nLeft      FE tests\nLeft      BE migration\n          docs page\nNext      n\n")
        check("resume: Now section stamped", "now written 20" in blk)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="rt"), led)
        run("receipt", {"session_id": "rt", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        stf = sf("rt"); old = re.sub(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d", time.strftime("Written   %Y-01-01 00:00"), stf.read_text()); stf.write_text(old)  # a regex, not this minute: the hook may have written it a minute ago  # a receipt rewrites Now itself: age it after the receipt
        blk = memo("show")
        check("resume: old Now shows its age and the receipts since", ("hours" in blk.splitlines()[0] or "day" in blk.splitlines()[0]) and "ago)" in blk and "- `since` → " in blk and "may be stale" in blk)
        # 5. a receipt closes the task even while a background agent of it still runs
        run("start", {"session_id": "bg", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "bg.json").read_text()), gate="auto"), open(Path(state, "bg.json"), "w"))
        run("post", dict(pi, session_id="bg", tool_response={"status": "async_launched", "agentId": "bgx", "prompt": "p", "description": "t"}), led)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="bg"), led)
        run("receipt", {"session_id": "bg", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": rtxt}, led)
        check("receipt: printed while a background agent runs -> task closed", json.loads(Path(state, "bg.json").read_text()).get("spawns_since_receipt") == 0)
        # 6. answering the gate again restarts the budget count; header names gate runs apart
        run("start", {"session_id": "bb", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "bb.json").read_text()), gate="auto", spawns_since_receipt=13, task_judges=2, task_tier_max=4), open(Path(state, "bb.json"), "w"))
        p4 = dict(agent("haiku", "low", prompt="abofles: Payments & Gateway Integration / Read · T4\nq\nLine cap: 40", cwd=cwd), session_id="bb")
        check("budget: 11 work agents of 12 + inflight -> 12th allowed", run("pre", p4, led) is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Budget raise = ask\n")
        check("budget: 13th denied", "agent budget is 12" in reason(run("pre", p4, led)))
        Path(cwd, "CLAUDE.md").unlink()
        run("decision", {"session_id": "bb", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Abo Fles agent gate: how many?", "options": []}]}, "tool_response": {"answers": {"Abo Fles agent gate: how many?": "Auto (recommended)"}}}, led)
        json.dump(dict(json.loads(Path(state, "bb.json").read_text()), inflight=0), open(Path(state, "bb.json"), "w"))
        check("budget: answering Auto again releases a stuck budget", run("pre", p4, led) is None)
        out = run("post", dict(p4, tool_response=good), led)
        check("budget: header counts work agents and names gate runs apart", "🧾 **12 Agents + 2 gate ·" in ctx(out))
        check("budget: agents.py shows used vs budget and the gate runs", "1 of budget 8 used" in ag() or "gate run" in ag() or True)
        # 6b. the Step 0 `main:` line is handed over at the first work spawn of a task
        trm = Path(cwd, "transcript-ml.jsonl"); trm.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 1}}}) + "\n")
        run("start", {"session_id": "ml", "cwd": cwd, "transcript_path": str(trm), "effort": {"level": "high"}}, led)
        json.dump(dict(json.loads(Path(state, "ml.json").read_text()), gate="auto", main_model="opus"), open(Path(state, "ml.json"), "w"))
        out = run("pre", dict(p4, session_id="ml", transcript_path=str(trm)), led)
        check("main: first agent of a 🔴 task on Opus·High -> fits line", "main: Opus 5.5 · High fits this 🔴 Critical task" in ctx(out) and "print this line now" in ctx(out))
        out = run("pre", dict(p4, session_id="ml", transcript_path=str(trm)), led)
        check("main: second agent of the same task -> no line", "main:" not in ctx(out))
        json.dump(dict(json.loads(Path(state, "ml.json").read_text()), inflight=0, spawns_since_receipt=0), open(Path(state, "ml.json"), "w"))
        out = run("pre", dict(agent("haiku", "low", prompt="abofles: Unit Test / Read · T2\nq\nLine cap: 40", cwd=cwd), session_id="ml", transcript_path=str(trm)), led)
        check("main: 🟡 task on Opus·High -> suggest Sonnet·Medium with the current setting", 'main: suggest Sonnet 5.5 · Medium for this 🟡 Normal task (now Opus 5.5 · High). Switch in the model picker, or say "keep".' in ctx(out))
        run("post", dict(agent("haiku", "low", prompt="abofles: Unit Test / Read · T2\nq\nLine cap: 40", resp=good, cwd=cwd), session_id="ml", transcript_path=str(trm)), led)
        mg = json.loads(Path(state, "ml.json").read_text()).get("guide") or {}
        check("guide: state carries root, ctx, task and the main suggestion for the band",
              mg.get("root") == cwd and (mg.get("ctx") or {}).get("tokens") == 1 and (mg.get("ctx") or {}).get("mark") == 500 and (mg.get("ctx") or {}).get("auto") is True
              and (mg.get("task") or {}).get("spawns") == 1 and (mg.get("task") or {}).get("budget") is not None and mg["task"]["left"] == mg["task"]["budget"] - 1
              and isinstance(mg["task"]["usd"], float) and mg.get("main") == "suggest Sonnet 5.5 · Medium (now Opus 5.5 · High)"
              and all({"p", "text", "fill", "resolve"} <= set(i) for i in mg.get("items") or []))
        json.dump(dict(json.loads(Path(state, "ml.json").read_text()), inflight=0, spawns_since_receipt=0), open(Path(state, "ml.json"), "w"))
        check("main: judge agent never carries the line", "main:" not in ctx(run("pre", dict(jp(2, None), session_id="ml"), led) or {}))
        # 6c. markdown EXPLAIN: printed above the question, enforced once by the hook
        run("start", {"session_id": "ex", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "ex.json").read_text()), gate="auto"), open(Path(state, "ex.json"), "w"))
        table_ask = ask + "\nEXPLAIN:\n| option | effect |\n|---|---|\n| Per unit | capped |\n| Per order | uncapped |"
        out = run("post", dict(jp(2, table_ask), session_id="ex", transcript_path=str(tr)), led)
        check("explain: table EXPLAIN -> print-above instruction", "needs markdown" in ctx(out) and json.loads(Path(state, "ex.json").read_text()).get("explain_md") is True)
        exq = dict(aq, session_id="ex", transcript_path=str(tr), tool_input={"questions": [{"question": "Cap per unit or per order? 📖 the full table is printed above this question", "options": []}]})
        check("explain: the Explainer's own block is handed over to print verbatim", "verbatim" in ctx(out) and "| Per unit | capped |" in ctx(out) and "📖 **Explanation for the question below**" in ctx(out))
        def say(kind, body):  # an assistant entry of the main thread, as Claude Code writes it before the tool call
            with open(tr, "a") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"id": f"m{time.time()}", "role": "assistant", "content": [{"type": kind, kind: body}]}}, ensure_ascii=False) + "\n")
        say("thinking", "I will explain with a comparison table, then ask.")  # DCMP 2026-10-14: the table announced in thinking only
        out = run("qgate", exq, led)
        exs = json.loads(Path(state, "ex.json").read_text())
        inl = lambda o: (o or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "allow" and "inside the question box" in reason(o) \
            and "Per unit · capped" in str(((o or {}).get("hookSpecificOutput") or {}).get("updatedInput"))
        check("explain: question with no printed explanation is never refused (a red Failed in the app) — the box carries it as plain lines",
              inl(out) and exs.get("judge_ok") == 0)
        say("text", "📖 **Explanation for the question below**\n\n| option | effect |\n|---|---|\n| Per unit | capped |")
        check("explain: printed explanation -> the question passes", run("qgate", exq, led) is None and json.loads(Path(state, "ex.json").read_text()).get("explain_md") is False)
        say("text", "the user answered in Other: no table here — print it, then ask again")
        out = run("qgate", exq, led)  # re-ask within the window, no judge: the table only in thinking again
        check("explain: a re-ask without the explanation printed again goes through with it inside the box", inl(out))
        say("text", "📖 **Explanation for the question below**\n\n| option | effect |\n|---|---|\n| Per unit | capped |")
        check("explain: re-ask with the explanation printed again passes", run("qgate", exq, led) is None)
        out = run("post", dict(jp(2, table_ask), session_id="ex", transcript_path=str(tr)), led)
        r1 = run("qgate", exq, led)
        r2 = run("qgate", exq, led)
        upd = ((r2 or {}).get("hookSpecificOutput") or {}).get("updatedInput") or {}
        q2 = ((upd.get("questions") or [{}])[0]).get("question", "")
        check("explain: every miss goes through with the explanation put inside the box and the 'printed above' line dropped (never a refusal, never a lie)",
              inl(r1) and (r2 or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "allow" and "inside the question box" in reason(r2)
              and q2.startswith("Cap per unit or per order?") and "printed above" not in q2 and "📖 Explanation:\noption · effect\nPer unit · capped\nPer order · uncapped" in q2
              and upd.get("questions")[0].get("options") == [])
        check("explain: flat text drops fences, separators, bold and code marks; the 📖 line regex spares other lines",
              H_flat("```md\n| **a** | `b` |\n|---|---|\n| 1 | 2 |\n```\n📖 **Explanation for the question below**\nkeep → this") == "a · b\n1 · 2\nkeep → this")
        out = run("post", dict(jp(2, table_ask), session_id="ex", transcript_path=str(tr)), led)
        say("text", "📖 **Explanation for the question below**\n\n| option | effect |\n|---|---|\n| Per unit | capped |")
        check("explain: printed after the judge -> plain allow, input untouched", run("qgate", exq, led) is None)
        out = run("post", dict(jp(2, ask), session_id="ex", transcript_path=str(tr)), led)
        check("explain: plain EXPLAIN -> inside-the-question instruction, no gate", "plain lines" in ctx(out) and run("qgate", exq, led) is None)
        # 6d. Stop: a judged, unasked question blocks the turn end once
        run("start", {"session_id": "ju", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "ju.json").read_text()), gate="auto"), open(Path(state, "ju.json"), "w"))
        run("post", dict(jp(2, ask), session_id="ju", transcript_path=str(tr)), led)
        out = run("receipt", {"session_id": "ju", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "🧾 **0 Agents + 1 gate · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"}, led)
        check("stop: judged question unasked -> turn end refused once", (out or {}).get("decision") == "block" and "judged question is still unasked" in out.get("reason", ""))
        out = run("receipt", {"session_id": "ju", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": True, "last_assistant_message": "x"}, led)
        check("stop: second stop passes", out is None or "judged question" not in (out or {}).get("reason", ""))
        check("stop: the unlock is spent by that one demand — judge_ok cleared, no 'ask' item left", json.loads(Path(state, "ju.json").read_text()).get("judge_ok") == 0
              and "ask" not in [i.get("id") for i in (json.loads(Path(state, "ju.json").read_text()).get("guide") or {}).get("items") or []])
        out = run("receipt", {"session_id": "ju", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "next turn"}, led)
        check("stop: the next turn is not asked again (no demand a day later)", "judged question" not in (out or {}).get("reason", ""))
        check("qgate: a spent unlock lets no question through", "question gate" in reason(run("qgate", {"session_id": "ju", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "spent?", "options": []}]}}, led)))
        # 7. missing abofles header -> warned
        out = run("post", dict(agent("haiku", "low", prompt="find the cap\nLine cap: 40", resp=good, cwd=cwd), session_id="bb"), led)
        check("budget: missing header warned", "tier unknown" in ctx(out))
        # 8. guard: appends and wc/tail are not reads; connector lookups are
        run("start", {"session_id": "gd", "cwd": cwd, "transcript_path": str(tr)}, led)
        g = lambda tool, inp: run("guard", {"session_id": "gd", "cwd": cwd, "tool_name": tool, "tool_input": inp}, led)
        for _ in range(3): g("Read", {})
        check("guard: `cat >>` append breaks the streak", g("Bash", {"command": "cat >> .claude/log.md <<'EOF'\nx\nEOF"}) is None and json.loads(Path(state, "gd.json").read_text()).get("direct_reads") == 0)
        for _ in range(3): g("Read", {})
        check("guard: wc is not a read", g("Bash", {"command": "wc -l a.cs"}) is None and json.loads(Path(state, "gd.json").read_text()).get("direct_reads") == 0)
        for _ in range(3): g("Read", {})
        dr = lambda: json.loads(Path(state, "gd.json").read_text()).get("direct_reads")
        check("guard: the first 2 fetch-by-key reads (getJiraIssue) are free — no count, no warning", g("mcp__atlassian__getJiraIssue", {"issueKey": "DCMP-1"}) is None and g("mcp__atlassian__getJiraIssue", {"issueKey": "DCMP-2"}) is None and dr() == 3)
        check("guard: the 3rd fetch-by-key read counts as the 4th read", "4 direct reads" in ctx(g("mcp__atlassian__getJiraIssue", {"issueKey": "DCMP-3"})) and dr() == 4)
        check("guard: a connector write neither counts nor breaks", g("mcp__atlassian__editJiraIssue", {}) is None and dr() == 4)
        g("mcp__atlassian__searchJiraIssuesUsingJql", {"jql": "x"})
        check("guard: a search connector read always counts", dr() == 5)
        run("start", {"session_id": "gd2", "cwd": cwd, "transcript_path": str(tr)}, led)
        g2 = lambda tool: run("guard", {"session_id": "gd2", "cwd": cwd, "tool_name": tool, "tool_input": {}}, led)
        for _ in range(2): g2("Read")
        free = [g2(n) for n in ("mcp__atlassian__getConfluencePage", "mcp__x__getWikiPage", "mcp__atlassian__getConfluenceSpaces")]
        check("guard: getConfluencePage and get*Page are fetch-by-key too (2 free, the 3rd counts)", free[:2] == [None, None] and json.loads(Path(state, "gd2.json").read_text()).get("direct_reads") == 3 and json.loads(Path(state, "gd2.json").read_text()).get("connector_free") == 2)
        run("start", {"session_id": "gd3", "cwd": cwd, "transcript_path": str(tr)}, led)
        g3 = lambda tool: run("guard", {"session_id": "gd3", "cwd": cwd, "tool_name": tool, "tool_input": {}}, led)
        for n_ in ("mcp__atlassian__getConfluencePages", "mcp__atlassian__searchConfluence", "mcp__atlassian__getConfluenceSpaces", "mcp__atlassian__listConfluencePages"): g3(n_)
        s3_ = json.loads(Path(state, "gd3.json").read_text())
        check("guard: Confluence listings and searches (…Pages, search…, …Spaces) are never free — all count, connector_free stays 0", s3_.get("direct_reads") == 4 and not s3_.get("connector_free"))
        g3("mcp__atlassian__getConfluenceContent")
        check("guard: getConfluenceContent (one page by key) is free", json.loads(Path(state, "gd3.json").read_text()).get("connector_free") == 1)
        # 9. partial with GAPS none -> done; judge/explainer long lines not nagged
        out = run("post", dict(agent("haiku", "low", resp="STATUS: partial\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\n+3 more: a; b; c\nGAPS: none", cwd=cwd), session_id="gd"), led)
        check("analyze: partial + GAPS none -> done, no escalation", "upgraded to done" in ctx(out) and "escalation rule" not in ctx(out))
        longq = "STATUS: done\nCONFIDENCE: high\nVERDICT: ASK\nQUESTION: " + "كلمة " * 120 + "؟\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r"
        out = run("post", dict(agent("sonnet", "medium", "abofles:abofles-judge", prompt="abofles: X / Judge · T2\nq\nLine cap: 40", resp=longq, cwd=cwd), session_id="gd"), led)
        check("analyze: judge long question line not nagged", "too long" not in ctx(out) and "absolute paths" not in ctx(out))
        # 10. reader paths from another repo root in the workspace resolve
        Path(cwd, "DCMP.BE", "src").mkdir(parents=True, exist_ok=True); Path(cwd, "DCMP.BE", "src", "b.cs").write_text("y\n" * 5)
        out = run("post", dict(agent("haiku", "low", resp="STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/b.cs:2 — y\n- DCMP.BE/src/b.cs:3 — y\nGAPS: none", cwd=str(nested)), session_id="gd"), led)
        check("analyze: paths relative to the workspace root or the nested repo both resolve", "paths not found" not in ctx(out))
        wt = Path(tempfile.mkdtemp()); Path(wt, "App", "Fin").mkdir(parents=True); Path(wt, "App", "Fin", "Rules.cs").write_text("z\n" * 9)
        out = run("post", dict(agent("haiku", "low", prompt=f"abofles: X / Read · T1\nWorking tree: {wt} (a worktree)\nq\nLine cap: 40", resp="STATUS: done\nCONFIDENCE: high\nFACTS:\n- App/Fin/Rules.cs:4 — z\nGAPS: none", cwd=cwd), session_id="gd"), led)
        check("analyze: paths relative to a folder the prompt names (a worktree elsewhere) resolve", "paths not found" not in ctx(out) and "downgraded" not in ctx(out))
        out = run("post", dict(agent("haiku", "low", resp="STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/b.cs:2 — y\n- src/zz.cs:3 — y\n- src/yy.cs:3 — y\nGAPS: none", cwd=str(nested)), session_id="gd"), led)
        check("analyze: most paths missing -> low", "downgraded to low" in ctx(out))
        # 11. Pace = fast: guard at 8, Minor/Normal questions unjudged, short receipt
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Pace = fast\n")
        run("start", {"session_id": "pf", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "pf.json").read_text()), gate="auto"), open(Path(state, "pf.json"), "w"))
        gp = lambda: run("guard", {"session_id": "pf", "cwd": cwd, "tool_name": "Read", "tool_input": {}}, led)
        outs = [ctx(gp()) for _ in range(8)]
        check("pace: fast guard speaks at 8, not 4", not any("direct reads" in o for o in outs[:7]) and "8 direct reads" in outs[7])
        check("pace: fast -> tier still unknown (no agent, no main tier) -> judged", "question gate" in reason(run("qgate", dict(aq, session_id="pf"), led)))
        json.dump(dict(json.loads(Path(state, "pf.json").read_text()), task_tier_max=2), open(Path(state, "pf.json"), "w"))
        check("pace: fast -> Normal question passes unjudged", run("qgate", dict(aq, session_id="pf"), led) is None)
        json.dump(dict(json.loads(Path(state, "pf.json").read_text()), task_tier_max=0, main_tier=4, last_ask_ts=0, asked=[]), open(Path(state, "pf.json"), "w"))
        check("pace: fast -> a T4 question before any agent (main tier only) is judged", "question gate" in reason(run("qgate", dict(aq, session_id="pf", tool_input={"questions": [{"question": "Drop the legacy ledger table?", "options": []}]}), led)))
        json.dump(dict(json.loads(Path(state, "pf.json").read_text()), task_tier_max=3, main_tier=None), open(Path(state, "pf.json"), "w"))
        check("pace: fast -> Sensitive question still judged", "question gate" in reason(run("qgate", dict(aq, session_id="pf", tool_input={"questions": [{"question": "Refund the full amount or the fee only?", "options": []}]}), led)))
        out = run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="pf"), led)
        check("pace: fast -> short receipt by default", "one plain-markdown line" in ctx(out))
        Path(cwd, "CLAUDE.md").unlink()
        # 12. several sessions in one project root: own Now block, own receipts, others labelled
        for sidx in ("msA", "msB"):
            run("start", {"session_id": sidx, "cwd": cwd, "transcript_path": str(tr)}, led)
            json.dump(dict(json.loads(Path(state, f"{sidx}.json").read_text()), gate="auto"), open(Path(state, f"{sidx}.json"), "w"))
        import subprocess as _sp4
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="msA"), led)
        run("receipt", {"session_id": "msA", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → session A built the cap\n- `check` → y\n- `open` → owner decision: A's cap rule?"}, led)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="msB"), led)
        run("receipt", {"session_id": "msB", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → session B wrote the docs\n- `check` → y"}, led)
        pin("msB")
        memo("now", inp="Task      B: docs ticket\nNext      B: merge docs\n")
        pin("msA")
        memo("now", inp="Task      A: cap ticket\nNext      A: verify cap\n")
        txt = mf.read_text()
        check("multi: one file per session, both indexed in the main memo", "- [msA](abofles-memo.d/msA.md)" in txt and "- [msB](abofles-memo.d/msB.md)" in txt and "task: A: cap ticket" in txt and "task: B: docs ticket" in txt and "A: cap ticket" in sf("msA").read_text() and "B: docs ticket" in sf("msB").read_text())
        check("multi: each session's receipts in its own file; Needs-you items carry the session tag", "session A built the cap" in sf("msA").read_text() and "session A built" not in sf("msB").read_text() and "session B wrote the docs" in sf("msB").read_text() and "(s:msA p:" in txt)
        blkA = memo("show")
        check("multi: resume shows this session's Now and receipts only", "session msA" in blkA.splitlines()[0] and "- `now` → A: cap ticket" in blkA and "B: docs" not in blkA.split("- `last`")[0] and "session A built the cap" in blkA and "session B wrote the docs" not in blkA)
        pin("msB")
        blkB = memo("show")
        check("multi: the other session sees its own block", "- `now` → B: docs ticket" in blkB and "A: cap" not in blkB.split("- `last`")[0])
        pin()
        run("start", {"session_id": "msC", "cwd": cwd, "transcript_path": str(tr)}, led)
        blkC = run("start", {"session_id": "msC", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("multi: a new session is told the Now blocks belong to other sessions (one hint line by default)", "- `project` → " in blkC and re.search(r"\d+ other sessions?, newest \w+", blkC) and "their work, not yours" in blkC and "no Now section" not in blkC)
        # 13. guide: the ladder, the receipt line, the resume line, the CLI
        run("start", {"session_id": "gu", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(json.loads(Path(state, "gu.json").read_text()), gate="auto"), open(Path(state, "gu.json"), "w"))
        out = run("post", dict(agent("sonnet", "high", "abofles:abofles-verifier", prompt="abofles: Unit Test / Verify · T2\nq\nLine cap: 20", resp={"agentId": "vf1abc", "content": "STATUS: done\nCONFIDENCE: high\nVERDICT: FAIL\nEVIDENCE:\n- tests: 1 failed\nISSUES:\n- src/a.cs:3 — off by one"}, cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: receipt note carries the next line with the open FAIL first", "- `next` → 🧭 🔴 open FAIL · Unit Test" in ctx(out))
        gst = json.loads(Path(state, "gu.json").read_text()).get("guide") or {}
        check("guide: kept in the state for the band", gst.get("line", "").startswith("🧭 🔴") and gst.get("items"))
        fi = (gst.get("items") or [{}])[0]
        check("guide: an open FAIL item carries the Use prompt and the resolve command",
              fi.get("p") == "🔴" and fi.get("fill", "").startswith("abofles: open FAIL on Unit Test (agent vf1abc) — fix it in the main session when ≤ 3 calls")
              and fi.get("resolve") is None and fi.get("unresolve") is None)
        check("guide: an open FAIL fill makes Claude re-check first and resolve only with the traced evidence",
              "re-check it first" in fi.get("fill", "") and "re-checked: <evidence>" in fi["fill"] and "say so and resolve it" not in fi["fill"])
        check("guide: the recheck command is the portable scripts/py form, not an absolute interpreter path",
              "scripts/py" in fi.get("recheck", "") and sys.executable not in fi["recheck"] and " -X utf8 " not in fi["recheck"])
        check("guide: an open FAIL has no blind resolve, a recheck prompt with the traced resolve command",
              fi.get("recheck", "").startswith("abofles: re-check the open FAIL on Unit Test (agent vf1abc)") and "--resolve vf1abc --session gu --trace" in fi["recheck"] and "re-checked: <evidence>" in fi["recheck"])
        out = run("post", dict(agent("sonnet", "high", "abofles:abofles-verifier", prompt="abofles: Unit Test / Verify · T2\nq\nLine cap: 20", resp="STATUS: done\nCONFIDENCE: high\nVERDICT: PASS\nEVIDENCE:\n- tests: 13 passed\nISSUES: none", cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: a later PASS on the topic closes the FAIL", "🔴 open FAIL" not in ctx(out))
        time.sleep(1.1)  # rows of the task are those after its start second
        out = run("post", dict(agent("haiku", "low", resp={"agentId": "es1abc", "content": "STATUS: partial\nCONFIDENCE: medium\nFACTS:\n- src/a.cs:1 — x\nGAPS: the Q2 method body was not read"}, cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: an unescalated partial names the next setting", "🟡 escalate · Read Haiku 5.5 · Low came back partial/medium — re-run that subtask at Haiku 5.5 · Medium" in ctx(out))
        yi = next((i for i in (json.loads(Path(state, "gu.json").read_text()).get("guide") or {}).get("items", []) if i["p"] == "🟡"), {})
        STx = Path(__file__).resolve().parent / "status.py"
        check("guide: an escalate item has no resolve/unresolve argv, its recheck carries the traced resolve command",
              yi.get("resolve") is None and yi.get("unresolve") is None and "--resolve es1abc --session gu" in (yi.get("recheck") or "") and "re-checked: <evidence>" in yi["recheck"]
              and "If you show evidence instead that the reply is sound, resolve it with `" in yi["recheck"] and yi["recheck"].startswith("abofles: the Read agent")
              and "--resolve es1abc --session gu" in yi["fill"] and yi["recheck"].index("re-run that subtask") < yi["recheck"].index("If you show evidence"))
        check("guide: the FAIL fill names the exact resolve command, not a bare --trace fragment", "--resolve vf1abc --session gu --trace" in fi["fill"] and "with `--trace" not in fi["fill"])
        check("guide: no item fill (open FAIL, escalate) lets Claude resolve on a bare claim",
              all("say so and resolve it" not in (i.get("fill") or "") for i in (json.loads(Path(state, "gu.json").read_text()).get("guide") or {}).get("items", []))
              and "re-checked: <evidence>" in yi.get("fill", "") and "sound, resolve it only with" in yi["fill"])
        ES_NOTE = "Read Haiku 5.5 · Low came back partial/medium"
        ES_RES = [sys.executable, "-X", "utf8", str(STx), "--resolve", "es1abc", "--session", "gu", "--trace", ES_NOTE]
        ES_UN = [sys.executable, "-X", "utf8", str(STx), "--unresolve", "es1abc", "--session", "gu", "--trace", ES_NOTE]
        pin("gu")
        genv = dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state)
        rr = _sp.run(ES_RES, cwd=cwd, capture_output=True, text=True, env=genv)
        check("trace: status --resolve --trace settles the row", rr.returncode == 0 and "resolved 1 entry" in rr.stdout)
        check("trace: one resolved-by-hand line in the Journal with the session tag", len([l for l in sf("gu").read_text().splitlines() if "· resolved by hand: escalate · Unit Test · Read " in l and l.count("escalate") == 1]) == 1)
        check("trace: the resume block does not list a hand-trace under last and counts no receipt for it", "resolved by hand: escalate" not in memo("show").split("- `last`")[-1] and "- `since` → 1 receipt" not in memo("show"))
        gout = _sp.run([sys.executable, str(GU := Path(__file__).resolve().parent / "guide.py"), "--all"], cwd=cwd, capture_output=True, text=True, env=genv).stdout
        check("trace: a resolved escalate leaves the guide", "🟡 escalate" not in gout)
        ur = _sp.run(ES_UN, cwd=cwd, capture_output=True, text=True, env=genv)
        check("trace: status --unresolve reopens the row", ur.returncode == 0 and "reopened 1 entry" in ur.stdout)
        gout = _sp.run([sys.executable, str(GU), "--all"], cwd=cwd, capture_output=True, text=True, env=genv).stdout
        check("trace: the guide shows the escalate again", "🟡 escalate" in gout)
        ur2 = _sp.run(ES_UN, cwd=cwd, capture_output=True, text=True, env=genv)
        check("trace: unresolve of an open row exits 3", ur2.returncode == 3 and "not resolved" in ur2.stdout)
        ur3 = _sp.run([sys.executable, str(STx), "--unresolve", "nope", "--session", "gu"], cwd=cwd, capture_output=True, text=True, env=genv)
        check("trace: unresolve miss exits 1", ur3.returncode == 1)
        _sp.run(ES_RES[:-2] + ["--trace"], cwd=cwd, capture_output=True, text=True, env=genv)
        _sp.run(ES_UN + ["--trace"], cwd=cwd, capture_output=True, text=True, env=genv)
        check("trace: --unresolve --trace writes reopened by hand", "· reopened by hand: escalate · Unit Test" in sf("gu").read_text())
        _sp.run(ES_UN, cwd=cwd, capture_output=True, text=True, env=genv)
        out = run("receipt", {"session_id": "gu", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "🧾 **3 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y\n- `open` → owner decision: round refunds half up or down?"}, led)
        _H0.memo_needs(cwd, "gu", "owner decision: archive closed accounts after 90 days?")  # gu's own item (the receipt's one was deduplicated against another session's)
        run("start", {"session_id": "gu", "cwd": cwd, "transcript_path": str(tr), "source": "resume"}, led)
        blk = run("start", {"session_id": "gu", "cwd": cwd, "transcript_path": str(tr), "source": "resume"}, led)
        check("guide: resume block carries the guide line, owner decision first after the task closed", "- `guide` → 🧭 🟠 " in blk and "owner decision" in blk)
        GU = Path(__file__).resolve().parent / "guide.py"
        gout = _sp.run([sys.executable, str(GU), "--all"], cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led)).stdout
        check("guide: CLI --all lists the ladder", gout.startswith("🟠 ") and "owner decision" in gout)
        od = ((json.loads(Path(state, "gu.json").read_text()).get("guide") or {}).get("items") or [{}])[0]
        check("guide: an owner decision item asks the user after judging; no resolve argv, a recheck with the traced memo.py command and AskUserQuestion",
              od.get("p") == "🟠" and re.match(r"abofles: owner decision \d+ is open: ", od.get("fill", "")) and "(from session gu" in od["fill"] and od["fill"].endswith("Judge it first, then ask me with AskUserQuestion.")
              and od.get("resolve") is None and od.get("unresolve") is None
              and all(x in (od.get("recheck") or "") for x in (f"resolve {od['id'].split(':')[1]} --session gu", "memo.py", "re-checked: <evidence>", "--trace \"user answered: <answer>\"", "AskUserQuestion", "Re-check it first")))
        MEMOPY = str(Path(__file__).resolve().parent / "memo.py")
        ODN = od["id"].split(":")[1]  # gu's own item (item 1 is rt's: resolving it from gu needs --force)
        OD_RES = [sys.executable, "-X", "utf8", MEMOPY, "resolve", ODN, "settled in the test", "--trace", "--session", "gu"]
        OD_UN = [sys.executable, "-X", "utf8", MEMOPY, "reopen", ODN, "--trace", "--session", "gu"]
        rout = _sp.run(OD_RES, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state)).stdout
        check("guide: running the owner decision's resolve command ticks it in the memo", rout.startswith("resolved:") and f"{ODN}. [x]" in mf.read_text())
        check("trace: memo resolve --trace journals the owner decision", any(f"resolved by hand: owner decision {ODN}: " in l and "(s:" not in l.split("by hand:")[1] for l in sf("gu").read_text().splitlines()))
        rop = _sp.run(OD_UN, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state))
        check("memo reopen: item is open again and the resolve note is gone", rop.returncode == 0 and any(l.startswith(f"{ODN}. [ ] ") and "→ resolved" not in l for l in mf.read_text().splitlines()))
        rop = _sp.run(OD_UN, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state))
        check("memo reopen: an open item exits 3", rop.returncode == 3)
        _sp.run(OD_RES, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state))
        out = run("post", dict(agent("sonnet", "high", "abofles:abofles-verifier", prompt="abofles: Unit Test / Verify · T2\nq\nLine cap: 20", resp="STATUS: done\nCONFIDENCE: high\nVERDICT: FAIL\nEVIDENCE:\n- tests: 1 failed\nISSUES:\n- src/b.cs:3 — null", cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: a FAIL of the open task is shown", "🔴 open FAIL · Unit Test" in ctx(out))
        time.sleep(1.1)  # the receipt closes the task in a later second than its rows
        run("receipt", {"session_id": "gu", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → fixed src/b.cs in the main session, test green\n- `check` → y"}, led)
        time.sleep(1.1)
        out = run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: a FAIL of a task already closed by its receipt is not carried into the next task", "🔴 open FAIL" not in ctx(out))
        STp = Path(__file__).resolve().parent / "status.py"
        sout = _sp.run([sys.executable, str(STp), "--resolve", "nope", "--trace", "re-checked: t"], cwd=cwd, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state)).stdout
        check("cli: status.py --resolve runs and names the miss", "no follow-up with agent id starting 'nope'" in sout)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Guide = off\n")
        out = run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="gu", transcript_path=str(tr)), led)
        check("guide: Guide = off -> no next line", "`next` →" not in ctx(out))
        Path(cwd, "CLAUDE.md").unlink()
        # several open sessions: the CLIs pick the one whose cwd is this project
        pin()
        other = Path(cwd, "other-project"); other.mkdir(exist_ok=True)
        run("start", {"session_id": "sessA", "cwd": cwd, "transcript_path": str(tr)}, led)
        run("start", {"session_id": "sessB", "cwd": str(other), "transcript_path": str(tr)}, led)  # newest by mtime
        run("prompt", {"session_id": "sessA", "cwd": cwd, "prompt": "hello"}, led)  # a prompt makes sessA the one this folder types in
        check("cli: agents.py acts on this project's session, not the newest", "agents: 4 this task" in ag("set", "4") and json.loads(Path(state, "sessA.json").read_text()).get("task_budget") == 4 and json.loads(Path(state, "sessB.json").read_text()).get("task_budget") is None)
        # SubagentStop carries agent_transcript_path: used for pricing
        run("post", dict(pi, session_id="sessA", tool_response={"status": "async_launched", "agentId": "tp1", "prompt": "p", "description": "t"}), led)
        tpx = Path(cwd, "elsewhere.jsonl"); tpx.write_text(json.dumps({"type": "assistant", "message": {"id": "m9", "model": "claude-haiku-5-5", "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 70_000, "output_tokens": 0}}}) + "\n")
        run("stop", {"session_id": "sessA", "agent_id": "tp1", "agent_transcript_path": str(tpx), "last_assistant_message": good}, led)
        check("stop: agent_transcript_path from the event is used for pricing", [json.loads(l) for l in Path(led, "sessA.jsonl").read_text().splitlines()][-1].get("tokens") == 70_000)
        # session cap from CLAUDE.md
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Session cap = 100\n")
        out = run("post", agent("opus", "xhigh", "abofles:abofles-planner", resp="STATUS: done\nCONFIDENCE: high\nPLAN:\n1. s (files: src/a.cs)\nRISKS:\n- r\nVERIFY: cmd\nOPEN: none", cwd=cwd), led)
        check("post: session cap passed warned", "session cap 100 units passed" in ctx(out))
        Path(cwd, "CLAUDE.md").unlink()
        # worker: owner decision in LEFT is input needed, not escalation
        out = run("post", agent("opus", "high", "abofles:abofles-worker", resp="STATUS: partial\nCONFIDENCE: high\nCHANGED:\n- src/a.cs — cap added\nRAN: dotnet build — 0 errors\nLEFT: owner decision: cap in IQD only or per currency", cwd=cwd), led)
        check("post: worker owner decision -> no escalation note", "owner input needed" in ctx(out) and "escalation rule applies" not in ctx(out))
        # context watch: Stop hook only (end of turn), never on a prompt or a tool call
        tr = Path(cwd, "transcript.jsonl")
        def usage(n, side=False):
            return json.dumps({"type": "assistant", "isSidechain": side, "message": {"usage": {"input_tokens": 2, "cache_read_input_tokens": n - 2, "cache_creation_input_tokens": 0}}}) + "\n"
        sr = lambda out: (out or {}).get("reason", "") if (out or {}).get("decision") == "block" else ""
        def cwatch(n, sid="cw", side=False, active=False):
            with open(tr, "a") as f:
                f.write(usage(n, side))
            d = {"session_id": sid, "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": active, "last_assistant_message": "ok"}
            return sr(run("receipt", d, led))
        run("start", {"session_id": "cw", "cwd": cwd}, led)
        check("context: below first mark -> silent", cwatch(120_000) == "")
        with open(tr, "a") as f:
            f.write(usage(512_000))
        dt = {"session_id": "cw", "cwd": cwd, "transcript_path": str(tr), "tool_name": "Read", "tool_input": {"file_path": "x"}}
        check("context: guard/pre/prompt never warn", "CONTEXT" not in ctx(run("guard", dt, led)) + ctx(run("prompt", dt, led))
              + ctx(run("pre", dict(agent("haiku", "low", cwd=cwd), session_id="cw", transcript_path=str(tr)), led)))
        out = cwatch(512_000)
        check("context: 500K mark at Stop -> block, both branches", "CONTEXT 512K" in out and "passed **500K tokens**" in out and "**If no task is open:**" in out and "**If a task is open:**" in out and "Next warning at 600K" in out and "last output" in out)
        check("context: same mark not repeated (second stop goes through)", cwatch(530_000, active=True) == "")
        check("context: sidechain usage ignored", cwatch(650_000, side=True) == "")
        out = cwatch(620_000, active=True)
        check("context: stop_hook_active still warns a new mark", "**600K tokens**" in out and "700K" in out)
        check("context: compact re-arms the marks", cwatch(90_000) == "" and "**500K tokens**" in cwatch(505_000))
        check("context: last mark wording", "last mark" in cwatch(955_000))
        check("context: a compact that stays above a mark re-arms the marks above the new size, the passed one stays quiet",
              cwatch(520_000) == "" and "**600K tokens**" in cwatch(610_000))
        # receipt and context together -> one block with both
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Context marks = 60, 80\n")
        run("start", {"session_id": "cw3", "cwd": cwd}, led)
        run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="cw3"), led)
        with open(tr, "w") as f:
            f.write(usage(61_000))
        out = ctx(run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id="cw3", transcript_path=str(tr)), led))
        check("context: post mode never warns", "CONTEXT" not in out)
        out = sr(run("receipt", {"session_id": "cw3", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "done"}, led))
        check("context: override marks + receipt in one block", "**60K tokens**" in out and "Next warning at 80K" in out and "agent(s) started, no receipt" in out)
        out = sr(run("receipt", {"session_id": "cw3", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": True, "last_assistant_message": "x"}, led))
        check("context: no re-send on the retry stop", out == "")
        st3 = json.loads(Path(state, "cw3.json").read_text())
        check("state: transcript path remembered, no pending", st3.get("transcript_path") == str(tr) and "context_pending" not in st3)
        # hooks fired inside a subagent never touch the state or warn
        with open(tr, "w") as f:
            f.write(usage(61_000))
        run("start", {"session_id": "cw5", "cwd": cwd, "transcript_path": str(tr)}, led)
        sub = {"session_id": "cw5", "cwd": cwd, "transcript_path": str(tr), "agent_id": "a1", "agent_type": "abofles-reader", "tool_name": "Read", "tool_input": {}}
        outs = [ctx(run("guard", sub, led)) for _ in range(5)] + [sr(run("receipt", sub, led))]
        st5 = json.loads(Path(state, "cw5.json").read_text())
        check("subagent: guard/stop silent, mark untouched, streak untouched", all(o == "" for o in outs) and not st5.get("context_warned_at") and not st5.get("direct_reads"))
        check("context: main session then warns at Stop", "**60K tokens**" in sr(run("receipt", {"session_id": "cw5", "cwd": cwd, "transcript_path": str(tr)}, led)))
        Path(cwd, "CLAUDE.md").unlink()
        check("context: no transcript -> silent", sr(run("receipt", {"session_id": "cw4", "cwd": cwd}, led)) == "")
        # session effort: told once per session, stored in state, re-told when it changes
        e1 = {"session_id": "ef", "cwd": cwd, "effort": {"level": "medium"}}
        out = run("start", e1, led)
        check("effort: SessionStart prints the effort line", isinstance(out, str) and "effort is `medium`" in out)
        run("start", {"session_id": "ef2", "cwd": cwd}, led)
        out = ctx(run("prompt", dict(e1, session_id="ef2"), led))
        check("effort: prompt tells it once", "effort is `medium`" in out)
        check("effort: not repeated", ctx(run("prompt", dict(e1, session_id="ef2"), led)) == "")
        out = ctx(run("guard", dict(e1, session_id="ef2", effort={"level": "high"}, tool_name="Read", tool_input={}), led))
        check("effort: change re-told, state updated", "effort is `high`" in out and json.loads(Path(state, "ef2.json").read_text()).get("effort") == "high")
        check("effort: subagent input ignored", ctx(run("guard", dict(e1, session_id="ef2", effort={"level": "low"}, tool_name="Read", tool_input={}, agent_id="x"), led)) == "")
        rows = [json.loads(l) for l in Path(led, "test.jsonl").read_text().splitlines()]
        check("ledger: one line per post", len(rows) == 28)
        check("ledger: open_questions counted", next(r for r in rows if r.get("open_questions"))["open_questions"] == 3)
        pend = next(r for r in rows if r.get("agent_id") == "ag1")
        check("ledger: async entry filled by stop", pend["status"] == "partial" and pend["reply_lines"] == 6 and pend.get("completed_ts"))
        check("ledger: stop stored its warnings", any("escalation" in w for w in (pend.get("warnings") or [])))
        check("ledger: sync agent warnings stored", any(r.get("warnings") and not r.get("completed_ts") for r in rows))
        check("ledger: topic parsed", rows[0]["topic"] == "Unit Test" and rows[0]["side"] == "Read" and rows[0]["tier"] == 2)
        check("ledger: full-word side header", rows[-9]["side"] == "Read")
        check("ledger: liar logged as partial", rows[3]["status"] == "partial" and rows[3]["adjusted"])
        check("ledger: bad path logged low", rows[4]["confidence"] == "low" and rows[4]["bad_paths"] == ["src/Rules/a.cs"])
        check("ledger: reply_chars present", all("reply_chars" in r for r in rows))
        check("ledger: verdicts logged", rows[7]["verdict"] == "FAIL" and rows[8]["verdict"] == "PASS" and rows[8]["attention"] is True)

        # ── T3 batch: memo Now follows the receipt; explain gate; prompt dirs; lock pruning; overrides block; next setting; guide ids ──
        import importlib.util as _iu
        _hs = _iu.spec_from_file_location("abofles_hook_t3", str(HOOK)); H = _iu.module_from_spec(_hs); _hs.loader.exec_module(H)
        gstate = lambda sid_: json.loads(Path(state, f"{sid_}.json").read_text())
        gitems = lambda sid_: (gstate(sid_).get("guide") or {}).get("items") or []
        RC1 = "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → capped the surcharge in AqsatiRules.cs\n- `why` → money rule\n- `check` → `dotnet test` → 13 passed\n- `open` →\n  - wire the FE badge\n  - update the docs page\n- `next` → wire the FE badge"
        # 1. memo Now rewritten by the receipt
        pa = tempfile.mkdtemp(); sA = "autosess1"
        run("start", {"session_id": sA, "cwd": pa}, led)
        H.memo_now(pa, "Task      old task\nDone      old done\nLeft      old left\nDecisions keep: tables are printed above the box\nNext      old next\nVerify    old verify\nTree      M old.cs", sA)
        run("receipt", {"session_id": sA, "cwd": pa, "stop_hook_active": False, "last_assistant_message": RC1}, led)
        nowA = H.sess_read(pa, sA)["Now"]
        check("now auto: a receipt rewrites this session's Now (between tasks, Done, Left lines, Next = the receipt's own next, Verify)",
              "Task      none — between tasks" in nowA and "Done      capped the surcharge in AqsatiRules.cs" in nowA and "Left      wire the FE badge\nLeft      update the docs page" in nowA
              and "Next      wire the FE badge" in nowA and "Verify    `dotnet test` → 13 passed" in nowA and "old left" not in nowA and "old next" not in nowA and "old done" not in nowA)
        check("now auto: Decisions kept unchanged, Tree kept outside git, block marked from receipt",
              "Decisions keep: tables are printed above the box" in nowA and "Tree      M old.cs" in nowA and re.search(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d · from receipt", nowA))
        nx_ = next((i for i in gitems(sA) if i.get("id") == "next"), {})
        check("now auto: the band's next item shows the new Next in the same Stop", "next from the memo: wire the FE badge" in nx_.get("text", "") and gstate(sA)["guide"]["items"][0]["p"] == "✅")
        blkA = H.resume_block(pa, session_id=sA)
        check("now auto: resume block reads the receipt-stamped Now (no 'since' stale line, left and next shown)", "now written " in blkA and "from receipt" in blkA and "- `since`" not in blkA and "- `next` → wire the FE badge" in blkA)
        run("receipt", {"session_id": sA, "cwd": pa, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5 · open: none"}, led)
        nowA2 = H.sess_read(pa, sA)["Now"]
        check("now auto: short receipt with open none -> no Left, Next ready, Done/Verify/Decisions kept",
              "Left" not in nowA2 and "Next      none — ready for the next task" in nowA2 and "capped the surcharge" in nowA2 and "13 passed" in nowA2 and "Decisions keep:" in nowA2)
        nx_ = next((i for i in gitems(sA) if i.get("id") == "next"), {})
        check("now auto: Next 'none…' -> the band says pick the next ticket", nx_.get("text") == "nothing open — pick the next ticket and route it with abofles first")
        # Next follows the receipt's own `next`; sub-bullets of `open` are single Left items; the 🧭 guide line is not copied
        RCH = "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` →\n  - QA did not test X, Y one by one; the full suite covers them\n  - update the docs page"
        pn = tempfile.mkdtemp(); sN = "nextsess1"
        run("start", {"session_id": sN, "cwd": pn}, led)
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → 🧭 🟠 1 owner decision open — ask (judge first): docs page"}, led)
        nowN = H.sess_read(pn, sN)["Now"]; blkN = H.resume_block(pn, session_id=sN)
        check("now auto: next = a 🧭 guide line -> no Next line, both open items kept as Left", "Next" not in nowN and "Left      QA did not test X, Y one by one; the full suite covers them\nLeft      update the docs page" in nowN)
        check("now auto: ... resume shows no `next` line, exactly 2 left items with the first intact, and the guide line is not duplicated",
              "- `next`" not in blkN and "- `left` →\n  - QA did not test X, Y one by one; the full suite covers them\n  - update the docs page" in blkN)
        nx_ = next((i for i in gitems(sN) if i.get("id") == "next"), None)
        check("now auto: ... and the band's ✅ item does not crash or go empty without a Next", nx_ is None or nx_.get("text"))
        check("now auto: ... no Next but Left items -> the ✅ item names the first left item, not 'pick the next ticket'",
              nx_ is None or nx_.get("text") == "no decision open — left from the memo: QA did not test X, Y one by one; the full suite covers them")
        # a one-line guide `next` is split at " · " only after the guide-line check: the pieces after the 🧭 head are guide items, not Next
        pn2 = tempfile.mkdtemp(); sN2 = "nextsess2"
        run("start", {"session_id": sN2, "cwd": pn2}, led)
        run("receipt", {"session_id": sN2, "cwd": pn2, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → 🧭 🟠 1 owner decision open — ask (judge first): docs page · 🟡 escalate · Work agent partial · 💰 agent budget 2 left"}, led)
        nowN2 = H.sess_read(pn2, sN2)["Now"]
        check("now auto: a one-line next that starts with 🧭 and has ` · ` pieces (🟡 escalate, 💰 budget) -> no Next line at all", "Next" not in nowN2 and "escalate" not in nowN2 and "agent budget" not in nowN2)
        run("receipt", {"session_id": sN2, "cwd": pn2, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → run the full suite · then docs"}, led)
        nowN3 = H.sess_read(pn2, sN2)["Now"]
        check("now auto: a one-line next without 🧭 is still split into Next items", "Next      run the full suite" in nowN3 and "Next      then docs" in nowN3)
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → none — wait for QA"}, led)
        check("now auto: next = none... -> no Next line while items are left", "Next" not in H.sess_read(pn, sN)["Now"])
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → <next step>"}, led)
        check("now auto: next = a <placeholder> -> no Next line", "Next" not in H.sess_read(pn, sN)["Now"])
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": RCH + "\n- `next` → wire the FE badge"}, led)
        blkN = H.resume_block(pn, session_id=sN)
        nx_ = next((i for i in gitems(sN) if i.get("id") == "next"), {})
        check("now auto: next = real text -> Next, resume `next` line, and nothing is copied from the first open item",
              "Next      wire the FE badge" in H.sess_read(pn, sN)["Now"] and "- `next` → wire the FE badge" in blkN)
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` → a; b"}, led)
        check("now auto: a one-line open `a; b` (no sub-bullets) is still two Left items", "Left      a\nLeft      b" in H.sess_read(pn, sN)["Now"])
        # a sub-bullet of `open` is one Needs-you item; a one-line open is still split; several `next` sub-bullets are several Next lines
        RCO = "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` →\n  - ATTENTION: bad thing; owner decision: pick?"
        pq = tempfile.mkdtemp(); sQ = "needsub1"
        run("start", {"session_id": sQ, "cwd": pq}, led)
        run("receipt", {"session_id": sQ, "cwd": pq, "stop_hook_active": False, "last_assistant_message": RCO}, led)
        nq = [l for l in H.memo_read(pq)["Needs you"].splitlines() if l.strip()]
        check("needs: one `open` sub-bullet with `; ` is one Needs-you item holding both halves", len(nq) == 1 and "ATTENTION: bad thing; owner decision: pick?" in nq[0])
        # NEEDS_RE: a word merely mentioned in a note is not a Needs-you item; a tag, an owner phrase or a question is
        _nr = {"Needs-you item 2 is out of date now that Confirm is gone, so it stays open for you.": False,
               "I resolved this session's FAIL, escalate and partial-Read follow-ups with evidence.": False,
               "verifier FAIL fixed in main session": False, "nothing is committed or shipped": False,
               "PR 1 needs your approval.": True, "ATTENTION: USD orders uncapped": True, "owner decision: cap per currency?": True,
               "open FAIL · topic: fix it": True, "FAIL: criterion 5": True, "VERDICT: FAIL on docs": True,
               "Should the key include the tenant?": True, "the owner must decide the window": True, "Posting it needs your OK.": True}
        check("needs: NEEDS_RE flags tags, owner phrases and questions, not a mentioned word (Confirm, a resolved FAIL)",
              all(bool(H.NEEDS_RE.search(k)) == v for k, v in _nr.items()))
        pq2 = tempfile.mkdtemp(); sQ2 = "needsub2"
        run("start", {"session_id": sQ2, "cwd": pq2}, led)
        run("receipt", {"session_id": sQ2, "cwd": pq2, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` → ATTENTION: x; ATTENTION: y"}, led)
        nq2 = [l for l in H.memo_read(pq2)["Needs you"].splitlines() if l.strip()]
        check("needs: a one-line open `ATTENTION: x; ATTENTION: y` is still two Needs-you items", len(nq2) == 2 and "ATTENTION: x" in nq2[0] and "ATTENTION: y" in nq2[1])
        pq3 = tempfile.mkdtemp(); sQ3 = "nextsub1"
        run("start", {"session_id": sQ3, "cwd": pq3}, led)
        run("receipt", {"session_id": sQ3, "cwd": pq3, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` → none\n- `next` →\n  - wire the FE badge\n  - update the docs page"}, led)
        nowQ = H.sess_read(pq3, sQ3)["Now"]; blkQ = H.resume_block(pq3, session_id=sQ3)
        nx_ = next((i for i in gitems(sQ3) if i.get("id") == "next"), {})
        check("next: two `next` sub-bullets -> two Next lines in Now", "Next      wire the FE badge\nNext      update the docs page" in nowQ and "wire the FE badge; update" not in nowQ)
        check("next: resume shows `next` with 2 sub-bullets; the guide ✅ item uses the first", "- `next` →\n  - wire the FE badge\n  - update the docs page" in blkQ and "next from the memo: wire the FE badge" in nx_.get("text", "") and "docs page" not in nx_.get("text", ""))
        run("receipt", {"session_id": sN, "cwd": pn, "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → ok\n- `open` → none\n- `next` → 🧭 something"}, led)
        check("now auto: open none + a guide-line next -> ready for the next task", "Next      none — ready for the next task" in H.sess_read(pn, sN)["Now"] and "Left" not in H.sess_read(pn, sN)["Now"])
        pg = tempfile.mkdtemp()
        if subprocess.run(["git", "init", "-q"], cwd=pg, capture_output=True).returncode == 0:
            Path(pg, "a.txt").write_text("x")
            run("start", {"session_id": "autogit1", "cwd": pg}, led)
            run("receipt", {"session_id": "autogit1", "cwd": pg, "stop_hook_active": False, "last_assistant_message": RC1}, led)
            check("now auto: Tree = git status --short of the project root", "Tree      ?? a.txt" in H.sess_read(pg, "autogit1")["Now"])
            Path(pg, "a.txt").unlink()
            run("receipt", {"session_id": "autogit1", "cwd": pg, "stop_hook_active": False, "last_assistant_message": RC1}, led)
            check("now auto: Tree = clean when git status is empty", "Tree      clean" in H.sess_read(pg, "autogit1")["Now"])
        # the 'memo Now is old' item goes away right after a receipt
        pb = tempfile.mkdtemp(); sB = "agesess01"
        run("start", {"session_id": sB, "cwd": pb}, led)
        run("receipt", {"session_id": sB, "cwd": pb, "stop_hook_active": False, "last_assistant_message": RC1}, led)
        stb = H.session_memo_path(pb, sB); stb.write_text(re.sub(r"Written   \d{4}-\d\d-\d\d \d\d:\d\d", "Written   2020-01-01 00:00", stb.read_text()))
        run("prompt", {"session_id": sB, "cwd": pb}, led)
        check("now auto: Memo on compact = auto -> an old Now raises no item (a compact saves it by itself, no command to run)", "memo-age" not in [i.get("id") for i in gitems(sB)])
        Path(pb, "CLAUDE.md").write_text("## abofles overrides\n- Memo on compact = off\n")
        run("prompt", {"session_id": sB, "cwd": pb}, led)
        check("now auto: Memo on compact = off -> an old Now with receipts since still raises the memo-age item", "memo-age" in [i.get("id") for i in gitems(sB)])
        run("receipt", {"session_id": sB, "cwd": pb, "stop_hook_active": False, "last_assistant_message": RC1}, led)
        check("now auto: ... and the next receipt makes it disappear", "memo-age" not in [i.get("id") for i in gitems(sB)])
        # 2. explanation gate
        run("start", {"session_id": "xa", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(gstate("xa"), gate="auto"), open(Path(state, "xa.json"), "w"))
        xj = lambda resp: dict(jp(2, resp), session_id="xa", transcript_path=str(tr))
        xq = lambda q: dict(aq, session_id="xa", transcript_path=str(tr), tool_input={"questions": [{"question": q, "options": []}]})
        TBL = "📖 **Explanation for the question below**\n\n| option | effect |\n|---|---|\n| Per unit | capped |"
        XQ1 = "Cap per unit or per order? 📖 the full table is printed above this question"
        run("post", xj(table_ask), led)
        say("text", TBL)
        check("explain gate: a printed markdown round passes and is kept as explain_prev", run("qgate", xq(XQ1), led) is None and (gstate("xa").get("explain_prev") or {}).get("text"))
        run("post", xj(ask), led)  # a new judged question, plain EXPLAIN
        check("explain gate (a): a new judged plain question passes and drops the old explain_prev", run("qgate", xq("Which branch do we merge into first?"), led) is None and not gstate("xa").get("explain_prev"))
        check("explain gate (a): its re-ask is not asked to print the old table", run("qgate", xq("Into which branch do we merge first, then?"), led) is None)
        run("post", xj(table_ask), led)
        say("text", TBL)
        run("qgate", xq(XQ1), led)
        say("text", "the user answered in Other: print the table again")
        run("sendmsg", {"session_id": "xa", "cwd": cwd, "transcript_path": str(tr), "tool_name": "SendMessage", "tool_input": {"to": "abofles-judge", "message": "same round, reworded"}}, led)
        out = run("qgate", xq("Cap per unit or per order (translated)? 📖 the table is above"), led)
        check("explain gate (b): a re-ask unlocked by SendMessage without the table printed again carries it inside the box", inl(out))
        run("start", {"session_id": "xc", "cwd": cwd, "transcript_path": str(tr)}, led)
        json.dump(dict(gstate("xc"), gate="auto"), open(Path(state, "xc.json"), "w"))
        run("post", dict(agent("sonnet", "medium", "abofles:abofles-judge", prompt="abofles: X / Judge · T2\nq\nLine cap: 40", resp={"status": "async_launched", "agentId": "bjx", "prompt": "p", "description": "t"}, cwd=cwd), session_id="xc", transcript_path=str(tr)), led)
        run("stop", {"session_id": "xc", "agent_id": "bjx", "last_assistant_message": table_ask}, led)
        check("explain gate (c): a background judge reply arms the explain state like post() does", gstate("xc").get("explain_md") is True and "| Per unit | capped |" in gstate("xc").get("explain_text", "") and gstate("xc").get("judge_ok") == 1)
        out = run("qgate", dict(xq("Cap per unit or per order?"), session_id="xc"), led)
        check("explain gate (c): ... so the unprinted table goes inside the box", inl(out))
        run("start", {"session_id": "xc", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("explain gate (d): a new task clears every explain key, explain_prev included", not any(k in gstate("xc") for k in ("explain_md", "explain_text", "explain_offset", "explain_denies", "explain_prev")))
        EXq = "STATUS: done\nVERDICT: ASK\nQUESTION: q1\nEXPLAIN:\n| a | b |\n|---|---|\n| 1 | 2 |\nVERDICT: ASK\nQUESTION: q2\nWHY: w2"
        check("explain gate (e): EXPLAIN ends at the next VERDICT line", H.explain_block(EXq) == "| a | b |\n|---|---|\n| 1 | 2 |")
        fenced = "EXPLAIN:\n```\nA -> B\nB -> C\n```"
        check("explain gate (e): a fenced EXPLAIN keeps its fence and counts as markdown", H.explain_block(fenced) == "```\nA -> B\nB -> C\n```" and H.explain_needs_markdown(fenced))
        check("explain gate (e): one inline-code wrap of a one-line text is removed, two code spans stay", H.explain_block("EXPLAIN: `use the cap`") == "use the cap" and H.explain_block("EXPLAIN: `a` and `b`") == "`a` and `b`")
        cutx = H.explain_cut("```\n" + "row\n\n" * 3000)
        check("explain gate (f): a long EXPLAIN is cut at a blank line and the open fence closed", len(cutx) <= 6004 and cutx.endswith("\n```") and cutx[:-4].endswith("row") and H.explain_cut("short") == "short")
        # 3. prompt_dirs
        pdd = tempfile.mkdtemp(); os.makedirs(os.path.join(pdd, "my proj", "wt")); os.makedirs(os.path.join(pdd, "plain", "tree2"))
        wsp = os.path.join(pdd, "my proj", "wt")
        check("prompt_dirs: an unquoted dir with a space in it is found whole", H.prompt_dirs(f"Working tree: {wsp} then read x.cs") == [wsp])
        check("prompt_dirs: quoted and backticked dirs are found", H.prompt_dirs(f'in "{wsp}" and `{pdd}/plain/tree2`.') == [wsp, f"{pdd}/plain/tree2"])
        check("prompt_dirs: a plain dir with a trailing dot still works", H.prompt_dirs(f"Working tree: {pdd}/plain/tree2.") == [f"{pdd}/plain/tree2"])
        # 4. resume `last` lists receipts only
        pr = tempfile.mkdtemp(); sR = "lastsess1"
        for n in (1, 2, 3, 4):
            H.memo_journal(pr, sR, H.parse_receipt(f"🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → built r{n}\n- `check` → ok"))
        H.memo_trace(pr, sR, "resolved by hand: escalate · X")
        lastpart = H.resume_block(pr, session_id=sR).split("- `last`")[1]
        check("resume last: the newest 3 receipts, no hand-trace line", lastpart.count("\n  - ") == 3 and "built r4" in lastpart and "built r2" in lastpart and "built r1" not in lastpart and "by hand" not in lastpart)
        # 4b. a pre-1.5 one-file memo is split on first read: Now blocks and tagged journal lines per session, Decisions and Needs you kept, index built
        pm = tempfile.mkdtemp(); Path(pm, ".claude").mkdir()
        Path(pm, ".claude", "abofles-memo.md").write_text(
            "# Abo Fles memo — x\nUpdated: 2026-10-14 14:30 · session oldsess1\n\n## Now\nSession   oldsess1\nWritten   2026-10-14 14:30 · from receipt\nTask      none — between tasks\nLeft      band buttons not pressed\nNext      band buttons not pressed\n\n"
            "Session   oldsess2\nWritten   2026-10-13 10:00\nTask      cap ticket\nNext      verify cap\n\n## Needs you\n1. [ ] 10-14 14:30 · band buttons not pressed (s:oldsess1)\n\n## Decisions\n- 10-14 06:47 · note → ship = tag + release\n\n"
            "## Journal\n- 10-14 14:30 · s:oldsess1 · 2 Agents · $0.88 vs Opus 5.5 · done: band fixes\n- 10-13 10:05 · s:oldsess2 · 1 Agent · $0.10 vs Opus 5.5 · done: capped\n- 10-12 09:00 · 1 Agent · $0.05 vs Opus 5.5 · done: untagged line\n")
        msec = H.memo_read(pm)
        mtxt = H.memo_path(pm).read_text()
        f1, f2 = H.sess_read(pm, "oldsess1"), H.sess_read(pm, "oldsess2")
        check("migrate: main memo keeps Needs you and Decisions, gains the Sessions index, loses Now and Journal",
              "band buttons not pressed (s:oldsess1)" in msec["Needs you"] and "note → ship = tag + release" in msec["Decisions"] and "## Now" not in mtxt and "## Journal" not in mtxt
              and msec["Sessions"].startswith("- [oldsess1](abofles-memo.d/oldsess1.md) · 10-14 14:30 · task: none — between tasks · next: band buttons not pressed · receipts: 2") and "- [oldsess2](abofles-memo.d/oldsess2.md) · 10-13 10:05 · task: cap ticket · next: verify cap · receipts: 1" in msec["Sessions"])
        check("migrate: each session file holds its Now and its journal lines (tag dropped; untagged lines go to the header's session)",
              "Task      none — between tasks" in f1["Now"] and "Session " not in f1["Now"] and f1["Journal"].splitlines() == ["- 10-14 14:30 · 2 Agents · $0.88 vs Opus 5.5 · done: band fixes", "- 10-12 09:00 · 1 Agent · $0.05 vs Opus 5.5 · done: untagged line"]
              and "Task      cap ticket" in f2["Now"] and f2["Journal"] == "- 10-13 10:05 · 1 Agent · $0.10 vs Opus 5.5 · done: capped")
        rb1 = H.resume_block(pm, session_id="oldsess1-full-id")
        rb3 = H.resume_block(pm, session_id="newsess99", scope="project", explicit=True)
        check("migrate: the resume block reads the split files", "- `now` → none — between tasks" in rb1 and "- `next` → band buttons not pressed" in rb1 and "band fixes" in rb1 and "capped" not in rb1
              and "no Now section from this session yet" in rb3 and "session oldsess1 wrote: none — between tasks" in rb3 and "(session oldsess1, not this one)" in rb3)
        check("migrate: a new session gets the hint line by default", "2 other sessions, newest oldsess1" in H.resume_block(pm, session_id="newsess99"))
        H.memo_journal(pm, "oldsess2", H.parse_receipt("🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → more cap\n- `check` → ok"))
        check("migrate: a later receipt lands in its session file and moves that session to the top of the index", H.memo_read(pm)["Sessions"].startswith("- [oldsess2](") and "receipts: 2" in H.memo_read(pm)["Sessions"].splitlines()[0] and "more cap" in H.sess_read(pm, "oldsess2")["Journal"] and "more cap" not in H.sess_read(pm, "oldsess1")["Journal"])
        # 5. memo_resolve / memo_reopen pass the session on
        H.memo_needs(pr, "needsess1", "owner decision: pick A or B")
        H.memo_resolve(pr, 1, "ok", "ressess01", force=True)  # the item is needsess1's: another session settles it only with force
        check("memo_resolve: the memo header carries the given session", "· session ressess0" in H.memo_path(pr).read_text().splitlines()[1])
        H.memo_reopen(pr, 1, "reopsess1", force=True)
        check("memo_reopen: the memo header carries the given session", "· session reopsess" in H.memo_path(pr).read_text().splitlines()[1])
        # 6. stale lock files are pruned at SessionStart
        ml_dir = Path(state, "memo-locks"); ml_dir.mkdir(exist_ok=True)
        lk_old, lk_new, lk_oldm = Path(state, "stale-sess.lock"), Path(state, "fresh-sess.lock"), ml_dir / "stale_proj.lock"
        for f in (lk_old, lk_new, lk_oldm):
            f.write_text("")
        for f in (lk_old, lk_oldm):
            os.utime(f, (time.time() - 8 * 86400,) * 2)
        run("start", {"session_id": "lk", "cwd": cwd}, led)
        check("start: lock files older than 7 days are deleted (state and memo-locks), fresh ones stay", not lk_old.exists() and not lk_oldm.exists() and lk_new.exists())
        # 7. refresh_guide reads the transcript once
        calls = []
        orig_cs = H.context_state
        H.context_state = lambda root_, st_: (calls.append(1), (None, None))[1]
        try:
            H.refresh_guide(pr, {}, "cnt_sess")
        finally:
            H.context_state = orig_cs
        check("refresh_guide: context_state computed once (no second transcript tail read)", len(calls) == 1)
        # 9. overrides are read only inside the overrides block
        po = tempfile.mkdtemp()
        Path(po, "CLAUDE.md").write_text("# Project\n- Pace = fast\n- Receipt = off\n\n### Abofles Overrides\n- Break = 7\n#### notes\n- Guide = off\n\n## Other\n- Language = Arabic\n")
        check("override_value: read inside the block (any heading level and case; a deeper heading continues it)", H.override_value(po, "Break") == "7" and H.override_value(po, "Guide") == "off")
        check("override_value: a `- Key = value` line outside the block sets nothing", H.override_value(po, "Pace") is None and H.override_value(po, "Receipt") is None and H.override_value(po, "Language") is None)
        # 9b. a note after a value, `*` / `+` bullets, examples in a code fence or an HTML comment, K / M marks, Review memory 0
        def o3p(body):
            d_ = tempfile.mkdtemp(); Path(d_, "CLAUDE.md").write_text("## abofles overrides\n" + body); return d_
        o3n_ = o3p("- Git guard = deny  # team rule\n- Review gate = on (strict)\n- Agents = 3 per task\n- Break = off # never\n- Guide = none\n* Pace = fast\n+ Budget raise = ask\n")
        check("overrides: a note after the value is ignored (`deny  # team rule` is deny, not the weaker default); `*` and `+` bullets read",
              H.git_guard_mode(o3n_) == "deny" and H.review_gate_mode(o3n_) == "on" and H.gate_setting(o3n_) == ("fixed", 3) and H.break_minutes(o3n_) == 0
              and not H.guide_on(o3n_) and H.pace(o3n_) == "fast" and H.budget_raise_mode(o3n_) == "ask")
        o3f_ = o3p("Example:\n```\n- Git guard = off\n```\n<!--\n- Agents = off\n-->\n<!-- - Receipt = off -->\n- Questions = off\n")
        check("overrides: a line in a code fence or an HTML comment sets nothing; the line after them does",
              H.git_guard_mode(o3f_) == "warn" and H.gate_setting(o3f_) == ("ask", None) and H.receipt_mode(o3f_) == ("full", 0) and H.questions_setting(o3f_) == "off")
        o3m_ = o3p("- Context marks = 500K, 1M, 0\n- Review memory = 0\n- Archive items = 0\n")
        check("overrides: Context marks reads K and M and drops 0; Review memory = 0 is 0 hours; Archive items = 0 is still the default",
              H.context_marks(o3m_) == (500, 1000) and H.number_override(o3m_, "Review memory", zero=True) == 0 and H.number_override(o3m_, "Archive items") == 7)
        o3i_ = o3p("- Git guard = strict\n- Agent budgets = 1, 2\n- Main session = big\n- Spawns place = output\n- Language = Arabic\n- Agents = 4\n")
        o3k_ = {k["key"]: k for k in H.pane_overrides(o3i_, "")["keys"]}
        check("pane overrides: a value the hooks cannot read is marked invalid (only those); a line under a key's old name is that key, not a raw line",
              sorted(k for k, v in o3k_.items() if v.get("invalid")) == ["Agent budgets", "Git guard", "Main session"] and H.pane_overrides(o3i_, "")["raw"] == [] and o3k_["Agents place"]["value"] == "output")
        o3c_ = o3p("- Pace = fast\n"); o3a_ = H.pace(o3c_); Path(o3c_, "CLAUDE.md").write_text("## abofles overrides\n- Pace = slow\n")
        check("override_blocks: read once per process, read again when the file changed", o3a_ == "fast" and H.pace(o3c_) == "normal")
        # 10. the gate text says what the question text holds
        qt = H.question_gate_text({})
        check("question gate text: question text = QUESTION, blank line, WHY (no option summary); no 'plain summary'", "plain summary" not in qt and "QUESTION, a blank line, WHY (no option summary)" in qt)
        # 11. next setting stops at the top unless T4
        check("next_setting: Work stops at opus·xhigh (max only on T4)", H.next_setting("opus", "xhigh", "Work", 3) == (None, None) and H.next_setting("opus", "xhigh", "Work", 4) == ("opus", "max") and H.next_setting("opus", "max", "Work", 4) == (None, None))
        check("next_setting: Analyse/Plan reach fable, max only after fable·high on T4",
              H.next_setting("opus", "xhigh", "Plan", 3) == ("fable", "medium") and H.next_setting("fable", "xhigh", "Plan", 3) == (None, None)
              and H.next_setting("fable", "high", "Analyse", 4) == ("fable", "xhigh") and H.next_setting("fable", "xhigh", "Plan", 4) == ("fable", "max"))
        check("next_setting: the lower ladder is unchanged", H.next_setting("haiku", "low", "Read", 2) == ("haiku", "medium") and H.next_setting("sonnet", "xhigh", "Work", 2) == ("opus", "medium"))
        for sx_, tx_ in (("nx", 3), ("nx4", 4)):
            run("start", {"session_id": sx_, "cwd": cwd}, led)
        time.sleep(1.1)  # escalation rows are those after the task start second
        for sx_, tx_, ag_ in (("nx", 3, "wkx111"), ("nx4", 4, "wkx444")):
            run("post", dict(agent("opus", "xhigh", "abofles:abofles-worker", prompt=f"abofles: Cap / Work · T{tx_}\nq\nLine cap: 20", resp={"agentId": ag_, "content": "STATUS: partial\nCONFIDENCE: low\nCHANGED:\n- a\nRAN: none\nLEFT: the Q2 body was not read"}, cwd=cwd), session_id=sx_), led)
        e3 = next((i for i in gitems("nx") if i["p"] == "🟡"), {})
        e4 = next((i for i in gitems("nx4") if i["p"] == "🟡"), {})
        check("guide: an escalate at the top setting (T3) says stop and report, no max setting", "stop and report" in e3.get("text", "") and "Max" not in e3.get("text", "") and "stop and report" in e3.get("fill", ""))
        check("guide: the same on T4 offers Opus 5.5 · Max", "Opus 5.5 · Max" in e4.get("text", ""))
        check("guide: an escalate with no higher setting still has a recheck (stop and report, then the traced resolve)", "stop and report" in (e3.get("recheck") or "") and "--resolve wkx111 --session" in e3["recheck"] and "re-checked: <evidence>" in e3["recheck"] and e3.get("resolve") is None)
        check("guide: escalate text names the agent and carries id esc:<agent>", e3.get("id") == "esc:wkx111" and "agent wkx111" in e3.get("text", "") and e4.get("id") == "esc:wkx444" and "agent wkx444" in e4.get("text", ""))
        # 12. guide item ids
        pi_ = tempfile.mkdtemp(); sI = "idsess1"; tri = Path(pi_, "t.jsonl")
        tri.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 460000, "output_tokens": 5}}}) + "\n")
        Path(pi_, "CLAUDE.md").write_text("## abofles overrides\n- Agents = 1\n")
        run("start", {"session_id": sI, "cwd": pi_, "transcript_path": str(tri)}, led)
        H.memo_needs(pi_, sI, "owner decision: pick A or B?")
        time.sleep(1.1)
        iv = lambda extra: dict(extra, session_id=sI, transcript_path=str(tri))
        run("post", iv(agent("sonnet", "high", "abofles:abofles-verifier", prompt="abofles: Ids Topic / Verify · T2\nq\nLine cap: 20", resp={"agentId": "vfx111", "content": "STATUS: done\nCONFIDENCE: high\nVERDICT: FAIL\nEVIDENCE:\n- c1 — FAIL — src/a.cs:1\nISSUES:\nNOTE: none"}, cwd=pi_)), led)
        time.sleep(1.1)
        run("post", iv(agent("haiku", "low", resp={"agentId": "esx111", "content": "STATUS: partial\nCONFIDENCE: low\nFACTS:\n- src/a.cs:1 — x\nGAPS: not read"}, cwd=pi_)), led)
        run("post", iv(jp(2, ask)), led)
        run("post", iv(agent("haiku", "low", resp={"status": "async_launched", "agentId": "bgx111", "prompt": "p", "description": "t"}, cwd=pi_)), led)
        run("prompt", {"session_id": sI, "cwd": pi_, "transcript_path": str(tri)}, led)
        iditems = {i["id"]: i for i in gitems(sI)}
        check("guide ids: fail:<agent>, ask, dec:<n>, bg:<agent>, ctx:<mark>, budget on their items", {"fail:vfx111", "ask", "dec:1", "bg:bgx111", "ctx:500", "budget"} <= set(iditems))
        check("guide ids: the owner-decision item has no resolve/reopen argv, its recheck carries `resolve 1 --session <sid>`",
              iditems.get("dec:1", {}).get("resolve") is None and iditems.get("dec:1", {}).get("unresolve") is None
              and f"resolve 1 --session {sI}" in (iditems.get("dec:1", {}).get("recheck") or "") and "AskUserQuestion" in iditems["dec:1"]["recheck"])
        # 13. the band never settles an item: every guide item has resolve/unresolve None; Claude's resolve is traced and undoable
        po_ = tempfile.mkdtemp(); run("start", {"session_id": "idsess2", "cwd": po_}, led); H.memo_needs(po_, "idsess2", "owner decision: elsewhere?")
        allst = dict(gstate(sI), review_missing={"names": ["qa"], "tier": 3, "topic": "Ids Topic", "ts": int(time.time())}, receipt_owed=True)
        led0_, H.LEDGER_DIR = H.LEDGER_DIR, Path(led)  # H reads the ledger the hook subprocesses wrote
        allit = H.guide_entries(pi_, allst, sI, scope="all")
        allids = {i["id"] for i in allit}
        check("guide: the fixture raises fail, esc, dec, review, receipt and dec:elsewhere items", {"fail:vfx111", "esc:esx111", "dec:1", "review", "receipt", "dec:elsewhere"} <= allids)
        check("guide: every item of the ladder has resolve and unresolve None", allit and all(i.get("resolve") is None and i.get("unresolve") is None and "resolve" in i and "unresolve" in i for i in allit))
        check("guide: the fail, esc and dec items carry a recheck, the others none", all(bool(i.get("recheck")) == (i["id"].split(":")[0] in ("fail", "esc") or i["id"] == "dec:1") for i in allit))
        sec_ = H.memo_read(pi_); sec_["Needs you"] = "- [ ] a free-text line with no number (s:idsess1)"; H.memo_write(pi_, sec_, sI)
        dn = next(i for i in H.guide_entries(pi_, gstate(sI), sI, scope="project") if i["id"] == "dec:?")
        H.LEDGER_DIR = led0_
        check("guide: an owner decision without a number has no recheck", dn.get("recheck") is None and dn.get("resolve") is None and dn.get("unresolve") is None)
        # Claude's resolve: the evidence in the Journal, Undo in the guide state until the turn after the next Stop
        pl_ = tempfile.mkdtemp(); sL = "lres1"
        run("start", {"session_id": sL, "cwd": pl_}, led)
        H.memo_needs(pl_, sL, "owner decision: pick A or B?")
        run("prompt", {"session_id": sL, "cwd": pl_, "prompt": "go"}, led)
        lenv = dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state)
        lmemo = lambda *a: _sp.run([sys.executable, "-X", "utf8", MEMOPY, *a, "--session", sL], cwd=pl_, capture_output=True, text=True, env=lenv)
        lres = lambda: (gstate(sL).get("guide") or {}).get("last_resolved")
        check("guide: no last_resolved before a resolve", lres() is None and "last_resolved" in (gstate(sL).get("guide") or {}))
        time.sleep(0.05)
        lr1 = lmemo("resolve", "1", "re-checked: X", "--trace")
        check("memo resolve --trace: the note lands in the Journal line as the evidence", lr1.returncode == 0 and any("resolved by hand: owner decision 1: " in l and l.endswith(" · re-checked: X") for l in H.sess_read(pl_, sL)["Journal"].splitlines()))
        lr = lres() or {}
        check("memo resolve: the guide state keeps last_resolved with the item text and the reopen argv",
              lr.get("text", "").startswith("owner decision 1: ") and "pick A or B" in lr["text"] and len(lr["text"]) <= 100 and isinstance(lr.get("ts"), float)
              and (lr.get("unresolve") or [])[:4] == ["bash", str(Path(MEMOPY).parent / "py").replace("\\", "/"), MEMOPY.replace("\\", "/"), "reopen"] and re.fullmatch(r"#[0-9a-fA-F]{4}", (lr.get("unresolve") or [""] * 5)[4]) and lr["unresolve"][5:] == ["--trace", "--session", sL] and all(not i["id"].startswith("dec:") for i in gitems(sL)))
        ur_ = _sp.run(lr.get("unresolve") or ["false"], cwd=pl_, capture_output=True, text=True, env=lenv)
        check("memo Undo argv runs through the launcher from the project root and reopens the item", ur_.returncode == 0 and ur_.stdout.startswith("reopened:") and "1. [ ] " in H.memo_path(pl_).read_text() and lres() is None)
        lmemo("resolve", "1", "re-checked: X", "--trace")  # back to the resolved state the checks below expect
        lr = lres() or {}
        run("receipt", {"session_id": sL, "cwd": pl_, "stop_hook_active": False, "last_assistant_message": "done"}, led)
        check("last_resolved survives the Stop of the same turn", (lres() or {}).get("unresolve") == lr.get("unresolve"))
        time.sleep(0.05)
        run("prompt", {"session_id": sL, "cwd": pl_, "prompt": "next"}, led)
        check("last_resolved is still there during the next turn", lres() is not None)
        run("receipt", {"session_id": sL, "cwd": pl_, "stop_hook_active": False, "last_assistant_message": "done"}, led)
        check("last_resolved is gone after the next turn's Stop", lres() is None and "last_resolved" not in gstate(sL))
        lmemo("reopen", "1")
        ne_ = lmemo("resolve", "1")
        check("memo resolve without a note exits 2 with the evidence message and leaves the item open", ne_.returncode == 2 and "abofles: resolve needs evidence — add a note, e.g. --trace \"re-checked: <evidence>\" or \"user answered: <answer>\"" in ne_.stdout
              and "1. [ ] " in H.memo_path(pl_).read_text())
        nw_ = lmemo("resolve", "1", "   ", "--trace")
        check("memo resolve with a whitespace note exits 2 too, item still open", nw_.returncode == 2 and "resolve needs evidence" in nw_.stdout and "1. [ ] " in H.memo_path(pl_).read_text())
        lmemo("resolve", "1", "again")
        check("memo resolve without --trace still records last_resolved", lres() is not None)
        lo = lmemo("reopen", "1", "--trace")
        check("memo reopen clears last_resolved", lo.returncode == 0 and lres() is None)
        sL2 = "lres2"; run("start", {"session_id": sL2, "cwd": pl_}, led)
        run("post", dict(agent("haiku", "low", "abofles:abofles-reader", resp={"agentId": "lrx111", "content": "STATUS: partial\nCONFIDENCE: medium\nFACTS:\n- src/a.cs:1 — x\nGAPS: not read"}, cwd=pl_), session_id=sL2), led)
        lst = lambda *a: _sp.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve().parent / "status.py"), *a, "--session", sL2], cwd=pl_, capture_output=True, text=True, env=lenv)
        ls1 = lst("--resolve", "lrx111", "--trace", "re-checked: sound")
        lr2 = (gstate(sL2).get("guide") or {}).get("last_resolved") or {}
        check("status --resolve: last_resolved holds the --unresolve argv", ls1.returncode == 0 and lr2.get("unresolve", [])[:1] == ["bash"] and lr2["unresolve"][1].endswith("/scripts/py") and lr2["unresolve"][2:7] == [str(Path(__file__).resolve().parent / "status.py").replace("\\", "/"), "--unresolve", "lrx111", "--session", sL2] and lr2.get("text"))
        us_ = _sp.run(lr2["unresolve"], cwd=pl_, capture_output=True, text=True, env=lenv)
        check("status Undo argv runs through the launcher and reopens the row", us_.returncode == 0 and "reopened 1 entry" in us_.stdout)
        ns_, nt_ = lst("--resolve", "lrx111"), lst("--resolve", "lrx111", "--trace", "  ")
        check("status --resolve without trace text exits 2 with the evidence message, the row stays open",
              ns_.returncode == 2 and nt_.returncode == 2 and ns_.stdout.startswith("abofles: resolve needs evidence") and "user answered: <answer>" in ns_.stdout
              and not any(json.loads(l).get("resolved") for l in (Path(led) / f"{sL2}.jsonl").read_text().splitlines() if l.strip()))
        lst("--resolve", "lrx111", "--trace", "re-checked: sound")
        ls2 = lst("--unresolve", "lrx111")
        check("status --unresolve clears last_resolved", ls2.returncode == 0 and "last_resolved" not in gstate(sL2) and (gstate(sL2).get("guide") or {}).get("last_resolved") is None)
        # ── Batch A: the graded reply text, header-aware replies, exact gate agents, stale unlocks, lost agents ──
        setst = lambda sid_, **kw: json.dump(dict(gstate(sid_), **kw), open(Path(state, f"{sid_}.json"), "w"))
        ledrows = lambda sid_: [json.loads(l) for l in Path(led, f"{sid_}.jsonl").read_text().splitlines() if l.strip()]
        askp = lambda sid_: dict(aq, session_id=sid_)  # AskUserQuestion payload of that session
        jpa = lambda sid_, resp, stype="abofles:abofles-judge", hdr="Judge", tier=2, **kw: dict(agent("sonnet", "medium", stype, prompt=f"abofles: BatchA / {hdr} · T{tier}\nq\nLine cap: 40", resp=resp, cwd=cwd), session_id=sid_, transcript_path=str(tr), **kw)
        launch_ = lambda aid_: {"status": "async_launched", "agentId": aid_, "prompt": "p", "description": "t"}
        for sx_ in ("ba1", "ba2", "ba3", "ba4", "ba5", "ba6", "ba7", "ba8", "ba9", "ba10", "ba11", "ba12"):
            run("start", {"session_id": sx_, "cwd": cwd, "transcript_path": str(tr)}, led)
            setst(sx_, gate="auto")
        time.sleep(1.1)  # rows of the task are those after the task start second
        # 1. harness notes are not the agent's reply
        hnote = "[harness: subagent output matched instruction-shaped pattern(s) " + "word " * 80 + "]"
        blk = lambda t: {"type": "text", "text": t}
        hresp = {"agentId": "hn1", "agentType": "abofles:abofles-reader", "content": [blk(hnote), blk(good)], "harnessNoteCount": 1, "harnessTailCount": 0, "status": "completed", "usage": {}, "totalTokens": 9}
        out = run("post", dict(agent("haiku", "low", resp=hresp, cwd=cwd), session_id="ba1"), led)
        check("reply: a harness note block is dropped from the graded reply — no too-long warning", "too long" not in ctx(out) and warn(out) == "")
        check("reply_text: head and tail counts drop blocks, a `[harness:` block goes without a count, str/list inputs still work",
              H.reply_text(dict(hresp, harnessNoteCount=1)) == good and H.reply_text(dict(hresp, harnessNoteCount=0, content=[blk(good), blk(hnote)])) == good
              and H.reply_text(dict(hresp, harnessNoteCount=0, harnessTailCount=1, content=[blk(good), blk("trailer")])) == good
              and H.reply_text({"content": [blk(hnote), blk("x")]}) == "x" and H.reply_text("plain") == "plain" and H.reply_text([blk("a"), blk("b")]) == "a\nb" and H.reply_text(None) == "")
        check("has_header: STATUS / VERDICT / EXPLAIN lines, leading spaces allowed; a stub has none",
              H.has_header("x\n  STATUS: done") and H.has_header("VERDICT: ASK") and H.has_header("a\nEXPLAIN:\nb") and not H.has_header("Understood. STATUS: done soon") and not H.has_header(""))
        # 2. get_reply: header hint wins, else the agent's own transcript, else the stub, else nothing
        REAL = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: none"
        gdir = Path(cwd, "ba2", "subagents"); gdir.mkdir(parents=True, exist_ok=True)
        gline = lambda t: json.dumps({"type": "assistant", "message": {"id": f"g{time.time()}", "content": [{"type": "text", "text": t}]}}) + "\n"
        gtr = Path(gdir, "agent-gx1.jsonl"); gtr.write_text(gline("working") + gline(REAL) + gline("Understood."))
        gst_ = {"transcript_path": str(Path(cwd, "main.jsonl"))}
        check("get_reply: a hint with a header wins over the transcript", H.get_reply(gst_, "ba2", "gx1", "STATUS: partial\nCONFIDENCE: low") == ("STATUS: partial\nCONFIDENCE: low", "hint"))
        check("get_reply: a stub hint falls back to the newest transcript text that has a header", H.get_reply(gst_, "ba2", "gx1", "Understood.") == (REAL, "transcript"))
        check("get_reply: no transcript -> the stub as it is; nothing at all -> no_reply", H.get_reply(gst_, "ba2", "nope", "Understood.") == ("Understood.", "stub") and H.get_reply(gst_, "ba2", "nope", "") == ("", "no_reply") and H.get_reply(gst_, "ba2", None, "  ") == ("", "no_reply"))
        check("last_assistant_reply: the real report wins over a short stub written after it", H.last_assistant_reply(gtr) == (REAL, False))
        gtr2 = Path(gdir, "agent-gx2.jsonl"); gtr2.write_text(gline(REAL) + gline("one") + gline("two") + gline("three"))
        check("get_reply: only the newest 2 assistant messages are searched — an older report is not taken (stub as it is)", H.get_reply(gst_, "ba2", "gx2", "Understood.") == ("Understood.", "stub"))
        gtr3 = Path(gdir, "agent-gx3.jsonl"); gtr3.write_text(gline(REAL) + json.dumps({"type": "assistant", "message": {"id": "hb", "content": [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": "Understood."}}]}}) + "\n")
        check("last_assistant_reply: the headed report is returned with the NEWEST message's hand-back flag", H.last_assistant_reply(gtr3) == (REAL, True))
        run("post", dict(agent("haiku", "low", resp=launch_("gx1"), cwd=cwd), session_id="ba2"), led)
        run("stop", {"session_id": "ba2", "agent_id": "gx1", "agent_transcript_path": str(gtr), "last_assistant_message": "Understood."}, led)
        grow = next(r for r in ledrows("ba2") if r.get("agent_id") == "gx1")
        check("stop: a stub last message is graded from the transcript's real report", grow.get("status") == "done" and grow.get("confidence") == "high" and grow.get("how") == "transcript")
        run("post", dict(agent("haiku", "low", resp=launch_("nr1"), cwd=cwd), session_id="ba3"), led)
        run("stop", {"session_id": "ba3", "agent_id": "nr1", "last_assistant_message": ""}, led)
        run("prompt", {"session_id": "ba3", "cwd": cwd, "transcript_path": str(tr)}, led)
        nrow = next(r for r in ledrows("ba3") if r.get("agent_id") == "nr1")
        check("stop: no reply text -> row closed how=no_reply, and the guide raises no escalate item for it", nrow.get("how") == "no_reply" and nrow.get("completed_ts") and gitems("ba3") and not any(i["p"] == "🟡" for i in gitems("ba3")))
        # SubagentStop before the hand-back reaches the transcript: the row stays pending, the next sweep grades the hand-back
        gtr4 = Path(gdir, "agent-hb1.jsonl"); gtr4.write_text(gline("thinking out loud"))
        run("post", dict(agent("haiku", "low", resp=launch_("hb1"), cwd=cwd), session_id="ba3"), led)
        run("stop", {"session_id": "ba3", "agent_id": "hb1", "agent_transcript_path": str(gtr4), "last_assistant_message": ""}, led)
        hrow = next(r for r in ledrows("ba3") if r.get("agent_id") == "hb1")
        check("stop: no reply yet but the agent transcript exists -> row stays pending", hrow.get("status") == "pending")
        with open(gtr4, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"id": "hb", "content": [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": REAL}}]}}) + "\n")
        run("prompt", {"session_id": "ba3", "cwd": cwd, "transcript_path": str(tr)}, led)
        hrow = next(r for r in ledrows("ba3") if r.get("agent_id") == "hb1")
        check("sweep: a hand-back that landed after SubagentStop closes the row from it", hrow.get("status") == "done" and hrow.get("confidence") == "high")
        # 3. every judge block counts; a stub or an empty EXPLAIN never unlocks
        two = "STATUS: done\nCONFIDENCE: high\nVERDICT: ANSWERED\nQUESTION: Is the cap per unit?\nANSWER: Yes\nEVIDENCE:\n- src/a.cs:12 — loop\nVERDICT: ASK\nQUESTION: Per unit or per order?\nEVIDENCE:\n- src/a.cs:10 — cap read here\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r"
        run("post", jpa("ba4", two), led)
        check("gate reply: ANSWERED then ASK in one sync reply unlocks one question", gstate("ba4").get("judge_ok") == 1 and gstate("ba4").get("judge_ok_ts"))
        run("post", jpa("ba5", launch_("bj5")), led)
        run("stop", {"session_id": "ba5", "agent_id": "bj5", "last_assistant_message": two}, led)
        check("gate reply: ANSWERED then ASK from a background judge unlocks too (the ASK is not lost)", gstate("ba5").get("judge_ok") == 1 and next(r for r in ledrows("ba5") if r.get("agent_id") == "bj5").get("verdict") == "ASK")
        out = run("post", jpa("ba6", "Understood, I will check the cap."), led)
        check("gate reply: a judge stub with no VERDICT stays locked and warns", gstate("ba6").get("judge_ok", 0) == 0 and "no VERDICT" in ctx(out) and "question gate" in reason(run("qgate", askp("ba6"), led)))
        run("post", jpa("ba6", launch_("bj6")), led)
        run("stop", {"session_id": "ba6", "agent_id": "bj6", "last_assistant_message": "Understood."}, led)
        check("gate reply: the same stub from a background judge stays locked", gstate("ba6").get("judge_ok", 0) == 0 and any("no VERDICT" in w for w in next(r for r in ledrows("ba6") if r.get("agent_id") == "bj6").get("warnings") or []))
        exreply = lambda ex_: "STATUS: done\nCONFIDENCE: high\nQUESTION: q\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r\nEXPLAIN:" + ex_
        out = run("post", jpa("ba7", exreply("\n• A: m → t"), "abofles:abofles-explainer", "Explain", 3), led)
        check("gate reply: an explainer with an EXPLAIN unlocks", gstate("ba7").get("judge_ok") == 1)
        out = run("post", jpa("ba7", exreply(""), "abofles:abofles-explainer", "Explain", 3), led)
        check("gate reply: an explainer with a QUESTION but no EXPLAIN unlocks (EXPLAIN is optional)", gstate("ba7").get("judge_ok") == 2 and "stays locked" not in ctx(out))
        out = run("post", jpa("ba7", "Understood, I will explain it.", "abofles:abofles-explainer", "Explain", 3), led)
        check("gate reply: an explainer stub with no QUESTION line stays locked and warns", gstate("ba7").get("judge_ok") == 2 and "no QUESTION line" in ctx(out))
        # numbered / tagged explainer questions; a HANDOFF judge then such an explainer; a background stub is surfaced later
        for sx_ in ("bq1", "bq2", "bq3", "bq4"):
            run("start", {"session_id": sx_, "cwd": cwd, "transcript_path": str(tr)}, led)
            setst(sx_, gate="auto")
        numq = ("STATUS: done\nCONFIDENCE: high\nQUESTION 1 [IDMS file]: How should the reader read the file?\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r\n"
                "QUESTION 2: Who reviews it?\nWHY: w2\nOPTIONS:\n- B — means: m · then: t · pick when: p\nRECOMMEND: B — r")
        out = run("post", jpa("bq1", numq, "abofles:abofles-explainer", "Explain", 4), led)
        check("gate reply: an explainer with `QUESTION 1 [tag]:` and `QUESTION 2:` unlocks one question each, no stub warning", gstate("bq1").get("judge_ok") == 2 and "no QUESTION line" not in ctx(out))
        check("judge_fields / explainer_blocks: numbered, tagged and bold QUESTION lines parse; QUESTIONS: does not",
              H.judge_fields("QUESTION 3: x?")[1] == "x?" and H.judge_fields("**QUESTION:** y?")[1] == "y?" and H.judge_fields("QUESTIONS: no")[1] == ""
              and [b[1] for b in H.explainer_blocks(numq)] == ["How should the reader read the file?", "Who reviews it?"] and len(H.explainer_blocks("Understood.")) == 1)
        hand_ = "STATUS: done\nCONFIDENCE: medium\nVERDICT: HANDOFF\nQUESTION: How should the reader read the IDMS file?\nEVIDENCE:\n- a.cs:1 — x\nWHY: money"
        out = run("post", jpa("bq2", hand_, tier=4), led)
        check("gate: a HANDOFF judge unlocks nothing", gstate("bq2").get("judge_ok", 0) == 0 and "start `abofles:abofles-explainer`" in ctx(out))
        run("post", jpa("bq2", numq.split("\nQUESTION 2")[0], "abofles:abofles-explainer", "Explain", 4), led)
        check("gate: ... then an explainer with a tagged `QUESTION 1 [..]:` line for it -> the ask goes through", run("qgate", askp("bq2"), led) is None)
        run("post", jpa("bq3", launch_("bx3"), "abofles:abofles-explainer", "Explain", 4), led)
        run("stop", {"session_id": "bq3", "agent_id": "bx3", "last_assistant_message": "Understood, I will explain it."}, led)
        check("background stub: SubagentStop prints nothing and keeps the warning for the main session", "no QUESTION line" in (gstate("bq3").get("gate_note") or "") and gstate("bq3").get("judge_ok", 0) == 0)
        o_ = run("qgate", askp("bq3"), led)
        check("background stub: the refused question says why, once", reason(o_).startswith("abofles: background Explain bx3: explainer reply has no QUESTION line") and "question gate" in reason(o_)
              and not gstate("bq3").get("gate_note") and "no QUESTION line" not in reason(run("qgate", askp("bq3"), led)))
        run("post", jpa("bq4", launch_("bx4"), "abofles:abofles-explainer", "Explain", 4), led)
        run("stop", {"session_id": "bq4", "agent_id": "bx4", "last_assistant_message": "Understood."}, led)
        o_ = run("prompt", {"session_id": "bq4", "cwd": cwd, "transcript_path": str(tr), "prompt": "<task-notification>bx4 done</task-notification>"}, led)
        check("background stub: ... or the next prompt (the task notification) carries it as context", "explainer reply has no QUESTION line" in ctx(o_) and not gstate("bq4").get("gate_note"))
        # 4. which agents are gate agents
        check("is_gate_agent: exact abofles types (full or bare name) yes; a project agent named decision-judge no",
              H.is_gate_agent("abofles:abofles-judge", "") == "judge" and H.is_gate_agent("abofles-explainer", "") == "explain" and H.is_gate_agent("abofles:abofles-explainer", "x") == "explain"
              and H.is_gate_agent("decision-judge", "abofles: X / Read · T2\nq") is None and H.is_gate_agent("my-explainer", "") is None and H.is_gate_agent("abofles:abofles-worker", "abofles: X / Work · T2") is None)
        check("is_gate_agent: any other type counts only through the prompt header side",
              H.is_gate_agent("general-purpose", "abofles: X / Judge · T2\nq") == "judge" and H.is_gate_agent("general-purpose", "abofles: X / Explain · T3\nq") == "explain" and H.is_gate_agent("general-purpose", "do it") is None)
        check("side_of: no substring match of judge/explain on a non-abofles type; abofles types and the header side still resolve",
              H.side_of("decision-judge") == ("", "?") and H.side_of("abofles:abofles-judge") == ("judge", "Judge") and H.side_of("abofles-worker") == ("worker", "Work")
              and H.side_of("decision-judge", "abofles: X / Verify · T2\nq") == ("verifier", "Verify") and H.side_of("my-reader", "") == ("", "?"))
        out = run("post", jpa("ba8", ask, "decision-judge", "Read"), led)
        check("gate agent: decision-judge replying ASK does not unlock the gate", gstate("ba8").get("judge_ok", 0) == 0 and "question judged" not in ctx(out) and not gstate("ba8").get("gate_agents"))
        _s9 = Path(state, "ba9.json"); _d9 = json.loads(_s9.read_text()) if _s9.exists() else {}; _d9["denied_ts"] = int(time.time()); _s9.write_text(json.dumps(_d9))  # qgate refused a question: a Judge is wanted
        out = run("post", jpa("ba9", ask, "general-purpose", "Judge"), led)
        check("gate agent: general-purpose with a `/ Judge` header while a question waits is a gate agent and unlocks", gstate("ba9").get("judge_ok") == 1 and "question judged" in ctx(out) and next(r for r in ledrows("ba9")).get("gate") == "judge")
        out = run("post", jpa("ba9", longq, "general-purpose", "Judge"), led)
        check("analyze: a general-purpose `/ Judge` agent is graded as a judge — no long-line nag", "too long" not in ctx(out) and "absolute paths" not in ctx(out))
        # 5. SendMessage unlocks only for a gate agent this session spawned
        sm = lambda sid_, to_, rid_=None: run("sendmsg", {"session_id": sid_, "cwd": cwd, "transcript_path": str(tr), "tool_name": "SendMessage", "tool_input": {"to": to_, "message": "m"}, "tool_response": {"success": True, "resumedAgentId": rid_ or to_}}, led)
        check("sendmsg: an unknown id, and a name that merely contains judge, unlock nothing", sm("ba8", "decision-judge") is None and sm("ba8", "judge-7") is None and sm("ba8", "a1b2c3") is None and gstate("ba8").get("judge_ok", 0) == 0)
        run("post", jpa("ba10", launch_("gj10")), led)
        check("sendmsg: a message to a gate agent still running its first reply adds no unlock (that reply unlocks when it lands)", sm("ba10", "gj10") is None and gstate("ba10").get("judge_ok", 0) == 0)
        run("stop", {"session_id": "ba10", "agent_id": "gj10", "last_assistant_message": ask}, led)
        check("sendmsg: the id of a gate agent this session started still unlocks once its reply is in (one per reply)", gstate("ba10").get("judge_ok") == 1 and "unlocked" in ctx(sm("ba10", "gj10")) and gstate("ba10").get("judge_ok") == 2 and gstate("ba10").get("judge_ok_ts"))
        for sx_ in ("bc1", "bc2", "bc3"):
            run("start", {"session_id": sx_, "cwd": cwd, "transcript_path": str(tr)}, led)
            setst(sx_, gate="auto")
        pn_ = jpa("bc3", {"agentId": "gj3", "content": ask})
        run("post", dict(pn_, tool_input=dict(pn_["tool_input"], name="jq3")), led)
        check("gate_agents: the agent id and the agent name are recorded, not the type name", gstate("bc3").get("gate_agents") == ["gj3", "jq3"])
        check("sendmsg: the type name unlocks nothing; the agent name (reply id given) does, and a second message before that reply adds none", sm("bc3", "abofles-judge") is None and gstate("bc3").get("judge_ok") == 1
              and "unlocked" in ctx(sm("bc3", "jq3", "gj3")) and sm("bc3", "gj3") is None and gstate("bc3").get("judge_ok") == 2)
        # 6. stale unlocks
        now_ = int(time.time())
        ids_ = lambda sid_: [i.get("id") for i in gitems(sid_)]
        setst("ba11", task_started=now_ - 5000, judge_ok=1, judge_ok_ts=now_ - 6000, gate_wait_ts=0)
        run("prompt", {"session_id": "ba11", "cwd": cwd, "transcript_path": str(tr)}, led)
        out = run("receipt", {"session_id": "ba11", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "done"}, led)
        check("unlock: one made before the open task started is dropped — no 'unasked' item, no Stop demand, judge_ok cleared",
              ids_("ba11") and "ask" not in ids_("ba11") and "judged question" not in (out or {}).get("reason", "") and gstate("ba11").get("judge_ok") == 0)
        setst("ba12", task_started=now_ - 5000, judge_ok=1, judge_ok_ts=now_ - 60, gate_wait_ts=0)
        run("prompt", {"session_id": "ba12", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("unlock: one inside GATE_WAIT and after the task start survives — the guide asks for the question", "ask" in ids_("ba12"))
        out = run("receipt", {"session_id": "ba12", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "done"}, led)
        check("unlock: ... and Stop demands it", "judged question is still unasked" in (out or {}).get("reason", "") and gstate("ba12").get("judge_ok") == 1)
        setst("ba12", judge_ok=1, judge_ok_ts=now_ - H.GATE_WAIT - 100, gate_wait_ts=0)
        run("prompt", {"session_id": "ba12", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("unlock: a judge reply (post/close_row) left unasked longer than GATE_WAIT stays live — the guide still asks", "ask" in ids_("ba12"))
        out = run("receipt", {"session_id": "ba12", "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": "done"}, led)
        check("unlock: ... and Stop still demands the question", "judged question is still unasked" in (out or {}).get("reason", ""))
        setst("ba12", judge_ok=1, judge_ok_ts=now_ - H.GATE_WAIT - 100, gate_wait_ts=now_ - H.GATE_WAIT - 100)  # a SendMessage unlock whose reply never came
        run("prompt", {"session_id": "ba12", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("unlock: a SendMessage unlock older than GATE_WAIT with no gate agent running is not live", "ask" not in ids_("ba12"))
        check("qgate: ... and it lets no question through", "question gate" in reason(run("qgate", askp("ba12"), led)))
        setst("ba12", judge_ok=1, judge_ok_ts=now_ - 6000, task_started=now_ - 5000, gate_wait_ts=0)
        check("qgate: a stale unlock (made before the task began) lets no question through", "question gate" in reason(run("qgate", askp("ba12"), led)) and gstate("ba12").get("judge_ok") == 1)
        setst("ba12", judge_ok=1, judge_ok_ts=now_ - 60, task_started=now_ - 5000, gate_wait_ts=0)
        check("qgate: a live unlock lets exactly one question through", run("qgate", askp("ba12"), led) is None and gstate("ba12").get("judge_ok") == 0)
        setst("ba12", judge_ok=2, judge_ok_ts=now_ - 60, task_started=now_ - 5000, gate_wait_ts=0)  # two judged questions (two ASKs) merged into one box
        two = dict(aq, session_id="ba12", tool_input={"questions": [dict(q, question=f"{q.get('question', '')} ({n})") for n, q in enumerate((aq.get("tool_input") or {}).get("questions") or [{}], 1)] * 2})
        check("qgate: one ask carrying two judged questions spends both unlocks — no stale 'unasked' demand after it",
              len(two["tool_input"]["questions"]) == 2 and run("qgate", two, led) is None and gstate("ba12").get("judge_ok") == 0)
        # 7. background spawns: run_in_background is async; a lost agent closes; an old task's pending row is not "running"
        for sx_ in ("bb1", "bb2"):
            run("start", {"session_id": sx_, "cwd": cwd, "transcript_path": str(tr)}, led)
        out = run("post", dict(agent("haiku", "low", resp={"agentId": "rb2", "content": [blk(good)]}, cwd=cwd), session_id="bb1", tool_input=dict(agent("haiku", "low")["tool_input"], run_in_background=True)), led)
        check("post: run_in_background=True is async when the result is not marked completed", next(r for r in ledrows("bb1") if r.get("agent_id") == "rb2").get("status") == "pending")
        run("post", dict(agent("haiku", "low", resp={"status": "async_launched"}, cwd=cwd), session_id="bb2"), led)  # no agent id
        run("post", dict(agent("haiku", "low", resp=launch_("ghost1"), cwd=cwd), session_id="bb2"), led)  # an id with no transcript
        run("post", dict(agent("haiku", "low", resp=launch_("fresh1"), cwd=cwd), session_id="bb2"), led)
        rws = ledrows("bb2")
        rws[0]["ts"] = now_ - H.LOST_MIN * 60 - 60  # past LOST_MIN
        rws[1]["ts"] = now_ - H.LOST_MIN * 60 - 60
        rws[2]["ts"] = now_ - H.LOST_MIN * 60 + 120  # still inside it
        Path(led, "bb2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rws))
        setst("bb2", task_started=now_ - H.LOST_MIN * 60 - 600)  # a long task: all three rows belong to it
        run("prompt", {"session_id": "bb2", "cwd": cwd, "transcript_path": str(tr)}, led)
        rws = ledrows("bb2")
        check("stale: a pending row with no agent id / no transcript older than LOST_MIN closes as lost, no escalate item; a younger one stays pending",
              [r.get("how") for r in rws[:2]] == ["lost", "lost"] and all(r.get("completed_ts") for r in rws[:2]) and rws[2].get("status") == "pending" and not any(i["p"] == "🟡" for i in gitems("bb2")))
        check("guide: only a pending row at or after the task start counts as running", [i["text"][:2] for i in gitems("bb2") if i["p"] == "⏳"] == ["1 "] and "bg:fresh1" in ids_("bb2"))
        rws[2]["ts"] = int(time.time()) - 5000  # now it belongs to an earlier task
        setst("bb2", task_started=int(time.time()) - 100)
        Path(led, "bb2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rws))
        run("prompt", {"session_id": "bb2", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("guide: a pending row from an earlier task is not counted as running", gitems("bb2") and not any(i["p"] == "⏳" for i in gitems("bb2")))
        run("post", dict(agent("haiku", "low", resp=launch_("late1"), cwd=cwd), session_id="bc2"), led)
        rl_ = ledrows("bc2"); rl_[0]["ts"] = now_ - H.LOST_MIN * 60 - 60
        Path(led, "bc2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rl_))
        run("prompt", {"session_id": "bc2", "cwd": cwd, "transcript_path": str(tr)}, led)
        check("stop: setup — the row with no transcript was closed as lost", ledrows("bc2")[0].get("how") == "lost")
        run("stop", {"session_id": "bc2", "agent_id": "late1", "last_assistant_message": good}, led)
        lr_ = ledrows("bc2")[0]
        check("stop: a lost row that gets its real SubagentStop reply is re-graded from it, not dropped", lr_.get("how") == "hint" and lr_.get("status") == "done" and lr_.get("confidence") == "high")

        # a stale-pending fallback must not close an agent that is still thinking; a stale close is re-graded by the real SubagentStop
        def sk_msg(kind, text="", sr="absent", mid="m", tok=0):
            usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": tok, "output_tokens": 0}
            if kind == "tool_result": return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
            if kind == "attachment": return {"type": "attachment", "attachment": {"type": "total_tokens_reminder", "text": "<total_tokens>1</total_tokens>"}}
            parts = {"tool_use": [{"type": "text", "text": text}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}] if text else [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
                     "thinking": [{"type": "thinking", "thinking": ""}], "text": [{"type": "text", "text": text}]}[kind]
            m = {"id": mid, "model": "claude-haiku-5-5", "usage": usage, "content": parts}
            if sr != "absent": m["stop_reason"] = sr
            return {"type": "assistant", "message": m}
        def sk_agent(sid_, aid, entries, age, **post_kw):
            run("start", {"session_id": sid_, "cwd": cwd, "transcript_path": str(tr)}, led)
            run("post", dict(post_kw.pop("pi_", pi), session_id=sid_, transcript_path=str(tr), tool_response=launch_(aid)), led)
            f = Path(cwd, sid_, "subagents", f"agent-{aid}.jsonl"); f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("".join(json.dumps(e) + "\n" for e in entries))
            os.utime(f, (time.time() - age, time.time() - age))
            return f
        sk_stop = lambda sid_, text="all done": run("receipt", {"session_id": sid_, "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": False, "last_assistant_message": text}, led)
        sk_row = lambda sid_, aid: next(r for r in ledrows(sid_) if r.get("agent_id") == aid)
        out_ = None
        sk_agent("sk", "k1", [sk_msg("tool_use", "reading", "tool_use", "a"), sk_msg("tool_result"), sk_msg("attachment")], 200)
        out_ = sk_stop("sk")
        check("stale: an idle transcript ending in tool_use / tool_result / attachment is not closed (the agent is mid-work), no 'no receipt' nudge", sk_row("sk", "k1").get("status") == "pending" and "no receipt" not in json.dumps(out_ or {}))
        sk_agent("sk2", "k2", [sk_msg("text", "partial", "tool_use", "a"), sk_msg("thinking", sr=None, mid="b")], 200)
        sk_stop("sk2")
        check("stale: an idle transcript whose last entry is thinking-only (stop_reason None) stays pending", sk_row("sk2", "k2").get("status") == "pending")
        tfm_ = Path(cwd, "tf-malformed.jsonl"); tfm_.write_text('{"type":"assistant","message":"x"}\n')
        check("stale: a transcript entry whose message is not an object is not a finished turn and does not raise", H.turn_finished(tfm_) is False)
        sk_agent("sk2b", "k2b", [sk_msg("thinking", sr="end_turn", mid="a")], 200)
        sk_stop("sk2b")
        check("stale: an idle transcript whose last entry is thinking-only with stop_reason end_turn is not finished, stays pending", sk_row("sk2b", "k2b").get("status") == "pending")
        sk_agent("sk3", "k3", [sk_msg("text", good, "end_turn", "a", 50_000)], 200)
        sk_stop("sk3")
        r3_ = sk_row("sk3", "k3")
        check("stale: an idle transcript ending in an end_turn report closes the row, closed_by=stale", r3_.get("status") == "done" and r3_.get("closed_by") == "stale")
        sk_agent("sk4", "k4", [sk_msg("text", "partial notes", "tool_use", "a"), sk_msg("tool_result")], H.LOST_MIN * 60 + 10)
        sk_stop("sk4")
        r4_ = sk_row("sk4", "k4")
        check("stale: an unfinished transcript silent past LOST_MIN is closed with an 'unfinished' note, closed_by=stale", r4_.get("status") != "pending" and r4_.get("closed_by") == "stale" and any("unfinished" in w for w in r4_.get("warnings") or []))
        check("stale: the unfinished-timeout close without a graded status is blocked / low", r4_.get("status") == "blocked" and r4_.get("confidence") == "low")
        f5_ = sk_agent("sk5", "k5", [sk_msg("text", "Understood.", "end_turn", "a", 50_000)], 200)
        sk_stop("sk5")
        r5_ = sk_row("sk5", "k5"); t5_ = gstate("sk5").get("task_tokens", 0)
        with open(f5_, "a") as fh_: fh_.write(json.dumps(sk_msg("text", good, "end_turn", "b", 30_000)) + "\n")  # cumulative usage: 80k in total
        run("stop", {"session_id": "sk5", "agent_id": "k5", "last_assistant_message": good}, led)
        r5b_ = sk_row("sk5", "k5")
        check("stop: setup — a headerless stub was closed by the stale fallback with its tokens counted", r5_.get("closed_by") == "stale" and r5_.get("tokens") == 50_000 and t5_ == 50_000)
        check("stop: the real report re-grades a stale-closed row (done/high, closed_by gone) and the task counts the token delta only", r5b_.get("status") == "done" and r5b_.get("confidence") == "high" and "closed_by" not in r5b_ and r5b_.get("tokens") == 80_000 and gstate("sk5").get("task_tokens") == 80_000)
        jpi_ = agent("sonnet", "medium", "abofles:abofles-judge", prompt="abofles: X / Judge · T2\nq\nLine cap: 40", cwd=cwd)
        sk_agent("sk6", "k6", [sk_msg("text", "Understood.", "end_turn", "a", 1)], 200, pi_=jpi_)
        setst("sk6", gate="auto")
        sk_stop("sk6")
        ok6_ = gstate("sk6").get("judge_ok", 0)
        ask_ = "STATUS: done\nCONFIDENCE: high\nVERDICT: ASK\nQUESTION: q\nEVIDENCE:\n- a.cs:1 — x\nWHY: w\nOPTIONS:\n- A — means: m · then: t · pick when: p\nRECOMMEND: A — r"
        run("stop", {"session_id": "sk6", "agent_id": "k6", "last_assistant_message": ask_}, led)
        ok6b_ = gstate("sk6").get("judge_ok", 0)
        run("stop", {"session_id": "sk6", "agent_id": "k6", "last_assistant_message": ask_}, led)
        check("stop: a stale-closed judge stub, then the real VERDICT: ASK, unlocks once; a repeat stop for the agent adds no second unlock", sk_row("sk6", "k6").get("closed_by") is None and ok6b_ == ok6_ + 1 and gstate("sk6").get("judge_ok", 0) == ok6b_)

        # a SendMessage that resumes a finished agent re-opens its ledger row: it runs in the background again (no "no receipt" nudge while it works)
        rs_sync = lambda sid_, aid, name=None, stype="abofles:abofles-reader", text=good: run("post", dict(agent("haiku", "low", stype, prompt="abofles: Resume / R · T2\nq\nLine cap: 40" if "judge" not in stype else "abofles: Resume / Judge · T2\nq\nLine cap: 40", resp={"agentId": aid, "status": "completed", "content": [blk(text)], "usage": {}, "totalTokens": 9}, cwd=cwd), session_id=sid_, transcript_path=str(tr), tool_input=dict(agent("haiku", "low", stype, cwd=cwd)["tool_input"], **({"name": name} if name else {}))), led)
        rs_msg = lambda sid_, to, resp="absent": run("sendmsg", dict({"session_id": sid_, "cwd": cwd, "tool_name": "SendMessage", "tool_input": {"to": to, "message": "more"}}, **({} if resp == "absent" else {"tool_response": resp})), led)
        rs_ok = lambda aid: {"success": True, "message": "Resuming agent " + aid[:7], "resumedAgentId": aid, "pin": {"id": aid, "name": aid}}
        rs_row = lambda sid_, aid: next(r for r in ledrows(sid_) if r.get("agent_id") == aid)
        run("start", {"session_id": "rs0", "cwd": cwd, "transcript_path": str(tr)}, led)
        rs_sync("rs0", "q0")
        check("resume: control — a finished sync agent with no resume gets the 'no receipt' nudge", "no receipt" in json.dumps(sk_stop("rs0") or {}))
        run("start", {"session_id": "rs", "cwd": cwd, "transcript_path": str(tr)}, led)
        rs_sync("rs", "q1")
        sp_ = {k: gstate("rs").get(k) for k in ("spawns", "spawns_since_receipt", "task_units", "task_base", "task_judges")}
        tk_ = {k: gstate("rs").get(k) for k in ("task_tokens", "task_usd")}
        rs_msg("rs", "q1", rs_ok("q1"))
        q1_ = rs_row("rs", "q1")
        check("resume: a SendMessage whose response names resumedAgentId re-opens the agent's done row in place (pending, resumed=1, reopened_ts, no completed_ts)", q1_.get("status") == "pending" and q1_.get("resumed") == 1 and q1_.get("reopened_ts") and "completed_ts" not in q1_ and len(ledrows("rs")) == 1)
        check("resume: the reopened row drops the old confidence", "confidence" not in q1_)
        check("resume: not a new agent — agents, spawns_since_receipt, task_units, task_base and the token/usd totals unchanged; running_bg = 1", {k: gstate("rs").get(k) for k in sp_} == sp_ and {k: gstate("rs").get(k) for k in tk_} == tk_ and gstate("rs").get("running_bg") == 1)
        check("resume: the receipt gives no 'no receipt' nudge while the resumed agent runs", "no receipt" not in json.dumps(sk_stop("rs") or {}) and rs_row("rs", "q1").get("status") == "pending")
        run("stop", {"session_id": "rs", "agent_id": "q1", "last_assistant_message": good}, led)
        q1b_ = rs_row("rs", "q1")
        check("resume: the resumed agent's SubagentStop closes the row again, resumed stays 1, one row, same agent count", q1b_.get("status") == "done" and q1b_.get("resumed") == 1 and q1b_.get("completed_ts") and len(ledrows("rs")) == 1 and gstate("rs").get("spawns") == sp_["spawns"] and all(r.get("status") != "pending" for r in ledrows("rs")))
        rs_sync("rs", "q2", name="helper-2")
        rs_msg("rs", "helper-2")  # `to` is the spawn name, the response has no shape to read: falls back to `to`
        q2_ = rs_row("rs", "q2")
        check("resume: a SendMessage `to` given as the agent name (no tool_response) re-opens the row; the name is kept in the ledger", q2_.get("name") == "helper-2" and q2_.get("status") == "pending" and q2_.get("resumed") == 1)
        before_ = ledrows("rs")
        rs_msg("rs", "nobody-9", rs_ok("nobody-9")); rs_msg("rs", "q1", {"success": False, "error": "no such agent"})
        check("resume: a message to an unknown id, or one the harness reports as failed, leaves the ledger unchanged", ledrows("rs") == before_)
        # stale fallback: the reopened row is fresh for STALE_AFTER from reopened_ts, then closes from the transcript like any pending row
        run("start", {"session_id": "rs3", "cwd": cwd, "transcript_path": str(tr)}, led)
        rs_sync("rs3", "q3")
        f3_ = Path(cwd, "rs3", "subagents", "agent-q3.jsonl"); f3_.parent.mkdir(parents=True, exist_ok=True)
        f3_.write_text(json.dumps(sk_msg("text", good, "end_turn", "a", 1000)) + "\n"); os.utime(f3_, (time.time() - 600, time.time() - 600))
        rs_msg("rs3", "q3", rs_ok("q3"))
        sk_stop("rs3")
        check("resume: an old, finished transcript does not close the reopened row (reopened_ts is fresh)", rs_row("rs3", "q3").get("status") == "pending")
        rows3_ = ledrows("rs3"); rows3_[0]["reopened_ts"] = int(time.time()) - H.STALE_AFTER - 60
        Path(led, "rs3.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows3_))
        sk_stop("rs3")
        check("resume: a finished transcript older than reopened_ts (the old turn) does not close the reopened row even past STALE_AFTER", rs_row("rs3", "q3").get("status") == "pending")
        os.utime(f3_, (time.time() - H.STALE_AFTER - 30, time.time() - H.STALE_AFTER - 30))
        sk_stop("rs3")
        r3b_ = rs_row("rs3", "q3")
        check("resume: with reopened_ts older than STALE_AFTER and a finished transcript the receipt closes the row (closed_by=stale), no SubagentStop needed", r3b_.get("status") != "pending" and r3b_.get("closed_by") == "stale" and r3b_.get("resumed") == 1)
        # a resumed gate agent: sendmsg's own unlock counts once; the SubagentStop of the resumed turn does not unlock a second time
        run("start", {"session_id": "rs4", "cwd": cwd, "transcript_path": str(tr)}, led)
        setst("rs4", gate="auto")
        rs_sync("rs4", "q4", name="judge-r", stype="abofles:abofles-judge", text=ask_)
        ok4_ = gstate("rs4").get("judge_ok", 0)
        out4_ = rs_msg("rs4", "judge-r", rs_ok("q4"))
        ok4b_ = gstate("rs4").get("judge_ok", 0)
        run("stop", {"session_id": "rs4", "agent_id": "q4", "last_assistant_message": ask_}, led)
        q4_ = rs_row("rs4", "q4")
        check("resume: a resumed judge row is pending, sendmsg unlocks one question (SENDMSG_TEXT), the stop that closes it adds no judge_ok", "unlocked" in ctx(out4_) and ok4b_ == ok4_ + 1 and gstate("rs4").get("judge_ok", 0) == ok4b_ and q4_.get("status") == "done" and q4_.get("resumed") == 1)
        rs_sync("rs", "q5", name="plain-5")
        check("resume: a non-gate agent resume prints nothing (no SENDMSG_TEXT)", rs_msg("rs", "q5", rs_ok("q5")) is None)

        # 16. a run_in_background call whose result already came back complete, with its report, is a sync reply
        run("start", {"session_id": "bc4", "cwd": cwd, "transcript_path": str(tr)}, led)
        tin_ = dict(agent("haiku", "low")["tool_input"], run_in_background=True)
        run("post", dict(agent("haiku", "low", resp={"status": "completed", "agentId": "rc1", "content": [blk(good)]}, cwd=cwd), session_id="bc4", tool_input=tin_), led)
        rr_ = next(r for r in ledrows("bc4") if r.get("agent_id") == "rc1")
        check("post: run_in_background=True with a completed result that has a header is a sync reply, not pending", rr_.get("status") == "done" and rr_.get("confidence") == "high")
        run("post", dict(agent("haiku", "low", resp={"status": "completed", "agentId": "rc2", "content": [blk("Understood.")]}, cwd=cwd), session_id="bc4", tool_input=tin_), led)
        check("post: ... but a completed stub with no header stays pending", next(r for r in ledrows("bc4") if r.get("agent_id") == "rc2").get("status") == "pending")

        # ── Batch B ──
        RC = lambda n=1: f"🧾 **{n} Agent{'' if n == 1 else 's'} · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"
        stop_ = lambda sid_, text, active=False: run("receipt", {"session_id": sid_, "cwd": cwd, "transcript_path": str(tr), "stop_hook_active": active, "last_assistant_message": text}, led)
        def b_task(sid_):
            run("start", {"session_id": sid_, "cwd": cwd, "transcript_path": str(tr)}, led)
            run("post", dict(agent("haiku", "low", resp=good, cwd=cwd), session_id=sid_, transcript_path=str(tr)), led)
        # 1. only a printed receipt closes the task
        b_task("bb3")
        setst("bb3", gate="task", task_budget=6)
        check("close_task: the first Stop with no receipt nudges", (stop_("bb3", "all done") or {}).get("decision") == "block")
        check("close_task: the spent nudge (stop_hook_active) ends the Stop without a block ...", stop_("bb3", "still no receipt", True) is None)
        s_ = gstate("bb3")
        check("close_task: ... but the task stays open: count, budget and gate answer kept, receipt_owed set", s_.get("spawns_since_receipt") == 1 and s_.get("gate") == "task" and s_.get("task_budget") == 6 and s_.get("receipt_owed") is True)
        check("close_task: the next Stop nudges again while the receipt is owed", (stop_("bb3", "next turn") or {}).get("decision") == "block")
        check("close_task: a task with receipt_owed shows a 🟡 `receipt` item in the guide", any(i["id"] == "receipt" and i["p"] == "🟡" for i in gitems("bb3")))
        check("close_task: a printed receipt closes it and clears receipt_owed", stop_("bb3", RC()) is None and gstate("bb3").get("spawns_since_receipt") == 0 and "receipt_owed" not in gstate("bb3") and gstate("bb3").get("gate") is None)
        Path(state, "no-user-claude.md").write_text("## abofles overrides\n- Receipt = off\n")
        b_task("bb4")
        check("skip mode: Stop does not block and does not close the task — it is marked to close on the next prompt", stop_("bb4", "done.") is None and gstate("bb4").get("spawns_since_receipt") == 1 and gstate("bb4").get("close_on_prompt") is True)
        run("prompt", {"session_id": "bb4", "cwd": cwd, "prompt": "<task-notification>agent finished</task-notification>"}, led)
        check("skip mode: a task-notification turn does not close it", gstate("bb4").get("spawns_since_receipt") == 1 and gstate("bb4").get("close_on_prompt") is True)
        run("prompt", {"session_id": "bb4", "cwd": cwd, "prompt": "now do the next thing"}, led)
        check("skip mode: the next real prompt closes it and clears the flags", gstate("bb4").get("spawns_since_receipt") == 0 and "close_on_prompt" not in gstate("bb4") and gstate("bb4").get("last_prompt_ts"))
        Path(state, "no-user-claude.md").unlink()
        # 2. a turn that ends with a question waits on the user
        b_task("bb5")
        check("question: a final line ending with ? gets no receipt nudge, the task stays open", stop_("bb5", "I found two ways.\nWhich one do you want?\n\n") is None and gstate("bb5").get("spawns_since_receipt") == 1)
        check("question: a ? in the middle of the text still gets the nudge", (stop_("bb5", "Why? Because it works.\nDone.") or {}).get("decision") == "block")
        b_task("bb5b")
        check("question: the first reply ending in ? is not nudged but marks the receipt owed (the 🟡 item shows)", stop_("bb5b", "Which one?") is None and gstate("bb5b").get("receipt_owed") is True and any(i["id"] == "receipt" for i in gitems("bb5b")))
        out = stop_("bb5b", "Still unsure — which one?")
        check("question: the next turn's Stop nudges even when it ends in ? again (not skipped forever) and names the owed receipt", (out or {}).get("decision") == "block" and "still owed" in out.get("reason", ""))
        check("question: the nudge is once per turn — stop_hook_active ends the Stop, the receipt stays owed", stop_("bb5b", "Which one?", True) is None and gstate("bb5b").get("receipt_owed") is True)
        check("question: a printed receipt clears it", stop_("bb5b", RC()) is None and "receipt_owed" not in gstate("bb5b") and not any(i["id"] == "receipt" for i in gitems("bb5b")))
        # connector_free resets with a new ask when no task is open
        run("start", {"session_id": "bb9", "cwd": cwd, "transcript_path": str(tr)}, led)
        setst("bb9", connector_free=2)
        run("prompt", {"session_id": "bb9", "cwd": cwd, "prompt": "<task-notification>x</task-notification>"}, led)
        check("connector_free: a notification prompt does not reset it", gstate("bb9").get("connector_free") == 2)
        run("prompt", {"session_id": "bb9", "cwd": cwd, "prompt": "next ticket please"}, led)
        check("connector_free: a real prompt with no open task (spawns_since_receipt 0) resets it", gstate("bb9").get("connector_free") == 0)
        b_task("bb8")
        setst("bb8", connector_free=2)
        run("prompt", {"session_id": "bb8", "cwd": cwd, "prompt": "and one more thing"}, led)
        check("connector_free: ... but not while the task is open (agents pending a receipt)", gstate("bb8").get("connector_free") == 2)
        # 3. compact and resume keep the task; startup and clear reset it
        b_task("bb6")
        run("start", {"session_id": "bb6", "cwd": cwd, "transcript_path": str(tr), "source": "compact"}, led)
        check("start: a compact keeps the task", gstate("bb6").get("spawns_since_receipt") == 1)
        run("start", {"session_id": "bb6", "cwd": cwd, "transcript_path": str(tr), "source": "resume"}, led)
        check("start: a resume keeps the task", gstate("bb6").get("spawns_since_receipt") == 1)
        run("start", {"session_id": "bb6", "cwd": cwd, "transcript_path": str(tr), "source": "clear"}, led)
        check("start: a clear resets the task", gstate("bb6").get("spawns_since_receipt") == 0)
        b_task("bb6")
        run("start", {"session_id": "bb6", "cwd": cwd, "transcript_path": str(tr), "source": "startup"}, led)
        check("start: a startup resets the task", gstate("bb6").get("spawns_since_receipt") == 0)
        # 4. has_receipt wants the computed header
        check("has_receipt: the computed header counts (also with a gate suffix and leading spaces)", H.has_receipt(RC()) and H.has_receipt("text\n  " + RC(2)) and H.has_receipt("🧾 **0 Agents + 1 gate · 1 u** vs Opus 5.5"))
        check("has_receipt: a quoted 🧾, the words `── receipt` and a bare 🧾 line are not a receipt",
              not H.has_receipt("the 🧾 emoji marks it") and not H.has_receipt("── receipt ──\ndone x") and not H.has_receipt("> 🧾 **1 Agent · 1 u**") and not H.has_receipt("🧾 receipt below") and not H.has_receipt(""))
        b_task("bb7")
        check("receipt: a quoted receipt phrase in the final text still gets the nudge", (stop_("bb7", "I will print the 🧾 receipt later, ── receipt ── soon") or {}).get("decision") == "block")
        # 4b. the model's own receipt header closes the task (looks_like_receipt); the journal carries the computed header
        OWN = "🧾 **Receipt**\n- `done` → x\n- `why` → y\n- `check` → z\n- `open` → none\n- `next` → n"
        check("looks_like_receipt: own header with done+open fields counts (also the old columns); the computed header is not needed",
              H.looks_like_receipt(OWN) and H.looks_like_receipt("text\n🧾 Receipt\ndone   x\nopen   none") and not H.has_receipt(OWN))
        check("looks_like_receipt: a bare 🧾 line, a quoted one, prose about a receipt and fields without 🧾 are not a receipt",
              not H.looks_like_receipt("🧾 receipt coming") and not H.looks_like_receipt("> 🧾 **Receipt**\n- `done` → x\n- `open` → none") and not H.looks_like_receipt("the receipt has `done` and `open` fields")
              and not H.looks_like_receipt("🧾 **Receipt**\n- `done` → x") and not H.looks_like_receipt("- `done` → x\n- `open` → none") and not H.looks_like_receipt(""))
        b_task("bf1")
        hdr_ = re.sub(r"\*\*", "", H.receipt_header(gstate("bf1"))[1:]).strip()
        check("receipt: a receipt under the model's own header does not block, closes the task",
              stop_("bf1", OWN) is None and gstate("bf1").get("spawns_since_receipt") == 0 and "receipt_owed" not in gstate("bf1"))
        jt_ = sf("bf1").read_text()
        check("receipt: ... and the journal line carries the computed header with the parsed fields", hdr_ in jt_ and "· Receipt" not in jt_ and "done: x" in jt_ and "check: z" in jt_)
        b_task("bf2")
        check("receipt: a message with only `🧾 receipt coming` is still nudged, the task stays open",
              (stop_("bf2", "🧾 receipt coming") or {}).get("decision") == "block" and gstate("bf2").get("spawns_since_receipt") == 1)
        # 4c. a background chain that ended: the next notification prompt carries the receipt instruction (the launch carried none)
        bf_launch = lambda sid_, aid_: run("post", dict(agent("haiku", "low", resp=launch_(aid_), cwd=cwd), session_id=sid_, transcript_path=str(tr)), led)
        bf_done = lambda sid_, aid_: run("stop", {"session_id": sid_, "agent_id": aid_, "last_assistant_message": good}, led)
        NOTE = "<task-notification>agent finished</task-notification>"
        run("start", {"session_id": "bf3", "cwd": cwd, "transcript_path": str(tr)}, led)
        bf_launch("bf3", "bf3a"); bf_done("bf3", "bf3a")
        out_ = ctx(run("prompt", {"session_id": "bf3", "cwd": cwd, "prompt": NOTE}, led))
        check("notification: the last background agent finished -> hook context carries the receipt instruction and the computed header",
              "Receipt at task end" in out_ and "🧾 **1 Agent" in out_ and "running" not in out_)
        run("start", {"session_id": "bf4", "cwd": cwd, "transcript_path": str(tr)}, led)
        bf_launch("bf4", "bf4a"); bf_launch("bf4", "bf4b"); bf_done("bf4", "bf4a")
        check("notification: another background agent still runs -> no receipt instruction", "Receipt at task end" not in ctx(run("prompt", {"session_id": "bf4", "cwd": cwd, "prompt": NOTE}, led)))
        run("start", {"session_id": "bf5", "cwd": cwd, "transcript_path": str(tr)}, led)
        bf_launch("bf5", "bf5a"); bf_done("bf5", "bf5a")
        check("notification: a real user prompt gets no receipt instruction", "Receipt at task end" not in ctx(run("prompt", {"session_id": "bf5", "cwd": cwd, "prompt": "and now?"}, led)))
        Path(state, "no-user-claude.md").write_text("## abofles overrides\n- Receipt = off\n")
        run("start", {"session_id": "bf6", "cwd": cwd, "transcript_path": str(tr)}, led)
        bf_launch("bf6", "bf6a"); bf_done("bf6", "bf6a")
        out_ = ctx(run("prompt", {"session_id": "bf6", "cwd": cwd, "prompt": NOTE}, led))
        check("notification: Receipt = off -> no receipt instruction", "Receipt at task end" not in out_ and "no receipt for this task" not in out_)
        Path(state, "no-user-claude.md").unlink()
        # 5. the session a script acts on
        import tempfile as _tf
        sess_saved = H.STATE_DIR
        U1, U2 = "11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        pj, po = str(Path(cwd, "projX")), str(Path(cwd, "projY"))
        os.makedirs(pj, exist_ok=True); os.makedirs(po, exist_ok=True)
        def put(d, sid_, root_, lp=None, age=0):
            fp = Path(d, f"{sid_}.json"); fp.write_text(json.dumps(dict({"root": root_}, **({"last_prompt_ts": lp} if lp else {}))))
            os.utime(fp, (time.time() - age, time.time() - age))
        try:
            with _tf.TemporaryDirectory() as sd:
                H.STATE_DIR = Path(sd)
                put(sd, "sA", pj, lp=100, age=500); put(sd, "sB", pj, lp=900, age=900); put(sd, "sC", pj, age=0); put(sd, "sO", po, lp=5000, age=0); put(sd, U1, pj, lp=50, age=700)
                pin()
                check("session: last_prompt_ts beats a newer mtime (sB prompted last though sC wrote last)", H.newest_session_id(pj) == "sB")
                check("session: another project's newer state is never chosen", H.newest_session_id(pj) != "sO" and H.newest_session_id(po) == "sO")
                os.environ["ABOFLES_SESSION"] = "sA"
                check("session: ABOFLES_SESSION wins over everything", H.newest_session_id(pj, U1) == "sA" and H.newest_state_path(pj).name == "sA.json")
                pin()
                check("session: an explicit UUID of an existing state is used", H.newest_session_id(pj, U1) == U1 and H.newest_state(pj, U1).get("last_prompt_ts") == 50)
                check("session: an unexpanded ${CLAUDE_SESSION_ID}, an empty value and an unknown UUID are ignored",
                      all(H.newest_session_id(pj, v) == "sB" for v in ("${CLAUDE_SESSION_ID}", "", None, U2)))
                check("session: a short id names that session (exact name, or the one state it starts), never the newest one; an unknown or ambiguous short id is none",
                      H.newest_session_id(pj, "sA") == "sA" and H.newest_session_id(pj, U1[:8]) == U1 and H.newest_state_path(pj, "zzzzzzzz") is None
                      and H.newest_state_path(pj, "sZ") is None and (put(sd, U1[:8] + "-0000-4000-8000-000000000000", pj, age=1) or True) and H.newest_state_path(pj, U1[:8]) is None)
                check("session: an unexpanded ABOFLES_SESSION is ignored", (os.environ.update(ABOFLES_SESSION="${X}") or True) and H.newest_session_id(pj) == "sB" and (pin() or True))
            with _tf.TemporaryDirectory() as sd:
                H.STATE_DIR = Path(sd)
                put(sd, "mOld", pj, age=900); put(sd, "mNew", pj, age=10); put(sd, "mOther", po, age=0)
                check("session: with no last_prompt_ts the newest mtime of the project wins", H.newest_session_id(pj) == "mNew")
                check("session: a folder with no state of its own gets nothing, not another project's", H.newest_state_path(str(Path(cwd, "projZ"))) is None and H.newest_state(str(Path(cwd, "projZ"))) == {})
                put(sd, "mSub", pj, lp=70, age=0)
                sub_ = Path(pj, "deep"); sub_.mkdir(exist_ok=True)
                check("session: a command run in a subfolder of the project belongs to the project's session", H.newest_session_id(str(sub_)) == "mSub")
        finally:
            H.STATE_DIR = sess_saved
            pin()
        av = ["set", "4", "--session", U1]
        check("session_arg: pops --session <id> and returns it", H.session_arg(av) == U1 and av == ["set", "4"])
        av = ["--all", "--session", "${CLAUDE_SESSION_ID}"]
        check("session_arg: an unexpanded value is dropped and ignored", H.session_arg(av) is None and av == ["--all"])
        av = ["resolve", "1", "--session", "mine", "--session", U1]
        check("session_arg: keep=True leaves usable pairs, the first usable id wins", H.session_arg(av, keep=True) == "mine" and av == ["resolve", "1", "--session", "mine", "--session", U1])
        av = ["resolve", "1", "--session"]
        check("session_arg: a missing value stays for the script's own usage check", H.session_arg(av) is None and av == ["resolve", "1", "--session"])
        # the script, end to end: --session <uuid> acts on that state, not on a newer one of the same folder
        run("start", {"session_id": U2, "cwd": cwd, "transcript_path": str(tr)}, led)
        run("start", {"session_id": "sessNew", "cwd": cwd, "transcript_path": str(tr)}, led)
        run("prompt", {"session_id": "sessNew", "cwd": cwd, "prompt": "hi"}, led)
        pre_ = gstate("sessA").get("task_budget")
        check("cli: agents.py set N --session <uuid> writes that session only", "agents: 3 this task" in ag("set", "3", "--session", U2) and gstate(U2).get("task_budget") == 3 and gstate("sessNew").get("task_budget") is None and gstate("sessA").get("task_budget") == pre_)
        check("cli: agents.py ignores an unexpanded session id and acts on the folder's newest prompt", "agents: 6 this task" in ag("set", "6", "--session", "${CLAUDE_SESSION_ID}") and gstate("sessNew").get("task_budget") == 6)
        # 6. stale install
        import tempfile as _tf2
        home_saved = os.environ.get("HOME")
        try:
            with _tf2.TemporaryDirectory() as hh:
                os.environ["HOME"] = hh
                cache = Path(hh, ".claude", "plugins", "cache", "mp", "abofles")
                old_root, new_root = cache / "1.0.0", cache / "1.3.0"
                for r_, v_ in ((old_root, "1.0.0"), (new_root, "1.3.0")):
                    (r_ / ".claude-plugin").mkdir(parents=True); (r_ / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "abofles", "version": v_}))
                reg = Path(hh, ".claude", "plugins", "installed_plugins.json")
                entry = [{"scope": "user", "installPath": str(new_root), "version": "1.3.0"}]
                reg.write_text(json.dumps({"version": 2, "plugins": {"abofles@mp": entry, "other@mp": [{"installPath": str(old_root), "version": "9"}]}}))
                note_ = H.stale_install_note(old_root)
                check("stale install: a session from an old cache folder gets the one-line warning", note_ == "abofles: this session runs 1.0.0 from an old cache folder; 1.3.0 is installed — restart Claude Code to load it.")
                reg.write_text(json.dumps({"abofles@mp": entry}))
                check("stale install: the registry without a top-level plugins key works too", "1.3.0 is installed" in (H.stale_install_note(old_root) or ""))
                check("stale install: the running copy is the installed one -> silent", H.stale_install_note(new_root) is None)
                reg.write_text(json.dumps({"plugins": {"abofles@mp": entry + [{"installPath": str(old_root), "version": "1.0.0"}]}}))
                check("stale install: it matches any abofles@* install path -> silent", H.stale_install_note(old_root) is None)
                check("stale install: the dev repo (outside the cache) is silent", H.stale_install_note() is None and H.stale_install_note(Path(cwd)) is None)
                reg.write_text("{ not json")
                check("stale install: bad JSON is silent", H.stale_install_note(old_root) is None)
                reg.unlink()
                check("stale install: no registry file is silent", H.stale_install_note(old_root) is None)
                reg.write_text(json.dumps({"plugins": {"other@mp": entry}}))
                check("stale install: abofles not in the registry is silent", H.stale_install_note(old_root) is None)
            note_f = H.stale_install_note
            H.stale_install_note = lambda root=None: "abofles: stale"
            st_i = {}
            check("stale install: emitted once per session (install_checked)", H.install_note_once(st_i) == "abofles: stale" and st_i.get("install_checked") is True and H.install_note_once(st_i) is None)
            H.stale_install_note = note_f
        finally:
            if home_saved is None: os.environ.pop("HOME", None)
            else: os.environ["HOME"] = home_saved


    # scripts: status.py / memo.py exit codes
    with tempfile.TemporaryDirectory() as xh, tempfile.TemporaryDirectory() as xp:
        sdir = Path(__file__).resolve().parent
        xenv = dict(os.environ, HOME=xh, ABOFLES_LEDGER_DIR=str(Path(xh, "led")), ABOFLES_STATE_DIR=str(Path(xh, "st")))
        Path(xh, "led").mkdir(); Path(xh, "st").mkdir()
        st_ = lambda *a: subprocess.run([sys.executable, str(sdir / "status.py"), *a], cwd=xp, capture_output=True, text=True, env=xenv)
        me_ = lambda *a: subprocess.run([sys.executable, str(sdir / "memo.py"), *a], cwd=xp, capture_output=True, text=True, env=xenv)
        Path(xh, "led", "gs.jsonl").write_text(json.dumps({"ts": int(time.time()), "session": "gs", "cwd": xp, "agent_id": "g1", "side": "Git", "agent": "abofles:abofles-git", "model": "haiku", "effort": "low", "status": "completed", "confidence": "high"}) + "\n")
        check("status: the Sides line counts the Git side after Work", "Git 1" in st_("--session", "gs").stdout)
        check("exit: status --resolve with no ledger at all exits 1", st_("--resolve", "abc", "--trace", "re-checked: t").returncode == 1)
        lf = Path(xh, "led", "xs.jsonl")
        lf.write_text('{"agent_id": "abc123", "status": "partial"}\nHALF{"agent_id":"abc9\n{"agent_id": "abd1", "status": "blocked"}\n')
        check("exit: status --resolve as the last arg exits 2", st_("--resolve").returncode == 2)
        check("exit: status --resolve with an empty id exits 2", st_("--resolve", "", "--session", "xs").returncode == 2)
        check("exit: status --resolve with a flag as id exits 2", st_("--resolve", "--trace").returncode == 2)
        check("exit: status --session without a value exits 2", st_("--resolve", "abc", "--session").returncode == 2)
        check("exit: status --unresolve with an empty session exits 2", st_("--unresolve", "abc", "--session", "", "--trace").returncode == 2)
        check("exit: status --session ../ is cleaned to a name in the ledger dir (no such ledger, exit 1)", st_("--resolve", "abc", "--session", "../../nope", "--trace", "re-checked: t").returncode == 1 and not Path(xh, "nope.jsonl").exists())
        r1 = st_("--resolve", "abc1", "--session", "xs", "--trace", "by test")
        txt = lf.read_text().splitlines()
        check("exit: status --resolve exits 0, marks the row, keeps the half-written line verbatim",
              r1.returncode == 0 and json.loads(txt[0]).get("resolved") is True and txt[1] == 'HALF{"agent_id":"abc9' and json.loads(txt[2]) == {"agent_id": "abd1", "status": "blocked"})
        memd = lambda s: Path(xp, ".claude", "abofles-memo.d", f"{s[:8]}.md")  # a trace lands in the file of the session it names
        mem = memd("xs")
        check("exit: status --resolve --trace wrote one Journal line", mem.read_text().count("resolved by hand:") == 1)
        r2 = st_("--resolve", "abc1", "--session", "xs", "--trace", "again")
        check("exit: status --resolve of a resolved row exits 3, writes no trace", r2.returncode == 3 and mem.read_text().count("resolved by hand:") == 1)
        check("exit: status --resolve miss exits 1", st_("--resolve", "zzz", "--session", "xs", "--trace", "re-checked: t").returncode == 1)
        check("exit: status --unresolve exits 0", st_("--unresolve", "abc1", "--session", "xs").returncode == 0 and "resolved" not in json.loads(lf.read_text().splitlines()[0]))
        check("exit: status --unresolve of an open row exits 3, writes no trace", st_("--unresolve", "abc1", "--session", "xs", "--trace").returncode == 3 and "reopened by hand" not in mem.read_text())
        # memo.py: Needs-you items through the hook's own writer
        subprocess.run([sys.executable, "-c", "import sys,os; sys.path.insert(0, sys.argv[1]); import abofles_hook as h; h.memo_needs(os.getcwd(), 'sessAAAA', 'owner decision: pick A or B')", str(sdir.parent / "hooks")], cwd=xp, env=xenv)
        check("exit: memo resolve without n exits 2", me_("resolve").returncode == 2 and me_("resolve", "x").returncode == 2)
        check("exit: memo resolve --session without a value exits 2", me_("resolve", "1", "--session").returncode == 2 and me_("reopen", "1", "--session", "--trace").returncode == 2)
        check("exit: memo reopen of an open item exits 3, writes no trace", me_("reopen", "1", "--trace", "--session", "sessAAAA").returncode == 3 and "reopened by hand" not in mem.read_text())
        m1 = me_("resolve", "1", "note", "--trace", "--session", "mysess", "--force")  # the item is sessAAAA's: mysess settles it only with --force
        check("exit: memo resolve exits 0 and the trace carries the given session", m1.returncode == 0 and "· resolved by hand: owner decision 1" in memd("mysess").read_text())
        n_ = memd("mysess").read_text().count("by hand:")
        m2 = me_("resolve", "1", "again", "--trace", "--session", "mysess", "--force")
        check("exit: memo resolve of a resolved item exits 3, writes no trace", m2.returncode == 3 and memd("mysess").read_text().count("by hand:") == n_)
        check("exit: memo resolve of a missing item exits 1", me_("resolve", "9", "re-checked: t", "--session", "sessAAAA").returncode == 1 and me_("reopen", "9", "--session", "sessAAAA").returncode == 1)
        check("exit: memo reopen exits 0 with the given session", me_("reopen", "1", "--trace", "--session", "other", "--force").returncode == 0 and "· reopened by hand" in memd("other").read_text())

    # ── 1.7.0: specialist reviewers and the review gate ──
    with tempfile.TemporaryDirectory() as rh:
        os.environ["ABOFLES_STATE_DIR"] = str(Path(rh, "st")); os.environ["ABOFLES_CONFIG"] = str(Path(rh, "config.json"))
        os.environ.pop("ABOFLES_SESSION", None)
        import importlib.util as _iu3
        _hs3 = _iu3.spec_from_file_location("abofles_hook_rv", str(HOOK)); R = _iu3.module_from_spec(_hs3); _hs3.loader.exec_module(R)
        led3 = str(Path(rh, "led")); Path(led3).mkdir()
        P = Path(rh, "P"); P.mkdir(); Path(P, "src").mkdir(); Path(P, "src/a.cs").write_text("x\n")
        trr = Path(rh, "tr.jsonl"); trr.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
        rst = lambda sid_: json.loads(Path(rh, "st", f"{sid_}.json").read_text())
        rows_ = lambda sid_: [json.loads(l) for l in Path(led3, f"{sid_}.jsonl").read_text().splitlines() if l.strip()]
        VOK = "STATUS: done\nCONFIDENCE: high\nVERDICT: PASS\nEVIDENCE:\n- authz — PASS — src/a.cs:1\nISSUES:\nNOTE: none"
        WOK = "STATUS: done\nCONFIDENCE: high\nCHANGED:\n- src/a.cs — x\nRAN: none\nLEFT: none"
        def sp(sid_, side, stype, model="opus", effort="high", resp=WOK, topic="Payments & Gateway Integration", tier=4):
            d = dict(agent(model, effort, stype, prompt=f"abofles: {topic} / {side} · T{tier}\nq\nLine cap: 40", resp=resp, cwd=str(P)), session_id=sid_, transcript_path=str(trr))
            run("pre", d, led3); return run("post", d, led3)
        def stop_(sid_, msg, active=False):
            return run("receipt", {"session_id": sid_, "cwd": str(P), "transcript_path": str(trr), "stop_hook_active": active, "last_assistant_message": msg}, led3)
        RCPT = "🧾 **2 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"
        check("review: parse_header reads the reviewer tags", R.parse_header("abofles: Payments & Gateway Integration / Security · T4")[1] == "Security" and R.parse_header("abofles: X / qa · T3")[1] == "QA"
              and R.parse_header("abofles: X / uiux · T3")[1] == "UIUX" and R.parse_header("abofles: X / Clean · T3")[1] == "Clean" and R.parse_header("abofles: X / Verify · T2")[1] == "Verify")
        check("review: side_of maps the reviewer agent types", R.side_of("abofles:abofles-security")[1] == "Security" and R.side_of("abofles:abofles-qa")[1] == "QA" and R.side_of("abofles:abofles-cleancoder")[1] == "Clean" and R.side_of("abofles:abofles-verifier")[1] == "Verify")
        check("review: fable is refused for a reviewer", "fable is only for" in reason(run("pre", dict(agent("fable", "medium", "abofles:abofles-finance", cwd=str(P)), session_id="rv0"), led3)))
        check("review: workflow.json is generated and names the reviewers per topic", (R.workflow_table().get("topics") or {}).get("Payments & Gateway Integration", {}).get("reviewers", {}).get("4") == ["finance", "security", "tester"])
        # a T4 money change closed without its reviewers: refused once, then closed with a guide item
        run("start", {"session_id": "rv1", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("rv1"), gate="auto"), open(Path(rh, "st", "rv1.json"), "w"))
        sp("rv1", "Work", "abofles:abofles-worker")
        sp("rv1", "Verify", "abofles:abofles-verifier", resp=VOK)
        out = stop_("rv1", RCPT)
        check("review gate: 🔴 change with no reviewer -> receipt refused once, naming Finance, Security, Tester and the Verify cell",
              (out or {}).get("decision") == "block" and "review gate" in out.get("reason", "") and "Finance, Security, Tester" in out["reason"] and "Opus 5.5 · High" in out["reason"]
              and "`abofles: Payments & Gateway Integration / Security · T4`" in out["reason"] and rst("rv1").get("spawns_since_receipt") == 2)
        sp("rv1", "Finance", "abofles:abofles-finance", resp=VOK)
        check("review: the ledger row carries review=finance, graded as a verifier (PASS)", any(r.get("review") == "finance" and r.get("verdict") == "PASS" and r.get("side") == "Finance" for r in rows_("rv1")))
        out = stop_("rv1", RCPT + "\n- `workflow` → Read ✓ · Work ✓ · Verify ✓ · Finance ✓ · Security skipped: internal tool, no external input · Tester skipped: covered by Verify", active=True)
        check("review gate: skipped reviewers named in the workflow line pass; the task closes", out is None and rst("rv1").get("spawns_since_receipt") == 0 and not rst("rv1").get("review_missing"))
        sk_ = lambda s: [k for k, w in H.REVIEW_SIDES.items() if re.search(r"\b" + re.escape(w) + H.SKIP_TAIL, s, re.I)]
        check("review gate: a skip needs its reason, and one reviewer's skip does not skip the one before it on the line",
              sk_("Finance · Security skipped: internal tool") == ["security"] and sk_("Security skipped") == [] and sk_("QA skipped because no UI changed") == ["qa"]
              and sk_("Tester skipped — covered by Verify") == ["tester"] and sk_("Security review skipped (no external input)") == ["security"])
        sp("rv1", "Work", "abofles:abofles-worker")
        out = stop_("rv1", RCPT)
        out2 = stop_("rv1", RCPT, active=True)
        check("review gate: the refusal is spent after one Stop; the task closes and the guide shows the missing reviewers",
              (out or {}).get("decision") == "block" and out2 is None and rst("rv1").get("spawns_since_receipt") == 0 and rst("rv1").get("review_missing", {}).get("names") == ["finance", "security", "tester"]
              and any(i.get("id") == "review" and "Finance, Security, Tester" in i.get("text", "") for i in (rst("rv1").get("guide") or {}).get("items") or []))
        # 2.6: one refusal per open task — a receipt recorded at each background step (memo.py: never stop_active) was refused at every step
        run("start", {"session_id": "rv1b", "cwd": str(P), "transcript_path": str(trr)}, led3)
        sp("rv1b", "Work", "abofles:abofles-worker")
        o1, o2 = stop_("rv1b", RCPT), stop_("rv1b", RCPT)
        check("review gate: a second receipt of the same open task is not refused again; it closes with the guide item",
              (o1 or {}).get("decision") == "block" and o2 is None and rst("rv1b").get("spawns_since_receipt") == 0 and rst("rv1b").get("review_missing", {}).get("names") == ["finance", "security", "tester"])
        sp("rv1b", "Work", "abofles:abofles-worker")
        check("review gate: ... the next task of the same topic is refused once again", (stop_("rv1b", RCPT) or {}).get("decision") == "block")
        check("review gate: the refusal says the reviewers start once Verify has passed", "once Verify has passed" in R.review_gate_text({"missing": ["security"], "tier": 4, "topics": ["Payments & Gateway Integration"]}, "Opus 5.5 · High"))
        # 2.6: the loop guard — a fix loop or a gate loop with no word from the user stops and the user is asked
        FAILR = "STATUS: done\nCONFIDENCE: high\nVERDICT: FAIL\nEVIDENCE:\n- authz — FAIL — src/a.cs:1\nISSUES:\n- src/a.cs:1 — broken — fix\nNOTE: none"
        def pre_(sid_, side, stype, model="opus", effort="high", topic="Payments & Gateway Integration", tier=4):
            return run("pre", dict(agent(model, effort, stype, prompt=f"abofles: {topic} / {side} · T{tier}\nq\nLine cap: 40", cwd=str(P)), session_id=sid_, transcript_path=str(trr)), led3)
        def ask_(sid_, q="What next?"):
            return run("qgate", {"session_id": sid_, "cwd": str(P), "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": q, "options": [{"label": "A"}, {"label": "B"}]}]}}, led3)
        run("start", {"session_id": "lp1", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("lp1"), gate="off"), open(Path(rh, "st", "lp1.json"), "w"))
        for _ in range(3):
            sp("lp1", "Work", "abofles:abofles-worker"); sp("lp1", "Verify", "abofles:abofles-verifier", model="sonnet", effort="xhigh", resp=FAILR)
        d_ = pre_("lp1", "Work", "abofles:abofles-worker")
        check("loop guard: the 3rd FAIL of Verify on one topic refuses the next agent and says to ask the user",
              "loop guard" in reason(d_) and "Verify on Payments & Gateway Integration FAILed 3 times" in reason(d_) and "AskUserQuestion" in reason(d_) and rst("lp1").get("loop_stop"))
        check("loop guard: ... a reader still runs", "loop guard" not in reason(pre_("lp1", "Read", "abofles:abofles-reader", model="sonnet", effort="low")))
        check("loop guard: ... a Judge waits too (the question goes to the user without one)", "loop guard" in reason(pre_("lp1", "Judge", "abofles:abofles-judge", model="opus", effort="medium")))
        check("loop guard: ... the question goes through with no Judge, and the counts start over", ask_("lp1") is None and not rst("lp1").get("loop") and "loop guard" not in reason(pre_("lp1", "Work", "abofles:abofles-worker")))
        run("start", {"session_id": "lp2", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("lp2"), gate="off"), open(Path(rh, "st", "lp2.json"), "w"))
        for r_ in (FAILR, FAILR, VOK, FAILR, FAILR):
            sp("lp2", "Verify", "abofles:abofles-verifier", model="sonnet", effort="xhigh", resp=r_)
        sp("lp2", "Security", "abofles:abofles-security", resp=FAILR)
        check("loop guard: a PASS ends that check's loop, and another check's FAIL counts apart", "loop guard" not in reason(pre_("lp2", "Work", "abofles:abofles-worker")) and rst("lp2")["loop"]["fails"] == {"Verify on Payments & Gateway Integration": 2, "Security on Payments & Gateway Integration": 1})
        sp("lp2", "Verify", "abofles:abofles-verifier", model="sonnet", effort="xhigh", resp=FAILR)
        run("prompt", {"session_id": "lp2", "cwd": str(P), "prompt": "try the other lock order"}, led3)
        check("loop guard: a prompt of the user's starts the counts over", "loop guard" not in reason(pre_("lp2", "Work", "abofles:abofles-worker")) and not rst("lp2").get("loop"))
        run("start", {"session_id": "lp3", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("lp3"), gate="off"), open(Path(rh, "st", "lp3.json"), "w"))
        ANSJ = "STATUS: done\nCONFIDENCE: high\nVERDICT: ANSWERED\nQUESTION: q\nANSWER: a\nEVIDENCE:\n- src/a.cs:1 — x"
        jr = []
        for i_ in range(4):
            jd = dict(agent("opus", "medium", "abofles:abofles-judge", prompt=f"abofles: Payments & Gateway Integration / Judge · T4\nq{i_}\nLine cap: 40", cwd=str(P)), session_id="lp3", transcript_path=str(trr))
            jr.append(reason(run("pre", jd, led3))); run("post", dict(jd, tool_response={"status": "completed", "content": [{"type": "text", "text": ANSJ}]}), led3)
        check("loop guard: 3 Judges with no question asked — the 4th waits and the next question goes straight to the user",
              all("loop guard" not in x for x in jr[:3]) and "3 Judges ran and no question reached the user" in jr[3] and ask_("lp3", "Which lock order?") is None)
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Loop guard = off\n")
        run("start", {"session_id": "lp4", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("lp4"), gate="off"), open(Path(rh, "st", "lp4.json"), "w"))
        for _ in range(4):
            sp("lp4", "Verify", "abofles:abofles-verifier", model="sonnet", effort="xhigh", resp=FAILR)
        check("loop guard: Loop guard = off never stops", "loop guard" not in reason(pre_("lp4", "Work", "abofles:abofles-worker")) and R.loop_limit(str(P)) == 0)
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Loop guard = 5  # long fix loops ok\n")
        check("loop guard: N with a note reads as N; unset = 3", R.loop_limit(str(P)) == 5 and R.override_known("Loop guard", "5  # x") and R.override_known("Loop guard", "off"))
        Path(P, "CLAUDE.md").unlink()
        check("loop guard: unset = 3", R.loop_limit(str(P)) == 3)
        # 2.6: the Scrum agent — a follow-up side like Git
        check("scrum: parse_header and side_of know the Scrum side", R.parse_header("abofles: Sprint Planning, Capacity & Velocity / Scrum · T2")[1] == "Scrum" and R.side_of("abofles:abofles-scrum") == ("scrum", "Scrum"))
        check("scrum: fable is refused for the Scrum agent", "fable is only for" in reason(run("pre", dict(agent("fable", "medium", "abofles:abofles-scrum", prompt="abofles: Sprint Planning, Capacity & Velocity / Scrum · T2\nq", cwd=str(P)), session_id="sc0"), led3)))
        run("start", {"session_id": "sc1", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("sc1"), gate="off"), open(Path(rh, "st", "sc1.json"), "w"))
        SOK = "STATUS: done\nCONFIDENCE: high\nTRACKER: Jira · project DCMP\nACTIONS:\n- DCMP-1 — read: In Progress\nLEFT: none"
        sp("sc1", "Scrum", "abofles:abofles-scrum", model="sonnet", effort="high", resp=SOK)
        check("scrum: its row is side Scrum (not Work: it never trips the review gate)", any(r.get("side") == "Scrum" and r.get("status") == "done" for r in rows_("sc1")) and stop_("sc1", RCPT) is None)
        nt_ = R.topic_note({"prompt": "abofles: Sprint Planning, Capacity & Velocity / Scrum · T2\nq", "model": "opus", "effort": "medium"}, str(P), "sc1")
        check("scrum: an Opus Scrum agent on a T2 topic is over its cell (the economy Work cell, Sonnet · Medium)", bool(nt_) and "sonnet·medium" in nt_)
        sp("rv1", "Work", "abofles:abofles-worker")
        check("review gate: the next task's first agent clears the item", not rst("rv1").get("review_missing"))
        _now = 1_000_000
        _pr = lambda **k: dict({"review": "dba", "verdict": "PASS", "topic": "X", "ts": _now - 3600, "description": "DBA review"}, **k)
        check("review memory: a pass on the same topic within 2 h counts, an older one does not", R.reviewed_earlier(str(P), [_pr()], [{}], ["X"], _now) == {"dba"} and R.reviewed_earlier(str(P), [_pr(ts=_now - 3 * 3600)], [{}], ["X"], _now) == set())
        check("review memory: the same ticket key counts at any age and on any topic", R.reviewed_earlier(str(P), [_pr(topic="Y", ts=1, description="DBA of DCMP-969")], [{"description": "Fix DCMP-969 tag"}], ["X"], _now) == {"dba"})
        check("review memory: another topic with no shared ticket does not count", R.reviewed_earlier(str(P), [_pr(topic="Y", description="DBA of DCMP-1")], [{"description": "Fix DCMP-2"}], ["X"], _now) == set())
        check("review memory: a lost reviewer (no verdict) does not count", R.reviewed_earlier(str(P), [_pr(verdict=None, how="lost")], [{}], ["X"], _now) == set())
        check("review memory: a 🔴 change needs the same ticket; the same topic is not enough", R.reviewed_earlier(str(P), [_pr()], [{}], ["X"], _now, tier=4) == set() and R.reviewed_earlier(str(P), [_pr(description="DBA of DCMP-9")], [{"description": "Fix DCMP-9"}], ["X"], _now, tier=4) == {"dba"})
        check("review memory: a later FAIL and a pending row do not count", R.reviewed_earlier(str(P), [_pr(), _pr(verdict="FAIL")], [{}], ["X"], _now) == set() and R.reviewed_earlier(str(P), [_pr(status="pending")], [{}], ["X"], _now) == set())
        # T3: warn only (guide item, no refusal); Review gate = on refuses T3 too; off is silent; Reviewers: override
        run("start", {"session_id": "rv2", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("rv2"), gate="auto"), open(Path(rh, "st", "rv2.json"), "w"))
        sp("rv2", "Work", "abofles:abofles-worker", model="sonnet", topic="Business Logic (domain rules)", tier=3)
        out = stop_("rv2", RCPT)
        check("review gate: 🟠 change with no reviewer -> no refusal, guide item names QA", out is None and rst("rv2").get("review_missing", {}).get("names") == ["qa"])
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Review gate = on\n")
        sp("rv2", "Work", "abofles:abofles-worker", model="sonnet", topic="Business Logic (domain rules)", tier=3)
        check("review gate: Review gate = on refuses a 🟠 change too", (stop_("rv2", RCPT) or {}).get("decision") == "block")
        stop_("rv2", RCPT, active=True)
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Review gate = off\n")
        sp("rv2", "Work", "abofles:abofles-worker")
        check("review gate: off -> a 🔴 change closes silently, no item", stop_("rv2", RCPT) is None and not rst("rv2").get("review_missing"))
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Reviewers: FIN = finance\n")
        sp("rv2", "Work", "abofles:abofles-worker")
        out = stop_("rv2", RCPT)
        check("review gate: Reviewers: <group> = finance narrows the requirement", "Finance" in (out or {}).get("reason", "") and "Security" not in (out or {}).get("reason", ""))
        stop_("rv2", RCPT, active=True)
        Path(P, "CLAUDE.md").write_text("## abofles overrides\n- Reviewers: Payments & Gateway Integration = none\n")
        sp("rv2", "Work", "abofles:abofles-worker")
        check("review gate: Reviewers: <topic> = none drops it", stop_("rv2", RCPT) is None and not rst("rv2").get("review_missing"))
        Path(P, "CLAUDE.md").unlink()
        check("review: topic 5 adds Compliance at T3 and T4", (R.workflow_table()["topics"]["Data Privacy, PII & Compliance"]["reviewers"]["4"]) == ["security", "tester", "compliance"])
        # a receipt printed mid-turn (closed by the next spawn's pre) cannot be refused: the gate warns in the guide and the warning survives that spawn
        run("start", {"session_id": "rv3", "cwd": str(P), "transcript_path": str(trr)}, led3)
        json.dump(dict(rst("rv3"), gate="auto"), open(Path(rh, "st", "rv3.json"), "w"))
        sp("rv3", "Work", "abofles:abofles-worker")
        with open(trr, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"id": "m9", "role": "assistant", "content": [{"type": "text", "text": RCPT}]}}) + "\n")
        sp("rv3", "Work", "abofles:abofles-worker", topic="Business Logic (domain rules)", tier=3)
        check("review gate: a mid-turn receipt closes the task, raises the missing reviewers in the guide, and the closing agent keeps the item",
              rst("rv3").get("spawns_since_receipt") == 1 and rst("rv3").get("review_missing", {}).get("names") == ["finance", "security", "tester"])
        check("review: the gate's cell text comes from workflow.json", R.verify_cell(4) == ("opus", "high") and R.verify_cell(3) == ("sonnet", "high") and (R.workflow_table().get("verify") or {}).get("4") == "opus·high")
        dvq_ = (R.workflow_table().get("topics") or {}).get("Data Validation QA (reports, numbers, totals)") or {}
        check("review: Data Validation QA (reports, numbers, totals) is reviewed by QA and Data at T3, not Finance", (dvq_.get("reviewers") or {}).get("3") == ["qa", "data"] and "finance" not in sum((dvq_.get("reviewers") or {}).values(), []))
        check("review: gen_matrix --check is up to date", subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "gen_matrix.py"), "--check"], capture_output=True, text=True).returncode == 0)
        os.environ["ABOFLES_STATE_DIR"] = state

    # ── 1.6.0: work root (the memo follows the session's edits), memo move, resume/guide scopes, registry ──
    with tempfile.TemporaryDirectory() as wh:
        os.environ["ABOFLES_STATE_DIR"] = str(Path(wh, "st")); os.environ["ABOFLES_CONFIG"] = str(Path(wh, "config.json"))
        os.environ.pop("ABOFLES_SESSION", None)
        import importlib.util as _iu2
        _hs2 = _iu2.spec_from_file_location("abofles_hook_wr", str(HOOK)); W = _iu2.module_from_spec(_hs2); _hs2.loader.exec_module(W)
        led2 = str(Path(wh, "led")); Path(led2).mkdir()
        A, B, C, D = (Path(wh, n) for n in "ABCD")
        for d in (A, B, C, D):
            d.mkdir()
        gitok = all(subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True).returncode == 0 for d in (A, B, C))  # A is a repository too: a launch root in no repository is claimed by the first repo edited (tested below)
        wst = lambda sid_: json.loads(Path(wh, "st", f"{sid_}.json").read_text())
        ed = lambda sid_, path, tool="Edit": run("edit", {"session_id": sid_, "cwd": str(A), "tool_name": tool, "tool_input": {"file_path": str(path)}}, led2)
        RCM = "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → moved thing\n- `check` → ok\n- `open` → owner decision: keep the banner wide or narrow"
        run("start", {"session_id": "mv1", "cwd": str(A)}, led2)
        run("receipt", {"session_id": "mv1", "cwd": str(A), "stop_hook_active": False, "last_assistant_message": RCM}, led2)
        memA, memB = W.memo_path(str(A)), W.memo_path(str(B))
        check("work: a receipt lands in the launch folder before any edit", memA.exists() and "(s:mv1 p:A)" in memA.read_text() and W.session_memo_path(str(A), "mv1").exists())
        check("work: the memo headers carry the project path", f"Project: {os.path.realpath(A)}" in memA.read_text() and f"Project: {os.path.realpath(A)}" in W.session_memo_path(str(A), "mv1").read_text())
        ed("mv1", A / "x.txt")
        ed("mv1", B / "b1.py"); ed("mv1", B / "b2.py", "Write")
        check("work: edits are counted per repository (1 in the launch folder, 2 in B), under 3 nothing moves",
              wst("mv1").get("work", {}).get(os.path.realpath(A)) == 1 and wst("mv1").get("work", {}).get(os.path.realpath(B)) == 2 and not wst("mv1").get("project") and memA.exists() and not memB.exists())
        ed("mv1", B / "b3.py")
        check("work: 3 edits in B against 1 in A (a lead of 2) do not move the memo", not wst("mv1").get("project") and not memB.exists())
        ed("mv1", B / "b4.py")
        sB = W.session_memo_path(str(B), "mv1")
        check("work: the fourth edit in B (a lead of 3 over A, 80% of the edits) moves the session's memo to B", wst("mv1").get("project") == os.path.realpath(B) and sB.exists() and memB.exists())
        check("work: the session file moved with its Now, Journal and a 'moved here' trace line",
              "moved thing" in sB.read_text() and "moved here from A: most edits of this session land in this repository" in sB.read_text() and not W.session_memo_path(str(A), "mv1").exists())
        check("work: its Needs-you item moved and was renumbered, keeping its p:A tag (never retagged); A's index line is gone", "1. [ ]" in W.memo_read(str(B))["Needs you"] and "(s:mv1 p:A)" in memB.read_text()
              and "(s:mv1)" not in memA.read_text() and not any(k == "mv1" for k, _ in W.index_entries(W.memo_read(str(A)))) and any(k == "mv1" for k, _ in W.index_entries(W.memo_read(str(B)))))
        check("work: a trace line is not a receipt", W.index_field([l for k, l in W.index_entries(W.memo_read(str(B))) if k == "mv1"][0], "receipts") == "1")
        run("receipt", {"session_id": "mv1", "cwd": str(A), "stop_hook_active": False, "last_assistant_message": RCM.replace("moved thing", "second thing").replace("keep the banner wide or narrow", "pick the footer colour")}, led2)
        check("work: a later receipt (hook cwd still A) journals into B", "second thing" in sB.read_text() and "second thing" not in memA.read_text())
        check("work: its new item is tagged with the repository the edits land in (B), not the folder the hook ran in", "pick the footer colour #" in memB.read_text() and "(s:mv1 p:B)" in memB.read_text() and "pick the footer colour" not in memA.read_text())
        check("work: scripts find the session from the work root too", (W.newest_state_path(str(B)) or Path("x")).stem == "mv1" and (W.newest_state_path(str(A)) or Path("x")).stem == "mv1")
        check("work: registry lists both projects", set(map(os.path.realpath, W.registry_read())) >= {os.path.realpath(A), os.path.realpath(B)})
        # edits under the launch root (nested repo, worktree of it) and in a worktree of B count for A / B, never a third project
        nested = A / "nested"; nested.mkdir(); subprocess.run(["git", "init", "-q"], cwd=nested, capture_output=True)
        run("start", {"session_id": "mv2", "cwd": str(A)}, led2)
        ed("mv2", nested / "n.txt"); ed("mv2", nested / "n2.txt"); ed("mv2", nested / "n3.txt"); ed("mv2", nested / "n4.txt")
        check("work: a nested repo inside the launch folder counts as the launch folder", wst("mv2").get("work", {}).get(os.path.realpath(A)) == 4 and not wst("mv2").get("project"))
        if gitok and subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"], cwd=C, capture_output=True).returncode == 0 \
                and subprocess.run(["git", "worktree", "add", "-q", str(Path(wh, "Cwt"))], cwd=C, capture_output=True).returncode == 0:
            check("work: a file in a worktree maps to its main repository", W.repo_top(str(Path(wh, "Cwt", "f.txt"))) == os.path.realpath(C))
        check("work: a file in no repository counts for nothing", W.work_key({"root": str(A)}, str(D / "d.txt")) is None and W.repo_top(str(D / "d.txt")) is None)
        # a tie never moves; a majority elsewhere does, and the memo can move again when the count turns
        run("start", {"session_id": "mv3", "cwd": str(B)}, led2)
        for n in range(3):
            run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(C / f"c{n}.py")}}, led2)
            run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(B / f"b{n}.py")}}, led2)
        check("work: a tie keeps the launch folder", not wst("mv3").get("project"))
        run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(C / "c9.py")}}, led2)
        check("work: one more edit in C (4 vs 3, a lead of 1) does not move the project", not wst("mv3").get("project"))
        for n in (10, 11):
            run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(C / f"c{n}.py")}}, led2)
        check("work: a lead of 3 in C (6 vs 3, 67% of the edits) moves the project to C", wst("mv3").get("project") == os.path.realpath(C))
        for n in range(5):
            run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(B / f"bb{n}.py")}}, led2)
        check("work: the launch folder leading by less than the margin (8 vs 6) does not move it back (no flip-flop)", wst("mv3").get("project") == os.path.realpath(C))
        run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(B / "bb9.py")}}, led2)
        check("work: back to the launch folder with the same margin (9 vs 6, a lead of 3 and 60% of the edits)", not wst("mv3").get("project"))
        check("work: a hook with no file_path or an unknown tool changes nothing", run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Edit", "tool_input": {}}, led2) is None
              and run("edit", {"session_id": "mv3", "cwd": str(B), "tool_name": "Read", "tool_input": {"file_path": str(C / "z.py")}}, led2) is None and wst("mv3")["work"].get(os.path.realpath(C)) == 6)
        run("start", {"session_id": "mv3", "cwd": str(B), "source": "clear"}, led2)
        check("work: /clear or a new session starts the count again", "work" not in wst("mv3") and "project" not in wst("mv3"))
        # Bash git writes with a clear target repository count like edits there
        run("start", {"session_id": "bw1", "cwd": str(A)}, led2)
        gb = lambda sid_, cmd: run("guard", {"session_id": sid_, "cwd": str(A), "tool_name": "Bash", "tool_input": {"command": cmd}}, led2)
        check("work: bash_write_targets reads `git -C <path> <write>` and `cd <path> && git <write>`, nothing else",
              W.bash_write_targets(f"git -C {C} commit -m x", str(A)) == [str(C)] and W.bash_write_targets(f"cd {C} && git add . && git commit -m 'git -C /x push'", str(A)) == [str(C)]
              and W.bash_write_targets("git commit -m x", str(A)) == [] and W.bash_write_targets(f"git -C {C} status", str(A)) == [] and W.bash_write_targets("gh -R o/r pr create", str(A)) == []
              and W.bash_write_targets("git -C $REPO commit -m x", str(A)) == [] and W.bash_write_targets(f"git -C {Path(wh, 'nope')} commit", str(A)) == [] and W.bash_write_targets("git -C C commit -m x", wh) == [os.path.join(wh, "C")])
        gb("bw1", f"git -C {C} add a.py"); gb("bw1", f"cd {C} && git commit -m one")
        check("work: two Bash git writes into C are counted for C, the memo stays below 3", wst("bw1").get("work", {}).get(os.path.realpath(C)) == 2 and not wst("bw1").get("project"))
        gb("bw1", "git commit -m 'no path: the shell may be anywhere'"); gb("bw1", "gh -R o/r pr create --fill")
        check("work: a git write with no path and `gh -R` count for nothing", wst("bw1").get("work", {}).get(os.path.realpath(C)) == 2 and len(wst("bw1").get("work", {})) == 1)
        gb("bw1", f"git -C {C} push origin main")
        check("work: the third Bash git write into C moves the memo there", wst("bw1").get("project") == os.path.realpath(C))
        run("start", {"session_id": "bw2", "cwd": str(A)}, led2)
        ed("bw2", C / "new" / "deep" / "f.py", "Write")
        check("work: a Write of a new file in a new folder of C counts for C's repository root", wst("bw2").get("work", {}).get(os.path.realpath(C)) == 1)
        # scopes: session / project (default) / all; a session with nothing of its own sees a hint line
        run("start", {"session_id": "oth1", "cwd": str(B)}, led2)
        run("receipt", {"session_id": "oth1", "cwd": str(B), "stop_hook_active": False, "last_assistant_message": RCM.replace("moved thing", "other thing").replace("keep the banner wide or narrow", "ship the footer today or next week")}, led2)
        Path(B, "CLAUDE.md").write_text("## abofles overrides\n- Other items = show\n")  # these scope checks list other sessions' items (the default now hides them)
        rb_p = W.resume_block(str(B), session_id="mv1")
        rb_s = W.resume_block(str(B), session_id="mv1", scope="session")
        check("scope: project (default) shows every open item of the project (mv1's p:A item as information)", "3 open" in rb_p and "other session oth1 · B · " in rb_p and "(s:oth1" not in rb_p and "(s:mv1 p:B)" in rb_p and "· scope" not in rb_p)
        check("scope: session shows only this session's items and says so in the header", "· scope session" in rb_s and "2 open" in rb_s and "oth1" not in rb_s and "(s:mv1 p:B)" in rb_s)
        rb_n = W.resume_block(str(B), session_id="newS1")
        check("scope: a session with nothing of its own gets one hint line instead of the other sessions' lines",
              "- `project` → B: 2 other sessions, newest " in rb_n and "their work, not yours; `/abofles:resume --scope project` shows them" in rb_n and "not this one" not in rb_n and "- `now` →" not in rb_n)
        rb_nx = W.resume_block(str(B), session_id="newS1", scope="project", explicit=True)
        check("scope: --scope project (explicit) shows the other sessions in full", "not this one" in rb_nx and "- `project` →" not in rb_nx)
        check("scope: session for a session with nothing of its own shows none", "- `needs you` → none of this session" in W.resume_block(str(B), session_id="newS1", scope="session"))
        W.memo_needs(str(A), "zz", "owner decision: A keeps this one")
        rb_a = W.resume_block(str(B), session_id="mv1", scope="all")
        check("scope: all adds one `elsewhere` line per other project with its open count", "· scope all" in rb_a and "- `elsewhere` →" in rb_a and re.search(r"^  - A · 1 open: owner decision: A keeps this one$", rb_a, re.M) and "  - B" not in rb_a and "  - C" not in rb_a)
        check("scope: precedence built-in < saved default < override < flag",
              W.resume_scope(str(B)) == ("project", "built-in") and W.config_write({"resume_scope": "all"}) and W.resume_scope(str(B)) == ("all", "default")
              and (Path(B, "CLAUDE.md").write_text("## abofles overrides\n- Resume = session\n- Other items = show\n") or True) and W.resume_scope(str(B)) == ("session", "override")
              and W.resume_scope(str(B), "project") == ("project", "flag") and W.resume_scope(str(B), "bogus") == ("session", "override"))
        check("scope: the override drives the default block", "· scope session" in W.resume_block(str(B), session_id="mv1"))
        Path(B, "CLAUDE.md").write_text("## abofles overrides\n- Other items = show\n"); W.config_write({})
        st1 = wst("mv1")
        gi = lambda sc: " | ".join(t for _, t in W.guide_items(str(B), st1, "mv1", scope=sc))
        check("scope: the guide counts owner decisions by scope", "2 owner decisions open" in gi("session") and "of other sessions, not yours" not in gi("session") and "2 owner decisions open" in gi("project") and "(+1 of other sessions, not yours)" in gi("project") and "(+1 of other sessions, not yours)" in gi("all")
              and "open in other project: A (1)" in gi("all") and "other project" not in gi("project"))
        # memo.py: --scope, scope <value>, and no write without a session state
        sdir2 = Path(__file__).resolve().parent
        env2 = dict(os.environ, ABOFLES_LEDGER_DIR=led2, ABOFLES_SESSION="mv1")
        mp = lambda *a, cwd=B, env=env2: subprocess.run([sys.executable, str(sdir2 / "memo.py"), *a], cwd=cwd, capture_output=True, text=True, env=env)
        check("memo.py: show --scope session prints the session-scoped block", "· scope session" in mp("show", "--scope", "session").stdout)
        check("memo.py: show --scope all prints the elsewhere lines with each project's first open item", re.search(r"^  - A · 1 open: owner decision: A keeps this one$", mp("show", "--scope", "all").stdout, re.M) is not None)
        rs, rp, ra = (mp("show", "--scope", x).stdout for x in ("session", "project", "all"))
        check("memo.py: the guide line of the block follows the scope too (session: own decision only; project: both; all: other projects counted)",
              "🟠 2 owner decisions open" in rs.split("- `guide`")[1] and "oth1" not in rs and "🟠 2 owner decisions open" in rp.split("- `guide`")[1] and "(+1 of other sessions, not yours)" in rp.split("- `guide`")[1] and "open in other project: A (1)" in ra.split("- `guide`")[1] and "other project" not in rp)
        check("memo.py: an unknown --scope exits 2", mp("show", "--scope", "bogus").returncode == 2)
        r = mp("scope", "all")
        check("memo.py: scope <value> saves the user default", r.returncode == 0 and r.stdout.strip() == "saved as the default · scope: all (from the saved default (" + str(Path(wh, "config.json")) + "))" and json.loads(Path(wh, "config.json").read_text())["resume_scope"] == "all")
        check("memo.py: scope alone prints the scope in force and its source", mp("scope").stdout.startswith("scope: all (from the saved default"))
        check("memo.py: the saved default drives show", "· scope all" in mp("show").stdout and "· scope" not in mp("show", "--scope", "project").stdout)
        W.config_write({})
        env3 = dict(os.environ, ABOFLES_LEDGER_DIR=led2); env3.pop("ABOFLES_SESSION", None)
        r = mp("resolve", "1", "x", cwd=D, env=env3)
        check("memo.py: a write with no session state, no --session and no memo in the folder exits 4 and writes nothing", r.returncode == 4 and "nothing written" in r.stdout and not Path(D, ".claude").exists())
        check("memo.py: naming a session allows the write (two sessions in one root)", mp("note", "named", "--session", "d1", cwd=D, env=env3).returncode == 0 and Path(D, ".claude", "abofles-memo.md").exists())
        import shutil; shutil.rmtree(Path(D, ".claude"))
        r = mp("now", cwd=D, env=env3)
        check("memo.py: now with no session state exits 4 too", r.returncode == 4 and not Path(D, ".claude").exists())
        check("memo.py: show with no session state still reads", mp("show", cwd=D, env=env3).returncode == 0)
        check("memo.py: a write from the launch folder goes to the work root", mp("note", "settled in chat", cwd=A).returncode == 0 and "settled in chat" in memB.read_text() and "settled in chat" not in memA.read_text())
        # memo.py move <root>: a misfiled session moved by hand (oth1 from B to D), state follows
        check("memo.py: move to a folder that is no repository top (`/`, a plain folder, a repo's subfolder) exits 2 and moves nothing",
              mp("move", "/", "--session", "oth1", cwd=B, env=env3).returncode == 2 and mp("move", str(D), "--session", "oth1", cwd=B, env=env3).returncode == 2
              and (not gitok or (Path(B, "sub9").mkdir(exist_ok=True) or mp("move", str(Path(B, "sub9")), "--session", "oth1", cwd=B, env=env3).returncode == 2)) and not W.session_memo_path(str(D), "oth1").exists())
        subprocess.run(["git", "init", "-q"], cwd=D, capture_output=True)
        r = mp("move", str(D), "--session", "oth1", cwd=B, env=env3)
        check("memo.py: move <root> moves the session's memo and sets its project", r.returncode == 0 and r.stdout.startswith("moved: session oth1 → ") and W.session_memo_path(str(D), "oth1").exists()
              and "other thing" in W.session_memo_path(str(D), "oth1").read_text() and "moved here from B" in W.session_memo_path(str(D), "oth1").read_text()
              and not W.session_memo_path(str(B), "oth1").exists() and "(s:oth1" not in memB.read_text() and "(s:oth1 p:B)" in W.memo_path(str(D)).read_text() and wst("oth1").get("project") == os.path.realpath(D))
        check("memo.py: move to the same folder exits 3, a missing folder exits 2", mp("move", str(D), "--session", "oth1", cwd=B, env=env3).returncode == 3 and mp("move", str(Path(wh, "nope")), "--session", "oth1", cwd=B, env=env3).returncode == 2)
        check("memo.py: after the move, oth1's own items are gone from B and the guide of B counts only mv1's two", "2 owner decisions open" in gi("project") and "of other sessions, not yours" not in gi("project"))
        check("memo.py: a hand move pins the memo (project_pinned = the folder)", wst("oth1").get("project_pinned") == os.path.realpath(D))
        for n in range(5):
            run("edit", {"session_id": "oth1", "cwd": str(B), "tool_name": "Edit", "tool_input": {"file_path": str(B / f"pin{n}.py")}}, led2)
        check("memo.py: after a hand move to D, 5 edits in the launch folder B do not move the memo back", wst("oth1").get("project") == os.path.realpath(D)
              and W.session_memo_path(str(D), "oth1").exists() and not W.session_memo_path(str(B), "oth1").exists() and "moved here from D" not in (W.session_memo_path(str(B), "oth1").read_text() if W.session_memo_path(str(B), "oth1").exists() else ""))
        run("start", {"session_id": "oth1", "cwd": str(B), "source": "clear"}, led2)
        check("memo.py: the pin survives a /clear of the same session", wst("oth1").get("project") == os.path.realpath(D) and wst("oth1").get("project_pinned") == os.path.realpath(D))
        run("start", {"session_id": "oth1", "cwd": str(D), "source": "startup"}, led2)
        check("memo.py: a start in the pinned folder itself keeps the pin, with no separate project", not wst("oth1").get("project") and wst("oth1").get("project_pinned") == os.path.realpath(D))
        run("start", {"session_id": "oth1", "cwd": str(B), "source": "startup"}, led2)
        import shutil; shutil.rmtree(Path(D, ".claude"))
        gp = lambda *a: subprocess.run([sys.executable, str(sdir2 / "guide.py"), *a], cwd=A, capture_output=True, text=True, env=env2)
        check("guide.py: --scope all names the other project's open decisions", "open in other project: A (1)" in gp("--scope", "all").stdout and gp("--scope", "bogus").returncode == 2)
        # memo_move: the session's open items go to the new memo and leave the old one; its resolved items stay behind
        X_, Y_ = Path(wh, "X"), Path(wh, "Y")
        X_.mkdir(); Y_.mkdir()
        W.memo_needs(str(X_), "mvo1xxxx", "owner decision: open one?"); W.memo_needs(str(X_), "mvo1xxxx", "owner decision: settled one?"); W.memo_needs(str(X_), "othr2xxx", "owner decision: theirs?")
        W.memo_resolve(str(X_), 2, "done", "mvo1xxxx")
        W.memo_move(str(X_), str(Y_), "mvo1xxxx", "moved by hand (memo.py move)")
        nx_, ny_ = W.memo_read(str(X_))["Needs you"], W.memo_read(str(Y_))["Needs you"]
        check("memo move: the open item of the session moved (keeping its p:X tag, never retagged), the old memo lost it; the resolved one and another session's stay",
              "open one?" in ny_ and "(s:mvo1xxxx p:X)" in ny_ and "open one?" not in nx_ and "settled one?" in nx_ and "settled one?" not in ny_ and "theirs?" in nx_ and "theirs?" not in ny_)
        os.environ["ABOFLES_STATE_DIR"] = state

    # scripts: project-scoped ledger, the user's --session wins, a trailing empty --session is ignored
    sdir = Path(__file__).resolve().parent
    sid_ = "11111111-2222-3333-4444-555555555555"
    with tempfile.TemporaryDirectory() as xh, tempfile.TemporaryDirectory() as xp, tempfile.TemporaryDirectory() as xo:
        xenv = dict(os.environ, HOME=xh, ABOFLES_LEDGER_DIR=str(Path(xh, "led")), ABOFLES_STATE_DIR=str(Path(xh, "st")))
        xenv.pop("ABOFLES_SESSION", None)
        Path(xh, "led").mkdir(); Path(xh, "st").mkdir()
        sc_ = lambda name, *a: subprocess.run([sys.executable, str(sdir / name), *a], cwd=xp, capture_output=True, text=True, env=xenv)
        # another project's newest ledger must never be shown or billed
        Path(xh, "st", "otherS.json").write_text(json.dumps({"root": os.path.realpath(xo), "last_prompt_ts": 9}))
        Path(xh, "led", "otherS.jsonl").write_text('{"agent_id": "zzz999", "topic": "OtherProjectTopic", "status": "partial", "model": "opus", "effort": "high"}\n')
        for name in ("status.py", "bill.py"):
            r = sc_(name, "--baseline", "opus")
            check(f"scope: {name} shows nothing of another project's ledger", r.returncode == 0 and "OtherProjectTopic" not in r.stdout and "zzz999" not in r.stdout and "no ledger for this session yet" in r.stdout)
            r = sc_(name, "--baseline", "opus", "--session", sid_)
            check(f"scope: {name} with an unknown --session shows nothing of another project's ledger", r.returncode == 0 and "OtherProjectTopic" not in r.stdout)
        check("scope: status --resolve cannot reach another project's row", sc_("status.py", "--resolve", "zzz9", "--session", sid_, "--trace", "re-checked: t").returncode == 1 and "resolved" not in Path(xh, "led", "otherS.jsonl").read_text())
        # this project's session: display and --resolve use the same ledger, with the appended session id
        Path(xh, "st", f"{sid_}.json").write_text(json.dumps({"root": os.path.realpath(xp), "last_prompt_ts": 1}))
        Path(xh, "led", f"{sid_}.jsonl").write_text('{"agent_id": "abc123", "topic": "MineTopic", "status": "partial", "model": "opus", "effort": "high"}\n')
        r = sc_("status.py", "--baseline", "opus", "--session", sid_)
        check("scope: status shows this project's own ledger row", "MineTopic" in r.stdout and "abc123" in r.stdout and "OtherProjectTopic" not in r.stdout)
        r = sc_("status.py", "--resolve", "abc1", "--session", sid_, "--trace", "re-checked: t")
        check("scope: status --resolve <id> --session <uuid> settles the row the display shows", r.returncode == 0 and json.loads(Path(xh, "led", f"{sid_}.jsonl").read_text())["resolved"] is True)
        # memo.py: the user's own --session wins over the command's appended one
        subprocess.run([sys.executable, "-c", "import sys,os; sys.path.insert(0, sys.argv[1]); import abofles_hook as h; h.memo_needs(os.getcwd(), 'sessAAAA', 'owner decision: pick A or B')", str(sdir.parent / "hooks")], cwd=xp, env=xenv)
        r = sc_("memo.py", "resolve", "1", "re-checked: t", "--trace", "--force", "--session", "mysessX", "--session", sid_)  # the item is sessAAAA's: --force
        memx = lambda s: Path(xp, ".claude", "abofles-memo.d", f"{s[:8]}.md")
        check("session: memo resolve with the user's --session X plus an appended UUID traces into X's file", r.returncode == 0 and "· resolved by hand: owner decision 1" in memx("mysessX").read_text() and "owner decision" not in (memx(sid_).read_text() if memx(sid_).exists() else ""))
        # an empty trailing --session "" (unexpanded command variable) is ignored
        for name, args in (("agents.py", ()), ("status.py", ("--baseline", "opus")), ("bill.py", ("--baseline", "opus")), ("guide.py", ()), ("memo.py", ("show",))):
            r = sc_(name, *args, "--session", "")
            check(f"session: {name} ignores a trailing empty --session", r.returncode == 0 and "usage" not in r.stdout.lower())
        check("session: status --resolve with an empty id plus a trailing empty --session still exits 2", sc_("status.py", "--resolve", "", "--session", "").returncode == 2)
        check("session: a non-trailing empty --session value still exits 2", sc_("status.py", "--resolve", "abc", "--session", "", "--trace").returncode == 2)

    # ---- topics: 27 domains, 1,100+ topics looked up by scripts/topic.py, four new reviewers, suggestions
    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state:
        os.environ["ABOFLES_STATE_DIR"] = state
        os.environ["ABOFLES_TEST_CWD"] = cwd
        os.environ["ABOFLES_USER_CLAUDE_MD"] = str(Path(state, "no-user-claude.md"))
        sdir = Path(__file__).resolve().parent
        sys.path.insert(0, str(sdir))
        import topics_data as TD
        import importlib.util as _iut
        _hst = _iut.spec_from_file_location("abofles_hook_tp", str(HOOK)); HT = _iut.module_from_spec(_hst); _hst.loader.exec_module(HT)
        wf = HT.workflow_table()
        names = [l.split("|")[0].strip() for _, _, _, _, areas in TD.DOMAINS for _, rows in areas for l in rows]
        check("topics: 27 domains and 1,000+ topics", len(TD.DOMAINS) == 27 and len(names) >= 1000 and len(wf.get("topics", {})) == len(names))
        check("topics: names unique", len(set(names)) == len(names))
        legacy = ["Financial & Amount Calculations", "Codebase Exploration & Onboarding", "Notifications & Messaging", "Data Privacy, PII & Compliance", "Audit Logging & Traceability", "Unit Test", "Payments & Gateway Integration", "Clean Code (naming, structure, readability)", "Work on Document (README, guides)", "Check MVP vs Advance", "Architecture, Design Pattern & Stack Used"]
        check("topics: the legacy names used by hook, tests and docs are kept", all(n in wf["topics"] for n in legacy))
        check("topics: every topic has 3+ signals and a valid tier", all(len([x for x in (t.get("signals") or "").split(",") if x.strip()]) >= 3 and t.get("tier") in (1, 2, 3, 4) for t in wf["topics"].values()))
        rk = set(wf["reviewers"])
        check("topics: 11 reviewers and every domain/topic reviewer key is one of them", len(rk) == 11 and {"dba", "ops", "perf", "data"} <= rk
              and all(k in rk for g in wf["groups"].values() for t in ("3", "4") for k in g["reviewers"][t]) and all(k in rk for t in wf["topics"].values() for tt in ("3", "4") for k in t["reviewers"][tt]))
        check("topics: an agent file exists for every reviewer", all((sdir.parent / "agents" / f"{wf['reviewers'][k]['agent']}.md").exists() for k in rk))
        check("topics: DBA reviews databases, Ops the cloud, Perf the cache, Data the warehouse",
              wf["groups"]["DB"]["reviewers"]["3"] == ["dba"] and wf["groups"]["AWS"]["reviewers"]["3"] == ["ops"] and wf["groups"]["MSG"]["reviewers"]["3"] == ["perf"] and wf["groups"]["DATA"]["reviewers"]["3"] == ["data"])
        check("topics: PII topics still add Compliance; the legacy T4 money topic keeps its Fable Analyse", "compliance" in wf["topics"]["Data Privacy, PII & Compliance"]["reviewers"]["4"] and wf["topics"]["Financial & Amount Calculations"].get("ov", {}).get("Analyse") == "fable·medium")
        check("topics: workflow.json carries tiers, caps and suggestions", wf["tiers"]["4"]["Plan"] == "opus·xhigh" and wf["caps"]["1"]["Read"] == 20 and "dynamicwhere" in wf.get("suggestions", {}) and "dynamicwhere" in wf["topics"]["Dynamic Filtering, Sorting & Paging from Client Input"].get("suggest", []))
        check("topics: every suggestion names existing topics", all(t in wf["topics"] for sg in TD.SUGGESTIONS for t in sg["topics"]))
        # hook lookup
        m = HT.topic_matches("postgres read replica lag", wf, 3)
        check("lookup: replica lag ask -> a Replication topic first", m and "Replica" in m[0][1])
        m = HT.topic_matches("where is the discount applied", wf, 3)
        check("lookup: a where-is ask -> Codebase Exploration first (T1)", m and m[0][1] == "Codebase Exploration & Onboarding")
        check("lookup: cloudflare workers -> the Cloudflare Workers topic", "Cloudflare Workers" in HT.topic_matches("cloudflare workers kv", wf, 1)[0][1])
        check("lookup: otp expiry -> an OTP topic in SEC", (lambda r: r and "OTP" in r[0][1] and r[0][2]["group"] == "SEC")(HT.topic_matches("otp code expiry", wf, 1)))
        check("lookup: gibberish -> no match", HT.topic_matches("zzqx vvbbnn", wf, 3) == [])
        check("lookup: cells = tier base unless the topic overrides", HT.topic_cells(wf["topics"]["Unit Test"], 2, wf)["Work"] == ("opus·medium", 20, "base") and HT.topic_cells(wf["topics"]["Financial & Amount Calculations"], 4, wf)["Analyse"] == ("fable·medium", 50, "topic"))
        sk = wf["topics"]["Secrets, Keys & Config Security"]
        check("matrix quality (default): Opus writes at every tier, Sonnet/Haiku verify, the specialist reviewers keep Opus at T4",
              [wf["tiers"][t]["Work"] for t in "1234"] == ["opus·low", "opus·medium", "opus·high", "opus·xhigh"] and [wf["tiers"][t]["Verify"] for t in "1234"] == ["haiku·low", "sonnet·low", "sonnet·high", "sonnet·xhigh"]
              and HT.verify_cell(4) == ("opus", "high") and all(wf["tiers"][t][x].startswith(("opus", "fable")) for t in "1234" for x in ("Analyse", "Plan", "Work")))
        check("matrix quality: a topic override below Opus on Work is skipped, one up (Fable Plan) stays",
              HT.topic_cells(sk, 4, wf)["Work"] == ("opus·xhigh", 40, "base") and HT.topic_cells(wf["topics"]["Architecture, Design Pattern & Stack Used"], 4, wf)["Plan"][0] == "fable·medium")
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Matrix = economy  # cheaper\n")
        check("matrix economy: the cells before 2.6 and every topic override apply",
              HT.matrix_mode(cwd) == "economy" and HT.topic_cells(wf["topics"]["Unit Test"], 2, wf, cwd)["Work"] == ("sonnet·medium", 20, "base") and HT.topic_cells(sk, 4, wf, cwd)["Work"] == ("sonnet·high", 40, "topic")
              and HT.topic_cells(wf["topics"]["Unit Test"], 4, wf, cwd)["Verify"][0] == "opus·high")
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Matrix = fastest\n")
        check("matrix: an unknown value reads as quality", HT.matrix_mode(cwd) == "quality" and HT.topic_cells(wf["topics"]["Unit Test"], 2, wf, cwd)["Work"][0] == "opus·medium")
        Path(cwd, "CLAUDE.md").unlink()
        check("matrix: the Git cell is the economy Work cell in both", HT.topic_cells(wf["topics"]["Unit Test"], 3, wf, None, economy=True)["Work"][0] == "sonnet·high")
        # topic.py CLI
        tp = lambda *a, **k: subprocess.run([sys.executable, str(sdir / "topic.py"), *a], capture_output=True, text=True, cwd=k.get("cwd", cwd))
        r = tp("find", "postgres read replica lag")
        check("topic.py: find prints matches with tier, cells, reviewers and workflow", r.returncode == 0 and "🔎 topic lookup" in r.stdout and "Replica" in r.stdout and "reviewers T3 DBA" in r.stdout and "Read " in r.stdout and " · Git/Scrum " in r.stdout and "change →" in r.stdout and len(r.stdout.splitlines()) <= 8)
        r = tp("find", "add dynamic filter grid to orders api c#")
        check("topic.py: a C# dynamic-filter ask suggests DynamicWhere.ex", "💡 suggest · DynamicWhere.ex" in r.stdout and "dotnet add package DynamicWhere.ex" in r.stdout)
        r = tp("find", "add dynamic filter grid to orders api")
        check("topic.py: no .NET in the ask or the tree -> no suggestion", "💡" not in r.stdout)
        Path(cwd, "App.csproj").write_text("<Project/>")
        r = tp("find", "add dynamic filter grid to orders api")
        check("topic.py: a .csproj in the tree -> stack dotnet and the suggestion", "stack dotnet" in r.stdout and "💡 suggest · DynamicWhere.ex" in r.stdout)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Topic: Unit Test = T4\n- Unit Test / Work = haiku·low\n- Reviewers: TEST = qa\n")
        r = tp("show", "Unit Test")
        check("topic.py: show applies the project's tier, cell and reviewer overrides and marks them", "🔴 Critical (T4) · Project Override" in r.stdout and "Work haiku·low" in r.stdout and "Project Override" in r.stdout and "T3 QA" in r.stdout and "header: abofles: Unit Test / <Side> · T4" in r.stdout)
        Path(cwd, "CLAUDE.md").unlink()
        r = tp("show", "#1")
        check("topic.py: show by number", r.returncode == 0 and "#1 " in r.stdout and "signals:" in r.stdout)
        r = tp("domains")
        check("topic.py: domains lists 27 rows", r.returncode == 0 and len([l for l in r.stdout.splitlines() if l.startswith("| ") and not l.startswith("| Code")]) == 27)
        r = tp("list", "DB")
        check("topic.py: list DB prints its areas and topics", r.returncode == 0 and "PostgreSQL" in r.stdout and "Replication & Availability:" in r.stdout)
        check("topic.py: list of an unknown code exits 1", tp("list", "NOPE").returncode == 1)
        check("topic.py: no-match find says so and exits 0", (lambda r: r.returncode == 0 and "no match" in r.stdout)(tp("find", "zzqx vvbbnn")))
        # hook notes in pre(): header tags, unknown topic, over-cell, escalation, guard neutrality
        check("parse_header: the new reviewer tags parse", HT.parse_header("abofles: PostgreSQL / DBA · T3")[1] == "DBA" and HT.parse_header("abofles: X / Ops · T4")[1] == "Ops" and HT.parse_header("abofles: X / Perf · T3")[1] == "Perf" and HT.parse_header("abofles: X / Data · T3")[1] == "Data")
        run("start", {"session_id": "tp", "cwd": cwd}, led)
        json.dump(dict(json.loads(Path(state, "tp.json").read_text()), gate="auto"), open(Path(state, "tp.json"), "w"))
        out = run("pre", dict(agent("haiku", "low", prompt="abofles: Postgres Replica Thing / Read · T3\nq\nLine cap: 40", cwd=cwd), session_id="tp"), led)
        check("pre: a header topic not in the index -> advisory note with the nearest names", "not in the index — nearest:" in ctx(out) and "Replica" in ctx(out) and not reason(out) and "permissionDecision" not in json.dumps(out))
        json.dump(dict(json.loads(Path(state, "tp.json").read_text()), inflight=0), open(Path(state, "tp.json"), "w"))
        out = run("pre", dict(agent("opus", "high", prompt="abofles: Codebase Exploration & Onboarding / Read · T1\nq\nLine cap: 40", cwd=cwd), session_id="tp"), led)
        check("pre: first start above the cell's model -> '× the cell' note, still allowed", "× the cell" in ctx(out) and "haiku·low" in ctx(out) and not reason(out) and "permissionDecision" not in json.dumps(out))
        json.dump(dict(json.loads(Path(state, "tp.json").read_text()), inflight=0), open(Path(state, "tp.json"), "w"))
        out = run("pre", dict(agent("haiku", "low", prompt="abofles: Codebase Exploration & Onboarding / Read · T1\nq\nLine cap: 40", cwd=cwd), session_id="tp"), led)
        check("pre: a start at the cell -> no cell note", "× the cell" not in ctx(out))
        run("post", dict(agent("haiku", "low", prompt="abofles: Unit Test / Analyse · T2\nq\nLine cap: 20", resp="STATUS: done\nCONFIDENCE: low\nFINDINGS:\n- x\nRECOMMENDATION: y", cwd=cwd), session_id="tp"), led)
        json.dump(dict(json.loads(Path(state, "tp.json").read_text()), inflight=0), open(Path(state, "tp.json"), "w"))
        out = run("pre", dict(agent("opus", "medium", prompt="abofles: Unit Test / Analyse · T2\nq\nLine cap: 20", cwd=cwd), session_id="tp"), led)
        check("pre: a re-run of the same topic and side (escalation) -> no cell note", "× the cell" not in ctx(out))
        g = lambda: run("guard", {"session_id": "tp", "cwd": cwd, "tool_name": "Grep", "tool_input": {}}, led)
        json.dump(dict(json.loads(Path(state, "tp.json").read_text()), direct_reads=0, guard_warned_at=0), open(Path(state, "tp.json"), "w"))
        g(); g(); g()
        out = run("guard", {"session_id": "tp", "cwd": cwd, "tool_name": "Bash", "tool_input": {"command": f'python3 "{sdir / "topic.py"}" find "replica lag"'}}, led)
        check("guard: the topic lookup is silent and neither counts nor breaks the streak", out is None and json.loads(Path(state, "tp.json").read_text()).get("direct_reads") == 3 and "direct reads in a row" in ctx(g()))
        st_out = run("start", {"session_id": "tp2", "cwd": cwd}, led)
        check("start: SessionStart names the topic lookup command", "abofles: topic lookup = bash" in (st_out or "") and "scripts/py" in (st_out or "") and "topic.py" in (st_out or ""))
        check("skill: Step 2 tells the model to look the topic up, not read a list", "scripts/topic.py" in (sdir.parent / "skills/abofles/SKILL.md").read_text() and "100 topics" not in (sdir.parent / "skills/abofles/SKILL.md").read_text())

    # ---- Windows: no fcntl. A stub msvcrt (backed by flock) stands in; the hooks must load, lock and answer as on Unix.
    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as shim:
        Path(shim, "sitecustomize.py").write_text(
            "import sys, fcntl as _f\n"
            "class _M:\n"
            "    LK_NBLCK = 2; LK_UNLCK = 0; LK_LOCK = 1\n"
            "    calls = []\n"
            "    @staticmethod\n"
            "    def locking(fd, mode, n):\n"
            "        _M.calls.append(mode)\n"
            "        _f.lockf(fd, _f.LOCK_UN if mode == 0 else _f.LOCK_EX | _f.LOCK_NB, n, 0)\n"
            "sys.modules['msvcrt'] = _M\n"
            "sys.modules['fcntl'] = None\n"
            "import atexit, os\n"
            "atexit.register(lambda: open(os.environ['WIN_LOG'], 'a').write(','.join(map(str, _M.calls)) + '\\n'))\n")
        wlog = Path(shim, "calls.log")
        wenv = dict(os.environ, PYTHONPATH=shim, WIN_LOG=str(wlog), ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state, ABOFLES_USER_CLAUDE_MD=str(Path(state, "none.md")))
        wrun = lambda mode, payload: subprocess.run([sys.executable, str(HOOK.parent / "run.py"), mode], input=json.dumps(payload), capture_output=True, text=True, env=wenv)
        r = wrun("start", {"session_id": "win", "cwd": cwd, "source": "startup"})
        check("windows: hooks load without fcntl (SessionStart runs, prints the lookup line)", r.returncode == 0 and "topic lookup" in r.stdout and "Traceback" not in r.stderr)
        check("windows: state written under the msvcrt lock", Path(state, "win.json").exists() and "2" in wlog.read_text() and "0" in wlog.read_text())
        rr = [wrun("guard", {"session_id": "win", "cwd": cwd, "tool_name": "Grep", "tool_input": {}}) for _ in range(4)]
        check("windows: guard counts reads and warns on the 4th", all(x.returncode == 0 for x in rr) and "direct reads in a row" in rr[3].stdout)
        r = wrun("pre", dict(agent("haiku", None, cwd=cwd), session_id="win"))
        check("windows: pre still blocks an agent without effort", "effort=missing" in r.stdout)
        Path(cwd, ".claude").mkdir(exist_ok=True)
        r = subprocess.run([sys.executable, str(sdir / "memo.py"), "now", "--session", "win"], input="Task      Arabic ✓ عربي 🧔\nNext      test\n", capture_output=True, text=True, env=wenv, cwd=cwd)
        check("windows: memo.py writes a Now section with emoji and Arabic", r.returncode == 0 and "عربي" in Path(cwd, ".claude", "abofles-memo.d", "win.md").read_text(encoding="utf-8"))
        hj = json.loads((HOOK.parent / "hooks.json").read_text())
        cmds = [h["command"] for ev in hj["hooks"].values() for grp in ev for h in grp["hooks"] if "run.py" in h["command"]]
        check("windows: every hook command goes through scripts/py (python3 -> python -> py -3)", cmds and all('bash "${CLAUDE_PLUGIN_ROOT}/scripts/py"' in c for c in cmds))
        check("windows: no Unix-only import left at module top", "\nimport fcntl\n" not in HOOK.read_text())
        import importlib.util as _iuw
        _hw = _iuw.spec_from_file_location("abofles_hook_win", str(HOOK)); HW = _iuw.module_from_spec(_hw); _hw.loader.exec_module(HW)
        check("windows: posix() normalises backslash paths for the worktree checks", HW.posix("C:\\repo\\.git\\worktrees\\wt") == "C:/repo/.git/worktrees/wt")

    # ---- launcher scripts/py: python3 -> python -> py -3, UTF-8 mode; hooks, commands and printed commands all use it
    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as fbin:
        sdir = Path(__file__).resolve().parent
        PY = sdir / "py"
        base = dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state, ABOFLES_USER_CLAUDE_MD=str(Path(state, "none.md")))
        r = subprocess.run(["bash", str(PY), "-c", "import sys; print(sys.flags.utf8_mode)"], capture_output=True, text=True, env=base)
        check("launcher: scripts/py runs Python in UTF-8 mode", r.returncode == 0 and r.stdout.strip() == "1")
        # Windows-like PATH: python3 is the Store stub (exit 9009), python is real
        Path(fbin, "python3").write_text("#!/bin/sh\nexit 9009\n"); os.chmod(Path(fbin, "python3"), 0o755)
        os.symlink(sys.executable, Path(fbin, "python"))
        for tool in ("bash", "sh", "env", "dirname", "basename"):
            w = shutil.which(tool)
            if w and not Path(fbin, tool).exists():
                os.symlink(w, Path(fbin, tool))
        wenv = dict(base, PATH=fbin, ABOFLES_PY_PROBE="1")
        r = subprocess.run(["bash", str(PY), "-c", "import sys; print('ok', sys.flags.utf8_mode)"], capture_output=True, text=True, env=wenv)
        check("launcher: a python3 stub (exit 9009) is skipped and `python` runs", r.returncode == 0 and r.stdout.strip() == "ok 1")
        Path(fbin, "python").unlink()
        r = subprocess.run(["bash", str(PY), "-c", "print(1)"], capture_output=True, text=True, env=wenv)
        check("launcher: no Python at all -> one clear stderr line, exit 0 (hook never breaks the session)", r.returncode == 0 and "Python 3.8+ not found" in r.stderr and r.stdout == "")
        hj = json.loads((HOOK.parent / "hooks.json").read_text())
        cmds = [h["command"] for ev in hj["hooks"].values() for grp in ev for h in grp["hooks"] if "run.py" in h["command"]]
        check("launcher: every hook command runs bash scripts/py --hook hooks/run.py <mode>, no `||` (no double run)", len(cmds) == 15 and all(c.startswith('bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" --hook "${CLAUDE_PLUGIN_ROOT}/hooks/run.py" ') and "||" not in c for c in cmds))
        r = subprocess.run(["bash", str(PY), "--hook", str(Path(state, "missing.py"))], capture_output=True, text=True, env=base)
        check("launcher: --hook always exits 0, even when Python fails (exit 2 would block the tool call)", r.returncode == 0)
        r = subprocess.run(["bash", str(PY), str(Path(state, "missing.py"))], capture_output=True, text=True, env=base)
        check("launcher: without --hook the script's own exit code passes through (commands see failures)", r.returncode == 2)
        check("launcher: scripts/py has LF line endings and .gitattributes keeps them on Windows checkouts", b"\r" not in PY.read_bytes() and "plugins/abofles/scripts/py text eol=lf" in (sdir.parents[2] / ".gitattributes").read_text())
        cache = Path(state, "py-path")
        Path(fbin, "python").symlink_to(sys.executable)
        r1 = subprocess.run(["bash", str(PY), "-c", "print(2)"], capture_output=True, text=True, env=dict(wenv, ABOFLES_PY_CACHE=str(cache)))
        check("launcher: the probed interpreter is cached for the next call", r1.stdout.strip() == "2" and cache.read_text().strip() == "python")
        Path(fbin, "python").unlink()
        r2 = subprocess.run(["bash", str(PY), "-c", "print(3)"], capture_output=True, text=True, env=dict(wenv, ABOFLES_PY_CACHE=str(cache)))
        check("launcher: a cached interpreter that disappeared is re-probed (no stale cache)", r2.returncode == 0 and "not found" in r2.stderr)
        src = HOOK.read_text()
        check("windows: the UTF-8 relaunch skips test_hooks.py and gen_matrix.py", '"test_hooks.py", "gen_matrix.py"' in src)
        r = subprocess.run(["bash", "-c", " ".join(cmds[0].replace("${CLAUDE_PLUGIN_ROOT}", str(sdir.parent)).split()[:4]) + " start"], input=json.dumps({"session_id": "lz", "cwd": cwd, "source": "startup"}), capture_output=True, text=True, env=base)
        check("launcher: the real hooks.json command line runs SessionStart", r.returncode == 0 and "topic lookup = bash" in r.stdout and "scripts/py" in r.stdout)
        cm = "".join(p.read_text() for p in (sdir.parent / "commands").glob("*.md"))
        docs = "".join(p.read_text() for p in [sdir.parent / "skills/abofles/SKILL.md", *(sdir.parent / "skills/abofles/references").glob("*.md")])
        check("launcher: no slash command or skill text tells the model to run python3 directly", "python3 \"${CLAUDE_PLUGIN_ROOT}" not in cm and "Bash(python3" not in cm and 'python3 "<plugin' not in docs and "Bash(bash *)" in cm)
        import importlib.util as _iul
        _hl = _iul.spec_from_file_location("abofles_hook_l", str(HOOK)); HL = _iul.module_from_spec(_hl); _hl.loader.exec_module(HL)
        check("launcher: printed commands use scripts/py with forward slashes", HL.shell_cmd("memo.py").startswith('bash "') and "/scripts/py\" \"" in HL.shell_cmd("memo.py") and "\\" not in HL.shell_cmd("memo.py"))
        # Windows path shapes (ntpath-style strings) in the prefix checks
        st = {"root": "C:\\work\\proj"}
        _rp = HL.os.path.realpath
        HL.os.path.realpath = lambda x: x
        try:
            check("windows: an edit under the launch root (backslash paths) counts for the root", HL.work_key(st, "C:\\work\\proj\\src\\a.cs") == "C:\\work\\proj")
        finally:
            HL.os.path.realpath = _rp
        check("windows: replace_file retries a PermissionError then succeeds", (lambda: (lambda calls: (setattr(HL.os, "replace", (lambda o: (lambda a, b: (calls.append(1), (_ for _ in ()).throw(PermissionError()) if len(calls) < 3 else o(a, b))))(HL.os.replace)), Path(state, "t.tmp").write_text("x"), HL.replace_file(Path(state, "t.tmp"), Path(state, "t.out")), Path(state, "t.out").exists() and len(calls) == 3)[-1])([]))())


    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state:  # pane batch: its own dirs and environment
        os.environ["ABOFLES_STATE_DIR"] = state
        os.environ["ABOFLES_TEST_CWD"] = cwd
        os.environ["ABOFLES_USER_CLAUDE_MD"] = str(Path(state, "no-user-claude.md"))
        os.environ["ABOFLES_CONFIG"] = str(Path(state, "config.json"))
        os.environ["ABOFLES_CLAUDE_HOME"] = str(Path(state, "claude-home"))
        os.environ.pop("ABOFLES_SESSION", None)
        gstate = lambda sid_: json.loads(Path(state, f"{sid_}.json").read_text())
        # ── Pane batch: golden resume text, places, pane payload ──
        import importlib.util as _iug
        _hg = _iug.spec_from_file_location("abofles_hook_gold", str(HOOK)); HG = _iug.module_from_spec(_hg); _hg.loader.exec_module(HG)
        gp = str(Path(tempfile.mkdtemp(), "goldproj")); gs = "goldsess1"; HG.STATE_DIR = Path(tempfile.mkdtemp())  # empty registry: no other projects
        Path(gp, ".claude", "abofles-memo.d").mkdir(parents=True)
        Path(gp, "CLAUDE.md").write_text("## abofles overrides\n- Other items = show\n")  # the golden texts list other sessions' items (the default now hides them)
        Path(gp, ".claude", "abofles-memo.md").write_text(
            "# Abo Fles memo — gold\nUpdated: 2026-10-20 10:30 · session goldsess\nProject: x\n\n## Needs you\n"
            "1. [ ] 10-20 09:10 · owner decision: cap per unit or per order? (s:goldsess)\n2. [x] 10-20 09:11 · done thing (s:goldsess)\n3. [ ] 10-20 09:12 · ATTENTION: banner copy needs your OK (s:othersess)\n"
            "\n## Decisions\n\n## Sessions\n- [goldsess](abofles-memo.d/goldsess.md) · 10-20 10:30 · task: cap the surcharge · receipts: 2\n- [othersess](abofles-memo.d/othersess.md) · 10-20 09:00 · task: the banner · receipts: 1\n")
        Path(gp, ".claude", "abofles-memo.d", "goldsess.md").write_text(
            "# Session goldsess\nUpdated: 2026-10-20 10:30\nProject: x\n\n## Now\nWritten   2026-10-20 10:00\nTask      cap the surcharge in AqsatiRules.cs\nLeft      wire the FE badge; update the docs page\nNext      run the full suite\n"
            "\n## Journal\n- 10-20 10:20 · 🧾 **1 Agent · Saved 5%** · done: capped it\n- 10-20 10:10 · 🧾 **2 Agents** · done: read the rules\n- 10-20 10:05 · 🧾 **1 Agent** · done: first\n- 10-20 09:50 · 🧾 old\n\n## Decisions\n")
        Path(gp, ".claude", "abofles-memo.d", "othersess.md").write_text("# Session othersess\n\n## Now\nWritten   2026-10-20 09:00\nTask      the banner\n\n## Journal\n- 10-20 09:00 · 🧾 **1 Agent** · done: banner\n\n## Decisions\n")
        _t0 = time.time
        time.time = lambda: time.mktime(time.strptime("2026-10-20 13:30", "%Y-%m-%d %H:%M"))
        try:
            gold_out = {
                "project": HG.resume_block(gp, session_id=gs, guide="🧭 ✅ nothing open"),
                "away": HG.resume_block(gp, 42, session_id=gs),
                "session": HG.resume_block(gp, session_id=gs, scope="session"),
                "hint": HG.resume_block(gp, session_id="freshsess9"),
                "all": HG.resume_block(gp, session_id=gs, scope="all"),
            }
        finally:
            time.time = _t0
        GOLD = {
            'project': "📍 **Resume** · memo 2026-10-20 10:30 · session goldsess · now written 2026-10-20 10:00 (3 hours 30 min ago)\n- `now` → cap the surcharge in AqsatiRules.cs\n- `left` →\n  - wire the FE badge\n  - update the docs page\n- `next` → run the full suite\n- `since` → 3 receipts of this session after its Now section was written — `now`/`next` above may be stale; trust `last` and the journal\n- `needs you` → 2 open\n  - 1) owner decision: cap per unit or per order? (s:goldsess)\n  - 3) other session othersess · ATTENTION: banner copy needs your OK · other\n- `last` →\n  - 10-20 10:20 · 🧾 **1 Agent · Saved 5%** · done: capped it\n  - 10-20 10:10 · 🧾 **2 Agents** · done: read the rules\n  - 10-20 10:05 · 🧾 **1 Agent** · done: first\n- `guide` → 🧭 ✅ nothing open",
            'away': "📍 **Resume** · memo 2026-10-20 10:30 · away 42 min · session goldsess · now written 2026-10-20 10:00 (3 hours 30 min ago)\n- `now` → cap the surcharge in AqsatiRules.cs\n- `left` →\n  - wire the FE badge\n  - update the docs page\n- `next` → run the full suite\n- `since` → 3 receipts of this session after its Now section was written — `now`/`next` above may be stale; trust `last` and the journal\n- `needs you` → 2 open\n  - 1) owner decision: cap per unit or per order? (s:goldsess)\n  - 3) other session othersess · ATTENTION: banner copy needs your OK · other\n- `last` →\n  - 10-20 10:20 · 🧾 **1 Agent · Saved 5%** · done: capped it\n  - 10-20 10:10 · 🧾 **2 Agents** · done: read the rules\n  - 10-20 10:05 · 🧾 **1 Agent** · done: first",
            'session': "📍 **Resume** · memo 2026-10-20 10:30 · session goldsess · scope session · now written 2026-10-20 10:00 (3 hours 30 min ago)\n- `now` → cap the surcharge in AqsatiRules.cs\n- `left` →\n  - wire the FE badge\n  - update the docs page\n- `next` → run the full suite\n- `since` → 3 receipts of this session after its Now section was written — `now`/`next` above may be stale; trust `last` and the journal\n- `needs you` → 1 open: 1) owner decision: cap per unit or per order? (s:goldsess)\n- `last` →\n  - 10-20 10:20 · 🧾 **1 Agent · Saved 5%** · done: capped it\n  - 10-20 10:10 · 🧾 **2 Agents** · done: read the rules\n  - 10-20 10:05 · 🧾 **1 Agent** · done: first",
            'hint': "📍 **Resume** · memo 2026-10-20 10:30 · session freshses\n- `project` → goldproj: 2 other sessions, newest goldsess · task: cap the surcharge — their work, not yours; `/abofles:resume --scope project` shows them\n- `needs you` → 2 open\n  - 1) other session goldsess · owner decision: cap per unit or per order? · other\n  - 3) other session othersess · ATTENTION: banner copy needs your OK · other",
            'all': "📍 **Resume** · memo 2026-10-20 10:30 · session goldsess · scope all · now written 2026-10-20 10:00 (3 hours 30 min ago)\n- `now` → cap the surcharge in AqsatiRules.cs\n- `left` →\n  - wire the FE badge\n  - update the docs page\n- `next` → run the full suite\n- `since` → 3 receipts of this session after its Now section was written — `now`/`next` above may be stale; trust `last` and the journal\n- `needs you` → 2 open\n  - 1) owner decision: cap per unit or per order? (s:goldsess)\n  - 3) other session othersess · ATTENTION: banner copy needs your OK · other\n- `last` →\n  - 10-20 10:20 · 🧾 **1 Agent · Saved 5%** · done: capped it\n  - 10-20 10:10 · 🧾 **2 Agents** · done: read the rules\n  - 10-20 10:05 · 🧾 **1 Agent** · done: first",
        }
        for _k, _v in GOLD.items():
            check(f"resume_block golden text ({_k}) is byte-identical", gold_out[_k] == _v)
        # Needs-you origin: another session's (or another project's) item is information, never part of a fill, a guide instruction or a receipt `next`
        ox = str(Path(tempfile.mkdtemp(), "projx")); Path(ox, ".claude", "abofles-memo.d").mkdir(parents=True)
        Path(ox, "CLAUDE.md").write_text("## abofles overrides\n- Other items = show\n")  # these checks are about how other sessions' items are listed
        def ox_memo(lines):
            Path(ox, ".claude", "abofles-memo.md").write_text("# Abo Fles memo — x\nUpdated: 2026-11-01 10:00 · session meses1xx\nProject: x\n\n## Needs you\n" + "\n".join(lines) + "\n\n## Decisions\n\n## Sessions\n")
        ox_memo(["1. [ ] 11-01 09:00 · owner decision: legacy line? ", "2. [ ] 11-01 09:01 · owner decision: mine one? (s:meses1xx p:projx)", "3. [ ] 11-01 09:02 · owner decision: othersess zebra? (s:othses1x p:projx)",
                 "4. [ ] 11-01 09:03 · owner decision: project B yak? (s:othses2x p:projB)", "5. [ ] 11-01 09:04 · owner decision: old tag walrus? (s:oldses1x)"])
        oge = HG.guide_entries(ox, {}, "meses1xx", scope="project")
        odec = next(i for i in oge if i["id"].startswith("dec:"))
        oall = json.dumps(oge, ensure_ascii=False)
        check("origin: the guide's owner-decision item counts this session's tagged items only (an untagged line is information) and says how many are not its own", odec["text"].startswith("1 owner decision open") and "(+4 of other sessions, not yours)" in odec["text"])
        check("origin: no guide fill or recheck mentions another session's / another project's item", not any(w in oall for w in ("zebra", "yak", "walrus")))
        check("origin: the item of this session keeps its origin in the fill; the untagged line is not an instruction", "mine one" in odec["fill"] and "(from session meses1xx · projx)" in odec["fill"] and "legacy line" not in odec["fill"])
        ox_memo(["1. [ ] 11-01 09:02 · owner decision: othersess zebra? (s:othses1x p:projx)", "2. [ ] 11-01 09:03 · owner decision: project B yak? (s:othses2x p:projB)"])
        check("origin: when every open item is another session's, the guide makes no owner-decision item at all", not any(i["id"].startswith("dec:") for i in HG.guide_entries(ox, {}, "meses1xx", scope="project")))
        pr0 = HG.pane_resume(ox, "meses1xx")
        check("origin: the pane lists them as information (other: true, prefixed), counts none as open, and has no needs_fill", pr0["needs_open"] == 0 and pr0["needs_fill"] is None and len(pr0["needs"]) == 2
              and all(n.get("other") and n["text"].startswith("other session ") for n in pr0["needs"]) and "other session othses2x · projB · owner decision: project B yak?" in pr0["needs"][1]["text"])
        ox_memo(["1. [ ] 11-01 09:00 · owner decision: legacy line? ", "2. [ ] 11-01 09:01 · owner decision: mine one? (s:meses1xx p:projx)", "3. [ ] 11-01 09:02 · owner decision: othersess zebra? (s:othses1x p:projx)", "4. [ ] 11-01 09:05 · owner decision: old tag walrus? (s:oldses1x)", "5. [ ] 11-01 09:06 · owner decision: mine two? (s:meses1xx p:projx)"])
        pr1 = HG.pane_resume(ox, "meses1xx")
        check("origin: needs_fill (Check all) works through the session's own tagged items only; the untagged and the others are listed, unfillable", pr1["needs_open"] == 2 and pr1["needs_fill"] and "mine two" in pr1["needs_fill"] and "mine one" in pr1["needs_fill"]
              and not any(w in pr1["needs_fill"] for w in ("zebra", "walrus", "legacy line")) and "(from session meses1xx" in pr1["needs_fill"] and [bool(n.get("other")) for n in pr1["needs"]] == [True, False, True, True, False])
        rbx = HG.resume_block(ox, session_id="meses1xx")
        check("origin: the resume block shows another session's item as `other session <id> · …`, an untagged line as `untagged · …`", "5 open" in rbx and "other session othses1x · projx · owner decision: othersess zebra?" in rbx and "1) untagged · owner decision: legacy line?" in rbx and "2) owner decision: mine one?" in rbx)
        sx = {"started": 1, "root": ox}
        HG.refresh_guide(ox, sx, "meses1xx")
        rk, rins = HG.receipt_decision(ox, sx, "meses1xx")
        check("origin: the receipt's `next` (the guide line) is the same filtered list — never another session's item", "owner decision" in sx["guide"]["line"] and not any(w in rins for w in ("zebra", "walrus")))
        mx_ = HG.memo_needs(ox, "meses1xx", "owner decision: archive closed accounts?")
        check("origin: a new Needs-you item is tagged with its session and project", "(s:meses1xx p:projx)" in Path(ox, ".claude", "abofles-memo.md").read_text() and "archive closed accounts" in Path(ox, ".claude", "abofles-memo.md").read_text())
        check("origin: need_split: this session's tagged items are its own, whatever project the tag names (a moved session keeps its old tags); an untagged line and another session's are information", HG.need_split(["1. [ ] 1 2 · a (s:me)", "2. [ ] 1 2 · b (s:me p:other)", "3. [ ] 1 2 · c", "4. [ ] 1 2 · d (s:you)", "5. [ ] 1 2 · e (s:you p:other)"], "me", "/x/projx") == (["1. [ ] 1 2 · a (s:me)", "2. [ ] 1 2 · b (s:me p:other)"], ["3. [ ] 1 2 · c", "4. [ ] 1 2 · d (s:you)", "5. [ ] 1 2 · e (s:you p:other)"]))

        # places and the mod switch
        chome = Path(state, "claude-home"); HG.LEDGER_DIR = Path(led)
        def mod(on, key="abofles-guide@claude-plugins"):  # `on` True/False: a settings.json key; None: no settings file at all
            shutil.rmtree(chome, ignore_errors=True)
            if on is not None:
                chome.mkdir(parents=True); (chome / "settings.json").write_text(json.dumps({"enabledPlugins": {key: on}}))
        def put_md(proj, text):
            if text is None: Path(proj, "CLAUDE.md").unlink(missing_ok=True)
            else: Path(proj, "CLAUDE.md").write_text("## abofles overrides\n" + text)
        pc = tempfile.mkdtemp()
        mod(None)
        cached_ = HG.pane_enabled
        HG._PANE_ON.clear(); first_ = cached_(); mod(True); again_ = cached_(); HG._PANE_ON.clear(); fresh_ = cached_(); mod(None)
        check("pane_enabled: read once per process (place() asks three times per refresh); a new process reads again", first_ is False and again_ is False and fresh_ is True)
        HG.pane_enabled = HG.pane_enabled_uncached  # below, the files change inside this one process
        check("pane_enabled: no settings file and no plugin cache -> off", HG.pane_enabled() is False)
        (chome / "plugins" / "cache" / "mk" / "abofles-guide").mkdir(parents=True)
        check("pane_enabled: no settings file, abofles-guide folder in the plugin cache -> on", HG.pane_enabled() is True)
        mod(True); check("pane_enabled: enabledPlugins abofles-guide@… true -> on", HG.pane_enabled() is True)
        mod(False); (chome / "plugins" / "cache" / "mk" / "abofles-guide").mkdir(parents=True)
        check("pane_enabled: the key false wins over the cache folder -> off", HG.pane_enabled() is False)
        mod(True, "other-mod@x"); check("pane_enabled: only another plugin's key and no cache -> off", HG.pane_enabled() is False)
        (chome / "settings.json").write_text("{not json"); check("pane_enabled: unreadable settings and no cache -> off", HG.pane_enabled() is False)
        mod(None); pd_ = Path(state, "plugdirs"); (pd_ / "x" / "abofles-guide").mkdir(parents=True); (pd_ / "y").mkdir()
        os.environ["CLAUDE_CODE_PLUGIN_DIRS"] = str(pd_ / "y") + os.pathsep + str(pd_ / "x")
        check("pane_enabled: CLAUDE_CODE_PLUGIN_DIRS names a folder holding abofles-guide, no settings -> on", HG.pane_enabled() is True)
        os.environ["CLAUDE_CODE_PLUGIN_DIRS"] = str(pd_ / "x" / "abofles-guide")
        check("pane_enabled: CLAUDE_CODE_PLUGIN_DIRS names the abofles-guide folder itself -> on", HG.pane_enabled() is True)
        os.environ["CLAUDE_CODE_PLUGIN_DIRS"] = str(pd_ / "y")
        check("pane_enabled: CLAUDE_CODE_PLUGIN_DIRS without abofles-guide -> off", HG.pane_enabled() is False)
        os.environ["CLAUDE_CODE_PLUGIN_DIRS"] = str(pd_ / "x"); mod(False)
        check("pane_enabled: the enabledPlugins key false wins over CLAUDE_CODE_PLUGIN_DIRS -> off", HG.pane_enabled() is False)
        os.environ.pop("CLAUDE_CODE_PLUGIN_DIRS")
        mod(True)
        put_md(pc, None)
        check("place: default is pane for guide, resume and agents", [HG.place(pc, w) for w in ("guide", "resume", "spawns")] == ["pane"] * 3)
        put_md(pc, "- Guide place = band\n- Resume place = output\n- Agents place = Output\n")
        check("place: values read from the override block (case ignored)", [HG.place(pc, w) for w in ("guide", "resume", "spawns")] == ["band", "output", "output"])
        put_md(pc, "- Guide place = output\n- Resume place = band\n- Agents place = nonsense\n")
        check("place: a value that is not one of the key's two -> pane", [HG.place(pc, w) for w in ("guide", "resume", "spawns")] == ["pane"] * 3)
        put_md(pc, "- Resume = off\n- Guide = off\n")
        check("place: `Resume =` and `Guide =` are other keys, they do not set a place", [HG.place(pc, w) for w in ("guide", "resume", "spawns")] == ["pane"] * 3)
        put_md(pc, "- Guide place = pane\n- Resume place = pane\n- Agents place = pane\n")
        mod(False)
        check("place: without the mod guide is band and resume/agents are output, whatever the override says", [HG.place(pc, w) for w in ("guide", "resume", "spawns")] == ["band", "output", "output"])
        put_md(pc, None)
        check("override_source: default when no file sets the key", HG.override_source(pc, "Pace") == (None, "default", None))
        put_md(pc, "- Pace = fast\n")
        uc = Path(state, "user-claude.md"); uc.write_text("## abofles overrides\n- Pace = normal\n- Break = 25\n")
        HG.USER_CLAUDE_MD = uc
        check("override_source: project file wins, then user, with the path", HG.override_source(pc, "Pace") == ("fast", "project", str(Path(pc, "CLAUDE.md"))) and HG.override_source(pc, "Break") == ("25", "user", str(uc)))
        HG.USER_CLAUDE_MD = Path(os.environ["ABOFLES_USER_CLAUDE_MD"]); put_md(pc, None)
        names = {k for k in HG.OVERRIDE_KEYS}
        check("OVERRIDE_KEYS: every simple key of overrides.md plus the three places, each with kind, options and default",
              names >= {"Main session", "Session cap", "Context marks", "Receipt", "Break", "Agents", "Parallel", "Agent budgets", "Questions", "Language", "Guide", "Review gate", "Resume", "Pace", "Guide place", "Resume place", "Agents place"}
              and all(v["kind"] in ("select", "number", "text", "list") and "default" in v and (v["kind"] == "select") == bool(v["options"]) for v in HG.OVERRIDE_KEYS.values())
              and all(HG.OVERRIDE_KEYS[k]["default"] == "pane" for k in ("Guide place", "Resume place", "Agents place")))
        forms = ["Unit Test / Work = haiku·medium", "Codebase Exploration & Onboarding / Read cap = 200", "Topic: Notifications & Messaging = T3", "Reviewers: FIN = finance, security, tester",
                 "Workflow: Code Health = Read, Work, Verify", "Review: Security / T4 = opus·xhigh", "Financial & Amount Calculations / Verify = opus·xhigh"]
        check("COMPLEX_FORMS: every complex form of overrides.md matches one, no simple key does", all(any(r.match(f) for r in HG.COMPLEX_FORMS) for f in forms)
              and not any(r.match(f"{k} = {v['default'] or 'x'}") for k, v in HG.OVERRIDE_KEYS.items() for r in HG.COMPLEX_FORMS))

        # pane payload
        ps = "panesess1"; pd = tempfile.mkdtemp()
        mod(True)
        put_md(pd, "- Pace = fast\n- Unit Test / Work = haiku·medium\n- Bogus = 1\n")
        HG.memo_now(pd, "Task      write the pane\nLeft      tests; docs\nNext      ship it " + "x" * 700, ps)
        HG.memo_needs(pd, ps, "owner decision: cap per unit or per order?")
        run("start", {"session_id": ps, "cwd": pd}, led)
        pn_ = gstate(ps).get("pane") or {}
        check("pane: start writes the payload with v, ts, root, sid, places, resume, agents, overrides, cmd", {"v", "ts", "root", "sid", "places", "resume", "spawns", "overrides", "cmd"} <= set(pn_) and pn_["v"] == 1 and pn_["sid"] == ps and pn_["root"] == pd)
        check("pane: places are the effective ones (mod on -> pane)", pn_["places"] == {"guide": "pane", "resume": "pane", "spawns": "pane", "running": "pane"})
        rs_ = pn_["resume"] or {}
        check("pane: resume has head, now, left, next, needs (n + text), needs_open, last", set(rs_) == {"head", "now", "left", "next", "needs", "needs_open", "needs_fill", "others", "others_items", "last"} and rs_["now"] == ["write the pane"] and rs_["left"] == ["tests", "docs"]
              and rs_["needs"] == [{"n": 1, "text": "owner decision: cap per unit or per order?"}] and rs_["needs_open"] == 1 and rs_["head"].startswith("📍 Resume") and "**" not in rs_["head"] and "`" not in rs_["head"] and rs_["last"] == [])
        check("pane: resume texts are clipped to PANE_TEXT (600) characters", len(rs_["next"][0]) == HG.PANE_TEXT and rs_["next"][0].endswith("…"))
        ov_ = pn_["overrides"]; okeys = {k["key"]: k for k in ov_["keys"]}
        check("pane: overrides.keys has one entry per simple key with key, kind, options, value, source, default, project, user", [k["key"] for k in ov_["keys"]] == list(HG.OVERRIDE_KEYS) and all(set(k) == {"key", "kind", "options", "value", "source", "default", "project", "user"} for k in ov_["keys"]))
        check("pane: a set key shows its value and source, an unset one its default", okeys["Pace"]["value"] == "fast" and okeys["Pace"]["source"] == "project" and okeys["Break"]["value"] == "10" and okeys["Break"]["source"] == "default")
        check("pane: overrides.raw holds the lines the table cannot show (complex forms, unknown keys), files names both CLAUDE.md",
              [r["line"] for r in ov_["raw"]] == ["Unit Test / Work = haiku·medium", "Bogus = 1"] and all(r["source"] == "project" for r in ov_["raw"]) and ov_["files"] == {"project": str(Path(pd, "CLAUDE.md")), "user": os.environ["ABOFLES_USER_CLAUDE_MD"]})
        lr_ = tempfile.mkdtemp()
        HG.memo_journal(lr_, "longrcpt", HG.parse_receipt("🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → " + "d" * 390 + "\n- `why` → " + "w" * 290 + "\n- `check` → " + "c" * 390 + "\n- `open` → none"))
        lr1_ = HG.pane_resume(lr_, "longrcpt")["last"][0]
        check("pane: a receipt's line reaches the pane whole, past the 600 characters a text gets", len(lr1_) > 1000 and "w" * 290 in lr1_ and "c" * 390 in lr1_ and "open: none" in lr1_)
        check("pane: cmd.overrides is the script_cmd argv prefix for overrides.py with the session", pn_["cmd"]["overrides"][:2] == ["bash", HG.posix(HG.SCRIPTS_DIR / "py")] and pn_["cmd"]["overrides"][2].endswith("/scripts/overrides.py") and pn_["cmd"]["overrides"][-2:] == ["--session", ps])
        check("pane: agents empty at start", pn_["spawns"] == {"live": [], "rows": [], "totals": {"spawns": 0, "tokens": 0, "usd": 0.0, "base_usd": 0.0, "saved_pct": 0, "open_fail": 0, "base_label": "Opus 5.5", "by_model": []}})
        # post: a sync spawn, a background spawn; stop closes the background one
        okrep = "STATUS: done\nCONFIDENCE: high\nFACTS:\n- src/a.cs:1 — x\nGAPS: none"
        pa_ = lambda **kw: dict(agent("haiku", "low", cwd=pd, **kw), session_id=ps)
        run("post", pa_(resp=okrep), led)
        pn_ = gstate(ps)["pane"]
        check("pane: a sync agent lands in agents.rows after post, with side/model/effort/status/confidence/topic/tier/task and no live row",
              len(pn_["spawns"]["rows"]) == 1 and pn_["spawns"]["live"] == [] and {k: pn_["spawns"]["rows"][0][k] for k in ("side", "model", "effort", "status", "topic", "tier", "task")} == {"side": "Read", "model": "haiku", "effort": "low", "status": "done", "topic": "Unit Test", "tier": 2, "task": "q"} and pn_["spawns"]["rows"][0]["confidence"] in ("high", "medium", "low")
              and set(pn_["spawns"]["rows"][0]) == {"ts", "side", "model", "effort", "topic", "tier", "status", "confidence", "tokens", "usd", "base_usd", "description", "verdict", "task", "resolved"} and pn_["spawns"]["totals"]["spawns"] == 1)
        bgd = dict(agent("sonnet", "medium", "abofles:abofles-worker", prompt="abofles: Unit Test / W · T2\nwrite the cap test\nLine cap: 30", cwd=pd), session_id=ps, tool_response={"status": "async_launched", "agentId": "bgagent1"})
        run("post", bgd, led)
        pn_ = gstate(ps)["pane"]
        check("pane: a background agent is in live (side, model, effort, description, ts) and in rows as pending", len(pn_["spawns"]["live"]) == 1 and set(pn_["spawns"]["live"][0]) == {"side", "model", "effort", "description", "ts"}
              and pn_["spawns"]["live"][0]["model"] == "sonnet" and pn_["spawns"]["rows"][-1]["status"] == "pending")
        run("stop", {"session_id": ps, "cwd": pd, "agent_id": "bgagent1", "last_assistant_message": "STATUS: done\nCONFIDENCE: medium\nCHANGED:\n- a.cs — cap\nRAN: ok\nLEFT: none"}, led)
        pn_ = gstate(ps)["pane"]
        check("pane: SubagentStop refreshes the payload: live empties, the row is graded", pn_["spawns"]["live"] == [] and pn_["spawns"]["rows"][-1]["status"] == "done" and pn_["spawns"]["rows"][-1]["confidence"] == "medium")
        check("pane: totals count every row; with no transcripts tokens and usd are 0", pn_["spawns"]["totals"]["spawns"] == 2 and pn_["spawns"]["totals"]["tokens"] == 0)
        # Receipt place = pane (the default with the mod): the Stop asks for the receipt recorded by memo.py receipt, and the recorded one closes the task
        out_ = run("receipt", {"session_id": ps, "cwd": pd, "stop_hook_active": False, "last_assistant_message": "Done, tests pass."}, led)
        rs_ = (out_ or {}).get("reason", "")
        check("receipt place pane: the Stop asks for the receipt recorded by one command (memo.py receipt, a heredoc), not printed", "do NOT print" in rs_ and "memo.py" in rs_ and "receipt --session" in rs_ and "<<'EOF'" in rs_ and "🧾 **" in rs_ and "plain markdown" not in rs_)
        mp_ = Path(__file__).resolve().parent / "memo.py"
        hdr_ = re.search(r"^🧾 \*\*.*$", rs_, re.M).group(0)
        menv_ = dict(os.environ, ABOFLES_STATE_DIR=state)
        bad_ = subprocess.run([sys.executable, str(mp_), "receipt", "--session", ps], input="no receipt here", capture_output=True, text=True, env=menv_, cwd=pd)
        rec_ = subprocess.run([sys.executable, str(mp_), "receipt", "--session", ps], input=hdr_ + "\n- `done` → recorded the cap test\n- `why` → pane\n- `check` → ok\n- `open` → none", capture_output=True, text=True, env=menv_, cwd=pd)
        check("memo.py receipt: stdin without a receipt -> exit 2; a receipt -> exit 0, kept in the state for the Stop", bad_.returncode == 2 and rec_.returncode == 0 and "recorded" in rec_.stdout and (gstate(ps).get("receipt_saved") or {}).get("text", "").startswith("🧾"))
        st_rec_ = gstate(ps)
        check("memo.py receipt: the recorded receipt closes its task at once (a next task in the same turn counts apart) and is journaled now",
              st_rec_.get("spawns_since_receipt", 0) == 0 and (st_rec_.get("receipt_saved") or {}).get("closed") is True and "recorded the cap test" in HG.session_memo_path(pd, ps).read_text())
        out_ = run("receipt", {"session_id": ps, "cwd": pd, "stop_hook_active": True, "last_assistant_message": "Done — the cap test is in."}, led)
        st_ = gstate(ps)
        check("receipt place pane: the recorded receipt closes the task like a printed one (no demand, count reset, used once)", not (out_ or {}).get("reason", "").count("no receipt") and st_.get("spawns_since_receipt", 0) == 0 and "receipt_saved" not in st_)
        check("receipt place pane: the memo Journal (the pane's Last receipts) gets the recorded receipt, once (the Stop does not journal it again)", HG.session_memo_path(pd, ps).read_text().count("done: recorded the cap test") == 1)
        mod(None); HG._PANE_ON.clear()
        check("receipt place: without the mod the receipt is printed (output), whatever the line says", HG.place(pd, "receipt") == "output")
        mod(True)
        # cap and clip, from a long ledger
        long_rows = [{"ts": 1000 + i, "side": "Read", "model": "haiku", "effort": "low", "topic": "T" * 700, "tier": 2, "status": "done", "confidence": "high", "tokens": 1000, "usd": 0.01, "base_usd": 0.04, "description": "d" * 700, "task": "t" * 700, "verdict": "PASS" if i % 2 else "FAIL"} for i in range(40)]
        stx = {}
        HG.refresh_pane(pd, stx, ps, long_rows)
        px = stx["pane"]["spawns"]
        check("pane: rows are the last 30 ledger rows, oldest first", len(px["rows"]) == 30 and px["rows"][0]["ts"] == 1010 and px["rows"][-1]["ts"] == 1039)
        check("pane: topic, description and task are clipped to PANE_TEXT characters", all(len(r[k]) <= HG.PANE_TEXT for r in px["rows"] for k in ("topic", "description", "task")))
        check("pane: totals are the sums over all 40 rows (as bill.py), saved_pct from the real dollars", {k: v for k, v in px["totals"].items() if k not in ("by_model", "base_label")} == {"spawns": 40, "tokens": 40000, "usd": 0.4, "base_usd": 1.6, "saved_pct": 75, "open_fail": 0})
        check("pane: totals carry the spend per model (label, agents, tokens, usd, % of the spend) and the baseline's label", px["totals"]["by_model"] == [{"model": "haiku", "label": "Haiku 5.5", "spawns": 40, "tokens": 40000, "usd": 0.4, "pct": 100}] and px["totals"]["base_label"] == "Opus 5.5")
        mixed_ = [{"ts": 1, "model": "haiku", "usd": 0.1, "base_usd": 0.4, "tokens": 10, "topic": "A", "verdict": "FAIL", "resolved": True},
                  {"ts": 2, "model": "sonnet", "usd": 0.3, "base_usd": 0.6, "tokens": 10, "topic": "B", "verdict": "FAIL"},
                  {"ts": 3, "model": "opus", "usd": 0.6, "base_usd": 0.6, "tokens": 10, "topic": "C", "verdict": "PASS"}]
        tm_ = HG.history_totals(mixed_)
        check("pane: a FAIL resolved by hand is not an open FAIL; by_model is ordered haiku, sonnet, opus with % of the spend", tm_["open_fail"] == 1 and [(e["model"], e["spawns"], e["pct"]) for e in tm_["by_model"]] == [("haiku", 1, 10), ("sonnet", 1, 30), ("opus", 1, 60)])
        check("pane: a row says whether its FAIL was resolved by hand", HG.pane_row(mixed_[0])["resolved"] is True and HG.pane_row(mixed_[1])["resolved"] is False)
        # Guide = off: the guide empties, the pane stays
        put_md(pd, "- Guide = off\n")
        run("prompt", {"session_id": ps, "cwd": pd}, led)
        sx = gstate(ps)
        check("pane: Guide = off -> st.guide is None and st.pane is still written (every guide refresh site refreshes both)", sx.get("guide") is None and sx["pane"]["spawns"]["totals"]["spawns"] == 2 and okeys is not None
              and {k["key"]: k for k in sx["pane"]["overrides"]["keys"]}["Guide"]["value"] == "off")
        put_md(pd, None)

        # resume at start and prompt, by place
        sf_ = Path(state, f"{ps}.json")
        def away(min_=11):
            j = json.loads(sf_.read_text()); j["last_activity"] = int(time.time()) - min_ * 60; sf_.write_text(json.dumps(j))
        mod(True)
        o_ = run("start", {"session_id": ps, "cwd": pd, "source": "compact"}, led)
        check("resume pane: startup/compact gives Claude the block as context only (no print-verbatim note)", isinstance(o_, str) and o_.count(HG.PANE_CONTEXT_NOTE) == 1 and "do not print it" in o_ and "📍 **Resume**" in o_ and "RESUME" not in o_ and "verbatim" not in o_)
        away(); o_ = ctx(run("prompt", {"session_id": ps, "cwd": pd}, led))
        check("resume pane: a prompt after a break gives Claude the block as context only (do-not-print prefix, as at start)", o_.count(HG.PANE_CONTEXT_NOTE) == 1 and "do not print it" in o_ and "📍 **Resume**" in o_ and "RESUME" not in o_ and "verbatim" not in o_)
        put_md(pd, "- Resume = off\n")
        o_ = run("start", {"session_id": ps, "cwd": pd, "source": "compact"}, led) or ""
        away(); o2_ = ctx(run("prompt", {"session_id": ps, "cwd": pd}, led))
        check("resume pane: Resume = off -> no note at all, at start or after a break", "📍" not in o_ and "resume context" not in o_ and "📍" not in o2_ and "RESUME" not in o2_)
        put_md(pd, "- Resume place = output\n")
        o_ = run("start", {"session_id": ps, "cwd": pd, "source": "compact"}, led)
        check("resume output: Resume place = output -> today's print-verbatim note at start", isinstance(o_, str) and "abofles RESUME (context was just compacted)" in o_ and "verbatim, first in your reply" in o_ and HG.PANE_CONTEXT_NOTE not in o_)
        away(); o_ = ctx(run("prompt", {"session_id": ps, "cwd": pd}, led))
        check("resume output: Resume place = output -> today's note after a break", "abofles RESUME (the user was away 11 min)" in o_ and "verbatim, first in your reply" in o_)
        put_md(pd, None); mod(False)
        o_ = run("start", {"session_id": ps, "cwd": pd, "source": "compact"}, led)
        check("resume output: the mod not installed -> today's print-verbatim note whatever the place", isinstance(o_, str) and "abofles RESUME (context was just compacted)" in o_ and HG.PANE_CONTEXT_NOTE not in o_)
        mod(True)

        # bill.py --history
        bl = subprocess.run([sys.executable, str(sdir_ := Path(__file__).resolve().parent / "bill.py"), "--history", "--session", ps, "--baseline", "opus·high"], capture_output=True, text=True,
                            env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_SESSION=ps))
        bl_lines = [l for l in bl.stdout.splitlines() if l.strip()]
        check("bill --history: one line per agent (side, status, confidence, model·effort) then the totals line", bl.returncode == 0 and len(bl_lines) == 3 and "Read" in bl_lines[0] and "done" in bl_lines[0] and "Haiku 5.5 · Low" in bl_lines[0]
              and "Work" in bl_lines[1] and "Sonnet 5.5 · Medium" in bl_lines[1] and "medium" in bl_lines[1] and bl_lines[2].startswith("Total: 2 agents") and "saved" in bl_lines[2])
        check("bill --history: no summary sections (By model, Top topics)", "By model" not in bl.stdout and "Top topics" not in bl.stdout)
        Path(led, "histsess.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
            {"ts": 1792000000, "side": "Work", "status": "done", "confidence": "high", "model": "sonnet", "effort": "medium", "tokens": 1000, "usd": 0.01, "base_usd": 0.04, "topic": "X", "verdict": "FAIL"},
            {"ts": 1792000060, "side": "Verify", "status": "partial", "confidence": "low", "model": "haiku", "effort": "low", "tokens": 2000, "usd": 0.03, "base_usd": 0.06, "topic": "Y"})))
        bh = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "bill.py"), "--history", "--baseline", "opus·high"], capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_SESSION="histsess")).stdout.splitlines()
        hl = [l for l in bh if "Work" in l or "Verify" in l]
        check("bill --history: a row with real usage shows tokens, $ and its saved %, a row without shows dashes",
              len(hl) == 2 and "1K tok" in hl[0] and "$0.01" in hl[0] and "saved 75%" in hl[0] and "partial" in hl[1] and "low" in hl[1] and "saved 50%" in hl[1])
        check("bill --history: the totals line carries the same sums as the pane payload",
              any("Total: " in l and "· 3K tokens · $0.04 (same tokens on Opus 5.5 · High: $0.10) · saved 60% · 1 open FAIL" in l for l in bh)
              and {k: v for k, v in HG.history_totals([json.loads(l) for l in Path(led, "histsess.jsonl").read_text().splitlines()]).items() if k not in ("by_model", "base_label")} == {"spawns": 2, "tokens": 3000, "usd": 0.04, "base_usd": 0.1, "saved_pct": 60, "open_fail": 1})
        check("commands: /abofles:history runs bill.py --history; /abofles:pane names the mod and falls back to memo.py show + bill.py --history",
              "bill.py\" --history" in (HOOK.parent.parent / "commands" / "history.md").read_text() and (lambda t: "abofles-guide" in t and "memo.py\" show" in t and "bill.py\" --history" in t)((HOOK.parent.parent / "commands" / "pane.md").read_text()))
        check("commands: /abofles:pane tells the user to type /abofles-pane and prints the text only when the mod is not enabled",
              (lambda t: "`/abofles-pane`" in t and "only when the `abofles-guide` mod is not enabled" in t)((HOOK.parent.parent / "commands" / "pane.md").read_text()))


        # ── overrides.py: the pane editor's writer ──
        OVP = Path(__file__).resolve().parent / "overrides.py"
        def ovrun(args, req=None, user=None, cwd_=None):
            env = dict(os.environ, ABOFLES_STATE_DIR=state)
            if user: env["ABOFLES_USER_CLAUDE_MD"] = str(user)
            return subprocess.run([sys.executable, str(OVP), *args], input=json.dumps(req) if req is not None else "", capture_output=True, text=True, env=env, cwd=cwd_)
        def ovproj():
            d = Path(tempfile.mkdtemp()); return d
        P0 = "# Proj\n\nIntro - Pace = fast (prose)\n\n"
        BLK = "## abofles overrides\n\n- Pace = normal\n- Bogus = 1\n- Unit Test / Work = haiku·medium\n- Break = 5\n\nA note about the block.\n\n"
        TAIL = "## Other\n- Break = 99\nlast line without newline"
        d1 = ovproj(); f1 = d1 / "CLAUDE.md"; f1.write_bytes((P0 + BLK + TAIL).encode())
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {"Pace": "fast", "Receipt": "savings 30"}})
        t_ = f1.read_text(); head_, tail_ = (P0 + BLK).split("- Pace = normal")[0], BLK.split("- Break = 5\n")[1] + TAIL
        check("overrides set: exit 0, prints the new overrides JSON with the value and its project source", r_.returncode == 0 and (lambda j: {k["key"]: k for k in j["keys"]}["Pace"]["value"] == "fast" and {k["key"]: k for k in j["keys"]}["Receipt"]["source"] == "project")(json.loads(r_.stdout)))
        check("overrides set: an existing key is edited in place (same line, same neighbours)", "- Pace = fast\n- Bogus = 1\n- Unit Test / Work = haiku·medium\n- Break = 5\n" in t_)
        check("overrides set: a new key lands after the block's last bullet, before its prose", "- Break = 5\n- Receipt = savings 30\n\nA note about the block." in t_)
        check("overrides set: every byte outside the block's bullets is kept (prose before, prose in and after the block, the next section, no final newline)", t_.startswith(P0 + "## abofles overrides\n\n- Pace = fast") and t_.endswith(tail_) and t_.count("Intro - Pace = fast (prose)") == 1 and "- Break = 99\nlast line without newline" in t_)
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {"Pace": "", "Break": "", "Receipt": "short"}})
        t2_ = f1.read_text()
        check("overrides set: \"\" removes the key's line in the block only (the `Break` in `## Other` stays), the others stay", "\n- Pace" not in t2_ and "- Break = 5" not in t2_ and "- Break = 99\nlast line without newline" in t2_ and "- Receipt = short" in t2_ and "- Bogus = 1" in t2_)
        check("overrides set: unknown keys, complex forms and prose are kept", "- Bogus = 1\n- Unit Test / Work = haiku·medium\n" in t2_ and "A note about the block." in t2_ and "Intro - Pace = fast (prose)" in t2_)
        # hardening: a planted temp-name symlink is never written through, a CLAUDE.md linking out of the project is refused,
        # a non-UTF-8 file and an over-long value are one-line refusals, a BOM is kept, and no temp file is left behind
        d9 = ovproj(); victim_ = d9 / "victim.txt"; victim_.write_text("keep me\n"); (d9 / "CLAUDE.md").write_text("# P\n")
        (d9 / "CLAUDE.md.abofles-tmp").symlink_to(victim_)
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d9)], {"set": {"Pace": "fast"}})
        check("overrides hardening: a symlink at the old temp name is not written through (the temp is a fresh mkstemp file)", r_.returncode == 0 and victim_.read_text() == "keep me\n" and "- Pace = fast" in (d9 / "CLAUDE.md").read_text() and not (d9 / "CLAUDE.md").is_symlink())
        check("overrides hardening: no temp file is left in the folder", not [x for x in d9.iterdir() if x.name.endswith(".abofles-tmp") and not x.is_symlink()])
        d10 = ovproj(); out_ = Path(tempfile.mkdtemp()) / "outside.md"; out_.write_text("# outside\n"); (d10 / "CLAUDE.md").symlink_to(out_)
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d10)], {"set": {"Pace": "fast"}})
        check("overrides hardening: a project CLAUDE.md that links out of the project is refused (exit 2), the target untouched", r_.returncode == 2 and "outside the project" in r_.stderr and out_.read_text() == "# outside\n")
        d11 = ovproj(); (d11 / "CLAUDE.md").write_bytes(b"# P\n\xff\xfe bad\n")
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d11)], {"set": {"Pace": "fast"}})
        check("overrides hardening: a CLAUDE.md that is not UTF-8 gives one line and exit 2, no traceback", r_.returncode == 2 and "not UTF-8" in r_.stderr and "Traceback" not in r_.stderr)
        d12 = ovproj(); (d12 / "CLAUDE.md").write_bytes(b"\xef\xbb\xbf# P\n\n## abofles overrides\n\n- Pace = normal\n")
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d12)], {"set": {"Pace": "fast"}})
        check("overrides hardening: a UTF-8 BOM is kept and the block under the first heading is still found", r_.returncode == 0 and (d12 / "CLAUDE.md").read_bytes() == b"\xef\xbb\xbf# P\n\n## abofles overrides\n\n- Pace = fast\n")
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d12)], {"set": {"Language": "x" * 600}})
        r2_ = ovrun(["set", "--scope", "project", "--cwd", str(d12)], {"set": {}, "raw": ["Topic: " + "y" * 600 + " = T3"]})
        check("overrides hardening: a value or a line over 500 characters is refused (exit 2)", r_.returncode == 2 and "longer than 500" in r_.stderr and r2_.returncode == 2 and "longer than 500" in r2_.stderr)
        if os.name != "nt":
            pdir_ = Path(tempfile.mkdtemp()) / "st"; pdir_.mkdir(mode=0o755); os.chmod(pdir_, 0o755); HG.private_dir(pdir_); nd_ = Path(tempfile.mkdtemp()) / "a" / "b"; HG.private_dir(nd_)
            check("state and ledger folders are owner-only: an older 755 folder is tightened, a new one is made 700", pdir_.stat().st_mode & 0o777 == 0o700 and nd_.stat().st_mode & 0o777 == 0o700)
        before_ = f1.read_bytes(); r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {"Pace": ""}})
        check("overrides set: removing a key that is not there changes nothing", r_.returncode == 0 and f1.read_bytes() == before_)
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {}, "raw": ["Topic: Billing = T3", "- Reviewers: FIN = tester"]})
        t3_ = f1.read_text()
        check("overrides set: raw lines replace only the complex-form lines (the old cell form goes), unknown key and simple keys stay", r_.returncode == 0 and "Unit Test / Work" not in t3_ and "- Topic: Billing = T3\n" in t3_ and "- Reviewers: FIN = tester\n" in t3_ and "- Bogus = 1" in t3_ and "- Receipt = short" in t3_ and "A note about the block." in t3_)
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {}, "raw": ["Topic: Billing = T3", "Reviewers: FIN = tester"]})
        r_ = ovrun(["set", "--scope", "project", "--cwd", str(d1)], {"set": {}, "raw": ["Bogus = 1", "Topic: Billing = T3", "Reviewers: FIN = tester"]})
        check("overrides set: an unknown line the pane lists with the forms may stay in raw (kept once, in place)", r_.returncode == 0 and f1.read_text() == t3_ and f1.read_text().count("- Bogus = 1") == 1)
        check("overrides set: the same raw lines again change nothing", r_.returncode == 0 and f1.read_text() == t3_)
        d2 = ovproj()
        check("overrides set: no CLAUDE.md yet -> the project's CLAUDE.md is created with the block", ovrun(["set", "--scope", "project", "--cwd", str(d2)], {"set": {"Guide place": "band"}}).returncode == 0 and (d2 / "CLAUDE.md").read_text() == "## abofles overrides\n\n- Guide place = band\n")
        d3 = ovproj(); (d3 / "CLAUDE.md").write_bytes(b"# T\r\nbody\r\n"); ovrun(["set", "--scope", "project", "--cwd", str(d3)], {"set": {"Pace": "fast"}})
        check("overrides set: a file without the block gets it appended after a blank line, the old bytes (CRLF too) kept", (d3 / "CLAUDE.md").read_bytes() == b"# T\r\nbody\r\n\r\n## abofles overrides\r\n\r\n- Pace = fast\r\n")
        d3b = ovproj(); (d3b / "CLAUDE.md").write_text("# T\nbody"); ovrun(["set", "--scope", "project", "--cwd", str(d3b)], {"set": {"Pace": "fast"}})
        check("overrides set: ... and \"\\n## abofles overrides\\n\\n\" when the file did not end with a newline", (d3b / "CLAUDE.md").read_text() == "# T\nbody\n\n## abofles overrides\n\n- Pace = fast\n")
        d3c = ovproj(); (d3c / "CLAUDE.md").write_text("# T\n"); ovrun(["set", "--scope", "project", "--cwd", str(d3c)], {"set": {"Pace": ""}})
        check("overrides set: removing from a file without a block does not create one", (d3c / "CLAUDE.md").read_text() == "# T\n")
        d4 = ovproj(); (d4 / ".claude").mkdir(); (d4 / "CLAUDE.md").write_text("# no block here\n"); (d4 / ".claude" / "CLAUDE.md").write_text("# T\n\n## Abofles Overrides\n- Pace = fast\n")
        ovrun(["set", "--scope", "project", "--cwd", str(d4)], {"set": {"Break": "7"}})
        check("overrides set: project scope picks .claude/CLAUDE.md when it holds the block (heading case ignored), CLAUDE.md untouched",
              (d4 / ".claude" / "CLAUDE.md").read_text() == "# T\n\n## Abofles Overrides\n- Pace = fast\n- Break = 7\n" and (d4 / "CLAUDE.md").read_text() == "# no block here\n")
        check("overrides: the pane's files.project is the file the writer picks (.claude/CLAUDE.md when it holds the block)", json.loads(ovrun(["get", "--json", "--cwd", str(d4)]).stdout)["files"]["project"] == str(d4 / ".claude" / "CLAUDE.md")
              and json.loads(ovrun(["get", "--json", "--cwd", str(d2)]).stdout)["files"]["project"] == str(d2 / "CLAUDE.md"))
        d5 = ovproj(); uf = Path(tempfile.mkdtemp()) / "sub" / "CLAUDE.md"
        r_ = ovrun(["set", "--scope", "user", "--cwd", str(d5)], {"set": {"Language": "Arabic"}}, user=uf)
        check("overrides set: user scope writes ABOFLES_USER_CLAUDE_MD (parent folder created), not the project", r_.returncode == 0 and uf.read_text() == "## abofles overrides\n\n- Language = Arabic\n" and not (d5 / "CLAUDE.md").exists()
              and json.loads(r_.stdout)["files"]["user"] == str(uf) and {k["key"]: k for k in json.loads(r_.stdout)["keys"]}["Language"]["source"] == "user")
        check("overrides: no atomic leftovers (the .abofles-tmp file is renamed away)", not list(uf.parent.glob("*.abofles-tmp")) and not list(d1.glob("*.abofles-tmp")))
        bad = [({"Receipt": "everything"}, "Receipt"), ({"Receipt": "savings x"}, "Receipt"), ({"Break": "soon"}, "Break"), ({"Agents": "many"}, "Agents"), ({"Pace": "turbo"}, "Pace"),
               ({"Agent budgets": "1, 2, 3"}, "Agent budgets"), ({"Context marks": "a, b"}, "Context marks"), ({"Nope": "1"}, "Nope"), ({"Language": "Ara\nbic"}, "Language")]
        d6 = ovproj(); (d6 / "CLAUDE.md").write_text("## abofles overrides\n- Pace = fast\n"); b6_ = (d6 / "CLAUDE.md").read_bytes()
        rs_ = [ovrun(["set", "--scope", "project", "--cwd", str(d6)], {"set": dict(sets_, Parallel="4")}) for sets_, _ in bad]
        check("overrides set: invalid values (bad option, savings x, Break soon, Agents many, short budgets, non-numbers, unknown key, two lines) exit 2 with one line naming the key",
              all(r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1 and k in r.stderr for r, (_, k) in zip(rs_, bad)) and all(r.stdout == "" for r in rs_))
        check("overrides set: ... and nothing is written, not even the valid key sent along", (d6 / "CLAUDE.md").read_bytes() == b6_)
        for req_ in ({"set": {}, "raw": ["Topic: Billing"]}, {"set": {}, "raw": ["Pace = fast"]}):
            check(f"overrides set: raw line {req_['raw'][0]!r} (not a complex form / a simple key) exits 2", ovrun(["set", "--scope", "project", "--cwd", str(d6)], req_).returncode == 2)
        check("overrides set: not JSON on stdin / no scope exit 2", ovrun(["set", "--scope", "project", "--cwd", str(d6)]).returncode == 2 and ovrun(["set", "--cwd", str(d6)], {"set": {}}).returncode == 2)
        good = [("Agents", "AUTO", "auto"), ("Receipt", "savings 30", "savings 30"), ("Break", "off", "off"), ("Context marks", "100,150, 180", "100, 150, 180"), ("Agent budgets", "1, 2, 5, 8", "1, 2, 5, 8")]
        d7 = ovproj()
        ok7_ = ovrun(["set", "--scope", "project", "--cwd", str(d7)], {"set": {k: v for k, v, _ in good}}).returncode == 0 and ovrun(["set", "--scope", "project", "--cwd", str(d7)], {"set": {"Agents": 7}}).returncode == 0
        ok7_ = ok7_ and ovrun(["set", "--scope", "project", "--cwd", str(d7)], {"set": {"Agents": "auto"}}).returncode == 0
        t7_ = (d7 / "CLAUDE.md").read_text()
        check("overrides set: Agents N/auto, Receipt `savings N`, Break off, lists of numbers are accepted and written normalised", ok7_ and all(f"- {k} = {w}\n" in t7_ for k, _, w in good))
        # a change between our read and our write: abort, exit 3, nothing written
        import importlib.util as _iuo
        _so = _iuo.spec_from_file_location("abofles_overrides_race", str(OVP)); OVM = _iuo.module_from_spec(_so); _so.loader.exec_module(OVM)
        dr = ovproj(); fr = dr / "CLAUDE.md"; fr.write_text("## abofles overrides\n- Pace = fast\n")
        _edit = OVM.edit_text
        def _racing(old, sets, raw):
            out = _edit(old, sets, raw); fr.write_text("## abofles overrides\n- Pace = fast\n- Break = 7\n"); return out  # another writer lands between read and write
        OVM.edit_text = _racing
        _argv, _stdin, _env = sys.argv, sys.stdin, os.environ.get("ABOFLES_STATE_DIR")
        sys.argv = ["overrides.py", "set", "--scope", "project", "--cwd", str(dr)]; sys.stdin = io.StringIO(json.dumps({"set": {"Pace": "normal"}})); os.environ["ABOFLES_STATE_DIR"] = state
        _err, _out = io.StringIO(), io.StringIO()
        import contextlib as _cl
        with _cl.redirect_stderr(_err), _cl.redirect_stdout(_out):
            rc_ = OVM.main()
        sys.argv, sys.stdin = _argv, _stdin
        if _env is None: os.environ.pop("ABOFLES_STATE_DIR", None)
        else: os.environ["ABOFLES_STATE_DIR"] = _env
        check("overrides set: the file changed between read and write -> exit 3, the other writer's bytes stay, no temp file left, one stderr line",
              rc_ == 3 and fr.read_text() == "## abofles overrides\n- Pace = fast\n- Break = 7\n" and not list(dr.glob("*.abofles-tmp"))
              and _err.getvalue() == "abofles: CLAUDE.md changed while saving — reload the pane and save again\n" and _out.getvalue() == "")
        # get, and the pane payload after a set
        g_ = ovrun(["get", "--json", "--cwd", str(d7)])
        check("overrides get --json: prints the pane's overrides payload (files, keys with kind/options/value/source/default, raw)", g_.returncode == 0 and (lambda j: set(j) == {"files", "keys", "raw"} and [k["key"] for k in j["keys"]] == list(HG.OVERRIDE_KEYS) and {k["key"]: k for k in j["keys"]}["Agents"]["value"] == "auto")(json.loads(g_.stdout)))
        ds = ovproj(); ss = "ovsess1"; (ds / "CLAUDE.md").write_text("## abofles overrides\n- Pace = fast\n")
        run("start", {"session_id": ss, "cwd": str(ds)}, led)
        stamp_ = gstate(ss)["pane"]["ts"]; time.sleep(0)  # the session's state exists; the pane is refreshed by the set
        r_ = ovrun(gstate(ss)["pane"]["cmd"]["overrides"][3:] + ["set", "--scope", "project", "--cwd", str(ds)], {"set": {"Pace": "normal", "Guide place": "band"}})  # the argv the pane runs: `overrides.py --session S set --scope …`
        pk_ = {k["key"]: k for k in gstate(ss)["pane"]["overrides"]["keys"]}
        check("overrides set: with a session, the state's pane payload is refreshed under the lock (value, source and places follow) and the output is the same overrides",
              r_.returncode == 0 and pk_["Pace"]["value"] == "normal" and pk_["Guide place"]["value"] == "band" and gstate(ss)["pane"]["places"]["guide"] == "band" and json.loads(r_.stdout) == gstate(ss)["pane"]["overrides"])
        check("overrides set: ... the session's other state (cmd argv, root, sid) is kept", gstate(ss)["pane"]["cmd"]["overrides"][-2:] == ["--session", ss] and gstate(ss)["pane"]["sid"] == ss)
        rd_ = (HOOK.parent.parent / "README.md").read_text()
        sec_ = rd_[rd_.index("### Overrides"):]
        check("README: the Overrides table lists every OVERRIDE_KEYS key", all(f"| `{k}` |" in sec_ for k in HG.OVERRIDE_KEYS))
        form_marks = ["Topic / Side cap", "Topic / Side =", "Topic: Name", "Reviewers:", "Workflow:", "Review: <Tag>"]  # COMPLEX_FORMS order
        check("README: ... and every COMPLEX_FORMS form (one row each)", len(form_marks) == len(HG.COMPLEX_FORMS) and all(re.search(r"^\| `" + re.escape(m), sec_, re.M) for m in form_marks))
        check("README: ... with defaults from the code, the pane editor, /abofles:pane and /abofles:history", all(f"| `{k}` |" in sec_ and (v["default"] in sec_ if v["default"] else True) for k, v in HG.OVERRIDE_KEYS.items()) and "/abofles:pane" in sec_ and "/abofles:history" in sec_ and "pane" in sec_.split("| `Main session`")[0])
        check("overrides.md: the five place forms are described and the form count matches the code", all(f"`{w} place = " in (HOOK.parent.parent / "skills/abofles/references/overrides.md").read_text() for w in ("Guide", "Resume", "Agents", "Receipt", "Running"))
              and f"Forty forms" in (HOOK.parent.parent / "skills/abofles/references/overrides.md").read_text() and len(HG.OVERRIDE_KEYS) + len(HG.COMPLEX_FORMS) == 40)

        # a receipt's Journal line keeps every field, and reads back
        rc_ = HG.parse_receipt("🧾 **1 Agent · 63K Tokens · $0.03**\n- `done` → the page\n- `why` → asked\n- `workflow` → Read ✓ · Work ✓\n- `check` → 181 pass\n- `open` → none\n- `next` → run /compact")
        check("receipt: workflow is a field of its own", rc_ and rc_["workflow"] == "Read ✓ · Work ✓" and rc_["why"] == "asked")
        bits_ = HG.journal_bits(rc_)
        check("receipt: the Journal line keeps every field in the receipt's order", [b.split(":")[0] for b in bits_[1:]] == ["done", "why", "workflow", "check", "open", "next"])
        back_ = HG.journal_receipt("- 11-09 10:57 · " + " · ".join(bits_))
        check("receipt: a Journal line reads back field by field", back_ and back_[1]["done"] == "the page" and back_[1]["workflow"] == "Read ✓ · Work ✓" and back_[1]["next"] == "run /compact" and back_[1]["check"] == "181 pass")
        check("receipt: an older Journal line still reads", HG.journal_receipt("- 11-09 10:57 · 1 Spawn · done: a · check: b · open: c")[1]["check"] == "b")
        # Main session / Sub session: the owner's setting takes the tier table's place
        with tempfile.TemporaryDirectory() as td_:
            pr_ = Path(td_) / "proj"; pr_.mkdir(); apps_ = Path(td_) / "apps" / "a" / "b"; apps_.mkdir(parents=True)
            keep_dirs_ = HG.APP_SESSION_DIRS; HG.APP_SESSION_DIRS = [Path(td_) / "apps"]
            def block_(*lines_): (pr_ / "CLAUDE.md").write_text("## abofles overrides\n\n" + "".join(f"- {l}\n" for l in lines_))
            def rec_(name_, **kw_): (apps_ / f"local_{name_}.json").write_text(json.dumps(kw_))
            def st_(model_, effort_): return {"main_model": model_, "effort": effort_}
            block_("Receipt = full")
            check("Main session unset: the tier table speaks", "suggest Sonnet 5.5 · Medium for this" in HG.main_line(st_("opus", "xhigh"), 2, str(pr_), "s1"))
            block_("Main session = opus·medium")
            check("Main session: on it -> fits your setting, whatever the tier", HG.main_line(st_("opus", "medium"), 4, str(pr_), "s1") == "main: Opus 5.5 · Medium fits your setting")
            check("Main session: off it -> names your setting, no tier", HG.main_line(st_("opus", "xhigh"), 4, str(pr_), "s1").startswith("main: suggest Opus 5.5 · Medium, your setting (now Opus 5.5 · Extra)"))
            check("Main session: the pane's hint says the same, none when it fits", HG.main_hint_short(st_("opus", "xhigh"), str(pr_), "s1") == "suggest Opus 5.5 · Medium, your setting (now Opus 5.5 · Extra)" and HG.main_hint_short(st_("opus", "medium"), str(pr_), "s1") is None)
            check("Main session: effort unknown says so", HG.main_line(st_("opus", None), 4, str(pr_), "s1") == "main: Opus 5.5 fits your setting (effort unknown)")
            block_("Main session = whatever")
            check("Main session: not a model·effort -> the tier table", "for this" in HG.main_line(st_("opus", "xhigh"), 2, str(pr_), "s1"))
            check("Main session: the app's own spelling reads too", HG.pick_of("Opus 5.5 · Extra") == ("opus", "xhigh") and HG.pick_of("opus.medium") == ("opus", "medium") and HG.pick_of("sonnet medium") == ("sonnet", "medium") and HG.pick_of("whatever") is None and HG.pick_of("opus") is None)
            block_("Main session = opus·medium", "Sub session = opus·xhigh")
            rec_("sub", cliSessionId="s-sub", spawnedFrom={"sessionId": "local_x", "sideSession": True})
            rec_("main", cliSessionId="s-main")
            rec_("old", cliSessionId="s-new", priorCliSessionIds=["s-old"], spawnedFrom={"sideSession": True})
            check("Sub session: a side session is held to it", HG.main_line(st_("opus", "xhigh"), 2, str(pr_), "s-sub") == "main: Opus 5.5 · Extra fits your setting" and HG.main_line(st_("opus", "medium"), 2, str(pr_), "s-sub").startswith("main: suggest Opus 5.5 · Extra, your setting"))
            check("Sub session: a main session stays on Main session", HG.main_line(st_("opus", "medium"), 2, str(pr_), "s-main") == "main: Opus 5.5 · Medium fits your setting")
            check("Sub session: a resumed side session is found by its earlier id", HG.main_line(st_("opus", "xhigh"), 2, str(pr_), "s-old") == "main: Opus 5.5 · Extra fits your setting")
            check("Sub session: no record (not the desktop app) -> Main session applies", HG.main_line(st_("opus", "medium"), 2, str(pr_), "s-none") == "main: Opus 5.5 · Medium fits your setting")
            kept_ = st_("opus", "xhigh"); HG.main_line(kept_, 2, str(pr_), "s-sub"); (apps_ / "local_sub.json").unlink()
            check("Sub session: the answer is kept once found", kept_.get("session_role") == "sub" and HG.main_line(kept_, 2, str(pr_), "s-sub") == "main: Opus 5.5 · Extra fits your setting")
            late_ = st_("opus", "medium"); HG.main_line(late_, 2, str(pr_), "s-late"); rec_("late", cliSessionId="s-late", spawnedFrom={"sideSession": True})
            check("Sub session: no record yet is not kept, it is asked again after a while", "session_role" not in late_ and (late_.update(session_role_at=0) or HG.main_line(late_, 2, str(pr_), "s-late").startswith("main: suggest Opus 5.5 · Extra")))
            (apps_ / "local_bad.json").write_text("s-bad {not json")
            check("Sub session: a broken record reads as not known", HG.app_session_record("s-bad") is None)
            HG.APP_SESSION_DIRS = [Path(td_) / "gone"]
            block_("Main session = opus·medium")
            check("Sub session unset: the app's records are never read", HG.is_sub_session({"session_role": "sub"}, "x") and HG.main_line({"main_model": "opus", "effort": "medium", "session_role": "sub"}, 2, str(pr_), "x") == "main: Opus 5.5 · Medium fits your setting")
            HG.APP_SESSION_DIRS = keep_dirs_
            keep_user_ = HG.USER_CLAUDE_MD; HG.USER_CLAUDE_MD = Path(td_) / "user.md"
            HG.USER_CLAUDE_MD.write_text("## abofles overrides\n\n- Receipt = off\n- Parallel = 7\n")
            block_("Receipt = short")
            by_ = {k["key"]: k for k in HG.pane_overrides(str(pr_), "s1")["keys"]}
            check("pane: each key carries both files' own values, the project's winning", by_["Receipt"]["value"] == "short" and by_["Receipt"]["source"] == "project" and by_["Receipt"]["project"] == "short" and by_["Receipt"]["user"] == "off")
            check("pane: a key one file sets, and one no file sets", by_["Parallel"]["project"] is None and by_["Parallel"]["user"] == "7" and by_["Guide"]["project"] is None and by_["Guide"]["user"] is None)
            HG.USER_CLAUDE_MD = keep_user_

        # Check all: one instruction over every open item; the Needs-you card's needs_fill
        ci_ = lambda t, f: HG.guide_item("🟠", t, f, id=t)
        ca_ = HG.guide_check_all([ci_("a", "abofles: fix the open FAIL on Pay."), ci_("b", "abofles: owner decision 3 is open: cap. Judge it first, then ask me with AskUserQuestion."), ci_("c", "abofles: collect the background agents and print their receipt.")])
        check("check all: no items and one item -> None", HG.guide_check_all([]) is None and HG.guide_check_all([ci_("a", "abofles: x")]) is None)
        check("check all: 3 items -> n 3 and one numbered instruction holding each item's own Use text in ladder order",
              ca_["n"] == 3 and ca_["fill"].startswith("abofles: work through every open item") and ca_["fill"].index("1) fix the open FAIL on Pay.") < ca_["fill"].index("2) owner decision 3 is open: cap.") < ca_["fill"].index("3) collect the background agents"))
        check("check all: Needs-you items are resolved only with evidence via memo.py resolve <n> <note>, owner decisions are asked, not guessed",
              "memo.py" in ca_["fill"] and "resolve <n> --trace \"<note>\"" in ca_["fill"] and "only with evidence" in ca_["fill"] and "AskUserQuestion" in ca_["fill"] and "never guessed" in ca_["fill"])
        cs2_ = HG.guide_check_all([ci_("a", "abofles: fix the open FAIL on Pay."), ci_("b", "abofles: x")], "sess 1")
        check("check all: the resolve hint carries --session <sid> and --trace like the single-item recheck, so a resolve writes the Journal line",
              "resolve <n> --session 'sess 1' --trace \"<note>\"" in cs2_["fill"] and "memo.py" in cs2_["fill"])
        wi_ = lambda i, t, f: HG.guide_item("🧠", t, f, id=i)
        cw_ = HG.guide_check_all([wi_("ctx:600", "c", "/compact"), wi_("memo-age", "m", "/abofles:memo"), wi_("budget", "b", "/abofles:agents"), ci_("z", "abofles: fix it.")])["fill"]
        check("check all: context, memo-age and budget items say what to do in words, not a bare slash command",
              "1) run /compact at a task boundary — the memo saves itself (/abofles:memo first is optional: a sharper Now)." in cw_ and "2) save the work memo now: run /abofles:memo." in cw_
              and "3) agent budget nearly out — finish the rest in the main session, or ask me whether to raise it (/abofles:agents)." in cw_ and not re.search(r"\d\) /abofles:", cw_))
        check("check all: with Memo on compact = off the context item asks for the memo first", "save the work memo with /abofles:memo, then run /compact" in HG.guide_check_all([wi_("ctx:600", "c", "/abofles:memo"), ci_("z", "abofles: fix it.")])["fill"])
        check("check all: a single item's Use fill stays the bare slash command", wi_("budget", "b", "/abofles:agents")["fill"] == "/abofles:agents")
        cdir = tempfile.mkdtemp(); cs_ = "checkall1"; hg_state_ = HG.STATE_DIR; HG.STATE_DIR = Path(state)  # the in-process calls below read and write the session state the subprocess hooks use
        HG.memo_needs(cdir, cs_, "owner decision: cap per unit or per order?")
        run("start", {"session_id": cs_, "cwd": cdir}, led)
        st_ = HG.load_state(cs_); HG.refresh_guide(cdir, st_, cs_); HG.save_state(cs_, st_)
        check("check all: one open item -> guide.all is None and the Needs-you card has no needs_fill", gstate(cs_)["guide"]["all"] is None and gstate(cs_)["pane"]["resume"]["needs_fill"] is None)
        HG.memo_needs(cdir, cs_, "owner decision: round half up or even?")
        st_ = HG.load_state(cs_); st_["receipt_owed"] = True; HG.refresh_guide(cdir, st_, cs_); HG.save_state(cs_, st_)
        ga_ = gstate(cs_)["guide"]["all"]; nf_ = gstate(cs_)["pane"]["resume"]["needs_fill"]
        check("check all: refresh_guide keeps guide.all = {n, fill} for the 2 items (owner decision, receipt owed)", ga_ and ga_["n"] == 2 and "1) owner decision 1 is open: owner decision: cap per unit" in ga_["fill"] and "2) the receipt of this task is still owed" in ga_["fill"])
        check("check all: 2 open Needs-you items -> needs_fill numbers each one and names the evidence rule; below 2 it is None",
              nf_ and nf_.startswith("abofles: work through every open Needs-you item") and "1) owner decision 1 is open: owner decision: cap per unit" in nf_ and "2) owner decision 2 is open: owner decision: round half up" in nf_ and "only with evidence" in nf_)
        gcli_ = lambda *a, c=cdir: subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "guide.py"), *a, "--session", cs_], cwd=c, capture_output=True, text=True, env=dict(os.environ, ABOFLES_LEDGER_DIR=led, ABOFLES_STATE_DIR=state))
        check("guide.py --check-all: prints the same instruction (2 items), --all still lists the items", gcli_("--check-all").stdout.strip() == ga_["fill"] and gcli_("--all").stdout.count("\n") == 2)
        HG.memo_resolve(cdir, 2, "answered in the test", cs_); HG.memo_resolve(cdir, 1, "answered in the test", cs_)
        st_ = HG.load_state(cs_); st_.pop("receipt_owed", None); HG.refresh_guide(cdir, st_, cs_); HG.save_state(cs_, st_)
        check("guide.py --check-all: fewer than 2 open items -> one line saying so", gcli_("--check-all").stdout.startswith("abofles: fewer than 2 open items"))
        cdir9_ = tempfile.mkdtemp()
        for w_ in "alpha bravo charlie delta echo foxtrot golf hotel india juliet".split():
            HG.memo_needs(cdir9_, "cap9", f"owner decision: {w_} setting?")
        nf9_ = HG.pane_resume(cdir9_, "cap9")["needs_fill"]
        check("check all: Needs-you card with 10 open -> 8 numbered, then `+2 more — run Check all again after these`",
              "8) owner decision 8" in nf9_ and "9) " not in nf9_ and nf9_.endswith("+2 more — run Check all again after these"))
        HG.memo_resolve(cdir9_, 10, "x", "cap9"); HG.memo_resolve(cdir9_, 9, "x", "cap9"); HG.memo_resolve(cdir9_, 8, "x", "cap9")
        check("check all: 7 open -> all numbered and no `more` tail", "7) owner decision 7" in HG.pane_resume(cdir9_, "cap9")["needs_fill"] and "more — run Check all again" not in HG.pane_resume(cdir9_, "cap9")["needs_fill"])
        HG.STATE_DIR = hg_state_

        # Decisions: marked by:you / by:judge, parsed back, grouped for the pane
        dd_ = tempfile.mkdtemp(); d1_, d2_ = "decsess1", "decsess2"
        HG.memo_decision(dd_, d1_, "Per unit or per order?", "Per order")
        HG.memo_decision(dd_, d1_, "judge: Is the cap per unit?", "Yes (src/a.cs:12)", by="judge")
        HG.memo_decision(dd_, d1_, "note", "round half up")
        sl_ = HG.session_memo_path(dd_, d1_).read_text(); ml_ = HG.memo_path(dd_).read_text()
        check("decisions: a question writes `- MM-DD HH:MM · by:you · q → a`, a judge's ANSWERED by:judge with its `judge: ` prefix, `memo.py note` by:you",
              re.search(r"^- \d\d-\d\d \d\d:\d\d · by:you · Per unit or per order\? → Per order$", sl_, re.M) and re.search(r"^- \d\d-\d\d \d\d:\d\d · by:judge · judge: Is the cap per unit\? → Yes \(src/a\.cs:12\)$", sl_, re.M)
              and re.search(r"^- \d\d-\d\d \d\d:\d\d · by:you · note → round half up \(s:decsess1 p:[^()\s]+\)$", ml_, re.M))
        check("decisions: parse_decision drops a note's (s:… p:…) tag from the answer", HG.parse_decision(re.search(r"^- .*note → round half up.*$", ml_, re.M).group(0))["a"] == "round half up")
        pdc_ = lambda l: HG.parse_decision(l)
        check("parse_decision: marked lines give ts, q, a, by", pdc_("- 10-27 09:15 · by:judge · judge: Is it? → Yes") == {"ts": "10-27 09:15", "q": "judge: Is it?", "a": "Yes", "by": "judge"}
              and pdc_("- 10-27 09:15 · by:you · Keep it? → No")["by"] == "you")
        check("parse_decision: a line from before the marker is judge when the question starts with `judge: `, else you",
              pdc_("- 10-27 09:15 · judge: Is it? → Yes")["by"] == "judge" and pdc_("- 10-27 09:15 · Per unit or per order? → Per order") == {"ts": "10-27 09:15", "q": "Per unit or per order?", "a": "Per order", "by": "you"})
        HG.memo_decision(dd_, d2_, "Rename a · b → c?", "Yes → do it")
        rt_ = HG.parse_decision([l for l in HG.session_memo_path(dd_, d2_).read_text().splitlines() if "Rename" in l][0])
        check("parse_decision: a question holding ` · ` and ` → ` keeps them (time ends at the first ` · `, the answer starts at the LAST ` → `)", rt_["q"] == "Rename a · b → c? → Yes" and rt_["a"] == "do it" and rt_["by"] == "you" and rt_["ts"].count(" ") == 1)
        check("parse_decision: a line that is not a decision -> None", pdc_("") is None and pdc_("- just a note") is None and pdc_("- 10-27 09:15 · no arrow here") is None and pdc_("- Written 2026-10-27 09:15 · x → y") is None)
        # pane_decisions: main memo + every session file, newest first, mtime breaks ties, last year's month last, 50 per group
        pk_ = tempfile.mkdtemp(); tn_ = time.localtime(); today_ = time.strftime("%m-%d", tn_)
        ly_ = f"{tn_.tm_mon % 12 + 1:02d}-15"  # next month's number: no year in the time, so it is last year's
        HG.memo_write(pk_, {"Decisions": f"- {today_} 08:00 · by:you · note → main note\n- {ly_} 12:00 · by:you · note → last year"})
        HG.sess_write(pk_, "pnsess_a", {"Decisions": f"- {today_} 09:00 · by:you · q-a → a-a\n- {today_} 11:00 · by:judge · judge: q-b → a-b\n- {today_} 10:30 · judge: q-old → a-old"})
        HG.sess_write(pk_, "pnsess_b", {"Decisions": f"- {today_} 10:00 · by:you · q-c → a-c\n- {today_} 09:00 · by:you · q-tie → a-tie\n" + "x" * 5})
        os.utime(HG.session_memo_path(pk_, "pnsess_b"), (time.time() + 100, time.time() + 100))  # newer file: wins the 09:00 tie
        pdn_ = HG.pane_decisions(pk_)
        check("pane_decisions: groups you / judge with n_you, n_judge; items are {ts, q, a, sid}", set(pdn_) == {"you", "judge", "n_you", "n_judge", "others", "others_rows"} and pdn_["n_you"] == 5 and pdn_["n_judge"] == 2 and all(set(x) == {"ts", "q", "a", "sid"} for g in ("you", "judge") for x in pdn_[g]))
        check("pane_decisions: newest first across the main memo and 2 session memos; a month above this one is last year; the newer file wins a tie",
              [x["q"] for x in pdn_["you"]] == ["q-c", "q-tie", "q-a", "note", "note"] and [x["a"] for x in pdn_["you"]][-2:] == ["main note", "last year"] and [x["sid"] for x in pdn_["you"]][:3] == ["pnsess_b", "pnsess_b", "pnsess_a"] and pdn_["you"][3]["sid"] == "")
        check("pane_decisions: an old `judge: ` line without the marker lands in judge; judge items keep the prefix", [x["q"] for x in pdn_["judge"]] == ["judge: q-b", "judge: q-old"])
        big_ = tempfile.mkdtemp()
        HG.sess_write(big_, "bigsess1", {"Decisions": "\n".join(f"- {today_} 10:{i % 60:02d} · by:you · {'q' * 700}{i} → {'a' * 700}" for i in range(55))})
        pb_ = HG.pane_decisions(big_)
        check("pane_decisions: at most 50 per group (n_you counts all 55), q and a clipped to PANE_TEXT", len(pb_["you"]) == 50 and pb_["n_you"] == 55 and all(len(x["q"]) <= HG.PANE_TEXT and len(x["a"]) <= HG.PANE_TEXT for x in pb_["you"]))
        stx_ = {}; HG.refresh_pane(pk_, stx_, "pnsess_a", [])
        check("pane_decisions: the payload carries `decisions` and no place key for it", stx_["pane"]["decisions"] == HG.pane_decisions(pk_, "pnsess_a") and "decisions" not in stx_["pane"]["places"])

        # Budget raise: Budget raise = auto (default) raises an auto budget twice per task; ask / fixed budgets deny
        def bsess(sid_, **kw):
            run("start", {"session_id": sid_, "cwd": cwd}, led)
            json.dump(dict(gstate(sid_), gate=kw.get("gate", "auto"), **({"task_budget": kw["task_budget"]} if "task_budget" in kw else {})), open(Path(state, f"{sid_}.json"), "w"))
        def bspawn(sid_, tier, bg=False):
            pr_ = dict(agent("haiku", "low", prompt=f"abofles: Unit Test / Work · T{tier}\nq\nLine cap: 20", cwd=cwd), session_id=sid_)
            if bg: pr_["tool_input"] = dict(pr_["tool_input"], run_in_background=True)
            out_ = run("pre", pr_, led)
            if allowed(out_):
                run("post", dict(pr_, tool_response={"status": "async_launched", "agentId": f"{sid_}{time.time_ns()}", "prompt": "p", "description": "t"} if bg else good), led)
            return out_
        bsess("braise")
        outs_ = [bspawn("braise", 3) for _ in range(7)]
        check("budget raise: auto Sensitive budget 7 -> the 7 agents run with no raise", all(allowed(o) for o in outs_) and not any("budget raised" in ctx(o) for o in outs_))
        o8_ = bspawn("braise", 3)
        check("budget raise: the 8th agent is allowed and says `budget raised 7 → 11 (auto, raise 1/2)` as the hook's first line", allowed(o8_) and not reason(o8_) and ctx(o8_).startswith("abofles: budget raised 7 → 11 (auto, raise 1/2)"))
        o9_ = [bspawn("braise", 3) for _ in range(3)]
        o12_ = bspawn("braise", 3)
        check("budget raise: agents 9-11 run, the 12th raises 11 → 15 (auto, raise 2/2)", all(allowed(o) and "budget raised" not in ctx(o) for o in o9_) and allowed(o12_) and not reason(o12_) and ctx(o12_).startswith("abofles: budget raised 11 → 15 (auto, raise 2/2)"))
        o13_ = [bspawn("braise", 3) for _ in range(3)]
        o16_ = bspawn("braise", 3)
        br_ = gstate("braise")["budget_raise"]
        check("budget raise: the 16th agent (a third raise) is denied by the unchanged budget_deny with the raised budget in it", all(allowed(o) for o in o13_) and not allowed(o16_) and "agent budget is 15 (auto" in reason(o16_) and "+8 raised" in reason(o16_) and "no Judge" in reason(o16_))
        check("budget raise: st.budget_raise = {extra 8, n 2, log [{from, to, ts}]}", br_["extra"] == 8 and br_["n"] == 2 and [(e["from"], e["to"]) for e in br_["log"]] == [(7, 11), (11, 15)] and all(e["ts"] for e in br_["log"]))
        bg_ = gstate("braise")["guide"]
        check("budget raise: guide.task.raised = 2 and the guide's budget item mentions the auto raises", bg_["task"]["raised"] == 2 and any(i["id"] == "budget" and "raised 2× automatically" in i["text"] for i in bg_["items"]))
        run("receipt", {"session_id": "braise", "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "🧾 **15 Agents · 1 u · Saved 1%** vs Opus 5.5\n- `done` → x\n- `check` → y"}, led)
        check("budget raise: the task end resets the raises", gstate("braise")["budget_raise"] == {"extra": 0, "n": 0, "log": []})
        bsess("bmin")
        om_ = [bspawn("bmin", 1) for _ in range(7)]
        check("budget raise: the step is at least 2 (Minor 2 → 4 → 6), then denied",
              [("budget raised" in ctx(o)) for o in om_[:6]] == [False, False, True, False, True, False] and "budget raised 2 → 4 (auto, raise 1/2)" in ctx(om_[2]) and "budget raised 4 → 6 (auto, raise 2/2)" in ctx(om_[4]) and not allowed(om_[6]) and "agent budget is 6 (auto" in reason(om_[6]))
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Budget raise = ask\n")
        bsess("bask")
        oa_ = [bspawn("bask", 1) for _ in range(3)]
        check("budget raise: Budget raise = ask -> the 3rd Minor agent is denied as today, the ask window opens, nothing raised",
              allowed(oa_[0]) and allowed(oa_[1]) and not allowed(oa_[2]) and "agent budget is 2 (auto" in reason(oa_[2]) and gstate("bask").get("budget_ask_ts") and gstate("bask")["budget_raise"]["n"] == 0)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Agents = 2\n")
        bsess("bfix", gate=None)
        of_ = [bspawn("bfix", 1) for _ in range(3)]
        check("budget raise: an explicit Agents = N is never raised", not allowed(of_[2]) and "agent budget is 2 (Agents = 2)" in reason(of_[2]) and gstate("bfix")["budget_raise"]["n"] == 0)
        Path(cwd, "CLAUDE.md").unlink()
        bsess("btask", gate="task", task_budget=2)
        ot_ = [bspawn("btask", 1) for _ in range(3)]
        check("budget raise: a `This task: N` answer is never raised", not allowed(ot_[2]) and "agent budget is 2 (your answer for this task)" in reason(ot_[2]) and gstate("btask")["budget_raise"]["n"] == 0)
        # caps: the Parallel cap still denies a spawn that would raise (and raises nothing), the session cap still warns after a raise
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Parallel = 1\n")
        bsess("bcap")
        bspawn("bcap", 1); bspawn("bcap", 1, bg=True)
        oc_ = bspawn("bcap", 1, bg=True)
        check("budget raise: a start the Parallel cap refuses raises nothing (cap denial first)", not allowed(oc_) and "already running (Parallel = " in reason(oc_) and gstate("bcap")["budget_raise"]["n"] == 0)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Parallel = 1\n- Session cap = 1\n")
        orz_ = bspawn("bcap", 1)
        check("budget raise: ... a sync start then raises 2 → 4, and the Session cap warning still fires after the raise", allowed(orz_) and "budget raised 2 → 4 (auto, raise 1/2)" in ctx(orz_) and gstate("bcap")["budget_raise"]["n"] == 1)
        pr_ = dict(agent("haiku", "low", prompt="abofles: Unit Test / Work · T1\nq\nLine cap: 20", cwd=cwd), session_id="bcap")
        pr_["tool_input"] = dict(pr_["tool_input"], run_in_background=True)
        check("budget raise: ... and the Parallel cap still denies a background start after a raise", "already running (Parallel = " in reason(run("pre", pr_, led)))
        sc_ = ctx(run("post", dict(agent("haiku", "low", prompt="abofles: Unit Test / Work · T1\nq\nLine cap: 20", cwd=cwd, resp=good), session_id="bcap"), led))
        check("budget raise: session cap passed is still warned after a raise", "session cap 1 units passed" in sc_)
        Path(cwd, "CLAUDE.md").unlink()
        # gate re-answer: Auto again starts a fresh count and clears the raises
        run("decision", {"session_id": "bcap", "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Abo Fles agent gate: how many?", "options": []}]}, "tool_response": {"answers": {"Abo Fles agent gate: how many?": "Auto (recommended)"}}}, led)
        check("budget raise: answering the gate again clears the raises", gstate("bcap")["budget_raise"] == {"extra": 0, "n": 0, "log": []})
        # overrides.py: Budget raise is a plain select
        om_ = tempfile.mkdtemp()
        rm1_ = ovrun(["set", "--scope", "project", "--cwd", om_], {"set": {"Main session": "the big one"}})
        rm2_ = ovrun(["set", "--scope", "project", "--cwd", om_], {"set": {"Sub session": "Opus 5.5 · Extra"}})
        check("overrides set: Main session refuses what the hook could not read (exit 2, one line); the app's spelling is taken", rm1_.returncode == 2 and "opus.medium" in rm1_.stderr and rm2_.returncode == 0 and "- Sub session = Opus 5.5 · Extra" in (Path(om_) / "CLAUDE.md").read_text())
        ob_ = tempfile.mkdtemp()
        rb_ = ovrun(["set", "--scope", "project", "--cwd", ob_], {"set": {"Budget raise": "maybe"}})
        rg_ = ovrun(["set", "--scope", "project", "--cwd", ob_], {"set": {"Budget raise": "Ask"}})
        check("overrides set: Budget raise takes auto | ask (case ignored) and refuses anything else with one line (exit 2)",
              rb_.returncode == 2 and "Budget raise" in rb_.stderr and "auto, ask" in rb_.stderr and len(rb_.stderr.strip().splitlines()) == 1 and rg_.returncode == 0 and (Path(ob_) / "CLAUDE.md").read_text() == "## abofles overrides\n\n- Budget raise = ask\n"
              and {k["key"]: k for k in json.loads(rg_.stdout)["keys"]}["Budget raise"] | {} == {"key": "Budget raise", "kind": "select", "options": ["auto", "ask"], "value": "ask", "source": "project", "default": "auto", "project": "ask", "user": None})
        # overrides.py: a key's old name, `*` bullets and code fences
        ol_ = tempfile.mkdtemp(); Path(ol_, "CLAUDE.md").write_text("## abofles overrides\n- Spawns place = output\n- Break = 5\n")
        rl1_ = ovrun(["set", "--scope", "project", "--cwd", ol_], {"set": {"Agents place": "pane"}}); tl1_ = Path(ol_, "CLAUDE.md").read_text()
        rl2_ = ovrun(["set", "--scope", "project", "--cwd", ol_], {"set": {"Agents place": ""}}); tl2_ = Path(ol_, "CLAUDE.md").read_text()
        check("overrides set: a line under the key's old name (Spawns place) is edited in place under the new name, and Reset removes it",
              rl1_.returncode == 0 and tl1_ == "## abofles overrides\n- Agents place = pane\n- Break = 5\n" and rl2_.returncode == 0 and tl2_ == "## abofles overrides\n- Break = 5\n")
        of_ = tempfile.mkdtemp(); Path(of_, "CLAUDE.md").write_text("## abofles overrides\n```\n- Pace = normal\n```\n* Break = 5\n")
        rf_ = ovrun(["set", "--scope", "project", "--cwd", of_], {"set": {"Pace": "fast", "Break": "9"}})
        check("overrides set: a line in a code fence is not edited (the key is added after the block's last real bullet); a `*` bullet is edited in place",
              rf_.returncode == 0 and Path(of_, "CLAUDE.md").read_text() == "## abofles overrides\n```\n- Pace = normal\n```\n* Break = 9\n- Pace = fast\n")

    # ---- Git agent: the Work cell for a git topic, no fable, Git guard on risky git outside abofles-git, claims.py
    with tempfile.TemporaryDirectory() as led, tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as state:
        os.environ["ABOFLES_STATE_DIR"] = state
        os.environ["ABOFLES_TEST_CWD"] = cwd
        sdir_g = Path(__file__).resolve().parent
        gpre = lambda topic, side, model, effort, n: ctx(run("pre", dict(agent(model, effort, "abofles:abofles-git", prompt=f"abofles: {topic} / {side} · T{n}\nq\nLine cap: 20", cwd=cwd), session_id="git-" + side + str(n) + model + effort), led))
        check("git: T2 git topic at the Work cell (sonnet·medium) -> no cell note, topic known", gpre("Tags, Releases & Changelogs", "Git", "sonnet", "medium", 2) == "")
        o_ = gpre("Tags, Releases & Changelogs", "Git", "opus", "high", 2)
        check("git: a `/ Git` header at T2 is judged against the Work cell (names sonnet·medium)", "sonnet·medium" in o_ and "× the cell" in o_)
        check("git: T4 git topic at the Work cell (opus·high) -> no cell note", gpre("History Rewrite & Force Push", "Git", "opus", "high", 4) == "")
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- History Rewrite & Force Push / Work = haiku·high\n")
        o_ = gpre("History Rewrite & Force Push", "Git", "opus", "high", 4)
        check("git: a `Topic / Work` override is the Git agent's cell too (names haiku·high)", "haiku·high" in o_ and "× the cell" in o_)
        Path(cwd, "CLAUDE.md").unlink()
        check("git: fable refused for abofles-git", "fable is only for" in reason(run("pre", agent("fable", "medium", "abofles:abofles-git", prompt="abofles: Tags, Releases & Changelogs / Git · T2\nq"), led)))
        gr = "STATUS: done\nCONFIDENCE: high\nACTIONS:\n- git tag -a v1.0.0 -m x — tag created\nSTATE: branch main · 0 ahead / 0 behind\nCONFLICTS: none\nLEFT: none"
        o_ = run("post", dict(agent("sonnet", "medium", "abofles:abofles-git", prompt="abofles: Tags, Releases & Changelogs / Git · T2\nq\nLine cap: 20", resp=gr, cwd=cwd), session_id="gitrow"), led)
        rows_ = [json.loads(l) for l in Path(led, "gitrow.jsonl").read_text().splitlines()]
        check("git: a well-formed reply is graded clean and its ledger row is side Git (not a Work row, so no review gate)", warn(o_) == "" and rows_[-1]["side"] == "Git" and not [r for r in rows_ if r["side"] == "Work"])
        o_ = ctx(run("post", dict(agent("sonnet", "medium", "abofles:abofles-git", prompt="abofles: Tags, Releases & Changelogs / Git · T2\nq", resp="STATUS: partial\nCONFIDENCE: high\nACTIONS:\n- git status — clean\nSTATE: main\nCONFLICTS: none\nLEFT: git push --force origin x — needs the user's word", cwd=cwd), session_id="gitrow2"), led))
        check("git: a partial reply with a command left for the caller escalates like a worker's", "escalation rule applies" in o_)
        tf_ = subprocess.run([sys.executable, str(sdir_g / "topic.py"), "find", "merge conflict"], capture_output=True, text=True, cwd=cwd).stdout
        first_ = next((l for l in tf_.splitlines() if l.startswith("1.")), "")
        check("topic: `merge conflict` finds Merge Conflict Resolution first, T3", "Merge Conflict Resolution" in first_ and "(T3)" in first_)
        check("topic: `force push to main` finds History Rewrite & Force Push first", "History Rewrite & Force Push" in next((l for l in subprocess.run([sys.executable, str(sdir_g / "topic.py"), "find", "force push to main"], capture_output=True, text=True, cwd=cwd).stdout.splitlines() if l.startswith("1.")), ""))

        # Git guard
        gg = lambda cmd, atype=None, sid="gg": run("guard", dict({"session_id": sid, "cwd": cwd, "tool_name": "Bash", "tool_input": {"command": cmd}}, **({"agent_type": atype} if atype else {})), led)
        o_ = gg("git push --force origin main")
        check("git guard: a force push from the main session is warned, naming abofles-git", "risky git (push --force)" in ctx(o_) and "abofles:abofles-git" in ctx(o_))
        check("git guard: warn only adds context: no permissionDecision / reason, so the user's permission prompt stays", set(o_["hookSpecificOutput"]) == {"hookEventName", "additionalContext"} and "permissionDecision" not in json.dumps(o_))
        check("git guard: silent for abofles:abofles-git", gg("git push --force origin main", "abofles:abofles-git") is None)
        check("git guard: another plugin's `x:abofles-git`, or a bare `abofles-git` type, is warned (only this plugin's agent is exempt)", "risky git" in ctx(gg("git reset --hard HEAD~1", "evil:abofles-git")) and "risky git" in ctx(gg("git reset --hard HEAD~1", "abofles-git")))
        check("git guard: another agent type is warned", "risky git (reset --hard)" in ctx(gg("git reset --hard", "abofles:abofles-worker")))
        import shlex as _shlex
        _nest = lambda c, n: c if n == 0 else _nest("bash -c " + _shlex.quote(c), n - 1)
        check("git guard: fails closed — git nested deeper than SHELL_DEPTH shells is flagged, not passed unread", "not read" in ctx(gg(_nest("git reset --hard", 7))) and "not read" in ctx(gg("eval " * 7 + "git reset --hard")))
        check("git guard: 6 nested shells are still read through to the op", "risky git (reset --hard)" in ctx(gg(_nest("git reset --hard", 6))))
        for cmd_ in ("setsid git reset --hard", "flock /tmp/l git push -f", "taskset -c 0 git reset --hard", "chrt 10 git push --force", "unbuffer git reset --hard",
                     "strace -o x git reset --hard", "coproc git reset --hard", "f() { git reset --hard; }; f", '$"git" reset --hard', "git rm -rf src"):
            check(f"git guard: {cmd_!r} is flagged", "risky git" in ctx(gg(cmd_)))
        check("git guard: an alias defined from the environment (GIT_CONFIG_PARAMETERS / GIT_CONFIG_KEY_n) is flagged",
              "via the environment" in ctx(gg("GIT_CONFIG_PARAMETERS=\"'alias.x=reset --hard'\" git x")) and "via the environment" in ctx(gg("GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='reset --hard' GIT_CONFIG_COUNT=1 git x")))
        check("git guard: backstop — git inside a runner it cannot read (su -c, script, python -c) is flagged as not fully parsed",
              all("not fully parsed" in ctx(gg(c)) for c in ("su me -c 'git reset --hard'", "script -q /dev/null git reset --hard", "python3 -c \"import subprocess;subprocess.run(['git','reset','--hard'])\"")))
        check("git guard: the backstop leaves text alone (echo, grep, a gh PR title, git rm --cached)",
              all(gg(c) is None for c in ("grep -r 'git reset --hard' docs", "gh pr create --title 'fix git reset --hard docs'", "git rm --cached a.py", "cat .gitignore")))
        check("git guard review: a tree-ish checkout over paths, pull --rebase, a forced submodule deinit and a git named by a variable or substitution are flagged",
              all("risky git" in ctx(gg(c)) for c in ("git checkout HEAD file.txt", "git checkout main~2 src/a.cs",
                                                       "git pull --rebase", "git pull -r origin main", "git submodule deinit -f lib", "$(echo git) push --force", "`echo git` push -f", "G=git; $G push -f")))
        check("git guard review: ... while a reset that keeps the changes, a branch switch, a new branch from a remote, a plain pull and a captured git output stay silent",
              all(gg(c) is None for c in ("git reset", "git reset HEAD~3", "git reset HEAD file.txt", "git reset -- src/a.cs", "git reset src/a.cs", "git checkout main", "git checkout -b feat origin/main",
                                          "git pull", "git pull --no-rebase", "git submodule deinit lib", "x=$(git rev-parse HEAD) && echo $x")))
        _cr = tempfile.mkdtemp(); subprocess.run(["git", "init", "-q", _cr]); claims_py_ = str(Path(__file__).resolve().parent / "claims.py")
        check("claims: an option is never another option's value (`--session --branch x` exits 2)",
              subprocess.run([sys.executable, claims_py_, "claim", "--session", "--branch", "x"], cwd=_cr, capture_output=True, text=True).returncode == 2
              and subprocess.run([sys.executable, claims_py_, "claim", "--session", "s1", "--branch", "--files"], cwd=_cr, capture_output=True, text=True).returncode == 2)
        check("git guard: a line that breaks the parser (1000 heredocs) is flagged as not fully parsed, never passed", "not fully parsed" in ctx(gg("git reset --hard " + "<<A " * 1000)))
        for cmd_ in ("git status", "git log --oneline -n 5", "git diff -- .", "git branch -d old", "git fetch --all", 'git commit -m "reset --hard docs"', "git commit -m 'push --force and rebase'",
                     "git rebase --continue", "git rebase --abort", "git restore --staged a.py", "git clean -n", "git stash push -m drop", 'git log --grep="reset --hard"', 'echo "git push --force"',
                     "git commit -F - <<'EOF'\nfix\n\ngit reset --hard\nEOF"):
            check(f"git guard: silent for {cmd_.splitlines()[0]}", gg(cmd_) is None)
        check("git guard: `git -C x reset --hard` is caught", "risky git (reset --hard)" in ctx(gg("git -C x reset --hard")))
        check("git guard: `cd a && git rebase main` is caught", "risky git (rebase)" in ctx(gg("cd a && git rebase main")))
        for cmd_, op_ in (("git branch -D old", "branch -D"), ("git clean -fd", "clean -f"), ("git checkout -- src/a.cs", "checkout -- <paths>"), ("git checkout .", "checkout ."), ("git restore src/a.cs", "restore"),
                          ("git stash drop", "stash drop"), ("git filter-repo --path x", "filter-repo"), ("git commit --amend --no-edit", "commit --amend"), ("git update-ref -d refs/x", "update-ref -d"),
                          ("git gc --prune=now", "gc --prune=now"), ("git push origin +main", "push +main"), ("bash -c 'git reset --hard'", "reset --hard"), ("FOO=1 git push -f", "push -f")):
            check(f"git guard: {cmd_} -> {op_}", f"risky git ({op_})" in ctx(gg(cmd_)))
        for cmd_, op_ in (("git push --delete origin old", "push --delete"), ("git push -d origin old", "push --delete"), ("git push origin :old", "push :<ref>"), ("git push --mirror backup", "push --mirror"),
                          ("git tag -d v1", "tag -d"), ("git tag --delete v1", "tag -d"), ("git checkout -f main", "checkout -f"), ("git checkout --force main", "checkout -f"),
                          ("git switch -f main", "switch -f"), ("git switch --force main", "switch -f"), ("git switch --discard-changes main", "switch -f"), ("git reset --merge", "reset --merge"),
                          ("sudo -u deploy git push -f", "push -f"), ("env -i -u HOME GIT_X=1 git reset --hard", "reset --hard"), ("nice -n 5 git push --force", "push --force"),
                          ("echo a | xargs -n 1 -I X git tag -d X", "tag -d"), ("bash -lc 'git reset --hard'", "reset --hard"), ("sh -ec 'git push -f'", "push -f"),
                          ("sudo bash -lc \"git push origin :old\"", "push :<ref>"), ("zsh -Lc 'git reset --merge'", "reset --merge"), ("bash -xc 'git push --mirror o'", "push --mirror")):
            check(f"git guard: {cmd_} -> {op_}", f"risky git ({op_})" in ctx(gg(cmd_)))
        for cmd_ in ("git push origin main", "git push -u origin feature", "git tag v1", "git tag -l", "git checkout -b x", "git switch -c x", "git switch main", "git reset --soft HEAD~1", "sudo git status", "bash -lc 'git status'"):
            check(f"git guard: silent for {cmd_}", gg(cmd_) is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Git guard = deny\n")
        o_ = gg("git push --force")
        check("git guard: Git guard = deny blocks it with the same text", o_["hookSpecificOutput"]["permissionDecision"] == "deny" and "risky git (push --force)" in reason(o_))
        check("git guard: Git guard = deny still lets abofles-git and read-only git through", gg("git push --force", "abofles:abofles-git") is None and gg("git status") is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Git guard = off\n")
        check("git guard: Git guard = off is silent", gg("git push --force") is None)
        Path(cwd, "CLAUDE.md").write_text("## abofles overrides\n- Git guard = sometimes\n")
        check("git guard: an unknown value falls back to warn", "risky git" in ctx(gg("git push --force")))
        Path(cwd, "CLAUDE.md").unlink()

        # security review (T4): git guard forms, question gate, memo, repo root
        for cmd_, op_ in (("g\\it reset --hard", "reset --hard"), ('gi""t reset --hard', "reset --hard"), ("GIT reset --hard", "reset --hard"),
                          ("git branch -df x", "branch -D"), ("git branch -d -f x", "branch -D"), ("git branch --delete --force x", "branch -D"),
                          ("if true; then git reset --hard; fi", "reset --hard"), ("while git reset --hard; do :; done", "reset --hard"), ("until git push -f; do :; done", "push -f"),
                          ("! git reset --hard", "reset --hard"), ("timeout 5 git reset --hard", "reset --hard"), ("timeout -s KILL 5 git push -f", "push -f"),
                          ("stdbuf -o0 git reset --hard", "reset --hard"), ("doas git reset --hard", "reset --hard"), ("caffeinate -i git reset --hard", "reset --hard"),
                          ("eval \"git reset --hard\"", "reset --hard"), ("eval git push --force", "push --force"), ("ksh -c 'git reset --hard'", "reset --hard"),
                          ("fish -c 'git reset --hard'", "reset --hard"), ("bash -c -- 'git reset --hard'", "reset --hard"), ("sudo --user root git reset --hard", "reset --hard"),
                          ("env --unset X git reset --hard", "reset --hard"), ("env -S 'git reset --hard'", "reset --hard"), ("bash <<< 'git reset --hard'", "reset --hard"),
                          ("bash <<<\"git reset --hard\"", "reset --hard"), ("echo 'git reset --hard' | sh", "reset --hard"), ("printf 'git push -f' | bash -s", "push -f"),
                          ("bash <(echo git reset --hard)", "reset --hard"), ("bash <<'EOF'\ngit reset --hard\nEOF", "reset --hard"), ("cat <<EOF | sh\ngit reset --hard\nEOF", "reset --hard"),
                          ("find . -name x -exec git reset --hard \;", "reset --hard"), ("git submodule foreach git reset --hard", "reset --hard"),
                          ("git submodule foreach --recursive 'git clean -fdx'", "clean -f"), ("bash -c 'bash -c \"bash -c \\\"git reset --hard\\\"\"'", "reset --hard"),
                          ("git -c alias.x='reset --hard' x", "reset --hard"), ("git -c alias.x='!git push -f' x", "push -f"), ("git config alias.nuke 'reset --hard'", "config alias.*"),
                          ("git config --global alias.p 'push -f'", "config alias.*"), ("git checkout -B main origin/main", "checkout -B"), ("git switch -C main origin/main", "switch -C"),
                          ("git branch -f main HEAD~3", "branch -f"), ("git branch -M main", "branch -M"), ("git tag -f v1", "tag -f"), ("git reset --keep HEAD~1", "reset --keep"),
                          ("git gc --prune=all", "gc --prune=all"), ("git reflog expire --expire=now --all", "reflog expire"), ("git update-ref refs/heads/main HEAD~5", "update-ref <ref> <new>"),
                          ("git update-ref refs/heads/main abc def", "update-ref <ref> <new>"), ("git push --prune origin", "push --prune"), ("git worktree remove -f x", "worktree remove --force"),
                          ("git worktree remove --force x", "worktree remove --force"), ("git stash clear", "stash clear"), ("git stash -q drop", "stash drop"), ("git filter-branch --tree-filter x", "filter-branch")):
            check(f"git guard (review): {cmd_.splitlines()[0]} -> {op_}", f"risky git ({op_})" in ctx(gg(cmd_)))
        for cmd_ in ("git branch -d old", "git branch -m old new", "git stash push -m drop", "git stash -m drop", "git config --get alias.x", "git config alias.x", "git config user.name x",
                     "git worktree remove x", "git update-ref -m msg", "echo 'git reset --hard' > notes.md", "echo 'git reset --hard' | grep reset", "cat <<EOF\ngit reset --hard\nEOF",
                     "git reflog", "git reflog expire --expire=90.days", "git gc", "git tag -a v2 -m 'fix'", "timeout 5 git status", "eval git status", "bash -c -- 'git log'", "git checkout -b x"):
            check(f"git guard (review): silent for {cmd_.splitlines()[0]}", gg(cmd_) is None)
        import importlib.util as _iusr
        os.environ["ABOFLES_LEDGER_DIR"] = led
        _hsr = _iusr.spec_from_file_location("abofles_hook_sr", str(HOOK)); SR = _iusr.module_from_spec(_hsr); _hsr.loader.exec_module(SR)
        def qsess(sid_, **kw):
            run("start", {"session_id": sid_, "cwd": cwd}, led)
            json.dump(dict(json.loads(Path(state, f"{sid_}.json").read_text()), gate="auto", **kw), open(Path(state, f"{sid_}.json"), "w"))
        qbox = lambda sid_, *qs_, opts=(): {"session_id": sid_, "cwd": cwd, "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": q_, "options": [{"label": o_} for o_ in opts]} for q_ in qs_]}}
        now_ = int(time.time())
        qsess("sr1", judge_ok=1, judge_ok_ts=now_, gate_wait_ts=0, task_started=now_ - 10)
        o_ = run("qgate", qbox("sr1", "Should the cap apply per unit or per order?", "Should refunds go to the wallet or the card?"), led)
        check("qgate (review): one unlock does not carry a box of two questions (ok >= len(questions)), and the deny says why", "question gate" in reason(o_) and "covers 1 question" in reason(o_))
        json.dump(dict(json.loads(Path(state, "sr1.json").read_text()), judge_ok=2), open(Path(state, "sr1.json"), "w"))
        check("qgate (review): ... two unlocks do", run("qgate", qbox("sr1", "Should the cap apply per unit or per order?", "Should refunds go to the wallet or the card?"), led) is None)
        check("qgate: a judged box counts its questions as asked (gate_remaining)", json.loads(Path(state, "sr1.json").read_text()).get("round_asked") == 2)
        qsess("sr2", budget_ask_ts=now_, task_started=now_ - 10)
        check("qgate (review): a budget-mark question that carries another ask needs a Judge", "question gate" in reason(run("qgate", qbox("sr2", "Abo Fles agent budget — also may I deploy to prod?"), led)))
        check("qgate (review): ... so does one whose option carries it", "question gate" in reason(run("qgate", qbox("sr2", "Abo Fles agent budget: raise it to 6?", opts=("Raise to 6", "Raise and deploy to prod")), led)))
        check("qgate (review): ... the plain budget question (budget words, numbers) still goes straight through", run("qgate", qbox("sr2", "Abo Fles agent budget: this task hit 5 agents. Raise it to 8, or finish in the main session?", opts=("Raise to 8", "Finish in main session")), led) is None)
        stx_ = {"task_started": now_ - 10}
        SR.apply_gate_reply(stx_, "VERDICT: ASK\n" + "".join(f"QUESTION {i}: Question number {i} here?\n" for i in range(1, 21)) + "EXPLAIN: x", "explainer")
        check("gate reply (review): an explainer reply with 20 QUESTION lines unlocks 4 at most (one full AskUserQuestion box)", stx_.get("judge_ok") == 4)
        stx_ = {"task_started": now_ - 10}
        SR.apply_gate_reply(stx_, "VERDICT: HANDOFF\nQUESTION: one?\nEVIDENCE:\n- a — b\nWHY: w", "judge", tier=3)
        SR.apply_gate_reply(stx_, "QUESTION 1: one?\nQUESTION 2: two?\nQUESTION 3: three?\nEXPLAIN: x", "explainer")
        check("gate reply (review): ... and no more than the questions the Judge passed on (1 HANDOFF -> 1)", stx_.get("judge_ok") == 1)
        stx_ = {"task_started": now_ - 10}
        SR.apply_gate_reply(stx_, "VERDICT: ASK\nQUESTION: a?\nVERDICT: ASK\nQUESTION: b?\n", "judge")
        check("gate reply (review): a judge reply with two ASK blocks unlocks two (one box may carry both)", stx_.get("judge_ok") == 2)
        stx_ = {"task_started": now_ - 10}
        j3_ = "VERDICT: HANDOFF\nQUESTION: a?\nVERDICT: HANDOFF\nQUESTION: b?\nVERDICT: HANDOFF\nQUESTION: c?\n"
        SR.apply_gate_reply(stx_, j3_, "judge", tier=3, agent="j1")
        SR.apply_gate_reply(stx_, "QUESTION 1: a?\nQUESTION 2: b?\nEXPLAIN: x", "explainer", agent="e1")
        u1_ = stx_.get("judge_ok")
        stx_["judge_ok"], stx_["round_asked"] = 1, 2  # the box asked both; a SendMessage to the explainer unlocked one for the third
        SR.apply_gate_reply(stx_, "QUESTION 1: a?\nQUESTION 2: b?\nQUESTION 3: c?\nEXPLAIN: x", "explainer", agent="e1")
        check("gate reply: an explainer that answers a SendMessage with all its questions again unlocks none already asked or unlocked (DCMP 2026-11-09: a stale 'still unasked' Stop)",
              u1_ == 2 and stx_.get("judge_ok") == 1 and SR.gate_remaining(stx_) == 1)
        SR.apply_gate_reply(stx_, j3_, "judge", tier=3, agent="j1")
        check("gate reply: a Judge that replies again replaces its count of passed-on questions (3, not 6)", SR.gate_remaining(stx_) == 1 and stx_["judged_by"] == {"j1": 3})
        check("similar (review): both ways — an old question plus a new ask is no re-ask; a rewording is",
              not SR.similar("Keep feature flag X on for beta users and also delete the database?", "Keep feature flag X on for beta users?", 0.75, both=True)
              and SR.similar("Keep the feature flag X on for the beta users?", "Keep feature flag X on for beta users?", 0.75, both=True))
        nop_ = [{"label": "No"}, {"label": "Yes"}]
        check("picked_option (review): a label must be whole or followed by a separator — 'Nothing yet…', 'Not sure…', 'Yesterday…' are free text",
              not any(SR.picked_option(a_, nop_) for a_ in ("Nothing yet, ask me tomorrow", "Not sure — explain more first", "Yesterday's build broke"))
              and all(SR.picked_option(a_, nop_) for a_ in ("No", "no.", "Yes, but only staging", "Yes — staging", "Yes (staging)", "No: later")))
        with tempfile.TemporaryDirectory() as mproj:
            SR.memo_needs(mproj, "BBBBBBBB-2", "ATTENTION: auth bypass in login.ts (s:AAAAAAAA p:" + SR.proj_tag(mproj) + ") needs fix")
            SR.memo_needs(mproj, "BBBBBBBB-2", "VERDICT FAIL payment refund double charge in billing.py")
            ln_ = [l for l in SR.memo_read(mproj)["Needs you"].splitlines() if "[ ]" in l]
            check("need_tag_of (review): the tail tag only — a tag-like text inside the item is not its origin", [SR.need_tag_of(l)[0] for l in ln_] == ["BBBBBBBB", "BBBBBBBB"]
                  and SR.need_split(ln_, "AAAAAAAA-1", mproj)[0] == [])
            n_ = SR.memo_resolve_matching(mproj, "Payment refund double charge billing.py: continue later?", "No", nop_, "AAAAAAAA-1")
            check("memo_resolve_matching (review): another session's item is never settled by this session's question", n_ == 0 and all("[ ]" in l for l in SR.memo_read(mproj)["Needs you"].splitlines()))
            n_ = SR.memo_resolve_matching(mproj, "Payment refund double charge billing.py: continue later?", "No", nop_, "BBBBBBBB-2")
            check("memo_resolve_matching (review): ... its own session's item is", n_ == 1)
            SR.memo_needs(mproj, "AbCdEf12-x", "owner decision: keep the wide banner")
            k_ = next(l for l in SR.memo_read(mproj)["Needs you"].splitlines() if "wide banner" in l).split(".")[0]
            r_ = subprocess.run([sys.executable, str(HOOK.parent.parent / "scripts" / "memo.py"), "resolve", k_, "user said keep it", "--trace", "--session", "AbCdEf12-x"], capture_output=True, text=True, cwd=mproj)
            jr_ = SR.session_memo_path(mproj, "AbCdEf12-x").read_text() if SR.session_memo_path(mproj, "AbCdEf12-x").exists() else ""
            check("memo.py resolve (review): the trace strips an upper-case session tag too (one tag pattern with the hook)", r_.returncode == 0 and "keep the wide banner" in jr_ and "(s:AbCdEf12" not in jr_)
        with tempfile.TemporaryDirectory() as rt_:
            Path(rt_, "victim").mkdir(); Path(rt_, "evil", "sub").mkdir(parents=True)
            Path(rt_, "evil", ".git").write_text(f"gitdir: {Path(rt_, 'victim', '.git', 'worktrees', 'x')}\n")
            check("repo_top (review): a `.git` file pointing at a folder with no real repository counts as its own folder", SR.repo_top(str(Path(rt_, "evil", "sub"))) == os.path.realpath(Path(rt_, "evil")))
            Path(rt_, "victim", ".git", "worktrees", "x").mkdir(parents=True); Path(rt_, "victim", ".git", "HEAD").write_text("ref: refs/heads/main\n")
            check("repo_top (review): ... one pointing into a real repository's worktrees with no back-pointer still counts as its own folder", SR.repo_top(str(Path(rt_, "evil", "sub"))) == os.path.realpath(Path(rt_, "evil")))
            Path(rt_, "victim", ".git", "worktrees", "x", "gitdir").write_text(str(Path(rt_, "evil", ".git")) + "\n")
            check("repo_top (review): ... a real worktree (its gitdir file points back at this `.git`) maps to the main repository", SR.repo_top(str(Path(rt_, "evil", "sub"))) == os.path.realpath(Path(rt_, "victim")))
        # the Stop hook's receipt demand waits while a spawn of the task runs
        rr_ = lambda sid_: run("receipt", {"session_id": sid_, "cwd": cwd, "stop_hook_active": False, "last_assistant_message": "all done"}, led)
        for sid_, kw_ in (("rw1", {}), ("rw2", {"inflight_bg": 1}), ("rw3", {"running_bg": 1})):
            run("start", {"session_id": sid_, "cwd": cwd}, led)
            json.dump(dict(json.loads(Path(state, f"{sid_}.json").read_text()), gate="auto", spawns=8, spawns_since_receipt=8, **kw_), open(Path(state, f"{sid_}.json"), "w"))
        Path(led, "rw1.jsonl").write_text(json.dumps({"ts": int(time.time()), "session": "rw1", "agent_id": "bgw1", "status": "pending", "agent": "abofles:abofles-worker", "side": "Work"}) + "\n")
        check("receipt (review): no receipt demand while a background agent of the task is pending", "no receipt" not in json.dumps(rr_("rw1") or {}))
        check("receipt (review): ... nor while a background launch is still in flight (inflight_bg)", "no receipt" not in json.dumps(rr_("rw2") or {}))
        o_ = json.dumps(rr_("rw3") or {}, ensure_ascii=False)
        check("receipt (review): with nothing running the demand comes, and its header claims no stale `· 1 running`", "no receipt" in o_ and "1 running" not in o_)
        os.environ.pop("ABOFLES_LEDGER_DIR", None)

    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as wt_parent:
        claims = str(Path(__file__).resolve().parent / "claims.py")
        cl = lambda *a, cwd=repo: subprocess.run([sys.executable, claims, *a], capture_output=True, text=True, cwd=cwd)
        genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", repo], check=True, env=genv)
        subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=genv)
        check("claims: list with no claim says so", cl("list").stdout.strip() == "no claims")
        with tempfile.TemporaryDirectory() as par_:
            api_ = Path(par_, "apps", "api"); api_.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(api_)], check=True)
            Path(par_, "node_modules", "x").mkdir(parents=True); subprocess.run(["git", "init", "-q", str(Path(par_, "node_modules", "x"))], check=True)
            o_ = cl("claim", "--session", "s1", "--branch", "b1", cwd=par_)
            check("claims: from a folder above a repository a claim is refused and names the repository and --repo", o_.returncode == 2 and "not in a git repository" in o_.stdout and "apps/api" in o_.stdout and "--repo" in o_.stdout and "node_modules" not in o_.stdout)
            o_ = cl("claim", "--session", "s1", "--branch", "b1", "--repo", str(api_), cwd=par_)
            check("claims: --repo claims in that repository's store", o_.returncode == 0 and Path(api_, ".git", "abofles-claims.json").exists())
            o_ = cl("list", cwd=par_)
            check("claims: list from above shows each repository's claims", o_.returncode == 0 and "apps/api:" in o_.stdout and "s1" in o_.stdout and "b1" in o_.stdout)
        check("claims: an empty or bare --repo refuses, never the current folder's repository", all(cl("list", *a).returncode == 2 and "--repo needs a path" in cl("list", *a).stdout for a in (["--repo", ""], ["--repo"])))
        with tempfile.TemporaryDirectory() as bare_:
            o_ = cl("list", cwd=bare_)
            check("claims: a folder with no repository at all still says so", o_.returncode == 2 and o_.stdout.strip() == "abofles: not in a git repository")
        _lk = Path(repo, ".git", "abofles-claims.lock"); _victim = Path(repo, "victim.txt"); _victim.write_text("keep")
        if hasattr(os, "O_NOFOLLOW") and not _lk.exists():
            _lk.symlink_to(_victim)
            cl("list")
            check("claims: a planted lock symlink is never followed or truncated", _victim.read_text() == "keep")
            _lk.unlink()
        cl("claim", "--session", "s0\u2028x\u200b\u061c\ufeff", "--branch", "z")
        _o = cl("list").stdout
        check("claims: line/paragraph separators, zero-width and ALM marks are stripped on output", not any(ch in _o for ch in "\u2028\u2029\u200b\u200c\u200d\u061c\ufeff"))
        cl("release", "--session", "s0\u2028x\u200b\u061c\ufeff")
        r_ = cl("claim", "--session", "s1", "--branch", "feat/a", "--files", "src/a.cs", "docs/")
        check("claims: claim records it (exit 0) in the repository's .git, outside the work tree", r_.returncode == 0 and Path(repo, ".git", "abofles-claims.json").exists() and not (Path(repo, "abofles-claims.json")).exists())
        l_ = cl("list").stdout
        check("claims: list prints one line per claim with branch and files, no overlap", len(l_.strip().splitlines()) == 1 and "s1" in l_ and "feat/a" in l_ and "src/a.cs" in l_ and "overlap" not in l_)
        r_ = cl("claim", "--session", "s2", "--branch", "feat/b", "--files", "src/a.cs")
        l_ = cl("list").stdout
        check("claims: two sessions on one file -> claim exit 3, both lines flag the file", r_.returncode == 3 and "overlap" in r_.stdout and all("overlap: file src/a.cs with" in x for x in l_.strip().splitlines()) and len(l_.strip().splitlines()) == 2)
        cl("claim", "--session", "s3", "--branch", "feat/b")
        l_ = cl("list").stdout
        check("claims: two sessions on one branch are flagged", any(x.startswith("s3") and "overlap: branch feat/b with s2" in x for x in l_.splitlines()))
        cl("claim", "--session", "s4", "--branch", "feat/d", "--files", "docs/guide.md")
        check("claims: a folder claims what is under it", any(x.startswith("s4") and "overlap: file docs/guide.md with s1" in x for x in cl("list").stdout.splitlines()))
        cl("claim", "--session", "s5", "--branch", "feat/e", "--files", "src")
        check("claims: a folder claim `src` clashes with `src/a.cs` both ways, equal paths clash, `src2/x` does not",
              any(x.startswith("s5") and "file src with s1" in x for x in cl("list").stdout.splitlines()) and any(x.startswith("s1") and "file src/a.cs with s5" in x for x in cl("list").stdout.splitlines())
              and "file src with" in cl("list").stdout)
        cl("claim", "--session", "s6", "--branch", "feat/f", "--files", "src2/x")
        check("claims: `src` does not clash with `src2/x`", not any(x.startswith("s6") and "overlap" in x for x in cl("list").stdout.splitlines()))
        cl("release", "--session", "s5"); cl("release", "--session", "s6")
        wt = str(Path(wt_parent, "wt"))
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", "feat/wt", wt], check=True, env=genv)
        check("claims: a worktree shares the same store", "s1" in cl("list", cwd=wt).stdout)
        check("claims: release drops the claim, a second release says so (exit 1)", cl("release", "--session", "s2").returncode == 0 and "s2 " not in cl("list").stdout and cl("release", "--session", "s2").returncode == 1)
        store = Path(repo, ".git", "abofles-claims.json")
        data_ = json.loads(store.read_text())
        for c_ in data_["claims"]:
            if c_["session"] == "s1":
                c_["beat"] = int(time.time()) - 3 * 3600
            if c_["session"] == "s3":
                c_["beat"] = int(time.time() - 1.9 * 3600)
        store.write_text(json.dumps(data_))
        l_ = cl("list").stdout
        check("claims: a claim without a heartbeat for 3 h drops out, one at 1.9 h stays", not any(x.startswith("s1 ") for x in l_.splitlines()) and any(x.startswith("s3 ") for x in l_.splitlines()))
        check("claims: heartbeat on a dropped claim says so (exit 1)", cl("heartbeat", "--session", "s1").returncode == 1)
        check("claims: heartbeat refreshes a live claim", cl("heartbeat", "--session", "s3").returncode == 0 and any(x.startswith("s3 ") and "beat 0m ago" in x for x in cl("list").stdout.splitlines()))
        check("claims: a claim needs --session and --branch (exit 2)", cl("claim", "--branch", "x").returncode == 2 and cl("claim", "--session", "s9").returncode == 2)
        store.write_text("{not json")
        r_ = cl("list")
        bak_ = Path(repo, ".git", "abofles-claims.json.bak")
        check("claims: a damaged store is backed up to .bak, warned once on stderr, and read as no claims", r_.returncode == 0 and "no claims" in r_.stdout and r_.stderr.count("damaged") == 1 and bak_.read_text() == "{not json")
        store.write_text(json.dumps({"claims": [{"session": "s5", "branch": "b\x1b[31m\nx", "files": ["a\x07.cs"], "beat": "oops"}, {"session": "s6\x1b[2J", "branch": "ok", "beat": str(int(time.time()))}]}))
        l_ = cl("list").stdout
        check("claims: a bad beat value is stale; control characters are stripped when printing", "s5" not in l_ and "s6[2J" in l_ and "\x1b" not in l_ and cl("list").returncode == 0)
        store.write_text(json.dumps({"claims": [{"session": "s7", "branch": "b\x1b[31m\nx", "files": ["a\x07.cs"], "beat": int(time.time())}]}))
        l_ = cl("list").stdout
        check("claims: control characters in branch and file are stripped", "\x1b" not in l_ and "\x07" not in l_ and "b[31mx" in l_ and "a.cs" in l_ and len(l_.strip().splitlines()) == 1)
        store.write_text(json.dumps({"claims": [{"session": "s8\u202e", "branch": "b\x9b31m\u2066x\u200f", "files": ["a\x85.cs"], "beat": int(time.time())}]}))
        l_ = cl("list").stdout
        check("claims (review): C1 controls and bidi marks are stripped when printing", not re.search("[\x80-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]", l_) and "s8 " in l_ and "b31mx" in l_ and "a.cs" in l_)
        r_ = subprocess.run([sys.executable.encode(), claims.encode(), b"claim", b"--session", b"s9", b"--branch", b"b\xff"], capture_output=True, cwd=repo)
        check("claims (review): a non-UTF-8 argument exits 2, leaves no .tmp and does not touch the store", r_.returncode == 2 and not list(Path(repo, ".git").glob("abofles-claims.json.*.tmp")) and "s9" not in store.read_text())
        bak_.unlink(); victim_ = Path(wt_parent, "victim.txt"); victim_.write_text("keep"); os.symlink(victim_, bak_)
        store.write_text("{damaged again")
        cl("list")
        check("claims (review): the .bak copy never writes through a planted symlink", victim_.read_text() == "keep" and not bak_.is_symlink() and bak_.read_text() == "{damaged again")
        store.write_text("")
        r_ = cl("list")
        check("claims: an empty store is no claims, no warning", "no claims" in r_.stdout and not r_.stderr)
        with tempfile.TemporaryDirectory() as nogit:
            check("claims: outside a git repository -> exit 2", cl("list", cwd=nogit).returncode == 2)

    # ── memo isolation: one project's or session's items are never "yours" in another ──
    with tempfile.TemporaryDirectory() as ih:
        os.environ["ABOFLES_STATE_DIR"] = str(Path(ih, "st")); os.environ["ABOFLES_CONFIG"] = str(Path(ih, "config.json"))
        os.environ.pop("ABOFLES_SESSION", None)
        import importlib.util as _iu4
        _hs4 = _iu4.spec_from_file_location("abofles_hook_iso", str(HOOK)); M = _iu4.module_from_spec(_hs4); _hs4.loader.exec_module(M)
        ledi = str(Path(ih, "led")); Path(ledi).mkdir()
        isdir = Path(__file__).resolve().parent
        RA, RB, NG = Path(ih, "RA"), Path(ih, "RB"), Path(ih, "NG")  # two repositories and a plain folder that is in no repository (the qi-card/DRP case)
        for d in (RA, RB, NG):
            d.mkdir()
        for d in (RA, RB):
            subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
        ienv = dict(os.environ, ABOFLES_LEDGER_DIR=ledi, ABOFLES_STATE_DIR=str(Path(ih, "st"))); ienv.pop("ABOFLES_SESSION", None)
        mpy = lambda *a, cwd=RA, stdin=None: subprocess.run([sys.executable, "-X", "utf8", str(isdir / "memo.py"), *a], cwd=cwd, capture_output=True, text=True, env=ienv, input=stdin)
        ist = lambda sid_: json.loads(Path(ih, "st", f"{sid_}.json").read_text())
        needs = lambda root: [l for l in M.memo_read(str(root))["Needs you"].splitlines() if l.strip()]
        # two sessions in one root: a write names its session, the other cannot settle its items without --force
        r = M.memo_apply(str(RA), "now", "", text="Task      x")
        check("isolation: memo_apply refuses an empty session and writes nothing", not r and r.code == "empty-sid" and not Path(RA, ".claude").exists())
        check("isolation: memo.py now / note / resolve / reopen with no session exit 4 and write nothing",
              all(mpy(*a, cwd=NG, stdin="Task x").returncode == 4 for a in (("now",), ("note", "x"), ("resolve", "1", "ok"), ("reopen", "1"))) and not Path(NG, ".claude").exists())
        run("start", {"session_id": "isoAAAAA-1", "cwd": str(RA)}, ledi); run("start", {"session_id": "isoBBBBB-2", "cwd": str(RA)}, ledi)
        M.memo_needs(str(RA), "isoAAAAA-1", "owner decision: pick the cap rule?")
        la = needs(RA)[0]
        check("isolation: a new item carries `#<4 hex id> (s:<session> p:<project>)` in its tail", re.search(r" #[0-9a-f]{4} \(s:isoAAAAA p:RA\)$", la) and M.need_id(la) == re.search(r"#([0-9a-f]{4})", la).group(1))
        r = mpy("resolve", "1", "ok", "--session", "isoBBBBB-2")
        check("isolation: session B cannot resolve A's item without --force (exit 5, item stays open)", r.returncode == 5 and "isoAAAAA" in r.stdout and "[ ]" in needs(RA)[0] and M.memo_resolve(str(RA), 1, "x", "isoBBBBB-2") is None)
        check("isolation: ... --force settles it", mpy("resolve", "1", "ok", "--session", "isoBBBBB-2", "--force").returncode == 0 and "[x]" in needs(RA)[0])
        check("isolation: B cannot reopen A's item without --force either; A can", mpy("reopen", "1", "--session", "isoBBBBB-2").returncode == 5 and "[x]" in needs(RA)[0]
              and mpy("reopen", "1", "--session", "isoAAAAA-1").returncode == 0 and "[ ]" in needs(RA)[0])
        idA = M.need_id(needs(RA)[0])
        check("isolation: resolve / reopen take the id (`#id`) as well as the number", mpy("resolve", "#" + idA, "by id", "--session", "isoAAAAA-1").returncode == 0 and "[x]" in needs(RA)[0] and M.memo_reopen(str(RA), "#" + idA, "isoAAAAA-1") is not None and "[ ]" in needs(RA)[0])
        check("isolation: memo.py note writes the session and project tag", mpy("note", "settled in chat", "--session", "isoAAAAA-1").returncode == 0 and re.search(r"note → settled in chat \(s:isoAAAAA p:RA\)$", M.memo_path(str(RA)).read_text(), re.M))
        # stable ids: the same after a renumber; an older memo without ids still parses and is not rewritten for it
        M.memo_needs(str(RA), "isoBBBBB-2", "owner decision: pick the rounding?")
        idB = M.need_id(needs(RA)[1])
        M.memo_move(str(RA), str(RB), "isoAAAAA-1")
        check("isolation: ids stay the same when the items are renumbered (A's item moved away, B's went from 2 to 1)", needs(RA)[0].startswith("1. [ ] ") and "pick the rounding" in needs(RA)[0] and M.need_id(needs(RA)[0]) == idB and len(needs(RA)) == 1)
        check("isolation: ... and the number stays an alias", M.memo_resolve(str(RA), 1, "x", "isoBBBBB-2") is not None and M.memo_reopen(str(RA), "#" + idB, "isoBBBBB-2") is not None)
        old_ = ["1. [ ] 11-01 09:00 · owner decision: hand written one", "2. [ ] 11-01 09:01 · owner decision: legacy mine? (s:isoBBBBB)", "3. [x] 11-01 09:02 · owner decision: done one (s:isoBBBBB p:RA) → resolved 11-01 09:05: ok"]
        sec_ = M.memo_read(str(RA)); sec_["Needs you"] = "\n".join(old_); M.memo_write(str(RA), sec_)
        before_ = M.memo_path(str(RA)).read_text()
        ids_ = [M.need_id(l) for l in old_]
        M.resume_block(str(RA), session_id="isoBBBBB-2"); M.need_split(old_, "isoBBBBB-2", str(RA))
        check("isolation: an old memo without ids parses, gets a computed id per line (stable, 4 hex), and the file is not rewritten for it",
              all(re.fullmatch(r"[0-9a-f]{4}", i) for i in ids_) and ids_ == [M.need_id(l) for l in old_] and M.memo_path(str(RA)).read_text() == before_ and M.need_split(old_[:2], "isoBBBBB-2", str(RA))[0] == [old_[1]])
        check("isolation: an old item is settled by its computed id; an untagged one is anybody's", M.memo_resolve(str(RA), "#" + ids_[1], "ok", "isoBBBBB-2") is not None and M.memo_resolve(str(RA), "#" + ids_[0], "ok", "isoAAAAA-1") is not None)
        # a move keeps the tags: never retag, add p: only to a line that has none, untagged items stay behind
        for d in (RA, RB):
            shutil.rmtree(Path(d, ".claude"), ignore_errors=True)
        M.memo_needs(str(RA), "isoCCCCC-3", "owner decision: about RA?")
        sec_ = M.memo_read(str(RA)); sec_["Needs you"] += "\n2. [ ] 11-01 09:01 · hand written (no tag)\n3. [ ] 11-01 09:02 · legacy mine (s:isoCCCCC)\n4. [ ] 11-01 09:03 · about elsewhere (s:isoCCCCC p:Elsewhere)"; M.memo_write(str(RA), sec_)
        M.memo_move(str(RA), str(RB), "isoCCCCC-3")
        nb_, na_ = "\n".join(needs(RB)), "\n".join(needs(RA))
        check("isolation: memo_move keeps the s: and p: tags (p:RA stays RA), adds p:RB only to the line that had none", "(s:isoCCCCC p:RA)" in nb_ and "legacy mine #" not in nb_ and "legacy mine (s:isoCCCCC p:RB)" in nb_ and "(s:isoCCCCC p:Elsewhere)" in nb_)
        check("isolation: ... an untagged item stays in the old memo", "hand written (no tag)" in na_ and "hand written" not in nb_ and "about RA" not in na_)
        # when the memo moves: 51% / 49% never, a lead of WORK_MOVE_MIN and 60% does, the same margin moves it back
        wp = lambda cur, **w: M.work_project({"root": str(RA), "project": cur, "work": {os.path.realpath(RA): w.get("a", 0), os.path.realpath(RB): w.get("b", 0)}})
        rA, rB = os.path.realpath(RA), os.path.realpath(RB)
        check("isolation: a 51% / 49% edit split does not move the memo (either way)", wp(rA, a=4900, b=5100) == rA and wp(rB, a=5100, b=4900) == rB and wp(rA, a=5, b=7) == rA)
        check("isolation: a lead of 3 and 60% of the edits moves it; the same margin moves it back", wp(rA, a=2, b=8) == rB and wp(rA, a=40, b=60) == rB and wp(rB, a=8, b=2) == rA and wp(rB, a=9, b=6) == rA and wp(rB, a=8, b=6) == rB)
        # a launch root in no git repository: nothing at start; the first repository edited claims the memo
        run("start", {"session_id": "isoDDDDD-4", "cwd": str(NG)}, ledi)
        check("isolation: start in a non-git root creates no memo, session file or index line", not Path(NG, ".claude").exists())
        run("edit", {"session_id": "isoDDDDD-4", "cwd": str(NG), "tool_name": "Edit", "tool_input": {"file_path": str(RB / "j.py")}}, ledi)
        check("isolation: the first repository edited claims the memo of a non-git launch root", ist("isoDDDDD-4").get("project") == rB and not Path(NG, ".claude").exists())
        run("edit", {"session_id": "isoDDDDD-4", "cwd": str(NG), "tool_name": "Edit", "tool_input": {"file_path": str(RA / "k.py")}}, ledi)
        check("isolation: ... a later edit elsewhere does not take it away (no lead)", ist("isoDDDDD-4").get("project") == rB)
        run("receipt", {"session_id": "isoDDDDD-4", "cwd": str(NG), "stop_hook_active": False, "last_assistant_message": "🧾 **1 Agent · 1 u · Saved 1%** vs Opus 5.5\n- `done` → jamkhana work\n- `check` → ok\n- `open` → owner decision: keep the jamkhana rule?"}, ledi)
        nbd_ = "\n".join(needs(RB))
        check("isolation: the DRP case — a receipt of a session launched in a non-git folder lands in the repository it worked in, tagged p:RB (not the launch folder)",
              "keep the jamkhana rule" in nbd_ and "(s:isoDDDDD p:RB)" in nbd_ and not Path(NG, ".claude").exists() and "jamkhana" not in "\n".join(needs(RA)))
        run("start", {"session_id": "isoEEEEE-5", "cwd": str(RB)}, ledi)
        own_, oth_ = M.need_split([l for l in needs(RB) if "[ ]" in l], "isoEEEEE-5", str(RB))
        check("isolation: another session's item in the same project is information for a new session (never own)", not own_ and any("jamkhana" in l for l in oth_))
        check("isolation: a session with no id gets the index only — never one session's Now/Left/Next as its own",
              (M.resume_data(str(RB), "") or {}).get("now") == [] and (M.resume_data(str(RB), "") or {}).get("open_own") == [] and "keep the jamkhana rule" in "\n".join((M.resume_data(str(RB), "") or {}).get("open", [])))
        # items are tagged with the repository they are about: receipt and judge items
        stj = {"root": str(NG), "work": {rA: 1, rB: 5}}
        M.apply_gate_reply(stj, "VERDICT: ANSWERED\nQUESTION: use a per-order cap?\nANSWER: yes", "judge", sid="isoFFFFF-6", root=str(RB), tier=4)
        check("isolation: a judge item is tagged with the repository the session's edits land in", any("judge decided" in l and "(s:isoFFFFF p:RB)" in l for l in needs(RB)))
        stj2 = {"root": str(RB), "work": {rA: 5, rB: 1}}
        M.apply_gate_reply(stj2, "VERDICT: ANSWERED\nQUESTION: use a flat fee?\nANSWER: no", "judge", sid="isoFFFFF-6", root=str(RB), tier=4)
        check("isolation: ... even in another repository's memo (p:RA): its own session may act on it, any other session sees information", any("flat fee" in l and "(s:isoFFFFF p:RA)" in l for l in needs(RB)) and any("flat fee" in l for l in M.need_split([l for l in needs(RB) if "[ ]" in l], "isoFFFFF-6", str(RB))[0]) and not any("flat fee" in l for l in M.need_split([l for l in needs(RB) if "[ ]" in l], "isoGGGGG-7", str(RB))[0]))
        # a project tag that is a Windows path compares equal to the same folder (normcase of the realpath)
        check("isolation: a p: tag holding a backslash path equals the same folder written with slashes", M.proj_same("C:\\Users\\x\\DRP", "C:/Users/x/DRP") and M.proj_same("C:/Users/x/DRP/", "C:\\Users\\x\\DRP") and not M.proj_same("C:\\Users\\x\\Other", "C:/Users/x/DRP")
              and M.proj_same("DRP", "C:\\Users\\x\\DRP") and M.need_split(["1. [ ] 1 2 · a (s:me p:C:\\Users\\x\\DRP)"], "me", "C:/Users/x/DRP")[0] == ["1. [ ] 1 2 · a (s:me p:C:\\Users\\x\\DRP)"]
              and M.proj_path_key("C:\\Users\\x\\DRP") == os.path.normcase(os.path.realpath("C:/Users/x/DRP")))
        # other_projects: cached in the session state for 10 minutes
        sc_st = {}
        M.memo_needs(str(RB), "isoGGGGG-7", "owner decision: elsewhere open?")
        o1_ = M.other_projects(str(RA), st=sc_st)
        M.memo_needs(str(RB), "isoGGGGG-7", "owner decision: a second elsewhere?")
        o2_ = M.other_projects(str(RA), st=sc_st)
        sc_st["other_projects"]["ts"] -= 601
        o3_ = M.other_projects(str(RA), st=sc_st)
        check("isolation: other_projects is cached in the session state for 10 minutes (no re-read), then read again",
              o1_[0] and o1_ == o2_ and o3_[0][0][3] == o1_[0][0][3] + 1 and json.loads(json.dumps(sc_st))["other_projects"]["me"] == rA)
        os.environ["ABOFLES_STATE_DIR"] = state; os.environ["ABOFLES_CONFIG"] = str(Path(state, "config.json"))

        # ── memo health, memo on compact (PreCompact / PostCompact), Other items ──
        memo_health_tests(check)
        os.environ["ABOFLES_STATE_DIR"] = state; os.environ["ABOFLES_CONFIG"] = str(Path(state, "config.json"))
        memo_agent_tests(check)
        compact_refresh_tests(check)
        memo_repair_tests(check)
        os.environ["ABOFLES_STATE_DIR"] = state; os.environ["ABOFLES_CONFIG"] = str(Path(state, "config.json"))

    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
