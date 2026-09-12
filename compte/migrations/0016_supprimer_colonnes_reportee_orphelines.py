# Certaines bases (notamment celle de render.com) ont déjà exécuté la
# version originale de la migration 0015 — celle qui ajoutait vraiment les
# colonnes "reportee" (NOT NULL) et "date_reportee" — AVANT que cette
# fonctionnalité soit abandonnée et que le fichier 0015 soit neutralisé
# (operations = []) plus tard dans le développement. Comme Django ne rejoue
# jamais une migration déjà marquée "appliquée", ces bases-là gardent
# physiquement les colonnes orphelines, alors que le modèle actuel
# (compte/models.py) ne les définit plus — d'où l'erreur "violation de la
# contrainte NOT NULL" à chaque nouvel enregistrement.
#
# Cette migration supprime ces colonnes si elles existent, et ne fait rien
# si elles n'existent pas (bases où 0015 n'a jamais réellement ajouté quoi
# que ce soit) — donc sans danger, peu importe l'état de la base.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0015_interetligne_reportee_date_reportee'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE compte_interetligne DROP COLUMN IF EXISTS reportee;
                ALTER TABLE compte_interetligne DROP COLUMN IF EXISTS date_reportee;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
