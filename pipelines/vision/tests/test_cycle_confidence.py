"""Vision chew-cycle confidence is the measured chewing-context probability."""

from chewsense.events.schema import MouthCycle
from chewsense.signals.mouth_state import (
    CONTEXT_CONFIDENCE_BY_NEIGHBORS,
    CONTEXT_CONFIDENCE_VERSION,
    calibrate_cycle_confidence,
)


def _cycle(index, end, confidence=0.75):
    return MouthCycle(id=f"cycle_{index:04d}", start_ts=end - 0.5, peak_ts=end - 0.25, end_ts=end,
                      duration_s=0.5, kind="closed_lip", peak_magnitude=2.0, confidence=confidence,
                      evidence={"heuristic_kind": "closed_lip"})


def test_isolated_cycles_get_low_and_rhythmic_cycles_high_confidence():
    cycles = [_cycle(1, 1.0), _cycle(2, 1.7), _cycle(3, 2.4), _cycle(4, 20.0, confidence=0.8)]
    calibrated = calibrate_cycle_confidence(cycles)
    assert [c.confidence for c in calibrated] == [
        CONTEXT_CONFIDENCE_BY_NEIGHBORS[2], CONTEXT_CONFIDENCE_BY_NEIGHBORS[2],
        CONTEXT_CONFIDENCE_BY_NEIGHBORS[2], CONTEXT_CONFIDENCE_BY_NEIGHBORS[0],
    ]
    assert calibrated[3].evidence["heuristic_confidence"] == 0.8
    assert calibrated[3].evidence["rhythm_neighbors"] == 0
    assert calibrated[0].evidence["confidence_basis"] == CONTEXT_CONFIDENCE_VERSION
    assert cycles[3].confidence == 0.8  # inputs are not mutated
    # Isolated proposals now fall under the loop's low-confidence review threshold.
    from chewsense.loop.review_tasks import LOW_CONFIDENCE
    assert calibrated[3].confidence < LOW_CONFIDENCE <= calibrated[0].confidence
