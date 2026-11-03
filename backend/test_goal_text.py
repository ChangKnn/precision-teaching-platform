"""Legacy repeated SOLO labels and nested names remain editable and export once."""
from unittest import TestCase
from backend.app.services.goal_text import strip_solo_suffixes, format_common_goal

class GoalTextTest(TestCase):
    def test_repeated_nested_labels_are_removed_only_from_the_end(self):
        value='解释案例（结合观察）（EA，拓展抽象结构（深化））。（EA，拓展抽象结构（深化））'
        self.assertEqual(strip_solo_suffixes(value),'解释案例（结合观察）')
        self.assertEqual(strip_solo_suffixes(strip_solo_suffixes(value)),'解释案例（结合观察）')

    def test_ordinary_parentheses_and_core_punctuation_survive(self):
        for value in ['解释案例（结合观察）。','比较（R与EA的关系）','检验模型（适用条件）']:
            self.assertEqual(strip_solo_suffixes(value),value)
        self.assertEqual(strip_solo_suffixes('解释案例 (R, 关联结构).'),'解释案例')

    def test_export_formats_one_label_using_the_confirmed_structure(self):
        common={'goal_statement':'解释案例（EA，拓展抽象结构（深化））。','target_cognitive_structure':{'solo_level':'EA','level_name':'拓展抽象结构（深化）'}}
        text=format_common_goal(common)
        self.assertEqual(text,'解释案例（EA，拓展抽象结构（深化））')
        self.assertEqual(format_common_goal({**common,'goal_statement':text}),text)
