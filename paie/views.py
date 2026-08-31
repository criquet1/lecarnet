import calendar as py_calendar
from datetime import date as date_type
from decimal import Decimal
from urllib.parse import urlencode


from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from holidays import country_holidays

from facture.utils import get_setting
from facture.working_period import get_working_period

from .forms import EmployeForm, PaieForm, ParametresTauxPaieForm
from .models import Employe, FrequencePaie, Paie, ParametresTauxPaie
from django.http import HttpResponse
from weasyprint import HTML
from .models import FeuilletFiscalAnnuel
from compte.models import Setting

from paie.views_journal import (
    creer_ecriture_salaire,
    mettre_a_jour_ecriture_salaire,
    journal_paies_page,
    _compute_employer_totals_for_period,
)


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
@xframe_options_sameorigin
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
@xframe_options_sameorigin
def employe_edit_page(request, employe_id):
	_ensure_default_frequences_paie()
	employe = get_object_or_404(Employe.objects.select_related('frequence_paie'), pk=employe_id)

	if request.method == 'POST':
		form = EmployeForm(request.POST, instance=employe)
		if form.is_valid():
			employe = form.save()
			messages.success(request, f'Employe mis a jour: {employe}.')
			return redirect(f"{reverse('paie:paie_employes')}?embed=1")
	else:
		form = EmployeForm(instance=employe)

	return render(request, 'paie/employe.html', {
		'title': 'Modifier un employe',
		'form': form,
		'employe': employe,
	})


@login_required
@xframe_options_sameorigin
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
@xframe_options_sameorigin
def saisir_paie_page(request):
	_ensure_default_frequences_paie()
	if request.method == 'POST':
		form = PaieForm(request.POST)
		if form.is_valid():
			paie = form.save()
			messages.success(request, f'Paie enregistree pour {paie.employe}.')
			journal_params = {'employe': paie.employe_id}
			if request.GET.get('embed') == '1':
				journal_params['embed'] = '1'
			journal_url = f"{reverse('paie:paie_journal')}?{urlencode(journal_params)}"
			return redirect(journal_url)
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
	vacances_cumulees = employe.solde_vacances()

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


@login_required
@xframe_options_sameorigin
def remises_mensuelles_page(request):
	_ensure_default_frequences_paie()
	working_period = get_working_period(request)
	selected_date = date_type(
		working_period['year'],
		working_period['month'],
		1,
	)

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
			'rrq_employeur',
			'rqap',
			'rqap_employeur',
			'ae',
			'ae_employeur',
			'cnesst_employeur',
			'fss_employeur',
			'impot_federal',
			'impot_provincial',
		)
		.filter(periode__date_paie__gte=selected_date, periode__date_paie__lt=next_month_date)
		.order_by('periode__date_paie', 'periode__date_fin', 'id')
	)
	def _d(value):
		return value if value is not None else Decimal('0.00')

	federal_total = {
		'ae_employe': Decimal('0.00'),
		'ae_employeur': Decimal('0.00'),
		'ae_total': Decimal('0.00'),
		'impot_federal': Decimal('0.00'),
		'total': Decimal('0.00'),
	}
	provincial_total = {
		'rrq_employe': Decimal('0.00'),
		'rrq_employeur': Decimal('0.00'),
		'rrq_total': Decimal('0.00'),
		'rqap_employe': Decimal('0.00'),
		'rqap_employeur': Decimal('0.00'),
		'rqap_total': Decimal('0.00'),
		'impot_provincial': Decimal('0.00'),
		'fss_employeur': Decimal('0.00'),
		'cnesst_employeur': Decimal('0.00'),
		'total': Decimal('0.00'),
	}
	revenu_brut_total = Decimal('0.00')

	for paie in paies:
		date_paie = paie.periode.date_paie or paie.periode.date_fin
		revenu_brut_total += _d(paie.salaire_brut_periode)

		rrq_employeur = _d(paie.rrq_employeur)
		rqap_employeur = _d(paie.rqap_employeur)
		ae_employeur = _d(paie.ae_employeur)
		cnesst_employeur = _d(paie.cnesst_employeur)
		fss_employeur = _d(paie.fss_employeur)

		federal_total['ae_employe'] += _d(paie.ae)
		federal_total['ae_employeur'] += ae_employeur
		federal_total['impot_federal'] += _d(paie.impot_federal)
		federal_total['total'] += _d(paie.ae) + ae_employeur + _d(paie.impot_federal)
		federal_total['ae_total'] = federal_total['ae_employe'] + federal_total['ae_employeur']

		provincial_total['rrq_employe'] += _d(paie.rrq)
		provincial_total['rrq_employeur'] += rrq_employeur
		provincial_total['rqap_employe'] += _d(paie.rqap)
		provincial_total['rqap_employeur'] += rqap_employeur
		provincial_total['impot_provincial'] += _d(paie.impot_provincial)
		provincial_total['fss_employeur'] += fss_employeur
		provincial_total['cnesst_employeur'] += cnesst_employeur
		provincial_total['total'] += _d(paie.rrq) + rrq_employeur + _d(paie.rqap) + rqap_employeur + _d(paie.impot_provincial) + fss_employeur + cnesst_employeur
		provincial_total['rrq_total'] = provincial_total['rrq_employe'] + provincial_total['rrq_employeur']
		provincial_total['rqap_total'] = provincial_total['rqap_employe'] + provincial_total['rqap_employeur']
		

	return render(request, 'paie/remises_mensuelles.html', {
		'title': 'Remises mensuelles',
		'federal_total': federal_total,
		'provincial_total': provincial_total,
		'periodes_count': len({paie.periode_id for paie in paies}),
		'revenu_brut_total': revenu_brut_total,
		'report_year_label': working_period['label'],
	})


@login_required
@xframe_options_sameorigin
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
		payday_weekday = settings_instance.date_premier_paiement_paie_annee.weekday()
		projected = PaieForm._build_projected_periods(
			settings_instance.frequence_paie,
			settings_instance.date_debut_periode_paie_annee,
			payday_weekday=payday_weekday,
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
		'report_year_label': month_label,
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


@login_required
@xframe_options_sameorigin
def test_temporaire_page(request):
	employe = Employe.objects.filter(actif=True).first()
	feuillet_debug = None
	if employe:
		from .models import FeuilletFiscalAnnuel
		feuillet_debug = FeuilletFiscalAnnuel.generer_pour_annee(employe, 2026)

	return render(request, 'paie/test_temporaire.html', {
		'title': 'Test temporaire',
		'feuillet_debug': feuillet_debug,
	})



@login_required
@xframe_options_sameorigin
def imprimer_feuillet_fiscal(request, employe_id, annee):
	employe = get_object_or_404(Employe, pk=employe_id)
	feuillet = FeuilletFiscalAnnuel.generer_pour_annee(employe, annee)
	employeur = Setting.objects.first()

	html_string = render(request, 'paie/feuillet_fiscal_pdf.html', {
		'feuillets': [feuillet],
		'employeur': employeur,
	}).content.decode('utf-8')

	pdf_file = HTML(string=html_string).write_pdf()

	response = HttpResponse(pdf_file, content_type='application/pdf')
	response['Content-Disposition'] = f'inline; filename="feuillet_{employe.nom}_{annee}.pdf"'
	return response