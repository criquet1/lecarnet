#!/usr/bin/env python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lecarnet.settings')
import django
django.setup()

from django.db import connection

# Remove the problematic migration records
print("Removing migration records...")
with connection.cursor() as cursor:
    # Remove compte migrations that created Setting
    cursor.execute("DELETE FROM django_migrations WHERE app='compte' AND name IN ('0007_setting')")
    print(f"Deleted compte.0007_setting records")
    
    # Remove facture 0031
    cursor.execute("DELETE FROM django_migrations WHERE app='facture' AND name IN ('0031_migrate_setting_to_compte')")
    print(f"Deleted facture.0031_migrate_setting_to_compte records")
    
    connection.commit()
    print("Migration records removed. You can now run: python manage.py migrate")
