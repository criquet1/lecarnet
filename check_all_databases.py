#!/usr/bin/env python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lecarnet.settings')
import django
django.setup()

from django.db import connections

# List all databases
from django.conf import settings
print("Available databases:", list(settings.DATABASES.keys()))
print()

# Check tables in each database
for db_alias in settings.DATABASES.keys():
    print(f"\n{'='*60}")
    print(f"Database: {db_alias}")
    print(f"{'='*60}")
    try:
        with connections[db_alias].cursor() as cursor:
            # Get all tables
            if settings.DATABASES[db_alias]['ENGINE'] == 'django.db.backends.postgresql':
                cursor.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    ORDER BY table_name
                """)
            else:  # SQLite
                cursor.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' 
                    ORDER BY name
                """)
            tables = cursor.fetchall()
            if tables:
                for row in tables:
                    print(f"  - {row[0]}")
            else:
                print("  (no tables)")
    except Exception as e:
        print(f"  Error: {e}")
