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
HYROS_KEY_FILE = Path("/Users/philglutting/Documents/Claude/Projects/PBI 2/hyros_key.txt")
HYROS_API = "https://api.hyros.com/v1/api/v1.0"
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


def daily_merged(since, until, campaign_ids):
    """Meta spend and link clicks per day, with Hyros registrations on the same days."""
    rows = daily(since, until)
    hy = hyros_daily(campaign_ids, since, until)
    for r in rows:
        r["meta_pixel_leads"] = r["leads"]
        r["leads"] = hy.get(r["date"], 0) if hy else 0
        r["hyros"] = bool(hy)
    return rows


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


def pull_window(since, until, label, note, campaign_ids):
    """One window. Spend and link clicks are Meta's; registrations are Hyros's.

    The whole page reports one registration number so nothing contradicts the hero, and
    Hyros is that number: it credits registrations the Meta pixel never sees. Over
    2026-08-11..13 Hyros counted 155 at ad level against the pixel's 52, and it reorders
    the board rather than just scaling it, so the ranking has to be built on it.

    Meta's own pixel count is kept per ad as meta_pixel_leads, for reference only.
    """
    print(f"  [{label}] {since} -> {until}")

    ad_rows = insights("ad", since, until, ",adset_name,ad_id,ad_name")
    ads = shape_ads(ad_rows)

    hy_ads = hyros_by_id("facebook_ad", [a["ad_id"] for a in ads], since, until)
    matched = 0
    for a in ads:
        h = hy_ads.get(a["ad_id"])
        a["meta_pixel_leads"] = a["leads"]
        a["leads"] = h["leads"] if h else 0
        a["hyros_row"] = h is not None
        matched += 1 if h else 0
        # Cost per registration pairs Meta's spend with Hyros's registrations, exactly as
        # the hero does, so an ad's figure and the day's figure are built the same way.
        a["cost_per_lead"] = round(a["spend"] / a["leads"], 2) if a["leads"] else None
    print(f"        hyros ad rows matched {matched}/{len(ads)}")

    hy_camps = hyros_by_id("facebook_campaign", campaign_ids, since, until)
    camp_rows = insights("campaign", since, until)
    campaigns = []
    for r in camp_rows:
        cid = str(r["campaign_id"])
        spend = num(r, "spend")
        leads = hy_camps.get(cid, {}).get("leads", 0)
        campaigns.append({
            "campaign_id": cid,
            "campaign_name": r["campaign_name"],
            **metrics(spend, leads, int(num(r, "inline_link_clicks"))),
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
    # but returns no ad row, so a strict equality check would fire permanently and train
    # everyone to ignore it. Only the two Meta-sourced figures are checked against Meta;
    # registrations are Hyros's and are reconciled separately below.
    recon = {
        "account": account,
        "ad_sum": {k: t[k] for k in ("spend", "link_clicks")},
        "deltas": {},
    }
    for k in ("spend", "link_clicks"):
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

    # Hyros's own cross-check: ad rows summed against the campaign-level answer for the
    # same window. They ran 155 against 153 on 2026-08-13, a 1.3% gap.
    camp_leads = sum(c["leads"] for c in campaigns)
    ad_leads = t["leads"]
    recon["hyros"] = {
        "ad_sum": ad_leads,
        "campaign_sum": camp_leads,
        "pct": round(abs(ad_leads - camp_leads) / camp_leads * 100, 2) if camp_leads else 0.0,
        "meta_pixel": sum(a["meta_pixel_leads"] for a in ads),
    }

    flags = " ".join(f"{k}:{d['grade']}" for k, d in recon["deltas"].items() if d["grade"] != "exact")
    print(f"        {len(ads)} ads | spend ${t['spend']:,.2f} | hyros regs {t['leads']} "
          f"(campaign-level {camp_leads}, meta pixel {recon['hyros']['meta_pixel']}) | "
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
        "daily": daily_merged(since, until, campaign_ids),
        "ads": ads,
    }


def hyros_key():
    key = os.environ.get("HYROS_API_KEY", "").strip()
    if not key and HYROS_KEY_FILE.exists():
        key = HYROS_KEY_FILE.read_text().strip()
    return key


def hyros_call(level, ids, since, until):
    """One /attribution read. Returns [] on any failure so callers degrade rather than die."""
    key = hyros_key()
    if not key or not ids:
        return []
    params = urllib.parse.urlencode({
        "startDate": since, "endDate": until,
        "attributionModel": "last_click",     # not lastClick, not LAST_CLICK
        "level": level,                       # facebook_ad / facebook_campaign
        "fields": "leads,cost,clicks",        # lowercase only
        "ids": ",".join(ids),
    })
    try:
        out = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", "60", "-H", f"API-Key: {key}",
             f"{HYROS_API}/attribution?{params}"],
            check=True, capture_output=True, text=True).stdout
        return json.loads(out).get("result", []) or []
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return []


def hyros_by_id(level, ids, since, until, batch=20):
    """Hyros wants real IDs and rejects `ids=ALL`, so ad reads go in batches."""
    out = {}
    for i in range(0, len(ids), batch):
        for r in hyros_call(level, ids[i:i + batch], since, until):
            out[str(r.get("id"))] = {
                "leads": int(r.get("leads") or 0),
                "cost": round(float(r.get("cost") or 0), 2),
                "clicks": int(r.get("clicks") or 0),
            }
    return out


def hyros_daily(campaign_ids, since, until, cap=31):
    """Registrations per day. Hyros has no day grouping on this endpoint, so it is one
    read per day; capped so a long window cannot fan out into hundreds of calls."""
    d0, d1 = date.fromisoformat(since), date.fromisoformat(until)
    days = (d1 - d0).days + 1
    if days > cap:
        return {}
    out = {}
    for i in range(days):
        day = (d0 + timedelta(days=i)).isoformat()
        rows = hyros_call("facebook_campaign", campaign_ids, day, day)
        out[day] = sum(int(r.get("leads") or 0) for r in rows)
    return out


def hyros_today(campaign_ids, day):
    """Today's blended webinar registrations, from Hyros rather than the Meta pixel.

    Hyros credits registrations the Meta pixel never sees: on 2026-08-13 it counted 53
    against the pixel's 22 on the same spend. Its click figure matched Meta's link
    clicks exactly, which is what makes the two safe to put in one box.

    Needs a PBI-scoped Hyros API key. The Lance key in the workspace is scoped to his
    account and returns an empty result for these campaigns, so it is not a fallback.
    Returns None when no key is configured; the caller degrades rather than failing.
    """
    if not hyros_key():
        return None
    rows = hyros_call("facebook_campaign", campaign_ids, day, day)
    if not rows:
        print("  hyros: key returned no rows for these campaigns (wrong account?)")
        return None

    leads = sum(int(r.get("leads") or 0) for r in rows)
    cost = round(sum(float(r.get("cost") or 0) for r in rows), 2)
    clicks = sum(int(r.get("clicks") or 0) for r in rows)
    return {
        "date": day,
        "leads": leads,
        "cost": cost,
        "clicks": clicks,
        "cost_per_lead": round(cost / leads, 2) if leads else None,
        "cost_per_click": round(cost / clicks, 2) if clicks else None,
        "conv_rate": round(leads / clicks * 100, 2) if clicks else None,
        "source": "Hyros REST /attribution, last click",
        "fetched_at": datetime.now(ACCOUNT_TZ).isoformat(timespec="seconds"),
    }


def hyros_seed(day):
    """Fallback for a run with no API key: the last figures fetched through the Hyros
    MCP, carrying their own date so the page can grey them out once they go stale."""
    seed_file = HERE / "hyros_seed.json"
    if not seed_file.exists():
        return None
    try:
        seed = json.loads(seed_file.read_text())
    except json.JSONDecodeError:
        return None
    seed["stale"] = seed.get("date") != day
    return seed


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

    # Hyros is asked about the campaigns that actually run this funnel, not all 219
    # name-matched ones: the archived ones stopped in 2024 and only cost round trips.
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

    # Today's box. Spend and link clicks come from Meta's own daily row for today so
    # they agree with the day strip below; registrations come from Hyros, which is the
    # whole point of calling them blended.
    today_iso = today.isoformat()
    day_row = next((d for d in windows[default_window]["daily"] if d["date"] == today_iso), None)
    hy = hyros_today(live_ids, today_iso) if live_ids else None
    if hy is None:
        hy = hyros_seed(today_iso)
        if hy:
            print(f"  hyros: using seeded figures from {hy.get('date')}"
                  + (" (STALE)" if hy.get("stale") else ""))
    else:
        print(f"  hyros: {hy['leads']} registrations, ${hy['cost']:,.2f}, {hy['clicks']} clicks")

    spend = day_row["spend"] if day_row else 0.0
    clicks = day_row["link_clicks"] if day_row else 0
    todays = {
        "date": today_iso,
        "spend": spend,
        "link_clicks": clicks,
        "cost_per_link_click": round(spend / clicks, 2) if clicks else None,
        "meta_pixel_leads": day_row["leads"] if day_row else 0,
        "hyros": hy,
    }
    if hy and hy.get("leads"):
        todays["cost_per_lead"] = round(spend / hy["leads"], 2)
        todays["conv_rate"] = round(hy["leads"] / clicks * 100, 2) if clicks else None
    else:
        todays["cost_per_lead"] = None
        todays["conv_rate"] = None

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
        "today": todays,
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
