from django.contrib import admin
from django.contrib.admin import AdminSite
from django.urls import path
from django.shortcuts import render
from django.db.models import Count, Sum
from apps.subscriptions.models import OrganizationSubscription, SubscriptionInvoice
from apps.teams.models import Organization
from apps.users.models import CustomUser

class BillmunshiAdminSite(AdminSite):
    site_header = 'Billmunshi Administration'
    site_title = 'Billmunshi Admin'
    index_title = 'Welcome to Billmunshi Administration'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('dashboard/', self.admin_view(self.dashboard_view), name='dashboard'),
        ]
        return custom_urls + urls

    def dashboard_view(self, request):
        """Custom admin dashboard with metrics"""
        context = {
            'title': 'Dashboard',
            'total_users': CustomUser.objects.count(),
            'total_organizations': Organization.objects.count(),
            'active_subscriptions': OrganizationSubscription.objects.filter(
                status__in=['trial', 'active']
            ).count(),
            'total_revenue': SubscriptionInvoice.objects.filter(
                status='paid'
            ).aggregate(total=Sum('total_amount'))['total'] or 0,
            'recent_signups': CustomUser.objects.order_by('-date_joined')[:5],
            'recent_subscriptions': OrganizationSubscription.objects.order_by('-created_at')[:5],
        }
        return render(request, 'admin/dashboard.html', context)

# Replace the default admin site
admin_site = BillmunshiAdminSite(name='admin')