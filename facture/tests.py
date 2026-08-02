import json
import re
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import DatabaseError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from compte.models import Compte, SoldeAuxLivres, Total
from facture.models import Compagnie, CompteReleve, Facture, RapportTaxes, Releve, SoldeFin, Source, TransactionListe, Tr_desc, Tr_detail
from facture.templatetags.facture_extras import accounting_amount
from facture.views import _company_invoices_queryset, dashboard, facture, grand_livre, journal_general, rapport_de_taxes, releve_bancaire, releve_ecriture_similaire, update_working_period
from facture.working_period import set_working_period
from compte.models import Setting
from tenancy.models import ClientDatabase, UserClientAccess
from tenancy.db_context import reset_current_tenant_alias, set_current_tenant_alias
from tenancy.services import SESSION_CLIENT_ALIAS_KEY, SESSION_CLIENT_ID_KEY


class SidebarNavigationTests(SimpleTestCase):
	def test_payroll_directory_contains_payroll_links_only(self):
		request = RequestFactory().get('/paie/')
		request.user = AnonymousUser()
		request.resolver_match = resolve('/paie/')
		context = {
			'site_settings': None,
			'working_period': {'tenant_key': 'test', 'value': '2026-08'},
		}

		payroll_html = render_to_string('paie/dashboard.html', context, request=request)
		for url_name in (
			'paie:paie_saisir',
			'paie:paie_journal',
			'paie:paie_employes',
			'paie:paie_calendrier',
			'paie:paie_remises_mensuelles',
		):
			self.assertIn(f'href="{reverse(url_name)}"', payroll_html)

		reports_html = render_to_string('rapports/index.html', context, request=request)
		self.assertNotIn(reverse('paie:paie_remises_mensuelles'), reports_html)

	def test_topbar_logout_uses_csrf_protected_post(self):
		request = RequestFactory().get('/dashboard/')
		request.user = AnonymousUser()
		html = render_to_string(
			'includes/navigation/app_topbar.html',
			{
				'site_settings': None,
				'working_period': {'tenant_key': 'test', 'value': '2026-08'},
			},
			request=request,
		)

		self.assertIn(
			'<form method="post" action="/logout/" class="topbar-logout-form">',
			html,
		)
		self.assertIn('name="csrfmiddlewaretoken"', html)
		self.assertNotIn('href="/logout/"', html)

		response = self.client.post(reverse('logout'))

		self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

	def test_current_section_has_one_distinct_active_link(self):
		sections = (
			('/dashboard/', '/dashboard/'),
			('/facture/', '/facture/'),
			('/releve-bancaire/', '/releve-bancaire/'),
			('/paie/', '/paie/'),
			('/paie/employes/', '/paie/'),
			('/grand-livre/', '/rapports/'),
			('/paie/remises-mensuelles/', '/paie/'),
		)

		for current_path, active_href in sections:
			with self.subTest(current_path=current_path):
				request = RequestFactory().get(current_path)
				request.resolver_match = resolve(current_path)
				html = render_to_string(
					'includes/navigation/app_sidebar.html',
					request=request,
				)

				self.assertEqual(html.count('sidebar-link active'), 1)
				self.assertEqual(html.count('aria-current="page"'), 1)
				self.assertRegex(
					html,
					rf'href="{re.escape(active_href)}"\s+class="sidebar-link active"',
				)


class ReleveTemplateBehaviorTests(SimpleTestCase):
	def test_entry_submission_restores_processed_row_position(self):
		request = RequestFactory().get('/releve-bancaire/')
		request.user = AnonymousUser()
		request.resolver_match = resolve('/releve-bancaire/')

		html = render_to_string(
			'releves/index.html',
			{
				'groupes': [],
				'working_period': {'tenant_key': 'test', 'value': '2026-08'},
			},
			request=request,
		)

		self.assertIn("createEcritureForm.addEventListener('submit', saveReturnPosition)", html)
		self.assertIn("sessionStorage.setItem(returnPositionKey", html)
		self.assertIn("bootstrap.Tab.getOrCreateInstance(tabButton).show()", html)
		self.assertIn("window.scrollTo(0, targetTop", html)
		self.assertIn('compagnieSelect.required = companyRequired', html)
		self.assertIn('obligatoire pour CAP/CAR', html)


class FactureMultiTenantTests(TestCase):
	databases = "__all__"

	def _tenant_aliases(self):
		return [alias for alias in settings.DATABASES.keys() if alias != 'default']

	def _build_compte(self, alias, numero, libelle, no_total='A'):
		if isinstance(no_total, Total):
			total = no_total
		else:
			total, _ = Total.objects.using(alias).get_or_create(
				no_total=0,
				defaults={'desc': 'Total test'},
			)
		return Compte.objects.using(alias).create(numero=numero, libelle=libelle, no_total=total)

	def test_post_facture_is_saved_on_active_tenant_only(self):
		tenant_aliases = self._tenant_aliases()
		if len(tenant_aliases) < 2:
			self.skipTest("Ce test requiert au moins deux alias tenant configures.")

		active_alias = tenant_aliases[0]
		other_alias = tenant_aliases[1]

		# Setup central auth/access data.
		user_model = get_user_model()
		user = user_model.objects.create_user(username='mt_admin', password='Pass1234!')
		active_client = ClientDatabase.objects.create(
			slug='client-active-test',
			name='Client Active Test',
			db_alias=active_alias,
			is_active=True,
		)
		UserClientAccess.objects.create(user=user, client=active_client, is_default=True)

		# Setup active tenant accounting/config data.
		total = Total.objects.using(active_alias).create(no_total=0, desc='Total test')
		cap = self._build_compte(active_alias, 2150, 'Compte CAP', no_total=total)
		car = self._build_compte(active_alias, 1200, 'Compte CAR', no_total=total)
		tps_percue = self._build_compte(active_alias, 1250, 'TPS percue', no_total=total)
		tvq_percue = self._build_compte(active_alias, 1270, 'TVQ percue', no_total=total)
		tps_payee = self._build_compte(active_alias, 1240, 'TPS payee', no_total=total)
		tvq_payee = self._build_compte(active_alias, 1260, 'TVQ payee', no_total=total)
		fr_retard = self._build_compte(active_alias, 5865, 'Frais de retard', no_total=total)
		vente = self._build_compte(active_alias, 4100, 'Ventes', no_total=total)

		Setting.objects.using(active_alias).create(
			nom='Client Active',
			logo='images.png',
			adresse='1 rue Test',
			ville='Quebec',
			code_postal='G1G1G1',
			pays='Canada',
			phone='4180000000',
			email='active@example.com',
			fin_annee_jour=31,
			fin_annee_mois=12,
			cap=cap,
			car=car,
			compte_tps_percue=tps_percue,
			compte_tps_payee=tps_payee,
			compte_tvq_percue=tvq_percue,
			compte_tvq_payee=tvq_payee,
			compte_fr_retard=fr_retard,
		)

		compagnie = Compagnie.objects.using(active_alias).create(
			nom='Compagnie Test',
			logo='images.png',
			cap_ou_car=Compagnie.MODE_CAP,
		)

		before_active = Tr_desc.objects.using(active_alias).count()
		before_other = Tr_desc.objects.using(other_alias).count()

		self.client.force_login(user)
		session = self.client.session
		session[SESSION_CLIENT_ID_KEY] = active_client.id
		session[SESSION_CLIENT_ALIAS_KEY] = active_alias
		session.save()

		response = self.client.post(reverse('facture'), data={
			'action': 'add_tr_desc',
			'selected_company_id': str(compagnie.id),
			'editing_tr_desc_id': '',
			'facture_total': '100.00',
			'trdesc-date': '2026-06-01',
			'trdesc-description': 'FACT-TEST-MT',
			'detail-TOTAL_FORMS': '1',
			'detail-INITIAL_FORMS': '0',
			'detail-MIN_NUM_FORMS': '0',
			'detail-MAX_NUM_FORMS': '1000',
			'detail-0-compte': str(vente.pk),
			'detail-0-montant': '100.00',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(Tr_desc.objects.using(active_alias).count(), before_active + 1)
		self.assertEqual(Tr_desc.objects.using(other_alias).count(), before_other)

		created = Tr_desc.objects.using(active_alias).order_by('-id').first()
		self.assertIsNotNone(created)
		details = list(Tr_detail.objects.using(active_alias).filter(tr_desc=created).order_by('id'))
		self.assertGreaterEqual(len(details), 2)

		comptes = {d.compte_id: d.montant for d in details}
		self.assertIn(vente.pk, comptes)
		self.assertEqual(comptes[vente.pk], Decimal('100.00'))
		self.assertIn(cap.pk, comptes)
		self.assertEqual(comptes[cap.pk], Decimal('-100.00'))


class AccountingSqlViewsTests(TestCase):
	databases = "__all__"

	def setUp(self):
		self.alias = next(alias for alias in settings.DATABASES if alias != 'default')
		total = Total.objects.using(self.alias).create(no_total=0, desc='Total vues SQL')
		self.compte = Compte.objects.using(self.alias).create(
			numero=4999,
			libelle='Compte vues SQL',
			no_total=total,
		)
		compagnie = Compagnie.objects.using(self.alias).create(
			nom='Compagnie vues SQL',
			cap_ou_car=Compagnie.MODE_CAR,
		)
		source = Source.objects.using(self.alias).create(nom='Facture')
		transaction = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-VUE-1',
			date=date(2026, 7, 31),
			desc_ctb='Test des vues SQL',
			compagnie=compagnie,
			source=source,
		)
		self.detail = Tr_detail.objects.using(self.alias).create(
			tr_desc=transaction,
			compte=self.compte,
			montant=Decimal('125.50'),
		)

	def test_transaction_liste_exposes_debit_and_text_no_ej(self):
		row = TransactionListe.objects.using(self.alias).get(transaction_id=self.detail.id)

		self.assertEqual(row.no_ej, 'EJ-VUE-1')
		self.assertEqual(row.compte_numero, self.compte.numero)
		self.assertEqual(row.debit, Decimal('125.50'))
		self.assertEqual(row.credit, Decimal('0'))

	def test_releve_cap_account_requires_company(self):
		bank_account = Compte.objects.using(self.alias).create(
			numero=1105,
			libelle='Banque validation CAP',
			no_total=self.compte.no_total,
		)
		cap_account = Compte.objects.using(self.alias).create(
			numero=2105,
			libelle='CAP validation releve',
			no_total=self.compte.no_total,
		)
		Setting.objects.using(self.alias).create(
			nom='Test CAP relevé',
			adresse='1 rue Test',
			ville='Québec',
			code_postal='G1G 1G1',
			pays='Canada',
			phone='418-555-0100',
			email='cap-releve@example.com',
			cap=cap_account,
		)
		statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='CAP-RELEVE',
			type_compte='BANQUE',
			nom_affichage='Banque CAP',
			compte_comptable=bank_account,
		)
		statement = Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='cap-releve.csv',
			nom_institut='Banque test',
			no_compte='CAP-RELEVE',
			type_compte='BANQUE',
			date=date(2026, 8, 1),
			no_ligne='1',
			desc_releve='Paiement fournisseur CAP',
			retrait=Decimal('100.00'),
			solde=Decimal('0'),
		)
		request = RequestFactory().post('/releve-bancaire/', {
			'action': 'create_ecriture',
			'releve_id': str(statement.pk),
			'compagnie_id': '',
			'trdesc_releve-date': '2026-08-01',
			'trdesc_releve-desc_ctb': 'Paiement fournisseur CAP',
			'detail_releve-TOTAL_FORMS': '1',
			'detail_releve-INITIAL_FORMS': '0',
			'detail_releve-MIN_NUM_FORMS': '0',
			'detail_releve-MAX_NUM_FORMS': '1000',
			'detail_releve-0-compte': str(cap_account.pk),
			'detail_releve-0-montant': '100.00',
		})
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)

		captured = {}
		def capture_render(_request, _template, context):
			captured.update(context)
			return HttpResponse('invalid')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', side_effect=capture_render):
				response = releve_bancaire(request)
		finally:
			reset_current_tenant_alias(token)

		statement.refresh_from_db(using=self.alias)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(statement.ecriture_creee)
		self.assertFalse(Tr_desc.objects.using(self.alias).filter(desc_ctb='Paiement fournisseur CAP').exists())
		self.assertIn(
			"Une compagnie est obligatoire lorsqu'une ligne utilise le compte CAP ou CAR.",
			captured['errors'],
		)
		self.assertTrue(captured['open_releve_modal'])

	def test_journal_and_ledger_merge_company_into_description(self):
		negative_account = Compte.objects.using(self.alias).create(
			numero=5998,
			libelle='Compte solde negatif',
			no_total=self.compte.no_total,
		)
		negative_transaction = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-NEG',
			date=date(2026, 7, 31),
			desc_ctb='Solde negatif',
			compagnie=self.detail.tr_desc.compagnie,
			source=self.detail.tr_desc.source,
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=negative_transaction,
			compte=negative_account,
			montant=Decimal('-25.00'),
		)
		request = RequestFactory().get('/rapport/')
		request.user = AnonymousUser()
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)

		token = set_current_tenant_alias(self.alias)
		try:
			journal_response = journal_general(request)
			ledger_response = grand_livre(request)
		finally:
			reset_current_tenant_alias(token)

		expected_description = 'Compagnie vues SQL - Test des vues SQL'
		self.assertContains(journal_response, expected_description)
		self.assertContains(ledger_response, expected_description)
		for response in (journal_response, ledger_response):
			self.assertContains(response, '<aside class="sidebar">')
			self.assertContains(response, '<header class="app-topbar">')
		self.assertNotContains(ledger_response, '<th>Compagnie</th>', html=True)
		self.assertContains(ledger_response, accounting_amount(Decimal('-25.00')))
		self.assertNotContains(ledger_response, '-25,00')

	def test_facture_filters_transaction_liste(self):
		row = Facture.objects.using(self.alias).get(transaction_id=self.detail.id)

		self.assertEqual(row.source, 'Facture')
		self.assertEqual(row.no_ej, 'EJ-VUE-1')

	def test_company_created_by_non_expert_is_marked_yellow(self):
		user = get_user_model().objects.create_user(username='facture-user', password='pass1234')
		request = RequestFactory().post('/facture/', {
			'action': 'add_company',
			'company-nom': 'Compagnie utilisateur',
			'company-logo': 'images.png',
			'company-cap_ou_car': Compagnie.MODE_AUTRE,
		})
		request.user = user
		request.active_client_alias = self.alias

		token = set_current_tenant_alias(self.alias)
		try:
			response = facture(request)
			company = Compagnie.objects.get(nom='Compagnie utilisateur')

			page_request = RequestFactory().get('/facture/')
			page_request.user = user
			page_request.active_client_alias = self.alias
			SessionMiddleware(lambda req: None).process_request(page_request)
			page_response = facture(page_request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 302)
		self.assertTrue(company.created_by_non_expert)
		self.assertContains(page_response, 'data-company-created-by-non-expert="1"')

	def test_company_created_by_expert_is_not_marked_yellow(self):
		user = get_user_model().objects.create_superuser(
			username='facture-expert',
			password='pass1234',
			email='expert@example.com',
		)
		request = RequestFactory().post('/facture/', {
			'action': 'add_company',
			'company-nom': 'Compagnie expert',
			'company-logo': 'images.png',
			'company-cap_ou_car': Compagnie.MODE_AUTRE,
		})
		request.user = user
		request.active_client_alias = self.alias

		token = set_current_tenant_alias(self.alias)
		try:
			response = facture(request)
			company = Compagnie.objects.get(nom='Compagnie expert')
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 302)
		self.assertFalse(company.created_by_non_expert)

	def test_invoice_accounts_must_match_invoice_total(self):
		car_account = Compte.objects.using(self.alias).create(
			numero=1201,
			libelle='CAR test equilibre',
			no_total=self.compte.no_total,
		)
		Setting.objects.using(self.alias).create(
			nom='Configuration equilibre',
			adresse='1 rue Test',
			ville='Quebec',
			code_postal='G1G 1G1',
			pays='Canada',
			phone='4180000000',
			email='equilibre@example.com',
			car=car_account,
		)
		before_count = Tr_desc.objects.using(self.alias).count()
		request = RequestFactory().post('/facture/', {
			'action': 'add_tr_desc',
			'selected_company_id': str(self.detail.tr_desc.compagnie_id),
			'editing_tr_desc_id': '',
			'facture_total': '100.00',
			'trdesc-date': '2026-07-31',
			'trdesc-desc_ctb': 'FACT-DESEQUILIBREE',
			'detail-TOTAL_FORMS': '1',
			'detail-INITIAL_FORMS': '0',
			'detail-MIN_NUM_FORMS': '0',
			'detail-MAX_NUM_FORMS': '1000',
			'detail-0-compte': str(self.compte.pk),
			'detail-0-montant': '90.00',
		})
		captured = {}

		def capture_render(_request, _template, context):
			captured.update(context)
			return HttpResponse('invalid')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', side_effect=capture_render):
				response = facture(request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(Tr_desc.objects.using(self.alias).count(), before_count)
		self.assertIn(
			'La somme des comptes doit correspondre au total de la facture.',
			str(captured['tr_desc_form'].non_field_errors()),
		)

	def test_company_invoices_excludes_releve_entries(self):
		releve_source = Source.objects.using(self.alias).create(nom='Releve')
		releve_entry = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-REL-1',
			date=date(2026, 7, 30),
			desc_ctb='Ecriture de releve',
			compagnie=self.detail.tr_desc.compagnie,
			source=releve_source,
		)

		invoice_ids = list(
			_company_invoices_queryset(self.detail.tr_desc.compagnie)
			.using(self.alias)
			.values_list('id', flat=True)
		)

		self.assertIn(self.detail.tr_desc_id, invoice_ids)
		self.assertNotIn(releve_entry.id, invoice_ids)

	def test_delete_existing_invoice_removes_entry_and_details(self):
		transaction_id = self.detail.tr_desc_id
		request = RequestFactory().post('/facture/', {
			'action': 'delete_tr_desc',
			'selected_company_id': str(self.detail.tr_desc.compagnie_id),
			'editing_tr_desc_id': str(transaction_id),
		})

		token = set_current_tenant_alias(self.alias)
		try:
			response = facture(request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 302)
		self.assertFalse(Tr_desc.objects.using(self.alias).filter(pk=transaction_id).exists())
		self.assertFalse(Tr_detail.objects.using(self.alias).filter(pk=self.detail.pk).exists())

	def test_delete_invoice_rejects_mismatched_company(self):
		other_company = Compagnie.objects.using(self.alias).create(
			nom='Autre compagnie',
			cap_ou_car=Compagnie.MODE_CAP,
		)
		request = RequestFactory().post('/facture/', {
			'action': 'delete_tr_desc',
			'selected_company_id': str(other_company.pk),
			'editing_tr_desc_id': str(self.detail.tr_desc_id),
		})

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', return_value=HttpResponse('invalid')):
				response = facture(request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(Tr_desc.objects.using(self.alias).filter(pk=self.detail.tr_desc_id).exists())
		self.assertTrue(Tr_detail.objects.using(self.alias).filter(pk=self.detail.pk).exists())

	def test_delete_invoice_rejects_transmitted_tax_lines(self):
		report = RapportTaxes.objects.using(self.alias).create(
			annee=2026,
			mois=7,
			transmis_le=timezone.now(),
		)
		Tr_detail.objects.using(self.alias).filter(pk=self.detail.pk).update(rapport_taxes=report)
		request = RequestFactory().post('/facture/', {
			'action': 'delete_tr_desc',
			'selected_company_id': str(self.detail.tr_desc.compagnie_id),
			'editing_tr_desc_id': str(self.detail.tr_desc_id),
		})

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', return_value=HttpResponse('protected')):
				response = facture(request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(Tr_desc.objects.using(self.alias).filter(pk=self.detail.tr_desc_id).exists())
		self.assertTrue(Tr_detail.objects.using(self.alias).filter(pk=self.detail.pk).exists())

	def test_transmit_tax_report_posts_balanced_amounts_to_configured_accounts(self):
		tax_accounts = {
			'tps_percue': Compte.objects.using(self.alias).create(numero=2310, libelle='TPS percue', no_total=self.compte.no_total),
			'tps_payee': Compte.objects.using(self.alias).create(numero=2311, libelle='TPS payee', no_total=self.compte.no_total),
			'tvq_percue': Compte.objects.using(self.alias).create(numero=2320, libelle='TVQ percue', no_total=self.compte.no_total),
			'tvq_payee': Compte.objects.using(self.alias).create(numero=2321, libelle='TVQ payee', no_total=self.compte.no_total),
			'car': Compte.objects.using(self.alias).create(numero=1200, libelle='Taxes a recevoir', no_total=self.compte.no_total),
		}
		Setting.objects.using(self.alias).create(
			nom='Configuration taxes',
			adresse='1 rue Test',
			ville='Quebec',
			code_postal='G1G 1G1',
			pays='Canada',
			phone='4180000000',
			email='taxes@example.com',
			car=tax_accounts['car'],
			compte_tps_percue=tax_accounts['tps_percue'],
			compte_tps_payee=tax_accounts['tps_payee'],
			compte_tvq_percue=tax_accounts['tvq_percue'],
			compte_tvq_payee=tax_accounts['tvq_payee'],
		)
		tax_transaction = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-TAX-1',
			date=date(2026, 7, 15),
			desc_ctb='Facture avec taxes',
			compagnie=self.detail.tr_desc.compagnie,
			source=self.detail.tr_desc.source,
		)
		for account_name, amount in (
			('tps_percue', Decimal('-100.00')),
			('tps_payee', Decimal('40.00')),
			('tvq_percue', Decimal('-200.00')),
			('tvq_payee', Decimal('75.00')),
		):
			tax_detail = Tr_detail(
				tr_desc=tax_transaction,
				compte=tax_accounts[account_name],
				montant=amount,
			)
			tax_detail._state.db = self.alias
			self.assertTrue(tax_detail.is_tax_line(db_alias=self.alias))
			tax_detail.save(using=self.alias)

		report = RapportTaxes.objects.using(self.alias).get(annee=2026, mois=7)
		request = RequestFactory().post('/rapport-de-taxes/', {
			'action': 'transmit_report',
			'report_id': str(report.pk),
		})
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)
		set_working_period(request, '2026-07')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', return_value=HttpResponse('ok')):
				response = rapport_de_taxes(request)
		finally:
			reset_current_tenant_alias(token)

		report.refresh_from_db(using=self.alias)
		transmission_entries = list(
			Tr_desc.objects.using(self.alias)
			.filter(source__nom='Rapport de taxes')
		)
		posted_by_account = {}
		for entry in transmission_entries:
			entry_details = list(Tr_detail.objects.using(self.alias).filter(tr_desc=entry))
			entry_total = sum((detail.montant for detail in entry_details), Decimal('0.00'))
			self.assertEqual(entry_total, Decimal('0.00'))
			for detail in entry_details:
				posted_by_account[detail.compte_id] = posted_by_account.get(detail.compte_id, Decimal('0.00')) + detail.montant

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(report.transmis_le)
		self.assertEqual(len(transmission_entries), 2)
		self.assertEqual(posted_by_account, {
			tax_accounts['tps_percue'].pk: Decimal('100.00'),
			tax_accounts['tps_payee'].pk: Decimal('-40.00'),
			tax_accounts['tvq_percue'].pk: Decimal('200.00'),
			tax_accounts['tvq_payee'].pk: Decimal('-75.00'),
			tax_accounts['car'].pk: Decimal('-185.00'),
		})

		for account_name in ('tps_percue', 'tps_payee', 'tvq_percue', 'tvq_payee'):
			account_total = sum(
				Tr_detail.objects.using(self.alias)
				.filter(compte=tax_accounts[account_name])
				.values_list('montant', flat=True),
				Decimal('0.00'),
			)
			self.assertEqual(account_total, Decimal('0.00'))

	def test_solde_fin_includes_transactions(self):
		row = SoldeFin.objects.using(self.alias).get(compte_numero=self.compte)

		self.assertEqual(row.total_transactions, Decimal('125.50'))
		self.assertEqual(row.solde_final, Decimal('125.50'))

	def test_statement_withdrawal_is_stored_as_positive_magnitude(self):
		statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='APRIL-01',
			type_compte='CC',
			nom_affichage='Carte avril',
		)
		statement = Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='avril.csv',
			nom_institut='Banque test',
			no_compte='APRIL-01',
			type_compte='CC',
			date=date(2026, 4, 7),
			no_ligne='7',
			desc_releve='Retrait test',
			retrait=Decimal('-35.70'),
			solde=Decimal('0'),
		)

		statement.refresh_from_db(using=self.alias)
		self.assertEqual(statement.retrait, Decimal('35.70'))

	def test_similar_entry_endpoint_returns_applicable_details(self):
		bank_account = Compte.objects.using(self.alias).create(
			numero=1100,
			libelle='Banque recherche similaire',
			no_total=self.compte.no_total,
		)
		expense_account = Compte.objects.using(self.alias).create(
			numero=6100,
			libelle='Fournitures recherche similaire',
			no_total=self.compte.no_total,
		)
		statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='SIMILAR-01',
			type_compte='CC',
			nom_affichage='Carte recherche similaire',
			compte_comptable=bank_account,
		)
		historical_entry = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-SIM-1',
			date=date(2026, 6, 1),
			desc_ctb='Papeterie du centre',
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=historical_entry,
			compte=bank_account,
			montant=Decimal('-42.50'),
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=historical_entry,
			compte=expense_account,
			montant=Decimal('42.50'),
		)
		Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='historique-similaire.csv',
			nom_institut='Banque test',
			no_compte='SIMILAR-01',
			type_compte='CC',
			date=date(2026, 6, 1),
			no_ligne='1',
			desc_releve='PAPETERIE DU CENTRE',
			retrait=Decimal('42.50'),
			solde=Decimal('0'),
			ecriture_creee=True,
			ecriture_tr_desc=historical_entry,
		)
		old_entry = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-OLD',
			date=date(2024, 12, 31),
			desc_ctb='Papeterie trop ancienne',
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=old_entry,
			compte=bank_account,
			montant=Decimal('-42.50'),
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=old_entry,
			compte=expense_account,
			montant=Decimal('42.50'),
		)
		Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='historique-trop-ancien.csv',
			nom_institut='Banque test',
			no_compte='SIMILAR-01',
			type_compte='CC',
			date=date(2024, 12, 31),
			no_ligne='ancien',
			desc_releve='PAPETERIE DU CENTRE',
			retrait=Decimal('42.50'),
			solde=Decimal('0'),
			ecriture_creee=True,
			ecriture_tr_desc=old_entry,
		)
		current_statement = Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='courant-similaire.csv',
			nom_institut='Banque test',
			no_compte='SIMILAR-01',
			type_compte='CC',
			date=date(2026, 7, 1),
			no_ligne='2',
			desc_releve='PAPETERIE DU CENTRE',
			retrait=Decimal('42.50'),
			solde=Decimal('0'),
		)

		token = set_current_tenant_alias(self.alias)
		try:
			response = releve_ecriture_similaire(
				RequestFactory().get(f'/releves/similaire/{current_statement.pk}/'),
				current_statement.pk,
			)
		finally:
			reset_current_tenant_alias(token)

		payload = json.loads(response.content)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(payload['resultats'][0]['no_ej'], 'EJ-SIM-1')
		self.assertEqual(payload['resultats'][0]['details'], [{
			'compte_id': expense_account.pk,
			'compte_label': str(expense_account),
			'montant': '42.50',
		}])

		near_match_statement = Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='presque-similaire.csv',
			nom_institut='Banque test',
			no_compte='SIMILAR-01',
			type_compte='CC',
			date=date(2026, 7, 2),
			no_ligne='3',
			desc_releve='PAPETERIE DU CENTRE QC',
			retrait=Decimal('42.50'),
			solde=Decimal('0'),
		)
		token = set_current_tenant_alias(self.alias)
		try:
			near_match_response = releve_ecriture_similaire(
				RequestFactory().get(f'/releves/similaire/{near_match_statement.pk}/'),
				near_match_statement.pk,
			)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual(json.loads(near_match_response.content)['resultats'], [])

	def test_similar_entry_endpoint_includes_inter_statement_transfer(self):
		source_account = Compte.objects.using(self.alias).create(
			numero=1100,
			libelle='Banque EOP',
			no_total=self.compte.no_total,
		)
		target_account = Compte.objects.using(self.alias).create(
			numero=1110,
			libelle='Banque ET2',
			no_total=self.compte.no_total,
		)
		source_statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='EOP-SIMILAR',
			type_compte='BANQUE',
			nom_affichage='EOP',
			compte_comptable=source_account,
		)
		target_statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='ET2-SIMILAR',
			type_compte='BANQUE',
			nom_affichage='ET2',
			compte_comptable=target_account,
		)
		historical_entry = Tr_desc.objects.using(self.alias).create(
			no_ej='EJ-TRANSFER-1',
			date=date(2026, 5, 18),
			desc_ctb='Virement AccesD vers ET2',
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=historical_entry,
			compte=source_account,
			montant=Decimal('-500.00'),
		)
		Tr_detail.objects.using(self.alias).create(
			tr_desc=historical_entry,
			compte=target_account,
			montant=Decimal('500.00'),
		)
		for statement_account, line_number, description, withdrawal, deposit in (
			(source_statement_account, '1', 'Virement - AccesD Internet /a ET 2', Decimal('500.00'), None),
			(target_statement_account, '1', 'Virement - AccesD Internet /de EOP', None, Decimal('500.00')),
		):
			Releve.objects.using(self.alias).create(
				compte_releve=statement_account,
				fichier_source='virement-historique.csv',
				nom_institut='Banque test',
				no_compte=statement_account.no_compte,
				type_compte='BANQUE',
				date=date(2026, 5, 18),
				no_ligne=line_number,
				desc_releve=description,
				retrait=withdrawal,
				depot=deposit,
				solde=Decimal('0'),
				ecriture_creee=True,
				ecriture_tr_desc=historical_entry,
			)

		current_statement = Releve.objects.using(self.alias).create(
			compte_releve=source_statement_account,
			fichier_source='virement-courant-eop.csv',
			nom_institut='Banque test',
			no_compte=source_statement_account.no_compte,
			type_compte='BANQUE',
			date=date(2026, 5, 21),
			no_ligne='2',
			desc_releve='Virement - AccesD Internet /a ET 2',
			retrait=Decimal('250.00'),
			solde=Decimal('0'),
		)
		Releve.objects.using(self.alias).create(
			compte_releve=target_statement_account,
			fichier_source='virement-courant-et2.csv',
			nom_institut='Banque test',
			no_compte=target_statement_account.no_compte,
			type_compte='BANQUE',
			date=date(2026, 5, 21),
			no_ligne='2',
			desc_releve='Virement - AccesD Internet /de EOP',
			depot=Decimal('250.00'),
			solde=Decimal('0'),
		)

		token = set_current_tenant_alias(self.alias)
		try:
			response = releve_ecriture_similaire(
				RequestFactory().get(f'/releves/similaire/{current_statement.pk}/'),
				current_statement.pk,
			)
		finally:
			reset_current_tenant_alias(token)

		payload = json.loads(response.content)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(payload['resultats'][0]['no_ej'], 'EJ-TRANSFER-1')
		self.assertEqual(payload['resultats'][0]['details'][0]['compte_id'], target_account.pk)

	def test_existing_negative_withdrawal_reduces_credit_card_running_balance(self):
		statement_account = CompteReleve.objects.using(self.alias).create(
			no_compte='APRIL-02',
			type_compte='CC',
			nom_affichage='Carte avril existante',
			type_onglet='carte_credit',
		)
		statement = Releve.objects.using(self.alias).create(
			compte_releve=statement_account,
			fichier_source='avril-render.csv',
			nom_institut='Banque test',
			no_compte='APRIL-02',
			type_compte='CC',
			date=date(2026, 4, 7),
			no_ligne='7',
			desc_releve='Retrait Render',
			retrait=Decimal('35.70'),
			solde=Decimal('0'),
		)
		Releve.objects.using(self.alias).filter(pk=statement.pk).update(retrait=Decimal('-35.70'))

		request = RequestFactory().get('/releve-bancaire/')
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)
		set_working_period(request, '2026-04')
		captured = {}

		def capture_render(_request, _template, context):
			captured.update(context)
			return HttpResponse('ok')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', side_effect=capture_render):
				response = releve_bancaire(request)
		finally:
			reset_current_tenant_alias(token)

		row = captured['releves_par_compte'][statement_account.pk][0]
		self.assertEqual(response.status_code, 200)
		self.assertEqual(row.retrait, Decimal('-35.70'))
		self.assertEqual(row.solde, Decimal('-35.70'))

	def test_grand_livre_displays_opening_balance_only_in_solde(self):
		bilan = Compte.objects.using(self.alias).create(
			numero=1200,
			libelle='Compte bilan vues SQL',
			no_total=self.compte.no_total,
		)
		SoldeAuxLivres.objects.using(self.alias).create(
			compte=bilan,
			solde_depart=Decimal('75.00'),
		)
		captured = {}

		def capture_render(_request, _template, context):
			captured.update(context)
			return HttpResponse('ok')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views._fetch_grand_livre_from_sql_view', side_effect=DatabaseError):
				with patch('facture.views.render', side_effect=capture_render):
					response = grand_livre(RequestFactory().get('/grand-livre/'))
		finally:
			reset_current_tenant_alias(token)

		opening_row = next(
			row
			for block in captured['comptes']
			for row in block['entries']
			if row.get('is_solde_depart') and block['compte'].pk == bilan.pk
		)
		expected_total = sum(
			(block['solde'] for block in captured['comptes']),
			Decimal('0'),
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(opening_row['debit'], Decimal('0'))
		self.assertEqual(opening_row['credit'], Decimal('0'))
		self.assertEqual(opening_row['solde'], Decimal('75.00'))
		self.assertEqual(captured['grand_total_solde'], expected_total)
		self.assertNotEqual(
			captured['grand_total_solde'],
			captured['grand_total_debit'] - captured['grand_total_credit'],
		)

	def test_dashboard_uses_tenant_working_period(self):
		captured = {}

		def capture_render(_request, _template, context):
			captured.update(context)
			return HttpResponse('ok')

		request = RequestFactory().get('/dashboard/')
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)
		set_working_period(request, '2025-09')

		token = set_current_tenant_alias(self.alias)
		try:
			with patch('facture.views.render', side_effect=capture_render):
				response = dashboard(request)
		finally:
			reset_current_tenant_alias(token)

		self.assertEqual((captured['mois'], captured['annee']), (9, 2025))
		self.assertNotIn('dashboard_mois', response.cookies)
		self.assertNotIn('dashboard_annee', response.cookies)

	def test_update_working_period_is_tenant_scoped_and_rejects_external_redirect(self):
		request = RequestFactory(HTTP_HOST='localhost').post('/working-period/', {
			'period': '2024-10',
			'next': 'https://example.com/escape',
		})
		request.user = get_user_model()(username='period-user')
		request.active_client_alias = self.alias
		SessionMiddleware(lambda req: None).process_request(request)

		response = update_working_period(request)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, '/dashboard/')
		self.assertEqual(request.session['working_periods'][self.alias], '2024-10')
		cookie_name = f'working_period_{self.alias}'
		self.assertEqual(response.cookies[cookie_name].value, '2024-10')
		self.assertEqual(response.cookies[cookie_name]['max-age'], 31536000)
