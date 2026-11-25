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
broker-serve --data-dir var          # API on http://127.0.0.1:8000, docs at /docs
```

State lives in `var/broker.db` and `var/provider.db`. Stop and restart the
server with the same `--data-dir` and nothing is lost: simulated time resumes
where it stopped and provider nodes keep billing in between.

## HTTP API

Times are ISO 8601 UTC. Simulated time moves only through `POST /clock/advance`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit `{team, gpus, estimated_minutes, priority}`. Optional `Idempotency-Key` header: a repeat returns the original job with `200`; the same key with a different body returns `409`. New jobs return `201`. |
| `GET` | `/jobs/{id}` | One job. |
| `GET` | `/jobs?status=&team=` | Jobs, newest first. |
| `POST` | `/jobs/{id}/cancel` | Cancel a queued or running job. Cancelling twice is a no-op; cancelling a finished job is `409`. |
| `POST` | `/jobs/{id}/complete` | Report `{outcome: succeeded \| failed}` for a running job. |
| `GET` | `/queue` | Waiting jobs, in the order the scheduler will consider them. |
| `GET` | `/nodes` | Live nodes with their jobs and GPU use. |
| `GET` | `/reports/cost?start=&end=` | GPU-hours and cost per team for a window, split into used and idle cost. |
| `GET` | `/clock` | Current simulated time. |
| `POST` | `/clock/advance` | `{seconds}`: process every event up to that point. |

Errors: `422` invalid input, `404` unknown job, `409` invalid state change or
idempotency conflict.
