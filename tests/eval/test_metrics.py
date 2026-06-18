from audio_repair.core.models import RepairReport
from audio_repair.eval.corruptors import CorruptionManifest
from audio_repair.eval.metrics import aggregate, score_case
from audio_repair.eval.report import write_scorecard


def _manifest(cat, recoverable):
    return CorruptionManifest(category=cat, expected_recoverable=recoverable, path="/x")


def test_score_recoverable_success():
    m = _manifest("WRONG_CONTAINER_VS_EXTENSION", True)
    rep = RepairReport(status="repaired", input_s3="s3://b/k", strategy="stream_copy_remux",
                       elapsed_ms=120)
    s = score_case(m, rep)
    assert s.success is True
    assert s.repaired_readable is True
    assert s.giveup_correct is False


def test_score_recoverable_but_failed():
    m = _manifest("DAMAGED_CONTAINER_HEADER", True)
    rep = RepairReport(status="unrepairable", input_s3="s3://b/k")
    s = score_case(m, rep)
    assert s.success is False
    assert s.giveup_correct is False  # it was supposed to be recoverable


def test_score_giveup_correct():
    m = _manifest("ZERO_BYTE_OR_NONMEDIA", False)
    rep = RepairReport(status="unrepairable", input_s3="s3://b/k")
    s = score_case(m, rep)
    assert s.giveup_correct is True
    assert s.success is True  # correctly giving up on an unrecoverable file counts


def test_aggregate_rates():
    scores = [
        score_case(_manifest("WRONG_CONTAINER_VS_EXTENSION", True),
                   RepairReport(status="repaired", input_s3="s3://b/1",
                                strategy="stream_copy_remux", elapsed_ms=100)),
        score_case(_manifest("ZERO_BYTE_OR_NONMEDIA", False),
                   RepairReport(status="unrepairable", input_s3="s3://b/2", elapsed_ms=10)),
        score_case(_manifest("DAMAGED_CONTAINER_HEADER", True),
                   RepairReport(status="unrepairable", input_s3="s3://b/3", elapsed_ms=300)),
    ]
    card = aggregate(scores)
    assert card.total == 3
    assert card.success == 2  # one repaired + one correct giveup
    assert abs(card.success_rate - 2 / 3) < 1e-9
    assert card.fast_path_hits == 1
    assert card.giveup_total == 1
    assert card.giveup_correct == 1
    assert card.p95_elapsed_ms == 300


def test_aggregate_empty():
    card = aggregate([])
    assert card.total == 0
    assert card.success_rate == 0.0


def test_write_scorecard(tmp_path):
    card = aggregate([
        score_case(_manifest("ZERO_BYTE_OR_NONMEDIA", False),
                   RepairReport(status="unrepairable", input_s3="s3://b/2")),
    ])
    paths = write_scorecard(card, tmp_path / "out")
    assert (tmp_path / "out" / "scorecard.json").exists()
    md = (tmp_path / "out" / "scorecard.md").read_text()
    assert "Audio Repair Eval Scorecard" in md
    assert paths["json"].endswith("scorecard.json")
