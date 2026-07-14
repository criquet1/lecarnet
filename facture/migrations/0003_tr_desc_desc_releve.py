from django.db import migrations, models
from django.db.utils import OperationalError, ProgrammingError


def populate_desc_releve(apps, schema_editor):
    Tr_desc = apps.get_model('facture', 'Tr_desc')
    Releve = apps.get_model('facture', 'Releve')

    db_alias = schema_editor.connection.alias
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if Tr_desc._meta.db_table not in existing_tables or Releve._meta.db_table not in existing_tables:
        return

    try:
        tr_desc_ids = list(Tr_desc.objects.using(db_alias).filter(desc_releve='').values_list('id', flat=True))
    except (OperationalError, ProgrammingError):
        return

    for tr_desc_id in tr_desc_ids:
        try:
            releve = (
                Releve.objects.using(db_alias)
                .filter(ecriture_tr_desc_id=tr_desc_id)
                .order_by('id')
                .first()
            )
        except (OperationalError, ProgrammingError):
            return

        if releve and releve.desc_releve:
            try:
                Tr_desc.objects.using(db_alias).filter(pk=tr_desc_id).update(desc_releve=releve.desc_releve)
            except (OperationalError, ProgrammingError):
                return


def reverse_populate_desc_releve(apps, schema_editor):
    Tr_desc = apps.get_model('facture', 'Tr_desc')

    db_alias = schema_editor.connection.alias
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if Tr_desc._meta.db_table not in existing_tables:
        return

    try:
        Tr_desc.objects.using(db_alias).filter(desc_releve__isnull=False).update(desc_releve='')
    except (OperationalError, ProgrammingError):
        return


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0002_seed_source_salaire'),
    ]

    operations = [
        migrations.AddField(
            model_name='tr_desc',
            name='desc_releve',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(populate_desc_releve, reverse_populate_desc_releve),
    ]