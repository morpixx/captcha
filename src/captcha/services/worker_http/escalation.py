"""HTTP -> browser reroute for `NeedsBrowserError`.

Contract §2: the HTTP worker publishes the envelope to the `browser` stream,
acks the source message, and neither refunds nor re-charges. `lane_hops`
caps the move at one; an envelope that already spent its hop is terminal and
the caller turns it into `ERR_UNSOLVABLE`.

Publish before ack on purpose: a crash in between leaves the source entry
pending for XAUTOCLAIM (duplicate work, at-least-once) instead of losing the
task entirely.
"""

from __future__ import annotations

from typing import Any

from captcha.adapters.queue import Consumer, Producer
from captcha.domain.lanes import Lane
from captcha.domain.task import TaskEnvelope
from captcha.observability import metrics

FROM_LANE: Lane = "http"
TO_LANE: Lane = "browser"


async def escalate_to_browser(
    *,
    producer: Producer,
    consumer: Consumer,
    message_id: str,
    envelope: TaskEnvelope,
    log: Any,
) -> TaskEnvelope:
    """Publish the next hop, ack the source, count one escalation.

    Raises `UnsolvableError` (from `TaskEnvelope.escalated`) when the hop
    budget is gone, and lets Redis errors bubble up so the caller can retry the
    HTTP attempt instead of dropping the task.
    """
    next_envelope = envelope.escalated()
    await producer.publish(next_envelope, lane=TO_LANE)
    await consumer.ack(message_id)
    metrics.escalations_total.labels(type=envelope.task.type.value).inc()
    log.info(
        "task.escalated",
        from_lane=FROM_LANE,
        to_lane=TO_LANE,
        lane_hops=next_envelope.lane_hops,
    )
    return next_envelope
