"""
Corrige les PeriodePaie dont date_paie == date_fin (valeur par défaut incorrecte)
en recalculant date_paie comme le jeudi suivant date_fin.
"""
from datetime import timedelta

from django.db import migrations


def _prochain_jeudi(date):
    days_ahead = (3 - date.weekday()) % 7  # 3 = jeudi
    if days_ahead == 0:
        days_ahead = 7
    return date + timedelta(days=days_ahead)


def fix_date_paie(apps, schema_editor):
    db_alias = schema_editor.connection.alias

    # Vérifier que la table existe dans cette base avant d'agir
    # (la base centrale ne contient pas les tables paie des tenants).
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'paie_periodepaie')"
        )
        (table_exists,) = cursor.fetchone()

    if not table_exists:
        return

    PeriodePaie = apps.get_model('paie', 'PeriodePaie')
    to_update = []

    for periode in PeriodePaie.objects.using(db_alias).filter(date_paie=None):
        periode.date_paie = _prochain_jeudi(periode.date_fin)
        to_update.append(periode)

    # Corriger les enregistrements où date_paie == date_fin
    # (ancienne valeur par défaut incorrecte).
    for periode in PeriodePaie.objects.using(db_alias).exclude(date_paie=None):
        if periode.date_paie == periode.date_fin:
            periode.date_paie = _prochain_jeudi(periode.date_fin)
            to_update.append(periode)

    if to_update:
        PeriodePaie.objects.using(db_alias).bulk_update(to_update, ['date_paie'])


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(fix_date_paie, migrations.RunPython.noop),
    ]
