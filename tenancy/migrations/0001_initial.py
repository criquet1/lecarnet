from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClientDatabase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=50, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('db_alias', models.SlugField(max_length=50, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Base client',
                'verbose_name_plural': 'Bases clients',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='UserClientAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_default', models.BooleanField(default=False)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_accesses', to='tenancy.clientdatabase')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tenant_accesses', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Acces utilisateur client',
                'verbose_name_plural': 'Acces utilisateurs clients',
                'unique_together': {('user', 'client')},
            },
        ),
    ]
