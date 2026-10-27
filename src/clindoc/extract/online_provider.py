"""Opt-in online LLM for free-text extraction, on top of the offline ClinicalTextProvider.

Works with any OpenAI-compatible chat-completions endpoint (OpenAI, Groq, Together, OpenRouter, Azure, a
self-hosted vLLM ...). Enabled only when CLINDOC_ONLINE_API_KEY is set; the key is read from the environment
and is never logged, returned by the API or written to disk.

PRIVACY: enabling it sends the text of uploaded documents to the configured host. Use it only with data you are
allowed to share (synthetic or public notes, or a provider covered by your data-processing agreement).

The offline extractor always runs; online results are merged in (de-duplicated by field + evidence). Any online
failure (network, auth, quota, bad JSON) is swallowed and the offline result is returned, so a run never breaks.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from clindoc.extract.llm_extractor import LLM_OUTPUT_SCHEMA, PERMITTED_NARRATIVE_FIELDS, _SYSTEM_PROMPT
from clindoc.extract.text_provider import ClinicalTextProvider, extract_text

logger = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _judge_mode(raw: str) -> str:
    raw = raw.strip().lower()
    return "both" if raw == "both" else "online" if raw in ("1", "true", "yes", "online") else "off"


class ChainedJudgeClient:
    """Stage C judge client: the local model answers; the hosted model is asked only if the local one fails.

    A failure is an exception (e.g. GPU out of memory) or output that does not validate against the verdict schema.
    The hosted model therefore sees only the claims the local judge could not handle.
    """

    def __init__(self, primary, secondary) -> None:
        self.primary, self.secondary = primary, secondary
        self.model_name = f"{getattr(primary, 'model_name', 'local')} + {getattr(secondary, 'model_name', 'online')}"

    def generate(self, prompt: str, schema) -> str:
        try:
            out = self.primary.generate(prompt, schema)
            schema.model_validate_json(out)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.info("local judge unusable (%s); asking the online judge", type(exc).__name__)
            return self.secondary.generate(prompt, schema)


@dataclass(frozen=True)
class OnlineConfig:
    api_key: str
    base_url: str
    model: str
    judge: str  # Stage C judge: "off" (local only) | "online" (hosted only) | "both" (local first, hosted as backup)
    fallback_models: tuple[str, ...] = ()  # tried in order when the main model is busy or retired

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    @classmethod
    def from_env(cls) -> "OnlineConfig | None":
        key = os.environ.get("CLINDOC_ONLINE_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key,
                   base_url=(os.environ.get("CLINDOC_ONLINE_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/"),
                   model=os.environ.get("CLINDOC_ONLINE_MODEL", "").strip() or "gpt-4o-mini",
                   judge=_judge_mode(os.environ.get("CLINDOC_ONLINE_JUDGE", "")),
                   fallback_models=tuple(m.strip() for m in os.environ.get("CLINDOC_ONLINE_FALLBACK_MODELS", "").split(",") if m.strip()))

    def public(self) -> dict:
        """Safe to show in the UI / API: never the key."""
        return {"enabled": True, "host": self.host, "model": self.model, "judge": self.judge}


class OnlineLLMProvider:
    """LLMProvider that asks a hosted model; returns the same JSON contract as LLM_OUTPUT_SCHEMA."""

    def __init__(self, cfg: OnlineConfig, timeout: float = 25.0, http_client=None) -> None:
        self.cfg, self.timeout, self._http = cfg, timeout, http_client

    def _post(self, payload: dict) -> dict:
        import httpx

        client = self._http or httpx.Client(timeout=self.timeout)
        return client.post(f"{self.cfg.base_url}/chat/completions", json=payload,
                           headers={"Authorization": f"Bearer {self.cfg.api_key}"})

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        modes = ({"type": "json_schema", "json_schema": {"name": "extraction", "schema": LLM_OUTPUT_SCHEMA, "strict": True}},
                 {"type": "json_object"})
        resp, error = None, None
        for model in (self.cfg.model, *self.cfg.fallback_models):  # a busy or retired model should not stop the others
            for fmt in modes:  # not every provider supports json_schema: fall back to plain JSON mode
                for attempt in range(2):  # 429/5xx spikes and timeouts are often gone a second later
                    try:
                        resp = self._post({"model": model, "temperature": 0, "messages": messages, "response_format": fmt})
                    except Exception as exc:  # noqa: BLE001: timeout / connection error
                        resp, error = None, exc
                        logger.info("online model %s: %s (attempt %d)", model, type(exc).__name__, attempt + 1)
                        continue
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                        time.sleep(1)
                        continue
                    break
                if resp is None:
                    break  # transport failed twice: next model
                if resp.status_code == 400 and fmt is modes[0]:
                    continue
                if resp.status_code >= 400:
                    break  # try the next model
                return resp.json()["choices"][0]["message"]["content"]
        if resp is None and error is not None:
            raise error
        resp.raise_for_status()
        raise RuntimeError("online provider rejected every model and response format")


class HybridTextProvider:
    """Offline rules + online LLM, merged. Has the router's `recovers_unparsed_regions` behaviour of the offline one."""

    recovers_unparsed_regions = True
    augments_regions = True  # router.py: also read regions the template parsers handled

    def __init__(self, online: OnlineLLMProvider) -> None:
        self.online = online
        self.offline = ClinicalTextProvider()

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        m = re.search(r"Extract clinical information from the following clinical text:\s*(.+?)Output JSON:", user_prompt, re.DOTALL)
        text = m.group(1).strip("\n") if m else user_prompt
        merged = list(extract_text(text))
        try:
            extra = json.loads(self.online.complete(_SYSTEM_PROMPT, user_prompt)).get("extractions", [])
        except Exception as exc:  # noqa: BLE001: online is best-effort; never include the error body (may echo input)
            logger.warning("online extraction failed (%s); using offline result only", type(exc).__name__)
            extra = []
        seen = {(e["field_type"], e["evidence_text"].lower()) for e in merged}
        for e in extra:
            key = (e.get("field_type"), str(e.get("evidence_text", "")).lower())
            # the NarrativeExtractor re-validates every span as a verbatim substring; only tidy obvious junk here
            if e.get("field_type") in PERMITTED_NARRATIVE_FIELDS and e.get("value") and e.get("evidence_text") and key not in seen:
                e = {**e, "group": e.get("group") or f"on{len(seen)}", "confidence": min(float(e.get("confidence", 0.8)), 0.9)}
                merged.append(e)
                seen.add(key)
        return json.dumps({"extractions": merged})


if __name__ == "__main__":  # self-check with a fake HTTP client: merge, dedupe, key hidden, failure -> offline only
    class _Resp:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return {"choices": [{"message": {"content": json.dumps(self._b)}}]}
        def raise_for_status(self):
            if self.status_code >= 400: raise RuntimeError("http " + str(self.status_code))
    class _Http:
        def __init__(self, resp): self.resp, self.calls = resp, []
        def post(self, url, json=None, headers=None): self.calls.append((url, headers)); return self.resp

    cfg = OnlineConfig("sk-test", "https://api.example.com/v1", "m", False)
    text = "Plavix 75 mg po daily and he takes tamsulosin 0.4 mg."
    prompt = f"Extract clinical information from the following clinical text:\n\n{text}\n\nOutput JSON:"
    http = _Http(_Resp(200, {"extractions": [
        {"field_type": "med.name", "value": "Plavix", "evidence_text": "Plavix"},  # duplicate of offline
        {"field_type": "med.name", "value": "tamsulosin", "evidence_text": "tamsulosin"}]}))
    out = json.loads(HybridTextProvider(OnlineLLMProvider(cfg, http_client=http)).complete("", prompt))["extractions"]
    names = [e["value"] for e in out if e["field_type"] == "med.name"]
    assert names.count("Plavix") == 1 and "tamsulosin" in names, names
    assert http.calls[0][0].endswith("/chat/completions") and http.calls[0][1]["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in json.dumps(cfg.public()) and cfg.public()["host"] == "api.example.com"
    bad = HybridTextProvider(OnlineLLMProvider(cfg, http_client=_Http(_Resp(401, {}))))
    offline_only = [e["value"] for e in json.loads(bad.complete("", prompt))["extractions"] if e["field_type"] == "med.name"]
    assert offline_only == [e["value"] for e in extract_text(text) if e["field_type"] == "med.name"], offline_only  # 401 -> offline only
    print("ok")
