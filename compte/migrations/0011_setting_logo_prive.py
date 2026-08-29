from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0010_setting_fin_annee_jour_semaine'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='logo_prive',
            field=models.BinaryField(
                blank=True,
                editable=True,
                help_text="Logo prive televerse pour ce tenant, stocke dans sa base de donnees. Prioritaire sur le champ Logo ci-dessus si rempli.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='setting',
            name='logo_prive_type',
            field=models.CharField(
                blank=True,
                help_text="Type MIME du logo prive (ex: image/png), utilise pour le servir correctement.",
                max_length=50,
                null=True,
            ),
        ),
    ]
