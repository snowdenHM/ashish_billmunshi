from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import extend_schema_serializer
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import serializers


class APIKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    """
    Custom authentication scheme for API Key authentication
    """
    target_class = 'apps.api.permissions.HasUserAPIKey'
    name = 'ApiKeyAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'API Key authentication using Bearer token format: "Bearer your-api-key"'
        }


def filter_schema_apis(result, generator, request, public):
    """
    Filter schema APIs based on user permissions and API access
    """
    # Don't filter anything in public schema
    if public:
        return result

    # Filter out admin-only endpoints for non-staff users
    if request and request.user.is_authenticated and not request.user.is_staff:
        filtered_paths = {}
        for path, methods in result.get('paths', {}).items():
            # Skip admin-only paths
            if '/admin/' in path:
                continue

            # Skip staff-only endpoints
            staff_only_endpoints = [
                '/api/subscriptions/analytics/',
                '/api/users/admin/',
            ]

            if any(endpoint in path for endpoint in staff_only_endpoints):
                continue

            filtered_paths[path] = methods

        result['paths'] = filtered_paths

    return result


class APIErrorSerializer(serializers.Serializer):
    """
    Standard API error response serializer
    """
    error = serializers.CharField(help_text="Error message")
    message = serializers.CharField(help_text="Detailed error description", required=False)
    code = serializers.CharField(help_text="Error code", required=False)
    details = serializers.DictField(help_text="Additional error details", required=False)


class APISuccessSerializer(serializers.Serializer):
    """
    Standard API success response serializer
    """
    message = serializers.CharField(help_text="Success message")
    data = serializers.DictField(help_text="Response data", required=False)


class PaginatedResponseSerializer(serializers.Serializer):
    """
    Standard paginated response serializer
    """
    count = serializers.IntegerField(help_text="Total number of items")
    next = serializers.URLField(help_text="Next page URL", allow_null=True)
    previous = serializers.URLField(help_text="Previous page URL", allow_null=True)
    results = serializers.ListField(help_text="List of items")


@extend_schema_serializer(
    examples=[
        {
            'error': 'Authentication failed',
            'message': 'Invalid API key provided',
            'code': 'INVALID_API_KEY'
        }
    ]
)
class AuthenticationErrorSerializer(APIErrorSerializer):
    """
    Authentication error response serializer
    """
    pass


@extend_schema_serializer(
    examples=[
        {
            'error': 'Permission denied',
            'message': 'You do not have permission to perform this action',
            'code': 'PERMISSION_DENIED'
        }
    ]
)
class PermissionErrorSerializer(APIErrorSerializer):
    """
    Permission error response serializer
    """
    pass


@extend_schema_serializer(
    examples=[
        {
            'error': 'Rate limit exceeded',
            'message': 'Too many requests. Please try again later.',
            'code': 'RATE_LIMIT_EXCEEDED',
            'details': {
                'limit': 1000,
                'window': 'hour',
                'retry_after': 3600
            }
        }
    ]
)
class RateLimitErrorSerializer(APIErrorSerializer):
    """
    Rate limit error response serializer
    """
    pass


@extend_schema_serializer(
    examples=[
        {
            'error': 'Validation failed',
            'message': 'The provided data is invalid',
            'code': 'VALIDATION_ERROR',
            'details': {
                'email': ['This field is required.'],
                'name': ['Ensure this field has at most 100 characters.']
            }
        }
    ]
)
class ValidationErrorSerializer(APIErrorSerializer):
    """
    Validation error response serializer
    """
    pass


class CustomAutoSchema(AutoSchema):
    """
    Custom schema generator with additional features
    """

    def get_operation_id(self):
        """Generate operation ID for API endpoints"""
        operation_id = super().get_operation_id()

        # Add prefixes for better organization
        if 'teams' in self.path:
            operation_id = f"teams_{operation_id}"
        elif 'users' in self.path:
            operation_id = f"users_{operation_id}"
        elif 'subscriptions' in self.path:
            operation_id = f"subscriptions_{operation_id}"

        return operation_id

    def get_tags(self):
        """Generate tags for API endpoints"""
        tags = super().get_tags()

        # Add additional tags based on path
        if 'organizations' in self.path:
            tags.append('Organizations')
        elif 'api-keys' in self.path:
            tags.append('API Keys')
        elif 'invitations' in self.path:
            tags.append('Invitations')
        elif 'subscriptions' in self.path:
            tags.append('Billing')
        elif 'users' in self.path:
            tags.append('User Management')

        return tags

    def get_responses(self):
        """Add common error responses to all endpoints"""
        responses = super().get_responses()

        # Add common error responses
        if self.method.lower() != 'options':
            responses['400'] = {
                'description': 'Bad Request',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/ValidationError'}
                    }
                }
            }

            responses['401'] = {
                'description': 'Authentication Failed',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/AuthenticationError'}
                    }
                }
            }

            responses['403'] = {
                'description': 'Permission Denied',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/PermissionError'}
                    }
                }
            }

            responses['429'] = {
                'description': 'Rate Limit Exceeded',
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/RateLimitError'}
                    }
                }
            }

        return responses