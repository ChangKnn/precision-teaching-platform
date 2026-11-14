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


def readable_student_names(value):
    """Resolve IDs only in narrative; retain identifiers and verbatim evidence."""
    names = {}
    def collect(item):
        if isinstance(item, dict):
            if item.get('student_id') and item.get('student_name'):
                names[str(item['student_id'])] = str(item['student_name'])
            for nested in item.values():
                collect(nested)
        elif isinstance(item, list):
            for nested in item:
                collect(nested)
    collect(value)
    known = '|'.join(re.escape(sid) for sid in sorted(names, key=len, reverse=True))
    pattern = re.compile(r'(?<![A-Za-z0-9_-])(?:' + (known + '|' if known else '') + r'stu-[A-Za-z0-9]+)(?![A-Za-z0-9_-])')
    def walk(item, key=''):
        if key in _RAW_KEYS or key.endswith(('_id', '_ids', '_refs')):
            return item
        if isinstance(item, dict):
            return {name: walk(nested, name) for name, nested in item.items()}
        if isinstance(item, list):
            return [walk(nested, key) for nested in item]
        if isinstance(item, str):
            return ''.join(part if index % 2 else pattern.sub(lambda match: names.get(match.group(), '该学生'), part)
                           for index, part in enumerate(_QUOTED.split(item)))
        return item
    return walk(value)
