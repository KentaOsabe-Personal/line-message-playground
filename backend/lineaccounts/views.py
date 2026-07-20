from __future__ import annotations

from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import OWNER_SESSION_KEY, OwnerPrincipal, OwnerSessionAuthentication
from .csrf import ExactOriginCsrfMixin
from .errors import SafeAPIError
from .gateway import HttpxLinePlatformGateway
from .permissions import HasOwnerSession, IsActiveOwner
from .recipient_services import (
    DefaultRecipientService,
    RecipientMutationFailed,
    RecipientMutationSucceeded,
)
from .repositories import (
    AccountPersistenceError,
    AccountStateError,
    DjangoAccountRepository,
)
from .runtime import (
    get_line_account_runtime,
    resolve_liff_linked_channel_policy,
)
from .serializers import (
    EmptyRequestSerializer,
    LineLoginRequestSerializer,
    RecipientRegistrationRequestSerializer,
    RecipientStateRequestSerializer,
)
from .session_services import (
    AnonymousSessionStatus,
    AuthenticatedSessionStatus,
    DefaultAccountSessionService,
    EstablishSessionRejected,
    UnlinkingSessionStatus,
)
from linechannels.repositories import DjangoLineChannelDirectory, PersistenceError


def build_session_service() -> DefaultAccountSessionService:
    runtime = get_line_account_runtime()
    return DefaultAccountSessionService(
        HttpxLinePlatformGateway(runtime),
        DjangoAccountRepository(),
        runtime.owner_eligibility,
    )


def build_recipient_service() -> DefaultRecipientService:
    runtime = get_line_account_runtime()
    directory = DjangoLineChannelDirectory()
    policy = resolve_liff_linked_channel_policy(runtime, directory)
    return DefaultRecipientService(
        directory,
        DjangoAccountRepository(),
        HttpxLinePlatformGateway(runtime),
        policy,
    )


def _session_id_from_cookie(request) -> UUID | None:
    value = request.session.get(OWNER_SESSION_KEY)
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return parsed if str(parsed) == value else None


def _status_response(status):
    if isinstance(status, AuthenticatedSessionStatus):
        return Response(
            {
                "state": "authenticated",
                "profile": {"displayName": status.display_name, "linked": True},
            }
        )
    if isinstance(status, UnlinkingSessionStatus):
        return Response(
            {
                "state": "unlinking",
                "stage": status.stage,
                "retryAction": status.retry_action,
            }
        )
    return Response({"state": "anonymous"})


def _storage_safe(operation):
    try:
        return operation()
    except AccountPersistenceError:
        raise SafeAPIError("storage_unavailable") from None


def _recipient_safe(operation):
    try:
        return operation()
    except (AccountPersistenceError, PersistenceError, ImproperlyConfigured):
        raise SafeAPIError("storage_unavailable") from None
    except AccountStateError as error:
        mapping = {
            "owner_not_active": "unlink_in_progress",
            "recipient_not_found": "recipient_not_found",
            "channel_not_found": "channel_not_found",
            "identity_mismatch": "owner_not_allowed",
            "identity_not_found": "owner_not_allowed",
        }
        raise SafeAPIError(mapping.get(error.code, "owner_operation_blocked")) from None


def _channel_link_data(item):
    return {
        "channelId": str(item.channel_id),
        "channelLabel": item.channel_label,
        "channelState": item.channel_state,
        "linkState": item.link_state,
        "friendshipState": item.friendship_state,
        "deliveryAvailable": item.delivery_available,
        "recipientId": (
            None if item.recipient_id is None else str(item.recipient_id)
        ),
    }


def _recipient_result(result):
    if isinstance(result, RecipientMutationSucceeded):
        return result.recipient
    assert isinstance(result, RecipientMutationFailed)
    raise SafeAPIError(result.code)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionAPIView(ExactOriginCsrfMixin, APIView):
    authentication_classes = [OwnerSessionAuthentication]
    permission_classes = [HasOwnerSession]

    def dispatch(self, request, *args, **kwargs):
        self._raw_method = request.method
        return super().dispatch(request, *args, **kwargs)

    def get_authenticators(self):
        if self._raw_method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self._raw_method == "GET":
            return []
        return super().get_permissions()

    def get(self, request):
        status = _storage_safe(
            lambda: build_session_service().get_status(
                _session_id_from_cookie(request), timezone.now()
            )
        )
        if isinstance(status, AnonymousSessionStatus):
            request.session.pop(OWNER_SESSION_KEY, None)
        return _status_response(status)

    def delete(self, request):
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        _storage_safe(
            lambda: build_session_service().logout(principal.owner_session_id)
        )
        request.session.flush()
        return Response({"state": "anonymous"})


class LineLoginAPIView(ExactOriginCsrfMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LineLoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _storage_safe(
            lambda: build_session_service().establish(
                serializer.validated_data["idToken"], timezone.now()
            )
        )
        if isinstance(result, EstablishSessionRejected):
            raise SafeAPIError(result.code)

        try:
            request.session.cycle_key()
            request.session[OWNER_SESSION_KEY] = str(result.session.public_id)
            request.session.save()
        except Exception:
            request.session.pop(OWNER_SESSION_KEY, None)
            request.session.modified = False
            raise SafeAPIError("storage_unavailable") from None

        if result.state == "unlinking":
            status = _storage_safe(
                lambda: build_session_service().get_status(
                    result.session.public_id, timezone.now()
                )
            )
            return _status_response(status)
        return Response(
            {
                "state": "authenticated",
                "profile": {"displayName": result.display_name, "linked": True},
            }
        )


class OwnerProtectedAPIView(ExactOriginCsrfMixin, APIView):
    authentication_classes = [OwnerSessionAuthentication]
    permission_classes = [IsActiveOwner]


class ChannelListAPIView(OwnerProtectedAPIView):
    def get(self, request):
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        items = _recipient_safe(
            lambda: build_recipient_service().list_channels(
                principal.identity_public_id
            )
        )
        return Response({"items": [_channel_link_data(item) for item in items]})


class RecipientCollectionAPIView(OwnerProtectedAPIView):
    def post(self, request):
        serializer = RecipientRegistrationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        item = _recipient_result(
            _recipient_safe(
                lambda: build_recipient_service().register(
                    principal.identity_public_id,
                    serializer.validated_data["channelId"],
                    serializer.validated_data.get("accessToken"),
                )
            )
        )
        return Response(_channel_link_data(item), status=201)


class RecipientDetailAPIView(OwnerProtectedAPIView):
    def patch(self, request, recipient_id):
        serializer = RecipientStateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        item = _recipient_result(
            _recipient_safe(
                lambda: build_recipient_service().set_enabled(
                    principal.identity_public_id,
                    recipient_id,
                    serializer.validated_data["enabled"],
                )
            )
        )
        return Response(_channel_link_data(item))

    def delete(self, request, recipient_id):
        serializer = EmptyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        _recipient_result(
            _recipient_safe(
                lambda: build_recipient_service().unlink(
                    principal.identity_public_id, recipient_id
                )
            )
        )
        return Response(status=204)
