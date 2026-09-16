# Generated manually, meme patron que 0032_facturephotoenattente.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0034_messagecontact'),
    ]

    operations = [
        migrations.CreateModel(
            name='PetiteCaissePhotoEnAttente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('photo', models.BinaryField()),
                ('photo_type', models.CharField(blank=True, default='', max_length=50)),
                ('fournisseur_detecte', models.CharField(blank=True, default='', max_length=255)),
                ('date_detectee', models.CharField(blank=True, default='', max_length=20)),
                ('montant_total_detecte', models.CharField(blank=True, default='', max_length=20)),
                ('tps_detectee', models.CharField(blank=True, default='', max_length=20)),
                ('tvq_detectee', models.CharField(blank=True, default='', max_length=20)),
                ('montant_avant_taxes_detecte', models.CharField(blank=True, default='', max_length=20)),
                ('description_detectee', models.CharField(blank=True, default='', max_length=255)),
                ('erreur_analyse', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('traite', models.BooleanField(default=False, help_text="Vrai une fois ajoute au tableau de la petite caisse. La photo est conservee (compressee) pour consultation future.")),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
