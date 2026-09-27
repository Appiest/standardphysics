import hashlib
import json
import shutil

from standardphysics_contracts import Mat4, NodeTextureCoverage, TextureBuild, TextureCoverage
from standardphysics_fixtures import build_graph
from standardphysics_pipeline.textures import BakeResult

from conftest import FIXTURE_DATA, create_scan, drain, no_blender_stages, put_artifact
from standardphysics_api import repository as repo


def _bake(inputs):
    inputs.out_dir.mkdir(parents=True, exist_ok=True)
    output = inputs.out_dir / 'scene.glb'
    shutil.copyfile(FIXTURE_DATA / 'shop.glb', output)
    coverage = TextureCoverage(textured_fraction=0.5, nodes=[NodeTextureCoverage(node_id=n.id, textured_fraction=0.5) for n in inputs.bake_graph.nodes], needs_another_view=[])
    return BakeResult(output, [], coverage, 1, 0.1)


def _room(client):
    scan_id = create_scan(client)
    from uuid import UUID
    graph = build_graph().model_copy(update={'scan_id': UUID(scan_id), 'capture_to_room': Mat4.identity()})
    with client.app.state.database.transaction() as connection:
        repo.save_revision(connection, graph, source='ingest')
    return scan_id, graph


def _photos(client, scan_id, *, manifest_first=False, wrong_hash=False):
    frame = b'photo fixture: baker injected'
    pose = {'metadata_version': 2, 'frame_id': 'frame-0007', 'image': 'frames/frame_0007.jpg', 'timestamp': 1, 'transform': Mat4.identity().m, 'intrinsics': [100,0,0,0,100,0,50,50,1], 'image_width':100, 'image_height':100, 'calibration_width':100, 'calibration_height':100, 'image_orientation':'sensor'}
    poses = json.dumps([pose]).encode()
    manifest = json.dumps({'manifest_version':1, 'poses_sha256':hashlib.sha256(poses).hexdigest(), 'frames':[{'frame_id':'frame-0007', 'sha256':'0'*64 if wrong_hash else hashlib.sha256(frame).hexdigest(), 'bytes':len(frame)}]}).encode()
    assert put_artifact(client, scan_id, 'poses', poses, 'poses').status_code == 201
    if manifest_first:
        assert put_artifact(client, scan_id, 'photo-manifest', manifest, 'photo_manifest').status_code == 201
        assert client.get(f'/api/scans/{scan_id}/textures').json()['state'] == 'waiting_for_photos'
    assert put_artifact(client, scan_id, 'frame-0007', frame, 'frames').status_code == 201
    if not manifest_first:
        assert put_artifact(client, scan_id, 'photo-manifest', manifest, 'photo_manifest').status_code == 201
    return manifest


def test_no_photos_truthful_and_get_never_queues(client):
    scan, _ = _room(client)
    result=client.get(f'/api/scans/{scan}/textures').json()
    assert result['state']=='needs_photos' and result['build'] is None
    with client.app.state.database.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


def test_complete_manifest_autoqueues_once_and_publishes_immutable_asset(make_client):
    with make_client(stages=no_blender_stages(bake_textures=_bake)) as client:
        scan, graph=_room(client)
        manifest=_photos(client, scan)
        assert client.get(f'/api/scans/{scan}/textures').json()['state']=='queued'
        assert put_artifact(client, scan, 'photo-manifest', manifest, 'photo_manifest').status_code==200
        assert client.post(f'/api/scans/{scan}/textures',json={'revision':0}).status_code==202
        with client.app.state.database.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM jobs WHERE kind='texture'").fetchone()[0]==1
        drain(client)
        status=client.get(f'/api/scans/{scan}/textures').json()
        assert status['state']=='complete' and status['exact']
        response=client.get(status['build']['glb_url'])
        assert response.status_code==200 and 'immutable' in response.headers['cache-control']
        with client.app.state.database.connect() as c:
            assert repo.graph_of(repo.get_revision(c,graph.scan_id,0))==graph
            assert c.execute('SELECT COUNT(*) FROM assessments').fetchone()[0]==0


def test_partial_manifest_waits_then_queues_after_last_photo(make_client):
    with make_client(stages=no_blender_stages(bake_textures=_bake)) as client:
        scan,_=_room(client)
        _photos(client,scan,manifest_first=True)
        assert client.get(f'/api/scans/{scan}/textures').json()['state']=='queued'


def test_manifest_rejects_duplicate_ids_and_mismatched_photo_hash(client):
    scan,_=_room(client)
    invalid=json.dumps({'frames':[]}).encode()
    assert put_artifact(client,scan,'bad-manifest',invalid,'photo_manifest').status_code==400
    _photos(client,scan,wrong_hash=True)
    status=client.get(f'/api/scans/{scan}/textures').json()
    assert status['state']=='failed' and not status['can_retry']


def test_failed_bake_keeps_scan_ready_and_explicit_retry_is_deduplicated(make_client):
    def broken(inputs):
        raise RuntimeError('test bake failure')
    with make_client(stages=no_blender_stages(bake_textures=broken)) as client:
        scan,graph=_room(client)
        _photos(client,scan)
        drain(client)
        status=client.get(f'/api/scans/{scan}/textures').json()
        assert status['state']=='failed' and status['can_retry']
        assert client.get(f'/api/scans/{scan}').json()['state']!='failed'
        client.app.state.worker.stages.bake_textures=_bake
        client.post(f'/api/scans/{scan}/textures',json={'revision':0})
        drain(client)
        assert client.get(f'/api/scans/{scan}/textures').json()['state']=='complete'


def test_moved_furniture_reuses_texture_build_and_new_shape_is_stale(make_client):
    with make_client(stages=no_blender_stages(bake_textures=_bake)) as client:
        scan,graph=_room(client)
        _photos(client,scan); drain(client)
        node=next(n for n in graph.nodes if n.kind=='object')
        matrix=node.transform.model_copy(deep=True);matrix.m[3]+=0.1
        moved=graph.model_copy(update={'revision':1,'nodes':[n.model_copy(update={'transform':matrix}) if n.id==node.id else n for n in graph.nodes]})
        with client.app.state.database.transaction() as c: repo.save_revision(c,moved,source='owner')
        status=client.get(f'/api/scans/{scan}/textures?revision=1').json()
        assert status['state']=='complete' and status['exact'] and status['stale_node_ids']==[]
        resized=moved.model_copy(update={'revision':2,'nodes':[n.model_copy(update={'dimensions':n.dimensions.model_copy(update={'x':n.dimensions.x+1})}) if n.id==node.id else n for n in moved.nodes]})
        with client.app.state.database.transaction() as c: repo.save_revision(c,resized,source='owner')
        status=client.get(f'/api/scans/{scan}/textures?revision=2').json()
        assert status['state']=='not_started' and str(node.id) in status['stale_node_ids']


def test_separate_workers_claim_only_their_job_kind(client):
    scan,graph=_room(client)
    with client.app.state.database.transaction() as c:
        repo.enqueue_job(c,graph.scan_id,'display',0)
        repo.enqueue_job(c,graph.scan_id,'texture',99)
        assert repo.claim_job(c,True)['kind']=='texture'
        assert repo.claim_job(c,True) is None
        assert repo.claim_job(c,False)['kind']=='display'


def test_asset_paths_require_published_build_and_safe_names(client):
    scan,_=_room(client)
    assert client.get(f'/api/scans/{scan}/textures/not-a-key/scene.glb').status_code==404
    assert client.get(f'/api/scans/{scan}/textures/'+ '0'*64 +'/result.json').status_code==404


def test_published_files_recover_after_interruption_without_rebaking(make_client):
    calls=[]
    def recorded(inputs):
        calls.append(inputs)
        return _bake(inputs)
    with make_client(stages=no_blender_stages(bake_textures=recorded)) as client:
        scan,graph=_room(client); _photos(client,scan); drain(client)
        before=client.get(f'/api/scans/{scan}/textures').json()['build']['glb_url']
        with client.app.state.database.transaction() as c:
            c.execute('UPDATE texture_builds SET result_json=NULL')
            c.execute("UPDATE jobs SET state='running' WHERE kind='texture'")
            repo.requeue_interrupted_jobs(c)
        drain(client)
        after=client.get(f'/api/scans/{scan}/textures').json()
        assert after['state']=='complete' and after['build']['glb_url']==before
        assert len(calls)==1


def _scan_build(scan_id, graph, key):
    from standardphysics_api.textures import build_prefix
    prefix = build_prefix(scan_id, key)
    return TextureBuild(build_id=key, glb_url=prefix + '/scene.glb', scan_glb_url=prefix + '/scan.glb',
                        coverage_mask_urls=[], bake_graph=graph,
                        coverage=TextureCoverage(textured_fraction=0.43, nodes=[], needs_another_view=[]),
                        frames_used=360, seconds=12.0)


def test_a_build_made_outside_the_queue_is_served_like_any_other(client):
    """A script that paints a scan registers it the same way the worker does."""
    from standardphysics_api.textures import build_dir, finish_build, record_build, staged_build_dir
    scan_id, graph = _room(client)
    key = hashlib.sha256(b'four rooms').hexdigest()
    staged = staged_build_dir(client.app.state.store, scan_id)
    shutil.copyfile(FIXTURE_DATA / 'shop.glb', staged / 'scan.glb')
    shutil.copyfile(FIXTURE_DATA / 'shop.glb', staged / 'scene.glb')
    result = _scan_build(scan_id, graph, key)
    finish_build(staged, build_dir(client.app.state.store, scan_id) / key, result)
    record_build(client.app.state.database, scan_id, key, graph, {'rooms': 4}, result)
    assert not staged.exists()
    assert client.get(f'/api/scans/{scan_id}/textures/{key}/scan.glb').status_code == 200
    status = client.get(f'/api/scans/{scan_id}/textures').json()
    assert status['build']['scan_glb_url'] == f'/api/scans/{scan_id}/textures/{key}/scan.glb'
    assert status['build']['coverage']['textured_fraction'] == 0.43


def test_a_staged_build_carrying_a_stray_file_is_refused(client):
    """The asset route serves whatever is in the directory, so only assets may enter it."""
    import pytest

    from standardphysics_api.textures import build_dir, finish_build, staged_build_dir
    scan_id, graph = _room(client)
    key = hashlib.sha256(b'stray').hexdigest()
    staged = staged_build_dir(client.app.state.store, scan_id)
    shutil.copyfile(FIXTURE_DATA / 'shop.glb', staged / 'scan.glb')
    (staged / 'notes.txt').write_text('not an asset')
    with pytest.raises(ValueError):
        finish_build(staged, build_dir(client.app.state.store, scan_id) / key, _scan_build(scan_id, graph, key))
    assert not (build_dir(client.app.state.store, scan_id) / key).exists()


def test_camera_metadata_rejects_nonfinite_or_nonrigid_transforms():
    import pytest
    from pydantic import ValidationError
    from standardphysics_contracts import PoseRecord
    camera={'image':'frames/frame_0000.jpg','timestamp':1,'transform':Mat4.identity().m,'intrinsics':[100,0,0,0,100,0,50,50,1]}
    for value in (float('nan'), 0.0, 2.0):
        transform=list(camera['transform']); transform[0]=value
        with pytest.raises(ValidationError): PoseRecord.model_validate({**camera,'transform':transform})


def test_a_new_scan_painter_keeps_the_boxes_it_already_baked(make_client, monkeypatch):
    from standardphysics_pipeline.textures import identity

    calls = []
    def recorded(inputs):
        calls.append(inputs)
        return _bake(inputs)
    with make_client(stages=no_blender_stages(bake_textures=recorded)) as client:
        scan, _ = _room(client); _photos(client, scan); drain(client)
        first = client.get(f'/api/scans/{scan}/textures').json()['build']
        monkeypatch.setattr(identity, 'TEXTURE_PIPELINE_VERSION', identity.BOX_BAKE_VERSION + '.next')
        assert client.post(f'/api/scans/{scan}/textures', json={'revision': 0}).status_code == 202
        drain(client)
        second = client.get(f'/api/scans/{scan}/textures').json()
        assert second['state'] == 'complete' and second['build']['build_id'] != first['build_id']
        assert second['build']['box_key'] == first['box_key']
        assert client.get(second['build']['glb_url']).status_code == 200
        assert len(calls) == 1


def test_a_running_build_says_which_step_it_is_on_and_how_far_through(make_client, monkeypatch):
    from standardphysics_pipeline.textures.stages import advanced, timed

    from standardphysics_api import textures
    monkeypatch.setattr(textures._ProgressFile, 'SECONDS_BETWEEN_COUNTS', 0.0)

    seen = []
    def reporting(inputs):
        with timed('choosing photos'):
            advanced(3, 8)
            seen.append(client.get(f'/api/scans/{scan}/textures').json())
        return _bake(inputs)
    with make_client(stages=no_blender_stages(bake_textures=reporting)) as client:
        scan, _ = _room(client); _photos(client, scan); drain(client)
        running = seen[0]
        assert running['state'] == 'running'
        assert running['progress']['step'] == 'choosing photos'
        assert (running['progress']['done'], running['progress']['total']) == (3, 8)
        finished = client.get(f'/api/scans/{scan}/textures').json()
        assert finished['state'] == 'complete' and finished['progress'] is None
        assert not list((client.app.state.store.scan_dir(scan) / 'textures').glob('*.progress.json'))
