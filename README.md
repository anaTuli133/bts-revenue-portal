# BTS-Wise Revenue Report — Web Portal

Live SQL version of the `July_26_BTS-Wise_Total_Revenue.xlsx` report. Queries
`VOICE_DATA_DETAILS_FINAL` on demand and pivots it into the same Site × Day
layout as the Excel workbook, styled to match the existing CDR Log Monitor
portal.

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open `http://<server>:5050/`.

## Before this will actually run against real data

Three things are placeholders on purpose — I didn't have the real values, so
don't guess-fill numbers that matter for a revenue report. Fix these in
`config.py`:

1. **Oracle credentials** (`ORACLE_CONFIG['user']` / `['password']`) — the
   `dwhadmin/dwhadmin` from the existing portal is an SSH login, not
   necessarily the Oracle DB login. Get the real DB account from your
   manager/DBA.

2. **Service name vs. SID** — I assumed `dwhdb01` is a Service Name. If it's
   actually a SID, swap `service_name=` for `sid=` in `db.py`'s
   `get_connection()` (there's a comment marking exactly where).

3. **Site geography join** — now wired up against the real table,
   `ZONE_DIM_FULL`. Two things worth knowing about how this works:
   - `ZONE_DIM_FULL` has one row per CGI/technology (several rows can share
     one `SITE_ID`), and the fact table's `SITE_OR_CGI` column can hold
     *either* a `SITE_ID` or a `CGI` value depending on which system/vendor
     produced that row. `db.py`'s `site_geo` CTE handles this by matching
     against both, `UNION`-ed together and de-duplicated per month.
   - This assumes Division/District/Upazila don't vary across the different
     technology rows for the same site (i.e. geography is a site-level
     attribute, not a per-CGI one). That should hold, but worth a spot
     check once real data is flowing — if it's ever wrong, the `UNION`
     would surface duplicate `KEY_VAL` rows with conflicting geography,
     which is easy to catch with `GROUP BY KEY_VAL HAVING COUNT(DISTINCT
     DIVISION) > 1` against `ZONE_DIM_FULL`.
   - The join uses a `LEFT JOIN`, not `INNER JOIN` — a site with no
     geography match still shows up in the report (with blank
     Division/District/Upazila) rather than silently disappearing from the
     revenue total. If you see blank-geography rows in practice, that's
     worth investigating rather than ignoring.
   - `ZONE_DIM_FULL.MONTH_KEY` is `VARCHAR2` (e.g. `'202607'`) while the
     fact table's `MONTH_KEY` is `NUMBER`. The query only uses the fact
     table's `DATE_VALUE` for date filtering and builds its own
     `YYYYMM` string to query `ZONE_DIM_FULL`, so this mismatch is handled
     — just flagging it since it'll bite you if you touch this query later.

## Revenue column mapping — verify this

`config.py`'s `CATEGORY_COLUMNS` maps each Excel tab to a fact-table column:

| Excel tab | Column used | Confidence |
|---|---|---|
| MOC | `VOICE_REV_TOT_MOC` | matches column name directly |
| MTC | `VOICE_REV_MTC` | matches column name directly |
| Data | `DATA_REV_TOT` | matches column name directly |
| SMS | `SMS_REV_TOT` | matches column name directly |
| GT (Grand Total) | `VOICE_REV_TOT_MOC + VOICE_REV_MTC + DATA_REV_TOT + SMS_REV_TOT` | **guessed** |

The table screenshot also had `VOICE_REVENUE`, `TOTAL_REVENUE_MOC_MTC`, and
`TOTAL_REVENUE_MOC` — these look like they might already be pre-computed
totals rather than something to re-derive. Worth checking with whoever wrote
the original Excel-generating query which column they actually summed for
the "GT Revenue" figures, rather than trusting my SUM expression.

## Site geography — confirmed

`ZONE_DIM_FULL` columns (confirmed): `SITE_ID`, `SITE_NAME`, `LONGITUDE`,
`LATITUDE`, `CELL_CODE`, `CGI`, `LAC`, `CI`, `CELL_NAME`, `FULL_ADDRESS`,
`SHOW_ADDRESS`, `UPAZILA`, `DISTRICT`, `DIVISION`, `TECHNOLOGY`, `MSC`,
`COUNTRY`, `DIVISION_CODE`, `ZILA_CODE`, `UPAZILA_CODE`, `MONTH_KEY`. See
the join notes above for how `SITE_ID`/`CGI` ambiguity and the `MONTH_KEY`
type mismatch are handled.

## What's built

- `app.py` — Flask app, two endpoints:
  - `GET /api/revenue-report?category=GT&year=2026&month=7&division=&district=`
  - `GET /api/filters` — Division/District dropdown options
- `db.py` — builds the pivot query dynamically per month (day columns aren't
  hardcoded, so it adapts automatically to 28/29/30/31-day months) and runs it
- `config.py` — all the settings above, in one place
- `templates/revenue.html` + `static/style.css` + `static/app.js` — frontend,
  styled to match the green/white theme and tab layout from your portal
  screenshots

## Merging into the existing portal

This is a standalone Flask app right now so it's easy to test independently.
To fold it into the existing `app.py` (the SSH/log-tailing one):
- Copy the `/api/filters` and `/api/revenue-report` routes into it
- Copy `db.py` and `config.py` in alongside it
- Add a "Revenue Report" tab to the existing nav bar, pointing at this
  template's content (the existing app renders its frontend inline in
  `app.py` rather than from `templates/`, so you'd port the HTML/JS in the
  same style it already uses)

## Known limitation

Query time will scale with how much of `VOICE_DATA_DETAILS_FINAL` needs
scanning per request — a full month × every site is a lot of rows if this
table isn't partitioned/indexed on `DATE_VALUE`. If it's slow in practice,
the fix is either an index on `DATE_VALUE` (and the join column) or a
pre-aggregated summary table refreshed nightly instead of querying the raw
fact table on every page load — worth flagging to your manager if it comes
up.

## Update: VOICE_DATA_DETAILS_FINAL_2, dynamic month filter, index hint, CSV export

- **Table swap**: now queries `VOICE_DATA_DETAILS_FINAL_2`. Its site column is
  named `SITE_ID` directly (not `SITE_OR_CGI`), so the join to
  `ZONE_DIM_FULL` is now a straightforward `SITE_ID = SITE_ID` match — no
  more SITE_ID-or-CGI `UNION` needed. Still de-dupes `ZONE_DIM_FULL` down to
  one geography row per site, since it has multiple rows per site (one per
  CGI/technology).
- **Month filter is now data-driven**: `/api/months` returns the distinct
  `MONTH_KEY` values that actually exist in `ZONE_DIM_FULL` (newest first),
  and the frontend's Month dropdown is populated from that — so you can
  never pick a month with no data behind it. The old separate Month+Year
  calendar dropdowns are gone; `/api/revenue-report` now takes a single
  `month=YYYYMM` param instead.
- **Index usage**: `VOICE_DATA_DETAILS_FINAL_2` has a composite index
  `IDX_VDDF2_DATE_SITE` on `(DATE_VALUE, SITE_ID)`. The query is written to
  use it naturally (a plain `DATE_VALUE` range with no wrapping functions,
  `GROUP BY SITE_ID`), and also carries an explicit Oracle hint —
  `/*+ INDEX(f IDX_VDDF2_DATE_SITE) */` — so the optimizer doesn't have to
  guess. Worth confirming with `EXPLAIN PLAN` once real data is flowing that
  it's actually being picked up, since hints are a suggestion, not a
  guarantee, if Oracle's statistics say a full scan is cheaper.
- **CSV export**: `GET /api/revenue-report/csv` (same params as
  `/api/revenue-report`) streams back a CSV with the same Division/
  District/Upazila/Site ID + daily columns shape. Wired to a "Download CSV"
  button next to Apply — it downloads whatever's currently filtered/applied
  on screen.

## Still outstanding: the Excel "Dashboard" tab (charts)

Went through the Excel's `Dashboard` sheet in detail — it's driven by 8
embedded charts (Daily Revenue Trend line chart, Total Revenue by category
bar chart, Total Revenue Share pie chart, and others) pulling from a
`Sheet1` pivot table. Some of those numbers — `Total_Attach`, `BTS Number`,
`Blended_ARPU`, and an operator-wise breakdown (Airtel/Banglalink/BTCL/
Edotco/Robi/TBL/etc) — aren't present in either `VOICE_DATA_DETAILS_FINAL_2`
or `ZONE_DIM_FULL`. That data has to be coming from another table
somewhere. Rather than fabricate those figures, this still needs:
which table holds subscriber attach counts / BTS counts / operator-wise
splits, before the dashboard charts can be built for real.

## Update: Dashboard restored (and ported forward) + performance work

**On the dashboard disappearing**: it didn't get removed by me — it was never
in anything I'd delivered up to that point. What got uploaded (`build
v3-dashboard`) was a genuinely solid addition — KPI cards, Chart.js line/
doughnut/dual-axis charts, top-10 districts table — built on top of an
*older* version of this code (still on `VOICE_DATA_DETAILS_FINAL` with
year/month dropdowns), from before the switch to `VOICE_DATA_DETAILS_FINAL_2`.
It's ported forward now onto the current schema/filters.

### Performance changes (the "data coming late" fix)

Three separate things were adding latency, addressed together:

1. **Connection pooling.** Every API call used to open a brand new Oracle
   connection (TCP handshake + auth) and discard it. `db.py` now keeps a
   pool of 2–10 connections open (`oracledb.create_pool`, `min=2, max=10`)
   and reuses them — a page load firing `/api/months`, `/api/filters`, and
   `/api/dashboard-summary` close together no longer pays connection setup
   cost 3 times over.

2. **Dashboard: 4 scans → 1.** `fetch_dashboard_summary` used to run 4
   independent queries against the fact table — totals, daily trend,
   division breakdown, district breakdown — each scanning the month's data
   from scratch. It's now a single query using
   `GROUP BY GROUPING SETS ((), (day), (division), (district))`, so Oracle
   reads the underlying rows once (via an internal temp-table transform)
   and produces all four aggregations from that pass. Results are told
   apart in Python by which of `DAY_VAL`/`DIVISION`/`DISTRICT` is non-null
   per row — there's no ambiguity since each grouping set nulls out the
   columns it didn't group by.

3. **Partition-pruning-friendly filter.** Both queries now filter directly
   on `f.MONTH_KEY = :fact_month_key` (a plain equality on the fact table's
   own `MONTH_KEY` column) *in addition to* the `DATE_VALUE` range. If
   `VOICE_DATA_DETAILS_FINAL_2` is partitioned by `MONTH_KEY` — a common
   pattern for a table this size — this is what lets Oracle skip scanning
   every other month's partition entirely rather than relying on it to
   infer that scope from the date range alone. **Worth confirming the
   actual partitioning scheme** with `SELECT * FROM DBA_TAB_PARTITIONS
   WHERE TABLE_NAME = 'VOICE_DATA_DETAILS_FINAL_2'` (or ask your DBA) — if
   it turns out to be partitioned some other way (e.g. by `DATE_VALUE`
   directly, or not partitioned at all), this predicate is harmless either
   way, but the real fix would need to match whatever the actual scheme is.

**Not yet done, worth knowing about:**
- The site-level pivot query (`/api/revenue-report`) still has to scan
  every row for the month for every site — that's inherent to a site × day
  report, not something a query rewrite can avoid. If it's still slow after
  the above, the next lever is a `PARALLEL` hint (`/*+ PARALLEL(f,4) */`)
  or a materialized/pre-aggregated summary table refreshed nightly instead
  of hitting the raw fact table live — worth raising with your manager if
  it comes to that, since either has real trade-offs (parallel query eats
  more DB resources per request; a summary table means the numbers are as
  fresh as the last refresh, not truly live).
- Run `EXPLAIN PLAN` on both queries once real data is flowing to confirm
  the index/partition pruning is actually kicking in — hints are a
  suggestion to the optimizer, not a guarantee, and it can still choose a
  full scan if its statistics say that's cheaper.

---

## Session update: UX states, cross-category caching, race protection, logo

**1. Loading / error / applied UI states**
- `showLoading()` / `showError()` / `showView()` in `app.js` immediately hide
  whatever's on screen the instant a fetch starts, so the previous result is
  never mistaken for the new one. Loading message reads `Loading <Category>
  Revenue…` for a tab click, `Applying current filters...` for Apply.
- Errors render a dedicated error panel with a **Retry** button that re-runs
  the same request; nothing overwrites the old (still-valid) data on failure.
- The Apply button now cycles **Apply Filters → Processing... → ✓ Applied**.
  "Applied" is computed by comparing the live dropdown values to a snapshot
  of the filters that produced the data on screen (`appliedSnapshot` in
  `app.js`) — so changing any dropdown immediately drops it back to
  "Apply Filters", even before the click.

**2. Filters preserved across category tabs**
  Already true structurally (tabs re-fetch using `state.division/district/
  month_key`, which Apply — not the tab click — updates); the loading/error
  rework above didn't change this contract, just confirmed/preserved it.

**3. Cross-category caching (the big one)**
- New `cache.py`: a small thread-safe in-memory TTL cache (15 min for
  revenue/dashboard data, 60 min for the month & division/district
  reference lists). Single-process-appropriate; see the module docstring
  for the multi-worker caveat.
- New `build_all_categories_pivot_query` in `db.py` computes **all 5**
  categories' day columns (`GT_D1..GT_Dn, MOC_D1..MOC_Dn, ...`) in **one**
  query instead of one query per category. `fetch_revenue_report_all_categories`
  caches that result keyed on `(month, division, district)` — **not** on
  category. `fetch_revenue_report(category, ...)` just slices the cached
  result. Net effect: the first tab click for a given filter set pays for
  one full scan; every other category (GT → MTC → SMS → Data → MOC) for the
  same filters is a cache hit — no DB round-trip at all.
- `fetch_dashboard_summary` and `fetch_available_months` / `fetch_filter_options`
  are cached the same way, keyed on their own params.
- CSV export reuses the same cached `fetch_revenue_report`, so it can never
  return different data than what's on screen for the same filters/category.

**4. Race-condition protection**
  `fetchJson()` in `app.js` stamps every request with an incrementing id and
  aborts the previous in-flight request via `AbortController` when a new one
  starts. A response is only rendered if its id is still the latest — so
  clicking MTC then quickly SMS can never let the stale MTC response land
  after the newer SMS one.

**5. Teletalk logo**
  Added to `static/images/teletalk-logo.png`, placed top-left of the header
  inside a white rounded badge (`.topbar-logo`) for contrast against the
  green gradient bar — the source PNG's green/gray mark would otherwise
  wash out against the header background. Existing header layout,
  responsive behavior, and the rest of the site are unchanged.

**Not done / worth knowing about:**
- The combined all-categories query has up to 155 aggregate expressions
  (5 categories × 31 days) in one SELECT — correctness-tested against a
  mocked cursor, but not yet run against live Oracle. Worth an `EXPLAIN
  PLAN` alongside the existing ones once you're testing against the real DB,
  since a query this wide is more work per row scanned even though it only
  scans once.
- Cache is process-local memory — fine for a single `flask run` / single
  gunicorn worker; would need Redis (or similar) if this is ever scaled to
  multiple worker processes, since each process would otherwise cache
  independently.
