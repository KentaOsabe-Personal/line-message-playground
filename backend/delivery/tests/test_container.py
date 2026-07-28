from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from delivery.container import (
    build_confirmation_service,
    build_delivery_service,
    build_receipt_handler,
    build_status_service,
    build_target_directory,
)


class DeliveryContainerTests(SimpleTestCase):
    # テストケース: target一覧用の最小compositionを構築する
    # 期待値: account adapterだけを生成し、credentialやgatewayを起動しない
    @patch("delivery.container.LINEChannelPushGateway")
    @patch("delivery.container.build_credential_repository")
    @patch("delivery.container.DeliveryTargetDirectory")
    def test_target_directory_build_is_minimal(
        self,
        directory_type,
        credential_builder,
        gateway_type,
    ):
        result = build_target_directory()

        self.assertIs(result, directory_type.return_value)
        directory_type.assert_called_once_with()
        credential_builder.assert_not_called()
        gateway_type.assert_not_called()

    # テストケース: preview用のconfirmation serviceを構築する
    # 期待値: confirmationだけを生成し送信adapterを生成しない
    @patch("delivery.container.LINEChannelPushGateway")
    @patch("delivery.container.build_credential_repository")
    @patch("delivery.container.ConfirmationService")
    @patch("delivery.container.DeliveryTargetDirectory")
    def test_confirmation_build_is_minimal(
        self,
        directory_type,
        confirmation_type,
        credential_builder,
        gateway_type,
    ):
        clock = Mock()
        result = build_confirmation_service(clock=clock)

        self.assertIs(
            result,
            confirmation_type.return_value,
        )
        confirmation_type.assert_called_once_with(clock=clock)
        directory_type.assert_not_called()
        credential_builder.assert_not_called()
        gateway_type.assert_not_called()

    # テストケース: linked sendのproduction compositionを構築する
    # 期待値: target、repository、credential、gateway、receipt factoryを生成しserviceへ注入する
    @patch("delivery.container.LINEChannelPushGateway")
    @patch("delivery.container.build_credential_repository")
    @patch("delivery.container.ReceiptCapabilityFactory")
    @patch("delivery.container.DjangoAttemptRepository")
    @patch("delivery.container.ConfirmationService")
    @patch("delivery.container.DeliveryTargetDirectory")
    def test_delivery_runtime_wires_explicit_service_dependencies(
        self,
        directory_type,
        confirmation_type,
        repository_type,
        receipt_factory_type,
        credential_builder,
        gateway_type,
    ):
        clock = Mock()

        service = build_delivery_service(clock=clock)

        self.assertIs(
            service._target_directory,
            directory_type.return_value,
        )
        self.assertIs(
            service._attempt_repository,
            repository_type.return_value,
        )
        self.assertIs(
            service._receipt_capability_factory,
            receipt_factory_type.return_value,
        )
        self.assertIs(
            service._credential_repository,
            credential_builder.return_value,
        )
        self.assertIs(
            service._channel_push_gateway,
            gateway_type.return_value,
        )
        repository_type.assert_called_once_with(clock=clock)
        confirmation_type.assert_not_called()

    # テストケース: status読取用の最小compositionを構築する
    # 期待値: attempt repositoryだけを生成し送信依存を生成しない
    @patch("delivery.container.LINEChannelPushGateway")
    @patch("delivery.container.build_credential_repository")
    @patch("delivery.container.DjangoAttemptRepository")
    def test_status_runtime_wires_only_attempt_repository(
        self,
        repository_type,
        credential_builder,
        gateway_type,
    ):
        clock = Mock()

        service = build_status_service(clock=clock)

        self.assertIs(
            service._attempt_repository,
            repository_type.return_value,
        )
        self.assertIsNone(service._target_directory)
        self.assertIsNone(service._receipt_capability_factory)
        self.assertIsNone(service._credential_repository)
        self.assertIsNone(service._channel_push_gateway)
        repository_type.assert_called_once_with(clock=clock)
        credential_builder.assert_not_called()
        gateway_type.assert_not_called()

    # テストケース: webhook receipt用compositionを構築する
    # 期待値: clockを共有するrepositoryとhandlerを明示的に接続する
    @patch("delivery.container.ReceiptHandler")
    @patch("delivery.container.DjangoAttemptRepository")
    def test_receipt_handler_wires_repository_and_clock(
        self,
        repository_type,
        handler_type,
    ):
        clock = Mock()

        handler = build_receipt_handler(clock=clock)

        self.assertIs(handler, handler_type.return_value)
        repository_type.assert_called_once_with(clock=clock)
        handler_type.assert_called_once_with(
            attempt_repository=repository_type.return_value,
            clock=clock,
        )
