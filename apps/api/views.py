from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from drf_spectacular.utils import extend_schema, OpenApiResponse
from apps.api.serializers import (
    LoginSerializer, LoginResponseSerializer, RegisterSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
    ChangePasswordSerializer, LogoutSerializer, EmailVerificationSerializer,
    ResendVerificationSerializer
)
from apps.users.models import UserActivity

User = get_user_model()


@extend_schema(
    request=LoginSerializer,
    responses={
        200: LoginResponseSerializer,
        400: OpenApiResponse(description="Invalid credentials"),
    },
    summary="User login",
    description="Authenticate user with email and password",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def login_view(request):
    """User login endpoint"""
    serializer = LoginSerializer(data=request.data, context={'request': request})

    if serializer.is_valid():
        user = serializer.validated_data['user']
        remember_me = serializer.validated_data.get('remember_me', False)

        # Login user
        login(request, user)

        # Create or get token
        token, created = Token.objects.get_or_create(user=user)

        # Set session expiry based on remember_me
        if remember_me:
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)  # 2 weeks
        else:
            request.session.set_expiry(0)  # Browser session

        # Update user last login info
        user.last_login = timezone.now()
        user.last_login_ip = get_client_ip(request)
        user.save(update_fields=['last_login', 'last_login_ip'])

        # Log activity
        UserActivity.objects.create(
            user=user,
            action='login',
            description=f'User logged in from {get_client_ip(request)}',
            metadata={'ip_address': get_client_ip(request), 'user_agent': request.META.get('HTTP_USER_AGENT')}
        )

        response_data = {
            'user': user,
            'token': token.key,
            'expires_at': timezone.now() + timedelta(days=30)  # Token expires in 30 days
        }

        return Response(
            LoginResponseSerializer(response_data).data,
            status=status.HTTP_200_OK
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=RegisterSerializer,
    responses={
        201: LoginResponseSerializer,
        400: OpenApiResponse(description="Registration failed"),
    },
    summary="User registration",
    description="Register a new user account",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def register_view(request):
    """User registration endpoint"""
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.save()

        # Create token
        token, created = Token.objects.get_or_create(user=user)

        # Log activity
        UserActivity.objects.create(
            user=user,
            action='register',
            description=f'User registered from {get_client_ip(request)}',
            metadata={'ip_address': get_client_ip(request)}
        )

        # Send verification email
        send_verification_email(user, request)

        # Auto login after registration
        login(request, user)

        response_data = {
            'user': user,
            'token': token.key,
            'expires_at': timezone.now() + timedelta(days=30)
        }

        return Response(
            LoginResponseSerializer(response_data).data,
            status=status.HTTP_201_CREATED
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=LogoutSerializer,
    responses={
        200: OpenApiResponse(description="Successfully logged out"),
    },
    summary="User logout",
    description="Logout user and invalidate session/token",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def logout_view(request):
    """User logout endpoint"""
    serializer = LogoutSerializer(data=request.data)

    if serializer.is_valid():
        all_devices = serializer.validated_data.get('all_devices', False)

        # Log activity
        UserActivity.objects.create(
            user=request.user,
            action='logout',
            description=f'User logged out from {get_client_ip(request)}',
            metadata={'ip_address': get_client_ip(request), 'all_devices': all_devices}
        )

        if all_devices:
            # Delete all tokens for user (logout from all devices)
            Token.objects.filter(user=request.user).delete()
        else:
            # Delete current token only
            try:
                request.user.auth_token.delete()
            except Token.DoesNotExist:
                pass

        # Logout from session
        logout(request)

        return Response(
            {'detail': 'Successfully logged out.'},
            status=status.HTTP_200_OK
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=PasswordResetRequestSerializer,
    responses={
        200: OpenApiResponse(description="Password reset email sent"),
    },
    summary="Request password reset",
    description="Send password reset email to user",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def password_reset_request_view(request):
    """Password reset request endpoint"""
    serializer = PasswordResetRequestSerializer(data=request.data)

    if serializer.is_valid():
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email)

            # Generate reset token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))

            # Create reset URL
            reset_url = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}/"

            # Send email
            context = {
                'user': user,
                'reset_url': reset_url,
                'site_name': settings.SITE_NAME,
            }

            subject = f'Password Reset - {settings.SITE_NAME}'
            html_message = render_to_string('emails/password_reset.html', context)
            plain_message = render_to_string('emails/password_reset.txt', context)

            send_mail(
                subject=subject,
                message=plain_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )

            # Log activity
            UserActivity.objects.create(
                user=user,
                action='password_reset_request',
                description=f'Password reset requested from {get_client_ip(request)}',
                metadata={'ip_address': get_client_ip(request)}
            )

        except User.DoesNotExist:
            # Don't reveal if email exists or not
            pass

        return Response(
            {'detail': 'If your email address exists in our database, you will receive a password reset link shortly.'},
            status=status.HTTP_200_OK
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=PasswordResetConfirmSerializer,
    responses={
        200: OpenApiResponse(description="Password reset successful"),
        400: OpenApiResponse(description="Invalid token or passwords don't match"),
    },
    summary="Confirm password reset",
    description="Reset password with token from email",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def password_reset_confirm_view(request):
    """Password reset confirmation endpoint"""
    serializer = PasswordResetConfirmSerializer(data=request.data)

    if serializer.is_valid():
        token = serializer.validated_data['token']
        password = serializer.validated_data['password']

        # Extract UID and token from the token parameter
        try:
            # Assuming token format is "uid-token"
            uid, reset_token = token.split('-', 1)
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)

            if default_token_generator.check_token(user, reset_token):
                user.set_password(password)
                user.save()

                # Invalidate all existing tokens
                Token.objects.filter(user=user).delete()

                # Log activity
                UserActivity.objects.create(
                    user=user,
                    action='password_reset_confirm',
                    description=f'Password reset confirmed from {get_client_ip(request)}',
                    metadata={'ip_address': get_client_ip(request)}
                )

                return Response(
                    {'detail': 'Password has been reset successfully.'},
                    status=status.HTTP_200_OK
                )
            else:
                return Response(
                    {'error': 'Invalid or expired token.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        except (ValueError, User.DoesNotExist):
            return Response(
                {'error': 'Invalid token.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=ChangePasswordSerializer,
    responses={
        200: OpenApiResponse(description="Password changed successfully"),
        400: OpenApiResponse(description="Invalid current password or passwords don't match"),
    },
    summary="Change password",
    description="Change user password (requires authentication)",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def change_password_view(request):
    """Change password endpoint"""
    serializer = ChangePasswordSerializer(data=request.data, context={'request': request})

    if serializer.is_valid():
        new_password = serializer.validated_data['new_password']

        # Set new password
        request.user.set_password(new_password)
        request.user.save()

        # Invalidate all existing tokens except current one
        current_token = getattr(request.user, 'auth_token', None)
        Token.objects.filter(user=request.user).exclude(pk=current_token.pk if current_token else None).delete()

        # Log activity
        UserActivity.objects.create(
            user=request.user,
            action='password_change',
            description=f'Password changed from {get_client_ip(request)}',
            metadata={'ip_address': get_client_ip(request)}
        )

        return Response(
            {'detail': 'Password changed successfully.'},
            status=status.HTTP_200_OK
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=EmailVerificationSerializer,
    responses={
        200: OpenApiResponse(description="Email verified successfully"),
        400: OpenApiResponse(description="Invalid token"),
    },
    summary="Verify email",
    description="Verify user email with token from email",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def verify_email_view(request):
    """Email verification endpoint"""
    serializer = EmailVerificationSerializer(data=request.data)

    if serializer.is_valid():
        token = serializer.validated_data['token']

        try:
            # Extract UID and token from the token parameter
            uid, verify_token = token.split('-', 1)
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)

            if default_token_generator.check_token(user, verify_token):
                user.is_verified = True
                user.save()

                # Log activity
                UserActivity.objects.create(
                    user=user,
                    action='email_verified',
                    description=f'Email verified from {get_client_ip(request)}',
                    metadata={'ip_address': get_client_ip(request)}
                )

                return Response(
                    {'detail': 'Email verified successfully.'},
                    status=status.HTTP_200_OK
                )
            else:
                return Response(
                    {'error': 'Invalid or expired token.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        except (ValueError, User.DoesNotExist):
            return Response(
                {'error': 'Invalid token.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=ResendVerificationSerializer,
    responses={
        200: OpenApiResponse(description="Verification email sent"),
        400: OpenApiResponse(description="Email already verified or user not found"),
    },
    summary="Resend verification email",
    description="Resend email verification link",
    tags=["Authentication"]
)
@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def resend_verification_view(request):
    """Resend verification email endpoint"""
    serializer = ResendVerificationSerializer(data=request.data)

    if serializer.is_valid():
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email)

            if not user.is_verified:
                send_verification_email(user, request)

                return Response(
                    {'detail': 'Verification email sent.'},
                    status=status.HTTP_200_OK
                )
            else:
                return Response(
                    {'error': 'Email is already verified.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        except User.DoesNotExist:
            return Response(
                {'error': 'User with this email does not exist.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Helper functions
def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def send_verification_email(user, request):
    """Send email verification email"""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    # Create verification URL
    verify_url = f"{settings.FRONTEND_URL}/verify-email/{uid}-{token}/"

    context = {
        'user': user,
        'verify_url': verify_url,
        'site_name': settings.SITE_NAME,
    }

    subject = f'Verify your email - {settings.SITE_NAME}'
    html_message = render_to_string('emails/email_verification.html', context)
    plain_message = render_to_string('emails/email_verification.txt', context)

    send_mail(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )