#!/usr/bin/env python3
"""Build abofles.com into site/dist.

Hand-written pages live in site/src/pages/*.html (a JSON front matter comment, then the <main> body).
Generated pages read the repo itself, so a release only needs `python3 site/build.py`:
  /changelog/  from CHANGELOG.md (every version, with GitHub release links)
  /config/     from the override table in plugins/abofles/README.md
  sitemap.xml, robots.txt, llms.txt, llms-full.txt, _headers, _redirects
Python 3.8+, standard library only.
"""
import html
import json
import re
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'site' / 'src'
OUT = ROOT / 'site' / 'dist'
BASE = 'https://abofles.com'
REPO = 'https://github.com/Sajadh92/abofles'
INSTA = 'https://www.instagram.com/abofles/'
TODAY = date.today().isoformat()

PLUGIN = json.loads((ROOT / 'plugins/abofles/.claude-plugin/plugin.json').read_text())
GUIDE = json.loads((ROOT / 'plugins/abofles-guide/.claude-plugin/plugin.json').read_text())
CHANGELOG = (ROOT / 'CHANGELOG.md').read_text()
README = (ROOT / 'plugins/abofles/README.md').read_text()

esc = html.escape


# ---------------------------------------------------------------- facts the copy quotes (all read from the repo)
def facts():
    import sys
    sys.path.insert(0, str(ROOT / 'plugins/abofles/scripts'))
    import topics_data  # noqa: E402
    domains = topics_data.DOMAINS
    # a domain is (code, name, covers, reviewers, [(area, ["Name | T# | signals", …]), …])
    n_topics = sum(len(lines) for d in domains for _area, lines in d[4])
    agents = sorted(p.stem for p in (ROOT / 'plugins/abofles/agents').glob('*.md'))
    versions = re.findall(r'^## (.+?) — (\d{4}-\d{2}-\d{2})$', CHANGELOG, re.M)
    return {
        'version': PLUGIN['version'],
        'guide_version': GUIDE['version'],
        'domains': len(domains),
        'topics': n_topics,
        'agents': len(agents),
        'reviewers': 13,
        'versions': len(versions),
        'first_release': versions[-1][1] if versions else '',
        'latest_date': versions[0][1] if versions else '',
    }


F = facts()
assert F['topics'] > 1000, F  # the lookup table must have been read


def fmt_int(n):
    return f'{n:,}'


# ---------------------------------------------------------------- inline markdown (the subset CHANGELOG / README use)
def inline(md):
    parts = re.split(r'(`[^`]+`)', md)
    out = []
    for p in parts:
        if p.startswith('`') and p.endswith('`') and len(p) > 1:
            out.append(f'<code>{esc(p[1:-1])}</code>')
            continue
        t = esc(p, quote=False)
        t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<em>\1</em>', t)

        def link(m):
            text, url = m.group(1), m.group(2)
            if not re.match(r'https?://', url):
                url = f'{REPO}/blob/main/{url.lstrip("./")}'
            return f'<a href="{esc(url)}" rel="noopener">{text}</a>'
        t = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, t)
        out.append(t)
    return ''.join(out)


def md_block(md):
    """Bullets (two levels), paragraphs. Enough for a changelog entry."""
    lines = md.strip('\n').split('\n')
    html_out, items = [], []
    para = []

    def flush_para():
        if para:
            html_out.append(f'<p>{inline(" ".join(para))}</p>')
            para.clear()

    def flush_list():
        if not items:
            return
        buf = ['<ul>']
        for top, subs in items:
            buf.append(f'<li>{inline(top)}')
            if subs:
                buf.append('<ul>' + ''.join(f'<li>{inline(s)}</li>' for s in subs) + '</ul>')
            buf.append('</li>')
        buf.append('</ul>')
        html_out.append(''.join(buf))
        items.clear()

    for raw in lines:
        if not raw.strip():
            flush_para()
            continue
        m_top = re.match(r'^- (.*)', raw)
        m_sub = re.match(r'^\s{2,}[-*] (.*)', raw)
        if m_top:
            flush_para()
            items.append([m_top.group(1), []])
        elif m_sub and items:
            items[-1][1].append(m_sub.group(1))
        elif items and raw.startswith('  '):
            if items[-1][1]:
                items[-1][1][-1] += ' ' + raw.strip()
            else:
                items[-1][0] += ' ' + raw.strip()
        else:
            flush_list()
            para.append(raw.strip())
    flush_para()
    flush_list()
    return '\n'.join(html_out)


# ---------------------------------------------------------------- shared pieces
ICON = {
    'github': '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .5a11.5 11.5 0 0 0-3.64 22.4c.58.1.79-.25.79-.56v-2c-3.2.7-3.87-1.36-3.87-1.36-.53-1.33-1.28-1.69-1.28-1.69-1.05-.71.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.76 2.7 1.25 3.36.96.1-.75.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.08 0 4.41-2.69 5.39-5.26 5.67.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z"/></svg>',
    'insta': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none"/></svg>',
    'copy': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    'menu': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    'arrow': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
}


def copy_btn(text=None, label='Copy'):
    data = f' data-text="{esc(text)}"' if text is not None else ''
    return f'<button class="copy" type="button" aria-label="Copy command"{data}>{ICON["copy"]}<span>{label}</span></button>'


def cmd(line, prompt=True):
    p = '<span class="p" aria-hidden="true">›</span>' if prompt else ''
    return f'<div class="cmd">{p}<code>{esc(line)}</code>{copy_btn()}</div>'


NAV = [
    ('/#features', 'Features'),
    ('/pane/', 'The pane'),
    ('/agents/', 'Agents'),
    ('/install/', 'Install'),
    ('/changelog/', 'Changelog'),
    ('/config/', 'Config'),
]


def header(path, lang):
    cur = ' aria-current="page"'
    links = ''.join(f'<a href="{h}"{cur if h == path else ""}>{t}</a>' for h, t in NAV)
    other = ('<a class="ar" href="/ar/" hreflang="ar" lang="ar">عربي</a>' if lang == 'en'
             else '<a href="/" hreflang="en" lang="en">English</a>')
    return f'''<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
  <a class="brand" href="{'/ar/' if lang == 'ar' else '/'}" aria-label="Abo Fles home">
    <img src="/assets/mascot.webp" width="38" height="38" alt="">
    <b>Abo Fles</b><span lang="ar">ابو فليس</span>
  </a>
  <nav class="nav" id="nav" aria-label="Main">{links}{other}
    <a class="gh" href="{REPO}" rel="noopener">{ICON['github']}<span>GitHub</span></a>
  </nav>
  <button class="menu-btn" type="button" aria-label="Menu" aria-expanded="false" aria-controls="nav">{ICON['menu']}</button>
</div></header>'''


def footer():
    return f'''<footer><div class="wrap">
  <div class="foot">
    <div class="about">
      <a class="brand" href="/"><img src="/assets/mascot.webp" width="38" height="38" alt=""><b>Abo Fles</b><span lang="ar">ابو فليس</span></a>
      <p>The stingy Iraqi uncle who counts every fils — and now every token. A free plugin for Claude Code.</p>
      <div class="socials">
        <a href="{INSTA}" rel="noopener" aria-label="Abo Fles on Instagram">{ICON['insta']}</a>
        <a href="{REPO}" rel="noopener" aria-label="Abo Fles on GitHub">{ICON['github']}</a>
      </div>
    </div>
    <div><h4>Product</h4><ul>
      <li><a href="/#features">Features</a></li><li><a href="/pane/">The pane</a></li>
      <li><a href="/agents/">Agents</a></li><li><a href="/config/">Config</a></li></ul></div>
    <div><h4>Get it</h4><ul>
      <li><a href="/install/">Install</a></li><li><a href="/install/#auto-update">Auto-update</a></li>
      <li><a href="/changelog/">Changelog</a></li><li><a href="{REPO}/releases" rel="noopener">Releases</a></li></ul></div>
    <div><h4>Elsewhere</h4><ul>
      <li><a href="{INSTA}" rel="noopener">Instagram @abofles</a></li><li><a href="{REPO}" rel="noopener">GitHub</a></li>
      <li><a href="/ar/" lang="ar">بالعربي</a></li><li><a href="/llms.txt">llms.txt</a></li></ul></div>
  </div>
  <div class="legal">
    <span>Abo Fles {F['version']} · guide {F['guide_version']} · MIT license</span>
    <span>Independent open-source project. Not made or endorsed by Anthropic.</span>
  </div>
</div></footer>'''


SOFTWARE_LD = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    'name': 'Abo Fles',
    'alternateName': ['abofles', 'ابو فليس'],
    'description': 'A free plugin for Claude Code that picks the model and effort for every subagent, gates questions through a Judge, runs specialist reviewers and prints a receipt with real tokens and dollars.',
    'applicationCategory': 'DeveloperApplication',
    'applicationSubCategory': 'Claude Code plugin',
    'operatingSystem': 'macOS, Linux, Windows',
    'softwareVersion': F['version'],
    'dateModified': F['latest_date'],
    'license': 'https://opensource.org/licenses/MIT',
    'url': BASE + '/',
    'downloadUrl': REPO,
    'codeRepository': REPO,
    'releaseNotes': BASE + '/changelog/',
    'image': BASE + '/assets/og.jpg',
    'offers': {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'},
    'author': {'@type': 'Person', 'name': 'Sajadh92', 'url': 'https://github.com/Sajadh92'},
    'sameAs': [REPO, INSTA],
}


def page(path, title, desc, body, lang='en', ld=None, crumbs=None, extra_head=''):
    canonical = BASE + path
    lds = [SOFTWARE_LD] if path in ('/', '/ar/') else []
    if path == '/':
        lds.append({'@context': 'https://schema.org', '@type': 'WebSite', 'name': 'Abo Fles', 'url': BASE + '/',
                    'inLanguage': ['en', 'ar']})
    if crumbs:
        lds.append({'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': n, 'item': BASE + u} for i, (n, u) in enumerate(crumbs)]})
    lds += ld or []
    ld_html = ''.join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>' for x in lds)
    alt = ''
    if path in ('/', '/ar/'):
        alt = (f'<link rel="alternate" hreflang="en" href="{BASE}/"><link rel="alternate" hreflang="ar" href="{BASE}/ar/">'
               f'<link rel="alternate" hreflang="x-default" href="{BASE}/">')
    fonts = ('family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=Geist:wght@400;500;600;700'
             '&family=Geist+Mono:wght@400;500;600;700&family=Marhey:wght@700')
    if lang == 'ar':
        fonts += '&family=IBM+Plex+Sans+Arabic:wght@400;500;700'
    noindex = '<meta name="robots" content="noindex">' if path == '/404/' else ''
    full_title = title if 'Abo Fles' in title else f'{title} · Abo Fles'
    return f'''<!doctype html>
<html lang="{lang}" dir="{'rtl' if lang == 'ar' else 'ltr'}" class="no-js">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{canonical}">{alt}
{noindex}<meta name="theme-color" content="#0E0D0C">
<meta name="color-scheme" content="dark">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Abo Fles">
<meta property="og:title" content="{esc(full_title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{BASE}/assets/og.jpg">
<meta property="og:image:width" content="1280"><meta property="og:image:height" content="640">
<meta property="og:locale" content="{'ar_IQ' if lang == 'ar' else 'en_US'}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(full_title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{BASE}/assets/og.jpg">
<link rel="icon" type="image/png" href="/assets/favicon.png">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{fonts}&display=swap">
<link rel="stylesheet" href="/css/site.css?v={F['version']}">
<link rel="alternate" type="text/plain" title="LLM summary" href="/llms.txt">
{extra_head}{ld_html}
</head>
<body>
{header(path, lang)}
<main id="main">
{body}
</main>
{footer()}
<script src="/js/site.js?v={F['version']}" defer></script>
</body>
</html>
'''


def sub(text):
    """{{fact}} placeholders in hand-written pages."""
    vals = {**{k: str(v) for k, v in F.items()}, 'topics_fmt': fmt_int(F['topics']), 'repo': REPO, 'insta': INSTA}

    def rep(m):
        key = m.group(1)
        if key.startswith('cmd:'):
            return cmd(key[4:])
        if key == 'copy':
            return copy_btn()
        if key.startswith('icon:'):
            return ICON[key[5:]]
        return vals[key]
    return re.sub(r'\{\{\s*([^}]+?)\s*\}\}', rep, text)


# ---------------------------------------------------------------- hand-written pages
def hand_pages():
    out = {}
    for f in sorted((SRC / 'pages').glob('*.html')):
        text = f.read_text()
        m = re.match(r'<!--\s*(\{.*?\})\s*-->\s*', text, re.S)
        meta = json.loads(m.group(1))
        body = sub(text[m.end():])
        out[meta['path']] = page(meta['path'], meta['title'], meta['description'], body, meta.get('lang', 'en'),
                                 ld=meta.get('ld'), crumbs=meta.get('crumbs'))
    return out


# ---------------------------------------------------------------- generated: changelog
def changelog_page():
    blocks = re.split(r'^## ', CHANGELOG, flags=re.M)[1:]
    rels = []
    for b in blocks:
        head, _, rest = b.partition('\n')
        m = re.match(r'(.+?) — (\d{4}-\d{2}-\d{2})$', head.strip())
        if not m:
            continue
        names, day = m.group(1), m.group(2)
        pairs = re.findall(r'(abofles(?:-guide)?) (\d+\.\d+\.\d+)', names)
        main = dict(pairs)
        slug = 'v' + (main.get('abofles') or ('guide-' + main.get('abofles-guide', ''))).replace('.', '-')
        rels.append({'names': names, 'day': day, 'pairs': pairs, 'slug': slug, 'body': rest})

    def rel_links(pairs):
        return ' · '.join(
            f'<a href="{REPO}/releases/tag/{n}--v{v}" rel="noopener">{n} {v}</a>' for n, v in pairs)

    rows = []
    for i, r in enumerate(rels):
        a = dict(r['pairs'])
        rows.append(
            f'<tr><td><a href="#{r["slug"]}">{esc(a.get("abofles", "—"))}</a></td>'
            f'<td>{esc(a.get("abofles-guide", "—"))}</td><td>{r["day"]}</td>'
            f'<td>{rel_links(r["pairs"])}</td></tr>')
    entries = []
    for i, r in enumerate(rels):
        latest = '<span class="latest">Latest</span>' if i == 0 else ''
        entries.append(f'''<article class="rel" id="{r['slug']}">
  <h2><a class="anchor" href="#{r['slug']}">{esc(r['names'])}</a> <span class="date">{r['day']}</span>{latest}</h2>
  {md_block(r['body'])}
  <div class="links">GitHub release: {rel_links(r['pairs'])}</div>
</article>''')
    n = len(rels)
    body = f'''<section class="page-hero"><div class="wrap narrow">
  <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span aria-hidden="true">/</span><span>Changelog</span></nav>
  <h1>Every fils, <span class="gold">written down.</span></h1>
  <p class="lede">All {n} versions since {rels[-1]['day']}, newest first, in full. The uncle keeps receipts — of course he keeps a changelog.</p>
  {cmd('/plugin marketplace update sajadh92')}
  <p class="small muted">Get the newest: run the line above and restart Claude Code, or <a class="gold" href="/install/#auto-update">turn on auto-update</a> once.</p>
</div></section>
<section style="padding-top:0"><div class="wrap narrow" style="display:grid;gap:40px">
  <div style="display:grid;gap:14px">
    <h2 style="font-size:clamp(26px,3vw,34px)">All versions</h2>
    <div class="tbl-wrap" style="max-height:420px"><table class="versions">
      <thead><tr><th scope="col">abofles</th><th scope="col">abofles-guide</th><th scope="col">Date</th><th scope="col">GitHub releases</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>
  </div>
  <div style="display:grid;gap:10px">
    <label for="filter" class="eyebrow">Search the changelog</label>
    <input id="filter" class="filter" type="search" placeholder="Try “Judge”, “Git guard”, “pane”, “receipt”…" autocomplete="off">
    <p id="filter-count" class="small muted" aria-live="polite"></p>
  </div>
  <div class="prose" style="max-width:none;gap:36px">{''.join(entries)}</div>
</div></section>'''
    return page('/changelog/', f'Changelog — every version of Abo Fles',
                f'Full changelog of Abo Fles, the free Claude Code plugin: all {n} versions with dates, details and GitHub release links. Latest: abofles {F["version"]}.',
                body, crumbs=[('Home', '/'), ('Changelog', '/changelog/')])


# ---------------------------------------------------------------- generated: config (the override table)
def config_page():
    sec = README[README.index('| Key or form |'):]
    rows = []
    for line in sec.split('\n')[2:]:
        if not line.startswith('|'):
            break
        cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        if len(cells) < 4:
            continue
        key, values, default, what = cells[0], cells[1], cells[2], ' | '.join(cells[3:])
        rows.append(f'<tr><td>{inline(key)}</td><td>{inline(values)}</td><td>{inline(default)}</td><td>{inline(what)}</td></tr>')
    example = README[README.index('## abofles overrides'):]
    example = example[:example.index('```')].strip()
    body = f'''<section class="page-hero"><div class="wrap narrow">
  <nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span aria-hidden="true">/</span><span>Config</span></nav>
  <h1>Every knob the <span class="gold">uncle</span> has.</h1>
  <p class="lede">Write a few lines in <code>CLAUDE.md</code> — or click them in the pane's Overrides editor. A project's block wins over your global <code>~/.claude/CLAUDE.md</code>. {len(rows)} keys and forms, all optional: the defaults are already stingy.</p>
</div></section>
<section style="padding-top:0"><div class="wrap" style="display:grid;gap:40px">
  <div class="narrow" style="display:grid;gap:14px">
    <h2 style="font-size:clamp(26px,3vw,34px)">A block to start from</h2>
    <pre class="block"><code>{esc(example)}</code>{copy_btn()}</pre>
    <p class="small muted">Or let Claude write one: <code>/abofles:init</code> reads your code and suggests tiers for what it does.</p>
  </div>
  <div style="display:grid;gap:14px">
    <h2 style="font-size:clamp(26px,3vw,34px)">All keys</h2>
    <div class="tbl-wrap"><table>
      <thead><tr><th scope="col">Key or form</th><th scope="col">Values</th><th scope="col">Default</th><th scope="col">What it does</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>
    <p class="small muted">Generated from the plugin's own README at build time, so it never drifts from the code.</p>
  </div>
</div></section>'''
    return page('/config/', 'Config — every override key',
                f'All {len(rows)} Abo Fles override keys for Claude Code: model and effort per topic, agent budgets, Git guard, receipts, memo — values and defaults.',
                body, crumbs=[('Home', '/'), ('Config', '/config/')])


# ---------------------------------------------------------------- machine-readable files
def llms_txt():
    return f'''# Abo Fles (abofles, ابو فليس)

> A free, MIT-licensed plugin for Claude Code. It picks the model (Haiku, Sonnet, Opus, Fable) and effort (low … max) for every subagent from a topic × side matrix, so cheap jobs stop running on expensive settings. Independent open-source project, not made or endorsed by Anthropic.

Latest: abofles {F['version']} and the companion mod abofles-guide {F['guide_version']} ({F['latest_date']}). {fmt_int(F['topics'])} topics in {F['domains']} domains for developers, designers and marketers; {F['agents']} agents including {F['reviewers']} specialist reviewers.

Install in Claude Code:
/plugin marketplace add Sajadh92/abofles
/plugin install abofles@sajadh92
/plugin install abofles-guide@sajadh92   (optional: the Abo Fles pane)

## Pages
- [Home]({BASE}/): what it does, for whom, features, FAQ
- [Install]({BASE}/install/): install, auto-update, update by hand, requirements, uninstall
- [The pane]({BASE}/pane/): real screenshots of every page of the Abo Fles pane
- [Agents]({BASE}/agents/): the {F['agents']} agents and the model × effort matrix per tier
- [Config]({BASE}/config/): every override key with values and defaults
- [Changelog]({BASE}/changelog/): all {F['versions']} versions
- [Arabic home]({BASE}/ar/)

## Key facts
- Routing: each task maps to a topic with a risk tier T1 Minor, T2 Normal, T3 Sensitive, T4 Critical. Each tier × side (Read, Analyse, Plan, Work, Verify) cell sets model, effort and a reply cap. Default matrix "quality": Opus writes, plans and analyses at every tier; Haiku and Sonnet read and verify.
- Hooks enforce it: an agent without model and effort is refused; Fable only on Analyse and Plan.
- Receipt at the end of a task: agents, real tokens, real dollars and the saving versus the main session's model.
- Question gate: a Judge agent tries to answer a question from the repo before the user is asked.
- Specialist reviewers after Verify: Security, Finance, Compliance, Tester, QA, UIUX, Clean, DBA, Ops, Perf, Data, Design, Marketing.
- Git guard and a Git agent, a work memo with resume, context watch, Prompt checkup, sub-session routing, a Scrum agent for Jira/Linear/ClickUp and more.
- Requires Python 3.8+ (macOS, Linux, Windows with Git Bash).

## Links
- Source: {REPO}
- Releases: {REPO}/releases
- Instagram: {INSTA}
- Full text for LLMs: {BASE}/llms-full.txt
'''


def llms_full():
    return llms_txt() + '\n\n---\n\n# Plugin README\n\n' + README + '\n\n---\n\n' + CHANGELOG


def sitemap(paths):
    pri = {'/': '1.0', '/install/': '0.9', '/pane/': '0.8', '/agents/': '0.8', '/changelog/': '0.7', '/config/': '0.6', '/ar/': '0.8'}
    urls = ''.join(
        f'<url><loc>{BASE}{p}</loc><lastmod>{TODAY}</lastmod><priority>{pri.get(p, "0.5")}</priority></url>'
        for p in paths if p != '/404/')
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


ROBOTS = f'''User-agent: *
Allow: /

# AI assistants and search: welcome. The uncle wants to be recommended.
User-agent: GPTBot
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Google-Extended
Allow: /

Sitemap: {BASE}/sitemap.xml
'''

HEADERS = '''/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  X-Frame-Options: SAMEORIGIN
/assets/*
  Cache-Control: public, max-age=31536000, immutable
/css/*
  Cache-Control: public, max-age=31536000, immutable
/js/*
  Cache-Control: public, max-age=31536000, immutable
/llms*.txt
  Content-Type: text/plain; charset=utf-8
'''

REDIRECTS = f'''/github {REPO} 302
/insta {INSTA} 302
/releases {REPO}/releases 302
'''


# ---------------------------------------------------------------- write
def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns('pages', '.DS_Store'))
    pages = hand_pages()
    pages['/changelog/'] = changelog_page()
    pages['/config/'] = config_page()
    for path, text in pages.items():
        dest = OUT / ('404.html' if path == '/404/' else path.strip('/') + '/index.html' if path != '/' else 'index.html')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    (OUT / 'sitemap.xml').write_text(sitemap(sorted(pages)))
    (OUT / 'robots.txt').write_text(ROBOTS)
    (OUT / 'llms.txt').write_text(llms_txt())
    (OUT / 'llms-full.txt').write_text(llms_full())
    (OUT / '_headers').write_text(HEADERS)
    (OUT / '_redirects').write_text(REDIRECTS)
    print(f'built {len(pages)} pages into {OUT.relative_to(ROOT)} · facts {F}')


if __name__ == '__main__':
    main()
