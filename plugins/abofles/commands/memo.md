---
description: "Optional: write a sharper Now section of the work memo (task, done, left, decisions, next, verify). /compact already saves the memo by itself"
allowed-tools: Bash(bash *), Bash(git *), Agent
---

Write a sharper **Now** section for this session. It is optional: every `/compact` and auto-compact already saves the memo by itself (`Memo on compact = auto`; with `off`, run this first).

The memo is two files in the project root — the directory the session started in, whatever `cd` happened since, until most of the session's edits land in another repository (the hooks move the memo there and say so in the Journal): the main memo `.claude/abofles-memo.md` (**Needs you**, **Decisions** = notes settled in chat, **Sessions** = one index line per session with a link) and this session's file `.claude/abofles-memo.d/<session>.md` (**Now**, **Journal** = one line per receipt, **Decisions** = the questions this session asked with their answers). `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" path mine --session "${CLAUDE_SESSION_ID}"` prints the session file. The hook stamps Now with the time it was written; the resume block shows that age. The hooks also rewrite Now from each receipt (stamped `· from receipt`) and from each compact summary (stamped `· from compact`, unless a Now written here is newer than the last receipt); this command writes the richer version. Everything else is kept by the hooks; never rewrite it.

1. Run `git status --short` first if the project is a git repository (first 20 lines go under `Tree`).

2. Write the Now section in one command (it lands in this session's file; the main memo's index line follows it) — short lines, repo-relative paths, no transcript quotes, exactly these labels padded with spaces:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" now --session "${CLAUDE_SESSION_ID}" <<'EOF'
Task      <the current task in one line, or "none — between tasks">
Done      <what is finished, one line per item, files named>
Left      <what is not finished, one line per item — repeat the `Left` label on every line>
Decisions <decisions the user made this session that must not be reopened, one per line>
Next      <the first thing to do after the compact, one line>
Verify    <the command that proves the current state, with its expected result>
Tree      <first 20 lines of git status --short, or "clean">
EOF
```

3. Unless the project's `Memo agent` override is `off`, start the memo agent in the background in the same step (the same message as the `memo.py now` call): print the route line `🗂️ \`Memo\` → **Memo Upkeep** · Haiku 5.5 · Low · Cap 15 · 🟢 Minor`, then call Agent with `subagent_type` `abofles:abofles-memo`, `model` `haiku`, `effort` `low`, `run_in_background` true, `description` `Memo upkeep` and this prompt: `abofles: Memo Upkeep / Work · T1`, then `Session id: ${CLAUDE_SESSION_ID}. Memo command: bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" <subcommand> ... --session ${CLAUDE_SESSION_ID}`, then `Line cap: 15`. It needs no budget, no receipt and no answer; never wait for it.

4. Reply with two lines: the session file path (the script prints it), then: `Memo saved. Run /compact when ready — the resume block is shown again after it.` The user runs `/compact` themselves (a command cannot). When the user typed `/abofles:memo`, those two lines are the whole reply. When the context watch made you run it mid-turn, print the two lines and continue the turn.
