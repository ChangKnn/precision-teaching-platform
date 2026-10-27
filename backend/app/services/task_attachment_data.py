"""Public task materials, separate from private AI-role references."""
from ..database import fetch_all


def attachment_rows(teaching_id: str) -> list[dict]:
    return fetch_all('SELECT * FROM diagnosis_task_attachments WHERE teaching_id=? ORDER BY created_at,id',(teaching_id,))


def append_attachment_text(text: str,attachments: list[dict]) -> str:
    for item in attachments:
        text+=f"\n\n【任务附件：{item['name']}】"
        if item['url']:text+='\n网页链接：'+item['url']
        if item['content_text']:text+='\n'+item['content_text']
        elif item['kind']=='link':text+='\n未提供网页正文；仅有链接，不能推测网页内容。'
    return text


def public_attachments(rows: list[dict],url_prefix: str) -> list[dict]:
    return [{'id':item['id'],'kind':item['kind'],'name':item['name'],'url':item['url'],
             'file_url':f"{url_prefix}/{item['id']}" if item['kind']=='file' else None} for item in rows]
