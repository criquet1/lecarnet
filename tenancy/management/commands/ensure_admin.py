import os
from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Cree/met a jour un superutilisateur admin sur la base centrale (ou alias cible)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="admin",
            help="Nom d'utilisateur admin cible (defaut: admin).",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Mot de passe admin en clair (usage local uniquement).",
        )
        parser.add_argument(
            "--password-env",
            default="",
            help="Nom de variable d'environnement contenant le mot de passe.",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Alias DB cible (defaut: default).",
        )
        parser.add_argument(
            "--prune-other-superusers",
            action="store_true",
            help="Supprime les autres superusers de la meme base.",
        )

    def _resolve_password(self, options):
        raw_password = (options.get("password") or "").strip()
        password_env = (options.get("password_env") or "").strip()

        if raw_password and password_env:
            raise CommandError("Utiliser soit --password, soit --password-env, pas les deux.")

        if password_env:
            env_value = os.environ.get(password_env, "")
            if not env_value:
                raise CommandError(f"Variable d'environnement introuvable ou vide: {password_env}")
            return env_value

        if raw_password:
            return raw_password

        prompt_password = getpass("Mot de passe admin: ")
        if not prompt_password:
            raise CommandError("Mot de passe vide non autorise.")
        return prompt_password

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in settings.DATABASES:
            raise CommandError(f"Alias inconnu: {alias}")

        username = (options["username"] or "").strip()
        if not username:
            raise CommandError("--username est obligatoire.")

        password = self._resolve_password(options)

        User = get_user_model()
        manager = User._default_manager.db_manager(alias)

        user = manager.filter(username=username).first()
        created = user is None
        if created:
            user = manager.model(username=username)

        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save(using=alias)

        pruned_count = 0
        if options.get("prune_other_superusers"):
            others = manager.filter(is_superuser=True).exclude(pk=user.pk)
            pruned_count = others.count()
            if pruned_count:
                others.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Admin pret sur '{alias}': username={username}, created={created}, pruned={pruned_count}"
            )
        )
