PYTHON ?= python
VENV ?= .venv

.PHONY: test
test:
	$(VENV)/bin/pytest

.PHONY: install-dev
install-dev:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements-dev.txt
