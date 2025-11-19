#!/usr/bin/env python3
"""Script to set up the Supabase database schema."""

import sys
from pathlib import Path
from app.models.database import get_supabase_client


def run_migration(sql_file: Path) -> bool:
    """Run a migration SQL file."""
    if not sql_file.exists():
        print(f"❌ Migration file not found: {sql_file}")
        return False

    print(f"\n📄 Running migration: {sql_file.name}")

    sql_content = sql_file.read_text()

    try:
        supabase = get_supabase_client()

        # Execute the SQL using Supabase's RPC or direct SQL
        # Note: Supabase Python client doesn't have direct SQL execution
        # We need to use the REST API directly
        import os
        import requests

        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY')

        if not supabase_url or not supabase_key:
            print("❌ SUPABASE_URL and SUPABASE_KEY environment variables required")
            return False

        # Use Supabase Management API to execute SQL
        # Note: This requires service_role key, not anon key
        print("⚠️  Note: Running migrations requires executing the SQL directly in Supabase dashboard")
        print(f"   Or use: supabase db push")
        print("\n" + "="*80)
        print("SQL to execute:")
        print("="*80)
        print(sql_content[:500] + "..." if len(sql_content) > 500 else sql_content)
        print("="*80)

        return False

    except Exception as e:
        print(f"❌ Error running migration: {e}")
        return False


def main():
    """Set up the database."""
    migrations_dir = Path(__file__).parent.parent / "supabase" / "migrations"

    if not migrations_dir.exists():
        print(f"❌ Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    print("🗄️  Setting up Supabase database schema...")
    print(f"📂 Migrations directory: {migrations_dir}")

    migration_files = sorted(migrations_dir.glob("*.sql"))

    if not migration_files:
        print("❌ No migration files found")
        sys.exit(1)

    print(f"Found {len(migration_files)} migration files")

    print("\n" + "="*80)
    print("IMPORTANT: Supabase Python client cannot execute DDL statements directly.")
    print("Please run migrations using one of these methods:")
    print("="*80)
    print("\nOption 1: Using Supabase CLI (Recommended)")
    print("  cd /Users/alexandersibast/Documents/Financesum")
    print("  supabase link --project-ref phikzlqaibobgipnxdmp")
    print("  supabase db push")
    print("\nOption 2: Using Supabase Dashboard")
    print("  1. Go to https://supabase.com/dashboard/project/phikzlqaibobgipnxdmp/editor")
    print("  2. Click 'SQL Editor'")
    print("  3. Copy and paste the migration files:")
    for migration_file in migration_files:
        print(f"     - {migration_file.name}")
    print("  4. Click 'Run'")
    print("\nOption 3: Using psql")
    print("  Get the connection string from Supabase dashboard and run:")
    print("  psql <connection_string> < supabase/migrations/001_initial_schema.sql")
    print("="*80)


if __name__ == "__main__":
    main()
