from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from facture.models import Compagnie, Tr_desc
from tenancy.db_context import reset_current_tenant_alias, set_current_tenant_alias

from .models import Compte, Setting, Total
from .views import transactions_page


class TransactionsPageTests(TestCase):
	databases = "__all__"

	def setUp(self):
		self.alias = next(alias for alias in settings.DATABASES if alias != 'default')
		total = Total.objects.using(self.alias).create(no_total=0, desc='Total test')
		self.cap = Compte.objects.using(self.alias).create(
			numero=2100,
			libelle='Compte CAP',
			no_total=total,
		)
		self.car = Compte.objects.using(self.alias).create(
			numero=2200,
			libelle='Compte CAR',
			no_total=total,
		)
		self.expense = Compte.objects.using(self.alias).create(
			numero=5100,
			libelle='Depense test',
			no_total=total,
		)
		Setting.objects.using(self.alias).create(
			nom='Client test',
			adresse='1 rue Test',
			ville='Quebec',
			code_postal='G1G 1G1',
			pays='Canada',
			phone='4180000000',
			email='test@example.com',
			cap=self.cap,
			car=self.car,
		)
		self.company = Compagnie.objects.using(self.alias).create(
			nom='Compagnie test CAP',
			cap_ou_car=Compagnie.MODE_CAP,
		)
		self.user = get_user_model().objects.create_superuser(
			username='expert_transactions',
			password='Pass1234!',
			email='expert@example.com',
		)

	def _build_request(self, company_id='', account=None):
		account = account or self.cap
		request = RequestFactory().post('/comptes/transactions/', data={
			'date_select': '2026-07-31',
			'compagnie': company_id,
			'description': 'Ecriture CAP',
			'source': 'Manuel',
			'comptes_comptables[]': [str(self.expense.pk), str(account.pk)],
			'montant_debit[]': ['100.00', ''],
			'montant_credit[]': ['', '100.00'],
		})
		request.user = self.user
		SessionMiddleware(lambda req: None).process_request(request)
		request.session.save()
		request._messages = FallbackStorage(request)
		return request

	def test_cap_and_car_accounts_require_company(self):
		for account in (self.cap, self.car):
			with self.subTest(account=account.pk):
				request = self._build_request(account=account)

				token = set_current_tenant_alias(self.alias)
				try:
					with patch('compte.views.render', return_value=HttpResponse('invalid')):
						response = transactions_page(request)
				finally:
					reset_current_tenant_alias(token)

				messages = [str(message) for message in get_messages(request)]
				self.assertEqual(response.status_code, 200)
				self.assertEqual(Tr_desc.objects.using(self.alias).count(), 0)
				self.assertIn(
					"Une compagnie est obligatoire lorsqu'une ligne utilise le compte CAP ou CAR.",
					messages,
				)

	def test_cap_account_accepts_selected_company(self):
		request = self._build_request(str(self.company.pk))

		token = set_current_tenant_alias(self.alias)
		try:
			response = transactions_page(request)
		finally:
			reset_current_tenant_alias(token)

		transaction = Tr_desc.objects.using(self.alias).get()
		self.assertEqual(response.status_code, 302)
		self.assertEqual(transaction.compagnie_id, self.company.pk)

# Create your tests here.
