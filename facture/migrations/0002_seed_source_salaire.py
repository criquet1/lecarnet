from django.db import migrations
from django.db.utils import OperationalError, ProgrammingError


def seed_source_salaire(apps, schema_editor):
    Source = apps.get_model('facture', 'Source')
    table_name = Source._meta.db_table
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if table_name not in existing_tables:
        return
    try:
        Source.objects.using(schema_editor.connection.alias).get_or_create(nom='Salaire')
    except (ProgrammingError, OperationalError):
        return


def unseed_source_salaire(apps, schema_editor):
    Source = apps.get_model('facture', 'Source')
    table_name = Source._meta.db_table
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if table_name not in existing_tables:
        return
    try:
        Source.objects.using(schema_editor.connection.alias).filter(nom='Salaire').delete()
    except (ProgrammingError, OperationalError):
        return


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_source_salaire, unseed_source_salaire),
    ]
