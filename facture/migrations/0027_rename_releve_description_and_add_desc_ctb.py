from django.db import migrations, models


def copy_desc_releve_to_desc_ctb(apps, schema_editor):
    table_names = set(schema_editor.connection.introspection.table_names())
    if 'facture_releve' not in table_names:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE facture_releve
            SET desc_ctb = desc_releve
            WHERE COALESCE(desc_ctb, '') = ''
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0026_setting_taxes_mode'),
    ]

    operations = [
        migrations.RenameField(
            model_name='releve',
            old_name='description',
            new_name='desc_releve',
        ),
        migrations.AddField(
            model_name='releve',
            name='desc_ctb',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(copy_desc_releve_to_desc_ctb, migrations.RunPython.noop),
    ]