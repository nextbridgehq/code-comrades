#!/usr/bin/env python3
"""Deterministic repository intelligence for code-comrades project skills.

Walks a repository once and emits a single JSON "facts sheet": languages,
manifests, package managers, frameworks, entry points, CI, containers,
documentation files, git identity, and a light structural scan of any
existing README.

Design rule (mirrors discover_files.py): everything deterministic lives
here; everything requiring judgment lives in the consuming skill's
SKILL.md. This script never edits anything, never touches the network,
and produces byte-identical output for an unchanged tree, so project
skills built on it inherit idempotency for free.

Compatible with Python 3.8+ (stdlib only). TOML parsing uses tomllib on
3.11+ and falls back to a line-oriented extraction of the handful of
fields we need on older interpreters — best-effort, flagged in output.
"""
import argparse
import json
import os
import re
import subprocess
import sys

# Shared substrate: reuse the batch engine's exclusion vocabulary so both
# execution models agree on what "the repository" means.
try:
    from discover_files import (
        CODE_EXTENSIONS,
        DEFAULT_EXCLUDE_DIRS,
        DEFAULT_EXCLUDE_DIR_GLOBS,
    )
except ImportError:  # pragma: no cover - direct invocation from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from discover_files import (
        CODE_EXTENSIONS,
        DEFAULT_EXCLUDE_DIRS,
        DEFAULT_EXCLUDE_DIR_GLOBS,
    )

import fnmatch

EXT_TO_LANGUAGE = {
    "py": "Python", "js": "JavaScript", "jsx": "JavaScript",
    "mjs": "JavaScript", "cjs": "JavaScript", "ts": "TypeScript",
    "tsx": "TypeScript", "go": "Go", "rs": "Rust", "java": "Java",
    "kt": "Kotlin", "kts": "Kotlin", "rb": "Ruby", "php": "PHP",
    "swift": "Swift", "c": "C", "h": "C", "cpp": "C++", "cc": "C++",
    "hpp": "C++", "cs": "C#", "scala": "Scala", "sc": "Scala",
    "ex": "Elixir", "exs": "Elixir", "hs": "Haskell", "lua": "Lua",
    "r": "R", "R": "R", "sh": "Shell", "bash": "Shell", "zsh": "Shell",
    "dart": "Dart", "zig": "Zig", "ml": "OCaml", "mli": "OCaml",
    "pl": "Perl", "pm": "Perl",
}

# Manifest filename -> (ecosystem, parser key). Root-level only by design:
# monorepo sub-packages are out of scope for a repo-level facts sheet.
MANIFESTS = {
    "package.json": ("node", "package_json"),
    "pyproject.toml": ("python", "pyproject"),
    "setup.py": ("python", None),
    "setup.cfg": ("python", None),
    "requirements.txt": ("python", "requirements"),
    "Cargo.toml": ("rust", "cargo"),
    "go.mod": ("go", "go_mod"),
    "pom.xml": ("java", None),
    "build.gradle": ("java", None),
    "build.gradle.kts": ("java", None),
    "composer.json": ("php", "composer_json"),
    "Gemfile": ("ruby", None),
    "pubspec.yaml": ("dart", "pubspec"),
    "mix.exs": ("elixir", None),
    "Makefile": ("make", None),
    "Taskfile.yml": ("task", None),
    "Taskfile.yaml": ("task", None),
    "CMakeLists.txt": ("cmake", None),
}

LOCKFILE_TO_MANAGER = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "bun",
    "bun.lock": "bun",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "Pipfile.lock": "pipenv",
    "Cargo.lock": "cargo",
    "go.sum": "go modules",
    "composer.lock": "composer",
    "Gemfile.lock": "bundler",
    "pubspec.lock": "pub",
    "mix.lock": "mix",
}

# Dependency name -> framework label, checked against every parsed
# dependency list. Deliberately coarse: signals, not a taxonomy.
DEP_TO_FRAMEWORK = {
    "react": "React", "next": "Next.js", "vue": "Vue", "nuxt": "Nuxt",
    "svelte": "Svelte", "@angular/core": "Angular", "express": "Express",
    "fastify": "Fastify", "koa": "Koa", "nestjs": "NestJS",
    "@nestjs/core": "NestJS", "electron": "Electron", "vite": "Vite",
    "django": "Django", "fastapi": "FastAPI", "flask": "Flask",
    "pytest": "pytest", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "pandas": "pandas", "typer": "Typer", "click": "Click",
    "actix-web": "Actix Web", "axum": "Axum", "tokio": "Tokio",
    "rocket": "Rocket", "gin": "Gin", "echo": "Echo", "laravel": "Laravel",
    "rails": "Rails", "flutter": "Flutter", "jest": "Jest",
    "vitest": "Vitest", "playwright": "Playwright",
    "@playwright/test": "Playwright", "tailwindcss": "Tailwind CSS",
}

CI_LOCATIONS = [
    (".github/workflows", "GitHub Actions"),
    (".gitlab-ci.yml", "GitLab CI"),
    (".circleci", "CircleCI"),
    ("Jenkinsfile", "Jenkins"),
    ("azure-pipelines.yml", "Azure Pipelines"),
    (".travis.yml", "Travis CI"),
    ("bitbucket-pipelines.yml", "Bitbucket Pipelines"),
]

DOC_FILES = [
    "LICENSE", "LICENSE.md", "LICENSE.txt", "CONTRIBUTING.md",
    "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "SUPPORT.md",
    "CITATION.cff",
]

LICENSE_SIGNATURES = [
    ("MIT License", "MIT"),
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Apache License", "Apache-2.0"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"),
    ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL"),
    ("Mozilla Public License", "MPL-2.0"),
    ("BSD 3-Clause", "BSD-3-Clause"),
    ("BSD 2-Clause", "BSD-2-Clause"),
    ("The Unlicense", "Unlicense"),
    ("ISC License", "ISC"),
]

BADGE_PATH_RE = re.compile(r"img\.shields\.io/github/([^)\s\"'?#]+)")


def badge_slugs(text):
    """Extract owner/repo slugs from shields.io GitHub badge URLs.

    The slug is the trailing pair of path segments (`github/v/release/
    acme/tool` -> `acme/tool`); a trailing workflow filename (`.../status/
    acme/tool/ci.yml`) is dropped first. Heuristic by design — the
    consuming skill treats these as candidates, not ground truth.

    Args:
        text: The markdown or HTML text containing badge URLs.

    Returns:
        A sorted list of unique owner/repo string slugs found in the text.
    """
    slugs = set()
    for path in BADGE_PATH_RE.findall(text):
        segments = [s for s in path.split("/") if s]
        if segments and re.search(r"\.(ya?ml|svg|json)$", segments[-1]):
            segments = segments[:-1]
        if len(segments) >= 2:
            slugs.add("/".join(segments[-2:]))
    return sorted(slugs)
FENCE_OPEN_RE = re.compile(r"^```(\w*)\s*$")


def iter_markdown_lines(text):
    """Yield (in_fence, line) for each line in markdown text."""
    in_fence = False
    for line in text.splitlines():
        if FENCE_OPEN_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        yield in_fence, line


# --------------------------------------------------------------------------
# small helpers

def read_text(path, limit=200_000):
    """Read a text file defensively; inspection must never crash the run.

    Args:
        path: Filepath to read.
        limit: Maximum characters to read to prevent memory exhaustion.

    Returns:
        The file contents as a string, or None if an OS error occurs.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def load_json(path):
    """Load and parse a JSON file.

    Args:
        path: Filepath to the JSON file.

    Returns:
        The parsed JSON dictionary, or None if reading/parsing fails.
    """
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def load_toml(path):
    """Parse TOML via tomllib (3.11+); return (data, exact) or (None, False).

    Args:
        path: Filepath to the TOML file.

    Returns:
        A tuple of (parsed_dict, is_exact_parse) or (None, False) on failure.
    """
    try:
        import tomllib  # noqa: PLC0415 - optional stdlib module
    except ImportError:
        return None, False
    try:
        with open(path, "rb") as f:
            return tomllib.load(f), True
    except Exception:
        return None, False


def toml_scalar_fallback(text, section, key):
    """Line-oriented single-scalar extraction for Python <3.11.

    Finds `key = "value"` inside `[section]`. Good enough for the name/
    version/description fields we need; anything richer requires tomllib.

    Args:
        text: Raw TOML text.
        section: TOML bracket block name (e.g., "project").
        key: Configuration key to extract.

    Returns:
        The extracted string value without quotes, or None if not found.
    """
    in_section = False
    pattern = re.compile(r'^\s*' + re.escape(key) + r'\s*=\s*"(.*?)"')
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[{}]".format(section)
            continue
        if in_section:
            match = pattern.match(line)
            if match:
                return match.group(1)
    return None


def set_meta(project, field, value, source):
    """First writer wins: manifests are consulted in priority order.

    Args:
        project: Project metadata dictionary to mutate.
        field: Metadata key to set.
        value: Value to assign if truthy and currently unset.
        source: Filename supplying this value.
    """
    if value and not project.get(field):
        project[field] = value
        project["sources"][field] = source


# --------------------------------------------------------------------------
# collectors

def scan_tree(root):
    """One walk: language counts + presence flags for a few directory names.

    Honors the batch engine's exclude vocabulary so vendored trees never
    skew language stats, and skips symlinked directories to avoid cycles.
    Also discovers markdown documentation files.

    Args:
        root: Repository root directory to walk.

    Returns:
        A tuple of (languages_list, notable_dirs_dict, doc_files_list).
    """
    counts = {}
    total = 0
    notable = {"tests": False, "docs": False, "examples": False,
               "benchmarks": False, "scripts": False, "src": False}
    doc_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in DEFAULT_EXCLUDE_DIRS
            and not any(fnmatch.fnmatch(d, g) for g in DEFAULT_EXCLUDE_DIR_GLOBS)
            and not os.path.islink(os.path.join(dirpath, d))
        )
        if dirpath == root:
            for d in dirnames:
                key = d.lower().rstrip("s") + "s" if d.lower() in (
                    "test", "doc", "example", "benchmark", "script") else d.lower()
                if key in notable:
                    notable[key] = True
                if d.lower() == "test":
                    notable["tests"] = True
        for name in filenames:
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            lang = EXT_TO_LANGUAGE.get(ext)
            if ext in CODE_EXTENSIONS and lang:
                counts[lang] = counts.get(lang, 0) + 1
                total += 1
            if name.lower().endswith(".md"):
                path = os.path.join(dirpath, name)
                relpath = os.path.relpath(path, root)
                if os.name == 'nt':
                    relpath = relpath.replace('\\', '/')
                text = read_text(path, 400_000)
                if text is not None:
                    lines = text.splitlines()
                    headings = []
                    for in_fence, line in iter_markdown_lines(text):
                        if not in_fence and line.startswith("#"):
                            headings.append(line.lstrip("#").strip())
                    doc_files.append({
                        "path": relpath,
                        "lines": len(lines),
                        "headings": headings
                    })
    languages = [
        {"language": lang, "files": n,
         "share": round(n / total, 3) if total else 0.0}
        for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    doc_files.sort(key=lambda x: x["path"])
    return languages, notable, doc_files


def collect_manifests(root, project, deps, entry_points, notes):
    """Detect root-level manifests and pull metadata in priority order.

    Args:
        root: Repository root directory.
        project: Metadata dictionary to populate.
        deps: Set to collect dependencies.
        entry_points: Dictionary to collect CLI and main entry points.
        notes: Dictionary to capture parsing limits or package manager pins.

    Returns:
        List of manifest filenames found.
    """
    found = []
    for filename in MANIFESTS:
        path = os.path.join(root, filename)
        if not os.path.isfile(path):
            continue
        found.append(filename)
        parser = MANIFESTS[filename][1]
        if parser == "package_json":
            data = load_json(path) or {}
            set_meta(project, "name", data.get("name"), filename)
            set_meta(project, "description", data.get("description"), filename)
            set_meta(project, "version", data.get("version"), filename)
            set_meta(project, "license", data.get("license"), filename)
            if isinstance(data.get("engines"), dict):
                node_req = data["engines"].get("node")
                set_meta(project, "runtime_requirement",
                         "node {}".format(node_req) if node_req else None,
                         filename)
            pm = data.get("packageManager")
            if isinstance(pm, str) and pm:
                notes.setdefault("package_manager_pins", []).append(pm)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key].keys())
            if isinstance(data.get("bin"), dict):
                entry_points["cli"] = sorted(data["bin"].keys())
            elif isinstance(data.get("bin"), str):
                entry_points["cli"] = [data.get("name") or "bin"]
            if data.get("main"):
                entry_points["main"] = data["main"]
            if isinstance(data.get("scripts"), dict):
                entry_points["npm_scripts"] = sorted(data["scripts"].keys())
        elif parser == "pyproject":
            data, exact = load_toml(path)
            if exact and isinstance(data, dict):
                proj = data.get("project", {}) or {}
                set_meta(project, "name", proj.get("name"), filename)
                set_meta(project, "description", proj.get("description"), filename)
                set_meta(project, "version", proj.get("version"), filename)
                lic = proj.get("license")
                if isinstance(lic, dict):
                    lic = lic.get("text") or lic.get("file")
                set_meta(project, "license", lic, filename)
                set_meta(project, "runtime_requirement",
                         "python {}".format(proj["requires-python"])
                         if proj.get("requires-python") else None, filename)
                for dep in proj.get("dependencies", []) or []:
                    deps.add(re.split(r"[<>=!~\[; ]", dep, 1)[0].lower())
                scripts = data.get("project", {}).get("scripts") or {}
                if scripts:
                    entry_points["cli"] = sorted(scripts.keys())
                poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
                set_meta(project, "name", poetry.get("name"), filename)
                set_meta(project, "description", poetry.get("description"), filename)
                set_meta(project, "version", poetry.get("version"), filename)
            else:
                text = read_text(path) or ""
                set_meta(project, "name",
                         toml_scalar_fallback(text, "project", "name"), filename)
                set_meta(project, "description",
                         toml_scalar_fallback(text, "project", "description"),
                         filename)
                set_meta(project, "version",
                         toml_scalar_fallback(text, "project", "version"), filename)
                req = toml_scalar_fallback(text, "project", "requires-python")
                set_meta(project, "runtime_requirement",
                         "python {}".format(req) if req else None, filename)
                notes.setdefault("parse_limits", []).append(
                    "pyproject.toml parsed best-effort (tomllib unavailable)")
        elif parser == "requirements":
            for line in (read_text(path) or "").splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "-")):
                    deps.add(re.split(r"[<>=!~\[; ]", line, 1)[0].lower())
        elif parser == "cargo":
            data, exact = load_toml(path)
            if exact and isinstance(data, dict):
                pkg = data.get("package", {}) or {}
                set_meta(project, "name", pkg.get("name"), filename)
                set_meta(project, "description", pkg.get("description"), filename)
                set_meta(project, "version", pkg.get("version"), filename)
                set_meta(project, "license", pkg.get("license"), filename)
                set_meta(project, "runtime_requirement",
                         "rust {}".format(pkg["rust-version"])
                         if pkg.get("rust-version") else None, filename)
                deps.update(k.lower() for k in (data.get("dependencies") or {}))
                bins = data.get("bin")
                if isinstance(bins, list):
                    names = [b.get("name") for b in bins if isinstance(b, dict)]
                    if any(names):
                        entry_points["cli"] = sorted(n for n in names if n)
            else:
                text = read_text(path) or ""
                set_meta(project, "name",
                         toml_scalar_fallback(text, "package", "name"), filename)
                set_meta(project, "description",
                         toml_scalar_fallback(text, "package", "description"),
                         filename)
                set_meta(project, "version",
                         toml_scalar_fallback(text, "package", "version"), filename)
        elif parser == "go_mod":
            text = read_text(path) or ""
            match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
            if match:
                set_meta(project, "name", match.group(1).rsplit("/", 1)[-1],
                         filename)
                entry_points["go_module"] = match.group(1)
            match = re.search(r"^go\s+([\d.]+)", text, re.MULTILINE)
            set_meta(project, "runtime_requirement",
                     "go {}".format(match.group(1)) if match else None, filename)
        elif parser == "composer_json":
            data = load_json(path) or {}
            set_meta(project, "name", data.get("name"), filename)
            set_meta(project, "description", data.get("description"), filename)
            set_meta(project, "license", data.get("license"), filename)
            if isinstance(data.get("require"), dict):
                deps.update(k.lower() for k in data["require"])
        elif parser == "pubspec":
            text = read_text(path) or ""
            for field in ("name", "description", "version"):
                match = re.search(r"^{}:\s*(.+)$".format(field), text, re.MULTILINE)
                set_meta(project, field,
                         match.group(1).strip().strip("\"'") if match else None,
                         filename)
            if re.search(r"^\s{2}flutter:", text, re.MULTILINE):
                deps.add("flutter")
    return found


def detect_package_managers(root):
    """Detect used package managers based on lockfiles present.

    Args:
        root: Repository root directory.

    Returns:
        Sorted list of package manager names (e.g., 'npm', 'cargo').
    """
    return sorted({mgr for lock, mgr in LOCKFILE_TO_MANAGER.items()
                   if os.path.isfile(os.path.join(root, lock))})


def detect_ci(root):
    """Identify Continuous Integration systems configured in the repository.

    Args:
        root: Repository root directory.

    Returns:
        List of dictionaries with 'system', 'path', and sometimes 'workflows'.
    """
    found = []
    for location, label in CI_LOCATIONS:
        path = os.path.join(root, location)
        if not os.path.exists(path):
            continue
        entry = {"system": label, "path": location}
        if label == "GitHub Actions" and os.path.isdir(path):
            entry["workflows"] = sorted(
                f for f in os.listdir(path) if f.endswith((".yml", ".yaml")))
        found.append(entry)
    return found


def detect_containers(root):
    """Discover Docker and devcontainer configuration files.

    Args:
        root: Repository root directory.

    Returns:
        Dict identifying dockerfiles, compose_files, and devcontainer presence.
    """
    dockerfiles = sorted(
        f for f in os.listdir(root)
        if f == "Dockerfile" or f.startswith("Dockerfile."))
    compose = sorted(
        f for f in os.listdir(root)
        if f in ("docker-compose.yml", "docker-compose.yaml",
                 "compose.yml", "compose.yaml"))
    return {
        "dockerfiles": dockerfiles,
        "compose_files": compose,
        "devcontainer": os.path.exists(os.path.join(root, ".devcontainer")),
    }


def detect_docs(root, project):
    """Identify documentation files and opportunistically extract license info.

    Args:
        root: Repository root directory.
        project: Project metadata dict (mutated to add 'license' if found).

    Returns:
        Dict indicating files present (issue_templates, pr_template, env_example).
    """
    docs = {"files": [], "env_example": False,
            "issue_templates": False, "pr_template": False}
    for filename in DOC_FILES:
        path = os.path.join(root, filename)
        if os.path.isfile(path):
            docs["files"].append(filename)
            if filename.startswith("LICENSE") and not project.get("license"):
                head = (read_text(path, 2_000) or "")
                for signature, spdx in LICENSE_SIGNATURES:
                    if signature.lower() in head.lower():
                        set_meta(project, "license", spdx, filename)
                        break
    docs["env_example"] = os.path.isfile(os.path.join(root, ".env.example"))
    docs["issue_templates"] = os.path.isdir(
        os.path.join(root, ".github", "ISSUE_TEMPLATE"))
    docs["pr_template"] = any(
        os.path.isfile(os.path.join(root, p)) for p in
        ("PULL_REQUEST_TEMPLATE.md",
         os.path.join(".github", "PULL_REQUEST_TEMPLATE.md")))
    return docs


def parse_remote_url(url):
    """Extract (host, owner, repo) from https:// or git@ remote forms.

    Args:
        url: Git remote URL string.

    Returns:
        Dict of host, owner, repo, and slug keys, or None if unparseable.
    """
    if not url:
        return None
    url = url.strip()
    match = re.match(r"^(?:https?://|git://)([^/]+)/([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not match:
        match = re.match(r"^(?:ssh://)?git@([^:/]+)[:/]([^/]+)/(.+?)(?:\.git)?$", url)
    if not match:
        return None
    host, owner, repo = match.groups()
    return {"host": host, "owner": owner, "repo": repo,
            "slug": "{}/{}".format(owner, repo)}


def git_facts(root):
    """Local-only git identity. Never touches the network.

    Args:
        root: Repository root directory.

    Returns:
        Dict with git metadata, or {"is_repo": False} if not a repository.
    """
    def run(*args):
        try:
            out = subprocess.run(
                ["git", "-C", root] + list(args),
                capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    if run("rev-parse", "--show-toplevel") is None:
        return {"is_repo": False}
    facts = {"is_repo": True}
    remote = run("remote", "get-url", "origin")
    facts["remote_url"] = remote
    facts["remote"] = parse_remote_url(remote)
    facts["default_branch"] = (
        (run("symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "")
        .replace("origin/", "") or run("rev-parse", "--abbrev-ref", "HEAD"))
    facts["latest_tag"] = run("describe", "--tags", "--abbrev=0")
    count = run("rev-list", "--count", "HEAD")
    facts["commit_count"] = int(count) if count and count.isdigit() else None
    return facts


def scan_readme(root):
    """Light structural scan of an existing README, for improve/sync modes.

    Extracts structure and machine-checkable claims (badge slugs, fenced
    commands, version-like statements). Judging whether a claim is stale
    is the consuming skill's job, not this script's.

    Args:
        root: Repository root directory.

    Returns:
        Dict detailing README structural findings, or {"exists": False}.
    """
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            break
    else:
        return {"exists": False}
    text = read_text(path, 400_000) or ""
    headings, commands = [], []
    for in_fence, line in iter_markdown_lines(text):
        if in_fence:
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "$ #")):
                commands.append(stripped.lstrip("$ ").split()[0]
                                if stripped.lstrip("$ ").split() else "")
        elif line.startswith("#"):
            headings.append(line.strip())
    version_claims = sorted(set(re.findall(
        r"\b(?:Python|Node(?:\.js)?|Go|Rust|Java|PHP|Ruby)\s*"
        r"(?:>=|<=|==|~|\^)?\s*v?\d+(?:\.\d+)*\+?", text)))
    return {
        "exists": True,
        "path": name,
        "bytes": os.path.getsize(path),
        "headings": headings[:60],
        "badge_slugs": badge_slugs(text),
        "fenced_commands": sorted({c for c in commands if c})[:40],
        "version_claims": version_claims,
        "has_toc": any("table of contents" in h.lower() or "toc" == h.lower().lstrip("# ")
                       for h in headings) or "<details>" in text[:4000],
        "has_images": bool(re.search(r"!\[[^\]]*\]\(|<img\s", text)),
    }


def scan_custom_manifests(root):
    """Scan root and one directory deep for potential custom manifests."""
    manifests = []
    target_keys = {"name", "description", "version", "author", "license", "commands", "permissions"}
    
    # Collect directories to search: root + depth 1
    search_dirs = [root]
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                d_name = entry.name
                if d_name not in DEFAULT_EXCLUDE_DIRS and not any(fnmatch.fnmatch(d_name, g) for g in DEFAULT_EXCLUDE_DIR_GLOBS):
                    search_dirs.append(entry.path)
    except OSError:
        pass

    for d in search_dirs:
        try:
            for entry in os.scandir(d):
                if entry.is_file(follow_symlinks=False):
                    name = entry.name
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext not in ("json", "yaml", "yml", "toml"):
                        continue
                    
                    path = entry.path
                    relpath = os.path.relpath(path, root)
                    if os.name == 'nt':
                        relpath = relpath.replace('\\', '/')
                        
                    text = read_text(path, 100_000)
                    if not text:
                        continue
                        
                    detected = []
                    m_type = "yaml" if ext in ("yaml", "yml") else ext
                    
                    if ext == "json":
                        try:
                            data = json.loads(text)
                            if isinstance(data, dict):
                                detected = sorted(list(target_keys.intersection(data.keys())))
                        except (json.JSONDecodeError, ValueError):
                            pass
                    else:
                        # Simple regex for yaml/toml parsing without dependencies
                        pattern = r'^\s*(name|description|version|author|license|commands|permissions)\s*[:=]'
                        matches = re.findall(pattern, text, re.MULTILINE)
                        if matches:
                            detected = sorted(list(set(matches)))
                            
                    if detected:
                        manifests.append({
                            "path": relpath,
                            "type": m_type,
                            "detected_fields": detected
                        })
        except OSError:
            continue
            
    # Sort deterministically
    manifests.sort(key=lambda x: x["path"])
    return manifests


def inspect(root):
    """Generate a comprehensive JSON facts sheet for a repository.

    Args:
        root: Repository root directory.

    Returns:
        A dictionary representing the repository facts sheet.
    """
    project = {"name": None, "description": None, "version": None,
               "license": None, "runtime_requirement": None, "sources": {}}
    deps, entry_points, notes = set(), {}, {}
    languages, notable_dirs, documentation_files = scan_tree(root)
    manifests = collect_manifests(root, project, deps, entry_points, notes)
    frameworks = sorted({label for dep, label in DEP_TO_FRAMEWORK.items()
                         if dep in deps})
    facts = {
        "schema": 1,
        "root": os.path.abspath(root),
        "project": project,
        "languages": languages,
        "manifests": sorted(manifests),
        "package_managers": detect_package_managers(root),
        "frameworks": frameworks,
        "entry_points": entry_points,
        "ci": detect_ci(root),
        "containers": detect_containers(root),
        "docs": detect_docs(root, project),
        "structure": notable_dirs,
        "git": git_facts(root),
        "readme": scan_readme(root),
        "documentation_files": documentation_files,
        "custom_manifests": scan_custom_manifests(root),
    }
    if notes:
        facts["notes"] = notes
    # Fall back to the directory name only after every manifest had its say.
    set_meta(project, "name", os.path.basename(os.path.abspath(root)),
             "directory name")
    return facts


def main(argv=None):
    """Parse CLI arguments and execute the repository inspection.

    Args:
        argv: Optional list of command-line arguments.

    Returns:
        Integer exit code (0 for success, 2 for invalid arguments).
    """
    parser = argparse.ArgumentParser(
        description="Emit a JSON facts sheet for a repository.")
    parser.add_argument("--path", default=".", help="repository root")
    parser.add_argument("--pretty", action="store_true",
                        help="indent output for humans")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.path):
        print("inspect_repo: {} is not a directory".format(args.path),
              file=sys.stderr)
        return 2
    facts = inspect(args.path)
    print(json.dumps(facts, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
