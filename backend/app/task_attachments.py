"""Student-visible files and web links with immutable published versions."""
import hashlib
from pathlib import Path
from urllib.parse import unquote,urlsplit
from uuid import uuid4

from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool

from .database import database,fetch_one,utc_now,write_audit
from .role_knowledge import extract_text,FORMATS,MAX_BYTES
from .services.task_attachment_data import attachment_rows
from .services.task_versions import preserve_published_task,get_version
from .task_materials import settings,material_state,_check_version,_invalidate,MaterialConfirmation

router=APIRouter(prefix='/api/precision-teachings')
MAX_ATTACHMENTS=8


def _store(teaching_id: str,expected: str,item: dict,content: bytes | None=None) -> dict:
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE');_check_version(teaching_id,expected)
        rows=attachment_rows(teaching_id)
        if len(rows)>=MAX_ATTACHMENTS:raise HTTPException(status_code=422,detail='文件与网页附件最多8份')
        if any(row['sha256']==item['sha256'] for row in rows):raise HTTPException(status_code=409,detail='该附件已添加')
        if sum(len(row['content_text']) for row in rows)+len(item['content_text'])>24000:
            raise HTTPException(status_code=413,detail='附件文字总量最多24000字，请精简材料')
        preserve_published_task(teaching_id,connection)
        target=None
        if content is not None:
            target=settings.upload_path/'task_attachments'/item['stored_filename']
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        try:
            connection.execute('''INSERT INTO diagnosis_task_attachments
                (id,teaching_id,kind,name,url,stored_filename,content_type,size_bytes,sha256,content_text,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (item['id'],teaching_id,item['kind'],item['name'],item.get('url',''),item.get('stored_filename',''),
                 item.get('content_type',''),item.get('size_bytes',0),item['sha256'],item['content_text'],utc_now()))
            _invalidate(teaching_id,connection)
        except Exception:
            if target:target.unlink(missing_ok=True)
            raise
    write_audit('add','task_attachment',teaching_id,{'attachment_id':item['id'],'kind':item['kind']})
    return material_state(teaching_id)


@router.post('/{teaching_id}/materials/files',status_code=201)
async def upload_task_file(teaching_id: str,request: Request) -> dict:
    name=Path(unquote(request.headers.get('x-file-name','')).replace('\\','/')).name
    extension=Path(name).suffix.lower()
    if not name or len(name)>180 or any(ord(c)<32 for c in name):raise HTTPException(status_code=422,detail='文件名无效')
    if extension not in FORMATS:raise HTTPException(status_code=415,detail='文件支持PDF、Word（.docx）和TXT')
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>MAX_BYTES:raise HTTPException(status_code=413,detail='每份文件最多5MB')
    if not content:raise HTTPException(status_code=422,detail='文件为空')
    try:text=await run_in_threadpool(extract_text,name,bytes(content))
    except HTTPException as error:
        raise HTTPException(status_code=error.status_code,detail=error.detail.replace('知识库','附件').replace('角色','任务')) from error
    expected=request.headers.get('x-material-version')
    if not expected:raise HTTPException(status_code=422,detail='请刷新附件列表后重试')
    key='taskfile-'+uuid4().hex
    return _store(teaching_id,expected,{'id':key,'kind':'file','name':name,'stored_filename':key+extension,
        'content_type':FORMATS[extension],'size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'content_text':text},bytes(content))


class WebAttachment(BaseModel):
    url: str=Field(min_length=1,max_length=2000)
    name: str=Field(default='',max_length=180)
    content_text: str=Field(default='',max_length=12000)
    source_hash: str


@router.post('/{teaching_id}/materials/links',status_code=201)
def add_web_attachment(teaching_id: str,payload: WebAttachment) -> dict:
    url=payload.url.strip()
    try:
        parsed=urlsplit(url)
        valid=parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password
        valid=valid and not any(c.isspace() or ord(c)<32 for c in url)
        parsed.port
    except ValueError:valid=False
    if not valid:raise HTTPException(status_code=422,detail='请输入有效的http或https网页链接')
    return _store(teaching_id,payload.source_hash,{'id':'tasklink-'+uuid4().hex,'kind':'link',
        'name':payload.name.strip() or parsed.hostname,'url':url,'sha256':hashlib.sha256(url.encode()).hexdigest(),'content_text':payload.content_text.strip()})


@router.delete('/{teaching_id}/materials/attachments/{attachment_id}')
def remove_attachment(teaching_id: str,attachment_id: str,payload: MaterialConfirmation) -> dict:
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE');_check_version(teaching_id,payload.source_hash)
        if not connection.execute('SELECT 1 FROM diagnosis_task_attachments WHERE id=? AND teaching_id=?',(attachment_id,teaching_id)).fetchone():
            raise HTTPException(status_code=404,detail='附件不存在')
        preserve_published_task(teaching_id,connection)
        connection.execute('DELETE FROM diagnosis_task_attachments WHERE id=? AND teaching_id=?',(attachment_id,teaching_id));_invalidate(teaching_id,connection)
    return material_state(teaching_id)


def attachment_response(item: dict) -> FileResponse:
    if item['kind']!='file':raise HTTPException(status_code=404,detail='该附件不是文件')
    path=settings.upload_path/'task_attachments'/Path(item['stored_filename']).name
    if not path.is_file():raise HTTPException(status_code=404,detail='附件文件不可用')
    return FileResponse(path,media_type=item['content_type'],filename=item['name'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/{teaching_id}/materials/attachments/{attachment_id}')
def teacher_attachment(teaching_id: str,attachment_id: str) -> FileResponse:
    row=fetch_one('SELECT * FROM diagnosis_task_attachments WHERE id=? AND teaching_id=?',(attachment_id,teaching_id))
    if not row:raise HTTPException(status_code=404,detail='附件不存在')
    return attachment_response(row)


@router.get('/{teaching_id}/task-versions/{version_id}/attachments/{attachment_id}')
def teacher_version_attachment(teaching_id: str,version_id: str,attachment_id: str) -> FileResponse:
    version=get_version(version_id)
    item=next((item for item in version['snapshot'].get('attachments',[]) if item['id']==attachment_id),None) if version and version['teaching_id']==teaching_id else None
    if not item:raise HTTPException(status_code=404,detail='此任务版本没有该附件')
    return attachment_response(item)
