#!/usr/bin/env python3
"""
MLB Strikeout Prop Analyzer
Fetches team K stats, batting avg, batted balls, pitcher K/9, and prop lines.
Generates an HTML dashboard and sends a daily email.
"""

import requests
import json
import os
import sys
from datetime import datetime, date
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "2f99184c4e06351cd8bbdc630f9574f3")
EMAIL_TO     = os.environ.get("EMAIL_TO", "")          # set in GitHub secrets
EMAIL_FROM   = os.environ.get("EMAIL_FROM", "")        # set in GitHub secrets
SENDGRID_KEY = os.environ.get("SENDGRID_API_KEY", "")  # set in GitHub secrets

TODAY = date.today().strftime("%Y-%m-%d")
SEASON = 2026

# Team name normalizations (MLB API → common name)
TEAM_NORM = {
    "Arizona Diamondbacks": "D-backs", "Atlanta Braves": "Braves",
    "Baltimore Orioles": "Orioles", "Boston Red Sox": "Red Sox",
    "Chicago Cubs": "Cubs", "Chicago White Sox": "White Sox",
    "Cincinnati Reds": "Reds", "Cleveland Guardians": "Guardians",
    "Colorado Rockies": "Rockies", "Detroit Tigers": "Tigers",
    "Houston Astros": "Astros", "Kansas City Royals": "Royals",
    "Los Angeles Angels": "Angels", "Los Angeles Dodgers": "Dodgers",
    "Miami Marlins": "Marlins", "Milwaukee Brewers": "Brewers",
    "Minnesota Twins": "Twins", "New York Mets": "Mets",
    "New York Yankees": "Yankees", "Oakland Athletics": "Athletics",
    "Philadelphia Phillies": "Phillies", "Pittsburgh Pirates": "Pirates",
    "San Diego Padres": "Padres", "San Francisco Giants": "Giants",
    "Seattle Mariners": "Mariners", "St. Louis Cardinals": "Cardinals",
    "Tampa Bay Rays": "Rays", "Texas Rangers": "Rangers",
    "Toronto Blue Jays": "Blue Jays", "Washington Nationals": "Nationals",
}

def norm(name):
    return TEAM_NORM.get(name, name)

# ── MLB Stats API helpers ───────────────────────────────────────────────────
MLB_BASE = "https://statsapi.mlb.com/api/v1"

def get_team_hitting_stats():
    """Returns dict of teamName -> {avg, strikeouts, batted_balls_proxy}"""
    url = f"{MLB_BASE}/teams/stats"
    params = {
        "season": SEASON,
        "sportId": 1,
        "group": "hitting",
        "stats": "season",
        "gameType": "R",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    stats = {}
    for entry in data.get("stats", [{}])[0].get("splits", []):
        team_name = norm(entry["team"]["name"])
        s = entry["stat"]
        stats[team_name] = {
            "avg": float(s.get("avg", 0) or 0),
            "strikeouts": int(s.get("strikeOuts", 0) or 0),
            # MLB API doesn't expose batted balls directly; use plate appearances minus Ks/BB as proxy
            "pa": int(s.get("plateAppearances", 0) or 0),
            "bb": int(s.get("baseOnBalls", 0) or 0),
            "so": int(s.get("strikeOuts", 0) or 0),
        }
        # batted balls ≈ PA - K - BB - HBP - SF - SH
        hbp = int(s.get("hitByPitch", 0) or 0)
        sf  = int(s.get("sacFlies", 0) or 0)
        sh  = int(s.get("sacBunts", 0) or 0)
        bbe = max(0, stats[team_name]["pa"] - stats[team_name]["so"] - stats[team_name]["bb"] - hbp - sf - sh)
        stats[team_name]["batted_balls"] = bbe
    return stats

def get_team_pitching_stats():
    """Returns dict of teamName -> k9"""
    url = f"{MLB_BASE}/teams/stats"
    params = {
        "season": SEASON,
        "sportId": 1,
        "group": "pitching",
        "stats": "season",
        "gameType": "R",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    stats = {}
    for entry in data.get("stats", [{}])[0].get("splits", []):
        team_name = norm(entry["team"]["name"])
        s = entry["stat"]
        stats[team_name] = {
            "k9": float(s.get("strikeoutsPer9Inn", 0) or 0),
        }
    return stats

def get_todays_games():
    """Returns list of {game_id, home_team, away_team, home_pitcher, away_pitcher}"""
    url = f"{MLB_BASE}/schedule"
    params = {
        "sportId": 1,
        "date": TODAY,
        "hydrate": "probablePitcher,team",
        "gameType": "R",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            home = norm(game["teams"]["home"]["team"]["name"])
            away = norm(game["teams"]["away"]["team"]["name"])
            home_pitcher = game["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
            away_pitcher = game["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_pitcher_id = game["teams"]["home"].get("probablePitcher", {}).get("id")
            away_pitcher_id = game["teams"]["away"].get("probablePitcher", {}).get("id")
            games.append({
                "game_id": game["gamePk"],
                "home_team": home,
                "away_team": away,
                "home_pitcher": home_pitcher,
                "away_pitcher": away_pitcher,
                "home_pitcher_id": home_pitcher_id,
                "away_pitcher_id": away_pitcher_id,
            })
    return games

def get_pitcher_k9(pitcher_id):
    """Returns K/9 for a specific pitcher this season."""
    if not pitcher_id:
        return None
    url = f"{MLB_BASE}/people/{pitcher_id}/stats"
    params = {
        "stats": "season",
        "group": "pitching",
        "season": SEASON,
        "gameType": "R",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if splits:
            return float(splits[0]["stat"].get("strikeoutsPer9Inn", 0) or 0)
    except Exception:
        pass
    return None

# ── Odds API ────────────────────────────────────────────────────────────────
def get_pitcher_strikeout_props():
    """
    Returns dict of pitcher_name -> {line, over_odds, under_odds, books}
    Uses The Odds API player props endpoint.
    """
    props = {}
    base = "https://api.the-odds-api.com/v4"

    # First get today's MLB event IDs
    events_url = f"{base}/sports/baseball_mlb/events"
    r = requests.get(events_url, params={"apiKey": ODDS_API_KEY}, timeout=15)
    if r.status_code != 200:
        print(f"Odds API events error: {r.status_code} {r.text}")
        return props

    events = r.json()
    today_events = [e for e in events if e["commence_time"][:10] == TODAY]
    print(f"Found {len(today_events)} MLB games today on Odds API")

    for event in today_events:
        event_id = event["id"]
        props_url = f"{base}/sports/baseball_mlb/events/{event_id}/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "pitcher_strikeouts",
            "oddsFormat": "american",
        }
        try:
            pr = requests.get(props_url, params=params, timeout=15)
            if pr.status_code != 200:
                continue
            event_data = pr.json()
            for bookmaker in event_data.get("bookmakers", []):
                book_name = bookmaker["title"]
                for market in bookmaker.get("markets", []):
                    if market["key"] != "pitcher_strikeouts":
                        continue
                    for outcome in market.get("outcomes", []):
                        pitcher = outcome["description"] if "description" in outcome else outcome.get("name", "")
                        bet_type = outcome["name"]  # "Over" or "Under"
                        line = outcome.get("point", 0)
                        price = outcome.get("price", 0)

                        if pitcher not in props:
                            props[pitcher] = {"over_lines": [], "under_lines": [], "over_odds": [], "under_odds": [], "books": set()}
                        props[pitcher]["books"].add(book_name)
                        if bet_type == "Over":
                            props[pitcher]["over_lines"].append(line)
                            props[pitcher]["over_odds"].append(price)
                        elif bet_type == "Under":
                            props[pitcher]["under_lines"].append(line)
                            props[pitcher]["under_odds"].append(price)
        except Exception as e:
            print(f"Error fetching props for event {event_id}: {e}")
            continue

    # Consolidate to consensus line (median)
    consolidated = {}
    for pitcher, data in props.items():
        if data["over_lines"]:
            median_line = sorted(data["over_lines"])[len(data["over_lines"]) // 2]
            avg_over_odds = int(sum(data["over_odds"]) / len(data["over_odds"])) if data["over_odds"] else None
            avg_under_odds = int(sum(data["under_odds"]) / len(data["under_odds"])) if data["under_odds"] else None
            consolidated[pitcher] = {
                "line": median_line,
                "over_odds": avg_over_odds,
                "under_odds": avg_under_odds,
                "books": list(data["books"]),
            }
    return consolidated

# ── Ranking logic ────────────────────────────────────────────────────────────
def build_composite_rankings(hitting_stats):
    """
    Ranks teams on 3 axes (most Ks, lowest avg, fewest batted balls).
    Returns sorted lists and composite scores.
    """
    teams = list(hitting_stats.keys())

    # Sort each axis
    by_ks       = sorted(teams, key=lambda t: hitting_stats[t]["strikeouts"], reverse=True)
    by_avg      = sorted(teams, key=lambda t: hitting_stats[t]["avg"])           # ascending = worse
    by_bbe      = sorted(teams, key=lambda t: hitting_stats[t]["batted_balls"])  # ascending = worse

    # Score: lower rank number = more vulnerable. Score = sum of ranks (lower = worse hitter)
    scores = defaultdict(int)
    for i, t in enumerate(by_ks):   scores[t] += (i + 1)
    for i, t in enumerate(by_avg):  scores[t] += (i + 1)
    for i, t in enumerate(by_bbe):  scores[t] += (i + 1)

    composite = sorted(teams, key=lambda t: scores[t])  # lowest score = most vulnerable

    return {
        "by_ks": by_ks,
        "by_avg": by_avg,
        "by_bbe": by_bbe,
        "composite": composite,
        "scores": dict(scores),
    }

# ── Main analysis ────────────────────────────────────────────────────────────
def analyze():
    print(f"Running MLB Strikeout Analyzer for {TODAY}...")

    print("Fetching team hitting stats...")
    hitting = get_team_hitting_stats()

    print("Fetching today's games and probable pitchers...")
    games = get_todays_games()

    print("Fetching pitcher prop lines...")
    props = get_pitcher_strikeout_props()

    print("Building composite rankings...")
    rankings = build_composite_rankings(hitting)

    # Build matchup analysis
    matchups = []
    pitcher_k9_cache = {}

    for game in games:
        for side in ["home", "away"]:
            pitcher_name = game[f"{side}_pitcher"]
            pitcher_id   = game[f"{side}_pitcher_id"]
            opp_team     = game["away_team"] if side == "home" else game["home_team"]
            my_team      = game[f"{side}_team"]

            if pitcher_name == "TBD":
                continue

            # Get pitcher K/9
            if pitcher_id not in pitcher_k9_cache:
                pitcher_k9_cache[pitcher_id] = get_pitcher_k9(pitcher_id)
            k9 = pitcher_k9_cache[pitcher_id]

            # Composite rank of opposing team (1 = most vulnerable)
            opp_rank = rankings["composite"].index(opp_team) + 1 if opp_team in rankings["composite"] else None
            opp_score = rankings["scores"].get(opp_team, 999)

            # K rank (team Ks taken, higher = more vulnerable)
            ks_rank = rankings["by_ks"].index(opp_team) + 1 if opp_team in rankings["by_ks"] else None
            avg_rank = rankings["by_avg"].index(opp_team) + 1 if opp_team in rankings["by_avg"] else None
            bbe_rank = rankings["by_bbe"].index(opp_team) + 1 if opp_team in rankings["by_bbe"] else None

            # Prop line
            prop = None
            for name, pdata in props.items():
                # fuzzy match on last name
                if pitcher_name.split()[-1].lower() in name.lower() or name.split()[-1].lower() in pitcher_name.lower():
                    prop = pdata
                    break

            # Expected Ks estimate: K/9 * (avg innings ~6) adjusted for opp vulnerability
            expected_ks = None
            if k9:
                base_innings = 5.5  # average starter innings
                vuln_factor = 1.0 + max(0, (15 - opp_rank) / 100) if opp_rank else 1.0
                expected_ks = round(k9 / 9 * base_innings * vuln_factor, 1)

            # Value flag
            value = None
            if expected_ks and prop:
                diff = expected_ks - prop["line"]
                if diff >= 0.5:
                    value = "OVER"
                elif diff <= -0.5:
                    value = "UNDER"
                else:
                    value = "NEUTRAL"

            matchups.append({
                "pitcher": pitcher_name,
                "my_team": my_team,
                "opp_team": opp_team,
                "k9": k9,
                "opp_composite_rank": opp_rank,
                "opp_score": opp_score,
                "ks_rank": ks_rank,
                "avg_rank": avg_rank,
                "bbe_rank": bbe_rank,
                "prop": prop,
                "expected_ks": expected_ks,
                "value": value,
            })

    # Sort by most interesting (strongest value bets first, then by opp vulnerability)
    def sort_key(m):
        v_order = {"OVER": 0, "UNDER": 1, "NEUTRAL": 2, None: 3}
        return (v_order.get(m["value"], 3), m["opp_composite_rank"] or 999)

    matchups.sort(key=sort_key)

    return {
        "date": TODAY,
        "rankings": rankings,
        "hitting": hitting,
        "matchups": matchups,
        "props_count": len(props),
    }

# ── HTML generator ────────────────────────────────────────────────────────────
def generate_html(data):
    rankings = data["rankings"]
    matchups = data["matchups"]
    hitting  = data["hitting"]

    def rank_badge(rank, total=30):
        if rank is None: return '<span class="rank-na">–</span>'
        pct = rank / total
        cls = "rank-good" if pct <= 0.33 else ("rank-mid" if pct <= 0.66 else "rank-bad")
        return f'<span class="{cls}">#{rank}</span>'

    def value_badge(value):
        if not value: return ""
        cls = {"OVER": "badge-over", "UNDER": "badge-under", "NEUTRAL": "badge-neutral"}[value]
        return f'<span class="badge {cls}">{value}</span>'

    def odds_fmt(o):
        if o is None: return "–"
        return f"+{o}" if o > 0 else str(o)

    # Top 10 vulnerable teams table
    top15 = rankings["composite"][:15]
    vuln_rows = ""
    for i, team in enumerate(top15):
        ks_r  = rankings["by_ks"].index(team) + 1  if team in rankings["by_ks"]  else "–"
        avg_r = rankings["by_avg"].index(team) + 1 if team in rankings["by_avg"] else "–"
        bbe_r = rankings["by_bbe"].index(team) + 1 if team in rankings["by_bbe"] else "–"
        score = rankings["scores"].get(team, "–")
        vuln_rows += f"""
        <tr>
          <td class="rank-num">{i+1}</td>
          <td class="team-name">{team}</td>
          <td>{rank_badge(ks_r)}</td>
          <td>{rank_badge(avg_r)}</td>
          <td>{rank_badge(bbe_r)}</td>
          <td><strong>{score}</strong></td>
        </tr>"""

    # Matchup rows
    matchup_rows = ""
    for m in matchups:
        prop = m["prop"]
        line_str = f'{prop["line"]}+ Ks' if prop else "No line"
        over_o   = odds_fmt(prop["over_odds"]) if prop else "–"
        under_o  = odds_fmt(prop["under_odds"]) if prop else "–"
        exp      = f'{m["expected_ks"]}' if m["expected_ks"] else "–"
        k9_str   = f'{m["k9"]:.2f}' if m["k9"] else "–"
        vbadge   = value_badge(m["value"])

        matchup_rows += f"""
        <tr class="{'highlight-row' if m['value'] == 'OVER' else ''}">
          <td class="pitcher-name">{m['pitcher']} {vbadge}</td>
          <td>{m['my_team']}</td>
          <td><strong>{m['opp_team']}</strong><br><small>Composite #{m['opp_composite_rank'] or '–'}</small></td>
          <td>{rank_badge(m['ks_rank'])}</td>
          <td>{rank_badge(m['avg_rank'])}</td>
          <td>{rank_badge(m['bbe_rank'])}</td>
          <td class="k9-cell">{k9_str}</td>
          <td class="exp-cell">{exp}</td>
          <td>{line_str}<br><small>O: {over_o} / U: {under_o}</small></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚾ MLB K Prop Analyzer — {data['date']}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0a0c0f;
    --surface: #111418;
    --surface2: #181c22;
    --border: #252b35;
    --accent: #e8ff47;
    --accent2: #47c8ff;
    --over: #47ff8a;
    --under: #ff6b6b;
    --neutral: #888;
    --text: #e8eaf0;
    --muted: #666;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    min-height: 100vh;
  }}
  .noise {{
    position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    opacity: 0.4;
  }}
  .container {{ position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 2rem; }}

  header {{
    display: flex; align-items: flex-end; justify-content: space-between;
    border-bottom: 2px solid var(--accent); padding-bottom: 1rem; margin-bottom: 2rem;
  }}
  .logo {{ font-family: 'Bebas Neue', sans-serif; font-size: 3.5rem; line-height: 1; color: var(--accent); letter-spacing: 2px; }}
  .logo span {{ color: var(--accent2); }}
  .date-badge {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.85rem;
    color: var(--muted); letter-spacing: 1px;
  }}

  .grid {{ display: grid; grid-template-columns: 340px 1fr; gap: 1.5rem; margin-bottom: 2rem; }}
  @media(max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}

  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; overflow: hidden;
  }}
  .card-header {{
    background: var(--surface2); padding: 0.75rem 1rem;
    font-family: 'Bebas Neue', sans-serif; font-size: 1.2rem;
    letter-spacing: 2px; color: var(--accent2);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 0.5rem;
  }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{
    background: var(--surface2); color: var(--muted);
    font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem;
    letter-spacing: 1px; text-transform: uppercase;
    padding: 0.6rem 0.75rem; text-align: left;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 0.65rem 0.75rem;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: var(--surface2); }}

  .highlight-row td {{ background: rgba(232,255,71,0.04); }}
  .highlight-row:hover td {{ background: rgba(232,255,71,0.08); }}

  .rank-num {{ font-family: 'IBM Plex Mono', monospace; color: var(--muted); font-size: 0.8rem; }}
  .team-name {{ font-weight: 600; }}
  .pitcher-name {{ font-weight: 600; color: var(--accent2); }}
  .k9-cell {{ font-family: 'IBM Plex Mono', monospace; color: var(--accent); }}
  .exp-cell {{ font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 1rem; }}

  .rank-good {{ color: var(--over); font-family: 'IBM Plex Mono', monospace; font-weight: 600; }}
  .rank-mid  {{ color: var(--accent); font-family: 'IBM Plex Mono', monospace; }}
  .rank-bad  {{ color: var(--muted); font-family: 'IBM Plex Mono', monospace; }}
  .rank-na   {{ color: var(--border); font-family: 'IBM Plex Mono', monospace; }}

  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 2px; font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; font-weight: 600; letter-spacing: 1px; margin-left: 0.4rem; }}
  .badge-over    {{ background: rgba(71,255,138,0.15); color: var(--over); border: 1px solid var(--over); }}
  .badge-under   {{ background: rgba(255,107,107,0.15); color: var(--under); border: 1px solid var(--under); }}
  .badge-neutral {{ background: rgba(136,136,136,0.1); color: var(--neutral); border: 1px solid var(--border); }}

  .section-title {{
    font-family: 'Bebas Neue', sans-serif; font-size: 1.6rem;
    letter-spacing: 3px; color: var(--text); margin-bottom: 1rem;
    padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
  }}
  .section-title span {{ color: var(--accent); }}

  small {{ color: var(--muted); font-size: 0.78rem; }}
  .legend {{ display: flex; gap: 1.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: var(--muted); }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; }}

  .stats-bar {{
    display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap;
  }}
  .stat-pill {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; padding: 0.75rem 1.25rem;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .stat-pill .val {{ font-size: 1.5rem; font-weight: 600; color: var(--accent); }}
  .stat-pill .lbl {{ font-size: 0.72rem; color: var(--muted); letter-spacing: 1px; text-transform: uppercase; }}

  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.78rem; font-family: 'IBM Plex Mono', monospace; }}
</style>
</head>
<body>
<div class="noise"></div>
<div class="container">
  <header>
    <div>
      <div class="logo">⚾ K<span>Prop</span></div>
      <div style="color:var(--muted);font-size:0.8rem;letter-spacing:1px;font-family:'IBM Plex Mono',monospace;">MLB STRIKEOUT PROP ANALYZER</div>
    </div>
    <div class="date-badge">{data['date']}<br>{len(matchups)} pitchers today<br>{data['props_count']} prop lines found</div>
  </header>

  <div class="stats-bar">
    <div class="stat-pill">
      <div class="val">{sum(1 for m in matchups if m['value']=='OVER')}</div>
      <div class="lbl">OVER Edges</div>
    </div>
    <div class="stat-pill">
      <div class="val">{sum(1 for m in matchups if m['value']=='UNDER')}</div>
      <div class="lbl">UNDER Edges</div>
    </div>
    <div class="stat-pill">
      <div class="val">{sum(1 for m in matchups if m['prop'])}</div>
      <div class="lbl">Lines Available</div>
    </div>
    <div class="stat-pill">
      <div class="val">{len(matchups)}</div>
      <div class="lbl">Total Pitchers</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-header">📊 Most Vulnerable Teams</div>
      <table>
        <thead>
          <tr>
            <th>#</th><th>Team</th><th>K Rank</th><th>Avg Rank</th><th>BBE Rank</th><th>Score</th>
          </tr>
        </thead>
        <tbody>{vuln_rows}</tbody>
      </table>
    </div>

    <div>
      <div class="section-title">Today's <span>Matchups</span></div>
      <div class="legend">
        <div class="legend-item"><div class="dot" style="background:var(--over)"></div> OVER edge (expected Ks &gt; line)</div>
        <div class="legend-item"><div class="dot" style="background:var(--under)"></div> UNDER edge</div>
        <div class="legend-item"><div class="dot" style="background:var(--neutral)"></div> Neutral / No edge</div>
        <div class="legend-item">Ranks: <span class="rank-good">#1–10</span> = most vulnerable</div>
      </div>
      <div class="card">
        <table>
          <thead>
            <tr>
              <th>Pitcher</th><th>Team</th><th>Opponent</th>
              <th>Opp K-Rank</th><th>Opp Avg-Rank</th><th>Opp BBE-Rank</th>
              <th>K/9</th><th>Exp Ks</th><th>Prop Line</th>
            </tr>
          </thead>
          <tbody>{matchup_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <footer>
    Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} · 
    Data: MLB Stats API + Baseball Savant + The Odds API · 
    For informational use only. Please gamble responsibly.
  </footer>
</div>
</body>
</html>"""
    return html

# ── Email via SendGrid ────────────────────────────────────────────────────────
def send_email(html_content, data):
    if not SENDGRID_KEY or not EMAIL_TO or not EMAIL_FROM:
        print("Email skipped: missing SENDGRID_API_KEY, EMAIL_TO, or EMAIL_FROM env vars.")
        return

    over_count = sum(1 for m in data["matchups"] if m["value"] == "OVER")
    subject = f"⚾ MLB K Props {data['date']} — {over_count} OVER edge{'s' if over_count != 1 else ''} today"

    payload = {
        "personalizations": [{"to": [{"email": EMAIL_TO}]}],
        "from": {"email": EMAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_content}],
    }

    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if r.status_code in (200, 202):
        print(f"Email sent to {EMAIL_TO}")
    else:
        print(f"Email failed: {r.status_code} {r.text}")

# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    data = analyze()
    html = generate_html(data)

    # Save HTML dashboard
    with open("index.html", "w") as f:
        f.write(html)
    print("Dashboard written to index.html")

    # Send email
    send_email(html, data)

    # Print summary to console
    print(f"\n{'='*60}")
    print(f"MLB K PROP ANALYSIS — {data['date']}")
    print(f"{'='*60}")
    for m in data["matchups"]:
        if m["value"] in ("OVER", "UNDER"):
            prop = m["prop"]
            line = f'Line: {prop["line"]}+' if prop else "No line"
            exp  = f'Exp: {m["expected_ks"]}' if m["expected_ks"] else ""
            print(f"  [{m['value']}] {m['pitcher']} vs {m['opp_team']} (#{m['opp_composite_rank']}) | K/9: {m['k9']:.2f} | {line} | {exp}")
    print(f"\n{sum(1 for m in data['matchups'] if m['value']=='OVER')} OVER edges, {sum(1 for m in data['matchups'] if m['value']=='UNDER')} UNDER edges")
