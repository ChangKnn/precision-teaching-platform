"""Small task-scoped lexical retrieval; no embedding service or external upload on ingestion."""
from __future__ import annotations

import math
import re

CHUNK_CHARS = 700
OVERLAP = 100
MAX_PASSAGES = 4


def _tokens(text: str) -> set[str]:
    words=set(re.findall(r'[a-z0-9_]{2,}',text.casefold()))
    for run in re.findall(r'[\u3400-\u9fff]+',text):
        words.update(run[index:index+2] for index in range(max(1,len(run)-1)))
    return words


def retrieve_references(documents: list[dict], question: str, context: str) -> list[dict]:
    question_tokens,context_tokens=_tokens(question),_tokens(context)
    passages=[]
    for document_index,document in enumerate(documents):
        text=document['content']
        for index,start in enumerate(range(0,len(text),CHUNK_CHARS-OVERLAP)):
            content=text[start:start+CHUNK_CHARS]
            tokens=_tokens(content)
            # Student wording drives selection; task/role supply context at startup.
            score=(4*len(tokens & question_tokens)+len(tokens & context_tokens))/math.sqrt(max(1,len(tokens)))
            passages.append((score,document_index,index,{'source_id':document['source_id']+f'#part-{index+1}','content':content}))
            if start+CHUNK_CHARS>=len(text):break
    ranked=sorted(passages,key=lambda item:(-item[0],item[1],item[2]))
    relevant=[item for item in ranked if item[0]>0]
    # No hit: offer only each file's introductory passage, never fabricate matches.
    selected=(relevant or [item for item in passages if item[2]==0])[:MAX_PASSAGES]
    return [item[3] for item in sorted(selected,key=lambda item:(item[1],item[2]))]
