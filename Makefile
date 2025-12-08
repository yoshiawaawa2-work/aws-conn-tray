PYTHON ?= python
VENV ?= .venv

.PHONY: test
test:
	PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(VENV)/bin/pytest -q

.PHONY: test-gui
# WX_GUI_TEST=1 を付けて GUI テストも実行したい場合はこちらを利用
test-gui:
	PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 WX_GUI_TEST=1 $(VENV)/bin/pytest -q

.PHONY: install-dev
install-dev:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements-dev.txt
