.PHONY: test-cloud lint typecheck wheel install-wheel check

test-cloud:
	PYTHONPATH=src .venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check src tests scripts

typecheck:
	.venv/bin/mypy src

wheel:
	.venv/bin/python -m pip wheel --no-deps --no-build-isolation --no-index . --wheel-dir dist

install-wheel: wheel
	.venv/bin/python scripts/check_wheel.py

check: test-cloud lint typecheck install-wheel
