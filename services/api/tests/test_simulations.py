from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from standardphysics_agents import (
    AdaptiveRedesignResult,
    TypeSafeCallBudget,
    VerificationLedger,
    load_pack,
)
from standardphysics_contracts import AdaptiveRoundResult, Scenario, SimulationRequest, graph_hash
from standardphysics_fixtures import build_graph, build_scenario

from conftest import drain, no_blender_stages
from standardphysics_api import repository as repo
from standardphysics_api.simulations import _progress_stride, _run_accessibility_loop
from standardphysics_api.stages import preview_ledger


def shop(client):
    return client.get('/api/scans').json()['scans'][0]['id']


def test_screening_is_queued_snapshotted_and_does_not_save_a_layout(make_client):
    with make_client(seed=True) as client:
        scan_id = shop(client)
        before = client.get(f'/api/scans/{scan_id}/scene').json()
        response = client.post(
            f'/api/scans/{scan_id}/simulations',
            json={'base_revision': 0, 'samples': 4, 'max_workers': 2},
        )
        assert response.status_code == 202, response.text
        assert response.json()['state'] == 'queued'
        assert client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0}).status_code == 409
        drain(client)
        result = client.get(f'/api/scans/{scan_id}/simulations?revision=0').json()
        assert result['state'] == 'done', result
        assert result['completed'] == 4
        assert result['result']['total_runs'] == 4
        assert result['result']['feedback']
        assert result['result']['unique_layouts'] >= 1
        assert result['result']['preview'] is True
        assert result['result']['physics']['resolution_inches'] == 1.0
        assert result['result']['typesafe_calls'] == 0
        assert result['result']['astra_calls'] == 0
        assert result['result']['loop_cycles'] == 1
        assert result['result']['violating_trials'] == 4
        assert result['result']['ada_rule_violations'] >= 1
        assert result['result']['converged'] is False
        assert result['result']['limitations']
        assert client.get(f'/api/scans/{scan_id}/scene').json() == before


def test_screening_without_confirmed_route_uses_an_inferred_entrance(make_client):
    with make_client(seed=True) as client:
        scan_id = shop(client)
        with client.app.state.database.transaction() as connection:
            connection.execute("DELETE FROM scenarios WHERE scan_id=?", (scan_id,))

        response = client.post(
            f'/api/scans/{scan_id}/simulations',
            json={'base_revision': 0, 'samples': 1, 'max_workers': 1},
        )

        assert response.status_code == 202, response.text
        with client.app.state.database.connect() as connection:
            snapshot = connection.execute(
                "SELECT scenario_json FROM simulations WHERE scan_id=? AND revision=0",
                (scan_id,),
            ).fetchone()
        simulation_scenario = Scenario.model_validate_json(snapshot["scenario_json"])
        assert any(stop.name == "Entrance" for stop in simulation_scenario.stops)
        assert client.get(f'/api/scans/{scan_id}/scenario').status_code == 404


def test_live_trials_persist_each_result_while_local_batches_update_one_percent():
    assert _progress_stride(SimulationRequest(base_revision=0, router="typesafe", samples=100)) == 1
    assert _progress_stride(SimulationRequest(base_revision=0, router="local", samples=1_000)) == 10


def test_accessibility_loop_waits_for_repair_then_retests_full_candidate_batch(
    monkeypatch
):
    graph = build_graph()
    scenario = build_scenario()
    pack = load_pack()
    ledger = preview_ledger()
    moved_id = next(node.id for node in graph.nodes if node.movable)
    candidate = graph.model_copy(update={
        "revision": graph.revision + 1,
        "nodes": [
            node.model_copy(update={
                "transform": node.transform.model_copy(update={
                    "m": [
                        value + (0.1 if index == 3 else 0)
                        for index, value in enumerate(node.transform.m)
                    ]
                })
            }) if node.id == moved_id else node
            for node in graph.nodes
        ],
    })
    failing_batch = SimpleNamespace(violating_trials=73, recommended_graph=graph)
    clean_batch = SimpleNamespace(violating_trials=0, recommended_graph=candidate)
    batches = iter([failing_batch, clean_batch])
    tested_graphs = []
    progress_cycles = []

    def run_batch(current, **kwargs):
        tested_graphs.append(current)
        return next(batches)

    astra_round = AdaptiveRoundResult(
        round=1,
        base_graph_hash=graph_hash(graph),
        astra_model="openai/gpt-6-astra",
        accepted=True,
        reasons=["deterministic_improvement_and_jev_preference"],
    )
    monkeypatch.setattr("standardphysics_api.simulations.run_workflow_batch", run_batch)
    monkeypatch.setattr(
        "standardphysics_api.simulations.run_adaptive_redesign",
        lambda *args, **kwargs: AdaptiveRedesignResult(
            graph=candidate, rounds=(astra_round,), astra_calls=1
        ),
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.simulation_result", lambda *args: object()
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.route_trial_evidence", lambda *args: {"trials": 1_000}
    )
    rule_counts = iter([2, 0])
    monkeypatch.setattr(
        "standardphysics_api.simulations._ada_rule_violation_count",
        lambda *args, **kwargs: next(rule_counts),
    )
    request = SimulationRequest(
        base_revision=graph.revision,
        samples=1_000,
        max_workers=1_000,
        router="typesafe",
        refine_with_astra=True,
        typesafe_call_limit=50_000,
        astra_rounds=4,
    )

    result = _run_accessibility_loop(
        graph,
        scenario,
        workflows=[object()],
        profiles=[object()],
        request=request,
        measure_factory=lambda: object(),
        router_factory=lambda: object(),
        rules=pack,
        ledger=ledger,
        mesh=None,
        budget=TypeSafeCallBudget(50_000),
        on_progress=lambda completed: None,
        on_cycle_start=progress_cycles.append,
    )

    assert tested_graphs == [graph, candidate]
    assert progress_cycles == [1, 2]
    assert result.batch is clean_batch
    assert result.candidate == candidate
    assert result.cycles == 2
    assert result.violating_trials == 0
    assert result.ada_rule_violations == 0
    assert result.converged is True
    assert result.astra_calls == 1
    assert list(result.adaptive_rounds) == [astra_round]


def test_accessibility_loop_never_converges_while_an_ada_problem_remains(
    monkeypatch
):
    graph = build_graph()
    rules = load_pack()
    ledger = preview_ledger()
    batch = SimpleNamespace(violating_trials=0, recommended_graph=graph)
    rejected_round = AdaptiveRoundResult(
        round=1,
        base_graph_hash=graph_hash(graph),
        accepted=False,
        reasons=["no_actionable_furniture_failure"],
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.run_workflow_batch", lambda *args, **kwargs: batch
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.run_adaptive_redesign",
        lambda *args, **kwargs: AdaptiveRedesignResult(
            graph=None, rounds=(rejected_round,), astra_calls=0
        ),
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.simulation_result", lambda *args: object()
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations.route_trial_evidence", lambda *args: {}
    )
    monkeypatch.setattr(
        "standardphysics_api.simulations._ada_rule_violation_count",
        lambda *args, **kwargs: 1,
    )
    request = SimulationRequest(
        base_revision=graph.revision,
        samples=1_000,
        max_workers=1_000,
        router="typesafe",
        refine_with_astra=True,
        typesafe_call_limit=50_000,
    )

    result = _run_accessibility_loop(
        graph,
        build_scenario(),
        workflows=[object()],
        profiles=[object()],
        request=request,
        measure_factory=lambda: object(),
        router_factory=lambda: object(),
        rules=rules,
        ledger=ledger,
        mesh=None,
        budget=TypeSafeCallBudget(50_000),
        on_progress=None,
    )

    assert result.violating_trials == 0
    assert result.ada_rule_violations == 1
    assert result.converged is False
    assert "no_actionable_furniture_failure" in result.stop_reason


def test_live_preflight_rejects_missing_key_before_queuing(make_client, monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    with make_client(seed=True) as client:
        scan_id = shop(client)
        response = client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0, 'router': 'typesafe'})
        assert response.status_code == 409
        assert client.get(f'/api/scans/{scan_id}/simulations?revision=0').status_code == 404


def test_job_limits_and_stale_layout_rejected(make_client):
    with make_client(seed=True) as client:
        scan_id = shop(client)
        for body in (
            {'samples': 10001},
            {'samples': 0},
            {'max_workers': 1001},
            {'max_workers': 0},
            {'router': 'made-up'},
            {'exhaustive_evaluations': 39},
            {'router': 'typesafe', 'refine_with_astra': True, 'typesafe_call_limit': 4},
            {'unknown': True},
        ):
            response = client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0, **body})
            assert response.status_code == 400
        response = client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 9})
        assert response.status_code == 409


def test_simulation_failure_keeps_room_ready_and_sanitizes_errors(make_client, monkeypatch):
    def fail(*args):
        raise RuntimeError('secret upstream payload')
    monkeypatch.setattr('standardphysics_api.worker.run_simulation', fail)
    with make_client(seed=True) as client:
        scan_id = shop(client)
        client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0, 'samples': 1})
        drain(client)
        state = client.get(f'/api/scans/{scan_id}/simulations?revision=0').json()
        assert state['state'] == 'failed'
        assert 'secret upstream payload' not in state['error']
        assert client.get(f'/api/scans/{scan_id}').json()['state'] == 'ready'


def test_rebuild_keeps_measured_geometry_and_rejects_stale_revision(make_client):
    calls = []

    def label(graph):
        calls.append(graph.revision)
        return graph.model_copy(
            update={
                'nodes': [
                    node.model_copy(update={'label': 'Reviewed ' + node.label})
                    for node in graph.nodes
                ]
            }
        )
    with make_client(seed=True, stages=no_blender_stages(label=label)) as client:
        scan_id = shop(client)
        before = client.get(f'/api/scans/{scan_id}/scene').json()
        result = client.post(f'/api/scans/{scan_id}/rebuild', json={'base_revision': 0})
        assert result.status_code == 201, result.text
        assert calls == [0]
        after = result.json()
        assert after['revision'] == 1
        for original, rebuilt in zip(before['nodes'], after['nodes']):
            assert original['dimensions'] == rebuilt['dimensions']
            assert original['transform'] == rebuilt['transform']
            assert original['id'] == rebuilt['id']
            assert rebuilt['label'].startswith('Reviewed ')
        assert client.post(f'/api/scans/{scan_id}/rebuild', json={'base_revision': 0}).status_code == 409
        drain(client)
        with client.app.state.database.connect() as connection:
            assert repo.get_revision(connection, __import__('uuid').UUID(scan_id), 1)['glb_path'] is not None


def test_deleting_room_cleans_queued_simulation(make_client):
    with make_client(seed=True) as client:
        scan_id = shop(client)
        response = client.post(
            f'/api/scans/{scan_id}/simulations',
            json={'base_revision': 0, 'samples': 1},
        )
        assert response.status_code == 202
        assert client.delete(f'/api/scans/{scan_id}').status_code == 204
        assert client.get(f'/api/scans/{scan_id}/simulations?revision=0').status_code == 404
        drain(client)


def test_deletion_waits_for_running_jobs(make_client):
    with make_client(seed=True) as client:
        scan_id = shop(client)
        client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0, 'samples': 1})
        with client.app.state.database.transaction() as connection:
            connection.execute("UPDATE jobs SET state='running' WHERE scan_id=? AND kind='simulate'", (scan_id,))
        assert client.delete(f'/api/scans/{scan_id}').status_code == 409
        assert client.get(f'/api/scans/{scan_id}').status_code == 200


def test_auto_deep_campaign_waits_for_route_and_is_idempotent(
    make_client, monkeypatch
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("standardphysics_api.simulations._live_ready", lambda stages, request: None)
    with make_client(
        seed=True,
        auto_deep_simulation=True,
        auto_deep_samples=7,
        auto_deep_typesafe_call_limit=23,
        auto_deep_astra_rounds=3,
        auto_deep_exhaustive_evaluations=400,
    ) as client:
        scan_id = shop(client)
        worker = client.app.state.worker
        database = client.app.state.database
        with database.transaction() as connection:
            connection.execute("DELETE FROM scenarios WHERE scan_id=?", (scan_id,))
        worker._assess(UUID(scan_id), 0)
        with database.connect() as connection:
            assert connection.execute(
                "SELECT 1 FROM simulations WHERE scan_id=?", (scan_id,)
            ).fetchone() is None

        with database.transaction() as connection:
            repo.save_scenario(connection, UUID(scan_id), build_scenario())
        worker._assess(UUID(scan_id), 0)
        worker._assess(UUID(scan_id), 0)
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT request_json FROM simulations WHERE scan_id=?", (scan_id,)
            ).fetchall()
        assert len(rows) == 1
        request = SimulationRequest.model_validate_json(rows[0]["request_json"])
        assert request.samples == 7
        assert request.typesafe_call_limit == 23
        assert request.astra_rounds == 3
        assert request.exhaustive_evaluations == 400


def _typesafe_configured(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setenv('TYPESAFE_BASE_URL', 'https://typesafe.example')


def test_typesafe_route_trials_queue_on_preview_rules(make_client, monkeypatch):
    _typesafe_configured(monkeypatch)
    with make_client(seed=True) as client:
        scan_id = shop(client)
        body = {'base_revision': 0, 'router': 'typesafe', 'samples': 100, 'typesafe_call_limit': 400}
        response = client.post(f'/api/scans/{scan_id}/simulations', json=body)
        assert response.status_code == 202, response.text
        assert response.json()['router'] == 'typesafe'


def test_typesafe_route_trials_need_rules_to_screen_against(make_client, monkeypatch):
    _typesafe_configured(monkeypatch)
    with make_client(seed=True, stages=no_blender_stages(ledger_factory=VerificationLedger)) as client:
        scan_id = shop(client)
        response = client.post(f'/api/scans/{scan_id}/simulations', json={'base_revision': 0, 'router': 'typesafe'})
        assert response.status_code == 409
        assert 'SP_PREVIEW_UNVERIFIED_RULES' in response.json()['error']
        assert client.get(f'/api/scans/{scan_id}/simulations?revision=0').status_code == 404


def test_astra_redesign_queues_on_preview_rules_but_the_exhaustive_campaign_does_not(make_client, monkeypatch):
    _typesafe_configured(monkeypatch)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    with make_client(seed=True) as client:
        scan_id = shop(client)
        exhaustive = {'base_revision': 0, 'router': 'typesafe', 'exhaustive_evaluations': 40}
        refused = client.post(f'/api/scans/{scan_id}/simulations', json=exhaustive)
        assert refused.status_code == 409
        assert 'human-verified' in refused.json()['error']
        astra = {'base_revision': 0, 'router': 'typesafe', 'refine_with_astra': True, 'samples': 4}
        assert client.post(f'/api/scans/{scan_id}/simulations', json=astra).status_code == 202
