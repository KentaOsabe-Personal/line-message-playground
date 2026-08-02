from io import BytesIO
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from linerichmenus.catalog import DefaultTemplateCatalog
from linerichmenus.renderer import DefaultDeterministicRenderer
from linerichmenus.types import (
    NormalizedTemplate,
    RenderRejected,
    RenderedImage,
    TemplateFieldValue,
    TemplateInput,
    TemplateReference,
)


class DeterministicRendererTests(SimpleTestCase):
    def setUp(self):
        self.catalog = DefaultTemplateCatalog()
        self.renderer = DefaultDeterministicRenderer(catalog=self.catalog)

    # テストケース: 同じ日本語入力を時刻に依存せず二度描画する。
    # 期待値: canonical pixel digestとmetadataなしPNGが完全一致する。
    def test_same_input_produces_identical_canonical_image(self):
        template = self._normalized(
            "jp-link-three",
            (
                ("ご利用案内", "https://example.com/guide"),
                ("商品一覧", "https://example.com/items"),
                ("お問い合わせ", "https://example.com/contact"),
            ),
        )

        first = self.renderer.render(template)
        second = self.renderer.render(template)

        self.assertIsInstance(first, RenderedImage)
        self.assertEqual(first, second)
        self.assertEqual(first.content_type, "image/png")
        self.assertEqual((first.width, first.height), (2500, 843))
        self.assertEqual(
            first.pixel_digest,
            "a85e7f1617185cb699971e5a0887d8759ee25ab17279e5ed1e229443c36ec6dc",
        )
        self.assertLessEqual(len(first.binary), 1024 * 1024)
        with Image.open(BytesIO(first.binary)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.size, (2500, 843))
            self.assertEqual(image.info, {})

    # テストケース: 同じ表示名でもtemplate版または領域配置を変えて描画する。
    # 期待値: digestがtemplate ID/versionとcanonical pixelを識別する。
    def test_digest_binds_template_reference_and_pixels(self):
        one = self.renderer.render(
            self._normalized("jp-link-one", (("案内", "https://example.com"),))
        )
        two = self.renderer.render(
            self._normalized(
                "jp-link-two",
                (("案内", "https://example.com"), ("案内", "https://example.com")),
            )
        )

        self.assertNotEqual(one.pixel_digest, two.pixel_digest)

    # テストケース: 固定fontのcmapに存在しないcode pointを描画する。
    # 期待値: fallback glyphを使わず該当fieldの安全な理由で拒否する。
    def test_rejects_unsupported_glyph_before_rendering(self):
        template = NormalizedTemplate(
            reference=TemplateReference("jp-link-one", 1),
            fields=(TemplateFieldValue("😀", "https://example.com"),),
        )

        result = self.renderer.render(template)

        self.assertIsInstance(result, RenderRejected)
        self.assertEqual(
            [(error.field, error.reason) for error in result.errors],
            [("area1.displayName", "unsupported_glyph")],
        )

    # テストケース: PNG encoderが破損binaryまたは1MB超過結果を返す。
    # 期待値: encode後のLINE画像制約検証で安全に拒否する。
    def test_revalidates_encoded_png_constraints(self):
        template = self._normalized("jp-link-one", (("案内", "https://example.com"),))
        for encoded in (b"not-a-png", b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(encoded)):
                with patch("linerichmenus.renderer._encode_png", return_value=encoded):
                    result = self.renderer.render(template)
                self.assertIsInstance(result, RenderRejected)
                self.assertEqual(result.code, "image_invalid")

    # テストケース: cmap内で最大advanceのglyphを上限20 code point入力する。
    # 期待値: 固定layoutから例外を漏らさず3分割領域へ決定的に描画できる。
    def test_renders_maximum_advance_glyph_at_display_name_limit(self):
        result = self.renderer.render(
            self._normalized(
                "jp-link-three",
                (
                    ("\u2e3b" * 20, "https://example.com/one"),
                    ("\u2e3b" * 20, "https://example.com/two"),
                    ("\u2e3b" * 20, "https://example.com/three"),
                ),
            )
        )

        self.assertIsInstance(result, RenderedImage)

    # テストケース: PNG parserがdecompression bombを検出する。
    # 期待値: parser例外を境界外へ漏らさず安全な画像拒否へ縮約する。
    def test_rejects_decompression_bomb_png_safely(self):
        template = self._normalized("jp-link-one", (("案内", "https://example.com"),))
        with patch(
            "linerichmenus.renderer.Image.open",
            side_effect=Image.DecompressionBombError("oversized dimensions"),
        ):
            result = self.renderer.render(template)

        self.assertIsInstance(result, RenderRejected)
        self.assertEqual(result.code, "image_invalid")

    # テストケース: startupで固定画像runtimeの完全性を確認できない状態にする。
    # 期待値: 描画を開始せず安全な生成拒否になる。
    def test_fails_closed_when_runtime_prerequisites_are_unavailable(self):
        template = self._normalized("jp-link-one", (("案内", "https://example.com"),))
        with patch("linerichmenus.renderer.runtime_prerequisites_available", return_value=False):
            result = self.renderer.render(template)

        self.assertIsInstance(result, RenderRejected)
        self.assertEqual(result.code, "image_invalid")

    # テストケース: render結果のデバッグ表現を生成する。
    # 期待値: PNG binaryやbase64相当をreprへ含めない。
    def test_rendered_image_repr_never_exposes_binary(self):
        rendered = self.renderer.render(
            self._normalized("jp-link-one", (("秘密の案内", "https://example.com/private"),))
        )

        representation = repr(rendered)
        self.assertNotIn("PNG", representation)
        self.assertNotIn("iVBOR", representation)
        self.assertNotIn("秘密の案内", representation)

    def _normalized(self, template_id, values):
        fields = {
            f"area{index}": {"displayName": display_name, "uri": uri}
            for index, (display_name, uri) in enumerate(values, start=1)
        }
        return self.catalog.normalize(
            TemplateInput(TemplateReference(template_id, 1), fields)
        )
