#!/usr/bin/env python3
"""
UniTime Database Schema Reference Extractor
============================================
Connects to a UniTime (Timetabling) database and extracts a comprehensive
reference document covering: all tables, columns (with types, nullability,
defaults), primary keys, foreign keys, indexes, views, sequences/auto-increments,
and row counts.

Supports: MySQL / MariaDB  |  PostgreSQL  |  Oracle

Usage:
    pip install pymysql psycopg2-binary cx_Oracle   # install only what you need
    python unitime_schema_dump.py

Then edit the CONFIG section below to match your connection.
"""

import sys
import datetime

# ─────────────────────────────────────────────
#  CONFIG  –  edit these before running
# ─────────────────────────────────────────────
DB_TYPE = "mysql"          # "mysql"  |  "postgres"  |  "oracle"

DB_CONFIG = {
    "host":     "localhost",
    "port":     3306,          # 3306 MySQL | 5432 Postgres | 1521 Oracle
    "database": "timetable",   # your UniTime schema / database name
    "user":     "root",
    "password": "1234",
    # Oracle-only extras (ignored for other DBs):
    # Oracle service name (alternative to database)
}

OUTPUT_FILE = "unitime_database_reference.txt"
INCLUDE_ROW_COUNTS = True      # set False to skip (faster on large DBs)
# ─────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════
#  CONNECTION HELPERS
# ══════════════════════════════════════════════════════════════════

def get_connection():
    db = DB_TYPE.lower()
    if db == "mysql":
        try:
            import pymysql
        except ImportError:
            sys.exit("ERROR: pymysql not installed. Run: pip install pymysql")
        return pymysql.connect(
            host=DB_CONFIG["host"],
            port=int(DB_CONFIG["port"]),
            database=DB_CONFIG["database"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    elif db == "postgres":
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            sys.exit("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        conn = psycopg2.connect(
            host=DB_CONFIG["host"],
            port=int(DB_CONFIG["port"]),
            dbname=DB_CONFIG["database"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
        )
        return conn
    elif db == "oracle":
        try:
            import cx_Oracle
        except ImportError:
            sys.exit("ERROR: cx_Oracle not installed. Run: pip install cx_Oracle")
        dsn = cx_Oracle.makedsn(
            DB_CONFIG["host"],
            int(DB_CONFIG["port"]),
            service_name=DB_CONFIG.get("service_name", DB_CONFIG.get("database")),
        )
        return cx_Oracle.connect(DB_CONFIG["user"], DB_CONFIG["password"], dsn)
    else:
        sys.exit(f"ERROR: Unknown DB_TYPE '{DB_TYPE}'. Use mysql, postgres, or oracle.")


def dict_cursor(conn):
    db = DB_TYPE.lower()
    if db == "mysql":
        return conn.cursor()
    elif db == "postgres":
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return conn.cursor()


def fetchall_as_dicts(cursor, oracle_cols=None):
    """Normalise rows to list-of-dicts across all drivers."""
    rows = cursor.fetchall()
    if DB_TYPE.lower() == "oracle" and oracle_cols:
        return [dict(zip(oracle_cols, row)) for row in rows]
    if DB_TYPE.lower() == "postgres":
        return [dict(r) for r in rows]
    # mysql DictCursor already returns dicts
    return list(rows)


# ══════════════════════════════════════════════════════════════════
#  QUERY BUILDERS (per DB flavour)
# ══════════════════════════════════════════════════════════════════

def get_tables(cursor, schema):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT, ENGINE,
                      TABLE_ROWS, CREATE_TIME, UPDATE_TIME
               FROM information_schema.TABLES
               WHERE TABLE_SCHEMA = %s
               ORDER BY TABLE_TYPE, TABLE_NAME""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT t.table_name AS TABLE_NAME,
                      t.table_type AS TABLE_TYPE,
                      obj_description((quote_ident(t.table_schema)||'.'||quote_ident(t.table_name))::regclass, 'pg_class') AS TABLE_COMMENT
               FROM information_schema.tables t
               WHERE t.table_schema = %s
               ORDER BY t.table_type, t.table_name""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute(
            """SELECT table_name AS TABLE_NAME, 'BASE TABLE' AS TABLE_TYPE, comments AS TABLE_COMMENT
               FROM user_tab_comments ORDER BY table_name"""
        )
        return fetchall_as_dicts(cursor, ["TABLE_NAME", "TABLE_TYPE", "TABLE_COMMENT"])


def get_columns(cursor, schema, table):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT,
                      IS_NULLABLE, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                      NUMERIC_PRECISION, NUMERIC_SCALE, COLUMN_TYPE,
                      COLUMN_KEY, EXTRA, COLUMN_COMMENT
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
               ORDER BY ORDINAL_POSITION""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT c.column_name AS COLUMN_NAME,
                      c.ordinal_position AS ORDINAL_POSITION,
                      c.column_default AS COLUMN_DEFAULT,
                      c.is_nullable AS IS_NULLABLE,
                      c.data_type AS DATA_TYPE,
                      c.character_maximum_length AS CHARACTER_MAXIMUM_LENGTH,
                      c.numeric_precision AS NUMERIC_PRECISION,
                      c.numeric_scale AS NUMERIC_SCALE,
                      c.udt_name AS COLUMN_TYPE,
                      pgd.description AS COLUMN_COMMENT
               FROM information_schema.columns c
               LEFT JOIN pg_catalog.pg_statio_all_tables st
                      ON st.schemaname = c.table_schema AND st.relname = c.table_name
               LEFT JOIN pg_catalog.pg_description pgd
                      ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
               WHERE c.table_schema = %s AND c.table_name = %s
               ORDER BY c.ordinal_position""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute(
            """SELECT col.column_name AS COLUMN_NAME,
                      col.column_id AS ORDINAL_POSITION,
                      col.data_default AS COLUMN_DEFAULT,
                      col.nullable AS IS_NULLABLE,
                      col.data_type AS DATA_TYPE,
                      col.char_length AS CHARACTER_MAXIMUM_LENGTH,
                      col.data_precision AS NUMERIC_PRECISION,
                      col.data_scale AS NUMERIC_SCALE,
                      col.data_type AS COLUMN_TYPE,
                      comm.comments AS COLUMN_COMMENT
               FROM user_tab_columns col
               LEFT JOIN user_col_comments comm
                      ON comm.table_name = col.table_name AND comm.column_name = col.column_name
               WHERE col.table_name = :tname
               ORDER BY col.column_id""",
            tname=table,
        )
        cols = ["COLUMN_NAME","ORDINAL_POSITION","COLUMN_DEFAULT","IS_NULLABLE",
                "DATA_TYPE","CHARACTER_MAXIMUM_LENGTH","NUMERIC_PRECISION",
                "NUMERIC_SCALE","COLUMN_TYPE","COLUMN_COMMENT"]
        return fetchall_as_dicts(cursor, cols)


def get_primary_keys(cursor, schema, table):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT COLUMN_NAME, CONSTRAINT_NAME
               FROM information_schema.KEY_COLUMN_USAGE
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                 AND CONSTRAINT_NAME = 'PRIMARY'
               ORDER BY ORDINAL_POSITION""",
            (schema, table),
        )
        return [r["COLUMN_NAME"] for r in fetchall_as_dicts(cursor)]
    elif db == "postgres":
        cursor.execute(
            """SELECT kcu.column_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema = kcu.table_schema
               WHERE tc.constraint_type = 'PRIMARY KEY'
                 AND tc.table_schema = %s AND tc.table_name = %s
               ORDER BY kcu.ordinal_position""",
            (schema, table),
        )
        return [r["column_name"] for r in fetchall_as_dicts(cursor)]
    else:  # oracle
        cursor.execute(
            """SELECT cols.column_name
               FROM all_constraints cons
               JOIN all_cons_columns cols
                    ON cons.constraint_name = cols.constraint_name
               WHERE cons.constraint_type = 'P'
                 AND cons.table_name = :tname
               ORDER BY cols.position""",
            tname=table,
        )
        return [r[0] for r in cursor.fetchall()]


def get_foreign_keys(cursor, schema, table):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT kcu.CONSTRAINT_NAME, kcu.COLUMN_NAME,
                      kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME,
                      rc.UPDATE_RULE, rc.DELETE_RULE
               FROM information_schema.KEY_COLUMN_USAGE kcu
               JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
                    ON rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                   AND rc.CONSTRAINT_SCHEMA = kcu.TABLE_SCHEMA
               WHERE kcu.TABLE_SCHEMA = %s AND kcu.TABLE_NAME = %s
                 AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
               ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT
                  tc.constraint_name AS CONSTRAINT_NAME,
                  kcu.column_name AS COLUMN_NAME,
                  ccu.table_name AS REFERENCED_TABLE_NAME,
                  ccu.column_name AS REFERENCED_COLUMN_NAME,
                  rc.update_rule AS UPDATE_RULE,
                  rc.delete_rule AS DELETE_RULE
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema = kcu.table_schema
               JOIN information_schema.referential_constraints rc
                    ON tc.constraint_name = rc.constraint_name
               JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = rc.unique_constraint_name
               WHERE tc.constraint_type = 'FOREIGN KEY'
                 AND tc.table_schema = %s AND tc.table_name = %s
               ORDER BY tc.constraint_name""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute(
            """SELECT a.constraint_name AS CONSTRAINT_NAME,
                      a.column_name AS COLUMN_NAME,
                      c_pk.table_name AS REFERENCED_TABLE_NAME,
                      b.column_name AS REFERENCED_COLUMN_NAME,
                      c.delete_rule AS DELETE_RULE
               FROM all_cons_columns a
               JOIN all_constraints c ON a.constraint_name = c.constraint_name
               JOIN all_constraints c_pk ON c.r_constraint_name = c_pk.constraint_name
               JOIN all_cons_columns b ON b.constraint_name = c.r_constraint_name
               WHERE c.constraint_type = 'R' AND a.table_name = :tname
               ORDER BY a.constraint_name, a.position""",
            tname=table,
        )
        cols = ["CONSTRAINT_NAME","COLUMN_NAME","REFERENCED_TABLE_NAME","REFERENCED_COLUMN_NAME","DELETE_RULE"]
        return fetchall_as_dicts(cursor, cols)


def get_indexes(cursor, schema, table):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS COLUMNS,
                      NON_UNIQUE, INDEX_TYPE, INDEX_COMMENT
               FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
               GROUP BY INDEX_NAME, NON_UNIQUE, INDEX_TYPE, INDEX_COMMENT
               ORDER BY INDEX_NAME""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT i.relname AS INDEX_NAME,
                      array_to_string(array_agg(a.attname ORDER BY ix.indkey_pos), ', ') AS COLUMNS,
                      NOT ix.indisunique AS NON_UNIQUE,
                      am.amname AS INDEX_TYPE
               FROM pg_class t
               JOIN pg_index ix ON t.oid = ix.indrelid
               JOIN pg_class i ON i.oid = ix.indexrelid
               JOIN pg_am am ON i.relam = am.oid
               JOIN pg_namespace ns ON ns.oid = t.relnamespace
               JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS ik(attnum, indkey_pos) ON true
               JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ik.attnum
               WHERE ns.nspname = %s AND t.relname = %s
               GROUP BY i.relname, ix.indisunique, am.amname
               ORDER BY i.relname""",
            (schema, table),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute(
            """SELECT i.index_name AS INDEX_NAME,
                      LISTAGG(ic.column_name, ', ') WITHIN GROUP (ORDER BY ic.column_position) AS COLUMNS,
                      CASE i.uniqueness WHEN 'UNIQUE' THEN 0 ELSE 1 END AS NON_UNIQUE,
                      i.index_type AS INDEX_TYPE
               FROM user_indexes i
               JOIN user_ind_columns ic ON ic.index_name = i.index_name
               WHERE i.table_name = :tname
               GROUP BY i.index_name, i.uniqueness, i.index_type
               ORDER BY i.index_name""",
            tname=table,
        )
        cols = ["INDEX_NAME","COLUMNS","NON_UNIQUE","INDEX_TYPE"]
        return fetchall_as_dicts(cursor, cols)


def get_views(cursor, schema):
    db = DB_TYPE.lower()
    if db == "mysql":
        cursor.execute(
            """SELECT TABLE_NAME AS VIEW_NAME, VIEW_DEFINITION
               FROM information_schema.VIEWS
               WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT table_name AS VIEW_NAME, view_definition AS VIEW_DEFINITION
               FROM information_schema.views
               WHERE table_schema = %s ORDER BY table_name""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute("SELECT view_name AS VIEW_NAME, text AS VIEW_DEFINITION FROM user_views ORDER BY view_name")
        return fetchall_as_dicts(cursor, ["VIEW_NAME","VIEW_DEFINITION"])


def get_row_count(cursor, table):
    try:
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`" if DB_TYPE == "mysql" else f'SELECT COUNT(*) FROM "{table}"')
        row = cursor.fetchone()
        if isinstance(row, dict):
            return list(row.values())[0]
        return row[0]
    except Exception:
        return "N/A"


def get_sequences(cursor, schema):
    """Return sequences / auto-increment info where available."""
    db = DB_TYPE.lower()
    if db == "mysql":
        # MySQL uses AUTO_INCREMENT per table – already captured in columns (EXTRA field)
        cursor.execute(
            """SELECT TABLE_NAME, AUTO_INCREMENT
               FROM information_schema.TABLES
               WHERE TABLE_SCHEMA = %s AND AUTO_INCREMENT IS NOT NULL
               ORDER BY TABLE_NAME""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    elif db == "postgres":
        cursor.execute(
            """SELECT sequence_name, start_value, minimum_value, maximum_value,
                      increment, cycle_option
               FROM information_schema.sequences
               WHERE sequence_schema = %s ORDER BY sequence_name""",
            (schema,),
        )
        return fetchall_as_dicts(cursor)
    else:  # oracle
        cursor.execute("SELECT sequence_name, min_value, max_value, increment_by, cycle_flag, last_number FROM user_sequences ORDER BY sequence_name")
        cols = ["sequence_name","min_value","max_value","increment_by","cycle_flag","last_number"]
        return fetchall_as_dicts(cursor, cols)


# ══════════════════════════════════════════════════════════════════
#  FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════

SEP_MAJOR = "═" * 100
SEP_MINOR = "─" * 80
SEP_THIN  = "·" * 60


def fmt_col_type(col):
    ct = str(col.get("COLUMN_TYPE") or col.get("DATA_TYPE") or "")
    prec = col.get("NUMERIC_PRECISION")
    scale = col.get("NUMERIC_SCALE")
    maxlen = col.get("CHARACTER_MAXIMUM_LENGTH")
    if maxlen:
        ct = f"{ct}({maxlen})"
    elif prec is not None and scale is not None:
        ct = f"{ct}({prec},{scale})"
    elif prec is not None:
        ct = f"{ct}({prec})"
    return ct or "?"


def v(val, fallback=""):
    """Safe stringify."""
    if val is None:
        return fallback
    return str(val).strip()


# ══════════════════════════════════════════════════════════════════
#  MAIN EXTRACTOR
# ══════════════════════════════════════════════════════════════════

def extract(conn, schema, out):

    def w(line=""):
        out.write(line + "\n")

    cursor = dict_cursor(conn)

    # ── Header ──────────────────────────────────────────────────
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    w(SEP_MAJOR)
    w("  UNITIME TIMETABLING DATABASE – COMPLETE SCHEMA REFERENCE")
    w(f"  Generated : {now}")
    w(f"  Database  : {DB_CONFIG['database']}  |  Engine: {DB_TYPE.upper()}")
    w(f"  Host      : {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    w(SEP_MAJOR)
    w()

    # ── Table list overview ──────────────────────────────────────
    w("SECTION 1 – TABLE OVERVIEW")
    w(SEP_MINOR)
    tables_meta = get_tables(cursor, schema)

    base_tables = [t for t in tables_meta if "VIEW" not in str(t.get("TABLE_TYPE","")).upper()]
    views_meta  = [t for t in tables_meta if "VIEW" in str(t.get("TABLE_TYPE","")).upper()]

    w(f"Total base tables : {len(base_tables)}")
    w(f"Total views       : {len(views_meta)}")
    w()
    w(f"  {'#':<5} {'TABLE NAME':<50} {'ENGINE':<10} {'APPROX ROWS':<14} {'COMMENT'}")
    w(f"  {'-'*5} {'-'*50} {'-'*10} {'-'*14} {'-'*40}")
    for i, t in enumerate(base_tables, 1):
        name    = v(t.get("TABLE_NAME"))
        engine  = v(t.get("ENGINE"), "-")
        trows   = v(t.get("TABLE_ROWS"), "-")
        comment = v(t.get("TABLE_COMMENT"))
        w(f"  {i:<5} {name:<50} {engine:<10} {trows:<14} {comment}")
    w()

    # ── Per-table detail ─────────────────────────────────────────
    w(SEP_MAJOR)
    w("SECTION 2 – DETAILED TABLE DEFINITIONS")
    w(SEP_MAJOR)

    for t in base_tables:
        tname = v(t.get("TABLE_NAME"))
        if not tname:
            continue

        w()
        w(SEP_MINOR)
        w(f"  TABLE: {tname}")
        if t.get("TABLE_COMMENT"):
            w(f"  Description : {v(t['TABLE_COMMENT'])}")
        if t.get("ENGINE"):
            w(f"  Engine      : {v(t['ENGINE'])}")
        if INCLUDE_ROW_COUNTS:
            rc = get_row_count(cursor, tname)
            w(f"  Row count   : {rc}")
        w(SEP_MINOR)

        # Columns
        columns = get_columns(cursor, schema, tname)
        pks     = get_primary_keys(cursor, schema, tname)
        pk_set  = set(pks)

        w()
        w("  COLUMNS")
        w(f"  {'POS':<5} {'COLUMN NAME':<35} {'TYPE':<25} {'NULLABLE':<10} {'DEFAULT':<20} {'KEY':<6} {'EXTRA / COMMENT'}")
        w(f"  {'-'*5} {'-'*35} {'-'*25} {'-'*10} {'-'*20} {'-'*6} {'-'*40}")

        for col in columns:
            pos     = v(col.get("ORDINAL_POSITION"))
            cname   = v(col.get("COLUMN_NAME"))
            ctype   = fmt_col_type(col)
            null    = v(col.get("IS_NULLABLE"), "YES")
            default = v(col.get("COLUMN_DEFAULT"), "")
            key     = "PK" if cname in pk_set else v(col.get("COLUMN_KEY",""))
            extra   = v(col.get("EXTRA",""))
            comment = v(col.get("COLUMN_COMMENT",""))
            extra_comment = " | ".join(filter(None, [extra, comment]))
            w(f"  {pos:<5} {cname:<35} {ctype:<25} {null:<10} {default:<20} {key:<6} {extra_comment}")

        # Primary Key
        if pks:
            w()
            w(f"  PRIMARY KEY  →  ({', '.join(pks)})")

        # Foreign Keys
        fks = get_foreign_keys(cursor, schema, tname)
        if fks:
            w()
            w("  FOREIGN KEYS")
            seen_fk = {}
            for fk in fks:
                cn   = v(fk.get("CONSTRAINT_NAME"))
                col  = v(fk.get("COLUMN_NAME"))
                rtbl = v(fk.get("REFERENCED_TABLE_NAME"))
                rcol = v(fk.get("REFERENCED_COLUMN_NAME"))
                upd  = v(fk.get("UPDATE_RULE",""))
                dlt  = v(fk.get("DELETE_RULE",""))
                if cn not in seen_fk:
                    seen_fk[cn] = {"cols":[], "rtbl": rtbl, "rcols":[], "upd": upd, "dlt": dlt}
                seen_fk[cn]["cols"].append(col)
                seen_fk[cn]["rcols"].append(rcol)
            for cn, info in seen_fk.items():
                rule = f"ON UPDATE {info['upd']}" if info['upd'] else ""
                if info['dlt']:
                    rule += f"  ON DELETE {info['dlt']}"
                w(f"    [{cn}]  ({', '.join(info['cols'])})  →  {info['rtbl']}({', '.join(info['rcols'])})  {rule}")

        # Indexes
        idxs = get_indexes(cursor, schema, tname)
        if idxs:
            w()
            w("  INDEXES")
            for idx in idxs:
                iname   = v(idx.get("INDEX_NAME"))
                icols   = v(idx.get("COLUMNS"))
                non_unq = idx.get("NON_UNIQUE")
                itype   = v(idx.get("INDEX_TYPE",""))
                uniq_lbl = "UNIQUE" if str(non_unq) in ("0","False","f") else "      "
                icomment = v(idx.get("INDEX_COMMENT",""))
                w(f"    {uniq_lbl:<7} [{iname}]  columns: ({icols})  type: {itype}  {icomment}")

        w()

    # ── Views ────────────────────────────────────────────────────
    w(SEP_MAJOR)
    w("SECTION 3 – VIEWS")
    w(SEP_MAJOR)
    w()
    all_views = get_views(cursor, schema)
    if not all_views:
        w("  (no views found)")
    for vw in all_views:
        vname = v(vw.get("VIEW_NAME") or vw.get("view_name",""))
        vdef  = v(vw.get("VIEW_DEFINITION") or vw.get("view_definition",""))
        w(SEP_MINOR)
        w(f"  VIEW: {vname}")
        w()
        if vdef:
            for line in vdef.splitlines():
                w(f"    {line}")
        w()

    # ── Sequences / Auto-increment ───────────────────────────────
    w(SEP_MAJOR)
    w("SECTION 4 – SEQUENCES / AUTO-INCREMENT")
    w(SEP_MAJOR)
    w()
    seqs = get_sequences(cursor, schema)
    if not seqs:
        w("  (none found)")
    for s in seqs:
        w(f"  {s}")
    w()

    # ── Foreign Key Relationship Map ─────────────────────────────
    w(SEP_MAJOR)
    w("SECTION 5 – FULL FOREIGN KEY RELATIONSHIP MAP")
    w(SEP_MAJOR)
    w()
    w("  FORMAT:  child_table.column  ──►  parent_table.column  [constraint]")
    w()
    for t in base_tables:
        tname = v(t.get("TABLE_NAME"))
        fks   = get_foreign_keys(cursor, schema, tname)
        for fk in fks:
            col  = v(fk.get("COLUMN_NAME"))
            rtbl = v(fk.get("REFERENCED_TABLE_NAME"))
            rcol = v(fk.get("REFERENCED_COLUMN_NAME"))
            cn   = v(fk.get("CONSTRAINT_NAME"))
            w(f"  {tname}.{col:<45}  ──►  {rtbl}.{rcol}  [{cn}]")
    w()

    # ── Footer ───────────────────────────────────────────────────
    w(SEP_MAJOR)
    w(f"  END OF REFERENCE  |  Generated: {now}")
    w(SEP_MAJOR)

    cursor.close()


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"[UniTime Schema Extractor]")
    print(f"  Connecting to {DB_TYPE.upper()} at {DB_CONFIG['host']}:{DB_CONFIG['port']} / {DB_CONFIG['database']} ...")

    conn = get_connection()
    schema = DB_CONFIG["database"]  # for Oracle this is the username/schema

    print(f"  Connected. Extracting schema into '{OUTPUT_FILE}' ...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        extract(conn, schema, out)

    conn.close()
    print(f"  Done! Reference saved to: {OUTPUT_FILE}")