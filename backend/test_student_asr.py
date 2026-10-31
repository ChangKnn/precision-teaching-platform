"""Exercise actual relay state/framing without a microphone, key or paid API."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import student_asr as asr


class Browser:
    def __init__(self, packet=None, origin='https://example.test'):
        self.url = SimpleNamespace(scheme='wss', query='')
        self.headers = {'host': 'example.test', 'origin': origin}
        self.incoming = asyncio.Queue(); self.sent = []; self.accepted = False; self.closed = None
        self.on_started = None
        if packet is not None: self.put(packet)

    def put(self, packet):
        self.incoming.put_nowait({'type': 'websocket.receive', 'text': json.dumps(packet)})

    async def receive(self): return await self.incoming.get()
    async def accept(self): self.accepted = True
    async def close(self, code=1000): self.closed = code
    async def send_json(self, value):
        self.sent.append(value)
        if value.get('header', {}).get('event') == 'task-started' and self.on_started:
            self.on_started()


class Provider:
    def __init__(self, auto_start=True, auto_finish=True):
        self.incoming = asyncio.Queue(); self.sent = []; self.closed = False
        self.auto_start, self.auto_finish = auto_start, auto_finish; self.task_id = None

    def event(self, kind, **header):
        self.incoming.put_nowait(json.dumps({'header': {'event': kind, 'task_id': self.task_id, **header}}))

    def sentence(self, text, final=False, heartbeat=False):
        self.incoming.put_nowait(json.dumps({'header': {'event': 'result-generated', 'task_id': self.task_id},
            'payload': {'output': {'sentence': {'sentence_id': 1, 'text': text, 'sentence_end': final, 'heartbeat': heartbeat}}}}))

    async def send(self, value):
        self.sent.append(value)
        if isinstance(value, str):
            packet = json.loads(value)
            if packet['header']['action'] == 'run-task':
                self.task_id = packet['header']['task_id']
                if self.auto_start: self.event('task-started')
            elif self.auto_finish:
                self.sentence('最后一句完整文字', True)
                self.event('task-finished')
        else:
            self.sentence('心跳忽略', heartbeat=True)
            self.sentence('中间文字')

    async def recv(self): return await self.incoming.get()


class StudentASRTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start = {'type': 'start', 'token': 'student-token', 'session_id': 'ses-test', 'sample_rate': 44100}
        self.settings = replace(asr.settings, dashscope_api_key='private-server-key', asr_max_seconds=10)
        asr._active_students.clear()
        self.addCleanup(asr._active_students.clear)
        for target in (patch.object(asr, 'settings', self.settings),
                       patch.object(asr, 'authorize', return_value={'id': 'student-test'})):
            target.start(); self.addCleanup(target.stop)

    async def run_relay(self, browser, provider):
        @asynccontextmanager
        async def connection(*args, **kwargs):
            self.connection_args = args, kwargs
            try: yield provider
            finally: provider.closed = True
        with patch.object(asr, 'connect', connection):
            await asyncio.wait_for(asr.websocket_asr(browser), 2)

    async def test_correct_run_binary_pcm_finish_and_tail_order(self):
        browser, provider = Browser(self.start), Provider()
        pcm = b'\x00\x80\xff\x7f'
        browser.on_started = lambda: (browser.put({'audio': base64.b64encode(pcm).decode()}), browser.put({'type': 'finish'}))
        await self.run_relay(browser, provider)
        self.assertEqual(self.connection_args[0][0], asr.ENDPOINT)
        self.assertEqual(self.connection_args[1]['additional_headers'], {'Authorization': 'Bearer private-server-key'})
        self.assertIsNone(self.connection_args[1]['proxy'])
        start, audio, finish = provider.sent
        self.assertIsInstance(start, str); self.assertIsInstance(finish, str)
        self.assertIsInstance(audio, bytes); self.assertEqual(audio, pcm)
        run_task, finish_task = json.loads(start), json.loads(finish)
        self.assertEqual(run_task['payload']['parameters'], {'format': 'pcm', 'sample_rate': 44100})
        self.assertEqual(run_task['payload']['model'], 'qwen-audio-3.1-asr-flash-streaming')
        self.assertEqual(run_task['header']['task_id'], finish_task['header']['task_id'])
        self.assertEqual(finish_task['header']['action'], 'finish-task')
        self.assertEqual([p['header']['event'] for p in browser.sent], ['task-started','result-generated','result-generated','task-finished'])
        self.assertEqual(browser.sent[-2]['payload']['output']['sentence']['text'], '最后一句完整文字')
        self.assertTrue(provider.closed); self.assertFalse(asr._active_students)
        self.assertNotIn('private-server-key', json.dumps(browser.sent))

    async def test_cross_origin_missing_origin_and_query_tokens_rejected(self):
        for origin in ('https://evil.test', ''):
            browser, provider = Browser(self.start, origin), Provider()
            await self.run_relay(browser, provider)
            self.assertFalse(browser.accepted); self.assertEqual(browser.closed, 1008)
            self.assertFalse(provider.sent)
        browser = Browser(self.start); browser.url.query = 'token=secret'
        await self.run_relay(browser, Provider()); self.assertFalse(browser.accepted)

    async def test_invalid_or_foreign_session_never_connects_to_provider(self):
        browser, provider = Browser(self.start), Provider()
        with patch.object(asr, 'authorize', side_effect=HTTPException(404, '学习会话不存在')):
            await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[0]['code'], 'AUTH_OR_SESSION'); self.assertFalse(provider.sent)

    async def test_key_missing_is_explicit_without_provider_connection(self):
        browser, provider = Browser(self.start), Provider()
        with patch.object(asr, 'settings', replace(self.settings, dashscope_api_key='')):
            await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[0]['code'], 'NOT_CONFIGURED'); self.assertFalse(provider.sent)

    async def test_client_cannot_supply_api_key_model_or_invalid_rate(self):
        for change in ({'sample_rate': 1}, {'sample_rate': True}, {'key': 'client-key'}, {'model': 'other'}):
            browser, provider = Browser({**self.start, **change}), Provider()
            await self.run_relay(browser, provider)
            self.assertEqual(browser.sent[0]['code'], 'BAD_START'); self.assertFalse(provider.sent)

    async def test_audio_before_task_started_is_rejected(self):
        browser, provider = Browser(self.start), Provider(auto_start=False)
        browser.put({'audio': 'AAA='})
        await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'NOT_STARTED')
        self.assertTrue(all(isinstance(v, str) for v in provider.sent))

    async def test_malformed_audio_and_binary_browser_frames_are_rejected(self):
        for packet in ({'audio': '*invalid*'}, {'audio': 'AA=='}, {'audio': ''}, {'audio': 'AAA=', 'extra': 1}):
            browser, provider = Browser(self.start), Provider()
            browser.on_started = lambda p=packet: browser.put(p)
            await self.run_relay(browser, provider)
            self.assertEqual(browser.sent[-1]['code'], 'BAD_AUDIO'); self.assertTrue(provider.closed)
        browser, provider = Browser(self.start), Provider()
        browser.on_started = lambda: browser.incoming.put_nowait({'type':'websocket.receive','bytes':b'\0\0'})
        await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'BAD_PACKET')

    async def test_finish_timeout_releases_both_connections(self):
        browser, provider = Browser(self.start), Provider(auto_finish=False)
        browser.on_started = lambda: browser.put({'type': 'finish'})
        with patch.object(asr, 'FINISH_TIMEOUT', .02): await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'FINISH_TIMEOUT'); self.assertTrue(provider.closed)
        self.assertFalse(asr._active_students)

    async def test_start_timeout_and_disconnect_cleanup(self):
        browser, provider = Browser(self.start), Provider(auto_start=False)
        with patch.object(asr, 'START_TIMEOUT', .02): await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'TIMEOUT'); self.assertTrue(provider.closed)
        browser, provider = Browser(self.start), Provider()
        browser.on_started = lambda: browser.incoming.put_nowait({'type':'websocket.disconnect','code':1001})
        await self.run_relay(browser, provider)
        self.assertTrue(provider.closed); self.assertFalse(asr._active_students)

    async def test_task_failed_details_sanitized_and_connection_released(self):
        browser, provider = Browser(self.start), Provider(auto_start=False)
        provider.event('task-failed', error_code='QUOTA', error_message='额度不足 private-server-key')
        await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'QUOTA')
        self.assertIn('额度不足', browser.sent[-1]['message'])
        self.assertNotIn('private-server-key', json.dumps(browser.sent)); self.assertTrue(provider.closed)

    async def test_only_one_connection_per_student(self):
        asr._active_students.add('student-test')
        browser, provider = Browser(self.start), Provider()
        await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[0]['code'], 'BUSY'); self.assertFalse(provider.sent)
        self.assertEqual(asr._active_students, {'student-test'})

    async def test_expired_auth_stops_active_recording(self):
        browser, provider = Browser(self.start), Provider()
        with patch.object(asr, 'authorize', side_effect=[{'id':'student-test'},HTTPException(401,'登录已失效')]), patch.object(asr, 'AUTH_CHECK_INTERVAL', .01):
            await self.run_relay(browser, provider)
        self.assertEqual(browser.sent[-1]['code'], 'AUTH_OR_SESSION'); self.assertTrue(provider.closed)

    async def test_duration_limit_finishes_once_and_drains_tail(self):
        browser, provider = Browser(self.start), Provider()
        with patch.object(asr, 'settings', replace(self.settings, asr_max_seconds=.02)):
            await self.run_relay(browser, provider)
        self.assertTrue(any(p.get('type') == 'stopping' for p in browser.sent))
        self.assertEqual(sum(json.loads(p)['header']['action']=='finish-task' for p in provider.sent if isinstance(p,str)),1)
        self.assertEqual(browser.sent[-1]['header']['event'],'task-finished')

    async def test_audio_rate_limit_does_not_allow_unbounded_uploads(self):
        browser, provider = Browser({**self.start,'sample_rate':8000}), Provider()
        packet = {'audio':base64.b64encode(b'\0'*32768).decode()}
        browser.on_started = lambda: (browser.put(packet),browser.put(packet))
        await self.run_relay(browser,provider)
        self.assertEqual(browser.sent[-1]['code'],'AUDIO_LIMIT'); self.assertTrue(provider.closed)


class ASRPermissionTest(unittest.TestCase):
    def test_permission_checks_use_existing_student_and_session_ownership(self):
        with patch.object(asr, 'require_student', return_value={'id':'stu'}) as login, patch.object(asr, 'owned_session', return_value={'status':'in_progress'}) as owner:
            self.assertEqual(asr.authorize('token','session'), {'id':'stu'})
        login.assert_called_once_with('token'); owner.assert_called_once_with('session','stu')
        with patch.object(asr, 'require_student', return_value={'id':'stu'}), patch.object(asr, 'owned_session', return_value={'status':'submitted'}), patch.object(asr, 'fetch_one', return_value=None):
            with self.assertRaises(HTTPException): asr.authorize('token','session')
