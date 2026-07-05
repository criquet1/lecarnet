from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_societe_and_accesses(apps, schema_editor):
    Societe = apps.get_model('tenancy', 'Societe')
    ClientDatabase = apps.get_model('tenancy', 'ClientDatabase')
    UserClientAccess = apps.get_model('tenancy', 'UserClientAccess')
    UserSocieteAccess = apps.get_model('tenancy', 'UserSocieteAccess')

    default_societe, _ = Societe.objects.get_or_create(
        slug='societe-principale',
        defaults={'name': 'Societe Principale', 'is_active': True},
    )

    ClientDatabase.objects.filter(societe__isnull=True).update(societe=default_societe)

    pairs = UserClientAccess.objects.filter(
        client__societe__isnull=False,
    ).values_list('user_id', 'client__societe_id').distinct()

    for user_id, societe_id in pairs:
        UserSocieteAccess.objects.get_or_create(
            user_id=user_id,
            societe_id=societe_id,
            defaults={'is_default': False},
        )

    for user_id in UserSocieteAccess.objects.values_list('user_id', flat=True).distinct():
        defaults = UserSocieteAccess.objects.filter(user_id=user_id, is_default=True)
        if defaults.exists():
            first_default = defaults.order_by('id').first()
            UserSocieteAccess.objects.filter(user_id=user_id).exclude(id=first_default.id).update(is_default=False)
            continue

        first_access = UserSocieteAccess.objects.filter(user_id=user_id).order_by('societe__name', 'id').first()
        if first_access:
            first_access.is_default = True
            first_access.save(update_fields=['is_default'])


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0002_usersecuritystate'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Societe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=50, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Societe',
                'verbose_name_plural': 'Societes',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='clientdatabase',
            name='societe',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clients', to='tenancy.societe'),
        ),
        migrations.CreateModel(
            name='UserSocieteAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_default', models.BooleanField(default=False)),
                ('societe', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_accesses', to='tenancy.societe')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='societe_accesses', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Acces utilisateur societe',
                'verbose_name_plural': 'Acces utilisateurs societes',
                'unique_together': {('user', 'societe')},
            },
        ),
        migrations.RunPython(seed_societe_and_accesses, migrations.RunPython.noop),
    ]
