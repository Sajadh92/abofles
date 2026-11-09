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
                                 the memo and the Abo Fles pane get it, the chat does not; exit 2 when stdin is no receipt
    memo.py path                 print the main memo path (`path mine`: this session's file)
    memo.py move <root>          move this session's memo (its file, its Needs-you items, its index line) to project <root> (a repository top)
                                 and make <root> the session's project — for a session that was started in the wrong folder
      a write (resolve, reopen, note, now) with no session — no --session and no session state for this folder — exits 4 and writes nothing

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
    memo.py archive-session <sid>              a closed session's file moves to abofles-memo.d/archive/; refused while it has an open item of its own, and for the calling session
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
import json
import os
import re
import sys
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
                 "drop-decision": "memo.py drop-decision <ref> --reason \"<why>\" --session <sid>", "archive-session": "memo.py archive-session <sid> --session <sid>", "rewrite-now": "memo.py rewrite-now <sid> --session <sid> < text"}


def take_opt(rest, name):
    """Take `name <value>` out of `rest` (edited in place); the value, "" when it has none, None when `name` is not there."""
    if name not in rest:
        return None
    i = rest.index(name)
    v = rest[i + 1] if i + 1 < len(rest) and not rest[i + 1].startswith("--") else ""
    del rest[i:i + (2 if v else 1)]
    return v


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
        pos = [a for a in rest if not a.startswith("--")]
        if len(pos) != 1:
            usage()
        r = memo_apply(cwd, "archive_session", sid, curator=True, target=pos[0])
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


def main():
    args = sys.argv[1:]
    if args[-2:] == ["--session", ""]:  # a command whose ${CLAUDE_SESSION_ID} expanded to nothing
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
    if cmd in ("now", "note") + CURATOR_CMDS and not sid:
        no_session()
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
        memo_move(cwd, dest, sid, "moved by hand (memo.py move)")
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
        if a_sid is not None:
            sid = re.sub(r"[^A-Za-z0-9_-]", "", a_sid) or "unknown"
        if not sid:
            no_session()
        if not undo and not " ".join(rest[1:]).strip():  # nothing is settled on a bare word: the note is the evidence (a question's answer settles its items in the hook, without this command)
            print(NO_EVIDENCE)
            sys.exit(2)
        r = memo_apply(cwd, cmd, sid, ref=n, force="--force" in args, **({} if undo else {"note": " ".join(rest[1:])}))
        if not r:
            print("abofles: " + r.reason)
            sys.exit({"empty-sid": 4, "other-session": 5, "already": 3}.get(r.code, 1))
        hit = r.value
        print(f"{'reopened' if undo else 'resolved'}: {hit}")
        item = NEED_TAIL_RE.sub("", re.sub(r"^\d+\. \[.\] \S+ \S+ · (owner decision:\s*)?", "", hit.split(" → resolved")[0]))  # the hook's own tag pattern
        note = "" if undo else " ".join(rest[1:]).strip()
        if "--trace" in args:
            memo_trace(cwd, sid, f"{'reopened' if undo else 'resolved'} by hand: owner decision {n}: " + (clip(item, 70) + f" · {note}" if note else item))  # the note is the evidence: it must survive the Journal clip
        if undo:
            last_resolved_note(cwd, sid)  # a reopen ends the band's Undo
        elif sid:
            last_resolved_note(cwd, sid, f"owner decision {n}: {item}", script_cmd("memo.py", "reopen", n, "--trace", "--session", sid))
    elif cmd == "note":
        text = " ".join(args[1:]).strip()
        if not text:
            print("usage: memo.py note <text>")
            return
        r = memo_apply(cwd, "decision", sid, question="note", answer=text)
        if not r:
            print("abofles: " + r.reason)
            sys.exit(4)
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
        with state_lock(sid):  # the task closes now, as a printed receipt closes it at the turn's end: a next task in this turn counts apart
            stx = load_state(sid)
            root = H.project_root({"cwd": cwd}, stx)
            closed, rc, refusal = H.receipt_close(root, stx, sid, text, H.parse_receipt(text), own, foreign, False) if stx.get("started") else (False, None, None)
            stx["receipt_saved"] = {"text": text, "ts": time.time(), **({"closed": True} if closed else {})}
            save_state(sid, stx)
        if closed:
            if rc:
                H.memo_journal(root, sid, rc)
            with state_lock(sid):  # the receipt's open items just reached Needs you: the guide and the pane show them
                stx = load_state(sid)
                refresh_guide(root, stx, sid)
                save_state(sid, stx)
        if refusal:
            print(refusal)
            print("abofles: receipt kept — the task stays open until the reviewers ran; record the receipt again after them")
        else:
            print("abofles: receipt recorded — it shows in the Abo Fles pane and the memo" + (" now" if closed else " when the turn ends") + "; end the turn with one short sentence, do not print it")
    elif cmd == "now":
        text = sys.stdin.read()
        if not text.strip():
            print("usage: memo.py now < text")
            return
        r = memo_apply(cwd, "now", sid, text=text)
        if not r:
            print("abofles: " + r.reason)
            sys.exit(4)
        print(session_memo_path(cwd, sid))  # where the Now section went
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
