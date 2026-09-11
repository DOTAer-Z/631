import unittest

from app.api.v1.data_import import router
from app.schemas.data_import import DataImportUpdateRequest


class DataImportRequestModelBindingTests(unittest.TestCase):
    def test_patch_route_binds_update_schema(self):
        target = [
            r for r in router.routes
            if getattr(r, "path", None) == "/data-imports/{import_id}"
            and "PATCH" in getattr(r, "methods", set())
        ]
        self.assertEqual(len(target), 1)
        self.assertIs(target[0].dependant.body_params[0].type_, DataImportUpdateRequest)


if __name__ == "__main__":
    unittest.main()
