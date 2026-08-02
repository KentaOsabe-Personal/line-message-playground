from dataclasses import FrozenInstanceError

from django.test import SimpleTestCase

from linerichmenus.catalog import DefaultTemplateCatalog
from linerichmenus.types import InputRejected, TemplateInput, TemplateReference


class TemplateCatalogDescriptorTests(SimpleTestCase):
    # テストケース: 組み込みテンプレートを列挙する。
    # 期待値: 3種類だけが安定順で版・表示情報・入力上限とともに返る。
    def test_lists_exactly_three_versioned_templates_in_stable_order(self):
        descriptors = DefaultTemplateCatalog().list_templates()

        self.assertEqual(
            [descriptor.reference for descriptor in descriptors],
            [
                TemplateReference("jp-link-one", 1),
                TemplateReference("jp-link-two", 1),
                TemplateReference("jp-link-three", 1),
            ],
        )
        self.assertEqual([len(item.areas) for item in descriptors], [1, 2, 3])
        for descriptor in descriptors:
            self.assertTrue(descriptor.display_name)
            self.assertEqual((descriptor.width, descriptor.height), (2500, 843))
            self.assertEqual(descriptor.display_name_limit, 20)
            self.assertEqual(descriptor.uri_limit, 1000)
            self.assertEqual(
                descriptor.required_fields,
                tuple(area.field_name for area in descriptor.areas),
            )
            self.assertTrue(all(area.description for area in descriptor.areas))

    # テストケース: 各テンプレートの領域を左から順に検査する。
    # 期待値: 重複・gap・canvas外がなく、3分割幅は834/833/833になる。
    def test_areas_cover_canvas_without_overlap_or_gap(self):
        descriptors = DefaultTemplateCatalog().list_templates()

        for descriptor in descriptors:
            cursor = 0
            for area in descriptor.areas:
                self.assertEqual(area.x, cursor)
                self.assertEqual(area.y, 0)
                self.assertEqual(area.height, descriptor.height)
                cursor += area.width
            self.assertEqual(cursor, descriptor.width)
        self.assertEqual(
            [area.width for area in descriptors[2].areas],
            [834, 833, 833],
        )

    # テストケース: 未知IDまたは未知版でカタログを検索する。
    # 期待値: 既知の別版へfallbackせず不存在として返る。
    def test_unknown_template_or_version_never_falls_back(self):
        catalog = DefaultTemplateCatalog()

        self.assertIsNone(catalog.get(TemplateReference("unknown", 1)))
        self.assertIsNone(catalog.get(TemplateReference("jp-link-one", 2)))

    # テストケース: 公開されたdescriptorの座標を変更する。
    # 期待値: immutable値として変更が拒否される。
    def test_descriptors_are_immutable(self):
        descriptor = DefaultTemplateCatalog().list_templates()[0]

        with self.assertRaises(FrozenInstanceError):
            descriptor.width = 1


class TemplateNormalizationTests(SimpleTestCase):
    def setUp(self):
        self.catalog = DefaultTemplateCatalog()

    # テストケース: trimとNFCが必要な2領域の入力を正規化する。
    # 期待値: template順のimmutable field列へ変換される。
    def test_normalizes_valid_fields_in_template_order(self):
        command = TemplateInput(
            reference=TemplateReference("jp-link-two", 1),
            fields={
                "area2": {"displayName": "  会社概要  ", "uri": " https://example.com/about "},
                "area1": {"displayName": "  カ\u3099イド  ", "uri": "https://example.com/guide"},
            },
        )

        result = self.catalog.normalize(command)

        self.assertEqual(
            [(field.display_name, field.uri) for field in result.fields],
            [
                ("ガイド", "https://example.com/guide"),
                ("会社概要", "https://example.com/about"),
            ],
        )

    # テストケース: 必須area欠落、余剰area、area内余剰keyを渡す。
    # 期待値: fallbackせず各pathの安全な理由をまとめて返す。
    def test_rejects_missing_and_extra_fields_strictly(self):
        result = self.catalog.normalize(
            TemplateInput(
                reference=TemplateReference("jp-link-two", 1),
                fields={
                    "area1": {
                        "displayName": "案内",
                        "uri": "https://example.com",
                        "action": "postback",
                    },
                    "area3": {"displayName": "余剰", "uri": "https://example.com/extra"},
                },
            )
        )

        self.assertIsInstance(result, InputRejected)
        self.assertEqual(
            {(error.field, error.reason) for error in result.errors},
            {
                ("area2", "required"),
                ("area3", "unexpected"),
                ("area1.action", "unexpected"),
            },
        )

    # テストケース: 空表示名、長すぎる表示名・URIを入力する。
    # 期待値: trim/NFC後のUnicode code point上限をfield単位で拒否する。
    def test_rejects_empty_and_over_limit_values(self):
        cases = (
            ("   ", "https://example.com", "area1.displayName", "required"),
            ("あ" * 21, "https://example.com", "area1.displayName", "too_long"),
            ("案内", "https://example.com/" + "a" * 981, "area1.uri", "too_long"),
        )
        for display_name, uri, field, reason in cases:
            with self.subTest(field=field, reason=reason):
                result = self._normalize_one(display_name, uri)
                self.assertIsInstance(result, InputRejected)
                self.assertIn((field, reason), {(item.field, item.reason) for item in result.errors})

    # テストケース: URI actionとして安全でない各URLを入力する。
    # 期待値: absolute HTTPS・host必須、userinfo/control/空白なし以外を拒否する。
    def test_rejects_invalid_uri_actions(self):
        invalid_uris = (
            "http://example.com",
            "/relative",
            "https:///missing-host",
            "https://user:pass@example.com/path",
            "https://example.com/path\nnext",
            "https://example.com/path with-space",
        )
        for uri in invalid_uris:
            with self.subTest(uri=repr(uri)):
                result = self._normalize_one("案内", uri)
                self.assertIsInstance(result, InputRejected)
                self.assertIn(
                    ("area1.uri", "invalid_uri"),
                    {(item.field, item.reason) for item in result.errors},
                )

    # テストケース: 未知template ID/versionとmapping以外の入力shapeを渡す。
    # 期待値: 既知版へ置換せず安全なfield-level rejectionになる。
    def test_rejects_unknown_template_and_invalid_input_shape(self):
        unknown = self.catalog.normalize(
            TemplateInput(TemplateReference("jp-link-one", 2), {})
        )
        malformed = self.catalog.normalize(
            TemplateInput(TemplateReference("jp-link-one", 1), {"area1": "unsafe"})
        )

        self.assertEqual(
            [(error.field, error.reason) for error in unknown.errors],
            [("template", "unknown")],
        )
        self.assertEqual(
            [(error.field, error.reason) for error in malformed.errors],
            [("area1", "invalid")],
        )

    # テストケース: 1領域と複数領域へ明示的nullを入力する。
    # 期待値: 例外や不完全なnormalized resultにせず該当pathを安全に拒否する。
    def test_rejects_explicit_null_without_partial_normalization(self):
        one_area = self._normalize_one(None, "https://example.com")
        two_areas = self.catalog.normalize(
            TemplateInput(
                TemplateReference("jp-link-two", 1),
                {
                    "area1": {"displayName": "案内", "uri": None},
                    "area2": {"displayName": "会社", "uri": "https://example.com"},
                },
            )
        )

        self.assertIsInstance(one_area, InputRejected)
        self.assertEqual(
            [(error.field, error.reason) for error in one_area.errors],
            [("area1.displayName", "required")],
        )
        self.assertIsInstance(two_areas, InputRejected)
        self.assertEqual(
            [(error.field, error.reason) for error in two_areas.errors],
            [("area1.uri", "required")],
        )

    def _normalize_one(self, display_name, uri):
        return self.catalog.normalize(
            TemplateInput(
                TemplateReference("jp-link-one", 1),
                {"area1": {"displayName": display_name, "uri": uri}},
            )
        )
