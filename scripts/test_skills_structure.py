"""Test suite for validating the structure and configuration of skill packages.

Ensures all skills within the code-comrades repository adhere to required
layout and metadata constraints, such as valid SKILL.md frontmatter and
correct batch/project YAML configurations.
"""

import os
import pytest
import yaml

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

def get_skills():
    """Discover all skill directories within the repository's skills directory.

    Returns:
        A list of string names for each valid skill directory found.
    """
    if not os.path.isdir(SKILLS_DIR):
        return []
    skills = []
    for d in os.listdir(SKILLS_DIR):
        if os.path.isdir(os.path.join(SKILLS_DIR, d)):
            skills.append(d)
    return skills

@pytest.mark.parametrize("skill_name", get_skills())
def test_skill_structure(skill_name):
    """Validate the directory structure and metadata files of a single skill.

    Checks for the presence of SKILL.md with valid YAML frontmatter matching
    the directory name. Also ensures mutually exclusive configuration files
    (batch.yaml or project.yaml) are well-formed and reference the correct skill.

    Args:
        skill_name: The directory name of the skill being tested.
    """
    skill_dir = os.path.join(SKILLS_DIR, skill_name)
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    
    # 1. SKILL.md must exist
    assert os.path.isfile(skill_md_path), f"{skill_name} is missing SKILL.md"
    
    # 2. Extract YAML frontmatter
    with open(skill_md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    parts = content.split("---")
    assert len(parts) >= 3, f"{skill_name} SKILL.md must contain YAML frontmatter surrounded by ---"
    
    frontmatter_content = parts[1]
    try:
        frontmatter = yaml.safe_load(frontmatter_content)
    except yaml.YAMLError as e:
        pytest.fail(f"{skill_name} frontmatter is invalid YAML: {e}")
        
    assert isinstance(frontmatter, dict), f"{skill_name} frontmatter must be a dictionary"
    
    # 3. Frontmatter must contain 'name' and 'description'
    assert "name" in frontmatter, f"{skill_name} frontmatter missing 'name'"
    assert "description" in frontmatter, f"{skill_name} frontmatter missing 'description'"
    
    # 4. Frontmatter 'name' must match directory name
    assert frontmatter["name"] == skill_name, f"{skill_name} frontmatter name '{frontmatter['name']}' does not match directory name"

    # 5. Configuration check (batch.yaml or project.yaml)
    batch_yaml_path = os.path.join(skill_dir, "batch.yaml")
    project_yaml_path = os.path.join(skill_dir, "project.yaml")
    
    has_batch = os.path.isfile(batch_yaml_path)
    has_project = os.path.isfile(project_yaml_path)
    
    assert not (has_batch and has_project), f"{skill_name} cannot have both batch.yaml and project.yaml"
    
    if has_batch:
        with open(batch_yaml_path, "r", encoding="utf-8") as f:
            try:
                batch_config = yaml.safe_load(f)
            except yaml.YAMLError as e:
                pytest.fail(f"{skill_name} batch.yaml is invalid YAML: {e}")
        assert isinstance(batch_config, dict), f"{skill_name} batch.yaml must be a dict"
        assert batch_config.get("skill") == skill_name, f"{skill_name} batch.yaml 'skill' must match directory name"
        assert "extensions" in batch_config, f"{skill_name} batch.yaml must declare 'extensions'"

    if has_project:
        with open(project_yaml_path, "r", encoding="utf-8") as f:
            try:
                project_config = yaml.safe_load(f)
            except yaml.YAMLError as e:
                pytest.fail(f"{skill_name} project.yaml is invalid YAML: {e}")
        assert isinstance(project_config, dict), f"{skill_name} project.yaml must be a dict"
        assert project_config.get("skill") == skill_name, f"{skill_name} project.yaml 'skill' must match directory name"
        assert project_config.get("execution") == "project", f"{skill_name} project.yaml must have 'execution: project'"
