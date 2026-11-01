"""
GitHub API helpers for finding pull requests linked to issues.

Searches the repo's pull requests for those that reference the issue (e.g. in
title or body: "Fixes #N", "Closes #N", or plain "#N"). Does not rely on the
issue's timeline cross-references, so it works when the issue page has no
cross-reference events.
"""

import time
from typing import Any

import requests

GITHUB_API = "https://api.github.com"
SEARCH_ACCEPT = "application/vnd.github+json"


def _get_headers(token: str | None) -> dict[str, str]:
    h: dict[str, str] = {
        "Accept": SEARCH_ACCEPT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_pull_requests_linked_to_issue(
    owner: str,
    repo: str,
    issue_id: str,
    token: str | None,
) -> list[dict[str, Any]]:
    """
    Return a list of pull requests in the repo that reference the given issue
    (e.g. "Fixes #N" or "#N" in title or body). Each item is a dict with at
    least "number"; may also include "title", "state", "html_url", "created_at".

    Uses the Search API so PRs are found even when the issue has no
    cross-reference events on its timeline.
    """
    # Search for PRs that contain the issue number in title/body
    query = f"repo:{owner}/{repo} type:pr {issue_id} linked:issue"
    url = f"{GITHUB_API}/search/issues"
    prs: list[dict[str, Any]] = []
    page = 1

    while True:
        r = requests.get(
            url,
            headers=_get_headers(token),
            params={"q": query, "per_page": 100, "page": page},
            timeout=30,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, dict):
            break
        items = data.get("items")
        if not isinstance(items, list):
            break
        for item in items:
            num = item.get("number")
            if num is None:
                continue
            prs.append(
                {
                    "number": num,
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "html_url": item.get("html_url"),
                    "created_at": item.get("created_at"),
                }
            )
        if len(items) < 100:
            break
        page += 1
        time.sleep(0.4)

    return prs


if __name__ == "__main__":
    import json
    import os

    prs = get_pull_requests_linked_to_issue(
        "etcd-io", "etcd", "15247", os.getenv("GITHUB_TOKEN")
    )
    print(json.dumps(prs, indent=4))
