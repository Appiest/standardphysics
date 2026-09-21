import json
from uuid import uuid4

import numpy as np
import pytest
from scipy import ndimage
from standardphysics_agents.evaluation.scan_campaign import (
    TaskDomain,
    bfs_path,
    evaluate_domain,
    make_replay,
    run_campaign,
    task_domain,
    unique_indices,
)
from standardphysics_agents.evaluation.scan_space import (
    BodyProfile,
    ScanSpace,
    build_spaces,
    floor_polygon,
    world_triangles,
)
from standardphysics_agents.evaluation.scan_tasks import (
    ScanTask,
    TaskSuite,
    choose_task,
    validate_tasks,
)
from standardphysics_agents.router.typesafe import TypeSafeRouter
from standardphysics_contracts import LidarMesh, Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures import build_graph, node_id
from standardphysics_pipeline import Grid


def task(task_id="medicine", node_id=None):
    return ScanTask(id=task_id, title="Retrieve medicine", target_node_id=node_id or uuid4(),
                    action="pick_up", prop="medicine", assumption="Hypothetical bottle")


def test_affine_sampling_is_unique_and_reproducible_across_composite_spaces():
    for size in (1, 6, 1000, 12321):
        first = unique_indices(size, size, np.random.default_rng(7))
        assert sorted(first) == list(range(size))
        assert np.array_equal(first, unique_indices(size, size, np.random.default_rng(7)))
    with pytest.raises(ValueError, match="distinct"):
        unique_indices(5, 6, np.random.default_rng())


def test_component_queries_match_bfs_and_separate_reach_from_routes():
    mask = np.ones((5, 5), dtype=bool)
    mask[:, 2] = False
    labels, _ = ndimage.label(mask)
    grid = Grid(0, 0, 0.25, ~mask, np.full(mask.shape, -1), [])
    space = ScanSpace(grid, mask.astype(float), mask, labels, np.argwhere(mask),
                      BodyProfile("test", 0.1, 1.2, 1.1, 1, 0.5), 0)
    domain = TaskDomain(space, task(), np.array([[2, 0], [2, 4]]),
                        np.array([[0.125, 0.625, 1], [3, 3, 1]]))
    indices = np.arange(domain.size)
    outcomes, values = evaluate_domain(domain, indices)
    assert set(outcomes) == {0, 1, 2}
    for index, outcome in enumerate(outcomes):
        start, goal = tuple(values['starts'][index]), tuple(values['goals'][index])
        assert bool(bfs_path(mask, start, goal)) == (outcome != 0)
        replay = make_replay(domain, index, index+1)
        assert replay['independent_bfs_agrees']
        assert all(mask[grid.to_cell(point['x'], point['y'])] for point in replay['path'])


def test_diagonal_corner_cannot_connect_a_body_route():
    assert bfs_path(np.eye(2, dtype=bool), (0, 0), (1, 1)) == []


def test_floor_uses_full_3d_transform_for_vertical_local_axes():
    # RoomPlan floor dimensions can have y=0 and extent along local z.
    floor = SceneNode(id=uuid4(), kind="floor", label="Floor", raw_category="floor",
                      dimensions=Vec3(x=4, y=0, z=6), movable=False, quality="measured",
                      transform=Mat4(m=[1,0,0,2, 0,0,1,3, 0,-1,0,0, 0,0,0,1]))
    graph = SceneGraph(scan_id=uuid4(), revision=0, nodes=[floor])
    polygon = floor_polygon(graph)
    assert polygon.min(axis=0) == pytest.approx([0, 0])
    assert polygon.max(axis=0) == pytest.approx([4, 6])


def test_lidar_converts_arkit_axes_and_floor_height_once():
    mesh = LidarMesh.model_validate({'floorY':-1, 'parts':[{'id':str(uuid4()),
        'transform':[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
        'vertices':[1,0,2, 2,0,2, 1,1,2], 'triangles':[0,1,2]}]})
    triangles = world_triangles(mesh)
    assert triangles[0,0] == pytest.approx([1,-2,1])
    with pytest.raises(ValueError, match="floorY"):
        world_triangles(mesh.model_copy(update={'floorY':None}))


class Transport:
    def __init__(self, choice):
        self.choice = choice
    def post(self, url, body, headers):
        request = json.loads(body)
        assert set(request['questions']['task']['criteria']) == {'medicine', 'computer'}
        return json.dumps({
            'model': 'jev-test',
            'answers': {'task': {
                'type': 'choice',
                'choice': self.choice,
                'probabilities': {
                    'medicine': 1.0 if self.choice == 'medicine' else 0.0,
                    'computer': 1.0 if self.choice == 'computer' else 0.0,
                },
                'confidence': 1.0,
            }},
            'usage': {'input_tokens': 10, 'output_tokens': 1},
        }).encode()


def test_real_choice_shape_controls_next_batch_and_unknown_choice_authorizes_none():
    tasks = [task(), task('computer')]
    picked, receipt = choose_task(tasks, [], TypeSafeRouter(api_key='test', transport=Transport('computer')))
    assert picked.id == 'computer'
    assert receipt['calls'] == 1
    assert receipt['response']['usage']['input_tokens'] == 10
    with pytest.raises(RuntimeError, match="response_choice_unknown"):
        choose_task(tasks, [], TypeSafeRouter(api_key='test', transport=Transport('invented')))


def test_model_cannot_anchor_a_task_to_an_unknown_scan_object():
    tasks = [task(f'task-{index}') for index in range(10)]
    tasks[1] = tasks[1].model_copy(update={'prop':'computer'})
    suite = TaskSuite(tasks=tasks)
    with pytest.raises(ValueError, match='scanned table'):
        validate_tasks(suite, SceneGraph(scan_id=uuid4(),revision=0,nodes=[]))


def test_target_placements_include_edges_without_hanging_props_off_the_table():
    table_id = uuid4()
    node = SceneNode(id=table_id,kind='object',label='Table',raw_category='table',
                     dimensions=Vec3(x=1,y=1,z=.8),transform=Mat4.translation(2,2,.4),
                     movable=True,quality='measured')
    graph = SceneGraph(scan_id=uuid4(),revision=0,nodes=[node])
    occupied = np.zeros((40,40),dtype=bool)
    occupied[15:25,15:25] = True
    mask = ~occupied
    grid = Grid(0,0,.1,occupied,np.full(occupied.shape,-1),[])
    labels,_ = ndimage.label(mask)
    space = ScanSpace(grid,mask.astype(float),mask,labels,np.argwhere(mask),
                      BodyProfile('test',.3,1.3,1.2,1,.6),0)
    domain = task_domain(graph,space,task(node_id=table_id))
    assert len(domain.targets) == 25
    assert domain.targets[:,:2].min(axis=0) == pytest.approx([1.54,1.54])
    assert domain.targets[:,:2].max(axis=0) == pytest.approx([2.46,2.46])
    assert np.all(domain.targets[:,2] == pytest.approx(.825))


def test_actual_mesh_height_blocks_a_tall_body_but_not_a_lower_body():
    floor = SceneNode(id=uuid4(),kind='floor',label='Floor',raw_category='floor',
                      dimensions=Vec3(x=4,y=4,z=0),transform=Mat4.translation(2,2,0),
                      movable=False,quality='measured')
    graph = SceneGraph(scan_id=uuid4(),revision=0,nodes=[floor])
    mesh = LidarMesh.model_validate({'floorY':0, 'parts':[{'id':str(uuid4()),
        'transform':[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
        'vertices':[2,1.4,0, 2,1.4,-4, 2,1.6,-4, 2,1.6,0],
        'triangles':[0,1,2,0,2,3]}]})
    profiles = (BodyProfile('low',.3,1.3,1.1,1,.6),BodyProfile('tall',.3,1.8,1.6,1.4,.6))
    low,tall = build_spaces(graph,mesh,profiles,cell_size=.1)
    point = low.grid.to_cell(2,2)
    assert low.traversable[point]
    assert not tall.traversable[point]
    assert not np.any(tall.traversable & ~low.traversable)


def test_campaign_runs_distinct_bounded_cases_and_preserves_the_scan(tmp_path):
    graph = build_graph()
    mesh = LidarMesh.model_validate({'floorY': 0, 'parts': [{'id': str(uuid4()),
        'transform': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        'vertices': [0, 0, 0, 1, 0, 0, 0, 0, -1], 'triangles': [0, 1, 2]}]})
    props = [
        'medicine', 'computer', 'drink', 'book', 'phone',
        'food', 'bag', 'paper', 'personal_item', 'table_edge',
    ]
    suite = TaskSuite(tasks=[
        ScanTask(
            id=f'task-{index}',
            title=f'Task {index}',
            target_node_id=node_id(f'table_{index % 4 + 1}'),
            action='operate',
            prop=prop,
            assumption='Hypothetical prop on a measured table',
        )
        for index, prop in enumerate(props)
    ])
    original = graph.model_dump_json()

    result = run_campaign(
        graph,
        mesh,
        suite,
        evaluations=40,
        seed=7,
        output=tmp_path / 'campaign.json',
        planner=lambda remaining, history: (
            remaining[0], {'source': 'test', 'calls': 0}
        ),
    )
    assert result['complete']
    assert result['evaluations'] == 40
    assert sum(result['outcomes'].values()) == 40
    assert len(result['batches']) == 10
    assert graph.model_dump_json() == original
