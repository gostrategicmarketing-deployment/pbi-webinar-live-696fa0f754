# PBI Webinar Dashboard

A branded, client-facing dashboard for the webinar campaigns in Meta ad account
**37394393 (Joy of Marketing)**. Separate from the general PBI ad report in
`../2026-08-03 - Ad Reporting Platform/`, which covers both PBI accounts and every campaign.

**Live:** <https://gostrategicmarketing-deployment.github.io/pbi-webinar-live-696fa0f754/>

```bash
python3 refresh.py     # pull Meta, rebuild, publish live   <- the one command
python3 serve.py       # local dashboard at :7771 with a working Refresh button
```

Or double-click **`refresh.command`** in Finder.

## The page, top to bottom

1. **Webinar registrations** — today only, the hero, with its two halves printed
   underneath. This is the number to steer on.
2. **Creative performance over [Since launch | Last 7 days]** — the window selector,
   deliberately below the hero so it clearly governs the creative and tables, not today.
3. **Best images** and **Best videos** — top five of each format, ranked on the same
   registrations reported in the hero.
4. **Day by day**, **By campaign**, **Every ad**, **Method**, **Brand palette**.

## Headline registrations: GoHighLevel + Meta instant forms (2026-09-28)

Today's box, the open weekly cycle and the previous-weeks table count registrations as
two halves added together, each read from the system that holds it (Phil, 2026-09-28):

| Half | Source | Detail |
|---|---|---|
| Funnel opt-ins | GoHighLevel `/forms/submissions` | "Weekly Webinar [Facebook]" funnel `jlPVWxKKFvE67r7hNCb0`, Opt in v2 step, its two A/B forms: Control `BU61M3IbD1mFFFw2HXe8` + Variant `jkt0KlO7oBUI0ZaGyozu`. Each person (contactId) once per window. |
| Instant forms | Meta `onsite_conversion.lead_grouped` | on-Meta lead form; these people never load the funnel page |

The line under each headline prints the two halves. GHL's synced copy of the Meta lead
forms (form id `fb-<location>`) is deliberately not read, so the lead-form half has one
source. A GHL read that fails shows "—", never 0. Checked against the funnel's own stats
screen: 2026-08-20 reads 56 here against 57 there.

If Erin adds a new form to that funnel step, add its id to `GHL_WEBINAR_FORMS` in
`pull.py`; `live.js` takes the list from `LIVE_CFG`.

**Hyros still ranks the creative** (Best images/videos, By campaign, Every ad, Day by day)
because it credits registrations per ad and reconciles at ad level. Its totals will not
equal the headline, and the Method notes say so. Hyros and Meta's pixel opt-ins appear
under today's box as cross-checks.

### One-off early week openings

`WEEK_EARLY_OPENS` in `pull.py` maps a nominal Monday-noon boundary to the instant it
actually fell. 2026-09-28: the ads for the 10-05 webinar started that morning (ad sets
set to 1:44 AM PDT), so that cycle runs **3:00 AM CDT Sep 28 to noon CDT Oct 5** and the
cycle before it closes at 3:00 AM. From Oct 5 noon it is plain noon-to-noon again.
Entries must sit on the top of an hour (Meta's hourly buckets). `live.js` reads the same
map from `LIVE_CFG`.

## Creative rankings: Hyros

Every registration figure below the weekly boxes, from the window tables down to a single
creative, is the **Hyros** count under last-click attribution.

Meta's own count rides alongside as a labelled cross-check, split into the two actions it
reports, and is never the headline:

| | Meta action | What it is |
|---|---|---|
| `meta_lead_form` | `onsite_conversion.lead_grouped` | on-Meta instant form, never leaves Facebook |
| `meta_page_optin` | `offsite_conversion.fb_pixel_lead` | opt-in on the GoHighLevel funnel page |

### Why Hyros and not Meta (2026-09-10)

For three days this page reported Meta's two actions added together, on the argument that
the client could reproduce them. That argument was sound and the numbers were not: the
funnel pixel was under-firing until it was repaired on **2026-08-27**, so Meta's series has
a step change there that Hyros does not. Measured per day across the programme:

| Era | Meta | Hyros |
|---|---|---|
| 2026-08-11..08-27, pixel under-firing | 5-51/day | 27-83/day |
| 2026-09-01..09-10, pixel repaired | 20-91/day | 24-96/day |

Hyros agrees with Meta now **and** measured the earlier weeks correctly. It is the only
continuous series over the whole programme, and the only one that can compare August with
September. That also retired the GoHighLevel restatement this repo carried for the
pre-repair weekly cycles: with Hyros there is no era split left to patch over, and the
weekly figures come back to exactly what they were before the Meta experiment
(391 / 405 / 299).

Ranking creative on it is safe because it reconciles with itself at ad level: summed ad
rows against the campaign-level answer measured **332 vs 333** over the last seven days and
**1605 vs 1564** since launch.

### Reading it against the other two systems

- **Meta** is the last to catch up. Its same-day conversions arrive over the following
  hours: measured 2026-09-08, the hours to 9am on Sep 7 read 13 registrations at the time
  and 16 once Meta had filled in. So an early-morning Meta figure is always low.
- **GoHighLevel's `Opt in v2`** counts the funnel half only, from every traffic source
  rather than from the ads. It is never expected to match a total, in either direction.
- All three settle close to one another once a day closes.

The three are printed together under today's box so the comparison is on the page rather
than left to be rediscovered.

### One trap that bites every Hyros lookup

`/attribution` takes an explicit `ids` list, so **a past day must be queried with that
day's own campaigns**. Any campaign since paused simply drops out and takes its leads with
it, silently, with no error. Measured 2026-09-08: asking about 09-06 with the campaigns
running on the 8th returned **11** leads against the true **43**, because
`TOF | Weekly Webinar Lead Ads` had stopped overnight and took its 34 lead forms with it.
`campaign_ids_by_day()` gets the whole map in one Graph call with `time_increment=1`, and
both `pull.py` and `live.js` feed Hyros from it.

There is deliberately **no window-level KPI row**. A second set of totals on a different
window read as a contradiction of the hero rather than as context.

## Five metrics, and only five

**Registrations, cost per registration, link clicks, cost per link click, total spent.**
Every ranking uses these and nothing else, so a video and a still are judged on the same
terms. Video engagement (ThruPlays, completion quartiles, watch time) is deliberately not
collected or shown.

Link clicks rather than all-clicks throughout: all-clicks runs about 2.4x higher on this
account and would flatter both the click count and the cost per click. Rates are
recomputed from summed components, never averaged across ads.

## Clocks

Meta reports on the ad account's **America/Los_Angeles** clock. The weekly cycle runs
noon-to-noon **America/Chicago**, and Central is two hours ahead of the account's clock all
year, so the boundary always lands on the top of one of Meta's hourly buckets. Those
buckets carry `actions` and sum to the day figure exactly, so registrations land on the
noon boundary as precisely as spend does.

The GoHighLevel funnel's stats page slices **whole days only**, so a restated cycle is
counted over the seven days it opens on rather than noon-to-noon. That approximation is
worth far less error than the thing it corrects.

## Scope

Every campaign in the account whose name contains **"webinar"**, matched by name rather
than by a fixed ID list, so next week's campaign is picked up without editing anything.
219 campaigns match; the two live are `TOF | Weekly Webinar | CBO` and
`TOF | Weekly Webinar Lead Ads`.

Three windows, read shortest to widest:

| Tab | Window | |
|---|---|---|
| Last 3 days | trailing 3 days | a spot check on right now |
| **Last 7 days** | trailing 7 days | **the default**: the funnel's own weekly cycle |
| Since launch | `WINDOW_START` (2026-08-11) to today | everything |

The week is the default because this funnel runs weekly: three days is too short to judge
a creative on, and since-launch flattens this week into the average of all of them. It
also sits between the other two, one click from either.

Every trailing window is clamped so it never reaches back before launch — the older
webinar campaigns stop in Oct 2024 and would blend a different era into "this week." Until
the program is more than seven days old, Last 7 days and Since launch return the same
figures, which is correct rather than a bug.

Pass explicit dates for a single custom window:

```bash
python3 pull.py 2024-05-01 2024-11-01
```

## How the best five are picked, per format

Images and videos rank in **separate blocks**, so the top five of each is always visible
even when one format dominates spend. Within a block: registrations descending, then cost
per registration ascending, then spend descending. Volume outranks efficiency on purpose:
a single registration on two dollars of spend yields a $2 cost and proves nothing. Ads
under **$25 spent or 20 link clicks** carry a `thin data` chip. If fewer than five ads of
a format have registered anyone, the rest backfill on link clicks.

Format comes from the creative: a `video_id`, or `object_type == VIDEO`, makes it a video.

## Refresh

`refresh.py` pulls Meta, rebuilds the local `index.html` (for the serve.py
preview), then dispatches the deploy repo's `refresh` workflow, which does its own pull +
build + deploy in the cloud (`build_type=workflow`; see the Schedule section).

### The button has three homes

| Where | What Refresh does | How current |
|---|---|---|
| `serve.py` | POSTs `/refresh`: pull, rebuild, publish, reload | now |
| Published, **live data on** | calls Meta **from the browser** and repaints in place | now |
| Published, no keys | re-checks whether a newer build has been published | as current as the last build |

The published page ships with no credentials and never will: the repo has to be public for
Pages, so a key in the HTML is a key on the open internet. The live path solves that by
asking the *reader* for keys instead. **Turn on live data** takes a read-only Meta token
and a PBI-scoped Hyros key, keeps them in that browser's `localStorage` on that device
only, and sends them to nobody but Meta and Hyros. Nothing is written back to the repo, and a reader who never enters a
token sees exactly the page they saw before. Two keys are needed again since 2026-09-10,
because registrations come from Hyros rather than from the same Meta read as the spend.

Phil holds the token, so Phil gets a pull that is never stale. Erin sees the newest build.

A live pull repaints **today's box, the open weekly cycle, and all three windows**:
totals, campaigns, day by day, every ad, and the featured creative cards, re-ranked. It
deliberately leaves two things at their build-time values and says so in the status line
rather than implying they were re-read:

- **Previous weeks** are closed noon-to-noon cycles. They do not move.
- **The Method notes**, whose reconciliation grades audit that Python pull, not these numbers.

Everything else on the page is the live figure, built the same way `pull.py` builds it.

### live.js

The live layer is `live.js`, injected verbatim into the page by `build.py` at build time
(so it ships as one self-contained file, like the fonts and the creatives). It is kept as
its own file rather than inlined in `PAGE` so it stays editable JavaScript instead of a
brace-doubled string, and so `node --check live.js` can vet it before a build.

**It mirrors `pull.py` and has to keep mirroring it.** Same Graph fields, same
`campaign.name CONTAIN` filter, the same two registration actions summed the same way,
same `rank_key`. `pull.py` carries `campaign_match`, `window_start`, `week_tz`,
`lead_form_action`, `page_optin_action` and `pixel_fix_date` in the snapshot's `meta`, and
`build.py` passes them through as `LIVE_CFG`, so each is defined once rather than twice. If
you change what `pull.py` reads, change this with it or the live numbers and the built
numbers will quietly disagree.

It mirrors `campaign_ids_by_day()` too: both sides feed Hyros the campaigns that
delivered on each day rather than one list for the whole range, for the reason above.

**It is parallel, which `pull.py` is not.** That is the whole reason to run this in a
browser: `pull.py` is one thread asking for one thing at a time, and the same sequence of
54 reads measured **45 seconds** that way, which is far too long to sit behind a button.
Issued concurrently it measures **under 7 seconds**. Three economies, all producing
identical numbers:

- The daily strip covers the widest window once and is sliced per window, rather than
  three overlapping sweeps. Since 2026-09-07 it carries registrations too, so the per-day
  reads that used to sit alongside it are gone entirely.
- The campaign list asks Meta for `effective_status=["ACTIVE"]`, turning a paged read of
  220 campaigns into a single page of two: 3.9s down to 0.3s.
- Everything independent goes at once: all three windows, the daily strip, the per-day
  reads, today's delivering campaigns and the open week, and within each window the ad and
  campaign reads.

The ceiling on requests in flight is **global**, in `gate()`, not per call site. That is
deliberate: the pull nests three deep, and per-call limits multiply, so six of them nested
is not six requests but dozens, which is how you trip Meta's app-level bucket and then wait
minutes for it to refill. Only the attempt is held; a retry's backoff waits outside the
gate, so one throttled call cannot stall the other five slots.

One rule it does not relax: **a failed read is not zero registrations.** Meta answering
with an error would otherwise repaint the hero as 0 and look like a real collapse, so a
failed read aborts the whole refresh and leaves every number as it was.

Because that failure is fatal, it has to arrive fast: any 4xx but a throttle is a permanent
answer and breaks out at once instead of being retried three times.

The DOM is patched rather than regenerated: rows and cards are cloned from ones already on
the page and their cells rewritten, so `build.py` stays the only place this page's markup
is written.

### The schedule is a chain, not a cron (2026-08-27)

The deploy repo's **`refresh` workflow** pulls Meta, rebuilds, deploys, then
**holds until it is 28 minutes old and starts the next run itself**. The credential lives
in the repo's **Actions Secrets** (`FB_TOKEN`, read-only), reaching the scripts only as an
env var during a run. The Mac plays no part.

**GitHub's cron does not work on this repo and cannot be made to.** That is measured, not
inferred:

| Cron | Result |
|---|---|
| `8 * * * *` | median gap 0.96h, but 6% of gaps past 1.9h, every night a ~2.3h hole around 23:30 UTC, and 08-26 lost **10.2 hours** outright (23:25Z straight to 09:34Z) |
| `17,47 * * * *` | **six consecutive slots, zero runs** |

Everything on this side is correct: the workflow is `active`, Actions is enabled, the cron
is registered on the default branch, and this is the only repo on the whole account with a
workflow at all. Scheduled events on free public repos are best-effort and these are simply
being shed. Do not spend another afternoon retiming the cron.

`workflow_dispatch` and `push` are a different code path: they are API calls, they fire
immediately, and they have never been dropped here. A throwaway two-hop workflow confirmed
the default `GITHUB_TOKEN` may dispatch this workflow, GitHub's recursion guard
notwithstanding: hop 2 started **seven seconds** after hop 1 asked for it.

So the job ends with two steps:

1. **Hold until the half hour is up**, measured from the start of the run rather than the
   end of the work, so a throttled pull eats its own slack instead of pushing every later
   link further behind.
2. **Start the next link**, unless a run is already queued, in which case that one carries
   the chain. A dispatch that produces no run **fails the step loudly** rather than leaving
   the page quietly frozen, which is the one failure that would put this back where it
   started.

A run that *failed* still hands on: a failed pull is exactly when the next attempt matters
most. `!cancelled()` covers success and failure but not a deliberate cancel, so pressing
Cancel stops the chain, which is what that button ought to mean.

Converging triggers cannot fork it. The dispatch is skipped when something is already
queued, and GitHub keeps only the newest *pending* run per concurrency group, so a push and
the fallback cron landing mid-chain collapse back to one.

`schedule` is still declared, on `9,39 * * * *`, purely as a way back in if the chain is
ever broken. Anything that depends on it firing is a bug. To restart the chain by hand: the
**Run workflow** button on the Actions tab, or `gh workflow run refresh.yml -R <repo>`.

The cost is that a runner is occupied more or less continuously. Public repos bill no
Actions minutes, so this is free, but it is the honest trade: GitHub will not keep time for
us, so we keep it ourselves.

**A run held by a concurrency group reports `pending`, not `queued`.** The first version of
the guard counted only `queued` runs, so it never saw one, and at 15:54Z on 2026-08-27 it
dispatched straight over a pending push run and cancelled it. Nothing was lost, because the
run it dispatched built the same commit, but a push that reports "cancelled" reads like a
failure. The guard now counts every run that has not completed, excluding itself by run id,
which covers `queued`, `pending`, `waiting` and `requested` alike.

### Watchdog

The chain has one dependency: every run must successfully start the next. Most of the time
it does, and a run that *fails* still hands on. But a job that hits its timeout, a cancelled
run, or an Actions outage will break the link, and GitHub's cron is not a backstop worth
having. Left alone, the page would then quietly stop updating.

`chain-watchdog.sh` is what notices. Every ten minutes it reads the **published page's own
`last-modified` header**; if the page has not changed in 75 minutes it cancels anything
wedged and dispatches a fresh run. While the page is current it does nothing at all, so it
cannot double the cadence or fight the workflow's own concurrency group.

```bash
"./chain-watchdog.sh" --status   # report what it sees, change nothing
```

#### It watches the page, not the run list (2026-09-13)

The first version asked only whether a `refresh` run existed, on the reasoning that a run in
flight *is* the chain: either working or sitting in its 28-minute hold. That holds right up
until GitHub accepts a run and then never gives it a runner.

Measured **2026-09-13**: the 04:23:55Z link was dispatched on time and its job did not start
until **11:52:54Z**, seven and a half hours later, spanning a critical GitHub incident
(09:16Z to 10:44Z, Actions and Pages degraded). The run reported `in_progress` throughout,
so the old test saw a healthy chain and stayed silent while the page sat frozen on its
04:24Z build. The 100 deployments before it, 09-11 05:25Z to 09-13 04:24Z, had **no gap over
45 minutes**: the chain itself was never the problem.

So the health signal is now the one the reader actually experiences. Not the deployments
API: a `github-pages` deployment only reports `success` when the *job* ends, and this job
deliberately holds for 28 minutes after deploying, so success lands half an hour late. Its
`created_at` is worse, stamped when the run is queued, which during that outage read 04:24Z
for a page that did not change until 11:54Z.

A run in flight past **75 minutes** is treated as wedged rather than as proof of life. It is
cancelled before the new link is dispatched, because the workflow's concurrency group does
not cancel in progress and would otherwise hold the replacement shut behind it. Cancelling
also stops that run chaining onward, which is why the dispatch is not conditional.

#### It cannot live on the Desktop (2026-09-13)

launchd **cannot execute anything under `~/Desktop`**: the path is TCC-protected, and the
agent is not. Every invocation from install on 2026-08-27 until 2026-09-13 died before
reaching the script:

```
/bin/bash: .../chain-watchdog.sh: Operation not permitted
```

**2,423 consecutive failures, one per ten minutes for 16 days, every line identical.** The
backstop built for exactly this outage had never run once. (Same trap as the Fall Summit
dashboard's launchd job.)

So the agent runs an installed copy outside the protected path. The repo copy stays
canonical; after editing it, reinstall.

Install, or reinstall after an edit:

```bash
cp "chain-watchdog.sh" ~/Library/Application\ Support/PBIWebinarChain/ && cp "com.philglutting.pbi-webinar-chain.plist" ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.philglutting.pbi-webinar-chain.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.philglutting.pbi-webinar-chain.plist
```

Check it is alive. The middle column is the last exit status, and it must be `0`; **`126` is
the TCC failure above**, which is silent everywhere else:

```bash
launchctl list | grep pbi
```

It logs to `~/Library/Logs/pbi-webinar-chain.log`, and writes there only when it actually
restarts something, so a silent log is the healthy state. That is also what hid the failure
for 16 days: the log was not silent, nobody was reading it, and nothing else reported. Exit
status is the thing to check.

This is insurance, not the schedule. It only runs while the Mac is awake, which is why it
is the second line and the chain is the first: `launchd` runs one missed interval on wake,
so a lid closed overnight costs at most one check. Nothing local can conjure a runner out of
GitHub, so an outage like 09-13 still freezes the page; the watchdog shortens it rather than
preventing it.

On-demand from any device: the workflow's **Run workflow** button on the repo's Actions
tab (or the GitHub mobile app). `refresh.command` on the Mac now does a local pull + build
for the serve.py preview, then dispatches that same workflow via `gh workflow run`.

Notes that keep this honest:

- The repo is **public** (free plan; Pages needs it), so the scripts, workflow, and run
  logs are visible. The logs print the same spend/registration summaries the public page
  shows; the secrets themselves are encrypted and masked. Nothing generated is committed:
  the page deploys as a Pages artifact, not a file in the repo.
- `pull.py` and `build.py` now live in **two places**: this folder (canonical) and the
  deploy repo. After editing here, `cp` into `.deploy/` and push; that push itself
  triggers a redeploy.
- Transient failures are retried rather than allowed to kill a run. Meta answers a
  perfectly good request with a 400 or a 502 often enough to have broken roughly one
  hourly run in twenty; `curl --fail` used to collapse those into exit 22, which was not
  in `pull.py`'s retry list and discarded the response body, so the log read only
  "returned error: 400". `curl()` now reads the status and body, retries 429/5xx and any
  response whose Graph error code is transient (1, 2, 4, 17, 32, 341, 613) five times
  with jittered backoff, and lets a real fault (100 bad field, 190 dead token) fail at
  once. The code is what decides, not the status: Meta hangs the app rate limit on a 400
  on one call and a 403 on the next, and while the body check was gated on `status ==
  400` the 403 form skipped retries entirely and killed the 2026-08-19 21:36 run on its
  first attempt.
- Throttles back off on their own, much slower schedule: 60s, 150s, 300s, 300s instead of
  3s, 6s, 12s, 24s. The app-level bucket (code 4, subcode 1504022) refills over minutes,
  so the ordinary backoff spends all five attempts inside the same closed window. A
  throttled call can now cost about 13 minutes, which the hourly schedule absorbs; the
  job carries a 45-minute timeout so a bad hour cannot hold the `refresh` concurrency
  group against the next run.
- GitHub disables cron in repos with no commit activity for 60 days; the workflow's last
  step pushes an empty keepalive commit whenever the newest commit is older than 50 days.

The page always states the time of the pull it was built from, so a stale page reads as
stale rather than as wrong.

## Credentials

Both read-only. Local runs read the files; cloud runs read the repo's Actions Secrets
(same values, set 2026-08-14 via `gh secret set`).

| What | Local | Cloud | In the browser | Notes |
|---|---|---|---|---|
| Meta | `PBI 2/fb_token.txt` or `$FB_TOKEN` | secret `FB_TOKEN` | `localStorage.pbi_meta_token` | System user token, does not expire, `ads_read` |
| Hyros | `PBI 2/hyros_key.txt` or `$HYROS_API_KEY` | secret `HYROS_API_KEY` | `localStorage.pbi_hyros_key` | PBI-scoped; mode `600` |
| GoHighLevel | `PBI 2/ghl_key.txt` or `$GHL_API_KEY` | secret `GHL_API_KEY` (set 2026-09-28) | `localStorage.pbi_ghl_key` (optional) | Private Integration Token, location `GmBTEcbq9PN9YY99gncv`; without it in the browser, a live refresh leaves today and the week at build-time values |

The browser column is per device and per reader, entered through **Turn on live data** and
cleared by **Forget these keys** or by clearing site data. They are read by `live.js` and
sent to `graph.facebook.com` and `api.hyros.com` and nothing else. They are never
committed, never inlined into the page at build time, and never leave the device except as
an `access_token` parameter and an `API-Key` header on those two hosts. Both allow the
browser call directly.

The Lance key in `Lance Morgan 2/Hyros Lookups/` is scoped to **his** Hyros account and
returns an empty result for these campaigns. It is not a fallback.

## Branding

Palette and type follow **PBI Brand Standards v1.0 (2023)**, read from the brand sheet
rather than sampled off a page. Each colour has one job, and the colophon at the foot of
the page doubles as the legend:

| Colour | Hex | Pantone | Job |
|---|---|---|---|
| Onyx | `#212121` | 419 C | Ground |
| Orange | `#FF522B` | 172 C | Registrations, primary accent |
| Burgundy | `#720C3A` | 4074 C | The blended hero |
| Slate | `#38C5BA` | 3262 C | The video block |
| Citron | `#E4E439` | 396 C | Thin-data and stale warnings only |
| Orchid | `#B065C0` | 2067 C | Accent |
| Livid Grey | `#EAECEF` | 649 C | Surfaces |

### Chart colours are re-stepped, not the raw brand hexes

The brand palette **fails as data marks** and the validator says so: run
`node scripts/validate_palette.js "#FF522B,#720C3A,#38C5BA,#E4E439,#B065C0,#212121" --mode light`
from the `dataviz` skill and Slate comes back at 2.07:1 contrast against white, Citron at
1.32:1 (a Citron bar is invisible), Burgundy and Onyx fall outside the lightness band, and
Onyx has zero chroma so it reads as plain gray.

So the two daily charts use brand hues re-stepped until every check passes:

| Role | Light | Dark |
|---|---|---|
| Registrations | `#E8431B` | `#DE5433` |
| Ad spend | `#00918A` | `#12A296` |

Both pairs return ALL CHECKS PASS against their own surface. Dark is stepped separately,
not flipped: its lightness band is 0.48–0.67 against 0.43–0.77 for light.

Registrations and spend are **two charts, never two y-axes** — different scales. One
series per chart means the heading is the legend, and only the newest bar carries a
standing label so the axis survives a window of thirty days.

Official faces (Secret Squirrel, Knockout, Gunterz, Optika) are not licensed here, so the
page is set in **Playfair Display + Work Sans**, the substitution already approved for PBI
work. `build.py` uses a local `fonts/` when present, else
`../../ad-reporting-platform/fonts/`.

### The logo

PBI's own lockup, taken from their site on 2026-08-13 and trimmed to its ink:

| File | Source | Used |
|---|---|---|
| `brand/pbi-logo-reversed.png` | site footer cut, white type + orange brackets, 214x118 | the dark masthead |
| `brand/pbi-logo.png` | site header cut, dark type, 187x105 | print, where the ground turns white |

Both are inlined as data URIs, and the print stylesheet swaps one for the other. If the
files go missing the masthead falls back to type alone rather than shipping a stand-in
mark. Note the page fetches nothing at runtime: these are baked in at build time like
every creative.

`photographybusinessinstitute.com` returns **403 to curl** but loads fine in a browser, so
the assets were pulled through the browser tools rather than the command line.

## Reconciliation

Every pull re-asks Meta for the same filter and window at account level and compares it to
the sum of the ad rows, **graded per metric** rather than pass/fail. Summing ad rows never
quite equals the account figure: an ad deleted mid-window still counts at account level
while returning no ad row.

| Grade | Meaning |
|---|---|
| `exact` | identical to the account figure |
| `drift` | within `RECON_TOLERANCE_PCT` (1%), reported as normal attribution |
| `differs` | outside tolerance; the page says to treat the pull as suspect |

All five figures are checked, registrations included, because they now come from the same
Meta read as the spend rather than from a second system. Registrations get a second
cross-check as well: the ad rows are summed and compared to the campaign-level answer for
the same window, which should agree exactly unless an ad was deleted mid-window. Both, and
the split into the two halves, are printed into the page's Method note.

## Files

```
refresh.py      pull -> build -> dispatch cloud refresh   (what the button runs locally)
refresh.command double-clickable wrapper
serve.py        local server; makes the button real
pull.py         Meta Graph + Hyros -> data/YYYY-MM-DDTHH_webinar_snapshot.json
build.py        snapshot -> index.html (fonts + creatives + live.js inlined)
live.js         the published page's live refresh: pulls Meta + Hyros in the
                reader's browser and repaints in place. Mirrors pull.py.
chain-watchdog.sh   restarts the refresh chain if it ever stops; does nothing
                while it is healthy
com.philglutting.pbi-webinar-chain.plist
                launchd agent that runs the watchdog every 10 minutes
data/           one snapshot per pull, newest KEEP_SNAPSHOTS (48) retained
creative-cache/ downscaled ad creatives, keyed on image identity
.deploy/        clone of the Pages repo: scripts, fonts, logos, and the refresh
                workflow; the built page deploys as an artifact, never a commit
```

Creatives are downloaded and inlined as data URIs rather than hotlinked: Meta's
`image_url` is signed and expires within days. Each creative is inlined **once** into a JS
map and painted into every element that references it.

Video posters have two traps, both in `build.py`:

1. They need `curl -L`. Meta serves them from `facebook.com/ads/image/?d=...`, which 302s
   to the CDN; without the redirect every video silently loses its still.
2. **The cache key must not strip the query.** CDN images carry identity in the *path* and
   a rotating signature in the query, so keying on the path is right for them. Ad-image
   assets are the opposite: one shared path, identity in `?d=`. Keying on the path
   collapsed all 23 video ads to a single cache entry, so every video card showed
   whichever poster downloaded first. `cache_key()` picks the identifying part per host.

The 23 video ads resolve to **6** distinct posters, which is correct: six videos, each
duplicated across the AC 1 and AC 2 ad sets, every ad holding its own signed token for the
same underlying asset.
Featured cards use `object-fit: contain`: these creatives run 9:16, and cropping them to a
square centre reduces a vertical ad to a swatch of its background colour.

## Local preview

`.claude/launch.json` has a `pbi-webinar-dashboard` entry running `serve.py` on port 7771.
Previewing from a `file://` URL renders as a static snapshot that does not follow
scrolling, and the Refresh button has nothing to talk to, so use the server.
