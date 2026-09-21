import hashlib
import json

import pytest
from standardphysics_contracts import SceneGraph, graph_hash

from standardphysics_api.replays import publish


def recording(tmp_path, client):
    scan_id = client.get('/api/scans').json()['scans'][0]['id']
    graph = SceneGraph.model_validate(client.get(f'/api/scans/{scan_id}/scene').json())
    chapter = {'seconds': 3, 'task': 'Retrieve medicine', 'evaluation': 7, 'outcome': 'out_of_reach'}
    report = {
        'scan_id': scan_id, 'revision': 0, 'graph_hash': graph_hash(graph),
        'complete': True, 'evaluations': 10, 'requested_evaluations': 10,
        'unique_layouts': 1, 'connectivity_builds': 4, 'model_calls': 1,
        'task_generation': {'source': 'Astra via OpenRouter'}, 'limitations': ['Hypothetical props.'],
        'recorded_runs': [{'task': {'title': chapter['task']}, 'evaluation': 7, 'outcome': chapter['outcome']}],
        'private_model_response': 'must not be served',
    }
    report_path = tmp_path / 'campaign.json'
    report_path.write_text(json.dumps(report))
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'0123456789-video-fixture')
    manifest = {
        'report': str(report_path), 'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
        'video': str(video), 'graph_hash': graph_hash(graph), 'duration_seconds': 8,
        'chapters': [chapter], 'selection': 'Selected example.',
    }
    source = tmp_path / 'video.json'
    source.write_text(json.dumps(manifest))
    return source, f'/api/scans/{scan_id}/revisions/0/replay'


def test_published_replay_is_revision_bound_sanitized_and_supports_seeking(make_client, tmp_path):
    with make_client(seed=True) as client:
        source, url = recording(tmp_path, client)
        before = client.get('/api/scans').json()
        publish(source, client.app.state.database, client.app.state.store)
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()['evaluations'] == 10
        assert str(tmp_path) not in response.text
        assert 'must not be served' not in response.text
        partial = client.get(url + '/video.mp4', headers={'Range': 'bytes=2-5'})
        assert partial.status_code == 206
        assert partial.content == b'2345'
        assert partial.headers['content-type'] == 'video/mp4'
        assert client.head(url + '/video.mp4').status_code == 200
        assert client.get(url.replace('/0/', '/1/')).status_code == 404
        assert client.get(url.replace('/0/', '/-1/')).status_code == 400
        assert client.get(url + '/manifest.json').status_code == 400
        assert client.get('/api/scans').json() == before
        with pytest.raises(ValueError, match='already has a recording'):
            publish(source, client.app.state.database, client.app.state.store)


@pytest.mark.parametrize('change', ['report', 'graph', 'chapters', 'incomplete'])
def test_mismatched_recording_is_never_published(make_client, tmp_path, change):
    with make_client(seed=True) as client:
        source, url = recording(tmp_path, client)
        manifest = json.loads(source.read_text())
        report_path = tmp_path / 'campaign.json'
        report = json.loads(report_path.read_text())
        if change == 'graph':
            manifest['graph_hash'] = 'f' * 64
        elif change == 'chapters':
            manifest['chapters'][0]['evaluation'] = 8
        elif change == 'incomplete':
            report['complete'] = False
            report_path.write_text(json.dumps(report))
            manifest['report_sha256'] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        else:
            report_path.write_text('{}')
        source.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            publish(source, client.app.state.database, client.app.state.store)
        assert client.get(url).status_code == 404


def test_changed_or_deleted_room_cannot_serve_old_evidence(make_client, tmp_path):
    with make_client(seed=True) as client:
        source, url = recording(tmp_path, client)
        publish(source, client.app.state.database, client.app.state.store)
        scan_id = url.split('/')[3]
        with client.app.state.database.transaction() as connection:
            row = connection.execute('SELECT graph_json FROM revisions WHERE scan_id=?', (scan_id,)).fetchone()
            graph = json.loads(row['graph_json'])
            graph['nodes'][0]['dimensions']['x'] += 0.2
            connection.execute('UPDATE revisions SET graph_json=? WHERE scan_id=?', (json.dumps(graph), scan_id))
        assert client.get(url).status_code == 409
        assert client.get(url + '/video.mp4').status_code == 409
        assert client.delete(f'/api/scans/{scan_id}').status_code == 204
        assert client.get(url).status_code == 404
