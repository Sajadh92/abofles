# Agent details: background agents, fallback agents, re-verify

Read when starting an agent in the background, when a model is unavailable, when the abofles agents are missing, or after a verifier FAIL.

9. **Model not available** (the agent fails with a model-access or permission error): restart one step down at the next effort up — `fable` → `opus·xhigh`, `opus` → `sonnet·xhigh`, `sonnet` → `haiku·high`. Say so in the route line with the modifier ` · Fallback: Fable 5.1 Unavailable`. Never retry the same model.

10. **Background agents.** Pass `run_in_background: false` on abofles agents unless they are a parallel group. A background reply reaches the hooks only through `SubagentStop`, which fills the ledger silently (any hook output there would be fed back to the subagent and replace its reply); for a background agent **you** check the `STATUS`/`CONFIDENCE` header when the result arrives and apply Step 4 yourself. `/abofles:bill` lists the warnings the hook recorded for background agents.

**The cell picks the model, not the agent type.** `abofles-reader` has read-only Bash (`git show`, `gh pr view`, grep), so most reads fit it. A ticket read through a tracker's connector (Jira, ClickUp, Linear, Asana, monday.com) goes to `abofles-scrum` at the Read cell (`haiku·low` at T1–T2). Another read it cannot do — a project agent — still takes the Read cell: `general-purpose` at `haiku·low`, never sonnet because the agent type changed.

If the abofles agents are not available (skill copied without the plugin), fall back to `Explore` for Read, `Plan` for Plan, `general-purpose` for Analyse/Work/Verify, and paste the reply format from `agents/<side>.md` into the prompt. Always pass `model` and `effort`.

**After a verifier FAIL** (Step 4): a known fix of ≤ 3 tool calls is applied by the main session (rule 7). Then re-verify **only the failed criterion**: run its command in the main session, or start Verify at −1 effort with that one criterion. A full re-verify at the original setting is only for a fix that touched more than the failed criterion.
