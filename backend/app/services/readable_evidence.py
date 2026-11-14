"""Keep internal evidence identifiers out of reader-facing narrative."""
import re

_REFERENCE = re.compile(r'(?<![A-Za-z0-9_])(?:turn|urn)[-–—－]\d+(?![A-Za-z0-9_])', re.I)
_QUOTED = re.compile(r'(“[^”]*”|‘[^’]*’|"[^"\n]*")')
_RAW_KEYS = {'quote', 'evidence_quote', 'ref', 'source_ref', 'source_id', 'turn_id', 'student_id', 'student_name', 'student_evidence_refs', 'output_student_ids'}


def _tidy_fragment(part: str) -> str:
    part = re.sub(r'([。！？!?])[ \t]*[；;]+', r'\1', part)
    return re.sub(r'；(?:[ \t]*；)+', '；', part)


def tidy_punctuation(value: str) -> str:
    return ''.join(part if index % 2 else _tidy_fragment(part)
                   for index, part in enumerate(_QUOTED.split(value)))


def readable_text(value: str) -> str:
    return ''.join(part if index % 2 else _tidy_fragment(_REFERENCE.sub('对话记录', part))
                   for index, part in enumerate(_QUOTED.split(value)))


def readable_narrative(value, key=''):
    if key in _RAW_KEYS:
        return value
    if isinstance(value, dict):
        return {name: readable_narrative(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [readable_narrative(item) for item in value]
    return readable_text(value) if isinstance(value, str) else value
