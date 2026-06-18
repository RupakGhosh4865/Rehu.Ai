# Savant.ai — developer tasks
# Usage: `make <target>`

.PHONY: help install-hooks scan-secrets test

help:
	@echo "Targets:"
	@echo "  install-hooks  Install the pre-commit secret-scanning hook (run once per clone)"
	@echo "  scan-secrets   Scan the whole repo + git history for secrets (gitleaks)"
	@echo "  test           Run the backend test suite"

# Install pre-commit + the gitleaks hook. Blocks any commit containing a secret.
install-hooks:
	pip install pre-commit
	pre-commit install
	@echo "Pre-commit hooks installed. Secrets will now be blocked at commit time."

# Manual full scan (also runs in CI / before a history rewrite).
# Requires gitleaks: https://github.com/gitleaks/gitleaks#installing
scan-secrets:
	gitleaks detect --config .gitleaks.toml --redact --verbose
	@echo "If gitleaks reports findings, DO NOT commit. Rotate and remove the secret first."

test:
	cd backend && python -m pytest -q
