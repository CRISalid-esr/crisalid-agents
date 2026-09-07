"""Generate the PTM and TRM workflow diagrams (SVG, 16:9, white background, for slides)."""
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1920, 1080
FONT = "DejaVu Sans, Liberation Sans, Arial, sans-serif"
C = {
    "ink": "#1f2430", "muted": "#5b6270", "lane": "#f3f4f6", "laneline": "#d9dce3",
    "ui": "#f6a15b", "ui_fill": "#fff1e6",            # interface / people
    "llm": "#7c5cbf", "llm_fill": "#efe9f9",          # LLM prompted steps
    "code": "#4b5563", "code_fill": "#ffffff",        # plain code steps
    "data": "#1f8a8a", "data_fill": "#e3f4f4",        # stores and services
    "out": "#2f7d32", "out_fill": "#e8f5e9",          # deliverables
}


class SVG:
    def __init__(self, title):
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{escape(title)}" font-family="{FONT}">',
            '<defs>',
            f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["ink"]}"/></marker>',
            f'<marker id="arrow-llm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["llm"]}"/></marker>',
            f'<marker id="arrow-data" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["data"]}"/></marker>',
            '</defs>',
            f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        ]

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, lines, size=22, color=None, anchor="start", weight="normal", lh=1.3, mono=False):
        color = color or C["ink"]
        fam = "DejaVu Sans Mono, Liberation Mono, monospace" if mono else FONT
        out = [f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}" font-family="{fam}">']
        for i, line in enumerate(lines if isinstance(lines, list) else [lines]):
            dy = 0 if i == 0 else size * lh
            out.append(f'<tspan x="{x}" dy="{dy}">{escape(line)}</tspan>')
        out.append("</text>")
        self.add("".join(out))

    def box(self, x, y, w, h, kind, title, lines=(), tag=None, size=21, title_size=23, r=10, num=None):
        stroke, fill = C[kind], C[f"{kind}_fill"]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="3"/>')
        ty = y + 36
        tx = x + 18
        if num is not None:
            self.add(f'<circle cx="{x + 32}" cy="{y + 28}" r="16" fill="{stroke}"/>')
            self.text(x + 32, y + 35, str(num), size=19, color="#ffffff", anchor="middle", weight="bold")
            tx = x + 58
        if tag:
            self.add(f'<rect x="{x + w - len(tag) * 10 - 40}" y="{y - 14}" width="{len(tag) * 10 + 26}" height="28" rx="14" fill="{stroke}"/>')
            self.text(x + w - len(tag) * 10 - 27, y + 6, tag, size=16, color="#ffffff", weight="bold")
        self.text(tx, ty, title, size=title_size, weight="bold", color=stroke if kind != "code" else C["ink"])
        if lines:
            self.text(x + 18, ty + title_size * 1.4, list(lines), size=size, color=C["ink"])

    def store(self, x, y, w, h, title, lines=(), size=21):
        # a data store: rounded box with a top band
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{C["data_fill"]}" stroke="{C["data"]}" stroke-width="3"/>')
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="14" rx="7" fill="{C["data"]}"/>')
        self.text(x + 18, y + 44, title, size=23, weight="bold", color=C["data"])
        if lines:
            self.text(x + 18, y + 44 + 32, list(lines), size=size)

    def arrow(self, points, label=None, kind="ink", dashed=False, label_dx=0, label_dy=-10, size=19, anchor="middle", both=False, label_at=None):
        color = C[kind]
        marker = {"ink": "arrow", "llm": "arrow-llm", "data": "arrow-data"}[kind]
        pts = " ".join(f"{px},{py}" for px, py in points)
        dash = ' stroke-dasharray="10 8"' if dashed else ""
        start = f' marker-start="url(#{marker})"' if both else ""
        self.add(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3"{dash} marker-end="url(#{marker})"{start}/>')
        if label:
            mx = sum(p[0] for p in points) / len(points) + label_dx
            my = sum(p[1] for p in points) / len(points) + label_dy
            if label_at:
                mx, my = label_at
            self.text(mx, my, label if isinstance(label, list) else [label], size=size, color=color, anchor=anchor)

    def lane(self, y, h, label, sub=()):
        self.add(f'<rect x="0" y="{y}" width="{W}" height="{h}" fill="{C["lane"]}"/>')
        self.add(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="{C["laneline"]}" stroke-width="2"/>')
        self.text(24, y + 34, label, size=22, weight="bold", color=C["muted"])
        if sub:
            self.text(24, y + 60, list(sub), size=17, color=C["muted"], lh=1.25)

    def legend(self, x, y):
        items = [("ui", "people"), ("llm", "LLM step (prompt)"), ("code", "code step"), ("data", "index / service"), ("out", "deliverable")]
        for i, (kind, label) in enumerate(items):
            bx = x + i * 195
            self.add(f'<rect x="{bx}" y="{y}" width="30" height="20" rx="5" fill="{C[kind + "_fill"]}" stroke="{C[kind]}" stroke-width="3"/>')
            self.text(bx + 40, y + 17, label, size=18, color=C["muted"])

    def save(self, path):
        self.add("</svg>")
        Path(path).write_text("\n".join(self.parts), encoding="utf-8")



def header(s, title, subtitle):
    s.text(24, 52, title, size=38, weight="bold")
    s.text(24, 88, subtitle, size=22, color=C["muted"])


# lanes: interface / pipeline / data / LLM — data arrows join adjacent lanes, prompts sit at the bottom
LANES = [(110, 150), (260, 290), (550, 260), (810, 260)]
IY, PY, DY, LY = 128, 300, 590, 850          # box tops
PH, DH, LH = 220, 190, 140                   # box heights

# ============================================================================ PTM
s = SVG("PTM workflow: a researcher asks the Horizon topic finder in the chat; the agent expands the question into English queries, searches the OpenSearch topic index once per query and answers with a shortlist")
header(s, "PTM — Project-to-Topic Match: the Horizon topic finder chat agent",
       "Synchronous: one question, one answer in the chat, about 10 to 20 s per turn")
s.legend(930, 66)
for (y, h), (label, sub) in zip(LANES, [("Interface", ["OpenWebUI", "(or the SoVisu+ chat)"]), ("Agent", ["project_topic_matching", "LangGraph ReAct loop"]),
                                        ("Data & services", ["built beforehand,", "shared with TRM"]), ("LLM", ["chat model +", "system prompt"])]):
    s.lane(y, h, label, sub)

s.box(300, IY, 360, 112, "ui", "Researcher", ["types a project idea: title or abstract,", "in French or English"], size=19)
s.box(1190, IY, 420, 112, "out", "Answer in the chat", ["≤ 5 topics: id, title, call, deadline, EU", "contribution, one sentence on the fit"], size=19)
s.box(1650, IY, 250, 112, "ui", "Follow-up", ["“budget? eligibility?”"], size=19)

s.box(300, PY, 360, PH, "llm", "Query expansion", ["3 to 5 English queries, 5–15 words,", "phrased like the work programme:", "challenge, expected outcome,", "research content"], tag="prompted", size=19, num=1)
s.box(760, PY, 360, PH, "code", "One tool call per query", ["search_horizon_topics(query,", "  cluster?, type_of_action?)", "issued in parallel, shown as", "tool blocks in the chat"], size=19, num=2)
s.box(1190, PY, 420, PH, "llm", "Merge and present", ["topics hit by several queries first,", "why it fits = best-matching passage,", "says when the match is only", "thematic; never invents ids,", "dates or amounts"], tag="prompted", size=19, num=3)
s.box(1650, PY, 250, PH, "code", "get_horizon_topic", ["full expected outcome,", "scope, conditions,", "budget, deadlines"], size=19)

s.box(760, DY, 300, DH, "data", "Embedding service", ["bge-m3, 1024 dims", "query → vector", "(the same model", "embedded the index)"], size=19)
s.store(1190, DY, 710, DH, "OpenSearch topic index",
        ["409 topics of the 2026-27 programme, one document per topic + one vector per section",
         "hybrid query: BM25 + topic vector + passage vectors, fused by reciprocal rank fusion",
         "built beforehand by the ingestion script from the work programme PDFs"], size=17)

s.box(300, LY, 1600, LH, "llm", "System prompt, used by steps 1 and 3 — the whole behaviour lives here",
      ["write the queries → call the search tool for each, without announcing them → merge → answer in the user's language,",
       "shortlist only, no invented rules or links → for any follow-up on a topic, call get_horizon_topic first and quote its text"], size=19)

s.arrow([(480, 240), (480, PY)], label="message", label_dx=60, label_dy=8)
s.arrow([(660, 410), (760, 410)], label="queries", label_dy=-12)
s.arrow([(940, PY + PH), (940, DY)], label="query text", kind="data", label_dx=75, label_dy=8)
s.arrow([(1060, 700), (1190, 700)], label=["vector +", "keywords"], kind="data", label_dy=-36, size=17)
s.arrow([(1400, DY), (1400, PY + PH)], label=["top-10 topics", "+ best passage"], kind="data", label_dx=95, label_dy=-6)
s.arrow([(1400, PY), (1400, 240)], label="shortlist", label_dx=65, label_dy=8)
s.arrow([(1775, 240), (1775, PY)], label=["question /", "answer"], dashed=True, both=True, label_dx=70, label_dy=6, size=17)
s.arrow([(1610, 410), (1650, 410)], dashed=True)
s.arrow([(1775, PY + PH), (1775, DY)], label="by id", kind="data", label_dx=45, label_dy=8, both=True)
s.save("specs/topic-matching/diagrams/ptm-workflow.svg")

# ============================================================================ TRM
s = SVG("TRM workflow: an administrator launches the batch; for every topic of a cluster the pipeline expands the topic into queries, retrieves the publications of the laboratories' members from the knowledge graph, verifies each candidate with the LLM, scores and writes JSON, Markdown and PDF reports")
header(s, "TRM — Topic-to-Researcher Match: the batch pipeline",
       "Asynchronous: every topic of a cluster, unattended, about 25 s per topic, reports as files")
s.legend(930, 66)
for (y, h), (label, sub) in zip(LANES, [("Interface", ["a command run by", "an administrator"]), ("Pipeline", ["trm/ — one LangGraph run", "per topic, repeated for the", "50 to 110 topics of the cluster"]),
                                        ("Data & services", ["topic index + knowledge", "graph via the MCP toolbox"]), ("LLM", ["TRM_MODEL, single-shot", "JSON prompts, no tools"])]):
    s.lane(y, h, label, sub)

s.box(300, IY, 560, 112, "ui", "Administrator", ["docker compose exec crisalid-agents-chat-api \\", "  python scripts/topic_researcher_match.py --cluster CL2"], size=18)
s.box(1140, IY, 760, 112, "out", "Reports per cluster: trm-CL2-<date>.json / .md / .pdf",
      ["per topic: ranked researchers, unit, verdict, justification, relevant papers,", "pair id topic|person for the feedback loop — collected on the server"], size=18)

s.box(300, PY, 340, PH, "llm", "Expand", ["JSON: summary + queries", "· topic (title + scope)", "· summary", "· 3–5 variations", "· researcher terms EN and FR", "≤ 10 queries, publication-like"], tag="prompted", size=18, num=1)
s.box(720, PY, 340, PH, "code", "Retrieve, rank", ["per query: 100 publications of", "members of our laboratories", "paper score = RRF over the", "queries × best cosine", "candidate = top-5 papers summed", "→ 25 candidates"], size=18, num=2)
s.box(1140, PY, 340, PH, "llm", "Verify", ["one call per candidate (25):", "each paper relevant? + reason,", "verdict strong / plausible /", "weak / none; kept if ≥ 1", "relevant paper and", "verdict ≠ none"], tag="prompted ×25", size=18, num=3)
s.box(1560, PY, 340, PH, "code", "Score, report", ["Σ relevant papers × recency", "(1.0 ≤ 5 y, 0.6 ≤ 10 y, else 0.3)", "normalised per topic (best = 1)", "≥ 0.35, ≤ 15 researchers,", "research unit(s) added,", "pair id = topic|person"], size=18, num=4)

s.store(300, DY, 340, DH, "OpenSearch topic index", ["the topics of the cluster:", "title, expected outcome, scope,", "call, deadlines"], size=18)
s.box(720, DY, 340, DH, "data", "Embedding service", ["bge-m3, 1024 dims", "10 queries → 10 vectors", "(one batch call)"], size=18)
s.box(1140, DY, 340, DH, "data", "MCP toolbox", ["publications-by-theme", "  (internal_only = true)", "get-person-memberships", "— the same tools the chat", "agents use"], size=18)
s.store(1560, DY, 340, DH, "Knowledge graph (Neo4j)", ["publications: title / abstract", "vectors, dates, contributors;", "external = false marks members,", "MEMBER_OF → research units"], size=17)

s.box(300, LY, 760, LH, "llm", "Expansion prompt (step 1)", ["“read the topic, write queries that read like the titles of the", "publications a relevant researcher would have written — no policy", "wording; add the terms a researcher would use, in English and French”"], size=18)
s.box(1140, LY, 760, LH, "llm", "Verification prompt (step 3, one call per candidate)", ["“judge only from the listed publications (found by semantic search,", "some unrelated); be strict: relevant only if the subject overlaps the", "research content; give a verdict and a two-sentence justification”"], size=18)

s.arrow([(580, 240), (580, PY)], label="--cluster, --topic, --min-score…", label_dx=150, label_dy=8, size=17)
s.arrow([(640, 410), (720, 410)], label="queries", label_dy=-12, size=16)
s.arrow([(1060, 410), (1140, 410)], label="candidates", label_dy=-12, size=15)
s.arrow([(1480, 410), (1560, 410)], label="verdicts", label_dy=-12, size=16)
s.arrow([(1730, PY), (1730, 240)], label="JSON → MD → PDF", label_dx=95, label_dy=8, size=17)
s.arrow([(470, DY), (470, PY + PH)], label="topic text", kind="data", label_dx=60, label_dy=8, size=17)
s.arrow([(800, PY + PH), (800, DY)], label="queries", kind="data", label_dx=50, label_dy=8, size=17)
s.arrow([(1060, 700), (1140, 700)], label="vectors", kind="data", label_dy=-12, size=16)
s.arrow([(1230, DY), (1230, 552), (1000, 552), (1000, PY + PH)], label=["publications", "+ contributors"], kind="data", size=16, label_at=(1250, 552), anchor="start")
s.arrow([(1640, PY + PH), (1640, 568), (1420, 568), (1420, DY)], label="units", kind="data", size=16, label_at=(1660, 560), anchor="start")
s.arrow([(1480, 720), (1560, 720)], label="Cypher", kind="data", label_dy=-12, size=16, both=True)
s.save("specs/topic-matching/diagrams/trm-workflow.svg")
print("svg written")
