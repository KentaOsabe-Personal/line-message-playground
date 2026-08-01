from rest_framework.response import Response

from lineaccounts.admin_authorization import OwnerOperationContext
from lineaccounts.authentication import OwnerPrincipal
from lineaccounts.errors import SafeAPIError
from lineaccounts.views import OwnerProtectedAPIView

from .admin_presenters import AdminPresenter
from .admin_serializers import (
    ConnectionCheckRequestSerializer,
    CreateChannelRequestSerializer,
    DeleteChannelRequestSerializer,
    SetChannelStateRequestSerializer,
    UpdateChannelRequestSerializer,
)
from .admin_types import (
    AdminChannelMutationSucceeded,
    AdminServiceFailed,
    ChannelDeleteSucceeded,
    ChannelListSucceeded,
    ChannelReadSucceeded,
    ConnectionCheckCompleted,
)
from .container import build_channel_admin_service


def _owner(request) -> OwnerOperationContext:
    principal = request.user
    assert isinstance(principal, OwnerPrincipal)
    return OwnerOperationContext(
        owner_session_id=principal.owner_session_id,
        identity_public_id=principal.identity_public_id,
    )


def _succeeded(result, expected_type):
    if isinstance(result, AdminServiceFailed):
        code = {
            "authentication_required": "authentication_required",
            "owner_operation_blocked": "owner_operation_blocked",
            "invalid_input": "validation_error",
            "duplicate_channel": "duplicate_channel",
            "channel_not_found": "channel_not_found",
            "stale_channel": "stale_channel",
            "provider_mismatch": "provider_mismatch",
            "provider_immutable": "provider_immutable",
            "credential_unavailable": "credential_unavailable",
            "encryption_failed": "storage_unavailable",
            "credential_unreadable": "credential_unavailable",
            "channel_referenced": "channel_referenced",
            "storage_retryable": "storage_retryable",
            "storage_unavailable": "storage_unavailable",
        }.get(result.code, "storage_unavailable")
        raise SafeAPIError(code)
    if not isinstance(result, expected_type):
        raise SafeAPIError("storage_unavailable")
    return result


class AdminAPIView(OwnerProtectedAPIView):
    presenter_class = AdminPresenter

    def service(self):
        return build_channel_admin_service()

    def presenter(self):
        return self.presenter_class()


class AdminChannelCollectionAPIView(AdminAPIView):
    def get(self, request):
        result = _succeeded(
            self.service().list_channels(_owner(request)), ChannelListSucceeded
        )
        return Response(
            {"items": [self.presenter().channel(item) for item in result.channels]}
        )

    def post(self, request):
        serializer = CreateChannelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _succeeded(
            self.service().register(_owner(request), serializer.to_command()),
            AdminChannelMutationSucceeded,
        )
        return Response(self.presenter().channel(result.channel), status=201)


class AdminChannelDetailAPIView(AdminAPIView):
    def get(self, request, channel_id):
        result = _succeeded(
            self.service().get_channel(_owner(request), channel_id),
            ChannelReadSucceeded,
        )
        return Response(self.presenter().channel(result.channel))

    def patch(self, request, channel_id):
        serializer = UpdateChannelRequestSerializer(
            data=request.data, context={"channel_id": str(channel_id)}
        )
        serializer.is_valid(raise_exception=True)
        result = _succeeded(
            self.service().update(_owner(request), serializer.to_command(channel_id)),
            AdminChannelMutationSucceeded,
        )
        return Response(self.presenter().channel(result.channel))

    def delete(self, request, channel_id):
        serializer = DeleteChannelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _succeeded(
            self.service().delete(_owner(request), serializer.to_command(channel_id)),
            ChannelDeleteSucceeded,
        )
        return Response(self.presenter().deleted(result))


class AdminChannelStateAPIView(AdminAPIView):
    def post(self, request, channel_id):
        serializer = SetChannelStateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _succeeded(
            self.service().set_state(_owner(request), serializer.to_command(channel_id)),
            AdminChannelMutationSucceeded,
        )
        return Response(self.presenter().channel(result.channel))


class AdminChannelConnectionCheckAPIView(AdminAPIView):
    def post(self, request, channel_id):
        serializer = ConnectionCheckRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _succeeded(
            self.service().check_connection(_owner(request), channel_id),
            ConnectionCheckCompleted,
        )
        return Response(self.presenter().connection(channel_id, result))
