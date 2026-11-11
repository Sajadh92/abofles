#!/usr/bin/env python3
"""Generator for the abofles topic matrix.

Topics live in scripts/topics_data.py (DOMAINS: 31 domains, areas, 1,300+ topic lines); tiers, caps,
reviewers, intents and workflows live below. Edit, then run:

    python3 scripts/gen_matrix.py

It regenerates skills/abofles/references/topics.md (the domain table the skill reads),
skills/abofles/references/topic-index.md (every topic by domain and area — for people, not the model),
skills/abofles/references/matrix.md (tier bases, caps, reviewers per domain), skills/abofles/references/workflow.md
and hooks/workflow.json (domains, topics with signals and overrides, tiers, caps — read by the hook's review gate and
by scripts/topic.py, the lookup the skill calls). `--check` writes nothing and exits 1 when any file is out of date.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics_data import DOMAINS, SUGGESTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOPICS_MD = ROOT / "skills/abofles/references/topics.md"
INDEX_MD = ROOT / "skills/abofles/references/topic-index.md"
MATRIX = ROOT / "skills/abofles/references/matrix.md"
WORKFLOW_MD = ROOT / "skills/abofles/references/workflow.md"
WORKFLOW_JSON = ROOT / "hooks/workflow.json"

SIDES = ["Read", "Analyse", "Plan", "Work", "Verify"]
SIDE_NAMES = {
    "Read": "Read & Collect",
    "Analyse": "Analyse & Find Gaps",
    "Plan": "Plan & Design",
    "Work": "Act & Work",
    "Verify": "Verify & Review",
}

# Base cell per tier and side under `Matrix = quality` (the default). Overrides on a topic replace single cells.
# Opus writes, plans and analyses at every tier; Haiku and Sonnet read and verify. Code written by Sonnet from a good plan came
# back from Verify as rework: in the owner's ledgers 33% of the verdicts on Sonnet · High Work at T3 were FAIL, 22% on Opus · High at T4.
TIERS = {
    1: dict(Read="haiku·low", Analyse="opus·low", Plan="opus·low", Work="opus·low", Verify="haiku·low"),
    2: dict(Read="haiku·low", Analyse="opus·medium", Plan="opus·medium", Work="opus·medium", Verify="sonnet·low"),
    3: dict(Read="haiku·medium", Analyse="opus·medium", Plan="opus·medium", Work="opus·high", Verify="sonnet·high"),
    4: dict(Read="sonnet·low", Analyse="opus·high", Plan="opus·xhigh", Work="opus·xhigh", Verify="sonnet·xhigh"),
}

# `Matrix = economy`: the cells before 2.6 — Sonnet and Haiku write most code; each agent costs less, more of them come back as rework.
TIERS_ECONOMY = {
    1: dict(Read="haiku·low", Analyse="sonnet·low", Plan="sonnet·low", Work="haiku·medium", Verify="haiku·low"),
    2: dict(Read="haiku·low", Analyse="sonnet·medium", Plan="sonnet·medium", Work="sonnet·medium", Verify="sonnet·low"),
    3: dict(Read="haiku·medium", Analyse="opus·medium", Plan="opus·medium", Work="sonnet·high", Verify="sonnet·high"),
    4: dict(Read="sonnet·low", Analyse="opus·high", Plan="opus·xhigh", Work="opus·high", Verify="opus·high"),
}

# The specialist reviewers' cell per tier, in both matrices: a missed money, auth or data flaw at T4 is judgement, so it stays on Opus.
REVIEW_TIERS = {1: "haiku·low", 2: "sonnet·low", 3: "sonnet·high", 4: "opus·high"}

# Under `Matrix = quality` a topic override never puts these sides below Opus (an `ov Work=sonnet·high` line is an economy cell).
QUALITY_SIDES = ("Analyse", "Plan", "Work")
MODEL_RANK = {"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3}


def quality_ov(ov):
    """A topic's overrides under `Matrix = quality`: those that put Analyse, Plan or Work below Opus are dropped."""
    return {k: v for k, v in ov.items() if not (k in QUALITY_SIDES and MODEL_RANK.get(v.split("·")[0], 2) < 2)}

# Reply cap in lines (status header excluded) per tier and side.
# A topic may override single caps with a 5th tuple element, e.g. {"Read": 120}.
CAPS = {
    1: dict(Read=20, Analyse=10, Plan=25, Work=10, Verify=10),
    2: dict(Read=40, Analyse=20, Plan=50, Work=20, Verify=20),
    3: dict(Read=60, Analyse=35, Plan=90, Work=30, Verify=35),
    4: dict(Read=100, Analyse=50, Plan=150, Work=40, Verify=60),
}

# Specialist reviewers: the Verify side split by what each one checks. Cell = REVIEW_TIERS at the tier unless a
# project override (`- Review: Security / T4 = opus·xhigh`) says otherwise. key -> (side word, emoji, agent, checks, tools)
REVIEWERS = {
    "security": ("Security", "🛡️", "abofles-security", "OWASP top 10, authz and IDOR, secrets, injection, crypto, session handling", "read-only"),
    "finance": ("Finance", "💰", "abofles-finance", "rounding and precision, currency, ledger invariants, idempotency, double entry, limits", "read-only"),
    "compliance": ("Compliance", "📜", "abofles-compliance", "PII exposure, consent, retention, audit trail, legal and regulatory text", "read-only"),
    "tester": ("Tester", "🧪", "abofles-tester", "runs the suite, adds the missing edge-case tests (zero, null, duplicate, concurrent, boundary)", "read + edit tests only"),
    "qa": ("QA", "🎯", "abofles-qa", "acceptance against the ticket, user flows end to end, regressions, error paths", "read + run"),
    "uiux": ("UIUX", "🎨", "abofles-uiux", "accessibility, RTL, responsive layout, design tokens, copy; Better Design and UX Pilot connectors when connected", "all tools (connectors)"),
    "cleancoder": ("Clean", "🧹", "abofles-cleancoder", "naming, duplication, dead code, structure — no behaviour change", "read-only"),
    "dba": ("DBA", "🗄️", "abofles-dba", "migration safety and reversibility, locks and long transactions, indexes and query plans, constraints, replicas and backups", "read + run read-only SQL"),
    "ops": ("Ops", "⚙️", "abofles-ops", "pipeline and deploy safety, IaC plan and drift, rollback path, secrets exposure, server and network config, blast radius", "read-only"),
    "perf": ("Perf", "⚡", "abofles-perf", "hot paths and complexity, N+1 and round-trips, allocations, caching, benchmark or load evidence before and after", "read + run benchmarks"),
    "data": ("Data", "📊", "abofles-data", "pipeline idempotency and backfills, metric definitions, aggregation correctness, double counting, freshness, data contracts", "read + run read-only queries"),
    "design": ("Design", "🖌️", "abofles-design", "brief fit, brand, typography and layout, print and export specs, video and audio specs, 3D output, licences of fonts, images, music and AI assets", "all tools (measures with sips, pdfinfo, ffprobe; Better Design when connected; never publishes)"),
    "marketing": ("Marketing", "📣", "abofles-marketing", "platform specs and limits, ad policy, honest claims and disclosure, hook and call to action, audience, budget and schedule, links and tracking, brand voice", "read + web fetch (never posts or changes a campaign)"),
}

# Specialists of the Work cell: not reviewers (no post-Verify step, no review gate), spawned instead of the generic worker for their kind of
# work. Cell = the topic's Work cell. key -> (side word, emoji, agent, owns, tools)
SPECIALISTS = {
    "scrum": ("Scrum", "📋", "abofles-scrum", "tickets and boards in Jira, ClickUp, Linear, Asana, monday.com, Trello or GitHub Issues: reads tickets for the other sides, refines stories and acceptance criteria, splits epics, estimates, plans sprints, keeps status and PR links in step with the code, standup and sprint reports; writes to the tracker only on the user's quoted ask, else drafts", "all tools (the trackers' connectors)"),
    "git": ("Git", "🌿", "abofles-git", "every git and gh write: branches and worktrees, commits, merges and rebases, conflicts, PRs, tags and releases; claims for multi-session work", "read + git/gh via Bash, edit (conflicts)"),
}

# Reviewers per domain and tier come from topics_data.DOMAINS (T1/T2: the generic verifier only). The first one is the
# domain's primary reviewer. A topic adds its own with `| +reviewer` on its line.
GROUP_REVIEWERS = {code: {3: list(rev.get(3, [])), 4: list(rev.get(4, []))} for code, _, _, rev, _ in DOMAINS}
TOPIC_REVIEWERS = {}  # filled by parse_topics()

# Workflow = the ordered steps of a task, by intent (what the ask is) and, for a change, by tier. "Review" = the
# specialist reviewers of the topic's group at that tier, run in parallel after Verify passes. Judge / Explain are
# not steps: the question gate runs them whenever a step raises an owner question (T4: the Plan's open decisions
# go through the Judge before Work starts).
INTENTS = {
    "question": ("where is X, what calls Y, how does Z work", ["Read"]),
    "review": ("review X, find risks, audit, compare options", ["Read", "Analyse", "Review"]),
    "design": ("plan how, design an approach or architecture, migration strategy", ["Read", "Analyse", "Plan"]),
    "change": ("add, fix, implement, refactor, make, create, edit, export, schedule, launch (a logo, poster, video or post too) — by tier", None),
}
CHANGE_WORKFLOWS = {
    1: ["Work", "Verify"],
    2: ["Read", "Work", "Verify"],
    3: ["Read", "Analyse", "Plan", "Work", "Verify", "Review"],
    4: ["Read", "Analyse", "Plan", "Work", "Verify", "Review"],
}
REVIEW_GATE = {3: "warn", 4: "on"}  # default `Review gate = auto`: T4 refuses the receipt once, T3 warns in the guide

TOPIC_RE = re.compile(r"^(?P<name>[^|]+?)\s*\|\s*T(?P<tier>[1-4])\s*\|\s*(?P<signals>[^|]*?)\s*(?:\|\s*(?P<rest>.*))?$")


def parse_line(line):
    """'Name | T# | signals[ | ov Side=cell,...][ | cap Side=N,...][ | +rev,+rev]' -> (name, tier, ov, signals, caps, extra_reviewers)."""
    m = TOPIC_RE.match(line.strip())
    if not m:
        raise ValueError(f"bad topic line: {line!r}")
    ov, caps, extra = {}, {}, []
    for part in (m.group("rest") or "").split("|"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("ov "):
            for kv in part[3:].split(","):
                k, v = kv.split("=")
                ov[k.strip()] = v.strip()
        elif part.startswith("cap "):
            for kv in part[4:].split(","):
                k, v = kv.split("=")
                caps[k.strip()] = int(v)
        elif part.startswith("+"):
            extra += [x.strip().lstrip("+") for x in part.split(",") if x.strip()]
        else:
            raise ValueError(f"bad topic suffix {part!r} in {line!r}")
    for k in list(ov) + list(caps):
        if k not in SIDES:
            raise ValueError(f"unknown side {k!r} in {line!r}")
    for r in extra:
        if r not in REVIEWERS:
            raise ValueError(f"unknown reviewer {r!r} in {line!r}")
    return m.group("name").strip(), int(m.group("tier")), ov, m.group("signals").strip(), caps, extra


def parse_topics():
    """[(code, domain name, blurb, [(area, [(name, tier, ov, signals, caps)])])] — also fills TOPIC_REVIEWERS and checks uniqueness."""
    out, seen = [], set()
    for code, name, blurb, rev, areas in DOMAINS:
        for k in list(rev.get(3, [])) + list(rev.get(4, [])):
            if k not in REVIEWERS:
                raise ValueError(f"unknown reviewer {k!r} on domain {code}")
        rows_by_area = []
        for area, lines in areas:
            rows = []
            for line in lines:
                tname, tier, ov, sig, caps, extra = parse_line(line)
                if tname in seen:
                    raise ValueError(f"duplicate topic name: {tname}")
                seen.add(tname)
                if extra:
                    TOPIC_REVIEWERS[tname] = extra
                rows.append((tname, tier, ov, sig, caps))
            rows_by_area.append((area, rows))
        out.append((code, name, blurb, rows_by_area))
    return out


PARSED = parse_topics()
# (code, domain name, [(topic, tier, overrides, signals, cap overrides), ...]) — flat per domain, areas merged in order
TOPICS = [(code, name, [row for _, rows in areas for row in rows]) for code, name, _, areas in PARSED]
DOMAIN_BLURB = {code: blurb for code, _, blurb, _ in PARSED}
DOMAIN_AREAS = {code: areas for code, _, _, areas in PARSED}
TIER_NAME = {1: "🟢 Minor", 2: "🟡 Normal", 3: "🟠 Sensitive", 4: "🔴 Critical"}


def numbered():
    n = 0
    for letter, group, rows in TOPICS:
        out = []
        for topic, tier, ov, sig, capov in rows:
            n += 1
            out.append((n, topic, tier, ov, sig, capov))
        yield letter, group, out


def cell(tier, ov, side):
    ov = quality_ov(ov)
    v = ov.get(side, TIERS[tier][side])
    return f"**{v}**" if side in ov else v


def reviewers_of(letter, topic, tier):
    """The reviewer keys a topic needs at a tier: its domain's list plus the topic's own, in order, no repeats."""
    if tier < 3:
        return []
    out = list(GROUP_REVIEWERS.get(letter, {}).get(tier, []))
    for k in TOPIC_REVIEWERS.get(topic, []):
        if k not in out:
            out.append(k)
    return out


def rev_names(keys):
    return ", ".join(REVIEWERS[k][0] for k in keys) if keys else "—"


def tier_span(rows):
    ts = sorted({t for _, t, *_ in rows})
    return "T%d" % ts[0] if len(ts) == 1 else "T%d–T%d" % (ts[0], ts[-1])


def build_topics_md():
    """topics.md: the domain table the skill reads (short), plus how to look a topic up. Never the topic list."""
    total = sum(len(r) for _, _, r in TOPICS)
    m = [
        "# Topics — domains and lookup",
        "",
        f"Generated by `scripts/gen_matrix.py` from `scripts/topics_data.py` — {total} topics in {len(TOPICS)} domains. Do not edit by hand.",
        "",
        "**Never read the topic list to route.** Look the topic up with one Bash call (≤ 6 lines back): "
        "`bash \"<plugin>/scripts/py\" \"<plugin>/scripts/topic.py\" find \"<the ask, 3–8 words>\"` — copy the exact command from the SessionStart note `abofles: topic lookup = …` (`scripts/py` picks python3, python or py -3); "
        "`topic.py show <#|name>` prints one topic's cells, `topic.py domains` this table, `topic.py list <CODE>` a domain. "
        "Project overrides (`Topic: Name = T#`, `Topic / Side = cell`, `Topic / Side cap = N`) are applied in the output and marked `· Project Override`. "
        "The whole list is in [topic-index.md](topic-index.md) (for people).",
        "",
        "| Code | Domain | Tiers | Covers | Reviewers 🟠 T3 → 🔴 T4 |",
        "|---|---|---|---|---|",
    ]
    for code, name, rows in TOPICS:
        g = GROUP_REVIEWERS.get(code, {})
        m.append(f"| `{code}` | {name} | {tier_span(rows)} | {DOMAIN_BLURB[code]} | {rev_names(g.get(3, []))} → {rev_names(g.get(4, []))} |")
    m += [
        "",
        "Topic not found (no match or score too low): take the closest domain's typical tier from the fast tier test and name the nearest topic in the route line; tell the user the topic is new so it can be added to `topics_data.py`.",
        "",
    ]
    return "\n".join(m)


def build_topic_index():
    """topic-index.md: every topic by domain and area — `#. Name · T# — overrides` — for browsing, not for routing."""
    total = sum(len(r) for _, _, r in TOPICS)
    m = [
        "# Topic index (full)",
        "",
        f"Generated by `scripts/gen_matrix.py` — {total} topics in {len(TOPICS)} domains. For people: the model routes with `scripts/topic.py find`, never by reading this file.",
        "",
        "`N. Topic · Tier — cell overrides, cap overrides, +extra reviewers` (sides named in full). Only the overrides differ from the tier base ([matrix.md](matrix.md)).",
        "",
    ]
    n = 0
    for code, name, blurb, areas in PARSED:
        g = GROUP_REVIEWERS.get(code, {})
        m.append(f"## {code}. {name}")
        m.append("")
        m.append(f"{blurb}. Reviewers: 🟠 {rev_names(g.get(3, []))} · 🔴 {rev_names(g.get(4, []))}.")
        for area, rows in areas:
            m.append("")
            m.append(f"**{area}**")
            for topic, tier, ov, _, capov in rows:
                n += 1
                parts = [f"{k}={v}" for k, v in ov.items()] + [f"cap {k}={v}" for k, v in capov.items()]
                parts += [f"+{REVIEWERS[r][0]}" for r in TOPIC_REVIEWERS.get(topic, [])]
                o = (" — " + ", ".join(parts)) if parts else ""
                m.append(f"- {n}. {topic} · T{tier}{o}")
        m.append("")
    return "\n".join(m).rstrip() + "\n"


def build_matrix():
    m = [
        "# Abo Fles — Matrix",
        "",
        "Generated by `scripts/gen_matrix.py`. Do not edit by hand.",
        "",
        "Each cell is `model·effort`. A topic's cells are its tier's row of the matrix the project picks (`- Matrix = quality | economy`, default quality) unless its line in `topics_data.py` overrides one "
        "(`scripts/topic.py show <topic>` prints the final cells, with project overrides applied).",
        "",
        "Sides: " + " · ".join(f"{k} = {v}" for k, v in SIDE_NAMES.items()),
        "",
        "## Tier base (`Matrix = quality`, the default)",
        "",
        "Opus writes, plans and analyses at every tier; Haiku and Sonnet read and verify. Code written by a weaker model from a good plan comes back from Verify as rework, and each round costs a Work and a Verify agent more.",
        "",
        "| Tier | A mistake means… | Read | Analyse | Plan | Work | Verify |",
        "|---|---|---|---|---|---|---|",
    ]
    means = {1: "cosmetic, local, instantly reversible", 2: "a test catches it; dev-only; reversible with a redeploy",
             3: "wrong production behaviour, business rule, shared infra, SLO", 4: "money, auth, secrets, PII, data loss, prod infra, irreversible"}
    for t, cells in TIERS.items():
        m.append(f"| {TIER_NAME[t]} (T{t}) | {means[t]} | " + " | ".join(cells[s] for s in SIDES) + " |")
    m += [
        "",
        "Under `quality` a topic override that puts Analyse, Plan or Work below Opus (an economy cell) is skipped; an override up (Fable on Plan or Analyse) stays.",
        "",
        "## Economy base (`- Matrix = economy`)",
        "",
        "The cells before 2.6: Sonnet and Haiku write most code. Each agent costs less; more of them come back from Verify as rework. Every topic override applies.",
        "",
        "| Tier | Read | Analyse | Plan | Work | Verify |",
        "|---|---|---|---|---|---|",
    ]
    for t, cells in TIERS_ECONOMY.items():
        m.append(f"| {TIER_NAME[t]} (T{t}) | " + " | ".join(cells[s] for s in SIDES) + " |")
    m += [
        "",
        "## Review and Git cells (both matrices)",
        "",
        "- **Review cell** (the specialist reviewers after Verify): " + " · ".join(f"T{t} {c}" for t, c in REVIEW_TIERS.items()) + ". A missed money, auth or data flaw is judgement, so 🔴 stays on Opus. `- Review: <Tag> / T<tier> = model·effort` changes one.",
        "- **Git cell** (`abofles-git`, and `abofles-scrum` for tickets and boards): the topic's economy Work cell — commits, merges and PRs follow the work up, they do not write it. `scripts/topic.py` prints it as `Git/Scrum …`.",
    ]
    m += [
        "",
        "## Reply cap base (lines, status header excluded)",
        "",
        "| Tier | Read | Analyse | Plan | Work | Verify |",
        "|---|---|---|---|---|---|",
    ]
    for t, caps in CAPS.items():
        m.append(f"| T{t} | " + " | ".join(str(caps[s]) for s in SIDES) + " |")
    m += [
        "",
        "Never cut a finding to fit a cap. Rank, keep the top ones in full, end with `+N more: <one line each>`.",
        "",
        "## Tier bumps (applied by the skill, Step 2)",
        "",
        "- **+1** when the ask names production, live customers, real money or real personal data and the topic's tier is below T4 (`prod`, `live`, `customer data`, `in production`).",
        "- **−1** (floor T1; never below T3 for Money, Security, Privacy topics) when the ask is read-only, a dry run, local-only or a sandbox (`read only`, `dry run`, `localhost`, `sandbox`, `test mode`).",
        "- A read that only collects facts stays **Codebase Exploration & Onboarding (T1)** whatever code it touches.",
        "",
        "## Domains",
        "",
        "| Code | Domain | Topics | Tiers | Areas | Reviewers 🟠 T3 | Reviewers 🔴 T4 |",
        "|---|---|---|---|---|---|---|",
    ]
    total = 0
    for code, name, blurb, areas in PARSED:
        rows = [r for _, rs in areas for r in rs]
        total += len(rows)
        g = GROUP_REVIEWERS.get(code, {})
        m.append(f"| `{code}` | {name} | {len(rows)} | {tier_span(rows)} | " + "; ".join(f"{a} ({len(rs)})" for a, rs in areas) + f" | {rev_names(g.get(3, []))} | {rev_names(g.get(4, []))} |")
    m += ["", "## Cell overrides (topics whose cells differ from the tier base)", "", "A Work, Analyse or Plan override below Opus applies under `Matrix = economy` only.", "", "| # | Topic | Domain | Tier | Overrides | Caps |", "|---|---|---|---|---|---|"]
    for letter, group, rows in numbered():
        for n, topic, tier, ov, sig, capov in rows:
            if ov or capov:
                m.append(f"| {n} | {topic} | {letter} | T{tier} | " + (", ".join(f"{k}={v}" for k, v in ov.items()) or "—") + " | " + (", ".join(f"{k}={v}" for k, v in capov.items()) or "—") + " |")
    m += ["", "Topics that always add a reviewer (T3 and T4): " + "; ".join(f"**{t}** → {rev_names(v)}" for t, v in TOPIC_REVIEWERS.items()) + ".", ""]
    m += ["## Suggestions (`💡 suggest` lines of the lookup)", ""]
    for sug in SUGGESTIONS:
        m.append(f"- **{sug['name']}** (stack `{sug['stack']}`) on: " + "; ".join(sug["topics"]) + f". {sug['text']}")
    m.append("")
    m[2] = f"Generated by `scripts/gen_matrix.py` — {total} topics in {len(TOPICS)} domains ([topic-index.md](topic-index.md) lists them; `scripts/topic.py` finds one). Do not edit by hand."
    return "\n".join(m)


def build_workflow_md():
    m = [
        "# Workflow and specialist reviewers",
        "",
        "Generated by `scripts/gen_matrix.py`. Do not edit by hand. Read when choosing the steps of a task (Step 1½) or the reviewers of a 🟠/🔴 change.",
        "",
        "Two inputs pick the workflow: the **topic** gives the tier, the **intent** (what the ask is) gives the shape. Judge and Explain are not steps — the question gate runs them whenever a step raises an owner question; on a 🔴 Critical change the Plan's open decisions go through the Judge before Work starts.",
        "",
        "## Intent → steps",
        "",
        "| Intent | The ask looks like | Steps |",
        "|---|---|---|",
    ]
    for k, (desc, steps) in INTENTS.items():
        m.append(f"| `{k}` | {desc} | " + (" → ".join(steps) if steps else "see the table below") + " |")
    m += ["", "## `change` by tier", "", "| Tier | Steps |", "|---|---|"]
    for t, steps in CHANGE_WORKFLOWS.items():
        m.append(f"| {TIER_NAME[t]} (T{t}) | " + " → ".join(steps) + (" (every reviewer required)" if t == 4 else "") + " |")
    m += [
        "",
        "`Review` = the specialist reviewers of the topic's domain at that tier (table below; `scripts/topic.py find` prints them per topic), started **in parallel after Verify passes**, each at the Review cell (T1 Haiku·Low, T2 Sonnet·Low, T3 Sonnet·High, T4 Opus·High) with the Verify cap, unless `- Review: <Name> / T<tier> = model·effort` overrides it. A reviewer FAIL → a Work agent fixes it → re-run **that reviewer only**. 🟢/🟡: the generic verifier alone.",
        "",
        "**Review gate** (hook, at the receipt): a 🔴 change closed with a required reviewer missing is refused once, naming the missing reviewers; a 🟠 change gets a 🟠 guide item instead. A reviewer you skip on purpose is named in the receipt's `workflow` line — `Security skipped: <reason>` — and the gate lets it pass. `- Review gate = auto | on | warn | off` (auto = this; on = refuse on T3 too; warn = never refuse; off = silent).",
        "",
        "## Reviewers",
        "",
        "| Reviewer | Route tag | Agent | Checks | Tools |",
        "|---|---|---|---|---|",
    ]
    for k, (word, emoji, agent, checks, tools) in REVIEWERS.items():
        m.append(f"| {emoji} {word} | `{word}` | `abofles:{agent}` | {checks} | {tools} |")
    m += [
        "",
        "Route line of a reviewer: `<emoji> `<Tag>` → **<Topic>** · <Model> · <Effort> · Cap <N> · <Tier>` (the Review cell). Prompt header: `abofles: <Topic> / <Tag> · T<tier>`. Reply format: the verifier's (STATUS, CONFIDENCE, VERDICT, EVIDENCE, ISSUES, NOTE).",
        "",
        "## Work specialists",
        "",
        "| Specialist | Route tag | Agent | Owns | Tools |",
        "|---|---|---|---|---|",
    ]
    for k, (word, emoji, agent, owns, tools) in SPECIALISTS.items():
        m.append(f"| {emoji} {word} | `{word}` | `abofles:{agent}` | {owns} | {tools} |")
    m += [
        "",
        "A specialist follows the work up, so it takes the topic's **economy Work cell** at its tier in both matrices (`scripts/topic.py` prints it as `Git/Scrum …`). Route line: `<emoji> `<Tag>` → **<Topic>** · <Model> · <Effort> · Cap <N> · <Tier>`. Prompt header: `abofles: <Topic> / Git · T<tier>` or `/ Scrum · T<tier>` (`/ Work` with the specialist's agent type is the same cell). Reply formats: Git — STATUS, CONFIDENCE, ACTIONS, STATE, CONFLICTS, LEFT; Scrum — STATUS, CONFIDENCE, TRACKER, ACTIONS, DRAFTS, BOARD, LEFT. Neither is a reviewer: they never trip the review gate, and Fable is refused for them. A ticket read for another side (a Judge or a Verifier that needs the acceptance criteria) is a Scrum agent's job when the tracker has a connector; a 🟢 read of one ticket the main session may still fetch itself.",
        "",
        "## Reviewers per domain",
        "",
        "| Domain | 🟠 T3 | 🔴 T4 |",
        "|---|---|---|",
    ]
    for letter, group, _ in TOPICS:
        g = GROUP_REVIEWERS.get(letter, {})
        m.append(f"| `{letter}` {group} | {rev_names(g.get(3, []))} | {rev_names(g.get(4, []))} |")
    m += ["", "Topics that always add a reviewer (T3 and T4): " + "; ".join(f"**{t}** → {rev_names(v)}" for t, v in TOPIC_REVIEWERS.items()) + ".", "",
          "## Overrides (project `## abofles overrides` block)", "",
          "- `- Workflow: <Domain code | Domain name | Topic> = Read, Work, Verify` — the steps of a `change` on that domain or topic (advice to the skill; the hook does not enforce steps).",
          "- `- Reviewers: <Domain code | Domain name | Topic> = security, tester` — replaces the reviewers of that domain or topic at T3 and T4 (names as in the table, lower case; `none` = no reviewer).",
          "- `- Review: <Tag> / T<tier> = model·effort` — the cell of one reviewer at one tier.",
          "- `- Review gate = auto | on | warn | off`.", ""]
    return "\n".join(m)


def build_workflow_json():
    topics = {}
    for code, name, blurb, areas in PARSED:
        for area, rows in areas:
            for topic, tier, ov, sig, capov in rows:
                topics[topic] = {"group": code, "area": area, "tier": tier, "signals": sig,
                                 "reviewers": {str(t): reviewers_of(code, topic, t) for t in (3, 4)}}
                if ov:
                    topics[topic]["ov"] = ov
                if capov:
                    topics[topic]["caps"] = capov
    n = 0
    for _, _, rows in TOPICS:
        for topic, *_ in rows:
            n += 1
            topics[topic]["n"] = n
    for sug in SUGGESTIONS:
        for t in sug["topics"]:
            if t not in topics:
                raise ValueError(f"suggestion {sug['key']} names unknown topic {t!r}")
            topics[t].setdefault("suggest", []).append(sug["key"])
    return json.dumps({
        "suggestions": {s["key"]: {k: v for k, v in s.items() if k != "key"} for s in SUGGESTIONS},
        "reviewers": {k: {"word": v[0], "emoji": v[1], "agent": v[2]} for k, v in REVIEWERS.items()},
        "groups": {code: {"name": name, "blurb": DOMAIN_BLURB[code], "areas": [a for a, _ in DOMAIN_AREAS[code]],
                          "reviewers": {str(t): GROUP_REVIEWERS.get(code, {}).get(t, []) for t in (3, 4)}} for code, name, _ in TOPICS},
        "topics": topics,
        "tiers": {str(t): TIERS[t] for t in TIERS},
        "tiers_economy": {str(t): TIERS_ECONOMY[t] for t in TIERS_ECONOMY},
        "quality_sides": list(QUALITY_SIDES),
        "caps": {str(t): CAPS[t] for t in CAPS},
        "change": {str(t): v for t, v in CHANGE_WORKFLOWS.items()},
        "intents": {k: v[1] for k, v in INTENTS.items() if v[1]},
        "gate": {str(t): v for t, v in REVIEW_GATE.items()},
        "verify": {str(t): REVIEW_TIERS[t] for t in REVIEW_TIERS},
    }, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    outputs = ((TOPICS_MD, build_topics_md()), (INDEX_MD, build_topic_index()), (MATRIX, build_matrix()), (WORKFLOW_MD, build_workflow_md()), (WORKFLOW_JSON, build_workflow_json()))
    if "--check" in sys.argv[1:]:
        stale = [p.name for p, new in outputs if not p.exists() or p.read_text() != new]
        if stale:
            print("stale: " + ", ".join(stale) + " (run gen_matrix.py)")
            sys.exit(1)
        print("ok: up to date")
        sys.exit(0)
    for p, new in outputs:
        p.write_text(new)
    count = sum(len(r) for _, _, r in TOPICS)
    print(f"ok: {count} topics, {len(TOPICS)} domains, {len(REVIEWERS)} reviewers -> " + ", ".join(p.name for p, _ in outputs))
