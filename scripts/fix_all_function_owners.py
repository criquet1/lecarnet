"""
Redonne la propriete (OWNER) de TOUTES les fonctions du schema public au role
de chaque tenant. Necessaire quand une fonction (ex. solde_fin_pour_exercice)
s'est retrouvee appartenant a un autre role (ex. postgres, apres une
restauration de base) : CREATE OR REPLACE FUNCTION echoue alors avec
"doit etre le proprietaire de la fonction" tant que le role du tenant n'est
pas remis proprietaire.

A lancer avec le python du venv du projet :

    venv\\Scripts\\python scripts\\fix_all_function_owners.py

Sans danger a relancer plusieurs fois (un ALTER OWNER deja a jour ne fait rien de plus).
"""
import psycopg2

DEFAULT_DB_USER = "postgres"
DEFAULT_DB_PASSWORD = "hOrizOn9*9"
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "5432"

# (nom_de_la_base, role_a_qui_redonner_la_propriete)
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
        cur.execute("""
            SELECT p.proname, pg_get_function_identity_arguments(p.oid)
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
        """)
        functions = cur.fetchall()
        for name, args in functions:
            cur.execute(f'ALTER FUNCTION "{name}"({args}) OWNER TO "{role}";')
        noms = ", ".join(n for n, _ in functions)
        print(f"OK  {dbname} -> proprietaire {role} sur {len(functions)} fonctions: {noms}")
        conn.close()
    except Exception as e:
        print(f"ECHEC {dbname} ({role}): {e}")
