from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0003_societe_and_user_societe_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='societe',
            name='adresse',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='societe',
            name='telephone',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='societe',
            name='email',
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name='societe',
            name='personne_ressource',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='societe',
            name='site_web',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='societe',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='societe',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default='2026-07-05T00:00:00Z'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='societe',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
