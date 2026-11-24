#!/usr/bin/env python3
"""Abo Fles memo: the work journal the hooks keep in `.claude/abofles-memo.md`.

    memo.py show                 the resume block (now · next · needs you · last receipts)
    memo.py show --scope session|project|all   this session only / every session of this project (default) / other projects too
    memo.py show --all           the main memo, then this session's file
    memo.py show --main          the main memo only (Needs you, Decisions, Sessions index)
    memo.py scope [session|project|all]        print the scope in force and where it comes from; with a value, save it as the user default
    memo.py resolve <n|#id> <note> [--trace] [--session <sid>] [--force]   tick item n (or its stable id `#a1b2`, shown on the line) of "Needs you"; the note is the evidence, required (--trace: one "resolved by hand" line in Journal)
    memo.py reopen <n|#id> [--trace] [--session <sid>] [--force]           turn a resolved item back to open (drops the resolve note)
      --session <sid> names the session that writes (and stamps the trace) instead of the newest one (two sessions in one root)
      --force settles an item another session wrote (its `s:` tag); without it that item is refused (exit 5)
      exit codes: 0 changed · 1 no such item · 2 usage (bad n, missing or empty --session value, resolve without a note)
                  · 3 nothing to change (already resolved / already open) — no trace line is written
                  · 4 no session (a write needs --session or a session state of this folder) · 5 the item is another session's (use --force)
    memo.py note <text>          add a user note to Decisions (something settled in chat), tagged `(s:<session> p:<project>)`
    memo.py now < text           replace the Now section from stdin (what /abofles:memo writes)
    memo.py receipt < text       record the task's receipt (Receipt place = pane): it counts as printed, at once — the task closes and
                                 the memo and the Abo Fles pane get it, the chat does not; exit 2 when stdin is no receipt,
                                 3 when its fields cannot be read (nothing recorded, the task stays open)
      any write: exit 6 `memo busy` when another writer held the memo lock over 10 s (nothing written), 7 when the memo file could not be
      written (the error is printed; never `recorded`)
    memo.py path                 print the main memo path (`path mine`: this session's file)
    memo.py close-check [--json] the evidence inventory before an archive: one line per finding, ⛔ blocker · ⚠ warning · ✓ checked · `not checked`,
                                 from the guide's open items, this session's Needs-you items, git (work repositories, worktrees, sub sessions' branches;
                                 read-only, 2 s per call, never a fetch), live claims, connector actions, writes outside the work roots, Verify after the
                                 last edit, the auto-memory and the `- Close: <name> = <rule>` override lines (never run: the close agent judges them).
                                 `- Close git = committed | local` turns the git lines that rule allows into ✓ "by rule"; `- Close PR check = on` asks
                                 `gh pr list` per branch (2 s cap, read-only): an open pull request is ⚠. The Verdict line: ✅ only with no
                                 `not checked` line, no ⛔ or ⚠ left unwaived and, under `Close agent = on`, the close agent's run recorded (`agent`: yes);
                                 the close agent's lines of the last check stay while what they judged is unchanged. Stored in the session state (`close`,
                                 with a fingerprint: an edit, a Bash command that changes the work or a new turn makes it stale) and the session file's
                                 Close section (a stored ⛔ refuses both archive paths); exit 0, 4 with no session
    memo.py close-waive <id> --trace "<owner's words>"   the owner accepts one ⛔, ⚠ or `not checked` line of the stored check (the id, or a unique end of
                                 it): it stays, shown as "accepted by you"; the verdict is counted again. The trace must be words of one of the owner's
                                 messages. Exit 1 no check / no such line · 2 usage · 3 already accepted · 5 refused (a rule line the agent judges; not
                                 the owner's words) · 6 busy
    memo.py close-digest [--json] what the close agent judges with (reads only): the stored check's `Close:` rules and open lines, this session's
                                 owner asks, final replies, AskUserQuestion answers and tool names (each clipped, never the raw tail), the memo and
                                 auto-memory paths. Exit 0; 3 under `- Close agent = off` (no agent runs)
    memo.py close-add --none     the close agent's `- none`: no line, its run recorded (`agent`: yes), the verdict counted again
    memo.py close-add <state> --text "<finding>" --evidence "<proof>" [--rule <name> | --src <word>] [--fix "<next step>"]   the main session records
                                 one line the close agent returned (⛔ ⚠ ✓ or "not checked"). Append only: a script line is never edited or removed;
                                 --rule <name> judges the stored `rule:<name>` line (⛔ ⚠ ✓ settle it in the verdict). The fingerprint is counted again:
                                 the agent's own ledger row is never in it, a changed tree makes it stale. Only after a close agent ran for the check.
                                 Exit 1 no check / no such rule · 2 usage · 3 already there · 5 no close agent ran since the check · 6 busy
    memo.py move <root>          move this session's memo (its file, its Needs-you items, its index line) to project <root> (a repository top)
                                 and make <root> the session's project — for a session that was started in the wrong folder
      a write (resolve, reopen, note, now) with no session — no --session and no session state for this folder — exits 4 and writes nothing;
      so does any write (receipt and the memo agent's too) whose --session names a session UUID that has no state: no session file is made for it;
      so does a write with an empty --session (never the folder's newest session) or under an ABOFLES_SESSION that has no state.
      An empty note or now and an unknown command print the usage and exit 2 (`help`: this text, exit 0)

The memo agent's subcommands (`abofles:abofles-memo` runs them in the background; they need the session that started it: pass `--session <sid>`; each is one
locked memo_apply operation on this session's project and may touch other sessions' items there, never another project except move-item's target;
refs are the `#id` of an item, the `ref` of a decision; quote a `#id` in the shell, a bare `#` starts a comment):
    memo.py list --json          the memo as JSON: items (id, n, text, state, s, p), decisions (ref, file, stamp, by, q, a), sessions (Now age, Task, Left, Next,
                                 compact file), other known projects
    memo.py merge-decisions <ref> <ref> ... --into "<question> → <answer>"   several decisions that mean the same become one: the oldest stamp and tag kept,
                                 the merged lines go to archive.md
    memo.py move-item "<#id>" <root>           an item about another project goes to <root> (an existing folder that is a git repository top or already has a memo);
                                 its text, stamp, id and tags are kept
    memo.py drop-item "<#id>" --reason "<why>" a noise item leaves Needs you; archive.md keeps its line with the reason (never deleted)
    memo.py drop-decision <ref> --reason "<why>"   a noise decision (a test line, an empty placeholder) leaves its file; archive.md keeps its line with the reason
    memo.py archive-session <sid> [--force --trace "<owner's words>"]   a closed session's file moves to abofles-memo.d/archive/; refused while it has an open item of
                                 its own, Left lines in its Now or work at risk in its repositories, and for the calling session. --force --trace (the owner said so) archives
                                 it anyway: its Left lines become one Needs-you item of this project first, the trace goes to archive.md and the caller's Journal
    memo.py rewrite-now <sid> < text           replace the Task, Left and Next lines of a stale Now (stamped `· from memo agent`); refused for a Now written by
                                 `memo.py now` that is not older than the last receipt
      exit codes: 0 done · 1 no such item / decision / session · 2 usage or a bad target · 3 nothing to change · 4 no session · 5 refused (another session's without
                  curator rights, an open item, the calling session, the user's own Now)

    memo.py repair [--dry-run] [--all] [--root <folder>]   put old memos right (the owner runs it; `- Memo repair = auto` runs it once per project at session start).
                                 Default: this project; --all walks every project of the registry (~/.abofles/state/memo-index.json); --root names one.
                                 Steps: a backup of the main memo and abofles-memo.d to abofles-memo.d/backup-<YYYYMMDD-HHMM>/ (only when something changes; no other
                                 reader looks inside), memo_health with no calling session, then per session file its real project from its state file (`work` counts
                                 under the work-project rule, else `project`; its Journal and item tags are only support): one that belongs to another existing project moves
                                 there (file, compact file, its own items with tags, index line); a session the state cannot place stays and is reported; an untagged Needs-you
                                 item gets the tag of the session file whose id its text names (else it is left and reported). Nothing is deleted; a second run does nothing.
                                 --dry-run writes nothing at all (no backup either) and prints what would change.

Layout: `.claude/abofles-memo.md` (main: Needs you, Decisions = notes, Sessions = one index line per
session with a link) and `.claude/abofles-memo.d/<session>.md` (Now, Journal = one line per receipt,
Decisions = that session's questions with their answers). The hooks fill everything but Now, which
the model writes through `now`. The memo lives in the project the session's edits land in (the launch
folder until most edits go to another repository; the `edit` hook moves it then).
Scope precedence: `--scope` · `- Resume = …` in the override block · the saved default (`scope <value>`,
~/.abofles/config.json) · `project`.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import abofles_hook as H  # noqa: E402
from abofles_hook import memo_repair, repair_roots, CODE_EXIT, memo_path, session_memo_path, sess_read, memo_apply, memo_trace, memo_read, resume_block, newest_state, newest_state_path, session_arg, resume_scope, config_read, config_write, SCOPES, CONFIG_PATH, memo_move, load_state, save_state, state_lock, refresh_guide, guide_line, last_resolved_note, script_cmd, clip, has_receipt, looks_like_receipt, repo_top, NEED_TAIL_RE, MEMO_DIR_NAME  # noqa: E402

NO_EVIDENCE = 'abofles: resolve needs evidence — add a note, e.g. --trace "re-checked: <evidence>" or "user answered: <answer>"'


def scope_arg(args):
    """Take `--scope <value>` out of args; the value, or None. An unknown value exits 2."""
    if "--scope" not in args:
        return None
    i = args.index("--scope")
    v = args[i + 1].strip().lower() if i + 1 < len(args) else ""
    if v not in SCOPES:
        print("usage: memo.py show --scope session|project|all")
        sys.exit(2)
    del args[i:i + 2]
    return v


def no_session():
    # a write with no session would belong to nobody — its items would show up as another session's or no session's
    print("abofles: no session — nothing written (run the command from the session that owns the memo, or name it with --session)")
    sys.exit(4)


CURATOR_CMDS = ("list", "merge-decisions", "move-item", "drop-item", "drop-decision", "archive-session", "rewrite-now")  # the memo agent's subcommands (memo_apply curator ops)
CURATOR_USAGE = {"list": "memo.py list --json --session <sid>", "merge-decisions": "memo.py merge-decisions <ref> <ref> ... --into \"<question> → <answer>\" --session <sid>",
                 "move-item": "memo.py move-item \"<#id>\" <project folder> --session <sid>", "drop-item": "memo.py drop-item \"<#id>\" --reason \"<why>\" --session <sid>",
                 "drop-decision": "memo.py drop-decision <ref> --reason \"<why>\" --session <sid>", "archive-session": "memo.py archive-session <sid> [--force --trace \"<owner's words>\"] --session <sid>", "rewrite-now": "memo.py rewrite-now <sid> --session <sid> < text"}


def take_opt(rest, name):
    """Take `name <value>` out of `rest` (edited in place); the value, "" when it has none, None when `name` is not there. The next word is the
    value unless it is a flag itself (`--word`): a text that starts with `--` (`--reason "-- a test line"`) is a value, and an empty value is taken
    with its flag (never left behind as a word)."""
    if name not in rest:
        return None
    i = rest.index(name)
    has = i + 1 < len(rest) and not FLAG_RE.fullmatch(rest[i + 1])
    v = rest[i + 1] if has else ""
    del rest[i:i + (2 if has else 1)]
    return v


FLAG_RE = re.compile(r"--[A-Za-z][\w-]*")  # a word that is a flag, never an option's value


def curator_cmd(cmd, rest, cwd, sid):
    """The memo agent's subcommands: one memo_apply curator operation each. Prints one line (list: JSON); a refusal exits with CODE_EXIT."""
    rest = list(rest)
    usage = lambda: (print("usage: " + CURATOR_USAGE[cmd]), sys.exit(2))
    if cmd == "list":
        r = memo_apply(cwd, "list", sid, curator=True)
    elif cmd == "merge-decisions":
        into = take_opt(rest, "--into")
        refs = [a for a in rest if not a.startswith("--")]
        if not into or len(refs) < 2:
            usage()
        r = memo_apply(cwd, "merge", sid, curator=True, refs=refs, into=into)
    elif cmd == "move-item":
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) != 2:
            usage()
        r = memo_apply(cwd, "move_item", sid, project=pos[1], curator=True, ref=pos[0])
    elif cmd == "drop-item":
        reason = take_opt(rest, "--reason")
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) != 1 or not reason:
            usage()
        r = memo_apply(cwd, "drop_item", sid, curator=True, ref=pos[0], reason=reason)
    elif cmd == "drop-decision":
        reason = take_opt(rest, "--reason")
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) != 1 or not reason:
            usage()
        r = memo_apply(cwd, "drop_decision", sid, curator=True, ref=pos[0], reason=reason)
    elif cmd == "archive-session":
        force = "--force" in rest
        rest = [a for a in rest if a != "--force"]
        trace = take_opt(rest, "--trace")
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) != 1 or (force and not (trace or "").strip()) or (trace is not None and not force):
            usage()
        r = memo_apply(cwd, "archive_session", sid, curator=True, target=pos[0], force=force, trace=trace or "")
    else:  # rewrite-now
        pos = [a for a in rest if not a.startswith("--")]
        text = sys.stdin.read()
        if len(pos) != 1 or not text.strip():
            usage()
        r = memo_apply(cwd, "rewrite_now", sid, curator=True, target=pos[0], text=text)
    if not r:
        print("abofles: " + r.reason)
        sys.exit(CODE_EXIT.get(r.code, 1))
    v = r.value
    if cmd == "list":
        print(json.dumps(v, ensure_ascii=False, indent=1))
    elif cmd == "merge-decisions":
        print(f"merged {len(set(refs))} decisions: {v}")
    elif cmd == "move-item":
        print(f"moved: {clip(v, 100)} → {os.path.realpath(pos[1])}")
    elif cmd == "drop-item":
        print(f"dropped: {clip(v, 100)} → {MEMO_DIR_NAME}/archive.md")
    elif cmd == "drop-decision":
        print(f"dropped: {clip(v, 100)} → {MEMO_DIR_NAME}/archive.md")
    elif cmd == "archive-session":
        print(f"archived: session {v} → {MEMO_DIR_NAME}/archive/{v}.md")
    else:
        print(f"rewritten: session {v} Now (Task, Left, Next) · from memo agent")


def repair_lines(r, dry):
    """The report of one project's repair: a header, one line per change or finding, the backup."""
    name = Path(r["root"]).name
    out = [f"{name} ({r['root']})" + (" · dry run, nothing written" if dry else "")]
    if r["legacy"]:
        return out + ["  a one-file memo from before 1.5: the first real run splits it into session files; nothing done now"]
    h = r["health"]
    tidy = [f"{h[k]} {w}" for k, w in (("archived_items", "resolved items archived"), ("archived_sessions", "idle sessions archived"), ("deduped", "duplicate decisions"), ("gate_answers", "agent gate answers archived"), ("judge_notes", "Judge notes archived"),
                                       ("trimmed", "journal lines trimmed"), ("orphans", "index lines or files out of step")) if h.get(k)]
    if tidy:
        out.append("  tidy-up: " + ", ".join(tidy))
    out += [f"  session {m['sid']} → {m['dest']} ({m['basis']}" + (f"; {m['support']}" if m["support"] else "") + ")" for m in r["moves"]]
    out += [f"  item {t['id']} (#{t['n']}) tagged s:{t['sid']} p:{t['p']}" for t in r["tags"]]
    out += [f"  left alone: session {u['sid']} — {u['why']}" + (f" ({u['support']})" if u["support"] else "") for u in r["unsure"]]
    if r["untagged"]:
        out.append(f"  left alone: {len(r['untagged'])} untagged item{'s' if len(r['untagged']) > 1 else ''} naming no session: "
                   + "; ".join(f"{u['id']} {u['text']}" for u in r["untagged"][:5]) + (" …" if len(r["untagged"]) > 5 else ""))
    if r["nothing"]:
        out.append("  nothing to do")
    for path in dict.fromkeys(([r["backup"]] if r["backup"] else []) + [p for p in (r.get("backups") or {}).values()]):
        out.append(f"  backup: {path}")
    return out


def repair_cmd(rest, cwd):
    """`memo.py repair [--dry-run] [--all] [--root <folder>]` (see the docstring)."""
    rest = list(rest)
    dry, everything = "--dry-run" in rest, "--all" in rest
    rest = [a for a in rest if a not in ("--dry-run", "--all")]
    root = take_opt(rest, "--root")
    if rest or (root is not None and (not root or everything)):
        print("usage: memo.py repair [--dry-run] [--all | --root <project folder>]")
        sys.exit(2)
    if root is not None and not os.path.isdir(root):
        print(f"abofles: {root} is no folder")
        sys.exit(2)
    roots = repair_roots(root or cwd, everything)
    if not roots:
        print("abofles: no project with a memo in the registry — nothing to repair")
        return
    backups = {}
    for r in roots:
        res = memo_repair(r, dry=dry, backups=backups)
        if not Path(r, ".claude", "abofles-memo.md").exists():
            print(f"abofles: no memo in {r}")
            continue
        print("\n".join(repair_lines(res, dry)))


CLOSE_TIMEOUT = H.CLOSE_TIMEOUT  # seconds per git call: a call past it makes its line `not checked`, never ✓
CLOSE_STATES = ("⛔", "⚠", "✓", "not checked")
CLOSE_RULE_RE = re.compile(r"^Close:\s*(.+?)\s*=\s*(.+?)\s*$", re.I)  # `- Close: <name> = <rule>` in the override block
SCHEDULE_TOOL_RE = re.compile(r"^mcp__.*schedul", re.I)  # a scheduled-task tool: each call its own line
GIT_FIX = "git work goes through the abofles-git agent, with your words for it"
GH = "gh"  # the GitHub CLI `Close PR check = on` asks (read-only: `gh pr list`)


def close_scratch():
    """The folders whose writes are scratch, never work left outside: the system temp folders (ABOFLES_SCRATCH_DIRS, os.pathsep-separated, replaces them)."""
    return H.close_scratch()


def under(path, top):
    return bool(top) and (path == top or path.startswith(top.rstrip(os.sep) + os.sep))


def close_line(id, state, src, text, fix=""):
    """One inventory line: `state` is ⛔ blocker, ⚠ warning, ✓ checked or `not checked`; `src` says where the evidence came from (guide, needs,
    git, pr, claim, sub, action, outside, verify, memory, rule); `fix` the next step, '' for none."""
    if state not in CLOSE_STATES:
        raise ValueError(f"close_line: bad state {state!r}")
    return {"id": id, "state": state, "src": src, "text": text, "fix": fix}


def call_timeout(timeout, deadline, args=()):
    """The timeout of one git call: `timeout`, cut to what is left before `deadline` (time.monotonic, None: no cap); none left raises TimeoutExpired."""
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired(["git", *args], 0)
    return min(timeout, left)


WINDOWS = os.name == "nt"


def run_capped(cmd, cwd, timeout, env=None, stdin=None):
    """subprocess.run(cmd) with captured text output and `timeout`. On Windows a call past it kills its whole process tree (taskkill /T /F):
    run() kills only the first process there, and a child of git or gh that still holds the pipes would keep the call waiting past its timeout."""
    if not WINDOWS:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, stdin=stdin)
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=stdin, env=env, text=True,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            p.kill()
            p.communicate(timeout=1)
        except Exception:
            for fh in (p.stdout, p.stderr):
                try:
                    fh.close()
                except Exception:
                    pass
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


class GitFailed(Exception):
    """A git call that must answer exited non-zero: its facts are unknown, never ✓."""


def git_ok(r, what):
    """`r` when its git call exited 0, else GitFailed naming the call and git's first error line."""
    if r.returncode != 0:
        msg = ((r.stderr or r.stdout or "").strip().splitlines() or [f"exit {r.returncode}"])[0]
        raise GitFailed(f"git {what} failed: {clip(msg, 100)}")
    return r


BRANCH_FMT = "%(refname:short)%00%(objectname)%00%(upstream)%00%(upstream:short)%00%(upstream:track)"


def branch_facts(line):
    """One `for-each-ref --format=BRANCH_FMT` line as {name, oid, upstream (short, '' none), local (the upstream is a local branch: never pushed),
    gone (its upstream ref is gone), ahead (commits past the upstream; None when gone or none)}."""
    name, oid, full, short, track = (line.split("\0") + [""] * 5)[:5]
    gone = "gone" in track
    m = re.search(r"ahead (\d+)", track)
    return {"name": name, "oid": oid, "upstream": short, "local": full.startswith("refs/heads/"), "gone": gone,
            "ahead": None if gone or not full else int(m.group(1)) if m else 0}


def close_git(repo, timeout=CLOSE_TIMEOUT, deadline=None):
    """The raw git facts of the repository at `repo`: read-only calls, each with its own `timeout` (and none past `deadline`, time.monotonic: the
    cap of a whole repository), never a fetch; a call that must answer and exits non-zero is an `error` (never ✓). Keys: repo, git (False: no
    repository or no git), timeout (a call ran out: the facts are incomplete), error, branch ('' when detached), detached, contained (a detached HEAD
    some branch or remote ref holds), dirty (porcelain lines, untracked files counted), upstream ('' when none), local (that upstream is a local
    branch), gone (that upstream is gone), ahead (commits past the upstream; None when unknown), stash (entries), worktrees ([{path, branch,
    detached, prunable}] of the other worktrees), branches (every other local branch: branch_facts plus on_remote, its tip held by a remote ref)."""
    f = {"repo": repo, "git": False, "timeout": False, "error": "", "branch": "", "detached": False, "contained": None, "dirty": 0,
         "upstream": "", "local": False, "gone": False, "ahead": None, "stash": 0, "worktrees": [], "branches": []}
    if not repo or not os.path.isdir(repo):
        f["error"] = "no folder"
        return f
    def git(*a):
        return run_capped(["git", "--no-optional-locks", *a], repo, call_timeout(timeout, deadline, a))
    try:
        r = git("rev-parse", "--is-inside-work-tree")
        if r.returncode != 0:
            if "not a git repository" not in (r.stderr or "").lower():  # dubious ownership, a broken .git: a repository git cannot read is no ✓
                git_ok(r, "rev-parse")
            return f
        if r.stdout.strip() != "true":
            return f
        f["git"] = True
        f["dirty"] = len([l for l in git_ok(git("status", "--porcelain", "--untracked-files=normal"), "status").stdout.splitlines() if l.strip()])
        b = git("symbolic-ref", "-q", "--short", "HEAD")
        if b.returncode not in (0, 1):
            git_ok(b, "symbolic-ref")
        f["branch"] = b.stdout.strip() if b.returncode == 0 else ""
        f["detached"] = not f["branch"]
        if f["detached"]:
            f["contained"] = bool(git_ok(git("for-each-ref", "--contains", "HEAD", "--count=1", "refs/heads", "refs/remotes"), "for-each-ref").stdout.strip())
        refs = [branch_facts(l) for l in git_ok(git("for-each-ref", "--format=" + BRANCH_FMT, "refs/heads"), "for-each-ref").stdout.splitlines() if l.strip()]
        for x in refs:
            if x["name"] == f["branch"]:
                f.update(upstream=x["upstream"], local=x["local"], gone=x["gone"], ahead=x["ahead"])
        f["stash"] = len([l for l in git_ok(git("stash", "list"), "stash list").stdout.splitlines() if l.strip()])
        w = git_ok(git("worktree", "list", "--porcelain"), "worktree list")
        blocks = [bl for bl in w.stdout.split("\n\n") if bl.strip()]
        for bl in blocks[1:]:  # the first block is the main worktree
            kv = {}
            for l in bl.splitlines():
                k, _, v = l.partition(" ")
                kv[k] = v
            if kv.get("worktree"):
                f["worktrees"].append({"path": kv["worktree"], "branch": re.sub(r"^refs/heads/", "", kv.get("branch") or ""), "detached": "detached" in kv,
                                       "prunable": "prunable" in kv})
        others = [x for x in refs if x["name"] != f["branch"]]
        if any(x["gone"] or not x["upstream"] or x["local"] or x["ahead"] for x in others):  # one call: the commits of local branches no remote ref holds
            unpushed = set(git_ok(git("rev-list", "--branches", "--not", "--remotes"), "rev-list").stdout.split())
        else:
            unpushed = set()
        f["branches"] = [dict(x, on_remote=x["oid"] not in unpushed) for x in others]
    except subprocess.TimeoutExpired:
        f["timeout"] = True
    except GitFailed as e:
        f["error"] = str(e)
    except FileNotFoundError:
        f["git"], f["error"] = False, "git not installed"
    except Exception as e:
        f["error"] = f"{type(e).__name__}: {e}"
    return f


def close_branch(repo, branch, timeout=CLOSE_TIMEOUT, deadline=None):
    """The raw facts of `branch` in the repository at `repo` (a sub session's): exists, upstream, local (the upstream is a local branch), gone (the
    upstream is gone), ahead, base (origin's HEAD, else main or master), merged (its tip is in the base; None when no base is known), squashed (not
    merged, but `git cherry` finds every commit in the base or the two trees are the same: a squash or rebase merge), timeout, error. Read-only,
    never a fetch; no call past `deadline`; a call that must answer and exits non-zero is an error."""
    f = {"exists": False, "upstream": "", "local": False, "gone": False, "ahead": None, "base": "", "merged": None, "squashed": False, "timeout": False, "error": ""}
    if not repo or not os.path.isdir(repo) or not branch:
        f["error"] = "no branch" if not branch else "no folder"
        return f
    def git(*a):
        return run_capped(["git", "--no-optional-locks", *a], repo, call_timeout(timeout, deadline, a))
    try:
        v = git("rev-parse", "--verify", "-q", f"refs/heads/{branch}")
        if v.returncode != 0:
            if v.returncode != 1:
                git_ok(v, "rev-parse")
            return f
        f["exists"] = True
        line = git_ok(git("for-each-ref", "--format=" + BRANCH_FMT, f"refs/heads/{branch}"), "for-each-ref").stdout.strip()
        x = branch_facts(line)
        f.update(upstream=x["upstream"], local=x["local"], gone=x["gone"], ahead=x["ahead"])
        o = git("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
        base = o.stdout.strip() if o.returncode == 0 else ""
        if not base:
            base = next((c for c in ("main", "master") if c != branch and git("rev-parse", "--verify", "-q", f"refs/heads/{c}").returncode == 0), "")
        f["base"] = base
        if base:
            m = git("merge-base", "--is-ancestor", f"refs/heads/{branch}", base)
            if m.returncode not in (0, 1):
                git_ok(m, "merge-base")
            f["merged"] = m.returncode == 0
            if not f["merged"]:
                c = git("cherry", base, f"refs/heads/{branch}")
                same = c.returncode == 0 and not any(l.startswith("+") for l in c.stdout.splitlines())
                f["squashed"] = same or git("diff", "--quiet", base, f"refs/heads/{branch}").returncode == 0
    except subprocess.TimeoutExpired:
        f["timeout"] = True
    except GitFailed as e:
        f["error"] = str(e)
    except FileNotFoundError:
        f["error"] = "git not installed"
    except Exception as e:
        f["error"] = f"{type(e).__name__}: {e}"
    return f


def close_pr(repo, branch, timeout=CLOSE_TIMEOUT, deadline=None):
    """The open pull requests whose head is `branch`, from `gh pr list --head <branch> --state open --json number,url` run in `repo`: a list of
    {number, url}, or the reason it could not be read (gh not installed, a call past `timeout` or `deadline`, gh's own error). Read-only: gh
    never prompts and never writes."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    try:
        r = run_capped([GH, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url"], repo, call_timeout(timeout, deadline, ("gh",)),
                       env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return "gh not installed"
    except subprocess.TimeoutExpired:
        return f"gh ran past {CLOSE_TIMEOUT} s"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return clip((r.stderr or r.stdout or "").strip().splitlines()[0] if (r.stderr or r.stdout or "").strip() else f"gh exited {r.returncode}", 120)
    try:
        prs = json.loads(r.stdout or "[]")
    except ValueError:
        return "gh gave no JSON"
    return [{"number": x.get("number"), "url": str(x.get("url") or "")} for x in prs if isinstance(x, dict)] if isinstance(prs, list) else "gh gave no list"


def close_pr_lines(repo, branch, name, prs):
    """The inventory lines of one close_pr result: ⚠ `PR open: <url>` per open pull request, ✓ when there is none, `not checked` when gh could
    not answer."""
    rid = f"pr:{repo}:{branch}"
    if isinstance(prs, str):
        return [close_line(rid, "not checked", "pr", f"{name}: open pull requests of {branch} not checked — {prs}")]
    if not prs:
        return [close_line(rid, "✓", "pr", f"{name}: no open pull request for {branch}")]
    return [close_line(f"{rid}:{p['number']}", "⚠", "pr", f"PR open: {p['url'] or '#' + str(p['number'])} ({name}, branch {branch})",
                       "merge or close it, or say it stays open") for p in prs]


def close_git_lines(f, name, worktree=False, skip=()):
    """The inventory lines of one close_git result: ⛔ dirty / unpushed / no upstream (a local upstream counts as none) / a detached HEAD on no
    branch, ⚠ a stash, an upstream that is gone or a detached HEAD a branch holds, ✓ only when every call ran and nothing is open, `not checked`
    for no repository, a timeout or an error. Not `worktree` (the main repository, whose refs every worktree shares): every other local branch
    too, but those in `skip` (checked out in a worktree that is checked, or a sub session's) and those whose tip a remote ref holds."""
    rid = f"git:{f['repo']}"
    if f["error"] and not f["git"]:
        return [close_line(rid, "not checked", "git", f"{name}: {'folder gone' if f['error'] == 'no folder' else f['error']}")]
    if not f["git"] and f["timeout"]:  # the first call ran out: whether it is a repository at all is not known
        return [close_line(rid + ":timeout", "not checked", "git", f"{name}: a git call ran past {CLOSE_TIMEOUT} s — the rest is not checked")]
    if not f["git"]:
        return [close_line(rid, "✓", "git", f"{name}: no git repository — nothing to check")]
    out = []
    if f["dirty"]:
        out.append(close_line(rid + ":dirty", "⛔", "git", f"{name}: {f['dirty']} uncommitted file{'s' if f['dirty'] != 1 else ''}",
                              f"commit them, or say they stay uncommitted ({GIT_FIX})"))
    if f["detached"] and f["contained"] is not None:
        out.append(close_line(rid + ":detached", "⚠" if f["contained"] else "⛔", "git",
                              f"{name}: detached HEAD" + (" (its commit is on a branch)" if f["contained"] else " with a commit no branch holds"),
                              "check out a branch" + ("" if f["contained"] else " that holds this commit, or it can be lost") + f" ({GIT_FIX})"))
    if f["branch"] and not f["timeout"] and not f["error"]:
        if f["gone"]:
            out.append(close_line(rid + ":gone", "⚠", "git", f"{name}: branch {f['branch']}: its upstream {f['upstream']} is gone (deleted on the remote)",
                                  f"if it was merged, delete the branch; else push it again ({GIT_FIX})"))
        elif not f["upstream"]:
            out.append(close_line(rid + ":upstream", "⛔", "git", f"{name}: branch {f['branch']} has no upstream (never pushed)", f"push it with an upstream ({GIT_FIX})"))
        elif f["local"]:
            out.append(close_line(rid + ":upstream", "⛔", "git", f"{name}: branch {f['branch']} tracks the local branch {f['upstream']} (never pushed)",
                                  f"push it with an upstream on a remote ({GIT_FIX})"))
        elif f["ahead"] is None:
            out.append(close_line(rid + ":ahead", "not checked", "git", f"{name}: commits ahead of {f['upstream']} could not be counted"))
        elif f["ahead"]:
            out.append(close_line(rid + ":ahead", "⛔", "git", f"{name}: {f['ahead']} unpushed commit{'s' if f['ahead'] != 1 else ''} on {f['branch']} (ahead of {f['upstream']})",
                                  f"push them ({GIT_FIX})"))
    for b in ([] if worktree or f["timeout"] or f["error"] else f["branches"]):  # the other local branches: refs are shared, told once at the main repository
        if b["name"] in skip or b["on_remote"]:
            continue  # its own worktree's lines tell it, or a remote ref holds its tip: nothing on it is lost
        bid = f"{rid}:branch:{b['name']}"
        if b["gone"]:
            out.append(close_line(bid + ":gone", "⚠", "git", f"{name}: branch {b['name']}: its upstream {b['upstream']} is gone and no remote holds its tip",
                                  f"if it was merged, delete the branch; else push it again ({GIT_FIX})"))
        elif not b["upstream"] or b["local"]:
            out.append(close_line(bid + ":upstream", "⛔", "git", f"{name}: branch {b['name']} " + (f"tracks the local branch {b['upstream']} (never pushed)" if b["upstream"]
                                  else "has no upstream (never pushed)"), f"push it with an upstream, or delete it once its work is safe ({GIT_FIX})"))
        elif b["ahead"]:
            out.append(close_line(bid + ":ahead", "⛔", "git", f"{name}: {b['ahead']} unpushed commit{'s' if b['ahead'] != 1 else ''} on {b['name']} (ahead of {b['upstream']})",
                                  f"push them ({GIT_FIX})"))
    if f["stash"] and not worktree:  # the stash is shared by every worktree: told once, at the main repository
        out.append(close_line(rid + ":stash", "⚠", "git", f"{name}: {f['stash']} stash entr{'ies' if f['stash'] != 1 else 'y'}", "drop them, or say what they hold"))
    if f["timeout"]:
        out.append(close_line(rid + ":timeout", "not checked", "git", f"{name}: a git call ran past {CLOSE_TIMEOUT} s — the rest is not checked"))
    elif f["error"]:
        out.append(close_line(rid + ":error", "not checked", "git", f"{name}: {f['error']}"))
    elif not out:
        out.append(close_line(rid, "✓", "git", f"{name}: clean, {f['branch']} pushed to {f['upstream']}"))
    return out


_TRANSCRIPTS = {}  # realpath -> ((size, mtime_ns), rows, readable): the last parse of each transcript this process read


def transcript_rows(tp):
    """The JSON rows of the transcript at `tp` as (dict rows, readable rows), parsed once per size and mtime (a grown or rewritten file is read
    again); None when the file cannot be read. Callers never change the rows."""
    try:
        rp = os.path.realpath(tp)
        stt = os.stat(rp)
    except (OSError, TypeError, ValueError):
        return None
    key = (stt.st_size, stt.st_mtime_ns)
    hit = _TRANSCRIPTS.get(rp)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    try:
        raw = Path(rp).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rows, good = [], 0
    for l in raw:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        good += 1
        if isinstance(d, dict):
            rows.append(d)
    _TRANSCRIPTS[rp] = (key, rows, good)
    return rows, good


def transcript_tools(tp, since=0):
    """[(name, input, ts)] of the tool_use entries of the transcript at `tp` (an `assistant` row's message.content[] items of type `tool_use`; ts from
    the row's ISO `timestamp`), those at or after `since`. None when the file cannot be read or holds no readable row."""
    from datetime import datetime
    got = transcript_rows(tp)
    if got is None:
        return None
    rows, good = got
    out = []
    for d in rows:
        m = d.get("message")
        if d.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        try:
            ts = datetime.fromisoformat(str(d.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0
        if ts and ts < since:
            continue
        for c in m["content"]:
            if isinstance(c, dict) and c.get("type") == "tool_use" and isinstance(c.get("name"), str):
                out.append((c["name"], c.get("input") if isinstance(c.get("input"), dict) else {}, int(ts)))
    return out if good else None


def memory_listed(idx):
    """The file names MEMORY.md text `idx` lists: every markdown link target and every bare `<name>.md` word, as a base name. A file counts as
    listed only by its exact name (`rule.md` is not listed by `cap-rule.md`)."""
    from urllib.parse import unquote
    names = [unquote(t).split("#")[0] for t in re.findall(r"\]\(<?([^)>]+?)>?\)", idx)]  # link targets, spaces kept
    names += re.findall(r"[^\s()\[\]<>`'\"|,;]+\.md(?![\w.-])", re.sub(r"\]\([^)]*\)", "]", idx))  # bare names and paths, outside the links
    return {n.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] for n in names if n.strip()}


def memory_dir(st):
    """This session's auto-memory folder: `memory/` next to its transcript, else ~/.claude/projects/<launch root with every non-alphanumeric as `-`>/memory."""
    tp = st.get("transcript_path") or ""
    if tp:
        return Path(tp).parent / "memory"
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", st.get("root") or "") / "memory"


def hm(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "?"


def close_inventory(root, st, sid, timeout=CLOSE_TIMEOUT, cap=None, pr=True):
    """The evidence inventory of session `sid` (state `st`, memo project `root`): close_line dicts from the guide's open items, this session's
    Needs-you items, git (work repositories, their worktrees, the sub sessions' branches; their open pull requests under `Close PR check = on`),
    this session's live claims, connector actions,
    writes outside the work roots, Verify after the last edit, the auto-memory and the `Close:` rules. Raw evidence only: no rule run; the
    `Close git` rule turns its git lines into ✓ "by rule" (close_by_rule). `cap` (seconds): every git call of one repository ends within it.
    `pr` False (`Close check = auto` at Stop): gh is never asked, one `not checked` line says so under `Close PR check = on`. The whole session
    counts from its own start (first_started: a compact or a resume moves `started`). Line ids are unique (a repeat gets `#2`, `#3`)."""
    lines = []
    started = session_start(st)
    # 1. the guide's open items, as guide_entries gives them (its FAIL, partial and stale-Now logic); owner decisions come per item below,
    #    context size and budget are no unfinished work
    try:
        for g in H.guide_entries(root, st, sid, ctx_state=(None, None)):
            gid = g.get("id") or ""
            if g.get("p") == "✅" or gid.startswith(("dec:", "ctx", "budget")) or gid == "close":  # close: the stored check itself
                continue
            lines.append(close_line(f"guide:{gid}", "⛔" if g.get("p") in ("🔴", "⏳") else "⚠", "guide", g.get("text") or "", g.get("recheck") or g.get("fill") or ""))
    except Exception as e:
        lines.append(close_line("guide", "not checked", "guide", f"the guide could not be read ({type(e).__name__})"))
    # 2. this session's own open Needs-you items
    try:
        need = [l for l in memo_read(root)["Needs you"].splitlines() if H.need_open(l)]
        own, _ = H.need_split(need, sid, root, "session")
        for l in own:
            iid = H.need_id(l)
            lines.append(close_line(f"need:{iid}", "⚠", "needs", clip(H.need_text(l), 160),
                                    H.shell_cmd("memo.py", "resolve", "#" + iid, "--session", sid) + ' --trace "<evidence or: user answered: …>"'))
    except Exception as e:
        lines.append(close_line("needs", "not checked", "needs", f"Needs you could not be read ({type(e).__name__})"))
    # 2b. the Now's Left lines: work this session itself says is not done, one ⛔ each
    try:
        for x in H.now_left(sess_read(root, sid)["Now"]):
            lines.append(close_line(f"left:{hashlib.sha1(x.encode()).hexdigest()[:8]}", "⛔", "left", f"Left in the Now: {clip(x, 160)}",
                                    "finish it, or say it stays open (an archive turns it into a Needs-you item)"))
    except Exception as e:
        lines.append(close_line("left", "not checked", "left", f"the Now's Left lines could not be read ({type(e).__name__})"))
    # 3. git: every work repository (the project root when nothing was edited), each of its worktrees, each open sub session's folder
    repos, seen = [], set()
    def add(path, name, wt=False):
        rp = os.path.realpath(path) if path else ""
        if rp and rp not in seen:
            seen.add(rp)
            repos.append((rp, name, wt))
    for key in H.close_work_keys(root, st) or [root]:  # the fingerprint (close_repos) also reads the launch root; scratch repositories are left out
        add(repo_top(key) or key, Path(repo_top(key) or key).name)
    try:
        subs = H.open_sub_sessions(sid, st, detail=True)
    except Exception:
        subs = None
    sub_list = subs[0] if subs else []
    for s in sub_list:
        if s.get("cwd"):
            add(s["cwd"], f"sub session {s['name']}", True)
    # a worktree is this session's to check only when it is under the project or the launch root, or a work key, a sub session's folder or a
    # file this session edited is in it; another worktree's branch is still told by its main repository's branch lines
    tops = [os.path.realpath(x) for x in (root, st.get("root")) if x]
    edited = []
    if st.get("transcript_path"):
        edited = [ti.get("file_path") or ti.get("notebook_path") for n, ti, _ in transcript_tools(st["transcript_path"], started) or [] if n in H.EDIT_TOOLS]
    inside = [os.path.realpath(x) for x in [*(st.get("work") or {}), *[s.get("cwd") for s in sub_list], st.get("root"), *edited] if x]
    in_scope = lambda wp: any(under(wp, t) for t in tops) or any(under(x, wp) for x in inside)
    sub_br = {(os.path.realpath(repo_top(s["cwd"]) or s["cwd"]), s.get("branch")) for s in sub_list if s.get("cwd") and s.get("branch")}
    stores = set()
    pr_on = H.close_pr_check(root)
    i = 0
    while i < len(repos):
        rp, name, wt = repos[i]
        i += 1
        end = time.monotonic() + cap if cap else None
        f = close_git(rp, timeout, end)
        mine = [w for w in f["worktrees"] if in_scope(os.path.realpath(w["path"]))]
        skip = {w["branch"] for w in mine if w["branch"]} | {b for r_, b in sub_br if r_ == rp}
        gl = close_git_lines(f, name, wt, skip)
        if f["git"]:  # a waive holds for this content only (the tree, HEAD and refs), never for the same words over other files
            try:
                rh = H.close_repo_hash(rp, timeout, end, worktrees=False).hex()
            except Exception:
                rh = repr(time.time())
            for l in gl:
                l["key"] = hashlib.sha1((l["text"] + "\0" + rh).encode()).hexdigest()[:12]
        lines += gl
        if pr_on and not pr and f["git"] and f["branch"]:
            if not any(l["id"] == "pr" for l in lines):
                lines.append(close_line("pr", "not checked", "pr", "open pull requests not checked at Stop (Close check = auto never asks gh)", "run close-check (/abofles:close)"))
        elif pr_on and f["git"] and f["branch"]:  # `Close PR check = on`: one gh call per branch, inside the repository's cap
            lines += close_pr_lines(rp, f["branch"], name, close_pr(rp, f["branch"], timeout, end))
        for w in mine:
            if w["prunable"]:
                lines.append(close_line(f"git:{w['path']}", "not checked", "git", f"{name}: worktree {w['path']} is gone (prunable)", "git worktree prune, once its work is safe"))
                seen.add(os.path.realpath(w["path"]))
            else:
                add(w["path"], f"{name} worktree {Path(w['path']).name}" + (f" ({w['branch']})" if w["branch"] else ""), True)
        if f["git"]:  # 4. this session's own live claim, once per claims store (worktrees share it)
            try:
                import claims as C
                left = timeout if end is None else min(timeout, end - time.monotonic())
                c = C.live_claim(rp, sid, left) if left > 0 else False
            except Exception:
                c = False
            if c is False:
                lines.append(close_line(f"claim:{rp}", "not checked", "claim", f"{name}: the claims store could not be read"))
            elif c:
                key = (c.get("session"), c.get("branch"), c.get("since"))
                if key not in stores:
                    stores.add(key)
                    lines.append(close_line(f"claim:{rp}", "⚠", "claim", f"{name}: this session still holds a live claim on branch {C.clean(c.get('branch') or '-')}",
                                            H.shell_cmd("claims.py", "release", "--session", c.get("session"), "--repo", rp)))
    # 5. open sub sessions: an unmerged or unpushed branch is a blocker
    if subs is None and not any(os.path.lexists(d) for d in H.APP_SESSION_DIRS):
        lines.append(close_line("sub", "✓", "sub", "no sub sessions (no app records)"))  # no desktop app here: no session of this one can have been started
    elif subs is None and app_dirs_readable() and not st.get("local_id") and H.own_app_record(sid) is None:
        lines.append(close_line("sub", "✓", "sub", "no sub sessions (this session has no app record: it was not started from the desktop app)"))
    elif subs is None:
        lines.append(close_line("sub", "not checked", "sub", "sub sessions: the app's session records could not be read"))
    for s in sub_list:
        nm, br, cwd_ = s.get("name") or "?", s.get("branch") or "", s.get("cwd") or ""
        end = time.monotonic() + cap if cap else None
        if not br and cwd_:
            br = close_git(cwd_, timeout, end).get("branch") or ""
        sid_ = f"sub:{s.get('id') or nm}"
        if not br or not cwd_:
            lines.append(close_line(sid_, "not checked", "sub", f"sub session {nm} is open; its {'branch' if cwd_ else 'folder'} is not known", "open it and check its work"))
            continue
        b = close_branch(cwd_, br, timeout, end)
        if b["timeout"] or b["error"]:
            lines.append(close_line(sid_, "not checked", "sub", f"sub session {nm}: branch {br} — " + ("a git call ran past %d s" % CLOSE_TIMEOUT if b["timeout"] else b["error"])))
            continue
        if not b["exists"]:
            lines.append(close_line(sid_, "not checked", "sub", f"sub session {nm} is open; its branch {br} is not in {cwd_}", "open it and check its work"))
            continue
        bad, warn = [], []
        if b["gone"]:
            warn.append(f"its upstream {b['upstream']} is gone")
        elif not b["upstream"]:
            bad.append("no upstream (never pushed)")
        elif b["local"]:
            bad.append(f"tracks the local branch {b['upstream']} (never pushed)")
        elif b["ahead"]:
            bad.append(f"{b['ahead']} unpushed commit{'s' if b['ahead'] != 1 else ''}")
        if b["merged"] is False and b["squashed"]:
            warn.append(f"not merged into {b['base']}, but its changes are there (squash or rebase merge)")
        elif b["merged"] is False:
            bad.append(f"not merged into {b['base']}")
        if bad:
            lines.append(close_line(sid_, "⛔", "sub", f"sub session {nm} is open: branch {br} " + ", ".join(bad + warn), f"finish or merge it, then archive the sub session ({GIT_FIX})"))
        elif b["merged"] is None:
            lines.append(close_line(sid_, "not checked", "sub", f"sub session {nm} is open: branch {br} " + (", ".join(warn) + "; " if warn else "pushed; ")
                                    + "no main branch to check the merge against"))
        elif warn:
            lines.append(close_line(sid_, "⚠", "sub", f"sub session {nm} is still open: branch {br} " + ", ".join(warn), "check its work is in the main branch, then archive the sub session"))
        else:
            lines.append(close_line(sid_, "⚠", "sub", f"sub session {nm} is still open (branch {br} merged into {b['base']})", "archive the sub session"))
    # 6. connector actions and 7. writes outside the work roots: from the hooks' records, else from the transcript's tool_use entries
    acts, outside = st.get("actions"), st.get("outside")
    if acts is None or outside is None:
        tools = transcript_tools(st.get("transcript_path") or "", started) if st.get("transcript_path") else None
        if tools is None:
            lines.append(close_line("transcript", "not checked", "action", "connector actions and outside writes: no hook record and the transcript could not be read"))
            tools = []
        if acts is None:
            acts = [{"tool": n, "ts": ts} for n, _, ts in tools if n.startswith("mcp__") and not H.MCP_READ.match(n) and not H.CLOSE_SKIP_TOOLS.match(n)]
        if outside is None:
            outside = []
            for n, ti, _ in tools:
                p = (ti.get("file_path") or ti.get("notebook_path") or "") if n in H.EDIT_TOOLS else ""
                if p and not H.work_key(st, p) and os.path.realpath(p) not in outside:
                    outside.append(os.path.realpath(p))
    groups = {}
    for a in acts or []:
        t, ts = a.get("tool") or "?", int(a.get("ts") or 0)
        if ts and ts < started:
            continue
        if SCHEDULE_TOOL_RE.match(t):
            lines.append(close_line(f"action:{t}:{ts}", "⚠", "action", f"scheduled task call {t} at {hm(ts)}", "confirm the task and its schedule are what you want"))
        else:
            n, last = groups.get(t, (0, 0))
            groups[t] = (n + 1, max(last, ts))
    for t, (n, last) in groups.items():
        lines.append(close_line(f"action:{t}", "⚠", "action", f"{t} ×{n} (last {hm(last)}): an action outside the repository", "confirm its outcome"))
    mem = memory_dir(st)
    mem_r = os.path.realpath(mem)
    scratch = close_scratch()
    for p in outside or []:
        if under(p, mem_r) or any(under(p, d) for d in scratch):
            continue  # the auto-memory has its own lines; a temp file is scratch
        lines.append(close_line(f"outside:{p}", "⚠", "outside", f"written outside the work roots: {p}", "confirm it belongs to this task, or move it into the repository"))
    # 8. Verify after the last edit
    et = {k: int(v or 0) for k, v in (st.get("edit_ts") or {}).items()}
    if et:
        last_key = max(et, key=et.get)
        vt = int(st.get("verify_ts") or 0)
        verify = H.now_verify(root, sid)
        if vt >= et[last_key]:
            lines.append(close_line("verify", "✓", "verify", f"Verify ran at {hm(vt)}, after the last edit ({hm(et[last_key])})"))
        elif not verify:
            lines.append(close_line("verify", "not checked", "verify", f"edits in {Path(last_key).name} (last {hm(et[last_key])}); the Now has no Verify line to check them against",
                                    "write the Verify line into Now (/abofles:memo), then run it"))
        else:
            lines.append(close_line("verify", "⚠", "verify", f"{Path(last_key).name} was edited at {hm(et[last_key])}" + (f", after the last Verify run ({hm(vt)})" if vt else "; Verify has not run since"),
                                    "run the Verify line: " + "; ".join(f"`{v}`" for v in verify)))
    # 9. the auto-memory: files changed this session, files MEMORY.md does not list
    try:
        if not mem.is_dir():
            lines.append(close_line("memory", "✓", "memory", f"no auto-memory folder ({mem})"))
        else:
            files = sorted(p for p in mem.glob("*.md") if p.name != "MEMORY.md")
            changed = [p.name for p in files + [mem / "MEMORY.md"] if p.exists() and started and p.stat().st_mtime >= started]
            lines.append(close_line("memory:changed", "✓", "memory", (f"{len(changed)} memory file{'s' if len(changed) != 1 else ''} changed this session: " + ", ".join(changed[:8])
                                                                    + (" …" if len(changed) > 8 else "")) if changed else "no memory file changed this session"))
            idx = (mem / "MEMORY.md").read_text(encoding="utf-8", errors="replace") if (mem / "MEMORY.md").exists() else ""
            listed = memory_listed(idx)
            for p in files:
                if p.name not in listed:
                    lines.append(close_line(f"memory:index:{p.name}", "⚠", "memory", f"memory file {p.name} is not listed in MEMORY.md", f"add a one-line entry for {p.name} to MEMORY.md"))
    except OSError as e:
        lines.append(close_line("memory", "not checked", "memory", f"the auto-memory folder could not be read ({type(e).__name__})"))
    # 10. the project's `Close:` rules: never run here, the close agent judges each one
    try:
        rules = [(CLOSE_RULE_RE.match(l), src) for l, src in H.override_lines(H.override_blocks(root))]
    except Exception:
        rules = []
    off = H.close_agent_mode(root) == "off"
    for m, src in rules:  # under `Close agent = off` nobody judges it: ⚠, the owner accepts it (close-waive) once it is checked by hand
        if m:
            lines.append(close_line(f"rule:{m.group(1).strip().lower()}", "⚠" if off else "not checked", "rule", f"rule {m.group(1)}: {m.group(2)} ({src})",
                                    "Close agent = off: check it yourself, then accept it with the owner's words (close-waive)" if off else "the close agent judges it"))
    # 11. a `- Close … = <value>` the hooks cannot read: its default is used, the owner should know
    for key in CLOSE_KEYS:
        try:
            v = H.override_value(root, key)
        except Exception:
            v = None
        if v is not None and str(v).strip() and H.select_of(key, v) is None:
            spec = H.OVERRIDE_KEYS[key]
            lines.append(close_line(f"config:{key.lower().replace(' ', '-')}", "⚠", "config", f"- {key} = {clip(str(v).strip(), 60)}: unknown value — the default {spec['default']} is used",
                                    "set one of: " + ", ".join(spec["options"])))
    seen = {}
    for l in lines:
        seen[l["id"]] = seen.get(l["id"], 0) + 1
        if seen[l["id"]] > 1:
            l["id"] = f"{l['id']}#{seen[l['id']]}"
    return close_by_rule(lines, H.close_git_rule(root))


CLOSE_KEYS = ("Close check", "Close git", "Close agent", "Close PR check", "Close place")


def session_start(st):
    """The session's own start (first_started, set at startup and /clear only), else `started`: what the close check reads the session from."""
    return int(st.get("first_started") or st.get("started") or 0)


def app_dirs_readable():
    """True when every app session-record folder that exists can be listed (a folder that cannot be read leaves the sub sessions unknown)."""
    for d in H.APP_SESSION_DIRS:
        if os.path.lexists(d):
            try:
                os.listdir(d)
            except OSError:
                return False
    return True


GIT_RULE_KINDS = {"committed": ("upstream", "ahead"), "local": ("dirty", "upstream", "ahead")}  # the git line kinds (id suffix) each `Close git` rule allows


def close_by_rule(lines, rule):
    """`Close git = committed | local`: the git lines that rule allows (committed: no upstream, unpushed commits; local: the dirty tree too) turn
    from ⛔ into ✓ "by rule" (`by_rule` names it): the owner's own rule settles them, so `local` can reach ✅ with no waive. pushed changes
    nothing; a detached HEAD, a sub session's branch and a stash keep their state."""
    kinds = GIT_RULE_KINDS.get(rule, ())
    for l in lines:
        if l["src"] == "git" and l["state"] == "⛔" and l["id"].split("#")[0].rsplit(":", 1)[-1] in kinds:
            l.update(state="✓", text=l["text"] + f" — by rule (Close git = {rule})", by_rule=f"Close git = {rule}")
    return lines


def wkey(l):
    """What a waive is bound to besides the line id: the line's content hash (`key`: a git line's tree and HEAD), else its text."""
    return l.get("key") or l.get("text")


def close_agent_target(root, st, sid, cap=None):
    """What the close agent judged: the work (close_fp_repos: every repository's refs and tree), the memo's Decisions (main and this session's)
    and the auto-memory files (names, mtimes). Its lines hold while this is unchanged."""
    h = H.close_fp_repos(root, st, cap=cap)
    try:
        h.update(("\0dec " + memo_read(root)["Decisions"] + "\0" + sess_read(root, sid)["Decisions"]).encode())
    except Exception:
        h.update(b"\0dec ?")
    try:
        mem = memory_dir(st)
        h.update(json.dumps(sorted((p.name, p.stat().st_mtime_ns) for p in mem.glob("*.md")) if mem.is_dir() else []).encode())
    except Exception:
        h.update(b"\0mem ?")
    return h.hexdigest()[:16]


def close_record_of(lines, root, st, sid, fp=True, cap=None):
    """The st["close"] record of an inventory: lines (a waive of the stored check kept on a line of the same id and content — wkey; the close
    agent's lines of the stored check kept while their target is unchanged — close_agent_target, the rule a line judges still worded the same),
    verdict (close_verdict with `agent`), ts, fp (close_fp; `fp=False`: '' — the caller sets it after its own writes), turn_ts, stale (False),
    git_rule, agent: off (Close agent = off), yes (the stored check's agent run still holds), no (the close agent has not run for this check)."""
    old = st.get("close") if isinstance(st.get("close"), dict) else {}
    oldl = [l for l in old.get("lines") or [] if isinstance(l, dict)]
    waives = {(l.get("id"), wkey(l)): l["waived"] for l in oldl if l.get("waived") and l.get("by") != "agent"}
    for l in lines:
        w = waives.get((l["id"], wkey(l)))
        if w and l["state"] != "✓":
            l["waived"] = w
    agent = "off" if H.close_agent_mode(root) == "off" else "no"
    ag = [dict(l) for l in oldl if l.get("by") == "agent"]
    tgt = None
    if agent == "no" and (ag or old.get("agent") == "yes"):
        tgt = close_agent_target(root, st, sid, cap)
        rules = {l["id"]: l["text"] for l in lines if l["src"] == "rule"}
        lines = lines + [l for l in ag if l.get("target") == tgt and (not l.get("judges") or rules.get(l["judges"]) == l.get("judges_text"))]
        if old.get("agent") == "yes" and old.get("agent_target") == tgt:
            agent = "yes"
    rec = {"lines": lines, "verdict": H.close_verdict(lines, agent), "ts": int(time.time()), "fp": H.close_fp(root, st, sid, cap=cap) if fp else "",
           "turn_ts": st.get("turn_ts") or 0, "stale": False, "git_rule": H.close_git_rule(root), "agent": agent}
    if agent == "yes":
        rec["agent_target"] = tgt
    if old.get("agent_seq"):
        rec["agent_seq"] = old["agent_seq"]
    return rec


def close_text(rec, root, sid):
    """The stored check for the chat: one line per entry (state, source, text; its fix, or the owner's words of a waive, indented below), then
    the Verdict line with the counts."""
    lines = rec["lines"]
    judged = H.close_judged(lines)
    out = [f"Close check · {Path(root).name} · session {sid[:8]} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(rec.get('ts') or time.time()))}"]
    for l in lines:
        out.append(f"{l['state']:<11} {l['src']:<8} {l['text']}" + (" · by the close agent" if l.get("by") == "agent" else "")
                   + (" · judged by the close agent" if l["src"] == "rule" and l.get("by") != "agent" and l["id"] in judged else "") + f"  [{l['id']}]")
        if l.get("evidence"):
            out.append(f"{'':<20} evidence: {l['evidence']}")
        if l.get("waived"):
            out.append(f"{'':<20} accepted by you: \"{l['waived'].get('trace') or ''}\"")
        elif l["fix"] and l["state"] != "✓":
            out.append(f"{'':<20} → {l['fix']}")
    n = {s: sum(1 for l in lines if l["state"] == s) for s in CLOSE_STATES}
    w = sum(1 for l in lines if l.get("waived"))
    out.append("")
    out.append(f"Verdict {rec['verdict']} · " + " · ".join(f"{s} {n[s]}" for s in CLOSE_STATES) + (f" · accepted by you {w}" if w else "")
               + (" — stale: run close-check again" if rec.get("stale") else "")
               + ("" if rec["verdict"] == "✅" else " — ✅ only with no `not checked` line and no ⛔ or ⚠ left unwaived"))
    if rec.get("agent") == "no":
        out.append("Close agent: not run for this check — start abofles-close, then record its lines with close-add (its `- none`: close-add --none); "
                   "✅ waits for it")
    return "\n".join(out)


def close_store(root, st, sid, rec):
    """Keep a check: the session file's Close section (close_body), then the fingerprint (after that write), then st["close"]. The memo write's
    failure is printed, the state still gets the record."""
    r = memo_apply(root, "close", sid, body=H.close_body(rec))
    if not r:
        print(f"abofles: the Close section was not written — {r.reason or r.code}")
    with state_lock(sid):
        stx = load_state(sid)
        if st.get("local_id") and not stx.get("local_id"):
            stx["local_id"] = st["local_id"]  # open_sub_sessions found this session's app id
        rec["fp"] = H.close_fp(root, stx, sid)
        stx["close"] = rec
        save_state(sid, stx)
    return rec


def close_check_cmd(rest, root, st, sid):
    """`memo.py close-check [--json]`: the evidence inventory and its verdict, text or JSON; kept in the session state (`close`: lines, verdict, ts,
    fp, turn_ts, stale, git_rule) and the session file's Close section. Exit 0."""
    if [a for a in rest if a != "--json"]:
        print("usage: memo.py close-check [--json]")
        sys.exit(2)
    rec = close_store(root, st, sid, close_record_of(close_inventory(root, st, sid), root, st, sid, fp=False))
    agent = H.close_agent_mode(root)
    if "--json" in rest:
        print(json.dumps({"session": sid, "project": root, **rec, "agent_mode": agent}, ensure_ascii=False, indent=1))
    else:
        print(close_text(rec, root, sid) + ("\n" + CLOSE_AGENT_OFF if agent == "off" else ""))


CLOSE_AGENT_OFF = "Close agent = off — skip the close agent: the script's lines only; a `Close:` rule line is ⚠ until you check it and the owner accepts it"
CLOSE_ADD_STATES = {"⛔": "⛔", "⚠": "⚠", "⚠️": "⚠", "✓": "✓", "not checked": "not checked", "not-checked": "not checked"}


def close_agent_ran(sid, rec):
    """True when a close agent's ledger row (`close`) is dated at or after the stored check `rec`: the agent ran for this check."""
    try:
        return any(r.get("close") and (r.get("ts") or 0) >= (rec.get("ts") or 0) for r in H.ledger_rows(sid))
    except Exception:
        return False


def uniq_id(lines, want):
    """`want`, or `want#2`, `want#3` … when a line has that id already."""
    ids, n, out = {l.get("id") for l in lines}, 1, want
    while out in ids:
        n += 1
        out = f"{want}#{n}"
    return out


def close_add_cmd(rest, root, st, sid):
    """`memo.py close-add <state> --text "<finding>" --evidence "<proof>" [--rule <name> | --src <word>] [--fix "<next step>"]` or
    `memo.py close-add --none`: the main session records one line the close agent returned (`--none`: its `- none`, no line). Only after a close
    agent ran for the stored check (its ledger row is dated after the check). It only appends: a script line is never edited or removed.
    `--rule <name>` judges the stored `Close:` rule line `rule:<name>` (⛔, ⚠ or ✓ settles it in the verdict; the rule line itself stays). Each
    line keeps its target (close_agent_target): a later check keeps it while that is unchanged. The record's `agent` turns yes; the verdict is
    counted again. The fingerprint is counted again after the write (the close agent's own row is never in it): a changed tree, HEAD or turn makes
    it stale. Exit 0 added · 1 no check stored or no such rule line · 2 usage · 3 the same line is there already · 5 no close agent ran since the
    check · 6 the session state is busy (nothing written)."""
    rest = list(rest)
    none = "--none" in rest
    rest = [a for a in rest if a != "--none"]
    text = " ".join((take_opt(rest, "--text") or "").split())
    ev = " ".join((take_opt(rest, "--evidence") or "").split())
    fix = " ".join((take_opt(rest, "--fix") or "").split())
    rule = (take_opt(rest, "--rule") or "").strip().lower()
    src = (take_opt(rest, "--src") or "").strip().lower()
    state = CLOSE_ADD_STATES.get(" ".join(rest).strip().lower() if rest else "")
    if (none and (rest or text or ev or fix or rule or src)) or (not none and (not state or not text or not ev or (src and not re.fullmatch(r"[a-z]{2,12}", src))
                                                                               or (rule and src and src != "rule"))):
        print('usage: memo.py close-add <⛔|⚠|✓|"not checked"> --text "<finding>" --evidence "<proof: path:line, command → result, the owner\'s words>" '
              '[--rule <name> | --src <word>] [--fix "<next step>"]  ·  memo.py close-add --none (the close agent found nothing)')
        sys.exit(2)
    with state_lock(sid) as lk:
        if lk.busy:
            print("abofles: the session state is busy — nothing added; run it again")
            sys.exit(6)
        stx = load_state(sid)
        rec = stx.get("close") if isinstance(stx.get("close"), dict) else None
        if not rec:
            print("abofles: no close check stored — run memo.py close-check first")
            sys.exit(1)
        if not close_agent_ran(sid, rec):
            print("abofles: no close agent ran for the stored check — start abofles-close (header `abofles: Close Check / Verify · T2`) after "
                  "close-check, then record its lines")
            sys.exit(5)
        lines = rec.get("lines") or []
        if rule and not any(l.get("id") == f"rule:{rule}" and l.get("by") != "agent" for l in lines):
            print(f"abofles: no rule line rule:{rule} in the stored check (rules: " + ", ".join(l["id"][5:] for l in lines if l.get("src") == "rule" and l.get("by") != "agent") + ")")
            sys.exit(1)
        tgt = close_agent_target(root, stx, sid)
        line = None
        if not none:
            seq = int(rec.get("agent_seq") or 0) + (0 if rule else 1)
            line = close_line(uniq_id(lines, f"agent:rule:{rule}" if rule else f"agent:{seq}"), state, "rule" if rule else src or "agent", text, fix)
            line.update(evidence=ev, by="agent", target=tgt, **({"judges": f"rule:{rule}", "judges_text": next(l.get("text") for l in lines if l.get("id") == f"rule:{rule}")} if rule else {}))
            if any(l.get("by") == "agent" and (l.get("state"), l.get("text"), l.get("judges")) == (state, text, line.get("judges")) for l in lines):
                print(f"abofles: the same line is there already — nothing changed")
                sys.exit(3)
            if not rule:
                rec["agent_seq"] = seq
        base = H.close_fp_repos(root, stx)  # before this write: the close agent's own row is never in the fingerprint, a change of the work is
        fresh = bool(rec.get("fp")) and H.close_fp_tail(base, H.close_ledger_n(sid), stx) == rec["fp"]
        if line:
            lines.append(line)
        if rec.get("agent") != "off":
            rec["agent"], rec["agent_target"] = "yes", tgt
        rec["lines"], rec["verdict"] = lines, H.close_verdict_of(rec)
        if not fresh:
            rec["stale"] = True
        stx["close"] = rec
        save_state(sid, stx)
    r = memo_apply(root, "close", sid, body=H.close_body(rec))
    if not r:
        print(f"abofles: the Close section was not written — {r.reason or r.code}")
    if fresh:
        with state_lock(sid):  # after the Close section's write: the fingerprint counts the session file in the tree
            stx = load_state(sid)
            c = stx.get("close")
            if isinstance(c, dict) and c.get("ts") == rec.get("ts") and not c.get("stale"):
                c["fp"] = H.close_fp(root, stx, sid)
                save_state(sid, stx)
    print((f"added: {state} {line['src']} {text}  [{line['id']}]" + (f" (judges rule {rule})" if rule else "") if line else "recorded: the close agent found nothing to add")
          + f"\nVerdict {rec['verdict']}" + (" — stale: the work changed since the check; run close-check again" if rec.get("stale") else ""))


DIGEST_ASKS, DIGEST_REPLIES, DIGEST_ANSWERS = 60, 30, 40  # the newest of each the digest keeps
DIGEST_ASK_CHARS, DIGEST_REPLY_CHARS = 400, 600  # each one clipped to this


def close_digest(tp, since=0, full=False):
    """What the close agent reads of the transcript at `tp` (rows at or after `since`; side-chain rows left out): {asks: [(ts, text)] the owner's
    own prompts (no tool result, no harness envelope), replies: [(ts, text)] the last assistant text of each turn, answers: [(ts, question,
    answer)] of every AskUserQuestion, tools: {name: count}, cut: {asks, replies, answers: how many older ones were left out}}. Never the raw tail:
    each text clipped, the newest DIGEST_* of each (`full`: every ask whole, for close-waive's check of the owner's words). None when the file
    cannot be read or holds no readable row."""
    from datetime import datetime
    got = transcript_rows(tp)
    if got is None:
        return None
    asks, replies, answers, tools, asked = [], [], [], {}, {}
    good, last = len(got[0]), None
    for d in got[0]:
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        c = m.get("content")
        try:
            ts = int(datetime.fromisoformat(str(d.get("timestamp") or "").replace("Z", "+00:00")).timestamp())
        except ValueError:
            ts = 0
        if (ts and ts < since) or d.get("isSidechain") or d.get("isMeta"):
            continue
        if d.get("type") == "user":
            blocks = c if isinstance(c, list) else [{"type": "text", "text": c}] if isinstance(c, str) else []
            res = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in res:
                q = asked.pop(b.get("tool_use_id"), None)
                if q is None:
                    continue
                tr = d.get("toolUseResult") if isinstance(d.get("toolUseResult"), dict) else {}
                got = tr.get("answers") if isinstance(tr.get("answers"), dict) else {}
                if not got:  # the result text: `"<question>"="<answer>"`, one pair per question
                    txt = b.get("content") if isinstance(b.get("content"), str) else " ".join(x.get("text") or "" for x in b.get("content") or [] if isinstance(x, dict))
                    got = dict(re.findall(r'"([^"]+)"="([^"]*)"', txt or ""))
                for qq in q or ["?"]:
                    answers.append((ts, clip(qq, 200), clip(str(got.get(qq, "(no answer read)")), 200)))
            text = "\n".join(b.get("text") or "" for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()
            if res or not text or H.prompt_envelope(text):
                continue
            if last:
                replies.append(last)
            last = None
            asks.append((ts, " ".join(text.split()) if full else clip(" ".join(text.split()), DIGEST_ASK_CHARS)))
        elif d.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    last = (ts, clip(" ".join(b["text"].split()), DIGEST_REPLY_CHARS))
                elif b.get("type") == "tool_use" and isinstance(b.get("name"), str):
                    tools[b["name"]] = tools.get(b["name"], 0) + 1
                    if b["name"] == "AskUserQuestion":
                        qs = (b.get("input") or {}).get("questions") if isinstance(b.get("input"), dict) else None
                        asked[b.get("id")] = [str(q.get("question") or "") for q in qs or [] if isinstance(q, dict)]
    if last:
        replies.append(last)
    if not good:
        return None
    if full:
        return {"asks": asks, "replies": replies, "answers": answers, "tools": tools, "cut": {"asks": 0, "replies": 0, "answers": 0}}
    cut = {"asks": max(0, len(asks) - DIGEST_ASKS), "replies": max(0, len(replies) - DIGEST_REPLIES), "answers": max(0, len(answers) - DIGEST_ANSWERS)}
    return {"asks": asks[-DIGEST_ASKS:], "replies": replies[-DIGEST_REPLIES:], "answers": answers[-DIGEST_ANSWERS:], "tools": tools, "cut": cut}


def close_digest_cmd(rest, root, st, sid):
    """`memo.py close-digest [--json]`: what the close agent judges with — the stored check's open lines and `Close:` rules, the owner's asks,
    the final replies, the AskUserQuestion answers and the tool names of this session's transcript (close_digest), and where the memo and the
    auto-memory are. Reads only. Exit 0; 3 under `Close agent = off` (no agent runs, nothing printed but that)."""
    if [a for a in rest if a != "--json"]:
        print("usage: memo.py close-digest [--json]")
        sys.exit(2)
    if H.close_agent_mode(root) == "off":
        print(CLOSE_AGENT_OFF)
        sys.exit(3)
    rec = st.get("close") if isinstance(st.get("close"), dict) else {}
    lines = rec.get("lines") or []
    dg = close_digest(st.get("transcript_path") or "", session_start(st)) if st.get("transcript_path") else None
    out = {"session": sid, "project": root, "memo": str(memo_path(root)), "session_file": str(session_memo_path(root, sid)), "memory": str(memory_dir(st)),
           "verdict": rec.get("verdict") or "", "stale": bool(rec.get("stale")),
           "rules": [{"id": l["id"], "text": l.get("text") or ""} for l in lines if l.get("src") == "rule" and l.get("by") != "agent"],
           "open": [{k: l.get(k) for k in ("id", "state", "src", "text")} for l in lines if l.get("state") != "✓" and not l.get("waived") and l.get("src") != "rule"],
           "transcript": "read" if dg is not None else "not readable", **(dg or {"asks": [], "replies": [], "answers": [], "tools": {}, "cut": {"asks": 0, "replies": 0, "answers": 0}})}
    if "--json" in rest:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    p = [f"Close digest · {Path(root).name} · session {sid[:8]} · since {hm(session_start(st))}",
         f"Memo {out['memo']} · session file {out['session_file']} · auto-memory {out['memory']}",
         f"Stored check: Verdict {out['verdict']}" + (" (stale)" if out["stale"] else "") if rec else "Stored check: none — run memo.py close-check first",
         "", "## Close rules"] + [f"- {r['id']} — {r['text']}" for r in out["rules"]] + ([] if out["rules"] else ["- none"])
    p += ["", "## Open lines"] + [f"- {l['state']} {l['src']} · {l['text']}" for l in out["open"]] + ([] if out["open"] else ["- none"])
    if dg is None:
        p += ["", "## Transcript", "- not readable: judge from the memo, the auto-memory and git only; a rule that needs the chat stays `not checked`"]
    else:
        cut = out.get("cut") or {}
        n = lambda k: f"{len(out[k])} of {len(out[k]) + cut[k]}" if cut.get(k) else str(len(out[k]))
        p += ["", f"## Owner asks ({n('asks')})"] + [f"- {hm(t)} · {x}" for t, x in out["asks"]]
        p += ["", f"## Final replies ({n('replies')})"] + [f"- {hm(t)} · {x}" for t, x in out["replies"]]
        p += ["", f"## Answers ({n('answers')})"] + [f'- {hm(t)} · "{q}" → "{a}"' for t, q, a in out["answers"]]
        if any(cut.values()):
            p += ["", "## Cut", "- " + ", ".join(f"the oldest {cut[k]} {k}" for k in ("asks", "replies", "answers") if cut.get(k))
                  + " are not in this digest: a rule or decision that needs them stays `not checked` (close-add \"not checked\" --src agent)"]
        p += ["", "## Tools", "- " + (" · ".join(f"{n} ×{k}" for n, k in sorted(out["tools"].items(), key=lambda x: -x[1])) or "none")]
    print("\n".join(p))


def owner_said(trace, asks):
    """True when `trace` (a leading `owner:` / `user said:` label taken off) is words of one of the owner's messages `asks` ([(ts, text)]): two
    words at least, found in that message in that order (case, punctuation and spacing ignored)."""
    norm = lambda x: " ".join(re.findall(r"\w+", (x or "").casefold()))
    t = norm(re.sub(r"^\s*(?:the\s+)?(?:owner|user|you)(?:\s+(?:said|says|wrote))?\s*[:：,—–-]\s*", "", trace or "", flags=re.I))
    return len(t.split()) >= 2 and any(f" {t} " in f" {norm(a)} " for _, a in asks or [])


def close_waive_cmd(rest, root, st, sid):
    """`memo.py close-waive <id> --trace "<owner's words>"`: the owner accepts one ⛔, ⚠ or `not checked` line of the stored check (a timeout, a gh
    error, a gone worktree too). `<id>`: the line's id, or a part of it that ends it and fits one line only. The trace must be the owner's own
    words, found in one of their messages of this session (owner_said). The line stays, shown as "accepted by you" with the words; the verdict is
    counted again; the Journal gets a trace line. Exit 0 done · 1 no check, no such line or an id that fits several · 2 usage · 3 already
    accepted or ✓ · 5 refused (a `Close:` rule line the close agent judges; words that are not the owner's) · 6 busy (nothing accepted)."""
    rest = list(rest)
    trace = " ".join((take_opt(rest, "--trace") or "").split())
    if len(rest) != 1 or not trace:
        print('usage: memo.py close-waive <id> --trace "<the owner\'s words>"')
        sys.exit(2)
    lid = rest[0]
    dg = close_digest(st.get("transcript_path") or "", session_start(st), full=True) if st.get("transcript_path") else None
    if not owner_said(trace, (dg or {}).get("asks")):
        print("abofles: the trace is not the owner's words — quote them as they wrote them in this session (two words or more)"
              + ("" if dg else "; the transcript could not be read, so nothing can be accepted"))
        sys.exit(5)
    with state_lock(sid) as lk:
        if lk.busy:
            print("abofles: the session state is busy — nothing accepted; run it again")
            sys.exit(6)
        stx = load_state(sid)
        rec = stx.get("close") if isinstance(stx.get("close"), dict) else None
        lines = (rec or {}).get("lines") or []
        hit = next((l for l in lines if l.get("id") == lid), None)
        fits = [l for l in lines if str(l.get("id") or "").endswith(lid)] if hit is None and lid else []
        if hit is None and len(fits) == 1:
            hit = fits[0]
        if not rec or hit is None:
            print("abofles: no close check stored — run memo.py close-check first" if not rec else
                  (f"abofles: {lid!r} fits {len(fits)} lines — give more of the id: " + ", ".join(l.get("id") or "?" for l in fits)[:400]) if fits else
                  f"abofles: no line {lid!r} in the stored check (ids: " + ", ".join(l.get("id") or "?" for l in lines if l.get("state") != "✓")[:400] + ")")
            sys.exit(1)
        lid = hit.get("id")
        if hit.get("state") == "not checked" and hit.get("src") == "rule" and hit.get("by") != "agent" and rec.get("agent") != "off":
            print(f"abofles: {lid} is a `Close:` rule the close agent judges — start it (/abofles:close), it records the rule's state")
            sys.exit(5)
        if hit.get("state") == "✓" or hit.get("waived"):
            print(f"abofles: {lid} is " + ("✓ already" if hit.get("state") == "✓" else "accepted already") + " — nothing changed")
            sys.exit(3)
        ts = int(time.time())
        hit["waived"] = {"trace": trace, "ts": ts}
        rec["verdict"] = H.close_verdict_of(rec)
        stx["close"] = rec
        save_state(sid, stx)
    r = memo_apply(root, "close", sid, body=H.close_body(rec))
    if not r and r.code == "busy":  # the Close section still says the old verdict: take the waive back, say nothing was accepted
        with state_lock(sid):
            stx = load_state(sid)
            c = stx.get("close") if isinstance(stx.get("close"), dict) else {}
            for l in c.get("lines") or []:
                if l.get("id") == lid and (l.get("waived") or {}).get("ts") == ts:
                    l.pop("waived", None)
            if c:
                c["verdict"] = H.close_verdict_of(c)
                save_state(sid, stx)
        print("abofles: memo busy — nothing accepted (the Close section could not be written); run it again")
        sys.exit(6)
    if not r:
        print(f"abofles: the Close section was not written — {r.reason or r.code}")
    memo_trace(root, sid, f"close check: accepted by you — {hit['state']} {clip(hit.get('text') or '', 100)}: {clip(trace, 120)}")
    print(f"accepted by you: {hit['state']} {hit.get('text')}  [{lid}]\nVerdict {rec['verdict']}" + (" — stale: run close-check again" if rec.get("stale") else ""))


def main():
    args = sys.argv[1:]
    blank = args[-2:] == ["--session", ""]  # a command whose ${CLAUDE_SESSION_ID} expanded to nothing: a read goes on, a write is refused below
    if blank:
        del args[-2:]
    sess = session_arg(args, keep=bool(args) and args[0] in ("resolve", "reopen"))  # those two read --session themselves
    scope = scope_arg(args)
    st = newest_state(os.getcwd(), sess)
    cwd = st.get("project") or st.get("root") or os.getcwd()  # the project the session's edits moved the memo to, else where it started — never the folder a `cd` left us in
    sp = newest_state_path(os.getcwd(), sess)
    sid = sp.stem if sp else ""  # the session id (its state file), so the Now block and receipts carry the same tag
    if not sid:  # a session named by hand (--session / ABOFLES_SESSION) that has no state file here
        named = sess or os.environ.get("ABOFLES_SESSION") or ""
        sid = re.sub(r"[^A-Za-z0-9_-]", "", named) if "${" not in named else ""
    cmd = args[0] if args else "show"
    writes = ("now", "note", "receipt", "resolve", "reopen", "move", "close-check", "close-waive", "close-add") + CURATOR_CMDS
    env_sid = os.environ.get("ABOFLES_SESSION") or ""
    env_sid = "" if "${" in env_sid else re.sub(r"[^A-Za-z0-9_-]", "", env_sid)
    if cmd in writes and blank and not (env_sid and H.state_path(env_sid).exists()):
        no_session()  # an empty --session names no session: never the folder's newest one, whose state and memo would get this write
    if cmd in writes and env_sid and not H.state_path(env_sid).exists():
        no_session()  # a ghost ABOFLES_SESSION: no session file is made for it, and the folder's newest session does not get its write either
    if cmd in ("now", "note") + CURATOR_CMDS and not sid:
        no_session()
    if not sp and (sess or "").strip() and cmd in ("now", "note", "receipt", "resolve", "reopen") + CURATOR_CMDS:
        no_session()  # a named session (a UUID or a short id) with no state here: a write would make a session file for a session that does not exist
    if cmd == "scope":
        want = (args[1] if len(args) > 1 else "").strip().lower()
        if want:
            if want not in SCOPES:
                print("usage: memo.py scope session|project|all" + (" (`off` is only valid as `- Resume = off` in the `## abofles overrides` block)" if want in ("off", "none", "0") else ""))
                sys.exit(2)
            cfg = config_read()
            cfg["resume_scope"] = want
            if not config_write(cfg):
                print(f"abofles: could not write {CONFIG_PATH}")
                sys.exit(1)
        sc, src = resume_scope(cwd, scope)
        where = {"flag": "--scope", "override": "`- Resume = …` in the override block", "default": f"the saved default ({CONFIG_PATH})", "built-in": "built-in default"}[src]
        print(("saved as the default · " if want else "") + f"scope: {sc} (from {where})")
    elif cmd == "move":
        dest = os.path.realpath(args[1]) if len(args) > 1 and args[1].strip() else ""
        if not dest or not os.path.isdir(dest) or repo_top(dest) != dest:  # a project is a repository's top folder, never any folder (`move /`)
            print("usage: memo.py move <project folder: the top of a git repository>")
            sys.exit(2)
        if not sid or not st:
            print("abofles: no session state for this folder — nothing moved")
            sys.exit(4)
        if dest == os.path.realpath(cwd):
            print(f"abofles: the memo is already in {dest}")
            sys.exit(3)
        r = H.memo_move_result(cwd, dest, sid, "moved by hand (memo.py move)")
        if not r:  # refused (busy, a failed read or write): the memo stays, and the session is not pinned to a project it is not in
            print(f"abofles: not moved — {r.reason or r.code}")
            sys.exit(1)
        with state_lock(sid):
            st2 = load_state(sid)
            st2["project_pinned"] = dest  # a hand move sticks: edit() never moves the memo off it, a startup/clear of this session keeps it
            if dest == os.path.realpath(st2.get("root") or ""):
                st2.pop("project", None)
            else:
                st2["project"] = dest
            refresh_guide(dest, st2, sid)
            save_state(sid, st2)
        print(f"moved: session {sid[:8]} → {memo_path(dest)}")
    elif cmd in CURATOR_CMDS:
        curator_cmd(cmd, args[1:], cwd, sid)
    elif cmd == "repair":
        repair_cmd(args[1:], cwd)
    elif cmd == "close-check":
        if not sp or not st:
            no_session()
        close_check_cmd(args[1:], cwd, st, sid)
    elif cmd == "close-waive":
        if not sp or not st:
            no_session()
        close_waive_cmd(args[1:], cwd, st, sid)
    elif cmd == "close-add":
        if not sp or not st:
            no_session()
        close_add_cmd(args[1:], cwd, st, sid)
    elif cmd == "close-digest":
        if not sp or not st:
            no_session()
        close_digest_cmd(args[1:], cwd, st, sid)
    elif cmd == "path":
        print(session_memo_path(cwd, sid) if "mine" in args[1:] else memo_path(cwd))
    elif cmd == "show":
        if "--all" in args or "--main" in args:
            try:
                out = memo_path(cwd).read_text()
            except Exception:
                print("abofles: no memo yet — it is written at the first receipt, or by /abofles:memo.")
                return
            if "--all" in args and sid and session_memo_path(cwd, sid).exists():
                out += "\n\n" + session_memo_path(cwd, sid).read_text()
            print(out)
            return
        # the guide line is computed here for the same scope as the block (the stored one follows the default scope)
        g = guide_line(cwd, st, sid, scope) if st else (st.get("guide") or {}).get("line")
        block = resume_block(cwd, session_id=sid, guide=g, scope=scope, explicit=scope is not None)
        print(block or "abofles: no memo yet — it is written at the first receipt, or by /abofles:memo.")
    elif cmd in ("resolve", "reopen"):
        undo = cmd == "reopen"
        usage = "usage: memo.py resolve <n|#id> <note> [--trace] [--session <sid>] [--force]" if not undo else "usage: memo.py reopen <n|#id> [--trace] [--session <sid>] [--force]"
        rest, a_sid = [], None
        i = 1
        while i < len(args):  # --session takes a value; everything else that is not --trace / --force is the note
            if args[i] == "--session":
                v = args[i + 1] if i + 1 < len(args) else ""
                if not v.strip() or v.startswith("--"):
                    print(usage)
                    sys.exit(2)
                if a_sid is None:  # the first one wins: the user's own, before the command's appended fallback
                    a_sid = v
                i += 2
                continue
            if args[i] not in ("--trace", "--force"):
                rest.append(args[i])
            i += 1
        if not rest or not (rest[0].isdigit() or re.fullmatch(r"#?[0-9a-fA-F]{4}", rest[0])):
            print(usage)
            sys.exit(2)
        n = rest[0]
        if a_sid is not None:  # the named session; a short id keeps the full one it resolved to (its state holds the band's Undo)
            named = re.sub(r"[^A-Za-z0-9_-]", "", a_sid) or "unknown"
            sid = sid if sid and sid.startswith(named) else named
        if not sid:
            no_session()
        if not undo and not " ".join(rest[1:]).strip():  # nothing is settled on a bare word: the note is the evidence (a question's answer settles its items in the hook, without this command)
            print(NO_EVIDENCE)
            sys.exit(2)
        r = memo_apply(cwd, cmd, sid, ref=n, force="--force" in args, **({} if undo else {"note": " ".join(rest[1:])}))
        if not r:
            print("abofles: " + r.reason)
            sys.exit({"empty-sid": 4, "other-session": 5, "already": 3, "busy": 6, "write-failed": 7}.get(r.code, 1))
        hit = r.value
        print(f"{'reopened' if undo else 'resolved'}: {hit}")
        item = NEED_TAIL_RE.sub("", re.sub(r"^\d+\. \[.\] \S+ \S+ · (owner decision:\s*)?", "", hit.split(" → resolved")[0]))  # the hook's own tag pattern
        note = "" if undo else " ".join(rest[1:]).strip()
        if "--trace" in args:
            memo_trace(cwd, sid, f"{'reopened' if undo else 'resolved'} by hand: owner decision {n}: " + (clip(item, 70) + f" · {note}" if note else item))  # the note is the evidence: it must survive the Journal clip
        if undo:
            last_resolved_note(cwd, sid)  # a reopen ends the band's Undo
        elif sid:
            iid = re.search(r"#([0-9a-fA-F]{4})\b", hit)  # Undo reopens the item by its stable id: its number can change before the press
            last_resolved_note(cwd, sid, f"owner decision {n}: {item}", script_cmd("memo.py", "reopen", f"#{iid.group(1)}" if iid else n, "--trace", "--session", sid))
    elif cmd == "note":
        text = " ".join(args[1:]).strip()
        if not text:
            print("usage: memo.py note <text>")
            sys.exit(2)
        r = memo_apply(cwd, "decision", sid, question="note", answer=text)
        if not r:
            print("abofles: " + r.reason)
            sys.exit({"busy": 6, "write-failed": 7}.get(r.code, 4))
        print("noted")
    elif cmd == "receipt":
        text = sys.stdin.read().strip()
        if not (has_receipt(text) or looks_like_receipt(text)):
            print("abofles: not a receipt — pass the receipt lines (the 🧾 header and its done/why/check/open/next) on stdin")
            sys.exit(2)
        if not sid:
            print("abofles: no session state — name it with --session")
            sys.exit(2)
        own = has_receipt(text)
        foreign = not own and looks_like_receipt(text)
        if H.parse_receipt(text) is None:  # a header the memo cannot read: refuse before the task closes, or the Journal never gets it
            print("abofles: receipt not read — nothing recorded, the task stays open. Use the receipt lines as the hook gave them: "
                  "the 🧾 header copied whole, then `- `done` → …` lines (short form: `· open: <items>` on the header line, "
                  "or `· open:` with one indented `  - <item>` sub-bullet per item); record it again")
            sys.exit(3)
        with state_lock(sid):  # the task closes now, as a printed receipt closes it at the turn's end: a next task in this turn counts apart
            stx = load_state(sid)
            root = H.project_root({"cwd": cwd}, stx)
            closed, rc, refusal = H.receipt_close(root, stx, sid, text, H.parse_receipt(text), own, foreign, False) if stx.get("started") else (False, None, None)
            stx["receipt_saved"] = {"text": text, "ts": time.time(), **({"closed": True} if closed else {})}
            save_state(sid, stx)
        wrote = None
        if closed:
            if rc:
                wrote = H.journal_keep(root, sid, rc)  # a busy or failed write is kept for the next Stop or spawn, as the hook's own path does
            with state_lock(sid):  # the receipt's open items just reached Needs you: the guide and the pane show them
                stx = load_state(sid)
                refresh_guide(root, stx, sid)
                save_state(sid, stx)
        if wrote is not None and not wrote:  # the task closed, but the Journal and Now never got the receipt: say so, never `recorded`
            print(f"abofles: receipt closed the task but the memo write failed — {wrote.reason}")
            sys.exit(CODE_EXIT.get(wrote.code, 1))
        if refusal:
            print(refusal)
            print("abofles: receipt kept — the task stays open until the reviewers ran; record the receipt again after them")
        else:
            print("abofles: receipt recorded — it shows in the Abo Fles pane and the memo" + (" now" if closed else " when the turn ends") + "; end the turn with one short sentence, do not print it")
    elif cmd == "now":
        text = sys.stdin.read()
        if not text.strip():
            print("usage: memo.py now < text")
            sys.exit(2)
        r = memo_apply(cwd, "now", sid, text=text)
        if not r:
            print("abofles: " + r.reason)
            sys.exit({"busy": 6, "write-failed": 7}.get(r.code, 4))
        print(session_memo_path(cwd, sid))  # where the Now section went
    elif cmd in ("help", "--help", "-h"):
        print(__doc__)
    else:
        print(__doc__)
        print(f"abofles: unknown command {clip(cmd, 40)!r} — see the usage above")
        sys.exit(2)


if __name__ == "__main__":
    main()
