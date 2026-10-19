"""Bounded, payload-free timings used only by the disposable capacity Web process."""

from collections import defaultdict, deque
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import math
import os
import threading
import time
from urllib.parse import urlsplit


class SubmissionTimings:
    def __init__(self):
        self.active = ContextVar("isolated_submission", default=False)
        self.samples = defaultdict(lambda: deque(maxlen=4096))
        self.counts = defaultdict(int)
        self.failures = defaultdict(int)
        self.lock = threading.Lock()

    def record(self, label, started, failed=False):
        with self.lock:
            self.samples[label].append((time.perf_counter() - started) * 1000)
            self.counts[label] += 1
            self.failures[label] += int(failed)

    def snapshot(self):
        with self.lock:
            result = {}
            for label, values in self.samples.items():
                ordered = sorted(values)
                result[label] = {"count": self.counts[label], "sampled": len(ordered),
                                 "failures": self.failures[label],
                                 "mean_ms": round(sum(ordered) / len(ordered), 3),
                                 "p95_ms": round(ordered[math.ceil(.95 * len(ordered)) - 1], 3),
                                 "max_ms": round(ordered[-1], 3)}
            return result

    def wrap(self, function, label, *, root=False):
        def enter():
            return self.active.set(True) if root else None

        def finish(started, failed, token):
            self.record(label, started, failed)
            if token is not None:
                self.active.reset(token)
                print("T07_SUBMISSION_DIAGNOSTICS " + json.dumps(self.snapshot()), flush=True)

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                if not root and not self.active.get():
                    return await function(*args, **kwargs)
                token, started, failed = enter(), time.perf_counter(), True
                try:
                    result = await function(*args, **kwargs)
                    failed = False
                    return result
                finally:
                    finish(started, failed, token)
            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            if not self.active.get():
                return function(*args, **kwargs)
            started, failed = time.perf_counter(), True
            try:
                result = function(*args, **kwargs)
                failed = False
                return result
            finally:
                finish(started, failed, None)
        return synchronous


def install():
    # No production module imports this observer. Refuse accidental use outside
    # the managed disposable DB even if someone imports the fixture manually.
    database = urlsplit(os.environ.get("NLP_AGENT_DATABASE_URL", ""))
    if (os.environ.get("NLP_AGENT_DEPLOYMENT_ENV") != "test"
            or database.hostname != "127.0.0.1"
            or not database.path.startswith("/nlp_agent_api_http_")
            or os.environ.get("T07_LOAD_RUN_KIND") != "regression"):
        raise ValueError("DIAGNOSTICS_REQUIRE_DISPOSABLE_REGRESSION")
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from gateway.core import BackendGateway
    from gateway.mysql_repository import MySQLGatewayRepository
    from server.agent.session_service import DatabaseSessionService

    timings = SubmissionTimings()
    for owner, method, label, root in (
        (BackendGateway, "submit_turn", "gateway_submit_total", True),
        (DatabaseSessionService, "resolve", "conversation_resolve", False),
        (DatabaseSessionService, "touch", "conversation_touch", False),
        (MySQLGatewayRepository, "create_turn", "repository_create_turn", False),
        (MySQLGatewayRepository, "turn_for_idempotency", "idempotency_lookup", False),
        (MySQLGatewayRepository, "latest_learning_state", "learning_state", False),
        (MySQLGatewayRepository, "expire_guided_sessions", "expire_guided", False),
        (MySQLGatewayRepository, "expire_exercise_sessions", "expire_exercise", False),
    ):
        setattr(owner, method, timings.wrap(getattr(owner, method), label, root=root))

    @event.listens_for(Engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        if timings.active.get():
            # Fixed categories only: never retain SQL text, parameters, IDs,
            # request content, credentials or exception messages.
            normalized = " ".join(statement.upper().split())
            label = ("sql_runtime_named_lock" if "GET_LOCK(" in normalized else
                     "sql_capacity_row_lock" if "FROM NLP_RUNTIME_CAPACITY" in normalized and "FOR UPDATE" in normalized else
                     "sql_other")
            context._t07_submission_timing = (label, time.perf_counter())

    @event.listens_for(Engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        marker = getattr(context, "_t07_submission_timing", None)
        if marker is not None:
            timings.record(*marker)

    @event.listens_for(Engine, "handle_error")
    def error(exception_context):
        marker = getattr(exception_context.execution_context, "_t07_submission_timing", None)
        if marker is not None:
            timings.record(*marker, failed=True)

    return timings
