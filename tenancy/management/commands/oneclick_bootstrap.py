import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections
from django.db.utils import OperationalError

from tenancy.models import ClientDatabase, UserClientAccess


class Command(BaseCommand):
    help = 'Bootstrap en un clic: migrations, clients tenancy et acces admin.'

    def _check_database_connection(self, alias):
        try:
            connections[alias].ensure_connection()
        except OperationalError as exc:
            db_settings = settings.DATABASES.get(alias, {})
            user = db_settings.get('USER', '')
            host = db_settings.get('HOST', '')
            port = db_settings.get('PORT', '')
            name = db_settings.get('NAME', '')
            raise CommandError(
                f"Connexion PostgreSQL impossible pour '{alias}' (db={name}, user={user}, host={host}, port={port}). "
                f"Verifie scripts/oneclick.config.json. Detail: {exc}"
            ) from exc

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-admin',
            action='store_true',
            help='Nes cree pas de compte admin local.',
        )

    def handle(self, *args, **options):
        tenant_aliases = [alias for alias in settings.DATABASES.keys() if alias != 'default']
        if not tenant_aliases:
            raise CommandError('Aucune base client configuree. Definis TENANT_DATABASES_JSON.')

        self.stdout.write(self.style.NOTICE('0/4 Verification des connexions DB...'))
        self._check_database_connection('default')
        for alias in tenant_aliases:
            self._check_database_connection(alias)

        self.stdout.write(self.style.NOTICE('1/4 Migration base centrale...'))
        call_command('migrate', database='default', interactive=False)

        self.stdout.write(self.style.NOTICE('2/4 Migration bases clientes...'))
        call_command('migrate_tenants')

        self.stdout.write(self.style.NOTICE('3/4 Synchronisation des clients tenancy...'))
        clients = []
        for alias in tenant_aliases:
            client, _ = ClientDatabase.objects.update_or_create(
                db_alias=alias,
                defaults={
                    'slug': alias,
                    'name': alias.replace('_', ' ').title(),
                    'is_active': True,
                },
            )
            clients.append(client)

        if options.get('skip_admin'):
            self.stdout.write(self.style.WARNING('4/4 Creation admin ignoree (--skip-admin).'))
            self.stdout.write(self.style.SUCCESS('Bootstrap termine.'))
            return

        self.stdout.write(self.style.NOTICE('4/4 Creation/mise a jour admin + acces clients...'))
        username = os.environ.get('ONECLICK_ADMIN_USERNAME', 'admin').strip() or 'admin'
        email = os.environ.get('ONECLICK_ADMIN_EMAIL', 'admin@localhost').strip() or 'admin@localhost'
        password = os.environ.get('ONECLICK_ADMIN_PASSWORD', 'Admin123!ChangeMe').strip() or 'Admin123!ChangeMe'

        User = get_user_model()
        admin, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'is_superuser': True,
                'is_staff': True,
                'is_active': True,
            },
        )

        if not created:
            admin.is_superuser = True
            admin.is_staff = True
            admin.is_active = True
            if email:
                admin.email = email

        admin.set_password(password)
        admin.save()

        for index, client in enumerate(clients):
            UserClientAccess.objects.update_or_create(
                user=admin,
                client=client,
                defaults={'is_default': index == 0},
            )

        self.stdout.write(self.style.SUCCESS('Bootstrap termine.'))
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin pret: username={username} / mot de passe={password}"
            )
        )
