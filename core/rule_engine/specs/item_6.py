"""
Rule specification for AGREE-II item 6.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
    TargetUserRule,
)


_TARGET_USER_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Verify only whether the quote can serve as candidate evidence for item 6. Do not decide the final score. "
        "The engine will aggregate accepted objects across the full document and decide whether the current "
        "guideline defines intended users, intended use, both, or neither."
    ),
)


ITEM_6_SPEC = ItemSpec(
    item_id="item_6",
    title="The target users of the guideline are clearly defined.",
    rule_profile="target_user",
    object_specs={
        "vague_target_user": RuleObjectSpec(
            type="vague_target_user",
            role="component",
            description="模糊提及指南使用者或受众，如 clinicians、patients、managers 等宽泛类别，但未具体说明专业、角色或场景。",
            max_objects=4,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "clear_target_user": RuleObjectSpec(
            type="clear_target_user",
            role="component",
            description="清晰定义本指南使用者或受众，例如具体专业人员、科室、患者/照护者作为使用者、机构管理者或政策制定者。",
            max_objects=4,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "vague_intended_use": RuleObjectSpec(
            type="vague_intended_use",
            role="component",
            description="模糊说明本指南应如何使用，例如 support care、guide practice、help decision-making，但缺少具体用途。",
            max_objects=4,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "clear_intended_use": RuleObjectSpec(
            type="clear_intended_use",
            role="component",
            description="清晰说明本指南用途，例如用于临床决策、政策制定、质量标准、护理标准、服务管理或患者共同决策。",
            max_objects=4,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "target_population_or_disease_scope": RuleObjectSpec(
            type="target_population_or_disease_scope",
            role="exclusion",
            description="仅描述指南主题、疾病范围、目标患者或推荐适用患者人群，并未定义指南使用者或使用场景。",
            max_objects=3,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "author_or_developer_group": RuleObjectSpec(
            type="author_or_developer_group",
            role="exclusion",
            description="仅描述作者、制定组、发布机构、委员会或参与开发者，并非本指南的预期使用者。",
            max_objects=3,
            verification=_TARGET_USER_VERIFICATION,
        ),
        "ordinary_clinical_context": RuleObjectSpec(
            type="ordinary_clinical_context",
            role="exclusion",
            description="仅描述普通临床推荐、临床适用场景、治疗流程或护理场景，未说明指南作为文档面向谁使用或如何使用。",
            max_objects=3,
            verification=_TARGET_USER_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_6 评价指南是否明确规定 target users/intended users/audience，以及指南应如何使用。"
        "抽取当前指南面向的使用者，例如 healthcare professionals、endocrinologists、primary care clinicians、nurses、"
        "patients/carers as users、clinical or institutional leaders/managers、policy makers。"
        "同时抽取指南用途，例如 inform clinical decision-making、policy formulation、standards of care、quality improvement、"
        "guide practice、support shared decision-making。"
        "允许从 Abstract、Purpose、Scope、Introduction、Preface、Standards of Care 或 Position Statement "
        "等开头区域识别使用者和用途，只要文本确实把相关人群表述为本指南的使用者、读者或应用者。"
        "不要把疾病范围、目标患者人群、诊断标准、纳入/排除标准、推荐适用人群、作者/制定组、发布机构或普通临床适用场景"
        "误判为指南使用者；patients 只有被明确写成 intended users/readers/audience 或供患者使用时才算。"
        "遇到仅描述疾病/患者范围、作者/制定组或普通临床场景的文本，抽取对应 exclusion object，不要抽取 clear/vague target user。"
        "clear_* 需要具体角色或具体用途；vague_* 只用于宽泛、笼统表达。"
    ),
    verification_guidance=(
        "AGREE-II item 6 evaluates whether a reader can determine whether the current guideline is relevant to them "
        "as a user. The rubric has two dimensions: whether the current guideline defines its intended user, and "
        "whether it defines the intended use of the guideline. Object verification should judge whether the quote "
        "can support one of these dimensions, not whether it is sufficient for a final score."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "target audience",
            "intended audience",
            "intended users",
            "users",
            "audience",
            "intended use",
            "purpose",
            "scope",
            "abstract",
            "preface",
            "executive summary",
            "introduction",
            "methods",
            "standards of care",
            "position statement",
            "目标用户",
            "使用者",
            "适用者",
            "目的",
            "范围",
        ),
        include_phrases=(
            "target audience",
            "intended audience",
            "intended users",
            "target users",
            "intended use",
            "this guideline is intended for",
            "these guidelines are intended for",
            "for use by",
            "developed for",
            "standards of care are intended",
            "position statement is intended",
            "inform clinical decision-making",
            "guide clinical practice",
            "standards of care",
            "policy formulation",
            "shared decision-making",
            "指南使用者",
            "目标用户",
            "临床决策",
            "护理标准",
            "政策制定",
        ),
        include_terms=(
            "audience",
            "users",
            "use",
            "purpose",
            "scope",
            "abstract",
            "clinicians",
            "professionals",
            "patients",
            "managers",
            "policy",
            "decision-making",
            "使用者",
            "用户",
            "目的",
            "范围",
            "临床医生",
            "患者",
            "管理者",
        ),
        negative_terms=(
            "diagnostic criteria",
            "eligibility criteria",
            "inclusion criteria",
            "author group",
        ),
        neighbor_window=2,
        max_chunks=24,
        max_domain_chunks=10,
    ),
    rule=TargetUserRule(),
)
