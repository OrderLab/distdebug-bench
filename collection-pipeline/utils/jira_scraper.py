import requests
from bs4 import BeautifulSoup
import json
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"
}

def scrape_jira_issue(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # ---- Title ----
    title_tag = soup.find("h1", id="summary-val")
    title = title_tag.get_text(strip=True) if title_tag else None

    # ---- Description ----
    desc_tag = soup.find("div", id="description-val")
    description = desc_tag.get_text("\n", strip=True) if desc_tag else None

    # ---- Comments / Conversations ----
    comments = []
    comment_blocks = soup.find_all("div", class_="issue-data-block")

    for block in comment_blocks:
        author_tag = block.find("a", class_="user-hover")
        time_tag = block.find("time")
        body_tag = block.find("div", class_="action-body")

        comments.append({
            "author": author_tag.get_text(strip=True) if author_tag else None,
            "time": time_tag.get_text(strip=True) if time_tag else None,
            "body": body_tag.get_text("\n", strip=True) if body_tag else None
        })

    return  { "url": url,
        "title": title,
        "description": description,
        "comments": comments
    }


def parse_input(text):
    """
    Each line is tab-separated.
    First column must be the JIRA URL.
    """
    urls = []
    for line in text.strip().splitlines():
        if line.startswith("http"):
            urls.append(line.split("\t")[0])
    return urls

def main(csv_path, json_path):
    input_text = open(csv_path, "r").read().strip()

    urls = parse_input(input_text)

    results = {}
    for url in urls:
        print(f"Scraping {url}")
        try:
            issue_id = url.split("/")[-1]
            issue = scrape_jira_issue(url)
            results[issue_id] = issue
            time.sleep(1)  # be polite to Apache JIRA
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")

    # ---- Save result ----
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved to {json_path}")


if __name__ == "__main__":
    main()