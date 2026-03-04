from __future__ import annotations

import json

from reflexor.orchestrator.queue import TaskEnvelope

_FIELD_ENVELOPE = "envelope"


def _canonical_envelope_json(envelope: TaskEnvelope) -> str:
    payload = envelope.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _decode_envelope(payload: str) -> TaskEnvelope:
    data = json.loads(payload)
    return TaskEnvelope.model_validate(data)
