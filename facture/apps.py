from django.apps import AppConfig


class FactureConfig(AppConfig):
    name = 'facture'
    
    def ready(self):
        """Configure l'admin Django pour afficher le client actif."""
        from django.contrib.admin import AdminSite
        
        # Sauvegarder la méthode originale
        original_each_context = AdminSite.each_context
        
        # Créer une nouvelle méthode qui inclut le client actif
        def each_context_with_client(self, request):
            context = original_each_context(self, request)
            context['active_client'] = getattr(request, 'active_client', None)
            return context
        
        # Remplacer la méthode sur la classe
        AdminSite.each_context = each_context_with_client
