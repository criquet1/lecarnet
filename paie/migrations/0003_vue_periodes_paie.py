"""
Crée la vue v_periodes_paie dans chaque base tenant.

La vue ajoute des colonnes calculées utiles :
  - annee_paiement      : année de date_paie
  - mois_num_paiement   : mois (1-12) de date_paie
  - mois_paiement       : premier jour du mois de date_paie (date)
  - numero_periode_annee: numéro de période dans l'année par fréquence
"""
from django.db import migrations

CREATE_VIEW = """
CREATE OR REPLACE VIEW v_periodes_paie AS
SELECT
    pp.id,
    pp.frequence_paie_id,
    pp.date_debut,
    pp.date_fin,
    pp.date_paie,
    pp.fermee,
    EXTRACT(YEAR  FROM pp.date_paie)::int                    AS annee_paiement,
    EXTRACT(MONTH FROM pp.date_paie)::int                    AS mois_num_paiement,
    DATE_TRUNC('month', pp.date_paie)::date                  AS mois_paiement,
    ROW_NUMBER() OVER (
        PARTITION BY pp.frequence_paie_id,
                     EXTRACT(YEAR FROM pp.date_paie)::int
        ORDER BY pp.date_paie, pp.date_fin
    )::int                                                   AS numero_periode_annee
FROM paie_periodepaie pp;
"""

DROP_VIEW = "DROP VIEW IF EXISTS v_periodes_paie;"


def create_vue(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'paie_periodepaie')"
        )
        (table_exists,) = cursor.fetchone()

    if not table_exists:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(CREATE_VIEW)


def drop_vue(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'paie_periodepaie')"
        )
        (table_exists,) = cursor.fetchone()

    if not table_exists:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DROP_VIEW)


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0002_fix_periode_date_paie_jeudi'),
    ]

    operations = [
        migrations.RunPython(create_vue, drop_vue),
    ]
