import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Pane, Release } from '../types'
import { versionCard, versionLine } from './html'
import type { Block, Tone } from './html'
import { GLYPH, buttonRow, getPane, glyph, isNum, isObj, isStr, paneW, pic } from './shared'
import type { Look, Ui } from './shared'

// Which Abo Fles this session runs, what Claude Code has installed, and the newest release on GitHub; Home's Version card says which of
// three things is true: up to date, a newer version installed but this session still runs the old one (Reload), or a newer one out (Update).
// The running abofles version comes from the hooks (they run from the folder the session started with); the running abofles-guide version
// is this module's own, read when it loads (a reload loads it again). The newest release is asked of GitHub's public list at most every 6 hours.

const REPO = 'Sajadh92/abofles'
const CHECK_MS = 6 * 3600_000
const release = atom({ plugin: 'abofles-guide', key: 'release' } as const, null as Release | null)
let runningGuide: string | null | undefined // this module's version: read on first use, once per load (a reload starts it over)

// `2.10.1` > `2.9.4`: numeric parts in order; anything else compares as equal.
export function newer(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false
  const pa = a.split(/[.-]/).map(Number)
  const pb = b.split(/[.-]/).map(Number)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0
    const y = pb[i] ?? 0
    if (Number.isNaN(x) || Number.isNaN(y)) return false
    if (x !== y) return x > y
  }
  return false
}

// The newest release per plugin from GitHub's list (tags `abofles--v2.10.1`, `abofles-guide--v0.12.1`); drafts and pre-releases left out.
export function releasesOf(list: unknown, at: number): Release {
  const out: Release = { abofles: null, guide: null, checkedAt: at }
  for (const r of Array.isArray(list) ? list : []) {
    if (!isObj(r) || r.draft || r.prerelease || !isStr(r.tag_name)) continue
    const m = /^(abofles|abofles-guide)--v(\d+\.\d+\.\d+)$/.exec(r.tag_name)
    if (!m) continue
    const k = m[1] === 'abofles' ? 'abofles' : 'guide'
    const url = isStr(r.html_url) && r.html_url.startsWith('https://github.com/') ? r.html_url : `https://github.com/${REPO}/releases`
    if (!out[k] || newer(m[2], out[k]?.version)) out[k] = { version: m[2]!, url }
  }
  return out
}

// Once per load: this module's own version, from its plugin.json. Never throws.
export async function noteRunning($: EngineInterface) {
  if (runningGuide !== undefined) return
  try {
    const v = JSON.parse(await $.fs.read(`${$.plugin.root}/.claude-plugin/plugin.json`)).version
    runningGuide = isStr(v) ? v : null
  } catch {
    runningGuide = null
  }
}

function cleanRelease(x: unknown): Release | null {
  if (!isObj(x) || !isNum(x.checkedAt)) return null
  const one = (y: unknown) => (isObj(y) && isStr(y.version) && isStr(y.url) ? { version: y.version, url: y.url } : null)
  return { abofles: one(x.abofles), guide: one(x.guide), checkedAt: x.checkedAt }
}

// The newest releases: the session's copy, else the one an earlier session kept; asked of GitHub again when older than 6 hours (or `force`).
// With `Update check = off` no request is made. Never throws.
export async function checkRelease($: EngineInterface, force = false) {
  try {
    if ((await getPane($))?.version?.check === 'off') return
    let cur = await read($, release)
    if (!cur) {
      cur = cleanRelease(await $.store.get('release').catch(() => undefined))
      if (cur) await update($, release, () => cur)
    }
    const now = await $.clock.now()
    if (!force && cur && now - cur.checkedAt < CHECK_MS) return
    const r = await $.http.fetch(`https://api.github.com/repos/${REPO}/releases?per_page=30`, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'abofles-guide' } })
    if (!r.ok) return
    const next = releasesOf(JSON.parse(r.text), now)
    await update($, release, () => next)
    await $.store.set('release', next).catch(() => {})
  } catch {
    // offline or refused: the card shows what it knows
  }
}

export type VersionView = {
  kind: 'update' | 'reload' | 'ok'
  tone: Tone
  word: string // the pill: Update, Reload, Up to date
  title: string
  sub: string
  line: string // the footer line: `Abo Fles 2.10.1 · guide 0.12.1 · up to date`
  url: string | null // the release notes of the newer version
  checked: number | null
}

// What the card says, from the three sources. Pure: the tests drive it.
export function versionView(p: Pane | null, guideRunning: string | null, rel: Release | null): VersionView | null {
  const v = p?.version
  // the hooks' own version; a payload an older hook wrote has none: the installed one stands in until the next refresh
  const runA = v?.running.abofles ?? v?.installed.abofles ?? null
  const insA = v?.installed.abofles ?? null
  const insG = v?.installed['abofles-guide'] ?? null
  if (!runA && !guideRunning) return null
  const runs = [runA ? `Abo Fles ${runA}` : null, guideRunning ? `guide ${guideRunning}` : null].filter(Boolean).join(' · ')
  const checked = rel?.checkedAt ?? null
  const outA = rel?.abofles && newer(rel.abofles.version, insA ?? runA) ? rel.abofles : null
  const outG = rel?.guide && newer(rel.guide.version, insG ?? guideRunning) ? rel.guide : null
  if (v?.check !== 'off' && (outA || outG)) {
    const what = [outA ? `Abo Fles ${outA.version}` : null, outG ? `guide ${outG.version}` : null].filter(Boolean).join(' and ')
    return { kind: 'update', tone: 'blue', word: 'Update', title: `${what} ${outA && outG ? 'are' : 'is'} out`, sub: `You have ${runs}.`, line: `${runs} · update available`, url: (outA ?? outG)?.url ?? null, checked }
  }
  const waitA = newer(insA, runA)
  const waitG = newer(insG, guideRunning)
  if (waitA || waitG) {
    const what = [waitA ? `Abo Fles ${insA}` : null, waitG ? `guide ${insG}` : null].filter(Boolean).join(' and ')
    return { kind: 'reload', tone: 'amber', word: 'Reload', title: `${what} ${waitA && waitG ? 'are' : 'is'} installed`, sub: `This session still runs ${runs}. Reload to use the new one.`, line: `${runs} · reload to update`, url: null, checked }
  }
  return { kind: 'ok', tone: 'green', word: 'Up to date', title: runs, sub: '', line: `${runs} · up to date`, url: null, checked }
}

export async function getVersionView($: EngineInterface): Promise<VersionView | null> {
  await noteRunning($)
  return versionView(await getPane($), runningGuide ?? null, await read($, release))
}

// The update, done by Claude in the session: the marketplace refreshed, then both plugins, then a reload.
export function updatePrompt(market: string | null): string {
  const m = market ?? 'sajadh92'
  return `Update Abo Fles: run \`claude plugin marketplace update ${m}\`, then \`claude plugin update abofles@${m}\` and \`claude plugin update abofles-guide@${m}\`, and tell me what changed. I will run /reload-plugins after.`
}

async function reloadNow($: EngineInterface) {
  try {
    await $.command.run({ command: 'reload-plugins', args: '' })
  } catch {
    const r = await $.prompt.fill({ text: '/reload-plugins', mode: 'replace' }).catch(() => ({ isFilled: false }))
    if (!r.isFilled) $.ui.toast('Type /reload-plugins to load the new version')
  }
}

async function updateNow($: EngineInterface, market: string | null) {
  const text = updatePrompt(market)
  const draft = (await $.prompt.read().catch(() => ({ text: '' }))).text.trim()
  const r = await $.prompt.fill({ text: draft ? `\n${text}` : text, mode: draft ? 'append' : 'replace' }).catch(() => ({ isFilled: false }))
  if (!r.isFilled) $.ui.toast('Prompt box is not open')
}

// Home's card: only when there is something to do (Update or Reload); up to date is the footer's line alone.
export async function versionHomeCard($: EngineInterface, ui: Ui, look: Look) {
  const v = await getVersionView($)
  if (!v || v.kind === 'ok') return null
  const { Box, Button, Link } = ui as Ui & { Link: (props: { href: string; label?: string }) => JSX.Element }
  const market = (await getPane($))?.version?.market ?? null
  const b: Block = versionCard({ word: v.word, tone: v.tone, title: v.title, sub: v.sub, paneW: paneW(look) })
  return (
    <Box flexDirection="column" rowGap="sm">
      {pic(ui, look, b, ['Version', v.word, v.title, v.sub])}
      {buttonRow(ui, [
        v.kind === 'update' ? <Button key="version:update" label={glyph(look, GLYPH.use, 'Update')} variant="primary" onPress={() => updateNow($, market)} /> : null,
        v.kind === 'reload' ? <Button key="version:reload" label={glyph(look, GLYPH.show, 'Reload now')} variant="primary" onPress={() => reloadNow($)} /> : null,
        v.url ? <Link key="version:notes" href={v.url} label="What's new" /> : null,
      ])}
    </Box>
  )
}

// The footer's version line, on every page: muted, one line (coloured when there is something to do).
export function versionFoot(ui: Ui, look: Look, v: VersionView | null) {
  if (!v) return null
  return pic(ui, look, versionLine(v.line, v.tone, v.kind !== 'ok'), [v.line])
}
