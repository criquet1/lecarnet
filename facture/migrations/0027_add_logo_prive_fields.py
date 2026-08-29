import facture.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0026_tr_desc_compte_modifie_par_non_expert'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='logo_prive',
            field=models.ImageField(
                blank=True,
                help_text="Logo prive televerse pour ce client. Visible seulement par les utilisateurs de ce tenant, prioritaire sur le champ Logo ci-dessus si rempli.",
                null=True,
                upload_to=facture.models.logo_prive_upload_path,
            ),
        ),
        migrations.AddField(
            model_name='fournisseur',
            name='logo_prive',
            field=models.ImageField(
                blank=True,
                help_text="Logo prive televerse pour ce fournisseur. Visible seulement par les utilisateurs de ce tenant, prioritaire sur le champ Logo ci-dessus si rempli.",
                null=True,
                upload_to=facture.models.logo_prive_upload_path,
            ),
        ),
    ]
