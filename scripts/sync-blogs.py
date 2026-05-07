#!/usr/bin/env python3
"""Sync blog markdown files to GitHub Issues and vice versa."""

import json
import os
import posixpath
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

# Cache: {rel_file_path: issue_number}
_issue_map: dict[str, str] = {}


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


def gh_api_list_issues(repo: str) -> list[dict]:
    """Fetch all open blog Issues via GitHub API, handling pagination."""
    all_issues = []
    page = 1
    while True:
        resp = gh([
            "api", f"repos/{repo}/issues",
            "-f", f"labels={BLOG_LABEL}",
            "-f", "state=open",
            "-f", "per_page=100",
            "-f", f"page={page}",
        ])
        items = json.loads(resp)
        if not items:
            break
        all_issues.extend(items)
        page += 1
    return all_issues


def build_issue_map(repo: str) -> dict[str, str]:
    """Build {file_path: issue_number} mapping from existing blog Issues."""
    mapping = {}
    for issue in gh_api_list_issues(repo):
        body = issue.get("body") or ""
        match = re.search(r"<!-- blog-sync: path=(.+?) -->", body)
        if match:
            mapping[match.group(1)] = str(issue["number"])
    return mapping


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


def convert_image_paths(content: str, base_url: str, file_path: str) -> str:
    """Convert relative image paths to absolute raw.githubusercontent.com URLs."""
    def replace(match):
        alt = match.group(1)
        src = match.group(2)
        if src.startswith("http://") or src.startswith("https://"):
            return match.group(0)
        if src.startswith("/"):
            return f"![{alt}]({base_url}{src.lstrip('/')})"
        # Relative: resolve against file's parent directory
        if file_path:
            parent = Path(file_path).parent
            resolved = parent / src
            normalized = posixpath.normpath(str(resolved))
            if normalized.startswith("/"):
                normalized = normalized[1:]
            return f"![{alt}]({base_url}{normalized})"
        return f"![{alt}]({base_url}{src})"
    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace, content)


def build_issue_body(filepath: Path, frontmatter: dict, repo: str) -> str:
    """Build the GitHub Issue body from markdown content and frontmatter."""
    parts = []

    if frontmatter.get("cover"):
        parts.append(f'<img src="{frontmatter["cover"]}" alt="Cover" style="max-width:100%;" />')
        parts.append("")

    if frontmatter.get("description"):
        parts.append(f'> {frontmatter["description"]}')
        parts.append("")

    body = extract_body(filepath)

    # Convert relative image paths to absolute URLs
    owner, name = repo.split("/")
    branch = os.environ.get("BRANCH", "main")
    raw_base = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/"
    rel_path = str(filepath.relative_to(Path(".")))
    body = convert_image_paths(body, raw_base, rel_path)

    parts.append(body)
    parts.append("")

    # Hidden marker for file-to-Issue mapping
    parts.append(MARKER_TEMPLATE.format(rel_path))

    return "\n".join(parts)


def normalize_tag(tag: str) -> str:
    """Convert a tag to a valid GitHub label name."""
    label = tag.lower().strip().replace(" ", "-")
    label = re.sub(r"[^a-z0-9一-鿿_\-]", "", label)
    return label[:50]


def ensure_label(label: str, repo: str) -> None:
    """Create a GitHub label if it doesn't exist."""
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
    """Look up the Issue number from the in-memory mapping."""
    rel_path = str(filepath.relative_to(Path(".")))
    return _issue_map.get(rel_path)


def create_issue(filepath: Path, frontmatter: dict, repo: str) -> str | None:
    """Create a new Issue for a blog post. Returns Issue number."""
    title = frontmatter.get("title", filepath.stem)
    body = build_issue_body(filepath, frontmatter, repo)
    tags = [normalize_tag(t) for t in frontmatter.get("tags", [])]
    labels = [BLOG_LABEL] + [t for t in tags if t]

    for label in labels:
        ensure_label(label, repo)

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

        url = result.stdout.strip()
        number = url.rstrip("/").split("/")[-1]
        rel_path = str(filepath.relative_to(Path(".")))
        _issue_map[rel_path] = number
        print(f"  Created issue #{number} for {filepath}")
        return number
    finally:
        os.unlink(body_file)


def update_issue(issue_number: str, filepath: Path, frontmatter: dict, repo: str) -> None:
    """Update an existing Issue's body and labels."""
    title = frontmatter.get("title", filepath.stem)
    body = build_issue_body(filepath, frontmatter, repo)
    tags = [normalize_tag(t) for t in frontmatter.get("tags", [])]
    labels = [BLOG_LABEL] + [t for t in tags if t]

    for label in labels:
        ensure_label(label, repo)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        # gh issue edit requires --title whenever labels are changed (newer gh)
        subprocess.run(
            ["gh", "issue", "edit", issue_number, "--repo", repo,
             "--title", title, "--body-file", body_file,
             "--remove-label", BLOG_LABEL,
             "--add-label", ",".join(labels)],
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"  Updated issue #{issue_number} for {filepath}")
    finally:
        os.unlink(body_file)


def close_issue(issue_number: str, repo: str) -> None:
    """Close an Issue when its corresponding file was deleted."""
    subprocess.run(
        ["gh", "issue", "close", issue_number, "--repo", repo],
        capture_output=True,
        text=True,
    )
    print(f"  Closed issue #{issue_number} (file deleted)")


def sync_forward(repo: str) -> list[str]:
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
            update_issue(issue_number, filepath, frontmatter, repo)
        else:
            create_issue(filepath, frontmatter, repo)

    return active_paths


def sync_reverse(repo: str, active_paths: list[str]) -> None:
    """Find Issues for deleted files and close them using in-memory mapping."""
    for file_path, issue_number in dict(_issue_map).items():
        if file_path not in active_paths:
            close_issue(issue_number, repo)


def main():
    if not os.environ.get("GH_TOKEN") or not os.environ.get("REPO"):
        print("ERROR: GH_TOKEN and REPO environment variables are required.",
              file=sys.stderr)
        sys.exit(1)

    repo = os.environ["REPO"]

    print("Starting blog sync...")
    # Build mapping from existing Issues before making any changes
    print("  Fetching existing blog Issues...")
    global _issue_map
    _issue_map = build_issue_map(repo)
    print(f"  Found {len(_issue_map)} existing blog Issue(s)")

    active_paths = sync_forward(repo)
    sync_reverse(repo, active_paths)
    print("Sync complete.")


if __name__ == "__main__":
    main()
