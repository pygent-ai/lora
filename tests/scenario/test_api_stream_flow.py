from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from httpx import ASGITransport, AsyncClient

from lora_api.app import create_app


@pytest.mark.asyncio
async def test_api_stream_persists_usage_reasoning_and_timing_across_restart(tmp_path, monkeypatch):
    """Exercise the HTTP provider, native runtime, SSE and reopened session together."""
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            chunks = [
                {'choices': [{'delta': {'reasoning_content': 'Checked the local configuration.'}}]},
                {'choices': [{'delta': {'content': 'Ready.'}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                {'choices': [], 'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}},
            ]
            body = ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            encoded = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    user_root = home / '.lora'
    user_root.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('LORA_LOCAL_STREAM_TEST_KEY', 'local-test-only')
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    (user_root / 'config.yaml').write_text('\n'.join([
        'agent:', '  default_alias: test', 'agents:', '  - alias: test',
        '    model_request:', '      routes:', '        - id: primary',
        '          provider: openai', '          model_name: local-test',
        f'          base_url: http://127.0.0.1:{provider.server_port}/v1',
        '          api_key_env: LORA_LOCAL_STREAM_TEST_KEY',
        'eternal_conversation:', '  enabled: false', '',
    ]), encoding='utf-8')
    try:
        app = create_app(workspace_root=str(workspace))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url='http://lora') as client:
                assert (await client.get('/health')).json()['status'] == 'ok'
                created = await client.post('/sessions', json={})
                assert created.status_code == 200
                session = created.json()
                assert Path(session['session_dir']).is_relative_to(user_root / 'projects')
                response = await client.post('/chat/stream', json={'session_id': session['session_id'], 'message': 'Check readiness.'})
                assert response.status_code == 200
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                terminal = next(event for event in events if event['kind'] == 'execution.completed')
                timing = terminal['data']['run_timing']
                assert timing['status'] == 'passed' and timing['finished_at']
                assert any(event['kind'] == 'model.text.delta' for event in events)
        reopened = create_app(workspace_root=str(workspace))
        async with reopened.router.lifespan_context(reopened):
            async with AsyncClient(transport=ASGITransport(reopened), base_url='http://lora') as client:
                detail = (await client.get(f"/sessions/{session['session_id']}")).json()
                answer = detail['history'][-1]
                assert answer['content'] == 'Ready.'
                assert answer['metadata']['reasoning_content'] == 'Checked the local configuration.'
                assert answer['usage']['total_tokens'] == 25
                assert answer['run_timing'] == timing
        assert requests and all(request['stream_options'] == {'include_usage': True} for request in requests)
        assert not (workspace / '.lora').exists()
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
