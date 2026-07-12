from rest_framework.test import APITestCase


class HealthViewTests(APITestCase):
    # テストケース: health APIへGETリクエストを送信する。
    # 期待値: HTTP 200と {"status": "ok"} が返される。
    def test_health(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
