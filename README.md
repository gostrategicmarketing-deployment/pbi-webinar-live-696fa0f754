# PBI Weekly Webinar dashboard

Self-refreshing GitHub Pages dashboard for the webinar campaigns in Meta ad account
37394393 (Joy of Marketing).

**Live:** <https://gostrategicmarketing-deployment.github.io/pbi-webinar-live-696fa0f754/>

The `refresh` workflow runs hourly (and on demand from the Actions tab): it pulls Meta and
Hyros, rebuilds `index.html` with every font and creative inlined, and deploys it straight
to Pages. Nothing generated is committed here.

Credentials are repository **Actions Secrets** (`FB_TOKEN`, `HYROS_API_KEY`), both
read-only keys. They reach the scripts only as environment variables during a run and are
masked in logs. No token exists in this repo or in the published page.

The full project (local preview server, design notes, snapshot history) lives in the
private workspace; this repo carries only what the cloud refresh needs.
