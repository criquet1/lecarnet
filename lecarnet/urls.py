"""
URL configuration for lecarnet project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def bonjour_page(request):
    return HttpResponse('<h1>Bonjour</h1>')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('bonjour/', bonjour_page, name='bonjour_page'),
    path('tenant/', include('tenancy.urls')),
    path('comptes/', include('compte.urls')),
    path('paie/', include('paie.urls')),
    path('', include('facture.urls')),
]

if settings.DEBUG:
    # En local seulement : sert les fichiers de media/ (logos prives, etc.).
    # En production, whitenoise ne sert que static/ -- voir le chantier
    # "logos par tenant" pour la vue protegee prevue pour la prod.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
