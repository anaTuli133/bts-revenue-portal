# ═══════════════════════════════════════════════════════════════
#  ORACLE DB CONFIG
# ═══════════════════════════════════════════════════════════════

ORACLE_CONFIG = {
    'user': 'dwh_user',               
    'password': 'dwh_user_123',   
    'host': '192.168.61.202',
    'port': 1521,
    'service_name': 'dwhdb01',         
}

# ═══════════════════════════════════════════════════════════════
#  FACT TABLE — switched to VOICE_DATA_DETAILS_FINAL_2.
#  Same columns as the old table, EXCEPT column 3 is now named
#  SITE_ID (was SITE_OR_CGI) — no more ambiguity about whether it
#  holds a site or a CGI value; it's a direct SITE_ID now.
#
#  This table has a composite index IDX_VDDF2_DATE_SITE on
#  (DATE_VALUE, SITE_ID) — db.py's query is written to use it
#  (range filter on DATE_VALUE, GROUP BY SITE_ID, plus an explicit
#  Oracle INDEX hint) rather than accidentally bypassing it.
# ═══════════════════════════════════════════════════════════════

FACT_TABLE = 'VOICE_DATA_DETAILS_FINAL_2'
FACT_TABLE_INDEX = 'IDX_VDDF2_DATE_SITE'
COL_SITE_ID = 'SITE_ID'
COL_DATE = 'DATE_VALUE'
COL_MONTH_KEY = 'MONTH_KEY'       # NUMBER(38,0) in this table, e.g. 202607

# Revenue column per report category (matches the Excel tabs: GT / MOC / MTC / Data / SMS)
# GT (Grand Total) is a SQL expression combining the others — verify this is the
# correct definition of "grand total" against how the Excel's GT sheet was built.
CATEGORY_COLUMNS = {
    'MOC':  'VOICE_REV_TOT_MOC',
    'MTC':  'VOICE_REV_MTC',
    'DATA': 'DATA_REV_TOT',
    'SMS':  'SMS_REV_TOT',
    'GT':   "(NVL(VOICE_REV_TOT_MOC,0) + NVL(VOICE_REV_MTC,0) + NVL(DATA_REV_TOT,0) + NVL(SMS_REV_TOT,0))",
}

# ═══════════════════════════════════════════════════════════════
#  SITE GEOGRAPHY — ZONE_DIM_FULL, monthly-versioned (own MONTH_KEY).
#  Now that the fact table's join column is unambiguously SITE_ID,
#  this matches directly against ZONE_DIM_FULL.SITE_ID (no more
#  SITE_ID-or-CGI UNION). Still has multiple rows per SITE_ID
#  (one per CGI/technology), so db.py still de-dupes down to one
#  geography row per site.
# ═══════════════════════════════════════════════════════════════

SITE_MASTER_TABLE = 'ZONE_DIM_FULL'
SITE_MASTER_SITE_ID_COL = 'SITE_ID'
SITE_MASTER_MONTH_KEY_COL = 'MONTH_KEY'   # VARCHAR2(6 BYTE), e.g. '202607'
SITE_MASTER_DIVISION_COL = 'DIVISION'
SITE_MASTER_DISTRICT_COL = 'DISTRICT'
SITE_MASTER_UPAZILA_COL = 'UPAZILA'
