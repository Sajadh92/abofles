---
description: "Session summary from the ledger: agents per model as bars, confidence, and the open follow-ups (partial, blocked, low, ATTENTION, FAIL); --resolve <id> --trace <evidence> closes one"
argument-hint: "[--all] [--resolve <agent id> --trace <evidence>] [--unresolve <agent id>] [--session <sid>] [--trace <text>]"
allowed-tools: Bash(bash *)
---

Run the status script and show its output to the user unchanged, inside a code block:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" $ARGUMENTS --baseline <your current main-session model> --session "${CLAUDE_SESSION_ID}"
```

Use the alias of the model you are running on (`haiku`, `sonnet`, `opus`, `fable`). The script reads the session's effort from the state the hooks keep; add `·<effort>` only when the user stated a different one.

After the output: if there are open follow-ups, add one line per follow-up saying what you will do about it (handle it now, ask the user, or why it can stay open). When you have handled one, run `/abofles:status --resolve <id> --trace "re-checked: <evidence>"` so it leaves the list (the memo Journal keeps a "resolved by hand" line); `--unresolve <id>` reopens one. If there are none, add nothing.

`<id>` is the whole agent id or a prefix of 6 characters or more (the list shows 8), and it names one agent: a follow-up row of the list (`--unresolve`: a resolved one), never a done/high row.

Exit codes: 0 changed · 1 no match, no ledger or a busy ledger · 2 usage (missing or empty value, `--resolve` without `--trace` evidence, a prefix under 6 characters, a prefix of several agents) · 3 nothing to change (already resolved / not resolved / no open follow-up; no trace line is written). `--session <sid>` picks a session other than this one (an empty trailing `--session ""` is ignored); the display and `--resolve` use the same ledger, only this project's; `--trace <text>` is required with `--resolve` (nothing is settled on a bare word) and optional with `--unresolve`.
