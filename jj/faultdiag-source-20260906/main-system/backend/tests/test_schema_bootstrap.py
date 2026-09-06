import unittest
from unittest.mock import patch

import create_tables


class SchemaBootstrapTests(unittest.TestCase):
    @patch("create_tables.command.stamp")
    @patch("create_tables.command.upgrade")
    @patch("create_tables._patch_columns")
    @patch("create_tables.Base.metadata.create_all")
    @patch("create_tables._has_alembic_version_table", return_value=False)
    def test_legacy_database_is_created_then_stamped(
        self, _has_version, create_all, patch_columns, upgrade, stamp
    ):
        calls = []
        create_all.side_effect = lambda **_kwargs: calls.append("create_all")
        patch_columns.side_effect = lambda: calls.append("patch_columns")
        stamp.side_effect = lambda *_args: calls.append("stamp")

        create_tables.initialize_schema()

        upgrade.assert_not_called()
        create_all.assert_called_once_with(bind=create_tables.engine)
        patch_columns.assert_called_once_with()
        stamp.assert_called_once()
        self.assertEqual(calls, ["create_all", "patch_columns", "stamp"])

    @patch("create_tables.command.stamp")
    @patch("create_tables.command.upgrade")
    @patch("create_tables._patch_columns")
    @patch("create_tables.Base.metadata.create_all")
    @patch("create_tables._has_alembic_version_table", return_value=True)
    def test_versioned_database_upgrades_without_restamping(
        self, _has_version, create_all, patch_columns, upgrade, stamp
    ):
        calls = []
        upgrade.side_effect = lambda *_args: calls.append("upgrade")
        create_all.side_effect = lambda **_kwargs: calls.append("create_all")
        patch_columns.side_effect = lambda: calls.append("patch_columns")

        create_tables.initialize_schema()

        upgrade.assert_called_once()
        create_all.assert_called_once_with(bind=create_tables.engine)
        patch_columns.assert_called_once_with()
        stamp.assert_not_called()
        self.assertEqual(calls, ["upgrade", "create_all", "patch_columns"])


if __name__ == "__main__":
    unittest.main()
