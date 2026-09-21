from django.test import SimpleTestCase

from textjudgmentlab.limits import LabLimitPermit, LabLimitRejected, LabLimits


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class LabLimitsTests(SimpleTestCase):
    # テストケース: 同じ本人が判定実行中にもう一件開始する。
    # 期待値: 同時実行は1件だけ許可し、解放後は再び取得できる。
    def test_allows_one_concurrent_call_per_owner(self) -> None:
        limits = LabLimits()

        first = limits.acquire("owner-a")
        blocked = limits.acquire("owner-a")
        other_owner = limits.acquire("owner-b")

        self.assertIsInstance(first, LabLimitPermit)
        self.assertEqual(blocked, LabLimitRejected("concurrent"))
        self.assertIsInstance(other_owner, LabLimitPermit)
        assert isinstance(first, LabLimitPermit)
        assert isinstance(other_owner, LabLimitPermit)
        first.release()
        other_owner.release()
        self.assertIsInstance(limits.acquire("owner-a"), LabLimitPermit)

    # テストケース: 同じ本人が60秒内に11件開始する。
    # 期待値: 失敗分を含む最初の10件だけ許可し、期限経過後に再開できる。
    def test_limits_ten_starts_in_rolling_sixty_seconds(self) -> None:
        clock = _Clock(100.0)
        limits = LabLimits(monotonic_clock=clock)

        for _ in range(10):
            permit = limits.acquire("owner-a")
            self.assertIsInstance(permit, LabLimitPermit)
            assert isinstance(permit, LabLimitPermit)
            permit.release()

        self.assertEqual(limits.acquire("owner-a"), LabLimitRejected("rate"))
        clock.value = 160.001
        self.assertIsInstance(limits.acquire("owner-a"), LabLimitPermit)

    # テストケース: permit利用中に例外が発生する。
    # 期待値: context終了時に実行枠を必ず解放する。
    def test_context_manager_releases_permit_after_exception(self) -> None:
        limits = LabLimits()

        with self.assertRaisesRegex(RuntimeError, "canary"):
            permit = limits.acquire("owner-a")
            assert isinstance(permit, LabLimitPermit)
            with permit:
                raise RuntimeError("canary")

        self.assertIsInstance(limits.acquire("owner-a"), LabLimitPermit)
