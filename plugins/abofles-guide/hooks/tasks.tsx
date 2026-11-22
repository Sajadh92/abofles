import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Plan, PlanItem, PlanStatus } from '../types'
import { WHOLE, pctOf, tasksCard } from './html'
import type { Block, TaskRow, Tone } from './html'
import { GLYPH, buttonRow, card, getPane, glyph, isNum, isObj, isStr, openDetail, paneW, pic, plain } from './shared'
import type { Folds, Look, Ui } from './shared'

// The session's task list, kept by the mod from the main session's own tool calls: the app's `update_plan` (and `wrap_up`, which
// checks it off), Claude Code's `TodoWrite`, `TaskCreate` and `TaskUpdate`. The app's own list under the message goes away when a
// turn ends; this one stays in the pane until a new list replaces it. No hook and no model call feeds it.

const plan = atom({ plugin: 'abofles-guide', key: 'plan' } as const, null as Plan | null)

const HOME_ROWS = 6
const TEXT_MAX = 300

const statusOf = (s: unknown): PlanStatus => (s === 'completed' || s === 'done' ? 'completed' : s === 'in_progress' ? 'in_progress' : 'pending')
const textOf = (s: unknown): string => {
  const t = plain(isStr(s) ? s : '').replace(/\s+/g, ' ').trim()
  return t.length > TEXT_MAX ? `${t.slice(0, TEXT_MAX - 1)}…` : t
}
const itemsOf = (list: unknown, key: string): PlanItem[] =>
  (Array.isArray(list) ? list : []).filter(isObj).map(x => ({ content: textOf(x[key] ?? x.content), status: statusOf(x.status) })).filter(x => x.content)

// The id a TaskCreate result gives the new task: `Task #3 created`, or an `id` / `taskId` field.
function createdId(ran: unknown): string | null {
  const r = isObj(ran) && isObj(ran.result) ? ran.result : null
  const direct = r ? [r.taskId, r.id, isObj(r.task) ? r.task.id : undefined].find(v => isStr(v) || isNum(v)) : undefined
  if (direct !== undefined) return String(direct)
  return /Task #(\d+)/.exec(JSON.stringify(ran) ?? '')?.[1] ?? null
}

// The list after one tool call (null: the call does not touch it). Pure: the tests drive it directly.
export function applyPlan(p: Plan | null, tool: string, a: Record<string, unknown>, ran: unknown, at: number): Plan | null {
  const items = p ? p.items.map(x => ({ ...x })) : []
  switch (tool) {
    case 'mcp__ccd_turn__update_plan': {
      if (!Array.isArray(a.items) && !p) return null
      const next = Array.isArray(a.items) ? itemsOf(a.items, 'content') : items
      if (Array.isArray(a.done)) for (const n of a.done) if (isNum(n) && next[n - 1]) next[n - 1]!.status = 'completed'
      if (isNum(a.start) && next[a.start - 1]) next[a.start - 1]!.status = 'in_progress'
      if (a.advance === true) {
        const cur = next.findIndex(x => x.status === 'in_progress')
        if (cur >= 0) next[cur]!.status = 'completed'
        const nx = next.findIndex((x, i) => i > cur && x.status === 'pending')
        const pick = nx >= 0 ? nx : next.findIndex(x => x.status === 'pending')
        if (pick >= 0) next[pick]!.status = 'in_progress'
      }
      return { items: next, ts: at, closed: next.length > 0 && next.every(x => x.status === 'completed') }
    }
    case 'mcp__ccd_turn__wrap_up':
      // The app checks its list off at the wrap-up only when one step is left open: with two or more open the list goes on, and the
      // next turn's `advance` picks it up where it stands (a session wrapped up at step 2 of 6, then advance said "step 3 of 6").
      if (!p || items.length === 0 || items.filter(x => x.status !== 'completed').length > 1) return null
      return { items: items.map(x => ({ ...x, status: 'completed' as const })), ts: at, closed: true }
    case 'TodoWrite': {
      if (!Array.isArray(a.todos)) return null
      const next = itemsOf(a.todos, 'content')
      return { items: next, ts: at, closed: next.length > 0 && next.every(x => x.status === 'completed') }
    }
    case 'TaskCreate': {
      const content = textOf(a.subject ?? a.content ?? a.description)
      if (!content) return null
      const base = p && !p.closed ? items : [] // a finished list makes room for the next one
      return { items: [...base, { content, status: 'pending', id: createdId(ran) ?? String(base.length + 1) }], ts: at, closed: false }
    }
    case 'TaskUpdate': {
      const id = isStr(a.taskId) || isNum(a.taskId) ? String(a.taskId) : null
      const i = id ? items.findIndex(x => x.id === id) : -1
      if (i < 0) return null
      if (a.status === 'deleted') items.splice(i, 1)
      else {
        if (isStr(a.status)) items[i]!.status = statusOf(a.status)
        if (isStr(a.subject) && a.subject.trim()) items[i]!.content = textOf(a.subject)
      }
      return { items, ts: at, closed: items.length > 0 && items.every(x => x.status === 'completed') }
    }
    default:
      return null
  }
}

// After a tool ran: a main-session call that changes the list (a subagent's own list is its own business). Never throws.
export async function notePlan($: EngineInterface, e: { tool: string; agentId?: string }, ran: unknown) {
  try {
    if (e.agentId) return
    if (isObj(ran) && (ran.deny || ran.isError)) return
    await seedPlan($) // a tick on a list made before the mod loaded: start from the hook's copy
    const at = await $.clock.now()
    const cur = await read($, plan)
    const next = applyPlan(cur, e.tool, e as unknown as Record<string, unknown>, ran, at)
    if (next) await update($, plan, () => next)
  } catch {
    // the card keeps what it has
  }
}

export async function getPlan($: EngineInterface): Promise<Plan | null> {
  await seedPlan($)
  const p = await read($, plan)
  return isObj(p) && Array.isArray(p.items) && p.items.length > 0 && !p.hidden ? p : null
}

// No list call seen by the mod yet (it loaded after the session made its list: a reload, an update, a restart): start from the hook's
// mirror, which it rebuilds from the transcript. Once the mod holds a list, its own notes keep it.
export async function seedPlan($: EngineInterface) {
  try {
    if ((await read($, plan)) !== null) return
    const from = (await getPane($))?.plan
    if (from && from.items.length > 0) await update($, plan, cur => cur ?? { items: from.items.map(x => ({ ...x })), ts: from.ts, closed: from.closed })
  } catch {
    // no card yet
  }
}

// A finished list taken off Home: its Hide button, or `Tasks hide`. An unfinished list never hides; the next list shows again.
export async function hidePlan($: EngineInterface) {
  try {
    await update($, plan, p => (p && p.closed ? { ...p, hidden: true } : p))
  } catch {
    // the card stays
  }
}

// `Tasks hide = auto`: the main turn that finished the list ended; `next`: the next main turn starts. Never throws.
export async function notePlanTurn($: EngineInterface, phase: 'start' | 'end', agentId?: string) {
  try {
    if (agentId) return
    const mode = (await getPane($))?.tasks_hide ?? 'manual'
    if ((phase === 'end' && mode === 'auto') || (phase === 'start' && mode === 'next')) await hidePlan($)
  } catch {
    // the card stays
  }
}

// Home's Now and Next while a list is open: the step in progress and the one after it (the memo's Now says the same, written by the hook).
export async function planWhere($: EngineInterface): Promise<{ now: string; next: string | null; step: string; nextStep: string | null } | null> {
  const p = await getPlan($)
  if (!p || p.closed) return null
  const i = p.items.findIndex(x => x.status === 'in_progress')
  const cur = i >= 0 ? i : p.items.findIndex(x => x.status === 'pending')
  if (cur < 0) return null
  const after = p.items.slice(cur + 1).find(x => x.status !== 'completed')
  return { now: `Step ${cur + 1} of ${p.items.length} — ${p.items[cur]!.content}`, next: after ? after.content : 'The last step: then the receipt', step: p.items[cur]!.content, nextStep: after?.content ?? null }
}

// Where the list stands: its word and tone, and the line beside it. `running`: the main turn is on; `busy`: agents and commands at work.
export function planState(p: Plan, running: boolean, busy: number): { word: string; tone: Tone; line: string; done: number; cur: number } {
  const n = p.items.length
  const done = p.items.filter(x => x.status === 'completed').length
  const now = p.items.findIndex(x => x.status === 'in_progress')
  const cur = now >= 0 ? now : p.items.findIndex(x => x.status === 'pending')
  if (done === n) return { word: 'Done', tone: 'green', line: `All ${n} ${n === 1 ? 'step' : 'steps'} done`, done, cur: -1 }
  const line = `Step ${cur + 1} of ${n}`
  if (running) return { word: 'Working', tone: 'blue', line, done, cur }
  if (busy > 0) return { word: busy === 1 ? 'Waiting on 1 job' : `Waiting on ${busy} jobs`, tone: 'blue', line, done, cur }
  return { word: 'Paused', tone: 'amber', line: `${line} · send a message to go on`, done, cur }
}

const rowOf = (x: PlanItem): TaskRow => ({ text: x.content, state: x.status === 'completed' ? 'done' : x.status === 'in_progress' ? 'now' : 'todo' })
const altOf = (r: TaskRow): string => `${r.state === 'done' ? 'Done' : r.state === 'now' ? 'Now' : 'To do'}: ${r.text}`

// The card's block: on Home a window from the step before the current one, one line per step (a line's height is exact, so no guessed
// room shows as a gap); every step and its whole text on the detail page (`whole`).
export function planBlock(p: Plan, running: boolean, busy: number, w: number, whole: boolean): { block: Block; alt: string[]; cut: boolean } {
  const s = planState(p, running, busy)
  const rows = p.items.map(rowOf)
  let from = 0
  let to = rows.length
  if (!whole) {
    if (s.cur < 0) to = 0 // all done: the header says so, the rows are on the detail page
    else {
      from = Math.max(0, Math.min(s.cur - 1, rows.length - HOME_ROWS))
      to = Math.min(rows.length, from + HOME_ROWS)
    }
  }
  const shown = rows.slice(from, to)
  const block = tasksCard({ paneW: w, rows: shown, done: s.done, total: rows.length, word: s.word, tone: s.tone, line: s.line, before: whole || to === 0 ? 0 : from, after: whole || to === 0 ? 0 : rows.length - to, max: whole ? WHOLE : 1, steps: rows.map(r => r.state) })
  return { block, alt: ['Tasks', `${s.done} / ${rows.length}`, s.word, s.line, `${pctOf({ done: s.done, total: rows.length })}%`, ...shown.map(altOf)], cut: shown.length < rows.length }
}

// Home's Tasks card, with `All tasks (N)` when the card shows part of the list.
export async function tasksHomeCard($: EngineInterface, ui: Ui, look: Look, running: boolean, busy: number) {
  const p = await getPlan($)
  if (!p) return null
  const { Box, Button } = ui
  const b = planBlock(p, running, busy, paneW(look), false)
  return (
    <Box flexDirection="column" rowGap="sm">
      {pic(ui, look, b.block, b.alt)}
      {buttonRow(ui, [
        b.cut ? <Button key="detail:tasks" label={glyph(look, GLYPH.detail, `All tasks (${p.items.length})`)} variant="secondary" onPress={() => openDetail($, 'tasks')} /> : null,
        p.closed ? <Button key="tasks:hide" label={glyph(look, '×', 'Hide')} variant="secondary" onPress={() => hidePlan($)} /> : null,
      ])}
    </Box>
  )
}

// The detail page: every step, nothing cut.
export async function tasksDetail($: EngineInterface, look: Look, running: boolean, busy: number) {
  const p = await getPlan($)
  if (!p) return null
  const b = planBlock(p, running, busy, paneW(look), true)
  const s = planState(p, running, busy)
  return { icon: 'list' as const, tone: s.tone, title: 'Tasks', sub: `${s.done} / ${p.items.length}`, subTone: s.tone, cards: [b.block], alt: b.alt }
}

// The terminal: a folding card of plain rows.
export async function tasksTerminal($: EngineInterface, ui: Ui, folds: Folds, look: Look, running: boolean, busy: number) {
  const p = await getPlan($)
  if (!p) return null
  const { Box, Button, Text } = ui
  const s = planState(p, running, busy)
  const mark = { completed: '✓', in_progress: '›', pending: '○' } as const
  return card($, ui, { id: 'tasks', title: 'Tasks', summary: `${s.done}/${p.items.length} · ${s.word}`, folds, icon: 'list', look }, (
    <Box flexDirection="column">
      {p.items.map((x, i) => (
        <Text key={`task:${i}`} dimColor={x.status === 'completed'} bold={x.status === 'in_progress'}>{`${mark[x.status]} ${x.content}`}</Text>
      ))}
      {p.closed ? buttonRow(ui, [<Button key="tasks:hide" label="Hide" variant="secondary" onPress={() => hidePlan($)} />]) : null}
    </Box>
  ))
}
