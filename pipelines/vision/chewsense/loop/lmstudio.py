"""Optional local LM Studio summaries of deterministic analysis results.

The model only rewrites evidence it is given into readable text. Output must
be schema-valid JSON, may only mention known weakness IDs, and may not contain
any number absent from the evidence. Anything else is discarded and the
deterministic text is used. The loop works identically when LM Studio is off.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["overview", "weaknesses"],
    "properties": {
        "overview": {"type": "string", "maxLength": 600},
        "weaknesses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "explanation", "suggested_wording"],
                "properties": {
                    "id": {"type": "string"},
                    "explanation": {"type": "string", "maxLength": 400},
                    "suggested_wording": {"type": "string", "maxLength": 200},
                },
            },
        },
    },
}

SYSTEM = (
    "You summarize a chewing-detection evaluation for the person collecting data. "
    "Use only the JSON evidence provided. Never invent metrics, counts or accuracy claims. "
    "Do not state that the system is accurate or ready. Explain each weakness in plain "
    "language and suggest friendly recording wording. Reply with JSON only."
)


def _numbers(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _numbers(item)
    elif isinstance(value, list):
        for item in value:
            found |= _numbers(item)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        for decimals in (0, 1, 2, 3):
            found.add(f"{value:.{decimals}f}".rstrip("0").rstrip(".") or "0")
        if isinstance(value, float) and 0 <= value <= 1:
            for decimals in (0, 1):
                found.add(f"{value * 100:.{decimals}f}".rstrip("0").rstrip("."))
    elif isinstance(value, str):
        found |= {m.rstrip("%") for m in _NUMBER.findall(value)}
    return found


def validate_summary(summary: Any, evidence: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(summary, dict) or set(summary) != {"overview", "weaknesses"}:
        return False, "unexpected structure"
    if not isinstance(summary["overview"], str) or not isinstance(summary["weaknesses"], list):
        return False, "unexpected structure"
    known = {w["id"] for w in evidence.get("weaknesses", [])}
    allowed = _numbers(evidence)
    texts = [summary["overview"]]
    for item in summary["weaknesses"]:
        if not isinstance(item, dict) or item.get("id") not in known:
            return False, "references an unknown weakness"
        texts += [str(item.get("explanation", "")), str(item.get("suggested_wording", ""))]
    for text in texts:
        for number in _NUMBER.findall(text):
            if number.rstrip("%") not in allowed:
                return False, f"number {number!r} is not in the evidence"
        if re.search(r"(?i)\b(production[- ]ready|validated accuracy|clinically)\b", text):
            return False, "unsupported readiness claim"
    return True, "ok"


class LMStudio:
    def __init__(self, base_url: str, *, timeout_s: float = 20, model: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.model = model

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read())

    def available(self) -> str | None:
        try:
            models = self._request("/models").get("data", [])
        except (OSError, urllib.error.URLError, ValueError):
            return None
        if self.model:
            return self.model if any(m.get("id") == self.model for m in models) else None
        return models[0]["id"] if models else None

    def summarize(self, evidence: dict[str, Any]) -> dict[str, Any]:
        model = self.available()
        if model is None:
            return {"used": False, "reason": "LM Studio not reachable or no model loaded"}
        try:
            response = self._request("/chat/completions", {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": json.dumps(evidence, sort_keys=True)},
                ],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "loop_summary", "strict": True, "schema": SCHEMA}},
            })
            content = response["choices"][0]["message"]["content"]
            summary = json.loads(content)
        except (OSError, urllib.error.URLError, KeyError, IndexError, ValueError) as exc:
            return {"used": False, "reason": f"LM Studio request failed: {exc}"}
        valid, reason = validate_summary(summary, evidence)
        if not valid:
            return {"used": False, "model": model, "reason": f"rejected output: {reason}"}
        return {"used": True, "model": model, "advisory_only": True, "summary": summary}
