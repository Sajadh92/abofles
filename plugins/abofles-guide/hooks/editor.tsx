import type { EngineInterface } from 'claude-code'

import type { Pane, PaneOverrideKey, PaneOverrides } from '../types'
import { chipSvg, overridesBlocks } from './html'
import { GLYPH, applyOverrides, buttonRow, card, glyph, hasSvg, htmlSection, emptyPic, paneW, pic, heavyRule, ruled, type Look, claimSave, getPaneDraft, getPaneSaving, plain, refresh, releaseSave, setFold, setPaneView, updatePaneDraft } from './shared'
import { plural } from './shared'
import type { Folds, IconName, Ui } from './shared'

// The overrides of the Abo Fles pane, in two places. On the pane's main page, a short card: the values a file sets, each with what
// it does and which file sets it, the other lines, and Edit. Edit turns the pane into the editor page, laid out as the app's settings:
// the keys in folding groups, one settings block each — the name with its badge at the end of the line, the help, the control
// (a Select for the keys with a fixed set of values, an Input for numbers, texts and lists), and under them, once it has a value,
// its own This project / All projects toggle and Reset — the other forms as lines of one multi-line Input with a target of their own, and
// Save / Ask Claude / Discard. Edits live in the paneDraft atom ('' = reset) until Save runs the abofles plugin's overrides.py once per
// file, All projects first, which edits the `## abofles overrides` block of that file and prints the new table. Nothing is written by
// this mod itself.

type Draft = Record<string, string>
type Scope = 'project' | 'user'

const SCOPE = '@scope' // the file the other forms' lines are edited in
const WHERE_TO = '@where:' // + key: the file a key's value goes to, when the toggle was pressed
const ASK_RESET = '@resetall' // Reset all waits for its confirmation
const RAW = '@raw'
const CUSTOM = '@custom:'
const DEFAULT_PICK = '@default' // the Select entries that are not a value of the key
const CUSTOM_PICK = '@custom'

// The keys that also take a value of their own: the Input a "custom value" pick opens, what it starts with and what it asks for.
const CUSTOM_KEYS: Record<string, { start: string; hint: string }> = {
  Agents: { start: '3', hint: 'N agents per task' },
  Receipt: { start: 'savings 30', hint: 'savings N (percent)' },
}

// The editor's groups, in order, and each key's group and help line. A key the table adds later lands in "More", without help.
const GROUPS = ['Session', 'Agents', 'Memo', 'Output', 'Where it shows', 'More'] as const
const GROUP_ICON: Record<(typeof GROUPS)[number], IconName> = { Session: 'clock', Agents: 'fork', Memo: 'bookmark', Output: 'list', 'Where it shows': 'compass', More: 'sliders' }
const KEY_INFO: Record<string, { group: (typeof GROUPS)[number]; help: string }> = {
  'Main session': { group: 'Session', help: 'Model and effort of a main session' },
  'Sub session': { group: 'Session', help: 'Same, for a session started by another' },
  'Session cap': { group: 'Session', help: 'Units before it stops to ask' },
  'Context marks': { group: 'Session', help: 'K tokens that suggest /compact' },
  'Continue after compact': { group: 'Session', help: 'After /compact, picks the work up or says nothing is left' },
  Break: { group: 'Session', help: 'Minutes away before the resume' },
  Pace: { group: 'Session', help: 'fast: fewer stops, short receipts' },
  Agents: { group: 'Agents', help: 'Agents per task: ask, auto, N, off' },
  Parallel: { group: 'Agents', help: 'Background agents at once' },
  'Agent budgets': { group: 'Agents', help: 'Per tier T1 to T4, under auto' },
  'Review gate': { group: 'Agents', help: 'Holds the receipt for reviewers' },
  'Review memory': { group: 'Agents', help: 'Hours a reviewer PASS counts for a Sensitive change on the same topic' },
  Questions: { group: 'Agents', help: 'A Judge answers first' },
  'Budget raise': { group: 'Agents', help: 'auto: +half the start budget, twice at most, then asks; ask: always asks' },
  'Git guard': { group: 'Agents', help: 'Warn or deny git writes outside the git agent, or off' },
  'Judge notes': { group: 'Memo', help: 'Judge answers that also go to Needs you' },
  'Memo agent': { group: 'Memo', help: 'auto: tidies after a compact; manual: /abofles:memo only; off: rules only' },
  'Memo agent every': { group: 'Memo', help: 'Hours between auto runs, per project' },
  'Memo on compact': { group: 'Memo', help: 'Saves the memo by itself at /compact' },
  'Archive items': { group: 'Memo', help: 'Days before resolved items are archived' },
  'Archive sessions': { group: 'Memo', help: 'Days idle, nothing open, before a session is archived' },
  'Other items': { group: 'Memo', help: 'Items of other sessions and projects: hide or show marked' },
  'Memo repair': { group: 'Memo', help: 'auto: repairs old memos at session start, backup first' },
  Receipt: { group: 'Output', help: 'Receipt form; savings N = only past N%' },
  Guide: { group: 'Output', help: 'The next-step line' },
  Resume: { group: 'Output', help: 'What it covers; off = not in chat' },
  Language: { group: 'Output', help: 'Language of owner questions' },
  'Guide place': { group: 'Where it shows', help: 'Pane, or the band over the prompt' },
  'Resume place': { group: 'Where it shows', help: 'Pane, or printed in the chat' },
  'Agents place': { group: 'Where it shows', help: 'Pane, or printed in the chat' },
  'Running place': { group: 'Where it shows', help: 'Running page and Home card, or off' },
  'Receipt place': { group: 'Where it shows', help: 'Recorded to the pane, or printed' },
}

// An input with no default says what it takes.
const PLACEHOLDER: Record<string, string> = { 'Main session': 'e.g. opus.medium', 'Sub session': 'e.g. opus.xhigh' }

// Which file a value comes from, as the badge says it.
const WHERE: Record<string, string> = { project: 'this project', user: 'all projects' }

export const scopeOf = (d: Draft): Scope => (d[SCOPE] === 'user' ? 'user' : 'project')

// The value as it would stand in the file: '' = no line (the default). A default value typed over a default is no line either.
export const written = (k: PaneOverrideKey, v: string): string => {
  const t = v.trim()
  return t === '' || (k.source === 'default' && t === k.value) ? '' : t
}

const baseline = (k: PaneOverrideKey): string => written(k, k.value)

const isSet = (k: PaneOverrideKey): boolean => k.source === 'project' || k.source === 'user'
const valueOf = (k: PaneOverrideKey, d: Draft): string => written(k, d[k.key] ?? k.value)

// The file this key's value goes to: the toggle's choice, else where it is set now, else this project, so a new value cannot reach every project by accident.
export const whereOf = (k: PaneOverrideKey, d: Draft): Scope => {
  const w = d[`${WHERE_TO}${k.key}`]
  return w === 'user' || w === 'project' ? w : k.source === 'user' ? 'user' : 'project'
}
const moved = (k: PaneOverrideKey, d: Draft): boolean => isSet(k) && valueOf(k, d) !== '' && whereOf(k, d) !== k.source

const isChanged = (k: PaneOverrideKey, d: Draft): boolean => (d[k.key] !== undefined && written(k, d[k.key]) !== baseline(k)) || moved(k, d)

// What Save writes, per file. A reset takes the key out of the file it comes from, one level at a time: under a project value an all-projects value takes over, and
// other projects change only when the row says All projects. All projects also takes the key out of this project, since a project value would win; This project
// leaves the all-projects line, so other projects keep it.
export function plansOf(ov: PaneOverrides, d: Draft): Record<Scope, Record<string, string>> {
  const plan: Record<Scope, Record<string, string>> = { project: {}, user: {} }
  for (const k of ov.keys.filter(x => isChanged(x, d))) {
    const v = valueOf(k, d)
    if (v === '') {
      plan[k.source === 'user' ? 'user' : 'project'][k.key] = ''
      continue
    }
    const to = whereOf(k, d)
    plan[to][k.key] = v
    if (to === 'user' && k.project !== null) plan.project[k.key] = ''
  }
  return plan
}

export const changedKeys = (ov: PaneOverrides, d: Draft): PaneOverrideKey[] => ov.keys.filter(k => isChanged(k, d))

const linesOf = (text: string): string[] => text.split('\n').map(l => l.replace(/^\s*-\s*/, '').trim()).filter(Boolean)
const rawBase = (ov: PaneOverrides, scope: Scope): string[] => ov.raw.filter(r => r.source === scope).map(r => r.line)
const rawLines = (ov: PaneOverrides, d: Draft, scope: Scope): string[] => (d[RAW] === undefined ? rawBase(ov, scope) : linesOf(d[RAW]))
const rawChanged = (ov: PaneOverrides, d: Draft, scope: Scope): boolean => d[RAW] !== undefined && rawLines(ov, d, scope).join('\n') !== rawBase(ov, scope).join('\n')

export const dirtyCount = (ov: PaneOverrides, d: Draft): number => changedKeys(ov, d).length + (rawChanged(ov, d, scopeOf(d)) ? 1 : 0)

// The prompt "Ask Claude to apply" fills: only what changed, in the block's own `- Key = value` shape, per file.
export function askText(files: Record<Scope, string>, ov: PaneOverrides, d: Draft): string {
  const plan = plansOf(ov, d)
  const parts: Record<Scope, string[]> = { project: [], user: [] }
  for (const to of ['user', 'project'] as const) {
    parts[to] = Object.entries(plan[to]).map(([key, v]) => (v === '' ? `- ${key} removed` : `- ${key} = ${v}`))
  }
  const scope = scopeOf(d)
  if (rawChanged(ov, d, scope)) {
    const base = rawBase(ov, scope)
    const now = rawLines(ov, d, scope)
    parts[scope].push(...now.filter(l => !base.includes(l)).map(l => `- ${l}`), ...base.filter(l => !now.includes(l)).map(l => `- remove: ${l}`))
  }
  return (['user', 'project'] as const)
    .filter(to => parts[to].length > 0)
    .map(to => `Apply in ${files[to]} \`## abofles overrides\`: ${parts[to].join(' ')}`)
    .join(' ')
}

// A path as the card shows it: relative to the project, or with ~ for the home folder.
function shortPath(path: string, root: string, home: string): string {
  if (root && path.startsWith(`${root}/`)) return path.slice(root.length + 1)
  if (home && path.startsWith(`${home}/`)) return `~${path.slice(home.length)}`
  return path
}

const firstLine = (text: string): string => text.split('\n').find(l => l.trim()) ?? ''

// The toggle: this key's value goes to that file.
function setWhere($: EngineInterface, key: string, to: Scope) {
  return updatePaneDraft($, cur => ({ ...cur, [`${WHERE_TO}${key}`]: to }))
}

// Reset takes the key's value out, as the Select's default does. Undo takes the key's edits back.
function resetKey($: EngineInterface, key: string) {
  return updatePaneDraft($, cur => {
    const next: Draft = { ...cur, [key]: '' }
    delete next[`${CUSTOM}${key}`]
    delete next[`${WHERE_TO}${key}`]
    return next
  })
}
function undoKey($: EngineInterface, key: string) {
  return updatePaneDraft($, cur => {
    const next: Draft = { ...cur }
    delete next[key]
    delete next[`${CUSTOM}${key}`]
    delete next[`${WHERE_TO}${key}`]
    return next
  })
}

function askResetAll($: EngineInterface, on: boolean) {
  return updatePaneDraft($, cur => {
    const next: Draft = { ...cur }
    if (on) next[ASK_RESET] = '1'
    else delete next[ASK_RESET]
    return next
  })
}

// Reset all, confirmed: every overridden key goes back one level, shown as an edit until Save.
function resetAll($: EngineInterface, keys: PaneOverrideKey[]) {
  return updatePaneDraft($, cur => {
    const next: Draft = { ...cur }
    delete next[ASK_RESET]
    for (const k of keys) {
      next[k.key] = ''
      delete next[`${CUSTOM}${k.key}`]
      delete next[`${WHERE_TO}${k.key}`]
    }
    return next
  })
}

function setDraft($: EngineInterface, key: string, value: string, custom: boolean | null = null) {
  return updatePaneDraft($, cur => {
    const next: Draft = { ...cur, [key]: value }
    if (custom === true) next[`${CUSTOM}${key}`] = '1'
    if (custom === false) delete next[`${CUSTOM}${key}`]
    return next
  })
}

// Another scope is another file: the lines typed for the old one are dropped, so they are never written to the new one.
function setScope($: EngineInterface, scope: Scope) {
  return updatePaneDraft($, cur => {
    if (scopeOf(cur) === scope) return cur
    const next: Draft = { ...cur, [SCOPE]: scope }
    delete next[RAW]
    return next
  })
}

// A clean draft: only the other forms' target stays.
const emptied = (cur: Draft): Draft => (cur[SCOPE] ? { [SCOPE]: cur[SCOPE] } : {})

// ---- the buttons

async function save($: EngineInterface, p: Pane, ov: PaneOverrides, d: Draft, files: Record<Scope, string>) {
  const argv = p.cmd.overrides
  if (!argv) {
    $.ui.toast('Update the abofles plugin to save here')
    return
  }
  const scope = scopeOf(d)
  const plan = plansOf(ov, d)
  const bodies: { to: Scope; body: { set: Record<string, string>; raw?: string[] } }[] = []
  // All projects first: if the second write fails, a value moved up is in both files, and the project's copy still wins with the same value
  for (const to of ['user', 'project'] as const) {
    const body: { set: Record<string, string>; raw?: string[] } = { set: plan[to] }
    if (to === scope && rawChanged(ov, d, scope)) body.raw = rawLines(ov, d, scope)
    if (Object.keys(body.set).length > 0 || body.raw !== undefined) bodies.push({ to, body })
  }
  if (bodies.length === 0) {
    $.ui.toast('Nothing to save')
    return
  }
  if (!(await claimSave($))) return
  try {
    const saved: string[] = []
    for (const { to, body } of bodies) {
      const r = await $.process.run([...argv, 'set', '--scope', to], { ...(p.root ? { cwd: p.root } : {}), stdin: JSON.stringify(body) })
      if (r.exitCode !== 0) {
        if (saved.length > 0) await refresh($)
        $.ui.toast(firstLine(r.stderr) || firstLine(r.stdout) || `Save failed (exit ${r.exitCode})`) // the draft stays
        return
      }
      saved.push(files[to])
      let again: unknown = null
      try {
        again = JSON.parse(r.stdout)
      } catch {
        // the file is written; the state file has the table
      }
      if (again !== null) await applyOverrides($, again)
      else await refresh($)
    }
    await updatePaneDraft($, emptied)
    $.ui.toast(`Saved to ${saved.join(' and ')}`)
  } catch (err) {
    $.ui.toast(`Save failed — ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    await releaseSave($)
  }
}

async function askClaude($: EngineInterface, ov: PaneOverrides, d: Draft, files: Record<Scope, string>) {
  if (dirtyCount(ov, d) === 0) {
    $.ui.toast('Nothing changed to ask for')
    return
  }
  const text = askText(files, ov, d)
  const have = (await $.prompt.read()).text.trim()
  const r = await $.prompt.fill({ text: have ? `\n${text}` : text, mode: have ? 'append' : 'replace' })
  if (!r.isFilled) $.ui.toast('Prompt box is not open')
}

// ---- the editor page

// What Save will do to a key whose value was taken out. An all-projects value is named before it reaches every project.
function resetNote(k: PaneOverrideKey): string {
  if (k.source === 'project' && k.user !== null) return `On save: uses the all-projects value, ${k.user}`
  return k.source === 'user' ? 'On save: back to default in every project' : 'On save: back to default'
}

// A value in the file the hooks cannot read: what they do instead, and the way out.
function invalidNote(k: PaneOverrideKey): string {
  return `The hooks cannot read "${k.value}" and use ${k.default ? k.default : 'nothing'} instead. Pick a value or Reset.`
}

// One key as a settings block, one thing per line: the name (with "· edited" while unsaved) and at its end the badge when a file sets it; what it does; the control; the
// custom value Input when open; then the key's own target and, at the end of that line, Reset. The name stands above the control, so
// the control itself carries no label.
function field($: EngineInterface, ui: Ui, look: Look, k: PaneOverrideKey, d: Draft) {
  const { Box, Button, Input, Select, Text } = ui
  const help = KEY_INFO[k.key]?.help ?? ''
  const edited = isChanged(k, d)
  const value = valueOf(k, d)
  const to = whereOf(k, d)
  const Svg = hasSvg(ui, look) ? (ui as Ui & { Svg: NonNullable<Ui['Svg']> }).Svg : null
  const chip = isSet(k) && Svg ? chipSvg('Set', 'blue') : null
  const resetting = isSet(k) && value === ''
  // the two targets: the chosen one marked ✓ and at full strength
  const target = (scope: Scope, label: string) => <Button key={`where:${k.key}:${scope}`} label={to === scope ? `✓ ${label}` : label} variant="secondary" dimColor={to !== scope} onPress={() => setWhere($, k.key, scope)} />
  const fallback = PLACEHOLDER[k.key] ?? (k.default ? `default ${k.default}` : 'none')
  const custom = CUSTOM_KEYS[k.key]
  const cur = written(k, d[k.key] ?? k.value)
  const known = k.options.find(o => o.toLowerCase() === cur.toLowerCase())
  const open = k.kind === 'select' && custom !== undefined && (d[`${CUSTOM}${k.key}`] !== undefined || (cur !== '' && known === undefined))
  let control: JSX.Element
  if (k.kind !== 'select') {
    // the app holds a one-line input at about 176 px and a plugin cannot widen it: the one long value gets a second line, so it shows whole
    control = <Input key={`in:${k.key}`} {...(k.key === 'Context marks' ? { rows: 2 } : {})} value={d[k.key] ?? k.value} placeholder={fallback} onInput={v => setDraft($, k.key, v)} onSubmit={v => setDraft($, k.key, v)} />
  } else {
    const pick = open ? CUSTOM_PICK : cur === '' ? DEFAULT_PICK : (known ?? cur)
    const options = [
      { value: DEFAULT_PICK, label: k.default ? `default (${k.default})` : 'default' },
      ...k.options.map(o => ({ value: o })),
      ...(custom ? [{ value: CUSTOM_PICK, label: 'custom value' }] : known === undefined && cur !== '' ? [{ value: cur }] : []),
    ]
    const onSelect = (v: string) => {
      if (v === CUSTOM_PICK) return setDraft($, k.key, cur !== '' && known === undefined ? cur : custom?.start ?? '', true)
      return setDraft($, k.key, v === DEFAULT_PICK ? '' : v, false)
    }
    control = <Select key={`sel:${k.key}`} options={options} value={pick} onSelect={onSelect} />
  }
  return (
    <Box key={`row:${k.key}`} flexDirection="column" rowGap="sm">
      <Box flexDirection="column" rowGap="xs">
        <Box key={`head:${k.key}`} flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={1}>
          <Box flexDirection="row" flexWrap="wrap" alignItems="center" columnGap={1} flexShrink={1}>
            <Text>{k.key}</Text>
            {edited ? <Text color="warning">{'· edited'}</Text> : null}
          </Box>
          {chip && Svg ? <Svg key={`set:${k.key}`} source={chip.source} alt="Overridden" width={chip.width} /> : isSet(k) ? <Text color="suggestion">set</Text> : null}
        </Box>
        {help ? <Text size="caption" dimColor>{help}</Text> : null}
      </Box>
      {control}
      {open ? (
        <Box flexDirection="column" rowGap="xs">
          <Text size="caption" dimColor>{custom?.hint ?? ''}</Text>
          <Input key={`custom:${k.key}`} value={d[k.key] ?? k.value} placeholder={custom?.hint} onInput={v => setDraft($, k.key, v, true)} onSubmit={v => setDraft($, k.key, v, true)} />
        </Box>
      ) : null}
      {resetting ? <Text size="caption" color="warning">{resetNote(k)}</Text> : k.invalid && !edited ? <Text size="caption" color="warning">{invalidNote(k)}</Text> : null}
      {value !== '' || isSet(k) ? (
        <Box flexDirection="row" flexWrap="wrap" justifyContent="space-between" alignItems="center" gap="xs">
          <Box flexDirection="row" alignItems="center" gap="xs">
            {value !== '' ? target('project', 'This project') : null}
            {value !== '' ? target('user', 'All projects') : null}
          </Box>
          {isSet(k) ? <Button key={`reset:${k.key}`} label={resetting ? 'Undo reset' : 'Reset'} variant="secondary" onPress={() => (resetting ? undoKey($, k.key) : resetKey($, k.key))} /> : null}
        </Box>
      ) : null}
    </Box>
  )
}

// What the page and the short card both need: the draft, the file names, the counts.
async function editorState($: EngineInterface, p: Pane, ov: PaneOverrides) {
  const d = (await getPaneDraft($)) ?? {}
  const home = (await $.env.get('HOME')) ?? ''
  const scope = scopeOf(d)
  const files = { project: shortPath(ov.files.project, p.root, home) || 'CLAUDE.md', user: shortPath(ov.files.user, p.root, home) || '~/.claude/CLAUDE.md' }
  return { d, scope, files, dirty: dirtyCount(ov, d) }
}

const unreadable = 'Pane data unreadable — update both abofles and abofles-guide'

// The page's top row: ‹ Overrides (back to the cards) and the unsaved count.
function pageTop($: EngineInterface, ui: Ui, dirty: number) {
  const { Box, Button, Text } = ui
  return (
    <Box flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={1}>
      <Button key="back" plain onPress={() => setPaneView($, 'main')}>
        <Text dimColor>{'‹ '}</Text>
        <Text weight="medium">Overrides</Text>
      </Button>
      {dirty > 0 ? <Text color="warning">{`${dirty} unsaved`}</Text> : null}
    </Box>
  )
}

// The groups of the editor page, one list with a line between them: a settings list reads closer together than the cards of the other pages.
function ruledGroups(ui: Ui, look: Look, items: JSX.Element[]) {
  const { Box } = ui
  return <Box flexDirection="column">{items.flatMap((x, i) => (i === 0 ? [x] : [heavyRule(ui, look), x]))}</Box>
}

// The page the pane turns into: the groups of keys, the other forms with their own target, and Save / Ask Claude / Reset all / Discard.
export async function overridesPage($: EngineInterface, ui: Ui, p: Pane | null, folds: Folds, look: Look) {
  const { Box, Button, Input, Text } = ui
  const ov: PaneOverrides | null = p?.overrides ?? null
  if (p === null || ov === null) {
    return (
      <Box flexDirection="column" gap={1}>
        {pageTop($, ui, 0)}
        <Text dimColor>{unreadable}</Text>
      </Box>
    )
  }
  const { d, scope, files, dirty } = await editorState($, p, ov)
  const saving = (await getPaneSaving($)) === true
  const raw = rawLines(ov, d, scope)
  const others = ov.raw.filter(r => r.source !== scope)
  const groups = GROUPS.map(name => ({ name, keys: ov.keys.filter(k => (KEY_INFO[k.key]?.group ?? 'More') === name) })).filter(g => g.keys.length > 0)
  const rawEdited = rawChanged(ov, d, scope)
  const setKeys = ov.keys.filter(k => isSet(k) && valueOf(k, d) !== '')
  const inAll = setKeys.filter(k => k.source === 'user').length
  const asking = d[ASK_RESET] === '1' && setKeys.length > 0
  // both secondary, the chosen one marked ✓: Save stays the page's one primary
  const scopeButton = (value: Scope, label: string) => <Button key={`scope:${value}`} label={scope === value ? `✓ ${label}` : label} variant="secondary" onPress={() => setScope($, value)} />
  return (
    <Box flexDirection="column" gap={1}>
      {pageTop($, ui, dirty)}
      <Text size="caption" dimColor>A project value wins over an all-projects value.</Text>
      {ruledGroups(ui, look, [...groups.map(g => {
        const edited = g.keys.filter(k => isChanged(k, d)).length
        const set = g.keys.filter(k => WHERE[k.source]).length
        return card(
          $,
          ui,
          { id: `g:${g.name}`, title: g.name, summary: edited > 0 ? `${edited} edited` : set > 0 ? `${set} set` : 'default', tone: edited > 0 ? 'warning' : undefined, folds, icon: GROUP_ICON[g.name], neutral: true, look },
          <Box flexDirection="column" rowGap="md">{ruled(ui, look, g.keys.map(k => field($, ui, look, k, d)))}</Box>,
        )
      }), card(
        $,
        ui,
        // folded while this file has none of these lines: they are the rare, free-form overrides
        { id: 'g:Other', title: 'Other overrides', summary: rawEdited ? 'edited' : raw.length > 0 ? `${raw.length} line${raw.length === 1 ? '' : 's'}` : others.length > 0 ? `${others.length} in ${WHERE[others[0].source] ?? others[0].source}` : null, tone: rawEdited ? 'warning' : undefined, folds, icon: 'sliders', neutral: true, look, def: raw.length > 0 || rawEdited },
        <Box flexDirection="column" rowGap={1}>
          <Box flexDirection="row" alignItems="center" flexWrap="wrap" columnGap={1}>
            {scopeButton('project', 'This project')}
            {scopeButton('user', 'All projects')}
          </Box>
          <Text size="caption" dimColor>{`${files[scope]} · one per line: topic tiers, cells, reviewers, workflows — e.g. Reviewers: Money = finance, tester`}</Text>
          <Input key="raw" rows={Math.min(8, Math.max(2, raw.length + 1))} placeholder="Topic: Billing = T3" value={d[RAW] ?? rawBase(ov, scope).join('\n')} onInput={v => setDraft($, RAW, v)} onSubmit={v => setDraft($, RAW, v)} />
          {others.map(r => (
            <Text size="caption" dimColor>{`${r.line} — in ${WHERE[r.source] ?? r.source}; switch above to edit it`}</Text>
          ))}
        </Box>,
      )])}
      {heavyRule(ui, look, 'actions divider')}
      {asking ? (
        <Box flexDirection="column" rowGap="xs">
          <Text size="caption" color="warning">{`Resets ${plural(setKeys.length, 'setting')}${inAll > 0 ? `; ${inAll} of them for every project` : ''}. Save applies it.`}</Text>
          <Box flexDirection="row" flexWrap="wrap" gap="xs">
            <Button key="reset-all-yes" label={`Reset ${plural(setKeys.length, 'setting')}`} variant="secondary" onPress={() => resetAll($, setKeys)} />
            <Button key="reset-all-no" label="Keep" variant="secondary" onPress={() => askResetAll($, false)} />
          </Box>
        </Box>
      ) : null}
      <Box flexDirection="row" flexWrap="wrap" alignItems="center" gap="xs">
        {saving ? (
          <Button key="save" label="Saving…" variant="secondary" onPress={() => undefined} />
        ) : (
          <Button key="save" label={dirty > 0 ? `Save ${dirty} change${dirty === 1 ? '' : 's'}` : 'Save'} variant={dirty > 0 ? 'primary' : 'secondary'} onPress={() => save($, p, ov, d, files)} />
        )}
        {dirty > 0 ? <Button key="reset" label="Discard" variant="secondary" onPress={() => updatePaneDraft($, emptied)} /> : null}
        <Button key="ask" label="Ask Claude" variant="secondary" onPress={() => askClaude($, ov, d, files)} />
        {setKeys.length > 0 && !asking ? <Button key="reset-all" label="Reset all" variant="secondary" onPress={() => askResetAll($, true)} /> : null}
      </Box>
    </Box>
  )
}

// Opens the editor page; `group` (an editor group's fold id) is opened with it.
async function openEditor($: EngineInterface, group?: string) {
  if (group) await setFold($, group, true)
  await setPaneView($, 'overrides')
}

// The short card on the pane's main page: a table of the values a file sets, the other lines, and Edit.
export async function overridesCard($: EngineInterface, ui: Ui, p: Pane | null, folds: Folds, look: Look) {
  const { Box, Button, Text } = ui
  const ov: PaneOverrides | null = p?.overrides ?? null
  const html = hasSvg(ui, look)
  if (p === null || ov === null) {
    if (html) return htmlSection(ui, look, { icon: 'sliders', tone: 'grey', title: 'Overrides' }, [emptyPic(ui, look, unreadable)])
    return card($, ui, { id: 'overrides', title: 'Overrides', def: false, folds, icon: 'sliders', look }, <Text dimColor>{unreadable}</Text>)
  }
  const { dirty } = await editorState($, p, ov)
  const set = ov.keys.filter(k => WHERE[k.source])
  if (html) {
    // the desktop: the keys set as one card (the key, its value and where it is set), Edit and the other lines as buttons under it
    const note = set.length > 0 ? 'Everything else uses the defaults.' : 'Everything uses the defaults.'
    const summary = dirty > 0 ? `${dirty} unsaved` : set.length > 0 ? `${set.length} set` : 'defaults'
    const other = ov.raw.length > 0 ? `${ov.raw.length} other line${ov.raw.length === 1 ? '' : 's'} · tiers, reviewers` : null
    const rowsOf = (source: Scope) => set.filter(k => k.source === source).map(k => ({ key: k.key, value: plain(k.value), help: KEY_INFO[k.key]?.help }))
    const groups = [{ title: 'This project', rows: rowsOf('project') }, { title: 'All projects', rows: rowsOf('user') }]
    const shown = groups.filter(g => g.rows.length > 0)
    const blocks = overridesBlocks(groups, note, paneW(look))
    // Edit on the title's row: the list can be long, and changing it is what this page is for
    const edit = <Button key="edit-overrides" label={glyph(look, GLYPH.edit, 'Edit')} variant="secondary" onPress={() => openEditor($)} />
    return htmlSection(ui, look, { icon: 'sliders', tone: 'grey', title: 'Overrides', summary, summaryTone: dirty > 0 ? 'amber' : set.length > 0 ? 'blue' : null, action: edit }, [
      other ? buttonRow(ui, [<Button key="edit-other" label={glyph(look, GLYPH.other, other)} variant="secondary" onPress={() => openEditor($, 'g:Other')} />]) : null,
      ...shown.map((g, i) => pic(ui, look, blocks.cards[i], [g.title, String(g.rows.length), ...g.rows.flatMap(k => [k.key, k.value, k.help])])),
      pic(ui, look, blocks.note, [note]),
    ])
  }
  const rows: JSX.Element[] = set.map(k => (
    <Box flexDirection="column">
      <Box flexDirection="row" columnGap={1}>
        <Box flexGrow={1} flexShrink={1}>
          <Text dimColor>{k.key}</Text>
        </Box>
        <Text>{plain(k.value)}</Text>
        <Box width={13} justifyContent="flex-end" flexShrink={0}>
          <Text size="caption" dimColor>{WHERE[k.source]}</Text>
        </Box>
      </Box>
      {KEY_INFO[k.key]?.help ? <Text size="caption" dimColor>{KEY_INFO[k.key].help}</Text> : null}
    </Box>
  ))
  const summary = dirty > 0 ? `${dirty} unsaved` : set.length > 0 ? `${set.length} set` : 'defaults'
  return card(
    $,
    ui,
    { id: 'overrides', title: 'Overrides', def: false, summary, tone: dirty > 0 ? 'warning' : set.length > 0 ? 'suggestion' : undefined, folds, icon: 'sliders', look },
    <Box flexDirection="column" rowGap={1}>
      {rows.length > 0 ? <Box flexDirection="column">{rows}</Box> : null}
      {ov.raw.length > 0 ? (
        <Button key="edit-other" plain dimColor onPress={() => openEditor($, 'g:Other')}>
          {`${ov.raw.length} other line${ov.raw.length === 1 ? '' : 's'} · tiers, reviewers ›`}
        </Button>
      ) : null}
      <Box flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={1}>
        <Text size="caption" dimColor>{set.length > 0 ? 'Everything else uses the defaults.' : 'Everything uses the defaults.'}</Text>
        <Button key="edit-overrides" label="Edit" plain dimColor onPress={() => openEditor($)} />
      </Box>
    </Box>,
  )
}
