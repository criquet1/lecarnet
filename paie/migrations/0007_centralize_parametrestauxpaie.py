from django.conf import settings
from django.db import connections, migrations
from django.db.utils import OperationalError, ProgrammingError


RATE_FIELDS = (
    'rrq_date_debut_effet',
    'rrq_date_fin_effet',
    'taux_rrq_employe',
    'taux_rrq_employeur',
    'max_assurable_rrq',
    'rqap_date_debut_effet',
    'rqap_date_fin_effet',
    'taux_rqap_employe',
    'taux_rqap_employeur',
    'max_assurable_rqap',
    'ae_date_debut_effet',
    'ae_date_fin_effet',
    'taux_ae_employe',
    'taux_ae_employeur',
    'max_assurable_ae',
)


def forward_move_rates_to_default(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return

    ParametresTauxPaie = apps.get_model('paie', 'ParametresTauxPaie')
    default_qs = ParametresTauxPaie.objects.using('default')

    existing_keys = set(default_qs.values_list(*RATE_FIELDS))

    for alias in settings.DATABASES:
        if alias == 'default':
            continue

        conn = connections[alias]
        table_names = conn.introspection.table_names()
        if ParametresTauxPaie._meta.db_table not in table_names:
            continue

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        rrq_date_debut_effet,
                        rrq_date_fin_effet,
                        taux_rrq_employe,
                        taux_rrq_employeur,
                        max_assurable_rrq,
                        rqap_date_debut_effet,
                        rqap_date_fin_effet,
                        taux_rqap_employe,
                        taux_rqap_employeur,
                        max_assurable_rqap,
                        ae_date_debut_effet,
                        ae_date_fin_effet,
                        taux_ae_employe,
                        taux_ae_employeur,
                        max_assurable_ae
                    FROM paie_parametrestauxpaie
                    ORDER BY rrq_date_debut_effet, id
                    """
                )
                rows = cursor.fetchall()
        except (ProgrammingError, OperationalError):
            continue

        for row in rows:
            key = tuple(row)
            if key in existing_keys:
                continue

            payload = dict(zip(RATE_FIELDS, row))
            default_qs.create(**payload)
            existing_keys.add(key)

    # Purge des enregistrements sur les bases tenant: source de verite = default.
    for alias in settings.DATABASES:
        if alias == 'default':
            continue

        conn = connections[alias]
        table_names = conn.introspection.table_names()
        if ParametresTauxPaie._meta.db_table not in table_names:
            continue

        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM paie_parametrestauxpaie")
        except (ProgrammingError, OperationalError):
            continue


def backward_noop(apps, schema_editor):
    # On ne repousse pas automatiquement les taux vers les tenants.
    return


class Migration(migrations.Migration):

    dependencies = [
        ('paie', '0006_move_fss_to_setting'),
    ]

    operations = [
        migrations.RunPython(forward_move_rates_to_default, backward_noop),
    ]
