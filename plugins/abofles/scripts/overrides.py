#!/usr/bin/env python3
"""Abo Fles overrides: read and write the `## abofles overrides` block (the Abo Fles pane's editor runs this).

    overrides.py get --json [--session S] [--cwd D]
        prints the pane's overrides payload: {files, keys: [{key, kind, options, value, source, default}], raw: [{line, source, path}]}
    overrides.py set --scope project|user [--session S] [--cwd D]      stdin: {"set": {"Key": "value" | ""}, "raw": ["Topic: X = T3", …],
                                                                               "close": {"<name>": "<rule>" | ""}}
        edits the block in place (a new key is appended, "" removes the key, in every project file for project scope; a trailing `# comment`
        on the key's line stays), `raw` replaces the
        block's lines that are no simple key (the complex forms; a typo or note left out of it is removed), `close` edits the
        `- Close: <name> = <rule>` lines by name (the name's case and spacing ignored): a new name is appended, a known one gets the new rule in
        place (project scope: in the project file where it lives, a copy in the other one removed), "" removes it (in every project file for project scope); everything outside the block and the simple keys not in `set` stay
        byte for byte. Prints the new `get` JSON.

Project scope = the project's CLAUDE.md or .claude/CLAUDE.md, whichever already holds the block (else CLAUDE.md); user scope =
~/.claude/CLAUDE.md. Bad input: one line on stderr, exit 2. Acts on the session you type in (see agents.py).
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import abofles_hook as H  # noqa: E402

def _KEY_LINE(key):
    """`- Key = value` (also `*` / `+` bullets and the key's old name): groups bullet, name, ` = `, value, trailing space."""
    names = "|".join(re.escape(n) for n in (key, H.OLD_KEY_NAMES.get(key)) if n)
    return re.compile(r"^(\s*(?:-\s*|[*+]\s+))(" + names + r")(\s*=\s*)(.+?)(\s*)$", re.I)
_OFF_KEYS = ("Break", "Loop guard", "Context stop", "Sub sessions", "Context marks")  # keys that also take off: every OFF_WORDS word is written `off` (Sub sessions = 0 is its own value: no sub session)
MAX_VALUE = 500  # characters per value or line: an override is one short line
BOM = b"\xef\xbb\xbf"


class Bad(Exception):
    """Input the writer refuses: one line, exit 2."""


def clean_value(key, spec, value):
    """The value as it is written, or Bad. Select: an option (Agents also a number, Receipt also `savings N`); number: an integer (Context stop:
    context_stop_value); list: integers (comma-separated). A key of _OFF_KEYS writes every OFF_WORDS word as `off`, as the hooks read it."""
    v = " ".join(str(value).split())
    if "\n" in str(value).strip() or "\r" in str(value).strip():
        raise Bad(f"{key}: one line only")
    if len(v) > MAX_VALUE:
        raise Bad(f"{key}: longer than {MAX_VALUE} characters")
    kind, opts = spec["kind"], spec["options"]
    if kind == "select":
        low = v.lower()
        if low in opts:
            return low
        if key == "Agents" and re.fullmatch(r"\d+", v):
            return str(int(v))
        if key == "Receipt" and re.fullmatch(r"savings\s+\d+", low):
            return "savings " + str(int(low.split()[1]))
        raise Bad(f"{key}: '{v}' is not one of {', '.join(opts)}" + (" or a number N" if key == "Agents" else " or 'savings N'" if key == "Receipt" else ""))
    zero = re.fullmatch(r"0+", v) is not None
    if key in _OFF_KEYS and (v.lower() in H.OFF_WORDS or zero) and not (key == "Sub sessions" and zero):
        return "off"
    if key == "Context stop":
        return context_stop_value(v)
    if kind == "number":
        if re.fullmatch(r"\d+", v):
            return str(int(v))
        raise Bad(f"{key}: '{v}' is not a whole number" + (" or off" if key in _OFF_KEYS else ""))
    if kind == "list":
        parts = [p.strip() for p in v.split(",")]
        if not all(re.fullmatch(r"\d+", p) for p in parts) or (key == "Agent budgets" and len(parts) != 4):
            raise Bad(f"{key}: '{v}' is not " + ("four whole numbers a, b, c, d" if key == "Agent budgets" else "whole numbers, comma-separated" + (" or off" if key in _OFF_KEYS else "")))
        return ", ".join(str(int(p)) for p in parts)
    if not v:
        raise Bad(f"{key}: empty value (use \"\" to remove the key)")
    if key in ("Main session", "Sub session") and not H.pick_of(v):
        raise Bad(f"{key}: '{v}' is not a model and effort, e.g. opus.medium")
    return v


def _num(x):
    """1.50 -> `1.5`, 650.0 -> `650`: no exponent, no trailing zeros."""
    return f"{x:.6f}".rstrip("0").rstrip(".")


def context_stop_value(v):
    """Context stop as written: `N` / `NK` are K tokens (`650` -> `650K`), `NM` and decimals with K or M are kept (`1.5m` -> `1.5M`), a bare
    whole number of 10000 or more is tokens (`600000` -> `600K`); else Bad."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", v)
    if not m or (not m.group(2) and "." in m.group(1)):
        raise Bad(f"Context stop: '{v}' is not N, NK, NM (e.g. 650K, 1.5M) or off")
    n, unit = float(m.group(1)), m.group(2).upper()
    if n == 0:
        return "off"
    if not unit:
        return f"{_num(n / 1000)}K" if n >= 10000 else f"{int(n)}K"
    return f"{_num(n)}{unit}"


def bullet(line):
    m = re.match(r"^\s*(?:-\s*|[*+]\s+)(.+?)\s*$", line.rstrip("\r\n"))
    return m.group(1) if m else None


def is_complex(content):
    return any(r.match(content) for r in H.COMPLEX_FORMS)


CLOSE_LINE = re.compile(r"^(\s*(?:-\s*|[*+]\s+))Close:\s*([^=]+?)\s*=\s*(.+?)(\s*)$", re.I)  # `- Close: <name> = <rule>`: bullet, name, rule, trailing space


def close_name(name):
    """A Close rule's name as it is matched: spaces folded, case ignored."""
    return " ".join(str(name).split()).lower()


def same(a, b):
    """Two raw lines are one line whatever their spacing: the pane sends them with the spaces folded."""
    return " ".join(a.split()) == " ".join(b.split())


def edit_text(txt, sets, raw, close=None):
    """The new file text: `sets` {key: value | ""} edited in place, appended (first block) or removed; `raw` (None: untouched) replaces the
    lines of the blocks the pane lists as raw (every bullet that is no simple key: the complex forms, typos, notes) — a line already there and
    still wanted stays where it is, one missing from `raw` is removed; `close` {name: rule | ""} (after `raw`) edits the `Close:` rule of that
    name in place (the first line of the name; a second one of the same name is removed), appends a new name, or removes every line of the
    name. No block yet and something to add: a new one."""
    nl = "\r\n" if "\r\n" in txt else "\n"
    lines = txt.splitlines(True)
    spans = lambda: H.override_spans([l.rstrip("\r\n") for l in lines])
    inside = lambda: H.override_indexes([l.rstrip("\r\n") for l in lines])  # not a line in a code fence or an HTML comment
    adds = []
    for key, val in sets.items():
        rx = _KEY_LINE(key)
        hits = [i for i in inside() if rx.match(lines[i].rstrip("\r\n"))]
        if val == "":
            for i in reversed(hits):
                del lines[i]
        elif hits:  # the first line wins when read; a line under the key's old name gets the new name
            body = lines[hits[0]].rstrip("\r\n")
            m = rx.match(body)
            note = re.search(r"\s+#.*$", m.group(4))  # a trailing `# comment` (the hooks read the first word) stays on the line
            lines[hits[0]] = m.group(1) + key + m.group(3) + val + (note.group(0) if note else "") + lines[hits[0]][len(body):]
        else:
            adds.append(f"- {key} = {val}")
    if raw is not None:
        old = [(i, bullet(lines[i])) for i in inside() if bullet(lines[i]) is not None and H.simple_key(bullet(lines[i])) is None]
        want, drop = list(raw), []
        for i, c in old:
            hit = next((w for w in want if same(w, c)), None)
            if hit is not None:
                want.remove(hit)
            else:
                drop.append(i)
        if old and want:  # next to the complex lines already there
            at = max(i for i, _ in old) + 1
            for i in reversed(drop):
                del lines[i]
            at -= len(drop)
            if not lines[at - 1].endswith(("\n", "\r")):
                lines[at - 1] += nl
            lines[at:at] = [f"- {c}{nl}" for c in want]
        else:
            for i in reversed(drop):
                del lines[i]
            adds = [f"- {c}" for c in want] + adds
    for name, rule in (close or {}).items():
        hits = [i for i in inside() if (m := CLOSE_LINE.match(lines[i].rstrip("\r\n"))) and close_name(m.group(2)) == close_name(name)]
        keep = hits[:1] if rule != "" else []
        if keep:
            body = lines[keep[0]].rstrip("\r\n")
            m = CLOSE_LINE.match(body)
            lines[keep[0]] = f"{m.group(1)}Close: {m.group(2)} = {rule}" + lines[keep[0]][len(body):]
        for i in reversed(hits[len(keep):]):
            del lines[i]
        if rule != "" and not hits:
            adds.append(f"- Close: {name} = {rule}")
    if adds:
        sp = spans()
        if sp:
            a, b = sp[0]
            mine = [i for i in inside() if a <= i < b]
            last = max([i for i in mine if bullet(lines[i]) is not None] or [i for i in range(a, b) if lines[i].strip()] or [a - 1])  # after the last bullet (not one in a code fence), else the last text
            if last >= 0 and not lines[last].endswith(("\n", "\r")):
                lines[last] += nl
            lines[last + 1:last + 1] = [x + nl for x in adds]
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += nl
            lines += ([nl] if lines else []) + ["## abofles overrides" + nl, nl] + [x + nl for x in adds]
    return "".join(lines)


class Changed(Exception):
    """The target changed between our read and our write: nothing is written, exit 3."""


def write_atomic(path, data, expect=None, inside=None):
    """Write `data` over `path` via a temp file. `expect` = the bytes read before editing (None = the file was missing); if the target
    holds anything else right before the replace, raise Changed instead. `inside`: a folder the real file (symlinks followed) must be in,
    else Bad — a project's CLAUDE.md that links out of the project is not written. The temp file is a fresh one (mkstemp: never a name a
    repo could plant a link at) in the target's folder, so the replace stays on one disk."""
    real = Path(os.path.realpath(path))
    if inside is not None:
        base = Path(os.path.realpath(inside))
        if real != base and base not in real.parents:
            raise Bad(f"{path.name} points outside the project ({real}) — edit it by hand")
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.", suffix=".abofles-tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            now = real.read_bytes()
        except FileNotFoundError:
            now = None
        if now != expect:
            raise Changed()
        if real.exists():
            shutil.copymode(real, tmp)
        else:  # mkstemp makes 0600: a new CLAUDE.md gets the usual mode
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(tmp, 0o666 & ~mask)
        os.replace(tmp, real)
    finally:
        tmp.unlink(missing_ok=True)


def read_request():
    try:
        req = json.loads(sys.stdin.read())
    except ValueError:
        raise Bad("stdin is not JSON")
    if (not isinstance(req, dict) or not isinstance(req.get("set", {}), dict) or not (req.get("raw") is None or isinstance(req["raw"], list))
            or not isinstance(req.get("close") or {}, dict)):
        raise Bad('stdin must be {"set": {Key: value}, "raw": [lines], "close": {name: rule}}')
    sets = {}
    for k, v in (req.get("set") or {}).items():
        key = next((n for n in H.OVERRIDE_KEYS if n.lower() == str(k).strip().lower()), None)
        if key is None:
            raise Bad(f"unknown key '{k}'")
        if not isinstance(v, (str, int)):
            raise Bad(f"{key}: value must be text")
        sets[key] = "" if str(v).strip() == "" else clean_value(key, H.OVERRIDE_KEYS[key], v)
    raw = None
    if req.get("raw") is not None:
        raw = []
        for l in req["raw"]:
            c = re.sub(r"^\s*(?:-\s*|[*+]\s+)", "", " ".join(str(l).split()))
            if not c:
                continue
            if c.endswith("…"):
                raise Bad(f"'{c[:40]}…' was clipped in the pane; edit that line in the file")
            if H.simple_key(c) is not None:
                raise Bad(f"'{c[:40]}' is a simple key; use its row")
            raw.append(c)
    return sets, raw, clean_close(req.get("close") or {})


def clean_close(close):
    """{name: rule | ""} of the request's `close`, each name and rule one line with its spaces folded (a leading `Close:` on the name is
    dropped), or Bad: an empty name, a name with `=`, a rule that is not text, a value past MAX_VALUE."""
    out = {}
    for k, v in close.items():
        name = re.sub(r"^\s*close:\s*", "", " ".join(str(k).split()), flags=re.I)
        if not name or "=" in name:
            raise Bad(f"Close rule: '{name[:40]}' is no rule name" + (" (no `=` in a name)" if name else ""))
        if not isinstance(v, (str, int)):
            raise Bad(f"Close: {name}: rule must be text")
        if "\n" in str(v).strip() or "\r" in str(v).strip():
            raise Bad(f"Close: {name}: one line only")
        rule = " ".join(str(v).split())
        if len(name) + len(rule) > MAX_VALUE:
            raise Bad(f"Close: {name}: longer than {MAX_VALUE} characters")
        if any(close_name(n) == close_name(name) for n in out):
            raise Bad(f"Close: {name}: named twice")
        out[name] = rule
    return out


def opt(args, name):
    """Take `name value` out of `args`; the value, or None."""
    if name in args and args.index(name) + 1 < len(args):
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return None


def session_of(args):
    """(cwd, session id, its state, the project root) for the command: --cwd, --session (ABOFLES_SESSION first), else the newest session of this folder."""
    cwd = opt(args, "--cwd") or os.getcwd()
    if args[-2:] == ["--session", ""]:  # a command whose ${CLAUDE_SESSION_ID} expanded to nothing
        del args[-2:]
    sp = H.newest_state_path(cwd, H.session_arg(args))
    sid = sp.stem if sp else ""
    st = H.load_state(sid) if sid else {}
    return cwd, sid, st, H.project_root({"cwd": cwd}, st)


def raw_lines(root):
    """[(line, source, path)] of the lines the pane lists as raw (as pane_overrides does), each with the file it is in."""
    return [(m.group(1), src, str(path)) for src, path, txt in H.override_blocks(root) for l in txt.splitlines()
            for m in [H._OVERRIDE_LINE.match(l)] if m and H.simple_key(m.group(1)) is None]


def payload(root, sid):
    """pane_overrides, each raw line with its file (`path`): the editor sends a file only its own lines."""
    p = H.pane_overrides(root, sid)
    p["raw"] = [{"line": l, "source": s, "path": f} for l, s, f in raw_lines(root)]
    return p


def close_home(close, path, files):
    """{file: {name: rule | ""}}: each `close` edit in the project file where the rule lives — the target `path` when it holds the name (or
    no file does: a new rule is appended there), else the first project file that holds it — and "" (drop) in every other project file that
    holds the name, so an edit never leaves a second copy with the old rule. A removal ("") goes to every file."""
    if not close:
        return {path: {}}
    texts = {f: read_text(f)[1] for f in files}
    holds = lambda f, name: any((m := CLOSE_LINE.match(l)) and close_name(m.group(2)) == close_name(name)
                                for ls in [texts[f].splitlines()] for i in H.override_indexes(ls) for l in [ls[i]])
    out = {f: {} for f in [path] + [f for f in files if f != path]}
    for name, rule in close.items():
        have = [f for f in files if holds(f, name)]
        home = path if rule == "" or path in have or not have else have[0]
        out[home][name] = rule
        for f in files:
            if f != home and (rule == "" or f in have):
                out[f][name] = ""
    return out


def project_files(root):
    """The project files override_blocks reads, in its order: a reset must reach the key in each, or the next one still sets it."""
    return [Path(path) for src, path, _ in H.override_blocks(root) if src == "project"]


def read_text(path):
    """(the bytes, None when missing; the text, a BOM left out) or Bad."""
    try:
        b = path.read_bytes()
    except FileNotFoundError:
        b = None
    try:
        return b, (b or b"").decode("utf-8-sig")  # a BOM is kept on the write
    except UnicodeDecodeError:
        raise Bad(f"{path.name} is not UTF-8 text — edit it by hand")


def main():
    args = sys.argv[1:]
    try:
        cwd, sid, st, root = session_of(args)  # --session / --cwd first or last: the pane runs `overrides.py --session S set --scope …`
        cmd = args.pop(0) if args else ""
        if cmd == "get":
            print(json.dumps(payload(root, sid)))
            return 0
        scope = opt(args, "--scope")
        if cmd != "set" or scope not in ("project", "user"):
            raise Bad("usage: overrides.py get --json | set --scope project|user  (stdin JSON {set, raw})")
        sets, raw, close = read_request()
        path = H.override_target(scope, root)
        old_b, old = read_text(path)
        if raw is not None:  # a line that is no complex form must already be in the block (an unknown key the pane lists): it stays where it is
            lines = old.splitlines()
            mine = [bullet(lines[i]) for i in H.override_indexes(lines) if bullet(lines[i]) is not None and H.simple_key(bullet(lines[i])) is None]
            elsewhere = [l for l, s, f in raw_lines(root) if s == scope and Path(f) != path]  # the other project file's lines: an older pane sends them too
            raw = [c for c in raw if any(same(c, m) for m in mine) or not any(same(c, e) for e in elsewhere)]
            for c in raw:
                if len(c) > MAX_VALUE:
                    raise Bad(f"'{c[:40]}…' is longer than {MAX_VALUE} characters")
                if not is_complex(c) and not any(same(c, m) for m in mine):
                    raise Bad(f"'{c[:40]}' is not an override form")
        writes = [(path, sets, raw, close)]
        if scope == "project":  # a reset takes the key out of every project file the hooks read; so does a removed Close rule (every file's rules count)
            resets = {k: "" for k, v in sets.items() if v == ""}
            homes = close_home(close, path, project_files(root))  # a rule edit lands in the file where it lives
            writes = [(path, sets, raw, homes[path])]
            writes += [(f, resets, None, homes.get(f) or {}) for f in project_files(root) if f != path and (resets or homes.get(f))]
        edits = []  # every file read and edited before the first write: a file that cannot be read stops the save before anything is written
        for f, sets_f, raw_f, close_f in writes:
            b, t = (old_b, old) if f == path else read_text(f)
            new = edit_text(t, sets_f, raw_f, close_f)
            if new != t:
                edits.append((f, (BOM if (b or b"").startswith(BOM) else b"") + new.encode("utf-8"), b))
        for f, data, b in edits:
            write_atomic(f, data, b, inside=root if scope == "project" else None)
        if sid:
            with H.state_lock(sid):
                st = H.load_state(sid)
                try:
                    H.refresh_guide(root, st, sid)  # the guide (Guide = off) and the pane follow
                except Exception:
                    H.refresh_pane(root, st, sid)
                if isinstance(st.get("pane"), dict) and "overrides" in st["pane"]:
                    st["pane"]["overrides"] = payload(root, sid)  # the raw lines with their files, as printed below
                H.save_state(sid, st)
        print(json.dumps(payload(root, sid)))
        return 0
    except Bad as e:
        print(f"abofles overrides: {e}", file=sys.stderr)
        return 2
    except Changed:
        print("abofles: CLAUDE.md changed while saving — reload the pane and save again", file=sys.stderr)
        return 3
    except OSError as e:
        print(f"abofles overrides: cannot write — {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
