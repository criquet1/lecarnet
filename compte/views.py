from decimal import Decimal
from datetime import date, timedelta
import json
import logging
import os
import re
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import connections, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from facture.models import Compagnie, CompagnieSoldeDepart, CompteReleve, Source, Tr_desc, Tr_detail
from facture.utils import ensure_tax_authority_companies, expert_required, get_settings, parse_decimal, read_csv_rows
from tenancy.models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess
from tenancy.services import mark_user_must_change_password, set_active_client_on_session, sync_user_client_accesses, user_must_change_password

from .forms import CompteCsvImportForm, CompteForm, CreerTenantForm, SettingForm
from .models import Compte, SoldeAuxLivres, Total


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
	db_alias = Compte.objects.all().db
	query = """
		SELECT
			c.numero AS compte_id,
			c.numero AS compte_numero,
			c.libelle AS compte_libelle,
			COALESCE(v.solde_depart, 0) AS solde_depart,
			COALESCE(v.debit, 0) AS debit,
			COALESCE(v.credit, 0) AS credit
		FROM compte_compte c
		LEFT JOIN facture_v_balance_verification v ON v.compte_id = c.numero
		ORDER BY c.numero
	"""

	rows = []
	with connections[db_alias].cursor() as cursor:
		cursor.execute(query)
		for compte_id, compte_numero, compte_libelle, solde_depart, debit, credit in cursor.fetchall():
			rows.append({
				'compte': SimpleNamespace(
					pk=compte_id,
					numero=compte_numero,
					libelle=compte_libelle,
				),
				'debit': Decimal(str(debit)) if debit not in (None, '') else Decimal('0'),
				'credit': Decimal(str(credit)) if credit not in (None, '') else Decimal('0'),
				'solde_depart': Decimal(str(solde_depart)) if solde_depart not in (None, '') else Decimal('0'),
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

	compagnies = Compagnie.objects.filter(
		cap_ou_car__in=[Compagnie.MODE_CAP, Compagnie.MODE_CAR],
	).order_by('cap_ou_car', 'nom')

	soldes_map = {
		item.compagnie_id: item.montant
		for item in CompagnieSoldeDepart.objects.select_related('compagnie')
	}

	repartition_rows = []
	cap_reparti = Decimal('0')
	car_reparti = Decimal('0')

	for compagnie in compagnies:
		montant = soldes_map.get(compagnie.id, Decimal('0'))
		repartition_rows.append({
			'compagnie': compagnie,
			'montant': montant,
			'field_name': f'repartition_{compagnie.id}',
		})
		if (compagnie.cap_ou_car or '').upper() == Compagnie.MODE_CAP:
			cap_reparti += montant
		elif (compagnie.cap_ou_car or '').upper() == Compagnie.MODE_CAR:
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
				compagnies = Compagnie.objects.filter(
					cap_ou_car__in=[Compagnie.MODE_CAP, Compagnie.MODE_CAR],
				)
				errors = []
				with transaction.atomic():
					for compagnie in compagnies:
						field_name = f'repartition_{compagnie.id}'
						montant = parse_decimal(request.POST.get(field_name, '0'))
						if montant is None:
							errors.append(f"{compagnie.nom}: montant invalide")
							continue
						CompagnieSoldeDepart.objects.update_or_create(
							compagnie=compagnie,
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
	})


@expert_required
def settings_page(request):
	from paie.models import FrequencePaie
	_ensure_default_frequences_paie()
	settings_instance = get_settings()

	if request.method == 'POST':
		form = SettingForm(request.POST, instance=settings_instance)
		if form.is_valid():
			settings_instance = form.save()
			ensure_tax_authority_companies(settings_instance)
			return redirect('settings')
	else:
		form = SettingForm(instance=settings_instance)

	return render(request, 'compte/settings.html', {
		'title': 'Paramètres',
		'form': form,
		'settings_instance': settings_instance,
		'frequences_paie': FrequencePaie.objects.all(),
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
				tenant_user = None
				db_config = None
				physical_db_created = False
				try:
					db_config = _build_tenant_db_config(db_alias, db_name)
					_register_runtime_tenant_db(db_alias, db_config)
					_create_physical_tenant_db(db_config)
					physical_db_created = True
					call_command('migrate', database=db_alias, interactive=False, verbosity=0)
					_persist_tenant_config(db_alias, db_config)

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
					UserSocieteAccess.objects.update_or_create(
						user=tenant_user,
						societe=societe,
						defaults={'is_default': True},
					)

					has_default = UserClientAccess.objects.filter(user=request.user, is_default=True).exists()
					access, _ = UserClientAccess.objects.update_or_create(
						user=request.user,
						client=client,
						defaults={'is_default': not has_default},
					)
					UserClientAccess.objects.update_or_create(
						user=tenant_user,
						client=client,
						defaults={'is_default': True},
					)
					UserClientAccess.objects.filter(user=tenant_user).exclude(client=client).delete()
					set_active_client_on_session(request, access)
					sync_user_client_accesses(request.user)

					messages.success(request, f"Le tenant '{name}' a ete cree et active. Utilisateur cree: {username}")
					if (os.environ.get('TENANT_DATABASES_JSON') or '').strip():
						messages.warning(request, 'TENANT_DATABASES_JSON est defini: ajoute aussi ce tenant dans cette variable pour le prochain redemarrage.')
					return redirect('settings')
				except Exception as exc:
					if tenant_user is not None:
						try:
							tenant_user.delete()
						except Exception:
							pass
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
	return render(request, 'compte/totaux.html', {
		'title': 'Totaux',
		'rows': rows,
		'total_debit': total_debit,
		'total_credit': total_credit,
		'is_balanced': is_balanced,
	})
@expert_required
def feuille_de_travail_page(request):
	return render(request, 'compte/feuille_de_travail.html', {
		'title': 'Feuille de travail',
	})


def _next_no_ej_transactions():
	last_tr_desc = Tr_desc.objects.order_by('-id').first()
	if not last_tr_desc:
		return 'EJ1'

	match = re.match(r'^EJ(\d+)$', last_tr_desc.no_ej or '')
	if not match:
		return 'EJ1'

	return f"EJ{int(match.group(1)) + 1}"


def _parse_compte_numero(raw_value):
	value = (raw_value or '').strip()
	if not value:
		return None
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
	compagnies = list(Compagnie.objects.order_by('nom'))

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
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
			})

		compagnie = Compagnie.objects.filter(pk=raw_compagnie_id).first()
		if not compagnie:
			messages.error(request, 'Veuillez selectionner une compagnie valide.')
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
			})

		if not description:
			messages.error(request, 'La description est obligatoire.')
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
			})

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
				return render(request, 'compte/transactions.html', {
					'title': 'Transactions',
					'compagnies': compagnies,
				})

			compte = Compte.objects.filter(pk=compte_numero).first()
			if not compte:
				messages.error(request, f'Ligne {idx + 1}: le compte {compte_numero} est introuvable.')
				return render(request, 'compte/transactions.html', {
					'title': 'Transactions',
					'compagnies': compagnies,
				})

			debit_amount = parse_decimal(debit_raw) if (debit_raw or '').strip() else Decimal('0')
			credit_amount = parse_decimal(credit_raw) if (credit_raw or '').strip() else Decimal('0')
			if debit_amount is None or credit_amount is None:
				messages.error(request, f'Ligne {idx + 1}: montant debit/credit invalide.')
				return render(request, 'compte/transactions.html', {
					'title': 'Transactions',
					'compagnies': compagnies,
				})

			if debit_amount > 0 and credit_amount > 0:
				messages.error(request, f'Ligne {idx + 1}: choisissez debit OU credit, pas les deux.')
				return render(request, 'compte/transactions.html', {
					'title': 'Transactions',
					'compagnies': compagnies,
				})

			if debit_amount <= 0 and credit_amount <= 0:
				messages.error(request, f'Ligne {idx + 1}: entrez un montant debit ou credit.')
				return render(request, 'compte/transactions.html', {
					'title': 'Transactions',
					'compagnies': compagnies,
				})

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
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
			})

		if total_debit.quantize(Decimal('0.01')) != total_credit.quantize(Decimal('0.01')):
			messages.error(request, 'La transaction doit etre equilibree (debit = credit).')
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
			})

		source, _ = Source.objects.get_or_create(nom=source_name[:15])

		with transaction.atomic():
			tr_desc = Tr_desc.objects.create(
				no_ej=_next_no_ej_transactions(),
				compagnie=compagnie,
				date=date_value,
				desc_releve=description,
				desc_ctb=description[:40],
				source=source,
			)

			for line in lines:
				Tr_detail.objects.create(
					tr_desc=tr_desc,
					compte=line['compte'],
					montant=line['montant'],
				)

		messages.success(request, f"Transaction sauvegardee ({tr_desc.no_ej}).")
		return redirect('transactions')

	return render(request, 'compte/transactions.html', {
		'title': 'Transactions',
		'compagnies': compagnies,
	})
