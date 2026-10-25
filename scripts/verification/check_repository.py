"""Offline release checks for source, config, pinned bytes and documentation.

No source downloads, database access, schema creation or ingestion. Runtime
artifact references in Markdown must use inline code, since those paths are
absent from a source-only GitHub checkout.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]


def check_python(root: Path) -> int:
    files = [path for folder in ("scripts", "tests") for path in (root / folder).rglob("*.py")]
    for path in files:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path.relative_to(root)))
    return len(files)


def check_config(root: Path) -> int:
    files = sorted((root / "config").glob("*.json"))
    if not files:
        raise ValueError("No configuration JSON files found")
    for path in files:
        if not isinstance(json.loads(path.read_text(encoding="utf-8-sig")), dict):
            raise ValueError("Configuration must be a JSON object: " + path.name)
    return len(files)


def check_pins(root: Path) -> int:
    source = root / "scripts/orchestration/guarded_preparation_executor.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "M4_PINNED_SHA256" for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError("Exactly one M4_PINNED_SHA256 definition is required")
    pins = ast.literal_eval(assignments[0].value)
    count = 0
    for stage in pins.values():
        for relative, expected in stage:
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
                raise ValueError("Unsafe pinned source path: " + relative)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Pinned M4 source bytes changed: " + relative)
            count += 1
    if count != 6:
        raise ValueError("Expected exactly six pinned M4 source files")
    return count


def check_markdown(root: Path) -> int:
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md")),
             *sorted((root / ".github").rglob("*.md"))]
    errors = []
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        # A link inside a fenced example is not a document navigation link.
        text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", text)
        for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+['\"][^)]*)?\)", text):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            relative = unquote(parsed.path)
            destination = path.parent / relative
            if not destination.resolve().is_relative_to(root.resolve()):
                errors.append(f"{path.relative_to(root)}: link escapes repository: {target}")
            elif destination.resolve().relative_to(root.resolve()).parts[:1] in (
                    ("reports",), ("raw-archive-v2",), ("raw-data",), ("venv",)):
                errors.append(f"{path.relative_to(root)}: runtime artifact must use inline code: {target}")
            elif not destination.exists():
                errors.append(f"{path.relative_to(root)}: missing link target: {target}")
    if errors:
        raise ValueError("\n".join(errors))
    return len(files)


def check_workflows(root: Path) -> int:
    import yaml

    class WorkflowLoader(yaml.SafeLoader):
        # GitHub uses YAML where 'on' is a mapping key, not YAML 1.1 boolean.
        yaml_implicit_resolvers = {
            key: [item for item in values if item[0] != "tag:yaml.org,2002:bool"]
            for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
        }

    WorkflowLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))

    def mapping(loader, node, deep=False):
        pairs = loader.construct_pairs(node, deep=deep)
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate workflow YAML key: " + str(key))
            result[key] = value
        return result

    WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    files = sorted((root / ".github/workflows").glob("*.y*ml"))
    if not files:
        raise ValueError("No GitHub Actions workflows found")
    for path in files:
        value = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=WorkflowLoader)
        if (not isinstance(value, dict) or not isinstance(value.get("on"), dict)
                or not isinstance(value.get("jobs"), dict) or not value["jobs"]):
            raise ValueError("Workflow requires on and jobs mappings: " + path.name)
        for job in value["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses", "")
                if action and not action.startswith("./") and not re.fullmatch(r"[^@]+@[a-f0-9]{40}", action):
                    raise ValueError("External action must use a full commit SHA: " + action)
    return len(files)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    checks = (("Python AST", check_python), ("JSON config", check_config),
              ("M4 source SHA256", check_pins), ("Markdown links", check_markdown),
              ("Workflow YAML/action pins", check_workflows))
    failed = False
    for label, check in checks:
        try:
            print(f"PASS {label}: {check(root)}")
        except Exception as exc:
            failed = True
            print(f"FAIL {label}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
