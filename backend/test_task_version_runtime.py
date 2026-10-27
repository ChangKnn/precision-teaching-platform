"""Teacher republishing, role-led openings and immutable student evidence."""
from dataclasses import replace
from types import SimpleNamespace
import json
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from backend import test_task_materials as base
from backend.app import database as db, main, student_api, task_materials as materials
from backend.app.schemas import DiagnosisTaskUpdate, DiagnosisRubricUpdate, StudentMessageCreate, StudentTaskSubmit, PrecisionTeachingUpdate
from backend.app.services import student_dialogue_skill as dialogue, task_versions as versions
from backend.app.services.manual_rubric import FIELDS, LEVELS


class TaskVersionRuntimeTest(TestCase):
    def setUp(self):
        base.TaskMaterialsTest.setUp(self)
        for module in (student_api, dialogue):
            target = patch.object(module, 'settings', replace(self.settings, student_ai_provider='mock'))
            target.start(); self.addCleanup(target.stop)
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('stu','class','测试学校','测试班','模拟学生',?,?)", (now,now))
        self.student = db.fetch_one("SELECT * FROM students WHERE id='stu'")
        self.confirm_rubric()
        self.publish()

    def confirm_rubric(self):
        levels = {level:{field:[f'{level}教师填写的{field}'] for field in FIELDS} for level in LEVELS}
        main.update_diagnosis_rubric('pt', DiagnosisRubricUpdate(rubric={'levels':levels}, confirmed=True))

    def save(self, status='published', **changes):
        task = db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        payload = {key:task[key] for key in ('goal','task_text','ai_role','design_intent','duration_minutes','diagnosis_type')}
        payload.update(status=status, classroom_ids=['class'] if status=='published' else None)
        payload.update(changes)
        return main.update_diagnosis('pt', DiagnosisTaskUpdate(**payload))

    def publish(self, **changes):
        return self.save(**changes)

    def start(self):
        return student_api.start_task('pt', self.student)

    def send(self, session_id, text='我觉得应该比较条件，因为不同条件会影响判断。'):
        return student_api.create_message(session_id, StudentMessageCreate(content=text), self.student)

    def test_republish_keeps_existing_role_and_assigns_new_role_to_new_students(self):
        result = self.start(); session_id = result['session']['id']
        opening = result['session']['messages'][0]
        self.assertNotIn(result['task']['task_text'], opening['content'])
        self.send(session_id)
        before = db.fetch_all('SELECT * FROM student_messages WHERE session_id=? ORDER BY id', (session_id,))
        self.publish(ai_role='AI 角色：案例人物小凡\n应遵循：以小凡身份交流，只追问已经表达的理由\n禁止行为：不得提供答案')
        self.send(session_id, '请继续听我解释。')
        after = db.fetch_all('SELECT * FROM student_messages WHERE session_id=? ORDER BY id', (session_id,))
        self.assertEqual(after[:len(before)], before)
        execution = json.loads(after[-1]['execution_json'])
        self.assertNotEqual(execution['role_applied'], '案例人物小凡')
        self.assertEqual(after[0]['task_version_id'], after[-1]['task_version_id'])
        self.assertEqual(versions.version_history(session_id, versions.published_version('pt')), [])
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('fresh','class','测试学校','测试班','新开始学生',?,?)",(now,now))
        fresh=student_api.start_task('pt',db.fetch_one("SELECT * FROM students WHERE id='fresh'"))
        self.assertEqual(fresh['task']['task_version_id'],versions.published_version('pt')['id'])
        self.assertIn('案例人物小凡',fresh['session']['messages'][0]['content'])

    def test_draft_role_changes_do_not_replace_published_contract(self):
        session_id = self.start()['session']['id']
        self.save('draft', ai_role='AI 角色：尚未发布的新角色\n应遵循：中性追问\n禁止行为：不得提供答案')
        self.assertEqual(len(student_api.task_rows(self.student)), 1)
        self.send(session_id)
        message = db.fetch_one("SELECT execution_json FROM student_messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,))
        self.assertNotIn('尚未发布', json.loads(message['execution_json'])['role_applied'])

    def test_student_task_exposes_university_course_even_when_subject_is_empty(self):
        with db.database() as connection:
            connection.execute("UPDATE precision_teachings SET subject='',education_stage='高等教育',grade='大三',course_name='中学教育学基础' WHERE id='pt'")
        self.publish()
        task=student_api.task_rows(self.student)[0]
        self.assertEqual(task['subject'],'')
        self.assertEqual(task['course_name'],'中学教育学基础')
        opened=self.start()['task']
        self.assertEqual(opened['course_name'],'中学教育学基础')
        self.assertEqual(opened['education_stage'],'高等教育')

    def test_edit_all_teaching_fields_after_students_start_keeps_old_and_submitted_versions(self):
        active=self.start()['session']['id'];self.send(active)
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('stu2','class','测试学校','测试班','模拟学生2',?,?)",(now,now))
        other=db.fetch_one("SELECT * FROM students WHERE id='stu2'")
        submitted=student_api.start_task('pt',other)['session']['id']
        student_api.submit_task(submitted,StudentTaskSubmit(submitted_text='旧版本的提交成果'),other)
        old=versions.published_version('pt')
        old_messages=db.fetch_all('SELECT * FROM student_messages ORDER BY id')
        teaching=db.fetch_one("SELECT * FROM precision_teachings WHERE id='pt'")
        fields=('title','goal','content','rationale','subject','grade','textbook','estimated_periods','education_stage','course_name','major')
        payload={key:teaching[key] for key in fields}
        payload.update(title='新的理论应用主题',goal='比较解释并迁移到新情境',content='案例比较与迁移',textbook='新章节',grade='高二',edit_intent='revision')
        request=SimpleNamespace(state=SimpleNamespace(teacher_workspace_id='ws'))
        main.update_precision_teaching('pt',PrecisionTeachingUpdate(**payload),request)
        self.assertEqual(versions.published_version('pt')['id'],old['id'])
        self.assertEqual(student_api.get_student_session(active,self.student)['task']['title'],teaching['title'])
        self.confirm_rubric()
        self.publish(ai_role='AI 角色：课堂研讨同伴\n应遵循：只澄清已表达的理由\n禁止行为：不提供答案')
        new=versions.published_version('pt')
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(new['snapshot']['teaching']['goal'],payload['goal'])
        self.assertEqual(student_api.get_student_session(active,self.student)['task']['title'],teaching['title'])
        self.assertEqual(student_api.get_student_session(submitted,other)['task']['task_version_id'],old['id'])
        self.assertEqual(db.fetch_all('SELECT * FROM student_messages ORDER BY id'),old_messages)
        self.assertEqual(versions.get_version(old['id'])['snapshot']['teaching']['goal'],teaching['goal'])

    def test_changed_task_keeps_old_transcript_but_does_not_use_it_as_new_question_evidence(self):
        result = self.start(); session_id = result['session']['id']
        self.send(session_id, '这是旧题的原始回答。')
        original_messages = db.fetch_all('SELECT id,content FROM student_messages WHERE session_id=? ORDER BY id', (session_id,))
        new_text = '请结合另一个真实课堂案例，说明学生为什么会作出不同判断，并解释自己的理由。'
        self.save('draft', task_text=new_text)
        self.assertNotEqual(student_api.get_student_session(session_id,self.student)['task']['task_text'], new_text)
        self.confirm_rubric(); self.publish()
        latest = student_api.get_student_session(session_id,self.student)
        self.assertEqual(latest['task']['task_text'], result['task']['task_text'])
        self.assertEqual(versions.version_history(session_id,versions.published_version('pt')), [])
        self.send(session_id, '这是新题回答，依据是新的课堂条件。')
        self.assertEqual(db.fetch_all('SELECT id,content FROM student_messages WHERE session_id=? ORDER BY id', (session_id,))[:len(original_messages)], original_messages)
        history = versions.version_history(session_id,versions.published_version('pt'))
        self.assertEqual(history, [])
        own_history=versions.version_history(session_id,versions.get_version(result['task']['task_version_id']))
        self.assertTrue(any('旧题的原始回答' in item['content'] for item in own_history))

    def test_submitted_work_and_diagnosis_use_original_task_and_rubric_after_republish(self):
        result = self.start(); session_id = result['session']['id']
        self.send(session_id, '这是我提交前的原始分析。')
        student_api.submit_task(session_id,StudentTaskSubmit(submitted_text='旧版本成果'),self.student)
        before = db.fetch_one('SELECT * FROM student_task_sessions WHERE id=?', (session_id,))
        self.save('draft', task_text='新任务：请说明另一种课堂现象中的判断过程，并结合资料给出自己的理由。')
        self.confirm_rubric(); self.publish()
        session = student_api.get_student_session(session_id,self.student)
        self.assertEqual(session['task']['task_text'], result['task']['task_text'])
        self.assertEqual(db.fetch_one('SELECT * FROM student_task_sessions WHERE id=?', (session_id,)), before)
        payload,_ = student_api.build_student_diagnosis_input(session_id)
        self.assertEqual(payload['diagnostic_task']['task_text'], result['task']['task_text'])
        self.assertEqual(payload['submitted_text'], '旧版本成果')

    def test_replacing_images_preserves_old_student_image_access(self):
        session_id = self.start()['session']['id']
        original = versions.published_version('pt')
        state = base.TaskMaterialsTest.upload(self)
        materials.review_recognition('pt',materials.ReadingReview(source_hash=state['source_hash'],task_text='图片中只有一份课堂案例，请基于案例说明理由。'))
        self.confirm_rubric(); self.publish()
        self.send(session_id)
        old = versions.published_version('pt'); image = old['snapshot']['images'][0]
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('viewer','class','测试学校','测试班','看过题图的学生',?,?)", (now,now))
        viewer = db.fetch_one("SELECT * FROM students WHERE id='viewer'")
        student_api.start_task('pt', viewer)
        state = materials.material_state('pt')
        materials.remove_task_image('pt',image['id'],materials.MaterialConfirmation(source_hash=state['source_hash']))
        self.confirm_rubric(); self.publish()
        response = student_api.student_version_image('pt',old['id'],image['id'],viewer)
        self.assertTrue(response.path.is_file())
        with self.assertRaises(HTTPException) as denied:
            student_api.student_version_image('pt',old['id'],image['id'],self.student)
        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(versions.get_version(original['id'])['snapshot']['images'], [])
        with self.assertRaises(HTTPException):
            student_api.student_version_image('pt',old['id'],image['id'],{**self.student,'classroom_id':'other'})
        with self.assertRaises(HTTPException):
            student_api.student_version_image('other',old['id'],image['id'],self.student)

    def test_current_published_image_is_accessible_to_assigned_student(self):
        state = base.TaskMaterialsTest.upload(self)
        materials.review_recognition('pt', materials.ReadingReview(source_hash=state['source_hash'], task_text='请围绕本图展示的课堂案例说明判断依据。'))
        self.confirm_rubric(); self.publish()
        version = versions.published_version('pt')
        image = version['snapshot']['images'][0]
        self.assertTrue(student_api.student_version_image('pt', version['id'], image['id'], self.student).path.is_file())

    def test_teacher_design_intent_and_outer_whitespace_do_not_trigger_material_lock(self):
        self.start()
        task = db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        self.publish(task_text='\n'+task['task_text']+'\n')
        self.assertEqual(db.fetch_one("SELECT status FROM diagnosis_rubrics WHERE teaching_id='pt'")['status'],'confirmed')
        self.save('draft', design_intent='更新后的诊断意图：观察推理依据。')
        self.assertEqual(db.fetch_one("SELECT design_intent FROM diagnosis_tasks WHERE teaching_id='pt'")['design_intent'],'更新后的诊断意图：观察推理依据。')

    def test_inflight_reply_can_finish_its_pinned_version_during_republish(self):
        session_id = self.start()['session']['id']
        def generate(config, sid, student_id, history, current):
            self.publish(ai_role='AI 角色：新发布角色\n应遵循：中性追问\n禁止行为：不得提供答案')
            return dialogue._mock_result(dialogue._payload(config,sid,student_id,history,current)), 'mock', None
        before = db.fetch_all('SELECT * FROM student_messages WHERE session_id=?',(session_id,))
        with patch.object(student_api,'generate_dialogue_turn',side_effect=generate):
            self.send(session_id)
        after=db.fetch_all('SELECT * FROM student_messages WHERE session_id=? ORDER BY id',(session_id,))
        self.assertEqual(after[:len(before)],before)
        self.assertEqual(after[-1]['task_version_id'],before[0]['task_version_id'])

    def test_unchanged_publish_does_not_create_spurious_versions(self):
        before = versions.published_version('pt')['id']
        self.publish()
        self.assertEqual(versions.published_version('pt')['id'],before)

    def test_student_cannot_read_another_students_session(self):
        session_id = self.start()['session']['id']
        with self.assertRaises(HTTPException) as raised:
            student_api.get_student_session(session_id,{**self.student,'id':'other'})
        self.assertEqual(raised.exception.status_code,404)

    def test_reopening_after_role_update_retains_original_opening(self):
        first = self.start()
        session_id = first['session']['id']
        self.publish(ai_role='AI 角色：案例中的小凡\n应遵循：用第一人称说话\n禁止行为：不得提供答案')
        reopened = self.start()
        self.assertEqual(reopened['session']['messages'][0],first['session']['messages'][0])
        self.assertEqual(reopened['session']['messages'],first['session']['messages'])
        self.assertEqual(len(self.start()['session']['messages']),1)

    def test_old_browser_can_send_and_submit_to_its_pinned_old_question(self):
        first=self.start(); session_id=first['session']['id']; old_version=first['task']['task_version_id']
        self.save('draft',task_text='新题：请依据另一个课堂教学案例，解释你判断教学安排的依据和理由。')
        self.confirm_rubric();self.publish()
        for action in (
            lambda:student_api.create_message(session_id,StudentMessageCreate(content='旧题的回答',task_version_id=old_version),self.student),
            lambda:student_api.submit_task(session_id,StudentTaskSubmit(submitted_text='旧题成果',task_version_id=old_version),self.student),
        ):
            action()
        session=student_api.get_student_session(session_id,self.student)
        self.assertEqual(session['task_version_id'],old_version)
        self.assertEqual(session['status'],'submitted')
        self.assertEqual(db.fetch_one('SELECT status FROM student_task_sessions WHERE id=?',(session_id,))['status'],'submitted')

    def test_role_only_change_accepts_answer_from_same_question_browser(self):
        first=self.start()
        self.publish(ai_role='AI 角色：新的中性同伴\n应遵循：追问已有理由\n禁止行为：不得提供答案')
        result=student_api.create_message(first['session']['id'],StudentMessageCreate(content='我继续说明原来的理由。',task_version_id=first['task']['task_version_id']),self.student)
        self.assertEqual(result['task']['task_version_id'],first['task']['task_version_id'])

    def test_standard_updates_preserve_report_text_and_require_fresh_diagnosis(self):
        first=self.start();sid=first['session']['id'];self.send(sid)
        student_api.submit_task(sid,StudentTaskSubmit(submitted_text='旧题成果'),self.student)
        now=db.utc_now()
        with db.database() as connection:
            connection.execute('''INSERT INTO student_diagnosis_reports
                (session_id,teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,report_text,generated_at,updated_at)
                VALUES (?,'pt','solo_student_diagnosis_feedback','1','openrouter','pushed','hash','{}','原报告',?,?)''',(sid,now,now))
        before=db.fetch_one('SELECT * FROM student_diagnosis_reports WHERE session_id=?',(sid,))
        self.save('draft',task_text='新任务：请分析另一个教学案例，说明自己的选择及依据，不要只给最终结论。')
        self.confirm_rubric();self.publish()
        after=db.fetch_one('SELECT * FROM student_diagnosis_reports WHERE session_id=?',(sid,))
        self.assertEqual(after['report_text'],before['report_text'])
        self.assertEqual(after['status'],'stale')
        main.refresh_feedback_counts('pt')
        counts=db.fetch_one("SELECT total_students,confirmed_count FROM feedback_summaries WHERE teaching_id='pt'")
        self.assertEqual(counts,{'total_students':1,'confirmed_count':0})
        from backend.app.services import class_report
        self.assertIsNone(class_report.class_report_input('pt','class')[0])

    def test_legacy_migration_is_idempotent_and_only_adds_version_metadata(self):
        first=self.start();sid=first['session']['id'];self.send(sid)
        with db.database() as connection:
            connection.execute('UPDATE student_task_sessions SET task_version_id=NULL WHERE id=?',(sid,))
            connection.execute('UPDATE student_messages SET task_version_id=NULL WHERE session_id=?',(sid,))
            connection.execute("UPDATE diagnosis_tasks SET published_version_id=NULL WHERE teaching_id='pt'")
        before=db.fetch_all('SELECT id,role,content,provider,execution_json,created_at FROM student_messages WHERE session_id=? ORDER BY id',(sid,))
        versions.preserve_legacy_task_versions();versions.preserve_legacy_task_versions()
        self.assertEqual(db.fetch_all('SELECT id,role,content,provider,execution_json,created_at FROM student_messages WHERE session_id=? ORDER BY id',(sid,)),before)
        self.assertIsNotNone(db.fetch_one('SELECT task_version_id FROM student_task_sessions WHERE id=?',(sid,))['task_version_id'])

    def test_legacy_session_keeps_frozen_role_when_current_role_changed_before_upgrade(self):
        sid = self.start()['session']['id']
        saved = db.fetch_one('SELECT dialogue_config_json FROM student_task_sessions WHERE id=?', (sid,))['dialogue_config_json']
        original = json.loads(saved)
        with db.database() as connection:
            connection.execute('UPDATE student_task_sessions SET task_version_id=NULL WHERE id=?', (sid,))
            connection.execute('UPDATE student_messages SET task_version_id=NULL WHERE session_id=?', (sid,))
            connection.execute("UPDATE diagnosis_tasks SET published_version_id=NULL,ai_role='AI 角色：升级前已经修改的新角色' WHERE teaching_id='pt'")
        versions.preserve_legacy_task_versions()
        with patch.object(student_api, 'generate_dialogue_turn', wraps=dialogue.generate_dialogue_turn) as generate:
            self.send(sid)
        self.assertEqual(generate.call_args.args[0], original)
        last = db.fetch_one('SELECT execution_json FROM student_messages WHERE session_id=? ORDER BY id DESC LIMIT 1', (sid,))
        self.assertEqual(json.loads(last['execution_json'])['role_applied'], original['ai_dialogue_contract']['ai_role'])
        self.assertEqual(db.fetch_one('SELECT dialogue_config_json FROM student_task_sessions WHERE id=?', (sid,))['dialogue_config_json'], saved)

    def test_minor_revisions_with_same_rubric_share_one_class_analysis(self):
        from backend.app.services import class_report
        from fastapi import BackgroundTasks
        first=self.start(); self.send(first['session']['id'])
        student_api.submit_task(first['session']['id'],StudentTaskSubmit(submitted_text='第一位学生成果'),self.student)
        self.publish(ai_role='AI 角色：新的研讨同伴\n应遵循：自然交流\n禁止行为：不得编造案例')
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('next','class','测试学校','测试班','另一位学生',?,?)",(now,now))
        other=db.fetch_one("SELECT * FROM students WHERE id='next'")
        second=student_api.start_task('pt',other)
        student_api.create_message(second['session']['id'],StudentMessageCreate(content='第二位学生的依据'),other)
        student_api.submit_task(second['session']['id'],StudentTaskSubmit(submitted_text='第二位学生成果'),other)
        for opened in (first,second):
            with db.database() as connection:
                connection.execute('''INSERT INTO student_diagnosis_reports
                    (session_id,teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,report_text,generated_at,updated_at)
                    VALUES (?,'pt','solo_student_diagnosis_feedback','1','openrouter','confirmed','hash','{}','测试报告',?,?)''',
                    (opened['session']['id'],now,now))
        self.assertNotEqual(first['task']['task_version_id'],second['task']['task_version_id'])
        payload,_,names=class_report.class_report_input('pt','class')
        self.assertEqual(len(payload['student_diagnosis_results']),2)
        self.assertEqual(set(names),{'stu','next'})

    def test_old_session_diagnosis_uses_latest_teacher_confirmed_standard(self):
        first=self.start();sid=first['session']['id'];self.send(sid)
        self.save('draft',task_text='修改中的新任务，请结合另一个案例说明自己的判断和依据。')
        student_api.submit_task(sid,StudentTaskSubmit(submitted_text='原任务成果'),self.student)
        with self.assertRaises(HTTPException):
            student_api.build_student_diagnosis_input(sid)
        levels = {level:{field:[f'{level}新版教师标准{field}'] for field in FIELDS} for level in LEVELS}
        updated=main.update_diagnosis_rubric('pt',DiagnosisRubricUpdate(rubric={'levels':levels},confirmed=True))
        payload,_=student_api.build_student_diagnosis_input(sid)
        self.assertEqual(payload['diagnostic_task']['task_text'],first['task']['task_text'])
        self.assertEqual(payload['task_specific_solo_rubric'],updated['rubric']['rubric'])
        self.assertNotEqual(payload['task_specific_solo_rubric'],versions.get_version(first['task']['task_version_id'])['snapshot']['rubric'])
