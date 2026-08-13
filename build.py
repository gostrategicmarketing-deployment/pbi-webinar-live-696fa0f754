#!/usr/bin/env python3
"""
Render the PBI webinar dashboard from the newest snapshot in data/.

Self-contained output: fonts and every ad creative are inlined, so the file works
offline, survives Meta's signed-CDN links expiring, and can be published as-is.

Each creative is inlined exactly once into a JS map and hydrated into the cards,
table rows and lightbox on load. Inlining per element instead would repeat the same
base64 three times per ad, across two windows.

    python3 build.py                 # newest snapshot
    python3 build.py data/2026-08-13T14_webinar_snapshot.json
"""

import base64
import hashlib
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
CACHE = HERE / "creative-cache"

# A local fonts/ wins so the deploy checkout is self-contained; in the workspace the
# shared folder is used instead and there is nothing to keep in sync. Missing fonts
# degrade to the CSS stack rather than failing the build.
_LOCAL_FONTS = HERE / "fonts"
FONTS = _LOCAL_FONTS if _LOCAL_FONTS.is_dir() else HERE.parent.parent / "ad-reporting-platform" / "fonts"

IMG_PX = 560
IMG_Q = 74

# How many ads get the featured treatment, per format. The rest live in the table.
FEATURED = 5

# An ad thinner than this is reported but flagged: a 1-lead ad on $2 of spend has a
# spectacular cost per lead and no evidence behind it.
THIN_SPEND = 25.0
THIN_LINK_CLICKS = 20

# PBI Brand Standards v1.0 (2023) via the pbi-brand-kit memory. Onyx + Orange is the
# core logomark pair; Burgundy carries the dark ground.
BRAND = {
    "onyx": "#212121",
    "orange": "#FF522B",
    "burgundy": "#720C3A",
}


def rank_key(ad):
    """Volume first, then efficiency. A lucky 1-lead ad must not outrank a 7-lead ad."""
    return (-ad["leads"], ad["cost_per_lead"] if ad["cost_per_lead"] is not None else 9e9,
            -ad["spend"])


def thin(ad):
    return ad["spend"] < THIN_SPEND or ad["link_clicks"] < THIN_LINK_CLICKS


def font_uri(name):
    p = FONTS / name
    if not p.exists():
        return None
    return "data:font/woff2;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def creative_uri(url):
    if not url:
        return None
    CACHE.mkdir(exist_ok=True)
    dst = CACHE / (hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16] + ".jpg")
    if not dst.exists():
        raw = dst.with_suffix(".raw")
        try:
            # -L matters: a video ad's poster is served from facebook.com/ads/image/?d=...,
            # which 302s to the CDN. Without it every video silently lost its still and
            # the whole Best videos block rendered as empty tiles.
            subprocess.run(["curl", "-sSL", "--fail", "--max-time", "25", "-o", str(raw), url],
                           check=True, capture_output=True)
            with Image.open(raw) as im:
                im = im.convert("RGB")
                im.thumbnail((IMG_PX, IMG_PX))
                im.save(dst, "JPEG", quality=IMG_Q, optimize=True)
        except (subprocess.CalledProcessError, OSError):
            # A missing creative is cosmetic; the row still carries its numbers.
            return None
        finally:
            raw.unlink(missing_ok=True)
    return "data:image/jpeg;base64," + base64.b64encode(dst.read_bytes()).decode("ascii")


def money(v, dp=2):
    return "—" if v is None else f"${v:,.{dp}f}"


def n(v):
    return "—" if v is None else f"{v:,.0f}"


def e(s):
    return html.escape(str(s if s is not None else ""))


def fmt_day(iso):
    y, mo, d = iso.split("-")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[int(mo) - 1]} {int(d)}, {y}"


def fmt_stamp(iso, abbrev=""):
    """'Aug 13, 2026 at 2:29 PM PDT' from the snapshot's tz-aware timestamp.

    The abbreviation is carried in the snapshot: parsing an ISO string yields a plain
    fixed offset, so tzname() here would render the useless 'UTC-07:00'.
    """
    dt = datetime.fromisoformat(iso)
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{fmt_day(dt.date().isoformat())} at {hour}:{dt.minute:02d} {ampm} {abbrev}".strip()


# ---------------------------------------------------------------- components

def stat_block(a, big=False):
    """The five reported metrics, in the order they are argued: what it produced,
    what that cost, what drove it, what that cost, what was spent to get it."""
    cells = [
        (n(a["leads"]), "leads"),
        (money(a["cost_per_lead"], 0), "per lead"),
        (n(a["link_clicks"]), "link clicks"),
        (money(a["cost_per_link_click"]), "per link click"),
        (money(a["spend"], 0), "spent"),
    ]
    return "".join(f'<span class="stat"><b>{v}</b><i>{lab}</i></span>' for v, lab in cells)


def featured_card(a, i, cr):
    fmt = a.get("format", "IMAGE")
    has = bool(cr.get(a["ad_id"], {}).get("thumb"))
    img = (f'<img data-cr="{e(a["ad_id"])}" alt="">' if has
           else '<span class="card-missing">creative unavailable</span>')
    play = '<span class="play" aria-hidden="true"></span>' if fmt == "VIDEO" else ""
    chips = '<span class="chip chip-thin">thin data</span>' if a["thin"] else ""
    return f"""
      <button class="card{' card-lead' if i == 0 else ''}" data-ad="{e(a['ad_id'])}"
              aria-label="Open creative and full numbers for {e(a['ad_name'])}">
        <span class="card-rank">{i + 1:02d}</span>
        <span class="card-img">{img}{play}</span>
        <span class="card-body">
          <span class="card-name">{e(a['ad_name'])}</span>
          <span class="card-set">{e(a['adset_name'])}{chips}</span>
          <span class="card-stats">{stat_block(a)}</span>
        </span>
      </button>"""


def thumb_button(a, cr):
    if cr.get(a["ad_id"], {}).get("thumb"):
        v = ' data-video="1"' if a.get("format") == "VIDEO" else ""
        return (f'<button class="thumb" data-ad="{e(a["ad_id"])}"{v} '
                f'aria-label="Open creative for {e(a["ad_name"])}">'
                f'<img data-cr="{e(a["ad_id"])}" alt=""></button>')
    return '<span class="thumb thumb-missing" aria-hidden="true"></span>'


def featured_block(ads, fmt, cr):
    """Top ads of one format. Backfills on link clicks when too few have led yet."""
    pool = [a for a in ads if a.get("format") == fmt]
    if not pool:
        return None, 0, []
    picked = [a for a in pool if a["leads"] > 0][:FEATURED]
    if len(picked) < FEATURED:
        rest = [a for a in pool if a not in picked]
        picked += sorted(rest, key=lambda x: -x["link_clicks"])[:FEATURED - len(picked)]
    return "".join(featured_card(a, i, cr) for i, a in enumerate(picked)), len(pool), picked


def recon_line(r):
    d = r["deltas"]
    if r["worst_grade"] == "exact":
        return ("Spend, leads and link clicks summed from the ad rows equal the figures Meta "
                "reports at account level for the same filter and window, exactly.")
    parts = []
    for k, label in (("spend", "Spend"), ("leads", "Leads"), ("link_clicks", "Link clicks")):
        x = d[k]
        if x["grade"] == "exact":
            parts.append(f"{label} match exactly.")
        else:
            val = money(x['ad_sum']) if k == "spend" else n(x['ad_sum'])
            acct = money(x['account']) if k == "spend" else n(x['account'])
            parts.append(f"{label} sum to {val} against {acct} at account level "
                         f"({x['pct']}%{', within tolerance' if x['grade'] == 'drift' else ', OUT OF TOLERANCE'}).")
    tail = ("" if r["worst_grade"] == "drift" else
            " A gap this size is not attribution rounding; treat this pull as suspect.")
    return (" ".join(parts) + " Ad rows and account totals differ slightly because an ad "
            "deleted mid-window still counts at account level while returning no ad row." + tail)


def window_html(key, w, cr, active):
    ads = sorted(w["ads"], key=rank_key)
    for a in ads:
        a["thin"] = thin(a)

    t = w["totals"]
    kpis = [
        ("Leads", n(t["leads"]), "Lead standard event", f"{money(t['cost_per_lead'])} per lead", "accent"),
        ("Cost per lead", money(t["cost_per_lead"]), "Spent ÷ leads", f"{n(t['leads'])} leads", "accent"),
        ("Link clicks", n(t["link_clicks"]), "Outbound to the page",
         f"{money(t['cost_per_link_click'])} per link click", ""),
        ("Cost per link click", money(t["cost_per_link_click"]), "Spent ÷ link clicks",
         f"{n(t['link_clicks'])} link clicks", ""),
        ("Total spent", money(t["spend"], 0), "Amount spent", f"over {w['days']} days", ""),
    ]
    kpi_html = "".join(f"""
      <div class="kpi {cls}">
        <p class="kpi-label">{e(label)}</p>
        <p class="kpi-value">{e(value)}</p>
        <p class="kpi-def">{e(defn)}</p>
        <p class="kpi-sub">{e(sub)}</p>
      </div>""" for label, value, defn, sub, cls in kpis)

    blocks = ""
    thin_flagged = False
    for fmt, title, blurb in (
        ("IMAGE", "Best images", "The strongest still creatives, ranked by leads, then by cost per lead."),
        ("VIDEO", "Best videos", "The strongest video creatives, ranked the same way and on the same "
                                 "five metrics as the images, so the two formats are directly comparable."),
    ):
        cards, pool_n, picked = featured_block(ads, fmt, cr)
        if cards is None:
            blocks += f"""
    <section>
      <div class="sec-head"><h2>{title}</h2>
      <p>No {fmt.lower()} ads delivered in this window.</p></div>
    </section>"""
            continue
        thin_flagged = thin_flagged or any(a["thin"] for a in picked)
        blocks += f"""
    <section>
      <div class="sec-head">
        <h2>{title}</h2>
        <p>{blurb} {pool_n} {fmt.lower()} ad{'s' if pool_n != 1 else ''} delivered in this window.
           Click any ad to see the full creative, its copy, and its numbers.</p>
      </div>
      <div class="cards">{cards}</div>
    </section>"""

    thin_note = ""
    if thin_flagged:
        thin_note = (f'<p class="note">Cards marked <span class="chip chip-thin">thin data</span> '
                     f'have under {money(THIN_SPEND, 0)} spent or under {THIN_LINK_CLICKS} link clicks '
                     f'behind them. Their cost per lead is a single event, not a rate. Read them as '
                     f'candidates to fund, not as winners to scale.</p>')

    camp_rows = "".join(f"""
      <tr>
        <td class="name">{e(c['campaign_name'])}</td>
        <td class="num strong">{n(c['leads'])}</td>
        <td class="num">{money(c['cost_per_lead'])}</td>
        <td class="num">{n(c['link_clicks'])}</td>
        <td class="num">{money(c['cost_per_link_click'])}</td>
        <td class="num">{money(c['spend'])}</td>
      </tr>""" for c in w["campaigns"])

    day_max = max((d["spend"] for d in w["daily"]), default=1) or 1
    days = "".join(f"""
      <div class="day">
        <div class="day-bar"><span style="height:max(5px,{d['spend'] / day_max * 100:.1f}%)"></span></div>
        <p class="day-date">{fmt_day(d['date'])}</p>
        <p class="day-spend">{money(d['spend'], 0)}</p>
        <p class="day-leads">{n(d['leads'])} {'lead' if d['leads'] == 1 else 'leads'}</p>
      </div>""" for d in w["daily"])

    table_rows = "".join(f"""
      <tr{' class="zero"' if not a['leads'] else ''}>
        <td class="cell-thumb">{thumb_button(a, cr)}</td>
        <td class="name">
          <span class="ad-name">{e(a['ad_name'])}<span class="fmt fmt-{a.get('format','IMAGE').lower()}">{'Video' if a.get('format') == 'VIDEO' else 'Image'}</span></span>
          <span class="ad-set">{e(a['adset_name'])}</span>
        </td>
        <td class="num strong">{n(a['leads'])}</td>
        <td class="num">{money(a['cost_per_lead'])}</td>
        <td class="num">{n(a['link_clicks'])}</td>
        <td class="num">{money(a['cost_per_link_click'])}</td>
        <td class="num">{money(a['spend'])}</td>
      </tr>""" for a in ads)

    lead_ads = sum(1 for a in ads if a["leads"])
    zero_spend = money(sum(a["spend"] for a in ads if not a["leads"]))

    return f"""
  <div class="win" id="win-{key}"{'' if active else ' hidden'}>
    <div class="kpis">{kpi_html}</div>
    {blocks}
    {thin_note}

    <section>
      <div class="sec-head">
        <h2>Day by day</h2>
        <p>Spend and leads per day. The final day is partial: it runs to the moment of the
           pull, on the ad account's Los Angeles clock.</p>
      </div>
      <div class="days">{days}</div>
    </section>

    <section>
      <div class="sec-head"><h2>By campaign</h2></div>
      <div class="tablewrap">
        <table>
          <thead><tr>
            <th class="l">Campaign</th><th>Leads</th><th>Cost / lead</th>
            <th>Link clicks</th><th>Cost / link click</th><th>Spent</th>
          </tr></thead>
          <tbody>{camp_rows}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="sec-head">
        <h2>Every ad</h2>
        <p>All {len(ads)} ads that delivered in this window, ranked the same way.
           {lead_ads} have produced a lead so far; {zero_spend} of spend sits on ads that
           have not. Click a thumbnail to open the creative.</p>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr>
            <th class="l"></th><th class="l">Ad</th><th>Leads</th><th>Cost / lead</th>
            <th>Link clicks</th><th>Cost / link click</th><th>Spent</th>
          </tr></thead>
          <tbody>{table_rows}</tbody>
        </table>
      </div>
    </section>

    <div class="method">
      <h3>Method</h3>
      <ul>
        <li><b>Window.</b> {e(w['note'])} {fmt_day(w['since'])} to {fmt_day(w['until'])},
            {w['days']} days.</li>
        <li><b>Reconciliation.</b> {recon_line(w['reconciliation'])}</li>
      </ul>
    </div>
  </div>"""


def build(snap):
    m = snap["meta"]
    cr_raw = snap["creatives"]

    # Download and downscale each creative once, keyed by ad, shared by both windows.
    cr = {}
    for ad_id, c in cr_raw.items():
        cr[ad_id] = {**c, "thumb": creative_uri(c.get("image_url"))}

    order = ["launch", "7d"] if "7d" in snap["windows"] else list(snap["windows"])
    default = m.get("default_window", order[0])

    tabs = "".join(
        f'<button class="tab{" on" if k == default else ""}" data-win="{k}" type="button">'
        f'{e(snap["windows"][k]["label"])}</button>' for k in order)
    wins = "".join(window_html(k, snap["windows"][k], cr, k == default) for k in order)

    # Every ad across every window, for the lightbox.
    seen = {}
    for k in order:
        for a in snap["windows"][k]["ads"]:
            seen.setdefault(a["ad_id"], {})[k] = a

    payload = {}
    for ad_id, per_win in seen.items():
        c = cr.get(ad_id, {})
        any_ad = next(iter(per_win.values()))
        payload[ad_id] = {
            "name": any_ad["ad_name"], "adset": any_ad["adset_name"],
            "campaign": any_ad["campaign_name"],
            "format": any_ad.get("format", "IMAGE"),
            "permalink": c.get("permalink"),
            "headline": c.get("headline"), "body": c.get("body"),
            "stats": {k: [
                ["Leads", n(a["leads"])],
                ["Cost per lead", money(a["cost_per_lead"])],
                ["Link clicks", n(a["link_clicks"])],
                ["Cost per link click", money(a["cost_per_link_click"])],
                ["Total spent", money(a["spend"])],
            ] for k, a in per_win.items()},
            "thin": {k: thin(a) for k, a in per_win.items()},
        }

    thumbs = {ad_id: c["thumb"] for ad_id, c in cr.items() if c.get("thumb")}

    pf, ws = font_uri("playfair-subset.woff2"), font_uri("worksans-subset.woff2")
    faces = ""
    if pf:
        faces += ("@font-face{font-family:'Playfair PBI';src:url(%s) format('woff2');"
                  "font-weight:400 900;font-display:swap}" % pf)
    if ws:
        faces += ("@font-face{font-family:'Work Sans PBI';src:url(%s) format('woff2');"
                  "font-weight:300 800;font-display:swap}" % ws)

    live = [c for c in m["campaigns_matched"] if c["status"] == "ACTIVE"]

    return PAGE.format(
        faces=faces,
        onyx=BRAND["onyx"], orange=BRAND["orange"],
        title=f"Weekly Webinar Performance — {m['short_name']}",
        client=e(m["client"]), account=e(m["account_label"]), account_id=e(m["account_id"]),
        stamp=e(fmt_stamp(m["pulled_at"], m.get("timezone_abbrev", ""))),
        live_count=len(live), matched=len(m["campaigns_matched"]),
        lead_action=e(m["lead_action"]),
        tabs=tabs, windows=wins,
        thin_spend=money(THIN_SPEND, 0), thin_clicks=THIN_LINK_CLICKS,
        featured_n=FEATURED,
        payload=json.dumps(payload, separators=(",", ":")),
        thumbs=json.dumps(thumbs, separators=(",", ":")),
        default=default,
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, noimageindex">
<!-- The build refreshes hourly; an open tab picks the new one up without a manual reload. -->
<meta http-equiv="refresh" content="1800">
<title>{title}</title>
<style>
{faces}
:root {{
  --onyx: {onyx};
  --accent: {orange};
  --accent-ink: #B8330F;
  --ground: #F7F5F4;
  --surface: #FFFFFF;
  --sunk: #EFECEA;
  --rule: #E1DCD9;
  --rule-soft: #EDE9E7;
  --ink: #211E1D;
  --ink-2: #57504C;
  --ink-3: #8A817C;
  --band: {onyx};
  --band-ink: #FFFFFF;
  --band-ink-2: #B9B2AE;
  --watch: #8A6A00;
  --shadow: 0 1px 2px rgba(33,30,29,.06), 0 8px 24px -12px rgba(33,30,29,.18);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --ground: #171514; --surface: #201D1C; --sunk: #2A2624; --rule: #37312E;
    --rule-soft: #2C2725; --ink: #F2EDEB; --ink-2: #BDB4AF; --ink-3: #8B817C;
    --accent: #FF6E4C; --accent-ink: #FF8A6B; --band: #100E0D; --band-ink: #F7F3F1;
    --band-ink-2: #9A918C; --watch: #D9BE4A;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
  }}
}}
:root[data-theme="dark"] {{
  --ground: #171514; --surface: #201D1C; --sunk: #2A2624; --rule: #37312E;
  --rule-soft: #2C2725; --ink: #F2EDEB; --ink-2: #BDB4AF; --ink-3: #8B817C;
  --accent: #FF6E4C; --accent-ink: #FF8A6B; --band: #100E0D; --band-ink: #F7F3F1;
  --band-ink-2: #9A918C; --watch: #D9BE4A;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
}}
:root[data-theme="light"] {{
  --ground: #F7F5F4; --surface: #FFFFFF; --sunk: #EFECEA; --rule: #E1DCD9;
  --rule-soft: #EDE9E7; --ink: #211E1D; --ink-2: #57504C; --ink-3: #8A817C;
  --accent: {orange}; --accent-ink: #B8330F; --band: {onyx}; --band-ink: #FFFFFF;
  --band-ink-2: #B9B2AE; --watch: #8A6A00;
  --shadow: 0 1px 2px rgba(33,30,29,.06), 0 8px 24px -12px rgba(33,30,29,.18);
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: 'Work Sans PBI', 'Helvetica Neue', Arial, sans-serif;
  font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 22px 72px; }}
h2 {{
  font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: 26px; line-height: 1.15; margin: 0; text-wrap: balance;
}}
.num, .kpi-value, table td.num, .stat b, .day-spend {{ font-variant-numeric: tabular-nums; }}

/* ---------- masthead ---------- */
.band {{ background: var(--band); color: var(--band-ink); padding: 34px 0 30px; }}
.band .wrap {{ padding-bottom: 0; }}
.eyebrow {{
  margin: 0 0 10px; font-size: 11px; font-weight: 600; letter-spacing: .16em;
  text-transform: uppercase; color: var(--accent);
}}
.band h1 {{
  font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(30px, 5vw, 44px); line-height: 1.05; margin: 0 0 14px;
  text-wrap: balance;
}}
.band-meta {{
  display: flex; flex-wrap: wrap; gap: 8px 26px; margin: 0;
  font-size: 13px; color: var(--band-ink-2);
}}
.band-meta b {{ color: var(--band-ink); font-weight: 500; }}
.live {{ display: inline-flex; align-items: center; gap: 7px; }}
.live::before {{
  content: ''; width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent); animation: pulse 2.4s ease-in-out infinite;
}}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .3; }} }}

/* ---------- window tabs ---------- */
.tabs {{ display: flex; gap: 8px; margin: 22px 0 0; flex-wrap: wrap; }}
.tab {{
  appearance: none; font: inherit; font-size: 13px; font-weight: 500; cursor: pointer;
  padding: 8px 16px; border-radius: 2px; border: 1px solid rgba(255,255,255,.22);
  background: transparent; color: var(--band-ink-2);
}}
.tab:hover {{ color: var(--band-ink); border-color: rgba(255,255,255,.45); }}
.tab.on {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* ---------- kpi row ---------- */
.kpis {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin: -26px 0 0; }}
.kpi {{
  background: var(--surface); border: 1px solid var(--rule); border-radius: 3px;
  padding: 18px 18px 16px; box-shadow: var(--shadow); border-top: 3px solid var(--rule);
}}
.kpi.accent {{ border-top-color: var(--accent); }}
.kpi-label {{
  margin: 0 0 10px; font-size: 11px; font-weight: 600; letter-spacing: .12em;
  text-transform: uppercase; color: var(--ink-3);
}}
.kpi-value {{
  margin: 0; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(26px, 3.1vw, 38px); line-height: 1;
}}
.kpi.accent .kpi-value {{ color: var(--accent-ink); }}
.kpi-def {{ margin: 10px 0 0; font-size: 12px; color: var(--ink-3); }}
.kpi-sub {{
  margin: 6px 0 0; padding-top: 8px; border-top: 1px solid var(--rule-soft);
  font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums;
}}

/* ---------- sections ---------- */
section {{ margin-top: 46px; }}
.sec-head {{ margin-bottom: 4px; }}
.sec-head p {{ margin: 6px 0 0; color: var(--ink-2); font-size: 14px; max-width: 68ch; }}
.note {{
  margin: 18px 0 0; padding: 12px 14px; background: var(--sunk);
  border-left: 2px solid var(--accent); border-radius: 2px;
  font-size: 13px; color: var(--ink-2); max-width: 78ch;
}}

/* ---------- featured cards ---------- */
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 20px; }}
.card {{
  grid-column: span 1; display: flex; flex-direction: column; position: relative;
  appearance: none; text-align: left; font: inherit; color: inherit; cursor: pointer;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 3px;
  padding: 0; overflow: hidden; box-shadow: var(--shadow);
  transition: transform .16s ease, border-color .16s ease;
}}
.card:hover, .card:focus-visible {{ transform: translateY(-3px); border-color: var(--accent); }}
.card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.card-lead {{ grid-column: span 2; grid-row: span 2; }}
.card-rank {{
  position: absolute; top: 10px; left: 10px; z-index: 2;
  font-family: 'Playfair PBI', Georgia, serif; font-weight: 700; font-size: 15px;
  line-height: 1; padding: 6px 8px; border-radius: 2px;
  background: rgba(16,14,13,.82); color: #fff; backdrop-filter: blur(3px);
}}
.card-lead .card-rank {{ font-size: 21px; padding: 9px 12px; }}
.card-img {{
  display: block; position: relative; background: var(--sunk); aspect-ratio: 1 / 1;
  overflow: hidden; flex: 1 1 auto; min-height: 0;
}}
.card-lead .card-img {{ aspect-ratio: auto; min-height: 300px; }}
/* contain, not cover. These creatives run 9:16 and 4:5; cropping them to the card's
   square centre reduces a vertical ad to a swatch of its background colour and hides
   the headline that is the whole reason to look at it. */
.card-img img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
/* A video ad's poster frame is a still like any other, so the format has to be marked
   or the two blocks look identical. */
.play {{
  position: absolute; inset: 0; margin: auto; width: 54px; height: 54px; z-index: 2;
  border-radius: 50%; background: rgba(16,14,13,.6); backdrop-filter: blur(3px);
  border: 1.5px solid rgba(255,255,255,.85);
}}
.play::after {{
  content: ''; position: absolute; inset: 0; margin: auto; width: 0; height: 0;
  border-style: solid; border-width: 10px 0 10px 16px;
  border-color: transparent transparent transparent #fff; transform: translateX(2px);
}}
.card-missing {{ display: grid; place-items: center; height: 100%; font-size: 12px; color: var(--ink-3); }}
.card-body {{ display: block; padding: 14px 15px 15px; flex: 0 0 auto; }}
.card-name {{ display: block; font-weight: 600; font-size: 14px; line-height: 1.35; overflow-wrap: anywhere; }}
.card-lead .card-name {{ font-size: 17px; }}
.card-set {{
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 3px; font-size: 12px; color: var(--ink-3);
}}
.card-stats {{
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px 10px;
  margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--rule-soft);
}}
.card-lead .card-stats {{ grid-template-columns: repeat(5, 1fr); }}
.stat {{ display: block; }}
.stat b {{
  display: block; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: 19px; line-height: 1.1;
}}
.card-lead .stat b {{ font-size: 23px; }}
.stat:first-child b {{ color: var(--accent-ink); }}
.stat i {{
  display: block; margin-top: 2px; font-style: normal; font-size: 10px;
  letter-spacing: .09em; text-transform: uppercase; color: var(--ink-3);
}}
.chip {{
  display: inline-block; padding: 2px 7px; border-radius: 2px;
  font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
}}
.chip-thin {{ background: color-mix(in srgb, var(--watch) 16%, transparent); color: var(--watch); }}
.fmt {{
  display: inline-block; margin-left: 8px; padding: 1px 6px; border-radius: 2px;
  font-size: 9.5px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  vertical-align: 1px; border: 1px solid var(--rule); color: var(--ink-3);
}}
.fmt-video {{ border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent-ink); }}

/* ---------- day strip ---------- */
.days {{ display: flex; gap: 10px; margin-top: 20px; overflow-x: auto; padding-bottom: 4px; }}
.day {{
  flex: 0 0 128px; background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 12px 13px 13px;
}}
.day-bar {{
  display: flex; align-items: flex-end; height: 46px;
  border-bottom: 1px solid var(--rule); margin-bottom: 10px;
}}
.day-bar span {{ display: block; width: 100%; background: var(--accent); border-radius: 3px 3px 0 0; opacity: .85; }}
.day-date {{ margin: 0; font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }}
.day-spend {{ margin: 3px 0 0; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700; font-size: 20px; line-height: 1.1; }}
.day-leads {{ margin: 2px 0 0; font-size: 12px; color: var(--ink-2); }}

/* ---------- tables ---------- */
.tablewrap {{
  margin-top: 20px; overflow-x: auto; background: var(--surface);
  border: 1px solid var(--rule); border-radius: 3px;
}}
table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
thead th {{
  position: sticky; top: 0; background: var(--sunk); z-index: 1;
  text-align: right; padding: 11px 14px; font-size: 10.5px; font-weight: 600;
  letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3);
  border-bottom: 1px solid var(--rule); white-space: nowrap;
}}
thead th:first-child, thead th.l {{ text-align: left; }}
tbody td {{ padding: 10px 14px; border-bottom: 1px solid var(--rule-soft); vertical-align: middle; }}
tbody tr:last-child td {{ border-bottom: 0; }}
tbody tr:hover {{ background: var(--sunk); }}
td.num {{ text-align: right; white-space: nowrap; }}
td.num.strong {{ font-weight: 600; color: var(--accent-ink); }}
tr.zero td.num.strong {{ color: var(--ink-3); font-weight: 400; }}
td.name {{ min-width: 240px; }}
.ad-name {{ display: block; font-weight: 500; overflow-wrap: anywhere; }}
.ad-set {{ display: block; font-size: 11.5px; color: var(--ink-3); margin-top: 1px; }}
.cell-thumb {{ width: 62px; padding-right: 0; }}
.thumb {{
  display: block; position: relative; width: 46px; height: 46px; padding: 0;
  border-radius: 2px; overflow: hidden; border: 1px solid var(--rule);
  background: var(--sunk); cursor: pointer; appearance: none;
}}
.thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.thumb[data-video]::after {{
  content: ''; position: absolute; inset: 0; margin: auto; width: 0; height: 0;
  border-style: solid; border-width: 6px 0 6px 10px;
  border-color: transparent transparent transparent #fff;
  filter: drop-shadow(0 0 3px rgba(0,0,0,.9));
}}
.thumb:hover, .thumb:focus-visible {{ border-color: var(--accent); }}
.thumb:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.thumb-missing {{ cursor: default; }}

/* ---------- lightbox ---------- */
.lb[hidden] {{ display: none; }}
.lb {{
  position: fixed; inset: 0; z-index: 50; display: grid; place-items: center;
  background: rgba(16,14,13,.74); padding: 24px; backdrop-filter: blur(4px);
}}
.lb-panel {{
  background: var(--surface); border-radius: 4px; max-width: 940px; width: 100%;
  max-height: 90vh; overflow: auto; display: grid; grid-template-columns: 1fr 1fr;
  box-shadow: 0 24px 64px -20px rgba(0,0,0,.6);
}}
.lb-img {{ background: var(--sunk); display: grid; place-items: center; min-height: 220px; }}
.lb-img img {{ width: 100%; height: 100%; max-height: 90vh; object-fit: contain; display: block; }}
.lb-side {{ padding: 26px 28px 28px; }}
.lb-side h3 {{
  margin: 0; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: 21px; line-height: 1.2; overflow-wrap: anywhere;
}}
.lb-sub {{ margin: 6px 0 0; font-size: 12.5px; color: var(--ink-3); }}
.lb-grid {{
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px 18px;
  margin: 20px 0 0; padding-top: 18px; border-top: 1px solid var(--rule);
}}
.lb-grid div b {{
  display: block; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: 19px; line-height: 1.1; font-variant-numeric: tabular-nums;
}}
.lb-grid div i {{
  display: block; font-style: normal; margin-top: 2px; font-size: 10px;
  letter-spacing: .09em; text-transform: uppercase; color: var(--ink-3);
}}
.lb-copy {{
  margin: 18px 0 0; padding-top: 16px; border-top: 1px solid var(--rule);
  font-size: 13px; color: var(--ink-2); white-space: pre-wrap;
  max-height: 190px; overflow: auto;
}}
.lb-actions {{ display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }}
.btn {{
  appearance: none; font: inherit; font-size: 13px; font-weight: 500;
  padding: 9px 15px; border-radius: 2px; cursor: pointer; text-decoration: none;
  border: 1px solid var(--rule); background: var(--surface); color: var(--ink);
}}
.btn-primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
.btn:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* ---------- method ---------- */
.method {{
  margin-top: 52px; padding-top: 22px; border-top: 1px solid var(--rule);
  font-size: 12.5px; color: var(--ink-3);
}}
.method h3 {{
  margin: 0 0 10px; font-size: 11px; font-weight: 600; letter-spacing: .13em;
  text-transform: uppercase; color: var(--ink-2);
}}
.method ul {{ margin: 0; padding-left: 18px; max-width: 82ch; }}
.method li {{ margin-bottom: 7px; }}

@media (max-width: 1080px) {{ .kpis {{ grid-template-columns: repeat(3, 1fr); }} }}
@media (max-width: 980px) {{
  .kpis {{ grid-template-columns: repeat(2, 1fr); margin-top: 20px; }}
  .cards {{ grid-template-columns: repeat(2, 1fr); }}
  .card-lead {{ grid-column: span 2; grid-row: auto; }}
  .card-lead .card-stats {{ grid-template-columns: repeat(3, 1fr); }}
  .lb-panel {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 560px) {{
  .kpis {{ grid-template-columns: 1fr; }}
  .cards {{ grid-template-columns: 1fr; }}
  .card-lead .card-stats, .card-stats {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
</head>
<body>

<header class="band">
  <div class="wrap">
    <p class="eyebrow">{client} · Meta ad account {account_id}</p>
    <h1>Weekly Webinar Performance</h1>
    <p class="band-meta">
      <span>Account <b>{account}</b></span>
      <span>Campaigns <b>{live_count} active</b> of {matched} webinar-named</span>
      <span class="live">Updated <b>{stamp}</b></span>
      <span>Refreshes <b>hourly</b></span>
    </p>
    <div class="tabs">{tabs}</div>
  </div>
</header>

<div class="wrap">
{windows}

  <div class="method">
    <h3>How to read this</h3>
    <ul>
      <li><b>Five metrics, everywhere.</b> Leads, cost per lead, link clicks, cost per link
          click, and total spent. Every ranking on this page uses them and nothing else, so a
          video and a still are judged on the same terms.</li>
      <li><b>Scope.</b> Every campaign in Meta ad account {account_id} whose name contains
          "webinar", matched by name rather than by a fixed ID list so next week's campaign is
          picked up without editing anything. {matched} campaigns match; {live_count} are
          active.</li>
      <li><b>Leads</b> are the <code>{lead_action}</code> standard event: the Lead pixel event
          fired on PBI's site. In this account the aggregated "Leads" column returns the same
          count, so there is no on-Facebook lead form inflating it.</li>
      <li><b>Link clicks, not all-clicks.</b> Every click figure and the cost per click use
          link clicks: the clicks that actually left for the landing page. All-clicks runs
          about 2.4x higher on this account and would flatter both.</li>
      <li><b>Ranking.</b> Leads first, then cost per lead, then spend. Volume outranks
          efficiency on purpose: a single lead on a few dollars produces a spectacular cost
          per lead and proves nothing. Images and videos are ranked in separate blocks so the
          top {featured_n} of each format is always visible, even when one format dominates.</li>
      <li><b>Rates are recomputed from summed components</b>, never averaged across ads, so a
          two-dollar ad cannot weigh as much as a two-hundred-dollar one.</li>
      <li><b>Thin data.</b> Ads under {thin_spend} spent or under {thin_clicks} link clicks are
          flagged. Their cost per lead is one event, not a rate.</li>
      <li><b>Source.</b> Meta Graph API v21.0, read-only. All figures are on the ad account's
          own clock, America/Los_Angeles. Creatives are downloaded and embedded because Meta's
          image links are signed and expire. Nothing on this page is estimated or inferred.</li>
    </ul>
  </div>
</div>

<div class="lb" id="lb" hidden role="dialog" aria-modal="true" aria-labelledby="lb-title">
  <div class="lb-panel">
    <div class="lb-img" id="lb-img"></div>
    <div class="lb-side">
      <h3 id="lb-title"></h3>
      <p class="lb-sub" id="lb-sub"></p>
      <div class="lb-grid" id="lb-grid"></div>
      <div class="lb-copy" id="lb-copy" hidden></div>
      <div class="lb-actions">
        <a class="btn btn-primary" id="lb-link" href="#" target="_blank" rel="noopener">View live post</a>
        <button class="btn" id="lb-close" type="button">Close</button>
      </div>
    </div>
  </div>
</div>

<script>
const ADS = {payload};
const THUMBS = {thumbs};
let WIN = "{default}";

// Each creative is stored once and painted into every element that references it.
for (const img of document.querySelectorAll('img[data-cr]')) {{
  const src = THUMBS[img.dataset.cr];
  if (src) img.src = src; else img.remove();
}}

document.querySelectorAll('.tab').forEach(function (tab) {{
  tab.addEventListener('click', function () {{
    WIN = tab.dataset.win;
    document.querySelectorAll('.tab').forEach(function (t) {{ t.classList.toggle('on', t === tab); }});
    document.querySelectorAll('.win').forEach(function (w) {{ w.hidden = w.id !== 'win-' + WIN; }});
    window.scrollTo({{ top: 0, behavior: 'smooth' }});
  }});
}});

const lb = document.getElementById('lb');
let lastFocus = null;

function openAd(id) {{
  const a = ADS[id];
  if (!a) return;
  lastFocus = document.activeElement;
  const src = THUMBS[id];
  document.getElementById('lb-img').innerHTML = src
    ? '<img src="' + src + '" alt="Creative for ' + a.name.replace(/"/g, '&quot;') + '">'
    : '';
  document.getElementById('lb-title').textContent = a.name;
  // An ad can be in one window and not the other; fall back to whichever it has.
  const stats = a.stats[WIN] || a.stats[Object.keys(a.stats)[0]];
  const isThin = a.thin[WIN] !== undefined ? a.thin[WIN] : false;
  document.getElementById('lb-sub').textContent =
    (a.format === 'VIDEO' ? 'Video' : 'Image') + ' · ' + a.adset + ' · ' + a.campaign
    + (isThin ? ' · thin data' : '');
  document.getElementById('lb-grid').innerHTML =
    stats.map(function (s) {{ return '<div><b>' + s[1] + '</b><i>' + s[0] + '</i></div>'; }}).join('');
  const copy = document.getElementById('lb-copy');
  const text = [a.headline, a.body].filter(Boolean).join('\\n\\n');
  copy.textContent = text;
  copy.hidden = !text;
  const link = document.getElementById('lb-link');
  if (a.permalink) {{
    link.href = a.permalink;
    link.hidden = false;
    link.textContent = a.format === 'VIDEO' ? 'Watch on Facebook' : 'View live post';
  }} else {{ link.hidden = true; }}
  lb.hidden = false;
  document.body.style.overflow = 'hidden';
  document.getElementById('lb-close').focus();
}}

function closeAd() {{
  lb.hidden = true;
  document.body.style.overflow = '';
  if (lastFocus) lastFocus.focus();
}}

document.addEventListener('click', function (e) {{
  const t = e.target.closest('[data-ad]');
  if (t) {{ openAd(t.dataset.ad); return; }}
  if (e.target === lb) closeAd();
}});
document.getElementById('lb-close').addEventListener('click', closeAd);
document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape' && !lb.hidden) closeAd(); }});
</script>
</body>
</html>
"""


def main():
    if len(sys.argv) > 1:
        snap_path = Path(sys.argv[1])
    else:
        snaps = sorted((HERE / "data").glob("*_webinar_snapshot.json"))
        if not snaps:
            print("No snapshot in data/. Run pull.py first.", file=sys.stderr)
            return 1
        snap_path = snaps[-1]

    snap = json.loads(snap_path.read_text())
    out = HERE / "index.html"
    out.write_text(build(snap))
    print(f"  {snap_path.name} -> {out.name}  ({out.stat().st_size / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
