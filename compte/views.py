from decimal import Decimal
from datetime import date, timedelta
import json
import logging
import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import DatabaseError, connections, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.connection import ConnectionDoesNotExist
from django.utils import timezone
from django.utils.html import format_html
from facture.helpers.dates import prochaine_date_fin_exercice, verifier_exercice_modifiable
from facture.models import Client, CompagnieSoldeDepart, CompteReleve, Fournisseur, SoldeFin, Source, Tr_desc, Tr_detail
from facture.views import _next_no_ej
from facture.utils import ensure_tax_authority_companies, expert_required, get_settings, parse_decimal, read_csv_rows
from tenancy.db_context import reset_current_tenant_alias, set_current_tenant_alias
from tenancy.models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess
from tenancy.services import mark_user_must_change_password, resolve_database_alias, set_active_client_on_session, sync_user_client_accesses, user_must_change_password

from .forms import CompteCsvImportForm, CompteForm, CreerTenantForm, SettingForm
from .models import Compte, ExerciceFinancier, Setting, SoldeAuxLivres, Total



logger = logging.getLogger(__name__)


def _ensure_default_frequences_paie():
	from paie.models import FrequencePaie

	defaults = [
		(FrequencePaie.HEBDOMADAIRE, 'Hebdomadaire', 52),
		(FrequencePaie.AUX_2_SEMAINES, 'Aux 2 semaines', 26),
		(FrequencePaie.DEUX_FOIS_MOIS, '2 fois par mois', 24),
		(FrequencePaie.PAR_MOIS, 'Par mois', 12),
	]

	for code, nom, periodes in defaults:
		FrequencePaie.objects.update_or_create(
			code=code,
			defaults={
				'nom': nom,
				'nombre_periodes_par_annee': periodes,
			},
		)


def _fetch_totaux_rows():
	rows = []
	for solde in SoldeFin.objects.select_related('compte_numero'):
		solde_final = solde.solde_final or Decimal('0')
		rows.append({
			'compte': solde.compte_numero,
			'debit': solde_final if solde_final >= 0 else Decimal('0'),
			'credit': abs(solde_final) if solde_final < 0 else Decimal('0'),
			'solde_depart': solde.solde_depart or Decimal('0'),
		})

	total_debit = sum((row['debit'] for row in rows), Decimal('0'))
	total_credit = sum((row['credit'] for row in rows), Decimal('0'))
	is_balanced = total_debit == total_credit
	return rows, total_debit, total_credit, is_balanced


def _import_comptes_csv(csv_file):
	rows = read_csv_rows(csv_file.read())
	report = {
		'created': 0,
		'updated': 0,
		'skipped': 0,
		'errors': [],
	}

	if not rows:
		report['errors'].append('Le fichier CSV ne contient aucune ligne de donnees.')
		return report

	parsed_rows = []
	for idx, row in enumerate(rows, start=2):
		numero_raw = (row.get('numero') or row.get('compte_no') or '').strip()
		libelle = (row.get('libelle') or row.get('compte_libelle') or '').strip()
		no_total_raw = (row.get('no_total') or row.get('compte_total') or '').strip()

		if not numero_raw and not libelle and not no_total_raw:
			continue

		try:
			numero = int(numero_raw)
		except ValueError:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: numero invalide ({numero_raw}).')
			continue

		if not libelle:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: libelle manquant.')
			continue

		try:
			no_total = int(no_total_raw)
		except ValueError:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: no_total invalide ({no_total_raw}).')
			continue

		parsed_rows.append((numero, libelle, no_total))

	if not parsed_rows:
		return report

	with transaction.atomic():
		unique_totals = sorted({no_total for _, _, no_total in parsed_rows})
		totals_map = Total.objects.in_bulk(unique_totals, field_name='no_total')

		missing_totals = [
			Total(
				no_total=no_total,
				desc='Sans total' if no_total == 0 else f'Total {no_total}',
			)
			for no_total in unique_totals
			if no_total not in totals_map
		]
		if missing_totals:
			Total.objects.bulk_create(missing_totals)
			totals_map = Total.objects.in_bulk(unique_totals, field_name='no_total')

		unique_numeros = sorted({numero for numero, _, _ in parsed_rows})
		existing_comptes = Compte.objects.in_bulk(unique_numeros, field_name='numero')

		to_create = []
		to_update = []
		for numero, libelle, no_total in parsed_rows:
			total_obj = totals_map.get(no_total)
			if total_obj is None:
				report['skipped'] += 1
				report['errors'].append(f'Ligne compte {numero}: total introuvable ({no_total}).')
				continue

			existing = existing_comptes.get(numero)
			if existing is None:
				to_create.append(
					Compte(
						numero=numero,
						libelle=libelle,
						no_total=total_obj,
					)
				)
			else:
				existing.libelle = libelle
				existing.no_total = total_obj
				to_update.append(existing)

		if to_create:
			Compte.objects.bulk_create(to_create)
		if to_update:
			Compte.objects.bulk_update(to_update, ['libelle', 'no_total'])

		report['created'] += len(to_create)
		report['updated'] += len(to_update)

		all_compte_ids = set(unique_numeros)
		existing_solde_ids = set(
			SoldeAuxLivres.objects.filter(compte_id__in=all_compte_ids).values_list('compte_id', flat=True)
		)
		missing_solde_ids = [
			SoldeAuxLivres(compte_id=compte_id, solde_depart=Decimal('0'))
			for compte_id in sorted(all_compte_ids - existing_solde_ids)
		]
		if missing_solde_ids:
			SoldeAuxLivres.objects.bulk_create(missing_solde_ids)

	return report

def _build_repartition_state():
	settings_instance = get_settings()
	cap_total = Decimal('0')
	car_total = Decimal('0')

	if settings_instance and settings_instance.cap_id:
		cap_solde = SoldeAuxLivres.objects.filter(compte_id=settings_instance.cap_id).first()
		if cap_solde:
			cap_total = cap_solde.solde_depart

	if settings_instance and settings_instance.car_id:
		car_solde = SoldeAuxLivres.objects.filter(compte_id=settings_instance.car_id).first()
		if car_solde:
			car_total = car_solde.solde_depart

	entities = sorted(
		[{'type': 'fournisseur', 'obj': f} for f in Fournisseur.objects.filter(active=True)] +
		[{'type': 'client', 'obj': c} for c in Client.objects.filter(active=True)],
		key=lambda item: item['obj'].nom.lower()
	)

	soldes_map = {}
	for item in CompagnieSoldeDepart.objects.filter(fournisseur__isnull=False):
		soldes_map[('fournisseur', item.fournisseur_id)] = item.montant
	for item in CompagnieSoldeDepart.objects.filter(client__isnull=False):
		soldes_map[('client', item.client_id)] = item.montant

	repartition_rows = []
	cap_reparti = Decimal('0')
	car_reparti = Decimal('0')

	for entity in entities:
		key = (entity['type'], entity['obj'].pk)
		montant = soldes_map.get(key, Decimal('0'))
		repartition_rows.append({
			'compagnie': entity['obj'],
			'type_label': 'CAP' if entity['type'] == 'fournisseur' else 'CAR',
			'montant': montant,
			'field_name': f"repartition_{entity['type']}_{entity['obj'].pk}",
		})
		if entity['type'] == 'fournisseur':
			cap_reparti += montant
		else:
			car_reparti += montant

	return {
		'rows': repartition_rows,
		'cap_total': cap_total,
		'car_total': car_total,
		'cap_reparti': cap_reparti,
		'car_reparti': car_reparti,
		'ecart_cap': cap_reparti - cap_total,
		'ecart_car': car_reparti - car_total,
	}


@expert_required
def compte_page(request):
	import_report = None
	repartition_report = None
	edit_numero = (request.GET.get('edit') or request.POST.get('editing_numero') or '').strip()
	editing_compte = Compte.objects.filter(pk=edit_numero).first() if edit_numero else None

	if request.method == 'POST' and request.POST.get('inline_solde_compte'):
		compte_id = (request.POST.get('inline_solde_compte') or '').strip()
		raw_value = (request.POST.get('solde_depart') or '').strip()
		compte = Compte.objects.filter(pk=compte_id).first() if compte_id else None

		if not compte:
			return JsonResponse({'ok': False, 'error': 'Compte introuvable.'}, status=404)

		solde_depart = parse_decimal(raw_value)
		if solde_depart is None:
			return JsonResponse({'ok': False, 'error': 'Valeur invalide.'}, status=400)

		SoldeAuxLivres.objects.update_or_create(
			compte=compte,
			defaults={'solde_depart': solde_depart},
		)

		return JsonResponse({
			'ok': True,
			'compte_id': compte.pk,
			'solde_depart': format(solde_depart, '.2f'),
		})

	if request.method == 'POST' and request.POST.get('link_releve_account'):
		releve_account_id = (request.POST.get('link_releve_account') or '').strip()
		compte_comptable_id = (request.POST.get('compte_comptable_id') or '').strip() or None
		releve_account = CompteReleve.objects.filter(pk=releve_account_id).first()

		if not releve_account:
			return JsonResponse({'ok': False, 'error': 'Compte de relevé introuvable.'}, status=404)

		compte_comptable = None
		if compte_comptable_id:
			compte_comptable = Compte.objects.filter(pk=compte_comptable_id).first()
			if not compte_comptable:
				return JsonResponse({'ok': False, 'error': 'Compte comptable introuvable.'}, status=404)

		releve_account.compte_comptable = compte_comptable
		releve_account.save(update_fields=['compte_comptable'])

		return JsonResponse({
			'ok': True,
			'releve_account_id': releve_account.pk,
			'compte_comptable_id': compte_comptable_id or '',
			'compte_comptable_label': str(compte_comptable) if compte_comptable else '',
		})

	if request.method == 'POST':
		if request.POST.get('import_csv'):
			import_form = CompteCsvImportForm(request.POST, request.FILES)
			if import_form.is_valid():
				try:
					import_report = _import_comptes_csv(import_form.cleaned_data['csv_file'])
				except UnicodeDecodeError as exc:
					import_report = {
						'created': 0,
						'updated': 0,
						'skipped': 0,
						'errors': [f'Import impossible: {exc}'],
					}
			form = CompteForm(instance=editing_compte)
		else:
			if request.POST.get('save_repartition'):
				import_form = CompteCsvImportForm()
				form = CompteForm(instance=editing_compte)
				entities = (
					[('fournisseur', f) for f in Fournisseur.objects.filter(active=True)] +
					[('client', c) for c in Client.objects.filter(active=True)]
				)
				errors = []
				with transaction.atomic():
					for entity_type, entity in entities:
						field_name = f'repartition_{entity_type}_{entity.pk}'
						montant = parse_decimal(request.POST.get(field_name, '0'))
						if montant is None:
							errors.append(f"{entity.nom}: montant invalide")
							continue
						if entity_type == 'fournisseur':
							CompagnieSoldeDepart.objects.update_or_create(
								fournisseur=entity,
								defaults={'montant': montant},
							)
						else:
							CompagnieSoldeDepart.objects.update_or_create(
								client=entity,
								defaults={'montant': montant},
							)

				if errors:
					repartition_report = {
						'ok': False,
						'message': 'Certaines lignes sont invalides.',
						'errors': errors,
					}
				else:
					repartition_report = {
						'ok': True,
						'message': 'Repartition enregistree.',
						'errors': [],
					}
			else:
				import_form = CompteCsvImportForm()
				form = CompteForm(request.POST, instance=editing_compte)
				if form.is_valid():
					compte = form.save()
					SoldeAuxLivres.objects.get_or_create(
						compte=compte,
						defaults={'solde_depart': Decimal('0')},
					)
					return redirect('compte')
	else:
		form = CompteForm(instance=editing_compte)
		import_form = CompteCsvImportForm()

	repartition_state = _build_repartition_state()

	comptes = Compte.objects.select_related('no_total').order_by('numero')
	soldes_par_compte = {compte.pk: Decimal('0') for compte in comptes}
	for solde in SoldeAuxLivres.objects.select_related('compte'):
		soldes_par_compte[solde.compte_id] = solde.solde_depart
	total_solde_depart = sum((soldes_par_compte.get(compte.pk, Decimal('0')) for compte in comptes), Decimal('0'))

	comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

	return render(request, 'compte/compte.html', {
		'title': 'Comptes',
		'form': form,
		'import_form': import_form,
		'import_report': import_report,
		'repartition_report': repartition_report,
		'repartition_state': repartition_state,
		'comptes': comptes,
		'editing_compte': editing_compte,
		'soldes_par_compte': soldes_par_compte,
		'total_solde_depart': total_solde_depart,
		'comptes_releves': comptes_releves,
		'onboarding_client': ClientDatabase.objects.filter(pk=(request.GET.get('onboarding') or '').strip()).first() if request.GET.get('onboarding') else None,
	})


@expert_required
def settings_page(request):
	from paie.models import FrequencePaie
	_ensure_default_frequences_paie()
	settings_instance = get_settings()
	onboarding_client_id = (request.POST.get('onboarding') or request.GET.get('onboarding') or '').strip()

	if request.method == 'POST':
		form = SettingForm(request.POST, instance=settings_instance)
		if form.is_valid():
			settings_instance = form.save()
			ensure_tax_authority_companies(settings_instance)
			if onboarding_client_id:
				messages.success(request, "Parametres enregistres. Passons a l'etape suivante.")
				return redirect('assistant_demarrage', client_id=onboarding_client_id)
			return redirect('settings')
	else:
		form = SettingForm(instance=settings_instance)

	onboarding_client = None
	if onboarding_client_id:
		onboarding_client = ClientDatabase.objects.filter(pk=onboarding_client_id).first()

	return render(request, 'compte/settings.html', {
		'title': 'Paramètres',
		'form': form,
		'settings_instance': settings_instance,
		'frequences_paie': FrequencePaie.objects.all(),
		'onboarding_client': onboarding_client,
	})


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


@login_required
def force_password_change_page(request):
	if not user_must_change_password(request.user):
		return redirect('accueil')

	if request.method == 'POST':
		form = PasswordChangeForm(request.user, request.POST)
		if form.is_valid():
			user = form.save()
			state = mark_user_must_change_password(user, False)
			update_session_auth_hash(request, user)
			if state is None or user_must_change_password(user):
				messages.error(request, 'Le mot de passe a ete mis a jour, mais le statut de securite n a pas pu etre synchronise. Reessayez.')
				return redirect('force_password_change')
			messages.success(request, 'Mot de passe mis a jour.')
			return redirect('accueil')
	else:
		form = PasswordChangeForm(request.user)

	return render(request, 'tenancy/force_password_change.html', {
		'title': 'Modifier votre mot de passe',
		'form': form,
	})


@login_required
def user_password_change_page(request):
	if user_must_change_password(request.user):
		return redirect('force_password_change')

	if request.method == 'POST':
		form = PasswordChangeForm(request.user, request.POST)
		if form.is_valid():
			user = form.save()
			update_session_auth_hash(request, user)
			messages.success(request, 'Mot de passe mis a jour.')
			return redirect('accueil')
	else:
		form = PasswordChangeForm(request.user)

	return render(request, 'tenancy/user_password_change.html', {
		'title': 'Modifier votre mot de passe',
		'form': form,
	})


@expert_required
def totaux_page(request):
	rows, total_debit, total_credit, is_balanced = _fetch_totaux_rows()
	onboarding_id = (request.GET.get('onboarding') or '').strip()
	return render(request, 'compte/totaux.html', {
		'title': 'Totaux',
		'rows': rows,
		'total_debit': total_debit,
		'total_credit': total_credit,
		'is_balanced': is_balanced,
		'onboarding_client': ClientDatabase.objects.filter(pk=onboarding_id).first() if onboarding_id else None,
	})
@expert_required
def feuille_de_travail_page(request):
	return render(request, 'compte/feuille_de_travail.html', {
		'title': 'Feuille de travail',
	})


def _parse_compte_numero(raw_value):
	value = (raw_value or '').strip()
	if not value:
		return None
	if value.isdigit():
		return int(value)
	match = re.match(r'^(\d{4})', value)
	if not match:
		return None
	return int(match.group(1))


def _resolve_transaction_date(raw_value):
	value = (raw_value or '').strip().lower()
	if value == 'today':
		return timezone.localdate()
	if value == 'yesterday':
		return timezone.localdate() - timedelta(days=1)
	try:
		return date.fromisoformat(value)
	except ValueError:
		return None
	return None


@expert_required
def transactions_page(request):
	try:
		try:
			compagnies = sorted(
				[{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
				[{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
				key=lambda item: item['obj'].nom.lower()
			)
			comptes = list(Compte.objects.order_by('numero'))
			settings_instance = get_settings()
		except (OperationalError, ProgrammingError, ConnectionDoesNotExist):
			logger.exception('Transactions indisponible: tables facture/compte manquantes sur la base active.')
			messages.error(
				request,
				'La base client active n\'est pas initialisee (tables facture/compte manquantes). '
				'Lancez les migrations tenant (migrate_tenants) puis rechargez la page.'
			)
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': [],
				'comptes': [],
			})

		def _render_transactions_page():
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
				'comptes': comptes,
				'compte_cap_id': settings_instance.cap_id if settings_instance else None,
				'compte_car_id': settings_instance.car_id if settings_instance else None,
			})

		if request.method == 'POST':
			raw_date = (request.POST.get('date_select') or '').strip()
			raw_compagnie_id = (request.POST.get('compagnie') or '').strip()
			description = (request.POST.get('description') or '').strip()
			source_name = (request.POST.get('source') or '').strip()

			compte_values = request.POST.getlist('comptes_comptables[]')
			debit_values = request.POST.getlist('montant_debit[]')
			credit_values = request.POST.getlist('montant_credit[]')

			date_value = _resolve_transaction_date(raw_date)
			if not date_value:
				messages.error(request, 'Veuillez selectionner une date valide.')
				return _render_transactions_page()

			compagnie = None
			compagnie_type = 'client'
			if raw_compagnie_id:
				if ':' in raw_compagnie_id:
					compagnie_type, raw_compagnie_id = raw_compagnie_id.split(':', 1)
					compagnie_type = compagnie_type.strip().lower()
				compagnie_model = Fournisseur if compagnie_type == 'fournisseur' else Client
				try:
					compagnie = compagnie_model.objects.filter(pk=raw_compagnie_id).first()
				except (ValueError, TypeError):
					messages.error(request, 'Compagnie invalide.')
					return _render_transactions_page()
				except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
					logger.exception('Echec lecture compagnie: base active non initialisee.')
					messages.error(
						request,
						'Impossible de charger la compagnie: base client active non initialisee. '
						'Executez les migrations tenant puis reessayez.'
					)
					return _render_transactions_page()
				if not compagnie:
					messages.error(request, 'Compagnie invalide.')
					return _render_transactions_page()

			if not description:
				messages.error(request, 'La description est obligatoire.')
				return _render_transactions_page()

			if not source_name:
				source_name = 'Manuel'

			line_count = max(len(compte_values), len(debit_values), len(credit_values))
			lines = []
			total_debit = Decimal('0')
			total_credit = Decimal('0')

			for idx in range(line_count):
				compte_raw = compte_values[idx] if idx < len(compte_values) else ''
				debit_raw = debit_values[idx] if idx < len(debit_values) else ''
				credit_raw = credit_values[idx] if idx < len(credit_values) else ''

				if not (compte_raw or debit_raw or credit_raw):
					continue

				compte_numero = _parse_compte_numero(compte_raw)
				if not compte_numero:
					messages.error(request, f'Ligne {idx + 1}: compte comptable invalide (format attendu: 1234).')
					return _render_transactions_page()

				try:
					compte = Compte.objects.filter(pk=compte_numero).first()
				except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
					logger.exception('Echec lecture compte: base active non initialisee.')
					messages.error(
						request,
						'Impossible de charger les comptes: base client active non initialisee. '
						'Executez les migrations tenant puis reessayez.'
					)
					return _render_transactions_page()
				if not compte:
					messages.error(request, f'Ligne {idx + 1}: le compte {compte_numero} est introuvable.')
					return _render_transactions_page()

				debit_amount = parse_decimal(debit_raw) if (debit_raw or '').strip() else Decimal('0')
				credit_amount = parse_decimal(credit_raw) if (credit_raw or '').strip() else Decimal('0')
				if debit_amount is None or credit_amount is None:
					messages.error(request, f'Ligne {idx + 1}: montant debit/credit invalide.')
					return _render_transactions_page()

				if debit_amount > 0 and credit_amount > 0:
					messages.error(request, f'Ligne {idx + 1}: choisissez debit OU credit, pas les deux.')
					return _render_transactions_page()

				if debit_amount <= 0 and credit_amount <= 0:
					messages.error(request, f'Ligne {idx + 1}: entrez un montant debit ou credit.')
					return _render_transactions_page()

				if debit_amount > 0:
					total_debit += debit_amount
					montant = debit_amount
				else:
					total_credit += credit_amount
					montant = -credit_amount

				lines.append({
					'compte': compte,
					'montant': montant,
				})

			if not lines:
				messages.error(request, 'Ajoutez au moins une ligne comptable.')
				return _render_transactions_page()

			company_required_account_ids = {
				account_id
				for account_id in (
					settings_instance.cap_id if settings_instance else None,
					settings_instance.car_id if settings_instance else None,
				)
				if account_id is not None
			}
			if compagnie is None and any(
				line['compte'].pk in company_required_account_ids
				for line in lines
			):
				messages.error(
					request,
					'Une compagnie est obligatoire lorsqu\'une ligne utilise le compte CAP ou CAR.'
				)
				return _render_transactions_page()

			if total_debit.quantize(Decimal('0.01')) != total_credit.quantize(Decimal('0.01')):
				messages.error(request, 'La transaction doit etre equilibree (debit = credit).')
				return _render_transactions_page()

			try:
				verifier_exercice_modifiable(date_value)
				with transaction.atomic():
					source, _ = Source.objects.get_or_create(nom=source_name[:15])

					tr_desc_kwargs = {
						'no_ej': _next_no_ej(date_value),
						'date': date_value,
						'desc_releve': description,
						'desc_ctb': description[:40],
						'source': source,
					}
					if compagnie is not None:
						if compagnie_type == 'fournisseur':
							tr_desc_kwargs['fournisseur'] = compagnie
						else:
							tr_desc_kwargs['client'] = compagnie
					tr_desc = Tr_desc.objects.create(**tr_desc_kwargs)

					for line in lines:
						Tr_detail.objects.create(
							tr_desc=tr_desc,
							compte=line['compte'],
							montant=line['montant'],
						)
			except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
				logger.exception('Echec sauvegarde transaction: base active non initialisee.')
				messages.error(
					request,
					'Impossible de sauvegarder: la base client active n\'est pas initialisee. '
					'Executez les migrations tenant puis reessayez.'
				)
				return _render_transactions_page()
			except ValueError as exc:
				messages.error(request, str(exc))
				return _render_transactions_page()
			except Exception:
				logger.exception('Echec sauvegarde transaction: erreur non prevue.')
				messages.error(
					request,
					'Impossible de sauvegarder la transaction pour le moment. '
					'Consultez les logs serveur pour le detail technique.'
				)
				return _render_transactions_page()

			messages.success(request, f"Transaction sauvegardee ({tr_desc.no_ej}).")
			return redirect('journal_general')

		return _render_transactions_page()
	except Exception:
		logger.exception('Erreur inattendue dans transactions_page.')
		messages.error(
			request,
			'Une erreur inattendue est survenue dans Transactions. '
			'Reessayez apres avoir redemarre le serveur.'
		)
		return render(request, 'compte/transactions.html', {
			'title': 'Transactions',
			'compagnies': [],
			'comptes': [],
		})
