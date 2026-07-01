from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0025_tr_desc_note_de_credit'),
    ]

    operations = [
        migrations.AddField(
            model_name='setting',
            name='taxes_mode',
            field=models.CharField(
                choices=[
                    ('RECLAMER', 'Les taxes sont generalement a reclamer'),
                    ('PAYER', 'Les taxes sont generalement a payer'),
                ],
                default='RECLAMER',
                max_length=16,
            ),
        ),
    ]
