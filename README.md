# Job Broker

A shared service that places GPU jobs from ML teams onto rented cloud nodes,
starts and stops nodes to control cost, and reports cost per team. It also
contains a retrieval endpoint that finds similar past job failures.

Design decisions and their reasoning are in [docs/DECISIONS.md](docs/DECISIONS.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
