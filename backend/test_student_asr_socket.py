"""Real loopback WebSockets through FastAPI, with a fake DashScope server."""
import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import uvicorn
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from backend.app import main, database as db, student_asr as asr


class ASRSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_websocket_upgrade_auth_envelope_binary_provider_and_final_drain(self):
        frames = []
        async def provider(ws):
            self.assertEqual(ws.request.headers['Authorization'], 'Bearer synthetic-key')
            raw = await ws.recv(); self.assertIsInstance(raw, str)
            start = json.loads(raw); task_id = start['header']['task_id']
            self.assertEqual(start['payload']['parameters']['sample_rate'], 48000)
            await ws.send(json.dumps({'header': {'event': 'task-started', 'task_id': task_id}}))
            pcm = await ws.recv(); frames.append(pcm); self.assertIsInstance(pcm, bytes)
            finish = json.loads(await ws.recv()); self.assertEqual(finish['header']['task_id'], task_id)
            self.assertEqual(finish['header']['action'], 'finish-task')
            await ws.send(json.dumps({'header': {'event': 'result-generated', 'task_id': task_id},
                'payload': {'output': {'sentence': {'sentence_id': 1, 'text': '模拟末尾文字', 'sentence_end': True}}}}))
            await ws.send(json.dumps({'header': {'event': 'task-finished', 'task_id': task_id}}))

        real_connect = connect
        with tempfile.TemporaryDirectory() as folder:
            isolated = replace(main.settings, database_path=Path(folder)/'test.db', upload_path=Path(folder)/'uploads',
                               seed_demo_data=False, dashscope_api_key='synthetic-key')
            async with serve(provider, '127.0.0.1', 0) as upstream:
                port = upstream.sockets[0].getsockname()[1]
                def proxy(uri, **kwargs):
                    self.assertEqual(uri, asr.ENDPOINT)
                    kwargs.pop('ssl', None)
                    return real_connect(f'ws://127.0.0.1:{port}', **kwargs)
                patches = [patch.object(main,'settings',isolated), patch.object(db,'settings',isolated),
                           patch.object(asr,'settings',isolated), patch.object(asr,'connect',proxy),
                           patch.object(asr,'authorize',return_value={'id':'synthetic-student'})]
                for item in patches: item.start()
                server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=0,
                                                      loop='asyncio', log_level='critical', access_log=False))
                serving = asyncio.create_task(server.serve())
                try:
                    async def ready():
                        while not server.started:
                            if serving.done(): serving.result()
                            await asyncio.sleep(.01)
                    await asyncio.wait_for(ready(), 10)
                    app_port = server.servers[0].sockets[0].getsockname()[1]
                    uri = f'ws://127.0.0.1:{app_port}/api/student/asr/ws'
                    with self.assertRaises(InvalidStatus) as denied:
                        async with real_connect(uri, origin='https://evil.test', proxy=None): pass
                    self.assertEqual(denied.exception.response.status_code,403)
                    async with real_connect(uri, origin=f'http://127.0.0.1:{app_port}', proxy=None) as browser:
                        await browser.send(json.dumps({'type':'start','token':'synthetic-token','session_id':'test-session','sample_rate':48000}))
                        first = json.loads(await asyncio.wait_for(browser.recv(),2))
                        self.assertEqual(first['header']['event'],'task-started')
                        await browser.send(json.dumps({'audio':base64.b64encode(b'\x00\x80\xff\x7f').decode()}))
                        await browser.send(json.dumps({'type':'finish'}))
                        result = json.loads(await asyncio.wait_for(browser.recv(),2))
                        self.assertEqual(result['payload']['output']['sentence']['text'],'模拟末尾文字')
                        finished = json.loads(await asyncio.wait_for(browser.recv(),2))
                        self.assertEqual(finished['header']['event'],'task-finished')
                    self.assertEqual(frames,[b'\x00\x80\xff\x7f'])
                finally:
                    server.should_exit = True
                    try: await asyncio.wait_for(serving,10)
                    finally:
                        for item in reversed(patches): item.stop()
