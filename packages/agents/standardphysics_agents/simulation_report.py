"""One wire report shared by the real-room CLI and HTTP jobs."""
from dataclasses import asdict

from standardphysics_contracts import LidarMesh, SimulationFeedback, SimulationResult, graph_hash

SIMULATION_LIMITATIONS = (
    "Screening identifies potential issues; it does not certify ADA compliance.",
    "Repeated trials test routing consistency; unique layouts count distinct measured arrangements.",
    "Reach checks test approach and target height; operability, horizontal reach and real-world use need review.",
    "Captured LiDAR remains evidence of the original room; moved furniture needs a new scan to confirm it.",
)

def simulation_result(batch, rules, ledger, mesh: LidarMesh | None) -> SimulationResult:
    enabled = rules.enabled(ledger, max_tier=3)
    feedback = []
    for item in batch.feedback:
        payload = asdict(item)
        payload["blocking_node_ids"] = [str(node_id) for node_id in item.blocking_node_ids]
        feedback.append(SimulationFeedback.model_validate(payload))
    return SimulationResult(
        total_runs=batch.total_runs,
        completed_runs=batch.completed_runs,
        rejected_runs=batch.rejected_runs,
        unique_layouts=len({graph_hash(run.final_graph) for run in batch.runs}),
        action_counts=batch.action_counts,
        rejection_counts=batch.rejection_counts,
        feedback=feedback,
        recommended_graph=batch.recommended_graph,
        rules_checked=len(enabled),
        rules_total=len(rules.rules),
        preview=any(
            "unverified preview" in ledger.entry_for(rule).verified_by
            for rule in enabled
        ),
        mesh_checked=mesh is not None,
        limitations=list(SIMULATION_LIMITATIONS),
    )

