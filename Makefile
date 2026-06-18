PY=python3.12

.PHONY: venv test cov
venv:
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -U pip && pip install -e ".[dev]"
test:
	. .venv/bin/activate && pytest -q
cov:
	. .venv/bin/activate && pytest --cov=audio_repair --cov-report=term-missing
