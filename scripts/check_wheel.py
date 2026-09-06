"""Install the built wheel in a fresh venv and exercise it outside source paths."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    wheels = list((project / "dist").glob("qsx_alpine-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one qsx_alpine wheel in dist")
    with tempfile.TemporaryDirectory(prefix="qsx-wheel-") as temporary:
        root = Path(temporary)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], check=True)
        python = root / "venv/bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                        str(wheels[0])], check=True, cwd=root, env=environment)
        executable = root / "venv/bin/qsx-alpine"
        for command in ("--version", "--help", "doctor"):
            subprocess.run([str(executable), command], check=True, cwd=root,
                           env=environment, stdout=subprocess.DEVNULL)
        subprocess.run([str(python), "-c",
                        ("import importlib.util; import qsx_alpine.cli; "
                         "assert importlib.util.find_spec('qsx') is None")],
                       check=True, cwd=root, env=environment)
    print("PASS: standalone wheel installed and exercised outside source tree")


if __name__ == "__main__":
    main()
