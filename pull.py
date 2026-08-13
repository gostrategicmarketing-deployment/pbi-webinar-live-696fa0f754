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
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
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

# The Lead standard pixel event. In this account `lead`, `offsite_conversion.fb_pixel_lead`
# and `onsite_web_lead` all return the same count, so the pixel event is read directly
# rather than through the aggregated `lead` column.
LEAD_ACTION = "offsite_conversion.fb_pixel_lead"

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


def get(path, params):
    params = dict(params)
    params["access_token"] = TOKEN
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    out = subprocess.run(
        ["curl", "-sS", "--fail", "--max-time", "90", url],
        check=True, capture_output=True, text=True,
    ).stdout
    d = json.loads(out)
    if "error" in d:
        raise RuntimeError(d["error"].get("message", d["error"]))
    return d


def get_all(path, params):
    """Follow Graph paging to the end."""
    rows, d = [], get(path, params)
    while True:
        rows.extend(d.get("data", []))
        nxt = d.get("paging", {}).get("next")
        if not nxt:
            return rows
        out = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", "90", nxt],
            check=True, capture_output=True, text=True,
        ).stdout
        d = json.loads(out)
        if "error" in d:
            raise RuntimeError(d["error"].get("message", d["error"]))


def acts(row, key="actions"):
    return {a["action_type"]: float(a["value"]) for a in row.get(key, []) or []}


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
    return [{
        "date": r["date_start"],
        "spend": round(num(r, "spend"), 2),
        "link_clicks": int(num(r, "inline_link_clicks")),
        "leads": int(acts(r).get(LEAD_ACTION, 0)),
    } for r in rows]


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


def metrics(spend, leads, link_clicks):
    """The five reported figures, and only those. Costs are None when undefined."""
    return {
        "spend": round(spend, 2),
        "leads": leads,
        "link_clicks": link_clicks,
        "cost_per_lead": round(spend / leads, 2) if leads else None,
        "cost_per_link_click": round(spend / link_clicks, 2) if link_clicks else None,
    }


def shape_ads(rows):
    ads = []
    for r in rows:
        a = acts(r)
        m = metrics(num(r, "spend"), int(a.get(LEAD_ACTION, 0)),
                    int(num(r, "inline_link_clicks")))
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
                sum(a["leads"] for a in ads),
                sum(a["link_clicks"] for a in ads))
    t["impressions"] = sum(a["impressions"] for a in ads)
    return t


def pull_window(since, until, label, note):
    print(f"  [{label}] {since} -> {until}")

    ad_rows = insights("ad", since, until, ",adset_name,ad_id,ad_name")
    ads = shape_ads(ad_rows)

    camp_rows = insights("campaign", since, until)
    campaigns = []
    for r in camp_rows:
        a = acts(r)
        campaigns.append({
            "campaign_id": r["campaign_id"],
            "campaign_name": r["campaign_name"],
            **metrics(num(r, "spend"), int(a.get(LEAD_ACTION, 0)),
                      int(num(r, "inline_link_clicks"))),
        })
    campaigns.sort(key=lambda c: -c["spend"])

    # Independent check: the same filter and window asked for at account level. Ad-level
    # rows are attributed per ad and can round a few cents away from the account figure,
    # so the delta is reported on the page rather than quietly reconciled away.
    acct_rows = insights("account", since, until)
    t = totals(ads)
    if acct_rows:
        acct = acct_rows[0]
        a_acts = acts(acct)
        account = {
            "spend": round(num(acct, "spend"), 2),
            "link_clicks": int(num(acct, "inline_link_clicks")),
            "leads": int(a_acts.get(LEAD_ACTION, 0)),
        }
    else:
        account = {"spend": 0.0, "link_clicks": 0, "leads": 0}

    # Graded per metric rather than a single pass/fail. Summing ad rows never quite
    # equals the account figure: an ad deleted mid-window still counts at account level
    # but returns no ad row. On an hourly dashboard a strict equality check would fire
    # on that permanently and train everyone to ignore it, so the tolerance below
    # separates attribution drift from a genuinely broken pull.
    recon = {
        "account": account,
        "ad_sum": {k: t[k] for k in ("spend", "link_clicks", "leads")},
        "deltas": {},
    }
    for k in ("spend", "link_clicks", "leads"):
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

    flags = " ".join(f"{k}:{d['grade']}" for k, d in recon["deltas"].items() if d["grade"] != "exact")
    print(f"        {len(ads)} ads | spend ${t['spend']:,.2f} | leads {t['leads']} | "
          f"link clicks {t['link_clicks']} | recon {recon['worst_grade']}"
          + (f" ({flags})" if flags else ""))

    return {
        "label": label,
        "note": note,
        "since": since,
        "until": until,
        "days": (date.fromisoformat(until) - date.fromisoformat(since)).days + 1,
        "totals": t,
        "reconciliation": recon,
        "campaigns": campaigns,
        "daily": daily(since, until),
        "ads": ads,
    }


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

    if len(sys.argv) >= 3:
        windows = {"launch": pull_window(
            sys.argv[1], sys.argv[2], "Custom window", "Explicit window passed on the command line.")}
        default_window = "launch"
    else:
        # The trailing week never reaches back before the program launched: those
        # campaigns stopped in 2024 and would blend a different era into "this week".
        seven = max(WINDOW_START, (today - timedelta(days=6)).isoformat())
        windows = {
            "launch": pull_window(
                WINDOW_START, today.isoformat(), "Since launch",
                "Everything since the weekly webinar program went live."),
            "7d": pull_window(
                seven, today.isoformat(), "Last 7 days",
                "The trailing seven days, matching the weekly webinar cadence."),
        }
        default_window = "launch"

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
            "lead_action": LEAD_ACTION,
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
