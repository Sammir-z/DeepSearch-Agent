import unittest


class HealthRouteTests(unittest.TestCase):
    def test_live_health_route_returns_ok_without_dependencies(self):
        from app.api import server

        response = server.health_live()
        self.assertEqual(response, {"status": "ok"})

    def test_live_health_route_is_registered(self):
        from app.api import server

        routes = {
            (route.path, tuple(sorted(getattr(route, "methods", set()) or set())))
            for route in server.app.routes
        }
        self.assertIn(("/health/live", ("GET",)), routes)
