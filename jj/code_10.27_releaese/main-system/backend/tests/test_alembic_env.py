from contextlib import nullcontext
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import alembic


class AlembicEnvTests(unittest.TestCase):
    def test_percent_encoded_password_is_not_doubled_in_alembic_url(self):
        config = SimpleNamespace(
            config_file_name=None,
            file_config=None,
            options={},
        )
        config.set_main_option = lambda key, value: config.options.__setitem__(key, value)
        config.get_main_option = lambda key: config.options[key]
        context = SimpleNamespace(
            config=config,
            is_offline_mode=lambda: True,
            configure=Mock(),
            begin_transaction=nullcontext,
            run_migrations=Mock(),
        )
        env_path = Path(__file__).parents[1] / "alembic" / "env.py"

        with (
            patch.object(alembic, "context", context),
            patch(
                "app.database.build_primary_database_url",
                return_value="postgresql+psycopg2://user:pa%40ss@db:5432/fault_diagnosis",
            ),
        ):
            runpy.run_path(str(env_path))

        url = config.options["sqlalchemy.url"]
        self.assertIn("pa%40ss", url)
        self.assertNotIn("pa%%40ss", url)


if __name__ == "__main__":
    unittest.main()
