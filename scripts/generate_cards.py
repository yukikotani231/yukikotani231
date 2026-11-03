#!/usr/bin/env python3
"""Generate profile stat cards (SVG) from the GitHub GraphQL API.

Usage:
  GITHUB_TOKEN=... python3 scripts/generate_cards.py --user yukikotani231 --out cards
  python3 scripts/generate_cards.py --input data.json --out cards   # render from saved data

Only the standard library is used, so no setup is needed in CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    login
    name
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        isPrivate
        stargazerCount
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------- data


def graphql(token: str, variables: dict) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        body = json.load(res)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"], indent=2))
    return body["data"]["user"]


def fetch(token: str, login: str) -> dict:
    user = graphql(token, {"login": login, "after": None})
    repos = list(user["repositories"]["nodes"])
    page = user["repositories"]["pageInfo"]
    while page["hasNextPage"]:
        more = graphql(token, {"login": login, "after": page["endCursor"]})
        repos += more["repositories"]["nodes"]
        page = more["repositories"]["pageInfo"]
    user["repositories"]["nodes"] = repos
    return user


def summarize(user: dict, top_langs: int) -> dict:
    cc = user["contributionsCollection"]
    repos = user["repositories"]["nodes"]

    langs: dict[str, dict] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = langs.setdefault(name, {"size": 0, "color": edge["node"]["color"] or "#8b949e"})
            entry["size"] += edge["size"]
    total = sum(v["size"] for v in langs.values()) or 1
    ranked = sorted(langs.items(), key=lambda kv: kv[1]["size"], reverse=True)
    top = [
        {"name": k, "color": v["color"], "pct": v["size"] / total * 100}
        for k, v in ranked[:top_langs]
    ]
    rest = sum(v["size"] for _, v in ranked[top_langs:])
    if rest:
        top.append({"name": "Other", "color": "#6e7681", "pct": rest / total * 100})

    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    days.sort(key=lambda d: d["date"])
    counts = [d["contributionCount"] for d in days]

    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    # Today may not have contributions yet; don't break the streak for it.
    tail = counts[:-1] if counts and counts[-1] == 0 else counts
    for c in reversed(tail):
        if not c:
            break
        current += 1

    weeks = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cc["contributionCalendar"]["weeks"]]

    return {
        "login": user["login"],
        "name": user["name"] or user["login"],
        "since": user["createdAt"][:4],
        "stars": sum(r["stargazerCount"] for r in repos),
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "commits": cc["totalCommitContributions"] + cc["restrictedContributionsCount"],
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "reviews": cc["totalPullRequestReviewContributions"],
        "contributed": cc["totalRepositoriesWithContributedCommits"],
        "contributions": cc["contributionCalendar"]["totalContributions"],
        "active_days": sum(1 for c in counts if c),
        "current_streak": current,
        "longest_streak": longest,
        "best_day": max(counts, default=0),
        "weeks": weeks,
        "first_week": days[0]["date"] if days else "",
        "languages": top,
    }


# ---------------------------------------------------------------- render

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "title": "#e6edf3", "text": "#c9d1d9",
        "muted": "#8b949e", "accent": "#58a6ff", "track": "#21262d",
    },
    "light": {
        "bg": "#ffffff", "border": "#d0d7de", "title": "#1f2328", "text": "#1f2328",
        "muted": "#656d76", "accent": "#0969da", "track": "#eaeef2",
    },
}

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"


def fmt(n: float) -> str:
    if n >= 10_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n:,}"


def frame(width: int, height: int, title: str, body: str, t: dict) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
<style>
  text {{ font-family: {FONT}; }}
  .title {{ font-size: 16px; font-weight: 600; fill: {t['title']}; }}
  .label {{ font-size: 13px; fill: {t['muted']}; }}
  .value {{ font-size: 13px; font-weight: 600; fill: {t['text']}; }}
  .big {{ font-size: 28px; font-weight: 700; fill: {t['accent']}; }}
  .small {{ font-size: 11px; fill: {t['muted']}; }}
  .fade {{ opacity: 0; animation: fade .5s ease-out forwards; }}
  @keyframes fade {{ to {{ opacity: 1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .fade {{ animation: none; opacity: 1; }} }}
</style>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="{t['bg']}" stroke="{t['border']}"/>
<text x="24" y="36" class="title">{escape(title)}</text>
{body}
</svg>
"""


def stats_card(s: dict, t: dict) -> str:
    rows = [
        ("Stars earned", s["stars"]),
        ("Commits (last year)", s["commits"]),
        ("Pull requests", s["prs"]),
        ("Issues", s["issues"]),
        ("Code reviews (last year)", s["reviews"]),
        ("Contributed to (last year)", s["contributed"]),
    ]
    body = []
    for i, (label, value) in enumerate(rows):
        y = 70 + i * 24
        body.append(
            f'<g class="fade" style="animation-delay:{i * 80}ms">'
            f'<text x="24" y="{y}" class="label">{label}</text>'
            f'<text x="268" y="{y}" class="value" text-anchor="end">{fmt(value)}</text></g>'
        )
    cx, cy, r = 380, 118, 52
    body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{t["track"]}" stroke-width="8"/>')
    body.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{t["accent"]}" stroke-width="8" '
        f'stroke-dasharray="{2 * 3.14159 * r * min(s["active_days"] / 365, 1):.1f} 999" '
        f'transform="rotate(-90 {cx} {cy})" stroke-linecap="round"/>'
    )
    body.append(f'<text x="{cx}" y="{cy + 6}" class="big" text-anchor="middle" style="font-size:24px">{fmt(s["contributions"])}</text>')
    body.append(f'<text x="{cx}" y="{cy + 24}" class="small" text-anchor="middle">contributions</text>')
    body.append(f'<text x="{cx}" y="{cy + r + 26}" class="small" text-anchor="middle">{s["active_days"]} active days / year</text>')
    return frame(495, 215, f"{s['name']}'s GitHub Stats", "\n".join(body), t)


def languages_card(s: dict, t: dict) -> str:
    langs = s["languages"]
    x, width = 24, 447
    bar = [f'<clipPath id="bar"><rect x="24" y="56" width="{width}" height="10" rx="5"/></clipPath><g clip-path="url(#bar)">']
    for lang in langs:
        w = width * lang["pct"] / 100
        bar.append(f'<rect x="{x:.2f}" y="56" width="{w + 0.5:.2f}" height="10" fill="{lang["color"]}"/>')
        x += w
    bar.append("</g>")
    items = []
    for i, lang in enumerate(langs):
        col, row = i % 2, i // 2
        lx, ly = 24 + col * 230, 96 + row * 26
        items.append(
            f'<g class="fade" style="animation-delay:{i * 60}ms">'
            f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{lang["color"]}"/>'
            f'<text x="{lx + 18}" y="{ly}" class="value">{escape(lang["name"])}</text>'
            f'<text x="{lx + 200}" y="{ly}" class="label" text-anchor="end">{lang["pct"]:.1f}%</text></g>'
        )
    rows = (len(langs) + 1) // 2
    return frame(495, max(215, 96 + rows * 26), "Most Used Languages", "\n".join(bar + items), t)


def activity_card(s: dict, t: dict) -> str:
    weeks = s["weeks"]
    peak = max(weeks, default=0) or 1
    left, top, w, h = 24, 60, 952, 110
    gap = 3
    bw = (w - gap * (len(weeks) - 1)) / max(len(weeks), 1)
    body = [f'<line x1="{left}" y1="{top + h + 0.5}" x2="{left + w}" y2="{top + h + 0.5}" stroke="{t["border"]}"/>']
    for i, n in enumerate(weeks):
        bh = max(h * n / peak, 2 if n else 0)
        body.append(
            f'<rect class="fade" style="animation-delay:{i * 12}ms" x="{left + i * (bw + gap):.2f}" '
            f'y="{top + h - bh:.2f}" width="{bw:.2f}" height="{bh:.2f}" rx="2" fill="{t["accent"]}" '
            f'fill-opacity="{0.35 + 0.65 * n / peak:.2f}"><title>{n} contributions</title></rect>'
        )
    if s["first_week"]:
        start = dt.date.fromisoformat(s["first_week"])
        for i in range(0, len(weeks), 1):
            d = start + dt.timedelta(weeks=i)
            if d.day <= 7:
                body.append(f'<text x="{left + i * (bw + gap):.2f}" y="{top + h + 18}" class="small">{d.strftime("%b")}</text>')
    stats = [
        ("Current streak", f"{s['current_streak']} days"),
        ("Longest streak", f"{s['longest_streak']} days"),
        ("Best day", f"{s['best_day']} contributions"),
        ("Busiest week", f"{max(weeks, default=0)} contributions"),
    ]
    for i, (label, value) in enumerate(stats):
        sx = left + i * 238
        body.append(f'<text x="{sx}" y="{top + h + 50}" class="label">{label}</text>')
        body.append(f'<text x="{sx}" y="{top + h + 70}" class="value" style="font-size:16px">{value}</text>')
    return frame(1000, 260, "Contributions per Week (last year)", "\n".join(body), t)


CARDS = {"stats": stats_card, "languages": languages_card, "activity": activity_card}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER"))
    p.add_argument("--out", default="cards")
    p.add_argument("--input", help="render from saved API JSON instead of calling the API")
    p.add_argument("--dump", help="save the raw API response to this path")
    p.add_argument("--top-langs", type=int, default=7)
    args = p.parse_args()

    if args.input:
        with open(args.input) as f:
            user = json.load(f)
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token or not args.user:
            print("GITHUB_TOKEN and --user are required", file=sys.stderr)
            return 1
        user = fetch(token, args.user)
        if args.dump:
            with open(args.dump, "w") as f:
                json.dump(user, f)

    summary = summarize(user, args.top_langs)
    os.makedirs(args.out, exist_ok=True)
    for name, render in CARDS.items():
        for theme, colors in THEMES.items():
            path = os.path.join(args.out, f"{name}-{theme}.svg")
            with open(path, "w") as f:
                f.write(render(summary, colors))
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
