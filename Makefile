.PHONY: test-cloud lint typecheck security-audit local-acceptance
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
local-acceptance:
	@echo "Requires operator approval on Ubuntu 24.04 host; see LOCAL_ACCEPTANCE.md"; exit 2
