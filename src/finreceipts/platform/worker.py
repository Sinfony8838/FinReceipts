"""RabbitMQ delivery -> fenced Java step -> acknowledge only after durable resolution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid

import httpx

from finreceipts.platform import engine
from finreceipts.platform.models import (
    Adapter,
    DispatchUnknown,
    ProviderRejected,
    cost_micros,
    request_body,
    reservation_micros,
)
from finreceipts.runtime.codec import decode_lookup
from finreceipts.tools.edgar import EdgarClient, EdgarError
from finreceipts.tools.http_cache import CacheMiss
from finreceipts.xbrl.metrics import lookup_from_companyfacts

LOG = logging.getLogger("finreceipts.worker")


class Coordinator:
    def __init__(self):
        self.http = httpx.Client(
            base_url=os.getenv("COORDINATOR_URL", "http://localhost:8080"),
            headers={"Authorization": "Bearer " + os.environ["WORKER_SECRET"]},
            timeout=10,
        )

    def post(self, path, body=None):
        response = self.http.post("/internal/v1/" + path, json=body or {})
        response.raise_for_status()
        return response.json()


class CoordinatedEdgar(EdgarClient):
    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator

    def get_json(self, url):
        cached = self.cache.get(url)
        if cached is not None or self.mode == "replay":
            return super().get_json(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        token = str(uuid.uuid4())
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if self.coordinator.post(f"source/{key}/lock", {"lease_token": token})["acquired"]:
                try:
                    cached = self.cache.get(url)
                    if cached is not None:
                        return cached
                    self.coordinator.post("source/permit")
                    return super().get_json(url)
                finally:
                    self.coordinator.post(f"source/{key}/unlock", {"lease_token": token})
            time.sleep(0.2)
        raise TimeoutError("source_busy")


def execute(task, coordinator):
    request, checkpoint, step = task["request"], task["checkpoint"], task["step"]
    if step == "PREPARE":
        try:
            with CoordinatedEdgar(coordinator) as edgar:
                raw = edgar.companyfacts(edgar.ticker_to_cik(request["ticker"]))
        except (EdgarError, CacheMiss) as exc:
            # A missing company or offline fixture is a visible partial failure.
            raise ValueError("evidence_unavailable") from exc
        return engine.prepare(request, lookup_from_companyfacts(raw))
    if step == "VERIFY":
        return engine.verify(task)
    if step != "MODEL":
        raise ValueError("unknown_step")
    engine.check_checkpoint(checkpoint)
    profile = task["profile"]
    if profile["provider"] != "fake":
        if not os.environ.get(
            "MINIMAX_API_KEY" if profile["provider"] == "minimax" else "DEEPSEEK_API_KEY"
        ):
            raise ValueError("provider_credentials_missing")
        request_body(profile, checkpoint["messages"], engine.SYSTEM, engine.tools())
    attempt = str(uuid.uuid4())
    prefix = f"tasks/{task['task_id']}/"
    reservation = reservation_micros(profile)
    try:
        coordinator.post(
            prefix + "reserve",
            {
                "lease_token": task["lease_token"],
                "attempt_id": attempt,
                "reserve_micros": reservation,
            },
        )
    except httpx.HTTPStatusError as exc:
        code = exc.response.json().get("code")
        if code in {"budget_exhausted", "attempt_limit"}:
            raise ValueError(code) from exc
        raise
    adapter = Adapter(profile["provider"])
    try:
        try:
            response = adapter.complete(
                profile,
                checkpoint["messages"],
                engine.SYSTEM,
                engine.tools(),
                fake_answer=engine.expected_answer(
                    engine.VerificationTarget.model_validate(request["target"]),
                    decode_lookup(task["evidence"]),
                ),
            )
        except ProviderRejected as exc:
            coordinator.post(
                prefix + "settle",
                {
                    "lease_token": task["lease_token"],
                    "attempt_id": attempt,
                    "actual_micros": 0,
                    "usage": {"rejected_status": exc.status},
                },
            )
            # A known rejection can be retried through a new bounded, budgeted step.
            if exc.retryable:
                time.sleep(1)
                return {
                    "next_step": "MODEL",
                    "checkpoint": checkpoint,
                    "evidence": None,
                    "result": None,
                    "machine_verdict": "PENDING",
                }
            raise ValueError(f"provider_rejected_{exc.status}") from exc
        amount = cost_micros(profile, response.usage)
        coordinator.post(
            prefix + "settle",
            {
                "lease_token": task["lease_token"],
                "attempt_id": attempt,
                "actual_micros": amount,
                "usage": {
                    **response.usage,
                    "model": response.model,
                    "fingerprint": response.fingerprint,
                },
            },
        )
        return engine.consume(task, response)
    finally:
        adapter.close()


def handle_message(coordinator, payload):
    if payload.get("protocol_version") != 1:
        raise ValueError("unsupported_message_version")
    task_id = payload["task_id"]
    traceparent = payload.get("traceparent")
    if isinstance(traceparent, str) and len(traceparent) == 55:
        coordinator.http.headers["traceparent"] = traceparent
    else:
        coordinator.http.headers.pop("traceparent", None)
    prefix = f"tasks/{task_id}/"
    try:
        task = coordinator.post(
            prefix + "claim",
            {
                "expected_revision": payload["revision"],
                "worker_id": os.getenv("HOSTNAME", "local-worker"),
            },
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 409}:
            return  # obsolete delivery, no external work performed
        raise
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(10):
            try:
                coordinator.post(prefix + "heartbeat", {"lease_token": task["lease_token"]})
            except httpx.HTTPError:
                stopped.set()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        delta = execute(task, coordinator)
        coordinator.post(
            prefix + "commit",
            {"lease_token": task["lease_token"], "expected_revision": task["revision"], **delta},
        )
    except (ValueError, DispatchUnknown) as exc:
        code = str(exc)
        if not code.replace("_", "").isalnum() or len(code) > 100:
            code = "worker_validation_failed"
        coordinator.post(
            prefix + "fail",
            {
                "lease_token": task["lease_token"],
                "code": code,
                "unknown": isinstance(exc, DispatchUnknown),
            },
        )
    finally:
        stopped.set()
        thread.join(timeout=11)


def consume_forever():
    import pika

    coordinator = Coordinator()
    while True:
        connection = None
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=os.getenv("RABBITMQ_HOST", "localhost"),
                    heartbeat=300,
                    blocked_connection_timeout=30,
                    credentials=pika.PlainCredentials(
                        os.getenv("RABBITMQ_USER", "finreceipts"), os.environ["RABBITMQ_PASSWORD"]
                    ),
                )
            )
            channel = connection.channel()
            channel.queue_declare(
                queue="finreceipts.v2.steps", durable=True, arguments={"x-queue-type": "quorum"}
            )
            channel.basic_qos(prefetch_count=1)
            for method, _properties, body in channel.consume(
                "finreceipts.v2.steps", inactivity_timeout=5
            ):
                if method is None:
                    continue
                try:
                    handle_message(coordinator, json.loads(body))
                except (json.JSONDecodeError, KeyError, ValueError):
                    LOG.error("invalid_delivery")
                    channel.basic_reject(method.delivery_tag, requeue=False)
                except Exception:
                    LOG.exception("step_not_acknowledged")
                    # Lease recovery resolves any in-flight work before redelivery can claim.
                    channel.basic_nack(method.delivery_tag, requeue=True)
                    time.sleep(1)
                else:
                    channel.basic_ack(method.delivery_tag)
        except Exception:
            LOG.warning("broker_unavailable", exc_info=False)
            time.sleep(3)
        finally:
            if connection is not None and connection.is_open:
                connection.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    consume_forever()


if __name__ == "__main__":
    main()
