"""HTTP API for the Job Broker.

The broker is single-threaded by design: one lock serialises every request,
so the scheduler never sees a half-applied change. At this scale (a handful
of teams, at most 10 nodes) throughput is not a concern; correctness is.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import BrokerConfig
from .models import Job, JobStatus, Node
from .retrieval import FailureIndex
from .service import Broker, IdempotencyConflict, InvalidTransition, NotFound, ValidationError
from .sim.fake_provider import FakeProvider
from .store import Store


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise HTTPException(422, f"invalid timestamp {value!r}, expected ISO 8601") from None


class SubmitJob(BaseModel):
    team: str
    gpus: int
    estimated_minutes: float = Field(gt=0)
    priority: Literal["low", "normal", "high"] = "normal"


class CompleteJob(BaseModel):
    outcome: Literal["succeeded", "failed"]


class SearchFailures(BaseModel):
    query: str = Field(min_length=1, max_length=50_000, description="free-text question or a failed job's log")
    k: int = Field(5, ge=1, le=20)


class AdvanceClock(BaseModel):
    seconds: float = Field(gt=0, le=30 * 24 * 3600)


def job_view(broker: Broker, job: Job) -> dict:
    return {
        "id": job.id,
        "team": job.team,
        "gpus": job.gpus,
        "estimated_minutes": job.estimated_minutes,
        "priority": job.priority.value,
        "effective_priority": broker.effective_priority(job).value,
        "status": job.status.value,
        "node_id": job.node_id,
        "attempts": job.attempts,
        "submitted_at": iso(job.submitted_at),
        "started_at": iso(job.started_at),
        "ended_at": iso(job.ended_at),
        "queued_seconds": round(
            job.queued_seconds + (broker.now - job.queued_since if job.queued_since is not None else 0.0), 1
        ),
    }


def node_view(broker: Broker, node: Node) -> dict:
    jobs = broker.jobs_on(node.id)
    return {
        "id": node.id,
        "team": node.team,
        "type": node.type,
        "interruptible": node.interruptible,
        "state": node.state.value,
        "gpus": node.gpus,
        "gpus_used": sum(j.gpus for j in jobs),
        "price_per_hour": node.price_per_hour,
        "launched_at": iso(node.launched_at),
        "ready_at": iso(node.ready_at),
        "idle_since": iso(node.idle_since),
        "jobs": [j.id for j in jobs],
    }


def create_app(broker: Broker, failures: FailureIndex | None = None) -> FastAPI:
    app = FastAPI(title="Job Broker", version="0.1.0")
    lock = threading.Lock()
    failures = failures or FailureIndex.from_jsonl()

    @app.exception_handler(ValidationError)
    def _validation(_, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    def _not_found(_, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidTransition)
    def _conflict(_, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IdempotencyConflict)
    def _idem_conflict(_, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.post("/jobs", status_code=201)
    def submit_job(body: SubmitJob, idempotency_key: str | None = Header(default=None, max_length=128)):
        with lock:
            job, created = broker.submit(body.team, body.gpus, body.estimated_minutes, body.priority, idempotency_key)
            view = job_view(broker, job)
        return view if created else JSONResponse(status_code=200, content=view)

    @app.get("/jobs")
    def list_jobs(status: JobStatus | None = None, team: str | None = None, limit: int = Query(100, le=1000)):
        with lock:
            jobs = [
                j
                for j in broker.jobs.values()
                if (status is None or j.status == status) and (team is None or j.team == team)
            ]
            jobs.sort(key=lambda j: j.seq, reverse=True)
            return [job_view(broker, j) for j in jobs[:limit]]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        with lock:
            return job_view(broker, broker.get_job(job_id))

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        with lock:
            return job_view(broker, broker.cancel(job_id))

    @app.post("/jobs/{job_id}/complete")
    def complete_job(job_id: str, body: CompleteJob):
        with lock:
            return job_view(broker, broker.complete(job_id, body.outcome))

    @app.get("/queue")
    def queue():
        with lock:
            return [job_view(broker, j) for j in broker.queued_jobs()]

    @app.get("/nodes")
    def nodes():
        with lock:
            return [node_view(broker, n) for n in broker.alive_nodes()]

    @app.get("/reports/cost")
    def cost(start: str | None = None, end: str | None = None):
        with lock:
            first_launch = min((n.launched_at for n in broker.nodes.values()), default=broker.now)
            t0 = parse_time(start) if start else first_launch
            t1 = parse_time(end) if end else broker.now
            if t1 < t0:
                raise HTTPException(422, "end must not be before start")
            report = broker.cost_report(t0, t1).to_dict()
        report["start"], report["end"] = iso(t0), iso(t1)
        return report

    @app.get("/healthz")
    def healthz():
        """Liveness plus the numbers an on-call person looks at first."""
        with lock:
            return {
                "status": "ok",
                "now": iso(broker.now),
                "alive_nodes": len(broker.alive_nodes()),
                "queued_jobs": len(broker.queued_jobs()),
                "launch_failures": broker.launch_failures,
            }

    @app.get("/clock")
    def clock():
        with lock:
            return {"now": iso(broker.now)}

    @app.post("/clock/advance")
    def advance(body: AdvanceClock):
        """Simulated time only moves when asked. Nothing real executes."""
        with lock:
            broker.advance(body.seconds)
            return {"now": iso(broker.now)}

    @app.post("/failures/search")
    def search_failures(body: SearchFailures):
        """Past failures similar to a log or question, with their fixes. Read-only, no lock needed."""
        return failures.search(body.query, k=body.k).to_dict()

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Job Broker HTTP API")
    parser.add_argument("--data-dir", default="var", help="where the broker and provider databases live")
    parser.add_argument("--start", default="2025-03-03T00:00:00Z", help="simulated start time on first run")
    parser.add_argument("--policy", default="fairshare", choices=["fairshare", "fifo"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.makedirs(args.data_dir, exist_ok=True)
    provider = FakeProvider(store=Store(os.path.join(args.data_dir, "provider.db")))
    broker = Broker(
        provider,
        Store(os.path.join(args.data_dir, "broker.db")),
        BrokerConfig(policy=args.policy),
        start_time=parse_time(args.start),
    )
    uvicorn.run(create_app(broker), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
