import os
from pathlib import Path
import subprocess
import sys


DASHBOARD_ROOT = Path(__file__).resolve().parents[1]


def test_container_exposes_dashboard_package_parent_to_streamlit() -> None:
    if DASHBOARD_ROOT != Path("/app/dashboard"):
        return

    package_parent = str(DASHBOARD_ROOT.parent)
    python_path = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    assert package_parent in python_path

    result = subprocess.run(
        [sys.executable, "-c", "import dashboard.api"],
        cwd=DASHBOARD_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
