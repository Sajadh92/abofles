---
description: "Optional: write a sharper Now section of the work memo (task, done, left, decisions, next, verify). /compact already saves the memo by itself"
allowed-tools: Bash(bash *), Bash(git *), Agent, Read, Write, Edit
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
Next      <the handoff: the first thing to do after the compact, one line a fresh session can act on without this transcript>
Verify    <the command that proves the current state, with its expected result>
Tree      <first 20 lines of git status --short, or "clean">
EOF
```

   The first `Next` line is the handoff: write it so a new session (or this one after `/compact`) can start from it alone — the step, the file, the check.

3. Update the auto-memory. If this session settled a durable fact — a rule, a preference, a decision or a project fact that holds beyond this task — that the project's auto-memory (the memory folder of this project that the session was given, its `MEMORY.md` index) does not hold yet, add it there: one short file per fact and one index line in `MEMORY.md`; change an existing entry instead of adding a second one when the fact changed. Nothing task-local (what is done or left now — that is the Now), no secrets. Nothing new: skip this step and say nothing about it.

4. Unless the project's `Memo agent` override is `off`, start the memo agent in the background in the same step (the same message as the `memo.py now` call): print the route line `🗂️ \`Memo\` → **Memo Upkeep** · Haiku 5.5 · Low · Cap 15 · 🟢 Minor`, then call Agent with `subagent_type` `abofles:abofles-memo`, `model` `haiku`, `effort` `low`, `run_in_background` true, `description` `Memo upkeep` and this prompt: `abofles: Memo Upkeep / Work · T1`, then `Session id: ${CLAUDE_SESSION_ID}. Memo command: bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" <subcommand> ... --session ${CLAUDE_SESSION_ID}`, then `Line cap: 15`. It needs no budget, no receipt and no answer; never wait for it.

5. Reply with two lines: the session file path (the script prints it), then: `Memo saved. Run /compact when ready — the resume block is shown again after it.` The user runs `/compact` themselves (a command cannot). When the user typed `/abofles:memo`, those two lines are the whole reply. When the context watch made you run it mid-turn, print the two lines and continue the turn.
