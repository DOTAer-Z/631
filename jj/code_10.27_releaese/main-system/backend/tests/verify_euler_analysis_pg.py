"""Run analysis tests in an isolated PG schema, rolled back on completion.

No real LLM calls, changes to model configuration or persistent user-data writes.
"""
import unittest
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine
from app.models import DiagnosisRecord, EulerArtifactImport, Run
from app.models.euler_analysis import EulerAnalysisBatch, EulerAnalysisResult
from tests.test_euler_analysis import EulerAnalysisTests
from app.services import euler_analysis_service as service


class PostgresEulerAnalysisTests(EulerAnalysisTests):
    def setUp(self):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.schema = "test_euler_analysis_" + uuid4().hex
        self.connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.connection.execute(text(f'SET LOCAL search_path TO "{self.schema}", public'))
        for model in (Run, EulerArtifactImport, DiagnosisRecord, EulerAnalysisBatch, EulerAnalysisResult):
            model.__table__.create(self.connection)
        self.connection.execute(text("CREATE TABLE mongo_docs (pk BIGSERIAL PRIMARY KEY, "
                                     "collection TEXT, run_id TEXT, doc JSONB)"))
        self.db = Session(bind=self.connection, join_transaction_mode="create_savepoint")

    def tearDown(self):
        self.db.close()
        self.transaction.rollback()
        self.connection.close()

    def test_postgres_lock_blocks_second_connection(self):
        self.assertTrue(service._try_lock(self.db))
        with engine.connect() as connection:
            with Session(bind=connection) as other:
                self.assertFalse(service._try_lock(other))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PostgresEulerAnalysisTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
