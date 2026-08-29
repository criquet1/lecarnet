from datetime import date
from decimal import Decimal
import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory
from django.template.loader import render_to_string
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware

from compte.models import Compte, Setting, Total
from facture.models import Tr_desc, Tr_detail
from paie.forms import EmployeForm, PaieForm
from paie.models import Employe, FrequencePaie, Paie, PeriodePaie, ParametresTauxPaie
from paie.views import creer_ecriture_salaire, mettre_a_jour_ecriture_salaire, journal_paies_page, saisir_paie_page, _compute_employer_totals_for_period
from paie.services.das import DASInputs, calculer_das, calculer_rrq


class DASTestCase(SimpleTestCase):
	def test_calcul_das_retourne_les_montants_attendus_pour_un_exemple_simple(self):
		resultat = calculer_das(
			DASInputs(
				salaire_brut_periode=Decimal('1000.00'),
				periodes_par_annee=26,
				taux_rrq_employe=Decimal('0.0630'),
				taux_rrq_supplementaire_2_employe=Decimal('0.0400'),
				exemption_base_rrq=Decimal('3500.00'),
				max_assurable_rrq=Decimal('74600.00'),
				max_supplementaire_rrq=Decimal('85000.00'),
				taux_rqap_employe=Decimal('0.00430'),
				max_assurable_rqap=Decimal('98700.00'),
				taux_ae_employe=Decimal('0.0130'),
				max_assurable_ae=Decimal('67500.00'),
				credit_personnel_federal_min=Decimal('16452.00'),
				taux_credit_federal=Decimal('0.14'),
				montant_canadien_pour_emploi=Decimal('1501.00'),
				abattement_federal_quebec=Decimal('0.165'),
				seuil_federal_1=Decimal('58523.00'),
				seuil_federal_2=Decimal('117045.00'),
				seuil_federal_3=Decimal('181440.00'),
				seuil_federal_4=Decimal('258482.00'),
				taux_federal_1=Decimal('0.14'),
				taux_federal_2=Decimal('0.205'),
				taux_federal_3=Decimal('0.26'),
				taux_federal_4=Decimal('0.29'),
				taux_federal_5=Decimal('0.33'),
				credit_personnel_quebec_min=Decimal('18952.00'),
				deduction_travailleur_qc_max_annuelle=Decimal('1450.00'),
				seuil_qc_1=Decimal('54345.00'),
				seuil_qc_2=Decimal('108680.00'),
				seuil_qc_3=Decimal('132245.00'),
				taux_qc_1=Decimal('0.14'),
				taux_qc_2=Decimal('0.19'),
				taux_qc_3=Decimal('0.24'),
				taux_qc_4=Decimal('0.2575'),
				taux_credit_quebec=Decimal('0.14'),
			)
		)

		# Valeurs de reference confirmees avec le calculateur officiel d'impot.net
		# (https://www.impot.net/fr/entreprises/das/) pour 1000.00$/periode, aux 2
		# semaines, seul, assujetti AE et RRQ, annee 2026.
		self.assertEqual(resultat.rqap, Decimal('4.30'))
		self.assertEqual(resultat.rrq, Decimal('54.52'))
		self.assertEqual(resultat.ae, Decimal('13.00'))
		self.assertEqual(resultat.impot_federal, Decimal('27.79'))
		self.assertEqual(resultat.impot_provincial, Decimal('28.93'))
		self.assertEqual(resultat.total_retenues, Decimal('128.54'))
		self.assertEqual(resultat.salaire_net, Decimal('871.46'))

	def test_rrq_apres_mga_applique_le_taux_supplementaire_2(self):
		rrq = calculer_rrq(
			salaire_brut_periode=Decimal('1000.00'),
			periodes_par_annee=26,
			cumul_salaire_brut_annee=Decimal('74600.00'),
			cumul_rrq_annee=Decimal('4348.00'),
			taux_rrq=Decimal('0.0630'),
			taux_rrq_supplementaire_2=Decimal('0.0400'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
		)

		self.assertEqual(rrq, Decimal('40.00'))

	def test_rrq_applique_supplementaire_2_apres_mga(self):
		rrq = calculer_rrq(
			salaire_brut_periode=Decimal('1000.00'),
			periodes_par_annee=26,
			cumul_salaire_brut_annee=Decimal('74600.00'),
			cumul_rrq_annee=Decimal('0.00'),
			taux_rrq=Decimal('0.0630'),
			taux_rrq_supplementaire_2=Decimal('0.0400'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
		)

		self.assertEqual(rrq, Decimal('40.00'))

	def test_calcul_das_refuse_une_frequence_invalide(self):
		with self.assertRaisesMessage(ValueError, 'periodes_par_annee doit etre superieur a zero'):
			calculer_das(
				DASInputs(
					salaire_brut_periode=Decimal('1000.00'),
					periodes_par_annee=0,
					taux_rrq_employe=Decimal('0.0630'),
					taux_rrq_supplementaire_2_employe=Decimal('0.0400'),
					exemption_base_rrq=Decimal('3500.00'),
					max_assurable_rrq=Decimal('74600.00'),
					max_supplementaire_rrq=Decimal('85000.00'),
					taux_rqap_employe=Decimal('0.00430'),
					max_assurable_rqap=Decimal('98700.00'),
					taux_ae_employe=Decimal('0.0130'),
					max_assurable_ae=Decimal('67500.00'),
					credit_personnel_federal_min=Decimal('16452.00'),
					taux_credit_federal=Decimal('0.14'),
					montant_canadien_pour_emploi=Decimal('1501.00'),
					abattement_federal_quebec=Decimal('0.165'),
					seuil_federal_1=Decimal('58523.00'),
					seuil_federal_2=Decimal('117045.00'),
					seuil_federal_3=Decimal('181440.00'),
					seuil_federal_4=Decimal('258482.00'),
					taux_federal_1=Decimal('0.14'),
					taux_federal_2=Decimal('0.205'),
					taux_federal_3=Decimal('0.26'),
					taux_federal_4=Decimal('0.29'),
					taux_federal_5=Decimal('0.33'),
					credit_personnel_quebec_min=Decimal('18952.00'),
					deduction_travailleur_qc_max_annuelle=Decimal('1450.00'),
					seuil_qc_1=Decimal('54345.00'),
					seuil_qc_2=Decimal('108680.00'),
					seuil_qc_3=Decimal('132245.00'),
					taux_qc_1=Decimal('0.14'),
					taux_qc_2=Decimal('0.19'),
					taux_qc_3=Decimal('0.24'),
					taux_qc_4=Decimal('0.2575'),
					taux_credit_quebec=Decimal('0.14'),
				)
			)


class PaieModelTestCase(TestCase):
	def setUp(self):
		Setting.objects.using('default').all().delete()
		Setting.objects.using('default').create(
			nom='Parametres test paie',
			adresse='Adresse',
			ville='Ville',
			code_postal='A1A1A1',
			pays='CA',
			phone='000-000-0000',
			email='test@example.com',
			taux_fss_employeur=Decimal('0.02000'),
			taux_cnesst_employeur=Decimal('0.02319'),
		)

	def _creer_parametres_taux_paie_defaut(self):
		return ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85200.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.60200'),
			max_assurable_rqap=Decimal('103000.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('68900.00'),
		)
	def test_saisie_paie_redirige_vers_onglet_journal(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Redirection',
			prenom='Journal',
			date_embauche='2026-01-01',
			salH='20.00',
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
			date_paie='2026-01-09',
		)
		request = RequestFactory().post(reverse('paie:paie_saisir'), {
			'employe': employe.pk,
			'heures_travaillees': '40.00',
		})
		request.user = get_user_model().objects.create_user(username='paie-redirection')
		SessionMiddleware(lambda req: None).process_request(request)
		setattr(request, '_messages', FallbackStorage(request))

		response = saisir_paie_page(request)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, f"{reverse('paie:paie_journal')}?employe={employe.pk}")

	def test_saisie_paie_embarquee_redirige_vers_journal_embarque(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='RedirectionEmbarquee',
			prenom='Journal',
			date_embauche='2026-01-01',
			salH='20.00',
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
			date_paie='2026-01-09',
		)
		request = RequestFactory().post(f"{reverse('paie:paie_saisir')}?embed=1", {
			'employe': employe.pk,
			'heures_travaillees': '40.00',
		})
		request.user = get_user_model().objects.create_user(username='paie-redirection-embarquee')
		SessionMiddleware(lambda req: None).process_request(request)
		setattr(request, '_messages', FallbackStorage(request))

		response = saisir_paie_page(request)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, f"{reverse('paie:paie_journal')}?employe={employe.pk}&embed=1")

	def test_saisie_paie_affiche_periode_et_taux_horaire(self):
		request = RequestFactory().get('/paie/saisir/')
		request.user = get_user_model().objects.create_user(username='saisie-paie-template')
		html = render_to_string(
			'paie/saisir_paie.html',
			{'title': 'Saisir une paie', 'form': PaieForm()},
			request=request,
		)

		self.assertIn('id_periode_date_fin', html)
		self.assertIn('id_periode_date_paie', html)
		self.assertIn('id="taux-horaire-display"', html)

	def test_periode_sans_date_paie_accepte_des_dates_iso(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)

		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
		)

		self.assertEqual(periode.date_paie, date(2026, 1, 15))

	def test_taux_horaire_employe_accepte_la_virgule_decimale(self):
		employe = Employe(salH='25,50 $')

		self.assertEqual(employe.taux_horaire_defaut, Decimal('25.50'))

	def test_formulaire_employe_refuse_un_taux_horaire_nul(self):
		form = EmployeForm(data={
			'nom': 'Sans taux',
			'prenom': 'Test',
			'date_embauche': '2026-01-01',
			'salH': '0',
			'taux_vacances': '4',
			'actif': True,
		})

		self.assertFalse(form.is_valid())
		self.assertIn('salH', form.errors)

	def test_saisie_paie_refuse_des_heures_avec_un_taux_horaire_nul(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Sans taux',
			prenom='Paie',
			date_embauche='2026-01-01',
			salH='0.00',
			frequence_paie=frequence,
		)
		form = PaieForm(data={
			'employe': employe.pk,
			'heures_travaillees': '40.00',
		})

		self.assertFalse(form.is_valid())
		self.assertIn('taux horaire', form.errors['employe'][0].lower())

	def test_rrq_accepte_un_taux_deja_en_ratio(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85200.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.60200'),
			max_assurable_rqap=Decimal('103000.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('68900.00'),
		)

		employe = Employe.objects.create(
			nom='Martin',
			prenom='Noa',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut=date(2026, 1, 1),
			date_fin=date(2026, 1, 14),
			date_paie=date(2026, 1, 16),
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		self.assertEqual(paie.rrq, Decimal('54.52'))

	def test_rrq_utilise_le_dernier_bloc_connu_si_aucun_bloc_actif(self):
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2025, 1, 1),
			rrq_date_fin_effet=date(2025, 12, 31),
			taux_rrq_employe=Decimal('6.40000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.40000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
			rqap_date_debut_effet=date(2025, 1, 1),
			rqap_date_fin_effet=date(2025, 12, 31),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.69200'),
			max_assurable_rqap=Decimal('98700.00'),
			ae_date_debut_effet=date(2025, 1, 1),
			ae_date_fin_effet=date(2025, 12, 31),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('67500.00'),
		)

		taux = Paie._taux_effectifs(date(2026, 1, 16))

		self.assertEqual(taux['taux_rrq_employe'], Decimal('0.06400'))

	def test_rrq_ignore_un_mga_invalide_a_zero(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('0.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.69200'),
			max_assurable_rqap=Decimal('98700.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('67500.00'),
		)

		employe = Employe.objects.create(
			nom='Lemieux',
			prenom='Ana',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut=date(2026, 1, 1),
			date_fin=date(2026, 1, 14),
			date_paie=date(2026, 1, 16),
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		self.assertEqual(paie.rrq, Decimal('54.52'))

	def test_rrq_corrige_un_taux_base_invalide_inferieur_ou_egal_au_supp2(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('4.00000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('4.00000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.69200'),
			max_assurable_rqap=Decimal('98700.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('67500.00'),
		)

		employe = Employe.objects.create(
			nom='Gosselin',
			prenom='Mia',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut=date(2026, 1, 1),
			date_fin=date(2026, 1, 14),
			date_paie=date(2026, 1, 16),
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		self.assertEqual(paie.rrq, Decimal('54.52'))

	def test_rrq_ignore_un_mga_trop_petit_non_nul(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('746.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.69200'),
			max_assurable_rqap=Decimal('98700.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('67500.00'),
		)

		employe = Employe.objects.create(
			nom='Boisvert',
			prenom='Zoe',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut=date(2026, 1, 1),
			date_fin=date(2026, 1, 14),
			date_paie=date(2026, 1, 16),
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		self.assertEqual(paie.rrq, Decimal('54.52'))

	def test_totaux_employeur_utilisent_rrq_employe_si_taux_employeur_zero(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('0.00000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85000.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.69200'),
			max_assurable_rqap=Decimal('98700.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('67500.00'),
		)

		employe = Employe.objects.create(
			nom='Pineault',
			prenom='Elio',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut=date(2026, 1, 1),
			date_fin=date(2026, 1, 14),
			date_paie=date(2026, 1, 16),
		)
		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		settings_instance = Setting.objects.create(
			nom='Parametres employeur',
			logo='images.png',
			adresse='Adresse',
			ville='Ville',
			code_postal='A1A1A1',
			pays='CA',
			phone='000-000-0000',
			email='test@example.com',
			taux_cnesst_employeur=Decimal('0.00000'),
			taux_fss_employeur=Decimal('0.00000'),
		)

		totals = _compute_employer_totals_for_period([paie], settings_instance)

		self.assertEqual(totals['rrq_employeur'], paie.rrq)

	def test_paie_utilise_les_donnees_employe_et_le_service_das(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Tremblay',
			prenom='Jeanne',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		self.assertEqual(paie.taux_horaire, Decimal('25.00'))
		self.assertEqual(paie.salaire_brut_periode, Decimal('1000.00'))
		self.assertEqual(paie.total_retenues, Decimal('128.54'))
		self.assertEqual(paie.salaire_net, Decimal('871.46'))

	def test_paie_inclut_les_heures_supplementaires_a_temps_et_demi(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Temps',
			prenom='Supplementaire',
			date_embauche='2026-01-01',
			salH='20.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
		)

		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
			heures_supp=Decimal('2.00'),
		)

		self.assertEqual(paie.salaire_brut_periode, Decimal('860.00'))

	def test_formulaire_paie_enregistre_heures_supp_et_vacances(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Formulaire',
			prenom='Supplementaire',
			date_embauche='2026-01-01',
			salH='20.00',
			taux_vacances=Decimal('0.04000'),
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
			date_paie='2026-01-09',
		)
		form = PaieForm(data={
			'employe': employe.pk,
			'heures_travaillees': '40.00',
			'heures_supp': '2.00',
		})

		self.assertTrue(form.is_valid(), form.errors)
		paie = form.save()

		self.assertEqual(paie.heures_supp, Decimal('2.00'))
		self.assertEqual(paie.salaire_brut_periode, Decimal('860.00'))
		self.assertEqual(paie.vacances, Decimal('34.40'))

	def test_paie_cumule_les_retenues_des_paies_precedentes(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Roy',
			prenom='Alex',
			date_embauche='2026-01-01',
			salH='100.00',
			frequence_paie=frequence,
		)
		premiere_periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
		)
		seconde_periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-08',
			date_fin='2026-01-14',
		)

		premiere_paie = Paie.objects.create(
			employe=employe,
			periode=premiere_periode,
			heures_travaillees=Decimal('10000.00'),
		)
		seconde_paie = Paie.objects.create(
			employe=employe,
			periode=seconde_periode,
			heures_travaillees=Decimal('10.00'),
		)

		self.assertEqual(premiere_paie.rrq, Decimal('5119.56'))
		self.assertEqual(seconde_paie.rrq, Decimal('0.00'))

	def test_cumuls_annuels_suivent_annee_de_date_paie(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Bouchard',
			prenom='Lina',
			date_embauche='2025-01-01',
			salH='100.00',
			frequence_paie=frequence,
		)
		periode_payee_2026_1 = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2025-12-25',
			date_fin='2025-12-31',
			date_paie='2026-01-02',
		)
		periode_payee_2026_2 = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
			date_paie='2026-01-09',
		)

		premiere_paie = Paie.objects.create(
			employe=employe,
			periode=periode_payee_2026_1,
			heures_travaillees=Decimal('10000.00'),
		)
		seconde_paie = Paie.objects.create(
			employe=employe,
			periode=periode_payee_2026_2,
			heures_travaillees=Decimal('10.00'),
		)

		self.assertEqual(premiere_paie.rrq, Decimal('5119.56'))
		self.assertEqual(seconde_paie.rrq, Decimal('0.00'))

	def test_save_sans_modification_ne_recalcule_pas_les_montants(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Dupuis',
			prenom='Maya',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
			date_paie='2026-01-16',
		)
		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		with patch.object(Paie, 'recalculer') as recalculer_mock:
			paie.save()

		recalculer_mock.assert_not_called()

	def test_save_avec_modification_recalcule_les_montants(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Goulet',
			prenom='Sami',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
			date_paie='2026-01-16',
		)
		paie = Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
		)

		paie.heures_travaillees = Decimal('41.00')
		with patch.object(Paie, 'recalculer') as recalculer_mock:
			paie.save()

		recalculer_mock.assert_called_once()

	def test_saisie_paie_utilise_la_prochaine_periode_disponible(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Simard',
			prenom='Nora',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		periode_1 = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
			date_paie='2026-01-16',
		)
		periode_2 = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-15',
			date_fin='2026-01-28',
			date_paie='2026-01-30',
		)
		Paie.objects.create(
			employe=employe,
			periode=periode_1,
			heures_travaillees=Decimal('1.00'),
		)

		form = PaieForm(data={
			'employe': employe.pk,
			'heures_travaillees': '40.00',
		})

		self.assertTrue(form.is_valid(), form.errors)
		paie = form.save()

		self.assertEqual(paie.periode_id, periode_2.id)
		self.assertEqual(paie.taux_horaire, Decimal('25.00'))
		self.assertEqual(str(paie.periode.date_debut), '2026-01-15')
		self.assertEqual(str(paie.periode.date_fin), '2026-01-28')
		self.assertEqual(str(paie.periode.date_paie), '2026-01-30')
		self.assertEqual(paie.deduction_tp1015_j, Decimal('0.00'))
		self.assertEqual(paie.deduction_tp1016_j1, Decimal('0.00'))

	def test_saisie_paie_force_j_et_j1_a_zero(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Lavoie',
			prenom='Mila',
			date_embauche='2026-01-01',
			salH='30.00',
			e_prov=2222,
			frequence_paie=frequence,
		)

		form = PaieForm(data={
			'employe': employe.pk,
			'heures_travaillees': '35.00',
			'deduction_tp1016_j1': '999.99',
		})
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-02-01',
			date_fin='2026-02-07',
			date_paie='2026-02-06',
		)

		self.assertTrue(form.is_valid(), form.errors)
		paie = form.save()

		self.assertEqual(paie.deduction_tp1015_j, Decimal('0.00'))
		self.assertEqual(paie.deduction_tp1016_j1, Decimal('0.00'))

	def test_suggestions_periode_retournent_erreur_si_aucune_base(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.PAR_MOIS,
			nom='Par mois',
			nombre_periodes_par_annee=12,
		)
		employe = Employe.objects.create(
			nom='Nadeau',
			prenom='Eva',
			date_embauche='2026-01-01',
			salH='28.00',
			frequence_paie=frequence,
		)
		next_candidate, near_today_candidate, erreur = PaieForm.suggestions_periode_pour_employe(employe)

		self.assertIsNone(next_candidate)
		self.assertIsNone(near_today_candidate)
		self.assertIn('Aucune periode de paie disponible', erreur)

	def test_prochaine_periode_projette_la_suivante_si_toutes_utilisees(self):
		self._creer_parametres_taux_paie_defaut()
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Gagnon',
			prenom='Leo',
			date_embauche='2026-01-01',
			salH='22.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
			date_paie='2026-01-21',
		)
		Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('10.00'),
		)

		next_candidate, near_today_candidate, erreur = PaieForm.suggestions_periode_pour_employe(employe)

		self.assertIsNone(erreur)
		self.assertEqual(next_candidate['mode'], 'projected')
		self.assertEqual(str(next_candidate['date_debut']), '2026-01-15')
		self.assertEqual(str(next_candidate['date_fin']), '2026-01-28')
		self.assertEqual(str(next_candidate['date_paie']), '2026-02-04')
		self.assertIsNotNone(near_today_candidate)
		self.assertEqual(near_today_candidate['mode'], 'projected')
		self.assertGreaterEqual(near_today_candidate['date_paie'], next_candidate['date_paie'])

	def test_options_fin_periode_annee_courante_ne_sont_pas_vides(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		Setting.objects.using('default').all().delete()
		Setting.objects.create(
			nom='Parametres test',
			logo='images.png',
			adresse='Adresse',
			ville='Ville',
			code_postal='A1A1A1',
			pays='CA',
			phone='000-000-0000',
			email='test@example.com',
			frequence_paie=frequence,
			date_debut_periode_paie_annee=date(date.today().year, 1, 1),
			date_premier_paiement_paie_annee=date(date.today().year, 1, 8),
		)
		employe = Employe.objects.create(
			nom='Fortin',
			prenom='Lea',
			date_embauche='2026-01-01',
			salH='29.00',
			frequence_paie=frequence,
		)

		options_payload, default_value, error_message = PaieForm.options_fin_periode_annee_courante(employe)

		self.assertIsNone(error_message)
		self.assertTrue(options_payload)
		self.assertIsNotNone(default_value)

	def test_options_excluent_les_dates_de_paiement_weekend(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Poirier',
			prenom='Iris',
			date_embauche='2026-01-01',
			salH='31.00',
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-07-01',
			date_fin='2026-07-07',
			date_paie='2026-07-11',  # samedi
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-07-08',
			date_fin='2026-07-14',
			date_paie='2026-07-10',  # vendredi
		)

		options_payload, default_value, error_message = PaieForm.options_fin_periode_annee_courante(employe)

		self.assertIsNone(error_message)
		self.assertTrue(options_payload)
		values = {item['value'] for item in options_payload}
		self.assertNotIn('2026-07-07', values)
		self.assertIn('2026-07-14', values)
		self.assertIn(default_value, values)

	def test_validation_refuse_selection_weekend_hors_choix(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Morin',
			prenom='Jade',
			date_embauche='2026-01-01',
			salH='30.00',
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-07-01',
			date_fin='2026-07-07',
			date_paie='2026-07-11',  # samedi
		)

		form = PaieForm(data={
			'employe': employe.pk,
			'periode_date_fin': '2026-07-07',
			'heures_travaillees': '35.00',
		})

		self.assertFalse(form.is_valid())
		self.assertIn('Sélectionnez un choix valide', str(form.errors))

	def test_to_business_day_devance_si_ferie(self):
		# 2026-06-24: Fete nationale du Quebec (ferie QC)
		adjusted = PaieForm._to_business_day(date(2026, 6, 24))
		self.assertEqual(str(adjusted), '2026-06-23')

	def test_options_excluent_date_paiement_ferie(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='Blais',
			prenom='Noe',
			date_embauche='2026-01-01',
			salH='30.00',
			frequence_paie=frequence,
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-06-18',
			date_fin='2026-06-24',
			date_paie='2026-06-24',  # ferie QC
		)
		PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-06-25',
			date_fin='2026-07-01',
			date_paie='2026-07-02',
		)

		options_payload, _, error_message = PaieForm.options_fin_periode_annee_courante(employe)

		self.assertIsNone(error_message)
		values = {item['value'] for item in options_payload}
		self.assertNotIn('2026-06-24', values)
		self.assertIn('2026-07-01', values)


class PaieEcritureSalairePermissionTestCase(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.user_model = get_user_model()

		Setting.objects.using('default').all().delete()
		Setting.objects.using('default').create(
			nom='Parametres test paie',
			adresse='Adresse',
			ville='Ville',
			code_postal='A1A1A1',
			pays='CA',
			phone='000-000-0000',
			email='test@example.com',
			taux_fss_employeur=Decimal('0.02000'),
			taux_cnesst_employeur=Decimal('0.02319'),
		)
		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85200.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.60200'),
			max_assurable_rqap=Decimal('103000.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('68900.00'),
		)

	def test_utilisateur_ordinaire_ne_peut_pas_creer_ecriture_salaire(self):
		user = self.user_model.objects.create_user(username='employe-paie', password='pass1234')
		request = self.factory.post(reverse('paie:paie_creer_ecriture_salaire', args=[999999]))
		request.user = user

		with self.assertRaisesMessage(PermissionDenied, 'Accès réservé aux experts.'):
			creer_ecriture_salaire(request, 999999)

		self.assertFalse(Tr_desc.objects.exists())

		journal_request = self.factory.get(reverse('paie:paie_journal'))
		journal_request.user = user
		SessionMiddleware(lambda req: None).process_request(journal_request)
		response = journal_paies_page(journal_request)

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'form-creer-ecriture-salaire')
		self.assertNotContains(response, 'js-creer-ecriture-salaire')

	def test_membre_du_groupe_expert_franchit_la_protection(self):
		user = self.user_model.objects.create_user(username='expert-groupe', password='pass1234')
		user.groups.add(Group.objects.create(name='expert'))
		request = self.factory.post(reverse('paie:paie_creer_ecriture_salaire', args=[999999]))
		request.user = user

		with self.assertRaises(Http404):
			creer_ecriture_salaire(request, 999999)

		journal_request = self.factory.get(reverse('paie:paie_journal'))
		journal_request.user = user
		SessionMiddleware(lambda req: None).process_request(journal_request)
		response = journal_paies_page(journal_request)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'form-creer-ecriture-salaire')

	def test_journal_affiche_un_onglet_par_employe(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		for index, (nom, prenom) in enumerate((('Alpha', 'Anne'), ('Bravo', 'Bob')), start=1):
			employe = Employe.objects.create(
				nom=nom,
				prenom=prenom,
				date_embauche='2026-01-01',
				salH='20.00',
				frequence_paie=frequence,
			)
			periode = PeriodePaie.objects.create(
				frequence_paie=frequence,
				date_debut=f'2026-01-{index:02d}',
				date_fin=f'2026-01-{index + 6:02d}',
				date_paie=f'2026-01-{index + 8:02d}',
			)
			Paie.objects.create(employe=employe, periode=periode, heures_travaillees=Decimal('1.00'))

		user = self.user_model.objects.create_user(username='journal-onglets')
		request = self.factory.get(reverse('paie:paie_journal'))
		request.user = user
		SessionMiddleware(lambda req: None).process_request(request)

		response = journal_paies_page(request)

		self.assertContains(response, 'Alpha Anne')
		self.assertContains(response, 'Bravo Bob')
		self.assertContains(response, 'id="journal-emp-', count=4)

	def test_journal_active_onglet_employe_cible_par_parametre(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employes = []
		for index, (nom, prenom) in enumerate((('Alpha', 'Anne'), ('Bravo', 'Bob')), start=1):
			employe = Employe.objects.create(
				nom=nom,
				prenom=prenom,
				date_embauche='2026-01-01',
				salH='20.00',
				frequence_paie=frequence,
			)
			periode = PeriodePaie.objects.create(
				frequence_paie=frequence,
				date_debut=f'2026-01-{index:02d}',
				date_fin=f'2026-01-{index + 6:02d}',
				date_paie=f'2026-01-{index + 8:02d}',
			)
			Paie.objects.create(employe=employe, periode=periode, heures_travaillees=Decimal('1.00'))
			employes.append(employe)

		user = self.user_model.objects.create_user(username='journal-onglet-cible')
		request = self.factory.get(reverse('paie:paie_journal'), {'employe': employes[1].pk})
		request.user = user
		SessionMiddleware(lambda req: None).process_request(request)

		response = journal_paies_page(request)
		content = response.content.decode()

		self.assertIn(f'tab-pane fade show active" id="journal-emp-{employes[1].pk}"', content)
		self.assertIn(f'nav-link active" id="journal-emp-{employes[1].pk}-tab"', content)
		self.assertIn('tab-pane fade" id="journal-total"', content)
		self.assertIn('nav-link" id="journal-total-tab"', content)

	def test_journal_colonne_heures_additionne_regulieres_et_supplementaires(self):
		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.HEBDOMADAIRE,
			nom='Hebdomadaire',
			nombre_periodes_par_annee=52,
		)
		employe = Employe.objects.create(
			nom='HeuresTotal',
			prenom='Test',
			date_embauche='2026-01-01',
			salH='20.00',
			frequence_paie=frequence,
		)
		periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-07',
			date_paie='2026-01-09',
		)
		Paie.objects.create(
			employe=employe,
			periode=periode,
			heures_travaillees=Decimal('40.00'),
			heures_supp=Decimal('3.50'),
		)

		user = self.user_model.objects.create_user(username='journal-heures-total')
		request = self.factory.get(reverse('paie:paie_journal'))
		request.user = user
		SessionMiddleware(lambda req: None).process_request(request)

		response = journal_paies_page(request)

		self.assertContains(response, '43,50')


class PaieEcritureSalaireTestCase(TestCase):
	def setUp(self):
		User = get_user_model()
		self.user = User.objects.create_user(
			username='expert-paie',
			email='expert@example.com',
			password='pass1234',
			is_superuser=True,
			is_staff=True,
		)
		self.factory = RequestFactory()

		total = Total.objects.create(no_total=1000, desc='Tests paie')
		self.compte_salaire = Compte.objects.create(numero=5000, libelle='Salaires', no_total=total)
		self.compte_vacances = Compte.objects.create(numero=2210, libelle='Vacances', no_total=total)
		self.compte_vacances_a_payer = Compte.objects.create(numero=2100, libelle='Vacances a payer', no_total=total)
		self.compte_benefices = Compte.objects.create(numero=5300, libelle='Benefices marginaux', no_total=total)
		self.compte_salaires_a_payer = Compte.objects.create(numero=2350, libelle='Salaires a payer', no_total=total)
		self.compte_das_fed = Compte.objects.create(numero=2360, libelle='DAS fed a payer', no_total=total)
		self.compte_das_prov = Compte.objects.create(numero=2370, libelle='DAS prov a payer', no_total=total)

		Setting.objects.all().delete()
		Setting.objects.create(
			nom='Parametres test paie',
			logo='images.png',
			adresse='Adresse',
			ville='Ville',
			code_postal='A1A1A1',
			pays='CA',
			phone='000-000-0000',
			email='test@example.com',
			compte_salaire=self.compte_salaire,
			compte_vacances=self.compte_vacances,
			compte_vacances_a_payer=self.compte_vacances_a_payer,
			compte_benefices_marginaux=self.compte_benefices,
			compte_salaires_a_payer=self.compte_salaires_a_payer,
			compte_das_federales=self.compte_das_fed,
			compte_das_provinciales=self.compte_das_prov,
			taux_fss_employeur=Decimal('0.02000'),
			taux_cnesst_employeur=Decimal('0.02319'),
		)

		ParametresTauxPaie.objects.using('default').create(
			rrq_date_debut_effet=date(2026, 1, 1),
			taux_rrq_employe=Decimal('6.30000'),
			taux_rrq_supplementaire_2_employe=Decimal('4.00000'),
			taux_rrq_employeur=Decimal('6.30000'),
			exemption_base_rrq=Decimal('3500.00'),
			max_assurable_rrq=Decimal('74600.00'),
			max_supplementaire_rrq=Decimal('85200.00'),
			rqap_date_debut_effet=date(2026, 1, 1),
			taux_rqap_employe=Decimal('0.43000'),
			taux_rqap_employeur=Decimal('0.60200'),
			max_assurable_rqap=Decimal('103000.00'),
			ae_date_debut_effet=date(2026, 1, 1),
			taux_ae_employe=Decimal('1.30000'),
			taux_ae_employeur=Decimal('1.82000'),
			max_assurable_ae=Decimal('68900.00'),
		)

		frequence = FrequencePaie.objects.create(
			code=FrequencePaie.AUX_2_SEMAINES,
			nom='Aux 2 semaines',
			nombre_periodes_par_annee=26,
		)
		employe = Employe.objects.create(
			nom='Tremblay',
			prenom='Nina',
			date_embauche='2026-01-01',
			salH='25.00',
			frequence_paie=frequence,
		)
		self.periode = PeriodePaie.objects.create(
			frequence_paie=frequence,
			date_debut='2026-01-01',
			date_fin='2026-01-14',
			date_paie='2026-01-16',
		)
		# Taux horaire et heures reels (et non 0.00) pour que recalculer() calcule
		# aussi les cotisations employeur (rrq_employeur, rqap_employeur, etc.)
		# a partir d'un salaire brut reel, plutot que de rester a 0.00.
		paie = Paie.objects.create(
			employe=employe,
			periode=self.periode,
			heures_travaillees=Decimal('80.00'),
			vacances_payees=Decimal('300.00'),
		)
		self.paie_id = paie.pk

		# Scenario de reference valide par l'utilisatrice.
		Paie.objects.filter(pk=paie.pk).update(
			salaire_brut_periode=Decimal('2300.00'),
			vacances_payees=Decimal('300.00'),
			vacances=Decimal('112.00'),
			salaire_net=Decimal('2022.84'),
			rrq=Decimal('60.00'),
			rqap=Decimal('40.00'),
			ae=Decimal('40.00'),
			impot_federal=Decimal('47.34'),
			impot_provincial=Decimal('89.82'),
			total_retenues=Decimal('277.16'),
		)

	def _creer_ecriture_et_map_par_compte(self):
		request = self.factory.post(reverse('paie:paie_creer_ecriture_salaire', args=[self.periode.id]))
		request.user = self.user
		session_middleware = SessionMiddleware(lambda req: None)
		session_middleware.process_request(request)
		request.session.save()
		setattr(request, '_messages', FallbackStorage(request))

		response = creer_ecriture_salaire(request, self.periode.id)
		self.assertEqual(response.status_code, 200)
		payload = json.loads(response.content)
		self.assertTrue(payload['ok'])
		self.assertFalse(payload['already_existed'])

		tr_desc = Tr_desc.objects.order_by('-id').first()
		self.assertIsNotNone(tr_desc)
		self.assertEqual(payload['no_ej'], tr_desc.no_ej)

		details = list(Tr_detail.objects.filter(tr_desc=tr_desc).select_related('compte'))
		by_compte = {detail.compte.numero: detail.montant for detail in details}
		return details, by_compte

	def test_creer_ecriture_salaire_genere_les_montants_attendus(self):
		details, by_compte = self._creer_ecriture_et_map_par_compte()

		self.assertEqual(by_compte[self.compte_salaire.numero], Decimal('2000.00'))
		self.assertEqual(by_compte[self.compte_vacances.numero], Decimal('112.00'))
		self.assertEqual(by_compte[self.compte_vacances_a_payer.numero], Decimal('188.00'))
		self.assertEqual(by_compte[self.compte_benefices.numero], Decimal('291.47'))
		self.assertEqual(by_compte[self.compte_salaires_a_payer.numero], Decimal('-2022.84'))
		self.assertEqual(by_compte[self.compte_das_fed.numero], Decimal('-129.20'))
		self.assertEqual(by_compte[self.compte_das_prov.numero], Decimal('-439.43'))

		total = sum((detail.montant for detail in details), Decimal('0.00'))
		self.assertEqual(total, Decimal('0.00'))

	def test_creer_ecriture_salaire_supporte_vacances_a_payer_crediteur(self):
		Paie.objects.filter(pk=self.paie_id).update(
			vacances_payees=Decimal('50.00'),
			vacances=Decimal('112.00'),
		)

		details, by_compte = self._creer_ecriture_et_map_par_compte()

		self.assertEqual(by_compte[self.compte_vacances.numero], Decimal('112.00'))
		self.assertEqual(by_compte[self.compte_vacances_a_payer.numero], Decimal('-62.00'))

		total = sum((detail.montant for detail in details), Decimal('0.00'))
		self.assertEqual(total, Decimal('0.00'))

	def test_creer_ecriture_salaire_retourne_json_existant_si_deja_creee(self):
		self._creer_ecriture_et_map_par_compte()
		tr_desc = Tr_desc.objects.order_by('-id').first()

		request = self.factory.post(reverse('paie:paie_creer_ecriture_salaire', args=[self.periode.id]))
		request.user = self.user
		session_middleware = SessionMiddleware(lambda req: None)
		session_middleware.process_request(request)
		request.session.save()
		setattr(request, '_messages', FallbackStorage(request))

		response = creer_ecriture_salaire(request, self.periode.id)

		self.assertEqual(response.status_code, 200)
		payload = json.loads(response.content)
		self.assertTrue(payload['ok'])
		self.assertTrue(payload['already_existed'])
		self.assertEqual(payload['no_ej'], tr_desc.no_ej)
		self.assertEqual(Tr_desc.objects.count(), 1)

	def _poster_creer_ecriture(self):
		request = self.factory.post(reverse('paie:paie_creer_ecriture_salaire', args=[self.periode.id]))
		request.user = self.user
		session_middleware = SessionMiddleware(lambda req: None)
		session_middleware.process_request(request)
		request.session.save()
		setattr(request, '_messages', FallbackStorage(request))
		response = creer_ecriture_salaire(request, self.periode.id)
		return json.loads(response.content)

	def _poster_mettre_a_jour_ecriture(self):
		request = self.factory.post(reverse('paie:paie_mettre_a_jour_ecriture_salaire', args=[self.periode.id]))
		request.user = self.user
		session_middleware = SessionMiddleware(lambda req: None)
		session_middleware.process_request(request)
		request.session.save()
		setattr(request, '_messages', FallbackStorage(request))
		response = mettre_a_jour_ecriture_salaire(request, self.periode.id)
		return json.loads(response.content)

	def test_creer_ecriture_salaire_signale_une_correspondance_si_rien_n_a_change(self):
		self._creer_ecriture_et_map_par_compte()

		payload = self._poster_creer_ecriture()

		self.assertTrue(payload['ok'])
		self.assertTrue(payload['already_existed'])
		self.assertTrue(payload['matches'])
		self.assertIsNone(payload['update_url'])

	def test_creer_ecriture_salaire_signale_un_ecart_si_la_paie_a_change(self):
		self._creer_ecriture_et_map_par_compte()

		Paie.objects.filter(pk=self.paie_id).update(salaire_brut_periode=Decimal('2500.00'))

		payload = self._poster_creer_ecriture()

		self.assertTrue(payload['ok'])
		self.assertTrue(payload['already_existed'])
		self.assertFalse(payload['matches'])
		self.assertTrue(payload['update_url'])

	def test_mettre_a_jour_ecriture_salaire_applique_les_nouveaux_montants(self):
		self._creer_ecriture_et_map_par_compte()
		tr_desc = Tr_desc.objects.order_by('-id').first()

		Paie.objects.filter(pk=self.paie_id).update(salaire_brut_periode=Decimal('2500.00'))

		payload = self._poster_mettre_a_jour_ecriture()

		self.assertTrue(payload['ok'])
		self.assertTrue(payload['updated'])
		self.assertTrue(payload['matches'])
		self.assertEqual(payload['no_ej'], tr_desc.no_ej)
		self.assertEqual(Tr_desc.objects.count(), 1)

		details = list(Tr_detail.objects.filter(tr_desc=tr_desc).select_related('compte'))
		by_compte = {detail.compte.numero: detail.montant for detail in details}
		self.assertEqual(by_compte[self.compte_salaire.numero], Decimal('2200.00'))

		verification = self._poster_creer_ecriture()
		self.assertTrue(verification['matches'])
