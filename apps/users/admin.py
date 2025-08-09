from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.contrib.auth import get_user_model

from .models import (
    UserPreference,
    UserActivity,
    UserSession,
    UserNotification
)

User = get_user_model()


class UserPreferenceInline(admin.StackedInline):
    model = UserPreference
    can_delete = False
    extra = 0
    fields = [
        'default_organization', 'dashboard_layout', 'notification_frequency',
        'default_api_format', 'profile_visibility', 'theme'
    ]


class UserActivityInline(admin.TabularInline):
    model = UserActivity
    extra = 0
    readonly_fields = ['action', 'description', 'ip_address', 'created_at']
    fields = ['action', 'description', 'organization', 'ip_address', 'created_at']
    ordering = ['-created_at']

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    Enhanced admin for CustomUser model
    """
    list_display = [
        'email', 'username', 'get_display_name', 'is_verified_display',
        'is_onboarded_display', 'total_organizations_display',
        'last_activity_display', 'is_active', 'date_joined'
    ]
    list_filter = [
        'is_active', 'is_staff', 'is_superuser', 'is_verified',
        'is_onboarded', 'two_factor_enabled', 'language', 'date_joined'
    ]
    search_fields = ['email', 'username', 'first_name', 'last_name', 'phone']
    readonly_fields = [
        'date_joined', 'last_login', 'avatar_url', 'gravatar_id',
        'has_verified_email', 'total_organizations', 'owned_organizations_count',
        'last_activity_at', 'last_login_ip'
    ]

    # Add custom fields to existing UserAdmin fieldsets
    fieldsets = UserAdmin.fieldsets + (
        ('Profile Information', {
            'fields': (
                'avatar', 'avatar_url', 'phone', 'bio', 'location', 'website'
            )
        }),
        ('Preferences', {
            'fields': (
                'timezone', 'language', 'email_notifications', 'marketing_emails'
            )
        }),
        ('Status', {
            'fields': (
                'is_verified', 'is_onboarded', 'two_factor_enabled'
            )
        }),
        ('Activity Tracking', {
            'fields': (
                'last_activity_at', 'last_login_ip'
            ),
            'classes': ('collapse',)
        }),
        ('Statistics', {
            'fields': (
                'total_organizations', 'owned_organizations_count', 'has_verified_email'
            ),
            'classes': ('collapse',)
        }),
    )

    inlines = [UserPreferenceInline, UserActivityInline]

    def is_verified_display(self, obj):
        if obj.is_verified:
            return format_html('<span style="color: green;">✓ Verified</span>')
        return format_html('<span style="color: red;">✗ Not Verified</span>')

    is_verified_display.short_description = 'Email Verified'

    def is_onboarded_display(self, obj):
        if obj.is_onboarded:
            return format_html('<span style="color: green;">✓ Complete</span>')
        return format_html('<span style="color: orange;">✗ Pending</span>')

    is_onboarded_display.short_description = 'Onboarding'

    def total_organizations_display(self, obj):
        count = obj.total_organizations
        if count > 0:
            url = reverse('admin:teams_organizationmember_changelist') + f'?user__id={obj.id}'
            return format_html('<a href="{}">{} orgs</a>', url, count)
        return '0 orgs'

    total_organizations_display.short_description = 'Organizations'

    def last_activity_display(self, obj):
        if obj.last_activity_at:
            return obj.last_activity_at.strftime('%Y-%m-%d %H:%M')
        return 'Never'

    last_activity_display.short_description = 'Last Activity'

    def save_model(self, request, obj, form, change):
        """Custom save logic"""
        super().save_model(request, obj, form, change)

        # Create user preferences if they don't exist
        if not hasattr(obj, 'preferences'):
            UserPreference.objects.create(user=obj)


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'default_organization', 'dashboard_layout',
        'notification_frequency', 'theme', 'updated_at'
    ]
    list_filter = [
        'dashboard_layout', 'notification_frequency', 'default_api_format',
        'profile_visibility', 'theme'
    ]
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    autocomplete_fields = ['user', 'default_organization']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Dashboard Preferences', {
            'fields': ('default_organization', 'dashboard_layout', 'theme')
        }),
        ('Notification Settings', {
            'fields': ('notification_frequency',)
        }),
        ('API Preferences', {
            'fields': ('default_api_format',)
        }),
        ('Privacy Settings', {
            'fields': ('profile_visibility',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        return obj.user.get_display_name()

    user_display.short_description = 'User'


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'action_display', 'organization',
        'ip_address', 'created_at'
    ]
    list_filter = ['action', 'created_at', 'organization']
    search_fields = [
        'user__email', 'user__first_name', 'user__last_name',
        'description', 'ip_address'
    ]
    readonly_fields = ['created_at', 'updated_at']
    autocomplete_fields = ['user', 'organization']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Activity Details', {
            'fields': ('user', 'action', 'description')
        }),
        ('Context', {
            'fields': ('organization', 'ip_address', 'user_agent')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        return obj.user.get_display_name()

    user_display.short_description = 'User'

    def action_display(self, obj):
        return obj.get_action_display()

    action_display.short_description = 'Action'

    def has_add_permission(self, request):
        """Disable manual addition of activities"""
        return False

    def has_change_permission(self, request, obj=None):
        """Make activities read-only"""
        return False


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'session_key_short', 'ip_address',
        'browser_info', 'is_active_display', 'last_activity', 'expires_at'
    ]
    list_filter = ['is_active', 'created_at', 'expires_at', 'country']
    search_fields = [
        'user__email', 'user__first_name', 'user__last_name',
        'session_key', 'ip_address', 'user_agent'
    ]
    readonly_fields = ['session_key', 'created_at', 'updated_at', 'browser_info']
    autocomplete_fields = ['user']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Session Details', {
            'fields': ('user', 'session_key', 'is_active')
        }),
        ('Client Information', {
            'fields': ('ip_address', 'user_agent', 'browser_info')
        }),
        ('Location', {
            'fields': ('country', 'city')
        }),
        ('Timing', {
            'fields': ('last_activity', 'expires_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        return obj.user.get_display_name()

    user_display.short_description = 'User'

    def session_key_short(self, obj):
        return f"{obj.session_key[:8]}..."

    session_key_short.short_description = 'Session Key'

    def is_active_display(self, obj):
        if obj.is_active and not obj.is_expired:
            return format_html('<span style="color: green;">✓ Active</span>')
        elif obj.is_expired:
            return format_html('<span style="color: red;">✗ Expired</span>')
        else:
            return format_html('<span style="color: orange;">✗ Inactive</span>')

    is_active_display.short_description = 'Status'

    def browser_info(self, obj):
        """Extract browser info from user agent"""
        user_agent = obj.user_agent.lower()

        if 'chrome' in user_agent:
            browser = 'Chrome'
        elif 'firefox' in user_agent:
            browser = 'Firefox'
        elif 'safari' in user_agent:
            browser = 'Safari'
        elif 'edge' in user_agent:
            browser = 'Edge'
        else:
            browser = 'Unknown'

        if 'mobile' in user_agent:
            device = 'Mobile'
        elif 'tablet' in user_agent:
            device = 'Tablet'
        else:
            device = 'Desktop'

        return f"{browser} on {device}"

    browser_info.short_description = 'Browser/Device'

    actions = ['terminate_sessions']

    def terminate_sessions(self, request, queryset):
        """Terminate selected sessions"""
        count = 0
        for session in queryset:
            if session.is_active:
                session.terminate()
                count += 1

        self.message_user(
            request,
            f'Successfully terminated {count} sessions.'
        )

    terminate_sessions.short_description = 'Terminate selected sessions'


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'title', 'notification_type', 'is_read_display',
        'organization', 'created_at'
    ]
    list_filter = [
        'notification_type', 'is_read', 'created_at', 'organization'
    ]
    search_fields = [
        'user__email', 'user__first_name', 'user__last_name',
        'title', 'message'
    ]
    readonly_fields = ['read_at', 'created_at', 'updated_at']
    autocomplete_fields = ['user', 'organization']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Notification Details', {
            'fields': ('user', 'title', 'message', 'notification_type')
        }),
        ('Status', {
            'fields': ('is_read', 'read_at')
        }),
        ('Action', {
            'fields': ('action_url', 'action_text')
        }),
        ('Context', {
            'fields': ('organization',)
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        return obj.user.get_display_name()

    user_display.short_description = 'User'

    def is_read_display(self, obj):
        if obj.is_read:
            return format_html('<span style="color: green;">✓ Read</span>')
        return format_html('<span style="color: orange;">✗ Unread</span>')

    is_read_display.short_description = 'Status'

    actions = ['mark_as_read', 'mark_as_unread']

    def mark_as_read(self, request, queryset):
        """Mark selected notifications as read"""
        from django.utils import timezone

        count = queryset.filter(is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )

        self.message_user(
            request,
            f'Successfully marked {count} notifications as read.'
        )

    mark_as_read.short_description = 'Mark selected notifications as read'

    def mark_as_unread(self, request, queryset):
        """Mark selected notifications as unread"""
        count = queryset.filter(is_read=True).update(
            is_read=False,
            read_at=None
        )

        self.message_user(
            request,
            f'Successfully marked {count} notifications as unread.'
        )

    mark_as_unread.short_description = 'Mark selected notifications as unread'


# Customize admin site header
admin.site.site_header = 'Billmunshi User Administration'
admin.site.site_title = 'Billmunshi Users'
admin.site.index_title = 'User Management'