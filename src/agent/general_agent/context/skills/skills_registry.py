"""Skills registry - load and list available skills."""

from pathlib import Path
from typing import Optional, List

SKILLS_DIR = Path(__file__).parent


def load_skill(skill_name: str) -> Optional[str]:
    """
    Load skill content by name.

    Args:
        skill_name: Name of the skill (without .md extension)

    Returns:
        Skill content as string, or None if skill not found
    """
    skill_path = SKILLS_DIR / f"{skill_name}.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return None


def load_triggers() -> Optional[str]:
    """
    Load trigger conditions from _triggers.md.

    Returns:
        Trigger conditions content as string, or None if not found
    """
    triggers_path = SKILLS_DIR / "_triggers.md"
    if triggers_path.exists():
        return triggers_path.read_text(encoding="utf-8").strip()
    return None


def list_available_skills() -> List[str]:
    """
    List all available skill names.
    Files prefixed with _ are metadata files, not skills.

    Returns:
        List of skill names (without .md extension)
    """
    return sorted([
        f.stem
        for f in SKILLS_DIR.glob("*.md")
        if f.is_file() and not f.name.startswith("_")
    ])
