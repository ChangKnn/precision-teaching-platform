"""A theme has multiple independent tasks while old evidence keeps its IDs."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from backend import test_task_version_runtime as runtime_tests
from backend.app import database as db, main, student_api
from backend.app import diagnostic_tasks as tasks
from backend.app.schemas import DiagnosisTaskUpdate, DiagnosisRubricUpdate, StudentTaskSubmit, PrecisionTeachingUpdate
from backend.app.services.manual_rubric import LEVELS, FIELDS
from backend.app.services.class_report import class_report_input
from backend.app.services.skills import load_skill


class MultipleDiagnosticTasksTest(TestCase):
    setUp = runtime_tests.TaskVersionRuntimeTest.setUp
    confirm_rubric = runtime_tests.TaskVersionRuntimeTest.confirm_rubric
    publish = runtime_tests.TaskVersionRuntimeTest.publish
    save = runtime_tests.TaskVersionRuntimeTest.save
    send = runtime_tests.TaskVersionRuntimeTest.send
    start = runtime_tests.TaskVersionRuntimeTest.start

    def request(self, workspace='ws'):
        return SimpleNamespace(state=SimpleNamespace(teacher_workspace_id=workspace))

    def add(self, source='pt', name='新案例迁移诊断'):
        return tasks.create_task(source,tasks.TaskName(name=name),self.request())['task_id']

    def publish_second(self, task_id):
        values=dict(goal='说明理由',task_text='请比较两个新案例并解释教育理论如何支持你的判断。',
                    ai_role='AI角色：研讨同伴；只讨论学生已表达的依据，不提供答案。',design_intent='观察新案例中的迁移解释',
                    duration_minutes=15,diagnosis_type='post',status='draft')
        main.update_diagnosis(task_id,DiagnosisTaskUpdate(**values))
        levels={level:{field:[f'{level}第二任务标准{field}'] for field in FIELDS} for level in LEVELS}
        main.update_diagnosis_rubric(task_id,DiagnosisRubricUpdate(rubric={'levels':levels},confirmed=True))
        main.update_diagnosis(task_id,DiagnosisTaskUpdate(**{**values,'status':'published','classroom_ids':['class']}))

    def test_new_task_is_empty_and_theme_list_stays_single(self):
        before=db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        child=self.add()
        self.assertEqual(len(main.ordered_teachings('ws')),1)
        self.assertEqual(len(tasks.family_tasks(child)),2)
        row=db.fetch_one('SELECT * FROM precision_teachings WHERE id=?',(child,))
        self.assertEqual(row['parent_teaching_id'],'pt')
        self.assertEqual(row['title'],main.ordered_teachings('ws')[0]['title'])
        task=db.fetch_one('SELECT * FROM diagnosis_tasks WHERE teaching_id=?',(child,))
        self.assertEqual((task['task_text'],task['ai_role'],task['status']),('','','draft'))
        self.assertIsNone(db.fetch_one('SELECT * FROM diagnosis_rubrics WHERE teaching_id=?',(child,)))
        self.assertFalse(db.fetch_all('SELECT * FROM diagnosis_assignments WHERE teaching_id=?',(child,)))
        self.assertEqual(db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'"),before)

    def test_same_student_has_independent_submissions_and_report_inputs(self):
        first=self.start()['session']['id'];self.send(first)
        student_api.submit_task(first,StudentTaskSubmit(submitted_text='第一次解释'),self.student)
        before=db.fetch_one('SELECT * FROM student_task_sessions WHERE id=?',(first,))
        messages=db.fetch_all('SELECT * FROM student_messages WHERE session_id=? ORDER BY id',(first,))
        child=self.add();self.publish_second(child)
        second=student_api.start_task(child,self.student)['session']['id'];self.send(second,'我会先比较新案例与原案例的差异，再说明理论适用的条件。')
        student_api.submit_task(second,StudentTaskSubmit(submitted_text='第二次解释'),self.student)
        self.assertNotEqual(first,second)
        self.assertEqual(db.fetch_one('SELECT * FROM student_task_sessions WHERE id=?',(first,)),before)
        self.assertEqual(db.fetch_all('SELECT * FROM student_messages WHERE session_id=? ORDER BY id',(first,)),messages)
        a,_=student_api.build_student_diagnosis_input(first);b,_=student_api.build_student_diagnosis_input(second)
        self.assertNotEqual(a['diagnostic_task']['task_text'],b['diagnostic_task']['task_text'])
        self.assertNotEqual(a['task_specific_solo_rubric'],b['task_specific_solo_rubric'])
        self.assertEqual(a['submitted_text'],'第一次解释');self.assertEqual(b['submitted_text'],'第二次解释')
        self.assertEqual(len(student_api.task_rows(self.student)),2)
        self.assertEqual({r['task_name'] for r in student_api.task_rows(self.student)},{'诊断任务一','新案例迁移诊断'})
        skill=load_skill('solo_student_diagnosis_feedback')
        result=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        # Aggregation intentionally excludes mock-provider reports. This fixture
        # must not depend on a developer's .env to represent a real report.
        with patch.object(student_api,'settings',replace(self.settings,ai_provider='openrouter',student_ai_provider='mock')),patch.object(student_api,'run_student_diagnosis_report',return_value=(result,'模拟报告','模拟反馈')),patch.object(student_api,'queue_class_report'):
            student_api.generate_student_report(first,BackgroundTasks())
            student_api.generate_student_report(second,BackgroundTasks())
        for tid in ['pt',child]:
            records=student_api.student_results(tid,BackgroundTasks())
            self.assertEqual(records['submitted_count'],1)
            payload,_,_=class_report_input(tid,'class')
            self.assertEqual(len(payload['student_diagnosis_results']),1)

    def test_unpublished_second_task_does_not_appear_to_students(self):
        self.add()
        self.assertEqual([r['teaching_id'] for r in student_api.task_rows(self.student)],['pt'])

    def test_task_under_child_stays_in_same_theme_and_selection_survives_refresh(self):
        child=self.add();third=self.add(child,'第三次诊断')
        self.assertEqual([r['task_order'] for r in tasks.family_tasks(third)],[1,2,3])
        self.assertEqual(main.workspace_for(third)['theme_id'],'pt')
        main.save_current_teaching_id(third,'ws')
        self.assertEqual(main.current_teaching_id(main.ordered_teachings('ws'),'ws'),third)

    def test_shared_information_changes_all_tasks_but_not_old_snapshots(self):
        child=self.add();self.publish_second(child)
        first=self.start()['session']['id']
        old_version=db.fetch_one('SELECT task_version_id FROM student_task_sessions WHERE id=?',(first,))['task_version_id']
        old_snapshot=db.fetch_one('SELECT snapshot_json FROM diagnosis_task_versions WHERE id=?',(old_version,))['snapshot_json']
        root=db.fetch_one("SELECT * FROM precision_teachings WHERE id='pt'")
        payload={key:root[key] for key in tasks.SHARED_FIELDS};payload.update(goal='新的共同教学目标',estimated_periods=3)
        main.update_precision_teaching(child,PrecisionTeachingUpdate(**payload),self.request())
        for tid in ['pt',child]:
            self.assertEqual(db.fetch_one('SELECT goal FROM precision_teachings WHERE id=?',(tid,))['goal'],'新的共同教学目标')
        self.assertEqual(db.fetch_one('SELECT snapshot_json FROM diagnosis_task_versions WHERE id=?',(old_version,))['snapshot_json'],old_snapshot)

    def test_names_and_permissions(self):
        child=self.add()
        tasks.rename_task(child,tasks.TaskName(name='第二课时案例分析'),self.request())
        self.assertEqual(tasks.family_tasks(child)[1]['task_name'],'第二课时案例分析')
        for callback in [lambda:tasks.list_tasks(child,self.request('other')),
                         lambda:tasks.create_task(child,tasks.TaskName(name='禁止'),self.request('other')),
                         lambda:tasks.rename_task(child,tasks.TaskName(name='禁止'),self.request('other'))]:
            with self.assertRaises(HTTPException) as error:callback()
            self.assertEqual(error.exception.status_code,404)
        with self.assertRaises(ValidationError):tasks.TaskName(name='   ')

    def test_database_migration_is_idempotent_and_theme_delete_cascades(self):
        child=self.add();db.initialize_database();db.initialize_database()
        self.assertEqual(len(tasks.family_tasks(child)),2)
        main.delete_precision_teaching('pt',self.request())
        self.assertIsNone(db.fetch_one('SELECT * FROM precision_teachings WHERE id=?',(child,)))
