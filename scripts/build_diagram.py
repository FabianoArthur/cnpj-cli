"""Generate the animated architecture diagrams in docs/assets/.

    python scripts/build_diagram.py

Writes how-it-works-{light,dark}.svg and how-it-works.pt-BR-{light,dark}.svg.
Pure SVG + CSS/SMIL: no JavaScript, no external fonts; the moving dots are
hidden when the reader prefers reduced motion.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets"
W, H = 880, 572
DUR = 9.0

THEMES = {
    "light": {
        "bg": "#f6f8fa",
        "bgs": "#d0d7de",
        "card": "#ffffff",
        "cards": "#d0d7de",
        "text": "#1f2328",
        "muted": "#59636e",
        "edge": "#818b98",
        "accent": "#c2410c",
        "zone": "#eef0fe",
        "zones": "#c3c8fa",
        "zonet": "#3b45b5",
        "disk": "#eef1f4",
        "code": "#0550ae",
    },
    "dark": {
        "bg": "#0d1117",
        "bgs": "#30363d",
        "card": "#161b22",
        "cards": "#30363d",
        "text": "#e6edf3",
        "muted": "#9198a1",
        "edge": "#6e7681",
        "accent": "#f0883e",
        "zone": "#161b2e",
        "zones": "#2f3a6b",
        "zonet": "#a5b4fc",
        "disk": "#1c2128",
        "code": "#79c0ff",
    },
}

TEXT = {
    "en": {
        "lang": "en",
        "title": "How cnpj-etl works",
        "desc": (
            "cnpj download fetches each monthly snapshot from the Receita Federal open data "
            "share, with retries, backoff, resumable ranges and at most four parallel "
            "downloads, into one folder per month. The archive is extracted safely: tar.gz or "
            "zip, inner zips, latin-1 CSV files. cnpj sqlite loads one month into a SQLite "
            "file that cnpj lookup queries, printing JSON, CSV or a table. cnpj postgres "
            "replaces a PostgreSQL snapshot in one transaction, and cnpj bulk-load appends "
            "every month with a competencia column, one transaction per month."
        ),
        "src": ("Receita Federal", "open data share", "one archive per month"),
        "dl": ("cnpj download", "retry · backoff · resume", "≤ 4 in parallel"),
        "disk": ("data/YYYY-MM/", "dados.tar.gz", "atomic rename, resumable"),
        "ex": ("safe extraction", "tar.gz or zip → inner zips", "→ latin-1 CSV, no traversal"),
        "sq": ("cnpj sqlite", "one month", "→ cnpj_YYYY_MM.db"),
        "pg": ("cnpj postgres", "one month, replaces", "the snapshot atomically"),
        "bl": ("cnpj bulk-load", "every month, with history", "one transaction per month"),
        "lk": ("cnpj lookup", "JSON · CSV · table"),
        "db": ("PostgreSQL", "schema cnpj · COPY FROM STDIN · indexes · ANALYZE"),
        "https": "HTTPS",
    },
    "pt-BR": {
        "lang": "pt-BR",
        "title": "Como o cnpj-etl funciona",
        "desc": (
            "O cnpj download baixa cada competência mensal do compartilhamento de dados "
            "abertos da Receita Federal, com novas tentativas, backoff, retomada por faixa e "
            "no máximo quatro downloads em paralelo, numa pasta por mês. O arquivo é extraído "
            "com segurança: tar.gz ou zip, zips internos, CSV em latin-1. O cnpj sqlite "
            "carrega um mês num arquivo SQLite consultado pelo cnpj lookup, que imprime JSON, "
            "CSV ou tabela. O cnpj postgres troca um snapshot no PostgreSQL numa transação, e o "
            "cnpj bulk-load acumula todos os meses com a coluna competencia, uma transação "
            "por mês."
        ),
        "src": ("Receita Federal", "dados abertos", "um arquivo por mês"),
        "dl": ("cnpj download", "retry · backoff · retomada", "≤ 4 em paralelo"),
        "disk": ("data/AAAA-MM/", "dados.tar.gz", "rename atômico, retomável"),
        "ex": ("extração segura", "tar.gz ou zip → zips internos", "→ CSV latin-1, sem traversal"),
        "sq": ("cnpj sqlite", "um mês", "→ cnpj_AAAA_MM.db"),
        "pg": ("cnpj postgres", "um mês, troca o", "snapshot atomicamente"),
        "bl": ("cnpj bulk-load", "todos os meses, com histórico", "uma transação por mês"),
        "lk": ("cnpj lookup", "JSON · CSV · tabela"),
        "db": ("PostgreSQL", "schema cnpj · COPY FROM STDIN · índices · ANALYZE"),
        "https": "HTTPS",
    },
}

# (path, start, end) as fractions of DUR: the order data really flows in.
EDGES = [
    ("M244,92 H310", 0.02, 0.12),
    ("M550,92 H616", 0.14, 0.24),
    ("M736,138 V170 H440 V202", 0.26, 0.40),
    ("M400,282 V312 H149 V346", 0.42, 0.56),
    ("M440,282 V346", 0.42, 0.56),
    ("M480,282 V312 H731 V346", 0.42, 0.56),
    ("M149,436 V470", 0.60, 0.70),
    ("M440,436 V470", 0.60, 0.70),
    ("M731,436 V470", 0.60, 0.70),
]


def css(t: dict) -> str:
    sans = (
        'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
    )
    mono = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'
    return f"""text{{font-family:{sans};fill:{t["text"]}}}
.b{{font-size:14px;font-weight:600}}
.h{{font-size:13px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}}
.m{{font-size:12.5px;fill:{t["muted"]}}}
.code{{font-family:{mono};font-size:13.5px;font-weight:600;fill:{t["code"]}}}
.bg{{fill:{t["bg"]};stroke:{t["bgs"]}}}
.card{{fill:{t["card"]};stroke:{t["cards"]}}}
.src{{fill:{t["zone"]};stroke:{t["zones"]}}}
.srct{{fill:{t["zonet"]}}}
.disk{{fill:{t["disk"]};stroke:{t["cards"]};stroke-dasharray:5 4}}
.e{{fill:none;stroke:{t["edge"]};stroke-width:1.5;stroke-linejoin:round}}
.dot{{fill:{t["accent"]}}}
.halo{{fill:{t["accent"]};opacity:.25}}
.hl{{fill:none;stroke:{t["accent"]};stroke-width:2;stroke-linejoin:round}}
@media (prefers-reduced-motion:reduce){{.pk,.hl{{display:none}}}}"""


def box(x, y, w, h, lines, cls="card", title_cls="code", rx=10) -> str:
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" class="{cls}"/>']
    cx = x + w / 2
    n = len(lines)
    top = y + h / 2 - (n - 1) * 10 + 5
    for i, line in enumerate(lines):
        c = title_cls if i == 0 else "m"
        out.append(
            f'<text x="{cx:.1f}" y="{top + i * 20:.1f}" class="{c}" '
            f'text-anchor="middle">{escape(line)}</text>'
        )
    return "\n".join(out)


def packet(path: str, start: float, end: float) -> str:
    k = f"0;{start:.4f};{end:.4f};1"
    fade = f"0;{max(0, start - 0.015):.4f};{start:.4f};{end:.4f};{min(1, end + 0.02):.4f};1"
    hl = f"0;{max(0, start - 0.02):.4f};{start:.4f};{end:.4f};{min(1, end + 0.07):.4f};1"
    return (
        f'<path d="{path}" class="hl" opacity="0"><animate attributeName="opacity" '
        f'dur="{DUR}s" repeatCount="indefinite" values="0;0;.9;.9;0;0" keyTimes="{hl}"/></path>'
        f'<g class="pk" opacity="0"><circle r="9" class="halo"/><circle r="4.5" class="dot"/>'
        f'<animateMotion dur="{DUR}s" repeatCount="indefinite" path="{path}" '
        f'keyPoints="0;0;1;1" keyTimes="{k}" calcMode="spline" '
        f'keySplines="0 0 1 1;.45 0 .55 1;0 0 1 1"/>'
        f'<animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" '
        f'values="0;0;1;1;0;0" keyTimes="{fade}"/></g>'
    )


def render(theme: str, lang: str) -> str:
    t, s = THEMES[theme], TEXT[lang]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
        f'height="{H}" role="img" aria-labelledby="title desc" lang="{s["lang"]}">',
        f'<title id="title">{escape(s["title"])}</title>',
        f'<desc id="desc">{escape(s["desc"])}</desc>',
        f"<style>\n{css(t)}\n</style>",
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" '
        f'fill="{t["edge"]}"/></marker></defs>',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="16" class="bg"/>',
        box(24, 46, 220, 92, s["src"], cls="src", title_cls="b srct"),
        f'<text x="277" y="84" class="m" text-anchor="middle">{s["https"]}</text>',
        box(310, 46, 240, 92, s["dl"]),
        box(616, 46, 240, 92, s["disk"], cls="disk"),
        box(300, 202, 280, 80, s["ex"], title_cls="b"),
        box(24, 346, 250, 90, s["sq"]),
        box(315, 346, 250, 90, s["pg"]),
        box(606, 346, 250, 90, s["bl"]),
        box(24, 470, 250, 62, s["lk"]),
        box(315, 470, 541, 62, s["db"], title_cls="b"),
    ]
    for path, _, _ in EDGES:
        parts.append(f'<path d="{path}" class="e" marker-end="url(#ah)"/>')
    for edge in EDGES:
        parts.append(packet(*edge))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in TEXT:
        suffix = "" if lang == "en" else f".{lang}"
        for theme in THEMES:
            (OUT / f"how-it-works{suffix}-{theme}.svg").write_text(render(theme, lang))


if __name__ == "__main__":
    main()
