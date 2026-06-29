# lecarnet

## Multi-client PostgreSQL (1 client = 1 base)

Le projet est maintenant prepare pour:

- Une base centrale (`default`) pour `auth`, `admin`, `sessions`, et le mapping utilisateur-client.
- Une base PostgreSQL par client pour les apps metier `facture` et `compte`.
- Une selection du client actif apres connexion.

## Variables d'environnement

### 1) Base centrale

Sous PowerShell:

```powershell
$env:DEFAULT_DB_ENGINE = "postgres"
$env:DEFAULT_DB_NAME = "lecarnet_central"
$env:DEFAULT_DB_USER = "postgres"
$env:DEFAULT_DB_PASSWORD = "postgres"
$env:DEFAULT_DB_HOST = "127.0.0.1"
$env:DEFAULT_DB_PORT = "5432"
```

### 2) Bases clientes

Configurer les alias DB clients via JSON:

```powershell
$env:TENANT_DATABASES_JSON = '{
	"client_alpha": {
		"ENGINE": "django.db.backends.postgresql",
		"NAME": "lecarnet_alpha",
		"USER": "postgres",
		"PASSWORD": "postgres",
		"HOST": "127.0.0.1",
		"PORT": "5432"
	},
	"client_beta": {
		"ENGINE": "django.db.backends.postgresql",
		"NAME": "lecarnet_beta",
		"USER": "postgres",
		"PASSWORD": "postgres",
		"HOST": "127.0.0.1",
		"PORT": "5432"
	}
}'
```

Les cles (`client_alpha`, `client_beta`) sont les alias utilises dans l'administration tenancy.

## Mise en service

1. Migrer la base centrale:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py migrate
```

2. Migrer les bases clientes:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py migrate_tenants
```

3. Creer un superuser:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py createsuperuser
```

4. Dans l'admin (`/admin`):

- Creer des `ClientDatabase` (slug, name, db_alias).
- Creer des `UserClientAccess` pour lier chaque utilisateur a un ou plusieurs clients.

5. Au login, l'utilisateur choisit son client actif si necessaire.

## Demarrage en un clic

1. Copier [scripts/oneclick.config.example.json](scripts/oneclick.config.example.json) vers [scripts/oneclick.config.json](scripts/oneclick.config.json), puis ajuster les valeurs PostgreSQL dans le fichier local.
2. Lancer le script PowerShell depuis la racine du projet:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/one_click_start.ps1
```

Ce script fait automatiquement:

- chargement des variables d'environnement (base centrale + bases clientes),
- migration de la base centrale,
- migration des bases clientes,
- creation/synchronisation des clients tenancy,
- creation/mise a jour du superuser admin et attribution de l'acces aux clients,
- `manage.py check`,
- demarrage du serveur Django.

Option utile:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/one_click_start.ps1 -NoRunServer
```

## Notes

- Pas de relation FK entre base centrale et bases clientes.
- Les donnees metier (`compte`, `facture`) sont routees vers la base client active via middleware + database router.
- Si un utilisateur n'a aucun client assigne, il est redirige vers l'ecran de selection client.

## Recuperation admin (phase 3)

Pour recreer/mettre a jour un admin fiable sur la base centrale:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py ensure_admin --username admin --password-env ADMIN_PASSWORD --database default --prune-other-superusers
```

Exemple (PowerShell):

```powershell
$env:ADMIN_PASSWORD = "TonMotDePasseFort"
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py ensure_admin --username admin --password-env ADMIN_PASSWORD --database default --prune-other-superusers
```

Cette commande:

- force `is_active`, `is_staff`, `is_superuser` sur le compte cible,
- met a jour le mot de passe,
- peut supprimer les autres superusers avec `--prune-other-superusers`.

## Healthcheck multi-tenant (phase 3)

Verification rapide et centralisee de l'etat multi-tenant:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py healthcheck_multitenant
```

Options utiles:

```powershell
# verifier seulement certains tenants
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py healthcheck_multitenant --alias client_alpha --alias client_test

# echouer aussi sur warnings (CI)
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py healthcheck_multitenant --fail-on-warn
```

La commande controle:

- connexion base centrale et bases tenants,
- coherence entre settings.DATABASES et tenancy.ClientDatabase,
- migrations en attente,
- presence d'au moins un superuser actif sur la base centrale.

## Validation parcours (phase 3.3)

Validation finale en une commande (healthcheck + parcours pages cles):

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py validate_parcours --username admin
```

Options:

```powershell
# forcer un tenant cible
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py validate_parcours --username admin --client-alias client_test

# mode strict (echec si warning healthcheck)
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py validate_parcours --username admin --strict
```

La commande valide notamment l'acces HTTP (statut 200) pour:

- accueil,
- facture,
- releve,
- journal general,
- grand livre,
- balance,
- CAP/CAR,
- rapport de taxes,
- pages comptes.

## Benchmark et test de charge (phase 2)

Benchmark SQL rapide des vues comptables:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py benchmark_ledger_views --database client_test
```

Chargement de donnees de stress sur un tenant:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py load_perf_tr_detail --database client_test --entries 25000 --details-per-entry 8
```

Nettoyage du dataset de stress cree par la commande:

```powershell
c:/Users/criqu/Documents/lecarnet/venv/Scripts/python.exe manage.py load_perf_tr_detail --database client_test --cleanup-only
```
