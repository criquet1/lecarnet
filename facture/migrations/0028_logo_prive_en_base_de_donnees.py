from django.db import migrations, models


class Migration(migrations.Migration):
    """Remplace le stockage du logo prive : au lieu d'un fichier sur disque
    (ImageField, ajoute par la migration 0027), l'image est desormais stockee
    directement dans la base de donnees du tenant (BinaryField), pour survivre
    aux redeploiements sur Render (disque non persistant). La colonne
    ImageField precedente est supprimee et recreee en BinaryField -- son
    contenu (un chemin de fichier, pas une image) n'a plus de sens dans le
    nouveau format, d'ou la suppression plutot qu'une conversion.
    """

    dependencies = [
        ('facture', '0027_add_logo_prive_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='client',
            name='logo_prive',
        ),
        migrations.AddField(
            model_name='client',
            name='logo_prive',
            field=models.BinaryField(
                blank=True,
                editable=True,
                help_text="Logo prive televerse pour ce client, stocke dans la base de donnees de ce tenant. Prioritaire sur le champ Logo ci-dessus si rempli.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='client',
            name='logo_prive_type',
            field=models.CharField(
                blank=True,
                help_text="Type MIME du logo prive (ex: image/png), utilise pour le servir correctement.",
                max_length=50,
                null=True,
            ),
        ),
        migrations.RemoveField(
            model_name='fournisseur',
            name='logo_prive',
        ),
        migrations.AddField(
            model_name='fournisseur',
            name='logo_prive',
            field=models.BinaryField(
                blank=True,
                editable=True,
                help_text="Logo prive televerse pour ce fournisseur, stocke dans la base de donnees de ce tenant. Prioritaire sur le champ Logo ci-dessus si rempli.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='fournisseur',
            name='logo_prive_type',
            field=models.CharField(
                blank=True,
                help_text="Type MIME du logo prive (ex: image/png), utilise pour le servir correctement.",
                max_length=50,
                null=True,
            ),
        ),
    ]
