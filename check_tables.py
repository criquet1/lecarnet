#!/usr/bin/env python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lecarnet.settings')
import django
django.setup()

from django.db import connection

print("Checking tables in 'default' database...")
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = cursor.fetchall()
    for row in tables:
        print(f"  - {row[0]}")
    
    # Check specifically for compte_setting
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'compte_setting'
        )
    """)
    exists = cursor.fetchone()[0]
    print(f"\ncompte_setting exists: {exists}")
