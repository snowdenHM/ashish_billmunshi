from drf_spectacular.extensions import OpenApiAuthenticationExtension
from apps.api.permissions import HasUserAPIKey


class HasUserAPIKeyExtension(OpenApiAuthenticationExtension):
    target_class = HasUserAPIKey
    name = 'ApiKeyAuth'
    priority = -1

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-API-KEY',
            'description': 'API key authentication. Use your organization API key in the X-API-KEY header.'
        }