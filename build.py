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
import urllib.parse
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

# PBI Brand Standards v1.0 (2023), read from the brand sheet rather than sampled off a
# page: Pantone pairings included so the palette is auditable. Onyx + Orange + White is
# the core logomark colorway; approved background colorways are Onyx, Orange, Burgundy,
# Slate and Livid Grey.
#
# Each colour has one job here, so the page reads as a system rather than as decoration:
#   Orange     the Lead metric and every primary accent
#   Burgundy   the blended Today box, the one panel that mixes two sources
#   Slate      the video block, so format is legible before you read a word
#   Citron     thin-data warnings only, never as a general accent
#   Livid Grey surfaces and rules
BRAND = {
    "onyx": "#212121",       # Pantone 419 C
    "burgundy": "#720C3A",   # Pantone 4074 C
    "orchid": "#B065C0",     # Pantone 2067 C
    "slate": "#38C5BA",      # Pantone 3262 C
    "citron": "#E4E439",     # Pantone 396 C
    "orange": "#FF522B",     # Pantone 172 C
    "livid": "#EAECEF",      # Pantone 649 C
}

# PBI's own lockup, taken from their site: the reversed (white type) cut for the dark
# masthead and the dark cut for print. Both are trimmed to their ink. If these files are
# missing the masthead falls back to type alone rather than shipping a stand-in mark.
LOGO_REVERSED = HERE / "brand" / "pbi-logo-reversed.png"
LOGO_DARK = HERE / "brand" / "pbi-logo.png"


def logo_uri(path):
    if not path.exists():
        return None
    return ("data:image/png;base64,"
            + base64.b64encode(path.read_bytes()).decode("ascii"))


def lockup():
    rev, dark = logo_uri(LOGO_REVERSED), logo_uri(LOGO_DARK)
    if not rev:
        return ('<span class="wordmark"><b>Photography Business Institute</b>'
                '<i>Joy of Marketing</i></span>')
    dark_img = (f'<img class="logo logo-print" src="{dark}" alt="">' if dark else "")
    return (f'<img class="logo logo-screen" src="{rev}" '
            f'alt="Photography Business Institute">{dark_img}')


def short_name(name):
    """Everything after the third pipe.

    Ad names run `Video 06 | AC 2 | H1 | Ad #5`, and the first three segments are the
    same on nearly every ad, so they cost two clamped lines and say nothing. Anything
    with fewer than four segments is left alone rather than truncated to nothing, and a
    trailing segment that is itself empty falls back to the full name.
    """
    parts = (name or "").split("|")
    if len(parts) < 4:
        return (name or "").strip()
    tail = "|".join(parts[3:]).strip()
    return tail or (name or "").strip()


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


def cache_key(url):
    """Identity of a creative, stable across pulls.

    Meta serves the two kinds of asset in opposite shapes:

      scontent-*.fbcdn.net/v/t45.../abc123.png?_nc_sig=...   identity in the PATH,
                                                             rotating signature in the query
      www.facebook.com/ads/image/?d=AQIGr64AC4Ud...          identity in the QUERY,
                                                             path identical for every asset

    Keying on the path alone is right for the first and catastrophic for the second: every
    video poster collapses to one key, so all 23 video ads reused whichever poster was
    downloaded first. Key on whichever part actually identifies the asset.
    """
    p = urllib.parse.urlparse(url)
    if "/ads/image" in p.path:
        d = urllib.parse.parse_qs(p.query).get("d", [""])[0]
        if d:
            return "adsimage:" + d
    return f"{p.scheme}://{p.netloc}{p.path}"


def creative_uri(url):
    if not url:
        return None
    CACHE.mkdir(exist_ok=True)
    dst = CACHE / (hashlib.sha1(cache_key(url).encode()).hexdigest()[:16] + ".jpg")
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


def pct(v, dp=2):
    return "—" if v is None else f"{v:.{dp}f}%"


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
        (n(a["leads"]), "registrations"),
        (money(a["cost_per_lead"], 0), "per registration"),
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
    # The name rides at the top of the card, not under the image. These creatives are
    # 9:16, so a name below a lead card sits a thousand pixels from the thing it names.
    return f"""
      <button class="card" data-ad="{e(a['ad_id'])}"
              aria-label="Open creative and full numbers for {e(a['ad_name'])}">
        <span class="card-head" title="{e(a['ad_name'])}">
          <span class="card-rank">{i + 1:02d}</span>
          <span class="card-name">{e(short_name(a['ad_name']))}</span>
        </span>
        <span class="card-img">{img}{play}</span>
        <span class="card-body">
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
        meta_part = ("Spend and link clicks summed from the ad rows equal the figures Meta "
                     "reports at account level for the same filter and window, exactly.")
    else:
        parts = []
        for k, label in (("spend", "Spend"), ("link_clicks", "Link clicks")):
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
        meta_part = (" ".join(parts) + " Ad rows and account totals differ slightly because an "
                     "ad deleted mid-window still counts at account level while returning no "
                     "ad row." + tail)

    h = r.get("hyros")
    if not h:
        return meta_part
    return (meta_part + f" Registrations are checked the same way inside Hyros: the ad rows sum "
            f"to {n(h['ad_sum'])} against {n(h['campaign_sum'])} at campaign level for the same "
            f"window, a {h['pct']}% gap. Meta's own Lead pixel counted {n(h['meta_pixel'])} over "
            f"the same period, which is the undercount this report exists to correct.")


def bar_path(x, y, w, h, r=4):
    """A bar with only its top corners rounded, so the data end reads as a cap and the
    baseline stays a hard edge. A fully rounded rect would lift the bar off the axis."""
    r = max(0, min(r, w / 2, h))
    return (f"M{x:.2f},{y + h:.2f} L{x:.2f},{y + r:.2f} Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f} "
            f"L{x + w - r:.2f},{y:.2f} Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f} "
            f"L{x + w:.2f},{y + h:.2f} Z")


def daily_chart(daily, key, label, unit, series_class):
    """One measure over time, as bars.

    Two measures on one pair of axes would need two scales, so registrations and spend
    get a chart each rather than a dual axis. One series per chart means no legend is
    needed: the heading names it. Only the latest bar is labelled, with the rest on
    hover, so the axis stays readable as the window grows past a handful of days.
    """
    if not daily:
        return ""
    vals = [d[key] for d in daily]
    top = max(vals) or 1
    n = len(daily)

    W, H = 760.0, 190.0
    PAD_L, PAD_R, PAD_T, PAD_B = 4.0, 4.0, 26.0, 30.0
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    slot = plot_w / n
    bw = min(46.0, slot - 8.0)          # 8px of surface between bars, never fatter than 46

    grid = "".join(
        f'<line class="cgrid" x1="0" x2="{W:.0f}" y1="{PAD_T + plot_h * f:.1f}" '
        f'y2="{PAD_T + plot_h * f:.1f}"/>' for f in (0, .5, 1))

    # Label every date when there is room, otherwise first, last and a middle sample.
    show_every = 1 if n <= 10 else max(2, round(n / 8))

    bars = []
    for i, d in enumerate(daily):
        v = d[key]
        h = (v / top) * plot_h if top else 0
        x = PAD_L + i * slot + (slot - bw) / 2
        y = PAD_T + plot_h - h
        last = i == n - 1
        day = fmt_day(d["date"])
        bars.append(
            f'<g class="cbar{" is-last" if last else ""}" tabindex="0" role="listitem">'
            f'<title>{e(day)}: {e(unit(v))}</title>'
            f'<rect class="chit" x="{PAD_L + i * slot:.2f}" y="{PAD_T:.1f}" '
            f'width="{slot:.2f}" height="{plot_h:.1f}"/>'
            f'<path class="cmark" d="{bar_path(x, y, bw, max(h, 2))}"/>'
            f'<text class="cval" x="{x + bw / 2:.2f}" y="{max(y - 8, 12):.1f}">{e(unit(v))}</text>'
            f'</g>')
        if i % show_every == 0 or last:
            bars.append(
                f'<text class="cday" x="{x + bw / 2:.2f}" y="{H - 10:.1f}">'
                f'{e(day.rsplit(",", 1)[0])}</text>')

    return f"""
      <figure class="chart {series_class}">
        <figcaption>
          <span class="chart-label">{e(label)}</span>
          <span class="chart-peak">peak {e(unit(top))}</span>
        </figcaption>
        <svg viewBox="0 0 {W:.0f} {H:.0f}" role="list" aria-label="{e(label)}">
          {grid}{''.join(bars)}
        </svg>
      </figure>"""


def swatches():
    """The brand palette, with Pantone pairings, as the report's colophon. It doubles as
    the legend for how colour is used above."""
    rows = [
        ("Onyx", "onyx", "419 C", "Ground"),
        ("Orange", "orange", "172 C", "Leads"),
        ("Burgundy", "burgundy", "4074 C", "Blended"),
        ("Slate", "slate", "3262 C", "Video"),
        ("Citron", "citron", "396 C", "Thin data"),
        ("Orchid", "orchid", "2067 C", "Accent"),
        ("Livid Grey", "livid", "649 C", "Surface"),
    ]
    return "".join(f"""
      <div class="sw">
        <span class="sw-chip" style="background:{BRAND[k]}"></span>
        <span class="sw-name">{name}</span>
        <span class="sw-pms">Pantone {pms}</span>
        <span class="sw-use">{use}</span>
      </div>""" for name, k, pms, use in rows)


def summary_box(flag, title, blurb, cells, note, extra_class=""):
    """One blended-registrations panel. Today and the weekly summary are the same object
    with different bounds, so they share this renderer rather than drifting apart."""
    hero = cells[0]
    rest = "".join(f"""
        <div class="tcell">
          <p class="tcell-label">{e(label)}</p>
          <p class="tcell-value">{e(value)}</p>
          <p class="tcell-def">{e(defn)}</p>
        </div>""" for label, value, defn in cells[1:])

    return f"""
  <section class="today {extra_class}">
    <div class="today-head">
      <span class="today-flag">{flag}</span>
      <h2>{e(title)}</h2>
      <p>{blurb}</p>
    </div>
    <div class="today-body">
      <div class="thero">
        <p class="thero-value">{hero[1]}</p>
        <p class="thero-label">{e(hero[0])}</p>
        <p class="thero-def">{e(hero[2])}</p>
      </div>
      <div class="tgrid">{rest}</div>
    </div>
    <p class="tnote">{note}</p>
  </section>"""


def blended_cells(leads, cost_per_lead, link_clicks, cost_per_click, spend, conv,
                  spend_def, have):
    """The six figures, in the order they are argued. Identical for every window."""
    return [
        ("Blended webinar registrations", n(leads) if have else "—", "Hyros, all attributed sources"),
        ("Cost per registration", money(cost_per_lead) if cost_per_lead else "—", "Spend ÷ registrations"),
        ("Link clicks", n(link_clicks), "Outbound to the page"),
        ("Cost per link click", money(cost_per_click), "Spend ÷ link clicks"),
        ("Total ad spend", money(spend, 2), spend_def),
        ("Conversion rate", pct(conv) if conv else "—", "Hyros registrations ÷ link clicks"),
    ]


def today_html(t):
    """Today's blended box. Outside the window tabs because it is always today, whichever
    tab is showing, and visually separated because it mixes two sources."""
    if not t:
        return ""
    hy = t.get("hyros")
    stale = bool(hy and hy.get("stale"))
    have = bool(hy and hy.get("leads"))

    cells = blended_cells(hy["leads"] if have else 0, t.get("cost_per_lead"),
                          t["link_clicks"], t["cost_per_link_click"], t["spend"],
                          t.get("conv_rate"), "Amount spent today", have)

    if not have:
        note = ('<span class="tflag bad">Hyros not connected</span> '
                'Registrations, cost per registration and conversion rate need a PBI-scoped '
                'Hyros API key at <code>PBI 2/hyros_key.txt</code>. Spend and link clicks '
                'above are Meta\'s and are live.')
    elif stale:
        note = (f'<span class="tflag bad">Stale</span> These registration figures are from '
                f'{fmt_day(hy["date"])}, not today. They were seeded through the Hyros MCP and '
                f'cannot refresh on their own; add the API key to make this box live.')
    else:
        note = (f'Registrations come from {e(hy.get("source", "Hyros"))}, and every registration '
                f'figure on this page is built the same way, down to the individual creative. '
                f'Spend and link clicks are Meta\'s.')

    return summary_box(f'Today · {fmt_day(t["date"])}',
                       "Blended webinar registrations",
                       "Every registration Hyros credits today, from every source, against "
                       "today\'s ad spend. This is the number to steer on.",
                       cells, note,
                       "today-stale" if (stale or not have) else "")


def week_html(w):
    """The Monday-to-Monday cycle, in the same format as today."""
    if not w:
        return ""
    have = bool(w.get("leads"))
    span = f'{fmt_day(w["since"])} – {fmt_day(w["until"])}'
    cells = blended_cells(w["leads"], w.get("cost_per_lead"), w["link_clicks"],
                          w.get("cost_per_link_click"), w["spend"], w.get("conv_rate"),
                          "Amount spent this week", have)

    if w["in_progress"]:
        note = (f'<span class="tflag">In progress</span> This week closes on '
                f'{fmt_day(w["until"])}. The figures cover {w["elapsed_days"]} of its '
                f'{w["days"]} days, through {fmt_day(w["api_until"])}, and will keep '
                f'climbing until it closes. Registrations are Hyros; spend and link '
                f'clicks are Meta\'s.')
    else:
        note = (f'A complete cycle: {w["days"]} days, {fmt_day(w["since"])} through '
                f'{fmt_day(w["until"])}. Registrations are Hyros; spend and link clicks '
                f'are Meta\'s.')

    return summary_box(f'Weekly summary · {span}',
                       "Blended webinar registrations",
                       "The funnel\'s own cycle: Monday to Monday, eight days counting both "
                       "ends, so a week opens on one live workshop and closes on the next.",
                       cells, note, "week" + (" week-open" if w["in_progress"] else ""))


def window_html(key, w, cr, active):
    ads = sorted(w["ads"], key=rank_key)
    for a in ads:
        a["thin"] = thin(a)

    # No window-level KPI row. Today's blended box is the one headline on this page:
    # a second row of totals on a different window, counting leads a different way,
    # reads as a contradiction of it rather than as context. The window's totals still
    # appear per campaign and per day below, where their scope is unambiguous.
    t = w["totals"]

    blocks = ""
    thin_flagged = False
    for fmt, title, blurb in (
        ("IMAGE", "Best images", "The strongest still creatives, ranked by the same Hyros "
                                 "registrations reported at the top of this page, then by cost "
                                 "per registration."),
        ("VIDEO", "Best videos", "The strongest video creatives, ranked on exactly the same five "
                                 "Hyros-based metrics as the images, so the two formats are "
                                 "directly comparable."),
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
    <section class="{'sec-video' if fmt == 'VIDEO' else 'sec-image'}">
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
                     f'behind them. Their cost per registration is a single event, not a rate. Read them as '
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

    days = (daily_chart(w["daily"], "leads", "Registrations per day", n, "s-reg")
            + daily_chart(w["daily"], "spend", "Ad spend per day",
                          lambda v: money(v, 0), "s-spend"))

    # A proportion bar behind the registration count: the column is the one thing being
    # ranked on, and 64 rows of bare numerals hide where the mass actually sits.
    top_leads = max((a["leads"] for a in ads), default=0) or 1
    table_rows = "".join(f"""
      <tr{' class="zero"' if not a['leads'] else ''}>
        <td class="cell-thumb">{thumb_button(a, cr)}</td>
        <td class="name" title="{e(a['ad_name'])}">
          <span class="ad-name">{e(short_name(a['ad_name']))}<span class="fmt fmt-{a.get('format','IMAGE').lower()}">{'Video' if a.get('format') == 'VIDEO' else 'Image'}</span></span>
          <span class="ad-set">{e(a['adset_name'])}</span>
        </td>
        <td class="num strong bar-cell">
          <span class="rbar" style="--w:{a['leads'] / top_leads * 100:.1f}%"></span>
          <span class="rval">{n(a['leads'])}</span>
        </td>
        <td class="num">{money(a['cost_per_lead'])}</td>
        <td class="num">{n(a['link_clicks'])}</td>
        <td class="num">{money(a['cost_per_link_click'])}</td>
        <td class="num">{money(a['spend'])}</td>
      </tr>""" for a in ads)

    lead_ads = sum(1 for a in ads if a["leads"])
    zero_spend = money(sum(a["spend"] for a in ads if not a["leads"]))

    return f"""
  <div class="win" id="win-{key}" data-label="{e(w['label'])}"{'' if active else ' hidden'}>
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
            <th class="l">Campaign</th><th>Registrations</th><th>Cost / reg.</th>
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
           {lead_ads} have produced a registration so far; {zero_spend} of spend sits on ads
           that have not. Click a thumbnail to open the creative.</p>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr>
            <th class="l"></th><th class="l">Ad</th><th>Registrations</th><th>Cost / reg.</th>
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

    # Shortest first, so the tabs read as a widening lens rather than an arbitrary set.
    order = [k for k in ("3d", "7d", "launch") if k in snap["windows"]] \
        or list(snap["windows"])
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
                ["Registrations", n(a["leads"])],
                ["Cost per registration", money(a["cost_per_lead"])],
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
        onyx=BRAND["onyx"], orange=BRAND["orange"], burgundy=BRAND["burgundy"],
        orchid=BRAND["orchid"], slate=BRAND["slate"], citron=BRAND["citron"],
        livid=BRAND["livid"],
        title=f"Weekly Webinar Performance — {m['short_name']}",
        client=e(m["client"]), account=e(m["account_label"]), account_id=e(m["account_id"]),
        stamp=e(fmt_stamp(m["pulled_at"], m.get("timezone_abbrev", ""))),
        live_count=len(live), matched=len(m["campaigns_matched"]),
        lead_action=e(m["lead_action"]),
        tabs=tabs, windows=wins,
        today=today_html(snap.get("today")) + week_html(snap.get("week")),
        logomark=lockup(), swatches=swatches(),
        thin_spend=money(THIN_SPEND, 0), thin_clicks=THIN_LINK_CLICKS,
        featured_n=FEATURED,
        payload=json.dumps(payload, separators=(",", ":")),
        thumbs=json.dumps(thumbs, separators=(",", ":")),
        default=default,
        build_stamp=e(fmt_stamp(m["pulled_at"], m.get("timezone_abbrev", ""))),
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, noimageindex">
<title>{title}</title>
<style>
{faces}
:root {{
  /* PBI Brand Standards v1.0 (2023), verbatim */
  --onyx: {onyx};
  --burgundy: {burgundy};
  --orchid: {orchid};
  --slate: {slate};
  --citron: {citron};
  --brand-orange: {orange};
  --livid: {livid};

  --accent: {orange};
  --accent-ink: #B8330F;
  --video: #0F8F86;      /* Slate, darkened for text contrast on white */
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
    --band-ink-2: #9A918C; --watch: {citron}; --video: {slate};
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
  }}
}}
:root[data-theme="dark"] {{
  --ground: #171514; --surface: #201D1C; --sunk: #2A2624; --rule: #37312E;
  --rule-soft: #2C2725; --ink: #F2EDEB; --ink-2: #BDB4AF; --ink-3: #8B817C;
  --accent: #FF6E4C; --accent-ink: #FF8A6B; --band: #100E0D; --band-ink: #F7F3F1;
  --band-ink-2: #9A918C; --watch: {citron}; --video: {slate};
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
/* Onyx ground with a burgundy cast: both are approved background colorways, and the
   pair gives the band depth without introducing a colour the brand does not own. */
.band {{
  background:
    radial-gradient(120% 150% at 88% -20%, color-mix(in srgb, var(--burgundy) 60%, transparent) 0%, transparent 62%),
    linear-gradient(180deg, color-mix(in srgb, var(--onyx) 92%, #000) 0%, var(--band) 100%);
  color: var(--band-ink); padding: 30px 0 40px; position: relative; overflow: hidden;
}}
/* The full palette as a hairline across the very top of the page. It sits above the
   band rather than below it, where the hero panel tucks up and would cover it. */
.band::before {{
  content: ''; position: absolute; left: 0; right: 0; top: 0; height: 4px; z-index: 2;
  background: linear-gradient(90deg,
    var(--brand-orange) 0 28%, var(--burgundy) 28% 46%, var(--orchid) 46% 60%,
    var(--slate) 60% 76%, var(--citron) 76% 88%, var(--livid) 88% 100%);
}}
.band .wrap {{ padding-bottom: 0; position: relative; z-index: 1; }}

.lockup {{ display: flex; align-items: center; gap: 18px; margin: 0 0 26px; flex-wrap: wrap; }}
/* PBI's own lockup, at its native 1.81:1. The reversed cut carries the dark masthead;
   the dark cut is swapped in for print, where the ground becomes white. */
.logo {{ display: block; height: 66px; width: auto; flex: 0 0 auto; }}
@media (max-width: 560px) {{ .logo {{ height: 54px; }} }}
.logo-print {{ display: none; }}
.wordmark {{ display: flex; flex-direction: column; line-height: 1.15; }}
.wordmark b {{
  font-family: 'Playfair PBI', Georgia, serif; font-weight: 700; font-size: 16.5px;
  letter-spacing: .005em; color: #fff;
}}
.wordmark i {{
  font-style: normal; font-size: 10.5px; font-weight: 600; letter-spacing: .2em;
  text-transform: uppercase; color: var(--brand-orange); margin-top: 3px;
}}
.lockup-rule {{ flex: 1 1 24px; height: 1px; background: rgba(255,255,255,.18); min-width: 24px; }}
.lockup-tag {{
  font-size: 10px; font-weight: 600; letter-spacing: .19em; text-transform: uppercase;
  color: var(--band-ink-2); border: 1px solid rgba(255,255,255,.22);
  padding: 6px 12px; border-radius: 2px; white-space: nowrap;
}}
.band h1 {{
  font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(34px, 6.2vw, 62px); line-height: .98; margin: 0 0 20px;
  letter-spacing: -.015em; text-wrap: balance;
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

/* ---------- controls ---------- */
.controls {{
  display: flex; align-items: center; justify-content: flex-end;
  gap: 16px; margin: 22px 0 0; flex-wrap: wrap;
}}
.refresh {{
  appearance: none; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer;
  display: inline-flex; align-items: center; gap: 10px;
  padding: 13px 26px; border-radius: 3px; border: 0;
  background: var(--accent); color: #fff; letter-spacing: .01em;
  box-shadow: 0 2px 0 rgba(0,0,0,.18); transition: filter .15s ease, transform .1s ease;
}}
.refresh:hover {{ filter: brightness(1.08); }}
.refresh:active {{ transform: translateY(1px); box-shadow: 0 1px 0 rgba(0,0,0,.18); }}
.refresh:focus-visible {{ outline: 2px solid #fff; outline-offset: 2px; }}
.refresh[disabled] {{ opacity: .62; cursor: default; filter: none; transform: none; }}
.refresh-ico {{
  width: 15px; height: 15px; border-radius: 50%; flex: 0 0 auto;
  border: 2.5px solid currentColor; border-top-color: transparent;
}}
/* Static until it is actually working, so the ring does not imply a pull that is not
   happening. */
.refresh.busy .refresh-ico {{ animation: spin .8s linear infinite; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.refresh-msg {{
  margin: 12px 0 0; font-size: 13px; color: var(--band-ink-2);
  border-left: 2px solid var(--accent); padding-left: 12px; max-width: 76ch;
}}
.refresh-msg a {{ color: var(--band-ink); }}
.refresh-msg.bad {{ border-left-color: #E5484D; }}

/* ---------- window tabs ---------- */
.winbar {{
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin: 40px 0 0; padding-bottom: 14px; border-bottom: 1px solid var(--rule);
}}
.winbar-label {{
  font-size: 10.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase;
  color: var(--ink-3);
}}
.tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.tab {{
  appearance: none; font: inherit; font-size: 13px; font-weight: 500; cursor: pointer;
  padding: 8px 16px; border-radius: 2px; border: 1px solid var(--rule);
  background: var(--surface); color: var(--ink-2);
}}
.tab:hover {{ color: var(--ink); border-color: var(--ink-3); }}
.tab.on {{
  background: var(--onyx); border-color: var(--onyx); color: #fff; font-weight: 600;
}}
@media (prefers-color-scheme: dark) {{ .tab.on {{ background: var(--accent); border-color: var(--accent); }} }}
:root[data-theme="dark"] .tab.on {{ background: var(--accent); border-color: var(--accent); }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* ---------- kpi row ---------- */
.kpis {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin: 44px 0 0; }}
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

/* ---------- today / blended box ---------- */
/* Deliberately unlike the KPI row above it: this box is a single day and mixes two
   sources, so it should not read as more of the same window totals. */
/* The one hero on the page. Burgundy is an approved background colorway, so going solid
   here makes today unmistakable against the light report below without inventing a
   colour. It tucks up under the masthead so it reads as part of the header block. */
.today {{
  margin-top: -26px; border-radius: 4px; overflow: hidden;
  background: linear-gradient(155deg, var(--burgundy) 0%, color-mix(in srgb, var(--burgundy) 72%, var(--onyx)) 100%);
  color: #fff; box-shadow: 0 3px 0 rgba(0,0,0,.16), 0 18px 44px -22px rgba(114,12,58,.7);
}}
.today-stale {{
  background: linear-gradient(155deg, #4A4A4A 0%, var(--onyx) 100%);
}}
/* The weekly box is the same object over a longer bound, so it keeps the format and
   drops only the masthead tuck: it sits in the flow under today, not under the band. */
.today.week {{ margin-top: 20px; }}
.week .today-flag {{ background: var(--onyx); color: #fff; }}
.week-open .today-flag {{ background: var(--citron); color: #3A3000; }}
.today-head {{ padding: 26px 28px 22px; }}
.today-flag {{
  display: inline-block; margin-bottom: 13px; padding: 6px 13px; border-radius: 2px;
  background: var(--brand-orange); color: #fff;
  font-size: 10.5px; font-weight: 700; letter-spacing: .17em; text-transform: uppercase;
}}
.today-stale .today-flag {{ background: var(--citron); color: #3A3000; }}
.today-head h2 {{
  font-size: clamp(26px, 3.6vw, 38px); line-height: 1.05; margin: 0; color: #fff;
  letter-spacing: -.01em;
}}
.today-head h2::before {{ display: none; }}
.today-head p {{ margin: 10px 0 0; font-size: 14px; color: rgba(255,255,255,.74); max-width: 64ch; }}

.today-body {{
  display: grid; grid-template-columns: minmax(230px, 1fr) 2.4fr; gap: 0;
  border-top: 1px solid rgba(255,255,255,.15);
}}
.thero {{
  padding: 26px 28px 28px; border-right: 1px solid rgba(255,255,255,.15);
  background: rgba(0,0,0,.16);
}}
.thero-value {{
  margin: 0; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(64px, 9vw, 104px); line-height: .86; color: var(--citron);
  font-variant-numeric: tabular-nums; letter-spacing: -.02em;
}}
.today-stale .thero-value {{ color: rgba(255,255,255,.5); }}
.thero-label {{
  margin: 14px 0 0; font-size: 12px; font-weight: 700; letter-spacing: .13em;
  text-transform: uppercase; color: #fff; line-height: 1.4;
}}
.thero-def {{ margin: 6px 0 0; font-size: 12px; color: rgba(255,255,255,.66); }}

/* auto-fit rather than a fixed five: the five cells sit in one row when there is room
   and reflow evenly when there is not, instead of leaving a hole in a 3+2 split. */
.tgrid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 0;
  padding: 0;
}}
.tcell {{
  padding: 24px 20px 26px; border-right: 1px solid rgba(255,255,255,.13);
}}
.tcell:last-child {{ border-right: 0; }}
.tcell-label {{
  margin: 0 0 10px; font-size: 10px; font-weight: 600; letter-spacing: .12em;
  text-transform: uppercase; color: rgba(255,255,255,.62); line-height: 1.35;
}}
.tcell-value {{
  margin: 0; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(24px, 2.6vw, 34px); line-height: 1; color: #fff;
  font-variant-numeric: tabular-nums;
}}
.tcell-def {{ margin: 8px 0 0; font-size: 11px; color: rgba(255,255,255,.55); line-height: 1.4; }}
.tnote {{
  margin: 0; padding: 16px 28px 20px; background: rgba(0,0,0,.24);
  font-size: 12px; color: rgba(255,255,255,.7); max-width: none;
}}
.tnote code {{ color: rgba(255,255,255,.9); }}
.tflag {{
  display: inline-block; margin-right: 7px; padding: 2px 8px; border-radius: 2px;
  font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  background: var(--citron); color: #3A3000;
}}

/* ---------- sections ---------- */
/* Numbered like a printed report: the counter gives the page a spine to read down,
   and it makes any single section citable in an email. */
.win {{ counter-reset: sec; }}
section {{ margin-top: 56px; }}
.sec-head {{ margin-bottom: 4px; position: relative; }}
.sec-head h2 {{ display: flex; align-items: baseline; gap: 14px; }}
.sec-head h2::before {{
  counter-increment: sec; content: counter(sec, decimal-leading-zero);
  font-family: 'Work Sans PBI', sans-serif; font-size: 12px; font-weight: 600;
  letter-spacing: .12em; color: var(--accent); flex: 0 0 auto;
  transform: translateY(-2px);
}}
.sec-video .sec-head h2::before {{ color: var(--video); }}
/* A hairline across the full measure, under every section heading. */
.sec-head::after {{
  content: ''; display: block; height: 1px; background: var(--rule);
  margin: 16px 0 0;
}}
.sec-head p {{ margin: 8px 0 0; color: var(--ink-2); font-size: 14px; max-width: 68ch; }}

/* Video cards carry Slate so the two blocks are distinguishable at a glance. */
.sec-video .card:hover, .sec-video .card:focus-visible {{ border-color: var(--slate); }}
.sec-video .card-rank {{ color: var(--video); }}
.sec-video .stat:first-child b {{ color: var(--video); }}
.note {{
  margin: 18px 0 0; padding: 12px 14px; background: var(--sunk);
  border-left: 2px solid var(--accent); border-radius: 2px;
  font-size: 13px; color: var(--ink-2); max-width: 78ch;
}}

/* ---------- featured cards ---------- */
/* Every creative the same size. Rank is carried by the number in the header and by
   reading order, not by area: sizing one card bigger made the section about the layout
   rather than about comparing five ads. auto-fit keeps them equal at any width. */
.cards {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px; margin-top: 20px;
}}
.card {{
  display: flex; flex-direction: column; position: relative;
  appearance: none; text-align: left; font: inherit; color: inherit; cursor: pointer;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 3px;
  padding: 0; overflow: hidden; box-shadow: var(--shadow);
  transition: transform .16s ease, border-color .16s ease;
}}
.card:hover, .card:focus-visible {{ transform: translateY(-3px); border-color: var(--accent); }}
.card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.card-head {{
  display: flex; align-items: baseline; gap: 10px; flex: 0 0 auto;
  padding: 12px 14px 11px; border-bottom: 1px solid var(--rule-soft);
  background: var(--sunk);
}}
.card-rank {{
  flex: 0 0 auto; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: 15px; line-height: 1; color: var(--accent-ink);
}}
.card-img {{
  display: block; position: relative; background: var(--sunk); aspect-ratio: 4 / 5;
  overflow: hidden; flex: 0 0 auto;
}}
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
.card-body {{ display: block; padding: 12px 15px 15px; flex: 0 0 auto; }}
/* Two lines, always. These names run to 45 characters and a third line on one card
   pushed its whole creative out of step with the four beside it. */
.card-name {{
  font-weight: 600; font-size: 13px; line-height: 1.35; overflow-wrap: anywhere;
  color: var(--ink); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; min-height: 2.7em;
}}
.card-set {{
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--ink-3);
}}
/* Fit the stats to the card, not to a fixed column count. Locked at three columns, a
   card narrow enough to sit five-across gave each stat about 57px and clipped the third
   one against the card's overflow:hidden edge. auto-fit drops to two columns instead. */
.card-stats {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(78px, 1fr)); gap: 12px 10px;
  margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--rule-soft);
}}
/* Grid items default to min-width:auto and refuse to shrink below their longest word,
   which is what pushed the row past the card edge rather than wrapping it. */
.stat {{ display: block; min-width: 0; }}
.stat b {{
  display: block; font-family: 'Playfair PBI', Georgia, serif; font-weight: 700;
  font-size: clamp(15px, 1.35vw, 19px); line-height: 1.1; overflow-wrap: anywhere;
}}
.stat:first-child b {{ color: var(--accent-ink); }}
.stat i {{
  display: block; margin-top: 2px; font-style: normal; font-size: 10px;
  letter-spacing: .07em; text-transform: uppercase; color: var(--ink-3);
  overflow-wrap: anywhere; line-height: 1.3;
}}
.chip {{
  display: inline-block; padding: 2px 7px; border-radius: 2px;
  font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
}}
/* Citron is the brand's warning colour and is used for nothing else on this page. */
.chip-thin {{
  background: color-mix(in srgb, var(--citron) 30%, transparent);
  color: #6B5A00; border: 1px solid color-mix(in srgb, var(--citron) 55%, transparent);
}}
@media (prefers-color-scheme: dark) {{ .chip-thin {{ color: var(--citron); }} }}
:root[data-theme="dark"] .chip-thin {{ color: var(--citron); }}
.fmt {{
  display: inline-block; margin-left: 8px; padding: 1px 6px; border-radius: 2px;
  font-size: 9.5px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  vertical-align: 1px; border: 1px solid var(--rule); color: var(--ink-3);
}}
.fmt-video {{ border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent-ink); }}

/* ---------- daily charts ---------- */
/* Two charts, never two y-axes. Registrations and spend are different scales, so they
   get a chart each; one series per chart means the heading is the legend.
   Mark colours are brand hues re-stepped to pass the palette validator against each
   surface: the raw brand Slate is 2.07:1 on white and Citron 1.32:1, both unusable as
   marks. Light #E8431B / #00918A and dark #DE5433 / #12A296 pass every check. */
.days {{ display: grid; gap: 18px; margin-top: 22px; }}
.chart {{
  margin: 0; background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 16px 18px 10px;
  --mark: {orange};
}}
.s-reg {{ --mark: #E8431B; }}
.s-spend {{ --mark: #00918A; }}
@media (prefers-color-scheme: dark) {{
  .s-reg {{ --mark: #DE5433; }} .s-spend {{ --mark: #12A296; }}
}}
:root[data-theme="dark"] .s-reg {{ --mark: #DE5433; }}
:root[data-theme="dark"] .s-spend {{ --mark: #12A296; }}
.chart figcaption {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 14px;
  margin-bottom: 6px;
}}
.chart-label {{
  font-size: 11px; font-weight: 600; letter-spacing: .13em; text-transform: uppercase;
  color: var(--ink-2);
}}
.chart-peak {{ font-size: 11px; color: var(--ink-3); font-variant-numeric: tabular-nums; }}
/* Scale uniformly. preserveAspectRatio="none" would fit the box exactly but stretch the
   axis labels and value text horizontally at every width except 760px. */
.chart svg {{ display: block; width: 100%; height: auto; }}
.cgrid {{ stroke: var(--rule-soft); stroke-width: 1; vector-effect: non-scaling-stroke; }}
.cmark {{ fill: var(--mark); transition: opacity .12s ease; }}
.chit {{ fill: transparent; }}
.cday {{
  fill: var(--ink-3); font-size: 11px; text-anchor: middle;
  font-family: 'Work Sans PBI', sans-serif;
}}
/* Only the newest bar carries a standing value; the rest surface on hover or focus, so
   the axis does not turn into a wall of numerals as the window grows. */
.cval {{
  fill: var(--ink); font-size: 12.5px; font-weight: 600; text-anchor: middle;
  font-family: 'Work Sans PBI', sans-serif; font-variant-numeric: tabular-nums;
  opacity: 0; transition: opacity .12s ease;
}}
.cbar.is-last .cval {{ opacity: 1; }}
.cbar:hover .cval, .cbar:focus-visible .cval {{ opacity: 1; }}
.cbar:hover .cmark {{ opacity: .78; }}
.cbar:focus {{ outline: none; }}
.cbar:focus-visible .cmark {{ opacity: .78; }}
.cbar:focus-visible .chit {{
  fill: color-mix(in srgb, var(--mark) 10%, transparent);
}}

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
/* Sequential single hue behind the ranked column: 64 rows of bare numerals hide where
   the mass sits. The bar is decoration under a value that is always readable on its own. */
.bar-cell {{ position: relative; }}
.rbar {{
  position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
  width: var(--w); max-width: calc(100% - 20px); height: 20px; border-radius: 2px;
  background: color-mix(in srgb, var(--accent) 26%, transparent);
}}
tr.zero .rbar {{ display: none; }}
.rval {{ position: relative; }}
td.name {{ min-width: 240px; }}
.ad-name {{ display: block; font-weight: 500; overflow-wrap: anywhere; }}
.ad-set {{ display: block; font-size: 11.5px; color: var(--ink-3); margin-top: 1px; }}
.cell-thumb {{ width: 60px; padding-right: 0; }}
/* Portrait box, and contain rather than cover, for the same reason the cards use it:
   these creatives are 9:16, and a square crop reduces a row to a slab of background
   colour that looks identical to the row above it. */
.thumb {{
  display: block; position: relative; width: 42px; height: 56px; padding: 0;
  border-radius: 2px; overflow: hidden; border: 1px solid var(--rule);
  background: var(--sunk); cursor: pointer; appearance: none;
}}
.thumb img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
.thumb[data-video]::after {{
  content: ''; position: absolute; inset: 0; margin: auto; width: 0; height: 0;
  border-style: solid; border-width: 7px 0 7px 11px;
  border-color: transparent transparent transparent #fff;
  filter: drop-shadow(0 0 3px rgba(0,0,0,.95));
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

/* ---------- colophon ---------- */
.colophon {{
  margin-top: 44px; padding: 26px 0 0; border-top: 2px solid var(--onyx);
}}
@media (prefers-color-scheme: dark) {{ .colophon {{ border-top-color: var(--rule); }} }}
:root[data-theme="dark"] .colophon {{ border-top-color: var(--rule); }}
.colophon h3 {{
  margin: 0 0 16px; font-size: 10.5px; font-weight: 600; letter-spacing: .16em;
  text-transform: uppercase; color: var(--ink-2);
}}
.swatches {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; }}
.sw {{ display: flex; flex-direction: column; gap: 2px; }}
.sw-chip {{
  display: block; height: 34px; border-radius: 2px; margin-bottom: 7px;
  border: 1px solid rgba(0,0,0,.12);
}}
.sw-name {{ font-size: 11.5px; font-weight: 600; color: var(--ink); }}
.sw-pms {{ font-size: 10.5px; color: var(--ink-3); }}
.sw-use {{
  font-size: 9.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-3); margin-top: 3px;
}}
.colo-foot {{
  display: flex; justify-content: space-between; gap: 18px; flex-wrap: wrap;
  margin: 22px 0 0; padding-top: 16px; border-top: 1px solid var(--rule-soft);
  font-size: 11.5px; color: var(--ink-3);
}}

@media (max-width: 1080px) {{
  .kpis {{ grid-template-columns: repeat(3, 1fr); }}
  .today-body {{ grid-template-columns: 1fr; }}
  .thero {{ border-right: 0; border-bottom: 1px solid rgba(255,255,255,.15); }}
}}
@media (max-width: 980px) {{
  .kpis {{ grid-template-columns: repeat(2, 1fr); margin-top: 32px; }}
  .cards {{ grid-template-columns: repeat(2, 1fr); }}
  .lb-panel {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 560px) {{
  .kpis {{ grid-template-columns: 1fr; }}
  .cards {{ grid-template-columns: 1fr; }}
  .card-stats {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}

/* ---------- print ---------- */
/* Clients print these and hand them round a room. Without this the dark panels burn a
   cartridge, the tabs print as dead buttons, and the hidden window vanishes silently. */
@media print {{
  @page {{ margin: 14mm; }}
  body {{ background: #fff; color: #000; font-size: 10.5pt; }}
  .band {{
    background: #fff !important; color: #000; padding: 0 0 12pt; border-bottom: 2pt solid #000;
  }}
  .band::before {{ display: none; }}
  .wordmark b, .band h1, .band-meta b {{ color: #000; }}
  .band-meta, .lockup-tag {{ color: #333; }}
  .logo-screen {{ display: none; }}
  .logo-print {{ display: block; }}
  .lockup-rule {{ background: #ccc; }}
  .controls, .refresh, .refresh-msg, .winbar, .lb {{ display: none !important; }}
  /* Print every window, each labelled, rather than only whichever tab was open. */
  .win[hidden] {{ display: block !important; }}
  .win::before {{
    content: 'Window: ' attr(data-label); display: block; margin: 18pt 0 6pt;
    font-size: 9pt; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
  }}
  .today {{
    background: #fff !important; color: #000; border: 1.5pt solid #000; box-shadow: none;
  }}
  .today-head h2, .tcell-value, .thero-label {{ color: #000; }}
  .today-head p, .tcell-label, .tcell-def, .tnote {{ color: #333; }}
  .thero-value {{ color: #000; }}
  .thero, .tnote {{ background: #fff !important; }}
  .today-flag {{ background: #000 !important; color: #fff !important; }}
  .card, .chart, .tablewrap, .kpi {{
    box-shadow: none; border: .75pt solid #999; break-inside: avoid;
  }}
  section, figure, tr {{ break-inside: avoid; }}
  h2 {{ break-after: avoid; }}
  thead {{ display: table-header-group; }}
  a[href^="http"]::after {{ content: ''; }}
}}
</style>
</head>
<body>

<header class="band">
  <div class="wrap">
    <div class="lockup">
      {logomark}
      <span class="lockup-rule"></span>
      <span class="lockup-tag">Paid Media Report</span>
    </div>
    <h1>Weekly Webinar<br>Performance</h1>
    <p class="band-meta">
      <span>Account <b>{account}</b> · {account_id}</span>
      <span>Campaigns <b>{live_count} active</b> of {matched} webinar-named</span>
      <span class="live">Updated <b>{stamp}</b></span>
    </p>
    <div class="controls">
      <button class="refresh" id="refresh" type="button">
        <span class="refresh-ico" aria-hidden="true"></span>
        <span id="refresh-text">Refresh</span>
      </button>
    </div>
    <p class="refresh-msg" id="refresh-msg" hidden></p>
  </div>
</header>

<div class="wrap">
{today}

  <!-- Below the hero on purpose: these tabs change the creative and tables underneath,
       never today's blended box, and sitting in the masthead made them look global. -->
  <div class="winbar">
    <span class="winbar-label">Creative performance over</span>
    <div class="tabs">{tabs}</div>
  </div>

{windows}

  <div class="method">
    <h3>How to read this</h3>
    <ul>
      <li><b>Five metrics, everywhere.</b> Registrations, cost per registration, link clicks,
          cost per link click, and total spent. Every ranking on this page uses them and
          nothing else, so a video and a still are judged on the same terms.</li>
      <li><b>One registration number, everywhere.</b> Every registration figure on this page,
          from the headline down to a single creative, is the <b>Hyros</b> count under
          last-click attribution. Meta's own Lead pixel is not used for any ranking: it sees
          only the registrations it can match itself, and it undercounts this funnel by
          roughly three to one. Using both would put two different answers on one page.</li>
      <li><b>Scope.</b> Every campaign in Meta ad account {account_id} whose name contains
          "webinar", matched by name rather than by a fixed ID list so next week's campaign is
          picked up without editing anything. {matched} campaigns match; {live_count} are
          active.</li>
      <li><b>Spend and link clicks are Meta's.</b> Cost per registration pairs Meta's spend
          with Hyros's registrations, the same way at every level of the page. Link clicks are
          the clicks that actually left for the landing page: all-clicks runs about 2.4x
          higher on this account and would flatter both the count and the cost.</li>
      <li><b>Ranking.</b> Registrations first, then cost per registration, then spend. Volume
          outranks efficiency on purpose: a single registration on a few dollars produces a
          spectacular cost and proves nothing. Images and videos are ranked in separate blocks
          so the top {featured_n} of each format is always visible, even when one format
          dominates.</li>
      <li><b>Rates are recomputed from summed components</b>, never averaged across ads, so a
          two-dollar ad cannot weigh as much as a two-hundred-dollar one.</li>
      <li><b>Thin data.</b> Ads under {thin_spend} spent or under {thin_clicks} link clicks are
          flagged. Their cost per registration is one event, not a rate.</li>
      <li><b>Source.</b> Meta Graph API v21.0 and the Hyros attribution API, both read-only.
          All Meta figures are on the ad account's own clock, America/Los_Angeles. Creatives
          are downloaded and embedded because Meta's image links are signed and expire.
          Nothing on this page is estimated or inferred.</li>
    </ul>
  </div>

  <div class="colophon">
    <h3>Brand palette · PBI Brand Standards v1.0</h3>
    <div class="swatches">{swatches}</div>
    <div class="colo-foot">
      <span>Photography Business Institute® · Onyx, Orange &amp; White are the core logomark colorways</span>
      <span>Set in Playfair Display &amp; Work Sans, standing in for Knockout, Gunterz &amp; Optika</span>
    </div>
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
const BUILD_STAMP = "{build_stamp}";

// The Refresh button has two jobs because the page has two homes.
//
// Served by serve.py, POST /refresh re-pulls Meta, rebuilds and publishes: one click,
// a few seconds, done. The published copy on GitHub Pages is a static file with no way
// to reach Meta, and giving it one would mean shipping an ads token into a public page,
// so there the button re-checks for a newer published build; a launchd job on the Mac
// republishes hourly, so a newer build is normally at most an hour away.
const btn = document.getElementById('refresh');
const btnText = document.getElementById('refresh-text');
const msgEl = document.getElementById('refresh-msg');
let polling = null;

function msg(text, bad) {{
  msgEl.innerHTML = text;
  msgEl.hidden = !text;
  msgEl.classList.toggle('bad', !!bad);
}}
function busy(on, label) {{
  btn.classList.toggle('busy', on);
  btn.disabled = on;
  btnText.textContent = label || 'Refresh';
}}

async function refresh() {{
  if (location.protocol === 'file:') {{
    msg('This is a saved copy of the page, so the button has nothing to talk to. ' +
        'Run <code>python3 serve.py</code> in the dashboard folder for a working ' +
        'Refresh, or open the published page.', true);
    return;
  }}

  busy(true, 'Refreshing');
  msg('Pulling the account from Meta, rebuilding and publishing. Takes a few seconds.');

  let res;
  try {{
    res = await fetch('refresh', {{ method: 'POST' }});
  }} catch (err) {{
    checkForNewer();
    return;
  }}

  if (res.status === 404 || res.status === 405 || res.status === 501) {{
    checkForNewer();
    return;
  }}

  let body = {{}};
  try {{ body = await res.json(); }} catch (err) {{ /* non-JSON: fall through to status */ }}

  if (res.ok && body.ok) {{
    busy(true, 'Reloading');
    location.reload();
  }} else {{
    busy(false);
    msg('Refresh failed: ' + (body.error || ('HTTP ' + res.status)) +
        '. The numbers below are unchanged, from ' + BUILD_STAMP + '.', true);
  }}
}}

// Published copy: ask GitHub for this same page again, bypassing the cache, and reload
// if a newer pull has been published since. Pages can lag a push by up to a minute, so
// "no change yet" is a real answer and is reported as one rather than as a failure.
async function checkForNewer() {{
  busy(true, 'Checking');
  try {{
    const r = await fetch(location.pathname + '?v=' + Date.now(), {{ cache: 'no-store' }});
    const html = await r.text();
    const m = html.match(/const BUILD_STAMP = "([^"]*)"/);
    if (m && m[1] && m[1] !== BUILD_STAMP) {{
      busy(true, 'Reloading');
      location.reload();
      return;
    }}
  }} catch (err) {{ /* offline or mid-deploy; fall through to the message */ }}

  busy(false);
  msg('You are seeing the newest published pull, from <b>' + BUILD_STAMP + '</b>. ' +
      'The page republishes itself about once an hour, so check back shortly and ' +
      'press Refresh again to pick up the next pull.<br><br>' +
      'The published copy cannot reach Meta from your browser: doing so would mean ' +
      'putting the ad account token into a public page. If a pull is needed sooner, ' +
      'run the <b>refresh</b> workflow from the repo\\'s Actions tab (works from any ' +
      'device with repo access), or <code>refresh.command</code> on the Mac.');
}}

btn.addEventListener('click', refresh);

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
