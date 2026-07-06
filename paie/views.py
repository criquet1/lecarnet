from datetime import date as date_type

from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render

from facture.utils import expert_required, get_setting

from .forms import EmployeForm, PaieForm
from .models import Employe, FrequencePaie, Paie, PeriodePaie


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


@expert_required
def paie_dashboard(request):
	_ensure_default_frequences_paie()
	resume = {
		'employes_actifs': Employe.objects.filter(actif=True).count(),
		'periodes_ouvertes': PeriodePaie.objects.filter(fermee=False).count(),
		'paies_total': Paie.objects.count(),
		'total_net': Paie.objects.aggregate(total=Sum('salaire_net')).get('total'),
	}
	paies_recentes = Paie.objects.select_related('employe', 'periode', 'periode__frequence_paie').order_by('-periode__date_fin', '-id')[:10]

	return render(request, 'paie/dashboard.html', {
		'title': 'Paie',
		'resume': resume,
		'paies_recentes': paies_recentes,
	})


@expert_required
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


@expert_required
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


@expert_required
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


@expert_required
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


@expert_required
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

	return JsonResponse({
		'ok': True,
		'date_fin': selected['value'] if selected else '',
		'date_paie': selected['date_paie'] if selected else '',
		'options': options_payload,
		'default_value': default_value,
	})


@expert_required
def journal_paies_page(request):
	paies = Paie.objects.select_related('employe', 'periode', 'periode__frequence_paie').order_by('-periode__date_fin', '-id')
	total_brut = paies.aggregate(total=Sum('salaire_brut_periode')).get('total')
	total_net = paies.aggregate(total=Sum('salaire_net')).get('total')

	return render(request, 'paie/journal_paies.html', {
		'title': 'Journal des paies',
		'paies': paies,
		'total_brut': total_brut,
		'total_net': total_net,
	})


@expert_required
def calendrier_paie_page(request):
	_ensure_default_frequences_paie()
	settings_instance = get_setting(
		'frequence_paie',
		'date_debut_periode_paie_annee',
		'date_premier_paiement_paie_annee',
	)

	current_year = date_type.today().year
	selected_year_raw = request.GET.get('annee')
	try:
		selected_year = int(selected_year_raw) if selected_year_raw else current_year
	except (TypeError, ValueError):
		selected_year = current_year

	calendar_rows = []
	error_message = None
	if not settings_instance or not settings_instance.frequence_paie_id or not settings_instance.date_debut_periode_paie_annee or not settings_instance.date_premier_paiement_paie_annee:
		error_message = 'Configurez la frequence de paie, le debut de premiere periode et la date du premier paiement dans Parametres.'
	else:
		projected = PaieForm._build_projected_periods(
			settings_instance.frequence_paie,
			settings_instance.date_debut_periode_paie_annee,
			settings_instance.date_premier_paiement_paie_annee,
			count=220,
		)
		rows = [
			{
				'date_debut': start,
				'date_fin': end,
				'date_paie': pay,
				'jour_paie': pay.strftime('%A') if pay else '',
			}
			for start, end, pay in projected
			if pay and pay.year == selected_year
		]
		for idx, row in enumerate(rows, start=1):
			row['index'] = idx
		calendar_rows = rows

	year_options = sorted({current_year - 1, current_year, current_year + 1, selected_year})

	return render(request, 'paie/calendrier_paie.html', {
		'title': 'Calendrier de paie',
		'selected_year': selected_year,
		'year_options': year_options,
		'calendar_rows': calendar_rows,
		'error_message': error_message,
	})
