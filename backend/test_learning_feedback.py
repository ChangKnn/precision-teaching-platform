import asyncio
import json
from unittest import TestCase
from unittest.mock import patch
from fastapi import HTTPException, Response
from backend import test_task_version_runtime as fixtures
from backend.app import database as db, student_api, learning_feedback as sharing, main
from backend.test_teacher_auth import request


class LearningFeedbackTest(TestCase):
    def setUp(self):
        self.case = fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.students = [self.case.student]
        self.sessions = []
        for index in range(3):
            if index:
                now = db.utc_now()
                with db.database() as c:
                    c.execute('INSERT INTO students VALUES (?,?,?,?,?,?,?)', (f'stu{index}', 'class', '测试学校', '测试班', f'学生{index}', now, now))
                self.students.append(db.fetch_one('SELECT * FROM students WHERE id=?', (f'stu{index}',)))
            student = self.students[index]
            session = student_api.start_task('pt', student)['session']
            self.sessions.append(session)
            student_api.create_message(session['id'], student_api.StudentMessageCreate(content=f'我用条件{index}解释理由。'), student)
            student_api.submit_task(session['id'], student_api.StudentTaskSubmit(), student)
            data = {'diagnosis': {'level': ['EA','R','M'][index], 'rationale': '已能说明关系。', 'evidence': []}}
            now = db.utc_now()
            with db.database() as c:
                c.execute('''INSERT INTO student_diagnosis_reports
                    (session_id,teaching_id,skill_key,skill_version,provider,input_hash,generated_json,report_text,student_feedback_text,generated_at,updated_at,report_kind)
                    VALUES (?,'pt','test','1','openrouter','hash',?,'教师私有报告','你已经能解释信息间的联系。',?,?,'brief')''',
                    (session['id'], json.dumps(data), now, now))
        self.selection = sharing.ExampleSelection(session_ids=[session['id'] for session in self.sessions[:2]])

    def test_snapshots_are_persisted_and_only_available_after_teacher_push(self):
        sid = self.sessions[2]['id']; student = self.students[2]
        self.assertFalse(sharing.learning_feedback_status(sid, student)['thinking_ready'])
        self.assertEqual(sharing.learning_feedback(sid, student)['examples'], [])
        self.assertEqual(sharing.push_thinking_feedback('pt','class')['pushed_count'], 3)
        sharing.push_examples('pt','class', self.selection)
        db.initialize_database()
        restored = sharing.learning_feedback(sid, student)
        self.assertIn('多点结构', restored['thinking_feedback']['report_text'])
        self.assertEqual(len(restored['examples']), 2)
        self.assertTrue(restored['status']['examples_ready'])
        self.assertTrue(student_api.task_rows(student)[0]['learning_feedback']['thinking_ready'])
        self.assertNotIn('session_id', restored['examples'][0])
        self.assertNotIn('student_name', restored['examples'][0])
        self.assertNotIn('教师私有报告', json.dumps(restored, ensure_ascii=False))
        # Published examples remain stable when source conversations are edited later.
        with db.database() as c:
            c.execute("UPDATE student_messages SET content='changed after publication' WHERE session_id=?", (self.sessions[0]['id'],))
        self.assertEqual(sharing.learning_feedback(sid,student)['examples'], restored['examples'])
        sharing.withdraw_examples('pt','class')
        self.assertFalse(sharing.learning_feedback_status(sid,student)['examples_ready'])

    def test_duplicates_wrong_class_unsubmitted_and_different_version_are_rejected(self):
        sid = self.sessions[0]['id']
        with self.assertRaises(HTTPException): sharing.push_examples('pt','class',sharing.ExampleSelection(session_ids=[sid,sid]))
        with self.assertRaises(HTTPException): sharing.push_examples('pt','other-class',self.selection)
        with db.database() as c:
            c.execute("UPDATE student_task_sessions SET status='in_progress' WHERE id=?", (self.sessions[2]['id'],))
        with self.assertRaises(HTTPException): sharing.learning_feedback(self.sessions[2]['id'],self.students[2])
        self.assertFalse(sharing.learning_feedback_status(self.sessions[2]['id'],self.students[2])['examples_ready'])
        with db.database() as c:
            c.execute("UPDATE student_task_sessions SET task_version_id=NULL WHERE id=?", (self.sessions[1]['id'],))
        with self.assertRaises(HTTPException): sharing.push_examples('pt','class',self.selection)
        with self.assertRaises(HTTPException): sharing.learning_feedback(sid,self.students[1])

    def test_stale_or_mock_diagnoses_are_not_pushable_and_older_turns_are_not_shared(self):
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET status='stale' WHERE session_id=?", (self.sessions[0]['id'],))
            c.execute("UPDATE student_diagnosis_reports SET provider='mock' WHERE session_id=?", (self.sessions[1]['id'],))
        candidates = sharing.sharing_state('pt','class')['candidates']
        self.assertEqual([r['session_id'] for r in candidates], [self.sessions[2]['id']])
        with self.assertRaises(HTTPException): sharing.push_examples('pt','class',self.selection)
        sid = self.sessions[2]['id']
        with db.database() as c:
            c.execute("INSERT INTO student_messages (session_id,role,content,provider,created_at) VALUES (?,'student','older version answer','live',?)", (sid,db.utc_now()))
        self.assertNotIn('older version answer',json.dumps(sharing.preview_dialogue('pt','class',sid)))

    def test_example_read_is_scoped_to_recipient_class_and_task_version(self):
        sharing.push_examples('pt','class',self.selection)
        recipient = self.students[2]
        sid = self.sessions[2]['id']
        with db.database() as c:
            c.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('class2','另一班',?,?)", (db.utc_now(),db.utc_now()))
            c.execute("INSERT INTO classroom_schools VALUES ('class2','测试学校',?)", (db.utc_now(),))
            c.execute("INSERT INTO diagnosis_assignments VALUES ('pt','class2',?,1)", (db.utc_now(),))
            c.execute("UPDATE students SET classroom_id='class2' WHERE id=?", (recipient['id'],))
        other_class = {**recipient, 'classroom_id':'class2'}
        self.assertEqual(sharing.learning_feedback(sid,other_class)['examples'], [])
        with db.database() as c:
            c.execute("UPDATE students SET classroom_id='class' WHERE id=?", (recipient['id'],))
        self.case.save('draft',task_text='新任务：比较两种条件，并说明自己的判断。')
        self.case.confirm_rubric()
        self.case.publish()
        from backend.app.services.task_versions import published_version
        with db.database() as c:
            c.execute('UPDATE student_task_sessions SET task_version_id=? WHERE id=?', (published_version('pt')['id'],sid))
        self.assertEqual(sharing.learning_feedback(sid,recipient)['examples'], [])

    def test_teacher_routes_are_scoped_to_teacher_workspace(self):
        async def response(_): return Response(status_code=204)
        async def access(workspace):
            with patch.object(main,'current_account',return_value={'role':'teacher','workspace_id':workspace,'must_change_password':False}):
                return await main.protect_teacher_api(request('/api/precision-teachings/pt/classrooms/class/learning-sharing'),response)
        self.assertEqual(asyncio.run(access('ws')).status_code,204)
        self.assertEqual(asyncio.run(access('other')).status_code,404)
