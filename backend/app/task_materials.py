"""Teacher-verified mathematical task materials shared by diagnosis and delivery."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import settings
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .services.http import verified_ssl_context
from .services.llm_debug import record_llm_request
from .services.skills import load_skill

router = APIRouter(prefix="/api/precision-teachings")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4


def image_rows(teaching_id: str) -> list[dict]:
    return fetch_all("SELECT * FROM diagnosis_task_images WHERE teaching_id = ? ORDER BY created_at, id", (teaching_id,))


def image_version(teaching_id: str) -> str:
    return hashlib.sha256(json.dumps([{key: row[key] for key in ('id', 'sha256')} for row in image_rows(teaching_id)], sort_keys=True).encode()).hexdigest()


def current_reading(teaching_id: str) -> dict:
    row = fetch_one('SELECT * FROM diagnosis_image_readings WHERE teaching_id = ?', (teaching_id,)) or {}
    if row.get('image_hash') != image_version(teaching_id):
        return {}
    if row.get('status') == 'processing' and (datetime.now(timezone.utc) - datetime.fromisoformat(row['updated_at'])).total_seconds() > 180:
        # A read must not open a second writer inside a caller's transaction.
        row = {**row, 'status':'failed', 'error':'识别超时或服务已重启，请重试；原图和任务已保留。'}
    return row


def canonical_task_text(teaching_id: str) -> str:
    row = fetch_one("SELECT task_text FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    notes = fetch_one("SELECT geometry_notes FROM diagnosis_task_materials WHERE teaching_id = ?", (teaching_id,))
    text = row["task_text"] if row else ""
    if notes and notes["geometry_notes"].strip():
        text += "\n\n【题图条件与补充说明（教师已核对）】\n" + notes["geometry_notes"].strip()
    reading = current_reading(teaching_id)
    if image_rows(teaching_id) and reading.get('status') == 'ready':
        text += '\n\n【图片附件内容（任务材料，非学生作答）】\n' + reading['task_text']
        if reading['diagram_notes'].strip():
            text += '\n【图形、图表与材料标注】\n' + reading['diagram_notes']
    return text


def material_state(teaching_id: str) -> dict:
    task = fetch_one("SELECT task_text FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    row = fetch_one("SELECT * FROM diagnosis_task_materials WHERE teaching_id = ?", (teaching_id,)) or {}
    images = image_rows(teaching_id)
    notes = row.get("geometry_notes", "")
    version_data = {
        "task_text": task["task_text"] if task else "", "geometry_notes": notes,
        "images": [{"id": item["id"], "sha256": item["sha256"]} for item in images],
    }
    reading = current_reading(teaching_id)
    # Preserve hashes of legacy tasks until a new reading exists.
    if reading:
        version_data['reading'] = {key: reading[key] for key in ('status', 'task_text', 'diagram_notes', 'uncertainties_json', 'reviewed')}
    version = hashlib.sha256(json.dumps(version_data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    locked = bool(fetch_one("SELECT 1 FROM student_task_sessions WHERE teaching_id = ? LIMIT 1", (teaching_id,)))
    return {
        "images": [{"id": item["id"], "content_type": item["content_type"], "size_bytes": item["size_bytes"],
                    "file_url": f"/api/precision-teachings/{teaching_id}/materials/images/{item['id']}"} for item in images],
        "geometry_notes": notes, "source_hash": version, 'image_hash': image_version(teaching_id),
        # Legacy text notes are part of the task, not evidence of an attachment.
        "confirmation_required": bool(images),
        "confirmed": row.get("confirmed_hash") == version or bool(images and reading.get('status') == 'ready'),
        "locked": locked,
        "recognition_available": settings.task_vision_provider in {"openrouter", "openai"} and bool(settings.task_vision_model and settings.task_vision_api_key),
        "recognition": {
            'status': reading.get('status', 'pending' if images else 'none'),
            'task_text': reading.get('task_text', ''), 'diagram_notes': reading.get('diagram_notes', ''),
            'uncertainties': json.loads(reading.get('uncertainties_json', '[]')),
            'error': reading.get('error', ''), 'reviewed': bool(reading.get('reviewed')),
            'model': reading.get('model', ''),
        },
    }


def require_confirmed_materials(teaching_id: str) -> None:
    state = material_state(teaching_id)
    if state["confirmation_required"] and not state["confirmed"]:
        raise HTTPException(status_code=409, detail="图片附件尚未准备好，请查看识别内容并处理待核对项；未启用视觉模型时可手动补全附件内容")


def assert_material_editable(teaching_id: str) -> None:
    if fetch_one("SELECT 1 FROM student_task_sessions WHERE teaching_id = ? LIMIT 1", (teaching_id,)):
        raise HTTPException(status_code=409, detail="本任务已有学生开始作答，不能更换题图或图形条件；请新建精准教学使用新题目")


def _invalidate(teaching_id: str, connection) -> None:
    now = utc_now()
    connection.execute("UPDATE diagnosis_task_materials SET confirmed_hash = '', updated_at = ? WHERE teaching_id = ?", (now, teaching_id))
    connection.execute("UPDATE diagnosis_rubrics SET status = 'stale', confirmed_at = NULL, updated_at = ? WHERE teaching_id = ?", (now, teaching_id))
    connection.execute("UPDATE diagnosis_tasks SET status = 'draft', updated_at = ? WHERE teaching_id = ?", (now, teaching_id))
    connection.execute("UPDATE precision_teachings SET current_stage = 'diagnosis', updated_at = ? WHERE id = ?", (now, teaching_id))
    connection.execute("UPDATE feedback_summaries SET status = 'pending', confirmed_count = 0, updated_at = ? WHERE teaching_id = ?", (now, teaching_id))
    for table in ("goal_path_designs", "activity_formative_designs", "class_diagnosis_reports"):
        connection.execute(f"UPDATE {table} SET status = 'stale', updated_at = ? WHERE teaching_id = ?", (now, teaching_id))


class MaterialNotes(BaseModel):
    geometry_notes: str = Field(default="", max_length=4000)
    source_hash: str


class MaterialConfirmation(BaseModel):
    source_hash: str


def _check_version(teaching_id: str, version: str) -> None:
    if material_state(teaching_id)["source_hash"] != version:
        raise HTTPException(status_code=409, detail="题目材料已更新，请刷新后重新核对")


@router.get("/{teaching_id}/materials")
def get_materials(teaching_id: str) -> dict:
    return material_state(teaching_id)


@router.put("/{teaching_id}/materials")
def save_material_notes(teaching_id: str, payload: MaterialNotes) -> dict:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_version(teaching_id, payload.source_hash)
        old = material_state(teaching_id)["geometry_notes"]
        if payload.geometry_notes.strip() != old:
            assert_material_editable(teaching_id)
            connection.execute("""INSERT INTO diagnosis_task_materials (teaching_id, geometry_notes, updated_at)
                VALUES (?, ?, ?) ON CONFLICT(teaching_id) DO UPDATE SET geometry_notes=excluded.geometry_notes, updated_at=excluded.updated_at""",
                (teaching_id, payload.geometry_notes.strip(), utc_now()))
            _invalidate(teaching_id, connection)
    return material_state(teaching_id)


@router.post("/{teaching_id}/materials/confirm")
def confirm_materials(teaching_id: str, payload: MaterialConfirmation) -> dict:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_version(teaching_id, payload.source_hash)
        reading = current_reading(teaching_id)
        if reading and reading.get('status') != 'ready':
            raise HTTPException(status_code=409, detail='请先核对并保存附件识别内容，不能跳过未完成的图片识别')
        task = fetch_one("SELECT task_text FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
        if not task or not task["task_text"].strip():
            raise HTTPException(status_code=422, detail="请先补充可供分析的题干与任务要求")
        connection.execute("""INSERT INTO diagnosis_task_materials (teaching_id, confirmed_hash, updated_at)
            VALUES (?, ?, ?) ON CONFLICT(teaching_id) DO UPDATE SET confirmed_hash=excluded.confirmed_hash, updated_at=excluded.updated_at""",
            (teaching_id, payload.source_hash, utc_now()))
    write_audit("confirm", "diagnosis_task_materials", teaching_id, {"source_hash": payload.source_hash})
    return material_state(teaching_id)


@router.post("/{teaching_id}/materials/images", status_code=201)
async def upload_task_image(teaching_id: str, request: Request, background_tasks: BackgroundTasks = None) -> dict:
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="每张题图请控制在 5 MB 以内")
    try:
        with Image.open(io.BytesIO(content)) as picture:
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(picture.format)
            if not extension or picture.width * picture.height > 20_000_000:
                raise ValueError("不支持的图片或尺寸过大")
            picture.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise HTTPException(status_code=415, detail="请上传有效的 PNG、JPG 或 WEBP 图片，像素总数不超过 2000 万") from error
    image_id = f"taskimg-{uuid4().hex}"
    filename = image_id + extension
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[extension]
    target = settings.upload_path / filename
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        assert_material_editable(teaching_id)
        version = request.headers.get("x-material-version")
        if version:
            _check_version(teaching_id, version)
        if len(image_rows(teaching_id)) >= MAX_IMAGES:
            raise HTTPException(status_code=422, detail="每个任务最多保留 4 张题图")
        settings.upload_path.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        connection.execute("""INSERT INTO diagnosis_task_images (id, teaching_id, stored_filename, content_type, sha256, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (image_id, teaching_id, filename, mime, hashlib.sha256(content).hexdigest(), len(content), utc_now()))
        _invalidate(teaching_id, connection)
    if background_tasks is not None and request.headers.get('x-defer-recognition') != '1':
        return queue_recognition(teaching_id, background_tasks)
    return material_state(teaching_id)


def image_response(teaching_id: str, image_id: str) -> FileResponse:
    row = fetch_one("SELECT * FROM diagnosis_task_images WHERE teaching_id = ? AND id = ?", (teaching_id, image_id))
    if not row:
        raise HTTPException(status_code=404, detail="题图不存在")
    path = settings.upload_path / Path(row["stored_filename"]).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="题图文件不可用")
    return FileResponse(path, media_type=row["content_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/{teaching_id}/materials/images/{image_id}")
def teacher_image(teaching_id: str, image_id: str) -> FileResponse:
    return image_response(teaching_id, image_id)


@router.delete("/{teaching_id}/materials/images/{image_id}")
def remove_task_image(teaching_id: str, image_id: str, payload: MaterialConfirmation, background_tasks: BackgroundTasks = None) -> dict:
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        assert_material_editable(teaching_id)
        _check_version(teaching_id, payload.source_hash)
        connection.execute("DELETE FROM diagnosis_task_images WHERE teaching_id = ? AND id = ?", (teaching_id, image_id))
        _invalidate(teaching_id, connection)
    # Keep the original file recoverable in uploads; remove only the task link.
    if background_tasks is not None and image_rows(teaching_id):
        return queue_recognition(teaching_id, background_tasks)
    return material_state(teaching_id)


class RecognizedTask(BaseModel):
    model_config = {"extra": "forbid"}
    task_text: str = Field(min_length=1, max_length=8000)
    geometry_notes: str = Field(default="", max_length=4000)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)


def recognize_task_images(teaching_id: str) -> dict:
    state = material_state(teaching_id)
    if not state["recognition_available"]:
        raise HTTPException(status_code=409, detail="当前未配置视觉识别模型。可以保留题图、手动填写题干和图形条件，再核对后继续；也可配置 TASK_VISION_MODEL 等视觉模型参数")
    images = image_rows(teaching_id)
    if not images:
        raise HTTPException(status_code=422, detail="请先粘贴或上传题目截图")
    content = [{"type": "text", "text": "请按图片顺序转录题目，返回 task_text、geometry_notes、uncertainties。"}]
    for image in images:
        data = (settings.upload_path / Path(image["stored_filename"]).name).read_bytes()
        encoded = base64.b64encode(data).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{image['content_type']};base64,{encoded}"}})
    body = {
        "model": settings.task_vision_model,
        "messages": [{"role": "system", "content": (
            load_skill('task_image_reading').instructions + '\n输出 JSON Schema：\n' + (settings.skills_path / 'task-image-reading/assets/image-reading-output.schema.json').read_text(encoding='utf-8')
        )}, {"role": "user", "content": content}],
        "response_format": {"type": "json_object"}, "max_tokens": 6000, "stream": False,
    }
    url = settings.task_vision_base_url + "/chat/completions"
    record_llm_request("teacher", "task_image_recognition", settings.task_vision_provider, url, body)
    request = URLRequest(url, data=json.dumps(body, ensure_ascii=False).encode(), headers={
        "Authorization": f"Bearer {settings.task_vision_api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=120, context=verified_ssl_context()) as response:
            result = json.loads(response.read().decode())
        raw = result["choices"][0]["message"]["content"]
        if raw.strip().startswith("```"):
            raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0].strip()
        recognized = RecognizedTask.model_validate(json.loads(raw)).model_dump()
    except HTTPError as error:
        raise HTTPException(status_code=502, detail=f"图片识别服务返回 {error.code}，请检查模型是否支持图片输入；原题图和任务未修改") from error
    except (URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        raise HTTPException(status_code=502, detail="图片识别未返回完整题目，请稍后重试或手动录入；原题图和任务未修改") from error
    if image_version(teaching_id) != state['image_hash']:
        raise HTTPException(status_code=409, detail='图片附件已变化，旧识别结果不会应用到新图')
    return {"source_hash": state["source_hash"], "result": recognized}


@router.post("/{teaching_id}/materials/recognize")
def recognize_images(teaching_id: str) -> dict:
    return recognize_task_images(teaching_id)


def queue_recognition(teaching_id: str, background_tasks: BackgroundTasks, force: bool = False) -> dict:
    """Persist a versioned job before starting network work; never fabricate mock OCR."""
    assert_material_editable(teaching_id)
    if not image_rows(teaching_id):
        return material_state(teaching_id)
    state = material_state(teaching_id)
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = current_reading(teaching_id)
        if old.get('status') == 'processing' or (old.get('status') in {'ready', 'review'} and not force):
            return material_state(teaching_id)
        token = uuid4().hex
        status = 'processing' if state['recognition_available'] else 'disabled'
        connection.execute("""INSERT INTO diagnosis_image_readings
            (teaching_id,image_hash,job_token,status,model,error,updated_at) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(teaching_id) DO UPDATE SET image_hash=excluded.image_hash,job_token=excluded.job_token,
            status=excluded.status,task_text='',diagram_notes='',uncertainties_json='[]',reviewed=0,
            model=excluded.model,error=excluded.error,updated_at=excluded.updated_at""",
            (teaching_id, image_version(teaching_id), token, status, settings.task_vision_model,
             '' if status == 'processing' else '当前未启用视觉模型。可手动填写附件内容，核对后继续；不会伪造识别结果。', utc_now()))
        _invalidate(teaching_id, connection)
    if status == 'processing':
        background_tasks.add_task(process_recognition, teaching_id, token)
    return material_state(teaching_id)


def process_recognition(teaching_id: str, token: str) -> None:
    job = current_reading(teaching_id)
    if job.get('job_token') != token or job.get('status') != 'processing':
        return
    try:
        result = recognize_task_images(teaching_id)['result']
        if not result['task_text'].strip():
            raise ValueError('empty recognition')
        doubtful = result['uncertainties'] or any(marker in result['task_text'] + result['geometry_notes'] for marker in ('待核对', '看不清', '无法辨认'))
        values = ('review' if doubtful else 'ready', result['task_text'], result['geometry_notes'], json.dumps(result['uncertainties'], ensure_ascii=False), '')
    except Exception:
        # Do not store provider text, secrets, or exception details in the teacher UI.
        values = ('failed', '', '', '[]', '图片识别未完成，请重试或手动填写附件内容；原图和任务描述已保留。')
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = current_reading(teaching_id)
        if row.get('job_token') != token or row.get('status') != 'processing' or row.get('image_hash') != job['image_hash']:
            return
        if fetch_one('SELECT 1 FROM student_task_sessions WHERE teaching_id=? LIMIT 1', (teaching_id,)):
            return
        connection.execute("""UPDATE diagnosis_image_readings SET status=?,task_text=?,diagram_notes=?,
            uncertainties_json=?,error=?,updated_at=? WHERE teaching_id=? AND job_token=?""", (*values, utc_now(), teaching_id, token))
        _invalidate(teaching_id, connection)


@router.post('/{teaching_id}/materials/recognition/start')
def start_recognition(teaching_id: str, background_tasks: BackgroundTasks) -> dict:
    return queue_recognition(teaching_id, background_tasks, force=True)


class ReadingReview(BaseModel):
    source_hash: str
    task_text: str = Field(default='', max_length=16000)
    diagram_notes: str = Field(default='', max_length=4000)
    # Clicking Save is the explicit review action; no separate checkbox required.
    checked: bool = True


@router.put('/{teaching_id}/materials/recognition')
def review_recognition(teaching_id: str, payload: ReadingReview) -> dict:
    if not image_rows(teaching_id):
        raise HTTPException(status_code=409, detail='当前没有图片附件，无需识别或核对，可直接进入分析标准')
    if not payload.checked or not payload.task_text.strip():
        raise HTTPException(status_code=422, detail='附件识别内容尚为空，请先识别图片，或在“查看识别内容”中补充后保存')
    if any(marker in payload.task_text + payload.diagram_notes for marker in ('[待核对]', '【待核对】')):
        raise HTTPException(status_code=422, detail='仍有待核对标记，请先补全关键内容')
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert_material_editable(teaching_id)
        _check_version(teaching_id, payload.source_hash)
        if not image_rows(teaching_id):
            raise HTTPException(status_code=409, detail='图片已移除，请刷新后继续')
        connection.execute("""INSERT INTO diagnosis_image_readings
            (teaching_id,image_hash,status,task_text,diagram_notes,reviewed,updated_at) VALUES (?,?,'ready',?,?,1,?)
            ON CONFLICT(teaching_id) DO UPDATE SET image_hash=excluded.image_hash,status='ready',
            job_token='',task_text=excluded.task_text,diagram_notes=excluded.diagram_notes,
            uncertainties_json='[]',error='',reviewed=1,updated_at=excluded.updated_at""",
            (teaching_id, image_version(teaching_id), payload.task_text.strip(), payload.diagram_notes.strip(), utc_now()))
        _invalidate(teaching_id, connection)
    write_audit('review', 'diagnosis_image_readings', teaching_id, {'image_hash': image_version(teaching_id)})
    return material_state(teaching_id)
