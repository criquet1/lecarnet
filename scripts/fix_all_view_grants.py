"""
Redonne le droit SELECT sur TOUTES les vues du schema public au role de chaque tenant.
A lancer avec le python du venv du projet :

    venv\\Scripts\\python scripts\\fix_all_view_grants.py

Sans danger a relancer plusieurs fois (un GRANT deja present ne fait rien).
"""
import psycopg2

DEFAULT_DB_USER = "postgres"
DEFAULT_DB_PASSWORD = "hOrizOn9*9"
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "5432"

# (nom_de_la_base, role_a_qui_redonner_le_droit)
TENANTS = [
    ("lecarnet_alpha", "client_alpha"),
    ("lecarnet_test", "client_test"),
    ("lecarnet_anonymus", "anonymus"),
    ("lecarnet_bravo", "client_bravo"),
    ("lecarnet_huppe", "huppe"),
    ("lecarnet_client_de_nancy", "client_de_nancy"),
]

for dbname, role in TENANTS:
    try:
        conn = psycopg2.connect(
            host=DEFAULT_DB_HOST, port=DEFAULT_DB_PORT,
            user=DEFAULT_DB_USER, password=DEFAULT_DB_PASSWORD,
            dbname=dbname,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.views WHERE table_schema = 'public'")
        views = [r[0] for r in cur.fetchall()]
        for view in views:
            cur.execute(f'GRANT SELECT ON "{view}" TO "{role}";')
        print(f"OK  {dbname} -> SELECT accorde a {role} sur {len(views)} vues: {', '.join(views)}")
        conn.close()
    except Exception as e:
        print(f"ECHEC {dbname} ({role}): {e}")
