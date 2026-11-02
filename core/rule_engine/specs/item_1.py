"""
Rule specification for AGREE-II item 1.
"""
from __future__ import annotations

from core.rule_engine.models import GateComponentRule, ItemSpec, RuleObjectSpec


ITEM_1_SPEC = ItemSpec(
    item_id="item_1",
    title="The overall objective(s) of the guideline is (are) specifically described.",
    rule_profile="gate_component_count",
    object_specs={
        "health_topic": RuleObjectSpec(
            type="health_topic",
            role="gate",
            description=(
                "疾病、药物、技术、临床问题或健康主题。"
                "例如：2 型糖尿病、二甲双胍、乳腺癌筛查。"
            ),
        ),
        "health_intent": RuleObjectSpec(
            type="health_intent",
            role="component",
            counts_for="specificity",
            description=(
                "指南目标中的健康行为或服务类型，例如预防、筛查、诊断、治疗、管理、随访。"
            ),
        ),
        "expected_outcome": RuleObjectSpec(
            type="expected_outcome",
            role="component",
            counts_for="specificity",
            description=(
                "预期健康收益或结果，例如降低死亡率、改善生活质量、减少并发症、提高诊断准确性。"
                "对于就业、教育、公共卫生或社会功能类指南，也包括安全有效工作、维持就业资格、"
                "避免不合理排除、改善功能表现、公平获得服务或促进社会参与等明确预期结果。"
                "仅有“提高规范性/提供参考”等流程性表达时应谨慎抽取。"
            ),
        ),
        "target_population": RuleObjectSpec(
            type="target_population",
            role="component",
            counts_for="specificity",
            description=(
                "目标患者、人群或社会群体，例如成人 2 型糖尿病患者、孕妇、儿童。"
                "注意：单纯疾病名不是目标人群。"
            ),
        ),
    },
    extraction_guidance=(
        "只抽取与 item_1 评分直接相关的对象。不要抽取作者、机构、指南使用者，"
        "除非文本明确将其作为患者/社会目标人群。不要根据标题或常识推断缺失对象。"
        "如果文本明确说明指南希望达到的工作能力、安全性、资格判断、公平就业、功能改善或社会参与结果，"
        "应抽取为预期结果。"
    ),
    rule=GateComponentRule(),
)
