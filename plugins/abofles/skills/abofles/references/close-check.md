# Close check — before an archive

Read when the owner asks whether a session can be archived ("update the memo and memory so I can archive", "ready to archive?", "جاهز للأركايف؟"), or runs `/abofles:close`.

**The chat sentence is never the verdict.** The verdict is the one `memo.py` stores in the session state (`close`) and in the session file's `## Close` section, with a fingerprint. Say "ready to archive" (or "safe to archive", "جاهز للأركايف / للأرشفة / للأرشيف") only when that stored verdict is ✅ and not stale. The Stop hook refuses such a sentence once when no fresh ✅ verdict backs it; both archive paths refuse a session whose Close section holds a ⛔.

## The flow (`/abofles:close`)

1. **Now and memory first** — `memo.py now` and the auto-memory, as `/abofles:memo` writes them. The check reads both.
2. **`memo.py close-check`** — the script's evidence inventory: one line per finding from the guide's open items, this session's Needs-you items, git (work repositories, worktrees, sub sessions' branches; read-only, 2 s per call, never a fetch), live claims, connector actions, writes outside the work roots, Verify after the last edit, the auto-memory, and one `not checked` line per `- Close: <name> = <rule>` override line (the script never runs a rule). States: ⛔ blocker · ⚠ warning · ✓ checked · `not checked`. Verdict ✅ only when no line is `not checked`, no ⛔ or ⚠ is left unwaived and, under `Close agent = on`, the close agent's run for this check is recorded (`agent`: yes). The close agent's lines of the last check stay while what they judged is unchanged.
3. **The close agent** (`abofles:abofles-close`, Sonnet 5.5 · Medium, foreground, header `abofles: Close Check / Verify · T2`, route emoji ✅; the hook refuses it without that header, above `effort` `medium` or with no check stored in this turn) — skipped under `- Close agent = off` (the check's last line says so; the hook also refuses the agent; each `Close:` rule line is then ⚠ until the owner accepts it). It reads `memo.py close-digest` (the stored rules and open lines, the owner's asks, final replies, AskUserQuestion answers, tool names — clipped, never the raw tail), the memo, the auto-memory and git, read-only. It judges every `Close:` rule (✓, ⚠ or ⛔ with evidence; undecidable stays `not checked`) and every decision or standing rule said in chat that neither the memo Decisions nor the auto-memory holds (⚠ `said in chat, not saved: "<the owner's words>"`). It returns lines; it never writes and never asks. It takes no agent budget, owes no receipt, is no Judge gate row and no Loop guard count; a hook lets it run only read tools.
4. **`memo.py close-add`** — the main session records each agent line: `close-add <state> --rule "<name>" | --src <word> --text … --evidence … [--fix …]`, each field through a quoted heredoc, never raw chat text. Its `- none` is `close-add --none` (the run recorded, no line); a blocked, low-confidence or cut reply adds `close-add "not checked" --src agent` after its complete lines. `close-add` refuses (exit 5) when no close agent ran since the check. Append only: a script line is never edited or removed. A rule line the agent judged (⛔, ⚠ or ✓) counts through the agent's line in the verdict; the rule line itself stays. The fingerprint is counted again after the write: the agent's own ledger row keeps the verdict fresh; a changed tree, HEAD or turn makes it stale.
5. **Show the stored verdict** — the `## Close` section verbatim, then one line: `Verdict ✅ — stored and fresh`, or `not ready:` and what is open.

## Settling a line

- Fix the work, then run `/abofles:close` again (a new check replaces the stored lines; the owner's waives of unchanged lines are kept).
- The owner accepts a ⛔, ⚠ or `not checked` line in their own words: `memo.py close-waive <id> --trace "<their exact words>"` (`<id>` the whole id or a unique end of it; the trace must be words of one of the owner's messages in this session, else exit 5). It stays, shown as "accepted by you". A `Close:` rule line still `not checked` under `Close agent = on` cannot be accepted: the close agent judges it.
- Git work goes through the `abofles-git` agent with the owner's words for it.

## Overrides

- `Close check = ask | auto` — ask (default): only the command or the pane's button runs it; auto: the script also runs at every Stop (no agent).
- `Close git = pushed | committed | local` — pushed (default): a dirty tree, no upstream and unpushed commits are ⛔; committed: unpushed commits and no upstream become ⚠ "by rule"; local: the dirty tree too. The idle-session auto-archive reads it as well: the git work the rule lets stay keeps no session.
- `Close agent = on | off` — on (default) starts the close agent in step 3; off skips it.
- `Close PR check = on | off` — off (default); on: the check runs `gh pr list --head <branch> --state open` for each branch it looks at (read-only, 2 s cap). An open pull request is ⚠ `PR open: <url>`; gh missing, slow or failing is `not checked`.
- `Close place = pane | chat | off` — where the *Ready to archive?* card shows: the pane's Home (default), the chat only (step 5 prints it), or nowhere. Without the mod: chat.
- `Close: <name> = <rule>` — a project rule the close agent judges (`- Close: docs = README lists every new flag`).

## Sub sessions outside the app

With no desktop app record store on the machine, no sub session can exist: the line is ✓ `no sub sessions (no app records)`. When the store exists but cannot be read, it stays `not checked`.
