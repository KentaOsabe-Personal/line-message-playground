from uuid import UUID

from django.db import DatabaseError
from rest_framework import status
from rest_framework.exceptions import (
    ParseError,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.response import Response

from lineaccounts.authentication import OwnerPrincipal, OwnerSessionContext
from lineaccounts.views import OwnerProtectedAPIView

from .confirmation import (
    ConfirmationError,
    ConfirmationService,
    ConfirmationTokenService,
)
from .formatters import (
    MessageValidationError,
    format_message,
    format_message_snapshot,
)
from .gateway import LINEGateway
from .serializers import (
    CanonicalUUIDField,
    DeliveryChannelChoiceResponseSerializer,
    DeliveryRecipientChoiceResponseSerializer,
    LinkedPreviewRequestSerializer,
    SendDeliveryRequestSerializer,
)
from .services import (
    DeliveryInProgressError,
    DeliveryService,
    OperationIdReusedError,
    SubmitDeliveryCommand,
)
from .types import (
    ConfirmationSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal as DeliveryOwnerPrincipal,
    TargetUnavailable,
)


SAFE_SUMMARIES = {
    "validation_error": "入力内容を確認してください。",
    "confirmation_required": "送信内容をもう一度確認してください。",
    "confirmation_stale": "内容が変更されています。もう一度確認してください。",
    "operation_id_reused": "この送信操作IDは別の内容に使用済みです。",
    "delivery_in_progress": "同じ内容の送信を処理中です。",
    "operation_not_found": "送信操作を確認できませんでした。",
    "target_not_available": "対象を確認できませんでした。",
    "target_not_deliverable": "選択した対象は現在配信できません。",
    "storage_unavailable": "処理を完了できませんでした。",
    "unexpected": "配信処理を完了できませんでした。",
}


def error_response(code, http_status, *, fields=None):
    error = {"code": code, "summary": SAFE_SUMMARIES[code]}
    if fields:
        error["fields"] = fields
    return Response({"error": error}, status=http_status)


def serializer_error_response(serializer):
    fields = {
        field: ["入力値が不正です。"]
        for field in serializer.errors
        if field != "non_field_errors"
    }
    return error_response("validation_error", status.HTTP_400_BAD_REQUEST, fields=fields)


def message_error_response(error):
    field = error.field or "message"
    return error_response(
        "validation_error",
        status.HTTP_400_BAD_REQUEST,
        fields={field: ["入力値が不正です。"]},
    )


def submission_response(submission, http_status):
    data = {
        "status": submission.status,
        "operationId": str(submission.operation_id),
        "acceptedAt": submission.accepted_at.isoformat(),
    }
    if submission.status == "processing":
        data["expiresAt"] = submission.processing_expires_at.isoformat()
    else:
        data["completedAt"] = submission.completed_at.isoformat()
        data["lineRequestId"] = submission.line_request_id
        if submission.status in ("failed", "unknown"):
            data["error"] = {
                "code": submission.failure_type,
                "summary": safe_delivery_summary(submission.failure_type),
            }
    return Response(data, status=http_status)


def safe_delivery_summary(failure_type):
    summaries = {
        "configuration": "Backendの配信設定を確認してください。",
        "invalid_request": "入力または配信設定を確認してください。",
        "authentication": "LINEの認証設定を確認してください。",
        "permission": "LINEチャネルの権限を確認してください。",
        "conflict": "LINE側で送信が競合しました。",
        "rate_limited": "時間をおいて利用上限を確認してください。",
        "service_unavailable": "LINE側の状態を確認してください。",
        "timeout_unknown": "送信結果を確認できませんでした。",
        "processing_expired": "処理結果を確認できませんでした。",
        "unexpected": "配信結果を確定できませんでした。",
    }
    return summaries.get(failure_type, summaries["unexpected"])


class LocalDeliveryAPIView(OwnerProtectedAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, (ParseError, UnsupportedMediaType)):
            return error_response("validation_error", status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class DeliveryTargetChannelListAPIView(LocalDeliveryAPIView):
    def get(self, request):
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        try:
            from lineaccounts.delivery_repositories import DeliveryTargetDirectory

            choices = DeliveryTargetDirectory().list_channels(
                principal.identity_public_id
            )
        except DatabaseError:
            return error_response(
                "storage_unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "items": DeliveryChannelChoiceResponseSerializer(
                    choices,
                    many=True,
                ).data
            }
        )


class DeliveryTargetRecipientListAPIView(LocalDeliveryAPIView):
    def get(self, request, channel_id):
        try:
            parsed_channel_id = CanonicalUUIDField().run_validation(channel_id)
        except ValidationError:
            return error_response(
                "validation_error",
                status.HTTP_400_BAD_REQUEST,
                fields={"channelId": ["入力値が不正です。"]},
            )
        principal = request.user
        assert isinstance(principal, OwnerPrincipal)
        try:
            from lineaccounts.delivery_repositories import DeliveryTargetDirectory

            choices = DeliveryTargetDirectory().list_recipients(
                principal.identity_public_id,
                parsed_channel_id,
            )
        except DatabaseError:
            return error_response(
                "storage_unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if isinstance(choices, TargetUnavailable):
            if choices.reason == "no_deliverable_recipient":
                return Response({"items": []})
            return error_response(
                "target_not_available",
                status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "items": DeliveryRecipientChoiceResponseSerializer(
                    choices,
                    many=True,
                ).data
            }
        )


class PreviewAPIView(LocalDeliveryAPIView):
    def post(self, request):
        serializer = LinkedPreviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return serializer_error_response(serializer)
        values = serializer.validated_data
        try:
            message = format_message_snapshot(
                values["subject"],
                values["body"],
            )
        except MessageValidationError as error:
            return message_error_response(error)

        principal = request.user
        context = request.auth
        assert isinstance(principal, OwnerPrincipal)
        assert isinstance(context, OwnerSessionContext)
        try:
            from lineaccounts.delivery_repositories import DeliveryTargetDirectory

            target = DeliveryTargetDirectory().resolve(
                principal.identity_public_id,
                values["channelId"],
                values["recipientId"],
            )
        except DatabaseError:
            return error_response(
                "storage_unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if isinstance(target, TargetUnavailable):
            return error_response(
                "target_not_available",
                status.HTTP_404_NOT_FOUND,
            )
        if not target.delivery_available:
            return error_response(
                "target_not_deliverable",
                status.HTTP_409_CONFLICT,
            )

        confirmation_service = ConfirmationService()
        receipt_expires_at = confirmation_service.receipt_expires_at(
            values["receiptRequested"]
        )
        confirmation = confirmation_service.issue(
            ConfirmationSnapshot(
                owner=DeliveryOwnerPrincipal(context.session.owner_slot),
                owner_identity=OwnerIdentitySnapshot(
                    principal.identity_public_id
                ),
                channel_public_id=target.snapshot.channel_public_id,
                recipient_public_id=target.snapshot.recipient_public_id,
                target_revision=target.revision,
                message_fingerprint=message.fingerprint,
                receipt_requested=values["receiptRequested"],
                receipt_expires_at=receipt_expires_at,
            )
        )
        return Response(
            {
                "channelId": str(target.snapshot.channel_public_id),
                "channelLabel": target.snapshot.channel_label,
                "recipientId": str(target.snapshot.recipient_public_id),
                "recipientDisplayName": context.session.display_name,
                "friendshipState": target.snapshot.friendship_state,
                "formattedText": message.formatted_text,
                "receiptRequested": values["receiptRequested"],
                "receiptExpiresAt": (
                    confirmation.receipt_expires_at.isoformat()
                    if confirmation.receipt_expires_at is not None
                    else None
                ),
                "confirmationToken": confirmation.token,
            }
        )


class DeliveryAPIView(LocalDeliveryAPIView):
    def post(self, request):
        serializer = SendDeliveryRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return serializer_error_response(serializer)
        values = serializer.validated_data
        try:
            message = format_message(values["subject"], values["body"])
        except MessageValidationError as error:
            return message_error_response(error)
        try:
            ConfirmationTokenService().verify(values["confirmationToken"], message)
        except ConfirmationError as error:
            code = (
                "confirmation_stale"
                if str(error) == "confirmation_mismatch"
                else "confirmation_required"
            )
            return error_response(code, status.HTTP_400_BAD_REQUEST)
        try:
            submission = DeliveryService(gateway=LINEGateway()).submit(
                SubmitDeliveryCommand(values["operationId"], message)
            )
        except OperationIdReusedError:
            return error_response("operation_id_reused", status.HTTP_409_CONFLICT)
        except DeliveryInProgressError:
            return error_response("delivery_in_progress", status.HTTP_409_CONFLICT)
        http_status = (
            status.HTTP_201_CREATED
            if submission.created
            else status.HTTP_202_ACCEPTED
            if submission.status == "processing"
            else status.HTTP_200_OK
        )
        return submission_response(submission, http_status)


class DeliveryStatusAPIView(LocalDeliveryAPIView):
    def post(self, request, operation_id):
        try:
            parsed_operation_id = UUID(operation_id)
        except (TypeError, ValueError, AttributeError):
            return error_response("validation_error", status.HTTP_400_BAD_REQUEST)
        submission = DeliveryService(gateway=LINEGateway()).check_status(
            parsed_operation_id
        )
        if submission is None:
            return error_response("operation_not_found", status.HTTP_404_NOT_FOUND)
        http_status = (
            status.HTTP_202_ACCEPTED
            if submission.status == "processing"
            else status.HTTP_200_OK
        )
        return submission_response(submission, http_status)
