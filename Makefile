.PHONY: test run

test:
	PYTHONPATH=src python -m pytest

run:
	PYTHONPATH=src python -m uvicorn contextops.main:app --host $${HOST:-127.0.0.1} --port $${PORT:-8000}
