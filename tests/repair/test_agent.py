from audio_repair.core.config import get_settings
from audio_repair.core.taxonomy import Category
from audio_repair.llm.client import LLMResponse, ToolCall, ToolSpec
from audio_repair.repair.agent import run_agent

S = get_settings({})


class ScriptedLLM:
    """Replays a fixed list of LLMResponses; repeats the last one forever."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0

    def complete(self, messages, tools, max_tokens):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


class StubRegistry:
    def __init__(self, results):
        # results: dict tool_name -> dict result
        self._results = results

    def specs(self):
        return [ToolSpec(name=n, description="d") for n in
                ["probe", "remux", "extract_audio", "reencode",
                 "force_format", "inspect_bytes", "decode_verify"]]

    def invoke(self, name, arguments):
        return self._results.get(name, {"ok": True})


class StubFt:
    def __init__(self, verify_ok=True, has_audio=True):
        self._verify_ok = verify_ok
        self._has_audio = has_audio

    def decode_verify(self, path):
        from audio_repair.core.models import DecodeVerifyResult
        return DecodeVerifyResult(ok=self._verify_ok, error_count=0 if self._verify_ok else 5)

    def probe(self, path):
        from audio_repair.core.models import ProbeResult, StreamInfo
        streams = [StreamInfo(index=0, codec_type="audio")] if self._has_audio else []
        return ProbeResult(ok=True, duration_s=1.0, streams=streams)


def _resp(tool, args, tokens=20):
    return LLMResponse(
        tool_calls=[ToolCall(id="c1", name=tool, arguments=args)],
        finish_reason="tool_calls",
        total_tokens=tokens,
    )


def test_scripted_success():
    llm = ScriptedLLM([
        _resp("probe", {}),
        _resp("extract_audio", {"audio_codec": "copy"}),
    ])
    registry = StubRegistry({
        "probe": {"ok": True},
        "extract_audio": {"ok": True, "output_path": "/sb/out.mka"},
    })
    out = run_agent(llm, registry, StubFt(), "/in.mp4", S, Category.DAMAGED_INDEX)
    assert out.repaired is True
    assert out.output_path == "/sb/out.mka"
    assert out.stop_reason == "repaired"
    assert out.iterations <= 2


def test_no_progress_identical_call():
    llm = ScriptedLLM([_resp("remux", {"fflags": "+genpts"})])  # repeats forever
    registry = StubRegistry({"remux": {"ok": True, "output_path": "/sb/x.mka"}})
    # verify fails so it never "succeeds" and the duplicate call trips no_progress
    out = run_agent(llm, registry, StubFt(verify_ok=False), "/in.mp4", S, Category.DAMAGED_INDEX)
    assert out.repaired is False
    assert out.stop_reason == "no_progress"
    assert out.iterations <= 3


def test_stops_at_max_iterations():
    # Distinct, valid-but-unhelpful calls each round; never succeeds, no decode errors.
    # Raise the tool-call cap so the *iteration* guard is the binding constraint.
    s = S.model_copy(update={"agent_max_tool_calls": 1000})
    responses = [_resp("inspect_bytes", {"n": i}) for i in range(s.agent_max_iterations)]
    llm = ScriptedLLM(responses)
    registry = StubRegistry({"inspect_bytes": {"ok": True, "size": 10}})
    out = run_agent(llm, registry, StubFt(verify_ok=False), "/in.mp4", s, Category.DAMAGED_INDEX)
    assert out.repaired is False
    assert out.stop_reason == "max_iterations"
    assert out.iterations == s.agent_max_iterations


def test_stops_at_token_budget():
    # Each round spends 20 tokens; a 10-token budget trips after the first round.
    s = S.model_copy(update={"agent_token_budget": 10, "agent_max_tool_calls": 1000})
    responses = [_resp("inspect_bytes", {"n": i}, tokens=20) for i in range(5)]
    llm = ScriptedLLM(responses)
    registry = StubRegistry({"inspect_bytes": {"ok": True, "size": 10}})
    out = run_agent(llm, registry, StubFt(verify_ok=False), "/in.mp4", s, Category.DAMAGED_INDEX)
    assert out.repaired is False
    assert out.stop_reason == "token_budget"
    assert out.iterations == 1


def test_wall_clock_guard():
    clock = {"t": 1000.0}

    def fake_now():
        clock["t"] += 1000.0  # each check jumps well past the timeout
        return clock["t"]

    llm = ScriptedLLM([_resp("probe", {})])
    registry = StubRegistry({"probe": {"ok": True}})
    out = run_agent(llm, registry, StubFt(), "/in.mp4", S, Category.DAMAGED_INDEX, now=fake_now)
    assert out.repaired is False
    assert out.stop_reason == "wall_clock"


def test_llm_error_stops():
    class BoomLLM:
        def complete(self, *a, **k):
            raise RuntimeError("backend down")

    registry = StubRegistry({})
    out = run_agent(BoomLLM(), registry, StubFt(), "/in.mp4", S, Category.DAMAGED_INDEX)
    assert out.stop_reason == "llm_error"
    assert out.repaired is False


def test_no_tool_calls_stops():
    llm = ScriptedLLM([LLMResponse(content="I give up", tool_calls=[], total_tokens=5)])
    out = run_agent(llm, StubRegistry({}), StubFt(), "/in.mp4", S, Category.DAMAGED_INDEX)
    assert out.stop_reason == "no_tool_calls"


def test_max_tool_calls_guard():
    # One response with many tool calls in a single round.
    many = LLMResponse(
        tool_calls=[ToolCall(id=f"c{i}", name="inspect_bytes", arguments={"n": i})
                    for i in range(S.agent_max_tool_calls + 5)],
        total_tokens=10,
    )
    llm = ScriptedLLM([many])
    registry = StubRegistry({"inspect_bytes": {"ok": True}})
    out = run_agent(llm, registry, StubFt(verify_ok=False), "/in.mp4", S, Category.DAMAGED_INDEX)
    assert out.stop_reason == "max_tool_calls"
    assert len(out.attempts) == S.agent_max_tool_calls
