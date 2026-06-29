from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from tenancy.models import ClientDatabase


class Command(BaseCommand):
    help = "Verifie la sante multi-tenant: connexions DB, coherence aliases, migrations, superuser central."

    def add_arguments(self, parser):
        parser.add_argument(
            "--alias",
            action="append",
            dest="aliases",
            help="Alias tenant a verifier (repeter l'option pour plusieurs).",
        )
        parser.add_argument(
            "--fail-on-warn",
            action="store_true",
            help="Retourne un code d'erreur aussi sur warnings.",
        )

    def _can_connect(self, alias):
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True, "ok"
        except Exception as exc:
            return False, str(exc)

    def _pending_migrations(self, alias):
        try:
            executor = MigrationExecutor(connections[alias])
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)
            return [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
        except Exception as exc:
            return [f"ERREUR_MIGRATIONS: {exc}"]

    def handle(self, *args, **options):
        warnings = []
        errors = []

        default_alias = "default"
        configured_aliases = set(settings.DATABASES.keys())
        all_tenant_aliases = [a for a in settings.DATABASES.keys() if a != default_alias]

        requested_aliases = options.get("aliases") or all_tenant_aliases
        tenant_aliases = []
        for alias in requested_aliases:
            if alias == default_alias:
                warnings.append("Alias default ignore dans --alias.")
                continue
            if alias not in configured_aliases:
                errors.append(f"Alias inconnu dans settings.DATABASES: {alias}")
                continue
            tenant_aliases.append(alias)

        self.stdout.write(self.style.NOTICE("[1/5] Verification connexion base centrale..."))
        ok, detail = self._can_connect(default_alias)
        if ok:
            self.stdout.write(self.style.SUCCESS("- default: connexion OK"))
        else:
            errors.append(f"Connexion default echouee: {detail}")

        self.stdout.write(self.style.NOTICE("[2/5] Verification connexion bases tenant..."))
        if not tenant_aliases:
            warnings.append("Aucun alias tenant a verifier.")
        for alias in tenant_aliases:
            ok, detail = self._can_connect(alias)
            if ok:
                self.stdout.write(self.style.SUCCESS(f"- {alias}: connexion OK"))
            else:
                errors.append(f"Connexion {alias} echouee: {detail}")

        self.stdout.write(self.style.NOTICE("[3/5] Coherence tenancy.ClientDatabase <-> settings..."))
        try:
            db_aliases_in_table = set(
                ClientDatabase.objects.using(default_alias).values_list("db_alias", flat=True)
            )
            active_db_aliases_in_table = set(
                ClientDatabase.objects.using(default_alias)
                .filter(is_active=True)
                .values_list("db_alias", flat=True)
            )

            missing_in_settings = sorted(db_aliases_in_table - set(all_tenant_aliases))
            missing_in_tenancy_table = sorted(set(all_tenant_aliases) - db_aliases_in_table)
            missing_active_in_settings = sorted(active_db_aliases_in_table - set(all_tenant_aliases))

            for alias in missing_in_settings:
                warnings.append(f"ClientDatabase.db_alias absent de settings.DATABASES: {alias}")
            for alias in missing_in_tenancy_table:
                warnings.append(f"Alias settings sans ligne ClientDatabase: {alias}")
            for alias in missing_active_in_settings:
                errors.append(f"Client actif sans config DB dans settings: {alias}")

            self.stdout.write(self.style.SUCCESS("- coherence aliases verifiee"))
        except Exception as exc:
            errors.append(f"Lecture ClientDatabase echouee: {exc}")

        self.stdout.write(self.style.NOTICE("[4/5] Verification migrations en attente..."))
        aliases_for_migrations = [default_alias] + tenant_aliases
        for alias in aliases_for_migrations:
            pending = self._pending_migrations(alias)
            if not pending:
                self.stdout.write(self.style.SUCCESS(f"- {alias}: aucune migration en attente"))
                continue

            if pending[0].startswith("ERREUR_MIGRATIONS:"):
                errors.append(f"{alias}: {pending[0]}")
                continue

            # On limite l'affichage pour garder un output lisible.
            shown = ", ".join(pending[:5])
            suffix = "" if len(pending) <= 5 else f" (+{len(pending) - 5} autres)"
            warnings.append(f"{alias}: migrations en attente: {shown}{suffix}")

        self.stdout.write(self.style.NOTICE("[5/5] Verification superuser central..."))
        try:
            User = get_user_model()
            superusers = User.objects.using(default_alias).filter(is_superuser=True, is_active=True).count()
            if superusers >= 1:
                self.stdout.write(self.style.SUCCESS(f"- superusers actifs sur default: {superusers}"))
            else:
                errors.append("Aucun superuser actif sur default.")
        except Exception as exc:
            errors.append(f"Verification superuser echouee: {exc}")

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("Resume healthcheck multi-tenant"))
        self.stdout.write(f"- tenants verifies: {len(tenant_aliases)}")
        self.stdout.write(f"- warnings: {len(warnings)}")
        self.stdout.write(f"- errors: {len(errors)}")

        if warnings:
            self.stdout.write(self.style.WARNING("Warnings:"))
            for item in warnings:
                self.stdout.write(f"  - {item}")

        if errors:
            self.stdout.write(self.style.ERROR("Errors:"))
            for item in errors:
                self.stdout.write(f"  - {item}")

        if errors or (warnings and options.get("fail_on_warn")):
            raise CommandError("Healthcheck multi-tenant en echec.")

        self.stdout.write(self.style.SUCCESS("Healthcheck multi-tenant OK."))
