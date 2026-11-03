"""Reference path structures accept all five mechanisms without a one-group cap."""
import copy
import importlib.util
import json
from pathlib import Path
from unittest import TestCase
from jsonschema import validate
from backend.app.services.ai import _skill_runtime_instructions
from backend.app.services.skills import load_skill

ROOT=Path(__file__).parent/"skills/precision-intervention-goal-path-design"
spec=importlib.util.spec_from_file_location("goal_path_contract_checks",ROOT/"scripts/validate_contract.py")
checks=importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)

class PathPrototypeRulesTest(TestCase):
    def setUp(self):
        self.source=json.loads((ROOT/"tests/sample-input.json").read_text(encoding="utf-8"))
        self.result=json.loads((ROOT/"tests/sample-output.json").read_text(encoding="utf-8"))
        self.schema=json.loads((ROOT/"assets/goal-path-output.schema.json").read_text(encoding="utf-8"))
        self.roster=checks.roster_from_input(self.source)
        self.goals=checks.validate_goals(self.source,self.result,self.roster)
        self.students=[copy.deepcopy(student) for goal in self.result["progression_goals"] for student in goal["target_students"]]

    def arrange(self,mode,arrangements):
        stages=[]
        for n,(minutes,units) in enumerate(arrangements,1):
            stage={"stage_id":f"ST{n}","stage_name":f"比较本课案例证据与解释{n}","duration_minutes":minutes,"activity_units":[]}
            for i,(code,students) in enumerate(units,1):
                stage["activity_units"].append({"unit_id":f"ST{n}-{code}{i}","organization_code":code,"organization_name":f"课堂学习（{code}）","activity_name":stage["stage_name"] if len(units)==1 else f"比较解释方向{i}","target_goal_ids":["CG"],"activity_summary":"比较本课已有案例证据，解释要素间联系，并记录修订后的判断。"})
            stages.append(stage)
        self.result["intervention_path"].update(primary_path_types=[mode],primary_path_label=mode,stages=stages,total_duration_minutes=45,alternative_path=None)
        validate(self.result,self.schema)
        checks.validate_path(self.source,self.result,self.roster,self.goals)

    def test_progressive_six_steps_and_two_group_stages_are_valid(self):
        all_students=self.students
        h=[('H',all_students[:2]),('H',all_students[2:])]
        self.arrange('递进推进型',[(5,[('C',all_students)]),(8,h),(10,[('X',all_students)]),(7,[('I',all_students)]),(10,[('I',all_students)]),(5,[('C',all_students)])])

    def test_parallel_groups_form_one_core_time_slot(self):
        a=self.students
        self.arrange('并行分层型',[(5,[('C',a)]),(30,[('H',a[:2]),('H',a[2:])]),(10,[('C',a)])])

    def test_main_task_continues_while_branch_supports_other_students(self):
        a=self.students
        self.arrange('主线嵌入型',[(5,[('C',a)]),(30,[('I',a[:2]),('H',a[2:])]),(10,[('C',a)])])

    def test_stations_can_rotate_across_real_time_slots(self):
        a=self.students
        self.arrange('站点轮转型',[(5,[('C',a)]),(15,[('S',a[:2]),('S',a[2:])]),(15,[('S',a[2:]),('S',a[:2])]),(10,[('C',a)])])
        bad=copy.deepcopy(self.result)
        bad['intervention_path']['stages'][1]['activity_units'][1]['target_students']=copy.deepcopy(a[:2])
        with self.assertRaises(AssertionError):
            checks.validate_path(self.source,bad,self.roster,self.goals)

    def test_individual_parallel_learning_does_not_need_a_group_stage(self):
        a=self.students
        self.arrange('个体自驱型',[(5,[('C',a)]),(30,[('I',[s]) for s in a]),(10,[('C',a)])])

    def test_complete_reference_paths_require_collective_opening_and_closing(self):
        for index in (0,-1):
            bad=copy.deepcopy(self.result)
            unit=bad['intervention_path']['stages'][index]['activity_units'][0]
            unit['organization_code']='I'
            with self.assertRaisesRegex(AssertionError,'start and end'):
                checks.validate_path(self.source,bad,self.roster,self.goals)

    def test_live_runtime_loads_the_updated_path_reference(self):
        skill=load_skill('precision_intervention_goal_path_design')
        instructions=_skill_runtime_instructions(skill)
        self.assertEqual(skill.version,'1.9.0')
        self.assertIn('每个I内部有独立的微型学习回路',instructions)
        self.assertIn('站内可H/X/I',instructions)
        self.assertIn('单课时一般安排一个小组活动时段，最多两个',instructions)
        self.assertIn('跨课时每课时分别遵循',instructions)
        self.assertIn('共同核心目标同时依据课程要求与班级诊断',instructions)
        self.assertIn('多数学生已表现为EA',instructions)
        self.assertNotIn('班级诊断只用于判断学生与目标之间的距离',instructions)
        self.assertIn('A（Application）是综合应用或真实迁移的任务功能',instructions)
        self.assertIn('stage_id',instructions)
        self.assertIn('target_goal_ids',instructions)
        self.assertNotIn('原则上只安排**一个集中小组活动时段**',instructions)


    def test_core_ea_deepening_uses_existing_five_level_contract(self):
        from backend.app.goal_path_api import _validate_design
        class_result=copy.deepcopy(self.source['diagnosis_bundle']['class_diagnosis'])
        for row in class_result['class_summary']['distribution']:
            row['students']=copy.deepcopy(self.students) if row['level']=='EA' else []
            row['count']=len(row['students'])
            row['proportion']=1 if row['level']=='EA' else 0
        for entry in class_result['traceability']['level_assignments']:
            entry['original_level']='EA'
        result=copy.deepcopy(self.result)
        result['common_core_goal']['goal_statement']='比较新情境下不同解释模型的适用边界，并依据证据修订原有解释。'
        result['common_core_goal']['target_cognitive_structure'].update(solo_level='EA',level_name='拓展抽象结构（深化）',structure_and_rationale='诊断已达到有条件迁移，本课深化复杂条件下的模型比较与解释修订。')
        result['common_core_goal']['observable_success_criteria']=['比较模型适用条件，指出新证据如何要求修订解释。']
        goal=copy.deepcopy(result['progression_goals'][0])
        goal.update(goal_id='PG1',target_level_code='EA_DEEPENING',target_level_name='拓展抽象深化',source_levels=['EA'],target_students=[{**s,'current_level':'EA'} for s in self.students],goal_statement='检验新条件下的模型边界并修订解释',observable_achievement='依据新证据说明模型需调整的条件',custom_goal_note=None)
        result['progression_goals']=[goal]
        for entry in result['student_goal_assignments']:
            entry.update(original_level='EA',primary_goal_id='PG1')
        for stage in result['intervention_path']['stages']:
            for unit in stage['activity_units']:
                unit['target_goal_ids']=['CG']
        _validate_design(result,class_result,45)
        self.assertEqual(result['common_core_goal']['target_cognitive_structure']['solo_level'],'EA')
        self.assertEqual(result['progression_goals'][0]['target_level_code'],'EA_DEEPENING')


    def test_teacher_cross_level_target_does_not_reclassify_students(self):
        from backend.app.goal_path_api import _validate_design
        edited=copy.deepcopy(self.result)
        edited['progression_goals'][0].update(target_level_code='M',target_level_name='多点结构',goal_statement='教师依据支架要求学生比较多个条件',observable_achievement='指出多个相关条件并说明各自作用')
        checks.validate_goals(self.source,edited,self.roster)
        _validate_design(edited,self.source['diagnosis_bundle']['class_diagnosis'],45)
        self.assertEqual(edited['progression_goals'][0]['target_students'][0]['current_level'],'P')
