"""Instructional Skills keep the teacher's lesson distinct from diagnostic tasks."""
from unittest import TestCase
from backend.app.services.skills import load_skill
from backend.app.services.ai import _skill_runtime_instructions


class InstructionalScopeTest(TestCase):
    def test_every_design_stage_loads_teacher_led_scope_rule(self):
        for key in ('precision_intervention_goal_path_design',
                    'precision_intervention_activity_formative_regulation',
                    'precision_intervention_plan_integration_review'):
            with self.subTest(skill=key):
                instructions = _skill_runtime_instructions(load_skill(key))
                self.assertIn('诊断任务是获取学情的工具，不一定是本节课的内容', instructions)
                self.assertIn('以教师提供的教学内容、课程要求和课堂构想为准', instructions)
                self.assertIn('不推断学生已掌握或不掌握另一内容', instructions)
                self.assertIn('不擅自改目标或主线', instructions)

    def test_integration_does_not_require_reusing_diagnostic_material(self):
        text = _skill_runtime_instructions(load_skill('precision_intervention_plan_integration_review'))
        self.assertIn('不因未复用诊断题目、情境或材料判为不一致', text)
