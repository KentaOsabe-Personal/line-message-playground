from uuid import UUID

from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .confirmation import ConfirmationError, ConfirmationTokenService
from .formatters import MessageValidationError, format_message
from .gateway import LINEGateway
from .serializers import PreviewRequestSerializer, SendDeliveryRequestSerializer
from .services import (
    DeliveryInProgressError,
    DeliveryService,
    OperationIdReusedError,
    SubmitDeliveryCommand,
)


SAFE_SUMMARIES = {
    "validation_error": "入力内容を確認してください。",
    "confirmation_required": "送信内容をもう一度確認してください。",
    "confirmation_stale": "内容が変更されています。もう一度確認してください。",
    "operation_id_reused": "この送信操作IDは別の内容に使用済みです。",
    "delivery_in_progress": "同じ内容の送信を処理中です。",
    "operation_not_found": "送信操作を確認できませんでした。",
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


class LocalDeliveryAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    def handle_exception(self, exc):
        if isinstance(exc, (ParseError, UnsupportedMediaType)):
            return error_response("validation_error", status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class PreviewAPIView(LocalDeliveryAPIView):
    def post(self, request):
        serializer = PreviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return serializer_error_response(serializer)
        try:
            message = format_message(**serializer.validated_data)
        except MessageValidationError as error:
            return message_error_response(error)
        return Response(
            {
                "formattedText": message.formatted_text,
                "confirmationToken": ConfirmationTokenService().issue(message),
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
