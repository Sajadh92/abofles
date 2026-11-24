import type { EngineInterface } from 'claude-code'
import type { CloseLine, PaneClose, Plan } from '../types'
import { closeCard } from './html'
import type { CloseRowIn, Fact, Tone } from './html'
import { GLYPH, buttonRow, fillPrompt, glyph, paneW, pic } from './shared'
import type { Look, Ui } from './shared'

// The close check on Home. The script's stored verdict is the only verdict: the card draws it from state and never from a chat
// sentence. Unfinished task steps join it as ⚠ lines only, never ⛔: the Tasks card can lag behind the app's own list.

export const CLOSE_CMD = '/abofles:close'
const ROWS = 6 // lines drawn on Home; the rest are counted
const FIX_BUTTONS = 2 // a fill button for the first open lines that carry a fix


// What a fix button fills: a question about the line, never an order in the owner's voice. A git line (or any line whose fix names a git
// command) asks for the owner's own words and sends no git command at all: the work goes through the abofles-git agent (Git guard).
const GIT_CMD = /\bgit\s+(push|commit|add|merge|rebase|reset|stash|checkout|switch|restore|branch|tag|pull|clean|cherry-pick|revert|worktree|rm|mv)\b/i
const flat = (t: string) => t.replace(/\s+/g, ' ').trim()
export function closeFill(l: CloseLine): string {
  const text = flat(l.text)
  const fix = flat(l.fix)
  if (l.src === 'git' || GIT_CMD.test(`${fix} ${text}`)) return `Close-check line: ${text}. Ask me what git should do here; use the abofles-git agent, and no git write before I answer.`
  return `Close-check line: ${text}.${fix ? ` Suggested fix: ${fix}.` : ''} Check it, then tell me what you will change before you change it.`
}

// the state in words beside each row's dot: colour is never the only signal (Better Design, colour + label)
const stateWord = (l: CloseLine): string => (l.waived ? 'Accepted' : l.state === '⛔' ? 'Blocker' : l.state === '⚠' ? 'Check' : l.state === '✓' ? 'Done' : 'Not checked')

// Open task steps join the card as ⚠ rows. A list the wrap-up hid with its statuses (`wrapped`) or one taken off Home (`hidden`) does not
// count, the same rule as the hook's plan_live; the caller passes null when Tasks place = off.
const tasksOpen = (p: Plan | null): string[] => (p && !p.hidden && !p.wrapped ? p.items.filter(i => i.status !== 'completed').map(i => i.content) : [])

// The check's time: HH:MM today, else the day too; nothing for a record with no time.
export function whenOf(sec: number, nowMs: number): string {
  if (!(sec > 0)) return ''
  const d = new Date(sec * 1000)
  const t = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const n = new Date(nowMs)
  const today = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  return today ? t : `${d.getDate()} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()]} ${t}`
}

// A script `Close:` rule line the close agent judged counts through the agent's line (the hook's close_judged): it is not drawn and not
// counted as `not checked`.
const judgedIds = (lines: CloseLine[]): Set<string> => new Set(lines.filter(l => l.by === 'agent' && l.judges && l.state !== 'not checked').map(l => l.judges as string))
const liveLines = (c: PaneClose | null): CloseLine[] => {
  if (!c) return []
  const judged = judgedIds(c.lines)
  return c.lines.filter(l => !(l.src === 'rule' && l.by !== 'agent' && judged.has(l.id)))
}
export const blockersOf = (c: PaneClose | null): number => liveLines(c).filter(l => l.state === '⛔' && !l.waived).length

export type CloseView = { word: string; tone: Tone; notes: { text: string; tone: Tone }[]; rows: CloseRowIn[]; more: string | null; fixes: CloseLine[]; blockers: number; lines: number }

// The card's content, or null when there is nothing to say: no check stored and either the guide still has open items or the session has
// not worked yet. The head word is the stored verdict (stale: out of date); open task steps and running work are notes and rows, and only
// keep a ✅ from reading "Yes".
export function closeView(c: PaneClose | null, plan: Plan | null, busy: number, guideClear: boolean, nowMs = Date.now(), whole = false, worked = true): CloseView | null {
  if (!c && (!guideClear || !worked)) return null
  const steps = tasksOpen(plan)
  const lines = liveLines(c)
  const blockers = blockersOf(c)
  const notes: CloseView['notes'] = []
  const when = c ? whenOf(c.ts, nowMs) : ''
  if (!c) notes.push({ text: `No close check yet — press Run close check`, tone: 'grey' })
  else if (c.stale) notes.push({ text: `Changed since the check${when ? ` at ${when}` : ''} — run it again`, tone: 'amber' })
  else if (when) notes.push({ text: `Checked at ${when}`, tone: 'grey' })
  if (c && c.agent === 'no' && !c.stale) notes.push({ text: 'The close agent has not judged this check yet', tone: 'amber' })
  if (busy > 0) notes.push({ text: `${busy} ${busy === 1 ? 'agent or command' : 'agents and commands'} still running`, tone: 'amber' })
  if (steps.length > 0) notes.push({ text: `${steps.length} task ${steps.length === 1 ? 'step' : 'steps'} still open`, tone: 'amber' })
  const warn = lines.filter(l => !l.waived && l.state === '⚠').length
  const [word, tone]: [string, Tone] = !c
    ? ['Not run', 'grey']
    : c.stale
      ? ['Out of date', 'amber']
      : c.verdict === '⛔'
        ? [`${blockers} ${blockers === 1 ? 'blocker' : 'blockers'}`, 'red']
        : c.verdict === '⚠'
          ? [`${warn} to check`, 'amber']
          : c.verdict === 'not checked'
            ? ['Not checked', 'grey']
            : steps.length > 0 || busy > 0 || c.agent === 'no'
              ? ['Not yet', 'amber']
              : ['Yes', 'green']
  const rank = (l: CloseLine) => (l.state === '⛔' && !l.waived ? 0 : l.state === '⚠' && !l.waived ? 1 : l.state === 'not checked' ? 2 : l.waived ? 3 : 4)
  const sorted = lines.map(line => ({ line, rank: rank(line) })).sort((a, b) => a.rank - b.rank)
  const toneOfLine = (l: CloseLine): Tone => (l.state === 'not checked' ? 'grey' : l.waived ? 'green' : l.state === '⛔' ? 'red' : l.state === '⚠' ? 'amber' : 'green')
  const rows: CloseRowIn[] = []
  for (const { line: l } of sorted.filter(r => r.rank < 4)) {
    // a by-rule line already names its rule in its text: under it, the source and the fix
    const sub = [l.src, l.state !== 'not checked' && l.waived ? 'accepted by you' : l.fix].filter(Boolean).join(' · ') || null
    rows.push({ text: flat(l.text), sub, tone: toneOfLine(l), word: l.state === 'not checked' ? 'Not checked' : stateWord(l) })
  }
  const storedRows = rows.length
  for (const s of steps) rows.push({ text: `Task step open: ${flat(s)}`, sub: 'tasks', tone: 'amber', word: 'Check' })
  const done = sorted.filter(r => r.rank === 4)
  if (done.length > 0) rows.push({ text: `${done.length} ${done.length === 1 ? 'line' : 'lines'} checked`, sub: null, tone: 'green', word: 'Done' })
  // Home keeps the stored lines first (a blocker is never pushed off by a task row); the rest are counted and on the Full details page
  const cap = whole ? rows.length : ROWS
  const shown = whole ? rows : [...rows.slice(0, Math.min(storedRows, cap)), ...rows.slice(storedRows).slice(0, Math.max(0, cap - storedRows))]
  const hidden = storedRows - Math.min(storedRows, cap)
  const more = !whole && hidden > 0 ? `+${hidden} more ${hidden === 1 ? 'line' : 'lines'} on Full details` : null
  const fixes = sorted.filter(r => r.rank < 2 && !!r.line.fix).slice(0, FIX_BUTTONS).map(r => r.line)
  return { word, tone, notes, rows: shown, more, fixes, blockers, lines: rows.length }
}

// The glance chip mid-session: a stored check with a blocker; amber and "out of date" once the work changed since.
export function closeFact(c: PaneClose | null): Fact | null {
  const n = blockersOf(c)
  if (n === 0) return null
  return { label: 'Close', value: `${n} ${n === 1 ? 'blocker' : 'blockers'}${c?.stale ? ' · out of date' : ''}`, tone: c?.stale ? 'amber' : 'red' }
}

export const closeAlt = (v: CloseView): string[] => ['Ready to archive?', v.word, ...v.notes.map(n => n.text), ...v.rows.flatMap(r => [r.text, [r.word, r.sub].filter(Boolean).join(' · ')]), ...(v.more ? [v.more] : [])]

// A fix button's label: the source, numbered when two buttons share it.
const fixLabel = (l: CloseLine, all: CloseLine[]): string => {
  const same = all.filter(x => x.src === l.src)
  return same.length > 1 ? `Fix ${l.src || 'line'} ${same.indexOf(l) + 1}` : `Fix ${l.src || 'line'}`
}

export function closeHomeCard($: EngineInterface, ui: Ui, look: Look, v: CloseView | null, openDetails: (() => Promise<void>) | null = null) {
  if (!v) return null
  const { Box, Button } = ui
  return (
    <Box flexDirection="column" rowGap="sm">
      {pic(ui, look, closeCard({ paneW: paneW(look), word: v.word, tone: v.tone, notes: v.notes, rows: v.rows, more: v.more }), closeAlt(v))}
      {buttonRow(ui, [
        <Button key="close-run" label={glyph(look, GLYPH.recheck, 'Run close check')} variant={v.blockers > 0 ? 'secondary' : 'primary'} onPress={() => fillPrompt($, null, CLOSE_CMD)} />,
        ...v.fixes.map((l, n) => <Button key={`close-fix:${l.id}`} label={glyph(look, GLYPH.use, fixLabel(l, v.fixes))} variant={n === 0 && v.blockers > 0 ? 'primary' : 'secondary'} onPress={() => fillPrompt($, null, closeFill(l))} />),
        openDetails && (v.more || v.lines > 0) ? <Button key="detail:close" label={glyph(look, GLYPH.detail, 'Full details')} variant="secondary" onPress={openDetails} /> : null,
      ])}
    </Box>
  )
}

// The terminal has no Home: a folding card with the same words, while a check is stored.
export function closeTerminal($: EngineInterface, ui: Ui, v: CloseView | null) {
  if (!v) return null
  const { Box, Button, Text } = ui
  return (
    <Box flexDirection="column" rowGap={1}>
      <Text>{`Ready to archive? ${v.word}`}</Text>
      {v.notes.map((n, i) => <Text key={`cn${i}`} dimColor>{n.text}</Text>)}
      {v.rows.map((r, i) => (
        <Box key={`cr${i}`} flexDirection="column">
          <Text>{`${r.word} · ${r.text}`}</Text>
          {r.sub ? <Text dimColor>{r.sub}</Text> : null}
        </Box>
      ))}
      {v.more ? <Text dimColor>{v.more.replace(' on Full details', `: run ${CLOSE_CMD}`)}</Text> : null}
      {buttonRow(ui, [
        <Button key="close-run" label="Run close check" variant="secondary" onPress={() => fillPrompt($, null, CLOSE_CMD)} />,
        ...v.fixes.map(l => <Button key={`close-fix:${l.id}`} label={fixLabel(l, v.fixes)} variant="secondary" onPress={() => fillPrompt($, null, closeFill(l))} />),
      ])}
    </Box>
  )
}
