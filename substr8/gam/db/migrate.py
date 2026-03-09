#!/usr/bin/env python3
"""
GAM Database Migration Script

Applies schema to Postgres + pgvector database.
"""

import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


def get_connection_string():
    """Get database connection string from env or file."""
    # Try environment variable first
    url = os.environ.get("GAM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    
    if url:
        return url
    
    # Try secrets file
    secrets_path = Path.home() / ".openclaw" / "secrets" / "neon-url.txt"
    if secrets_path.exists():
        return secrets_path.read_text().strip()
    
    raise ValueError(
        "No database URL found. Set GAM_DATABASE_URL or DATABASE_URL environment variable."
    )


def apply_schema(conn, schema_path: Path):
    """Apply SQL schema file."""
    schema_sql = schema_path.read_text()
    
    with conn.cursor() as cur:
        # Split by semicolons and execute each statement
        statements = [s.strip() for s in schema_sql.split(';') if s.strip() and not s.strip().startswith('--')]
        
        for i, stmt in enumerate(statements):
            try:
                cur.execute(stmt)
                print(f"  ✓ Statement {i+1}/{len(statements)}")
            except psycopg2.Error as e:
                if "already exists" in str(e):
                    print(f"  ○ Statement {i+1}/{len(statements)} (already exists)")
                else:
                    print(f"  ✗ Statement {i+1}/{len(statements)}: {e}")
                    raise
    
    conn.commit()


def check_pgvector(conn):
    """Check if pgvector extension is available."""
    with conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        result = cur.fetchone()
        return result is not None


def migrate(database_url: str = None):
    """Run all migrations."""
    print("🧠 GAM Database Migration")
    print("=" * 40)
    
    url = database_url or get_connection_string()
    print(f"Database: {url[:50]}...")
    
    # Connect
    print("\n📡 Connecting...")
    conn = psycopg2.connect(url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    print("  ✓ Connected")
    
    # Check pgvector
    print("\n🔌 Checking pgvector...")
    if check_pgvector(conn):
        print("  ✓ pgvector already installed")
    else:
        print("  ○ Installing pgvector...")
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("  ✓ pgvector installed")
    
    # Apply schema
    print("\n📋 Applying schema...")
    schema_path = Path(__file__).parent / "schema.sql"
    apply_schema(conn, schema_path)
    
    # Verify tables
    print("\n🔍 Verifying tables...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name LIKE 'gam_%'
            ORDER BY table_name
        """)
        tables = cur.fetchall()
        for (table,) in tables:
            print(f"  ✓ {table}")
    
    conn.close()
    
    print("\n" + "=" * 40)
    print("✓ Migration complete!")
    return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="GAM Database Migration")
    parser.add_argument("--url", help="Database URL (or set GAM_DATABASE_URL)")
    parser.add_argument("--verify", action="store_true", help="Only verify, don't apply")
    args = parser.parse_args()
    
    try:
        if args.verify:
            url = args.url or get_connection_string()
            conn = psycopg2.connect(url)
            
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name LIKE 'gam_%'
                """)
                tables = [t[0] for t in cur.fetchall()]
            
            conn.close()
            
            expected = ["gam_tenants", "gam_branches", "gam_memories", "gam_proposals", "gam_audit_log"]
            missing = set(expected) - set(tables)
            
            if missing:
                print(f"Missing tables: {missing}")
                sys.exit(1)
            else:
                print(f"✓ All {len(expected)} tables present")
                sys.exit(0)
        else:
            migrate(args.url)
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
