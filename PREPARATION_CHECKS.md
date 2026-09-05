# Kontrola przygotowania pakietu

Wyniki dotyczą wyłącznie przygotowania tych plików (2026-09-05), nie wdrożenia QSX.

| Sprawdzenie | Wynik | Zakres |
|---|---|---|
| Bash syntax: setup.sh | PASS | bash -n; no installation performed |
| Context guard: setup.sh | PASS | Without QSX_BUILD_CONTEXT, exits 64 before making changes |
| Bash syntax: maintenance.sh | PASS | bash -n; no installation performed |
| Context guard: maintenance.sh | PASS | Without QSX_BUILD_CONTEXT, exits 64 before making changes |
| Python syntax | PASS | ast.parse |
| Read-only inventory | PASS | Executed in the artifact-preparation environment; valid JSON, not a Codex Cloud report |
| Document assembly | PASS | Core requirements retained; original disk UUID removed; short AGENTS.md |
| Dependency installation in your Codex Cloud | NOT_RUN | Requires setup on your chosen environment |
| ShellCheck | NOT_RUN | Tool not available in the preparation environment |
| QSX application implementation | NOT_RUN | This bundle is the implementation task, not the implemented app |
| Local QEMU/KVM and network isolation | NOT_RUN | No access to your host; reserved for local acceptance |
