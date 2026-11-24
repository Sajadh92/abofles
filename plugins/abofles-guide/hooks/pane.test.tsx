import { expect, mock, test } from 'claude-code/testing'
import { adoptMirror, applyPlan, planBlock, planState } from './tasks'
import type { PlanStatus } from '../types'
import type { OpEventResult } from 'claude-code'
import { checkRelease, newer, releasesOf, updatePrompt, versionView } from './version'

import { fmtWhen, headParts, receiptOf, spendOf } from './pane'
import { spendCard } from './html'
import { rawBase } from './editor'
import { QUIET_MS, canStop, fmtElapsed, isQuiet, liveOf, loopQuietSince, toolLabel, withSizes } from './running'
import { cleanClose, cleanPane, COLOR_PAIRS, CONTINUE_PROMPT, shortHash } from './shared'
import { closeFact, closeFill, closeView, whenOf } from './close'

const SURFACES = ['terminal', 'desktop'] as const
const NOW = 1_700_000_000 // epoch seconds the mocked clock stands at
const hhmm = (ts: number): string => `${String(new Date(ts * 1000).getHours()).padStart(2, '0')}:${String(new Date(ts * 1000).getMinutes()).padStart(2, '0')}` // local time, as the card draws a live start

const PANE_PROPS = { title: 'Abo Fles', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} } as const
const BAND_PROPS = { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 4, bodyColumns: 46, scroll: { offset: 0, bodyRows: 4 }, view: {} } as const
const RUN = { args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 100 } } as const

type It = { p: string; text: string; fill: string; recheck?: string | null; id?: string }
const item = (over: Partial<It> = {}): It => ({ p: '🟡', id: 'esc:a', text: 'escalate it', fill: 'do it', recheck: null, ...over })
const guideOf = (items: It[], more: object = {}) => ({ line: `🧭 ${items.map(i => `${i.p} ${i.text}`).join(' · ')}`, ts: 1, root: '/proj', items, ctx: null, task: null, main: null, ...more })

const PLACES = { guide: 'pane', resume: 'pane', spawns: 'pane', running: 'pane' }
const ROWS = [
  { ts: NOW - 900, side: 'Read', model: 'haiku', effort: 'low', topic: 'T1', tier: 'T1', status: 'done', confidence: 'high', tokens: 12000, usd: 0.01, base_usd: 0.1, description: 'read the docs', verdict: null, task: 'Fix login' },
  { ts: NOW - 600, side: 'Work', model: 'sonnet', effort: 'medium', topic: 'T2', tier: 'T2', status: 'done', confidence: 'medium', tokens: 80000, usd: 0.4, base_usd: 0.8, description: 'patch it', verdict: 'PASS', task: 'Fix login' },
  { ts: NOW - 300, side: 'Verify', model: 'opus', effort: 'high', topic: 'T3', tier: 'T3', status: 'done', confidence: 'low', tokens: null, usd: null, base_usd: null, description: 'check it', verdict: null, task: null },
]
const panePayload = (over: object = {}) => ({
  v: 1,
  ts: 1,
  root: '/proj',
  sid: 'sid-pane',
  places: PLACES,
  resume: { head: '📍 Resume · memo 2026-10-20 09:00 · session sid-pane · now written 2026-10-20 09:00 from receipt', now: ['wiring the pane'], left: ['tests'], next: ['ship it'], needs: [{ n: 1, text: 'pick the cap' }, { n: 3, text: 'name the key' }], needs_open: 2, last: ['wrote cards'] },
  spawns: {
    live: [{ side: 'Work', model: 'sonnet', effort: 'high', description: 'build the card', ts: NOW - 125 }],
    rows: ROWS,
    totals: { spawns: 3, tokens: 92000, usd: 0.41, base_usd: 0.9, saved_pct: 54, open_fail: 1, base_label: 'Opus 5.5', by_model: [{ model: 'haiku', label: 'Haiku 5.5', spawns: 1, tokens: 2000, usd: 0.01, pct: 2 }, { model: 'sonnet', label: 'Sonnet 5.5', spawns: 1, tokens: 90000, usd: 0.4, pct: 98 }, { model: 'opus', label: 'Opus 5.5', spawns: 1, usd: 0, pct: 0 }] },
  },
  overrides: {
    files: { project: '/proj/CLAUDE.md', user: '/home/.claude/CLAUDE.md' },
    keys: [
      { key: 'Receipt', kind: 'select', options: ['full', 'short'], value: 'short', source: 'project', default: 'full' },
      { key: 'Parallel', kind: 'number', options: [], value: '3', source: 'default', default: '3' },
    ],
    raw: [{ line: 'Topic: Billing = T3', source: 'user' }],
  },
  cmd: { overrides: ['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/overrides.py', '--session', 'sid-pane'] },
  ...over,
})

// One place that answers the state file, the pane calls, the process, the prompt box and the clipboard.
// `w.state` is the whole state file (null: none); `w.canPlace` whether an unasked open is drawn (a narrow terminal: not).
async function harness($: any, on: any, state: object | null, options: { canPlace?: boolean; store?: Record<string, unknown>; start?: boolean } = {}) {
  const clock = mock.clock(on, { now: NOW * 1000 })
  mock.store(on, options.store ?? {})
  mock.env(on, { ABOFLES_STATE_DIR: '/state', HOME: '/home' })
  const w = {
    clock,
    state: state as object | null,
    files: {} as Record<string, string>,
    canPlace: options.canPlace ?? true,
    open: [] as { id: string; isPlaced: boolean }[],
    opens: [] as { title?: string }[],
    closes: [] as string[],
    registered: [] as string[],
    toasts: [] as string[],
    fills: [] as { text: string; mode: string }[],
    copies: [] as string[],
    runs: [] as { argv: string[]; cwd?: string }[],
    stdins: [] as (string | undefined)[], // what each run was given on stdin
    exits: [] as number[],
    stdouts: [] as string[], // the next runs' stdout (default: one line)
    stderrs: [] as string[],
    afterRuns: [] as (() => void)[], // run after the next process runs, in order: the script has rewritten the state file
    duringRun: null as (() => Promise<void>) | null, // runs while a process is "running"
  }
  on('session.id', () => ({ value: 'sid-pane' }))
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd }))
  on('turn.complete', ($: any, e: any) => ({ text: e.answer }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', ($: any, e: any) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  on('command.register', ($: any, e: any) => {
    w.registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('fs.read', ($: any, e: any) => (String(e.path).endsWith('.output') ? (e.path in w.files ? { value: w.files[e.path] } : { deny: 'too large' }) : w.state === null ? { deny: 'no such file' } : { value: JSON.stringify(w.state) }))
  on('ui.open', ($: any, e: any) => {
    w.opens.push({ title: e.title })
    const isPlaced = w.canPlace
    w.open = [...w.open.filter(p => p.id !== e.id), { id: e.id, isPlaced }]
    return { value: isPlaced ? { isPlaced: true } : { isPlaced: false, reason: 'narrow' } } as never
  })
  on('ui.panes', () => ({ value: w.open.map(p => ({ id: p.id, title: 'Abo Fles', isShown: p.isPlaced, isFocused: false, isPlaced: p.isPlaced })) }))
  on('ui.close', ($: any, e: any) => {
    w.closes.push(`${e.id}:${e.origin.kind}`)
    w.open = w.open.filter(p => p.id !== e.id)
    return { value: undefined }
  })
  on('process.run', async ($: any, e: any) => {
    w.runs.push({ argv: e.argv, cwd: e.init?.cwd })
    w.stdins.push(e.init?.stdin)
    await w.duringRun?.()
    w.afterRuns.shift()?.()
    return { value: { exitCode: w.exits.shift() ?? 0, stdout: w.stdouts.shift() ?? 'undone line\n', stderr: w.stderrs.shift() ?? '' } } as never
  })
  on('prompt.read', () => ({ value: { text: '', cursor: 0 } }))
  on('prompt.fill', ($: any, e: any) => {
    w.fills.push({ text: e.text, mode: e.mode })
    return { isFilled: true }
  })
  on('ui.copy', ($: any, e: any) => {
    w.copies.push(e.text)
    return { value: { isCopied: true } }
  })
  on('ui.render', { component: 'AbovePrompt' }, ($: any, e: any) => {
    const { Text } = $.ui.resolve(e)
    return <Text key="engine-own"> </Text>
  })
  if (options.start !== false) await $.session.start({ cwd: '/home', surface: 'desktop', isInteractive: true })
  return w
}

// The desktop shows one page at a time under its nav bar (Home, Guide, Resume, Decisions, Spawns, Overrides). So the tests read the
// pane as a person who knows where things are: a lookup tries the page shown, then each page of the nav bar, and stays on the page
// that has it (a press that follows lands there); found nowhere, it goes back to the page it started on. findAll gathers every page.
const mountRaw = async ($: any, surface: string, props: object = {}) => {
  const ui = await $.ui.mount({ plugin: 'abofles-guide', surface, component: 'Pane', requestId: 'abofles', props: { ...PANE_PROPS, ...props } })
  const find = ui.find.bind(ui)
  const findAll = ui.findAll.bind(ui)
  const press = ui.press.bind(ui)
  const navs = async (): Promise<{ key: string; on: boolean }[]> =>
    (await findAll({ type: 'Button' })).filter((b: any) => /^nav:/.test(String(b.props.key ?? ''))).map((b: any) => ({ key: String(b.props.key), on: !b.props.dimColor }))
  const visit = async <T,>(each: () => Promise<T | undefined>, all: boolean): Promise<T[]> => {
    const first = await each()
    if (first !== undefined && !all) return [first]
    const out: T[] = first === undefined ? [] : [first]
    const tabs = await navs()
    const start = tabs.find(t => t.on)?.key
    for (const t of tabs) {
      if (t.key === start) continue
      await press({ key: t.key })
      const r = await each()
      if (r !== undefined) {
        out.push(r)
        if (!all) return out
      }
    }
    if (start && tabs.length) await press({ key: start })
    return out
  }
  const over: Record<string, unknown> = {}
  over.find = async (q: any) => (await visit(() => find(q), false))[0]
  over.findAll = async (q: any) => (await visit(async () => { const r = await findAll(q); return r.length ? r : undefined }, true)).flat()
  for (const verb of ['press', 'input', 'select'] as const) {
    const orig = ui[verb].bind(ui)
    over[verb] = async (a: any) => {
      if (a?.key && (await find({ key: a.key })) === undefined) await (over.find as (q: object) => Promise<unknown>)({ key: a.key })
      return orig(a)
    }
  }
  // the mounted handle is frozen: a stand-in answers with the seeking verbs and passes the rest through
  return new Proxy({}, { get: (_t, k: string) => (k in over ? over[k] : typeof ui[k] === 'function' ? ui[k].bind(ui) : ui[k]) }) as any
}
// The sections start folded (Guide open). Most tests look inside them, so this opens each folded section the way a person does: a press on its header.
const SECTIONS = ['guide', 'resume', 'spawns', 'decisions', 'overrides']
const mountPane = async ($: any, surface: string, props: object = {}) => {
  const ui = await mountRaw($, surface, props)
  for (const id of SECTIONS) if ((await labelOf(ui, `fold-caret:${id}`)) === '▸') await ui.press({ key: `fold:${id}` })
  return ui
}
const mountBand = ($: any, surface: string) => $.ui.mount({ plugin: 'abofles-guide', surface, component: 'AbovePrompt', props: BAND_PROPS })
// a Text, or a Button's label (a desktop draws a Button as its label: the section titles, the carets, the links)
// (or a line of an HTML card's alt: the card draws inside an Svg, its alt carries every text, one per line)
// a desktop button's label leads with its glyph (shared.tsx GLYPH): the tests read the words after it
const bare = (label: unknown): string => String(label ?? '').replace(/^[✓↵↻⧉⤓↓↑↶✎↺⋯≡] /, '')
const said = async (ui: any, re: RegExp) =>
  (await ui.find({ type: 'Text', text: re })) !== undefined ||
  (await ui.findAll({ type: 'Button' })).some((b: any) => re.test(bare(b.props.label))) ||
  (await ui.findAll({ type: 'Svg' })).some((x: any) => String(x.props.alt ?? '').split('\n').some(l => re.test(l)))
const has = async (ui: any, key: string) => (await ui.find({ key })) !== undefined
const valueOf = async (ui: any, key: string) => (await ui.find({ key }))?.props.value
const propOf = async (ui: any, key: string, prop: string) => (await ui.find({ key }))?.props[prop]
const labelOf = async (ui: any, key: string) => { const l = await propOf(ui, key, 'label'); return l === undefined ? undefined : bare(l) }
// every line of every HTML card's alt, in order
const altLines = async (ui: any): Promise<string[]> => (await ui.findAll({ type: 'Svg' })).flatMap((x: any) => String(x.props.alt ?? '').split('\n'))
// the Read more / Show less link run inside a cut paragraph's Button (it has no label: a desktop would draw the label alone)
const moreOf = async (ui: any, key: string) => { const t = JSON.stringify((await ui.find({ key }))?.props ?? {}); return t.includes('Show less') ? 'Show less' : t.includes('Read more') ? 'Read more' : undefined }

test('every card draws from the state file, on every surface', async ($, on) => {
  await harness($, on, { guide: guideOf([item({ p: '🔴', text: 'open FAIL', id: 'fail:a' }), item({ text: 'then escalate' })], { ctx: { tokens: 412000, mark: 500 }, task: { spawns: 3, usd: 0.41, budget: 5, left: 2 } }), pane: panePayload() })
  for (const surface of SURFACES) {
    const ui = await mountPane($, surface)
    // Guide: the top item after a dot in its tone (no emoji), the rest dim, the numbers as label/value rows, the buttons
    expect([String(/^open FAIL$/), await said(ui, /^open FAIL$/)]).toEqual([String(/^open FAIL$/), true])
    expect([String(/^then escalate$/), await said(ui, /^then escalate$/)]).toEqual([String(/^then escalate$/), true])
    expect([String(/🔴|🟡/), await said(ui, /🔴|🟡/)]).toEqual([String(/🔴|🟡/), false])
    for (const re of [/^Context$/, /^412K \/ 500K$/, /^This task$/, /^3 agents · \$0\.41$/, /^Agent budget$/, /^2 of 5 left$/]) expect([String(re), await said(ui, re)]).toEqual([String(re), true])
    // the meters and rules: SVG on the desktop (exact width, never wrapping), a row of ━ / ─ in the terminal (exact cells)
    if (surface === 'desktop') {
      const svgs = await ui.findAll({ type: 'Svg' })
      // the Guide is one HTML card: as wide as the slot (4096 capped), its height fixed, its alt every text
      const guide = svgs.find((x: any) => /^Next up, urgent priority\n/.test(x.props.alt) && /Context 82% of the next mark/.test(x.props.alt)) // the Guide page's card (Home has its own, shorter)
      expect(guide?.props.width).toBe(4096)
      expect(guide?.props.source).toContain('<foreignObject')
      expect(String(guide?.props.alt).split('\n')).toContain('Context 82% of the next mark')
      expect(await altLines(ui)).toContain('Spend by model: Haiku 5.5 2%, Sonnet 5.5 98%, Opus 5.5 0%')
      for (const t of ['Guide', 'Resume', 'Agents', 'Overrides']) expect([t, svgs.some((x: any) => String(x.props.alt).split('\n')[0] === t)]).toEqual([t, true]) // each section's header strip
      expect(svgs.filter((x: any) => !(x.props.height === undefined && /viewBox="0 0 \d+ \d+"/.test(x.props.source))).map((x: any) => x.props.alt)).toEqual([]) // laid out at the pane's width, scaled keeping the ratio
      expect([String(/^[━─]+$/), await said(ui, /^[━─]+$/)]).toEqual([String(/^[━─]+$/), false])
    } else {
      expect([String(/^━+$/), await said(ui, /^━+$/)]).toEqual([String(/^━+$/), true])
      expect([String(/^─+$/), await said(ui, /^─+$/)]).toEqual([String(/^─+$/), true])
    }
    for (const key of ['use', 'copy']) expect(await has(ui, key)).toBe(true)
    expect(await has(ui, 'hide')).toBe(false) // no Hide: every action is a button
    expect(await propOf(ui, 'copy', 'variant')).toBe('secondary')
    // Resume: Now, Next, Needs you (numbers in their own column), Left, Last receipts, then the facts of the head
    expect([String(/wiring the pane/), await said(ui, /wiring the pane/)]).toEqual([String(/wiring the pane/), true])
    expect([String(/^pick the cap$/), await said(ui, /^pick the cap$/)]).toEqual([String(/^pick the cap$/), true])
    expect([String(/^1$/), await said(ui, /^1$/)]).toEqual([String(/^1$/), true])
    expect([String(/^name the key$/), await said(ui, /^name the key$/)]).toEqual([String(/^name the key$/), true])
    expect([String(/wrote cards/), await said(ui, /wrote cards/)]).toEqual([String(/wrote cards/), true])
    for (const re of [/^Session$/, /^sid-pane$/, /^Memo saved$/, /^Oct 20, 09:00$/]) expect([String(re), await said(ui, re)]).toEqual([String(re), true])
    expect([String(/now written/), await said(ui, /now written/)]).toEqual([String(/now written/), false]) // the noisy part of the head is left out
    // Spawns: the saving first, the split by model, then Running and History (chip, ask, cost, dim detail), newest first
    for (const re of [/^54% saved$/, /^\$0\.41 of \$0\.90 on Opus 5\.5$/, /^Haiku 5\.5(: .*)?$/, /^Sonnet 5\.5(: .*)?$/, /(^|, )98%( of spend)?$/, /^Tokens$/, /^92K$/, /^Open FAIL$/]) expect([String(re), await said(ui, re)]).toEqual([String(re), true])
    if (surface === 'terminal') expect([String(/^■$/), await said(ui, /^■$/)]).toEqual([String(/^■$/), true]) // the model swatches (the desktop draws them in its card)
    expect([String(/^3 agents · \$0\.41$/), await said(ui, /^3 agents · \$0\.41$/)]).toEqual([String(/^3 agents · \$0\.41$/), true]) // the card's summary
    expect([String(/Fix login/), await said(ui, /Fix login/)]).toEqual([String(/Fix login/), false]) // a spawn's prompt line is no heading
    expect([String(/^read the docs$/), await said(ui, /^read the docs$/)]).toEqual([String(/^read the docs$/), true])
    expect([String(new RegExp(`^${hhmm(NOW - 900)} · Haiku 5\\.5 · Low · 12K tokens$`)), await said(ui, new RegExp(`^${hhmm(NOW - 900)} · Haiku 5\\.5 · Low · 12K tokens$`))]).toEqual([String(new RegExp(`^${hhmm(NOW - 900)} · Haiku 5\\.5 · Low · 12K tokens$`)), true])
    expect([String(/^\$0\.01$/), await said(ui, /^\$0\.01$/)]).toEqual([String(/^\$0\.01$/), true])
    expect([String(/^patch it$/), await said(ui, /^patch it$/)]).toEqual([String(/^patch it$/), true])
    expect([String(/Sonnet 5\.5 · Medium · 80K tokens · PASS$/), await said(ui, /Sonnet 5\.5 · Medium · 80K tokens · PASS$/)]).toEqual([String(/Sonnet 5\.5 · Medium · 80K tokens · PASS$/), true])
    expect([String(/^check it$/), await said(ui, /^check it$/)]).toEqual([String(/^check it$/), true])
    expect([String(/Opus 5\.5 · High$/), await said(ui, /Opus 5\.5 · High$/)]).toEqual([String(/Opus 5\.5 · High$/), true]) // no tokens, plain done: no status word
    expect([String(/^DONE$/), await said(ui, /^DONE$/)]).toEqual([String(/^DONE$/), false])
    expect([String(/^Updated \d\d:\d\d · refreshes when a turn ends$/), await said(ui, /^Updated \d\d:\d\d · refreshes when a turn ends$/)]).toEqual([String(/^Updated \d\d:\d\d · refreshes when a turn ends$/), true])
    const asks = /^(read the docs|patch it|check it)$/
    const rows = surface === 'terminal' ? (await ui.findAll({ type: 'Text', text: asks })).map((r: any) => r.text) : (await altLines(ui)).filter(l => asks.test(l))
    expect(rows[0]).toBe('check it') // newest first
    expect(rows[rows.length - 1]).toBe('read the docs')
    expect([String(/^Work$/), await said(ui, /^Work$/)]).toEqual([String(/^Work$/), true]) // the side, semibold, before its ask
    // Overrides: a short card on the main page (a table of what a file sets, the other lines), the editor one press away
    expect([String(/^Overrides$/), await said(ui, /^Overrides$/)]).toEqual([String(/^Overrides$/), true])
    for (const re of [/^Receipt$/, /^short$/, /^this project$/i, /^1 set$/]) expect([String(re), await said(ui, re)]).toEqual([String(re), true])
    expect([String(/^Parallel$/), await said(ui, /^Parallel$/)]).toEqual([String(/^Parallel$/), false]) // a default is not listed
    expect(await labelOf(ui, 'edit-other')).toMatch(/^1 other line · tiers, reviewers/)
    expect(await labelOf(ui, 'edit-overrides')).toBe('Edit')
    expect(await has(ui, 'sel:Receipt')).toBe(false)
    for (const key of surface === 'terminal' ? ['fold:guide', 'fold:resume', 'fold:spawns', 'fold:overrides', 'edit-overrides'] : ['edit-overrides']) expect(await has(ui, key)).toBe(true)
    // the editor page: ‹ Overrides, the scope buttons, settings rows (name and help on the left, the control on the right), the buttons
    await ui.press({ key: 'edit-overrides' })
    expect(await has(ui, 'use')).toBe(false) // the cards make room for the page
    expect(await labelOf(ui, 'back')).toBe('‹ Overrides')
    expect(await said(ui, /^A project value wins over an all-projects value\.$/)).toBe(true)
    expect([String(/^Receipt$/), await said(ui, /^Receipt$/)]).toEqual([String(/^Receipt$/), true]) // the name beside the control
    expect(await labelOf(ui, 'sel:Receipt')).toBeUndefined()
    expect(await valueOf(ui, 'sel:Receipt')).toBe('short')
    expect(await valueOf(ui, 'in:Parallel')).toBe('3')
    expect([String(/^Receipt form; savings N = only past N%$/), await said(ui, /^Receipt form; savings N = only past N%$/)]).toEqual([String(/^Receipt form; savings N = only past N%$/), true]) // the help, and the file that sets it
    expect([String(/^Background agents at once$/), await said(ui, /^Background agents at once$/)]).toEqual([String(/^Background agents at once$/), true]) // a default: the help alone
    expect(await has(ui, 'raw')).toBe(false) // Other overrides: folded while this file has none of its lines
    expect([String(/^1 in all projects$/), await said(ui, /^1 in all projects$/)]).toEqual([String(/^1 in all projects$/), true]) // its summary
    await ui.press({ key: 'fold:g:Other' })
    expect(await propOf(ui, 'scope:project', 'label')).toBe('✓ This project') // the free-form lines keep one target of their own
    expect(await propOf(ui, 'scope:user', 'label')).toBe('All projects')
    expect(await said(ui, /^Topic: Billing = T3 — in all projects; switch above to edit it$/)).toBe(true)
    for (const key of ['back', 'save', 'ask', 'raw', 'fold:g:Agents', 'fold:g:Output', 'fold:g:Other']) expect(await has(ui, key)).toBe(true)
    expect(await labelOf(ui, 'ask')).toBe('Ask Claude')
    expect(await has(ui, 'reset')).toBe(false) // nothing to discard
    await ui.press({ key: 'fold:g:Other' }) // as it was, for the next surface
    await ui.press({ key: 'back' })
    expect(await has(ui, 'use')).toBe(true) // the cards are back
    await ui.unmount()
  }
})

test('empty cards say so: no memo yet, no agents yet', async ($, on) => {
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: null, spawns: { live: [], rows: [], totals: { spawns: 0, tokens: 0, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) })
  for (const surface of SURFACES) {
    const ui = await mountPane($, surface)
    expect(await said(ui, /^No memo yet — it starts at the first receipt$/)).toBe(true)
    expect(await said(ui, /No agents yet/)).toBe(true)
    await ui.unmount()
  }
})

test('a place other than the pane hides its card; Overrides always draws', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ places: { guide: 'band', resume: 'output', spawns: 'output' } }) })
  for (const surface of SURFACES) {
    const ui = await mountPane($, surface)
    expect(await said(ui, /Guide|Resume/)).toBe(false)
    expect(await said(ui, /No agents yet|^3 agents/)).toBe(false)
    expect(await has(ui, 'use')).toBe(false)
    expect(await said(ui, /^Overrides$/)).toBe(true)
    await ui.unmount()
  }
  w.state = { guide: guideOf([item()]), pane: panePayload({ places: { guide: 'pane', resume: 'output', spawns: 'pane' } }) }
  await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answered' } as never)
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /^Guide$/)).toBe(true)
  expect(await said(ui, /^Resume$/)).toBe(false)
  expect(await said(ui, /^Agents$/)).toBe(true)
  await ui.unmount()
})

test('a state file from older hooks (no pane): the pane has the Overrides card only and the band is today\'s', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]) })
  const band = await mountBand($, 'desktop')
  for (const key of ['use', 'copy', 'hide']) expect(await has(band, key)).toBe(true)
  expect(await has(band, 'pane')).toBe(false)
  await band.unmount()
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /^Guide$|^Resume$|^Agents$/)).toBe(false)
  expect(await said(ui, /^Pane data unreadable — update both abofles and abofles-guide$/)).toBe(true)
  await ui.unmount()
  expect(w.opens).toEqual([]) // nothing opened unasked
})

test('malformed parts of the pane payload are dropped, the rest is drawn', async ($, on) => {
  const pane = panePayload({
    resume: { head: 5, now: 'x', left: [1, 'ok'], next: null, needs: [{ n: 'a', text: 7 }, { n: 2, text: 'fine' }], last: [] },
    spawns: { live: [3, { side: 'Work' }], rows: ['x', { status: 'done', tokens: 'many' }, null], totals: 'no' },
    overrides: { keys: [{ key: 9 }, { key: 'Receipt', value: 'short' }], raw: 'no' },
    cmd: 'no',
  })
  await harness($, on, { guide: guideOf([item()]), pane })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /^ok$/)).toBe(true)
  expect(await said(ui, /^fine$/)).toBe(true)
  expect(await said(ui, /^\?$/)).toBe(true) // no model, no tokens, no start: the meta line is a bare ?
  await ui.press({ key: 'edit-overrides' })
  expect(await has(ui, 'in:Receipt')).toBe(true) // no kind: a text Input
  await ui.unmount()
})

test('a pane payload of another version is not used: nothing opens, the band is today\'s', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ v: 2 }) })
  expect(w.opens).toEqual([])
  const band = await mountBand($, 'desktop')
  expect(await has(band, 'use')).toBe(true)
  expect(await has(band, 'pane')).toBe(false)
  await band.unmount()
})

test('Use, Copy, Re-check, Show and Undo work inside the pane (no Hide)', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item({ recheck: 'run it again' })]), pane: panePayload() })
  for (const surface of SURFACES) {
    w.fills.length = 0
    w.copies.length = 0
    w.state = { guide: guideOf([item({ id: `first:${surface}`, recheck: 'run it again' })]), pane: panePayload() } // a guide not hidden before
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `t0-${surface}`, reason: 'answered' } as never)
    const ui = await mountPane($, surface)
    await ui.press({ key: 'copy' })
    expect(w.copies).toEqual(['do it'])
    await ui.press({ key: 'recheck' })
    expect(w.fills).toEqual([{ text: 'run it again', mode: 'replace' }])
    expect(await has(ui, 'use')).toBe(false) // a fill hides the guide, as the band does
    expect(await said(ui, /Hidden until the list changes/)).toBe(true)
    await ui.unmount()
    w.state = { guide: guideOf([item({ id: `esc:${surface}` })]), pane: panePayload() } // another item: the guide is back
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `t-${surface}`, reason: 'answered' } as never)
    const again = await mountPane($, surface)
    await again.press({ key: 'use' })
    expect(w.fills[w.fills.length - 1]).toEqual({ text: 'do it', mode: 'replace' })
    await again.unmount()
  }
  // no Hide button: a fill hides the card (as the band does), and Show brings it back
  w.state = { guide: guideOf([item({ id: 'esc:hide', recheck: 'again' })]), pane: panePayload() }
  await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: 't-hide', reason: 'answered' } as never)
  const ui = await mountPane($, 'desktop')
  expect(await has(ui, 'hide')).toBe(false)
  await ui.press({ key: 'recheck' })
  expect(await has(ui, 'use')).toBe(false)
  expect(await propOf(ui, 'show', 'variant')).toBe('secondary') // the hidden card offers its way back, as a button
  await ui.press({ key: 'show' })
  expect(await has(ui, 'use')).toBe(true)
  expect(await has(ui, 'show')).toBe(false)
  expect(await said(ui, /Hidden until the list changes/)).toBe(false)
  await ui.unmount()
  // Undo: the row of Claude's last resolve, with the hook's argv
  w.state = { guide: guideOf([item({ id: 'esc:undo' })], { last_resolved: { text: 'FAIL cleared', unresolve: ['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/status.py', '--unresolve', 'a1'], ts: 7 } }), pane: panePayload() }
  await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: 't-undo', reason: 'answered' } as never)
  const un = await mountPane($, 'desktop')
  expect(await said(un, /✓ resolved: FAIL cleared/)).toBe(true)
  await un.press({ key: 'undo' })
  expect(w.runs).toEqual([{ argv: ['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/status.py', '--unresolve', 'a1'], cwd: '/proj' }])
  expect(await said(un, /✓ resolved/)).toBe(false)
  await un.unmount()
})

test('/abofles:pane and /abofles-pane open the pane, then close it', async ($, on) => {
  // the guide is in the band here, so the session start opens nothing on its own
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ places: { guide: 'band', resume: 'output', spawns: 'output' } }) })
  expect(w.registered).toEqual(['abofles-pane'])
  expect(w.opens).toEqual([])
  const first = await $.command.run({ command: 'abofles:pane', ...RUN } as never)
  expect(first.text).toBe('Abo Fles pane opened.')
  expect(w.opens).toEqual([{ title: 'Abo Fles' }])
  const second = await $.command.run({ command: 'abofles:pane', ...RUN } as never)
  expect(second.text).toBe('Abo Fles pane closed.')
  expect(w.closes).toEqual(['abofles:plugin'])
  const third = await $.command.run({ command: 'abofles-pane', ...RUN } as never)
  expect(third.text).toBe('Abo Fles pane opened.')
  expect(w.opens.length).toBe(2)
})

test('the session start opens the pane unasked, unless an earlier session left it closed', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload() }, { store: {} })
  expect(w.opens).toEqual([{ title: 'Abo Fles' }])
})

test('closed by the person in an earlier session (the store says false): the session start leaves it closed', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload() }, { store: { paneOpen: false } })
  expect(w.opens).toEqual([])
  // not drawn, so the guide's opener is in the band
  const band = await mountBand($, 'desktop')
  expect(await has(band, 'pane')).toBe(true)
  await band.press({ key: 'pane' })
  expect(w.opens).toEqual([{ title: 'Abo Fles' }])
  await band.unmount()
})

test('a pane that is not placed: the band shows the opener, and its button opens the pane', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item({ p: '🔴', text: 'open FAIL', id: 'fail:a' })]), pane: panePayload({ places: { guide: 'band', resume: 'pane', spawns: 'output' } }) }, { canPlace: false })
  expect(w.opens.length).toBe(1) // the unasked open, not drawn
  const band = await mountBand($, 'desktop')
  // the guide stays in the band, with the Pane button beside its own
  for (const key of ['use', 'copy', 'hide', 'pane']) expect(await has(band, key)).toBe(true)
  w.canPlace = true
  await band.press({ key: 'pane' })
  expect(w.opens.length).toBe(2)
  expect(w.open).toEqual([{ id: 'abofles', isPlaced: true }])
  await band.unmount()
})

test('Guide place pane: the band is one opener row while the pane is not up, and empty while it is', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item({ p: '🔴', text: 'open FAIL', id: 'fail:a' }), item()]), pane: panePayload() }, { canPlace: false })
  for (const surface of SURFACES) {
    const band = await mountBand($, surface)
    expect(await said(band, /^Abo Fles · 🔴 open FAIL$/)).toBe(true)
    expect(await has(band, 'pane')).toBe(true)
    for (const key of ['use', 'copy', 'hide', 'recheck']) expect(await has(band, key)).toBe(false) // the guide lives in the pane
    await band.unmount()
  }
  // the Pane button opens it as asked; the band then goes empty
  w.canPlace = true
  const band = await mountBand($, 'desktop')
  await band.press({ key: 'pane' })
  expect(w.open).toEqual([{ id: 'abofles', isPlaced: true }])
  expect(await has(band, 'pane')).toBe(false)
  expect(await said(band, /Abo Fles/)).toBe(false)
  await band.unmount()
})

test('Guide place pane with the pane closed by the command: the opener row comes back', async ($, on) => {
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const band = await mountBand($, 'desktop')
  expect(await has(band, 'pane')).toBe(false) // up
  await $.command.run({ command: 'abofles:pane', ...RUN } as never) // closes
  expect(await has(band, 'pane')).toBe(true)
  await band.unmount()
})

test('the classic PostCompact refreshes the pane: the context number drops without waiting for a reply', async ($, on) => {
  on('classic.PostCompact', () => ({}))
  const w = await harness($, on, { guide: { ...guideOf([item()]), ctx: { tokens: 462000, mark: 500, auto: true } }, pane: panePayload() })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /462K \/ 500K/)).toBe(true)
  w.state = { guide: { ...guideOf([item()]), ctx: { tokens: 33000, mark: 500, auto: true } }, pane: panePayload() }
  await $.classic.PostCompact({ trigger: 'manual', compact_summary: 'x' } as never)
  expect(await said(ui, /33K \/ 500K/)).toBe(true)
  expect(await said(ui, /462K/)).toBe(false)
  await ui.unmount()
})

// The transcript a compact runs over: a test has no core to hand it in.
const COMPACT_MESSAGES = [{ role: 'user' as const, text: 'summary', toolUses: [] }]
const compactNow = ($: any, more: object = {}) => $.session.compact({ messages: COMPACT_MESSAGES, ...more })

test('after a compact the Context row stays: the engine\'s size until the hooks can read one, whichever fires first', async ($, on) => {
  on('classic.PostCompact', () => ({}))
  on('session.compact', () => ({ messages: COMPACT_MESSAGES, tokensAfter: 34076 }))
  const wait = { ctx_wait: { marks: [500, 600], auto: true } }
  const w = await harness($, on, { guide: { ...guideOf([item()]), ctx: { tokens: 219000, mark: 500, auto: true } }, pane: panePayload() })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /219K \/ 500K/)).toBe(true)
  // PostCompact first: the hooks have no size and the engine has not answered yet
  w.state = { guide: { ...guideOf([item()]), ...wait }, pane: panePayload() }
  await $.classic.PostCompact({ trigger: 'manual', compact_summary: 'x' } as never)
  expect(await said(ui, /^Compacted$/)).toBe(true)
  expect(await said(ui, /219K/)).toBe(false)
  // the engine answers
  await compactNow($)
  expect(await said(ui, /34K \/ 500K/)).toBe(true)
  expect(await said(ui, /Compacted/)).toBe(false)
  // the hooks read the real number on the next prompt: it wins
  w.state = { guide: { ...guideOf([item()]), ctx: { tokens: 112000, mark: 500, auto: true }, ctx_wait: null }, pane: panePayload() }
  await $.classic.PostCompact({ trigger: 'manual', compact_summary: 'x' } as never)
  expect(await said(ui, /112K \/ 500K/)).toBe(true)
  await ui.unmount()
})

test('a compact with no recorded size shows Compacted, a size past a mark takes the next mark, and a subagent\'s compact changes nothing', async ($, on) => {
  let after: number | undefined
  let runs = 0
  on('session.compact', () => (runs++, { messages: COMPACT_MESSAGES, tokensAfter: after }))
  const wait = { guide: { ...guideOf([item()]), ctx_wait: { marks: [500, 600], auto: true } }, pane: panePayload() }
  await harness($, on, wait)
  const ui = await mountPane($, 'desktop')
  await compactNow($)
  expect(await said(ui, /^Compacted$/)).toBe(true)
  after = 520000
  await compactNow($)
  expect(await said(ui, /520K \/ 600K/)).toBe(true)
  after = 999000
  await compactNow($, { agentId: 'a1' })
  expect(await said(ui, /520K \/ 600K/)).toBe(true)
  expect(runs).toBe(3)
  await ui.unmount()
})

test('after the person\'s /compact one turn picks the work up; not after an automatic compact or a subagent\'s, not with Continue after compact = off, not without abofles state', async ($, on) => {
  const sent: string[] = []
  on('prompt.submit', ($: any, e: any) => (sent.push(e.text), { text: e.text }))
  on('session.compact', () => ({ messages: COMPACT_MESSAGES, tokensAfter: 34000 }))
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload(), compact_continue: true })
  const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
  await compactNow($, { trigger: 'manual' })
  await settle()
  expect(sent).toEqual([CONTINUE_PROMPT])
  expect(CONTINUE_PROMPT).toMatch(/nothing is left to continue, say so/)
  await compactNow($, { trigger: 'auto' })
  await compactNow($, { trigger: 'manual', agentId: 'a1' })
  w.state = { guide: guideOf([item()]), pane: panePayload(), compact_continue: false }
  await compactNow($, { trigger: 'manual' })
  w.state = null
  await compactNow($, { trigger: 'manual' })
  await settle()
  expect(sent.length).toBe(1)
})

test('the classic PostToolUse and SubagentStop refresh the pane, so the agent history updates', async ($, on) => {
  on('classic.PostToolUse', () => ({}))
  on('classic.SubagentStop', () => ({}))
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: [], totals: { spawns: 0, tokens: 0, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /read the docs/)).toBe(false)
  w.state = { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: ROWS.slice(0, 1), totals: { spawns: 1, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) }
  await $.classic.PostToolUse({ tool_name: 'Agent', tool_input: {}, tool_response: {}, tool_use_id: 'u1' } as never)
  expect(await said(ui, /^read the docs$/)).toBe(true)
  w.state = { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: ROWS, totals: { spawns: 3, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) }
  await $.classic.SubagentStop({ stop_hook_active: false, agent_id: 'a1', agent_type: 'general', agent_transcript_path: '', last_assistant_message: '' } as never)
  expect(await said(ui, /^read the docs$/)).toBe(true)
  expect(await said(ui, /^Read$/)).toBe(true)
  await ui.unmount()
})

// ---- the overrides editor

const ovKeys = (more: object[] = []) => [
  { key: 'Receipt', kind: 'select', options: ['full', 'short'], value: 'short', source: 'project', default: 'full' },
  { key: 'Parallel', kind: 'number', options: [], value: '3', source: 'default', default: '3' },
  ...more,
]
const AGENTS = { key: 'Agents', kind: 'select', options: ['ask', 'auto', 'off'], value: 'ask', source: 'default', default: 'ask' }
const ovState = (keys: object[] = ovKeys(), raw: object[] = [{ line: 'Topic: Billing = T3', source: 'user' }]) => ({
  guide: guideOf([item()]),
  pane: panePayload({ overrides: { files: { project: '/proj/CLAUDE.md', user: '/home/.claude/CLAUDE.md' }, keys, raw } }),
})
const FILES = { project: '/proj/CLAUDE.md', user: '/home/.claude/CLAUDE.md' }
const saved = (keys: object[], raw: object[] = []) => JSON.stringify({ files: FILES, keys, raw })
// The next Save: the script prints the table and has rewritten the state file (`places` too), which the mod reads again.
const willSave = (w: any, keys: object[], raw: object[] = [], places: object = PLACES) => {
  w.stdouts.push(saved(keys, raw))
  w.afterRuns.push(() => {
    w.state = { guide: guideOf([item()]), pane: panePayload({ places, overrides: { files: FILES, keys, raw } }) }
  })
}
// The pane on its editor page (the main page's Edit button).
const mountEditor = async ($: any, surface: string, props: object = {}) => {
  const ui = await mountPane($, surface, props)
  if (await has(ui, 'edit-overrides')) await ui.press({ key: 'edit-overrides' })
  return ui
}

for (const surface of SURFACES) {
  test(`${surface}: the Overrides card draws a row per key and Select/Input edits go into the draft, with the count in the title`, async ($, on) => {
    await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'fold:g:Other' }) // folded while the project file has none of its lines
    expect(await propOf(ui, 'scope:project', 'label')).toBe('✓ This project')
    expect(await labelOf(ui, 'scope:project')).toBe('This project')
    expect(await labelOf(ui, 'scope:user')).toBe('All projects')
    // settings rows: the name is a Text beside the control, the control carries no label of its own
    expect(await said(ui, /^Save to$/)).toBe(false) // each setting has its own target now
    for (const re of [/^Parallel$/, /^Receipt$/]) expect(await said(ui, re)).toBe(true)
    expect(await labelOf(ui, 'sel:Receipt')).toBeUndefined()
    expect(await labelOf(ui, 'in:Parallel')).toBeUndefined()
    expect(await propOf(ui, 'raw', 'placeholder')).toBe('Topic: Billing = T3')
    expect(await said(ui, /unsaved/)).toBe(false)
    expect(await propOf(ui, 'save', 'variant')).toBe('secondary') // nothing to save
    expect(await has(ui, 'reset')).toBe(false) // nothing to discard
    const opts = (await ui.find({ key: 'sel:Receipt' }))?.props.options as { value: string; label?: string }[]
    expect(opts.map(o => o.label ?? o.value)).toEqual(['default (full)', 'full', 'short', 'custom value']) // the default named; Receipt also takes `savings N`
    expect(await has(ui, 'sel:Parallel')).toBe(false) // a number is an Input
    expect(await propOf(ui, 'in:Parallel', 'placeholder')).toBe('default 3')
    expect(await labelOf(ui, 'back')).toBe('‹ Overrides') // one label: the desktop drops styled Text inside a Button
    await ui.select({ key: 'sel:Receipt', value: 'full' })
    expect(await valueOf(ui, 'sel:Receipt')).toBe('full')
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    expect(await said(ui, /^1 edited$/)).toBe(true) // the group's summary
    expect(await propOf(ui, 'save', 'variant')).toBe('primary') // something to save
    expect(await labelOf(ui, 'reset')).toBe('Discard')
    expect(await labelOf(ui, 'save')).toBe('Save 1 change')
    expect(await said(ui, /^· edited$/)).toBe(true) // beside Receipt's name
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    expect(await valueOf(ui, 'in:Parallel')).toBe('5')
    expect(await said(ui, /^2 unsaved$/)).toBe(true)
    await ui.select({ key: 'sel:Receipt', value: '@default' }) // "default (full)" removes the line: a change from `short`
    expect(await said(ui, /^2 unsaved$/)).toBe(true)
    await ui.select({ key: 'sel:Receipt', value: 'short' }) // back to what the file has: no change
    await ui.input({ key: 'in:Parallel', text: '3' })
    expect(await said(ui, /unsaved/)).toBe(false)
    expect(await propOf(ui, 'save', 'variant')).toBe('secondary')
    await ui.press({ key: 'back' }) // the main page's card follows the draft
    await ui.press({ key: 'edit-overrides' })
    await ui.unmount()
  })

  test(`${surface}: Save runs the script with the scope and the changes on stdin, toasts, applies the printed table and clears the draft`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.select({ key: 'sel:Receipt', value: 'full' })
    await ui.input({ key: 'in:Parallel', text: ' 5 ', kind: 'change' })
    willSave(w, [{ key: 'Receipt', kind: 'select', options: ['full', 'short'], value: 'full', source: 'project', default: 'full' }, { key: 'Parallel', kind: 'number', options: [], value: '5', source: 'project', default: '3' }])
    await ui.press({ key: 'save' })
    expect(w.runs).toEqual([{ argv: ['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/overrides.py', '--session', 'sid-pane', 'set', '--scope', 'project'], cwd: '/proj' }])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Receipt: 'full', Parallel: '5' } })
    expect(w.toasts).toContain('Saved to CLAUDE.md')
    expect(await said(ui, /unsaved/)).toBe(false) // the draft is clear
    expect(await valueOf(ui, 'in:Parallel')).toBe('5') // the table is the one the script printed
    expect(await said(ui, /^Receipt form; savings N = only past N%$/)).toBe(true) // both keys now set in the project file
    expect(await propOf(ui, 'where:Parallel:project', 'label')).toBe('✓ This project')
    expect(await said(ui, /Topic: Billing/)).toBe(false) // the printed table had no raw lines
    await ui.unmount()
  })

  test(`${surface}: a failed Save toasts the first line of stderr and keeps the draft`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.input({ key: 'in:Parallel', text: 'many', kind: 'change' })
    w.exits.push(2)
    w.stderrs.push('abofles overrides: Parallel: \'many\' is not a whole number\nsecond line')
    await ui.press({ key: 'save' })
    expect(w.toasts).toContain("abofles overrides: Parallel: 'many' is not a whole number")
    expect(w.toasts.some(t => t.startsWith('Saved'))).toBe(false)
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    expect(await valueOf(ui, 'in:Parallel')).toBe('many')
    willSave(w, ovKeys())
    await ui.input({ key: 'in:Parallel', text: '4', kind: 'change' })
    await ui.press({ key: 'save' }) // pressed again: the claim was released
    expect(w.runs.length).toBe(2)
    await ui.unmount()
  })

  test(`${surface}: a Save that finds the file changed meanwhile (exit 3) toasts it and keeps the draft`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    w.exits.push(3)
    w.stderrs.push('abofles: CLAUDE.md changed while saving — reload the pane and save again')
    await ui.press({ key: 'save' })
    expect(w.toasts).toContain('abofles: CLAUDE.md changed while saving — reload the pane and save again')
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    expect(await labelOf(ui, 'save')).toBe('Save 1 change') // free to press again
    await ui.unmount()
  })

  test(`${surface}: Save with nothing changed runs nothing`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'save' })
    expect(w.runs).toEqual([])
    expect(w.toasts.some(t => t === 'Nothing to save')).toBe(true)
    await ui.unmount()
  })

  test(`${surface}: Ask Claude to apply fills the prompt box with the changed keys only, and writes nothing`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'ask' })
    expect(w.fills).toEqual([]) // nothing changed: nothing to ask
    await ui.select({ key: 'sel:Receipt', value: '@default' })
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    await ui.press({ key: 'ask' })
    expect(w.fills).toEqual([{ text: 'Apply in CLAUDE.md `## abofles overrides`: - Receipt removed - Parallel = 5', mode: 'replace' }])
    expect(w.runs).toEqual([])
    expect(await said(ui, /^2 unsaved$/)).toBe(true) // the draft stays for Save
    await ui.press({ key: 'reset' })
    await ui.unmount()
  })

  test(`${surface}: the scope Select picks the target file, and the lines of the forms follow it`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'fold:g:Other' }) // folded while the project file has none of its lines
    expect(await valueOf(ui, 'raw')).toBe('')
    expect(await said(ui, /^Topic: Billing = T3 — in all projects; switch above to edit it$/)).toBe(true)
    await ui.press({ key: 'scope:user' })
    expect(await propOf(ui, 'scope:user', 'label')).toBe('✓ All projects')
    expect(await valueOf(ui, 'raw')).toBe('Topic: Billing = T3')
    expect(await said(ui, /switch above/)).toBe(false)
    expect(await said(ui, /^~\/\.claude\/CLAUDE\.md · one per line/)).toBe(true)
    await ui.input({ key: 'raw', text: 'Topic: Billing = T3\n- Reviewers: FIN = tester\n', kind: 'change' })
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    expect(await said(ui, /^edited$/)).toBe(true) // the Other overrides group's summary
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    await ui.press({ key: 'where:Parallel:user' })
    await ui.press({ key: 'ask' })
    expect(w.fills[0]?.text).toBe('Apply in ~/.claude/CLAUDE.md `## abofles overrides`: - Parallel = 5 - Reviewers: FIN = tester')
    willSave(w, ovKeys(), [{ line: 'Topic: Billing = T3', source: 'user' }, { line: 'Reviewers: FIN = tester', source: 'user' }])
    await ui.press({ key: 'save' })
    expect(w.runs[0]?.argv).toEqual(['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/overrides.py', '--session', 'sid-pane', 'set', '--scope', 'user'])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Parallel: '5' }, raw: ['Topic: Billing = T3', 'Reviewers: FIN = tester'] })
    expect(w.toasts).toContain('Saved to ~/.claude/CLAUDE.md')
    expect(await propOf(ui, 'scope:user', 'label')).toBe('✓ All projects') // the target stays
    expect(await valueOf(ui, 'raw')).toBe('Topic: Billing = T3\nReviewers: FIN = tester')
    await ui.press({ key: 'scope:project' })
    await ui.unmount()
  })

  test(`${surface}: Discard edits clears the draft`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'fold:g:Other' }) // folded while the project file has none of its lines
    await ui.select({ key: 'sel:Receipt', value: 'full' })
    await ui.input({ key: 'in:Parallel', text: '9', kind: 'change' })
    await ui.press({ key: 'scope:user' })
    await ui.input({ key: 'raw', text: 'Topic: X = T1', kind: 'change' })
    expect(await said(ui, /^3 unsaved$/)).toBe(true)
    await ui.press({ key: 'reset' })
    expect(await said(ui, /unsaved/)).toBe(false)
    expect(await has(ui, 'reset')).toBe(false)
    expect(await valueOf(ui, 'sel:Receipt')).toBe('short')
    expect(await valueOf(ui, 'in:Parallel')).toBe('3')
    expect(await valueOf(ui, 'raw')).toBe('Topic: Billing = T3')
    expect(w.runs).toEqual([])
    await ui.press({ key: 'scope:project' })
    await ui.unmount()
  })

  test(`${surface}: Agents and Receipt offer custom value, which opens an Input for N / savings N`, async ($, on) => {
    const w = await harness($, on, ovState(ovKeys([AGENTS])))
    const ui = await mountEditor($, surface)
    const opts = (await ui.find({ key: 'sel:Agents' }))?.props.options as { value: string; label?: string }[]
    expect(opts.map(o => o.label ?? o.value)).toEqual(['default (ask)', 'ask', 'auto', 'off', 'custom value'])
    expect((await ui.find({ key: 'sel:Receipt' }))?.props.options).toHaveLength(4)
    expect(await valueOf(ui, 'sel:Agents')).toBe('@default')
    expect(await has(ui, 'custom:Agents')).toBe(false)
    await ui.select({ key: 'sel:Agents', value: '@custom' })
    expect(await valueOf(ui, 'sel:Agents')).toBe('@custom')
    expect(await has(ui, 'custom:Agents')).toBe(true)
    expect(await valueOf(ui, 'custom:Agents')).toBe('3') // starts with a sensible N
    expect(await propOf(ui, 'custom:Agents', 'placeholder')).toBe('N agents per task') // its hint
    expect(await said(ui, /^N agents per task$/)).toBe(true) // and the same hint beside it
    await ui.input({ key: 'custom:Agents', text: '7', kind: 'change' })
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    await ui.select({ key: 'sel:Receipt', value: '@custom' })
    expect(await valueOf(ui, 'custom:Receipt')).toBe('savings 30')
    await ui.input({ key: 'custom:Receipt', text: 'savings 40', kind: 'change' })
    willSave(w, [{ key: 'Agents', kind: 'select', options: ['ask', 'auto', 'off'], value: '7', source: 'project', default: 'ask' }, { key: 'Receipt', kind: 'select', options: ['full', 'short'], value: 'savings 40', source: 'project', default: 'full' }])
    await ui.press({ key: 'save' })
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Agents: '7', Receipt: 'savings 40' } })
    // a value from the file that is not an option shows as custom, its Input open with the value
    expect(await valueOf(ui, 'sel:Agents')).toBe('@custom')
    expect(await valueOf(ui, 'custom:Agents')).toBe('7')
    expect(await valueOf(ui, 'custom:Receipt')).toBe('savings 40')
    await ui.select({ key: 'sel:Agents', value: 'auto' }) // a plain option closes the Input
    expect(await has(ui, 'custom:Agents')).toBe(false)
    expect(await valueOf(ui, 'sel:Agents')).toBe('auto')
    await ui.unmount()
  })
}

const GUIDE_PLACE = { key: 'Guide place', kind: 'select', options: ['pane', 'band'], value: 'pane', source: 'default', default: 'pane' }

for (const surface of SURFACES) {
  test(`${surface}: switching scope drops the lines typed for the other file, so they are never written to it`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'fold:g:Other' }) // folded while the project file has none of its lines
    await ui.input({ key: 'raw', text: 'Reviewers: FIN = tester', kind: 'change' }) // typed for the project file
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    await ui.press({ key: 'scope:user' })
    expect(await valueOf(ui, 'raw')).toBe('Topic: Billing = T3') // the user file's own lines, not the typed ones
    expect(await said(ui, /unsaved/)).toBe(false)
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    await ui.press({ key: 'where:Parallel:user' })
    willSave(w, ovKeys())
    await ui.press({ key: 'save' })
    expect(w.runs[0]?.argv).toEqual(['bash', '/x/plugins/abofles/scripts/py', '/x/plugins/abofles/scripts/overrides.py', '--session', 'sid-pane', 'set', '--scope', 'user'])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Parallel: '5' } }) // no raw lines
    await ui.press({ key: 'scope:project' })
    await ui.unmount()
  })

  test(`${surface}: while Save runs it reads "Saving…" and cannot be pressed again; after it, "Save"`, async ($, on) => {
    const w = await harness($, on, ovState())
    const ui = await mountEditor($, surface)
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    let during: unknown = null
    w.duringRun = async () => {
      during = await labelOf(ui, 'save')
      await ui.press({ key: 'save' }) // a second press does nothing
    }
    willSave(w, ovKeys())
    await ui.press({ key: 'save' })
    expect(during).toBe('Saving…')
    expect(w.runs.length).toBe(1)
    expect(await labelOf(ui, 'save')).toBe('Save')
    await ui.unmount()
  })

  test(`${surface}: a Save that changes Guide place to band removes the Guide card at once`, async ($, on) => {
    const w = await harness($, on, ovState(ovKeys([GUIDE_PLACE])))
    const ui = await mountPane($, surface)
    expect(await said(ui, /^Guide$/)).toBe(true)
    expect(await has(ui, 'use')).toBe(true)
    await ui.press({ key: 'edit-overrides' })
    expect(await said(ui, /^Where it shows$/)).toBe(true) // the place keys' group
    await ui.select({ key: 'sel:Guide place', value: 'band' })
    willSave(w, ovKeys([{ ...GUIDE_PLACE, value: 'band', source: 'project' }]), [], { ...PLACES, guide: 'band' })
    await ui.press({ key: 'save' })
    await ui.press({ key: 'back' })
    expect(await said(ui, /^Guide$/)).toBe(false)
    expect(await has(ui, 'use')).toBe(false)
    expect(await said(ui, /^Resume$/)).toBe(true) // the other places stay
    await ui.unmount()
  })

  test(`${surface}: every card folds to its header and stays folded; the choice is kept in the store`, async ($, on) => {
    if (surface !== 'terminal') return // the desktop's HTML sections do not fold: every section is in view (tested below)
    const w = await harness($, on, ovState())
    const ui = await mountPane($, surface) // every section opened, as a person does
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▾') // the open caret beside the title, a grey button of its own
    await ui.press({ key: 'fold:resume' })
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▸') // the folded triangle
    expect(await said(ui, /^Resume$/)).toBe(true) // the header stays
    expect(await said(ui, /^2 need you$/)).toBe(true) // with its summary
    expect(await said(ui, /wiring the pane/)).toBe(false) // the body is gone
    expect(await said(ui, /^patch it$/)).toBe(true) // the other cards are untouched
    await ui.unmount()
    const again = await mountRaw($, surface)
    expect(await said(again, /wiring the pane/)).toBe(false)
    await again.press({ key: 'fold:resume' })
    expect(await said(again, /wiring the pane/)).toBe(true)
    await again.unmount()
    expect(w.runs).toEqual([])
  })

  test(`${surface}: a fresh pane shows Guide open and Resume, Agents, Decisions, Overrides folded; a press on a header opens it`, async ($, on) => {
    if (surface !== 'terminal') return // the desktop's HTML sections do not fold: every section is in view (tested below)
    await harness($, on, ovState())
    const ui = await mountRaw($, surface)
    expect(await labelOf(ui, 'fold:guide')).toBe('Guide')
    expect(await labelOf(ui, 'fold-caret:guide')).toBe('▾')
    for (const [id, title] of [['resume', 'Resume'], ['spawns', 'Agents'], ['overrides', 'Overrides']]) {
      expect(await labelOf(ui, `fold:${id}`)).toBe(title)
      expect(await labelOf(ui, `fold-caret:${id}`)).toBe('▸')
    }
    expect(await said(ui, /^2 need you$/)).toBe(true) // a folded section keeps its summary
    expect(await said(ui, /^3 agents · \$0\.41$/)).toBe(true)
    expect(await said(ui, /wiring the pane/)).toBe(false) // and shows no body
    expect(await said(ui, /^patch it$/)).toBe(false)
    expect(await has(ui, 'edit-overrides')).toBe(false)
    await ui.press({ key: 'fold:resume' })
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▾')
    expect(await said(ui, /wiring the pane/)).toBe(true)
    expect(await labelOf(ui, 'fold-caret:spawns')).toBe('▸') // the others stay folded
    await ui.unmount()
  })

  test(`${surface}: the sections are flat, with one rule between each pair and a triangle that toggles (title or caret)`, async ($, on) => {
    if (surface !== 'terminal') return // the desktop's HTML sections do not fold: every section is in view (tested below)
    await harness($, on, ovState())
    const ui = await mountPane($, surface)
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▾')
    expect(await propOf(ui, 'fold-caret:resume', 'dimColor')).toBe(true) // grey, as the native caret
    if ((surface as string) === 'desktop') {
      const rules = (await ui.findAll({ type: 'Svg' })).filter((x: any) => x.props.alt === 'section divider')
      const heads = (await ui.findAll({ type: 'Button' })).filter((x: any) => /^fold:(guide|resume|decisions|spawns|overrides)$/.test(String(x.props.key ?? '')))
      expect(rules.length).toBe(heads.length - 1) // none above the first, none below the last
    }
    await ui.press({ key: 'fold-caret:resume' }) // the caret folds it like the title
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▸')
    expect(await said(ui, /wiring the pane/)).toBe(false)
    expect(await said(ui, /^2 need you$/)).toBe(true) // the summary is a Text: coloured where it means something
    await ui.press({ key: 'fold:resume' })
    expect(await labelOf(ui, 'fold-caret:resume')).toBe('▾')
    await ui.unmount()
  })

  test(`${surface}: a fold an earlier session kept in the store is honoured at the start`, async ($, on) => {
    if (surface !== 'terminal') return // the desktop's HTML sections do not fold: every section is in view (tested below)
    await harness($, on, ovState(), { store: { paneFolds: { resume: true, spawns: false, 'more:needs': true, junk: 'x' } } })
    const ui = await mountRaw($, surface)
    expect(await said(ui, /^patch it$/)).toBe(false)
    expect(await labelOf(ui, 'fold-caret:spawns')).toBe('▸')
    expect(await said(ui, /^pick the cap$/)).toBe(true)
    await ui.unmount()
  })

  test(`${surface}: long lists show their first rows and a Show all; nothing is cut or keeps markdown marks`, async ($, on) => {
    const long = 'Confirm the Kurdish app name "مرۆڤ" with the store team before the build goes out to review, and keep the old name in the release notes'
    const needs = Array.from({ length: 11 }, (_, i) => ({ n: i + 3, text: i === 0 ? `**Your decision**, \`next build\` ${long}` : `decision ${i + 3}` }))
    const rows = Array.from({ length: 8 }, (_, i) => ({ ...ROWS[1], ts: NOW - 1000 + i, description: `step ${i}`, task: 'Ship it' }))
    await harness($, on, { guide: guideOf([item({ p: '🟠', text: long, id: 'dec:1' }), item({ id: 'a' }), item({ id: 'b' }), item({ id: 'c' })]), pane: panePayload({ resume: { ...panePayload().resume, needs, needs_open: 11 }, spawns: { live: [], rows, totals: { spawns: 8, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) })
    const ui = await mountPane($, surface, { bodyColumns: 160 }) // wide: both long lines fit two lines, shown whole
    expect(await said(ui, new RegExp(`^${long}$`))).toBe(true) // the top guide item in full, after its dot
    expect(await said(ui, new RegExp(`^Your decision, next build ${long}$`))).toBe(true) // whole, ** and \` dropped
    expect(await said(ui, /^Needs you$/)).toBe(true)
    expect(await said(ui, /^11 need you$/)).toBe(true)
    expect(await said(ui, /^decision 5$/)).toBe(true)
    expect(await said(ui, /^decision 6$/)).toBe(false) // past the first three
    expect(await labelOf(ui, 'more:needs')).toBe('Show all 11')
    await ui.press({ key: 'more:needs' })
    expect(await said(ui, /^decision 13$/)).toBe(true)
    expect(await labelOf(ui, 'more:needs')).toBe('Show fewer') // a list's toggle, never read as a text's Show less
    // the guide's other items: two, then Show all
    expect(await labelOf(ui, 'more:guide')).toBe('Show all 4')
    expect(await said(ui, /^4 open$/)).toBe(true)
    // the history: the newest five rows, then Show all
    expect(await said(ui, /^step 7$/)).toBe(true)
    expect(await said(ui, /^step 3$/)).toBe(true)
    expect(await said(ui, /^step 2$/)).toBe(false)
    expect(await labelOf(ui, 'more:history')).toBe('Show all 8')
    await ui.press({ key: 'more:history' })
    expect(await said(ui, /^step 0$/)).toBe(true)
    await ui.unmount()
  })

  test(`${surface}: History's Show all counts the Session rows too (totals.sessions), not only the agents`, async ($, on) => {
    const rows = Array.from({ length: 8 }, (_, i) => ({ ...ROWS[1], ts: NOW - 1000 + i, description: `step ${i}`, task: 'Ship it' }))
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows, totals: { spawns: 6, sessions: 4, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) })
    const ui = await mountPane($, surface, { bodyColumns: 160 })
    expect(await labelOf(ui, 'more:history')).toBe('Show last 8 of 10') // 6 agents + 4 sub sessions; the pane holds 8 rows, so it says so
    await ui.unmount()
  })

  test(`${surface}: the editor page groups the keys, folds a group, and Back keeps the draft`, async ($, on) => {
    await harness($, on, ovState(ovKeys([AGENTS, GUIDE_PLACE, { key: 'Newer key', kind: 'text', options: [], value: '', source: 'default', default: '' }])))
    const ui = await mountEditor($, surface)
    for (const g of ['Agents', 'Output', 'Where it shows', 'More', 'Other overrides']) expect(await said(ui, new RegExp(`^${g}$`))).toBe(true)
    expect(await said(ui, /^Session$/)).toBe(false) // no key of it in this table
    expect(await said(ui, /^1 set$/)).toBe(true) // Output: Receipt is set in the project file
    await ui.press({ key: 'fold:g:Agents' })
    expect(await has(ui, 'sel:Agents')).toBe(false)
    expect(await has(ui, 'in:Parallel')).toBe(false)
    expect(await has(ui, 'sel:Receipt')).toBe(true)
    await ui.press({ key: 'fold:g:Agents' })
    await ui.input({ key: 'in:Parallel', text: '6', kind: 'change' })
    await ui.press({ key: 'back' })
    expect(await said(ui, /^1 unsaved$/)).toBe(true) // the main page's card says so
    await ui.press({ key: 'edit-overrides' })
    expect(await valueOf(ui, 'in:Parallel')).toBe('6')
    expect(await propOf(ui, 'in:Newer key', 'placeholder')).toBe('none') // a key without a default
    await ui.unmount()
  })

  test(`${surface}: the Memo button is primary when nothing else is open`, async ($, on) => {
    const live = { live: [{ side: 'Work', model: 'sonnet', effort: 'high', description: 'build the card', ts: null }], rows: [], totals: { spawns: 0, tokens: 0, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } }
    const w = await harness($, on, { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:a' })], { ctx: { tokens: 480000, mark: 500 } }), pane: panePayload({ spawns: live }) })
    const ui = await mountPane($, surface)
    expect(await labelOf(ui, 'memo')).toBe('Save memo')
    expect(await said(ui, /^Near the next mark: save the memo, then \/compact\.$/)).toBe(true)
    expect(await propOf(ui, 'memo', 'variant')).toBe('primary')
    await ui.unmount()
    w.state = { guide: guideOf([item({ id: 'esc:open' })], { ctx: { tokens: 480000, mark: 500 } }), pane: panePayload() }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `t-memo-${surface}`, reason: 'answered' } as never)
    const open = await mountPane($, surface)
    expect(await propOf(open, 'memo', 'variant')).toBe('secondary') // an open item comes first
    expect(await propOf(open, 'use', 'variant')).toBe('primary')
    await open.unmount()
  })
}

test('a journal receipt line reads as a table row (time, spawns, $, saved), its done / check / open as details', () => {
  const line = '10-22 07:19 · 1 Spawn · 319K Tokens · $0.28 · Saved 46% ($0.24) vs Opus 5.5 · done: the pane · check: `79 pass` · open: none'
  expect(receiptOf(line)).toEqual({ time: '10-22 07:19', spawns: '1 agent', usd: '$0.28', saved: '46%', over: null, tokens: '319K tokens', money: '46% saved ($0.24) vs Opus 5.5', text: '', details: ['done: the pane', 'check: 79 pass', 'open: none'] })
  expect(receiptOf('10-22 08:00 · 11 Spawns · $7.96 · Saved 27% ($2.93) vs Opus 5.5').spawns).toBe('11 agents') // a line written before the rename reads the same
  expect(receiptOf('10-22 08:00 · 2 Agents · $7.96 · Saved 27% ($2.93) vs Opus 5.5').spawns).toBe('2 agents')
  expect(receiptOf('10-22 08:00 · done: only a note')).toEqual({ time: '10-22 08:00', spawns: null, usd: null, saved: null, tokens: null, money: null, text: 'done: only a note', details: [] })
  expect(receiptOf('wrote cards')).toEqual({ time: null, spawns: null, usd: null, saved: null, tokens: null, money: null, text: 'wrote cards', details: [] })
  expect(headParts('📍 **Resume** · memo 2026-10-22 07:19 · away 2 days · session 8e23c250 · now written 2026-10-22 07:19 from receipt')).toEqual({ memo: '2026-10-22 07:19', away: '2 days', session: '8e23c250' })
  expect(fmtWhen('10-22 07:19')).toBe('Oct 22 07:19')
  expect(fmtWhen('2026-10-24 10:32', true)).toBe('Oct 24, 10:32')
  expect(fmtWhen('soon')).toBe('soon')
})

for (const surface of SURFACES) {
  test(`${surface}: Last receipts are one row each (caret, time, spawns, $, saved); a row's caret opens its details`, async ($, on) => {
    const last = ['10-22 07:19 · 1 Spawn · 319K Tokens · $0.28 · Saved 46% ($0.24) vs Opus 5.5 · done: the pane · check: 79 pass']
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: { ...panePayload().resume, last } }) })
    const ui = await mountPane($, surface)
    for (const re of [/^Last receipts$/, /^Oct 22 07:19$/, /^1 agent$/, /^\$0\.28$/, /^46%( saved)?$/]) expect(await said(ui, re)).toBe(true)
    if (surface !== 'terminal') {
      // the desktop: a card per receipt, what was done as its headline and the rest as labelled rows, no caret to press
      expect(await said(ui, /^done: the pane$/)).toBe(true)
      expect(await has(ui, 'rcpt:10-22 07:19:0')).toBe(false)
      const src = String((await ui.findAll({ type: 'Svg' })).find((x: any) => /^Oct 22 07:19\n/.test(String(x.props.alt)))?.props.source)
      expect(src).toContain('<span class="lbl t-blue">check</span>')
      expect(src).not.toContain('>done</span>') // done is the headline, not a label
      expect(await labelOf(ui, 'detail:receipt:0')).toBe('Full details') // every receipt opens on a page of its own
      await ui.unmount()
      // a long detail: Full details, right under that card, opens the whole receipt as a page; Back returns
      const long = Array.from({ length: 30 }, (_, i) => `step${i}`).join(' ')
      w.state = { guide: guideOf([item()]), pane: panePayload({ resume: { ...panePayload().resume, last: [`10-22 07:19 · 1 Spawn · $0.28 · done: the pane · open: ${long}`] } }) }
      await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: 'rcpt-long', reason: 'answered' } as never)
      const lg = await mountPane($, surface)
      await lg.press({ key: 'detail:receipt:0' })
      const pics = await lg.findAll({ type: 'Svg' })
      const text = pics.map((x: any) => String(x.props.alt)).join('\n')
      expect(text).toContain(long) // the whole text, nothing clamped
      for (const part of ['Oct 22, 07:19', '$0.28', '1 agent']) expect(text).toContain(part)
      expect(pics.some((x: any) => String(x.props.source).includes('>open</span>'))).toBe(true) // a section of its own, labelled
      expect(await lg.find({ type: 'Markdown' })).toBeUndefined() // cards, like the main pages
      expect(await labelOf(lg, 'detail-back')).toBe('‹ Resume') // back to the cards, named by the page it returns to
      expect(await has(lg, 'use')).toBe(false) // a page of its own
      await lg.press({ key: 'detail-back' })
      expect(await has(lg, 'use')).toBe(true)
      expect(await lg.find({ type: 'Markdown' })).toBeUndefined()
      return lg.unmount()
    }
    expect(await said(ui, /^done: the pane$/)).toBe(false)
    await ui.press({ key: 'rcpt:10-22 07:19:0' })
    expect(await said(ui, /^done: the pane$/)).toBe(true)
    expect(await said(ui, /^check: 79 pass$/)).toBe(true)
    expect(await labelOf(ui, 'rcpt:10-22 07:19:0')).toBe('▾') // the row's caret, open
    expect(await propOf(ui, 'rcpt:10-22 07:19:0', 'dimColor')).toBe(true)
    await ui.press({ key: 'rcpt:10-22 07:19:0' })
    expect(await said(ui, /^done: the pane$/)).toBe(false)
    expect(await said(ui, /^1 agent$/)).toBe(true) // the row stays
    await ui.unmount()
  })
}

for (const surface of SURFACES) {
  test(`${surface}: two receipts in the same minute get distinct keys and open on their own`, async ($, on) => {
    if (surface !== 'terminal') return // the desktop shows both rows' details at once: no keys to tell apart
    const last = ['10-22 07:19 · 1 Spawn · $0.28 · done: first one', '10-22 07:19 · 2 Spawns · $0.50 · done: second one']
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: { ...panePayload().resume, last } }) })
    const ui = await mountPane($, surface)
    expect(await has(ui, 'rcpt:10-22 07:19:0')).toBe(true)
    expect(await has(ui, 'rcpt:10-22 07:19:1')).toBe(true)
    await ui.press({ key: 'rcpt:10-22 07:19:1' })
    expect(await said(ui, /^done: second one$/)).toBe(true)
    expect(await said(ui, /^done: first one$/)).toBe(false)
    await ui.unmount()
  })
}

test('history groups rows by day: one muted header per day, the rows under it show only the time', async ($, on) => {
  const day = 86400
  const rows = [
    { ...ROWS[0], ts: NOW - 3 * day, description: 'old one' },
    { ...ROWS[1], ts: NOW - day, description: 'mid one' },
    { ...ROWS[2], ts: NOW - day + 60, description: 'mid two' },
    { ...ROWS[2], ts: NOW, description: 'new one' },
  ]
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows, totals: { spawns: 4, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0, by_model: [] } } }) })
  const desk = await mountPane($, 'desktop') // the desktop's card: the same day captions and order in its alt
  const dl = (await altLines(desk)).filter(l => /^(Today|Yesterday|old one|mid one|mid two|new one|[A-Z][a-z]{2} \d{1,2})$/.test(l))
  expect(dl[0]).toBe('Today')
  expect(dl[1]).toBe('new one')
  expect(dl[2]).toBe('Yesterday')
  await desk.unmount()
  const ui = await mountPane($, 'terminal')
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const d = new Date((NOW - 3 * day) * 1000)
  const labels = (await ui.findAll({ type: 'Text', text: /^(Today|Yesterday|[A-Z][a-z]{2} \d{1,2})$/ })).map((x: any) => x.text)
  expect(labels).toEqual(['Today', 'Yesterday', `${MON[d.getMonth()]} ${d.getDate()}`]) // three days, three headers (two rows share one)
  const order = (await ui.findAll({ type: 'Text', text: /^(Today|Yesterday|old one|mid one|mid two|new one|[A-Z][a-z]{2} \d{1,2})$/ })).map((x: any) => x.text)
  expect(order.slice(0, 2)).toEqual(['Today', 'new one'])
  expect(order[2]).toBe('Yesterday')
  expect(order.slice(3, 5).sort()).toEqual(['mid one', 'mid two'])
  expect(order[5]).toMatch(/^[A-Z][a-z]{2} \d{1,2}$/)
  expect(order[6]).toBe('old one')
  expect(await said(ui, /^[A-Z][a-z]{2} \d{1,2} \d\d:\d\d$/)).toBe(false) // no date on a row, only the time
  await ui.unmount()
})

test('a history row meta line: time, the model as the app names it, tokens, the status word only when it means something', async ($, on) => {
  const base = { ...ROWS[0], side: 'Work', model: 'opus', effort: 'xhigh', tokens: 810000, usd: 0.1 }
  const rows = [
    { ...base, ts: NOW - 40, description: 'plain', status: 'done', verdict: null },
    { ...base, ts: NOW - 30, description: 'passed', status: 'done', verdict: 'PASS' },
    { ...base, ts: NOW - 20, description: 'failed', status: 'done', verdict: 'FAIL' },
    { ...base, ts: NOW - 10, description: 'part', status: 'partial', verdict: null },
  ]
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows, totals: { spawns: 4, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0, by_model: [] } } }) })
  const ui = await mountPane($, 'desktop')
  const lines = (await altLines(ui)).filter(l => /^\d\d:\d\d · Opus 5\.5 · Extra · 810K tokens/.test(l)).map(l => l.replace(/^\d\d:\d\d/, 'T'))
  expect(lines).toEqual(['T · Opus 5.5 · Extra · 810K tokens · PARTIAL', 'T · Opus 5.5 · Extra · 810K tokens · FAIL', 'T · Opus 5.5 · Extra · 810K tokens · PASS', 'T · Opus 5.5 · Extra · 810K tokens']) // newest first; DONE has no word
  const src = String((await ui.findAll({ type: 'Svg' })).find((x: any) => /^History\n/.test(String(x.props.alt)))?.props.source)
  for (const [word, tone] of [['PASS', 'green'], ['FAIL', 'red'], ['PARTIAL', 'amber']]) expect(src).toContain(`<span class="word ink t-${tone}">${word}</span>`) // the word in its tone
  await ui.unmount()
  const term = await mountPane($, 'terminal')
  expect(await said(term, /^\d\d:\d\d · Opus 5\.5 · Extra · 810K tokens · PASS$/)).toBe(true) // the terminal: the same line, no Svg
  expect(await term.find({ type: 'Svg' })).toBeUndefined()
  await term.unmount()
})

test('a history row keeps its Read more state when a newer row is prepended (keyed by ts + text, not position)', async ($, on) => {
  const desc = 'rebuild the whole spawn pipeline so that every long running agent reports back through the shared ledger before the next turn starts'
  const old = { ...ROWS[0]!, description: desc }
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: [old], totals: { spawns: 1, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) })
  const key = `more:spawn:${old.ts}:${shortHash(`${old.side} · ${desc}`)}`
  const ui = await mountPane($, 'terminal') // the native rows' own Read more (the desktop's card has one per section)
  expect(await moreOf(ui, key)).toBe('Read more')
  await ui.press({ key })
  expect(await moreOf(ui, key)).toBe('Show less')
  await ui.unmount()
  w.state = { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: [old, { ...ROWS[1], ts: NOW - 10 }], totals: { spawns: 2, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0 } } }) }
  const again = await mountPane($, 'terminal')
  expect(await moreOf(again, key)).toBe('Show less') // still open: a new top row moved nothing
  await again.unmount()
})

test('Read more: text at most ~20% over the limit is shown whole; a two-row text is cut at a word, never between a letter and its mark', async ($, on) => {
  const limit = 60 - 4 - 3 // a guide item under the top one: one row of the card, less the dot column
  const near = 'a'.repeat(Math.floor(limit * 1.1))
  const arabic = 'x' + 'بَ'.repeat(90) // one base then base+mark pairs
  await harness($, on, { guide: guideOf([item({ id: 'top', text: 'the top item' }), item({ p: '🟠', id: 'near', text: near })]), pane: panePayload({ resume: { ...panePayload().resume, needs: [{ n: 1, text: arabic }] } }) })
  const ui = await mountPane($, 'terminal') // the native rows' cut (the desktop's card clamps with CSS)
  expect(await said(ui, new RegExp(`^${near}$`))).toBe(true)
  expect(await has(ui, 'more:guide:near')).toBe(false)
  const cut = (await ui.findAll({ type: 'Text', text: /^x.*…$/ }))[0]?.text as string // Needs you shows two rows: cut by code
  expect(cut).toBeDefined()
  expect(/\p{M}…$/u.test(cut)).toBe(true) // ends on a whole letter + mark, not a bare letter
  expect(arabic.startsWith(cut.slice(0, -1))).toBe(true)
  expect(await moreOf(ui, 'more:resume:needs:1')).toBe('Read more')
  await ui.unmount()
})

const ROGUE = [['sh', '-c', 'touch /tmp/x'], ['bash', '/x/plugins/abofles/scripts/py', '/tmp/evil.py'], ['bash', '/tmp/scripts/py', '/tmp/scripts/memo.py'], ['bash', '/x/plugins/abofles/scripts/py', '/y/plugins/abofles/scripts/memo.py']]
for (const argv of ROGUE) {
  test(`an argv in the state file runs only in script_cmd's shape: ${argv.join(' ')} is dropped, so a press never runs it`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item({ id: `r:${argv[2]}` })], { last_resolved: { text: 'looks resolved', unresolve: argv, ts: 9 } }), pane: panePayload({ cmd: { overrides: argv } }) })
    const ui = await mountPane($, 'desktop')
    expect(await said(ui, /✓ resolved/)).toBe(false) // no Undo row to press
    await ui.press({ key: 'edit-overrides' })
    await ui.select({ key: 'sel:Receipt', value: 'full' })
    await ui.press({ key: 'save' })
    expect(w.runs).toEqual([])
    expect(w.toasts).toContain('Update the abofles plugin to save here')
    await ui.press({ key: 'reset' })
    await ui.press({ key: 'back' })
    await ui.unmount()
  })
}

// ---- Check all and Decisions

const FILL_ALL = 'abofles guide — check all: one, two'
const dec = (i: number, over: object = {}) => ({ ts: `10-${String(10 + (i % 18)).padStart(2, '0')} 09:${String(i % 60).padStart(2, '0')}`, q: `question ${i}`, a: `answer ${i}`, sid: 'sid-pane', ...over })
const decisions = (you: object[], judge: object[], more: object = {}) => ({ you, judge, n_you: you.length, n_judge: judge.length, ...more })

for (const surface of SURFACES) {
  test(`${surface}: Check all is hidden with one item, shown with two, fills the prompt box and does not submit`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item()], { all: { n: 1, fill: FILL_ALL } }), pane: panePayload() })
    const one = await mountPane($, surface)
    expect(await has(one, 'check-all')).toBe(false)
    expect(await has(one, 'use')).toBe(true)
    await one.unmount()
    w.state = { guide: guideOf([item({ id: 'a' }), item({ id: 'b', text: 'second' })], { all: { n: 2, fill: FILL_ALL } }), pane: panePayload() }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `ca-${surface}`, reason: 'answered' } as never)
    const ui = await mountPane($, surface)
    expect(await labelOf(ui, 'check-all')).toBe('Check all (2)')
    expect(await propOf(ui, 'check-all', 'variant')).toBe('primary')
    for (const key of ['use', 'copy']) expect(await has(ui, key)).toBe(true) // the other buttons stay
    expect(await propOf(ui, 'use', 'variant')).toBe('secondary') // one primary in the row
    await ui.press({ key: 'check-all' })
    expect(w.fills).toEqual([{ text: FILL_ALL, mode: 'replace' }]) // filled, never sent
    expect(w.runs).toEqual([])
    expect(w.toasts).toEqual([])
    expect(await has(ui, 'check-all')).toBe(false) // a landed fill hides the guide, as Use does
    expect(await said(ui, /Hidden until the list changes/)).toBe(true)
    await ui.unmount()
  })

  test(`${surface}: a guide without "all" (older hooks) or with a malformed one draws no Check all`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item({ id: 'a' }), item({ id: 'b' })]), pane: panePayload() })
    const ui = await mountPane($, surface)
    expect(await has(ui, 'check-all')).toBe(false)
    expect(await has(ui, 'use')).toBe(true)
    await ui.unmount()
    for (const [n, all] of [['bad1', { n: 'two', fill: FILL_ALL }], ['bad2', { n: 3 }], ['bad3', { n: 3, fill: '  ' }], ['bad4', 'x']] as const) {
      w.state = { guide: guideOf([item({ id: `${n}a` }), item({ id: `${n}b` })], { all }), pane: panePayload() }
      await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `${n}-${surface}`, reason: 'answered' } as never)
      const bad = await mountPane($, surface)
      expect(await has(bad, 'check-all')).toBe(false)
      expect(await has(bad, 'use')).toBe(true)
      await bad.unmount()
    }
  })

  test(`${surface}: the Resume card's Needs you list has a Check all when the hook gave the combined text`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
    const none = await mountPane($, surface)
    expect(await has(none, 'check-needs')).toBe(false) // no needs_fill: no button
    await none.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload({ resume: { ...panePayload().resume, needs_fill: 'abofles — decide: pick the cap; name the key' } }) }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `rn-${surface}`, reason: 'answered' } as never)
    const ui = await mountPane($, surface)
    expect(await labelOf(ui, 'check-needs')).toBe('Check all (2)')
    await ui.press({ key: 'check-needs' })
    expect(w.fills).toEqual([{ text: 'abofles — decide: pick the cap; name the key', mode: 'replace' }])
    expect(w.runs).toEqual([])
    expect(await said(ui, /Hidden until the list changes/)).toBe(true) // the same hide-on-land as Use
    await ui.unmount()
  })

  test(`${surface}: items of other sessions and projects stay hidden behind Show others, a step back from the owner's own`, async ($, on) => {
    const base = panePayload().resume
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: { ...base, others: { sessions: 2, projects: 1 }, others_items: [{ n: 26, text: 'Jamkhana API #131 owner decision' }] } }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /Jamkhana API #131/)).toBe(false) // hidden: never reads as the owner's own
    expect(await labelOf(ui, 'others:needs')).toBe('Show others (3)')
    await ui.press({ key: 'others:needs' })
    expect(await said(ui, /Jamkhana API #131 owner decision/)).toBe(true)
    expect(await said(ui, /^26$/m)).toBe(false) // a bullet, as in Needs you: the memo's item number means nothing here
    expect(await labelOf(ui, 'others:needs')).toBe('Hide others')
    expect(await has(ui, 'check-needs')).toBe(false) // never part of Check all
    await ui.unmount()
  })

  test(`${surface}: Other items = show marks another session's item; an older hook sends neither field and nothing changes`, async ($, on) => {
    const base = panePayload().resume
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: { ...base, needs: [{ n: 1, text: 'my own call' }, { n: 2, text: 'their call', other: true }], needs_open: 1 } }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /their call · other/)).toBe(true)
    expect(await said(ui, /my own call · other/)).toBe(false)
    expect(await has(ui, 'others:needs')).toBe(false)
    await ui.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload() }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `oh-${surface}`, reason: 'answered' } as never)
    const old = await mountPane($, surface)
    expect(await has(old, 'others:needs')).toBe(false)
    await old.unmount()
  })

  test(`${surface}: with the memo saved by a compact the button is Compact and fills /compact; an older hook keeps Save memo`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:a' })], { ctx: { tokens: 480000, mark: 500, auto: true } }), pane: panePayload() })
    const ui = await mountPane($, surface)
    expect(await labelOf(ui, 'memo')).toBe('Compact')
    await ui.press({ key: 'memo' })
    expect(w.fills).toEqual([{ text: '/compact', mode: 'replace' }])
    await ui.unmount()
    w.state = { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:b' })], { ctx: { tokens: 480000, mark: 500 } }), pane: panePayload() }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `mc-${surface}`, reason: 'answered' } as never)
    const old = await mountPane($, surface)
    expect(await labelOf(old, 'memo')).toBe('Save memo')
    await old.unmount()
  })

  test(`${surface}: the memo's tidy-up shows as one fact, only when it moved something`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ memo_health: { archived_items: 47, archived_sessions: 1, trimmed: 0, deduped: 2, line: 'x' } }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /^Tidied$/)).toBe(true)
    expect(await said(ui, /^48 archived · 2 duplicates removed$/)).toBe(true)
    await ui.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload({ memo_health: { archived_items: 0, archived_sessions: 0, trimmed: 0, deduped: 0, line: '' } }) }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `mh-${surface}`, reason: 'answered' } as never)
    const quiet = await mountPane($, surface)
    expect(await said(quiet, /^Tidied$/)).toBe(false)
    await quiet.unmount()
  })

  test(`${surface}: Decisions of other sessions stay behind Show others`, async ($, on) => {
    const d = (q: string) => ({ ts: '11-05 10:00', q, a: 'yes', sid: 'aa' })
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: { you: [d('Mine?')], judge: [], n_you: 1, n_judge: 0, others: { sessions: 1, projects: 0 }, others_rows: [d('Theirs?')] } }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /Theirs\?/)).toBe(false)
    expect(await labelOf(ui, 'others:decisions')).toBe('Show others (1)')
    await ui.press({ key: 'others:decisions' })
    expect(await said(ui, /Theirs\?/)).toBe(true)
    expect(await labelOf(ui, 'others:decisions')).toBe('Hide others')
    await ui.unmount()
  })

  test(`${surface}: Decisions draws By you and By Judge, five rows each, then Show all`, async ($, on) => {
    const you = Array.from({ length: 7 }, (_, i) => dec(i))
    const judge = [dec(0, { q: 'judge: which cap?', a: 'five', ts: '10-11 08:00' }), ...Array.from({ length: 2 }, (_, i) => dec(i + 100))]
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: decisions(you, judge) }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /^Decisions$/)).toBe(true)
    expect(await said(ui, /^10 · 3 by Judge$/)).toBe(true)
    expect(await said(ui, /^By you$/)).toBe(true)
    expect(await said(ui, /^By Judge$/)).toBe(true)
    expect(await said(ui, /^7$/)).toBe(true) // the group counts
    expect(await said(ui, /^question 0$/)).toBe(true) // the question, then the answer after an arrow
    expect(await said(ui, /^answer 0$/)).toBe(true)
    if (surface === 'terminal') expect(await said(ui, /^→$/)).toBe(true) // the desktop's card draws its arrow
    expect(await said(ui, /^Oct 10 09:00$/)).toBe(true)
    expect(await said(ui, /^question 4$/)).toBe(true)
    expect(await said(ui, /^question 5$/)).toBe(false) // past the fifth
    expect(await labelOf(ui, 'more:dec-you')).toBe('Show all 7')
    expect(await said(ui, /^which cap\?$/)).toBe(true) // the "judge: " lead is dropped
    expect(await said(ui, /^five$/)).toBe(true)
    expect(await said(ui, /judge:/)).toBe(false)
    expect(await has(ui, 'more:dec-judge')).toBe(false) // three rows: no fold
    await ui.press({ key: 'more:dec-you' })
    expect(await said(ui, /^question 6$/)).toBe(true)
    expect(await labelOf(ui, 'more:dec-you')).toBe('Show fewer')
    await ui.press({ key: 'more:dec-you' })
    expect(await said(ui, /^question 6$/)).toBe(false)
    await ui.unmount()
  })

  test(`${surface}: a Decisions group with no rows says so; no decisions in the state means no card`, async ($, on) => {
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: decisions([dec(1)], []) }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /^question 1$/)).toBe(true)
    expect(await said(ui, /^No decisions yet$/)).toBe(true) // By Judge is empty
    await ui.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload() } // older hooks: no decisions at all
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `nd-${surface}`, reason: 'answered' } as never)
    const none = await mountPane($, surface)
    expect(await said(none, /^Decisions$/)).toBe(false)
    expect(await has(none, 'fold:decisions')).toBe(false)
    expect(await said(none, /^By you$/)).toBe(false)
    await none.unmount()
  })

  test(`${surface}: malformed decisions are dropped, the valid ones kept, each group held to 50`, async ($, on) => {
    const many = Array.from({ length: 60 }, (_, i) => dec(i))
    const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: { you: [7, null, { ts: '10-10 09:00', q: 'no answer' }, { ts: 5, q: 'x', a: 'y' }, dec(1)], judge: 'no', n_you: 'many', n_judge: 4 } }) })
    const ui = await mountPane($, surface)
    expect(await said(ui, /^question 1$/)).toBe(true)
    expect(await said(ui, /^no answer$|^y$/)).toBe(false)
    expect(await said(ui, /^No decisions yet$/)).toBe(true) // the judge group was no list
    expect(await said(ui, /^1$/)).toBe(true) // n_you fell back to the rows kept
    expect(await said(ui, /^4$/)).toBe(true) // n_judge kept
    await ui.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload({ decisions: 'garbage' }) }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `md-${surface}`, reason: 'answered' } as never)
    const bad = await mountPane($, surface)
    expect(await said(bad, /^Decisions$/)).toBe(false)
    await bad.unmount()
    w.state = { guide: guideOf([item()]), pane: panePayload({ decisions: { you: many, judge: [], n_you: 60, n_judge: 0 } }) }
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `mx-${surface}`, reason: 'answered' } as never)
    const cap = await mountPane($, surface)
    expect(await labelOf(cap, 'more:dec-you')).toBe('Show all 50')
    await cap.unmount()
  })
}

test('the editor explains the Budget raise and Git guard keys in the Agents group', async ($, on) => {
  const raise = { key: 'Budget raise', kind: 'select', options: ['auto', 'ask'], value: 'auto', source: 'default', default: 'auto' }
  const git = { key: 'Git guard', kind: 'select', options: ['warn', 'deny', 'off'], value: 'warn', source: 'default', default: 'warn' }
  await harness($, on, ovState(ovKeys([raise, git])))
  const ui = await mountEditor($, 'desktop')
  expect(await said(ui, /^Budget raise$/)).toBe(true)
  expect(await said(ui, /^auto: \+half the start budget, twice at most, then asks; ask: always asks$/)).toBe(true)
  expect(await said(ui, /^Git guard$/)).toBe(true)
  expect(await said(ui, /^Warn or deny git writes outside the git agent, or off$/)).toBe(true)
  expect(await has(ui, 'sel:Budget raise')).toBe(true)
  expect(await has(ui, 'sel:Git guard')).toBe(true)
  await ui.unmount()
})

for (const surface of SURFACES) {
  test(`${surface}: the editor puts Sub sessions (a number) and Session routing (on/warn/off) in the Agents group, with their help`, async ($, on) => {
    const cap = { key: 'Sub sessions', kind: 'number', options: [], value: '', source: 'default', default: '3' }
    const routing = { key: 'Session routing', kind: 'select', options: ['on', 'warn', 'off'], value: 'on', source: 'default', default: 'on' }
    await harness($, on, ovState(ovKeys([cap, routing])))
    const ui = await mountEditor($, surface)
    expect(await said(ui, /^Sub sessions$/)).toBe(true)
    expect(await said(ui, /^Open sub sessions one session may start; 0 = none allowed; off = no cap$/)).toBe(true)
    expect(await said(ui, /^Session routing$/)).toBe(true)
    expect(await said(ui, /^on: a sub session must carry the routed model and effort; warn: note only; off$/)).toBe(true)
    expect(await propOf(ui, 'in:Sub sessions', 'placeholder')).toBe('default 3')
    expect((await propOf(ui, 'sel:Session routing', 'options')).map((o: any) => o.value)).toEqual(['@default', 'on', 'warn', 'off'])
    await ui.press({ key: 'fold:g:Agents' }) // both fold with the Agents group, so neither landed in More
    expect(await has(ui, 'in:Sub sessions')).toBe(false)
    expect(await has(ui, 'sel:Session routing')).toBe(false)
    expect(await said(ui, /^More$/)).toBe(false)
    await ui.press({ key: 'fold:g:Agents' })
    await ui.input({ key: 'in:Sub sessions', text: 'off', kind: 'change' })
    expect(await valueOf(ui, 'in:Sub sessions')).toBe('off')
    await ui.unmount()
  })

  test(`${surface}: a sub session row (side Session) shows its 🪟 Session label; an unknown status is grey with its word, never failed`, async ($, on) => {
    const sub = { ts: NOW - 60, side: 'Session', model: 'sonnet', effort: 'medium', topic: 'Docs', tier: 'T2', status: 'open', confidence: null, tokens: null, usd: null, base_usd: null, description: 'docs sync', verdict: null, task: null, agent_id: 'local_abc', name: 'docs-sync' }
    const rows = [{ ...ROWS[1], ts: NOW - 120, description: 'patch it' }, sub]
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows, totals: { spawns: 2, tokens: 1, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0, by_model: [] } } }) })
    const ui = await mountPane($, surface)
    if (surface === 'desktop') {
      const hist = (await ui.findAll({ type: 'Svg' })).find((x: any) => /^History\n/.test(String(x.props.alt)))
      const src = String(hist?.props.source)
      expect(src).toContain('🪟 Session')
      expect(src).toContain('<span class="dot t-grey"></span>') // neutral, not red
      expect(src).not.toContain('<span class="dot t-red"></span>')
      expect(src).toContain('<span class="word">OPEN</span>') // its word, untinted
      const alt = String(hist?.props.alt).split('\n')
      expect(alt).toContain('Session') // the alt keeps the plain name
      expect(alt).toContain('docs sync')
      expect(alt.some(l => /Sonnet 5\.5 · Medium · OPEN$/.test(l))).toBe(true)
    } else {
      expect(await said(ui, /^🪟 Session$/)).toBe(true)
      expect(await said(ui, /^docs sync$/)).toBe(true)
      expect(await said(ui, /Sonnet 5\.5 · Medium · OPEN$/)).toBe(true)
      expect(await said(ui, /FAIL/)).toBe(false)
      expect(await said(ui, /^Work$/)).toBe(true) // the other sides keep their name alone
    }
    await ui.unmount()
  })
}

// ---- Read more, header icons, dots

// The desktop draws the Guide as one HTML card (an Svg): its rows cut at the real width by CSS, so these two hold for the terminal's
// native rows; the card's own Read more is tested below.
test('desktop: the Guide is one HTML card; a long top item is clamped, Full details opens every item whole on a page; text is escaped', async ($, on) => {
  const LONG = 'Confirm the Kurdish app name with the store team before the build goes out to review, and keep the old name in the release notes so nobody is surprised'
  await harness($, on, { guide: guideOf([item({ id: 'top', text: `${LONG} ${LONG}` }), item({ p: '🟠', text: 'ship <b>R&D</b> notes', id: 'dec:1' })]), pane: panePayload() })
  const ui = await mountPane($, 'desktop', { bodyColumns: 40 })
  const card = async (u: any) => (await u.findAll({ type: 'Svg' })).find((x: any) => /^Next up, /.test(String(x.props.alt)) && /\nAlso open\n/.test(String(x.props.alt))) // the Guide page's card
  const before = await card(ui)
  expect(String(before.props.alt).split('\n')).toContain(`${LONG} ${LONG}`) // the alt carries the whole text
  expect(String(before.props.source)).toContain('-webkit-line-clamp:4')
  expect(String(before.props.source)).toContain('ship &lt;b&gt;R&amp;D&lt;/b&gt; notes') // escaped, never markup
  expect(String(before.props.source)).not.toMatch(/<script|<b>/)
  expect(await propOf(ui, 'detail:guide', 'label')).toBe('≡ Full details') // a button, its glyph first
  expect(await propOf(ui, 'detail:guide', 'variant')).toBe('secondary')
  await ui.press({ key: 'detail:guide' })
  const pics = await ui.findAll({ type: 'Svg' })
  expect(pics.map((x: any) => String(x.props.alt)).join('\n')).toContain(`${LONG} ${LONG}`) // the whole top item
  expect(pics.some((x: any) => String(x.props.source).includes('ship &lt;b&gt;R&amp;D&lt;/b&gt; notes'))).toBe(true) // escaped, never markup
  expect(pics.every((x: any) => !/<script|<b>/.test(String(x.props.source)))).toBe(true)
  await ui.press({ key: 'detail-back' })
  expect(await card(ui)).toBeDefined()
  expect(await has(ui, 'more:guide:dec:1')).toBe(false) // the other items are one line each, cut by CSS: no link
  await ui.unmount()
})

for (const surface of ['terminal'] as const) {
  const LONG = 'Confirm the Kurdish app name with the store team before the build goes out to review, and keep the old name in the release notes so nobody is surprised'
  test(`${surface}: a short description is shown whole; a long one is clipped to its row with Read more, opens to the full text and Show less, and is remembered`, async ($, on) => {
    await harness($, on, { guide: guideOf([item({ id: 'top', text: LONG }), item({ p: '🟠', text: LONG, id: 'dec:1' }), item({ id: 'b', text: 'short rest' })]), pane: panePayload() })
    const ui = await mountPane($, surface)
    expect(await said(ui, /^short rest$/)).toBe(true) // fits: whole, no link
    expect(await has(ui, 'more:guide:b')).toBe(false)
    const longs = async (u: any) => (await u.findAll({ type: 'Text', text: new RegExp(`^${LONG}$`) })).map((x: any) => x.props.wrap ?? 'wrap')
    expect(await longs(ui)).toEqual(['wrap', 'truncate-end']) // the top item whole (Use hands it over); the other the whole text, clipped by the surface at its row's real width
    expect(await moreOf(ui, 'more:guide:dec:1')).toBe('Read more')
    await ui.press({ key: 'more:guide:dec:1' })
    expect(await longs(ui)).toEqual(['wrap', 'wrap'])
    expect(await moreOf(ui, 'more:guide:dec:1')).toBe('Show less')
    await ui.unmount()
    const again = await mountPane($, surface) // remembered in the folds
    expect(await longs(again)).toEqual(['wrap', 'wrap'])
    await again.press({ key: 'more:guide:dec:1' })
    expect(await moreOf(again, 'more:guide:dec:1')).toBe('Read more')
    await again.unmount()
  })
}

for (const surface of ['terminal'] as const) {
  test(`${surface}: Read more is a small grey link of its own after the cut text, on its row; the text stays a Text; open, the full text and Show less`, async ($, on) => {
    const LONG = 'Confirm the Kurdish app name with the store team before the build goes out to review, and keep the old name in the release notes so nobody is surprised'
    await harness($, on, { guide: guideOf([item({ id: 'top', text: 'the top item' }), item({ p: '🟠', text: LONG, id: 'dec:1' })]), pane: panePayload() })
    const ui = await mountPane($, surface)
    const link = await ui.find({ key: 'more:guide:dec:1' })
    expect(link?.type).toBe('Button')
    expect(link?.props.label).toBe('Read more') // the link alone: the description is never inside a Button
    expect(link?.props.plain).toBe(true)
    expect(link?.props.dimColor).toBe(true)
    const cut = (await ui.findAll({ type: 'Text', text: new RegExp(`^${LONG}$`) }))[0]
    expect(cut).toBeDefined() // a Text of its own, the whole text
    expect(cut.props.wrap).toBe('truncate-end') // one row: the surface cuts it at the row's real width, so it uses all the room
    const order = (await ui.findAll({})).map((x: any) => (x.props?.key === 'more:guide:dec:1' ? 'link' : x.type === 'Text' && /^Confirm the Kurdish/.test(x.text ?? '') ? 'text' : null)).filter(Boolean)
    expect(order).toEqual(['text', 'link']) // the text, then its link at the row's end
    const boxes = (await ui.findAll({ type: 'Box' })).filter((b: any) => b.props.flexGrow === 1 && b.props.minWidth === 0)
    expect(boxes.length).toBeGreaterThan(0) // the text's box grows to fill the row: the link sits at the right edge
    await ui.press({ key: 'more:guide:dec:1' })
    expect(await said(ui, new RegExp(`^${LONG}$`))).toBe(true)
    expect(await labelOf(ui, 'more:guide:dec:1')).toBe('Show less')
    expect(await said(ui, /^Read more$/)).toBe(false)
    await ui.unmount()
  })
}

for (const surface of SURFACES) {
  test(`${surface}: a Decisions row has its time as a headline above the text, and a light divider between rows`, async ($, on) => {
    await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: decisions([dec(0), dec(1), dec(2)], []) }) })
    const ui = await mountPane($, surface)
    if (surface !== 'terminal') {
      // the desktop's card: time, question, answer per row (its alt in that order), a hairline between rows
      const lines = await altLines(ui)
      const at = (t: string) => lines.indexOf(t)
      expect(at('Oct 10 09:00')).toBeGreaterThan(-1)
      expect(at('question 0')).toBe(at('Oct 10 09:00') + 1)
      expect(at('answer 0')).toBe(at('question 0') + 1)
      const src = String((await ui.findAll({ type: 'Svg' })).find((x: any) => /^By you\n/.test(String(x.props.alt)))?.props.source)
      expect(src.split('class="hair"').length - 1).toBe(2) // three decisions: two hairlines
      return ui.unmount()
    }
    const texts = (await ui.findAll({ type: 'Text' })).map((x: any) => x.text)
    const at = (re: RegExp) => texts.findIndex((t: string) => re.test(t))
    expect(at(/^Oct 10 09:00$/)).toBeGreaterThan(-1)
    expect(at(/^question 0$/)).toBe(at(/^Oct 10 09:00$/) + 1) // the headline, then the question
    expect(at(/^answer 0$/)).toBe(at(/^question 0$/) + 2) // then the arrow and the answer
    expect(at(/^Oct 11 09:01$/)).toBe(at(/^answer 0$/) + 2) // the divider sits between the rows
    await ui.unmount()
  })
}

test('desktop: each section is a header strip (icon tile, title, summary) over its cards, all open, no caret; the terminal draws none', async ($, on) => {
  await harness($, on, ovState())
  const desktop = await mountRaw($, 'desktop') // fresh: nothing pressed
  const heads = (await desktop.findAll({ type: 'Svg' })).filter((x: any) => /class="head"/.test(String(x.props.source)))
  expect(heads.map((x: any) => String(x.props.alt).split('\n')[0])).toEqual(['Home', 'Running', 'Guide', 'Resume', 'Agents', 'Overrides']) // one page each, in the nav bar's order
  expect(heads.every((x: any) => /class="htile t-\w+"/.test(x.props.source) && /stroke-linecap="round"/.test(x.props.source))).toBe(true)
  expect(await said(desktop, /wiring the pane/)).toBe(true) // every section in view at once
  expect(await said(desktop, /^patch it$/)).toBe(true)
  expect(await has(desktop, 'fold-caret:resume')).toBe(false) // no dropdown triangle
  expect(await has(desktop, 'fold:resume')).toBe(false)
  await desktop.unmount()
  const term = await mountPane($, 'terminal')
  expect((await term.findAll({ type: 'Svg' })).length).toBe(0)
  expect(await said(term, /^Guide$/)).toBe(true)
  expect(await said(term, /^Abo Fles · ابو فليس$/)).toBe(true) // the brand line as dim text, no picture
  await term.unmount()
})

test('every SVG colour has a light twin and a dark value, each >= 4.5:1 on white / #1a1a1a', () => {
  const lum = (hex: string): number => {
    const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
    return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!
  }
  const ratio = (a: string, b: string): number => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05)
  for (const [name, { light, dark }] of Object.entries(COLOR_PAIRS)) {
    expect([name, ratio(light, '#ffffff') >= 4.5]).toEqual([name, true])
    expect([name, ratio(dark, '#1a1a1a') >= 4.5]).toEqual([name, true])
  }
})

test('desktop: a nav bar of icons shows one page at a time: each page its header icon, the one shown filled, a blank press area over each; Home sums up every section', async ($, on) => {
  await harness($, on, { guide: guideOf([item({ p: '🔴', text: 'open FAIL', id: 'fail:a' }), item({ text: 'then escalate' })], { all: { n: 2, fill: 'all' }, ctx: { tokens: 412000, mark: 500 }, task: { spawns: 3, usd: 0.41, budget: 5, left: 2 } }), pane: panePayload({ decisions: decisions([dec(0)], []) }) })
  const ui = await $.ui.mount({ plugin: 'abofles-guide', surface: 'desktop', component: 'Pane', requestId: 'abofles', props: PANE_PROPS }) // no tab seeking: the page as drawn
  const navs = (await ui.findAll({ type: 'Button' })).filter((b: any) => /^nav:/.test(String(b.props.key)))
  expect(navs.map((b: any) => b.props.key)).toEqual(['nav:home', 'nav:running', 'nav:guide', 'nav:resume', 'nav:decisions', 'nav:spawns', 'nav:overrides'])
  expect(navs.every((b: any) => b.props.plain === true && /^\u2007+$/.test(b.props.label))).toBe(true) // a blank press area, no name
  expect(navs.map((b: any) => b.props.dimColor)).toEqual([false, true, true, true, true, true, true]) // the page shown is the one not dimmed
  const icons = (await ui.findAll({ type: 'Svg' })).filter((x: any) => x.props.width === 36)
  expect(icons.map((x: any) => x.props.alt)).toEqual(['Home, shown', 'Running', 'Guide', 'Resume', 'Decisions', 'Agents', 'Overrides'])
  expect(icons.every((x: any) => /class="htile t-\w+"/.test(x.props.source))).toBe(true) // the header strips' own tiles
  expect(icons[0]!.props.source).toContain('background:color-mix(in srgb,var(--c) 80%,#000);color:#fff') // filled: the page shown
  // Home: the next step with its buttons, the numbers at a glance, where the work stands, the latest decision and receipt
  const alts = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  expect(alts.some(a => /^Next up, urgent priority\nopen FAIL$/.test(a))).toBe(true)
  expect(alts.some(a => a.startsWith('At a glance') && a.includes('Needs you\n2') && !a.includes('Running') && a.includes('Open FAIL\n1') && !a.includes('Saved'))).toBe(true)
  expect(alts.some(a => a.startsWith('Agents\n3\n54% saved\n$0.41 of $0.90 on Opus 5.5') && a.includes('Sonnet 5.5: 90K tokens, 1 agent, $0.40, 98% of spend') && !a.includes('Open FAIL'))).toBe(true) // the Spawns page's card; the open FAIL stays in the glance
  expect(alts.some(a => a.startsWith('Now\nwiring the pane'))).toBe(true)
  expect(alts.some(a => a.startsWith('Last decision') && a.includes('answer 0'))).toBe(true)
  expect((await ui.find({ key: 'check-all' }))?.props.variant).toBe('primary')
  expect(await ui.find({ key: 'fold:guide' })).toBeUndefined()
  // a press on a glyph moves to that page; its cards slide in for a moment
  await ui.press({ key: 'nav:spawns' })
  expect((await ui.find({ key: 'nav:spawns' }))?.props.dimColor).toBe(false)
  expect((await ui.find({ key: 'nav:home' }))?.props.dimColor).toBe(true)
  const page = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  expect(page.some(a => a.startsWith('History'))).toBe(true)
  expect(page.some(a => a.startsWith('At a glance'))).toBe(false) // one page at a time
  const moved = (await ui.findAll({ type: 'Svg' })).filter((x: any) => x.props.width !== 36 && !String(x.props.alt).startsWith('Abo Fles'))
  expect(moved.every((x: any) => /class="root enter"/.test(x.props.source))).toBe(true) // the page's cards slide in; the nav bar and the brand footer stay still
  // the brand footer closes every page: the mascot inlined, the English and Arabic name, no card around it, drawn still
  const foot = (await ui.findAll({ type: 'Svg' })).filter((x: any) => String(x.props.alt) === 'Abo Fles · ابو فليس')
  expect(foot.length).toBe(1)
  expect(foot[0]!.props.source).toContain('class="foot"')
  expect(foot[0]!.props.source).toContain('src="data:image/png;base64,')
  expect(foot[0]!.props.source).toContain('ابو فليس')
  expect(foot[0]!.props.source).not.toContain('class="card"')
  expect(foot[0]!.props.source).not.toContain('root enter')
  await ui.unmount()
})

test('the spend card: five titled columns with the tokens per model, on the Agents page and Home; a narrow pane puts each number under its model with its word', async ($, on) => {
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const spendPic = async (ui: any, first: string) => (await ui.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).startsWith(first))
  const home = await $.ui.mount({ plugin: 'abofles-guide', surface: 'desktop', component: 'Pane', requestId: 'abofles', props: PANE_PROPS })
  const card = await spendPic(home, 'Agents\n3\n')
  for (const title of ['Model', 'Tokens', 'Agents', 'Cost', '%']) expect([title, card?.props.source.includes(`>${title}</span>`)]).toEqual([title, true])
  for (const cell of ['>2K<', '>90K<', '>—<']) expect([cell, card?.props.source.includes(cell)]).toEqual([cell, true]) // Opus reported no tokens: a dash, not 0
  expect(card?.props.source.indexOf('>Tokens<')).toBeLessThan(card?.props.source.lastIndexOf('>Agents<')) // the columns in the asked order (the card's own title says Agents too)
  await home.press({ key: 'nav:spawns' })
  const page = await spendPic(home, '54% saved')
  expect(page?.props.source).toContain('>Agents</span>')
  expect(page?.props.alt).toContain('Open FAIL') // the Agents page keeps it; Home has it in At a glance
  await home.unmount()
  // narrow: no column titles, every number says what it is
  const narrow = await $.ui.mount({ plugin: 'abofles-guide', surface: 'desktop', component: 'Pane', requestId: 'abofles', props: { ...PANE_PROPS, bodyColumns: 30 } })
  await narrow.press({ key: 'nav:home' })
  const small = await spendPic(narrow, 'Agents\n3\n')
  expect(small?.props.source).toContain('54% saved')
  expect(small?.props.source).not.toContain('class="row thead"') // no column titles here
  expect(small?.props.source).toContain('90K tokens · 1 agent · $0.40')
  await narrow.unmount()
  // the terminal has the same five columns in text
  const term = await mountPane($, 'terminal')
  for (const re of [/^Model$/, /^Tokens$/, /^Agents$/, /^Cost$/, /^%$/, /^90K$/, /^—$/]) expect([String(re), await said(term, re)]).toEqual([String(re), true])
  await term.unmount?.()
})

test('nothing spawned: Home has no spend card', async ($, on) => {
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ spawns: { live: [], rows: [], totals: { spawns: 0, tokens: 0, usd: 0, base_usd: 0, saved_pct: 0, open_fail: 0, base_label: 'Opus 5.5', by_model: [] } } }) })
  const ui = await $.ui.mount({ plugin: 'abofles-guide', surface: 'desktop', component: 'Pane', requestId: 'abofles', props: PANE_PROPS })
  const alts = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  expect(alts.some(a => a.startsWith('Next up'))).toBe(true) // Home is the page drawn
  expect(alts.some(a => a.startsWith('Agents\n') || a.includes('% saved'))).toBe(false)
  await ui.unmount()
})

const MAIN_SWITCH = 'main: suggest Opus 5.5 · Medium for this 🟠 Sensitive task (now Opus 5.5 · Extra). Switch in the model picker, or say "keep".'

test('Main model shows the model in use like the other facts; what to do about it is a whole sentence under it, wrapped, never cut', async ($, on) => {
  await harness($, on, { guide: guideOf([item()], { main: MAIN_SWITCH }), pane: panePayload() })
  const card = async (u: any) => (await u.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).includes('Main model'))
  const wide = await mountPane($, 'desktop')
  const c = await card(wide)
  expect(String(c.props.alt)).toContain('Main model\nOpus 5.5 · Extra\nUse Opus 5.5 · Medium for this task. Switch in the model picker.')
  expect(String(c.props.source)).toContain('ink t-amber">Opus 5.5 · Medium</span>') // the part to act on
  expect(String(c.props.source)).not.toContain('v ink t-amber') // the value itself stays plain, like the other facts
  await wide.unmount()
  const narrow = await mountPane($, 'desktop', { bodyColumns: 30 })
  expect(String((await card(narrow)).props.source)).toContain('small tuck clamp') // two lines, no ellipsis in the middle of the advice
  await narrow.unmount()
  const term = await mountPane($, 'terminal')
  for (const re of [/^Main model$/, /^Opus 5\.5 · Extra$/, /^Use Opus 5\.5 · Medium for this task\. Switch in the model picker\.$/]) expect([String(re), await said(term, re)]).toEqual([String(re), true])
  await term.unmount()
})

test('Main model that fits says so, with no colour on the value', async ($, on) => {
  await harness($, on, { guide: guideOf([item()], { main: 'main: Opus 5.5 · Medium fits this 🟠 Sensitive task' }), pane: panePayload() })
  const ui = await mountPane($, 'desktop')
  const c = (await ui.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).includes('Main model'))
  expect(String(c.props.alt)).toContain('Main model\nOpus 5.5 · Medium\nFits this Sensitive task')
  await ui.unmount()
})

test('every Full details page is the main page\'s cards with nothing cut, with Back and Copy, and no raw markdown', async ($, on) => {
  const LONG = Array.from({ length: 60 }, (_, i) => `word${i}`).join(' ')
  const base = panePayload()
  await harness($, on, {
    guide: guideOf([item({ id: 'top', text: LONG }), item({ p: '🟠', id: 'b', text: 'the second item' })], { main: MAIN_SWITCH }),
    pane: { ...base, resume: { ...base.resume, now: [LONG], left: ['left one', LONG], last: [`10-22 07:19 · 1 Spawn · $0.28 · done: the pane · check: a; b · open: ${LONG}`] }, spawns: { ...base.spawns, rows: base.spawns.rows.map((r: any) => ({ ...r, description: LONG })) }, decisions: { you: [{ ts: '10-22 07:19', q: LONG, a: LONG }], judge: [{ ts: '10-22 07:20', q: 'judge: a question?', a: 'an answer' }], n_you: 1, n_judge: 1 } },
  })
  const ui = await mountPane($, 'desktop')
  const open = async (tab: string, key: string) => {
    await ui.press({ key: `nav:${tab}` })
    await ui.press({ key })
    const pics = await ui.findAll({ type: 'Svg' })
    expect(await ui.find({ type: 'Markdown' })).toBeUndefined()
    expect(await has(ui, 'detail-back')).toBe(true)
    expect(await has(ui, 'detail-copy')).toBe(true)
    return { alt: pics.map((x: any) => String(x.props.alt)).join('\n'), src: pics.map((x: any) => String(x.props.source)).join('\n') }
  }
  const back = () => ui.press({ key: 'detail-back' })
  let d = await open('guide', 'detail:guide')
  for (const s of ['Next up, ', 'Then, ', LONG, 'the second item', 'Main model']) expect([s, d.alt.includes(s)]).toEqual([s, true])
  await back()
  d = await open('resume', 'detail:resume')
  for (const s of ['Now', LONG, 'Left', 'left one']) expect([s, d.alt.includes(s)]).toEqual([s, true])
  await back()
  d = await open('resume', 'detail:receipt:0')
  for (const s of ['$0.28', 'done: the pane', 'check: a; b', LONG]) expect([s, d.alt.includes(s)]).toEqual([s, true])
  expect(d.src).toContain('class="bul"') // a list reads as a list
  await back()
  d = await open('spawns', 'detail:spawns')
  for (const s of ['History', LONG]) expect([s, d.alt.includes(s)]).toEqual([s, true])
  await back()
  d = await open('decisions', 'detail:decisions')
  for (const s of ['By you', 'By Judge', LONG, 'a question?', 'an answer']) expect([s, d.alt.includes(s)]).toEqual([s, true])
  await back()
  await ui.unmount()
})

// ---- Running

const AGENT = { id: 'a1', description: 'build the card', type: 'abofles:abofles-worker', status: 'running' }

// The engine's side of what the Running page reads: the agents, the turn events, the tools (each call recorded in `calls`).
function workOn(on: any, list: { value: object[]; fail?: boolean }, calls: any[] = [], answer: (e: any) => object = () => ({ result: {}, text: 'ok' })) {
  on('agent.list', () => {
    if (list.fail) throw new Error('no list')
    return { value: list.value }
  })
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId, text: e.text }))
  on('turn.step', async function* ($: any, e: any) {
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn', usage: { model: 'claude-sonnet-5-5', input_tokens: 500, output_tokens: 100, cache_read_input_tokens: 400, cache_creation_input_tokens: 0 } }
  })
  on('tool.call', ($: any, e: any) => {
    calls.push(e)
    return answer(e)
  })
  on('classic.Stop', () => ({}))
  on('classic.SubagentStop', () => ({}))
}
const step = async ($: any, agentId?: string) => {
  for await (const _ of $.turn.step({ turnId: 't1', index: 0, model: 'claude-sonnet-5-5', effort: 'high', messageCount: 1, agentId })) void _
}
const mountDesk = async ($: any, tab: string, props: object = {}) => {
  const ui = await $.ui.mount({ plugin: 'abofles-guide', surface: 'desktop', component: 'Pane', requestId: 'abofles', props: { ...PANE_PROPS, ...props } })
  if (tab !== 'home') await ui.press({ key: `nav:${tab}` })
  return ui
}

test('tool labels say what a loop does from the call itself; elapsed reads 45s, 2m 05s, 1h 12m', () => {
  expect(toolLabel('Read', { file_path: '/repo/hooks/html.ts' })).toBe('Read html.ts')
  expect(toolLabel('Edit', { file_path: 'C:\\repo\\pane.tsx' })).toBe('Edit pane.tsx')
  expect(toolLabel('Bash', { command: 'npm test\necho done', description: 'Run the tests' })).toBe('Bash: Run the tests')
  expect(toolLabel('Bash', { command: 'npm test\necho done' })).toBe('Bash: npm test')
  expect(toolLabel('Grep', { pattern: 'agent.spawn' })).toBe('Grep agent.spawn')
  expect(toolLabel('WebFetch', { url: 'https://example.com/a/b' })).toBe('WebFetch example.com')
  expect(toolLabel('mcp__docs__query-docs', {})).toBe('query-docs')
  expect(toolLabel('Agent', { description: 'review it' })).toBe('Agent: review it')
  expect(toolLabel('Bash', { command: 'x'.repeat(300) }).length).toBeLessThanOrEqual(90)
  expect(fmtElapsed(45_000)).toBe('45s')
  expect(fmtElapsed(125_000)).toBe('2m 05s')
  expect(fmtElapsed(72 * 60_000)).toBe('1h 12m')
  expect(fmtElapsed(-5)).toBe('0s')
})

test('Running has its own nav page and an empty state; with nothing running Home has no Running card and the tile no dot', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const home = await mountDesk($, 'home')
  expect(await has(home, 'nav:running')).toBe(true)
  expect(await altLines(home)).not.toContain('Running, shown')
  const tile = (await home.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).startsWith('Running'))
  expect(tile?.props.alt).toBe('Running')
  expect((await home.findAll({ type: 'Svg' })).some((x: any) => /^Running\n/.test(String(x.props.alt)))).toBe(false)
  await home.press({ key: 'nav:running' })
  expect(await said(home, /^Nothing running\. Agents and commands show here\.$/)).toBe(true)
  await home.unmount()
})

test('a running agent: its side, ask, last tool, model, tool count, tokens and elapsed, on the page and on Home, with no model call', async ($, on) => {
  const calls: any[] = []
  const list = { value: [AGENT] }
  workOn(on, list, calls)
  const { clock } = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.turn.start({ text: 'go', turnId: 't1', agentId: 'a1' } as never)
  await step($, 'a1')
  await $.tool.call({ tool: 'Read', file_path: '/repo/hooks/html.ts', agentId: 'a1' } as never)
  await clock.advance(125_000)
  const home = await mountDesk($, 'home')
  const lines = await altLines(home)
  expect(lines).toContain('Running')
  for (const t of ['Work', 'build the card', 'Read html.ts', 'Sonnet 5.5 · High · 1 tool · 1K tokens', '2m 05s']) expect([t, lines.includes(t)]).toEqual([t, true]) // the side comes from the hook's row of this agent
  const tile = (await home.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).startsWith('Running, 1 running'))
  expect(tile).toBeDefined()
  expect(lines).not.toContain('At a glance\nRunning') // the card holds it: At a glance does not say it twice
  await home.press({ key: 'nav:running' })
  expect(await said(home, /^1$/)).toBe(true) // the title says Running: the pill holds the count
  expect(await said(home, /^Read html\.ts$/)).toBe(true)
  expect(await has(home, 'open:a1')).toBe(true)
  expect(await has(home, 'stop:a1')).toBe(false) // a foreground agent: TaskStop takes background work and named agents only
  expect(calls.map(c => c.tool)).toEqual(['Read']) // nothing but the agent's own tool ran
  // the agent ends: its row and its notes go
  list.value = []
  await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer', agentId: 'a1' } as never)
  expect(await said(home, /^Nothing running/)).toBe(true)
  await home.unmount()
})

test('a waiting agent says so; one with no notes yet still draws, by its type', async ($, on) => {
  workOn(on, { value: [{ id: 'z9', description: 'watch the build', type: 'general-purpose', status: 'waiting' }] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const ui = await mountDesk($, 'running')
  const lines = await altLines(ui)
  for (const t of ['General-purpose', 'watch the build', 'waiting']) expect([t, lines.includes(t)]).toEqual([t, true])
  await ui.unmount()
})

test('an agent whose reply is in but holds its own background command says replied, not waiting; its command says who started it', async ($, on) => {
  const list = { value: [{ id: 'g1', description: 'Push DRP-461 BE', type: 'abofles:abofles-git', status: 'running' }] as object[] }
  workOn(on, list, [], e => (e.tool === 'Bash' ? { result: { backgroundTaskId: 'ci1' }, text: 'started' } : { result: {}, text: 'ok' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.turn.start({ text: 'go', turnId: 't1', agentId: 'g1' } as never)
  await $.tool.call({ tool: 'Bash', command: 'gh run watch 42', description: 'Wait for CI', run_in_background: true, agentId: 'g1' } as never)
  await $.turn.complete({ answer: 'pushed', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer', agentId: 'g1' } as never)
  list.value = [{ id: 'g1', description: 'Push DRP-461 BE', type: 'abofles:abofles-git', status: 'waiting' }]
  const ui = await mountDesk($, 'running')
  const lines = await altLines(ui)
  for (const t of ['Git', 'Push DRP-461 BE', 'replied', 'Waits for its command: Wait for CI', 'Wait for CI', 'started by Git']) expect([t, lines.includes(t)]).toEqual([t, true])
  expect(lines).not.toContain('waiting')
  await ui.unmount()
  // a message wakes it: it works again, and a held turn says waiting once more
  await $.turn.start({ text: 'ci done', turnId: 't2', agentId: 'g1' } as never)
  const again = await mountDesk($, 'running')
  const after = await altLines(again)
  expect(after).toContain('waiting')
  expect(after).not.toContain('replied')
  await again.unmount()
})

test('a one-line ask keeps only its own line: the spare estimated line falls under the card, not above the command', async ($, on) => {
  workOn(on, { value: [] }, [], () => ({ result: { backgroundTaskId: 'v1' }, text: 'started' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'python3 clone.py --all', description: 'Generate full clip in each cloned voice', run_in_background: true } as never)
  const ui = await mountDesk($, 'running')
  const row = (await ui.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).startsWith('Shell\nGenerate full clip')) // the command's own card, not the main turn's last tool
  const src = String(row?.props.source)
  expect(src).toContain('python3 clone.py --all')
  expect(/-webkit-line-clamp:\d;max-height:\d+px/.test(src)).toBe(true) // the ask's room is a ceiling, not a fixed height
  await ui.unmount()
})

test('a background command shows at once; Stop asks first, Keep running takes it back, Stop command runs TaskStop and the row goes', async ($, on) => {
  const calls: any[] = []
  workOn(on, { value: [] }, calls, e => (e.tool === 'Bash' ? { result: { backgroundTaskId: 'b1' }, text: 'started' } : { result: {}, text: 'stopped' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'sleep 300', description: 'long sleep', run_in_background: true } as never)
  const ui = await mountDesk($, 'running')
  for (const t of ['Background', 'Shell', 'long sleep', 'sleep 300']) expect([t, await said(ui, new RegExp(`^${t}$`))]).toEqual([t, true])
  expect(await has(ui, 'open:b1')).toBe(false) // a command has no transcript to open
  await ui.press({ key: 'stop:b1' })
  expect(await labelOf(ui, 'stop-yes:b1')).toBe('Stop command')
  expect(calls.some(c => c.tool === 'TaskStop')).toBe(false)
  await ui.press({ key: 'stop-no:b1' })
  expect(await has(ui, 'stop:b1')).toBe(true)
  await ui.press({ key: 'stop:b1' })
  await ui.press({ key: 'stop-yes:b1' })
  expect(calls.find(c => c.tool === 'TaskStop')?.task_id).toBe('b1')
  expect(await said(ui, /^long sleep$/)).toBe(false)
  await ui.unmount()
})

test('a refused Stop keeps the row and says why', async ($, on) => {
  workOn(on, { value: [] }, [], e => (e.tool === 'TaskStop' ? { deny: 'not yours' } : { result: { backgroundTaskId: 'b2' }, text: '' }))
  const h = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'sleep 9', description: 'nap' } as never)
  const ui = await mountDesk($, 'running')
  await ui.press({ key: 'stop:b2' })
  await ui.press({ key: 'stop-yes:b2' })
  expect(await said(ui, /^nap$/)).toBe(true)
  expect(h.toasts.some((t: string) => /Could not stop it: not yours/.test(t))).toBe(true)
  await ui.unmount()
})

test('every Stop names the work still in flight: a task that ended goes, a new one and the scheduled wake-ups show, a subagent stays the agent list\'s', async ($, on) => {
  workOn(on, { value: [] }, [], () => ({ result: { backgroundTaskId: 'old' }, text: '' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'sleep 1', description: 'ended quietly' } as never)
  const ui = await mountDesk($, 'running')
  expect(await said(ui, /^ended quietly$/)).toBe(true)
  await $.classic.Stop({ stop_hook_active: false, background_tasks: [
    { id: 'm1', type: 'monitor', status: 'running', description: 'watch the deploy', tool: 'watch' },
    { id: 's1', type: 'subagent', status: 'running', description: 'not here', agent_type: 'general-purpose' },
  ], session_crons: [{ id: 'c1', schedule: '0 9 * * 1-5', recurring: true, prompt: 'check the build' }] } as never)
  expect(await said(ui, /^ended quietly$/)).toBe(false)
  expect(await said(ui, /^not here$/)).toBe(false)
  for (const t of ['Monitor', 'watch the deploy', 'Scheduled', 'check the build', '0 9 \\* \\* 1-5 · repeats']) expect([t, await said(ui, new RegExp(`^${t}$`))]).toEqual([t, true])
  expect(await has(ui, 'stop:c1')).toBe(false) // a scheduled wake-up is no task to stop
  expect((await altLines(ui)).slice(0, 12).join(' ')).toContain('Running 1')
  await ui.unmount()
})

test('a task notification takes its task off the page, inside a running turn too', async ($, on) => {
  workOn(on, { value: [] }, [], () => ({ result: { backgroundTaskId: 'b3' }, text: '' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'sleep 2', description: 'short nap' } as never)
  const ui = await mountDesk($, 'running')
  expect(await said(ui, /^short nap$/)).toBe(true)
  await $.session.append({ message: { role: 'user', content: [{ type: 'text', text: '<task-notification><task-id>b3</task-id><status>completed</status></task-notification>' }] }, door: 'queue', origin: { kind: 'task-notification' }, uuid: 'u9' } as never)
  expect(await said(ui, /^short nap$/)).toBe(false)
  await ui.unmount()
})

test('desktop: Full details shows what an agent did, newest first, and never asks for a main view; Back returns to Running', async ($, on) => {
  const views: object[] = []
  workOn(on, { value: [AGENT] })
  on('ui.view', ($: any, e: any) => {
    views.push(e)
    return { deny: 'no main view here' }
  })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.turn.start({ text: 'go', turnId: 't1', agentId: 'a1' } as never)
  await $.tool.call({ tool: 'Read', file_path: '/repo/one.ts', agentId: 'a1' } as never)
  await $.tool.call({ tool: 'Edit', file_path: '/repo/two.ts', agentId: 'a1' } as never)
  const ui = await mountDesk($, 'running')
  expect(await labelOf(ui, 'open:a1')).toBe('Full details')
  await ui.press({ key: 'open:a1' })
  expect(views).toEqual([])
  const lines = await altLines(ui)
  expect(lines).toContain('Recent tools')
  const newest = lines.lastIndexOf('Edit two.ts')
  expect(newest).toBeGreaterThan(lines.indexOf('Recent tools'))
  expect(newest).toBeLessThan(lines.lastIndexOf('Read one.ts'))
  expect(await has(ui, 'detail-copy')).toBe(true)
  expect(await labelOf(ui, 'detail-back')).toContain('Running')
  await ui.press({ key: 'detail-back' })
  expect(await has(ui, 'open:a1')).toBe(true)
  await ui.unmount()
})

test('desktop: Full details on a command shows the end of its output, says when there is none or it cannot be read, and an ended row leaves a plain note', async ($, on) => {
  workOn(on, { value: [] }, [], () => ({ result: { backgroundTaskId: 'b7' }, text: 'Command running in background with ID: b7. Output is being written to: /tmp/s/tasks/b7.output. You will be notified.' }))
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'npm test', description: 'run the tests', run_in_background: true } as never)
  w.files['/tmp/s/tasks/b7.output'] = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join('\n')
  const ui = await mountDesk($, 'running')
  await ui.press({ key: 'open:b7' })
  const lines = await altLines(ui)
  expect(lines).toContain('line 40')
  expect(lines).not.toContain('line 10') // the last thirty
  expect(lines).toContain('last 30 of 40')
  w.files['/tmp/s/tasks/b7.output'] = ''
  await $.classic.Stop({ stop_hook_active: false } as never)
  expect(await said(ui, /^No output yet\.$/)).toBe(true)
  delete w.files['/tmp/s/tasks/b7.output']
  await $.classic.Stop({ stop_hook_active: false } as never)
  expect(await said(ui, /^Too large or gone: \/tmp\/s\/tasks\/b7\.output$/)).toBe(true)
  await $.classic.Stop({ stop_hook_active: false, background_tasks: [] } as never)
  expect(await said(ui, /^Nothing to show here any more\.$/)).toBe(true)
  await ui.unmount()
})

test('a path in a result that does not end in the task\'s own id is not taken as its output', async ($, on) => {
  workOn(on, { value: [] }, [], () => ({ result: { backgroundTaskId: 'b8' }, text: 'see /etc/passwd and /tmp/other.output' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.tool.call({ tool: 'Bash', command: 'x', description: 'y' } as never)
  const ui = await mountDesk($, 'running')
  expect(await has(ui, 'open:b8')).toBe(false)
  await ui.unmount()
})

test('terminal: Open puts the agent in the main view and a refusal is a toast; while one is in view its row has no Open and the card leads back', async ($, on) => {
  const views: object[] = []
  let answer: OpEventResult<'ui.view'> = { value: {} }
  workOn(on, { value: [AGENT] })
  on('ui.view', ($: any, e: any) => {
    views.push(e)
    return answer
  })
  const h = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const open = async (props: object = {}) => {
    const ui = await mountRaw($, 'terminal', props)
    if ((await labelOf(ui, 'fold-caret:running')) === '▸') await ui.press({ key: 'fold:running' })
    return ui
  }
  const ui = await open()
  await ui.press({ key: 'open:a1' })
  expect(views).toEqual([{ agentId: 'a1' }])
  answer = { deny: 'no such view here' }
  await ui.press({ key: 'open:a1' })
  expect(h.toasts.join(' ')).toContain('Cannot open it here')
  expect(h.toasts.join(' ')).toContain('no such view here')
  await ui.unmount()
  answer = { value: {} }
  const viewing = await open({ view: { agentId: 'a1' } })
  expect(await has(viewing, 'open:a1')).toBe(false)
  await viewing.press({ key: 'view-main' })
  expect(views[views.length - 1]).toEqual({})
  await viewing.unmount()
})

test('Running place = off: no page and no Home card; a state file from older hooks keeps both', async ($, on) => {
  workOn(on, { value: [AGENT] })
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ places: { ...PLACES, running: 'off' } }) })
  const off = await mountDesk($, 'home')
  expect(await has(off, 'nav:running')).toBe(false)
  expect((await altLines(off)).includes('build the card')).toBe(false)
  await off.unmount()
  w.state = { guide: guideOf([item()]), pane: panePayload({ places: { guide: 'pane', resume: 'pane', spawns: 'pane' } }) }
  await $.classic.Stop({ stop_hook_active: false } as never)
  const old = await mountDesk($, 'home')
  expect(await has(old, 'nav:running')).toBe(true)
  await old.unmount()
})

test('Home holds the first three, with a button to the whole page', async ($, on) => {
  workOn(on, { value: [1, 2, 3, 4].map(n => ({ id: `a${n}`, description: `task ${n}`, type: 'Explore', status: 'running' })) })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const ui = await mountDesk($, 'home')
  expect(await said(ui, /^task 3$/)).toBe(true)
  expect(await said(ui, /^task 4$/)).toBe(false)
  expect(await labelOf(ui, 'home-running')).toBe('All running (4)')
  await ui.press({ key: 'home-running' })
  expect(await said(ui, /^task 4$/)).toBe(true)
  await ui.unmount()
})

test('terminal: a Running card only while something runs; a note that fails never stops the tool', async ($, on) => {
  const list: { value: object[]; fail?: boolean } = { value: [AGENT] }
  workOn(on, list)
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  const ui = await mountRaw($, 'terminal')
  expect(await labelOf(ui, 'fold:running')).toBe('Running')
  await ui.unmount()
  list.value = []
  const idle = await mountRaw($, 'terminal')
  expect(await has(idle, 'fold:running')).toBe(false)
  await idle.unmount()
  list.fail = true
  const ran = await $.tool.call({ tool: 'Bash', command: 'true', description: 'x' } as never)
  expect((ran as any).text).toBe('ok')
})

test('Main model with your own setting: it fits with no colour, or names your setting in amber', async ($, on) => {
  on('classic.Stop', () => ({}))
  const w = await harness($, on, { guide: guideOf([item()], { main: 'Opus 5.5 · Medium fits your setting' }), pane: panePayload() })
  const lines = async () => {
    const ui = await mountPane($, 'desktop')
    const out = await altLines(ui)
    await ui.unmount()
    return out
  }
  expect(await lines()).toContain('Fits your setting')
  w.state = { guide: guideOf([item()], { main: 'suggest Opus 5.5 · Medium, your setting (now Opus 5.5 · Extra)' }), pane: panePayload() }
  await $.classic.Stop({ stop_hook_active: false } as never)
  const out = await lines()
  expect(out).toContain('Opus 5.5 · Extra')
  expect(out).toContain('Your setting is Opus 5.5 · Medium. Switch in the model picker.')
})

test('the nav bar is always one row: edge to edge when full, in the middle with few pages, smaller tiles in a narrow pane', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item()]), pane: panePayload({ decisions: { you: [], judge: [], n_you: 0, n_judge: 0 } }) })
  const tiles = async (ui: any) => (await ui.findAll({ type: 'Svg' })).filter((x: any) => x.props.alt && /^(Home|Running|Guide|Resume|Decisions|Agents|Overrides)(,|$)/.test(String(x.props.alt)) && !String(x.props.alt).includes('\n'))
  const wide = await mountDesk($, 'home')
  const bar = await wide.find({ key: 'navbar' })
  expect(bar?.props.flexWrap).toBe('nowrap')
  expect(bar?.props.justifyContent).toBe('space-between')
  expect((await tiles(wide)).map((x: any) => x.props.width)).toEqual([36, 36, 36, 36, 36, 36, 36])
  await wide.unmount()
  const narrow = await mountDesk($, 'home', { bodyColumns: 18 })
  const small = (await tiles(narrow)).map((x: any) => x.props.width)
  expect(small.length).toBe(7)
  expect(small.every((n: number) => n < 36 && n >= 24)).toBe(true)
  await narrow.unmount()
  void w
})

test('the overrides editor shows every key\'s description whole, with no Read more', async ($, on) => {
  const long = 'Hours a reviewer PASS counts for a Sensitive change on the same topic'
  const base = panePayload() as any
  await harness($, on, { guide: guideOf([item()]), pane: { ...base, overrides: { ...base.overrides, keys: [{ key: 'Review memory', kind: 'number', options: [], value: '2', source: 'default', default: '2' }, { key: 'Budget raise', kind: 'select', options: ['auto', 'ask'], value: 'auto', source: 'default', default: 'auto' }] } } })
  for (const surface of SURFACES) {
    const ui = await mountEditor($, surface, { bodyColumns: 34 })
    if (await has(ui, 'fold:g:Agents') && (await labelOf(ui, 'fold-caret:g:Agents')) === '▸') await ui.press({ key: 'fold:g:Agents' })
    expect([surface, await said(ui, new RegExp(`^${long}$`))]).toEqual([surface, true])
    expect(await said(ui, /^auto: \+half the start budget, twice at most, then asks; ask: always asks$/)).toBe(true)
    expect(await has(ui, 'more:help:Review memory')).toBe(false)
    expect(await has(ui, 'more:help:Budget raise')).toBe(false)
    await ui.unmount()
  }
})

// ---- each setting's own target, Reset and badge

const K = (key: string, value: string, source: string, more: object = {}) => ({ key, kind: 'select', options: ['full', 'short', 'off'], value, source, default: 'full', project: source === 'project' ? value : null, user: source === 'user' ? value : null, ...more })
const N = (key: string, value: string, source = 'default') => ({ key, kind: 'number', options: [], value, source, default: '3', project: source === 'project' ? value : null, user: source === 'user' ? value : null })

for (const surface of SURFACES) {
  test(`${surface}: an overridden setting has a badge, a target and Reset; one at default has none of them until it gets a value`, async ($, on) => {
    await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '3')]))
    const ui = await mountEditor($, surface)
    const badges = async () => (surface === 'desktop' ? (await ui.findAll({ type: 'Svg' })).filter((x: any) => x.props.alt === 'Overridden').length : (await ui.findAll({ type: 'Text', text: /^set$/ })).length)
    expect(await badges()).toBe(1)
    expect(await propOf(ui, 'where:Receipt:project', 'label')).toBe('✓ This project')
    expect(await propOf(ui, 'where:Receipt:user', 'label')).toBe('All projects')
    expect(await propOf(ui, 'where:Receipt:user', 'dimColor')).toBe(true) // the chosen one is at full strength
    expect(await propOf(ui, 'where:Receipt:user', 'variant')).toBe('secondary') // a button with its border
    expect(await propOf(ui, 'reset:Receipt', 'variant')).toBe('secondary')
    expect(await labelOf(ui, 'reset:Receipt')).toBe('Reset')
    for (const key of ['where:Parallel:project', 'where:Parallel:user', 'reset:Parallel']) expect([key, await has(ui, key)]).toEqual([key, false])
    await ui.input({ key: 'in:Parallel', text: '5', kind: 'change' })
    expect(await propOf(ui, 'where:Parallel:project', 'label')).toBe('✓ This project') // a new value starts on this project
    expect(await has(ui, 'reset:Parallel')).toBe(false) // nothing to reset yet: Discard takes it back
    expect(await badges()).toBe(1) // not overridden until it is saved
    await ui.unmount()
  })

  test(`${surface}: All projects writes to the user file and takes the value out of this project; the toggle alone counts as a change`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '3')]))
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'where:Receipt:user' })
    expect(await propOf(ui, 'where:Receipt:user', 'label')).toBe('✓ All projects')
    expect(await said(ui, /^1 unsaved$/)).toBe(true)
    await ui.press({ key: 'where:Receipt:project' })
    expect(await said(ui, /unsaved/)).toBe(false) // back where it is: nothing to save
    await ui.press({ key: 'where:Receipt:user' })
    await ui.press({ key: 'ask' })
    expect(w.fills[0]?.text).toBe('Apply in ~/.claude/CLAUDE.md `## abofles overrides`: - Receipt = short Apply in CLAUDE.md `## abofles overrides`: - Receipt removed')
    const after = [K('Receipt', 'short', 'user'), N('Parallel', '3')]
    willSave(w, after)
    willSave(w, after)
    await ui.press({ key: 'save' })
    expect(w.runs.map((r: any) => r.argv.slice(-2))).toEqual([['--scope', 'user'], ['--scope', 'project']]) // all projects first
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Receipt: 'short' } })
    expect(JSON.parse(w.stdins[1] ?? '')).toEqual({ set: { Receipt: '' } })
    expect(w.toasts).toContain('Saved to ~/.claude/CLAUDE.md and CLAUDE.md')
    expect(await said(ui, /unsaved/)).toBe(false)
    expect(await propOf(ui, 'where:Receipt:user', 'label')).toBe('✓ All projects')
    await ui.unmount()
  })

  test(`${surface}: This project writes to the project file and leaves the all-projects line, so other projects keep it`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'user')]))
    const ui = await mountEditor($, surface)
    expect(await propOf(ui, 'where:Receipt:user', 'label')).toBe('✓ All projects')
    await ui.press({ key: 'where:Receipt:project' })
    willSave(w, [K('Receipt', 'short', 'project', { user: 'short' })])
    await ui.press({ key: 'save' })
    expect(w.runs.length).toBe(1)
    expect(w.runs[0]!.argv.slice(-2)).toEqual(['--scope', 'project'])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Receipt: 'short' } })
    await ui.unmount()
  })

  test(`${surface}: a failed second write keeps the draft and shows what the first one did`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'project')]))
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'where:Receipt:user' })
    willSave(w, [K('Receipt', 'short', 'project', { user: 'short' })])
    w.exits.push(0, 3)
    w.stderrs.push('', 'abofles: CLAUDE.md changed while saving — reload the pane and save again')
    await ui.press({ key: 'save' })
    expect(w.toasts).toContain('abofles: CLAUDE.md changed while saving — reload the pane and save again')
    expect(await said(ui, /^1 unsaved$/)).toBe(true) // the row still wants to move
    await ui.unmount()
  })

  test(`${surface}: a value the hooks cannot read says what they use instead until it is edited`, async ($, on) => {
    await harness($, on, ovState([K('Receipt', 'brief', 'project', { invalid: true }), K('Questions', 'off', 'project')]))
    const ui = await mountEditor($, surface)
    expect(await said(ui, /^The hooks cannot read "brief" and use full instead\. Pick a value or Reset\.$/)).toBe(true)
    expect(await said(ui, /cannot read "off"/)).toBe(false) // a value they read has no note
    await ui.select({ key: 'sel:Receipt', value: 'short' })
    expect(await said(ui, /cannot read/)).toBe(false)
    await ui.unmount()
  })

  test(`${surface}: Reset is an edit until Save, can be undone, and says what it will do`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'project')]))
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'reset:Receipt' })
    expect(await said(ui, /^On save: back to default$/)).toBe(true)
    expect(await said(ui, /^· edited$/)).toBe(true)
    expect(await labelOf(ui, 'reset:Receipt')).toBe('Undo reset')
    expect(await has(ui, 'where:Receipt:user')).toBe(false)
    expect(await valueOf(ui, 'sel:Receipt')).toBe('@default')
    expect(w.runs).toEqual([]) // nothing written yet
    await ui.press({ key: 'reset:Receipt' })
    expect(await said(ui, /unsaved/)).toBe(false)
    expect(await valueOf(ui, 'sel:Receipt')).toBe('short')
    await ui.press({ key: 'reset:Receipt' })
    willSave(w, [K('Receipt', 'full', 'default')])
    await ui.press({ key: 'save' })
    expect(w.runs[0]!.argv.slice(-2)).toEqual(['--scope', 'project'])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Receipt: '' } })
    await ui.unmount()
  })

  test(`${surface}: Reset goes back one level: under a project value the all-projects value takes over, and an all-projects value says it changes every project`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'project', { user: 'off' })]))
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'reset:Receipt' })
    expect(await said(ui, /^On save: uses the all-projects value, off$/)).toBe(true)
    willSave(w, [K('Receipt', 'off', 'user')])
    await ui.press({ key: 'save' })
    expect(w.runs.length).toBe(1)
    expect(w.runs[0]!.argv.slice(-2)).toEqual(['--scope', 'project']) // the all-projects line stays
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Receipt: '' } })
    await ui.press({ key: 'reset:Receipt' })
    expect(await said(ui, /^On save: back to default in every project$/)).toBe(true)
    willSave(w, [K('Receipt', 'full', 'default')])
    await ui.press({ key: 'save' })
    expect(w.runs[1]!.argv.slice(-2)).toEqual(['--scope', 'user'])
    expect(JSON.parse(w.stdins[1] ?? '')).toEqual({ set: { Receipt: '' } })
    await ui.unmount()
  })

  test(`${surface}: Reset all asks first, says how many change every project, and writes only at Save`, async ($, on) => {
    const w = await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '5', 'user'), K('Guide', 'off', 'default')]))
    const ui = await mountEditor($, surface)
    await ui.press({ key: 'reset-all' })
    expect(await has(ui, 'reset-all')).toBe(false)
    expect(await said(ui, /^Resets 2 settings; 1 of them for every project\. Save applies it\.$/)).toBe(true)
    expect(await labelOf(ui, 'reset-all-yes')).toBe('Reset 2 settings')
    await ui.press({ key: 'reset-all-no' })
    expect(await has(ui, 'reset-all-yes')).toBe(false)
    expect(await said(ui, /unsaved/)).toBe(false)
    await ui.press({ key: 'reset-all' })
    await ui.press({ key: 'reset-all-yes' })
    expect(await said(ui, /^2 unsaved$/)).toBe(true)
    expect(w.runs).toEqual([])
    willSave(w, [K('Receipt', 'full', 'default'), N('Parallel', '3')])
    willSave(w, [K('Receipt', 'full', 'default'), N('Parallel', '3')])
    await ui.press({ key: 'save' })
    expect(w.runs.map((r: any) => r.argv.slice(-1)[0])).toEqual(['user', 'project'])
    expect(JSON.parse(w.stdins[0] ?? '')).toEqual({ set: { Parallel: '' } })
    expect(JSON.parse(w.stdins[1] ?? '')).toEqual({ set: { Receipt: '' } })
    expect(await has(ui, 'reset-all')).toBe(false) // nothing left to reset
    await ui.unmount()
  })

  test(`${surface}: the Overrides card shows what each setting does and where it is set`, async ($, on) => {
    await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '5', 'user')]))
    const ui = await mountPane($, surface)
    for (const re of [/^Receipt form; savings N = only past N%$/, /^Background agents at once$/, /^this project$/i, /^all projects$/i]) expect([String(re), await said(ui, re)]).toEqual([String(re), true])
    await ui.unmount()
  })
}

test('the Overrides card never cuts a name or a value: a long value goes under its name', async ($, on) => {
  const marks = '500, 600, 700, 750, 800, 850, 900, 950'
  await harness($, on, ovState([K('Main session', 'opus·medium', 'user'), { ...N('Context marks', marks, 'user'), kind: 'list' }]))
  const ui = await mountDesk($, 'overrides', { bodyColumns: 34 })
  const pic = (await ui.findAll({ type: 'Svg' })).find((x: any) => String(x.props.alt).split('\n').includes('Main session'))
  const src = String(pic?.props.source)
  expect(src).toContain('>Main session</span>')
  expect(src).toContain('>opus·medium</span>')
  expect(src).toContain('>All projects<') // the scope is the group's title, said once
  expect(src).toContain(marks)
  expect(src).toMatch(/class="one">Context marks<\/div>/)
  await ui.unmount()
})

test('the Overrides page holds Edit above the list, and groups the settings by where they apply', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '5', 'user')], []))
  const ui = await mountDesk($, 'overrides')
  const lines = await altLines(ui)
  expect(lines.indexOf('This project')).toBeLessThan(lines.indexOf('Receipt'))
  expect(lines.indexOf('Receipt')).toBeLessThan(lines.indexOf('All projects'))
  expect(lines.indexOf('All projects')).toBeLessThan(lines.indexOf('Parallel'))
  expect(await has(ui, 'edit-overrides')).toBe(true)
  await ui.unmount()
})

test('each scope has a card of its own, and what the rest uses stands under them', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '5', 'user')], []))
  const ui = await mountDesk($, 'overrides')
  const pics = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  const project = pics.find((a: string) => a.startsWith('This project\n'))
  const all = pics.find((a: string) => a.startsWith('All projects\n'))
  expect(project).toContain('Receipt')
  expect(project).not.toContain('Parallel')
  expect(all).toContain('Parallel')
  expect(all).not.toContain('Receipt')
  expect(pics).toContain('Everything else uses the defaults.')
  await ui.unmount()
})

test('a setting set in no file of a group draws no empty group', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'user')], []))
  const ui = await mountDesk($, 'overrides')
  const lines = await altLines(ui)
  expect(lines).toContain('All projects')
  expect(lines).not.toContain('This project')
  await ui.unmount()
})

test('the Edit page draws a line between settings and between groups, and the long list value gets a second line', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'project'), K('Guide', 'off', 'user'), N('Parallel', '3'), { ...N('Context marks', '500, 600, 700, 750, 800, 850, 900, 950', 'user'), kind: 'list' }], []))
  const ui = await mountEditor($, 'desktop')
  const alts = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  expect(alts.filter((a: string) => a === 'group divider').length).toBeGreaterThanOrEqual(2)
  expect(alts.filter((a: string) => a === 'row divider').length).toBeGreaterThanOrEqual(1)
  expect(alts).toContain('actions divider')
  expect(await valueOf(ui, 'in:Context marks')).toBe('500, 600, 700, 750, 800, 850, 900, 950')
  expect(await propOf(ui, 'in:Context marks', 'rows')).toBe(2) // the one long value shows whole
  await ui.unmount()
})

test('Edit sits on the Overrides title row, at its end; the title strip keeps its own size beside it', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'project')], []))
  const ui = await mountDesk($, 'overrides')
  const row = await ui.find({ key: 'section-head' })
  expect(row?.props.flexWrap).toBe('nowrap')
  expect(row?.props.justifyContent).toBe('space-between')
  const strip = (await ui.findAll({ type: 'Svg' })).find((x: any) => /class="head"/.test(String(x.props.source)))
  expect(strip?.props.width).toBeLessThan(260) // drawn at its size, not stretched over the pane
  expect(await has(ui, 'edit-overrides')).toBe(true)
  await ui.press({ key: 'edit-overrides' })
  expect(await has(ui, 'save')).toBe(true)
  await ui.unmount()
})

test('a setting on the Edit page reads one thing per line: its name and badge, what it does, the control, then its target and Reset', async ($, on) => {
  await harness($, on, ovState([K('Receipt', 'short', 'project'), N('Parallel', '3')], []))
  const ui = await mountEditor($, 'desktop')
  expect((await ui.find({ key: 'head:Receipt' }))?.props.justifyContent).toBe('space-between') // the badge at the end of the name's line
  expect(await propOf(ui, 'in:Parallel', 'rows')).toBeUndefined()
  await ui.unmount()
})

test('every card of a Full details page says its own text, so the desktop draws them all: Resume keeps Now, Needs you and Left', async ($, on) => {
  const base = panePayload() as any
  await harness($, on, { guide: guideOf([item()]), pane: { ...base, resume: { ...base.resume, now: ['the work now'], needs: [{ n: 4, text: 'ship it, A & B' }], needs_open: 1, left: ['left one'] } } })
  const ui = await mountDesk($, 'resume')
  await ui.press({ key: 'detail:resume' })
  const alts = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt))
  expect(alts.every((a: string) => a.trim() !== '')).toBe(true)
  expect(alts.some((a: string) => a.split('\n').includes('Needs you') && a.includes('ship it, A & B') && !a.startsWith('Now'))).toBe(true)
  expect(alts.some((a: string) => a.split('\n').includes('left one') && !a.startsWith('Now'))).toBe(true)
  await ui.unmount()
})

test('a Full details page spaces its cards like the main page and says its count as the main page does', async ($, on) => {
  const base = panePayload() as any
  const long = Array.from({ length: 60 }, (_, i) => `word${i}`).join(' ')
  await harness($, on, { guide: guideOf([item()]), pane: { ...base, resume: { ...base.resume, now: ['the work now'], needs: [{ n: 4, text: long }], needs_open: 1, left: ['left one'] } } })
  const ui = await mountDesk($, 'resume')
  await ui.press({ key: 'detail:resume' })
  const pics = await ui.findAll({ type: 'Svg' })
  const head = pics.find((x: any) => /class="head"/.test(String(x.props.source)))
  expect(String(head?.props.source)).toContain('class="pill t-amber"')
  const needs = pics.find((x: any) => String(x.props.source).includes('word59'))
  expect(String(needs?.props.source)).toContain('.root>.card{min-height:100%}') // room left over stays inside the card
  expect(String(needs?.props.source)).toContain('class="whole') // the text is never clamped
  await ui.unmount()
})

test('a receipt keeps all it said: its numbers on every line form, and its Full details page shows why, workflow, check, open and next', async ($, on) => {
  const line = '11-09 10:57 · 1 Agent + 1 memo · 63K Tokens · $0.03 · Saved 73% ($0.09) vs Opus 5.5 · largest: Read Haiku 5.5 · Low $0.02 (72%) · done: the page · why: asked · workflow: Read ✓ · Work ✓ · check: 181 pass · open: ship it · next: run /compact'
  const x = receiptOf(line)
  expect(x).toMatchObject({ spawns: '1 agent + 1 memo', tokens: '63K tokens', usd: '$0.03', saved: '73%', money: '73% saved ($0.09) vs Opus 5.5' })
  expect(x.details).toEqual(['largest: Read Haiku 5.5 · Low $0.02 (72%)', 'done: the page', 'why: asked', 'workflow: Read ✓ · Work ✓', 'check: 181 pass', 'open: ship it', 'next: run /compact'])
  const base = panePayload() as any
  await harness($, on, { guide: guideOf([item()]), pane: { ...base, resume: { ...base.resume, last: [line] } } })
  const ui = await mountDesk($, 'resume')
  const short = await altLines(ui)
  expect(short).toContain('check: 181 pass')
  const card = (await ui.findAll({ type: 'Svg' })).map((p: any) => String(p.props.source)).find((t: string) => t.includes('181 pass'))
  expect(card).not.toContain('Read ✓') // the short card stays short
  await ui.press({ key: 'detail:receipt:0' })
  const src = (await ui.findAll({ type: 'Svg' })).map((p: any) => String(p.props.source)).join('')
  for (const t of ['the page', '63K tokens', '73% saved ($0.09) vs Opus 5.5', '>why<', 'asked', '>workflow<', 'Read ✓ · Work ✓', '>check<', '>open<', '>next<', 'run /compact', '>largest<']) expect([t, src.includes(t)]).toEqual([t, true])
  await ui.unmount()
})

test('Needs you lists its items with a bullet: the memo\'s item number is not shown', async ($, on) => {
  const base = panePayload() as any
  await harness($, on, { guide: guideOf([item()]), pane: { ...base, resume: { ...base.resume, needs: [{ n: 4, text: 'ship it' }], needs_open: 1 } } })
  for (const surface of SURFACES) {
    const ui = await mountPane($, surface)
    expect(await said(ui, /^ship it$/)).toBe(true)
    expect(await said(ui, /^4$/)).toBe(false)
    await ui.unmount()
  }
})

// ---- 2.9.1: the overrides editor's files, Running's agent rows and Stop

test('two project files: Other overrides edits (and Save sends) only the lines of the file Save writes to; lines with no file keep the old reading', () => {
  const ov = (raw: object[]) => ({ files: { project: '/proj/CLAUDE.md', user: '/home/.claude/CLAUDE.md' }, keys: [], raw }) as any
  const two = [{ line: 'Topic: A = T2', source: 'project', path: '/proj/CLAUDE.md' }, { line: 'Topic: B = T3', source: 'project', path: '/proj/.claude/CLAUDE.md' }, { line: 'Topic: U = T1', source: 'user', path: '/home/.claude/CLAUDE.md' }]
  expect(rawBase(ov(two), 'project')).toEqual(['Topic: A = T2'])
  expect(rawBase(ov(two), 'user')).toEqual(['Topic: U = T1'])
  expect(rawBase(ov([{ line: 'Topic: A = T2', source: 'project' }, { line: 'Topic: B = T3', source: 'project' }]), 'project')).toEqual(['Topic: A = T2', 'Topic: B = T3'])
})

test('two agents with the same ask each get their own ledger row by agent id; a row with no id is matched by its ask, once', () => {
  const agents = [{ id: 'a1', description: 'build the card' }, { id: 'a2', description: 'build the card' }, { id: 'a3', description: 'check it' }, { id: 'a4', description: 'check it' }, { id: 'a5', description: 'build the card' }]
  const live = [
    { side: 'Work', model: 'sonnet', effort: 'high', description: 'build the card', ts: NOW, agent_id: 'a2' },
    { side: 'Verify', model: 'opus', effort: 'high', description: 'build the card', ts: NOW, agent_id: 'a1' },
    { side: 'Read', model: 'haiku', effort: 'low', description: 'check it', ts: NOW },
    { side: 'Plan', model: 'opus', effort: 'high', description: 'check it', ts: NOW, agent_id: null },
  ]
  const m = liveOf(agents, live)
  expect(['a1', 'a2', 'a3', 'a4', 'a5'].map(id => m.get(id)?.side)).toEqual(['Verify', 'Work', 'Read', 'Plan', undefined]) // a5: both rows of its ask name other agents
})

test('Stop shows only for an agent TaskStop can stop: a named, teammate, plugin-started or background one, not a foreground agent', async ($, on) => {
  const list = { value: [
    { id: 'f1', description: 'in the foreground', type: 'general-purpose', status: 'running' },
    { id: 'n1', description: 'named one', type: 'general-purpose', status: 'running', name: 'scout' },
    { id: 't1', description: 'a teammate', type: 'teammate', status: 'running', teammateId: 'tm@team' },
    { id: 'p1', description: 'plugin started', type: 'general-purpose', status: 'running', spawnedBy: 'other' },
    { id: 'b1', description: 'in the background', type: 'general-purpose', status: 'running' },
  ] }
  workOn(on, list)
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.classic.Stop({ stop_hook_active: false, background_tasks: [{ id: 'b1', type: 'subagent', status: 'running', description: 'in the background', agent_type: 'general-purpose' }] } as never)
  for (const surface of SURFACES) {
    const ui = surface === 'desktop' ? await mountDesk($, 'running') : await mountRaw($, 'terminal')
    if (surface === 'terminal' && (await labelOf(ui, 'fold-caret:running')) === '▸') await ui.press({ key: 'fold:running' })
    expect([surface, await has(ui, 'stop:f1')]).toEqual([surface, false])
    for (const id of ['n1', 't1', 'p1', 'b1']) expect([surface, id, await has(ui, `stop:${id}`)]).toEqual([surface, id, true])
    expect([surface, await has(ui, 'open:f1')]).toEqual([surface, true]) // its transcript still opens
    await ui.unmount()
  }
  // a pending ledger row with the agent's id is a background agent's; a row matched by its ask alone says nothing
  const row = { side: 'Work', model: 'sonnet', effort: 'high', description: 'x', ts: NOW }
  expect([canStop({ id: 'l1' }, [], { ...row, agent_id: 'l1' }), canStop({ id: 'l2' }, [], row), canStop({ id: 'l3' }, [], undefined)]).toEqual([true, false, false])
})

test('a command put in the background while a Stop reads the agent list stays: the Stop\'s task list is older than it', async ($, on) => {
  let gate: Promise<void> | null = null
  let reached = false
  on('agent.list', async () => {
    if (gate) {
      const g = gate
      gate = null
      reached = true
      await g
    }
    return { value: [] }
  })
  on('tool.call', () => ({ result: { backgroundTaskId: 'n1' }, text: 'started' }))
  on('classic.Stop', () => ({}))
  const { clock } = await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  let release = () => undefined as void
  gate = new Promise<void>(r => (release = r))
  const stop = $.classic.Stop({ stop_hook_active: false, background_tasks: [] } as never)
  for (let i = 0; i < 200 && !reached; i++) await Promise.resolve()
  expect(reached).toBe(true)
  await clock.advance(1000)
  await $.tool.call({ tool: 'Bash', command: 'sleep 60', description: 'started meanwhile', run_in_background: true } as never)
  release()
  await stop
  const ui = await mountDesk($, 'running')
  expect(await said(ui, /^started meanwhile$/)).toBe(true)
  await ui.unmount()
})

test("the payload keeps a live row's agent_id and the Session count from newer hooks; an empty agent_id is dropped", () => {
  const base = panePayload()
  const p = cleanPane({ ...base, spawns: { ...base.spawns, live: [{ side: 'Work', model: 'opus', effort: 'high', description: 'x', ts: 1, agent_id: 'abc' }, { side: 'Read', model: 'haiku', effort: 'low', description: 'y', ts: 2, agent_id: '' }], totals: { ...base.spawns.totals, sessions: 3 } } })
  expect(p?.spawns.live.map(l => l.agent_id ?? null)).toEqual(['abc', null])
  expect(p?.spawns.totals.sessions).toBe(3)
})

// ---- Tasks: the session's task list, kept after the turn ends

test('the task list follows update_plan: items, advance, done/start, wrap_up; TodoWrite and TaskCreate/TaskUpdate; other tools and subagents leave it', () => {
  const UP = 'mcp__ccd_turn__update_plan'
  let p = applyPlan(null, UP, { items: [{ content: 'Look', status: 'in_progress' }, { content: 'Build', status: 'pending' }, { content: '**Test**', status: 'pending' }] }, {}, 1)
  expect(p?.items.map(x => x.status)).toEqual(['in_progress', 'pending', 'pending'])
  expect(p?.items[2]!.content).toBe('Test')
  p = applyPlan(p, UP, { advance: true }, {}, 2)
  expect(p?.items.map(x => x.status)).toEqual(['completed', 'in_progress', 'pending'])
  p = applyPlan(p, UP, { done: [2], start: 3 }, {}, 3)
  expect(p?.items.map(x => x.status)).toEqual(['completed', 'completed', 'in_progress'])
  expect(p?.closed).toBe(false)
  // the app's wrap-up hides its list and changes no status (1070/1070 app results); a later advance resumes it
  const mid = applyPlan(null, UP, { items: [{ content: 'a', status: 'in_progress' }, { content: 'b', status: 'pending' }] }, {}, 3)
  const wrapped = applyPlan(mid, 'mcp__ccd_turn__wrap_up', { recap: 'x' }, {}, 4)
  expect(wrapped?.items.map(x => x.status)).toEqual(['in_progress', 'pending'])
  expect(wrapped).toMatchObject({ closed: true, wrapped: true })
  expect(planState(wrapped!, false, 0)).toMatchObject({ word: 'Wrapped up', line: '2 steps left open' })
  // the app's own result after such a wrap-up: "Plan updated: step 2 of 2 is in progress."
  const resumed = applyPlan(wrapped, UP, { advance: true }, {}, 5)
  expect(resumed?.items.map(x => x.status)).toEqual(['completed', 'in_progress'])
  expect(resumed?.closed).toBe(false)
  expect(resumed?.wrapped).toBeUndefined()
  expect(applyPlan(wrapped, UP, { items: [{ content: 'n', status: 'in_progress' }] }, {}, 6)?.closed).toBe(false)
  // items beside advance: the app ignores the advance, so the mod does too
  expect(applyPlan(null, UP, { items: [{ content: 'x', status: 'in_progress' }, { content: 'y', status: 'pending' }], advance: true }, {}, 6)?.items.map(x => x.status)).toEqual(['in_progress', 'pending'])
  // advance, done and start given as strings
  const s3 = applyPlan(null, UP, { items: [{ content: 'a', status: 'in_progress' }, { content: 'b', status: 'pending' }, { content: 'c', status: 'pending' }] }, {}, 7)
  expect(applyPlan(s3, UP, { advance: 'true' }, {}, 8)?.items.map(x => x.status)).toEqual(['completed', 'in_progress', 'pending'])
  expect(applyPlan(s3, UP, { done: ['1'], start: '3' }, {}, 8)?.items.map(x => x.status)).toEqual(['completed', 'pending', 'in_progress'])
  // start without done: the app keeps the step already in progress ("steps 1, 2 of 3 are in progress")
  expect(applyPlan(s3, UP, { start: 2 }, {}, 9)?.items.map(x => x.status)).toEqual(['in_progress', 'in_progress', 'pending'])
  p = applyPlan(p, 'mcp__ccd_turn__wrap_up', { recap: 'x' }, {}, 4)
  expect(p?.items.map(x => x.status)).toEqual(['completed', 'completed', 'in_progress'])
  expect(p?.closed).toBe(true)
  expect(applyPlan(null, UP, { advance: true }, {}, 5)).toBeNull()
  expect(applyPlan(p, 'Read', { file_path: '/a' }, {}, 5)).toBeNull()
  const t = applyPlan(null, 'TodoWrite', { todos: [{ content: 'a', status: 'completed', activeForm: 'A' }, { content: 'b', status: 'in_progress', activeForm: 'B' }] }, {}, 6)
  expect(t?.items.map(x => x.status)).toEqual(['completed', 'in_progress'])
  let c = applyPlan(null, 'TaskCreate', { subject: 'one', description: 'd' }, { result: 'Task #7 created successfully: one' }, 7)
  c = applyPlan(c, 'TaskCreate', { subject: 'two', description: 'd' }, { result: { task: { id: '8' } } }, 8)
  expect(c?.items.map(x => x.id)).toEqual(['7', '8'])
  c = applyPlan(c, 'TaskUpdate', { taskId: '7', status: 'completed' }, {}, 9)
  c = applyPlan(c, 'TaskUpdate', { taskId: '8', status: 'deleted' }, {}, 10)
  expect(c?.items).toEqual([{ content: 'one', status: 'completed', id: '7' }])
  expect(applyPlan(c, 'TaskUpdate', { taskId: '99', status: 'completed' }, {}, 11)).toBeNull()
  // a list seeded with no ids (an older hook copy): TaskUpdate's number names the step
  const seeded = { items: [{ content: 'one', status: 'pending' as const }, { content: 'two', status: 'pending' as const }], ts: 1, closed: false }
  expect(applyPlan(seeded, 'TaskUpdate', { taskId: '2', status: 'completed' }, {}, 12)?.items[1]!.status).toBe('completed')
})

// A replay of app result texts (scrubbed from real sessions): the card's step line must say what the app said.
test('Tasks replay: each call gives the same "step N of M" the app answered', () => {
  const UP = 'mcp__ccd_turn__update_plan'
  const inProg = (p: any) => p.items.flatMap((x: any, i: number) => (x.status === 'in_progress' ? [i + 1] : []))
  const calls: [string, object, number[]][] = [
    [UP, { items: [1, 2, 3, 4, 5].map(i => ({ content: `s${i}`, status: i === 1 ? 'in_progress' : 'pending' })) }, [1]], // "Plan shown (5 steps)."
    ['mcp__ccd_turn__wrap_up', { recap: 'r' }, [1]], // the app hides its list; nothing changes
    [UP, { advance: true }, [2]], // "Plan updated: step 2 of 5 is in progress."
    [UP, { start: 4 }, [2, 4]], // "Plan updated: steps 2, 4 of 5 are in progress."
    [UP, { done: [2, 4], start: 5 }, [5]], // "Plan updated: step 5 of 5 is in progress."
    ['mcp__ccd_turn__wrap_up', { recap: 'r' }, [5]],
  ]
  let p: any = null
  calls.forEach(([tool, a, want], i) => {
    p = applyPlan(p, tool, a as never, {}, i + 1) ?? p
    expect([i, inProg(p)]).toEqual([i, want])
  })
})

test('Tasks: TaskCreate after a wrap-up keeps the open steps (the hook mirror appends too)', () => {
  const w = { items: [{ content: 'a', status: 'in_progress' as const, id: '1' }], ts: 1, closed: true, wrapped: true }
  expect(applyPlan(w, 'TaskCreate', { subject: 'b' }, { result: 'Task #2 created' }, 2)?.items.map(x => x.content)).toEqual(['a', 'b'])
})

test('Running: an agent loop is quiet from its last tool call, else its start', () => {
  expect(loopQuietSince({ startedAt: 100 })).toBe(100)
  expect(loopQuietSince({ startedAt: 100, recent: [{ at: 150 }, { at: 900 }] })).toBe(900)
  expect(loopQuietSince({ startedAt: 100, recent: [{ at: 150 }, { at: 900 }], doneAt: 950 })).toBe(950) // quiet from the tool's return
  expect(loopQuietSince({ startedAt: 100, recent: [{ at: 150 }, { at: 900 }], doneAt: 200 })).toBe(Number.MAX_SAFE_INTEGER) // a tool still running: never quiet
})

test('Tasks: the hook mirror is adopted when 2 s newer and different; +N done counts done steps only; the last step names a step left above', () => {
  const a = { items: [{ content: 'a', status: 'in_progress' as const }, { content: 'b', status: 'pending' as const }], ts: 1000, closed: false }
  const b = { items: [{ content: 'a', status: 'completed' as const }, { content: 'b', status: 'completed' as const }], ts: 3500, closed: true }
  expect(adoptMirror(a, b)).toBe(true)
  expect(adoptMirror(a, { ...b, ts: 2000 })).toBe(false)
  expect(adoptMirror(a, { ...a, ts: 9000 })).toBe(false)
  // steps 1-3 pending above the current step 5: they are not "done above"
  const odd = { items: Array.from({ length: 9 }, (_, i) => ({ content: `s${i + 1}`, status: (i === 4 ? 'in_progress' : i === 3 ? 'completed' : 'pending') as PlanStatus })), ts: 1, closed: false }
  const blk = planBlock(odd, true, 0, 400, false)
  expect(blk.alt).not.toContain('Done: s1')
  // two in progress: the header follows the one further down
  const two = { items: [{ content: 'a', status: 'in_progress' as const }, { content: 'b', status: 'in_progress' as const }, { content: 'c', status: 'pending' as const }], ts: 1, closed: false }
  expect(planState(two, true, 0).line).toBe('Step 2 of 3')
  // a background command with nothing new for 30 min is quiet: the card does not wait on it
  expect(isQuiet({ startedAt: 0, size: 1 }, QUIET_MS - 1)).toBe(false)
  expect(isQuiet({ startedAt: 0, size: 1 }, QUIET_MS)).toBe(true)
  // a job whose output grew lately is not quiet, however long it has run; one whose output was never seen is never quiet
  expect(isQuiet({ startedAt: 0, outAt: QUIET_MS, size: 5 }, QUIET_MS + 1000)).toBe(false)
  expect(isQuiet({ startedAt: 0 }, QUIET_MS * 3)).toBe(false)
  // the tick's look at the output sizes: the first look records, growth moves outAt, no growth keeps it
  const t0 = [{ id: 'b1', kind: 'shell', desc: 'd', detail: null, startedAt: 100, out: '/x/b1.output' }]
  const t1 = withSizes(t0, new Map([['b1', 10]]), 500)
  expect(t1[0]).toMatchObject({ size: 10, outAt: 100 })
  const t2 = withSizes(t1, new Map([['b1', 25]]), 900)
  expect(t2[0]).toMatchObject({ size: 25, outAt: 900 })
  expect(withSizes(t2, new Map([['b1', 25]]), 5000)[0]!.outAt).toBe(900)
  expect(withSizes(t2, new Map(), 5000)[0]!.outAt).toBe(900)
})

test('the list state reads Working while the turn runs, Waiting on jobs, Paused once it ends, Done when all are checked', () => {
  const p = { items: [{ content: 'a', status: 'completed' as const }, { content: 'b', status: 'in_progress' as const }, { content: 'c', status: 'pending' as const }], ts: 1, closed: false }
  expect(planState(p, true, 0)).toMatchObject({ word: 'Working', line: 'Step 2 of 3', done: 1 })
  expect(planState(p, false, 2).word).toBe('Waiting on 2 jobs')
  expect(planState(p, false, 0)).toMatchObject({ word: 'Paused', tone: 'amber' })
  expect(planState({ ...p, items: p.items.map(x => ({ ...x, status: 'completed' as const })) }, false, 0)).toMatchObject({ word: 'Done', line: 'All 3 steps done' })
  // Home shows a window from the step before the current one; the rest is counted
  const long = { items: Array.from({ length: 11 }, (_, i) => ({ content: `step ${i + 1}`, status: (i < 5 ? 'completed' : i === 5 ? 'in_progress' : 'pending') as PlanStatus })), ts: 1, closed: false }
  const b = planBlock(long, true, 0, 400, false)
  expect(b.alt).toContain('Done: step 5')
  expect(b.alt).toContain('Now: step 6')
  expect(b.alt).not.toContain('Done: step 4')
  expect(b.cut).toBe(true)
  expect(planBlock(long, true, 0, 400, true).alt.filter(x => /^(Done|Now|To do): /.test(x)).length).toBe(11)
})

test('Home keeps the Tasks card after the turn ends, ticks it as the plan moves, and opens every step on All tasks', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload() })
  await $.turn.start({ text: 'go', turnId: 't1' } as never)
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: Array.from({ length: 9 }, (_, i) => ({ content: `step ${i + 1}`, status: i === 0 ? 'in_progress' : 'pending' })), scope: 'first_task' } as never)
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', advance: true } as never)
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: [{ content: 'sub step', status: 'in_progress' }], agentId: 'a1' } as never) // a subagent's own list is not the session's
  const home = await mountDesk($, 'home')
  let lines = await altLines(home)
  for (const t of ['Tasks', '1 / 9', 'Working', 'Step 2 of 9', 'Done: step 1', 'Now: step 2']) expect([t, lines.includes(t)]).toEqual([t, true])
  expect(lines).not.toContain('Now: sub step')
  await $.turn.complete({ turnId: 't1', answer: 'ok' } as never)
  lines = await altLines(home)
  expect(lines).toContain('Paused')
  expect(lines).toContain('Now: step 2') // still there after the turn
  await home.press({ key: 'detail:tasks' })
  lines = await altLines(home)
  expect(lines).toContain('To do: step 9')
  await home.unmount()
})

test('a finished list has Hide; Tasks hide = auto hides it at the turn end, next at the next turn; an unfinished list never hides; a new list shows again', async ($, on) => {
  workOn(on, { value: [] })
  const UP = 'mcp__ccd_turn__update_plan'
  const list = (n: number, done: number) => Array.from({ length: n }, (_, i) => ({ content: `s${i + 1}`, status: i < done ? 'completed' : i === done ? 'in_progress' : 'pending' }))
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ tasks_hide: 'auto' }) })
  await $.turn.start({ text: 'go', turnId: 't1' } as never)
  await $.tool.call({ tool: UP, items: list(3, 1) } as never)
  await $.turn.complete({ turnId: 't1', answer: 'ok' } as never)
  const home = await mountDesk($, 'home')
  expect(await altLines(home)).toContain('Paused') // unfinished: auto leaves it
  expect(await has(home, 'tasks:hide')).toBe(false)
  await $.turn.start({ text: 'go', turnId: 't2' } as never)
  await $.tool.call({ tool: UP, items: list(3, 3) } as never)
  expect(await altLines(home)).toContain('Done')
  expect(await has(home, 'tasks:hide')).toBe(true)
  await $.turn.complete({ turnId: 't2', answer: 'ok' } as never)
  expect(await altLines(home)).not.toContain('Tasks') // auto: gone with the turn that finished it
  await $.turn.start({ text: 'go', turnId: 't3' } as never)
  await $.tool.call({ tool: UP, items: list(2, 2) } as never)
  expect(await altLines(home)).toContain('Done') // a new list shows again
  await home.press({ key: 'tasks:hide' })
  expect(await altLines(home)).not.toContain('Tasks')
  await home.unmount()
})

test('Tasks hide = next keeps a finished list through its turn end and hides it when the next turn starts; Tasks place = off is its own test', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ tasks_hide: 'next' }) })
  await $.turn.start({ text: 'go', turnId: 't1' } as never)
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: [{ content: 'a', status: 'completed' }] } as never)
  await $.turn.complete({ turnId: 't1', answer: 'ok' } as never)
  const home = await mountDesk($, 'home')
  expect(await altLines(home)).toContain('Done')
  await $.turn.start({ text: 'next', turnId: 't2' } as never)
  expect(await altLines(home)).not.toContain('Tasks')
  await home.unmount()
})

test('Tasks place = off draws no Tasks card', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ places: { ...PLACES, tasks: 'off' } }) })
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: [{ content: 'a', status: 'in_progress' }] } as never)
  const home = await mountDesk($, 'home')
  expect(await altLines(home)).not.toContain('Tasks')
  await home.unmount()
})

test('the Tasks card says the share of steps done, rounded', () => {
  const p = (done: number, n: number) => ({ items: Array.from({ length: n }, (_, i) => ({ content: `s${i}`, status: (i < done ? 'completed' : i === done ? 'in_progress' : 'pending') as PlanStatus })), ts: 1, closed: done >= n })
  expect(planBlock(p(1, 4), true, 0, 400, false).alt).toContain('25%')
  expect(planBlock(p(0, 4), true, 0, 400, false).alt).toContain('0%')
  expect(planBlock(p(1, 3), true, 0, 400, false).alt).toContain('33%')
  expect(planBlock(p(4, 4), false, 0, 400, false).alt).toContain('100%')
})

test('a list the session made before the mod loaded shows from the hook copy, and the next tick moves it', async ($, on) => {
  workOn(on, { value: [] })
  const plan = { items: [{ content: 'one', status: 'completed' }, { content: 'two', status: 'in_progress' }, { content: 'three', status: 'pending' }], ts: 5 }
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ plan }) })
  const home = await mountDesk($, 'home')
  let lines = await altLines(home)
  for (const t of ['Tasks', '1 / 3', 'Now: two', 'To do: three']) expect([t, lines.includes(t)]).toEqual([t, true])
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', advance: true } as never)
  lines = await altLines(home)
  expect(lines).toContain('2 / 3')
  expect(lines).toContain('Now: three')
  await home.unmount()
})

// ---- Version: up to date, a newer version installed (Reload), a newer one out (Update)

test('versions compare by number; GitHub releases give the newest per plugin, drafts and pre-releases left out', () => {
  expect(newer('2.10.1', '2.9.9')).toBe(true)
  expect(newer('2.10.0', '2.10.0')).toBe(false)
  expect(newer('0.12.0', '0.12.1')).toBe(false)
  expect(newer(null, '1.0.0')).toBe(false)
  const r = releasesOf([
    { tag_name: 'abofles-guide--v0.12.1', html_url: 'https://github.com/Sajadh92/abofles/releases/tag/abofles-guide--v0.12.1' },
    { tag_name: 'abofles--v2.11.0', html_url: 'https://github.com/x', draft: true },
    { tag_name: 'abofles--v2.10.1', html_url: 'https://github.com/Sajadh92/abofles/releases/tag/abofles--v2.10.1' },
    { tag_name: 'abofles--v2.9.1', html_url: 'https://github.com/Sajadh92/abofles/releases/tag/abofles--v2.9.1' },
    { tag_name: 'other--v9.0.0', html_url: 'https://github.com/x' },
  ], 7)
  expect(r).toEqual({ abofles: { version: '2.10.1', url: 'https://github.com/Sajadh92/abofles/releases/tag/abofles--v2.10.1' }, guide: { version: '0.12.1', url: 'https://github.com/Sajadh92/abofles/releases/tag/abofles-guide--v0.12.1' }, checkedAt: 7 })
  expect(updatePrompt('sajadh92')).toContain('claude plugin update abofles-guide@sajadh92')
})

test('the version view: up to date, Reload when a newer one is installed than this session runs, Update when a newer one is out', () => {
  const pane = (run: string, insA: string, insG: string, check: 'on' | 'off' = 'on') => ({ version: { running: { abofles: run }, installed: { abofles: insA, 'abofles-guide': insG }, market: 'sajadh92', check } }) as any
  const rel = (a: string, g: string) => ({ abofles: { version: a, url: 'https://github.com/a' }, guide: { version: g, url: 'https://github.com/g' }, checkedAt: 1 })
  expect(versionView(pane('2.10.1', '2.10.1', '0.12.1'), '0.12.1', rel('2.10.1', '0.12.1'))).toMatchObject({ kind: 'ok', line: 'Abo Fles 2.10.1 · guide 0.12.1 · up to date' })
  const reload = versionView(pane('2.10.0', '2.10.1', '0.12.1'), '0.12.0', rel('2.10.1', '0.12.1'))
  expect(reload).toMatchObject({ kind: 'reload', tone: 'amber', title: 'Abo Fles 2.10.1 and guide 0.12.1 are installed' })
  expect(reload?.sub).toBe('This session still runs Abo Fles 2.10.0 · guide 0.12.0. Reload to use the new one.')
  const upd = versionView(pane('2.10.1', '2.10.1', '0.12.1'), '0.12.1', rel('2.11.0', '0.12.1'))
  expect(upd).toMatchObject({ kind: 'update', tone: 'blue', title: 'Abo Fles 2.11.0 is out', url: 'https://github.com/a' })
  expect(versionView(pane('2.10.1', '2.10.1', '0.12.1', 'off'), '0.12.1', rel('2.11.0', '0.12.1'))?.kind).toBe('ok') // check off: no Update
  expect(versionView(null, null, null)).toBeNull()
})

test('Update check = off asks GitHub nothing, and nor does a session whose state is not read yet; on asks once', async ($, on) => {
  workOn(on, { value: [] })
  const asked: string[] = []
  on('http.fetch', (($: any, e: any) => (asked.push(e.url), { value: { ok: true, status: 200, headers: {}, json: async () => [], text: async () => '[]' } })) as any)
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ version: { running: { abofles: '2.10.0' }, installed: { abofles: '2.10.0' }, market: 'sajadh92', check: 'off' } }) })
  await checkRelease($ as any, true)
  expect(asked).toEqual([])
})

test('a refused release check counts as an attempt: the next turn asks GitHub nothing', async ($, on) => {
  workOn(on, { value: [] })
  const asked: string[] = []
  on('http.fetch', (($: any, e: any) => (asked.push(e.url), { value: { ok: false, status: 403, headers: {}, text: 'rate limited' } })) as any)
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ version: { running: { abofles: '2.10.0' }, installed: { abofles: '2.10.0' }, market: 'sajadh92', check: 'on' } }) })
  const before = asked.length
  await checkRelease($ as any)
  await checkRelease($ as any)
  expect(asked.length - before).toBeLessThanOrEqual(1)
})

test('Home shows the Version card with Reload when a newer abofles is installed, and the footer says what runs', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ version: { running: { abofles: '2.10.0' }, installed: { abofles: '2.10.1' }, market: 'sajadh92', check: 'off' } }) })
  const home = await mountDesk($, 'home')
  const lines = await altLines(home)
  for (const t of ['Version', 'Reload', 'Abo Fles 2.10.1 is installed', 'Abo Fles 2.10.0 · reload to update']) expect([t, lines.includes(t)]).toEqual([t, true])
  expect(await has(home, 'version:reload')).toBe(true)
  expect(await has(home, 'version:update')).toBe(false)
  await home.unmount()
})

test('an open task list adds its step to Home Now and Next; the footer names abofles from the installed version when the hooks gave none yet', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ version: { running: {}, installed: { abofles: '2.11.0' }, market: 'sajadh92', check: 'off' } }) })
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: [{ content: 'G3 check', status: 'completed' }, { content: 'G4 build', status: 'in_progress' }, { content: 'G4 review', status: 'pending' }] } as never)
  const home = await mountDesk($, 'home')
  const lines = await altLines(home)
  for (const t of ['Now', 'Step 2 of 3 — G4 build · wiring the pane', 'Next', 'G4 review · ship it']) expect([t, lines.includes(t)]).toEqual([t, true])
  expect(lines.some(l => l.startsWith('Abo Fles 2.11.0'))).toBe(true)
  await home.unmount()
})

test('Home keeps the memo Now and Next and adds the live step and the running count only where the memo does not say them', async ($, on) => {
  workOn(on, { value: [] })
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ resume: { ...(panePayload() as any).resume, now: ['writing the G4 labels'], next: ['ask about the BE sort ticket'] } }) })
  await $.tool.call({ tool: 'mcp__ccd_turn__update_plan', items: [{ content: 'G4 build', status: 'in_progress' }, { content: 'G4 review', status: 'pending' }] } as never)
  const home = await mountDesk($, 'home')
  const lines = await altLines(home)
  expect(lines).toContain('Step 1 of 2 — G4 build · writing the G4 labels')
  expect(lines).toContain('G4 review · ask about the BE sort ticket')
  await home.unmount()
})

test('the Agents section shows the Prompt checkup counts once the hook flagged something, and nothing before', async ($, on) => {
  const last = { kind: 'user', problems: ['names no file or component', 'says no end state'], at: '2026-11-21T10:00:00Z' }
  await harness($, on, { guide: guideOf([item()]), pane: panePayload({ checkup: { agent: 3, user: 1, denied: 2, last } }) })
  for (const surface of SURFACES) {
    const ui = await mountPane($, surface)
    const lastLine = /^Last, your ask: names no file or component; says no end state$/
    const want = surface === 'desktop'
      ? [/^Prompt checkup$/, /^Agent prompts flagged 3$/, /^Your asks flagged 1$/, /^Agents refused 2$/, lastLine]
      : [/^Prompt checkup$/, /^3 agent prompts · 1 of your asks · 2 refused$/, lastLine]
    for (const re of want) expect([surface, String(re), await said(ui, re)]).toEqual([surface, String(re), true])
  }
})

for (const [name, checkup] of [['no hits yet', { agent: 0, user: 0, denied: 0, last: null }], ['an older state file with no checkup', undefined]] as const) {
  test(`the Agents section draws no Prompt checkup block with ${name}`, async ($, on) => {
    await harness($, on, { guide: guideOf([item()]), pane: panePayload(checkup ? { checkup } : {}) })
    for (const surface of SURFACES) {
      const ui = await mountPane($, surface)
      expect([surface, await said(ui, /^Prompt checkup$/)]).toEqual([surface, false])
    }
  })
}

test('agents that cost more than the base model read as an over cost in red, never a negative saving', () => {
  const t = { spawns: 7, tokens: 29_200_000, usd: 31.55, base_usd: 17.47, saved_pct: -81, open_fail: 0, base_label: 'Opus 5.5', by_model: [{ model: 'fable', label: 'Fable 5.2', spawns: 5, tokens: 24_300_000, usd: 31.05, pct: 98 }] }
  const s = spendOf(t as never, false)
  expect(s.saved).toBe('81% over cost')
  expect(s.tone).toBe('red')
  expect(s.versus).toBe('$31.55 · $14.08 more than $17.47 on Opus 5.5')
  expect(s.models[0]!.tone).toBe('violet') // Fable has its own colour, not grey
  expect(spendCard(s, 400).html).toContain('class="big ink t-red">81% over cost')
  const ok = spendOf({ ...t, usd: 0.41, base_usd: 0.9, saved_pct: 54 } as never, false)
  expect([ok.saved, ok.tone]).toEqual(['54% saved', null])
  const r = receiptOf('11-23 05:00 · 4 Agents · 23.6M Tokens · $29.01 · Cost +110% ($15.17) vs Opus 5.5 · done: analysis')
  expect([r.saved, r.over, r.money]).toEqual([null, '110%', '110% over cost ($15.17) vs Opus 5.5'])
})

// ---- the close check on Home (st["close"], written by memo.py close-check). Fixture lines read as memo.py writes them.

const closeLine = (over: object = {}) => ({ id: 'git:/r/repo-a:dirty', state: '⛔', src: 'git', text: 'repo-a: 3 uncommitted files', fix: 'commit them, then push', ...over })
const closeRaw = (over: object = {}) => ({ lines: [closeLine()], verdict: '⛔', ts: 1_700_000_000, fp: 'x', turn_ts: 1, stale: false, git_rule: 'pushed', agent: 'yes', ...over })
const NOW_MS = 1_700_000_000_000 + 60_000
const okRaw = (over: object = {}) => closeRaw({ lines: [closeLine({ state: '✓', text: 'repo-a: clean, main pushed' })], verdict: '✅', ...over })

test('cleanClose reads the stored check; an unknown state reads as not checked, never ✓', () => {
  const c = cleanClose(closeRaw({ lines: [closeLine(), closeLine({ id: 'x', state: 'ok?' }), closeLine({ id: 'w', state: '⚠', waived: { trace: 'fine', ts: 2 }, by_rule: 'Close git = local', by: 'agent', judges: 'rule:Tests' })] }))
  expect(c?.lines.map(l => l.state)).toEqual(['⛔', 'not checked', '⚠'])
  expect(c?.lines[2]).toMatchObject({ waived: true, byRule: 'Close git = local', by: 'agent', judges: 'rule:Tests' })
  expect(c?.agent).toBe('yes')
  expect(cleanClose(closeRaw({ agent: 'maybe' }))?.agent).toBe(null)
  expect(cleanClose(null)).toBe(null)
  expect(cleanClose({ verdict: '✅' })).toBe(null) // no lines: no check
  expect(cleanClose(closeRaw({ verdict: 'yes' }))?.verdict).toBe('not checked')
  const many = Array.from({ length: 120 }, (_, i) => closeLine({ id: `l${i}`, state: '✓' }))
  expect(cleanClose(closeRaw({ lines: [...many, closeLine({ id: 'late', by: 'agent' })] }))?.lines.at(-1)?.id).toBe('late') // a late agent ⛔ is never cut off
})

test('the glance chip counts unwaived blockers; a stale check reads amber and out of date', () => {
  expect(closeFact(cleanClose(closeRaw()))).toEqual({ label: 'Close', value: '1 blocker', tone: 'red' })
  expect(closeFact(cleanClose(closeRaw({ lines: [closeLine(), closeLine({ id: 'b' }), closeLine({ id: 'c', waived: { trace: 'ok', ts: 1 } })] })))?.value).toBe('2 blockers')
  expect(closeFact(cleanClose(closeRaw({ stale: true })))).toEqual({ label: 'Close', value: '1 blocker · out of date', tone: 'amber' })
  expect(closeFact(cleanClose(closeRaw({ lines: [closeLine({ state: '⚠' })] })))).toBe(null)
  expect(closeFact(null)).toBe(null)
})

test('the card head is the stored verdict in words; stale reads out of date; task steps and running work keep ✅ at Not yet', () => {
  const v = (raw: object | null, busy = 0, plan: any = null) => closeView(raw ? cleanClose(raw) : null, plan, busy, true, NOW_MS)
  expect(v(closeRaw())).toMatchObject({ word: '1 blocker', tone: 'red' })
  const stale = v(closeRaw({ stale: true }))
  expect(stale).toMatchObject({ word: 'Out of date', tone: 'amber' }) // a stale blocker is not shown as a live red one
  expect(stale?.notes[0]?.text).toMatch(/^Changed since the check at \d\d:\d\d — run it again$/)
  expect(v(closeRaw({ lines: [closeLine({ state: '⚠' })], verdict: '⚠' }))).toMatchObject({ word: '1 to check', tone: 'amber' })
  expect(v(closeRaw({ lines: [closeLine({ state: 'not checked' })], verdict: 'not checked' }))).toMatchObject({ word: 'Not checked', tone: 'grey' })
  const ok = v(okRaw())
  expect(ok).toMatchObject({ word: 'Yes', tone: 'green' })
  expect(ok?.notes[0]?.text).toMatch(/^Checked at \d\d:\d\d$/) // the check's time on every card
  const busy = v(okRaw(), 2)
  expect(busy).toMatchObject({ word: 'Not yet', tone: 'amber' })
  expect(busy?.notes.map(n => n.text)).toContain('2 agents and commands still running')
  expect(v(okRaw({ agent: 'no' }))).toMatchObject({ word: 'Not yet' })
  expect(v(okRaw({ agent: 'no' }))?.notes.map(n => n.text)).toContain('The close agent has not judged this check yet')
  expect(v(null)).toMatchObject({ word: 'Not run', tone: 'grey' }) // guide clear, no check yet
  expect(closeView(null, null, 0, false)).toBe(null) // guide still open and no check: no card
  expect(closeView(null, null, 0, true, NOW_MS, false, false)).toBe(null) // a new session that has not worked yet: no card
})

test('the check time: HH:MM today, the day too when older, nothing for a record with no time', () => {
  const d = new Date(2026, 10, 24, 9, 5).getTime()
  expect(whenOf(d / 1000, d + 3_600_000)).toBe('09:05')
  expect(whenOf(d / 1000, d + 2 * 86_400_000)).toBe('24 Nov 09:05')
  expect(whenOf(0, d)).toBe('')
})

test('a judged Close: rule line counts through the agent line: verdict ✅ draws Yes, not Not checked', () => {
  const raw = okRaw({ lines: [closeLine({ state: '✓', text: 'repo-a: clean' }), { id: 'rule:Tests', state: 'not checked', src: 'rule', text: 'Close: Tests = the suite passes', fix: '' }, { id: 'agent:1', state: '✓', src: 'agent', text: 'Tests: 231 pass', fix: '', by: 'agent', judges: 'rule:Tests' }] })
  const v = closeView(cleanClose(raw), null, 0, true, NOW_MS)
  expect(v).toMatchObject({ word: 'Yes', tone: 'green' })
  expect(v?.rows.some(r => /Close: Tests/.test(r.text))).toBe(false)
})

test('open task steps are ⚠ rows only; a wrapped or hidden list does not count, and Tasks place = off passes no list', () => {
  const plan = { items: [{ content: 'ship it', status: 'in_progress' }, { content: 'done', status: 'completed' }], ts: 1, closed: false }
  const v = closeView(cleanClose(okRaw()), plan as never, 0, true, NOW_MS)
  expect(v).toMatchObject({ word: 'Not yet', tone: 'amber', blockers: 0 })
  expect(v?.rows.find(r => r.sub === 'tasks')).toEqual({ text: 'Task step open: ship it', sub: 'tasks', tone: 'amber', word: 'Check' })
  expect(closeView(cleanClose(okRaw()), { ...plan, hidden: true } as never, 0, true, NOW_MS)?.word).toBe('Yes')
  expect(closeView(cleanClose(okRaw()), { ...plan, closed: true, wrapped: true } as never, 0, true, NOW_MS)?.word).toBe('Yes')
})

test('Home keeps the stored lines first: a blocker is never pushed off by task rows; the rest are counted', () => {
  const lines = Array.from({ length: 8 }, (_, i) => closeLine({ id: `l${i}`, text: `line ${i}` }))
  const plan = { items: [{ content: 'a', status: 'pending' }, { content: 'b', status: 'pending' }], ts: 1, closed: false }
  const v = closeView(cleanClose(closeRaw({ lines })), plan as never, 0, true, NOW_MS)
  expect(v?.rows.map(r => r.text)).toEqual(['line 0', 'line 1', 'line 2', 'line 3', 'line 4', 'line 5'])
  expect(v?.more).toBe('+2 more lines on Full details')
  const whole = closeView(cleanClose(closeRaw({ lines })), plan as never, 0, true, NOW_MS, true)
  expect(whole?.rows.length).toBe(10)
  expect(whole?.more).toBe(null)
})

test('a fill asks, never orders: a git line asks for the owner\'s words and carries no git command', () => {
  const git = cleanClose(closeRaw())!.lines[0]!
  expect(closeFill(git)).toBe('Close-check line: repo-a: 3 uncommitted files. Ask me what git should do here; use the abofles-git agent, and no git write before I answer.')
  const agentGit = cleanClose(closeRaw({ lines: [closeLine({ src: 'agent', text: 'branch not pushed', fix: 'run git push origin main' })] }))!.lines[0]!
  expect(closeFill(agentGit)).not.toMatch(/git push/) // a git command in any line's fix is dropped
  const plainLine = cleanClose(closeRaw({ lines: [closeLine({ src: 'memory', text: 'feedback-x.md\nmissing from MEMORY.md', fix: 'add its line' })] }))!.lines[0]!
  expect(closeFill(plainLine)).toBe('Close-check line: feedback-x.md missing from MEMORY.md. Suggested fix: add its line. Check it, then tell me what you will change before you change it.')
  const path = cleanClose(closeRaw({ lines: [closeLine({ src: 'memory', text: 'git repos folder note', fix: 'add it' })] }))!.lines[0]!
  expect(closeFill(path)).toMatch(/^Close-check line: git repos folder note\. Suggested fix/) // "git repos" is a path, not a git command
  const v = closeView(cleanClose(closeRaw({ lines: [closeLine(), closeLine({ id: 'm', src: 'memory', state: '⚠', fix: 'add it' }), closeLine({ id: 'n', state: 'not checked', fix: 'x' }), closeLine({ id: 'k', fix: 'y' })] })), null, 0, true, NOW_MS)
  expect(v?.fixes.map(l => l.id)).toEqual(['git:/r/repo-a:dirty', 'k']) // blockers first, two at most, a not-checked line has no fix button
  expect(v?.rows.map(r => r.tone)).toEqual(['red', 'red', 'amber', 'grey'])
  expect(v?.rows.map(r => r.word)).toEqual(['Blocker', 'Blocker', 'Check', 'Not checked']) // the state in words, not by colour alone
})

test('a waived line stays on the card as Accepted', () => {
  const v = closeView(cleanClose(closeRaw({ lines: [closeLine({ waived: { trace: 'keep it', ts: 1 } })], verdict: '✅' })), null, 0, true, NOW_MS)
  expect(v?.rows[0]).toMatchObject({ word: 'Accepted', sub: 'git · accepted by you', tone: 'green' })
})

test('desktop: Home says guide clear, shows the blocker chip and the Ready to archive? card, its buttons fill, and Full details opens every line', async ($, on) => {
  const w = await harness($, on, { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:a' })]), pane: panePayload(), close: closeRaw({ lines: [closeLine(), closeLine({ id: 'git:/r/repo-b:dirty', text: 'repo-b: 1 uncommitted file' })] }) })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /^guide clear$/)).toBe(true)
  expect(await said(ui, /^Ready to archive\?$/)).toBe(true)
  expect(await said(ui, /^2 blockers$/)).toBe(true)
  expect(await said(ui, /^repo-a: 3 uncommitted files$/)).toBe(true)
  expect(await said(ui, /^Blocker · git · commit them, then push$/)).toBe(true)
  // the last card on Home (owner's pick): every card used all day comes before it
  const cards = (await ui.findAll({ type: 'Svg' })).map((x: any) => String(x.props.alt ?? '').split('\n')[0])
  const home = cards.slice(cards.indexOf('Home'), cards.findIndex((t: string) => t.startsWith('Abo Fles')))
  expect(home[home.length - 1]).toBe('Ready to archive?')
  expect(home.indexOf('At a glance')).toBeGreaterThan(0)
  expect(await labelOf(ui, 'close-run')).toBe('Run close check')
  expect(await labelOf(ui, 'close-fix:git:/r/repo-a:dirty')).toBe('Fix git 1') // two git fixes: numbered
  expect(await labelOf(ui, 'close-fix:git:/r/repo-b:dirty')).toBe('Fix git 2')
  await ui.press({ key: 'close-run' })
  expect(w.fills[w.fills.length - 1]).toEqual({ text: '/abofles:close', mode: 'replace' })
  await ui.press({ key: 'close-fix:git:/r/repo-a:dirty' })
  expect(w.fills[w.fills.length - 1]?.text).toMatch(/^Close-check line: repo-a: 3 uncommitted files\. Ask me what git should do/)
  await ui.press({ key: 'detail:close' })
  expect(await said(ui, /^Close check$/)).toBe(true)
  expect(await said(ui, /^repo-b: 1 uncommitted file$/)).toBe(true)
  await ui.unmount()
})

test('desktop: with Guide place = band the close card still shows on Home', async ($, on) => {
  await harness($, on, { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:b' })]), pane: panePayload({ places: { ...PLACES, guide: 'band' } }), close: closeRaw() })
  const ui = await mountPane($, 'desktop')
  expect(await said(ui, /^Ready to archive\?$/)).toBe(true)
  expect(await has(ui, 'close-run')).toBe(true)
  await ui.unmount()
})

test('terminal: a stored check folds in as a Close check card with its buttons', async ($, on) => {
  await harness($, on, { guide: guideOf([item({ p: '✅', text: 'all clear', id: 'ok:t' })]), pane: panePayload(), close: closeRaw() })
  const ui = await mountPane($, 'terminal')
  expect(await said(ui, /^Ready to archive\? 1 blocker$/)).toBe(true)
  expect(await said(ui, /^Blocker · repo-a: 3 uncommitted files$/)).toBe(true)
  expect(await has(ui, 'close-run')).toBe(true)
  await ui.unmount()
})

test('desktop: Close place = chat or off keeps the Ready to archive? card and its chip off Home', async ($, on) => {
  const state = (close: string) => ({ guide: guideOf([item({ p: '✅', text: 'all clear', id: `ok:${close}` })]), pane: panePayload({ places: { ...PLACES, close } }), close: closeRaw() })
  const w = await harness($, on, state('chat'))
  for (const close of ['chat', 'off']) {
    w.state = state(close)
    await $.turn.complete({ answer: 'x', durationMs: 1, isAborted: false, turnId: `t-close-${close}`, reason: 'answered' } as never)
    const ui = await mountPane($, 'desktop')
    expect(await said(ui, /^Ready to archive\?$/)).toBe(false)
    expect(await has(ui, 'close-run')).toBe(false)
    await ui.unmount()
  }
})
