from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0009_alter_source_nom'),
    ]

    operations = [
        migrations.AddField(
            model_name='compagnie',
            name='created_by_non_expert',
            field=models.BooleanField(default=False),
        ),
    ]