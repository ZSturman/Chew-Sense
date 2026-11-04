"""Evidence-derived next actions shared by Collect and the Mac dashboard."""
from __future__ import annotations

from .eating_detector import eating_detector_stage
from .experiments import ExperimentDefinition


def timing_check_definition(version: int = 1) -> ExperimentDefinition:
    activities = []
    for index in range(3):
        activities.extend([
            dict(id=f"still-{index + 1}", kind="rest", instruction="Keep your head and mouth still",
                 duration_s=3, expected_behavior="Still head and jaw", intended_label="not_chewing"),
            dict(id=f"nod-{index + 1}", kind="sync_marker",
                 instruction="Slowly nod down and up once, then keep still", duration_s=5,
                 expected_behavior="One clear comfortable nod, then stillness", intended_label="not_chewing",
                 sync_marker=True, on_quality_loss="repeat", min_valid_fraction=0.9),
        ])
    activities.append(dict(id="still-end", kind="rest", instruction="Keep still until the recording ends",
                           duration_s=3, expected_behavior="Still head and jaw", intended_label="not_chewing"))
    return ExperimentDefinition.model_validate(dict(
        experiment_id="timing-check", version=version, title="30-Second Timing Check",
        summary="Three clear nods to check that video and AirPods motion line up. No food needed.",
        purpose="Resolve inconclusive timing evidence before collecting a larger training dataset.",
        request=dict(source="planner", weakness_id="sync-evidence",
                     summary="Existing recordings do not yet have three distinct matched nods."),
        priority="high", required=True, target_recordings=1,
        preparation=["Wear your connected AirPod and check Motion is ready.",
                     "Prop the iPad or iPhone so the preview is upright and your face is visible.",
                     "Do not eat, drink or chew gum. Make one comfortable down-and-up nod per prompt."],
        equipment=["AirPods", "Stable device support"], food=dict(kind="none"),
        airpods=dict(configuration="either"),
        camera=dict(position="Stable front camera at face height, upright preview, face and jaw visible."),
        safety_notes=["Remain seated and make comfortable head movements; stop if uncomfortable."],
        activities=activities, completion=dict(required_sync_markers=3),
        labeling=dict(wearer_no_food_attestation=True, event_review_required=False,
                      blind_evaluation_eligible=False),
        evaluation_purpose=["synchronization"], tags=dict(activity=["synchronization"])))


def build_requirements(records, dataset, evaluation, sync, readiness, history, tasks, config, eating=None):
    training = dataset["training"]
    complete = sum(bool(r.get("device_validation_complete")) for r in records)
    incomplete = sum(bool(r.get("guided") and not r.get("device_validation_complete")) for r in records)
    reviewable = [t for t in tasks if not t.get("blocked") and t.get("kind") != "recording_incomplete"]
    active = [h for h in history if h.get("state") in {"running", "requested"}]
    finished = [h for h in history if h.get("state") == "completed"]
    gold = evaluation.get("tolerances", {}).get(f"{config['evaluation']['primary_tolerance_s']:.3f}", {}).get("gold_events", 0)
    goals = [
        dict(id="positive", title="Verified chewing training recordings", current=training["verified_positive_sessions"],
             target=config["training"]["min_verified_training_sessions"]),
        dict(id="negative", title="Verified talking / walking / drinking / head-movement recordings",
             current=training.get("verified_hard_negative_sessions", 0), target=config["training"]["min_verified_negative_sessions"]),
        dict(id="evaluation", title="Independent evaluation recordings", current=evaluation["evaluation_sessions"],
             target=config["evaluation"]["min_evaluation_sessions"]),
        dict(id="gold", title="Independent chew events for accuracy verification", current=gold,
             target=config["evaluation"]["min_gold_events"]),
    ]
    actions = []
    timing_received = any(r.get("experiment_id") == "timing-check" and r.get("device_validation_complete") for r in records)
    if (sync["exceeds_budget"] or timing_received) and not sync["consistent"]:
        actions.append(dict(id="inspect-timing", kind="review", destination="mac",
                            title="Inspect the recorded timing check on the Mac",
                            detail="Timing is not confirmed. Compare the existing nods and signals before collecting more data; automatic peak matches may be ambiguous."))
    elif sync["inconclusive"] and not sync["consistent"]:
        actions.append(dict(id="timing-check", kind="record", destination="collect", experiment_id="timing-check",
                            title="Record the short timing check",
                            detail="Three distinct nods are needed before timing can be checked. No food required."))
    if sync.get("consistent") and not sync.get("human_confirmed", 0):
        actions.append(dict(id="confirm-timing", kind="review", destination="mac", title="Confirm timing on the Mac",
                            detail="A marker check is consistent. Compare the nods and signals; automatic checks never confirm clocks for you."))
    if reviewable:
        task = reviewable[0]
        actions.append(dict(id="review-next", kind="review", destination="mac", title=task.get("title", "Review recording on the Mac"),
                            session_id=task.get("session_id", ""),
                            detail="Use the phone-labeled sections in Review. Save actual event decisions; requested actions are navigation only."))
    if incomplete:
        actions.append(dict(id="incomplete", kind="record", destination="collect", title="Complete a full guided recording",
                            detail=f"{incomplete} shortened or failed recording(s) are preserved but do not count toward training or evaluation readiness."))
    if not actions and not readiness["ready"]:
        actions.append(dict(id="collect-next", kind="record", destination="collect", title="Collect a requested experiment",
                            detail="Choose a requested recording below, follow the overlays, review it and send it to the Mac."))
    stages = [
        dict(id="receive", title="Receive and process", state="complete" if records else "waiting",
             detail=f"{len(records)} received; {complete} complete; {incomplete} incomplete"),
        dict(id="timing", title="Check timing", state="complete" if sync.get("human_confirmed", 0) else "needs_action",
             detail=f"{sync.get('human_confirmed', 0)} human-confirmed; {sync['consistent']} consistent automatic checks; {sync['exceeds_budget']} outside the automatic budget"),
        dict(id="review", title="Verify labels", state="needs_action" if reviewable else "waiting",
             detail=f"{len(reviewable)} available review task(s); {training['verified_sessions']} verified training recordings"),
        dict(id="train", title="Train experimental model", state="running" if active else "complete" if finished else "blocked",
             detail="Training runs in the background" if active else f"{len(finished)} completed run(s); data gates stay enforced"),
        dict(id="test", title="Test the motion model", state="complete" if finished else "waiting",
             detail="Locked validation and test results are available" if finished else "Waiting for an eligible model; no accuracy result yet"),
        dict(id="repeat", title="Request the next data", state="ready",
             detail="Requests update from reviewed evidence and locked-test errors; nothing is auto-promoted"),
    ]
    if eating is not None:
        # Coarse chewing-span detector trained from existing human labels; descriptive only.
        stages.insert(5, eating_detector_stage(eating))
    return dict(goals=goals, stages=stages, actions=actions, review_tasks=len(reviewable),
                received_sessions=len(records), complete_sessions=complete, incomplete_sessions=incomplete,
                training_state="running" if active else "completed" if finished else "blocked")
