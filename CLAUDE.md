# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

This is a personal blog system that uses GitHub Issues as the publishing platform. Markdown files in `posts/` are synced to GitHub Issues via a GitHub Actions workflow.

## Architecture

```
.github/
├── scripts/sync-blogs.py      # Python sync script (only dep: pyyaml)
└── workflows/sync-blog.yml    # GitHub Actions workflow
posts/
└── *.md                        # Blog posts with YAML frontmatter
assets/
└── figures/                    # Images referenced by posts
```

### Sync Flow

1. Push to `main` (only `posts/**/*.md` changes) triggers GitHub Actions
2. `sync-blogs.py` fetches all open Issues with `blog` label
3. For each `.md` file: creates a new Issue or updates existing one (skipped if md5 hash matches)
4. For each blog Issue whose file no longer exists: closes the Issue

### Issue Mapping

Each synced Issue has an HTML comment at the bottom of its body:
```html
<!-- blog-sync: path=posts/filename.md hash=<md5> -->
```
This maps the Issue back to its source file and tracks content changes.

### Image Path Handling

All relative image paths in blog posts are converted to absolute `raw.githubusercontent.com` URLs. Paths are resolved relative to the `.md` file's parent directory, then converted to:
`https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/{resolved_path}`

### Frontmatter Fields

| Field | Type | Description |
|---|---|---|
| `title` | string | Issue title |
| `tags` | list | Issue labels (auto-normalized) |
| `draft` | bool | If true, skip syncing |
| `cover` | string | Cover image URL (relative paths auto-converted) |
| `description` | string | Displayed as blockquote in Issue body |

## Development

### Local Testing

```bash
cd /home/iori/workspace/blogs
source .venv/bin/activate
GH_TOKEN=$(gh auth token) REPO=iori2333/test-blogs python3 .github/scripts/sync-blogs.py
```

### Dependencies

- Python 3.12+ (installed via Actions)
- `pyyaml` (installed via Actions)
- `gh` CLI (pre-installed in GitHub Actions runners)

### Manual Trigger

```bash
gh workflow run sync-blog.yml --repo iori2333/test-blogs
```

### Creating New Posts

Place a `.md` file in `posts/` with YAML frontmatter:

```yaml
---
title: "Post Title"
tags: [tag1, tag2]
draft: false
cover: "assets/figures/cover.png"
description: "Brief description"
---

Post content with markdown...
```

Image paths are relative to the `.md` file (e.g., `../assets/figures/photo.png`).
