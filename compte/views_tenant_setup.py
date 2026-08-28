"""Vues et helpers pour la création et l'accompagnement de nouveaux clients
(tenants) : configuration de la base de données dédiée, assistant de
démarrage, création du tenant. Extrait de compte/views.py pour alléger ce
fichier.
"""

import json
import logging
import os
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import connections, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html

from facture.helpers.dates import prochaine_date_fin_exercice
from facture.models import Client, Fournisseur
from facture.utils import expert_required, get_settings
from tenancy.db_context import reset_current_tenant_alias, set_current_tenant_alias
from tenancy.models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess
from tenancy.services import resolve_database_alias, set_active_client_on_session, sync_user_client_accesses

from .forms import CreerTenantForm
from .models import Compte, ExerciceFinancier, Setting, Total


logger = logging.getLogger(__name__)


def _build_tenant_db_config(db_alias, db_name):
	default_db = settings.DATABASES.get('default', {})
	base_runtime_db = dict(connections.databases.get('default', default_db))
	engine = default_db.get('ENGINE', '')

	if 'postgresql' in engine:
		resolved_name = db_name or f'lecarnet_{db_alias}'
		config = dict(base_runtime_db)
		config.update({
			'ENGINE': default_db.get('ENGINE', 'django.db.backends.postgresql'),
			'NAME': resolved_name,
			'USER': default_db.get('USER', config.get('USER', '')),
			'PASSWORD': default_db.get('PASSWORD', config.get('PASSWORD', '')),
			'HOST': default_db.get('HOST', config.get('HOST', '127.0.0.1')),
			'PORT': default_db.get('PORT', config.get('PORT', '5432')),
			'OPTIONS': default_db.get('OPTIONS', config.get('OPTIONS', {})),
		})
		return config

	if 'sqlite3' in engine:
		filename = db_name or f'tenant_{db_alias}.sqlite3'
		if not filename.lower().endswith('.sqlite3'):
			filename = f'{filename}.sqlite3'
		config = dict(base_runtime_db)
		config.update({
			'ENGINE': 'django.db.backends.sqlite3',
			'NAME': str(settings.BASE_DIR / filename),
			'OPTIONS': default_db.get('OPTIONS', config.get('OPTIONS', {})),
		})
		return config

	raise ValueError('Moteur de base non supporte pour creation automatique de tenant.')


def _register_runtime_tenant_db(alias, db_config):
	settings.DATABASES[alias] = db_config
	connections.databases[alias] = db_config


def _rollback_runtime_tenant_db(alias):
	try:
		if alias in connections:
			connections[alias].close()
	except Exception:
		pass

	settings.DATABASES.pop(alias, None)
	connections.databases.pop(alias, None)


def _create_physical_tenant_db(db_config):
	engine = db_config.get('ENGINE', '')
	if 'postgresql' in engine:
		db_name = db_config['NAME']
		default_conn = connections['default']
		autocommit_before = default_conn.get_autocommit()
		default_conn.set_autocommit(True)
		try:
			with default_conn.cursor() as cursor:
				cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [db_name])
				if cursor.fetchone():
					raise ValueError(f"La base '{db_name}' existe deja.")
				cursor.execute(f'CREATE DATABASE "{db_name}"')
		finally:
			default_conn.set_autocommit(autocommit_before)
		return

	if 'sqlite3' in engine:
		return

	raise ValueError('Moteur de base non supporte pour creation automatique de tenant.')


def _drop_physical_tenant_db(db_config):
	engine = db_config.get('ENGINE', '')
	if 'postgresql' in engine:
		db_name = db_config['NAME']
		default_conn = connections['default']
		autocommit_before = default_conn.get_autocommit()
		default_conn.set_autocommit(True)
		try:
			with default_conn.cursor() as cursor:
				cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [db_name])
				if cursor.fetchone():
					cursor.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s', [db_name])
					cursor.execute(f'DROP DATABASE "{db_name}"')
		finally:
			default_conn.set_autocommit(autocommit_before)
		return

	if 'sqlite3' in engine:
		path = db_config.get('NAME')
		if path and os.path.exists(path):
			os.remove(path)
		return


def _persist_tenant_config(alias, db_config):
	config_path = settings.BASE_DIR / 'scripts' / 'oneclick.config.json'
	data = {}

	if config_path.exists():
		with config_path.open('r', encoding='utf-8') as fh:
			data = json.load(fh)

	tenants = data.get('tenants') if isinstance(data, dict) else None
	if not isinstance(tenants, dict):
		tenants = {}

	tenants[alias] = db_config
	data['tenants'] = tenants

	with config_path.open('w', encoding='utf-8') as fh:
		json.dump(data, fh, indent=2)


def _configured_tenant_aliases():
	tenant_json = (os.environ.get('TENANT_DATABASES_JSON') or '').strip()
	if tenant_json:
		try:
			payload = json.loads(tenant_json)
			if isinstance(payload, dict):
				return set(payload.keys())
		except ValueError:
			return set()
		return set()

	config_path = settings.BASE_DIR / 'scripts' / 'oneclick.config.json'
	if not config_path.exists():
		return set()

	try:
		with config_path.open('r', encoding='utf-8') as fh:
			payload = json.load(fh)
	except (OSError, ValueError):
		return set()

	tenants = payload.get('tenants') if isinstance(payload, dict) else {}
	if not isinstance(tenants, dict):
		return set()
	return set(tenants.keys())


def _tenant_env_snippet(alias, db_config):
	"""Extrait JSON pret a fusionner dans la variable Render TENANT_DATABASES_JSON."""
	return json.dumps({alias: db_config}, indent=2, ensure_ascii=False)


# --- Assistant de demarrage -------------------------------------------------
#
# Guide un utilisateur non-technique (ex: un comptable) a travers les etapes
# necessaires pour qu'un tenant fraichement cree soit pret a l'emploi:
# parametres de la compagnie, plan comptable, groupes de comptes. Chaque
# etape est detectee automatiquement (pas d'etat separe a maintenir) et les
# pages concernees redirigent ici une fois l'action terminee.

ONBOARDING_STEPS = [
	{
		'key': 'compagnies_facturation',
		'title': 'Compagnies (facturation)',
		'description': 'Ajouter les clients et fournisseurs utilises pour la facturation.',
		'url_name': 'facture',
		'action_label': 'Ouvrir la facturation',
	},
	{
		'key': 'plan_comptable',
		'title': 'Plan comptable',
		'description': 'Creer au moins un compte pour cette entreprise.',
		'url_name': 'compte',
		'action_label': 'Ouvrir le plan comptable',
	},
	{
		'key': 'parametres',
		'title': "Parametres de l'entreprise",
		'description': "Coordonnees et comptes de reference (TPS/TVQ, CAP/CAR, compte cheques, frais de retard). Les parametres de paie sont optionnels.",
		'url_name': 'settings',
		'action_label': 'Remplir les parametres',
	},
	{
		'key': 'groupes',
		'title': 'Groupes de comptes',
		'description': 'Creer au moins un groupe (total) utilise dans les etats financiers.',
		'url_name': 'totaux',
		'action_label': 'Ouvrir les groupes de comptes',
	},
]


def _onboarding_status(client):
	"""Calcule, pour un ClientDatabase donne, l'etat de chaque etape de l'assistant.

	Se connecte temporairement a la base du tenant (via le contextvar utilise par
	TenantDatabaseRouter) pour verifier ce qui existe deja, puis revient a l'etat
	precedent. Retourne (steps, alias) ou steps est une liste de dicts enrichis
	avec 'done', et alias est None si le tenant n'est pas connecte sur ce serveur.
	"""
	alias = resolve_database_alias(client.db_alias)
	steps = [dict(step) for step in ONBOARDING_STEPS]

	if not alias:
		for step in steps:
			step['done'] = False
		return steps, None

	token = set_current_tenant_alias(alias)
	try:
		settings_instance = Setting.objects.first()
		# Les coordonnees de base ET les comptes de reference (TPS/TVQ, CAP/CAR,
		# compte cheques, frais de retard) sont obligatoires pour considerer
		# cette etape terminee. Les parametres/comptes de paie restent optionnels:
		# toutes les compagnies ne font pas de paie.
		parametres_done = bool(
			settings_instance
			and settings_instance.nom
			and settings_instance.adresse
			and settings_instance.ville
			and settings_instance.code_postal
			and settings_instance.pays
			and settings_instance.phone
			and settings_instance.email
			and settings_instance.cap_id
			and settings_instance.car_id
			and settings_instance.compte_cheques_id
			and settings_instance.compte_fr_retard_id
			and settings_instance.compte_tps_percue_id
			and settings_instance.compte_tps_payee_id
			and settings_instance.compte_tvq_percue_id
			and settings_instance.compte_tvq_payee_id
		)
		plan_comptable_done = Compte.objects.exists()
		groupes_done = Total.objects.exists()
		compagnies_facturation_done = Client.objects.exists() or Fournisseur.objects.exists()
	finally:
		reset_current_tenant_alias(token)

	done_map = {
		'parametres': parametres_done,
		'plan_comptable': plan_comptable_done,
		'groupes': groupes_done,
		'compagnies_facturation': compagnies_facturation_done,
	}
	for step in steps:
		step['done'] = done_map.get(step['key'], False)

	return steps, alias


@login_required
@expert_required
def assistant_demarrage_list_page(request):
	clients = ClientDatabase.objects.select_related('societe').filter(is_active=True).order_by('name', 'id')
	rows = []
	for client in clients:
		steps, alias = _onboarding_status(client)
		rows.append({
			'client': client,
			'alias_ok': bool(alias),
			'complete': alias is not None and all(step['done'] for step in steps),
			'steps_done': sum(1 for step in steps if step['done']),
			'steps_total': len(steps),
		})

	return render(request, 'compte/assistant_demarrage_list.html', {
		'title': 'Assistant de demarrage',
		'rows': rows,
	})


@login_required
@expert_required
def assistant_demarrage_page(request, client_id):
	client = get_object_or_404(ClientDatabase.objects.select_related('societe'), pk=client_id)

	if not (request.user.is_superuser or UserClientAccess.objects.filter(user=request.user, client=client).exists()):
		messages.error(request, "Tu n as pas acces a ce tenant.")
		return redirect('assistant_demarrage_list')

	access, _ = UserClientAccess.objects.get_or_create(
		user=request.user,
		client=client,
		defaults={'is_default': not UserClientAccess.objects.filter(user=request.user, is_default=True).exists()},
	)
	set_active_client_on_session(request, access)
	sync_user_client_accesses(request.user)

	steps, alias = _onboarding_status(client)

	if alias is None:
		messages.warning(
			request,
			f"Le tenant '{client.name}' n est pas encore connecte sur ce serveur. "
			"Redemarre l application ou contacte le support technique.",
		)

	next_step = next((step for step in steps if not step['done']), None)
	all_done = alias is not None and next_step is None

	for step in steps:
		step['url'] = f"{reverse(step['url_name'])}?onboarding={client.id}"

	return render(request, 'compte/assistant_demarrage.html', {
		'title': f"Assistant de demarrage - {client.name}",
		'client': client,
		'steps': steps,
		'next_step': next_step,
		'all_done': all_done,
		'alias_ok': alias is not None,
	})


@login_required
@expert_required
def creer_tenant_page(request):
	if not Societe.objects.filter(is_active=True).exists():
		Societe.objects.get_or_create(
			slug='societe-principale',
			defaults={'name': 'Societe Principale', 'is_active': True},
		)

	if request.user.is_superuser:
		allowed_societes = Societe.objects.filter(is_active=True).order_by('name', 'id')
	else:
		allowed_societes = Societe.objects.filter(
			is_active=True,
			user_accesses__user=request.user,
		).order_by('name', 'id').distinct()

	fixed_societe = None
	if not request.user.is_superuser and allowed_societes.count() == 1:
		fixed_societe = allowed_societes.first()

	if request.method == 'POST':
		form = CreerTenantForm(request.POST, societes_qs=allowed_societes, fixed_societe=fixed_societe)
		if form.is_valid():
			name = form.cleaned_data['name'].strip()
			slug = form.cleaned_data['slug']
			societe = form.cleaned_data['societe']
			db_alias = form.cleaned_data['db_alias']
			db_name = form.cleaned_data['db_name']
			username = form.cleaned_data['username']
			temp_password = form.cleaned_data['temp_password']

			alias_exists_in_db = ClientDatabase.objects.filter(db_alias=db_alias).exists()
			alias_exists_in_settings = db_alias in settings.DATABASES

			# Un alias peut rester en memoire dans le process Django apres suppression;
			# on retire ces aliases runtime non persistes avant de valider le conflit.
			if alias_exists_in_settings and not alias_exists_in_db:
				persisted_aliases = _configured_tenant_aliases()
				if db_alias not in persisted_aliases:
					_rollback_runtime_tenant_db(db_alias)
					alias_exists_in_settings = db_alias in settings.DATABASES

			if not allowed_societes.filter(pk=societe.pk).exists():
				form.add_error('societe', 'Vous ne pouvez pas creer de tenant pour cette societe.')
			elif ClientDatabase.objects.filter(slug=slug).exists():
				form.add_error('slug', 'Ce slug est deja utilise.')
			elif alias_exists_in_db or alias_exists_in_settings:
				form.add_error('db_alias', 'Cet alias est deja utilise.')
			else:
				db_config = None
				physical_db_created = False
				try:
					db_config = _build_tenant_db_config(db_alias, db_name)
					_register_runtime_tenant_db(db_alias, db_config)
					_create_physical_tenant_db(db_config)
					physical_db_created = True
					call_command('migrate', database=db_alias, interactive=False, verbosity=0)

					# Cree tout de suite un exercice financier initial sur la base du
					# tenant: sans lui, la moindre page de facturation/comptabilite
					# plante des qu'elle touche une date, peu importe l'ordre dans
					# lequel l'expert complete ensuite l'assistant de demarrage.
					tenant_token = set_current_tenant_alias(db_alias)
					try:
						if not ExerciceFinancier.objects.using(db_alias).exists():
							initial_settings = get_settings()
							date_fin = prochaine_date_fin_exercice(date.today(), initial_settings)
							ExerciceFinancier.creer_a_partir_de_la_date_fin(date_fin, alias=db_alias)
					finally:
						reset_current_tenant_alias(tenant_token)

					# Toutes les ecritures dans la base centrale (utilisateur, tenant,
					# acces) sont regroupees dans une seule transaction: si l'une
					# d'elles echoue, elles sont TOUTES annulees automatiquement.
					# Cela evite qu'un tenant "fantome" (ClientDatabase sans base
					# physique valide, ou l'inverse) ne reste enregistre.
					with transaction.atomic():
						# Le nom d'utilisateur/mot de passe sont optionnels: l'expert qui
						# cree le tenant y a deja acces automatiquement (voir plus bas),
						# donc ce compte supplementaire n'est cree que si on le demande
						# explicitement. Les autres utilisateurs pourront toujours etre
						# ajoutes ensuite depuis "Gestion des societes".
						tenant_user = None
						if username and temp_password:
							user_model = get_user_model()
							tenant_user = user_model.objects.create_user(username=username, password=temp_password)

						client = ClientDatabase.objects.create(
							slug=slug,
							name=name,
							db_alias=db_alias,
							societe=societe,
							is_active=True,
						)

						UserSocieteAccess.objects.get_or_create(
							user=request.user,
							societe=societe,
							defaults={'is_default': not UserSocieteAccess.objects.filter(user=request.user, is_default=True).exists()},
						)

						has_default = UserClientAccess.objects.filter(user=request.user, is_default=True).exists()
						access, _ = UserClientAccess.objects.update_or_create(
							user=request.user,
							client=client,
							defaults={'is_default': not has_default},
						)

						if tenant_user is not None:
							UserSocieteAccess.objects.update_or_create(
								user=tenant_user,
								societe=societe,
								defaults={'is_default': True},
							)
							UserClientAccess.objects.update_or_create(
								user=tenant_user,
								client=client,
								defaults={'is_default': True},
							)
							UserClientAccess.objects.filter(user=tenant_user).exclude(client=client).delete()

						set_active_client_on_session(request, access)
						sync_user_client_accesses(request.user)

					# On ne memorise la connexion sur disque qu'une fois la
					# transaction ci-dessus confirmee: le fichier de config ne
					# peut plus pointer vers un tenant qui n'a pas ete cree.
					_persist_tenant_config(db_alias, db_config)

					if tenant_user is not None:
						messages.success(request, f"Le tenant '{name}' a ete cree et active. Utilisateur cree: {username}")
					else:
						messages.success(request, f"Le tenant '{name}' a ete cree et active. Tu peux deja y travailler avec ton propre compte.")
					if (os.environ.get('TENANT_DATABASES_JSON') or '').strip():
						snippet = _tenant_env_snippet(db_alias, db_config)
						messages.warning(
							request,
							format_html(
								'TENANT_DATABASES_JSON est defini sur ce serveur: ce tenant ne survivra pas au '
								'prochain redemarrage tant que tu n\'as pas ajoute la cle "{alias}" ci-dessous dans '
								'cette variable d\'environnement (fusionne-la avec les cles existantes, ne remplace '
								'pas les autres tenants), puis redeploie.<br>'
								'<pre class="mt-2 mb-0 p-2 bg-light border rounded" style="white-space: pre-wrap;">{snippet}</pre>',
								alias=db_alias,
								snippet=snippet,
							),
						)
					return redirect('assistant_demarrage', client_id=client.id)
				except Exception as exc:
					# tenant_user / client / acces sont crees a l'interieur d'un
					# bloc transaction.atomic(): en cas d'erreur, Django les a
					# deja annules automatiquement. On ne nettoie ici que ce qui
					# n'est PAS gere par la transaction (la base physique et
					# l'alias enregistre en memoire).
					_rollback_runtime_tenant_db(db_alias)
					if physical_db_created and db_config is not None:
						try:
							_drop_physical_tenant_db(db_config)
						except Exception:
							pass
					error_message = str(exc).strip() or 'Erreur inconnue'
					logger.exception('Echec creation tenant alias=%s name=%s username=%s', db_alias, name, username)
					messages.error(request, f"Creation impossible ({exc.__class__.__name__}): {error_message}")
					form.add_error(None, f"Creation impossible ({exc.__class__.__name__}): {error_message}")
	else:
		form = CreerTenantForm(societes_qs=allowed_societes, fixed_societe=fixed_societe)

	return render(request, 'compte/creer_tenant.html', {
		'title': 'Creer un tenant',
		'form': form,
		'fixed_societe': fixed_societe,
	})

