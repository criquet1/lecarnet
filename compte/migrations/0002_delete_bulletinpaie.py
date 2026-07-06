from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('compte', '0001_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='BulletinPaie',
        ),
    ]