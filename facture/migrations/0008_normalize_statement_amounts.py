from django.db import migrations
from django.db.models import F


def normalize_statement_amounts(apps, schema_editor):
    Releve = apps.get_model('facture', 'Releve')
    database_alias = schema_editor.connection.alias
    Releve.objects.using(database_alias).filter(retrait__lt=0).update(retrait=-F('retrait'))
    Releve.objects.using(database_alias).filter(depot__lt=0).update(depot=-F('depot'))


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0007_correct_accounting_views'),
    ]

    operations = [
        migrations.RunPython(normalize_statement_amounts, migrations.RunPython.noop),
    ]