from django.db import migrations
from django.db.utils import OperationalError, ProgrammingError


def _safe_fetch_tenant_rate(schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                """
                SELECT taux_cnesst_employeur
                FROM paie_parametrestauxpaie
                WHERE taux_cnesst_employeur IS NOT NULL
                ORDER BY rrq_date_debut_effet DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except (ProgrammingError, OperationalError):
            return None


def forward_copy_cnesst_to_setting(apps, schema_editor):
    Setting = apps.get_model('compte', 'Setting')
    table_names = schema_editor.connection.introspection.table_names()
    if Setting._meta.db_table not in table_names:
        return

    try:
        setting = Setting.objects.order_by('id').first()
    except (ProgrammingError, OperationalError):
        return

    if setting is None:
        return

    taux = _safe_fetch_tenant_rate(schema_editor)
    if taux is None:
        return

    setting.taux_cnesst_employeur = taux
    setting.save(update_fields=['taux_cnesst_employeur'])


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0004_setting_taux_cnesst_employeur'),
        ('paie', '0004_parametrestauxpaie_blocs_et_cnesst'),
    ]

    operations = [
        migrations.RunPython(forward_copy_cnesst_to_setting, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='annee_cnesst',
        ),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='taux_cnesst_employeur',
        ),
    ]
