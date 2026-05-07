#!/usr/bin/env python3
"""Sync blog markdown files to GitHub Issues and vice versa."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

POSTS_DIR = Path("posts")
MARKER_TEMPLATE = "<!-- blog-sync: path={} -->"
BLOG_LABEL = "blog"


def gh(args: list[str], check: bool = True) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        print(f"gh error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def parse_frontmatter(filepath: Path) -> dict | None:
    """Extract and parse YAML frontmatter from a markdown file.
    Returns None if no valid frontmatter found."""
    content = filepath.read_text()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return None
    try:
        fm = yaml.safe_load(match.group(1))
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError as e:
        print(f"YAML error in {filepath}: {e}", file=sys.stderr)
        return None


def extract_body(filepath: Path) -> str:
    """Return markdown content with frontmatter stripped."""
    content = filepath.read_text()
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)", content, re.DOTALL)
    return match.group(1).strip() if match else content.strip()


def build_issue_body(filepath: Path, frontmatter: dict) -> str:
    """Build the GitHub Issue body from markdown content and frontmatter."""
    parts = []

    if frontmatter.get("cover"):
        parts.append(f'<img src="{frontmatter["cover"]}" alt="Cover" style="max-width:100%;" />')
        parts.append("")

    if frontmatter.get("description"):
        parts.append(f'> {frontmatter["description"]}')
        parts.append("")

    parts.append(extract_body(filepath))
    parts.append("")

    rel_path = filepath.relative_to(Path("."))
    parts.append(MARKER_TEMPLATE.format(rel_path))

    return "\n".join(parts)


def normalize_tag(tag: str) -> str:
    """Convert a tag to a valid GitHub label name."""
    label = tag.lower().strip().replace(" ", "-")
    label = re.sub(r"[^a-z0-9一-鿿_\-]", "", label)
    return label[:50]


def ensure_label(label: str) -> None:
    """Create a GitHub label if it doesn't exist."""
    repo = os.environ["REPO"]
    result = subprocess.run(
        ["gh", "label", "list", "--repo", repo, "--json", "name"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return

    existing = {l["name"] for l in json.loads(result.stdout)}
    if label not in existing:
        subprocess.run(
            ["gh", "label", "create", label, "--color", "5319e7", "--repo", repo],
            capture_output=True,
            text=True,
        )


def find_issue_for_file(filepath: Path) -> str | None:
    """Find an existing Issue by searching for the file-path marker."""
    repo = os.environ["REPO"]
    rel_path = str(filepath.relative_to(Path(".")))
    marker = MARKER_TEMPLATE.format(rel_path)
    # Escape the marker for gh search (quote it)
    result = subprocess.run(
        ["gh", "search", "issues", f'"{marker}"', "--repo", repo,
         "--limit", "1", "--json", "number"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    issues = json.loads(result.stdout)
    return str(issues[0]["number"]) if issues else None


def create_issue(filepath: Path, frontmatter: dict) -> str | None:
    """Create a new Issue for a blog post. Returns Issue number."""
    repo = os.environ["REPO"]
    title = frontmatter.get("title", filepath.stem)
    body = build_issue_body(filepath, frontmatter)
    tags = [normalize_tag(t) for t in frontmatter.get("tags", [])]
    labels = [BLOG_LABEL] + [t for t in tags if t]

    for label in labels:
        ensure_label(label)

    # Use --body-file to avoid shell argument limits
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        args = [
            "gh", "issue", "create", "--repo", repo,
            "--title", title, "--body-file", body_file,
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])

        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Failed to create issue for {filepath}: {result.stderr}",
                  file=sys.stderr)
            return None

        # Extract issue number from URL in output
        url = result.stdout.strip()
        number = url.rstrip("/").split("/")[-1]
        print(f"  Created issue #{number} for {filepath}")
        return number
    finally:
        os.unlink(body_file)


def update_issue(issue_number: str, filepath: Path, frontmatter: dict) -> None:
    """Update an existing Issue's body and labels."""
    repo = os.environ["REPO"]
    title = frontmatter.get("title", filepath.stem)
    body = build_issue_body(filepath, frontmatter)
    tags = [normalize_tag(t) for t in frontmatter.get("tags", [])]
    labels = [BLOG_LABEL] + [t for t in tags if t]

    for label in labels:
        ensure_label(label)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        subprocess.run(
            ["gh", "issue", "edit", issue_number, "--repo", repo,
             "--title", title, "--body-file", body_file],
            capture_output=True,
            text=True,
        )

        # Replace all labels (remove old ones, add new ones)
        subprocess.run(
            ["gh", "issue", "edit", issue_number, "--repo", repo,
             "--add-label", ",".join(labels)],
            capture_output=True,
            text=True,
        )
        print(f"  Updated issue #{issue_number} for {filepath}")
    finally:
        os.unlink(body_file)


def close_issue(issue_number: str) -> None:
    """Close an Issue when its corresponding file was deleted."""
    repo = os.environ["REPO"]
    subprocess.run(
        ["gh", "issue", "close", issue_number, "--repo", repo],
        capture_output=True,
        text=True,
    )
    print(f"  Closed issue #{issue_number} (file deleted)")


def sync_forward() -> list[str]:
    """Sync .md files -> Issues. Returns list of active file paths."""
    active_paths = []

    if not POSTS_DIR.exists():
        print(f"Posts directory '{POSTS_DIR}' not found.")
        return active_paths

    for filepath in sorted(POSTS_DIR.rglob("*.md")):
        if "drafts" in filepath.parts:
            print(f"  Skipping {filepath} (in drafts directory)")
            continue

        frontmatter = parse_frontmatter(filepath)
        if frontmatter is None:
            print(f"  Skipping {filepath} (no valid frontmatter)")
            continue

        if frontmatter.get("draft", False):
            print(f"  Skipping {filepath} (draft=true)")
            continue

        active_paths.append(str(filepath))

        issue_number = find_issue_for_file(filepath)
        if issue_number:
            update_issue(issue_number, filepath, frontmatter)
        else:
            create_issue(filepath, frontmatter)

    return active_paths


def sync_reverse(active_paths: list[str]) -> None:
    """Find Issues for deleted files and close them."""
    repo = os.environ["REPO"]

    # Search for all open issues with our blog-sync marker
    result = subprocess.run(
        ["gh", "search", "issues", '"<!-- blog-sync: path="',
         "--state", "open", "--repo", repo,
         "--limit", "100", "--json", "number,body"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Failed to search issues: {result.stderr}", file=sys.stderr)
        return

    issues = json.loads(result.stdout) if result.stdout.strip() else []
    for issue in issues:
        body = issue.get("body") or ""
        match = re.search(r"<!-- blog-sync: path=(.+?) -->", body)
        if not match:
            continue

        file_path = match.group(1)
        if file_path not in active_paths:
            close_issue(str(issue["number"]))


def main():
    if not os.environ.get("GH_TOKEN") or not os.environ.get("REPO"):
        print("ERROR: GH_TOKEN and REPO environment variables are required.",
              file=sys.stderr)
        sys.exit(1)

    print("Starting blog sync...")
    active_paths = sync_forward()
    sync_reverse(active_paths)
    print("Sync complete.")


if __name__ == "__main__":
    main()
