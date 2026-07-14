import calendar as py_calendar
import re
from datetime import date as date_type
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.db.models.functions import Coalesce
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from holidays import country_holidays

from facture.models import Source, Tr_desc, Tr_detail
from facture.utils import get_setting

from .forms import EmployeForm, PaieForm, ParametresTauxPaieForm
from .models import Employe, FrequencePaie, Paie, ParametresTauxPaie, PeriodePaie


def _ensure_default_frequences_paie():
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


def _superuser_required(request):
	if not request.user.is_superuser:
		raise PermissionDenied('Acces reserve au superuser.')


@login_required
def paie_dashboard(request):
	_ensure_default_frequences_paie()
	paies_agg = Paie.objects.aggregate(
		paies_total=Count('id'),
		total_net=Sum('salaire_net'),
	)
	resume = {
		'employes_actifs': Employe.objects.filter(actif=True).count(),
		'periodes_ouvertes': PeriodePaie.objects.filter(fermee=False).count(),
		'paies_total': paies_agg.get('paies_total'),
		'total_net': paies_agg.get('total_net'),
	}
	paies_recentes = (
		Paie.objects
		.select_related('employe', 'periode', 'periode__frequence_paie')
		.only(
			'id',
			'employe__id',
			'employe__nom',
			'employe__prenom',
			'periode__id',
			'periode__date_fin',
			'periode__frequence_paie__code',
			'salaire_brut_periode',
			'total_retenues',
			'salaire_net',
		)
		.order_by('-periode__date_fin', '-id')[:10]
	)

	return render(request, 'paie/dashboard.html', {
		'title': 'Paie',
		'resume': resume,
		'paies_recentes': paies_recentes,
	})


@login_required
def employes_page(request):
	_ensure_default_frequences_paie()
	if request.method == 'POST':
		form = EmployeForm(request.POST)
		if form.is_valid():
			employe = form.save()
			messages.success(request, f'Employe enregistre: {employe}.')
			return redirect('paie:paie_employes')
	else:
		form = EmployeForm()

	employes = Employe.objects.select_related('frequence_paie').annotate(nb_paies=Count('paies')).order_by('nom', 'prenom', 'id')
	return render(request, 'paie/employes.html', {
		'title': 'Employes',
		'form': form,
		'employes': employes,
	})


@login_required
def employe_edit_page(request, employe_id):
	_ensure_default_frequences_paie()
	employe = get_object_or_404(Employe.objects.select_related('frequence_paie'), pk=employe_id)

	if request.method == 'POST':
		form = EmployeForm(request.POST, instance=employe)
		if form.is_valid():
			employe = form.save()
			messages.success(request, f'Employe mis a jour: {employe}.')
			return redirect('paie:paie_employes')
	else:
		form = EmployeForm(instance=employe)

	return render(request, 'paie/employe.html', {
		'title': 'Modifier un employe',
		'form': form,
		'employe': employe,
	})


@login_required
def employe_desactiver_page(request, employe_id):
	if request.method != 'POST':
		return redirect('paie:paie_employes')

	employe = get_object_or_404(Employe, pk=employe_id)
	if employe.actif:
		employe.actif = False
		employe.save(update_fields=['actif'])
		messages.success(request, f'Employe desactive: {employe}.')
	else:
		messages.info(request, f'Employe deja inactif: {employe}.')

	return redirect('paie:paie_employes')


@login_required
def saisir_paie_page(request):
	_ensure_default_frequences_paie()
	if request.method == 'POST':
		form = PaieForm(request.POST)
		if form.is_valid():
			paie = form.save()
			messages.success(request, f'Paie enregistree pour {paie.employe}.')
			return redirect('paie:paie_journal')
	else:
		form = PaieForm()

	return render(request, 'paie/saisir_paie.html', {
		'title': 'Saisir une paie',
		'form': form,
	})


@login_required
def prochaine_periode_employe_api(request):
	employe_id = request.GET.get('employe_id')
	if not employe_id:
		return JsonResponse({'ok': False, 'error': 'Employe requis.'}, status=400)

	try:
		employe = Employe.objects.select_related('frequence_paie').get(pk=employe_id, actif=True)
	except Employe.DoesNotExist:
		return JsonResponse({'ok': False, 'error': 'Employe introuvable ou inactif.'}, status=404)

	options_payload, default_value, error_message = PaieForm.options_fin_periode_annee_courante(employe)
	if error_message:
		return JsonResponse({'ok': False, 'error': error_message}, status=404)

	selected = next((o for o in options_payload if o['value'] == default_value), None)
	vacances_cumulees = (
		Paie.objects
		.filter(employe=employe)
		.aggregate(total=Coalesce(Sum('vacances'), Decimal('0.00')))
		.get('total')
	)

	return JsonResponse({
		'ok': True,
		'date_fin': selected['value'] if selected else '',
		'date_paie': selected['date_paie'] if selected else '',
		'options': options_payload,
		'default_value': default_value,
		'vacances_cumulees': str(vacances_cumulees),
		'taux_vacances': str(employe.taux_vacances or Decimal('0.00000')),
		'taux_horaire': str(employe.taux_horaire_defaut),
	})


def _next_no_ej_paie():
	last_tr_desc = Tr_desc.objects.order_by('-id').first()
	if not last_tr_desc:
		return 'EJ1'

	match = re.match(r'^EJ(\d+)$', last_tr_desc.no_ej or '')
	if not match:
		return 'EJ1'

	return f"EJ{int(match.group(1)) + 1}"


def _money(value):
	return Decimal(value).quantize(Decimal('0.01'))


def _compute_employer_totals_for_period(paies, settings_instance):
	if not paies:
		return {
			'rrq_employeur': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
		}

	taux_rows = list(
		ParametresTauxPaie.objects.using('default')
		.only(
			'id',
			'rrq_date_debut_effet',
			'rrq_date_fin_effet',
			'taux_rrq_employe',
			'taux_rrq_employeur',
			'rqap_date_debut_effet',
			'rqap_date_fin_effet',
			'taux_rqap_employe',
			'taux_rqap_employeur',
			'ae_date_debut_effet',
			'ae_date_fin_effet',
			'taux_ae_employe',
			'taux_ae_employeur',
			'taux_cnt_employeur',
		)
	)

	taux_cnesst_setting = getattr(settings_instance, 'taux_cnesst_employeur', None) or Decimal('0.00')
	taux_fss_setting = getattr(settings_instance, 'taux_fss_employeur', None) or Decimal('0.00')
	block_cache = {}

	def _d(value):
		return value if value is not None else Decimal('0.00')

	def _row_for_block(date_value, start_field, end_field):
		cache_key = (date_value, start_field, end_field)
		cached_row = block_cache.get(cache_key)
		if cached_row is not None or cache_key in block_cache:
			return cached_row
		candidates = [
			row for row in taux_rows
			if getattr(row, start_field) <= date_value and (getattr(row, end_field) is None or getattr(row, end_field) >= date_value)
		]
		if not candidates:
			block_cache[cache_key] = None
			return None
		row = sorted(candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]
		block_cache[cache_key] = row
		return row

	def _employer_from_employee(employee_amount, employe_rate, employeur_rate, fallback_to_employee=True):
		employee_amount = _d(employee_amount)
		if employe_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		if employeur_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		ratio = Decimal(str(employeur_rate)) / Decimal(str(employe_rate))
		return _money(employee_amount * ratio)

	totals = {
		'rrq_employeur': Decimal('0.00'),
		'rqap_employeur': Decimal('0.00'),
		'ae_employeur': Decimal('0.00'),
		'cnt_employeur': Decimal('0.00'),
		'fss_employeur': Decimal('0.00'),
		'cnesst_employeur': Decimal('0.00'),
	}

	for paie in paies:
		date_paie = paie.periode.date_paie or paie.periode.date_fin
		rrq_row = _row_for_block(date_paie, 'rrq_date_debut_effet', 'rrq_date_fin_effet')
		rqap_row = _row_for_block(date_paie, 'rqap_date_debut_effet', 'rqap_date_fin_effet')
		ae_row = _row_for_block(date_paie, 'ae_date_debut_effet', 'ae_date_fin_effet')

		rrq_employeur = _employer_from_employee(
			paie.rrq,
			getattr(rrq_row, 'taux_rrq_employe', None),
			getattr(rrq_row, 'taux_rrq_employeur', None),
		)
		rqap_employeur = _employer_from_employee(
			paie.rqap,
			getattr(rqap_row, 'taux_rqap_employe', None),
			getattr(rqap_row, 'taux_rqap_employeur', None),
		)
		ae_employeur = _employer_from_employee(
			paie.ae,
			getattr(ae_row, 'taux_ae_employe', None),
			getattr(ae_row, 'taux_ae_employeur', None),
		)
		taux_cnt_percent = getattr(rrq_row, 'taux_cnt_employeur', Decimal('0.06000')) if rrq_row else Decimal('0.06000')
		cnt_employeur = _money(_d(paie.salaire_brut_periode) * (Decimal(str(taux_cnt_percent)) / Decimal('100')))
		fss_employeur = _money(_d(paie.salaire_brut_periode) * taux_fss_setting)
		cnesst_employeur = _money(_d(paie.salaire_brut_periode) * taux_cnesst_setting)

		totals['rrq_employeur'] += rrq_employeur
		totals['rqap_employeur'] += rqap_employeur
		totals['ae_employeur'] += ae_employeur
		totals['cnt_employeur'] += cnt_employeur
		totals['fss_employeur'] += fss_employeur
		totals['cnesst_employeur'] += cnesst_employeur

	return totals


@login_required
def creer_ecriture_salaire(request, periode_id):
	if request.method != 'POST':
		return redirect('paie:paie_journal')

	periode = get_object_or_404(PeriodePaie, pk=periode_id)
	paies = list(
		Paie.objects
		.filter(periode=periode)
		.select_related('periode')
		.only(
			'id',
			'periode__id',
			'periode__date_fin',
			'periode__date_paie',
			'salaire_brut_periode',
			'salaire_net',
			'vacances_payees',
			'vacances',
			'rrq',
			'rqap',
			'ae',
			'impot_federal',
			'impot_provincial',
		)
	)

	if not paies:
		messages.error(request, 'Aucune paie trouvee pour cette periode.')
		return redirect('paie:paie_journal')

	settings_instance = get_setting(
		'compte_salaire',
		'compte_vacances',
		'compte_benefices_marginaux',
		'compte_salaires_a_payer',
		'compte_vacances_a_payer',
		'compte_das_federales',
		'compte_das_provinciales',
		'taux_cnesst_employeur',
		'taux_fss_employeur',
	)

	required_accounts = [
		('Salaires (débit)', getattr(settings_instance, 'compte_salaire', None)),
		('Vacances (débit)', getattr(settings_instance, 'compte_vacances', None)),
		('Bénéfices marginaux (débit)', getattr(settings_instance, 'compte_benefices_marginaux', None)),
		('Salaires à payer (crédit)', getattr(settings_instance, 'compte_salaires_a_payer', None)),
		('Vacances à payer', getattr(settings_instance, 'compte_vacances_a_payer', None)),
		('DAS féd à payer (crédit)', getattr(settings_instance, 'compte_das_federales', None)),
		('DAS prov à payer (crédit)', getattr(settings_instance, 'compte_das_provinciales', None)),
	]

	missing_labels = [label for label, account in required_accounts if account is None]
	if missing_labels:
		messages.error(request, 'Comptes de paie manquants dans les paramètres: ' + ', '.join(missing_labels))
		return redirect('paie:paie_journal')

	source_salaire, _ = Source.objects.get_or_create(nom='Salaire')
	entry_date = periode.date_paie or periode.date_fin
	desc_ctb = f"Paie P{periode.id} {periode.date_fin:%Y-%m-%d}"[:40]

	existing = Tr_desc.objects.filter(
		source=source_salaire,
		desc_ctb=desc_ctb,
		date=entry_date,
	).first()
	if existing:
		messages.info(request, f"L'écriture salaire existe deja ({existing.no_ej}).")
		return redirect('journal_general')

	total_brut = _money(sum((p.salaire_brut_periode or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_vacances_payees = _money(sum((p.vacances_payees or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_vacances = _money(sum((p.vacances or Decimal('0.00') for p in paies), Decimal('0.00')))

	# Vacances payees reduisent le debit salaires dans l'ecriture.
	# Vacances (accrues) sont au debit, puis Vacances a payer porte le net restant.
	debit_salaire = _money(total_brut - total_vacances_payees)
	debit_vacances = total_vacances
	# Montant signe: positif = debit, negatif = credit.
	montant_vacances_a_payer = _money(total_vacances_payees - total_vacances)
	credit_salaires_a_payer = _money(sum((p.salaire_net or Decimal('0.00') for p in paies), Decimal('0.00')))

	total_rrq = _money(sum((p.rrq or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_rqap = _money(sum((p.rqap or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_ae = _money(sum((p.ae or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_impot_fed = _money(sum((p.impot_federal or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_impot_prov = _money(sum((p.impot_provincial or Decimal('0.00') for p in paies), Decimal('0.00')))

	employer_totals = _compute_employer_totals_for_period(paies, settings_instance)
	# RAMQ employeur est mappe ici au bloc RQAP employeur utilise dans la paie.
	debit_benefices = _money(
		employer_totals['rrq_employeur']
		+ employer_totals['rqap_employeur']
		+ employer_totals['ae_employeur']
		+ employer_totals['cnt_employeur']
		+ employer_totals['fss_employeur']
		+ employer_totals['cnesst_employeur']
	)

	total_qc = _money(
		total_rrq
		+ employer_totals['rrq_employeur']
		+ total_rqap
		+ employer_totals['rqap_employeur']
		+ total_impot_prov
		+ employer_totals['cnt_employeur']
		+ employer_totals['fss_employeur']
		+ employer_totals['cnesst_employeur']
	)
	total_ca = _money(total_ae + employer_totals['ae_employeur'] + total_impot_fed)

	credit_das_prov = total_qc
	credit_das_fed = total_ca

	debit_total = _money(debit_salaire + debit_vacances + max(montant_vacances_a_payer, Decimal('0.00')) + debit_benefices)
	credit_total = _money(credit_salaires_a_payer + max(-montant_vacances_a_payer, Decimal('0.00')) + credit_das_fed + credit_das_prov)

	detail_rows = [
		(getattr(settings_instance, 'compte_salaire'), debit_salaire),
		(getattr(settings_instance, 'compte_vacances'), debit_vacances),
		(getattr(settings_instance, 'compte_vacances_a_payer'), montant_vacances_a_payer),
		(getattr(settings_instance, 'compte_benefices_marginaux'), debit_benefices),
		(getattr(settings_instance, 'compte_salaires_a_payer'), -credit_salaires_a_payer),
		(getattr(settings_instance, 'compte_das_federales'), -credit_das_fed),
		(getattr(settings_instance, 'compte_das_provinciales'), -credit_das_prov),
	]

	with transaction.atomic():
		tr_desc = Tr_desc.objects.create(
			no_ej=_next_no_ej_paie(),
			date=entry_date,
			desc_ctb=desc_ctb,
			source=source_salaire,
		)

		for compte, montant in detail_rows:
			if _money(montant) == Decimal('0.00'):
				continue
			Tr_detail.objects.create(
				tr_desc=tr_desc,
				compte=compte,
				montant=_money(montant),
			)

	messages.success(request, f"Ecriture salaire creee ({tr_desc.no_ej}).")
	return redirect('journal_general')


@login_required
def journal_paies_page(request):
	paies = list(
		Paie.objects
		.select_related('employe', 'periode', 'periode__frequence_paie')
		.only(
			'id',
			'employe__id',
			'employe__nom',
			'employe__prenom',
			'periode__id',
			'periode__date_debut',
			'periode__date_fin',
			'periode__date_paie',
			'periode__frequence_paie__code',
			'heures_travaillees',
			'vacances_payees',
			'salaire_brut_periode',
			'salaire_net',
			'vacances',
			'rrq',
			'rqap',
			'ae',
			'impot_federal',
			'impot_provincial',
		)
		.order_by('periode__date_paie', 'periode__date_fin', 'id')
	)
	taux_rows = list(
		ParametresTauxPaie.objects.using('default')
		.only(
			'id',
			'rrq_date_debut_effet',
			'rrq_date_fin_effet',
			'taux_rrq_employe',
			'taux_rrq_employeur',
			'rqap_date_debut_effet',
			'rqap_date_fin_effet',
			'taux_rqap_employe',
			'taux_rqap_employeur',
			'ae_date_debut_effet',
			'ae_date_fin_effet',
			'taux_ae_employe',
			'taux_ae_employeur',
			'taux_cnt_employeur',
		)
	)
	settings_instance = get_setting(
		'frequence_paie',
		'date_debut_periode_paie_annee',
		'date_premier_paiement_paie_annee',
		'taux_cnesst_employeur',
		'taux_fss_employeur',
	)

	def _d(value):
		return value if value is not None else Decimal('0.00')

	def _money(value):
		return Decimal(value).quantize(Decimal('0.01'))

	taux_cnesst_setting = _d(getattr(settings_instance, 'taux_cnesst_employeur', None))
	taux_fss_setting = _d(getattr(settings_instance, 'taux_fss_employeur', None))

	def _row_for_block(date_value, start_field, end_field):
		cache_key = (date_value, start_field, end_field)
		cached_row = block_cache.get(cache_key)
		if cached_row is not None or cache_key in block_cache:
			return cached_row
		candidates = [
			row for row in taux_rows
			if getattr(row, start_field) <= date_value and (getattr(row, end_field) is None or getattr(row, end_field) >= date_value)
		]
		if not candidates:
			block_cache[cache_key] = None
			return None
		row = sorted(candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]
		block_cache[cache_key] = row
		return row

	def _employer_from_employee(employee_amount, employe_rate, employeur_rate, fallback_to_employee=True):
		employee_amount = _d(employee_amount)
		if employe_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		if employeur_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		ratio = Decimal(str(employeur_rate)) / Decimal(str(employe_rate))
		return _money(employee_amount * ratio)

	def _employer_charges_for_paie(paie, date_paie):
		rrq_row = _row_for_block(date_paie, 'rrq_date_debut_effet', 'rrq_date_fin_effet')
		rqap_row = _row_for_block(date_paie, 'rqap_date_debut_effet', 'rqap_date_fin_effet')
		ae_row = _row_for_block(date_paie, 'ae_date_debut_effet', 'ae_date_fin_effet')

		rrq_employeur = _employer_from_employee(
			paie.rrq,
			getattr(rrq_row, 'taux_rrq_employe', None),
			getattr(rrq_row, 'taux_rrq_employeur', None),
		)
		rqap_employeur = _employer_from_employee(
			paie.rqap,
			getattr(rqap_row, 'taux_rqap_employe', None),
			getattr(rqap_row, 'taux_rqap_employeur', None),
		)
		ae_employeur = _employer_from_employee(
			paie.ae,
			getattr(ae_row, 'taux_ae_employe', None),
			getattr(ae_row, 'taux_ae_employeur', None),
		)
		taux_cnt_percent = getattr(rrq_row, 'taux_cnt_employeur', Decimal('0.06000')) if rrq_row else Decimal('0.06000')
		cnt_employeur = _money(_d(paie.salaire_brut_periode) * (Decimal(str(taux_cnt_percent)) / Decimal('100')))
		fss_employeur = _money(_d(paie.salaire_brut_periode) * taux_fss_setting)
		cnesst_employeur = _money(_d(paie.salaire_brut_periode) * taux_cnesst_setting)

		charge_employeur = _money(rrq_employeur + rqap_employeur + ae_employeur + cnt_employeur + fss_employeur + cnesst_employeur)
		return {
			'rrq_employeur': rrq_employeur,
			'rqap_employeur': rqap_employeur,
			'ae_employeur': ae_employeur,
			'cnt_employeur': cnt_employeur,
			'fss_employeur': fss_employeur,
			'cnesst_employeur': cnesst_employeur,
			'charge_employeur': charge_employeur,
		}

	paie_entries = []
	block_cache = {}
	for paie in paies:
		date_paie = paie.periode.date_paie or paie.periode.date_fin
		paie_entries.append({
			'paie': paie,
			'date_paie': date_paie,
			'employer': _employer_charges_for_paie(paie, date_paie),
		})

	mois_fr = {
		1: 'Janvier',
		2: 'Fevrier',
		3: 'Mars',
		4: 'Avril',
		5: 'Mai',
		6: 'Juin',
		7: 'Juillet',
		8: 'Aout',
		9: 'Septembre',
		10: 'Octobre',
		11: 'Novembre',
		12: 'Decembre',
	}

	month_options = []
	current_month = date_type.today().replace(day=1)
	for offset in range(-12, 13):
		option_date = PaieForm._add_months(current_month, offset)
		month_options.append({
			'value': option_date.strftime('%Y-%m'),
			'label': f"{mois_fr.get(option_date.month, option_date.month)} {option_date.year}",
		})

	def _build_journal_rows(paie_entries_list):
		journal_rows = []
		current_month_key = None
		month_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'rrq': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'cnt_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'charge_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'net': Decimal('0.00'),
		}
		total_brut_local = Decimal('0.00')
		total_net_local = Decimal('0.00')
		total_charge_employeur_local = Decimal('0.00')

		for entry in paie_entries_list:
			paie = entry['paie']
			date_paie = entry['date_paie']
			employer = entry['employer']
			month_key = (date_paie.year, date_paie.month)

			if current_month_key is None:
				current_month_key = month_key
			elif month_key != current_month_key:
				journal_rows.append({
					'type': 'subtotal',
					'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
					'totals': month_totals.copy(),
				})
				journal_rows.append({'type': 'separator'})
				current_month_key = month_key
				month_totals = {
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'rrq': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'cnt_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'net': Decimal('0.00'),
				}

			journal_rows.append({
				'type': 'paie',
				'paie': paie,
				'date_paie': date_paie,
				'employer': employer,
			})

			month_totals['heures'] += _d(paie.heures_travaillees)
			month_totals['vacances_payees'] += _d(paie.vacances_payees)
			month_totals['brut'] += _d(paie.salaire_brut_periode)
			month_totals['vacances'] += _d(paie.vacances)
			month_totals['rrq'] += _d(paie.rrq)
			month_totals['rrq_employeur'] += employer['rrq_employeur']
			month_totals['rqap'] += _d(paie.rqap)
			month_totals['rqap_employeur'] += employer['rqap_employeur']
			month_totals['ae'] += _d(paie.ae)
			month_totals['ae_employeur'] += employer['ae_employeur']
			month_totals['cnt_employeur'] += employer['cnt_employeur']
			month_totals['fss_employeur'] += employer['fss_employeur']
			month_totals['cnesst_employeur'] += employer['cnesst_employeur']
			month_totals['charge_employeur'] += employer['charge_employeur']
			month_totals['impot_federal'] += _d(paie.impot_federal)
			month_totals['impot_provincial'] += _d(paie.impot_provincial)
			month_totals['net'] += _d(paie.salaire_net)

			total_brut_local += _d(paie.salaire_brut_periode)
			total_net_local += _d(paie.salaire_net)
			total_charge_employeur_local += employer['charge_employeur']

		if current_month_key is not None:
			journal_rows.append({
				'type': 'subtotal',
				'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
				'totals': month_totals.copy(),
			})

		return journal_rows, total_brut_local, total_net_local, total_charge_employeur_local

	def _build_total_period_rows(paie_entries_list):
		period_groups = {}
		for entry in paie_entries_list:
			paie = entry['paie']
			date_paie = entry['date_paie']
			employer = entry['employer']
			key = paie.periode_id
			if key not in period_groups:
				period_groups[key] = {
					'periode': paie.periode,
					'date_paie': date_paie,
					'date_fin': paie.periode.date_fin,
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'net': Decimal('0.00'),
					'rrq_employe': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'cnt_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'total_qc': Decimal('0.00'),
					'total_ca': Decimal('0.00'),
				}
			bucket = period_groups[key]
			bucket['heures'] += _d(paie.heures_travaillees)
			bucket['vacances_payees'] += _d(paie.vacances_payees)
			bucket['brut'] += _d(paie.salaire_brut_periode)
			bucket['vacances'] += _d(paie.vacances)
			bucket['net'] += _d(paie.salaire_net)
			bucket['rrq_employe'] += _d(paie.rrq)
			bucket['rrq_employeur'] += employer['rrq_employeur']
			bucket['rqap'] += _d(paie.rqap)
			bucket['rqap_employeur'] += employer['rqap_employeur']
			bucket['impot_provincial'] += _d(paie.impot_provincial)
			bucket['ae'] += _d(paie.ae)
			bucket['ae_employeur'] += employer['ae_employeur']
			bucket['cnt_employeur'] += employer['cnt_employeur']
			bucket['fss_employeur'] += employer['fss_employeur']
			bucket['cnesst_employeur'] += employer['cnesst_employeur']
			bucket['charge_employeur'] += employer['charge_employeur']
			bucket['impot_federal'] += _d(paie.impot_federal)
			bucket['total_qc'] += (
				_d(paie.rrq)
				+ employer['rrq_employeur']
				+ _d(paie.rqap)
				+ employer['rqap_employeur']
				+ _d(paie.impot_provincial)
				+ employer['cnt_employeur']
				+ employer['fss_employeur']
				+ employer['cnesst_employeur']
			)
			bucket['total_ca'] += (
				_d(paie.ae)
				+ employer['ae_employeur']
				+ _d(paie.impot_federal)
			)

		ordered_groups = sorted(
			period_groups.values(),
			key=lambda g: (g['date_paie'], g['date_fin']),
		)
		period_number_counters = {}

		grand_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'net': Decimal('0.00'),
			'rrq_employe': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'cnt_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'total_qc': Decimal('0.00'),
			'total_ca': Decimal('0.00'),
		}

		total_rows = []
		current_month_key = None
		month_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'net': Decimal('0.00'),
			'rrq_employe': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'cnt_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'charge_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'total_qc': Decimal('0.00'),
			'total_ca': Decimal('0.00'),
		}

		for idx, group in enumerate(ordered_groups, start=1):
			date_paie = group['date_paie']
			month_key = (date_paie.year, date_paie.month)
			periode = group['periode']
			period_cycle_key = (periode.frequence_paie_id, date_paie.year)
			period_no = period_number_counters.get(period_cycle_key, 0) + 1
			period_number_counters[period_cycle_key] = period_no

			if current_month_key is None:
				current_month_key = month_key
			elif month_key != current_month_key:
				total_rows.append({
					'type': 'subtotal',
					'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
					'totals': month_totals.copy(),
				})
				total_rows.append({'type': 'separator'})
				current_month_key = month_key
				month_totals = {
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'net': Decimal('0.00'),
					'rrq_employe': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'cnt_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'total_qc': Decimal('0.00'),
					'total_ca': Decimal('0.00'),
				}

			total_rows.append({
				'type': 'period',
				'period_index': period_no,
				'date_paie': group['date_paie'],
				'date_fin': group['date_fin'],
				'totals': group,
			})

			month_totals['heures'] += group['heures']
			month_totals['vacances_payees'] += group['vacances_payees']
			month_totals['brut'] += group['brut']
			month_totals['vacances'] += group['vacances']
			month_totals['net'] += group['net']
			month_totals['rrq_employe'] += group['rrq_employe']
			month_totals['rrq_employeur'] += group['rrq_employeur']
			month_totals['rqap'] += group['rqap']
			month_totals['rqap_employeur'] += group['rqap_employeur']
			month_totals['impot_provincial'] += group['impot_provincial']
			month_totals['ae'] += group['ae']
			month_totals['ae_employeur'] += group['ae_employeur']
			month_totals['cnt_employeur'] += group['cnt_employeur']
			month_totals['fss_employeur'] += group['fss_employeur']
			month_totals['cnesst_employeur'] += group['cnesst_employeur']
			month_totals['charge_employeur'] += group['charge_employeur']
			month_totals['impot_federal'] += group['impot_federal']
			month_totals['total_qc'] += group['total_qc']
			month_totals['total_ca'] += group['total_ca']

			grand_totals['heures'] += group['heures']
			grand_totals['vacances_payees'] += group['vacances_payees']
			grand_totals['brut'] += group['brut']
			grand_totals['vacances'] += group['vacances']
			grand_totals['net'] += group['net']
			grand_totals['rrq_employe'] += group['rrq_employe']
			grand_totals['rrq_employeur'] += group['rrq_employeur']
			grand_totals['rqap'] += group['rqap']
			grand_totals['rqap_employeur'] += group['rqap_employeur']
			grand_totals['impot_provincial'] += group['impot_provincial']
			grand_totals['ae'] += group['ae']
			grand_totals['ae_employeur'] += group['ae_employeur']
			grand_totals['cnt_employeur'] += group['cnt_employeur']
			grand_totals['fss_employeur'] += group['fss_employeur']
			grand_totals['cnesst_employeur'] += group['cnesst_employeur']
			grand_totals['impot_federal'] += group['impot_federal']
			grand_totals['total_qc'] += group['total_qc']
			grand_totals['total_ca'] += group['total_ca']

		if current_month_key is not None:
			total_rows.append({
				'type': 'subtotal',
				'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
				'totals': month_totals.copy(),
			})

		return total_rows, grand_totals

	journal_rows, total_brut, total_net, total_charge_employeur = _build_journal_rows(paie_entries)
	total_rows, total_rows_grand_totals = _build_total_period_rows(paie_entries)

	by_employe = {}
	for entry in paie_entries:
		paie = entry['paie']
		employe_entry = by_employe.setdefault(
			paie.employe_id,
			{'employe': paie.employe, 'paies': []},
		)
		employe_entry['paies'].append(entry)

	employe_tabs = []
	for _, payload in sorted(by_employe.items(), key=lambda item: (item[1]['employe'].nom, item[1]['employe'].prenom, item[1]['employe'].id)):
		rows, brut, net, charge_employeur = _build_journal_rows(payload['paies'])
		employe_tabs.append({
			'employe': payload['employe'],
			'tab_id': f"emp-{payload['employe'].id}",
			'journal_rows': rows,
			'total_brut': brut,
			'total_net': net,
			'total_charge_employeur': charge_employeur,
		})

	return render(request, 'paie/journal_paies.html', {
		'title': 'Journal des paies',
		'journal_rows': journal_rows,
		'total_rows': total_rows,
		'total_rows_grand_totals': total_rows_grand_totals,
		'total_brut': total_brut,
		'total_net': total_net,
		'total_charge_employeur': total_charge_employeur,
		'employe_tabs': employe_tabs,
	})


@login_required
def remises_mensuelles_page(request):
	_ensure_default_frequences_paie()
	mois_fr = {
		1: 'Janvier',
		2: 'Fevrier',
		3: 'Mars',
		4: 'Avril',
		5: 'Mai',
		6: 'Juin',
		7: 'Juillet',
		8: 'Aout',
		9: 'Septembre',
		10: 'Octobre',
		11: 'Novembre',
		12: 'Decembre',
	}

	selected_month_raw = request.GET.get('mois')
	try:
		if selected_month_raw and len(selected_month_raw) == 7:
			selected_year = int(selected_month_raw[:4])
			selected_month = int(selected_month_raw[5:7])
			selected_date = date_type(selected_year, selected_month, 1)
		else:
			selected_date = date_type.today().replace(day=1)
	except (TypeError, ValueError):
		selected_date = date_type.today().replace(day=1)

	if selected_date.month == 12:
		next_month_date = date_type(selected_date.year + 1, 1, 1)
	else:
		next_month_date = date_type(selected_date.year, selected_date.month + 1, 1)

	paies = list(
		Paie.objects
		.select_related('periode', 'periode__frequence_paie')
		.only(
			'id',
			'periode__id',
			'periode__date_debut',
			'periode__date_fin',
			'periode__date_paie',
			'periode__frequence_paie__code',
			'heures_travaillees',
			'salaire_brut_periode',
			'salaire_net',
			'rrq',
			'rqap',
			'ae',
			'impot_federal',
			'impot_provincial',
		)
		.filter(periode__date_paie__gte=selected_date, periode__date_paie__lt=next_month_date)
		.order_by('periode__date_paie', 'periode__date_fin', 'id')
	)
	settings_instance = get_setting('taux_cnesst_employeur', 'taux_fss_employeur')
	taux_rows = list(
		ParametresTauxPaie.objects.using('default')
		.only(
			'id',
			'rrq_date_debut_effet',
			'rrq_date_fin_effet',
			'taux_rrq_employe',
			'taux_rrq_employeur',
			'rqap_date_debut_effet',
			'rqap_date_fin_effet',
			'taux_rqap_employe',
			'taux_rqap_employeur',
			'ae_date_debut_effet',
			'ae_date_fin_effet',
			'taux_ae_employe',
			'taux_ae_employeur',
		)
	)

	def _d(value):
		return value if value is not None else Decimal('0.00')

	def _money(value):
		return Decimal(value).quantize(Decimal('0.01'))

	taux_cnesst_setting = _d(getattr(settings_instance, 'taux_cnesst_employeur', None))
	taux_fss_setting = _d(getattr(settings_instance, 'taux_fss_employeur', None))

	def _row_for_block(date_value, start_field, end_field):
		candidates = [
			row for row in taux_rows
			if getattr(row, start_field) <= date_value and (getattr(row, end_field) is None or getattr(row, end_field) >= date_value)
		]
		if not candidates:
			return None
		return sorted(candidates, key=lambda row: (getattr(row, start_field), row.id), reverse=True)[0]

	def _employer_from_employee(employee_amount, employe_rate, employeur_rate, fallback_to_employee=True):
		employee_amount = _d(employee_amount)
		if employe_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		if employeur_rate in (None, Decimal('0.00'), 0, '0', '0.0'):
			return employee_amount if fallback_to_employee else Decimal('0.00')
		ratio = Decimal(str(employeur_rate)) / Decimal(str(employe_rate))
		return _money(employee_amount * ratio)

	federal_total = {
		'ae_employe': Decimal('0.00'),
		'ae_employeur': Decimal('0.00'),
		'impot_federal': Decimal('0.00'),
		'total': Decimal('0.00'),
	}
	provincial_total = {
		'rrq_employe': Decimal('0.00'),
		'rrq_employeur': Decimal('0.00'),
		'rqap_employe': Decimal('0.00'),
		'rqap_employeur': Decimal('0.00'),
		'impot_provincial': Decimal('0.00'),
		'total': Decimal('0.00'),
	}

	for paie in paies:
		date_paie = paie.periode.date_paie or paie.periode.date_fin
		rrq_row = _row_for_block(date_paie, 'rrq_date_debut_effet', 'rrq_date_fin_effet')
		rqap_row = _row_for_block(date_paie, 'rqap_date_debut_effet', 'rqap_date_fin_effet')
		ae_row = _row_for_block(date_paie, 'ae_date_debut_effet', 'ae_date_fin_effet')

		rrq_employeur = _employer_from_employee(
			paie.rrq,
			getattr(rrq_row, 'taux_rrq_employe', None),
			getattr(rrq_row, 'taux_rrq_employeur', None),
		)
		rqap_employeur = _employer_from_employee(
			paie.rqap,
			getattr(rqap_row, 'taux_rqap_employe', None),
			getattr(rqap_row, 'taux_rqap_employeur', None),
		)
		ae_employeur = _employer_from_employee(
			paie.ae,
			getattr(ae_row, 'taux_ae_employe', None),
			getattr(ae_row, 'taux_ae_employeur', None),
		)
		cnesst_employeur = _money(_d(paie.salaire_brut_periode) * taux_cnesst_setting)
		fss_employeur = _money(_d(paie.salaire_brut_periode) * taux_fss_setting)

		federal_total['ae_employe'] += _d(paie.ae)
		federal_total['ae_employeur'] += ae_employeur
		federal_total['impot_federal'] += _d(paie.impot_federal)
		federal_total['total'] += _d(paie.ae) + ae_employeur + _d(paie.impot_federal)

		provincial_total['rrq_employe'] += _d(paie.rrq)
		provincial_total['rrq_employeur'] += rrq_employeur
		provincial_total['rqap_employe'] += _d(paie.rqap)
		provincial_total['rqap_employeur'] += rqap_employeur
		provincial_total['impot_provincial'] += _d(paie.impot_provincial)
		provincial_total['total'] += _d(paie.rrq) + rrq_employeur + _d(paie.rqap) + rqap_employeur + _d(paie.impot_provincial) + cnesst_employeur

	month_value = selected_date.strftime('%Y-%m')
	previous_year_value = PaieForm._add_months(selected_date, -12).strftime('%Y-%m')
	next_year_value = PaieForm._add_months(selected_date, 12).strftime('%Y-%m')

	return render(request, 'paie/remises_mensuelles.html', {
		'title': 'Remises mensuelles',
		'selected_date': selected_date,
		'selected_month_value': month_value,
		'previous_year_value': previous_year_value,
		'next_year_value': next_year_value,
		'federal_total': federal_total,
		'provincial_total': provincial_total,
		'paies_count': len(paies),
	})


@login_required
def calendrier_paie_page(request):
	_ensure_default_frequences_paie()
	settings_instance = get_setting(
		'frequence_paie',
		'date_debut_periode_paie_annee',
		'date_premier_paiement_paie_annee',
	)

	mois_fr = {
		1: 'Janvier',
		2: 'Fevrier',
		3: 'Mars',
		4: 'Avril',
		5: 'Mai',
		6: 'Juin',
		7: 'Juillet',
		8: 'Aout',
		9: 'Septembre',
		10: 'Octobre',
		11: 'Novembre',
		12: 'Decembre',
	}

	selected_month_raw = request.GET.get('mois')
	try:
		if selected_month_raw and len(selected_month_raw) == 7:
			selected_year = int(selected_month_raw[:4])
			selected_month = int(selected_month_raw[5:7])
			selected_date = date_type(selected_year, selected_month, 1)
		else:
			selected_date = date_type.today().replace(day=1)
	except (TypeError, ValueError):
		selected_date = date_type.today().replace(day=1)

	previous_month_value = PaieForm._add_months(selected_date, -1).strftime('%Y-%m')
	next_month_value = PaieForm._add_months(selected_date, 1).strftime('%Y-%m')
	previous_year_value = PaieForm._add_months(selected_date, -12).strftime('%Y-%m')
	next_year_value = PaieForm._add_months(selected_date, 12).strftime('%Y-%m')
	selected_month_value = selected_date.strftime('%Y-%m')
	month_label = f"{mois_fr.get(selected_date.month, selected_date.month)} {selected_date.year}"

	error_message = None
	if not settings_instance or not settings_instance.frequence_paie_id or not settings_instance.date_debut_periode_paie_annee or not settings_instance.date_premier_paiement_paie_annee:
		error_message = 'Configurez la frequence de paie, le debut de premiere periode et la date du premier paiement dans Parametres.'

	projected = []
	if settings_instance and settings_instance.frequence_paie_id and settings_instance.date_debut_periode_paie_annee and settings_instance.date_premier_paiement_paie_annee:
		projected = PaieForm._build_projected_periods(
			settings_instance.frequence_paie,
			settings_instance.date_debut_periode_paie_annee,
			settings_instance.date_premier_paiement_paie_annee,
			count=2600,
		)

	payday_map = {}
	period_number_counters = {}
	for date_debut, date_fin, date_paie in projected:
		period_cycle_key = (settings_instance.frequence_paie_id if settings_instance else None, date_paie.year)
		period_no = period_number_counters.get(period_cycle_key, 0) + 1
		period_number_counters[period_cycle_key] = period_no

		if not date_paie or date_paie.year != selected_date.year or date_paie.month != selected_date.month:
			continue
		payday_map.setdefault(date_paie, []).append({
			'index': period_no,
			'date_debut': date_debut,
			'date_fin': date_fin,
			'date_paie': date_paie,
			'label': f'Paie {period_no}',
		})

	holiday_lookup = {}
	try:
		holiday_years = {selected_date.year - 1, selected_date.year, selected_date.year + 1}
		qc_holidays = country_holidays('CA', subdiv='QC', years=sorted(holiday_years))
		holiday_lookup = dict(qc_holidays)
	except Exception:
		holiday_lookup = {}

	calendar_weeks = []
	month_calendar = py_calendar.Calendar(firstweekday=6).monthdatescalendar(selected_date.year, selected_date.month)
	today = date_type.today()
	for week in month_calendar:
		week_days = []
		for day in week:
			holiday_name = holiday_lookup.get(day)
			payday_entries = payday_map.get(day, [])
			week_days.append({
				'date': day,
				'in_current_month': day.month == selected_date.month,
				'is_today': day == today,
				'is_holiday': bool(holiday_name),
				'holiday_name': holiday_name,
				'payday_entries': payday_entries,
				'is_payday': bool(payday_entries),
				'is_both': bool(holiday_name and payday_entries),
			})
		calendar_weeks.append(week_days)

	return render(request, 'paie/calendrier_paie.html', {
		'title': 'Calendrier de paie',
		'selected_date': selected_date,
		'selected_month_value': selected_month_value,
		'previous_month_value': previous_month_value,
		'next_month_value': next_month_value,
		'previous_year_value': previous_year_value,
		'next_year_value': next_year_value,
		'month_label': month_label,
		'calendar_weeks': calendar_weeks,
		'error_message': error_message,
		'paydays_total': sum(len(entries) for entries in payday_map.values()),
	})


@login_required
def parametres_taux_page(request):
	_superuser_required(request)
	edit_id = request.GET.get('edit')
	instance = None
	if edit_id:
		instance = get_object_or_404(ParametresTauxPaie.objects.using('default'), pk=edit_id)

	if request.method == 'POST':
		instance = None
		if request.POST.get('taux_id'):
			instance = get_object_or_404(ParametresTauxPaie.objects.using('default'), pk=request.POST.get('taux_id'))
		form = ParametresTauxPaieForm(request.POST, instance=instance)
		if form.is_valid():
			row = form.save(commit=False)
			row.save(using='default')
			form.save_m2m()
			if instance:
				messages.success(request, f'Configuration mise a jour: {row}.')
			else:
				messages.success(request, f'Configuration creee: {row}.')
			return redirect('paie:paie_parametres_taux')
	else:
		form = ParametresTauxPaieForm(instance=instance)

	rows = ParametresTauxPaie.objects.using('default').order_by('-rrq_date_debut_effet', '-id')
	return render(request, 'paie/parametres_taux.html', {
		'title': 'Parametres des taux de paie',
		'form': form,
		'rows': rows,
		'editing': bool(instance),
		'editing_id': instance.id if instance else None,
	})
