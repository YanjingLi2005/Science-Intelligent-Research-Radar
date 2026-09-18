"""Reviewer personas used by the rebuttal arena."""

REVIEWER_PERSONAS = [
    {
        "id": "method_strict",
        "label": "理论/方法严苛型",
        "description": "重点检查理论假设、方法设计、识别策略、因果链条与论证是否严密。",
    },
    {
        "id": "innovation_skeptic",
        "label": "创新质疑型",
        "description": "重点质疑真正的新颖性、与既有工作的边界、贡献是否被夸大以及先前研究覆盖。",
    },
    {
        "id": "experiment_generalization",
        "label": "实验泛化型",
        "description": "重点检查实验充分性、基线与消融、公平性、统计可信度和跨数据集泛化能力。",
    },
    {
        "id": "ac_arbiter",
        "label": "AC 仲裁者",
        "description": "站在领域主席角度综合判断贡献、证据、风险与接收建议，关注审稿意见能否被回应。",
    },
]

REVIEWER_PERSONAS_BY_ID = {persona["id"]: persona for persona in REVIEWER_PERSONAS}
