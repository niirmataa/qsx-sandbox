.PHONY: test-cloud lint typecheck security-audit install-wheel local-acceptance
test-cloud:
	PYTHONPATH=src .venv/bin/python -m pytest -q
lint:
	.venv/bin/ruff check src tests
typecheck:
	.venv/bin/mypy src
security-audit:
	git diff --check
	! git grep -nE '(BEGIN (RSA |OPENSSH )?PRIVATE KEY|sk-[A-Za-z0-9]{20,})' -- ':!tests'
	.venv/bin/python -m compileall -q src tests
install-wheel:
	rm -rf build/wheel-test dist
	.venv/bin/python -m build --wheel --no-isolation
	python3 -m venv build/wheel-test
	build/wheel-test/bin/pip install --no-index --no-deps dist/qsx-*.whl
	cd /tmp && $(CURDIR)/build/wheel-test/bin/qsx --help >/dev/null
	cd /tmp && $(CURDIR)/build/wheel-test/bin/qsx doctor >/dev/null
local-acceptance:
	@echo "Requires operator approval on Ubuntu 24.04 host; see LOCAL_ACCEPTANCE.md"; exit 2
