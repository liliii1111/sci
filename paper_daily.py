import datetime as dt
import email.utils
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


GITHUB_API = "https://api.github.com"
ARXIV_API = "https://export.arxiv.org/api/query"


def request_json(url, token, method="GET", payload=None):
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "paper-daily",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json(text):
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in Research Interests issue body.")


def get_research_config(repo, token, issue_title):
    query = urllib.parse.urlencode({"state": "open", "per_page": 100})
    issues = request_json(f"{GITHUB_API}/repos/{repo}/issues?{query}", token)
    for issue in issues:
        if "pull_request" in issue:
            continue
        if issue.get("title", "").strip().lower() == issue_title.strip().lower():
            return extract_json(issue.get("body") or "")
    raise ValueError(f'Open issue titled "{issue_title}" was not found.')


def topic_query(topic):
    parts = []
    for keyword in topic.get("keywords", []):
        keyword = str(keyword).strip()
        if keyword:
            parts.append(f'all:"{keyword}"')
    for category in topic.get("arxiv_categories", []):
        category = str(category).strip()
        if category:
            parts.append(f"cat:{category}")
    return " OR ".join(parts) if parts else "all:*"


def parse_arxiv_date(value):
    return dt.datetime.strptime(value[:10], "%Y-%m-%d").date()


def search_arxiv(topic, lookback_days):
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=lookback_days)
    params = {
        "search_query": topic_query(topic),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(int(topic.get("max_results", 8))),
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        root = ET.fromstring(resp.read())

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        published = entry.findtext("atom:published", default="", namespaces=ns)
        if not published or parse_arxiv_date(published) < cutoff:
            continue
        papers.append(
            {
                "title": " ".join(entry.findtext("atom:title", default="", namespaces=ns).split()),
                "url": entry.findtext("atom:id", default="", namespaces=ns),
                "published": published[:10],
                "summary": " ".join(entry.findtext("atom:summary", default="", namespaces=ns).split()),
                "authors": [
                    author.findtext("atom:name", default="", namespaces=ns)
                    for author in entry.findall("atom:author", ns)
                ],
            }
        )
    return papers


def build_daily_body(config, results, lookback_days):
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    lines = [
        f"# Paper Daily - {today}",
        "",
        f"Lookback days: {lookback_days}",
        "",
    ]
    if not results:
        lines.append("No matching arXiv papers found.")
        return "\n".join(lines)

    for topic_name, papers in results:
        lines.extend([f"## {topic_name}", ""])
        if not papers:
            lines.extend(["No matching papers found.", ""])
            continue
        for paper in papers:
            authors = ", ".join(paper["authors"][:4])
            if len(paper["authors"]) > 4:
                authors += ", et al."
            lines.extend(
                [
                    f"- [{paper['title']}]({paper['url']})",
                    f"  - Published: {paper['published']}",
                    f"  - Authors: {authors or 'Unknown'}",
                    f"  - Summary: {paper['summary'][:500]}",
                    "",
                ]
            )
    return "\n".join(lines)


def create_issue(repo, token, title, body):
    return request_json(
        f"{GITHUB_API}/repos/{repo}/issues",
        token,
        method="POST",
        payload={"title": title, "body": body},
    )


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required.")

    issue_title = os.environ.get("RESEARCH_INTERESTS_ISSUE_TITLE", "Research Interests")
    config = get_research_config(repo, token, issue_title)
    lookback_days = int(config.get("lookback_days", 7))
    topics = config.get("topics", [])

    if not topics:
        print("No topics configured. Nothing to collect.")
        return

    results = []
    for topic in topics:
        name = topic.get("name") or "Untitled topic"
        results.append((name, search_arxiv(topic, lookback_days)))

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    daily_title = f"Paper Daily - {today}"
    body = build_daily_body(config, results, lookback_days)
    issue = create_issue(repo, token, daily_title, body)
    print(f"Created issue: {issue.get('html_url')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"paper_daily failed: {exc}", file=sys.stderr)
        raise

