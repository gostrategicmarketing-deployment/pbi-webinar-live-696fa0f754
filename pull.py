#!/usr/bin/env python3
"""
Pull the webinar-campaign slice of the Joy of Marketing Meta ad account (act_37394393).

Scope is name-based on purpose: every campaign in the account whose name contains
"webinar" is in, everything else is out. That keeps the dashboard correct when PBI
spins up next week's webinar campaign without anyone editing an ID list here.

Two windows are pulled every run and both land in one snapshot:
  launch  since the weekly program started (WINDOW_START), the all-time view
  7d      the trailing seven days, matching the weekly webinar cadence

Reported metrics are fixed at five: leads, cost per lead, link clicks, cost per
link click, and total spent. Nothing else is collected or shown.

Reads the Meta Graph API with the account's ads_read token. Writes one snapshot per
run into data/, stamped to the hour, keeping the most recent KEEP_SNAPSHOTS.

    python3 pull.py                      # both windows
    python3 pull.py 2024-05-01 2024-11-01 # one explicit window instead
"""

import json
import os
import random
import subprocess
import sys
from time import sleep
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TOKEN_FILE = Path("/Users/philglutting/Documents/Claude/Projects/PBI 2/fb_token.txt")
ACCOUNT = "act_37394393"
ACCOUNT_LABEL = "Joy of Marketing"
API = "https://graph.facebook.com/v21.0"

# Meta buckets every figure by the ad account's own clock, so the dashboard reports
# in that clock too. Reading it in UTC would silently shift the day boundary and make
# "today" wrong for seven hours out of every twenty-four.
ACCOUNT_TZ = ZoneInfo("America/Los_Angeles")

# The current weekly-webinar program launched 2026-08-11. Older webinar campaigns in
# this account stop in Oct 2024, and their creative and CPLs come from a different era,
# so the default window starts at the live program rather than sweeping the archive in.
WINDOW_START = "2026-08-11"

CAMPAIGN_MATCH = "webinar"

# A webinar registration reaches PBI down one of two paths, and Meta reports them as two
# different actions. Both are counted, and both are carried separately so the page can
# show its own arithmetic:
#
#   lead form   on-Meta instant form. The person never leaves Facebook, so no pixel and no
#               funnel page view is involved. This is the exact count Meta itself reports
#               as "Lead (form)" and it is the figure PBI treats as ground truth.
#   page opt-in the ad sends the person to the GoHighLevel funnel and they fill in the
#               "Opt in v2" step. Meta learns about it from the pixel.
#
# Reading only the pixel event, as this file did until 2026-09-07, scored every lead-form
# ad as zero: the lead-form campaigns emit `onsite_conversion.lead_grouped` and never
# `offsite_conversion.fb_pixel_lead`. That silently erased what is now the larger half of
# the program (196 of 358 registrations over 2026-08-28..09-06).
LEAD_FORM_ACTION = "onsite_conversion.lead_grouped"
PAGE_OPTIN_ACTION = "offsite_conversion.fb_pixel_lead"

# The pixel on the funnel page was repaired on 2026-08-27 (see ../2026-08-27 - Pixel
# Events/). Before that it fired on a fraction of opt-ins, so the page-opt-in half of
# every earlier cycle is understated. Measured against the funnel's own counter:
#
#   date          GHL "Opt in v2"   Meta pixel   captured
#   2026-08-20               57           15        26%
#   2026-08-28..09-06       245          162        66%
#   2026-09-01..09-06       111          100        90%
#   2026-09-06                9            9       100%
#
# So cycles that closed before the repair are restated on the funnel's own count, taken
# from the GoHighLevel funnel stats page (GHL_STATS_URL below) over the cycle's whole-day
# span. Cycles after it are left on Meta, which now agrees with the funnel to within
# about a tenth. Verify with GHL_STATS_URL: "Opt in v2" opt-ins plus Meta's Lead (form)
# is the registration figure this page reports.
PIXEL_FIX_DATE = date(2026, 8, 27)
GHL_STATS_URL = ("https://app.pbiflightcrew.com/v2/location/GmBTEcbq9PN9YY99gncv"
                 "/funnels-websites/funnels/jlPVWxKKFvE67r7hNCb0/stats")
GHL_OPTINS = {
    # (first day, last day) inclusive, ad-account clock -> "Opt in v2" opt-ins
    ("2026-08-10", "2026-08-16"): 316,
    ("2026-08-17", "2026-08-23"): 359,
    ("2026-08-24", "2026-08-30"): 269,
}

# Hourly runs would otherwise fill data/ with 24 files a day. Two days of history is
# enough to diff a bad pull against a good one; Meta remains the source of truth.
KEEP_SNAPSHOTS = 48

# Summed ad rows within this much of the account figure count as attribution drift,
# not as a broken pull. Measured at 0.29% on link clicks, 0% on spend and leads.
RECON_TOLERANCE_PCT = 1.0


def token():
    """Env first so CI can inject a secret; the local file is the developer fallback."""
    env = os.environ.get("FB_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    raise SystemExit(
        "No Meta token. Set FB_TOKEN in the environment, or place the read-only token at\n"
        f"  {TOKEN_FILE}"
    )


TOKEN = None


def redact(text):
    """Never let the access token reach stdout. This repo is public, so an unhandled
    error in CI would otherwise print the token into a world-readable Actions log."""
    return text.replace(TOKEN, "***") if TOKEN else text


# curl exits: partial transfer, timeout, empty reply, send failure, receive failure.
TRANSIENT_TRANSPORT = (18, 28, 52, 55, 56)

# Graph answers a request that is fine an hour later with one of these often enough to
# break about one hourly run in twenty. 500/502/503/504 are Meta's own bad minute; 429
# is the plain rate limit.
TRANSIENT_HTTP = (429, 500, 502, 503, 504)

# Graph also hides transient conditions under HTTP 400 with the real cause in the body:
#   1   unknown/temporary   2   service temporarily unavailable
#   4   app rate limit      17  user request limit reached
#   32  page rate limit     341 application limit reached
#   613 calls-per-second limit
# A genuinely bad request (bad field, dead token) carries a different code and must fail
# on the first attempt rather than being retried five times into the same wall.
TRANSIENT_GRAPH_CODES = (1, 2, 4, 17, 32, 341, 613)

# The throttles, as opposed to Meta's bad minute. These do not clear in three seconds:
# the app-level bucket (code 4, subcode 1504022) refills over minutes, so the ordinary
# 3/6/12/24s backoff just spends five attempts inside the same closed window. The
# 2026-08-19 21:36 run died that way. Throttles get their own, much slower schedule.
RATE_LIMIT_CODES = (4, 17, 32, 341, 613)
RATE_LIMIT_WAITS = (60, 150, 300, 300)


def graph_error(body):
    """The `error` object out of a Graph response body, or None if there isn't one."""
    try:
        d = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return d.get("error") if isinstance(d, dict) else None


def http_retryable(status, body):
    """Retry on a transient status, or on any status carrying a transient Graph code.

    The code is what matters, not the status Meta chose to hang it on: the app rate
    limit arrives as HTTP 400 on one call and HTTP 403 on the next. Gating the body
    check on `status == 400` meant the 403 form skipped every retry and killed the run
    on its first attempt (2026-08-19 21:36). A dead token (code 190) or a bad field
    (code 100) still carries a code outside the list and still fails immediately.
    """
    if status in TRANSIENT_HTTP:
        return True
    err = graph_error(body) or {}
    return err.get("code") in TRANSIENT_GRAPH_CODES


def curl(url, tries=5):
    """A Graph read, retried on anything transient: transport *and* HTTP status.

    The earlier version passed `--fail` and retried only on transport-level curl exits.
    `--fail` collapses every HTTP status into exit 22 and discards the response body, so
    a throttled or hiccuping Graph call was neither retried nor diagnosable: the log read
    "curl: (22) The requested URL returned error: 400" and nothing else. Between
    2026-08-13 and 2026-08-19 that killed the hourly run on a 400 and on a 502, both of
    which succeeded unchanged the following hour.

    So: read the status and the body, retry the transient ones with backoff, and put
    Meta's own error code and message in the log when giving up. Errors are raised with
    the token stripped out; this repo is public and the log is world-readable.
    """
    last, throttled = "", False
    for attempt in range(tries):
        p = subprocess.run(
            ["curl", "-sS", "--max-time", "90", "-w", "\n%{http_code}", url],
            capture_output=True, text=True)

        if p.returncode == 0:
            body, _, tail = p.stdout.rpartition("\n")
            status = int(tail) if tail.strip().isdigit() else 0
            if status == 200:
                return body
            err = graph_error(body) or {}
            last = ("HTTP {} | code {} subcode {} | {}".format(
                status, err.get("code"), err.get("error_subcode"),
                err.get("message", body)[:200]))
            throttled = err.get("code") in RATE_LIMIT_CODES
            if not http_retryable(status, body):
                break
        else:
            last = (p.stderr or p.stdout or "").strip()
            throttled = False
            if p.returncode not in TRANSIENT_TRANSPORT:
                break

        if attempt == tries - 1:
            break
        if throttled:
            # 60s, 150s, 300s, 300s: a throttled call waits out the bucket instead of
            # burning its attempts inside it. Worst case one call costs about 13 minutes,
            # which the hourly schedule absorbs; the alternative is an hour-stale page.
            base = RATE_LIMIT_WAITS[min(attempt, len(RATE_LIMIT_WAITS) - 1)]
        else:
            # 3s, 6s, 12s, 24s for Meta's ordinary bad minute.
            base = min(45, 3 * 2 ** attempt)
        # Jittered so a retry storm does not resynchronise on Meta.
        wait = base * (0.75 + random.random() * 0.5)
        print("    graph retry {}/{} in {:.0f}s after {}".format(
            attempt + 1, tries - 1, wait, redact(last)[:120]), flush=True)
        sleep(wait)

    raise RuntimeError("Graph request failed after {} attempt{}: {}".format(
        attempt + 1, "" if attempt == 0 else "s", redact(last)[:300]))


def get(path, params):
    params = dict(params)
    params["access_token"] = TOKEN
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    d = json.loads(curl(url))
    if "error" in d:
        raise RuntimeError(redact(str(d["error"].get("message", d["error"]))))
    return d


def get_all(path, params):
    """Follow Graph paging to the end."""
    rows, d = [], get(path, params)
    while True:
        rows.extend(d.get("data", []))
        nxt = d.get("paging", {}).get("next")
        if not nxt:
            return rows
        d = json.loads(curl(nxt))
        if "error" in d:
            raise RuntimeError(redact(str(d["error"].get("message", d["error"]))))


def acts(row, key="actions"):
    return {a["action_type"]: float(a["value"]) for a in row.get(key, []) or []}


def reg_parts(row):
    """(lead-form, page-opt-in) registrations for one insights row."""
    return reg_split(acts(row))


def reg_split(a):
    """The same, for an actions dict that has already been built."""
    return int(a.get(LEAD_FORM_ACTION, 0)), int(a.get(PAGE_OPTIN_ACTION, 0))


def num(row, field):
    v = row.get(field)
    return float(v) if v not in (None, "") else 0.0


def webinar_campaigns():
    rows = get_all(f"{ACCOUNT}/campaigns", {
        "fields": "id,name,status,effective_status,objective,created_time,start_time,stop_time,daily_budget",
        "limit": 200,
    })
    return [c for c in rows if CAMPAIGN_MATCH in c["name"].lower()]


def insights(level, since, until, extra_fields=""):
    fields = ("campaign_id,campaign_name,spend,impressions,clicks,"
              "actions,inline_link_clicks" + extra_fields)
    return get_all(f"{ACCOUNT}/insights", {
        "level": level,
        "fields": fields,
        "filtering": json.dumps(
            [{"field": "campaign.name", "operator": "CONTAIN", "value": CAMPAIGN_MATCH}]
        ),
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": 500,
    })


def daily(since, until):
    rows = get_all(f"{ACCOUNT}/insights", {
        "level": "account",
        "fields": "spend,inline_link_clicks,actions",
        "filtering": json.dumps(
            [{"field": "campaign.name", "operator": "CONTAIN", "value": CAMPAIGN_MATCH}]
        ),
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
        "limit": 500,
    })
    out = []
    for r in rows:
        form, page = reg_parts(r)
        out.append({
            "date": r["date_start"],
            "spend": round(num(r, "spend"), 2),
            "link_clicks": int(num(r, "inline_link_clicks")),
            "lead_form": form,
            "page_optin": page,
            "leads": form + page,
        })
    return out


def creatives(ad_ids):
    """Creative image, format, and the live post link, so every ad opens on Facebook.

    Format matters because the dashboard ranks images and videos in separate blocks.
    A creative is VIDEO when Meta gives it a video_id or types it VIDEO; everything
    else is treated as a still. Video engagement is deliberately not collected: video
    ads are judged on the same five metrics as every other ad.
    """
    out = {}
    for i in range(0, len(ad_ids), 40):
        chunk = ad_ids[i:i + 40]
        d = get("", {
            "ids": ",".join(chunk),
            "fields": "id,name,effective_status,creative{id,image_url,thumbnail_url,"
                      "object_type,video_id,object_story_id,effective_object_story_id,"
                      "object_story_spec,body,title}",
        })
        for ad_id, ad in d.items():
            cr = ad.get("creative", {}) or {}
            oss = cr.get("object_story_spec", {}) or {}
            video_id = cr.get("video_id") or (oss.get("video_data", {}) or {}).get("video_id")
            is_video = bool(video_id) or cr.get("object_type") == "VIDEO"

            story = cr.get("effective_object_story_id") or cr.get("object_story_id") or ""
            permalink = None
            if "_" in story:
                page_id, post_id = story.split("_", 1)
                permalink = f"https://www.facebook.com/{page_id}/posts/{post_id}"

            # A video ad's still is the poster frame Meta already serves for it, so a
            # video card looks like the ad rather than like a blank tile.
            poster = cr.get("image_url") or (oss.get("video_data", {}) or {}).get("image_url") \
                or cr.get("thumbnail_url")

            out[ad_id] = {
                "status": ad.get("effective_status"),
                "format": "VIDEO" if is_video else "IMAGE",
                "video_id": video_id,
                "image_url": poster,
                "permalink": permalink,
                "headline": cr.get("title") or (oss.get("video_data", {}) or {}).get("title"),
                "body": cr.get("body") or (oss.get("video_data", {}) or {}).get("message"),
            }
    return out


def metrics(spend, link_clicks, lead_form, page_optin):
    """The five reported figures, and only those. Costs are None when undefined.

    Registrations always arrive as their two components so that every box on the page
    can show the arithmetic the client is asked to verify: lead form plus page opt-in.
    """
    leads = lead_form + page_optin
    return {
        "spend": round(spend, 2),
        "leads": leads,
        "lead_form": lead_form,
        "page_optin": page_optin,
        "link_clicks": link_clicks,
        "cost_per_lead": round(spend / leads, 2) if leads else None,
        "cost_per_link_click": round(spend / link_clicks, 2) if link_clicks else None,
    }


def shape_ads(rows):
    ads = []
    for r in rows:
        form, page = reg_parts(r)
        m = metrics(num(r, "spend"), int(num(r, "inline_link_clicks")), form, page)
        ads.append({
            "ad_id": r["ad_id"],
            "ad_name": r["ad_name"],
            "adset_name": r.get("adset_name"),
            "campaign_name": r["campaign_name"],
            "impressions": int(num(r, "impressions")),
            **m,
        })
    return ads


def totals(ads):
    """Rates are recomputed from the summed components, never averaged across ads."""
    t = metrics(sum(a["spend"] for a in ads),
                sum(a["link_clicks"] for a in ads),
                sum(a["lead_form"] for a in ads),
                sum(a["page_optin"] for a in ads))
    t["impressions"] = sum(a["impressions"] for a in ads)
    return t


def pull_window(since, until, label, note, campaign_ids):
    """One window, entirely from Meta: spend, link clicks and both registration halves.

    Registrations are the lead-form count plus the page-opt-in count, per ad, so every
    figure on the page traces back to something the client can look up themselves: the
    lead-form half against Meta's own "Lead (form)" column, the page-opt-in half against
    the funnel's "Opt in v2" row. Nothing here is modelled or blended.
    """
    print(f"  [{label}] {since} -> {until}")

    ad_rows = insights("ad", since, until, ",adset_name,ad_id,ad_name")
    ads = shape_ads(ad_rows)

    camp_rows = insights("campaign", since, until)
    win_ids = [str(r["campaign_id"]) for r in camp_rows] or campaign_ids
    campaigns = []
    for r in camp_rows:
        form, page = reg_parts(r)
        campaigns.append({
            "campaign_id": str(r["campaign_id"]),
            "campaign_name": r["campaign_name"],
            **metrics(num(r, "spend"), int(num(r, "inline_link_clicks")), form, page),
        })
    campaigns.sort(key=lambda c: -c["spend"])

    # Independent check: the same filter and window asked for at account level. Ad-level
    # rows are attributed per ad and can round a few cents away from the account figure,
    # so the delta is reported on the page rather than quietly reconciled away.
    acct_rows = insights("account", since, until)
    t = totals(ads)
    if acct_rows:
        acct = acct_rows[0]
        a_form, a_page = reg_parts(acct)
        account = {
            "spend": round(num(acct, "spend"), 2),
            "link_clicks": int(num(acct, "inline_link_clicks")),
            "leads": a_form + a_page,
            "lead_form": a_form,
            "page_optin": a_page,
        }
    else:
        account = {"spend": 0.0, "link_clicks": 0, "leads": 0,
                   "lead_form": 0, "page_optin": 0}

    # Graded per metric rather than a single pass/fail. Summing ad rows never quite
    # equals the account figure: an ad deleted mid-window still counts at account level
    # but returns no ad row, so a strict equality check would fire permanently and train
    # everyone to ignore it. Registrations are now checked here too, because they come
    # from the same Meta read as the spend rather than from a second system.
    recon = {
        "account": account,
        "ad_sum": {k: t[k] for k in ("spend", "link_clicks", "leads", "lead_form", "page_optin")},
        "deltas": {},
    }
    for k in ("spend", "link_clicks", "leads", "lead_form", "page_optin"):
        a_val, s_val = account[k], recon["ad_sum"][k]
        pct = round(abs(s_val - a_val) / a_val * 100, 3) if a_val else 0.0
        recon["deltas"][k] = {
            "account": a_val, "ad_sum": s_val, "diff": round(s_val - a_val, 2), "pct": pct,
            "grade": "exact" if s_val == a_val else ("drift" if pct <= RECON_TOLERANCE_PCT else "differs"),
        }
    recon["spend_delta_pct"] = recon["deltas"]["spend"]["pct"]
    recon["worst_grade"] = ("differs" if any(d["grade"] == "differs" for d in recon["deltas"].values())
                            else "drift" if any(d["grade"] == "drift" for d in recon["deltas"].values())
                            else "exact")

    # Second cross-check: ad rows summed against the campaign-level answer for the same
    # window. Both come from Meta, so these should agree exactly unless an ad was deleted
    # mid-window.
    camp_leads = sum(c["leads"] for c in campaigns)
    recon["registrations"] = {
        "ad_sum": t["leads"],
        "campaign_sum": camp_leads,
        "pct": round(abs(t["leads"] - camp_leads) / camp_leads * 100, 2) if camp_leads else 0.0,
        "lead_form": t["lead_form"],
        "page_optin": t["page_optin"],
    }

    flags = " ".join(f"{k}:{d['grade']}" for k, d in recon["deltas"].items() if d["grade"] != "exact")
    print(f"        {len(ads)} ads | spend ${t['spend']:,.2f} | regs {t['leads']} "
          f"(lead form {t['lead_form']}, page opt-in {t['page_optin']}; "
          f"campaign-level {camp_leads}) | "
          f"link clicks {t['link_clicks']} | recon {recon['worst_grade']}"
          + (f" ({flags})" if flags else ""))

    return {
        "label": label,
        "note": note,
        "since": since,
        "until": until,
        # A window reaching back before the pixel repair carries a page-opt-in half that
        # Meta undercounts, and it cannot be restated the way the weekly cycles are:
        # the funnel's stats have no per-ad breakdown, so there is nothing to correct an
        # individual creative against. The page says so rather than quietly rescaling.
        "page_optin_understated": date.fromisoformat(since) <= PIXEL_FIX_DATE,
        "pixel_fix_date": PIXEL_FIX_DATE.isoformat(),
        "days": (date.fromisoformat(until) - date.fromisoformat(since)).days + 1,
        "totals": t,
        "reconciliation": recon,
        "campaigns": campaigns,
        # One source per day means the day strip adds up to the window totals above it,
        # which it could not do while the days and the totals came from two systems.
        "daily": daily(since, until),
        "ads": ads,
    }


# PBI counts a webinar week from noon Central to noon Central on the following Monday:
# seven days, not eight, because it is noon-to-noon. Central is two hours ahead of the ad
# account's Los Angeles clock all year, since both zones change over on the same dates,
# so the boundary always falls on the top of an hour in Meta's hourly buckets.
WEEK_TZ = ZoneInfo("America/Chicago")
WEEK_HOUR = 12
PREV_WEEKS_MAX = 6


def week_bounds(now):
    """The cycle currently open: the noon-Monday boundary just passed, and the next one."""
    now_c = now.astimezone(WEEK_TZ)
    monday = now_c.date() - timedelta(days=now_c.weekday())
    opened = datetime.combine(monday, time(WEEK_HOUR), WEEK_TZ)
    if now_c < opened:          # before noon on a Monday the open cycle is the earlier one
        opened -= timedelta(days=7)
    return opened, opened + timedelta(days=7)


def meta_instant_range(opened, closed):
    """Spend, link clicks and both registration halves between two instants.

    Day-level insights cannot answer a noon boundary, so this reads the 24 hourly buckets
    per day and keeps the ones inside the window. The buckets are labelled in the ad
    account's own timezone, which is what `date_start` is keyed to as well. Meta returns
    `actions` per bucket and they sum to the day figure exactly, so registrations land on
    the noon boundary as precisely as spend does.
    """
    o = opened.astimezone(ACCOUNT_TZ)
    c = closed.astimezone(ACCOUNT_TZ)
    rows = get_all(f"{ACCOUNT}/insights", {
        "level": "account",
        "fields": "spend,inline_link_clicks,actions",
        "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
        "filtering": json.dumps(
            [{"field": "campaign.name", "operator": "CONTAIN", "value": CAMPAIGN_MATCH}]),
        "time_range": json.dumps({"since": o.date().isoformat(), "until": c.date().isoformat()}),
        "time_increment": 1,
        "limit": 500,
    })
    spend = 0.0
    clicks = 0
    hours = 0
    form = 0
    page = 0
    for r in rows:
        bucket = r.get("hourly_stats_aggregated_by_advertiser_time_zone", "")
        try:
            hour = int(bucket[:2])
        except ValueError:
            continue
        stamp = datetime.combine(date.fromisoformat(r["date_start"]), time(hour), ACCOUNT_TZ)
        if o <= stamp < c:
            spend += num(r, "spend")
            clicks += int(num(r, "inline_link_clicks"))
            f, p = reg_parts(r)
            form += f
            page += p
            hours += 1
    return round(spend, 2), clicks, hours, form, page


def ghl_restatement(opened, closed):
    """The funnel's own opt-in count for a cycle that predates the 2026-08-27 pixel fix.

    Returns (opt-ins, whole-day span) or (None, None). The cycle runs noon Monday to noon
    Monday but the funnel's stats page only slices whole days, so the count is taken over
    the seven days the cycle opens on. That approximation is worth far less error than
    the thing it corrects: the pixel was capturing about a quarter of opt-ins.

    Keyed on the day the cycle OPENED, not the day it closed. The cycle that opened
    2026-08-24 was still running when the pixel was repaired on the 27th, so three of its
    seven days are undercounted by Meta and the funnel's own count is the better figure
    for the whole cycle. Only a cycle that opens after the repair is left on Meta.
    """
    first = opened.astimezone(ACCOUNT_TZ).date()
    if first > PIXEL_FIX_DATE:
        return None, None
    span = (first.isoformat(), (first + timedelta(days=6)).isoformat())
    return GHL_OPTINS.get(span), span


def week_cycle(campaign_ids, opened, closed, now, label):
    """One noon-Monday-to-noon-Monday cycle, on the same five metrics as every other box."""
    # Never ask Meta past now: an hour bucket that has not happened yet returns nothing,
    # and counting it as elapsed would understate the cycle's rates on live spend.
    api_close = min(closed, now)
    closing_now = closed > now

    spend, clicks, hours, form, page = meta_instant_range(opened, api_close)

    # Cycles that closed before the pixel repair are restated on the funnel's own opt-in
    # count, because Meta's page-opt-in half is known to be short for those weeks.
    restated, span = ghl_restatement(opened, closed)
    if restated is not None:
        page_source = "GoHighLevel funnel, Opt in v2"
        page = restated
    else:
        page_source = "Meta pixel"
    leads = form + page

    return {
        "lead_form": form,
        "page_optin": page,
        "page_optin_source": page_source,
        "page_optin_restated": restated is not None,
        "page_optin_span": list(span) if span else None,
        "pixel_fix_date": PIXEL_FIX_DATE.isoformat(),
        "label": label,
        "opened": opened.isoformat(timespec="minutes"),
        "closed": closed.isoformat(timespec="minutes"),
        # Rendered under the cycle's own CDT/CST label, so it has to be carried in
        # Central. `now` comes off the account's Los Angeles clock, two hours behind, and
        # shipping it raw made an open week read "through 10:40 AM CDT" at 12:40 CDT:
        # a boundary report that looked like it had stopped before the noon cap.
        "api_closed": api_close.astimezone(WEEK_TZ).isoformat(timespec="minutes"),
        "closing_now": closing_now,
        # Elapsed comes off the clock. `buckets` is how many hourly rows Meta actually
        # returned, which is lower whenever an hour had no delivery, so it is not a
        # measure of progress through the cycle.
        "elapsed_hours": int((api_close - opened).total_seconds() // 3600),
        "total_hours": int((closed - opened).total_seconds() // 3600),
        "buckets": hours,
        "tz_abbrev": opened.strftime("%Z"),
        "spend": spend,
        "link_clicks": clicks,
        "cost_per_link_click": round(spend / clicks, 2) if clicks else None,
        "leads": leads,
        "cost_per_lead": round(spend / leads, 2) if leads else None,
        "conv_rate": round(leads / clicks * 100, 2) if clicks else None,
    }


def previous_weeks(campaign_ids, opened, now):
    """Completed cycles before the open one, newest first, back to the program launch."""
    out = []
    launch = date.fromisoformat(WINDOW_START)
    cur = opened
    for _ in range(PREV_WEEKS_MAX):
        cur = cur - timedelta(days=7)
        if (cur + timedelta(days=7)).astimezone(ACCOUNT_TZ).date() <= launch:
            break                       # cycle closed before the program existed
        w = week_cycle(campaign_ids, cur, cur + timedelta(days=7), now, "Previous week")
        if w["spend"] or w["leads"]:
            out.append(w)
    return out


def delivering_campaign_ids(since, until):
    """Every webinar campaign that actually delivered in this range, per Meta.

    Selecting by ACTIVE status instead silently drops a campaign that has since been
    paused while its spend still lands in the totals: on 2026-08-16 that was
    `TOF | Weekly Webinar Lead Ads`, contributing $890.79 of spend and zero
    registrations, which inflated cost per registration across every box.
    """
    return [str(r["campaign_id"]) for r in insights("campaign", since, until)]


def prune(data_dir):
    snaps = sorted(data_dir.glob("*_webinar_snapshot.json"))
    for old in snaps[:-KEEP_SNAPSHOTS]:
        old.unlink()
    return max(0, len(snaps) - KEEP_SNAPSHOTS)


def main():
    global TOKEN
    TOKEN = token()

    now = datetime.now(ACCOUNT_TZ)
    today = now.date()

    print(f"Account {ACCOUNT} ({ACCOUNT_LABEL})   {now:%Y-%m-%d %H:%M %Z}")

    camps = webinar_campaigns()
    live = [c for c in camps if c["effective_status"] == "ACTIVE"]
    print(f"  {len(camps)} campaigns match '{CAMPAIGN_MATCH}' ({len(live)} active)")

    # Only the campaigns that actually run this funnel, not all 219 name-matched ones:
    # the archived ones stopped in 2024 and only cost round trips.
    live_ids = [c["id"] for c in live] or [c["id"] for c in camps[:2]]

    if len(sys.argv) >= 3:
        windows = {"launch": pull_window(
            sys.argv[1], sys.argv[2], "Custom window",
            "Explicit window passed on the command line.", live_ids)}
        default_window = "launch"
    else:
        # Trailing windows never reach back before the program launched: those campaigns
        # stopped in 2024 and would blend a different era into "this week".
        def back(days):
            return max(WINDOW_START, (today - timedelta(days=days - 1)).isoformat())

        windows = {
            "3d": pull_window(
                back(3), today.isoformat(), "Last 3 days",
                "The trailing three days: what the account is doing right now.", live_ids),
            "7d": pull_window(
                back(7), today.isoformat(), "Last 7 days",
                "The trailing seven days, matching the weekly webinar cadence.", live_ids),
            "launch": pull_window(
                WINDOW_START, today.isoformat(), "Since launch",
                "Everything since the weekly webinar program went live.", live_ids),
        }
        # The funnel runs on a weekly cycle, so the week is the honest default: three days
        # is a spot check and since-launch flattens this week into the average of all of
        # them. It also sits between the other two, one click from either.
        default_window = "7d"

    # One creative store shared by every window: the same ad appears in both, and
    # inlining its image twice would double the page for nothing.
    ad_ids = sorted({a["ad_id"] for w in windows.values() for a in w["ads"]})
    cr = creatives(ad_ids)
    fmt = {}
    for aid in ad_ids:
        fmt[aid] = cr.get(aid, {}).get("format", "IMAGE")
    for w in windows.values():
        for a in w["ads"]:
            a["format"] = fmt.get(a["ad_id"], "IMAGE")
    n_video = sum(1 for v in cr.values() if v["format"] == "VIDEO")
    print(f"  {len(cr)} creatives resolved ({n_video} video, {len(cr) - n_video} image)")

    # Today's box, entirely from Meta's own daily row, so it agrees with the day strip
    # below rather than contradicting it. Both registration halves are carried through:
    # the lead-form count is what Meta reports as "Lead (form)", and the page opt-in is
    # what the GoHighLevel funnel records on its "Opt in v2" step.
    today_iso = today.isoformat()
    day_row = next((d for d in windows[default_window]["daily"] if d["date"] == today_iso), None)

    spend = day_row["spend"] if day_row else 0.0
    clicks = day_row["link_clicks"] if day_row else 0
    form = day_row["lead_form"] if day_row else 0
    page = day_row["page_optin"] if day_row else 0
    leads = form + page
    print(f"  today: {leads} registrations (lead form {form}, page opt-in {page}), "
          f"${spend:,.2f}, {clicks} link clicks")
    todays = {
        "date": today_iso,
        "spend": spend,
        "link_clicks": clicks,
        "cost_per_link_click": round(spend / clicks, 2) if clicks else None,
        "leads": leads,
        "lead_form": form,
        "page_optin": page,
        "cost_per_lead": round(spend / leads, 2) if leads else None,
        "conv_rate": round(leads / clicks * 100, 2) if clicks else None,
        "source": "Meta Graph API: Lead (form) plus pixel opt-ins on the funnel page",
        "fetched_at": datetime.now(ACCOUNT_TZ).isoformat(timespec="seconds"),
    }

    opened, closed = week_bounds(now)
    wk = week_cycle(live_ids, opened, closed, now, "This week") if live_ids else None
    prev = previous_weeks(live_ids, opened, now) if live_ids else []
    if wk:
        print(f"  week {wk['opened']} -> {wk['closed']} "
              f"({'open' if wk['closing_now'] else 'complete'}, "
              f"{wk['elapsed_hours']}/{wk['total_hours']}h): "
              f"{wk['leads']} registrations, ${wk['spend']:,.2f}, {wk['link_clicks']} clicks")
    for w in prev:
        print(f"    prev {w['opened'][:16]} -> {w['closed'][:16]}: "
              f"{w['leads']} registrations, ${w['spend']:,.2f}")
    if not prev:
        print("    no completed cycle before this one yet")

    snap = {
        "meta": {
            "client": "Photography Business Institute",
            "short_name": "Joy of Marketing",
            "account_id": ACCOUNT.replace("act_", ""),
            "account_label": ACCOUNT_LABEL,
            "currency": "USD",
            "timezone": str(ACCOUNT_TZ),
            "timezone_abbrev": now.strftime("%Z"),
            "scope": f"Campaigns whose name contains '{CAMPAIGN_MATCH}'",
            # Carried literally as well as in prose: the page's live-refresh layer has to
            # send Meta the same filter this pull used, and parsing it back out of `scope`
            # would be a second definition waiting to drift.
            "campaign_match": CAMPAIGN_MATCH,
            "window_start": WINDOW_START,
            "week_tz": str(WEEK_TZ),
            "lead_form_action": LEAD_FORM_ACTION,
            "page_optin_action": PAGE_OPTIN_ACTION,
            "pixel_fix_date": PIXEL_FIX_DATE.isoformat(),
            "ghl_stats_url": GHL_STATS_URL,
            "ghl_optins": {f"{a}..{b}": v for (a, b), v in GHL_OPTINS.items()},
            "source": "Meta Graph API v21.0 (ads_read)",
            "pulled_at": now.isoformat(timespec="seconds"),
            "pulled_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "default_window": default_window,
            "campaigns_matched": [
                {"id": c["id"], "name": c["name"], "status": c["effective_status"],
                 "created": c["created_time"][:10]}
                for c in sorted(camps, key=lambda x: x["created_time"], reverse=True)
            ],
        },
        "today": todays,
        "week": wk,
        "prev_weeks": prev,
        "creatives": cr,
        "windows": windows,
    }

    data_dir = HERE / "data"
    data_dir.mkdir(exist_ok=True)
    out = data_dir / f"{now:%Y-%m-%dT%H}_webinar_snapshot.json"
    out.write_text(json.dumps(snap, indent=1))
    dropped = prune(data_dir)
    print(f"  wrote {out.relative_to(HERE)}" + (f"  (pruned {dropped} old)" if dropped else ""))


if __name__ == "__main__":
    main()
