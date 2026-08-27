/* ============================================================================
   live.js — the Refresh button, run entirely in the reader's browser.

   The published page is a static file on GitHub Pages. It cannot hold the Meta
   or Hyros credentials, because the repo is public and a key in the HTML is a
   key on the open internet. So the credentials live in the *reader's* browser:
   entered once, kept in localStorage on that device, sent only to Meta and to
   Hyros. Nothing is transmitted anywhere else, and nothing is written back to
   the repo.

   A reader who has not entered keys (the client, on any device) sees exactly the
   page they saw before: the newest hourly build, with the button falling back to
   its old job of checking whether a newer build has been published.

   WHAT A LIVE REFRESH COVERS
     today's blended box, the open weekly cycle, and all three windows: totals,
     campaigns, day-by-day, every ad, and the featured creative cards.

   WHAT IT DOES NOT
     previous (closed) weekly cycles, and the reconciliation lines in Method.
     Closed cycles do not move, and reconciliation is an audit of the Python
     pull rather than a figure. Both keep their build-time values and the page
     says so after a live refresh rather than implying they were re-read.

   FIDELITY
     Every call here mirrors pull.py: the same Graph fields, the same filter,
     the same lowercase Hyros parameters (`last_click`, `facebook_ad`, not the
     camel- or upper-case forms, which this endpoint rejects), the same
     Hyros-over-pixel rule, and the same ranking. If pull.py changes, this
     changes with it or the two will quietly disagree.
   ========================================================================== */
(function () {
  'use strict';

  var CFG = window.LIVE_CFG || {};
  var GRAPH = 'https://graph.facebook.com/v21.0';
  var HYROS = 'https://api.hyros.com/v1/api/v1.0';
  var LS_META = 'pbi_meta_token';
  var LS_HYROS = 'pbi_hyros_key';
  var THIN_SPEND = CFG.thin_spend || 25;
  var THIN_CLICKS = CFG.thin_clicks || 20;
  var FEATURED = CFG.featured_n || 5;
  var WEEK_HOUR = 12;

  /* ---------------------------------------------------------- credentials */

  function creds() {
    return {
      meta: (localStorage.getItem(LS_META) || '').trim(),
      hyros: (localStorage.getItem(LS_HYROS) || '').trim()
    };
  }
  function armed() { var c = creds(); return !!(c.meta && c.hyros); }

  /* ------------------------------------------- formatting, as in build.py */

  function money(v, dp) {
    if (v === null || v === undefined || v === '') return '—';
    dp = (dp === undefined) ? 2 : dp;
    return '$' + Number(v).toLocaleString('en-US',
      { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function num(v) {
    if (v === null || v === undefined || v === '') return '—';
    return Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  function pctf(v, dp) {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(dp === undefined ? 2 : dp) + '%';
  }
  function r2(v) { return Math.round(v * 100) / 100; }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function fmtDay(iso) {
    var p = iso.split('-');
    return MON[+p[1] - 1] + ' ' + (+p[2]) + ', ' + p[0];
  }
  function pad(x) { return String(x).padStart(2, '0'); }

  /** build.py's short_name: everything after the third pipe. */
  function shortName(name) {
    var parts = String(name || '').split('|');
    if (parts.length < 4) return String(name || '').trim();
    return parts.slice(3).join('|').trim() || String(name || '').trim();
  }

  /* ------------------------------------------------------ timezone helpers */

  function partsIn(tz, d) {
    var f = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    });
    var o = {};
    f.formatToParts(d).forEach(function (p) { if (p.type !== 'literal') o[p.type] = p.value; });
    return { y: +o.year, m: +o.month, d: +o.day,
             H: +(o.hour === '24' ? '0' : o.hour), M: +o.minute, S: +o.second };
  }
  function tzOffsetMs(tz, d) {
    var p = partsIn(tz, d);
    return Date.UTC(p.y, p.m - 1, p.d, p.H, p.M, p.S) - (d.getTime() - d.getMilliseconds());
  }
  /** The instant at a given wall-clock time in a given zone. DST-safe by iteration. */
  function zoned(tz, y, m, d, H, M) {
    var t = Date.UTC(y, m - 1, d, H || 0, M || 0, 0);
    for (var i = 0; i < 3; i++) {
      t = Date.UTC(y, m - 1, d, H || 0, M || 0, 0) - tzOffsetMs(tz, new Date(t));
    }
    return new Date(t);
  }
  function ymd(tz, d) { var p = partsIn(tz, d); return p.y + '-' + pad(p.m) + '-' + pad(p.d); }
  function addDays(iso, k) {
    var p = iso.split('-').map(Number);
    return new Date(Date.UTC(p[0], p[1] - 1, p[2] + k)).toISOString().slice(0, 10);
  }
  function dayDiff(a, b) {
    var x = a.split('-').map(Number), y = b.split('-').map(Number);
    return Math.round((Date.UTC(y[0], y[1] - 1, y[2]) - Date.UTC(x[0], x[1] - 1, x[2])) / 864e5);
  }
  function tzAbbrev(tz, d) {
    var p = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
      .formatToParts(d).filter(function (x) { return x.type === 'timeZoneName'; });
    return p.length ? p[0].value : '';
  }
  /** '2026-08-24T12:00:00-05:00' — the shape Hyros and pull.py both use. */
  function isoOffset(tz, d, withSeconds) {
    var p = partsIn(tz, d);
    var off = Math.round(tzOffsetMs(tz, d) / 60000);
    var sign = off >= 0 ? '+' : '-', a = Math.abs(off);
    return p.y + '-' + pad(p.m) + '-' + pad(p.d) + 'T' + pad(p.H) + ':' + pad(p.M)
      + (withSeconds ? ':' + pad(p.S) : '')
      + sign + pad(Math.floor(a / 60)) + ':' + pad(a % 60);
  }
  /** build.py's fmt_dt: 'Mon Aug 10, 12:00 PM CDT'. */
  function fmtDT(tz, d) {
    var p = partsIn(tz, d);
    var dow = DOW[new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay()];
    var h = p.H % 12 || 12, ampm = p.H < 12 ? 'AM' : 'PM';
    return dow + ' ' + MON[p.m - 1] + ' ' + p.d + ', ' + h + ':' + pad(p.M) + ' '
      + ampm + ' ' + tzAbbrev(tz, d);
  }
  /** build.py's fmt_stamp: 'Aug 13, 2026 at 2:29 PM PDT'. */
  function fmtStamp(tz, d) {
    var p = partsIn(tz, d);
    var h = p.H % 12 || 12, ampm = p.H < 12 ? 'AM' : 'PM';
    return fmtDay(p.y + '-' + pad(p.m) + '-' + pad(p.d)) + ' at ' + h + ':' + pad(p.M)
      + ' ' + ampm + ' ' + tzAbbrev(tz, d);
  }

  /* ---------------------------------------------------------- fetch layer */

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function qs(params) {
    var u = new URLSearchParams();
    Object.keys(params).forEach(function (k) { u.set(k, params[k]); });
    return u.toString();
  }

  /** Transient Graph codes, same list pull.py retries on. */
  var GRAPH_TRANSIENT = [1, 2, 4, 17, 32, 341, 613];

  async function graph(path, params) {
    var p = Object.assign({}, params, { access_token: creds().meta });
    var url = GRAPH + path + '?' + qs(p);
    var lastErr = 'no attempt made';
    for (var attempt = 0; attempt < 4; attempt++) {
      var r, j;
      try {
        r = await fetch(url, { cache: 'no-store' });
        j = await r.json().catch(function () { return {}; });
      } catch (e) {
        lastErr = 'network error';
        await sleep(1500 * Math.pow(2, attempt));
        continue;
      }
      if (r.ok && !j.error) return j;
      var code = j.error && j.error.code;
      lastErr = (j.error && j.error.message) || ('HTTP ' + r.status);
      // A real fault (100 bad field, 190 dead token) fails at once; a throttle waits.
      if (GRAPH_TRANSIENT.indexOf(code) === -1 && r.status < 500) break;
      await sleep((code === 4 || code === 17 || code === 32 ? 20000 : 2000) * (attempt + 1));
    }
    throw new Error('Meta: ' + lastErr);
  }

  async function graphAll(path, params) {
    var j = await graph(path, params);
    var out = (j.data || []).slice(), guard = 0;
    while (j.paging && j.paging.next && guard++ < 40) {
      var r = await fetch(j.paging.next, { cache: 'no-store' });
      j = await r.json();
      if (j.error) throw new Error('Meta: ' + j.error.message);
      out = out.concat(j.data || []);
    }
    return out;
  }

  /**
   * One Hyros /attribution read, in pull.py's parameter casing.
   *
   * A failure here is NOT neutral: an empty result reads downstream as *zero
   * registrations*, which would silently understate every figure on the page.
   * So this throws after its retries rather than degrading, and the whole
   * refresh aborts with the numbers left as they were.
   */
  async function hyrosCall(level, ids, since, until) {
    if (!ids || !ids.length) return [];
    var url = HYROS + '/attribution?' + qs({
      startDate: since, endDate: until,
      attributionModel: 'last_click',
      level: level,
      fields: 'leads,cost,clicks',
      ids: ids.join(',')
    });
    var lastErr = 'no attempt made';
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        var r = await fetch(url, { cache: 'no-store', headers: { 'API-Key': creds().hyros } });
        if (r.ok) {
          var j = await r.json();
          return j.result || [];
        }
        lastErr = 'HTTP ' + r.status;
        if (r.status === 401 || r.status === 403) break;   // a wrong key will not fix itself
      } catch (e) {
        lastErr = 'network error';
      }
      if (attempt < 2) await sleep(1200 * Math.pow(2, attempt));
    }
    throw new Error('Hyros: ' + lastErr);
  }

  /** Hyros rejects ids=ALL, so ad reads go in batches, as in pull.py. */
  async function hyrosById(level, ids, since, until, batch) {
    batch = batch || 20;
    var out = {};
    for (var i = 0; i < ids.length; i += batch) {
      var rows = await hyrosCall(level, ids.slice(i, i + batch), since, until);
      rows.forEach(function (r) {
        if (!r || typeof r !== 'object') return;
        out[String(r.id)] = {
          leads: parseInt(r.leads || 0, 10),
          cost: r2(parseFloat(r.cost || 0)),
          clicks: parseInt(r.clicks || 0, 10)
        };
      });
    }
    return out;
  }

  async function hyrosRange(campaignIds, since, until) {
    var rows = (await hyrosCall('facebook_campaign', campaignIds, since, until))
      .filter(function (r) { return r && typeof r === 'object'; });
    if (!rows.length) return null;
    return {
      leads: rows.reduce(function (a, r) { return a + parseInt(r.leads || 0, 10); }, 0),
      cost: r2(rows.reduce(function (a, r) { return a + parseFloat(r.cost || 0); }, 0)),
      clicks: rows.reduce(function (a, r) { return a + parseInt(r.clicks || 0, 10); }, 0)
    };
  }

  /* --------------------------------------------------------- Meta reading */

  var ACT = 'act_' + String(CFG.account_id || '').replace(/^act_/, '');
  function filterJSON() {
    return JSON.stringify([{ field: 'campaign.name', operator: 'CONTAIN', value: CFG.campaign_match }]);
  }
  function acts(row) {
    var o = {};
    (row.actions || []).forEach(function (a) { o[a.action_type] = parseFloat(a.value || 0); });
    return o;
  }
  function fnum(row, field) { return parseFloat(row[field] || 0) || 0; }

  function insights(level, since, until, extra) {
    return graphAll('/' + ACT + '/insights', {
      level: level,
      fields: 'campaign_id,campaign_name,spend,impressions,clicks,actions,inline_link_clicks'
        + (extra || ''),
      filtering: filterJSON(),
      time_range: JSON.stringify({ since: since, until: until }),
      limit: 500
    });
  }

  async function metaDaily(since, until) {
    var rows = await graphAll('/' + ACT + '/insights', {
      level: 'account',
      fields: 'spend,inline_link_clicks,actions',
      filtering: filterJSON(),
      time_range: JSON.stringify({ since: since, until: until }),
      time_increment: 1,
      limit: 500
    });
    return rows.map(function (r) {
      return {
        date: r.date_start,
        spend: r2(fnum(r, 'spend')),
        link_clicks: parseInt(fnum(r, 'inline_link_clicks'), 10),
        meta_pixel_leads: parseInt(acts(r)[CFG.lead_action] || 0, 10)
      };
    });
  }

  /** Spend and link clicks between two instants, from Meta's hourly buckets. */
  async function metaInstantRange(opened, closed) {
    var tz = CFG.account_tz;
    var rows = await graphAll('/' + ACT + '/insights', {
      level: 'account',
      fields: 'spend,inline_link_clicks',
      breakdowns: 'hourly_stats_aggregated_by_advertiser_time_zone',
      filtering: filterJSON(),
      time_range: JSON.stringify({ since: ymd(tz, opened), until: ymd(tz, closed) }),
      time_increment: 1,
      limit: 500
    });
    var spend = 0, clicks = 0, hours = 0;
    rows.forEach(function (r) {
      var bucket = r.hourly_stats_aggregated_by_advertiser_time_zone || '';
      var hour = parseInt(bucket.slice(0, 2), 10);
      if (isNaN(hour)) return;
      var p = r.date_start.split('-').map(Number);
      var stamp = zoned(tz, p[0], p[1], p[2], hour, 0);
      if (stamp >= opened && stamp < closed) {
        spend += fnum(r, 'spend');
        clicks += parseInt(fnum(r, 'inline_link_clicks'), 10);
        hours += 1;
      }
    });
    return { spend: r2(spend), clicks: clicks, hours: hours };
  }

  async function creativeMap(adIds) {
    var out = {};
    for (var i = 0; i < adIds.length; i += 40) {
      var chunk = adIds.slice(i, i + 40);
      try {
        var d = await graph('/', {
          ids: chunk.join(','),
          fields: 'id,creative{image_url,thumbnail_url,object_type,video_id}'
        });
        Object.keys(d).forEach(function (adId) {
          var c = (d[adId] && d[adId].creative) || {};
          out[adId] = {
            format: (c.video_id || c.object_type === 'VIDEO') ? 'VIDEO' : 'IMAGE',
            thumb: c.image_url || c.thumbnail_url || ''
          };
        });
      } catch (e) { /* thumbnails are cosmetic: never fail the report over them */ }
    }
    return out;
  }

  /* -------------------------------------------------------------- shaping */

  function metrics(spend, leads, clicks) {
    return {
      spend: r2(spend),
      leads: leads,
      link_clicks: clicks,
      cost_per_lead: leads ? r2(spend / leads) : null,
      cost_per_link_click: clicks ? r2(spend / clicks) : null
    };
  }
  function rankSort(ads) {
    return ads.slice().sort(function (a, b) {
      if (b.leads !== a.leads) return b.leads - a.leads;
      var ac = a.cost_per_lead === null ? 9e9 : a.cost_per_lead;
      var bc = b.cost_per_lead === null ? 9e9 : b.cost_per_lead;
      if (ac !== bc) return ac - bc;
      return b.spend - a.spend;
    });
  }
  function isThin(a) { return a.spend < THIN_SPEND || a.link_clicks < THIN_CLICKS; }

  /** build.py's featured_block: top of one format, backfilled on link clicks. */
  function featuredPick(ads, fmt) {
    var pool = ads.filter(function (a) { return (a.format || 'IMAGE') === fmt; });
    if (!pool.length) return { picked: [], pool: 0 };
    var picked = pool.filter(function (a) { return a.leads > 0; }).slice(0, FEATURED);
    if (picked.length < FEATURED) {
      var rest = pool.filter(function (a) { return picked.indexOf(a) === -1; })
        .sort(function (x, y) { return y.link_clicks - x.link_clicks; });
      picked = picked.concat(rest.slice(0, FEATURED - picked.length));
    }
    return { picked: picked, pool: pool.length };
  }

  /* ------------------------------------------------------- the whole pull */

  async function pullWindow(key, since, until, campaignIds, dailyAll, hyDailyAll, say) {
    say('Reading ' + CFG.window_labels[key].toLowerCase() + ' from Meta');

    var adRows = await insights('ad', since, until, ',adset_name,ad_id,ad_name');
    var ads = adRows.map(function (r) {
      var a = acts(r);
      return Object.assign({
        ad_id: r.ad_id,
        ad_name: r.ad_name,
        adset_name: r.adset_name,
        campaign_name: r.campaign_name,
        impressions: parseInt(fnum(r, 'impressions'), 10),
        meta_pixel_leads: parseInt(a[CFG.lead_action] || 0, 10)
      }, metrics(fnum(r, 'spend'), parseInt(a[CFG.lead_action] || 0, 10),
                 parseInt(fnum(r, 'inline_link_clicks'), 10)));
    });

    say('Reading ' + CFG.window_labels[key].toLowerCase() + ' registrations from Hyros');
    var hyAds = await hyrosById('facebook_ad', ads.map(function (a) { return a.ad_id; }),
                                since, until);
    ads.forEach(function (a) {
      var h = hyAds[a.ad_id];
      a.leads = h ? h.leads : 0;
      a.cost_per_lead = a.leads ? r2(a.spend / a.leads) : null;
      a.thin = isThin(a);
    });

    var campRows = await insights('campaign', since, until);
    var winIds = campRows.map(function (r) { return String(r.campaign_id); });
    if (!winIds.length) winIds = campaignIds;
    var hyCamps = await hyrosById('facebook_campaign', winIds, since, until);
    var campaigns = campRows.map(function (r) {
      var cid = String(r.campaign_id);
      var leads = (hyCamps[cid] || {}).leads || 0;
      return Object.assign({ campaign_id: cid, campaign_name: r.campaign_name },
        metrics(fnum(r, 'spend'), leads, parseInt(fnum(r, 'inline_link_clicks'), 10)));
    }).sort(function (a, b) { return b.spend - a.spend; });

    var totals = metrics(
      ads.reduce(function (s, a) { return s + a.spend; }, 0),
      ads.reduce(function (s, a) { return s + a.leads; }, 0),
      ads.reduce(function (s, a) { return s + a.link_clicks; }, 0));

    var daily = dailyAll.filter(function (d) { return d.date >= since && d.date <= until; })
      .map(function (d) {
        return Object.assign({}, d, {
          leads: hyDailyAll ? (hyDailyAll[d.date] || 0) : 0,
          hyros: !!hyDailyAll
        });
      });

    return {
      key: key, since: since, until: until,
      days: dayDiff(since, until) + 1,
      totals: totals, campaigns: campaigns, daily: daily, ads: rankSort(ads)
    };
  }

  async function weekCycle(campaignIds, opened, closed, now) {
    var apiClose = closed > now ? now : closed;
    var m = await metaInstantRange(opened, apiClose);
    var dayIds = (await insights('campaign', ymd(CFG.account_tz, opened),
                                 ymd(CFG.account_tz, apiClose)))
      .map(function (r) { return String(r.campaign_id); });
    var ids = dayIds.length ? dayIds : campaignIds;
    var hy = await hyrosRange(ids, isoOffset(CFG.week_tz, opened, true),
                              isoOffset(CFG.week_tz, apiClose, true));
    var leads = hy ? hy.leads : 0;
    return {
      opened: opened, closed: closed, api_closed: apiClose,
      closing_now: closed > now,
      elapsed_hours: Math.floor((apiClose - opened) / 36e5),
      total_hours: Math.round((closed - opened) / 36e5),
      buckets: m.hours,
      tz_abbrev: tzAbbrev(CFG.week_tz, opened),
      spend: m.spend,
      link_clicks: m.clicks,
      cost_per_link_click: m.clicks ? r2(m.spend / m.clicks) : null,
      leads: leads,
      have: !!leads,
      cost_per_lead: leads ? r2(m.spend / leads) : null,
      conv_rate: m.clicks ? r2(leads / m.clicks * 100) : null
    };
  }

  /** The noon-Central Monday cycle currently open. */
  function weekBounds(now) {
    var p = partsIn(CFG.week_tz, now);
    var dow = new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay();
    var backToMonday = (dow + 6) % 7;                 // Monday = 0
    var mon = addDays(p.y + '-' + pad(p.m) + '-' + pad(p.d), -backToMonday).split('-').map(Number);
    var opened = zoned(CFG.week_tz, mon[0], mon[1], mon[2], WEEK_HOUR, 0);
    // Before noon on a Monday the open cycle is still the earlier one. Re-anchor off the
    // instant rather than off arithmetic on the date string, so a DST changeover inside
    // the week cannot shift the boundary by an hour.
    if (now < opened) {
      var b = partsIn(CFG.week_tz, new Date(opened.getTime() - 7 * 864e5));
      opened = zoned(CFG.week_tz, b.y, b.m, b.d, WEEK_HOUR, 0);
    }
    var cp = partsIn(CFG.week_tz, new Date(opened.getTime() + 7 * 864e5));
    return { opened: opened, closed: zoned(CFG.week_tz, cp.y, cp.m, cp.d, WEEK_HOUR, 0) };
  }

  async function pullSnapshot(say) {
    var now = new Date();
    var tz = CFG.account_tz;
    var today = ymd(tz, now);

    say('Listing webinar campaigns');
    var camps = (await graphAll('/' + ACT + '/campaigns',
      { fields: 'id,name,effective_status', limit: 200 }))
      .filter(function (c) {
        return (c.name || '').toLowerCase().indexOf(CFG.campaign_match) !== -1;
      });
    var live = camps.filter(function (c) { return c.effective_status === 'ACTIVE'; });
    var liveIds = (live.length ? live : camps.slice(0, 2)).map(function (c) { return c.id; });

    // Trailing windows never reach back before the program launched.
    function back(days) {
      var d = addDays(today, -(days - 1));
      return d > CFG.launch ? d : CFG.launch;
    }
    var spans = { '3d': [back(3), today], '7d': [back(7), today], launch: [CFG.launch, today] };
    var widest = spans.launch[0];

    // The daily strip and Hyros-per-day are pulled once over the widest span and
    // sliced per window: three separate sweeps would be the same data three times,
    // and Hyros has no day grouping, so each day is its own call.
    say('Reading the daily strip from Meta');
    var dailyAll = await metaDaily(widest, today);

    var dayCount = dayDiff(widest, today) + 1;
    var hyDailyAll = null;
    if (dayCount <= 31) {
      hyDailyAll = {};
      for (var i = 0; i < dayCount; i++) {
        var d = addDays(widest, i);
        say('Reading registrations day by day from Hyros (' + (i + 1) + ' of ' + dayCount + ')');
        var rows = await hyrosCall('facebook_campaign', liveIds, d, d);
        hyDailyAll[d] = rows.reduce(function (a, r) { return a + parseInt((r && r.leads) || 0, 10); }, 0);
      }
    }

    var windows = {};
    for (var k of ['3d', '7d', 'launch']) {
      windows[k] = await pullWindow(k, spans[k][0], spans[k][1], liveIds, dailyAll, hyDailyAll, say);
    }

    say('Resolving creatives');
    var adIds = {};
    Object.keys(windows).forEach(function (k) {
      windows[k].ads.forEach(function (a) { adIds[a.ad_id] = 1; });
    });
    var cr = await creativeMap(Object.keys(adIds));
    Object.keys(windows).forEach(function (k) {
      windows[k].ads.forEach(function (a) {
        a.format = (cr[a.ad_id] || {}).format || 'IMAGE';
        a.thumb = (cr[a.ad_id] || {}).thumb || '';
      });
    });

    say('Reading today from Hyros');
    var dayRow = windows[CFG.default_window].daily.filter(function (d) { return d.date === today; })[0];
    var todayIds = (await insights('campaign', today, today))
      .map(function (r) { return String(r.campaign_id); });
    if (!todayIds.length) todayIds = liveIds;
    var hyToday = await hyrosRange(todayIds, today, today);
    var tSpend = dayRow ? dayRow.spend : 0;
    var tClicks = dayRow ? dayRow.link_clicks : 0;
    var tLeads = hyToday ? hyToday.leads : 0;
    var todayBox = {
      date: today,
      spend: tSpend,
      link_clicks: tClicks,
      cost_per_link_click: tClicks ? r2(tSpend / tClicks) : null,
      leads: tLeads,
      cost_per_lead: tLeads ? r2(tSpend / tLeads) : null,
      conv_rate: tClicks ? r2(tLeads / tClicks * 100) : null,
      have: !!tLeads,          // a zero count reads as "—", exactly as in build.py
      hyros_ok: !!hyToday      // whether Hyros answered at all, which greys the box
    };

    say('Reading the open week');
    var wb = weekBounds(now);
    var week = await weekCycle(liveIds, wb.opened, wb.closed, now);

    return {
      now: now, today: todayBox, week: week, windows: windows,
      live_count: live.length, matched: camps.length
    };
  }

  /* ------------------------------------------------------------- painting */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /** The six blended figures, in build.py's order. */
  function blendedValues(b) {
    return [
      b.have ? num(b.leads) : '—',
      b.cost_per_lead ? money(b.cost_per_lead) : '—',
      num(b.link_clicks),
      money(b.cost_per_link_click),
      money(b.spend, 2),
      b.conv_rate ? pctf(b.conv_rate) : '—'
    ];
  }

  function paintBox(box, b) {
    if (!box) return;
    var vals = blendedValues(b);
    var hero = $('.thero-value', box);
    if (hero) hero.textContent = vals[0];
    var cells = $$('.tgrid .tcell-value', box);
    cells.forEach(function (el, i) { if (vals[i + 1] !== undefined) el.textContent = vals[i + 1]; });
  }

  function paintToday(snap) {
    var box = $('#box-today');
    if (!box) return;
    paintBox(box, snap.today);
    var flag = $('.today-flag', box);
    if (flag) flag.textContent = 'Today · ' + fmtDay(snap.today.date);
    box.classList.toggle('today-stale', !snap.today.hyros_ok);
  }

  function paintWeek(snap) {
    var box = $('#box-week');
    if (!box) return;
    var w = snap.week;
    paintBox(box, w);
    var flag = $('.today-flag', box);
    if (flag) {
      flag.textContent = 'Weekly summary · ' + fmtDT(CFG.week_tz, w.opened)
        + ' – ' + fmtDT(CFG.week_tz, w.closed);
    }
    var note = $('.tnote', box);
    if (note) {
      note.innerHTML = w.closing_now
        ? '<span class="tflag">Open</span> ' + w.elapsed_hours + ' of ' + w.total_hours
          + ' hours counted, through ' + fmtDT(CFG.week_tz, w.api_closed) + '. It closes at '
          + fmtDT(CFG.week_tz, w.closed) + ' and will keep climbing until then. Spend and link '
          + 'clicks are sliced from Meta’s hourly figures so the noon boundary is exact; '
          + 'registrations are Hyros.'
        : 'A complete cycle: ' + w.total_hours + ' hours, ' + fmtDT(CFG.week_tz, w.opened)
          + ' to ' + fmtDT(CFG.week_tz, w.closed) + '. Spend and link clicks are sliced from '
          + 'Meta’s hourly figures so the noon boundary is exact; registrations are Hyros.';
    }
    box.classList.toggle('week-open', !!w.closing_now);
  }

  /** Set the five stat spans inside a featured card. */
  function cardStats(a) {
    var cells = [
      [num(a.leads), 'registrations'],
      [money(a.cost_per_lead, 0), 'per registration'],
      [num(a.link_clicks), 'link clicks'],
      [money(a.cost_per_link_click), 'per link click'],
      [money(a.spend, 0), 'spent']
    ];
    return cells.map(function (c) {
      return '<span class="stat"><b>' + esc(c[0]) + '</b><i>' + c[1] + '</i></span>';
    }).join('');
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /**
   * Repaint one grid of featured cards.
   *
   * Cards are cloned from a card already on the page rather than written out
   * here, so build.py stays the only place this markup exists.
   */
  function paintCards(grid, picked, thumbs) {
    if (!grid) return;
    var template = grid.querySelector('.card') || document.querySelector('.card');
    if (!template) return;
    var existing = {};
    $$('.card', grid).forEach(function (c) { existing[c.dataset.ad] = c; });

    var out = picked.map(function (a, i) {
      var el = existing[a.ad_id] || template.cloneNode(true);
      el.dataset.ad = a.ad_id;
      el.setAttribute('aria-label',
        'Open creative and full numbers for ' + a.ad_name);
      var head = $('.card-head', el);
      if (head) head.title = a.ad_name;
      var rank = $('.card-rank', el);
      if (rank) rank.textContent = pad(i + 1);
      var nm = $('.card-name', el);
      if (nm) nm.textContent = shortName(a.ad_name);

      var imgWrap = $('.card-img', el);
      if (imgWrap) {
        var src = thumbs[a.ad_id] || a.thumb || '';
        imgWrap.innerHTML = src
          ? '<img data-cr="' + esc(a.ad_id) + '" alt="" src="' + esc(src) + '">'
          : '<span class="card-missing">creative unavailable</span>';
        if (a.format === 'VIDEO') {
          imgWrap.insertAdjacentHTML('beforeend', '<span class="play" aria-hidden="true"></span>');
        }
      }
      var set = $('.card-set', el);
      if (set) {
        set.innerHTML = esc(a.adset_name)
          + (a.thin ? '<span class="chip chip-thin">thin data</span>' : '');
      }
      var stats = $('.card-stats', el);
      if (stats) stats.innerHTML = cardStats(a);
      return el;
    });

    grid.innerHTML = '';
    out.forEach(function (el) { grid.appendChild(el); });
  }

  /** Rows are cloned and patched, never authored here. */
  function paintRows(tbody, items, fill) {
    if (!tbody) return;
    var template = tbody.querySelector('tr');
    if (!template) return;
    var existing = {};
    $$('tr', tbody).forEach(function (tr) {
      if (tr.dataset.key) existing[tr.dataset.key] = tr;
    });
    var out = items.map(function (item) {
      var key = item.__key;
      var tr = existing[key] || template.cloneNode(true);
      tr.dataset.key = key;
      fill(tr, item);
      return tr;
    });
    tbody.innerHTML = '';
    out.forEach(function (tr) { tbody.appendChild(tr); });
  }

  function numCells(tr) {
    return $$('td.num', tr);
  }

  /**
   * The thumbnail cell. A creative that has one gets a real <button>; one that does not
   * gets an inert <span>, as in build.py's thumb_button. A row cloned from the wrong
   * kind has its element swapped rather than just restyled, so nothing ends up looking
   * clickable while doing nothing.
   */
  function setThumb(td, a, src) {
    if (!td) return;
    var cur = td.querySelector('.thumb');
    var want = src ? 'BUTTON' : 'SPAN';
    if (!cur || cur.tagName !== want) {
      var el = document.createElement(want.toLowerCase());
      if (cur) td.replaceChild(el, cur); else td.appendChild(el);
      cur = el;
    }
    if (src) {
      cur.className = 'thumb';
      cur.type = 'button';
      cur.dataset.ad = a.ad_id;
      cur.setAttribute('aria-label', 'Open creative for ' + a.ad_name);
      if (a.format === 'VIDEO') cur.dataset.video = '1';
      else delete cur.dataset.video;
      cur.innerHTML = '<img data-cr="' + esc(a.ad_id) + '" alt="" src="' + esc(src) + '">';
    } else {
      cur.className = 'thumb thumb-missing';
      cur.setAttribute('aria-hidden', 'true');
      delete cur.dataset.ad;
      cur.innerHTML = '';
    }
  }

  function paintWindow(key, w, thumbs) {
    var root = $('#win-' + key);
    if (!root) return;

    var imgs = featuredPick(w.ads, 'IMAGE');
    var vids = featuredPick(w.ads, 'VIDEO');
    paintCards($('#cards-' + key + '-image'), imgs.picked, thumbs);
    paintCards($('#cards-' + key + '-video'), vids.picked, thumbs);
    var poolImg = $('#pool-' + key + '-image');
    if (poolImg) poolImg.textContent = imgs.pool;
    var poolVid = $('#pool-' + key + '-video');
    if (poolVid) poolVid.textContent = vids.pool;

    // Campaigns
    paintRows($('#camp-' + key), w.campaigns.map(function (c) {
      c.__key = c.campaign_id; return c;
    }), function (tr, c) {
      var nameCell = $('td.name', tr);
      if (nameCell) nameCell.textContent = c.campaign_name;
      var cells = numCells(tr);
      var vals = [num(c.leads), money(c.cost_per_lead), num(c.link_clicks),
                  money(c.cost_per_link_click), money(c.spend)];
      cells.forEach(function (td, i) { if (vals[i] !== undefined) td.textContent = vals[i]; });
    });

    // Every ad
    var topLeads = Math.max.apply(null, w.ads.map(function (a) { return a.leads; }).concat([0])) || 1;
    paintRows($('#ads-' + key), w.ads.map(function (a) { a.__key = a.ad_id; return a; }),
      function (tr, a) {
        tr.classList.toggle('zero', !a.leads);
        var src = thumbs[a.ad_id] || a.thumb || '';
        setThumb($('td.cell-thumb', tr), a, src);
        var nameCell = $('td.name', tr);
        if (nameCell) {
          nameCell.title = a.ad_name;
          var an = $('.ad-name', nameCell);
          if (an) {
            an.innerHTML = esc(shortName(a.ad_name))
              + '<span class="fmt fmt-' + (a.format === 'VIDEO' ? 'video' : 'image') + '">'
              + (a.format === 'VIDEO' ? 'Video' : 'Image') + '</span>';
          }
          var as = $('.ad-set', nameCell);
          if (as) as.textContent = a.adset_name || '';
        }
        var bar = $('.rbar', tr);
        if (bar) bar.style.setProperty('--w', (a.leads / topLeads * 100).toFixed(1) + '%');
        var rval = $('.rval', tr);
        if (rval) rval.textContent = num(a.leads);
        var cells = numCells(tr);
        // The registrations cell is .num.strong.bar-cell and is painted above;
        // the remaining four are cost/reg, clicks, cost/click, spent.
        var rest = cells.filter(function (td) { return !td.classList.contains('bar-cell'); });
        var vals = [money(a.cost_per_lead), num(a.link_clicks),
                    money(a.cost_per_link_click), money(a.spend)];
        rest.forEach(function (td, i) { if (vals[i] !== undefined) td.textContent = vals[i]; });

        // Keep the lightbox in step with what the table now says.
        if (window.ADS) {
          if (!window.ADS[a.ad_id]) {
            window.ADS[a.ad_id] = {
              name: a.ad_name, adset: a.adset_name, campaign: a.campaign_name,
              format: a.format, stats: {}, thin: {}
            };
          }
          window.ADS[a.ad_id].stats[key] = [
            ['Registrations', num(a.leads)],
            ['Cost per registration', money(a.cost_per_lead)],
            ['Link clicks', num(a.link_clicks)],
            ['Cost per link click', money(a.cost_per_link_click)],
            ['Total spent', money(a.spend)]
          ];
          window.ADS[a.ad_id].thin[key] = a.thin;
        }
        if (src && window.THUMBS && !window.THUMBS[a.ad_id]) window.THUMBS[a.ad_id] = src;
      });

    var adsN = $('#ads-n-' + key);
    if (adsN) adsN.textContent = w.ads.length;
    var leadN = $('#lead-n-' + key);
    if (leadN) leadN.textContent = w.ads.filter(function (a) { return a.leads; }).length;
    var zeroSpend = $('#zero-spend-' + key);
    if (zeroSpend) {
      zeroSpend.textContent = money(w.ads.filter(function (a) { return !a.leads; })
        .reduce(function (s, a) { return s + a.spend; }, 0));
    }

    paintCharts(key, w.daily);
  }

  /* ------------------------------------------------------------- the bars */

  function barPath(x, y, w, h, r) {
    r = Math.max(0, Math.min(r === undefined ? 4 : r, w / 2, h));
    return 'M' + x.toFixed(2) + ',' + (y + h).toFixed(2)
      + ' L' + x.toFixed(2) + ',' + (y + r).toFixed(2)
      + ' Q' + x.toFixed(2) + ',' + y.toFixed(2) + ' ' + (x + r).toFixed(2) + ',' + y.toFixed(2)
      + ' L' + (x + w - r).toFixed(2) + ',' + y.toFixed(2)
      + ' Q' + (x + w).toFixed(2) + ',' + y.toFixed(2) + ' ' + (x + w).toFixed(2) + ',' + (y + r).toFixed(2)
      + ' L' + (x + w).toFixed(2) + ',' + (y + h).toFixed(2) + ' Z';
  }

  function paintChart(fig, daily, field, unit) {
    if (!fig || !daily.length) return;
    var vals = daily.map(function (d) { return d[field]; });
    var top = Math.max.apply(null, vals) || 1;
    var count = daily.length;
    var W = 760, H = 190, PAD_L = 4, PAD_R = 4, PAD_T = 26, PAD_B = 30;
    var plotW = W - PAD_L - PAD_R, plotH = H - PAD_T - PAD_B;
    var slot = plotW / count;
    var bw = Math.min(46, slot - 8);
    var showEvery = count <= 10 ? 1 : Math.max(2, Math.round(count / 8));

    var out = [0, .5, 1].map(function (f) {
      var y = (PAD_T + plotH * f).toFixed(1);
      return '<line class="cgrid" x1="0" x2="' + W + '" y1="' + y + '" y2="' + y + '"/>';
    }).join('');

    daily.forEach(function (d, i) {
      var v = d[field];
      var h = top ? (v / top) * plotH : 0;
      var x = PAD_L + i * slot + (slot - bw) / 2;
      var y = PAD_T + plotH - h;
      var last = i === count - 1;
      var day = fmtDay(d.date);
      out += '<g class="cbar' + (last ? ' is-last' : '') + '" tabindex="0" role="listitem">'
        + '<title>' + esc(day) + ': ' + esc(unit(v)) + '</title>'
        + '<rect class="chit" x="' + (PAD_L + i * slot).toFixed(2) + '" y="' + PAD_T.toFixed(1)
        + '" width="' + slot.toFixed(2) + '" height="' + plotH.toFixed(1) + '"/>'
        + '<path class="cmark" d="' + barPath(x, y, bw, Math.max(h, 2)) + '"/>'
        + '<text class="cval" x="' + (x + bw / 2).toFixed(2) + '" y="'
        + Math.max(y - 8, 12).toFixed(1) + '">' + esc(unit(v)) + '</text></g>';
      if (i % showEvery === 0 || last) {
        out += '<text class="cday" x="' + (x + bw / 2).toFixed(2) + '" y="' + (H - 10).toFixed(1)
          + '">' + esc(day.split(',')[0]) + '</text>';
      }
    });

    var svg = fig.querySelector('svg');
    if (svg) svg.innerHTML = out;
    var peak = fig.querySelector('.chart-peak');
    if (peak) peak.textContent = 'peak ' + unit(top);
  }

  function paintCharts(key, daily) {
    var days = $('#days-' + key);
    if (!days) return;
    paintChart($('.chart.s-reg', days), daily, 'leads', num);
    paintChart($('.chart.s-spend', days), daily, 'spend', function (v) { return money(v, 0); });
  }

  function paint(snap) {
    var thumbs = window.THUMBS || {};
    paintToday(snap);
    paintWeek(snap);
    ['3d', '7d', 'launch'].forEach(function (k) {
      if (snap.windows[k]) paintWindow(k, snap.windows[k], thumbs);
    });
    var stamp = $('#stamp');
    if (stamp) stamp.textContent = fmtStamp(CFG.account_tz, snap.now);
    var band = $('#band-campaigns');
    if (band) band.textContent = snap.live_count + ' active';
    document.body.classList.add('is-live');
  }

  /* ------------------------------------------------------------ the dialog */

  function openKeyDialog(onSaved) {
    var veil = $('#keyveil');
    if (!veil) return;
    var c = creds();
    $('#key-meta').value = c.meta;
    $('#key-hyros').value = c.hyros;
    veil.hidden = false;
    $('#key-meta').focus();
    veil.__then = onSaved || null;
  }
  function closeKeyDialog() {
    var veil = $('#keyveil');
    if (veil) { veil.hidden = true; veil.__then = null; }
  }

  /* ------------------------------------------------------------- the button */

  var running = false;

  async function liveRefresh(ui) {
    if (running) return;
    running = true;
    var started = Date.now();
    ui.busy(true, 'Refreshing');
    try {
      var snap = await pullSnapshot(function (label) {
        ui.msg(esc(label) + '… <span class="live-note">reading Meta and Hyros directly '
          + 'from this browser</span>');
      });
      paint(snap);
      var secs = Math.round((Date.now() - started) / 1000);
      ui.msg('<b>Live</b> — pulled just now, in ' + secs + ' second'
        + (secs === 1 ? '' : 's') + ', straight from Meta and Hyros, into this browser. '
        + 'Two things below are still from the <b>' + esc(window.BUILD_STAMP || 'last')
        + '</b> build and say so rather than being repainted: <b>previous weeks</b>, which '
        + 'are closed cycles and do not move, and the <b>Method</b> notes, whose '
        + 'reconciliation audits that Python pull rather than these numbers.');
      ui.busy(false, 'Refresh');
    } catch (err) {
      ui.busy(false, 'Refresh');
      ui.msg('Live refresh failed — ' + esc(err.message) + '. The numbers below are '
        + 'unchanged, from the <b>' + esc(window.BUILD_STAMP || 'last') + '</b> build. '
        + (/Hyros/.test(err.message)
          ? 'A Hyros read that fails is not treated as zero registrations, so nothing was '
            + 'repainted. '
          : '')
        + '<a href="#" id="relink">Check the keys</a>.', true);
      var relink = $('#relink');
      if (relink) {
        relink.addEventListener('click', function (e) {
          e.preventDefault();
          openKeyDialog(function () { liveRefresh(ui); });
        });
      }
    } finally {
      running = false;
    }
  }

  /* Exposed so build.py's own refresh() can hand off without duplicating any of this. */
  window.PBILive = {
    armed: armed,
    refresh: liveRefresh,
    // Exposed so a repaint can be exercised, and inspected, without spending a pull.
    paint: paint,
    pull: pullSnapshot,
    openKeyDialog: openKeyDialog,
    closeKeyDialog: closeKeyDialog,
    saveKeys: function (meta, hyros) {
      localStorage.setItem(LS_META, meta.trim());
      localStorage.setItem(LS_HYROS, hyros.trim());
    },
    clearKeys: function () {
      localStorage.removeItem(LS_META);
      localStorage.removeItem(LS_HYROS);
    }
  };
}());
