#!/usr/bin/env python3
"""Generate a unified set of GitHub profile-stat cards as SVG.

Cards (each rendered for a light and a dark theme):
    * overview.svg   - repositories / stars / followers / contributions + sparkline
    * languages.svg  - most used languages (native GitHub colours)
    * streak.svg     - current / longest / total contribution streak

Data sources (no third-party services):
    * contributions : https://github.com/users/<user>/contributions  (public HTML)
    * user + repos  : https://api.github.com  (public REST; optional token raises limits)

The output is written to <out>/light and <out>/dark and is meant to be published
to a branch by the accompanying GitHub Actions workflow.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date

FONT = ('-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", '
        'Helvetica, Arial, sans-serif')

THEMES = {
    "dark": {
        "bg": "#161B22", "border": "#30363D", "title": "#F0F6FC",
        "label": "#8B949E", "value": "#F0F6FC", "accent": "#58A6FF",
    },
    "light": {
        "bg": "#FFFFFF", "border": "#D0D7DE", "title": "#1F2328",
        "label": "#59636E", "value": "#1F2328", "accent": "#0969DA",
    },
}

# GitHub linguist colours for the languages we are most likely to hit.
LANG_COLORS = {
    "Java": "#B07219", "Go": "#00ADD8", "Vue": "#41B883", "Python": "#3572A5",
    "Rust": "#DEA584", "JavaScript": "#F1E05A", "TypeScript": "#3178C6",
    "C": "#555555", "C++": "#F34B7D", "C#": "#178600", "Ruby": "#701516",
    "PHP": "#4F5D95", "Shell": "#89E051", "HTML": "#E34C26", "CSS": "#563D7C",
    "SCSS": "#C6538C", "Kotlin": "#A97BFF", "Swift": "#F05138", "Dart": "#00B4AB",
    "Scala": "#C22D40", "Haskell": "#5E5086", "Lua": "#000080", "Perl": "#0298C3",
    "Objective-C": "#438EFF", "Elixir": "#6E4A7E", "Erlang": "#B83998",
    "Clojure": "#DB5855", "R": "#198CE7", "Julia": "#A270BA", "Zig": "#EC915C",
    "Dockerfile": "#384D54", "Jupyter Notebook": "#DA5B0B", "Makefile": "#427819",
    "Vim Script": "#199F4B", "TeX": "#3D6117", "Astro": "#FF5A03", "Svelte": "#FF3E00",
}
LANG_FALLBACK = "#8B949E"


# --------------------------------------------------------------------------- data
def _request(url, token=None):
    headers = {"User-Agent": "jcbl1-profile-stats", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_user(user, token):
    return json.loads(_request(f"https://api.github.com/users/{user}", token))


def fetch_repos(user, token, limit=100):
    repos, page = [], 1
    while len(repos) < limit:
        batch = json.loads(_request(
            f"https://api.github.com/users/{user}/repos"
            f"?per_page=100&page={page}&type=owner&sort=pushed", token))
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos[:limit]


def fetch_languages(user, name, token):
    data = _request(f"https://api.github.com/repos/{user}/{name}/languages", token)
    return json.loads(data) if data.strip() else {}


def fetch_contributions(user):
    """Parse the public contribution calendar into {date: count}."""
    html = _request(f"https://github.com/users/{user}/contributions")
    ids = {}
    for td in re.findall(r"<td\b[^>]*>", html):
        m_id = re.search(r'id="(contribution-day-component-\d+-\d+)"', td)
        m_date = re.search(r'data-date="([\d-]+)"', td)
        if m_id and m_date:
            ids[m_id.group(1)] = m_date.group(1)
    out = {}
    for m in re.finditer(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>(.*?)</tool-tip>', html, re.S):
        day = ids.get(m.group(1))
        if not day:
            continue
        hit = re.match(r"\s*(\d+)\s+contribution", m.group(2))
        out[day] = int(hit.group(1)) if hit else 0
    for day in ids.values():
        out.setdefault(day, 0)
    if not out:
        raise RuntimeError("could not parse the contribution calendar")
    return out


def compute_metrics(user, token):
    account = fetch_user(user, token)
    repos = fetch_repos(user, token)

    stars = 0
    langs = {}
    for r in repos:
        # stars count every owned repo, forks included
        stars += r.get("stargazers_count", 0)
        # language mix should reflect your own code, so skip forks
        if r.get("fork"):
            continue
        try:
            for lang, n in fetch_languages(user, r["name"], token).items():
                langs[lang] = langs.get(lang, 0) + n
        except Exception as exc:  # noqa: BLE001 - a missing repo must not kill the run
            print(f"  ! languages for {r['name']}: {exc}", file=sys.stderr)

    top = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:5]
    total_bytes = sum(n for _, n in top) or 1
    languages = [(name, n / total_bytes * 100)
                 for name, n in top if n / total_bytes * 100 >= 0.05]

    contrib = fetch_contributions(user)
    days = sorted(contrib)
    total_contrib = sum(contrib.values())
    longest = current = run = 0
    for i, d in enumerate(days):
        if contrib[d] > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    for d in reversed(days):
        if contrib[d] > 0:
            current += 1
        elif current == 0:
            continue  # today may still be incomplete
        else:
            break

    months = {}
    for d in days:
        months[d[:7]] = months.get(d[:7], 0) + contrib[d]
    spark = [months[k] for k in sorted(months)][-12:]

    return {
        "overview": [
            ("Repositories", f"{account['public_repos']:,}"),
            ("Stars", f"{stars:,}"),
            ("Followers", f"{account['followers']:,}"),
            ("Contributions", f"{total_contrib:,}"),
        ],
        "spark": spark or [0],
        "languages": languages,
        "streak": [
            ("Current", f"{current}"),
            ("Longest", f"{longest}"),
            ("Total", f"{total_contrib:,}"),
        ],
    }


# --------------------------------------------------------------------------- svg
def svg_open(w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img">'
            f'<style>text{{font-family:{FONT}}}</style>')


def card_rect(w, h, t):
    return (f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" '
            f'fill="{t["bg"]}" stroke="{t["border"]}"/>')


def label(x, y, s, t):
    return (f'<text x="{x}" y="{y}" font-size="11" font-weight="600" '
            f'letter-spacing="2.2" fill="{t["label"]}">{s}</text>')


def spark_path(pts, x0, y0, w, h):
    n = len(pts)
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1
    step = w / (n - 1) if n > 1 else 0
    xy = [(x0 + i * step, y0 + h - (v - lo) / rng * h) for i, v in enumerate(pts)]
    d = f'M {xy[0][0]:.1f} {xy[0][1]:.1f}'
    for i in range(1, n):
        mx = (xy[i - 1][0] + xy[i][0]) / 2
        my = (xy[i - 1][1] + xy[i][1]) / 2
        d += f' Q {xy[i-1][0]:.1f} {xy[i-1][1]:.1f} {mx:.1f} {my:.1f}'
    d += f' T {xy[-1][0]:.1f} {xy[-1][1]:.1f}'
    area = d + f' L {xy[-1][0]:.1f} {y0 + h:.1f} L {xy[0][0]:.1f} {y0 + h:.1f} Z'
    return d, area, xy


def svg_overview(t, data):
    w, h = 870, 190
    s = [svg_open(w, h), card_rect(w, h, t), label(30, 42, "GITHUB OVERVIEW", t)]
    x = 30
    for k, v in data["overview"]:
        s.append(f'<text x="{x}" y="112" font-size="32" font-weight="700" '
                 f'fill="{t["value"]}">{v}</text>')
        s.append(f'<text x="{x}" y="136" font-size="12.5" fill="{t["label"]}">{k}</text>')
        x += 138
    sx, sy, sw, sh = 616, 66, 224, 78
    line, area, xy = spark_path(data["spark"], sx, sy, sw, sh)
    s.append(f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{t["accent"]}" stop-opacity="0.35"/>'
             f'<stop offset="1" stop-color="{t["accent"]}" stop-opacity="0"/></linearGradient></defs>')
    s.append(f'<path d="{area}" fill="url(#g)"/>')
    s.append(f'<path d="{line}" fill="none" stroke="{t["accent"]}" stroke-width="2.5" '
             f'stroke-linecap="round" stroke-linejoin="round"/>')
    cx, cy = xy[-1]
    s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{t["accent"]}"/>')
    s.append(f'<text x="{sx + sw}" y="164" text-anchor="end" font-size="12" '
             f'fill="{t["label"]}">contributions · last 12 months</text>')
    s.append('</svg>')
    return "".join(s)


def languages_body(t, data):
    w, h = 420, 190
    s = [card_rect(w, h, t), label(26, 38, "MOST USED LANGUAGES", t)]
    langs = data["languages"]
    total = sum(p for _, p in langs) or 1
    bx, by, bw, bh = 26, 56, w - 52, 10
    s.append(f'<defs><clipPath id="bar"><rect x="{bx}" y="{by}" width="{bw}" '
             f'height="{bh}" rx="5"/></clipPath></defs><g clip-path="url(#bar)">')
    x = bx
    for name, p in langs:
        seg = bw * p / total
        s.append(f'<rect x="{x:.1f}" y="{by}" width="{seg:.1f}" height="{bh}" '
                 f'fill="{LANG_COLORS.get(name, LANG_FALLBACK)}"/>')
        x += seg
    s.append('</g>')
    col_w = (w - 52) / 2
    for i, (name, p) in enumerate(langs):
        ox = 26 + (i % 2) * col_w
        oy = 104 + (i // 2) * 26
        col = LANG_COLORS.get(name, LANG_FALLBACK)
        s.append(f'<circle cx="{ox + 5}" cy="{oy - 4}" r="5" fill="{col}"/>')
        s.append(f'<text x="{ox + 18}" y="{oy}" font-size="13" fill="{t["value"]}">{name}</text>')
        s.append(f'<text x="{ox + col_w - 14}" y="{oy}" text-anchor="end" font-size="12.5" '
                 f'fill="{t["label"]}">{p:.1f}%</text>')
    return "".join(s)


def streak_body(t, data):
    w, h = 420, 190
    s = [card_rect(w, h, t), label(26, 38, "CONTRIBUTION STREAK", t)]
    thirds = [26 + (w - 52) / 6, 26 + (w - 52) / 2, 26 + (w - 52) * 5 / 6]
    for c in (1, 2):
        xx = 26 + (w - 52) * c / 3
        s.append(f'<line x1="{xx:.1f}" y1="86" x2="{xx:.1f}" y2="132" '
                 f'stroke="{t["border"]}"/>')
    for i, (k, v) in enumerate(data["streak"]):
        col = t["accent"] if i == 0 else t["value"]
        s.append(f'<text x="{thirds[i]:.1f}" y="118" text-anchor="middle" font-size="34" '
                 f'font-weight="700" fill="{col}">{v}</text>')
        s.append(f'<text x="{thirds[i]:.1f}" y="142" text-anchor="middle" font-size="12.5" '
                 f'fill="{t["label"]}">{k}</text>')
    return "".join(s)


def svg_breakdown(t, data):
    """Languages + streak on one 870-wide canvas so it aligns exactly with the
    overview card. Two separately sized images can never line up in a README
    because percentage widths cannot absorb the whitespace between them."""
    w, h, gap = 870, 190, 30
    right = 420 + gap
    return (svg_open(w, h)
            + f'<g transform="translate(0,0)">{languages_body(t, data)}</g>'
            + f'<g transform="translate({right},0)">{streak_body(t, data)}</g>'
            + '</svg>')


# --------------------------------------------------------------------------- run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "jcbl1"))
    ap.add_argument("--out", default="out")
    ap.add_argument("--data", help="render from a metrics JSON file instead of the API")
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    if args.data:
        with open(args.data) as f:
            data = json.load(f)
        print(f"loaded metrics from {args.data}")
    else:
        print(f"collecting metrics for {args.user} ...")
        data = compute_metrics(args.user, token)
    print(f"  repos={data['overview'][0][1]} stars={data['overview'][1][1]} "
          f"followers={data['overview'][2][1]} contributions={data['overview'][3][1]}")
    print(f"  languages={[n for n, _ in data['languages']]}")
    print(f"  spark={data['spark']}")
    print(f"  streak={data['streak']}")

    builders = {"overview": svg_overview, "breakdown": svg_breakdown}
    for theme, t in THEMES.items():
        d = os.path.join(args.out, theme)
        os.makedirs(d, exist_ok=True)
        for name, fn in builders.items():
            with open(os.path.join(d, f"{name}.svg"), "w") as f:
                f.write(fn(t, data))
    print(f"done -> {args.out}/{{light,dark}}")


if __name__ == "__main__":
    main()
