import unittest

from app.api.v1.log_analysis import router
from app.schemas.log_analysis import AnalyzeTextRequest, ManualScoreAnalyzeRequest


class LogAnalysisRequestModelBindingTests(unittest.TestCase):
    def _body_model_for_endpoint(self, endpoint_name):
        for route in router.routes:
            if getattr(route.endpoint, "__name__", None) == endpoint_name:
                self.assertEqual(len(route.dependant.body_params), 1)
                return route.dependant.body_params[0].type_
        self.fail(f"Route for endpoint {endpoint_name} not found")

    def _routes_for_path_and_method(self, path, method):
        return [
            route
            for route in router.routes
            if getattr(route, "path", None) == path
            and method in getattr(route, "methods", set())
        ]

    def test_analyze_text_route_uses_analyze_text_request(self):
        self.assertIs(self._body_model_for_endpoint("analyze_text"), AnalyzeTextRequest)

    def test_analyze_log_post_path_binds_only_score_endpoint(self):
        routes = self._routes_for_path_and_method("/log-analysis/analyze/log", "POST")
        self.assertEqual(len(routes), 1)

        route = routes[0]
        self.assertEqual(route.endpoint.__name__, "analyze_log_score")
        self.assertEqual(len(route.dependant.body_params), 1)
        self.assertIs(route.dependant.body_params[0].type_, ManualScoreAnalyzeRequest)


if __name__ == "__main__":
    unittest.main()
