from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from compte.models import Setting
from paie.forms import PaieForm
from paie.models import Employe, FrequencePaie, Paie, PeriodePaie
from paie.services.das import DASInputs, calculer_das, calculer_rrq


class DASTestCase(SimpleTestCase):
	def test_calcul_das_retourne_les_montants_attendus_pour_un_exemple_simple(self):
		resultat = calculer_das(
			DASInputs(
				salaire_brut_periode=Decimal('1000.00'),
				periodes_par_annee=26,
			)
		)

		self.assertEqual(resultat.rqap, Decimal('4.30'))
		self.assertEqual(resultat.rrq, Decimal('54.52'))
		self.assertEqual(resultat.ae, Decimal('13.00'))
		self.assertEqual(resultat.impot_federal, Decimal('51.41'))
		self.assertEqual(resultat.impot_provincial, Decimal('30.14'))
		self.assertEqual(resultat.total_retenues, Decimal('153.37'))
		self.assertEqual(resultat.salaire_net, Decimal('846.63'))

	def test_rrq_est_bloque_a_zero_si_le_plafond_annuel_est_atteint(self):
		rrq = calculer_rrq(
			salaire_brut_periode=Decimal('1000.00'),
			periodes_par_annee=26,
			cumul_rrq_annee=Decimal('4348.00'),
		)

		self.assertEqual(rrq, Decimal('0.00'))

	def test_calcul_das_refuse_une_frequence_invalide(self):
		with self.assertRaisesMessage(ValueError, 'periodes_par_annee doit etre superieur a zero'):
			calculer_das(
				DASInputs(
					salaire_brut_periode=Decimal('1000.00'),
					periodes_par_annee=0,
				)
			)


class PaieModelTestCase(TestCase):
	def test_paie_utilise_les_donnees_employe_et_le_service_das(self):
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
		self.assertEqual(paie.total_retenues, Decimal('153.37'))
		self.assertEqual(paie.salaire_net, Decimal('846.63'))

	def test_paie_cumule_les_retenues_des_paies_precedentes(self):
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

		self.assertEqual(premiere_paie.rrq, Decimal('4348.00'))
		self.assertEqual(seconde_paie.rrq, Decimal('0.00'))

	def test_cumuls_annuels_suivent_annee_de_date_paie(self):
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

		self.assertEqual(premiere_paie.rrq, Decimal('4348.00'))
		self.assertEqual(seconde_paie.rrq, Decimal('0.00'))

	def test_save_sans_modification_ne_recalcule_pas_les_montants(self):
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
			date_paie='2026-02-08',
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
