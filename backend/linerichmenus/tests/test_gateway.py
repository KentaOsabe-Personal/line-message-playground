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
    ImageAbsent,
    ImageObservationUnknown,
    ImageObserved,
    ResourceListAccepted,
    ResourceObserved,
    RichMenuArea,
    RichMenuBounds,
    RichMenuDefaultExternal,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuGatewayContext,
    RichMenuObject,
    RichMenuUriAction,
    _build_sdk_clients,
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

    def get_default_rich_menu_id(self, *args, **kwargs):
        return self._call("get_default_rich_menu_id", *args, **kwargs)

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

    # テストケース: gatewayが呼び出すdefault取得名を実際に導入済みのLINE SDKで確認する。
    # 期待値: SDK更新やFakeの誤模倣で実在しないmethod名を受け入れない。
    def test_default_observation_method_matches_installed_line_sdk(self):
        clients = _build_sdk_clients("access-token-canary", retries=0)
        try:
            self.assertTrue(callable(getattr(clients.json, "get_default_rich_menu_id")))
            self.assertFalse(hasattr(clients.json, "get_default_rich_menu"))
        finally:
            clients.close()

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
                "get_default_rich_menu_id": {"richMenuId": "rich-menu-id-canary"},
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
            all(call[2].get("_request_timeout") == 3.5 for call in self.factory.json.calls)
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
                result = DefaultRichMenuGateway(factory).create(self.context, self.request)
                self.assertIsInstance(result, GatewayUnknown)
                rendered = repr(result)
                self.assertNotIn("timeout-canary", rendered)
                self.assertNotIn("connection-canary", rendered)
                self.assertNotIn("malformed-response-canary", rendered)

    # テストケース: 全JSON endpointへ4xx・429・5xx・timeout・connection・malformed・close失敗を注入する。
    # 期待値: endpoint固有契約でrejected/unknown/special observationを分類し、生失敗を公開しない。
    def test_every_json_endpoint_failure_matrix(self):
        cases = (
            (
                "validate_rich_menu_object",
                lambda gateway: gateway.validate(self.context, self.request),
                None,
                False,
            ),
            (
                "create_rich_menu",
                lambda gateway: gateway.create(self.context, self.request),
                {"richMenuId": "id"},
                True,
            ),
            (
                "get_rich_menu_list",
                lambda gateway: gateway.list_resources(self.context),
                {"richmenus": []},
                True,
            ),
            (
                "get_rich_menu",
                lambda gateway: gateway.get_resource(self.context, "id"),
                {"richMenuId": "id", "name": "marker"},
                True,
            ),
            (
                "set_default_rich_menu",
                lambda gateway: gateway.set_default(self.context, "id"),
                None,
                False,
            ),
            (
                "get_default_rich_menu_id",
                lambda gateway: gateway.get_default(self.context),
                {"richMenuId": "id"},
                True,
            ),
            (
                "cancel_default_rich_menu",
                lambda gateway: gateway.clear_default(self.context),
                None,
                False,
            ),
            ("delete_rich_menu", lambda gateway: gateway.delete(self.context, "id"), None, False),
        )
        failures = (
            ("4xx", ApiError(400), "rejected"),
            ("429", ApiError(429), "unknown"),
            ("5xx", ApiError(500), "unknown"),
            ("timeout", TimeoutError("matrix-timeout-canary"), "unknown"),
            ("connection", ConnectionError("matrix-connection-canary"), "unknown"),
        )
        for method, invoke, success, response_bearing in cases:
            for category, failure, expected in failures:
                with self.subTest(method=method, category=category):
                    factory = FakeFactory()
                    factory.json.responses[method] = failure
                    result = invoke(DefaultRichMenuGateway(factory))
                    self.assertEqual(getattr(result, "status", None), expected)
                    self.assertNotIn("canary", repr(result))
                    self.assertEqual(factory.close_calls, 1)

            with self.subTest(method=method, category="malformed"):
                factory = FakeFactory()
                factory.json.responses[method] = {"malformed": "matrix-canary"}
                malformed = invoke(DefaultRichMenuGateway(factory))
                self.assertEqual(
                    getattr(malformed, "status", None),
                    "unknown" if response_bearing else "accepted",
                )

            with self.subTest(method=method, category="close"):
                factory = FakeFactory()
                factory.json.responses[method] = success
                factory.close_error = RuntimeError("matrix-close-canary")
                closed = invoke(DefaultRichMenuGateway(factory))
                self.assertEqual(getattr(closed, "status", None), "unknown")
                self.assertNotIn("matrix-close-canary", repr(closed))

    # テストケース: default endpointの404と403を観測する。
    # 期待値: 404はdefaultなし、403は外部manager管理として分類する。
    def test_default_404_is_none_and_403_is_external_manager_default(self):
        for response, expected in (
            (ApiError(404), RichMenuDefaultNone),
            (ApiError(403), RichMenuDefaultExternal),
        ):
            with self.subTest(expected=expected.__name__):
                factory = FakeFactory()
                factory.json.responses["get_default_rich_menu_id"] = response
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
        self.assertEqual(upload_call[2]["_headers"], {"Content-Type": "image/png"})
        self.assertEqual(upload_call[2]["_request_timeout"], 5.0)

    def test_real_sdk_upload_receives_binary_content_type(self):
        from linebot.v3.messaging import MessagingApiBlob

        class RecordingApiClient:
            def __init__(self):
                self.call = None

            def call_api(self, *args, **kwargs):
                self.call = (args, kwargs)

        api_client = RecordingApiClient()
        blob = MessagingApiBlob(api_client)
        binary = b"png-binary-canary"
        blob.set_rich_menu_image(
            "rich-menu-id-canary",
            binary,
            _headers={"Content-Type": "image/png"},
            _request_timeout=5.0,
        )

        self.assertIsNotNone(api_client.call)
        args, kwargs = api_client.call
        self.assertEqual(args[4], {"Content-Type": "image/png"})
        self.assertEqual(kwargs["body"], binary)

    def test_download_404_is_observed_as_absent(self):
        self.factory.blob.responses["get_rich_menu_image"] = ApiError(404)

        result = self.gateway.download(self.context, "rich-menu-id-canary")

        self.assertIsInstance(result, ImageAbsent)

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

    # テストケース: upload/downloadへ4xx・429・5xx・timeout・connection・malformed・close失敗を注入する。
    # 期待値: blob endpointもJSONと同じ安全分類を使い、binaryやLINE IDを露出しない。
    def test_blob_endpoint_failure_matrix(self):
        from io import BytesIO

        from PIL import Image

        from linerichmenus.types import RenderedImage

        output = BytesIO()
        Image.new("RGBA", (800, 550), (20, 90, 120, 255)).save(output, format="PNG")
        image = RenderedImage("image/png", 800, 550, "b" * 64, output.getvalue())
        for category, failure, expected in (
            ("4xx", ApiError(400), "rejected"),
            ("429", ApiError(429), "unknown"),
            ("5xx", ApiError(500), "unknown"),
            ("timeout", TimeoutError("blob-timeout-canary"), "unknown"),
            ("connection", ConnectionError("blob-connection-canary"), "unknown"),
        ):
            for method in ("set_rich_menu_image", "get_rich_menu_image"):
                with self.subTest(method=method, category=category):
                    factory = FakeFactory()
                    factory.blob.responses[method] = failure
                    gateway = DefaultRichMenuGateway(factory)
                    result = (
                        gateway.upload(self.context, "id", image)
                        if method == "set_rich_menu_image"
                        else gateway.download(self.context, "id")
                    )
                    self.assertEqual(result.status, expected)
                    self.assertNotIn("canary", repr(result))
                    self.assertEqual(factory.calls, [("access-token-canary", 0)])

        malformed_upload = self.gateway.upload(self.context, "id", image)
        self.factory.blob.responses["get_rich_menu_image"] = b"malformed-blob-canary"
        malformed_download = self.gateway.download(self.context, "id")
        self.assertEqual(malformed_upload.status, "accepted")
        self.assertEqual(malformed_download.status, "unknown")

        for method, success in (
            ("set_rich_menu_image", None),
            ("get_rich_menu_image", BytesIO(output.getvalue())),
        ):
            with self.subTest(method=method, category="close"):
                factory = FakeFactory()
                factory.blob.responses[method] = success
                factory.close_error = RuntimeError("blob-close-canary")
                gateway = DefaultRichMenuGateway(factory)
                result = (
                    gateway.upload(self.context, "id", image)
                    if method == "set_rich_menu_image"
                    else gateway.download(self.context, "id")
                )
                self.assertEqual(result.status, "unknown")


class ContentDownloadTests(SimpleTestCase):
    @staticmethod
    def image_bytes(size=(800, 550), image_format="PNG"):
        from io import BytesIO

        from PIL import Image

        output = BytesIO()
        Image.new("RGB", size, (20, 90, 120)).save(output, format=image_format)
        return output.getvalue()

    def download(self, chunks=(), status=200, *, delay=0, timeout=5, error=None):
        import asyncio

        import httpx

        calls = []
        closed = []
        clients = []
        kwargs_seen = []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for chunk in chunks:
                    calls.append("read")
                    if delay:
                        await asyncio.sleep(delay)
                    if error:
                        raise error
                    yield chunk

            async def aclose(self):
                closed.append(True)

        def handle(request):
            calls.append(request)
            return httpx.Response(status, stream=Stream(), headers={"Content-Type": "text/html"})

        def factory(**kwargs):
            kwargs_seen.append(kwargs)
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(client)
            return client

        self.content_calls = calls
        self.content_closed = closed
        self.content_clients = clients
        self.content_options = kwargs_seen
        gateway = DefaultRichMenuGateway(content_client_factory=factory, timeout_seconds=timeout)
        return gateway.download_content(context(), "rich-menu-id-canary")

    # テストケース: PNG/JPEGを上流Content-Typeに依存せずstreamで取得する。
    # 期待値: 元bytesとcanonical digestがSDK downloadと一致し、一度だけ取得してcloseする。
    def test_original_png_and_jpeg_bytes_and_sdk_digest_are_preserved(self):
        from linerichmenus.gateway import DownloadedImage

        for image_format in ("PNG", "JPEG"):
            with self.subTest(image_format=image_format):
                binary = self.image_bytes(image_format=image_format)
                result = self.download((binary[:32], binary[32:]))
                self.assertIsInstance(result, DownloadedImage)
                self.assertEqual(result.binary, binary)
                self.assertEqual(result.content_type, f"image/{image_format.lower()}")
                factory = FakeFactory()
                factory.blob.responses["get_rich_menu_image"] = binary
                old_result = DefaultRichMenuGateway(factory).download(context(), "id")
                self.assertEqual(result.pixel_digest, old_result.pixel_digest)
                self.assertEqual(result.byte_size, len(binary))
                self.assertEqual(len(self.content_closed), 1)
                self.assertTrue(self.content_clients[0].is_closed)
                request = self.content_calls[0]
                self.assertEqual(request.method, "GET")
                self.assertEqual(
                    str(request.url),
                    "https://api-data.line.me/v2/bot/richmenu/rich-menu-id-canary/content",
                )
                self.assertEqual(request.headers["Authorization"], "Bearer access-token-canary")
                self.assertFalse(self.content_options[0]["follow_redirects"])
                self.assertFalse(self.content_options[0]["trust_env"])
                self.assertEqual(
                    request.extensions["timeout"],
                    dict.fromkeys(("connect", "read", "write", "pool"), 5.0),
                )
                self.assertNotIn("canary", repr(result))
                self.assertNotIn(str(binary), str(result))
                with self.assertRaises(TypeError):
                    pickle.dumps(result)

    # テストケース: 404、429、5xx、認証拒否、redirectを受け取る。
    # 期待値: bodyを読まず安全分類し、redirect追従・retryなしで必ずcloseする。
    def test_http_failure_matrix_does_not_read_or_retry(self):
        for status, expected in (
            (404, ImageAbsent()),
            (429, ImageObservationUnknown("rate_limited")),
            (500, ImageObservationUnknown()),
            (503, ImageObservationUnknown()),
            (401, GatewayRejected("line_rejected")),
            (403, GatewayRejected("line_rejected")),
            (302, ImageObservationUnknown()),
        ):
            with self.subTest(status=status):
                self.assertEqual(self.download((b"body-canary",), status), expected)
                self.assertEqual(len(self.content_calls), 1)
                self.assertEqual(len(self.content_closed), 1)
                self.assertTrue(self.content_clients[0].is_closed)

    # テストケース: 破損、禁止形式、範囲外寸法、過大bytesを取得する。
    # 期待値: 画像不正となり、上限+1 byteに達した後のchunkを読まない。
    def test_invalid_images_and_oversize_stop_before_further_reads(self):
        invalid = (
            b"broken-image-canary",
            b"<svg>image-canary</svg>",
            b"<html>image-canary</html>",
            self.image_bytes(image_format="GIF"),
            self.image_bytes((799, 250)),
            self.image_bytes((2501, 250)),
            self.image_bytes((800, 249)),
            self.image_bytes((800, 552)),
        )
        for binary in invalid:
            with self.subTest(length=len(binary)):
                self.assertEqual(self.download((binary,)), GatewayRejected("image_invalid"))
                self.assertTrue(self.content_clients[0].is_closed)
        self.assertEqual(
            self.download((b"x" * (1024 * 1024), b"x", b"must-not-read")),
            GatewayRejected("image_invalid"),
        )
        self.assertEqual(self.content_calls.count("read"), 2)
        self.assertEqual(len(self.content_closed), 1)

    # テストケース: 各readはtimeout未満だが累積時間が単一budgetを超えるstreamを取得する。
    # 期待値: readごとの猶予更新をせず、途中でtimeoutとなりresponse/clientをcloseする。
    def test_single_deadline_cancels_slow_drip_and_closes(self):
        import time

        started = time.monotonic()
        result = self.download((b"a",) * 20, delay=0.015, timeout=0.05)
        elapsed = time.monotonic() - started
        self.assertEqual(result, ImageObservationUnknown("timeout_unknown"))
        self.assertLess(elapsed, 0.25)
        self.assertLess(self.content_calls.count("read"), 20)
        self.assertEqual(len(self.content_closed), 1)
        self.assertTrue(self.content_clients[0].is_closed)

    # テストケース: HTTPXの各I/O timeoutと途中切断へ秘密canaryを含める。
    # 期待値: 安全な理由だけが返り、例外・token・bodyはログにも結果にも露出しない。
    def test_transport_failures_are_safe_and_close(self):
        import logging
        from unittest.mock import patch

        import httpx

        for error in (
            httpx.ConnectTimeout("token-canary"),
            httpx.ReadTimeout("body-canary"),
            httpx.WriteTimeout("body-canary"),
            httpx.PoolTimeout("body-canary"),
            httpx.ReadError("body-canary"),
        ):
            with self.subTest(error_type=type(error)), patch.object(logging.Logger, "_log") as log:
                result = self.download((b"image-canary",), error=error)
                expected = (
                    "timeout_unknown"
                    if isinstance(error, httpx.TimeoutException)
                    else "response_unknown"
                )
                self.assertEqual(result, ImageObservationUnknown(expected))
                self.assertNotIn("canary", repr(result))
                self.assertNotIn("access-token-canary", str(log.call_args_list))
                self.assertNotIn("body-canary", str(log.call_args_list))
                self.assertEqual(len(self.content_closed), 1)
                self.assertTrue(self.content_clients[0].is_closed)

    # テストケース: 寸法不正画像を既存SDK経路でdownloadする。
    # 期待値: pixel展開のloadを呼ぶ前に寸法で拒否し、既存のunknown型を保つ。
    def test_dimensions_and_format_are_checked_before_pixel_expansion(self):
        from unittest.mock import patch

        for binary in (self.image_bytes((2501, 250)), self.image_bytes(image_format="GIF")):
            factory = FakeFactory()
            factory.blob.responses["get_rich_menu_image"] = binary
            with patch(
                "PIL.Image.Image.load", side_effect=AssertionError("must-not-decode")
            ) as load:
                result = DefaultRichMenuGateway(factory).download(context(), "id")
            self.assertIsInstance(result, ImageObservationUnknown)
            load.assert_not_called()

    # テストケース: 許可境界の寸法とちょうど1 MiBのPNGを取得する。
    # 期待値: 境界を過剰に拒否せず、末尾を含む元bytesをそのまま返す。
    def test_exact_size_and_dimension_boundaries(self):
        from linerichmenus.gateway import DownloadedImage

        for size in ((800, 250), (2500, 1724)):
            with self.subTest(size=size):
                binary = self.image_bytes(size)
                binary += b"\x00" * (1024 * 1024 - len(binary))
                result = self.download((binary,))
                self.assertIsInstance(result, DownloadedImage)
                self.assertEqual(result.binary, binary)
                self.assertEqual((result.width, result.height), size)

    # テストケース: 不正IDとpath記号を含むIDで閲覧を要求する。
    # 期待値: 不正IDは接続せず、許可IDの記号はencodeして固定host/path内だけで使う。
    def test_resource_id_cannot_change_host_or_path(self):
        import httpx

        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(404)

        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

        gateway = DefaultRichMenuGateway(content_client_factory=factory)
        for invalid in (None, "", " ", ".", "..", "あ", "x" * 129, "x\n"):
            self.assertEqual(
                gateway.download_content(context(), invalid), GatewayRejected("invalid_input")
            )
        self.assertEqual(requests, [])
        result = gateway.download_content(context(), "id/../?secret#fragment%")
        self.assertEqual(result, ImageAbsent())
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "api-data.line.me")
        self.assertEqual(
            requests[0].url.raw_path,
            b"/v2/bot/richmenu/id%2F..%2F%3Fsecret%23fragment%25/content",
        )
        self.assertEqual(requests[0].url.query, b"")

    # テストケース: headers受信前の停止と接続失敗、response/clientのclose失敗を発生させる。
    # 期待値: timeoutと安全な失敗へ分類し、取得を再試行せずclientもcloseする。
    def test_header_timeout_and_close_failures(self):
        import asyncio

        import httpx

        for phase in ("headers", "connect", "response_close", "client_close"):
            calls = []
            response_closes = []
            client_closes = []
            binary = self.image_bytes()

            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield binary

                async def aclose(self):
                    response_closes.append(True)
                    if phase == "response_close":
                        raise RuntimeError("close-secret-canary")

            async def handle(request):
                calls.append(request)
                if phase == "headers":
                    await asyncio.sleep(0.2)
                if phase == "connect":
                    raise httpx.ConnectError("connect-secret-canary")
                return httpx.Response(200, stream=Stream())

            class Client(httpx.AsyncClient):
                async def __aexit__(self, *args):
                    try:
                        await super().__aexit__(*args)
                    finally:
                        client_closes.append(True)
                    if phase == "client_close":
                        raise RuntimeError("client-secret-canary")

            def factory(**kwargs):
                return Client(transport=httpx.MockTransport(handle), **kwargs)

            with self.subTest(phase=phase):
                gateway = DefaultRichMenuGateway(
                    content_client_factory=factory, timeout_seconds=0.03
                )
                result = gateway.download_content(context(), "id")
                expected = "timeout_unknown" if phase == "headers" else "response_unknown"
                self.assertEqual(result, ImageObservationUnknown(expected))
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(client_closes), 1)
                self.assertEqual(
                    len(response_closes), int(phase in {"response_close", "client_close"})
                )
                self.assertNotIn("canary", repr(result))

    # テストケース: 個別timeoutが5秒を超える設定で新downloadを開始する。
    # 期待値: 閲覧取得の総budgetと各I/O timeoutは5秒を超えない。
    def test_total_budget_never_exceeds_five_seconds(self):
        self.assertEqual(self.download((), status=404, timeout=30), ImageAbsent())
        self.assertEqual(self.content_options[0]["timeout"].read, 5)
        self.assertEqual(self.content_options[0]["timeout"].connect, 5)
