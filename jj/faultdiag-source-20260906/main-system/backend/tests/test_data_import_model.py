import unittest

from app.models.dataset_import import DatasetImport


class DatasetImportModelTests(unittest.TestCase):
    def test_table_name_and_required_columns(self):
        cols = DatasetImport.__table__.columns
        self.assertEqual(DatasetImport.__tablename__, "dataset_imports")
        for name in [
            "import_id",
            "original_filename",
            "storage_path",
            "sha256",
            "is_deleted",
        ]:
            self.assertIn(name, cols)

    def test_sha256_deleted_has_index_not_unique_constraint(self):
        index_names = {idx.name for idx in DatasetImport.__table__.indexes}
        self.assertIn("idx_dataset_imports_sha256_is_deleted", index_names)

        unique_constraints = {
            c.name
            for c in DatasetImport.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        }
        self.assertNotIn("uq_dataset_imports_sha256_active", unique_constraints)


if __name__ == "__main__":
    unittest.main()
