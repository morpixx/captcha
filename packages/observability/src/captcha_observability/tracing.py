from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_log = logging.getLogger(__name__)
_INSTALLED = False


def setup_tracing(service_name: str, endpoint: str) -> trace.Tracer | None:
    """Install an OTel tracer provider with OTLP/gRPC export.

    No-op (returns None) when endpoint is empty — lets envs leave tracing off without a branch
    at every call site.

    Idempotent: a second call with a non-empty endpoint keeps the first provider; drop and
    restart the process to switch endpoints.
    """
    global _INSTALLED
    if not endpoint:
        return None
    if _INSTALLED:
        return trace.get_tracer(service_name)

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "captcha",
        }
    )
    provider = TracerProvider(resource=resource)
    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:  # noqa: BLE001
        _log.exception("otel exporter init failed, tracing disabled")
        return None

    trace.set_tracer_provider(provider)
    _INSTALLED = True
    return trace.get_tracer(service_name)
