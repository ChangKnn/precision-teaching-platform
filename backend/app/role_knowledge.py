"""Teacher-only role reference uploads; never expose these through student routes."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote
from uuid import uuid4
import zipfile

from docx import Document
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from .config import settings
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .services.task_versions import preserve_published_task

router = APIRouter(prefix='/api/precision-teachings')
MAX_FILES = 5
MAX_BYTES = 5 * 1024 * 1024
MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 24000
FORMATS = {'.txt':'text/plain', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.pdf':'application/pdf'}


def knowledge_rows(teaching_id: str) -> list[dict]:
    return fetch_all('SELECT * FROM diagnosis_role_knowledge WHERE teaching_id=? ORDER BY created_at,id', (teaching_id,))


def _signature(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps([(row['id'],row['sha256']) for row in rows],ensure_ascii=False).encode()).hexdigest()


def knowledge_state(teaching_id: str) -> dict:
    rows = knowledge_rows(teaching_id)
    task = fetch_one('SELECT published_version_id FROM diagnosis_tasks WHERE teaching_id=?', (teaching_id,))
    version = fetch_one('SELECT snapshot_json FROM diagnosis_task_versions WHERE id=?', (task['published_version_id'],)) if task and task['published_version_id'] else None
    published = json.loads(version['snapshot_json']).get('role_knowledge',[]) if version else []
    return {
        'documents':[{'id':row['id'],'name':row['original_filename'],'size_bytes':row['size_bytes'],
            'characters':len(row['content_text']),'text':row['content_text']} for row in rows],
        'source_hash':_signature(rows), 'pending_publish':_signature(rows)!=_signature(published),
        'max_files':MAX_FILES,'max_bytes':MAX_BYTES,'max_total_characters':MAX_TOTAL_CHARS,
    }


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in FORMATS:
        raise HTTPException(status_code=415, detail='知识库支持 PDF、Word（.docx）和 TXT；旧版 .doc 请另存为 .docx')
    try:
        if extension == '.txt':
            if b'\0' in content:
                raise ValueError('binary text')
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('gb18030')
            if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',text):
                raise ValueError('control bytes')
        elif extension == '.docx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members)>1000 or sum(item.file_size for item in members)>25*1024*1024:
                    raise HTTPException(status_code=413, detail='Word文件解压后过大，请精简角色资料后上传')
            document = Document(io.BytesIO(content))
            text = '\n'.join([p.text for p in document.paragraphs] + ['\t'.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows])
        else:
            # Published PDFs can contain recoverable producer errors (for example,
            # duplicate /MediaBox keys). Keep upload limits, but allow the parser
            # to recover these instead of rejecting otherwise readable documents.
            reader = PdfReader(io.BytesIO(content),strict=False)
            if reader.is_encrypted:
                raise HTTPException(status_code=422, detail='请先解除PDF密码保护后再上传')
            if len(reader.pages)>40:
                raise HTTPException(status_code=413, detail='知识库PDF最多40页，请只保留本次角色需要的资料')
            chunks=[]
            for page in reader.pages:
                # Reject pathological compressed streams before extraction.
                stream=page.get('/Contents')
                streams=list(stream.get_object()) if stream and isinstance(stream.get_object(),list) else [stream] if stream else []
                if any(len(item.get_object().get_data())>5*1024*1024 for item in streams):
                    raise HTTPException(status_code=413, detail='PDF页面内容过大，请精简后上传')
                chunks.append(page.extract_text() or '')
                if sum(map(len,chunks))>MAX_FILE_CHARS:
                    raise HTTPException(status_code=413,detail=f'单份知识库提取文字最多{MAX_FILE_CHARS}字，请精简后上传')
            text='\n'.join(chunks)
    except HTTPException:
        raise
    except Exception as error:
        # Parser failures must not expose file internals or filesystem paths.
        if extension == '.pdf':
            raise HTTPException(status_code=422,detail='PDF解析失败，文件可能损坏或使用暂不兼容的编码；请重新导出为PDF，或另存为DOCX、UTF-8文本后重试') from error
        raise HTTPException(status_code=422,detail='无法读取该文件，请确认格式有效；可另存为DOCX或UTF-8文本后重试') from error
    text=text.replace('\r\n','\n').strip()
    if not text:
        raise HTTPException(status_code=422,detail='未提取到文字；扫描型PDF请先转为可复制文字的PDF、Word或TXT')
    if len(text)>MAX_FILE_CHARS:
        raise HTTPException(status_code=413,detail=f'单份知识库提取文字最多{MAX_FILE_CHARS}字，请精简后上传')
    return text


def _check_version(teaching_id: str, expected: str) -> None:
    if knowledge_state(teaching_id)['source_hash']!=expected:
        raise HTTPException(status_code=409,detail='角色知识库已在另一页面更新，请刷新文件列表后重试')


@router.get('/{teaching_id}/role-knowledge')
def get_role_knowledge(teaching_id: str) -> dict:
    return knowledge_state(teaching_id)


@router.post('/{teaching_id}/role-knowledge',status_code=201)
async def upload_role_knowledge(teaching_id: str, request: Request) -> dict:
    if not fetch_one('SELECT 1 FROM diagnosis_tasks WHERE teaching_id=?',(teaching_id,)):
        raise HTTPException(status_code=404,detail='诊断任务不存在')
    name=Path(unquote(request.headers.get('x-file-name','')).replace('\\','/')).name
    if not name or len(name)>180 or any(ord(char)<32 for char in name):
        raise HTTPException(status_code=422,detail='请上传文件名有效的角色参考资料')
    if Path(name).suffix.lower() not in FORMATS:
        raise HTTPException(status_code=415,detail='知识库支持 PDF、Word（.docx）和 TXT')
    expected=request.headers.get('x-knowledge-version')
    if not expected:
        raise HTTPException(status_code=422,detail='缺少知识库版本，请刷新页面后上传')
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>MAX_BYTES:
            raise HTTPException(status_code=413,detail='每份知识库文件请控制在5MB以内')
    if not content:
        raise HTTPException(status_code=422,detail='文件为空，请选择有效资料')
    text=await run_in_threadpool(extract_text,name,bytes(content))
    digest=hashlib.sha256(content).hexdigest()
    document_id='rolekb-'+uuid4().hex
    extension=Path(name).suffix.lower()
    stored=document_id+extension
    directory=settings.upload_path/'role_knowledge'
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _check_version(teaching_id,expected)
        rows=knowledge_rows(teaching_id)
        if len(rows)>=MAX_FILES:
            raise HTTPException(status_code=422,detail=f'每个任务最多上传{MAX_FILES}份角色知识库')
        if any(row['sha256']==digest for row in rows):
            raise HTTPException(status_code=409,detail='这份资料已上传，无需重复添加')
        if sum(len(row['content_text']) for row in rows)+len(text)>MAX_TOTAL_CHARS:
            raise HTTPException(status_code=413,detail=f'本任务知识库文字总量最多{MAX_TOTAL_CHARS}字，请精简资料')
        preserve_published_task(teaching_id,connection)
        directory.mkdir(parents=True,exist_ok=True)
        target=directory/stored
        target.write_bytes(content)
        try:
            connection.execute('''INSERT INTO diagnosis_role_knowledge
                (id,teaching_id,original_filename,stored_filename,content_type,size_bytes,sha256,content_text,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',(document_id,teaching_id,name,stored,FORMATS[extension],len(content),digest,text,utc_now()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
    write_audit('upload','diagnosis_role_knowledge',teaching_id,{'document_id':document_id,'sha256':digest})
    return knowledge_state(teaching_id)


class KnowledgeVersion(BaseModel):
    source_hash: str


@router.delete('/{teaching_id}/role-knowledge/{document_id}')
def remove_role_knowledge(teaching_id: str,document_id: str,payload: KnowledgeVersion) -> dict:
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _check_version(teaching_id,payload.source_hash)
        if not connection.execute('SELECT 1 FROM diagnosis_role_knowledge WHERE teaching_id=? AND id=?',(teaching_id,document_id)).fetchone():
            raise HTTPException(status_code=404,detail='角色资料不存在或已移除')
        preserve_published_task(teaching_id,connection)
        connection.execute('DELETE FROM diagnosis_role_knowledge WHERE teaching_id=? AND id=?',(teaching_id,document_id))
    # Published snapshots retain their own text; keep source files in the private backup directory.
    write_audit('remove','diagnosis_role_knowledge',teaching_id,{'document_id':document_id})
    return knowledge_state(teaching_id)
