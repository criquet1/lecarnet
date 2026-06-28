from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from . import views


urlpatterns = [
    path('login/', LoginView.as_view(template_name='registration/login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('', login_required(views.index), name='accueil'),
    path('facture/', login_required(views.facture), name='facture'),
    path('releve-bancaire/', login_required(views.releve_bancaire), name='releve_bancaire'),
    path('journal-general/', login_required(views.journal_general), name='journal_general'),
    path('grand-livre/', login_required(views.grand_livre), name='grand_livre'),
    path('balance-de-verification/', login_required(views.balance_de_verification), name='balance_de_verification'),
    path('compte-a-payer/', login_required(views.compte_a_payer), name='compte_a_payer'),
    path('compte-a-recevoir/', login_required(views.compte_a_recevoir), name='compte_a_recevoir'),
    path('rapport-de-taxes/', login_required(views.rapport_de_taxes), name='rapport_de_taxes'),
]
