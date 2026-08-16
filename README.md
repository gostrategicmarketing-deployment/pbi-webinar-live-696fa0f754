# PBI Webinar Dashboard

A branded, client-facing dashboard for the webinar campaigns in Meta ad account
**37394393 (Joy of Marketing)**. Separate from the general PBI ad report in
`../2026-08-03 - Ad Reporting Platform/`, which covers both PBI accounts and every campaign.

**Live:** <https://gostrategicmarketing-deployment.github.io/pbi-webinar-live-696fa0f754/>

```bash
python3 refresh.py     # pull Meta + Hyros, rebuild, publish live   <- the one command
python3 serve.py       # local dashboard at :7771 with a working Refresh button
```

Or double-click **`refresh.command`** in Finder.

## The page, top to bottom

1. **Blended webinar registrations** — today only, the hero. Every registration Hyros
   credits today from every source, against today's ad spend. This is the number to
   steer on.
2. **Creative performance over [Since launch | Last 7 days]** — the window selector,
   deliberately below the hero so it clearly governs the creative and tables, not today.
3. **Best images** and **Best videos** — top five of each format, ranked on the same
   Hyros registrations reported in the hero.
4. **Day by day**, **By campaign**, **Every ad**, **Method**, **Brand palette**.

## One registration number, everywhere

Every registration figure on the page, from the hero down to a single creative, is the
**Hyros** count under last-click attribution. Meta's Lead pixel is not used for any
ranking; it is kept per ad as `meta_pixel_leads` for reference only.

That is not a cosmetic choice. Over 2026-08-11..13 Hyros credited **155** registrations at
ad level against the pixel's **52**, and it *reorders* the board rather than just scaling
it: `Video 06 | AC 2 | H1 | Ad #5` reads 2 registrations on the pixel and **78** on Hyros,
taking it from mid-table to the single best creative in the account. Ranking on the pixel
would have hidden the winner.

Spend and link clicks stay Meta's, and cost per registration pairs Meta spend with Hyros
registrations at every level, so an ad's figure and the day's figure are built the same
way.

Hyros `clicks` matched Meta `inline_link_clicks` exactly for a single day (296) but not
over three (1792 against 1060), so link clicks are always read from Meta.

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

Meta reports on the ad account's **America/Los_Angeles** clock; Hyros on its own
**-05:00 Central** clock. The two "today" boundaries sit two hours apart, so late-evening
figures can disagree slightly until both days close. The page says so in the hero.

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

`refresh.py` pulls Meta, pulls Hyros, rebuilds the local `index.html` (for the serve.py
preview), then dispatches the deploy repo's `refresh` workflow, which does its own pull +
build + deploy in the cloud (`build_type=workflow`; see the Schedule section).

The **Refresh button on the page** is real when the page is served by `serve.py`: it POSTs
`/refresh`, which runs the above, then reloads. On the published copy it cannot be, because
a static page reaching Meta and Hyros would mean shipping both API keys into a public HTML
file. There the button re-checks for a newer published build and says where a real refresh
comes from.

### Schedule (moved to GitHub Actions 2026-08-14)

The deploy repo's own **`refresh` workflow** pulls Meta + Hyros, rebuilds and deploys the
page hourly (cron minute 8, UTC; GitHub can lag a scheduled run by a few minutes). The Mac
plays no part: credentials live in the repo's **Actions Secrets** (`FB_TOKEN`,
`HYROS_API_KEY`, both read-only), reaching the scripts only as env vars during a run.

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
- GitHub disables cron in repos with no commit activity for 60 days; the workflow's last
  step pushes an empty keepalive commit whenever the newest commit is older than 50 days.

The page always states the time of the pull it was built from, so a stale page reads as
stale rather than as wrong.

## Credentials

Both are read-only. Local runs read the files; cloud runs read the repo's Actions
Secrets (same values, set 2026-08-14 via `gh secret set`).

| What | Local | Cloud | Notes |
|---|---|---|---|
| Meta | `PBI 2/fb_token.txt` or `$FB_TOKEN` | secret `FB_TOKEN` | System user token, does not expire, `ads_read` |
| Hyros | `PBI 2/hyros_key.txt` or `$HYROS_API_KEY` | secret `HYROS_API_KEY` | PBI-scoped; mode `600` |

The Lance key in `Lance Morgan 2/Hyros Lookups/` is scoped to **his** Hyros account and
returns an empty result for these campaigns. It is not a fallback.

`hyros_seed.json` holds figures fetched through the Hyros MCP and is only read when the
REST call cannot run. It carries its own date, so the hero greys itself out and says
"Stale" rather than passing old numbers off as today's.

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

Only the two Meta-sourced figures are checked against Meta. Registrations get their own
cross-check inside Hyros: the ad rows are summed and compared to the campaign-level answer
for the same window (155 against 153 on 2026-08-13, a 1.3% gap). Both, plus the Meta pixel
count for contrast, are printed into the page's Method note.

## Files

```
refresh.py      pull -> build -> dispatch cloud refresh   (what the button runs)
refresh.command double-clickable wrapper
serve.py        local server; makes the button real
pull.py         Meta Graph + Hyros -> data/YYYY-MM-DDTHH_webinar_snapshot.json
build.py        snapshot -> index.html (fonts + creatives inlined)
hyros_seed.json MCP-fetched fallback, self-dating
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
