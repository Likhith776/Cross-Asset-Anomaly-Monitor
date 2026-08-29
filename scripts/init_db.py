#!/usr/bin/env python3
"""
Database initialization script for the Cross-Asset Anomaly Detection System.

Creates the core analytical tables: market_features, anomaly_events,
and correlation_snapshots. Designed to be run once before seeding
or starting the application.

Usage:
    python scripts/init_db.py
    # or with a custom connection string:
    DATABASE_URL=postgresql://user:pass@host:5432/db python scripts/init_db.py
"""

import os
import sys
import time

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    """Create a psycopg2 connection from the DATABASE_URL environment variable."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[ERROR] DATABASE_URL environment variable is not set.")
        print("        Example: export DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies")
        sys.exit(1)
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        print(f"[INFO] Connected to database successfully.")
        return conn
    except psycopg2.OperationalError as e:
        print(f"[ERROR] Failed to connect to database: {e}")
        sys.exit(1)


def create_market_features_table(cur):
    """Create the market_features table with composite index on (timestamp DESC, symbol)."""
    print("[CREATE] market_features ...", end=" ", flush=True)
    start = time.time()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_features (
            id              SERIAL          PRIMARY KEY,
            timestamp       TIMESTAMPTZ     NOT NULL,
            symbol          VARCHAR(20)     NOT NULL,
            price           DECIMAL(12,4),
            return_1m       DECIMAL(10,6),
            return_5m       DECIMAL(10,6),
            z_score         DECIMAL(8,4),
            ewma_vol        DECIMAL(10,8),
            pca_residual    DECIMAL(8,4),
            volume          BIGINT,
            created_at      TIMESTAMPTZ     DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_features_timestamp_symbol
        ON market_features (timestamp DESC, symbol);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_features_symbol
        ON market_features (symbol);
    """)

    elapsed = time.time() - start
    print(f"done ({elapsed:.2f}s)")


def create_anomaly_events_table(cur):
    """Create the anomaly_events table with indexes on timestamp and symbol."""
    print("[CREATE] anomaly_events ...", end=" ", flush=True)
    start = time.time()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_events (
            id              SERIAL          PRIMARY KEY,
            timestamp       TIMESTAMPTZ     NOT NULL,
            symbol          VARCHAR(20)     NOT NULL,
            anomaly_score   DECIMAL(4,3)    NOT NULL,
            z_flag          BOOLEAN         DEFAULT FALSE,
            ewma_flag       BOOLEAN         DEFAULT FALSE,
            pca_flag        BOOLEAN         DEFAULT FALSE,
            description     TEXT,
            macro_context   VARCHAR(120),
            llm_explanation TEXT,
            created_at      TIMESTAMPTZ     DEFAULT NOW()
        );
    """)

    # Migrations for databases created before these columns existed.
    # Idempotent: ADD COLUMN IF NOT EXISTS is a no-op once applied.
    cur.execute("""
        ALTER TABLE anomaly_events
            ADD COLUMN IF NOT EXISTS macro_context VARCHAR(120);
    """)
    cur.execute("""
        ALTER TABLE anomaly_events
            ADD COLUMN IF NOT EXISTS llm_explanation TEXT;
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_events_timestamp
        ON anomaly_events (timestamp DESC);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_events_symbol
        ON anomaly_events (symbol);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_events_score
        ON anomaly_events (anomaly_score DESC);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_events_timestamp_symbol
        ON anomaly_events (timestamp DESC, symbol);
    """)

    elapsed = time.time() - start
    print(f"done ({elapsed:.2f}s)")


def create_correlation_snapshots_table(cur):
    """Create the correlation_snapshots table with index on timestamp."""
    print("[CREATE] correlation_snapshots ...", end=" ", flush=True)
    start = time.time()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS correlation_snapshots (
            id              SERIAL          PRIMARY KEY,
            timestamp       TIMESTAMPTZ     NOT NULL,
            symbol_a        VARCHAR(20)     NOT NULL,
            symbol_b        VARCHAR(20)     NOT NULL,
            correlation     DECIMAL(6,4),
            CONSTRAINT uq_corr_snapshot_pair_time
                UNIQUE (timestamp, symbol_a, symbol_b)
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_correlation_snapshots_timestamp
        ON correlation_snapshots (timestamp DESC);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_correlation_snapshots_pair
        ON correlation_snapshots (symbol_a, symbol_b);
    """)

    elapsed = time.time() - start
    print(f"done ({elapsed:.2f}s)")


def create_anomaly_feedback_table(cur):
    """Create the anomaly_feedback table.

    One row per human judgment on an anomaly_events row. A given event
    can be labeled multiple times — the most recent row wins, and the
    rolling-precision endpoint counts each event at most once.
    """
    print("[CREATE] anomaly_feedback ...", end=" ", flush=True)
    start = time.time()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_feedback (
            id                  SERIAL          PRIMARY KEY,
            anomaly_event_id    INTEGER         NOT NULL
                                                REFERENCES anomaly_events(id)
                                                ON DELETE CASCADE,
            label               VARCHAR(20)     NOT NULL
                                                CHECK (label IN ('confirmed', 'false_positive')),
            noted_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            note                TEXT
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_feedback_event
        ON anomaly_feedback (anomaly_event_id);
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_feedback_noted_at
        ON anomaly_feedback (noted_at DESC);
    """)

    elapsed = time.time() - start
    print(f"done ({elapsed:.2f}s)")


def grant_permissions(cur):
    """Grant necessary permissions if a non-superuser might be used."""
    print("[GRANT] Setting table permissions ...", end=" ", flush=True)
    tables = [
        "market_features",
        "anomaly_events",
        "correlation_snapshots",
        "anomaly_feedback",
    ]
    for table in tables:
        cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON TABLE {} TO postgres;").format(
            sql.Identifier(table)
        ))
        cur.execute(sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {}_id_seq TO postgres;").format(
            sql.Identifier(table)
        ))
    print("done")


def verify_tables(cur):
    """Verify all tables exist and print row counts."""
    print("\n[VERIFY] Checking created tables:")
    tables = [
        "market_features",
        "anomaly_events",
        "correlation_snapshots",
        "anomaly_feedback",
    ]
    all_ok = True
    for table in tables:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);",
            (table,)
        )
        exists = cur.fetchone()[0]
        if exists:
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {};").format(sql.Identifier(table)))
            count = cur.fetchone()[0]
            print(f"         ✓ {table:<30} ({count:,} rows)")
        else:
            print(f"         ✗ {table:<30} MISSING")
            all_ok = False
    return all_ok


def print_indexes(cur):
    """Print all indexes created on our tables."""
    print("\n[INDEXES] Listing indexes on project tables:")
    cur.execute("""
        SELECT
            tablename,
            indexname,
            indexdef
        FROM pg_indexes
        WHERE tablename IN (
            'market_features', 'anomaly_events',
            'correlation_snapshots', 'anomaly_feedback'
        )
        ORDER BY tablename, indexname;
    """)
    rows = cur.fetchall()
    for tablename, indexname, indexdef in rows:
        print(f"         {tablename}.{indexname}")
    print(f"         Total: {len(rows)} indexes")


def drop_tables_if_requested(cur):
    """Optionally drop existing tables to allow a clean re-init."""
    drop = os.environ.get("DROP_EXISTING", "").lower() in ("1", "true", "yes")
    if not drop:
        return

    print("\n[DROP] Dropping existing tables (DROP_EXISTING=1) ...")
    tables_in_order = [
        "anomaly_feedback",   # drops first — references anomaly_events
        "correlation_snapshots",
        "anomaly_events",
        "market_features",
    ]
    for table in tables_in_order:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(sql.Identifier(table)))
        print(f"         Dropped {table}")


def main():
    """Main entry point: connect, create tables, verify, close."""
    print("=" * 64)
    print("  Cross-Asset Anomaly Detection — Database Initialization")
    print("=" * 64)
    print()

    conn = get_connection()
    cur = conn.cursor()

    try:
        # Optionally drop tables for a clean slate
        drop_tables_if_requested(cur)

        # Create all tables
        create_market_features_table(cur)
        create_anomaly_events_table(cur)
        create_correlation_snapshots_table(cur)
        create_anomaly_feedback_table(cur)

        # Permissions
        #grant_permissions(cur)

        # Commit all DDL
        conn.commit()

        # Verify
        all_ok = verify_tables(cur)
        print_indexes(cur)

        if all_ok:
            print("\n[DONE] All tables created and verified successfully.")
            print("       Run `python scripts/seed_demo_data.py` to populate with demo data.")
        else:
            print("\n[WARN] Some tables are missing. Check the error output above.")
            sys.exit(1)

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cur.close()
        conn.close()
        print("\n[INFO] Database connection closed.")


if __name__ == "__main__":
    main()