import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.core import checks
from django.test import SimpleTestCase

from linerichmenus.apps import (
    EXPECTED_PILLOW_VERSION,
    FONT_ASSET_DIR,
    validate_runtime_prerequisites,
)


class RichMenuRuntimePrerequisiteTests(SimpleTestCase):
    # テストケース: Backend buildに同梱された固定依存とフォント資産を検証する。
    # 期待値: Pillow版、font版・digest、OFLがすべて一致し、失敗理由がない。
    def test_fixed_runtime_assets_are_valid(self):
        self.assertEqual(
            validate_runtime_prerequisites(
                asset_dir=FONT_ASSET_DIR,
                pillow_version=EXPECTED_PILLOW_VERSION,
            ),
            (),
        )

    # テストケース: 固定版と異なるPillow runtimeを検証する。
    # 期待値: binaryや資産名を診断へ含めず、版不一致としてfail closedになる。
    def test_wrong_pillow_version_fails_closed_without_binary_details(self):
        failures = validate_runtime_prerequisites(
            asset_dir=FONT_ASSET_DIR,
            pillow_version="0.0.0",
        )

        self.assertEqual([failure.code for failure in failures], ["pillow_version"])
        self.assertNotIn("NotoSans", repr(failures))

    # テストケース: 同梱font binaryが改変された状態を検証する。
    # 期待値: 改変内容を診断へ含めず、digest不一致としてfail closedになる。
    def test_tampered_font_digest_fails_closed(self):
        with self._copied_assets() as asset_dir:
            font_path = asset_dir / "NotoSansJP-Regular.otf"
            font_path.write_bytes(font_path.read_bytes() + b"tampered")

            failures = validate_runtime_prerequisites(
                asset_dir=asset_dir,
                pillow_version=EXPECTED_PILLOW_VERSION,
            )

        self.assertIn("font_digest", {failure.code for failure in failures})
        self.assertNotIn("tampered", repr(failures))

    # テストケース: OFL資産が欠落した状態を検証する。
    # 期待値: ライセンス未確認としてfail closedになる。
    def test_missing_license_fails_closed(self):
        with self._copied_assets() as asset_dir:
            (asset_dir / "OFL-1.1.txt").unlink()

            failures = validate_runtime_prerequisites(
                asset_dir=asset_dir,
                pillow_version=EXPECTED_PILLOW_VERSION,
            )

        self.assertIn("font_license", {failure.code for failure in failures})

    # テストケース: Django起動時にlinerichmenus appとsystem checkが登録されることを検証する。
    # 期待値: 正しいbuildでは登録済みcheckがエラーなしで完了する。
    def test_runtime_check_is_registered_at_startup(self):
        from linerichmenus.apps import check_runtime_prerequisites

        self.assertTrue(apps.is_installed("linerichmenus"))
        self.assertIn(check_runtime_prerequisites, checks.registry.registry.get_checks())
        self.assertEqual(check_runtime_prerequisites(), [])

    # テストケース: startup check中にPillow固定版が一致しない状態を検証する。
    # 期待値: 登録済みDjango checkが安定した安全エラーを返す。
    def test_registered_check_rejects_wrong_pillow_version(self):
        with patch("PIL.__version__", "0.0.0"):
            errors = checks.run_checks()

        self.assertIn("linerichmenus.E001", {error.id for error in errors})

    # テストケース: startup check中にfont版とdigestが一致しない状態を検証する。
    # 期待値: 登録済みDjango checkが版・完全性の両方を安全エラーにする。
    def test_registered_check_rejects_wrong_font_version_and_digest(self):
        with self._copied_assets() as asset_dir:
            font_path = asset_dir / "NotoSansJP-Regular.otf"
            font_path.write_bytes(
                font_path.read_bytes().replace(
                    "Version 2.004".encode("utf-16-be"),
                    "Version 9.999".encode("utf-16-be"),
                    1,
                )
            )
            with patch("linerichmenus.apps.FONT_ASSET_DIR", asset_dir):
                errors = checks.run_checks()

        error_ids = {error.id for error in errors}
        self.assertIn("linerichmenus.E003", error_ids)
        self.assertIn("linerichmenus.E004", error_ids)

    # テストケース: startup check中にOFL資産が欠落した状態を検証する。
    # 期待値: 登録済みDjango checkがライセンス未確認の安全エラーを返す。
    def test_registered_check_rejects_missing_license(self):
        with self._copied_assets() as asset_dir:
            (asset_dir / "OFL-1.1.txt").unlink()
            with patch("linerichmenus.apps.FONT_ASSET_DIR", asset_dir):
                errors = checks.run_checks()

        self.assertIn("linerichmenus.E005", {error.id for error in errors})

    def _copied_assets(self):
        temporary_directory = TemporaryDirectory()
        destination = Path(temporary_directory.name)
        shutil.copytree(FONT_ASSET_DIR, destination, dirs_exist_ok=True)

        class _AssetsContext:
            def __enter__(self):
                return destination

            def __exit__(self, exc_type, exc, traceback):
                temporary_directory.cleanup()

        return _AssetsContext()
