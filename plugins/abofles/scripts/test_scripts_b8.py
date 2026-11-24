#!/usr/bin/env python3
"""Batch 8C checks: bill.py, status.py, overrides.py, topic.py, claims.py, gen_matrix.py and hooks/run.py.

    python3 scripts/test_scripts_b8.py      prints one line per failed check, then ALL PASS (exit 0) or N FAILED (exit 1)

Temp folders only: HOME, the ledgers, the states and the last-good copy all point into a fresh temp dir, never the real ~/.abofles.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
TMP = tempfile.mkdtemp(prefix="abofles-b8-")
ENV = {k: v for k, v in os.environ.items() if k != "ABOFLES_SESSION"}  # a ghost session of the shell must not pick the ledger
ENV.update(HOME=TMP, USERPROFILE=TMP, ABOFLES_LEDGER_DIR=str(Path(TMP, "led")), ABOFLES_STATE_DIR=str(Path(TMP, "st")),
           ABOFLES_LAST_GOOD=str(Path(TMP, "lg")))
os.environ.clear()
os.environ.update(ENV)  # the in-process imports below (claims, gen_matrix, abofles_hook) read the temp dirs too
Path(TMP, "led").mkdir()
Path(TMP, "st").mkdir()
PROJ = Path(TMP, "proj")
PROJ.mkdir()

FAILS = []


def check(name, ok):
    if not ok:
        FAILS.append(name)
        print(f"FAIL: {name}")


def run(script, *args, stdin=None, cwd=PROJ):
    return subprocess.run([sys.executable, "-X", "utf8", str(HERE / script), *args], cwd=str(cwd), input=stdin, capture_output=True, text=True, env=ENV)


def ledger(sid, rows):
    Path(TMP, "led", f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def rows_of(sid):
    return [json.loads(l) for l in Path(TMP, "led", f"{sid}.jsonl").read_text().splitlines() if l.strip()]


def line_with(text, word):
    return next((l for l in text.splitlines() if word in l), "")


# 1. bill.py: an open FAIL row's cost counts, so bill and status word the same saving on the same rows
ledger("b8bill", [
    {"agent_id": "f1aaaaaa", "side": "V", "model": "opus", "effort": "high", "topic": "Pay", "verdict": "FAIL", "status": "done", "confidence": "high"},
    {"agent_id": "w1aaaaaa", "side": "W", "model": "sonnet", "effort": "low", "topic": "Pay", "status": "done", "confidence": "high"},
    {"agent_id": "r1aaaaaa", "side": "R", "model": "haiku", "effort": "low", "topic": "Pay", "status": "done", "confidence": "high"},
])
b = run("bill.py", "--session", "b8bill")
s = run("status.py", "--session", "b8bill")
# opus·high 60 + sonnet·low 10 + haiku·low 0.5 = 70.5 units vs 3 x 60 = 180: saved 61% (the FAIL row skipped gave 10.5 vs 180: 94%)
check("1 bill: the open FAIL row's cost is counted (saved 61%, not 94%)", b.returncode == 0 and "saved 61%" in line_with(b.stdout, "Versus"))
check("1 bill: the open FAIL is still listed as open", "FAIL (open, Pay) 1" in b.stdout)
check("1 status: the same 3 rows word the same saving as the bill", s.returncode == 0 and "saved 61%" in s.stdout.splitlines()[0])

# 4. --baseline: a dot (opus.medium) prices Opus·Medium; a flag as the last arg is usage + exit 2, never an IndexError
b = run("bill.py", "--session", "b8bill", "--baseline", "opus.medium")
same = line_with(b.stdout, "Same agents all at")
check("4 bill --baseline opus.medium prices Opus·Medium (3 x 40 = 120 units, saved 41%)",
      b.returncode == 0 and "· Medium" in same and same.rstrip().endswith(": 120 units") and "saved 41%" in line_with(b.stdout, "Versus"))
b = run("bill.py", "--session", "b8bill", "--baseline", "Opus-Low")
check("4 bill --baseline Opus-Low prices Opus·Low (3 x 20 = 60 units)", "· Low" in line_with(b.stdout, "Same agents all at") and line_with(b.stdout, "Same agents all at").rstrip().endswith(": 60 units"))
s = run("status.py", "--session", "b8bill", "--baseline", "opus.medium")
check("4 status --baseline opus.medium prices Opus·Medium (saved 41%)", s.returncode == 0 and "saved 41%" in s.stdout.splitlines()[0] and "· Medium" in s.stdout.splitlines()[0])
for name, args in (("bill.py", ["--baseline"]), ("bill.py", ["--baseline", "--all"]), ("status.py", ["--baseline"]), ("status.py", ["--baseline", ""]),
                   ("topic.py", ["find", "invoice", "--limit"]), ("topic.py", ["find", "invoice", "--cwd"]), ("topic.py", ["--limit", "abc", "find", "x"]),
                   ("topic.py", ["--cwd", "--limit", "2", "find", "x"])):
    r = run(name, *args)
    check(f"4 {name} {' '.join(args) or '(empty)'}: usage + exit 2, no traceback", r.returncode == 2 and "Traceback" not in r.stderr and "usage" in (r.stdout + r.stderr).lower())
r = run("topic.py", "find", "invoice", "--limit", "1")
check("4 topic.py --limit 1 after the query still finds one topic", r.returncode == 0 and "1 match" in r.stdout)

# 2. status.py --resolve: 6+ characters (or the whole id), follow-up rows only, one agent id
ledger("b8res", [
    {"agent_id": "abcdef111", "side": "R", "model": "haiku", "effort": "low", "topic": "T", "status": "partial", "confidence": "medium"},
    {"agent_id": "abcdef222", "side": "R", "model": "haiku", "effort": "low", "topic": "T", "status": "blocked", "confidence": "medium"},
    {"agent_id": "done12345", "side": "W", "model": "opus", "effort": "high", "topic": "T", "status": "done", "confidence": "high"},
    {"agent_id": "zz1", "side": "R", "model": "haiku", "effort": "low", "topic": "T", "status": "partial", "confidence": "medium"},
])
res = lambda *a: run("status.py", *a, "--session", "b8res", "--trace", "re-checked: test")
r = res("--resolve", "abc")
check("2 resolve: a 3-character prefix is refused (exit 2), nothing settled", r.returncode == 2 and "too short" in r.stdout and not any(x.get("resolved") for x in rows_of("b8res")))
r = res("--resolve", "abcdef")
check("2 resolve: a prefix of two agent ids is refused (exit 2), nothing settled", r.returncode == 2 and "2 agent ids" in r.stdout and not any(x.get("resolved") for x in rows_of("b8res")))
r = res("--resolve", "done12")
check("2 resolve: a done/high row is no follow-up (exit 3), not marked", r.returncode == 3 and "no open follow-up" in r.stdout and not any(x.get("resolved") for x in rows_of("b8res")))
r = res("--resolve", "zz1")
check("2 resolve: a whole id shorter than 6 settles its own row", r.returncode == 0 and [x["agent_id"] for x in rows_of("b8res") if x.get("resolved")] == ["zz1"])
r = res("--resolve", "abcdef1")
check("2 resolve: a 7-character prefix of one agent settles that agent only",
      r.returncode == 0 and "resolved 1 entry" in r.stdout and sorted(x["agent_id"] for x in rows_of("b8res") if x.get("resolved")) == ["abcdef111", "zz1"])
r = run("status.py", "--unresolve", "abcdef1", "--session", "b8res")
check("2 unresolve: the same prefix reopens that agent only", r.returncode == 0 and [x["agent_id"] for x in rows_of("b8res") if x.get("resolved")] == ["zz1"])
r = run("status.py", "--unresolve", "abcdef", "--session", "b8res")
check("2 unresolve: a prefix of two agent ids is refused too (exit 2)", r.returncode == 2)

# 2. status.py header: the $ and the saving name how many agents were priced
ledger("b8price", [
    {"agent_id": "p1aaaaaa", "side": "R", "model": "haiku", "effort": "low", "status": "done", "confidence": "high", "tokens": 10000, "usd": 0.01, "base_usd": 0.2, "base_model": "opus"},
    {"agent_id": "p2aaaaaa", "side": "W", "model": "opus", "effort": "high", "status": "done", "confidence": "high"},
])
s = run("status.py", "--session", "b8price")
check("2 status header: 2 agents, 1 priced — the header says so", s.returncode == 0 and "2 agents" in s.stdout.splitlines()[0] and "(1 of 2 priced" in s.stdout.splitlines()[0])
s = run("status.py", "--session", "b8bill")
check("2 status header: all priced or none priced has no priced note", "priced" not in s.stdout.splitlines()[0])

# 2. status.py Context: from the --session state's transcript, not the newest state's
t1, t2 = Path(TMP, "t1.jsonl"), Path(TMP, "t2.jsonl")
t1.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 123000}}}) + "\n")
t2.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 456000}}}) + "\n")
Path(TMP, "st", "b8ctx.json").write_text(json.dumps({"root": str(PROJ), "transcript_path": str(t1), "last_prompt_ts": 1}))
ledger("b8ctx", [{"agent_id": "c1aaaaaa", "side": "R", "model": "haiku", "effort": "low", "status": "done", "confidence": "high"}])
time.sleep(0.05)
Path(TMP, "st", "b8other.json").write_text(json.dumps({"root": str(PROJ), "transcript_path": str(t2), "last_prompt_ts": time.time()}))
s = run("status.py", "--session", "b8ctx")
check("2 status Context: the named session's transcript (123K), not the newest state's (456K)", "Context:    123K tokens" in s.stdout and "456K" not in s.stdout)

# 3. overrides.py: a Close rule edit lands in the file where it lives; a key edit keeps its `# comment`
op = Path(TMP, "ovproj")
(op / ".claude").mkdir(parents=True)
(op / "CLAUDE.md").write_text("# P\n\n## abofles overrides\n- Break = 15  # team rule\n- Parallel = 3\n")
(op / ".claude" / "CLAUDE.md").write_text("## abofles overrides\n- Close: deploy = old rule\n")
ov = lambda req: run("overrides.py", "set", "--scope", "project", "--cwd", str(op), stdin=json.dumps(req), cwd=op)
r = ov({"close": {"Deploy": "new rule"}})
a, c = (op / "CLAUDE.md").read_text(), (op / ".claude" / "CLAUDE.md").read_text()
check("3 overrides: a Close rule living in .claude/CLAUDE.md is edited there", r.returncode == 0 and "- Close: deploy = new rule" in c and "old rule" not in c)
check("3 overrides: no second copy of the rule in the target CLAUDE.md", "Close:" not in a)
(op / "CLAUDE.md").write_text("# P\n\n## abofles overrides\n- Break = 15  # team rule\n- Close: deploy = target copy\n")
r = ov({"close": {"deploy": "one rule"}})
a, c = (op / "CLAUDE.md").read_text(), (op / ".claude" / "CLAUDE.md").read_text()
check("3 overrides: a rule in both files is edited in the target, the other copy removed", r.returncode == 0 and "- Close: deploy = one rule" in a and "Close:" not in c)
r = ov({"close": {"smoke": "run the smoke test"}})
check("3 overrides: a new rule is appended to the target", r.returncode == 0 and "- Close: smoke = run the smoke test" in (op / "CLAUDE.md").read_text() and "smoke" not in (op / ".claude" / "CLAUDE.md").read_text())
r = ov({"set": {"Break": "20"}})
a = (op / "CLAUDE.md").read_text()
check("3 overrides: a key edit keeps the trailing # comment", r.returncode == 0 and "- Break = 20  # team rule\n" in a)
r = ov({"set": {"Break": "25"}, "close": {"deploy": ""}})
a = (op / "CLAUDE.md").read_text()
check("3 overrides: a removal still reaches every project file", r.returncode == 0 and "deploy" not in a and "- Break = 25  # team rule" in a)

# 5. claims.py: a beat in the future is clamped to now, so it expires TTL later
sys.path.insert(0, str(HERE))
import claims as CL  # noqa: E402
now = time.time()
cs = [{"session": "A", "beat": now + 10 ** 7}, {"session": "B", "beat": now - 60}, {"session": "C", "beat": now - CL.TTL - 5}]
lv = CL.live(cs, now)
check("5 claims: a future beat is live now and clamped to now", [c["session"] for c in lv] == ["A", "B"] and cs[0]["beat"] <= now)
check("5 claims: the clamped beat drops out after TTL", CL.live(cs, now + CL.TTL + 5) == [])
if shutil.which("git"):
    repo = Path(TMP, "repo")
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True)
    store = repo / ".git" / "abofles-claims.json"
    store.write_text(json.dumps({"claims": [{"session": "A", "branch": "a", "files": [], "since": 1, "beat": int(now) + 10 ** 7}]}))
    r = run("claims.py", "claim", "--session", "B", "--branch", "b", cwd=repo)
    st_ = json.loads(store.read_text())["claims"]
    check("5 claims: the next locked write stores the clamped beat", r.returncode == 0 and next(c for c in st_ if c["session"] == "A")["beat"] <= time.time())

# 5. gen_matrix.py: an `ov` cell must be model·effort of the hook's tables
import gen_matrix as GM  # noqa: E402
for bad in ("opsu·high", "opus-high", "opus·hihg", "opus", "·high"):
    try:
        GM.parse_line(f"Some Topic | T2 | sig | ov Work={bad}")
        check(f"5 gen_matrix: ov Work={bad} is refused", False)
    except ValueError as e:
        check(f"5 gen_matrix: ov Work={bad} is refused with the cell named", "bad cell" in str(e))
check("5 gen_matrix: a good cell parses", GM.parse_line("Some Topic | T2 | sig | ov Work=fable·medium, Read=haiku·low")[2] == {"Work": "fable·medium", "Read": "haiku·low"})

# 5. hooks/run.py: keep_good never copies a save that is broken (a save landing during the run)
hd = Path(TMP, "hooks")
hd.mkdir()
shutil.copy2(HOOKS / "run.py", hd / "run.py")
lg = Path(TMP, "lg", "abofles_hook.py")
GOOD_SRC = "def main():\n    pass\n"
(hd / "abofles_hook.py").write_text(GOOD_SRC)
r = subprocess.run([sys.executable, str(hd / "run.py")], capture_output=True, text=True, env=ENV)
check("5 run.py: a whole run keeps the last-good copy", r.returncode == 0 and lg.exists() and lg.read_text() == GOOD_SRC
      and int(lg.stat().st_mtime) == int((hd / "abofles_hook.py").stat().st_mtime))
BROKEN_SAVE = ("import os\n\ndef main():\n    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'abofles_hook.py')\n"
               "    open(p, 'w').write('def main(:\\n    pass\\n')  # a half-written save lands while the hook runs\n")
(hd / "abofles_hook.py").write_text(BROKEN_SAVE)
r = subprocess.run([sys.executable, str(hd / "run.py")], capture_output=True, text=True, env=ENV)
check("5 run.py: a broken save during the run is never copied (last-good unchanged)", r.returncode == 0 and lg.read_text() == GOOD_SRC)
r = subprocess.run([sys.executable, str(hd / "run.py")], capture_output=True, text=True, env=ENV)
check("5 run.py: the next run loads the last-good copy, which still works", r.returncode == 0 and "running the last good copy" in r.stderr and lg.read_text() == GOOD_SRC)
shutil.rmtree(Path(TMP, "lg"))
(hd / "abofles_hook.py").write_text(BROKEN_SAVE)
subprocess.run([sys.executable, str(hd / "run.py")], capture_output=True, text=True, env=ENV)
check("5 run.py: with no last-good yet, a broken save during the run creates none", not lg.exists())

shutil.rmtree(TMP, ignore_errors=True)
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL PASS")
