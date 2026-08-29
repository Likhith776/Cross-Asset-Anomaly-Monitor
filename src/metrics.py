"""
Lightweight Prometheus instrumentation shared across services.

Design constraints:
- Entirely additive: without prometheus_client installed (slim profile,
  older deployments) every helper becomes a no-op and nothing changes.
- Exposure is opt-in per service: call start_metrics_server_if_configured()
  and set METRICS_HTTP_PORT in the environment (only the docker-compose
  profile does). No server is started otherwise.
- The FastAPI app additionally mounts /metrics via mount_metrics(app).

Metric catalog (single registry per process; Prometheus distinguishes
targets by the automatic `instance` label, so services need no extra
labels to stay distinct):

  pipeline_ticks_fetched_total{symbol}   producer: fresh quotes fetched
  producer_fetch_seconds                 producer: fetch-cycle latency
  pipeline_messages_consumed_total       consumers: Kafka messages processed
  detector_latency_seconds{detector}     DetectionPipeline per-detector time
  anomalies_persisted_total              anomaly_events rows written
  db_write_total{outcome}                feature writes (success|fail)
  db_query_seconds{endpoint}             API hot-endpoint DB latency
  kafka_consumer_lag_messages{partition} consumer lag (sampled periodically)
  batch_cycle_seconds                    scheduler detection-cycle latency
"""

import logging
import os

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        make_asgi_app,
        start_http_server,
    )

    HAVE_PROMETHEUS = True
except ImportError:  # slim profile / minimal deployments: full no-op
    HAVE_PROMETHEUS = False

    class _NoopMetric:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, amount=1):
            pass

        def observe(self, amount):
            pass

        def set(self, value):
            pass

        def time(self):
            import contextlib

            return contextlib.nullcontext()

    Counter = _NoopMetric
    Gauge = _NoopMetric
    Histogram = _NoopMetric

    def start_http_server(*args, **kwargs):
        pass

    def make_asgi_app(*args, **kwargs):
        raise RuntimeError("prometheus_client not installed")


# ---------------------------------------------------------------------------
# Shared metrics
# ---------------------------------------------------------------------------

TICKS_FETCHED = Counter(
    "pipeline_ticks_fetched_total",
    "Fresh quotes fetched by the producer, per symbol",
    ["symbol"],
)
PRODUCER_FETCH_SECONDS = Histogram(
    "producer_fetch_seconds",
    "Producer fetch-cycle wall time",
)
MESSAGES_CONSUMED = Counter(
    "pipeline_messages_consumed_total",
    "Kafka messages processed by a consumer",
)
DETECTOR_LATENCY = Histogram(
    "detector_latency_seconds",
    "Detector execution latency inside DetectionPipeline.detect",
    ["detector"],
)
ANOMALIES_PERSISTED = Counter(
    "anomalies_persisted_total",
    "anomaly_events rows written",
)
DB_WRITE = Counter(
    "db_write_total",
    "Feature-writer database write attempts",
    ["outcome"],
)
DB_QUERY_SECONDS = Histogram(
    "db_query_seconds",
    "DB query latency for API hot endpoints",
    ["endpoint"],
)
CONSUMER_LAG = Gauge(
    "kafka_consumer_lag_messages",
    "Consumer lag in messages, per topic partition",
    ["partition"],
)
BATCH_CYCLE_SECONDS = Histogram(
    "batch_cycle_seconds",
    "Scheduler batch detection-cycle wall time",
)


def start_metrics_server_if_configured() -> int | None:
    """
    Start the process-local metrics HTTP server when METRICS_HTTP_PORT
    is set (only the docker-compose profile sets it). Never raises:
    a metrics-server failure must never take the pipeline down.
    """
    port = os.getenv("METRICS_HTTP_PORT")
    if not port:
        return None
    try:
        start_http_server(int(port))
        logger.info("[METRICS] exposition server listening on :%s", port)
        return int(port)
    except Exception as exc:
        logger.warning("[METRICS] failed to start on :%s — %s", port, exc)
        return None


def mount_metrics(app):
    """Mount /metrics on a FastAPI/ASGI app (no-op without the library)."""
    if HAVE_PROMETHEUS:
        app.mount("/metrics", make_asgi_app())
    return app
