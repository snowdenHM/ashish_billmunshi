from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import (
    Organization,
    OrganizationMember,
    Role,
    Invitation,
    OrganizationAPIKey,
    APIKeyUsageLog
)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'description', 'can_manage_organization',
        'can_manage_members', 'can_manage_api_keys',
        'can_view_analytics', 'can_manage_billing'
    ]
    list_filter = [
        'can_manage_organization', 'can_manage_members',
        'can_manage_api_keys', 'can_view_analytics', 'can_manage_billing'
    ]
    search_fields = ['name', 'description']
    readonly_fields = ['name']  # Prevent changing role names


class OrganizationMemberInline(admin.TabularInline):
    model = OrganizationMember
    extra = 0
    fields = ['user', 'role', 'is_active', 'joined_at', 'invited_by']
    readonly_fields = ['joined_at']
    autocomplete_fields = ['user', 'invited_by']


class OrganizationAPIKeyInline(admin.TabularInline):
    model = OrganizationAPIKey
    extra = 0
    fields = ['name', 'masked_key', 'is_active', 'usage_count', 'created_by', 'created_at']
    readonly_fields = ['masked_key', 'usage_count', 'created_at']


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'slug', 'owner', 'member_count_display',
        'api_key_count_display', 'is_active', 'created_at'
    ]
    list_filter = ['is_active', 'created_at', 'max_users']
    search_fields = ['name', 'slug', 'owner__email', 'description']
    readonly_fields = ['slug', 'member_count', 'api_key_count', 'created_at', 'updated_at']
    autocomplete_fields = ['owner']
    inlines = [OrganizationMemberInline, OrganizationAPIKeyInline]

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'description', 'logo')
        }),
        ('Contact Information', {
            'fields': ('website', 'phone', 'address')
        }),
        ('Settings', {
            'fields': ('owner', 'is_active', 'max_users')
        }),
        ('Statistics', {
            'fields': ('member_count', 'api_key_count'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def member_count_display(self, obj):
        count = obj.member_count
        url = reverse('admin:teams_organizationmember_changelist') + f'?organization__id={obj.id}'
        return format_html('<a href="{}">{} members</a>', url, count)

    member_count_display.short_description = 'Members'

    def api_key_count_display(self, obj):
        count = obj.api_key_count
        url = reverse('admin:teams_organizationapikey_changelist') + f'?organization__id={obj.id}'
        return format_html('<a href="{}">{} API keys</a>', url, count)

    api_key_count_display.short_description = 'API Keys'


@admin.register(OrganizationMember)
class OrganizationMemberAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'organization', 'role', 'is_active',
        'joined_at', 'invited_by_display'
    ]
    list_filter = ['role', 'is_active', 'joined_at', 'organization']
    search_fields = [
        'user__email', 'user__first_name', 'user__last_name',
        'organization__name', 'role__name'
    ]
    readonly_fields = ['joined_at', 'created_at', 'updated_at']
    autocomplete_fields = ['user', 'organization', 'invited_by']

    fieldsets = (
        ('Membership Details', {
            'fields': ('organization', 'user', 'role', 'is_active')
        }),
        ('Additional Information', {
            'fields': ('invited_by', 'joined_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        return f"{obj.user.get_display_name()} ({obj.user.email})"

    user_display.short_description = 'User'

    def invited_by_display(self, obj):
        if obj.invited_by:
            return f"{obj.invited_by.get_display_name()}"
        return "-"

    invited_by_display.short_description = 'Invited By'


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = [
        'email', 'organization', 'role', 'status',
        'invited_by_display', 'expires_at', 'is_expired_display'
    ]
    list_filter = ['status', 'role', 'created_at', 'expires_at', 'organization']
    search_fields = [
        'email', 'organization__name', 'invited_by__email',
        'invited_by__first_name', 'invited_by__last_name'
    ]
    readonly_fields = ['token', 'is_expired', 'created_at', 'updated_at']
    autocomplete_fields = ['organization', 'role', 'invited_by', 'user']

    fieldsets = (
        ('Invitation Details', {
            'fields': ('organization', 'email', 'role', 'status')
        }),
        ('Invitation Settings', {
            'fields': ('token', 'expires_at', 'is_expired')
        }),
        ('User Information', {
            'fields': ('invited_by', 'user')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def invited_by_display(self, obj):
        return f"{obj.invited_by.get_display_name()}"

    invited_by_display.short_description = 'Invited By'

    def is_expired_display(self, obj):
        if obj.is_expired:
            return format_html('<span style="color: red;">Yes</span>')
        return format_html('<span style="color: green;">No</span>')

    is_expired_display.short_description = 'Expired'


@admin.register(OrganizationAPIKey)
class OrganizationAPIKeyAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'organization', 'masked_key', 'is_active',
        'usage_count', 'last_used_at', 'created_by_display'
    ]
    list_filter = [
        'is_active', 'created_at', 'last_used_at',
        'organization', 'rate_limit_per_hour'
    ]
    search_fields = [
        'name', 'organization__name', 'created_by__email',
        'created_by__first_name', 'created_by__last_name'
    ]
    readonly_fields = [
        'key', 'prefix', 'masked_key', 'usage_count',
        'last_used_at', 'is_expired', 'created_at', 'updated_at'
    ]
    autocomplete_fields = ['organization', 'created_by']

    fieldsets = (
        ('API Key Details', {
            'fields': ('organization', 'name', 'key', 'prefix', 'masked_key')
        }),
        ('Status & Usage', {
            'fields': ('is_active', 'usage_count', 'last_used_at', 'is_expired')
        }),
        ('Rate Limiting', {
            'fields': ('rate_limit_per_hour', 'rate_limit_per_day')
        }),
        ('Security', {
            'fields': ('allowed_ips', 'expires_at')
        }),
        ('Meta Information', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def created_by_display(self, obj):
        return f"{obj.created_by.get_display_name()}"

    created_by_display.short_description = 'Created By'

    def save_model(self, request, obj, form, change):
        """Generate API key if creating new instance"""
        if not change:  # Creating new object
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class APIKeyUsageLogAdmin(admin.ModelAdmin):
    list_display = [
        'api_key_display', 'endpoint', 'method', 'status_code',
        'ip_address', 'response_time_ms', 'created_at'
    ]
    list_filter = [
        'method', 'status_code', 'created_at',
        'api_key__organization', 'api_key__name'
    ]
    search_fields = [
        'api_key__name', 'api_key__organization__name',
        'endpoint', 'ip_address', 'user_agent'
    ]
    readonly_fields = ['created_at', 'updated_at']
    autocomplete_fields = ['api_key']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Request Details', {
            'fields': ('api_key', 'endpoint', 'method', 'status_code')
        }),
        ('Client Information', {
            'fields': ('ip_address', 'user_agent')
        }),
        ('Performance', {
            'fields': ('response_time_ms',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def api_key_display(self, obj):
        return f"{obj.api_key.organization.name} - {obj.api_key.name}"

    api_key_display.short_description = 'API Key'

    def has_add_permission(self, request):
        """Disable manual addition of usage logs"""
        return False

    def has_change_permission(self, request, obj=None):
        """Make usage logs read-only"""
        return False


admin.site.register(APIKeyUsageLog, APIKeyUsageLogAdmin)

# Customize admin site
admin.site.site_header = 'Billmunshi Administration'
admin.site.site_title = 'Billmunshi Admin'
admin.site.index_title = 'Welcome to Billmunshi Administration'