"""Authenticated, bounded ASR relay. Audio and transcripts aren't persisted here."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass, field
import json
import logging
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .config import settings
from .database import fetch_one
from .services.http import verified_ssl_context
from .student_api import require_student, owned_session

router = APIRouter(prefix='/api/student/asr')
ENDPOINT = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
START_TIMEOUT = 15
FINISH_TIMEOUT = 12
SEND_TIMEOUT = 5
AUTH_CHECK_INTERVAL = 15
MAX_PACKET_CHARS = 48000
MAX_PCM_BYTES = 32768
_active_students: set[str] = set()
_transport_logger = logging.getLogger('precision_teaching.asr.transport')
_transport_logger.setLevel(logging.WARNING)  # Never debug-log Authorization, PCM or transcript frames.


class ASRError(Exception):
    def __init__(self, message: str, code: str = 'ASR_ERROR'):
        self.message, self.code = message, code


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: str
    token: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=120)
    sample_rate: int = Field(strict=True, ge=8000, le=192000)


def authorize(token: str, session_id: str) -> dict:
    student = require_student(token)
    session = owned_session(session_id, student['id'])
    if session['status'] == 'submitted' and not fetch_one(
        "SELECT 1 FROM student_diagnosis_reports WHERE session_id=? AND status='pushed'", (session_id,)
    ):
        raise HTTPException(status_code=409, detail='任务已提交，等待教师反馈后才能继续输入')
    return student


@router.get('/config')
def config(student: dict = Depends(require_student)) -> dict:
    return {'enabled': bool(settings.dashscope_api_key), 'max_seconds': settings.asr_max_seconds}


def origin_allowed(ws: WebSocket) -> bool:
    # ASGI HTTP middleware doesn't protect WebSockets. Never accept token-bearing
    # cross-origin connections or put student tokens in URLs/access logs.
    scheme = 'https' if ws.url.scheme in ('https', 'wss') else 'http'
    return not ws.url.query and bool(ws.headers.get('host')) and ws.headers.get('origin') == f"{scheme}://{ws.headers['host']}"


async def read_packet(ws: WebSocket) -> dict:
    event = await ws.receive()
    if event['type'] == 'websocket.disconnect':
        raise WebSocketDisconnect(event.get('code', 1000))
    text = event.get('text')
    if not isinstance(text, str) or len(text) > MAX_PACKET_CHARS:
        raise ASRError('语音消息格式无效或数据块过大', 'BAD_PACKET')
    try:
        packet = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise ASRError('语音消息不是有效JSON', 'BAD_PACKET') from error
    if not isinstance(packet, dict):
        raise ASRError('语音消息格式无效', 'BAD_PACKET')
    return packet


async def send_client(ws: WebSocket, value: dict) -> None:
    await asyncio.wait_for(ws.send_json(value), SEND_TIMEOUT)


def control(action: str, task_id: str, sample_rate: int | None = None) -> str:
    payload = {'input': {}}
    if action == 'run-task':
        payload.update(task_group='audio', task='asr', function='recognition', model=settings.asr_model,
                       parameters={'format': 'pcm', 'sample_rate': sample_rate})
    return json.dumps({'header': {'action': action, 'task_id': task_id, 'streaming': 'duplex'}, 'payload': payload})


def decode_audio(packet: dict) -> bytes:
    if set(packet) != {'audio'} or not isinstance(packet['audio'], str):
        raise ASRError('语音数据格式无效', 'BAD_AUDIO')
    try:
        audio = base64.b64decode(packet['audio'], validate=True)
    except ValueError as error:
        raise ASRError('语音数据编码无效', 'BAD_AUDIO') from error
    if not audio or len(audio) > MAX_PCM_BYTES or len(audio) % 2:
        raise ASRError('PCM音频数据长度无效', 'BAD_AUDIO')
    return audio


@dataclass
class Relay:
    ws: WebSocket
    upstream: object
    start: Start
    task_id: str
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    finishing: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float = 0
    sent_bytes: int = 0

    async def finish(self):
        if self.finishing.is_set():
            return
        if not self.ready.is_set():
            raise ASRError('语音任务尚未启动', 'NOT_STARTED')
        self.finishing.set()
        await asyncio.wait_for(self.upstream.send(control('finish-task', self.task_id)), SEND_TIMEOUT)

    async def client_audio(self):
        while True:
            packet = await read_packet(self.ws)
            if packet == {'type': 'finish'}:
                await self.finish()
            elif 'audio' in packet:
                if not self.ready.is_set():
                    raise ASRError('请等待语音服务准备完成后再发送音频', 'NOT_STARTED')
                audio = decode_audio(packet)
                if self.finishing.is_set():
                    continue  # Never send PCM after finish-task, including late network frames.
                self.sent_bytes += len(audio)
                allowance = self.start.sample_rate * 2 * min(settings.asr_max_seconds + 2,
                                                             monotonic() - self.started_at + 3)
                if self.sent_bytes > allowance:
                    raise ASRError('音频发送速度或总量超过限制，请重新开始', 'AUDIO_LIMIT')
                # bytes, not base64 or JSON: DashScope's PCM frames must be binary.
                await asyncio.wait_for(self.upstream.send(audio), SEND_TIMEOUT)
            else:
                raise ASRError('不支持的语音控制消息', 'BAD_PACKET')

    async def provider_events(self):
        while True:
            raw = await asyncio.wait_for(self.upstream.recv(), START_TIMEOUT if not self.ready.is_set()
                                         else settings.asr_max_seconds + FINISH_TIMEOUT)
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ASRError('语音服务返回了无效消息', 'UPSTREAM_PROTOCOL')
            try:
                value = json.loads(raw)
                header = value['header']
                event = header['event']
                if header.get('task_id') not in (None, self.task_id):
                    raise ValueError('task mismatch')
            except (ValueError, KeyError, TypeError) as error:
                raise ASRError('语音服务返回了无效事件', 'UPSTREAM_PROTOCOL') from error
            if event == 'task-started':
                if self.ready.is_set():
                    continue
                self.started_at = monotonic()
                self.ready.set()
                await send_client(self.ws, {'header': {'event': event, 'task_id': self.task_id}})
            elif event == 'result-generated':
                sentence = value.get('payload', {}).get('output', {}).get('sentence')
                if not isinstance(sentence, dict) or sentence.get('heartbeat') is True:
                    continue
                sid, text = sentence.get('sentence_id'), sentence.get('text')
                if type(sid) is not int or sid < 0 or not isinstance(text, str) or len(text) > 12000:
                    raise ASRError('语音服务返回了无效识别结果', 'UPSTREAM_PROTOCOL')
                await send_client(self.ws, {'header': {'event': event, 'task_id': self.task_id},
                    'payload': {'output': {'sentence': {'sentence_id': sid, 'text': text,
                                                        'sentence_end': sentence.get('sentence_end') is True}}}})
            elif event == 'task-finished':
                await send_client(self.ws, {'header': {'event': event, 'task_id': self.task_id}})
                return
            elif event == 'task-failed':
                raise ASRError(str(header.get('error_message') or '语音识别失败'), str(header.get('error_code') or 'TASK_FAILED'))

    async def deadline(self):
        await asyncio.wait_for(self.ready.wait(), START_TIMEOUT)
        try:
            await asyncio.wait_for(self.finishing.wait(), settings.asr_max_seconds)
        except asyncio.TimeoutError:
            await send_client(self.ws, {'type': 'stopping', 'reason': 'duration_limit'})
            await self.finish()
        await asyncio.sleep(FINISH_TIMEOUT)
        raise ASRError('等待最后一句识别超时，已保留收到的文字，请检查后再发送', 'FINISH_TIMEOUT')

    async def check_auth(self):
        while True:
            await asyncio.sleep(AUTH_CHECK_INTERVAL)
            await run_in_threadpool(authorize, self.start.token, self.start.session_id)

    async def run(self):
        await asyncio.wait_for(self.upstream.send(control('run-task', self.task_id, self.start.sample_rate)), SEND_TIMEOUT)
        tasks = [asyncio.create_task(coro()) for coro in
                 (self.client_audio, self.provider_events, self.deadline, self.check_auth)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket('/ws')
async def websocket_asr(ws: WebSocket):
    if not origin_allowed(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    owner = None
    try:
        start = Start.model_validate(await asyncio.wait_for(read_packet(ws), 8))
        if start.type != 'start':
            raise ASRError('请先初始化语音任务', 'BAD_START')
        student = await run_in_threadpool(authorize, start.token, start.session_id)
        if not settings.dashscope_api_key:
            raise ASRError('语音服务尚未配置，请先使用键盘输入', 'NOT_CONFIGURED')
        if student['id'] in _active_students or len(_active_students) >= settings.asr_max_connections:
            raise ASRError('语音连接正在使用或人数已满，请停止其他录音后重试', 'BUSY')
        owner = student['id']
        _active_students.add(owner)
        # Domestic Beijing endpoint, deliberately independent of the GPT/Claude proxy.
        async with connect(ENDPOINT, additional_headers={'Authorization': 'Bearer ' + settings.dashscope_api_key},
                           ssl=verified_ssl_context(), proxy=None, open_timeout=10, close_timeout=2,
                           max_size=65536, max_queue=8, write_limit=32768, ping_interval=20, ping_timeout=20,
                           compression=None, logger=_transport_logger) as upstream:
            await Relay(ws, upstream, start, str(uuid4())).run()
    except WebSocketDisconnect:
        pass
    except HTTPException as error:
        with suppress(Exception):
            await send_client(ws, {'type': 'error', 'code': 'AUTH_OR_SESSION', 'message': str(error.detail)})
    except ValidationError:
        with suppress(Exception):
            await send_client(ws, {'type': 'error', 'code': 'BAD_START', 'message': '语音初始化参数无效，请刷新后重试'})
    except Exception as error:
        if isinstance(error, ASRError):
            code, message = error.code, error.message
        elif isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            code, message = 'TIMEOUT', '语音连接或识别超时，请检查网络后重试'
        elif isinstance(error, InvalidStatus):
            code, message = 'UPSTREAM_CONNECT', f'语音服务连接被拒绝（HTTP {error.response.status_code}），请联系教师检查配置'
        elif isinstance(error, (ConnectionClosed, OSError)):
            code, message = 'CONNECTION_CLOSED', '语音连接中断，已保留收到的文字，请检查后重试'
        else:
            code, message = 'ASR_ERROR', '语音识别暂不可用，请稍后重试或使用键盘输入'
        if settings.dashscope_api_key:
            message = message.replace(settings.dashscope_api_key, '[已隐藏]')
            code = code.replace(settings.dashscope_api_key, '[已隐藏]')
        with suppress(Exception):
            await send_client(ws, {'type': 'error', 'code': code[:100], 'message': message[:500]})
    finally:
        if owner is not None:
            _active_students.discard(owner)
        with suppress(Exception):
            await ws.close(code=1000)
