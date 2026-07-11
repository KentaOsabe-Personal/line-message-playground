from rest_framework.test import APITestCase


class HealthViewTests(APITestCase):
    def test_health(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
