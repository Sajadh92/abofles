export type GuideItem = {
  id?: string // stable per underlying thing (fail:<agent>, dec:<n>, ctx:<mark>, …); absent in older state files: text stands in
  p: string // the priority emoji (🔴🟠🟡⏳🧠💰✅)
  text: string
  fill: string // what Use puts in the prompt box and Copy copies
  recheck?: string | null // 🔴🟠🟡 items: the prompt Re-check puts in the prompt box (Claude verifies, then resolves)
  // The hook still writes `resolve: null` and `unresolve: null` on every item; the band never reads them.
}

export type Guide = {
  line: string
  ts: number
  root: string
  items: GuideItem[]
  ctx: { tokens: number; mark: number | null; auto?: boolean } | null // mark in K tokens; null: past the last mark; auto: a compact saves the memo by itself
  ctx_wait?: { marks: number[]; auto: boolean } | null // a compact ran and the hooks have no size yet: every mark in K tokens, for the mod's own figure; absent in older state files
  task: { spawns: number; usd: number; budget: number | null; left: number | null; raised?: number } | null // raised: automatic agent-budget raises this task
  all?: { n: number; fill: string } | null // 2+ items open: what Check all puts in the prompt box; absent in older state files
  main: string | null // "suggest Opus 5.5 · Medium (now Fable 5.1 · Medium)"; null when the main session fits
  // Claude's last resolve, kept by the hook until the Stop of the turn after it; null when nothing. Undo runs `unresolve` (cwd = root).
  last_resolved: { text: string; unresolve: string[]; ts: number } | null
}

// ---- the Abo Fles pane: `pane` in the session's state file, written by the abofles hooks (v 1). Every part is optional to the mod:
// cleanPane() drops what is malformed and keeps the rest; a state file from older hooks has no `pane` at all.

export type PanePlaces = {
  guide: 'pane' | 'band' // where the guide shows; old hooks: the band
  resume: 'pane' | 'output' // where /abofles:resume shows; old hooks: the output
  spawns: 'pane' | 'output' // where /abofles:bill shows; old hooks: the output
  running: 'pane' | 'off' // the Running page and Home's Running card; old hooks: the pane
}

export type PaneResume = {
  head: string
  now: string[]
  left: string[]
  next: string[]
  needs: { n: number | null; text: string; other?: boolean }[] // the open decisions, numbered as the memo numbers them; other: another session's or project's (Other items = show), information only
  needs_open: number
  needs_fill?: string | null // 2+ Needs-you items open: the combined text Check all puts in the prompt box
  others?: { sessions: number; projects: number } | null // Other items = hide: how many items of other sessions and projects were left out of needs
  others_items?: { n: number | null; text: string }[] // those items, drawn only after Show others
  last: string[]
}

export type PaneHealth = { archived_items: number; archived_sessions: number; trimmed: number; deduped: number; line: string }

export type PaneDecision = { ts: string; q: string; a: string; sid: string; other?: boolean } // ts: "MM-DD HH:MM"; a judge q may start with "judge: "; other: of another session or project

export type PaneDecisions = { you: PaneDecision[]; judge: PaneDecision[]; n_you: number; n_judge: number; others?: { sessions: number; projects: number } | null; others_rows?: PaneDecision[] } // newest first, at most 50 per group; others: left out (Other items = hide), others_rows: drawn only after Show others

export type PaneLive = { side: string; model: string; effort: string; description: string; ts: number | null } // ts: epoch seconds

export type PaneRow = {
  ts: number | null
  side: string
  model: string
  effort: string
  topic: string
  tier: string | null
  status: string | null
  confidence: string | null
  tokens: number | null
  usd: number | null
  base_usd: number | null
  description: string
  verdict: string | null
  task: string | null // the first line of the spawn's prompt
  resolved: boolean // a FAIL closed by hand with evidence (status.py --resolve)
}

export type PaneModel = { model: string; label: string; spawns: number; tokens: number; usd: number; pct: number } // pct: share of the spend

export type PaneTotals = { spawns: number; tokens: number; usd: number; base_usd: number; saved_pct: number; open_fail: number; base_label: string; by_model: PaneModel[] }

export type PaneSpawns = { live: PaneLive[]; rows: PaneRow[]; totals: PaneTotals } // rows: the last 30, oldest first

export type PaneOverrideKey = { key: string; kind: string; options: string[]; value: string; source: string; default: string; project: string | null; user: string | null } // project, user: that file's own value, null when it sets none

export type PaneOverrides = {
  files: { project: string; user: string }
  keys: PaneOverrideKey[]
  raw: { line: string; source: string }[] // override lines the table cannot show
}

export type Pane = {
  v: 1
  ts: number
  root: string
  sid: string
  places: PanePlaces
  resume: PaneResume | null // null: no memo yet
  spawns: PaneSpawns
  overrides: PaneOverrides | null
  decisions?: PaneDecisions | null // absent in older state files: no Decisions card
  memo_health?: PaneHealth | null // what the memo's last tidy-up moved; absent in older state files
  cmd: { overrides: string[] | null } // argv prefix of the script the overrides editor runs
}

// ---- what runs now, kept by the mod from the engine's events (running.tsx); no hook and no model call feeds it.
export type WorkLoop = { startedAt: number; now: string | null; uses: number; tokens: number; model: string | null; effort: string | null; recent?: { at: number; s: string }[] } // startedAt: ms; now: its last tool, in words; recent: its last tools, oldest first
export type WorkTask = { id: string; kind: string; desc: string; detail: string | null; startedAt: number; out?: string | null } // a background command, monitor or workflow; out: its output file
export type WorkCron = { id: string; schedule: string; recurring: boolean; prompt: string }
export type Work = { main: WorkLoop | null; agents: Record<string, WorkLoop>; tasks: WorkTask[]; crons: WorkCron[] }

declare module 'claude-code' {
  interface PluginState {
    // `hiddenKey`: the ids of the items shown when the person hid the band (Hide or Use), plus the memo mark when the
    // memo was due; the band stays down while those are the same, and comes back when an item appears or goes.
    // `dismissedTs`: the `ts` of the last_resolved row the person hid; that row stays down.
    // `running`: true while an Undo command runs; its button is gone meanwhile.
    // `toasted`: the ids of the red items already toasted; a toast fires only for a red id not in it.
    // `compactedTo`: the size the engine gave for the last compact, in tokens; stands in until the hooks can read one.
    // `pane`: the pane payload of the state file (null: older hooks, or none yet).
    // `paneOpen`: true after the person or a command opened the pane, false after it was closed by hand or by the
    // command, null while undecided; also kept in $.store under `paneOpen` so a new session honours it.
    // `paneAuto`: true once this session tried the unasked open (once per session).
    // `paneDraft`: unsaved values of the overrides editor, by override key (+ '@scope', '@raw', '@custom:<key>').
    // `paneSaving`: true while the editor's Save runs its command.
    // `paneView`: the page the pane shows: 'main' (the cards), 'overrides' (the editor) or 'detail' (the full text of one card).
    // `paneDetail`: which card the detail page shows: 'guide', 'resume', 'spawns', 'decisions' or 'receipt:<n>'.
    // `paneTab`: the page the desktop's nav bar shows ('home', 'running', 'guide', 'resume', 'decisions', 'spawns', 'overrides').
    // `paneNavTs`: when the person last moved to a page (ms): its cards slide in for a moment after.
    // `work`: what runs now (Work); `workTick`: bumped every few seconds while anything runs, so the elapsed times move; `workBusy`: how many agents and commands run, the nav bar's dot;
    // `workConfirm`: the row whose Stop waits for its confirmation.
    // `paneFolds`: open (true) or folded (false) by id: a card, an editor group (`g:<name>`), a long list (`more:<id>`); also in $.store.
    'abofles-guide': {
      guide: Guide | null
      hiddenKey: string | null
      dismissedTs: number | null
      running: boolean
      toasted: string[]
      compactedTo: number | null
      pane: Pane | null
      paneOpen: boolean | null
      paneAuto: boolean
      paneDraft: Record<string, string>
      paneSaving: boolean
      paneView: 'main' | 'overrides' | 'detail'
      paneDetail: string | null
      paneTab: string
      paneNavTs: number
      paneFolds: Record<string, boolean>
      work: Work
      workTick: number
      workBusy: number
      workConfirm: string | null
    }
  }
}
