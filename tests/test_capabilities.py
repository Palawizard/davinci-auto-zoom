from davinci_auto_zoom.resolve.capabilities import QUESTIONS, Status, evaluate, summarize
from davinci_auto_zoom.resolve.docs import DocumentedMethod


def _documented(owner: str, method: str) -> dict[str, dict[str, DocumentedMethod]]:
    return {owner: {method: DocumentedMethod(owner, method, "", "Bool", "")}}


def _result(results: tuple, key: str):  # type: ignore[type-arg]
    return next(r for r in results if r.key == key)


def test_documented_and_present_read_only_capability_is_confirmed() -> None:
    runtime = {"Timeline": frozenset({"GetTrackCount"})}
    results = evaluate(_documented("Timeline", "GetTrackCount"), runtime)
    assert _result(results, "track.count").status is Status.CONFIRMED


def test_write_capability_is_never_marked_confirmed() -> None:
    results = evaluate(
        _documented("MediaPool", "AppendToTimeline"), {"MediaPool": frozenset({"AppendToTimeline"})}
    )
    assert _result(results, "write.append_asset").status is Status.DOCUMENTED_WRITE_GATED


def test_documented_but_absent_at_runtime() -> None:
    results = evaluate(_documented("Timeline", "GetTrackCount"), {})
    assert _result(results, "track.count").status is Status.DOCUMENTED_ONLY


def test_runtime_only_method_is_not_promoted_to_supported() -> None:
    results = evaluate({}, {"MediaPoolItem": frozenset({"GetTranscription"})})
    assert _result(results, "speech.read_transcript").status is Status.UNDOCUMENTED


def test_absent_everywhere_is_unsupported() -> None:
    results = evaluate({}, {})
    assert _result(results, "speech.read_transcript").status is Status.UNSUPPORTED
    assert summarize(results) == {str(Status.UNSUPPORTED): len(QUESTIONS)}


def test_capability_keys_are_unique() -> None:
    keys = [question.key for question in QUESTIONS]
    assert len(keys) == len(set(keys))
