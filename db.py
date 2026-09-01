import calendar
import oracledb

from cache import cache_get, cache_set, TTL_REVENUE_DATA, TTL_REFERENCE_DATA
from config import (
    ORACLE_CONFIG, FACT_TABLE, FACT_TABLE_INDEX, COL_SITE_ID, COL_DATE, COL_MONTH_KEY,
    CATEGORY_COLUMNS,
    SITE_MASTER_TABLE, SITE_MASTER_SITE_ID_COL,
    SITE_MASTER_MONTH_KEY_COL, SITE_MASTER_DIVISION_COL,
    SITE_MASTER_DISTRICT_COL, SITE_MASTER_UPAZILA_COL
)

# ═══════════════════════════════════════════════════════════════
#  CONNECTION POOLING
#  Every request used to open a brand new Oracle connection
#  (TCP handshake + auth) and throw it away. On a page with several
#  API calls firing close together (months, filters, dashboard),
#  that overhead adds up and is a likely contributor to "data
#  coming late". A pool keeps a handful of connections open and
#  reuses them, so each request only pays for the query itself.
# ═══════════════════════════════════════════════════════════════

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = oracledb.create_pool(
            user=ORACLE_CONFIG['user'],
            password=ORACLE_CONFIG['password'],
            dsn=oracledb.makedsn(
                ORACLE_CONFIG['host'],
                ORACLE_CONFIG['port'],
                service_name=ORACLE_CONFIG['service_name']
            ),
            min=2,
            max=10,
            increment=1
        )
    return _pool


def get_connection():
    """Acquire a connection from the pool (returns it on close/context-exit)."""
    return get_pool().acquire()


def _site_geo_cte(month_key_bind):
    """
    ZONE_DIM_FULL has one row per CGI/technology (several rows share a
    SITE_ID). De-dupes down to one geography row per site for the given
    month, assuming Division/District/Upazila don't vary by technology
    for the same site.
    """
    return f"""
        site_geo AS (
            SELECT DISTINCT
                {SITE_MASTER_SITE_ID_COL} AS SITE_ID,
                {SITE_MASTER_DIVISION_COL} AS DIVISION,
                {SITE_MASTER_DISTRICT_COL} AS DISTRICT,
                {SITE_MASTER_UPAZILA_COL} AS UPAZILA
            FROM {SITE_MASTER_TABLE}
            WHERE {SITE_MASTER_MONTH_KEY_COL} = :{month_key_bind}
        )
    """


def _month_binds(month_key):
    """
    Common date-scoping binds for one month_key ('YYYYMM').
    fact_month_key is bound as a NUMBER to match COL_MONTH_KEY directly —
    if VOICE_DATA_DETAILS_FINAL_2 is range/list PARTITIONED BY MONTH_KEY
    (a common warehouse pattern for a table this size), filtering on it
    directly — rather than only on DATE_VALUE — is what lets Oracle prune
    partitions outside the requested month instead of scanning all of them.
    Worth confirming the actual partitioning scheme (DBA_TAB_PARTITIONS)
    to make sure this predicate lines up with it.
    """
    year, month = int(month_key[:4]), int(month_key[4:6])
    last_day = calendar.monthrange(year, month)[1]
    return {
        'range_start': f"{year:04d}-{month:02d}-01",
        'range_end': f"{year:04d}-{month:02d}-{last_day}",
        'geo_month_key': month_key,
        'fact_month_key': int(month_key),
    }, year, month, last_day


def build_all_categories_pivot_query(month_key, division=None, district=None, upazila=None):
    """
    Same pivot as build_pivot_query, but computes EVERY category's day
    columns (GT/MOC/MTC/DATA/SMS) in one pass instead of one category at a
    time. This is what lets category-tab switching (GT -> MTC -> SMS -> ...)
    reuse a single scan of the fact table instead of re-scanning it once per
    category — see fetch_revenue_report_all_categories, which is the only
    thing that calls this and is itself cached per (month, division, district,
    upazila).
    """
    binds, year, month, last_day = _month_binds(month_key)

    day_columns = []
    for day in range(1, last_day + 1):
        bind_name = f"d{day}"
        binds[bind_name] = f"{year:04d}-{month:02d}-{day:02d}"
        for category, revenue_expr in CATEGORY_COLUMNS.items():
            day_columns.append(
                f"SUM(CASE WHEN f.{COL_DATE} = TO_DATE(:{bind_name}, 'YYYY-MM-DD') "
                f"THEN {revenue_expr} ELSE 0 END) AS {category}_D{day}"
            )

    where_extra = ""
    if division:
        where_extra += " AND s.DIVISION = :division"
        binds['division'] = division
    if district:
        where_extra += " AND s.DISTRICT = :district"
        binds['district'] = district
    if upazila:
        where_extra += " AND s.UPAZILA = :upazila"
        binds['upazila'] = upazila

    sql = f"""
        WITH {_site_geo_cte('geo_month_key')}
        SELECT /*+ INDEX(f {FACT_TABLE_INDEX}) */
            s.DIVISION,
            s.DISTRICT,
            s.UPAZILA AS UPAZILA,
            f.{COL_SITE_ID} AS SITE_ID,
            {', '.join(day_columns)}
        FROM {FACT_TABLE} f
        LEFT JOIN site_geo s
          ON f.{COL_SITE_ID} = s.SITE_ID
        WHERE f.{COL_MONTH_KEY} = :fact_month_key
          AND f.{COL_DATE} BETWEEN TO_DATE(:range_start, 'YYYY-MM-DD')
                                AND TO_DATE(:range_end, 'YYYY-MM-DD')
        {where_extra}
        GROUP BY s.DIVISION, s.DISTRICT, s.UPAZILA, f.{COL_SITE_ID}
        ORDER BY s.DIVISION, s.DISTRICT, s.UPAZILA, f.{COL_SITE_ID}
    """
    return sql, binds, last_day


def fetch_revenue_report_all_categories(month_key, division=None, district=None, upazila=None):
    """
    Runs the combined all-categories pivot query and returns
    (rows, last_day). Cached per (month, division, district, upazila) — NOT
    per category, since the point is that every category comes out of this
    one query/cache entry. TTL keeps this from serving indefinitely-stale
    data after the warehouse's next ETL load.
    """
    cache_key = ('revenue_all_categories', month_key, division, district, upazila)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    sql, binds, last_day = build_all_categories_pivot_query(month_key, division, district, upazila)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            columns = [c[0] for c in cur.description]
            rows = cur.fetchall()
    result = ([dict(zip(columns, row)) for row in rows], last_day)
    cache_set(cache_key, result, TTL_REVENUE_DATA)
    return result


def fetch_revenue_report(category, month_key, division=None, district=None, upazila=None):
    """
    Per-category view (site x day, D1..Dn) sliced out of the cached
    all-categories result — same response shape as before (D1..Dn column
    names), so the API contract and CSV export are unaffected. Only the
    FIRST call for a given (month, division, district, upazila) combination
    — for whichever category is requested first — actually hits the
    database; every other category for those same filters reads from cache.
    """
    if category not in CATEGORY_COLUMNS:
        raise ValueError(f"Unknown category '{category}'. Expected one of {list(CATEGORY_COLUMNS)}")

    all_rows, last_day = fetch_revenue_report_all_categories(month_key, division, district, upazila)

    sliced = []
    for row in all_rows:
        out = {
            'DIVISION': row.get('DIVISION'),
            'DISTRICT': row.get('DISTRICT'),
            'UPAZILA': row.get('UPAZILA'),
            'SITE_ID': row.get('SITE_ID'),
        }
        for day in range(1, last_day + 1):
            out[f'D{day}'] = row.get(f'{category}_D{day}')
        sliced.append(out)
    return sliced


def fetch_dashboard_summary(month_key, division=None, district=None, upazila=None):
    """
    Aggregated data for the Dashboard tab: category totals, daily trend,
    division breakdown, district breakdown — for one month.

    Optimization: this used to be 4 separate queries, each doing its own
    full scan of the month's data (totals, daily, division, district).
    Combined here into ONE query using GROUP BY GROUPING SETS, so Oracle
    reads the underlying rows once and produces all four aggregations
    from that single pass (via a temp-table transform internally) instead
    of scanning the fact table 4 times. Each result row is tagged by
    which of DAY_VAL / DIVISION / DISTRICT is non-null to tell the
    grouping sets apart in Python.

    NOTE: the Excel Dashboard sheet also charts ARPU, attach %, subscriber
    counts, and minutes/GB usage — those aren't in this data source yet
    (see README).
    """
    cache_key = ('dashboard_summary', month_key, division, district, upazila)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    binds, year, month, last_day = _month_binds(month_key)

    where_extra = ""
    if division:
        where_extra += " AND s.DIVISION = :division"
        binds['division'] = division
    if district:
        where_extra += " AND s.DISTRICT = :district"
        binds['district'] = district
    if upazila:
        where_extra += " AND s.UPAZILA = :upazila"
        binds['upazila'] = upazila

    category_sums = ", ".join(f"SUM({expr}) AS {cat}" for cat, expr in CATEGORY_COLUMNS.items())

    sql = f"""
        WITH {_site_geo_cte('geo_month_key')}
        SELECT
            TRUNC(f.{COL_DATE}) AS DAY_VAL,
            s.DIVISION AS DIVISION,
            s.DISTRICT AS DISTRICT,
            {category_sums},
            COUNT(DISTINCT f.{COL_SITE_ID}) AS SITE_COUNT
        FROM {FACT_TABLE} f
        LEFT JOIN site_geo s ON f.{COL_SITE_ID} = s.SITE_ID
        WHERE f.{COL_MONTH_KEY} = :fact_month_key
          AND f.{COL_DATE} BETWEEN TO_DATE(:range_start, 'YYYY-MM-DD')
                                AND TO_DATE(:range_end, 'YYYY-MM-DD')
        {where_extra}
        GROUP BY GROUPING SETS (
            (),
            (TRUNC(f.{COL_DATE})),
            (s.DIVISION),
            (s.DISTRICT)
        )
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    totals_row = next((r for r in rows if r['DAY_VAL'] is None and r['DIVISION'] is None and r['DISTRICT'] is None), {})
    daily_rows = [r for r in rows if r['DAY_VAL'] is not None]
    division_rows = [r for r in rows if r['DAY_VAL'] is None and r['DIVISION'] is not None]
    district_rows = [r for r in rows if r['DAY_VAL'] is None and r['DISTRICT'] is not None]

    daily_trend = sorted(
        [{'day': r['DAY_VAL'].day, 'value': float(r['GT'] or 0)} for r in daily_rows],
        key=lambda x: x['day']
    )
    division_breakdown = sorted(
        [{'division': r['DIVISION'], 'value': float(r['GT'] or 0), 'site_count': r['SITE_COUNT'] or 0} for r in division_rows],
        key=lambda x: x['value'], reverse=True
    )
    district_breakdown = sorted(
        [{'district': r['DISTRICT'], 'value': float(r['GT'] or 0), 'site_count': r['SITE_COUNT'] or 0} for r in district_rows],
        key=lambda x: x['value'], reverse=True
    )[:10]  # top 10, trimmed in Python since a single GROUPING SETS query can't FETCH FIRST per-set

    result = {
        'category_totals': {k: float(totals_row.get(k) or 0) for k in CATEGORY_COLUMNS},
        'site_count': totals_row.get('SITE_COUNT') or 0,
        'daily_trend': daily_trend,
        'division_breakdown': division_breakdown,
        'district_breakdown': district_breakdown,
    }
    cache_set(cache_key, result, TTL_REVENUE_DATA)
    return result


def fetch_available_months():
    """
    Months that actually exist, sourced from ZONE_DIM_FULL's own MONTH_KEY
    partitioning. Returns [{'month_key': '202607', 'label': 'July 2026'}, ...],
    newest first.
    """
    cache_key = ('available_months',)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    sql = f"""
        SELECT DISTINCT {SITE_MASTER_MONTH_KEY_COL}
        FROM {SITE_MASTER_TABLE}
        WHERE {SITE_MASTER_MONTH_KEY_COL} IS NOT NULL
        ORDER BY {SITE_MASTER_MONTH_KEY_COL} DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    months = []
    for (month_key,) in rows:
        month_key = str(month_key).strip()
        if len(month_key) != 6:
            continue
        year, month = int(month_key[:4]), int(month_key[4:6])
        months.append({'month_key': month_key, 'label': f"{calendar.month_name[month]} {year}"})

    cache_set(cache_key, months, TTL_REFERENCE_DATA)
    return months


def fetch_filter_options():
    """
    Returns distinct Division/District/Upazila values for the filter
    dropdowns. ZONE_DIM_FULL can contain NULL/blank Division, District or
    Upazila (unmapped sites) — filtered defensively both in SQL and in
    Python so a None/blank value can never reach sorted().

    upazilas_by_division_district is nested {division: {district: [upazila,...]}}
    so the frontend can populate the Upazila dropdown to exactly the set of
    upazilas that exist under whichever Division/District is currently
    selected.
    """
    cache_key = ('filter_options',)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    sql = f"""
        SELECT DISTINCT {SITE_MASTER_DIVISION_COL}, {SITE_MASTER_DISTRICT_COL}, {SITE_MASTER_UPAZILA_COL}
        FROM {SITE_MASTER_TABLE}
        WHERE {SITE_MASTER_DIVISION_COL} IS NOT NULL
        ORDER BY 1, 2, 3
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    def clean(val):
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    by_division = {}
    upazilas_by_division_district = {}
    for division, district, upazila in rows:
        division, district, upazila = clean(division), clean(district), clean(upazila)
        if division is None:
            continue
        by_division.setdefault(division, set())
        if district:
            by_division[division].add(district)
            if upazila:
                upazilas_by_division_district.setdefault(division, {}).setdefault(district, set()).add(upazila)

    result = {
        'divisions': sorted(by_division.keys()),
        'districts_by_division': {k: sorted(v) for k, v in by_division.items()},
        'upazilas_by_division_district': {
            div: {dist: sorted(ups) for dist, ups in dmap.items()}
            for div, dmap in upazilas_by_division_district.items()
        }
    }
    cache_set(cache_key, result, TTL_REFERENCE_DATA)
    return result