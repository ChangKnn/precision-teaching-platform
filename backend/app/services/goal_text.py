"""Normalize trailing SOLO labels without removing parentheses inside goal text."""
import re


def strip_solo_suffixes(text: str) -> str:
    value = str(text or '').strip()
    while value:
        candidate = re.sub(r'[。.;；\s]+$', '', value)
        if not candidate.endswith(('）', ')')):
            break
        depth = 0
        start = None
        for index in range(len(candidate) - 1, -1, -1):
            char = candidate[index]
            if char in '）)': depth += 1
            elif char in '（(':
                depth -= 1
                if depth == 0:
                    start = index
                    break
        if start is None:
            break
        inside = candidate[start + 1:-1].strip()
        if not re.match(r'^(?:SOLO\s*[:：]?\s*)?(EA|P|U|M|R)(?=$|[\s，,：:、（(])', inside, re.I):
            break
        value = candidate[:start].strip()
    return value


def format_common_goal(common: dict) -> str:
    structure = common['target_cognitive_structure']
    return f"{strip_solo_suffixes(common['goal_statement'])}（{structure['solo_level']}，{structure['level_name']}）"
