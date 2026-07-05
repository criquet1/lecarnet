import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'lecarnet.settings'
import django
django.setup()
from django.db import connections

for db in ['Anonymus', 'client_alpha', 'client_test']:
    with connections[db].cursor() as c:
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='compte_setting' ORDER BY column_name")
        cols = [r[0] for r in c.fetchall()]
        print(f"{db}: {cols}")
