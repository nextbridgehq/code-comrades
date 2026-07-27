"""Tests for the repository facts sheet (inspect_repo.py).

The facts sheet is the substrate every project skill builds on, so these
pin three properties above all: it never crashes on weird trees, it is
deterministic (same tree, byte-identical JSON), and it never lets
vendored directories skew what it reports about the project itself.
"""

import json
import os
import subprocess

import inspect_repo as ir


def write(root, relpath, content=""):
    """Write text content to a relative path and return the created Path."""
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_node_repo(tmp_path):
    """Populate a temporary directory with files simulating a typical Node.js project."""
    write(tmp_path, "package.json", json.dumps({
        "name": "acme-cli",
        "description": "Does acme things.",
        "version": "2.1.0",
        "license": "MIT",
        "engines": {"node": ">=18"},
        "bin": {"acme": "./bin/acme.js"},
        "scripts": {"test": "jest", "build": "tsc"},
        "dependencies": {"express": "^4.0.0"},
        "devDependencies": {"jest": "^29.0.0"},
    }))
    write(tmp_path, "package-lock.json", "{}")
    write(tmp_path, "src/index.js", "module.exports = 1;\n")
    write(tmp_path, ".github/workflows/ci.yml", "name: ci\n")
    write(tmp_path, "Dockerfile", "FROM node:18\n")
    return tmp_path


# --- resilience and determinism ---------------------------------------------

def test_empty_directory_produces_a_valid_sheet_not_a_crash(tmp_path):
    """Project skills run this unconditionally; an empty or minimal tree
    must yield a well-formed sheet with honest absences, not an error."""
    facts = ir.inspect(str(tmp_path))
    assert facts["schema"] == 1
    assert facts["languages"] == []
    assert facts["manifests"] == []
    assert facts["readme"] == {"exists": False}
    assert facts["git"]["is_repo"] is False


def test_output_is_deterministic_for_an_unchanged_tree(tmp_path):
    """Idempotency of every project skill rests on the facts sheet being
    byte-stable — sorted keys, sorted lists, no timestamps."""
    make_node_repo(tmp_path)
    first = json.dumps(ir.inspect(str(tmp_path)), sort_keys=True)
    second = json.dumps(ir.inspect(str(tmp_path)), sort_keys=True)
    assert first == second


def test_vendored_directories_do_not_skew_language_stats(tmp_path):
    """A node_modules full of JS must not make a Python project look like
    a JavaScript one — the sheet reuses the batch engine's excludes."""
    write(tmp_path, "app.py", "x = 1\n")
    for i in range(5):
        write(tmp_path, "node_modules/dep/f{}.js".format(i), "1;\n")
    langs = {l["language"]: l["files"] for l in ir.inspect(str(tmp_path))["languages"]}
    assert langs == {"Python": 1}


# --- metadata extraction -----------------------------------------------------

def test_node_repo_metadata_managers_ci_and_entry_points(tmp_path):
    """Verify metadata extraction from a standard Node.js repository structure."""
    make_node_repo(tmp_path)
    facts = ir.inspect(str(tmp_path))
    project = facts["project"]
    assert project["name"] == "acme-cli"
    assert project["description"] == "Does acme things."
    assert project["version"] == "2.1.0"
    assert project["license"] == "MIT"
    assert project["runtime_requirement"] == "node >=18"
    assert project["sources"]["name"] == "package.json"
    assert facts["package_managers"] == ["npm"]
    assert facts["entry_points"]["cli"] == ["acme"]
    assert "build" in facts["entry_points"]["npm_scripts"]
    assert facts["ci"][0]["system"] == "GitHub Actions"
    assert facts["ci"][0]["workflows"] == ["ci.yml"]
    assert facts["containers"]["dockerfiles"] == ["Dockerfile"]
    assert "Express" in facts["frameworks"] and "Jest" in facts["frameworks"]


def test_pyproject_metadata_and_poetry_lockfile(tmp_path):
    """Verify metadata extraction from a Python project using pyproject.toml and Poetry."""
    write(tmp_path, "pyproject.toml", "\n".join([
        "[project]",
        'name = "acme-py"',
        'description = "Python acme."',
        'version = "0.3.0"',
        'requires-python = ">=3.9"',
        'dependencies = ["fastapi>=0.100", "click"]',
        "",
        "[project.scripts]",
        'acme = "acme.cli:main"',
    ]))
    write(tmp_path, "poetry.lock", "")
    facts = ir.inspect(str(tmp_path))
    assert facts["project"]["name"] == "acme-py"
    assert facts["project"]["runtime_requirement"] == "python >=3.9"
    assert facts["package_managers"] == ["poetry"]
    assert facts["entry_points"]["cli"] == ["acme"]
    assert "FastAPI" in facts["frameworks"] and "Click" in facts["frameworks"]


def test_manifest_priority_first_writer_wins_and_directory_name_is_last_resort(tmp_path):
    """package.json outranks pyproject for shared fields (dict order defines
    priority); the directory name only fills a still-empty name."""
    write(tmp_path, "package.json", json.dumps({"name": "from-node"}))
    write(tmp_path, "pyproject.toml", '[project]\nname = "from-python"\n')
    facts = ir.inspect(str(tmp_path))
    assert facts["project"]["name"] == "from-node"
    bare = tmp_path / "bare-dir"
    bare.mkdir()
    facts = ir.inspect(str(bare))
    assert facts["project"]["name"] == "bare-dir"
    assert facts["project"]["sources"]["name"] == "directory name"


def test_license_detected_from_license_file_text_when_no_manifest_says(tmp_path):
    """Verify fallback license detection from standalone LICENSE files."""
    write(tmp_path, "LICENSE",
          "MIT License\n\nPermission is hereby granted, free of charge...")
    facts = ir.inspect(str(tmp_path))
    assert facts["project"]["license"] == "MIT"
    assert facts["project"]["sources"]["license"] == "LICENSE"


def test_toml_scalar_fallback_reads_only_the_named_section():
    """Verify TOML parsing strictly scopes scalar retrieval to requested sections."""
    text = '[tool.other]\nname = "wrong"\n[package]\nname = "right"\n'
    assert ir.toml_scalar_fallback(text, "package", "name") == "right"
    assert ir.toml_scalar_fallback(text, "missing", "name") is None


# --- git identity ------------------------------------------------------------

def test_parse_remote_url_https_ssh_and_garbage():
    """Verify Git remote URL parsing across common SSH, HTTPS, and invalid formats."""
    for url in ("https://github.com/acme/tool.git",
                "https://github.com/acme/tool",
                "git@github.com:acme/tool.git",
                "ssh://git@github.com/acme/tool.git"):
        parsed = ir.parse_remote_url(url)
        assert parsed == {"host": "github.com", "owner": "acme",
                          "repo": "tool", "slug": "acme/tool"}, url
    assert ir.parse_remote_url("not a url") is None
    assert ir.parse_remote_url(None) is None


def test_git_facts_from_a_real_local_repo(tmp_path):
    """Verify extraction of Git remote information from a live local repository."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "git@github.com:acme/widget.git"], cwd=tmp_path, check=True)
    facts = ir.git_facts(str(tmp_path))
    assert facts["is_repo"] is True
    assert facts["remote"]["slug"] == "acme/widget"


# --- README structural scan --------------------------------------------------

def test_readme_scan_headings_badges_commands_and_version_claims(tmp_path):
    """Verify structured data extraction from README markdown files."""
    write(tmp_path, "README.md", "\n".join([
        "# Acme",
        "![v](https://img.shields.io/github/v/release/acme/tool)",
        "Requires Python 3.9 or newer.",
        "## Install",
        "```bash",
        "pip install acme",
        "# a comment inside the fence",
        "```",
        "## Usage",
    ]))
    readme = ir.inspect(str(tmp_path))["readme"]
    assert readme["exists"] is True
    assert "# Acme" in readme["headings"] and "## Install" in readme["headings"]
    assert readme["badge_slugs"] == ["acme/tool"]
    assert readme["fenced_commands"] == ["pip"]
    assert readme["version_claims"] == ["Python 3.9"]
    # Fence content must never be mistaken for headings.
    assert all("comment" not in h for h in readme["headings"])


def test_cli_exit_codes(tmp_path, capsys):
    """Verify CLI exit codes for valid and invalid target paths."""
    assert ir.main(["--path", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == 1
    assert ir.main(["--path", str(tmp_path / "nope")]) == 2


def test_documentation_files_discovery(tmp_path):
    (tmp_path / "USAGE.md").write_text("# Usage\n\nRun the app.", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "api.md").write_text("## API\nDetails.", encoding="utf-8")
    
    facts = ir.inspect(str(tmp_path))
    
    docs = facts.get("documentation_files", [])
    assert len(docs) == 2
    paths = {d["path"] for d in docs}
    assert "USAGE.md" in paths
    assert "docs/api.md" in paths
    
    usage_doc = next(d for d in docs if d["path"] == "USAGE.md")
    assert usage_doc["headings"] == ["Usage"]
    assert usage_doc["lines"] == 3


def test_custom_manifests_detection(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "test-plugin", "description": "A test plugin"}', encoding="utf-8")
    
    facts = ir.inspect(str(tmp_path))
    
    manifests = facts.get("custom_manifests", [])
    assert len(manifests) == 1
    m = manifests[0]
    assert m["path"] == ".claude-plugin/plugin.json"
    assert m["type"] == "json"
    assert set(m["detected_fields"]) == {"name", "description"}

def test_custom_manifests_yaml_toml_detection(tmp_path):
    (tmp_path / "skill.yaml").write_text("name: test-skill\ndescription: \n  - A multiline\n  - desc\ncommands:\n  run: echo 1", encoding="utf-8")
    (tmp_path / "plugin.toml").write_text("name = 'toml-plugin'\nversion='1.0'\nauthor = 'me'", encoding="utf-8")
    
    facts = ir.inspect(str(tmp_path))
    manifests = facts.get("custom_manifests", [])
    assert len(manifests) == 2
    
    plugin = next(m for m in manifests if m["path"] == "plugin.toml")
    assert plugin["type"] == "toml"
    assert set(plugin["detected_fields"]) == {"name", "version", "author"}
    
    skill = next(m for m in manifests if m["path"] == "skill.yaml")
    assert skill["type"] == "yaml"
    assert set(skill["detected_fields"]) == {"name", "description", "commands"}
