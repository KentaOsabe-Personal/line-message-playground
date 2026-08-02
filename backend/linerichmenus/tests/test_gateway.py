import pickle
from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from linechannels.types import AccessToken

from linerichmenus.gateway import (
    CreateAccepted,
    DefaultRichMenuGateway,
    GatewayAccepted,
    GatewayRejected,
    GatewayUnknown,
    ImageObserved,
    ImageObservationUnknown,
    RichMenuArea,
    RichMenuBounds,
    RichMenuGatewayContext,
    RichMenuObject,
    RichMenuUriAction,
    RichMenuDefaultExternal,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    ResourceListAccepted,
    ResourceObserved,
)


class ApiError(Exception):
    def __init__(self, status):
        self.status = status


class FakeJsonClient:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        response = self.responses.get(name)
        if isinstance(response, BaseException):
            raise response
        return response

    def validate_rich_menu_object(self, *args, **kwargs):
        return self._call("validate_rich_menu_object", *args, **kwargs)

    def create_rich_menu(self, *args, **kwargs):
        return self._call("create_rich_menu", *args, **kwargs)

    def get_rich_menu_list(self, *args, **kwargs):
        return self._call("get_rich_menu_list", *args, **kwargs)

    def get_rich_menu(self, *args, **kwargs):
        return self._call("get_rich_menu", *args, **kwargs)

    def set_default_rich_menu(self, *args, **kwargs):
        return self._call("set_default_rich_menu", *args, **kwargs)

    def get_default_rich_menu(self, *args, **kwargs):
        return self._call("get_default_rich_menu", *args, **kwargs)

    def cancel_default_rich_menu(self, *args, **kwargs):
        return self._call("cancel_default_rich_menu", *args, **kwargs)

    def delete_rich_menu(self, *args, **kwargs):
        return self._call("delete_rich_menu", *args, **kwargs)


class FakeBlobClient:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def set_rich_menu_image(self, *args, **kwargs):
        self.calls.append(("set_rich_menu_image", args, kwargs))
        response = self.responses.get("set_rich_menu_image")
        if isinstance(response, BaseException):
            raise response
        return response

    def get_rich_menu_image(self, *args, **kwargs):
        self.calls.append(("get_rich_menu_image", args, kwargs))
        response = self.responses.get("get_rich_menu_image")
        if isinstance(response, BaseException):
            raise response
        return response


class FakeFactory:
    def __init__(self):
        self.calls = []
        self.json = FakeJsonClient()
        self.blob = FakeBlobClient()
        self.close_calls = 0
        self.close_error = None

    def __call__(self, access_token, *, retries):
        self.calls.append((access_token, retries))
        factory = self

        class Clients:
            json = factory.json
            blob = factory.blob

            def close(self):
                factory.close_calls += 1
                if factory.close_error is not None:
                    raise factory.close_error

        return Clients()


def context():
    return RichMenuGatewayContext(
        channel_public_id=uuid4(),
        channel_revision=datetime(2026, 8, 2, tzinfo=timezone.utc),
        access_token=AccessToken("access-token-canary"),
    )


def menu_object():
    return RichMenuObject(
        width=2500,
        height=843,
        name="rich-menu-name-canary",
        chat_bar_text="メニュー",
        areas=(
            RichMenuArea(
                bounds=RichMenuBounds(0, 0, 2500, 843),
                action=RichMenuUriAction("https://example.com/guide"),
            ),
        ),
    )


class GatewayContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = FakeFactory()
        self.gateway = DefaultRichMenuGateway(self.factory, timeout_seconds=3.5)
        self.context = context()
        self.request = menu_object()

    # テストケース: JSON/defaultの全endpointを同一scoped tokenで呼び出す。
    # 期待値: SDK retryは0、timeoutを渡し、各clientが必ずcloseされる。
    def test_json_methods_use_scoped_token_once_with_sdk_retries_disabled(self):
        self.factory.json.responses.update(
            {
                "validate_rich_menu_object": None,
                "create_rich_menu": {"richMenuId": "rich-menu-id-canary"},
                "get_rich_menu_list": {"richmenus": []},
                "get_rich_menu": {
                    "richMenuId": "rich-menu-id-canary",
                    "name": "marker-canary",
                },
                "set_default_rich_menu": None,
                "get_default_rich_menu": {"richMenuId": "rich-menu-id-canary"},
                "cancel_default_rich_menu": None,
                "delete_rich_menu": None,
            }
        )

        results = (
            self.gateway.validate(self.context, self.request),
            self.gateway.create(self.context, self.request),
            self.gateway.list_resources(self.context),
            self.gateway.get_resource(self.context, "rich-menu-id-canary"),
            self.gateway.set_default(self.context, "rich-menu-id-canary"),
            self.gateway.get_default(self.context),
            self.gateway.clear_default(self.context),
            self.gateway.delete(self.context, "rich-menu-id-canary"),
        )

        self.assertIsInstance(results[0], GatewayAccepted)
        self.assertIsInstance(results[1], CreateAccepted)
        self.assertIsInstance(results[2], ResourceListAccepted)
        self.assertIsInstance(results[3], ResourceObserved)
        self.assertIsInstance(results[4], GatewayAccepted)
        self.assertIsInstance(results[5], RichMenuDefaultPresent)
        self.assertIsInstance(results[6], GatewayAccepted)
        self.assertIsInstance(results[7], GatewayAccepted)
        self.assertEqual(
            self.factory.calls,
            [("access-token-canary", 0)] * 8,
        )
        self.assertEqual(self.factory.close_calls, 8)
        request = self.factory.json.calls[0][1][0]
        chat_bar_text = getattr(request, "chat_bar_text", None)
        if chat_bar_text is None:
            chat_bar_text = request["chatBarText"]
        self.assertEqual(chat_bar_text, "メニュー")
        self.assertTrue(
            all(
                call[2].get("_request_timeout") == 3.5
                for call in self.factory.json.calls
            )
        )

    # テストケース: 4xx、429、5xxのLINE応答をmutation gatewayへ渡す。
    # 期待値: 4xxはrejected、429/5xxはunknownとなり、raw IDやbodyを出さない。
    def test_4xx_without_429_is_rejected_but_429_and_5xx_are_unknown(self):
        for status, expected in (
            (400, GatewayRejected),
            (401, GatewayRejected),
            (403, GatewayRejected),
            (404, GatewayRejected),
            (415, GatewayRejected),
            (429, GatewayUnknown),
            (500, GatewayUnknown),
        ):
            with self.subTest(status=status):
                factory = FakeFactory()
                factory.json.responses["set_default_rich_menu"] = ApiError(status)
                result = DefaultRichMenuGateway(factory).set_default(
                    self.context, "rich-menu-id-canary"
                )
                self.assertIsInstance(result, expected)
                self.assertNotIn("rich-menu-id-canary", repr(result))
                self.assertEqual(factory.close_calls, 1)

    # テストケース: timeout・connection failure・malformed responseを発生させる。
    # 期待値: すべてunknownへ縮約し、例外文字列やraw responseを返さない。
    def test_timeout_connection_and_malformed_response_are_unknown_without_raw_details(self):
        cases = (
            TimeoutError("timeout-canary"),
            ConnectionError("connection-canary"),
            {"unexpected": "malformed-response-canary"},
        )
        for response in cases:
            with self.subTest(response=type(response).__name__):
                factory = FakeFactory()
                factory.json.responses["create_rich_menu"] = response
                result = DefaultRichMenuGateway(factory).create(
                    self.context, self.request
                )
                self.assertIsInstance(result, GatewayUnknown)
                rendered = repr(result)
                self.assertNotIn("timeout-canary", rendered)
                self.assertNotIn("connection-canary", rendered)
                self.assertNotIn("malformed-response-canary", rendered)

    # テストケース: default endpointの404と403を観測する。
    # 期待値: 404はdefaultなし、403は外部manager管理として分類する。
    def test_default_404_is_none_and_403_is_external_manager_default(self):
        for response, expected in (
            (ApiError(404), RichMenuDefaultNone),
            (ApiError(403), RichMenuDefaultExternal),
        ):
            with self.subTest(expected=expected.__name__):
                factory = FakeFactory()
                factory.json.responses["get_default_rich_menu"] = response
                result = DefaultRichMenuGateway(factory).get_default(self.context)
                self.assertIsInstance(result, expected)

    # テストケース: gateway context必須性とtokenのrepr/serializationを検証する。
    # 期待値: contextなしは拒否され、tokenはdebug表現・pickleへ出ない。
    def test_context_and_token_are_required_but_never_rendered(self):
        with self.assertRaises(TypeError):
            self.gateway.get_default(None)
        rendered = repr(self.gateway)
        self.assertNotIn("access-token-canary", rendered)
        with self.assertRaises(TypeError):
            pickle.dumps(self.context)


class GatewayImageContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = FakeFactory()
        self.gateway = DefaultRichMenuGateway(self.factory)
        self.context = context()

    # テストケース: 不正なrendered imageをuploadへ渡す。
    # 期待値: image_invalidとなり、client生成とblob外部callを開始しない。
    def test_upload_rejects_invalid_rendered_image_before_external_call(self):
        from linerichmenus.types import RenderedImage

        image = RenderedImage(
            content_type="image/png",
            width=2500,
            height=843,
            pixel_digest="a" * 64,
            binary=b"not-an-image",
        )

        result = self.gateway.upload(self.context, "rich-menu-id-canary", image)

        self.assertEqual(result.code, "image_invalid")
        self.assertEqual(self.factory.calls, [])
        self.assertEqual(self.factory.blob.calls, [])

    # テストケース: valid imageをuploadし、同じresourceをdownloadする。
    # 期待値: binaryはcall中だけ渡され、download結果はdigest付きでblobがcloseされる。
    def test_upload_passes_binary_for_the_call_only_and_download_closes_blob(self):
        from io import BytesIO

        from PIL import Image

        from linerichmenus.types import RenderedImage

        output = BytesIO()
        Image.new("RGBA", (800, 550), (20, 90, 120, 255)).save(output, format="PNG")
        binary = output.getvalue()
        image = RenderedImage(
            content_type="image/png",
            width=800,
            height=550,
            pixel_digest="b" * 64,
            binary=binary,
        )
        self.factory.blob.responses["get_rich_menu_image"] = BytesIO(binary)

        uploaded = self.gateway.upload(self.context, "rich-menu-id-canary", image)
        downloaded = self.gateway.download(self.context, "rich-menu-id-canary")

        self.assertEqual(uploaded, GatewayAccepted())
        self.assertIsInstance(downloaded, ImageObserved)
        self.assertEqual(downloaded.width, 800)
        self.assertEqual(downloaded.height, 550)
        self.assertEqual(self.factory.close_calls, 2)
        upload_call = self.factory.blob.calls[0]
        self.assertEqual(upload_call[0], "set_rich_menu_image")
        self.assertEqual(upload_call[1][0], "rich-menu-id-canary")
        self.assertEqual(upload_call[1][1], binary)
        self.assertEqual(upload_call[2]["_request_timeout"], 5.0)

    # テストケース: malformed image downloadとclient close failureを発生させる。
    # 期待値: 画像観測・mutation結果をunknownへ縮約し、失敗内容を露出しない。
    def test_malformed_download_and_client_close_failure_are_unknown(self):
        self.factory.blob.responses["get_rich_menu_image"] = b"malformed-image-canary"
        malformed = self.gateway.download(self.context, "rich-menu-id-canary")
        self.assertIsInstance(malformed, ImageObservationUnknown)

        self.factory.json.responses["set_default_rich_menu"] = None
        self.factory.close_error = RuntimeError("close-failure-canary")
        closed = self.gateway.set_default(self.context, "rich-menu-id-canary")
        self.assertIsInstance(closed, GatewayUnknown)
        self.assertNotIn("close-failure-canary", repr(closed))
