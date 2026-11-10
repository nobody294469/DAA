"""Opt-in online LLM: key handling, offline fallback, local+online judge chain and the .env loader (no network)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from clindoc.cli import _load_dotenv
from clindoc.extract.online_provider import (ChainedJudgeClient, HybridTextProvider, OnlineConfig,
                                             OnlineLLMProvider)
from clindoc.extract.text_provider import extract_text
from clindoc.verify.stage_c import StageCJudgeOutput

PROMPT = "Extract clinical information from the following clinical text:\n\nPlavix 75 mg po daily.\n\nOutput JSON:"
VALID = StageCJudgeOutput(label="SUPPORTED", confidence=0.9, contradiction_type=None, rationale="The source document states the claim as written.").model_dump_json()


class _Resp:
    def __init__(self, code, body=None): self.status_code, self._b = code, body or {}
    def json(self): return {"choices": [{"message": {"content": json.dumps(self._b)}}]}
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(str(self.status_code))


class _Http:
    def __init__(self, *resps): self.resps, self.sent = list(resps), []
    def post(self, url, json=None, headers=None):
        self.sent.append(json); return self.resps.pop(0)


class _Judge:
    def __init__(self, out): self.out, self.calls, self.model_name = out, 0, "j"
    def generate(self, prompt, schema):
        self.calls += 1
        if isinstance(self.out, Exception): raise self.out
        return self.out


def test_off_without_key_and_key_never_public(monkeypatch):
    monkeypatch.delenv("CLINDOC_ONLINE_API_KEY", raising=False)
    assert OnlineConfig.from_env() is None
    monkeypatch.setenv("CLINDOC_ONLINE_API_KEY", "sk-secret")
    cfg = OnlineConfig.from_env()
    assert cfg.base_url.startswith("https://") and "sk-secret" not in json.dumps(cfg.public())


def test_judge_mode_values(monkeypatch):
    monkeypatch.setenv("CLINDOC_ONLINE_API_KEY", "k")
    for raw, want in (("", "off"), ("no", "off"), ("yes", "online"), ("both", "both")):
        monkeypatch.setenv("CLINDOC_ONLINE_JUDGE", raw)
        assert OnlineConfig.from_env().judge == want


def test_merges_online_results_and_falls_back_offline():
    cfg = OnlineConfig("k", "https://x.test/v1", "m", "off")
    extra = {"extractions": [{"field_type": "med.name", "value": "Brilinta", "evidence_text": "Plavix"}]}
    merged = json.loads(HybridTextProvider(OnlineLLMProvider(cfg, http_client=_Http(_Resp(200, extra)))).complete("", PROMPT))
    assert any(e["field_type"] == "med.name" for e in merged["extractions"])
    broken = json.loads(HybridTextProvider(OnlineLLMProvider(cfg, http_client=_Http(_Resp(401)))).complete("", PROMPT))
    assert broken["extractions"] == json.loads(json.dumps(extract_text("Plavix 75 mg po daily.")))


def test_falls_back_to_json_object_when_schema_mode_unsupported():
    cfg = OnlineConfig("k", "https://x.test/v1", "m", "off")
    http = _Http(_Resp(400), _Resp(200, {"extractions": []}))
    OnlineLLMProvider(cfg, http_client=http).complete("s", "u")
    assert [h["response_format"]["type"] for h in http.sent] == ["json_schema", "json_object"]


def test_chained_judge_uses_local_and_only_asks_online_when_local_fails():
    local_ok, online = _Judge(VALID), _Judge(VALID)
    assert ChainedJudgeClient(local_ok, online).generate("p", StageCJudgeOutput) == VALID
    assert (local_ok.calls, online.calls) == (1, 0)  # online never sees the claim when local answers
    for bad in ("not json", RuntimeError("cuda oom")):
        local_bad, online = _Judge(bad), _Judge(VALID)
        assert ChainedJudgeClient(local_bad, online).generate("p", StageCJudgeOutput) == VALID
        assert (local_bad.calls, online.calls) == (1, 1)


def test_dotenv_loader_only_clindoc_keys_and_env_wins(tmp_path: Path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# c\nCLINDOC_ONLINE_MODEL=abc\nOTHER=1\nCLINDOC_ONLINE_BASE_URL="https://h/v1"\n', encoding="utf-8")
    monkeypatch.delenv("CLINDOC_ONLINE_MODEL", raising=False)
    monkeypatch.setenv("CLINDOC_ONLINE_BASE_URL", "kept")
    monkeypatch.delenv("OTHER", raising=False)
    _load_dotenv(f)
    assert os.environ["CLINDOC_ONLINE_MODEL"] == "abc" and os.environ["CLINDOC_ONLINE_BASE_URL"] == "kept"
    assert "OTHER" not in os.environ


def test_busy_model_falls_back_to_next_model(monkeypatch):
    monkeypatch.setattr("clindoc.extract.online_provider.time.sleep", lambda s: None)
    cfg = OnlineConfig("k", "https://x.test/v1", "main", "off", ("backup",))
    http = _Http(_Resp(503), _Resp(503), _Resp(200, {"extractions": []}))  # main: 2 busy answers, then backup answers
    OnlineLLMProvider(cfg, http_client=http).complete("s", "u")
    assert [h["model"] for h in http.sent] == ["main", "main", "backup"]


def test_same_dose_from_rules_and_model_is_one_claim():
    cfg = OnlineConfig("k", "https://x.test/v1", "m", "off")
    extra = {"extractions": [{"field_type": "med.dose", "value": "75", "evidence_text": "75"}]}  # rules already have "75 mg"
    merged = json.loads(HybridTextProvider(OnlineLLMProvider(cfg, http_client=_Http(_Resp(200, extra)))).complete("", PROMPT))
    assert [e["value"] for e in merged["extractions"] if e["field_type"] == "med.dose"] == ["75 mg"]
