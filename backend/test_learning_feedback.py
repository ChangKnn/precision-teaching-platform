import json
from unittest import TestCase
from fastapi import HTTPException
from backend import test_task_version_runtime as fixtures
from backend.app import database as db, student_api, learning_feedback as sharing


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

    def test_automatic_feedback_and_two_strong_examples_persist_without_teacher_action(self):
        sid = self.sessions[2]['id']; student = self.students[2]
        self.assertEqual(sharing.learning_feedback(sid,student)['examples'],[])
        changes = sharing.sync_automatic_learning('pt','class')
        self.assertEqual(changes,{'feedback_updated':3,'example_sets_updated':1})
        db.initialize_database()
        restored = sharing.learning_feedback(sid,student)
        self.assertIn('多点结构',restored['thinking_feedback']['report_text'])
        self.assertEqual([e['level_name'] for e in restored['examples']], ['抽象拓展结构','关联结构'])
        self.assertTrue(student_api.task_rows(student)[0]['learning_feedback']['thinking_ready'])
        self.assertNotIn('session_id',restored['examples'][0])
        self.assertNotIn('student_name',restored['examples'][0])
        self.assertNotIn('教师私有报告',json.dumps(restored,ensure_ascii=False))
        self.assertEqual(sharing.sync_automatic_learning('pt','class'),{'feedback_updated':0,'example_sets_updated':0})
        self.assertEqual(sharing.learning_feedback(sid,student),restored)

    def test_fewer_than_two_strong_or_complete_dialogues_are_not_recommended(self):
        sid=self.sessions[1]['id']
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET generated_json=? WHERE session_id=?", (json.dumps({'diagnosis':{'level':'U'}}),sid))
        sharing.sync_automatic_learning('pt','class')
        self.assertFalse(sharing.learning_feedback_status(self.sessions[2]['id'],self.students[2])['examples_ready'])
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET generated_json=? WHERE session_id=?", (json.dumps({'diagnosis':{'level':'R'}}),sid))
            c.execute("DELETE FROM student_messages WHERE session_id=? AND role='student'",(sid,))
        sharing.sync_automatic_learning('pt','class')
        self.assertEqual(sharing.learning_feedback(self.sessions[2]['id'],self.students[2])['examples'],[])

    def test_unsubmitted_students_cannot_view_feedback_or_peer_dialogues(self):
        sharing.sync_automatic_learning('pt','class')
        sid=self.sessions[2]['id']; student=self.students[2]
        with db.database() as c:
            c.execute("UPDATE student_task_sessions SET status='in_progress' WHERE id=?",(sid,))
        with self.assertRaises(HTTPException):sharing.learning_feedback(sid,student)
        self.assertFalse(sharing.learning_feedback_status(sid,student)['examples_ready'])
        with self.assertRaises(HTTPException):sharing.learning_feedback(self.sessions[0]['id'],student)

    def test_stale_or_mock_diagnoses_are_excluded_and_late_analysis_updates_recommendations(self):
        sid=self.sessions[0]['id']
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET status='stale' WHERE session_id=?",(sid,))
        sharing.sync_automatic_learning('pt','class')
        self.assertFalse(sharing.learning_feedback_status(sid,self.students[0])['thinking_ready'])
        self.assertFalse(sharing.learning_feedback_status(self.sessions[2]['id'],self.students[2])['examples_ready'])
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET status='draft' WHERE session_id=?",(sid,))
        sharing.sync_automatic_learning('pt','class')
        self.assertTrue(sharing.learning_feedback_status(sid,self.students[0])['examples_ready'])
        with db.database() as c:
            c.execute("UPDATE student_diagnosis_reports SET provider='mock' WHERE session_id=?",(sid,))
        sharing.sync_automatic_learning('pt','class')
        self.assertFalse(sharing.learning_feedback_status(self.sessions[2]['id'],self.students[2])['examples_ready'])

    def test_example_read_is_scoped_to_recipient_class_and_task_version(self):
        sharing.sync_automatic_learning('pt','class')
        recipient=self.students[2];sid=self.sessions[2]['id']
        with db.database() as c:
            c.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('class2','另一班',?,?)",(db.utc_now(),db.utc_now()))
            c.execute("INSERT INTO classroom_schools VALUES ('class2','测试学校',?)",(db.utc_now(),))
            c.execute("INSERT INTO diagnosis_assignments VALUES ('pt','class2',?,1)",(db.utc_now(),))
            c.execute("UPDATE students SET classroom_id='class2' WHERE id=?",(recipient['id'],))
        other_class={**recipient,'classroom_id':'class2'}
        self.assertEqual(sharing.learning_feedback(sid,other_class)['examples'],[])
        with db.database() as c:c.execute("UPDATE students SET classroom_id='class' WHERE id=?",(recipient['id'],))
        self.case.save('draft',task_text='新任务：比较两种条件，并说明自己的判断。')
        self.case.confirm_rubric();self.case.publish()
        from backend.app.services.task_versions import published_version
        with db.database() as c:
            c.execute('UPDATE student_task_sessions SET task_version_id=? WHERE id=?',(published_version('pt')['id'],sid))
        self.assertEqual(sharing.learning_feedback(sid,recipient)['examples'],[])

    def test_previous_task_turns_are_not_in_automatic_recommendations(self):
        with db.database() as c:
            c.execute("INSERT INTO student_messages (session_id,role,content,provider,created_at) VALUES (?,'student','旧题回答','live',?)",(self.sessions[0]['id'],db.utc_now()))
        sharing.sync_automatic_learning('pt','class')
        result=sharing.learning_feedback(self.sessions[2]['id'],self.students[2])
        self.assertNotIn('旧题回答',json.dumps(result,ensure_ascii=False))
