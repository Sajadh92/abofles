---
description: "Close check before an archive: write Now and memory, run the evidence inventory (memo.py close-check), let the close agent judge the Close: rules and the chat's decisions, record its lines, show the stored verdict"
allowed-tools: Bash(bash *), Bash(git *), Agent, Read, Write, Edit
---

Check whether this session can be archived. The verdict is the one the script stores, never a sentence of yours: call the session ready to archive only when the stored verdict is ✅ and not stale (the Stop hook refuses such a sentence once when no fresh ✅ backs it).

Append `--session "${CLAUDE_SESSION_ID}"` to every `memo.py` call below. `memo.py` is `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py"`. Run steps 2 to 4 in one turn: the hook lets the close agent judge only a check stored in this turn.

1. **Now and memory first.** Write this session's Now (`memo.py now`, the labels of `/abofles:memo` step 2) and update the auto-memory (`/abofles:memo` step 3). Both must be on disk before the check: the check reads them.

2. **The inventory.** Run `memo.py close-check --session "${CLAUDE_SESSION_ID}"`. It prints one line per finding (⛔ blocker · ⚠ warning · ✓ checked · `not checked`) with its id in `[…]`, and the Verdict line, and stores them in the session state and the session file's Close section.

3. **The close agent.** Skip steps 3 and 4 when the check's last line starts `Close agent = off` (the hook refuses the agent then; each `Close:` rule line is ⚠ until the owner accepts it). Else print the route line `✅ \`Close\` → **Close Check** · Sonnet 5.5 · Medium · Cap 30 · 🟡 Normal`, then call Agent with `subagent_type` `abofles:abofles-close`, `model` `sonnet`, `effort` `medium`, `run_in_background` false, `description` `Close check` and this prompt: `abofles: Close Check / Verify · T2`, then `Session id: ${CLAUDE_SESSION_ID}. Project: <the project folder>. Digest command: bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" close-digest --session ${CLAUDE_SESSION_ID}`, then `Line cap: 30`. The header is fixed: `Close Check` is the plugin's own topic (no index topic matches it: `topic.py find "close session archive check"` names only Security topics), its tier T2 is the route line's 🟡 Normal, and the hook refuses the agent without that header, on a model other than `sonnet` or an effort above `medium`, or with no check from this turn. It takes no agent budget and owes no receipt. It returns lines; it never writes.

4. **Record its reply.** Under `Close agent = on` the Verdict is ✅ only after its run is recorded, so record every reply, even an empty one. `close-add` works only after a close agent ran for the stored check (exit 5 otherwise), only appends (a script line is never edited or removed), and prints the new Verdict after each call; exit 3 means the same line is there already.
   - Pass each field through a quoted heredoc, never as raw chat text inside `--text "…"`: a quote, `$` or backtick in the agent's words must reach the script unchanged. One call per line, the fields copied exactly:

     ```
     bash "${CLAUDE_PLUGIN_ROOT}/scripts/py" "${CLAUDE_PLUGIN_ROOT}/scripts/memo.py" close-add "<state>" --rule "<name>" --text "$(cat <<'ABOFLES_FIELD'
     <text>
     ABOFLES_FIELD
     )" --evidence "$(cat <<'ABOFLES_FIELD'
     <evidence>
     ABOFLES_FIELD
     )" --fix "$(cat <<'ABOFLES_FIELD'
     <fix>
     ABOFLES_FIELD
     )" --session "${CLAUDE_SESSION_ID}"
     ```

     A rule line (`rule:<name>`) takes `--rule "<name>"`, quoted; any other line takes `--src <src>` instead. Leave `--fix` out when the line has none. `<state>` is `⛔`, `⚠`, `✓` or `not checked`.
   - `LINES:` then `- none`: run `memo.py close-add --none --session "${CLAUDE_SESSION_ID}"` (its run recorded, no line).
   - `STATUS: blocked`, `CONFIDENCE: low`, or a cut reply (no `NOTE:` line at its end, a line that breaks off): record the complete lines it returned, then one more line, `close-add "not checked" --src agent --text "close agent: <blocked | low confidence | reply cut>" --evidence "<its STATUS / CONFIDENCE line, or its last line>"`, fields through the heredoc as above. The verdict stays short of ✅ until a new `/abofles:close` judges again or the owner accepts that line.

5. **Show the stored verdict.** Print the `## Close` section of this session's file (`memo.py path mine` names it) verbatim, then one line: `Verdict ✅ — stored and fresh` only when its Verdict is ✅ and the last call did not say stale; else `not ready: <the ⛔, ⚠ and not checked lines in a few words>`. A stale verdict means the work changed since the check: run this command again.

The owner accepts a ⛔, ⚠ or `not checked` line only in their own words: `memo.py close-waive "<id>" --trace "<their exact words>" --session "${CLAUDE_SESSION_ID}"`. `<id>` is the line's id from `[…]`, or a unique end of it. The trace must be words of one of the owner's messages in this session, two words or more (exit 5 otherwise); a `Close:` rule line still `not checked` cannot be accepted while `Close agent = on`: the close agent judges it. Never waive a line yourself. Git fixes go through the `abofles-git` agent with the owner's words, never a raw git command from here.
