from django.db import migrations
from django.db.utils import OperationalError, ProgrammingError


def _safe_fetch_tenant_rate(schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                """
                SELECT taux_fss_employeur
                FROM paie_parametrestauxpaie
                WHERE taux_fss_employeur IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except (ProgrammingError, OperationalError):
            return None


def forward_copy_fss_to_setting(apps, schema_editor):
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

    setting.taux_fss_employeur = taux
    setting.save(update_fields=['taux_fss_employeur'])


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0005_setting_taux_fss_employeur'),
        ('paie', '0005_move_cnesst_to_setting'),
    ]

    operations = [
        migrations.RunPython(forward_copy_fss_to_setting, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='fss_date_debut_effet',
        ),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='fss_date_fin_effet',
        ),
        migrations.RemoveField(
            model_name='parametrestauxpaie',
            name='taux_fss_employeur',
        ),
    ]
