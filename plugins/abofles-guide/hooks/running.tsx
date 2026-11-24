import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Pane, PaneLive, Work, WorkCron, WorkLoop, WorkTask } from '../types'
import { WHOLE, runCard, runOutput, runRecent, runningHome, subhead } from './html'
import type { Block, IconName, SpawnRow, Tone } from './html'
import {
  GLYPH,
  buttonRow,
  card,
  emptyPic,
  fmtTokens,
  glyph,
  goTab,
  hasSvg,
  htmlSection,
  isNum,
  isObj,
  isStr,
  modelName,
  openDetail,
  paneW,
  pic,
  plain,
  plural,
} from './shared'
import type { Folds, Look, RenderEvent, Ui } from './shared'

// The Running page and Home's Running card: what works right now, from the engine's own events, with no model call. An agent's row reads
// `$.agent.list()` for its status and this module's notes for the rest: its last tool, tool count, tokens, model and start. A background
// command comes from the Bash result and from the `background_tasks` every Stop and SubagentStop carry. The engine's own progress summaries
// (one model call per agent every 30 s) are left off: a mod cannot read them, and the last tool says what an agent does for free.

// bg: the agents the last Stop named as background tasks, which TaskStop takes
type WorkNow = Work & { bg?: string[] }
const EMPTY: WorkNow = { main: null, agents: {}, tasks: [], crons: [] }
const work = atom({ plugin: 'abofles-guide', key: 'work' } as const, EMPTY)
const workTick = atom({ plugin: 'abofles-guide', key: 'workTick' } as const, 0) // bumped while anything runs: the elapsed times move
const mainOn = atom({ plugin: 'abofles-guide', key: 'mainOn' } as const, false) // the main turn is on: written only at its start and end, so Home (which reads it) does not redraw on every tool call
const workBusy = atom({ plugin: 'abofles-guide', key: 'workBusy' } as const, 0) // agents + background commands: the nav bar's dot reads this alone, so it redraws only when the count changes
const workConfirm = atom({ plugin: 'abofles-guide', key: 'workConfirm' } as const, null as string | null) // the row whose Stop waits for its confirmation

const TICK_MS = 5000
const ACTIVE = new Set(['pending', 'running', 'waiting'])
const NOW_MAX = 90
const HOME_ROWS = 3
const RECENT_MAX = 15
const OUTPUT_LINES = 30

// A note must never disturb the work it watches: each catches its own failure, so the tool or the model call goes on and the page shows less.

const fresh = (at: number): WorkLoop => ({ startedAt: at, now: null, uses: 0, tokens: 0, model: null, effort: null, doneAt: at }) // doneAt from the start: a first tool that never returns is seen as in flight

function withLoop(w: Work, agentId: string | undefined, at: number, f: (l: WorkLoop) => WorkLoop): Work {
  if (agentId) return { ...w, agents: { ...w.agents, [agentId]: f(w.agents[agentId] ?? fresh(at)) } }
  return { ...w, main: f(w.main ?? fresh(at)) }
}

const baseName = (p: string): string => p.split(/[\\/]/).filter(Boolean).pop() ?? p
const firstLine = (t: string): string => t.trim().split('\n')[0]!

// What a tool call says the loop is doing, from its own arguments: `Read html.ts`, `Bash: run the tests`, `Grep agent.spawn`.
export function toolLabel(tool: string, a: Record<string, unknown>): string {
  const s = (k: string): string => (isStr(a[k]) ? (a[k] as string).trim() : '')
  const one = (name: string, arg: string): string => (arg ? `${name} ${arg}` : name)
  let text: string
  switch (tool) {
    case 'Read':
    case 'Edit':
    case 'Write':
    case 'NotebookEdit':
      text = one(tool, baseName(s('file_path') || s('notebook_path')))
      break
    case 'Bash':
    case 'PowerShell':
      text = `${tool}: ${s('description') || firstLine(s('command'))}`.replace(/: $/, '')
      break
    case 'Grep':
    case 'Glob':
      text = one(tool, s('pattern'))
      break
    case 'Agent':
    case 'Task':
      text = `Agent: ${s('description')}`.replace(/: $/, '')
      break
    case 'WebSearch':
      text = one(tool, s('query'))
      break
    case 'WebFetch':
      text = one(tool, s('url').replace(/^https?:\/\//, '').split('/')[0]!)
      break
    default: {
      const m = /^mcp__.+?__(.+)$/.exec(tool)
      text = m ? m[1]! : tool
    }
  }
  const clean = plain(text)
  return clean.length > NOW_MAX ? `${clean.slice(0, NOW_MAX - 1)}…` : clean
}

// `45s`, `2m 05s`, `1h 12m`: tabular, so a row's figure holds its place as it counts.
export function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

async function activeAgents($: EngineInterface) {
  try {
    return (await $.agent.list()).filter(a => ACTIVE.has(a.status))
  } catch {
    return []
  }
}

// The count the nav bar's dot and the timer read. Written only when it changed: every reader redraws at a write.
async function syncBusy($: EngineInterface): Promise<number> {
  const n = (await activeAgents($)).length + (await read($, work)).tasks.length
  if ((await read($, workBusy)) !== n) await update($, workBusy, () => n)
  return n
}

// The main session's turn is on (its loop started and has not ended).
export async function getMainRunning($: EngineInterface): Promise<boolean> {
  try {
    return (await read($, mainOn)) === true
  } catch {
    return false
  }
}

// A background command with nothing new for this long is quiet: a server, a watcher or a loop that never ends. It still shows on
// Running, but grey with its quiet time, and the Tasks card does not wait on it.
export const QUIET_MS = 30 * 60 * 1000
// Quiet is time since the output last grew (its file's size, looked at each tick), else since the start: a long job that still writes is not quiet.
// A command whose output file was never seen (a row the Stop list seeded after a reload) has no known output: it is never called quiet.
export const quietSince = (t: { startedAt: number; outAt?: number }) => t.outAt ?? t.startedAt
export const isQuiet = (t: { startedAt: number; outAt?: number; size?: number }, now: number) => t.size !== undefined && now - quietSince(t) >= QUIET_MS

// An agent's loop is quiet when its last tool call is that old (or, with none, its start): a hung agent no longer reads as busy.
// Since when a loop has been quiet: its last tool's start, or its end when the tool returned. A tool that started and has not returned
// (one long build) is not quiet: the loop is at work, so the time is the far future.
export const loopQuietSince = (l: { startedAt: number; recent?: { at: number }[]; doneAt?: number }) => {
  const last = l.recent?.[l.recent.length - 1]?.at ?? 0
  // a loop from before this was tracked (no doneAt) keeps the old reading: quiet from its last tool's start
  if (last > 0 && l.doneAt !== undefined && l.doneAt < last) return Number.MAX_SAFE_INTEGER
  return Math.max(l.startedAt, last, l.doneAt ?? 0)
}

// Each tick: the size of every command's output file. A file that grew moves its `outAt`. Written only when one changed.
async function noteOutput($: EngineInterface) {
  const w = await read($, work)
  if (!w.tasks.some(t => t.out)) return
  const now = await $.clock.now()
  const sizes = new Map<string, number>()
  for (const t of w.tasks) {
    if (!t.out) continue
    try {
      sizes.set(t.id, (await $.fs.stat(t.out)).size)
    } catch {
      // gone or unreadable: its time stays
    }
  }
  if (!w.tasks.some(t => grewOf(t, sizes))) return
  await update($, work, cur => ({ ...cur, tasks: withSizes(cur.tasks, sizes, now) }))
}

const grewOf = (t: WorkTask, sizes: Map<string, number>) => sizes.has(t.id) && sizes.get(t.id) !== t.size

// The tasks after one look at their output sizes: a file that grew moves `outAt` to now (the first look only records the size).
export function withSizes(tasks: WorkTask[], sizes: Map<string, number>, now: number): WorkTask[] {
  return tasks.map(t => (grewOf(t, sizes) ? { ...t, size: sizes.get(t.id), outAt: t.size === undefined ? t.startedAt : now } : t))
}

// What the Tasks card waits on: the agents at work and the commands that are not quiet.
export async function getWaitBusy($: EngineInterface): Promise<number> {
  try {
    const now = await $.clock.now()
    const w = await read($, work)
    // an agent quiet past QUIET_MS (hung, or polling forever) is not waited on, the same as a quiet command
    const agents = (await activeAgents($)).filter(a => {
      const l = w.agents[a.id]
      return !(a.status === 'running' && l && l.endedAt === undefined && now - loopQuietSince(l) >= QUIET_MS)
    })
    return agents.length + w.tasks.filter(t => !isQuiet(t, now)).length
  } catch {
    return 0
  }
}

export async function getWorkBusy($: EngineInterface): Promise<number> {
  const n = await read($, workBusy)
  return isNum(n) ? n : 0
}

// ---- the notes, one per engine event

export async function noteTurnStart($: EngineInterface, agentId?: string) {
  try {
    const at = await $.clock.now()
    await update($, work, w => {
      if (!agentId) return { ...w, main: fresh(at) }
      const l = w.agents[agentId]
      if (!l) return { ...w, agents: { ...w.agents, [agentId]: fresh(at) } }
      if (l.endedAt === undefined) return w
      const { endedAt: _woke, ...rest } = l // a message woke it: it works again
      return { ...w, agents: { ...w.agents, [agentId]: rest } }
    })
    if (agentId) await syncBusy($)
    else if ((await read($, mainOn)) !== true) await update($, mainOn, () => true)
  } catch {
    // the page keeps what it has
  }
}

// An agent's turn ended: its reply is in. The engine may still list it as `waiting` on background work it owns (a CI wait it started),
// so its notes stay, marked, and its row says it replied; the next Stop or SubagentStop drops them once the list no longer names it.
export async function noteTurnEnd($: EngineInterface, agentId?: string) {
  try {
    const at = await $.clock.now()
    await update($, work, w => {
      if (!agentId) return { ...w, main: null }
      const l = w.agents[agentId]
      return l ? { ...w, agents: { ...w.agents, [agentId]: { ...l, endedAt: at } } } : w
    })
    if (!agentId && (await read($, mainOn)) !== false) await update($, mainOn, () => false)
    await syncBusy($)
  } catch {
    // the page keeps what it has
  }
}

const num = (v: unknown): number => (isNum(v) ? v : 0)

export async function noteStep($: EngineInterface, e: { agentId?: string; model?: unknown; effort?: unknown }, r: { usage?: unknown } | null | undefined) {
  try {
    const at = await $.clock.now()
    const u = isObj(r?.usage) ? r.usage : null
    const tokens = u ? num(u.input_tokens) + num(u.output_tokens) + num(u.cache_read_input_tokens) + num(u.cache_creation_input_tokens) : 0
    await update($, work, w =>
      withLoop(w, e.agentId, at, l => ({ ...l, tokens: l.tokens + tokens, model: isStr(e.model) ? e.model : l.model, effort: isStr(e.effort) ? e.effort : l.effort })),
    )
  } catch {
    // the page keeps what it has
  }
}

export async function noteTool($: EngineInterface, e: { tool: string; agentId?: string }) {
  try {
    const at = await $.clock.now()
    await update($, work, w => withLoop(w, e.agentId, at, l => {
      const s = toolLabel(e.tool, e as unknown as Record<string, unknown>)
      return { ...l, now: s, uses: l.uses + 1, recent: [...(l.recent ?? []), { at, s }].slice(-RECENT_MAX) }
    }))
  } catch {
    // the page keeps what it has
  }
}

// A command moved to the background shows at once, not only at the next Stop. A stopped task goes at once too.
export async function noteToolDone($: EngineInterface, e: { tool: string; agentId?: string }, ran: unknown) {
  try {
    // an agent's tool returned: its quiet clock starts here, not at the tool's start
    if (e.agentId) {
      const done = await $.clock.now()
      await update($, work, w => {
        const l = w.agents[e.agentId as string]
        return l ? { ...w, agents: { ...w.agents, [e.agentId as string]: { ...l, doneAt: done } } } : w
      })
    }
    const a = e as unknown as Record<string, unknown>
    const out = isObj(ran) && isObj(ran.result) ? ran.result : null
    if (e.tool === 'TaskStop' && isObj(ran) && !ran.deny && !ran.isError) {
      const id = isStr(a.task_id) ? a.task_id : ''
      if (id) await dropTask($, id)
      return
    }
    if (!out || !['Bash', 'PowerShell', 'Monitor'].includes(e.tool)) return
    const id = [out.backgroundTaskId, out.taskId, out.task_id].find(isStr)
    if (!id) return
    const at = await $.clock.now()
    const command = isStr(a.command) ? a.command : ''
    const desc = plain(isStr(a.description) && a.description ? a.description : firstLine(command))
    // the output file, as the result names it: only a path ending in this task's own id
    // a structured result may name it in a field; else the text names it. No path: the row is never called quiet (no size to watch)
    const named = [out.outputFile, out.output_file, out.outputPath].find(isStr)
    const file = named ?? new RegExp(`([^\\s"'\\\\]+/${id.replace(/[^\w-]/g, '')}\\.output)`).exec(JSON.stringify(ran) ?? '')?.[1] ?? null
    const task: WorkTask = { id, kind: e.tool === 'Monitor' ? 'monitor' : 'shell', desc, detail: command ? plain(firstLine(command)) : null, startedAt: at, out: file, owner: e.agentId ?? null }
    await update($, work, w => (w.tasks.some(t => t.id === id) ? w : { ...w, tasks: [...w.tasks, task] }))
    await syncBusy($)
  } catch {
    // the page keeps what it has
  }
}

async function dropTask($: EngineInterface, id: string) {
  await update($, work, w => ({ ...w, tasks: w.tasks.filter(t => t.id !== id) }))
  await syncBusy($)
}

// Every Stop and SubagentStop names all the work still in flight: the one list that also sees what ended quietly. The agents are
// `$.agent.list()`'s to show, so a subagent's task is left out (its id is kept: Stop can stop it); a loop no list names (an engine fork)
// leaves its notes here. A command noted after this handler started is newer than the list: it stays.
export async function noteBackground($: EngineInterface, e: { background_tasks?: unknown; session_crons?: unknown }) {
  try {
    const at = await $.clock.now()
    const live = new Set((await activeAgents($)).map(a => a.id))
    await update($, work, w => {
      const known = new Map(w.tasks.map(t => [t.id, t]))
      const tasks: WorkTask[] = !Array.isArray(e.background_tasks)
        ? w.tasks
        : e.background_tasks.flatMap(t => {
            if (!isObj(t) || !isStr(t.id) || t.type === 'subagent' || live.has(t.id)) return []
            const was = known.get(t.id)
            const detail = [t.command, t.name, t.tool].find(isStr)
            return [{ id: t.id, kind: isStr(t.type) ? t.type : 'task', desc: plain(isStr(t.description) ? t.description : ''), detail: detail ? plain(firstLine(detail)) : was?.detail ?? null, startedAt: was?.startedAt ?? at, out: was?.out ?? null, owner: was?.owner ?? null, ...(was?.size !== undefined ? { size: was.size } : {}), ...(was?.outAt !== undefined ? { outAt: was.outAt } : {}) }]
          })
      const listed = new Set(tasks.map(t => t.id))
      const newer = Array.isArray(e.background_tasks) ? w.tasks.filter(t => t.startedAt > at && !listed.has(t.id)) : []
      const crons: WorkCron[] = !Array.isArray(e.session_crons)
        ? w.crons
        : e.session_crons.flatMap(c => (isObj(c) && isStr(c.id) && isStr(c.schedule) ? [{ id: c.id, schedule: c.schedule, recurring: c.recurring === true, prompt: plain(isStr(c.prompt) ? c.prompt : '') }] : []))
      const bg = Array.isArray(e.background_tasks) ? e.background_tasks.flatMap(t => (isObj(t) && isStr(t.id) && t.type === 'subagent' ? [t.id] : [])) : (w as WorkNow).bg
      return { ...w, tasks: [...tasks, ...newer], crons, agents: Object.fromEntries(Object.entries(w.agents).filter(([id]) => live.has(id))), ...(bg ? { bg } : {}) }
    })
    await syncBusy($)
  } catch {
    // the page keeps what it has
  }
}

// A background task's notification reached the session, possibly inside a running turn: that task ended.
export async function noteNotified($: EngineInterface, message: unknown) {
  try {
    const id = /<task-id>([^<]+)<\/task-id>/.exec(JSON.stringify(message) ?? '')?.[1]
    if (id) await dropTask($, id)
  } catch {
    // the page keeps what it has
  }
}

async function tick($: EngineInterface) {
  try {
    await noteOutput($).catch(() => {})
    const busy = await syncBusy($)
    // agents or commands at work: every tick moves their times. Only quiet ones (a server, a watcher): every 12th tick (60 s), their
    // minutes still move. Only the main turn: every 6th tick (30 s), enough for its own row
    tickN += 1
    const live = busy > 0 && (await getWaitBusy($)) > 0
    if (live || (busy > 0 && tickN % 12 === 0) || ((await read($, work)).main && tickN % 6 === 0)) await update($, workTick, n => n + 1)
  } catch {
    // the next tick tries again
  }
}

// The elapsed times move while something runs. It starts over at a reload (the module's variables do); the engine ends the old one.
let ticking = false
let tickN = 0
export function startTick($: EngineInterface) {
  if (ticking) return
  ticking = true
  try {
    $.clock.every(TICK_MS, () => void tick($))
  } catch {
    ticking = false // a surface or a test without timers: the times move with the events
  }
}

// ---- the rows

// stop: what the row is, for its Stop and its Full details; stoppable: Stop is drawn (TaskStop takes background work and named agents only).
export type RunRow = SpawnRow & { id: string; stop: 'agent' | 'command' | null; open: boolean; stoppable?: boolean }
export type Running = { main: RunRow | null; agents: RunRow[]; tasks: RunRow[]; crons: RunRow[]; n: number; confirm: string | null; alt: string[] }

const ALIAS = /(haiku|sonnet|opus|fable)/
const loopModel = (l: WorkLoop | undefined, labels: Record<string, string>, fallback?: { model: string; effort: string }): string => {
  const raw = l?.model ?? fallback?.model
  if (!raw) return ''
  return modelName(ALIAS.exec(raw)?.[1] ?? raw, l?.effort ?? fallback?.effort ?? '', labels)
}
const loopMeta = (l: WorkLoop | undefined, model: string): string =>
  [model, l && l.uses > 0 ? plural(l.uses, 'tool') : '', l && l.tokens > 0 ? `${fmtTokens(l.tokens)} tokens` : ''].filter(Boolean).join(' · ')

// `abofles:abofles-worker` -> `Worker`, `general-purpose` -> `General-purpose`.
function typeName(type: string): string {
  const t = type.split(':').pop()?.replace(/^abofles-/, '') ?? type
  return t ? t[0]!.toUpperCase() + t.slice(1) : 'Agent'
}

type Live = Omit<PaneLive, 'agent_id'> & { agent_id?: string | null }

// Each agent's ledger row (side, model): the row with its agent id; else, as a fallback, the first row with its ask that no other agent took
// and that names no other agent — two agents with one ask each get their own.
export function liveOf(agents: { id: string; description: string }[], live: Live[]): Map<string, Live> {
  const out = new Map<string, Live>()
  const ids = new Set(agents.map(a => a.id))
  const taken = new Set<Live>()
  for (const a of agents) {
    const row = live.find(x => x.agent_id === a.id)
    if (row) {
      out.set(a.id, row)
      taken.add(row)
    }
  }
  for (const a of agents) {
    if (out.has(a.id)) continue
    const desc = plain(a.description)
    const row = live.find(x => !taken.has(x) && !(x.agent_id && ids.has(x.agent_id)) && plain(x.description) === desc)
    if (row) {
      out.set(a.id, row)
      taken.add(row)
    }
  }
  return out
}

// TaskStop takes a background task, a teammate or a named background agent, not a foreground agent: one the engine names (`name`, its own for a
// background agent), a teammate, a plugin's spawn (always background), one the last Stop listed as a background task, or one whose ledger row
// (by its id) is still pending, which only a background agent's is.
export function canStop(a: { id: string; name?: string; teammateId?: string; spawnedBy?: string }, bg: string[], row: Live | undefined): boolean {
  return !!a.name || !!a.teammateId || !!a.spawnedBy || bg.includes(a.id) || (!!row && row.agent_id === a.id)
}

const word = (status: string): { word: string; wordTone: 'amber' | 'blue' | null; tone: 'amber' | 'blue' } =>
  status === 'waiting' ? { word: 'waiting', wordTone: 'amber', tone: 'amber' } : status === 'pending' ? { word: 'starting', wordTone: 'blue', tone: 'blue' } : { word: '', wordTone: null, tone: 'blue' }

// A `waiting` agent whose turn ended: it replied, and what it still waits for is a background command of its own (a CI wait).
function repliedOf(owned: WorkTask[]): Pick<RunRow, 'word' | 'wordTone' | 'tone' | 'now'> {
  const first = owned[0]?.desc
  const now = owned.length > 1 ? `Waits for its ${owned.length} background commands` : first ? `Waits for its command: ${first}` : 'Waits for its background work'
  return { word: 'replied', wordTone: 'green', tone: 'green', now: now.length > NOW_MAX ? `${now.slice(0, NOW_MAX - 1)}…` : now }
}

export async function runningOf($: EngineInterface, p: Pane | null): Promise<Running> {
  const w = await read($, work)
  await read($, workTick)
  const confirm = await read($, workConfirm)
  const now = await $.clock.now()
  const labels = Object.fromEntries((p?.spawns.totals.by_model ?? []).map(m => [m.model, m.label]))
  const since = (l: { startedAt: number } | undefined) => (l ? fmtElapsed(now - l.startedAt) : '')
  const sides = new Map<string, string>()
  const list = await activeAgents($)
  const rows = liveOf(list, p?.spawns.live ?? [])
  const bg = (w as WorkNow).bg
  const agents = list.map((a): RunRow => {
    const l = w.agents[a.id]
    const desc = plain(a.description)
    const live = rows.get(a.id)
    const side = live?.side || typeName(a.type)
    sides.set(a.id, side)
    const row: RunRow = { id: a.id, ...word(a.status), side, desc, cost: since(l), now: l?.now ?? '', meta: loopMeta(l, loopModel(l, labels, live)), stop: 'agent', open: true, stoppable: canStop(a, Array.isArray(bg) ? bg : [], live) }
    if (a.status === 'running' && l && l.endedAt === undefined && now - loopQuietSince(l) >= QUIET_MS) return { ...row, tone: 'grey', word: `quiet ${Math.floor((now - loopQuietSince(l)) / 60000)}m`, wordTone: 'grey' }
    // its reply is in and the engine holds it only on its own background work: not stuck, so it says so
    return a.status === 'waiting' && l?.endedAt !== undefined ? { ...row, ...repliedOf(w.tasks.filter(t => t.owner === a.id)) } : row
  })
  const tasks = w.tasks.map((t): RunRow => {
    const detail = t.detail && t.detail !== t.desc ? t.detail : ''
    const owner = t.owner ? sides.get(t.owner) : undefined
    if (isQuiet(t, now)) return { id: t.id, tone: 'grey', word: `quiet ${Math.floor((now - quietSince(t)) / 60000)}m`, wordTone: 'grey', side: typeName(t.kind), desc: t.desc, cost: since(t), now: detail, meta: owner ? `started by ${owner}` : '', stop: 'command', open: !!t.out }
    return { id: t.id, tone: 'blue', word: '', wordTone: null, side: typeName(t.kind), desc: t.desc, cost: since(t), now: detail, meta: owner ? `started by ${owner}` : '', stop: 'command', open: !!t.out }
  })
  const crons = w.crons.map((c): RunRow => ({ id: c.id, tone: 'grey', word: '', wordTone: null, side: 'Scheduled', desc: c.prompt, cost: '', now: '', meta: `${c.schedule} · ${c.recurring ? 'repeats' : 'once'}`, stop: null, open: false }))
  const main: RunRow | null = w.main ? { id: 'main', tone: 'blue', word: '', wordTone: null, side: 'Main session', desc: '', cost: since(w.main), now: w.main.now ?? '', meta: loopMeta(w.main, loopModel(w.main, labels)), stop: null, open: false } : null
  const alt = [main, ...agents, ...tasks, ...crons].flatMap(r => (r ? [r.side, r.desc, r.now, r.meta, r.word, r.cost].filter((x): x is string => !!x) : []))
  return { main, agents, tasks, crons, n: agents.length + tasks.length, confirm, alt }
}

const rowAlt = (r: RunRow) => [r.side, r.desc, r.now, r.meta, r.word, r.cost]

// ---- the actions

// The terminal: the agent's transcript in the main view, as the app's own tasks list does. A desktop has no main view a plugin can switch: `Full details` opens a page instead.
async function openAgent($: EngineInterface, id: string) {
  try {
    const r = await $.ui.view({ agentId: id })
    if (r && 'deny' in r && r.deny) $.ui.toast(`Cannot open it here: ${plain(String(r.deny))}`)
  } catch (err) {
    $.ui.toast(`Cannot open it here: ${plain(err instanceof Error ? err.message : String(err))}`)
  }
}

async function backToMain($: EngineInterface) {
  try {
    await $.ui.view({})
  } catch {
    // the main conversation stays where it is
  }
}

async function askStop($: EngineInterface, id: string | null) {
  await update($, workConfirm, () => id)
}

async function stopTask($: EngineInterface, id: string) {
  await askStop($, null)
  try {
    const r = (await $.tool.call({ tool: 'TaskStop', task_id: id } as never)) as { deny?: string; isError?: boolean; text?: string }
    if (r.deny || r.isError) $.ui.toast(`Could not stop it: ${plain(r.deny ?? r.text ?? 'refused')}`)
    else await dropTask($, id)
  } catch (err) {
    $.ui.toast(`Could not stop it: ${plain(err instanceof Error ? err.message : String(err))}`)
  }
}

// Full details (the terminal: Open, for an agent) and Stop under a row. Stop cannot be undone, so its press asks first, in place.
function rowButtons($: EngineInterface, ui: Ui, look: Look, r: RunRow, confirm: string | null, viewing: string | null) {
  const { Button } = ui
  const stop = r.stop !== null && r.stoppable !== false
  if (!stop && !r.open) return null
  const desk = hasSvg(ui, look)
  if (confirm === r.id && stop) {
    return buttonRow(ui, [
      <Button key={`stop-yes:${r.id}`} label={r.stop === 'agent' ? 'Stop agent' : 'Stop command'} variant="primary" onPress={() => stopTask($, r.id)} />,
      <Button key={`stop-no:${r.id}`} label="Keep running" variant="secondary" onPress={() => askStop($, null)} />,
    ])
  }
  return buttonRow(ui, [
    r.open && desk ? <Button key={`open:${r.id}`} label={glyph(look, GLYPH.detail, 'Full details')} variant="secondary" onPress={() => openDetail($, `run:${r.id}`)} /> : null,
    r.open && !desk && r.stop === 'agent' && viewing !== r.id ? <Button key={`open:${r.id}`} label={glyph(look, GLYPH.open, 'Open')} variant="secondary" onPress={() => openAgent($, r.id)} /> : null,
    stop ? <Button key={`stop:${r.id}`} label={glyph(look, GLYPH.stop, 'Stop')} variant="secondary" onPress={() => askStop($, r.id)} /> : null,
  ])
}

// ---- the page and the card

// The agent whose transcript is in the main view, as this site reads it.
const viewingOf = (e: RenderEvent): string | null => {
  const props = (e as unknown as { props?: { view?: { agentId?: unknown } } }).props
  return isStr(props?.view?.agentId) ? props.view.agentId : null
}

const EMPTY_TEXT = 'Nothing running. Agents and commands show here.'

// The desktop's Running page: the main turn, then each agent and command in a card of its own with its buttons, then what is scheduled.
export async function runningPage($: EngineInterface, ui: Ui, p: Pane | null, look: Look) {
  const r = await runningOf($, p)
  const w = paneW(look)
  const rowPic = (x: RunRow) => pic(ui, look, runCard(x, w), rowAlt(x))
  const { Box } = ui
  // a row's buttons sit closer to their own card than to the next card
  const item = (x: RunRow) => <Box key={`run:${x.id}`} flexDirection="column" rowGap="xs">{rowPic(x)}{rowButtons($, ui, look, x, r.confirm, null)}</Box>
  const group = (title: string, rows: RunRow[]) => (rows.length === 0 ? [] : [pic(ui, look, subhead(title), [title, String(rows.length)]), ...rows.map(item)])
  const body = [
    r.main ? rowPic(r.main) : null,
    ...group('Agents', r.agents),
    ...group('Background', r.tasks),
    ...group('Scheduled', r.crons),
  ]
  if (!body.some(Boolean)) body.push(emptyPic(ui, look, EMPTY_TEXT))
  return htmlSection(ui, look, { icon: 'activity', tone: 'blue', title: 'Running', summary: r.n > 0 ? String(r.n) : null, summaryTone: r.n > 0 ? 'blue' : null }, body)
}

export type RunDetail = { icon: IconName; tone: Tone; title: string; sub: string | null; cards: Block[]; alt: (string | null | undefined)[] }

const clock = (ms: number): string => new Date(ms).toTimeString().slice(0, 8)
const plainLines = (text: string): string[] => text.split('\n').map(l => plain(l).trimEnd()).filter(Boolean)

// A running command's last lines, or why there are none. A file past what one read may hold says so: the page still opens.
async function outputOf($: EngineInterface, file: string | null | undefined): Promise<{ lines: string[]; note: string | null }> {
  if (!file) return { lines: [], note: 'No output file for this one.' }
  try {
    const all = plainLines(await $.fs.read(file))
    return all.length === 0 ? { lines: [], note: 'No output yet.' } : { lines: all.slice(-OUTPUT_LINES), note: all.length > OUTPUT_LINES ? `last ${OUTPUT_LINES} of ${all.length}` : null }
  } catch {
    return { lines: [], note: `Too large or gone: ${file}` }
  }
}

// A row's Full details page on the desktop: the row with nothing cut, then what it did. Null once it ended.
export async function runningDetail($: EngineInterface, id: string, p: Pane | null, look: Look): Promise<RunDetail | null> {
  const r = await runningOf($, p)
  const row = [...r.agents, ...r.tasks].find(x => x.id === id)
  if (!row) return null
  const w = await read($, work)
  const width = paneW(look)
  const cards: Block[] = [runCard(row, width, WHOLE)]
  const alt: (string | null | undefined)[] = rowAlt(row)
  if (row.stop === 'agent') {
    const recent = [...(w.agents[id]?.recent ?? [])].reverse().map(x => ({ when: clock(x.at), text: x.s }))
    if (recent.length) {
      cards.push(runRecent(recent, width))
      alt.push('Recent tools', ...recent.flatMap(x => [x.when, x.text]))
    }
  } else {
    const o = await outputOf($, w.tasks.find(t => t.id === id)?.out)
    cards.push(runOutput(o.lines, o.note, width))
    alt.push('Output', o.note, ...o.lines)
  }
  return { icon: 'activity', tone: 'blue', title: 'Running', sub: null, cards, alt }
}

// Home's card: the agents and commands that run now, the first few; the rest and the buttons are on the Running page. The main turn
// stays off it: it runs whenever the chat does, so the card would come and go with every reply. Nothing runs: no card.
export async function runningHomeCard($: EngineInterface, ui: Ui, p: Pane | null, look: Look) {
  const { Button } = ui
  const r = await runningOf($, p)
  if (r.n === 0) return null
  const rows = [...r.agents, ...r.tasks]
  const shown = rows.slice(0, HOME_ROWS)
  const { Box } = ui
  return (
    <Box flexDirection="column" rowGap="sm">
      {pic(ui, look, runningHome(shown, r.n, paneW(look)), ['Running', String(r.n), ...shown.flatMap(rowAlt)])}
      {rows.length > HOME_ROWS ? buttonRow(ui, [<Button key="home-running" label={glyph(look, GLYPH.detail, `All running (${r.n})`)} variant="secondary" onPress={() => goTab($, 'running')} />]) : null}
    </Box>
  )
}

// The terminal has one long list of folding cards: this one only while an agent or a command runs.
export async function runningTerminal($: EngineInterface, ui: Ui, p: Pane | null, folds: Folds, look: Look, e: RenderEvent) {
  const { Box, Text } = ui
  const r = await runningOf($, p)
  if (r.n === 0 && r.crons.length === 0) return null
  const viewing = viewingOf(e)
  const { Button } = ui
  const row = (x: RunRow) => (
    <Box key={`run:${x.id}`} flexDirection="column">
      <Box flexDirection="row" justifyContent="space-between" columnGap={1}>
        <Text>{[x.side, x.word].filter(Boolean).join(' · ')}</Text>
        {x.cost ? <Text dimColor tabular>{x.cost}</Text> : null}
      </Box>
      {x.desc ? <Text>{x.desc}</Text> : null}
      {x.now ? <Text dimColor>{x.now}</Text> : null}
      {x.meta ? <Text dimColor>{x.meta}</Text> : null}
      {rowButtons($, ui, look, x, r.confirm, viewing)}
    </Box>
  )
  return card($, ui, { id: 'running', title: 'Running', summary: r.n > 0 ? `${r.n} running` : null, folds, icon: 'activity', look }, <Box flexDirection="column" rowGap={1}>{viewing ? buttonRow(ui, [<Button key="view-main" label="Back to main chat" variant="secondary" onPress={() => backToMain($)} />]) : null}{[...r.agents, ...r.tasks, ...r.crons].map(row)}</Box>)
}
