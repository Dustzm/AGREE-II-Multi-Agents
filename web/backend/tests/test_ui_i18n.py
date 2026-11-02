"""用户消息契约、语言隔离和旧任务数据的回归测试。"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from web.backend.tests.test_api import client  # noqa: F401 - isolated authenticated app fixture
from web.backend.tests.test_batch_jobs import isolated_batch_db  # noqa: F401
from web.backend.app.i18n import EN_MESSAGES, ZH_MESSAGES, get_language, request_language, t
from web.backend.app.i18n.handlers import install_i18n
from core.pipeline import Phase
from core.pipeline.events import PipelineEvent
from web.backend.app.i18n.messages import sanitize_diagnostic, ui_message
from web.backend.app.api.routes import config, reports
from web.backend.app.api.routes.chat import ConnectionManager, _ui_failures
from web.backend.app.services import batch_jobs, skills_service
from web.backend.app.services import batch_worker_runner
from web.backend.app.services.agent_runner import AppraisalRunner
from core.skills import loader


@pytest.mark.parametrize('header,expected', [
    (None, 'en'), ('zh-CN', 'zh'), ('en-US', 'en'),
    ('fr, zh-TW;q=0.9, en;q=0.2', 'zh'), ('zh;q=0, en;q=0.5', 'en'),
    ('zh;q=0.4,en-US;q=0.8', 'en'), ('fr,de', 'en'),
])
def test_language_weights(header, expected):
    assert get_language(header) == expected


@pytest.mark.parametrize('lang', ['en-US', 'zh-CN'])
def test_api_error_contracts(client, lang):
    client.headers['Accept-Language'] = lang
    response = client.get('/ama/api/files/list')
    assert response.status_code == 401
    assert response.json()['i18n']['key'] == 'error.unauthorized'
    assert response.json()['detail'] == t('error.unauthorized', get_language(lang))

    response = client.post('/ama/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
    assert response.status_code == 401
    assert response.json()['i18n']['key'] == 'error.invalid_credentials'
    assert 'wrong' not in response.text

    assert client.post('/ama/api/auth/login', json={'username': 'reviewer', 'password': 'reviewer123'}).status_code == 200
    response = client.put('/ama/api/skills/item_1', json={'enabled': True})
    assert response.status_code == 403
    assert response.json()['i18n']['key'] == 'error.admin_required'

    response = client.post('/ama/api/upload', files={'file': ('bad.txt', b'data', 'text/plain')})
    assert response.status_code == 400
    assert response.json()['i18n']['key'] == 'error.pdf_only'

    response = client.get('/ama/api/reports/nonexistent-i18n-test')
    assert response.status_code == 404
    assert response.json()['i18n']['key'] == 'error.report_not_found'

    assert client.post('/ama/api/auth/login', json={'username': 'admin', 'password': 'secret123'}).status_code == 200
    response = client.post('/ama/api/users', json={'username': 'new-user', 'password': 'x'})
    assert response.status_code == 422
    detail = response.json()['detail']
    assert isinstance(detail, list)
    assert all('i18n' in item and 'input' not in item and 'ctx' not in item for item in detail)
    assert any(item['loc'][-1] == 'password' for item in detail)
    response = client.put('/ama/api/config/llm', json={})
    assert response.status_code == 400
    assert response.json()['i18n']['key'] == 'error.config_empty'
    response = client.get('/ama/api/manual')
    assert response.status_code == 200
    assert response.json()['lang'] == lang
    response = client.get('/ama/api/manual', params={'lang': 'en-US'})
    assert response.json()['lang'] == 'en-US'


@pytest.mark.parametrize('lang', ['en-US', 'zh-CN'])
def test_connection_timeout_and_exception(client, lang, monkeypatch):
    client.headers['Accept-Language'] = lang
    client.post('/ama/api/auth/login', json={'username': 'admin', 'password': 'secret123'})
    effective = SimpleNamespace(provider='vllm', base_url='http://example.test/v1', api_key='fake-test-key', model_name='fake', timeout=10, is_vision_model=False)
    monkeypatch.setattr(config, '_build_effective_llm_config', lambda draft: effective)

    class OfflineClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ReadTimeout('API_KEY="fake-test-key"')

    monkeypatch.setattr(config.httpx, 'AsyncClient', lambda **kwargs: OfflineClient())
    response = client.post('/ama/api/config/llm/test', json={})
    assert response.status_code == 200
    assert response.json()['success'] is False
    assert response.json()['i18n']['key'] == 'config.connection.timeout'
    assert 'ReadTimeout' in response.json()['diagnostic']
    assert 'fake-test-key' not in response.text

    async def fail_model(**kwargs):
        raise RuntimeError('API_KEY="fake-test-key" password="two words"')

    import litellm
    monkeypatch.setattr(litellm, 'acompletion', fail_model)
    response = client.post('/ama/api/config/llm/health', json={})
    assert response.json()['success'] is False
    assert response.json()['i18n']['key'] == 'config.health.error'
    assert 'fake-test-key' not in response.text and 'two words' not in response.text
    assert '[redacted]' in response.json()['diagnostic']


def test_request_language_isolated_and_unexpected_exception_translated():
    app = FastAPI()
    install_i18n(app)

    @app.get('/message')
    async def message():
        await asyncio.sleep(0)
        return ui_message('status.password_changed')

    @app.get('/failure')
    async def failure():
        raise RuntimeError('token="private"')

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as browser:
            english, chinese = await asyncio.gather(browser.get('/message', headers={'Accept-Language': 'en'}), browser.get('/message', headers={'Accept-Language': 'zh'}))
            assert english.json()['message'] == t('status.password_changed', 'en')
            assert chinese.json()['message'] == t('status.password_changed', 'zh')
            failed = await browser.get('/failure', headers={'Accept-Language': 'zh'})
            assert failed.status_code == 500
            assert failed.json()['detail'] == t('error.unexpected', 'zh')
            assert 'private' not in failed.text

    asyncio.run(requests())


@pytest.mark.parametrize('language', ['en-US', 'zh-CN'])
def test_websocket_language_context(language):
    app = FastAPI()
    install_i18n(app)

    @app.websocket('/messages')
    async def messages(socket: WebSocket):
        await socket.accept()
        await socket.send_json(ui_message('status.password_changed'))

    with TestClient(app) as browser:
        with browser.websocket_connect(f'/messages?lang={language}') as socket:
            assert socket.receive_json()['message'] == t('status.password_changed', get_language(language))
    assert request_language.get() == 'en'


def test_skill_mutations_return_requested_language(client, monkeypatch, tmp_path):
    skills = tmp_path / 'skills'
    skills.mkdir()
    data = {'enabled': True, 'domain': 1, 'domain_name': 'Scope', 'domain_name_zh': '范围', 'title': 'Objectives', 'title_zh': '目标', 'description': 'English description', 'description_zh': '中文描述', 'locating_info': 'Locate', 'locating_info_zh': '定位', 'criteria': [], 'scoring_principle': []}
    import yaml
    (skills / 'item_1.yaml').write_text(yaml.safe_dump(data, allow_unicode=True))
    monkeypatch.setattr(loader, 'SKILLS_DIR', skills)
    monkeypatch.setattr(skills_service, 'SKILLS_DIR', skills)
    client.post('/ama/api/auth/login', json={'username': 'admin', 'password': 'secret123'})
    for lang, title in [('zh-CN', '目标'), ('en-US', 'Objectives')]:
        client.headers['Accept-Language'] = lang
        assert client.get('/ama/api/skills').json()[0]['title'] == title
        assert client.put('/ama/api/skills/item_1', json={'enabled': False}).json()['title'] == title
        assert client.put('/ama/api/skills/batch/status', json={'enabled': True}).json()[0]['title'] == title
        details = {name: data[name] for name in ['title_zh', 'description_zh', 'locating_info_zh', 'title', 'description', 'locating_info', 'criteria', 'scoring_principle']}
        assert client.put('/ama/api/skills/item_1/details', json=details).json()['title'] == title


def test_report_display_language_does_not_modify_results(client, tmp_path, monkeypatch):
    task = tmp_path / 'report-test'
    task.mkdir()
    document = {'task_id': 'report-test', 'guideline_title': 'User content', 'domain_scores': {'1': {'name': 'Scope and Purpose', 'score': 70, 'item_count': 1, 'evaluated_count': 1}}, 'overall_assessment': {'strengths': ['English model output'], 'recommendation': 'Recommend'}}
    report_file = task / 'appraisal_report.json'
    report_file.write_text(json.dumps(document))
    before = report_file.read_bytes()
    monkeypatch.setattr(reports, 'get_output_dir', lambda: tmp_path)
    monkeypatch.setattr(reports, 'load_evaluations', lambda task: [{'item_id': 'item_1', 'domain': 1, 'title': 'English title', 'chain_of_thought': {'score': 6, 'step3_reasoning': 'English reasoning'}, 'metadata': {}}])
    client.post('/ama/api/auth/login', json={'username': 'reviewer', 'password': 'reviewer123'})
    for lang in ['en', 'zh']:
        response = client.get('/ama/api/reports/report-test', headers={'Accept-Language': lang})
        assert response.status_code == 200
        data = response.json()
        assert data['domain_scores']['1']['score'] == 70
        assert data['overall_assessment']['strengths'] == ['English model output']
        assert data['item_evaluations'][0]['step3_reasoning'] == 'English reasoning'
        assert data['item_evaluations'][0]['title'] == loader.load_skill_config('item_1', False, lang)['title']
        condition = data['item_evaluations'][0]['scoring_principle'][0]['condition']
        assert bool(re.search(r'[\u4e00-\u9fff]', condition)) == (lang == 'zh')
    assert report_file.read_bytes() == before
    report_file.write_text('{')
    for lang in ['en', 'zh']:
        response = client.get('/ama/api/reports/report-test', headers={'Accept-Language': lang})
        assert response.status_code == 500
        assert response.json()['i18n']['key'] == 'error.report_parse_failed'
        assert 'JSONDecodeError' in response.json()['diagnostic']


def test_old_sqlite_upgrade_is_idempotent_and_keeps_history():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(batch_jobs._CREATE_BATCH_JOBS_TABLE.replace('    ui_messages_json    TEXT,\n', ''))
    conn.executescript(batch_jobs._CREATE_BATCH_JOB_ITEMS_TABLE.replace('    ui_messages_json    TEXT,\n', ''))
    for table in ['batch_jobs', 'batch_job_items']:
        assert 'ui_messages_json' not in [row['name'] for row in conn.execute(f'PRAGMA table_info({table})')]
    for _ in range(2):
        batch_jobs.init_batch_job_tables(conn)
    for table in ['batch_jobs', 'batch_job_items']:
        columns = [row['name'] for row in conn.execute(f'PRAGMA table_info({table})')]
        assert columns.count('ui_messages_json') == 1
    conn.close()


def test_batch_descriptors_persist_and_history_is_adapted(isolated_batch_db):
    pdf = isolated_batch_db / 'example.pdf'
    pdf.write_bytes(b'%PDF')
    created = batch_jobs.create_batch_job([str(pdf)], 1, 1)
    job_id = created['job_id']
    item = batch_jobs.list_batch_job_items(job_id)[0]
    step = {'key': 'status.parse_done_mineru', 'params': {'pages': 2}}
    batch_jobs.update_batch_item(job_id, item['item_id'], current_step='i18n:batch.parsingPdf', ui_messages={'step': step})
    assert batch_jobs.get_batch_job_status_payload(job_id)['tasks'][0]['step_i18n'] == step
    # Same legacy step must still update metadata (e.g. parsing finishes).
    done = {'key': 'status.parse_done_mineru', 'params': {'pages': 3}}
    batch_jobs.update_batch_item(job_id, item['item_id'], current_step='i18n:batch.parsingPdf', ui_messages={'step': done}, throttle_step_window_seconds=2)
    assert batch_jobs.get_batch_job_status_payload(job_id)['tasks'][0]['step_i18n'] == done
    batch_jobs.update_batch_item(job_id, item['item_id'], status='error', current_step='i18n:batch.failed:token="private"', last_error='Reporter timed out (60s) token="private"')
    payload = batch_jobs.get_batch_job_status_payload(job_id)['tasks'][0]
    assert payload['step_i18n']['key'] == 'batch.failedGeneric'
    assert payload['error_i18n'] == {'key': 'error.reporter_timeout', 'params': {'seconds': 60}}
    assert 'private' not in payload['diagnostic']


def test_structured_runner_and_offline_replay_preserve_state():
    messages, old = [], []
    runner = AppraisalRunner(lang='en', on_message=messages.append, on_status=lambda *args: old.append(args))
    runner.emit(PipelineEvent('status', Phase.PARSING, 'status.parse_done_mineru', 'done', {'pages': 2}))
    assert messages[0]['phase'] == 'parsing' and messages[0]['state'] == 'done'
    assert old[0] == ('status', messages[0]['content'])
    failures = _ui_failures({'item_1': {'type': 'Timeout', 'stage': 'scoring', 'message': 'token="private"'}})
    assert failures['item_1']['i18n']['key'] == 'error.appraisal_failed'
    assert 'private' not in json.dumps(failures)
    assert ui_message('status.partial', completed=22, total=23)['i18n']['params'] == {'completed': 22, 'total': 23}
    manager = ConnectionManager()

    class Socket:
        def __init__(self):
            self.received = []

        async def accept(self):
            pass

        async def send_json(self, message):
            self.received.append(message)

    async def replay():
        await manager.send_message('offline', messages[0])
        socket = Socket()
        await manager.connect('offline', socket)
        assert socket.received == messages

    asyncio.run(replay())


@pytest.mark.parametrize('timeout', [False, True])
def test_real_runner_parsing_branch_emits_structured_state(client, monkeypatch, tmp_path, timeout):
    """Run the real parsing branch with a fake parser; stop before model execution."""
    from core import agents, parsers
    from core.llm.context import RequestBudget
    from core.parsers import mineru_cache
    from core.config import get_settings

    task = tmp_path / 'parsing-task'
    prep = task / '.ama/prep'
    prep.mkdir(parents=True)
    pdf = tmp_path / 'parsing.pdf'
    pdf.write_bytes(b'%PDF fixture')
    source = task / 'source.md'
    monkeypatch.setattr(RequestBudget, 'require_configured', lambda self: None)
    monkeypatch.setattr(agents, 'init_task_directory', lambda *args, **kwargs: {'status': 'success', 'task_id': 'parsing-task', 'paths': {'task_dir': str(task), 'source': str(source), 'prep_dir': str(prep)}})
    monkeypatch.setattr(mineru_cache, 'restore_document_cache', lambda *args, **kwargs: None)
    monkeypatch.setattr(get_settings().mineru, 'server_url', 'http://fixture.test')

    class Parser:
        server_url = 'http://fixture.test'

        async def health_check(self):
            if timeout:
                raise asyncio.TimeoutError('token="private"')
            return True

        async def parse_pdf_to_file(self, **kwargs):
            source.write_text('Parsed fixture')
            return {'status': 'success', 'pages': 2}

    monkeypatch.setattr(parsers, 'MinerUParser', Parser)
    messages = []

    def on_message(message):
        messages.append(message)
        if message['i18n']['key'] == 'status.parse_done_mineru':
            runner.cancel()

    runner = AppraisalRunner(lang='en', on_message=on_message, refresh_model_config_on_start=False)
    asyncio.run(runner.run('parsing-task', str(pdf)))
    if timeout:
        failure = messages[-1]
        assert failure['i18n'] == {'key': 'error.mineru_timeout', 'params': {'seconds': runner.pipeline.mineru_health_timeout}}
        assert 'TimeoutError' in failure['diagnostic'] and 'private' not in failure['diagnostic']
    else:
        assert source.exists()
        assert messages[-1]['state'] == 'done' and messages[-1]['phase'] == 'parsing'
        assert not any(message['type'] == 'error' for message in messages)


@pytest.mark.parametrize('failure_mode', ['event', 'exception'])
def test_worker_uses_structured_messages_and_persists_errors(isolated_batch_db, monkeypatch, failure_mode):
    from core.utils.cancellation import CancellationToken

    pdf = isolated_batch_db / 'worker.pdf'
    pdf.write_bytes(b'%PDF')
    language_token = request_language.set('zh')
    try:
        created = batch_jobs.create_batch_job([str(pdf)], 1, 1)
    finally:
        request_language.reset(language_token)
    assert batch_jobs.get_batch_job(created['job_id'])['round_runtime']['ui_language'] == 'zh'
    claimed = batch_jobs.claim_next_batch_job('i18n-worker', lease_seconds=20)
    item = batch_jobs.list_batch_job_items(created['job_id'])[0]

    class Runner:
        def __init__(self, **callbacks):
            assert callbacks['lang'] == 'zh'
            self.on_message = callbacks['on_message']
            self.callbacks = callbacks

        async def run(self, task_id, pdf_path, **kwargs):
            self.on_message({'type': 'status', 'phase': 'parsing', 'state': 'done', **ui_message('status.parse_done_mineru', 'en', field='content', pages=2)})
            current = batch_jobs.get_batch_job_status_payload(created['job_id'])['tasks'][0]
            assert current['step_i18n']['key'] == 'status.parse_done_mineru'
            if failure_mode == 'exception':
                try:
                    raise ValueError('token="private"')
                except ValueError as cause:
                    raise RuntimeError('Worker fixture failed') from cause
            self.on_message({'type': 'error', **ui_message('error.reporter_timeout', 'en', field='content', seconds=60, diagnostic='token="private"')})

        def get_token_usage(self):
            return {}

        def get_status_reason(self):
            return 'reporter_timeout'

    monkeypatch.setattr(batch_worker_runner, 'AppraisalRunner', Runner)
    worker = batch_worker_runner.BatchJobRunner(created['job_id'], 'i18n-worker', claimed['run_token'], CancellationToken(), None)
    worker.MAX_RETRY = 0
    asyncio.run(worker._run_single_item(item, 1, 'fixture-batch'))
    current = batch_jobs.get_batch_job_status_payload(created['job_id'])['tasks'][0]
    assert current['status'] == 'error'
    if failure_mode == 'event':
        assert current['error_i18n'] == {'key': 'error.reporter_timeout', 'params': {'seconds': 60}}
    else:
        assert current['error_i18n'] == {'key': 'error.appraisal_failed', 'params': {}}
        assert 'ValueError' in current['diagnostic'] and 'RuntimeError' in current['diagnostic']
    assert 'private' not in current['diagnostic']
    assert current['result_task_id'] is None


def test_legacy_events_are_adapted_without_rewriting_storage(isolated_batch_db):
    from web.backend.app.database import get_db

    pdf = isolated_batch_db / 'historical.pdf'
    pdf.write_bytes(b'%PDF')
    created = batch_jobs.create_batch_job([str(pdf)], 1, 1)
    event = {'type': 'task_update', 'task': {'id': 'old', 'current_step': 'i18n:batch.failed:old error', 'error': 'Reporter timed out (60s) token="private"'}}
    event_id = batch_jobs.append_batch_job_event(created['job_id'], 'task_update', event)
    get_db().commit()
    stored = get_db().execute('SELECT payload_json FROM batch_job_events WHERE id = ?', (event_id,)).fetchone()[0]
    output = batch_jobs.list_batch_job_events(created['job_id'], event_id - 1)[0]['payload']['task']
    assert output['step_i18n']['key'] == 'batch.failedGeneric'
    assert output['error_i18n']['key'] == 'error.reporter_timeout'
    assert 'private' not in output['diagnostic']
    assert get_db().execute('SELECT payload_json FROM batch_job_events WHERE id = ?', (event_id,)).fetchone()[0] == stored


def test_dictionaries_match_frontend_resources():
    def flatten(data, prefix=''):
        result = {}
        for key, value in data.items():
            name = f'{prefix}.{key}' if prefix else key
            result.update(flatten(value, name) if isinstance(value, dict) else {name: value})
        return result

    assert EN_MESSAGES.keys() == ZH_MESSAGES.keys()
    locale_dir = Path(__file__).parents[2] / 'frontend/src/locales'
    for code, messages in [('en-US', EN_MESSAGES), ('zh-CN', ZH_MESSAGES)]:
        resources = flatten(json.loads((locale_dir / f'{code}.json').read_text())['server'])
        assert resources.keys() == messages.keys()
        for key, message in messages.items():
            assert resources[key] == re.sub(r'\{(\w+)\}', r'{{\1}}', message)
    assert sanitize_diagnostic('password="two words" Bearer token sk-abcdefghijk') == 'password="[redacted]" Bearer [redacted] [redacted]'
