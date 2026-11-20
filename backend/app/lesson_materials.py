"""Private, teacher-reviewed lesson sources, independent of diagnostic attachments."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import posixpath
from urllib.parse import unquote
from uuid import uuid4
import zipfile
from xml.etree import ElementTree as ET
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from .config import settings
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .role_knowledge import extract_text as extract_document_text

router = APIRouter(prefix='/api/precision-teachings')
MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 24000
FORMATS = {'.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
           '.pdf': 'application/pdf', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def extract_lesson_text(name: str, content: bytes) -> str:
    if Path(name).suffix.lower() != '.pptx':
        try:
            text = extract_document_text(name, content)
            if Path(name).suffix.lower() == '.docx':
                # Preserve the teacher's paragraph/table order, unlike a
                # paragraphs-first export that moves all tables to the end.
                from docx import Document
                from docx.table import Table
                from docx.text.paragraph import Paragraph
                document = Document(io.BytesIO(content))
                chunks = []
                for child in document.element.body.iterchildren():
                    if child.tag.endswith('}p'):
                        chunks.append(Paragraph(child, document).text)
                    elif child.tag.endswith('}tbl'):
                        chunks.extend('\t'.join(c.text for c in row.cells) for row in Table(child, document).rows)
                text = '\n'.join(chunks).strip()
            return text
        except HTTPException as error:
            raise HTTPException(error.status_code, str(error.detail).replace('角色资料', '教学资料').replace('知识库', '教学资料')) from error
        except Exception as error:
            raise HTTPException(422, '教学资料解析失败，请另存为新版DOCX或可复制文字的PDF后重试') from error
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 3000 or sum(m.file_size for m in members) > 50 * 1024 * 1024:
                raise HTTPException(413, '课件解压后过大，请精简后上传')
            rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
            targets = {r.get('Id'): r.get('Target') for r in rels if r.get('TargetMode') != 'External'}
            presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
            slide_ids = presentation.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId')
            if len(slide_ids) > 100:
                raise HTTPException(413, '课件最多100页，请只保留本课内容')
            chunks = []
            for n, slide in enumerate(slide_ids, 1):
                target = targets[slide.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
                path = posixpath.normpath(posixpath.join('ppt', target)) if not target.startswith('/') else target.lstrip('/')
                if not path.startswith('ppt/slides/'):
                    raise ValueError('invalid slide relationship')
                xml = ET.fromstring(archive.read(path))
                paragraphs = [''.join(t.text or '' for t in p.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t')) for p in xml.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}p')]
                text = '\n'.join(p.strip() for p in paragraphs if p.strip())
                chunks.append(f'第{n}页\n' + (text or '（本页未提取到文字，请补充图片或图表含义）'))
                if sum(map(len, chunks)) > MAX_CHARS:
                    raise HTTPException(413, '课件文字过多，请精简至24000字以内')
            if not chunks:
                raise ValueError('no slides')
            return '\n\n'.join(chunks)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(422, '课件解析失败，请确认是有效PPTX，或导出为可复制文字的PDF后重试') from error


def material_state(teaching_id: str) -> dict:
    rows = fetch_all('SELECT * FROM lesson_materials WHERE teaching_id=? ORDER BY created_at,id', (teaching_id,))
    review = fetch_one('SELECT * FROM lesson_material_reviews WHERE teaching_id=?', (teaching_id,))
    signature = hashlib.sha256(json.dumps([(r['id'],r['sha256']) for r in rows]).encode()).hexdigest()
    confirmed = bool(rows and review and review['source_hash'] == signature)
    text = review['content_text'] if confirmed else '\n\n'.join(r['original_filename']+'\n'+r['content_text'] for r in rows)
    revision = hashlib.sha256(json.dumps([signature, review], sort_keys=True).encode()).hexdigest()
    return {'documents': [{'id': r['id'], 'name': r['original_filename'], 'size_bytes': r['size_bytes'],
                          'download_url': f"/api/precision-teachings/{teaching_id}/lesson-materials/{r['id']}/file"} for r in rows],
            'text': text, 'confirmed': confirmed, 'source_hash': signature, 'revision': revision,
            'extraction_note': '已提取文字。图片、图表、公式及扫描页可能不完整，请在下方补充并核对；当前未做图片识别。'}


def confirmed_materials(teaching_id: str) -> dict | None:
    state = material_state(teaching_id)
    if not state['documents']:
        return None
    if not state['confirmed']:
        raise HTTPException(409, '请先核对并保存“当前教学设计方案”的提取内容')
    return {'source_hash': state['revision'], 'filenames': [d['name'] for d in state['documents']], 'text': state['text']}


def _check_version(teaching_id, revision):
    if not revision or material_state(teaching_id)['revision'] != revision:
        raise HTTPException(409, '教学资料已在其他页面更新，请刷新后重试')


def _invalidate(connection, teaching_id):
    # Keep old content available, but prevent confirming an outdated design.
    for table in ('goal_path_designs', 'activity_formative_designs'):
        connection.execute(f"UPDATE {table} SET status='stale', updated_at=? WHERE teaching_id=?", (utc_now(),teaching_id))


@router.get('/{teaching_id}/lesson-materials')
def get_materials(teaching_id: str):
    return material_state(teaching_id)


@router.post('/{teaching_id}/lesson-materials', status_code=201)
async def upload_material(teaching_id: str, request: Request):
    if not fetch_one('SELECT id FROM precision_teachings WHERE id=?', (teaching_id,)):
        raise HTTPException(404, '教学任务不存在')
    name = Path(unquote(request.headers.get('x-file-name','')).replace('\\','/')).name
    if not name or len(name)>180 or any(ord(c)<32 for c in name) or Path(name).suffix.lower() not in FORMATS:
        raise HTTPException(415, '请上传DOCX、PDF或PPTX；旧版DOC、PPT请另存为新版格式')
    data = bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_BYTES:
            raise HTTPException(413, '单份教学资料最多10MB')
        data.extend(chunk)
    if not data:
        raise HTTPException(422, '文件为空')
    text = await run_in_threadpool(extract_lesson_text, name, bytes(data))
    digest = hashlib.sha256(data).hexdigest()
    doc_id = 'lesson-'+uuid4().hex
    stored = doc_id+Path(name).suffix.lower()
    directory = settings.upload_path/'lesson_materials'
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _check_version(teaching_id, request.headers.get('x-material-version'))
        rows = fetch_all('SELECT sha256,content_text FROM lesson_materials WHERE teaching_id=?', (teaching_id,))
        if len(rows)>=5:
            raise HTTPException(422, '每个任务最多上传5份教学资料')
        if any(r['sha256']==digest for r in rows):
            raise HTTPException(409, '该文件已上传')
        if len(text)+sum(len(r['content_text']) for r in rows)>MAX_CHARS:
            raise HTTPException(413, '教学资料文字总量最多24000字，请精简后上传')
        directory.mkdir(parents=True, exist_ok=True)
        target = directory/stored
        target.write_bytes(data)
        try:
            connection.execute('INSERT INTO lesson_materials VALUES (?,?,?,?,?,?,?,?,?)',
                (doc_id,teaching_id,name,stored,FORMATS[Path(name).suffix.lower()],len(data),digest,text,utc_now()))
            _invalidate(connection,teaching_id)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    write_audit('upload','lesson_material',teaching_id,{'document_id':doc_id})
    return material_state(teaching_id)


class Review(BaseModel):
    revision: str
    text: str = Field(min_length=1, max_length=MAX_CHARS)


@router.put('/{teaching_id}/lesson-materials')
def confirm_materials(teaching_id: str, payload: Review):
    if not payload.text.strip():
        raise HTTPException(422, '请补充本课教学内容再保存')
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _check_version(teaching_id, payload.revision)
        state = material_state(teaching_id)
        if not state['documents']:
            raise HTTPException(409, '请先上传教学资料')
        if state['confirmed'] and payload.text.strip() == state['text']:
            return state
        connection.execute('INSERT INTO lesson_material_reviews VALUES (?,?,?,?) ON CONFLICT(teaching_id) DO UPDATE SET source_hash=excluded.source_hash, content_text=excluded.content_text, updated_at=excluded.updated_at',
            (teaching_id,state['source_hash'],payload.text.strip(),utc_now()))
        _invalidate(connection,teaching_id)
    return material_state(teaching_id)


class Revision(BaseModel):
    revision: str


@router.delete('/{teaching_id}/lesson-materials/{document_id}')
def remove_material(teaching_id: str, document_id: str, payload: Revision):
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _check_version(teaching_id,payload.revision)
        cursor = connection.execute('DELETE FROM lesson_materials WHERE teaching_id=? AND id=?', (teaching_id,document_id))
        if not cursor.rowcount:
            raise HTTPException(404, '资料不存在')
        _invalidate(connection,teaching_id)
    return material_state(teaching_id)


@router.get('/{teaching_id}/lesson-materials/{document_id}/file')
def download_material(teaching_id: str, document_id: str):
    row = fetch_one('SELECT * FROM lesson_materials WHERE teaching_id=? AND id=?', (teaching_id,document_id))
    if not row:
        raise HTTPException(404, '资料不存在')
    target = settings.upload_path/'lesson_materials'/row['stored_filename']
    if not target.is_file():
        raise HTTPException(404, '原文件不可用')
    return FileResponse(target, media_type=row['content_type'], filename=row['original_filename'], headers={'Cache-Control':'private, no-store'})


def attach_materials(payload: dict, teaching_id: str) -> None:
    material = confirmed_materials(teaching_id)
    if material:
        payload['current_lesson_materials'] = material


def require_same_materials(payload: dict, teaching_id: str) -> None:
    if payload.get('current_lesson_materials') != confirmed_materials(teaching_id):
        raise HTTPException(409, '生成期间教学资料已修改，请基于最新内容重新生成；原方案已保留')
