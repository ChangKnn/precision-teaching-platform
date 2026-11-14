import copy
import json
from unittest import TestCase
from unittest.mock import patch

from backend.app import student_api
from backend.app.services.readable_evidence import readable_narrative, readable_text
from backend.app.services.skills import load_skill
from backend.app.services.class_report import render_class_markdown, with_student_case_summaries


class ReadableEvidenceTest(TestCase):
    def test_narrative_hides_references_but_preserves_quotes_and_identifiers(self):
        source = {'rationale':'学生在turn-3232提出观点，turn–3668中补充依据。',
                  'evidence':[{'ref':'turn-3232','quote':'学生原话 turn-3232','interpretation':'见turn-3232'}]}
        before = copy.deepcopy(source)
        shown = readable_narrative(source)
        self.assertNotIn('turn-', shown['rationale'])
        self.assertNotIn('turn–', shown['rationale'])
        self.assertEqual(shown['evidence'][0]['ref'], 'turn-3232')
        self.assertEqual(shown['evidence'][0]['quote'], '学生原话 turn-3232')
        self.assertEqual(source, before)
        self.assertEqual(readable_text('原话“turn-3232”来自turn-3232'), '原话“turn-3232”来自对话记录')

    def test_existing_student_report_is_sanitized_at_read_time(self):
        skill=load_skill('solo_student_diagnosis_feedback')
        result=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        result['diagnosis']['rationale']='学生在turn-3668解释原因。'
        original=json.dumps(result,ensure_ascii=False)
        row={'session_id':'s','skill_key':'test','skill_version':'1','provider':'openrouter','status':'draft',
             'generated_json':original,'report_text':'依据turn-3668判断。','student_feedback_text':'你的turn-3668说明了想法。',
             'generated_at':'t','reviewed_at':None,'pushed_at':None,'updated_at':'t'}
        with patch.object(student_api,'fetch_one',return_value=row): shown=student_api.serialize_student_report('s')
        self.assertIn('对话记录',shown['result']['diagnosis']['rationale'])
        self.assertNotIn('turn-3668',shown['report_text'] + shown['student_feedback_text'])
        self.assertEqual(row['generated_json'],original)

    def test_class_markdown_does_not_print_internal_evidence_reference(self):
        skill=load_skill('solo_class_diagnosis_intervention')
        result=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        result['level_analyses'][0]['typical_evidence'][0]['source_ref']='turn-3668'
        result['level_analyses'][0]['main_performance']='在turn-3668解释原因'
        self.assertNotIn('turn-3668',render_class_markdown(result))

    def test_cases_use_report_snapshot_not_raw_quotation_and_deduplicate(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        result = json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        payload = json.loads((skill.path.parent/'tests/sample-input.json').read_text(encoding='utf-8'))
        level = result['level_analyses'][0]
        level['typical_evidence'].append(copy.deepcopy(level['typical_evidence'][0]))
        student = next(s for s in payload['student_diagnosis_results'] if s['student']['student_id'] == level['typical_evidence'][0]['student_id'])
        view = with_student_case_summaries(result, payload)
        cases = view['level_analyses'][0]['student_cases']
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['level_reason'], student['diagnosis']['rationale'])
        self.assertEqual(cases[0]['obstacle'], student['progression']['main_obstacle'])
        rendered = render_class_markdown(result, payload)
        self.assertIn(cases[0]['obstacle'], rendered)
        self.assertNotIn(level['typical_evidence'][0]['quote'], rendered)
        self.assertNotIn('student_cases', result['level_analyses'][0])
