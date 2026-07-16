import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOALS = {
    "/goal Исследование": ("1c-autoresearch-research-goal", "research-goal-router.md"),
    "/goal Параллельное исследование": ("1c-autoresearch-parallel-research-goal", "parallel-research-goal.md"),
    "/goal Подготовь ревью": ("1c-autoresearch-review-preparation-goal", "research-review-preparation.md"),
    "/goal Ручная разметка": ("1c-autoresearch-manual-markup-goal", "manual-markup-goal.md"),
    "/goal Карта разрывов": ("1c-autoresearch-functional-gap-goal", "functional-gap-goal.md"),
}


def test_every_goal_has_skill_method_and_index_entry():
    index = (ROOT / "docs/agent/index.md").read_text(encoding="utf-8")
    for goal, (skill, method) in GOALS.items():
        assert goal in index
        assert (ROOT / ".agents/skills" / skill / "SKILL.md").is_file()
        assert (ROOT / "docs/method" / method).is_file()


def test_agent_links_and_onboarding_roots_are_current():
    files = list((ROOT / "docs/agent").glob("*.md")) + list((ROOT / ".agents/skills").glob("*/SKILL.md"))
    for path in files:
        for target in re.findall(r"\[[^]]*\]\(([^)#]+)", path.read_text(encoding="utf-8")):
            if "://" not in target:
                assert (path.parent / target).resolve().exists(), f"broken link in {path}: {target}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for root in ("analysis/customization-registry/", "analysis/migration-requirements/", "analysis/parallel-research/"):
        assert root in readme


def test_manual_model_is_explicit():
    assert "gpt-5.4-mini" not in (ROOT / "docs/method/manual-markup-goal.md").read_text(encoding="utf-8")
    assert "CODEX_MANUAL_MODEL" in (ROOT / "src/one_c_autoresearch/manual_cleanup_commands/manual_cleanup_codex_exec.py").read_text(encoding="utf-8")
