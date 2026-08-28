from decimal import Decimal
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from facture.models import Client, CompagnieSoldeDepart, CompteReleve, Fournisseur, SoldeFin
from facture.utils import ensure_tax_authority_companies, expert_required, get_settings, parse_decimal, read_csv_rows
from tenancy.models import ClientDatabase
from tenancy.services import mark_user_must_change_password, user_must_change_password

from .forms import CompteCsvImportForm, CompteForm, SettingForm
from .models import Compte, SoldeAuxLivres, Total

from compte.views_tenant_setup import (
    assistant_demarrage_list_page,
    assistant_demarrage_page,
    creer_tenant_page,
)
from compte.views_transactions import (
    transactions_page,
    transaction_edit_page,
    transaction_delete,
)



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
				return JsonResponse({'ok': False, 'error': 'Compte de grand livre introuvable.'}, status=404)

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


