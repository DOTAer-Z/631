from __future__ import annotations

import subprocess
import sys


def test_importing_training_child_does_not_import_transformers():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "assert 'transformers' not in sys.modules; "
                "import app.training_worker.child; "
                "assert 'transformers' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
