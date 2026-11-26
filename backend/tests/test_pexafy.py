import base64

from app.capabilities import pexafy
from app.db import get_session_factory
from app.models import PexafySettings


def test_pexafy_settings_and_tools(client, auth_headers, monkeypatch, tmp_path):
    settings = '/api/admin/mcp/pexafy/settings'
    assert client.get(settings).status_code == 401
    assert client.get(settings, headers=auth_headers).json() == {'configured': False}
    assert client.put(settings, headers=auth_headers, json={'api_key': 'test-pexafy-secret'}).json() == {'configured': True}
    with get_session_factory()() as db:
        assert db.get(PexafySettings, 1).api_key_encrypted != 'test-pexafy-secret'
    calls = []

    async def fake_api(key, method, path, **kwargs):
        assert key == 'test-pexafy-secret'
        calls.append((method, path, kwargs))
        return {'success': True, 'data': {'urls': {'large': 'https://images.pexels.com/test.jpg'}, 'width': 100, 'height': 50}}

    monkeypatch.setattr(pexafy, 'api_call', fake_api)
    assert client.get('/api/admin/mcp/pexafy/usage', headers=auth_headers).status_code == 200
    assert calls[-1][:2] == ('GET', '/usage')
    catalog = client.get('/api/admin/mcp/catalog', headers=auth_headers).json()['items']
    entry = next(item for item in catalog if item['capability_id'] == 'pexafy')
    assert len(entry['tools']) == 9
    assert len(entry['integration']['rest_endpoints']) == 9
    created = client.post('/api/admin/mcp/keys', headers=auth_headers, json={'name': 'pexafy', 'capability_ids': ['pexafy']})
    assert created.status_code == 201, created.text
    headers = {'Authorization': 'Bearer ' + created.json()['key']}
    mcp_headers = {**headers, 'Accept': 'application/json, text/event-stream'}
    response = client.post('/mcp', headers=mcp_headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == 200, response.text
    assert {tool['name'] for tool in response.json()['result']['tools']} == {tool.name for tool in pexafy.TOOLS}
    response = client.post('/mcp', headers=mcp_headers, json={
        'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'pexafy_similar_photos', 'arguments': {'photo_id': 'abc'}},
    })
    assert response.status_code == 200 and not response.json()['result'].get('isError'), response.text
    assert calls[-1][:2] == ('GET', '/photos/abc/similar')
    base = '/v1/capabilities/pexafy/'
    assert client.post(base + 'search_photos', headers=headers, json={'q': '办公桌', 'orientation': ['landscape']}).status_code == 200
    assert calls[-1] == ('GET', '/search/photos', {'params': {'q': '办公桌', 'orientation': ['landscape']}})
    assert client.post(base + 'search_by_image', headers=headers, json={'photo_id': 'abc', 'q': 'office'}).status_code == 200
    assert calls[-1][0] == 'POST'
    image = b'\xff\xd8\xfftest'
    assert client.post(base + 'search_by_image', headers=headers, json={'image_base64': base64.b64encode(image).decode()}).status_code == 200
    assert calls[-1][2]['upload']['image'][1] == image
    assert client.post(base + 'search_by_image', headers=headers, json={}).status_code == 422
    assert client.post(base + 'get_photo', headers=headers, json={'photo_id': '..'}).status_code == 422
    for op in ('colors', 'sources', 'orientations', 'licenses'):
        assert client.post(base + 'list_' + op, headers=headers, json={}).status_code == 200
        assert calls[-1][:2] == ('GET', '/facets/' + op)

    async def fake_fetch(url):
        assert url == 'https://images.pexels.com/test.jpg'
        return image, 'image/jpeg'

    monkeypatch.setattr(pexafy, 'fetch_image', fake_fetch)
    monkeypatch.setattr(pexafy.files, 'public_base', lambda: 'http://testserver')
    monkeypatch.setattr(pexafy.files, 'file_path', lambda identifier, **kwargs: tmp_path / identifier)
    result = client.post(base + 'download_photo', headers=headers, json={'photo_id': 'abc'})
    assert result.status_code == 200, result.text
    assert client.get(result.json()['download_url']).content == image
    assert client.get(result.json()['download_url'].split('?')[0]).status_code == 404
    assert client.put(settings, headers=auth_headers, json={'api_key': None}).json() == {'configured': False}
    assert client.post(base + 'search_photos', headers=headers, json={'q': 'office'}).status_code == 503
