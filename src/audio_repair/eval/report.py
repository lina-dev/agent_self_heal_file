"""Scorecard rendering (spec §8).

Writes both a machine-readable JSON scorecard and a human-readable markdown
summary to a (gitignored) output directory. The markdown is generated, never
committed.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import Scorecard


def _markdown(card: Scorecard) -> str:
    lines = [
        "# Audio Repair Eval Scorecard",
        "",
        f"- **Cases:** {card.total}",
        f"- **Success rate:** {card.success_rate:.0%} ({card.success}/{card.total})",
        f"- **Fast-path hit rate:** {card.fast_path_rate:.0%} ({card.fast_path_hits} hits)",
        f"- **Giveup-correct rate:** {card.giveup_correct_rate:.0%} "
        f"({card.giveup_correct}/{card.giveup_total})",
        f"- **Mean latency:** {card.mean_elapsed_ms:.0f} ms",
        f"- **p95 latency:** {card.p95_elapsed_ms} ms",
        "",
        "## Per-category",
        "",
        "| Category | Total | Success | Giveup-correct | Success rate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in sorted(card.per_category):
        st = card.per_category[name]
        lines.append(
            f"| {name} | {st.total} | {st.success} | {st.giveup_correct} | {st.success_rate:.0%} |"
        )
    return "\n".join(lines) + "\n"


def write_scorecard(card: Scorecard, out_dir: str | Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scorecard.json"
    md_path = out_dir / "scorecard.md"
    json_path.write_text(card.model_dump_json(indent=2))
    md_path.write_text(_markdown(card))
    return {"json": str(json_path), "markdown": str(md_path)}
