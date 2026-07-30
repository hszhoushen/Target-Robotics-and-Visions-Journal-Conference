#!/usr/bin/env python3
"""Fetch current ranking and journal metrics from LetPub.

The script prints tab-separated records and does not modify the repository.
It intentionally records the LetPub journal URL so every value can be audited.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://www.letpub.com.cn/"
AUTOCOMPLETE_URL = urljoin(BASE_URL, "journalappAjaxXS.php")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
)


JOURNALS = [
    "Science Robotics",
    "IEEE Transactions on Robotics",
    "International Journal of Robotics Research",
    "Journal of Field Robotics",
    "IEEE/ASME Transactions on Mechatronics",
    "IEEE Transactions on Automation Science and Engineering",
    "IEEE Transactions on Intelligent Transportation Systems",
    "IEEE Transactions on Cybernetics",
    "Robotics and Computer-Integrated Manufacturing",
    "Soft Robotics",
    "IEEE Robotics and Automation Letters",
    "IEEE Transactions on Cognitive and Developmental Systems",
    "Robotics and Autonomous Systems",
    "Autonomous Robots",
    "IEEE Transactions on Pattern Analysis and Machine Intelligence",
    "IEEE Transactions on Image Processing",
    "International Journal of Computer Vision",
    "Nature Machine Intelligence",
    "IEEE Transactions on Neural Networks and Learning Systems",
    "Neural Networks",
    "Pattern Recognition",
    "IEEE Transactions on Multimedia",
    "IEEE Transactions on Circuits and Systems for Video Technology",
    "Computer Vision and Image Understanding",
    "Information Sciences",
    "Knowledge-Based Systems",
    "Expert Systems with Applications",
    "IEEE Transactions on Instrumentation and Measurement",
    "Applied Soft Computing",
    "Engineering Applications of Artificial Intelligence",
    "Journal of Intelligent Manufacturing",
    "International Journal of Intelligent Systems",
    "Neurocomputing",
    "Neural Computing and Applications",
    "Applied Intelligence",
    "Pattern Recognition Letters",
    "Image and Vision Computing",
    "IEEE Signal Processing Letters",
    "Machine Vision and Applications",
    "Journal of Visual Communication and Image Representation",
    "Expert Systems",
    "The Visual Computer",
    "Multimedia Tools and Applications",
    "Multimedia Systems",
    "International Journal of Neural Systems",
    "Natural Computing",
    "CAAI Transactions on Intelligence Technology",
    "Information Fusion",
]

DETAIL_URL_OVERRIDES = {
    # LetPub autocomplete currently omits these two canonical titles.
    "Neural Computing and Applications": (
        f"{BASE_URL}index.php?journalid=6123&page=journalapp&view=detail"
    ),
    "The Visual Computer": (
        f"{BASE_URL}index.php?journalid=8059&page=journalapp&view=detail"
    ),
}


@dataclass
class RankingRecord:
    title: str
    new_ranking: str
    category: str
    top: str
    impact_factor: str
    annual_articles: str
    url: str


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def get(session: requests.Session, url: str, *, params: dict | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"GET failed after retries: {url}: {last_error}")


def find_detail_url(session: requests.Session, title: str) -> str:
    if title in DETAIL_URL_OVERRIDES:
        return DETAIL_URL_OVERRIDES[title]

    response = session.get(
        AUTOCOMPLETE_URL,
        params={"querytype": "autojournal", "term": title},
        timeout=60,
    )
    response.raise_for_status()
    candidates = [
        (
            item.get("label", ""),
            (
                f"{BASE_URL}index.php?journalid={item['id']}"
                "&page=journalapp&view=detail"
            ),
        )
        for item in response.json()
        if item.get("id") and item.get("label")
    ]

    target = normalize_title(title)
    for candidate_title, url in candidates:
        if normalize_title(candidate_title) == target:
            return url

    # LetPub omits the leading "The" for a few canonical journal names.
    for candidate_title, url in candidates:
        candidate = normalize_title(candidate_title)
        if candidate.removeprefix("the") == target.removeprefix("the"):
            return url

    rendered = ", ".join(name for name, _ in candidates[:10]) or "no results"
    raise LookupError(f"no exact LetPub match for {title!r}; candidates: {rendered}")


def visible_zone(cell: Tag) -> str:
    for span in cell.find_all("span"):
        style = re.sub(r"\s+", "", span.get("style", "")).casefold()
        text = span.get_text(" ", strip=True)
        if re.fullmatch(r"[1-4]区", text) and "display:none" not in style:
            return text
    return ""


def parse_new_ranking(title: str, url: str, html: str) -> RankingRecord:
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    impact_factor_match = re.search(r"影响因子([0-9.]+)分", page_title)
    impact_factor = impact_factor_match.group(1) if impact_factor_match else ""

    annual_label = soup.find(string=lambda s: s and s.strip() == "年文章数")
    annual_row = annual_label.find_parent("tr") if annual_label else None
    annual_text = annual_row.get_text(" ", strip=True) if annual_row else ""
    annual_match = re.search(r"年文章数\s*(\d+)", annual_text)
    annual_articles = annual_match.group(1) if annual_match else ""

    label = soup.find(string=lambda s: s and "2026年3月发布" in s)
    if label is None:
        return RankingRecord(
            title, "未收录", "", "", impact_factor, annual_articles, url
        )

    label_cell = label.find_parent("td")
    row = label_cell.find_parent("tr") if label_cell else None
    if row is None:
        raise LookupError("2026 New Ranking row not found")

    cells = row.find_all("td", recursive=False)
    if len(cells) < 2:
        raise LookupError("2026 New Ranking value cell not found")

    table = cells[1].find("table")
    data_row = table.find("tr").find_next_sibling("tr") if table else None
    if data_row is None:
        return RankingRecord(
            title, "未收录", "", "", impact_factor, annual_articles, url
        )

    data_cells = data_row.find_all("td", recursive=False)
    if not data_cells:
        raise LookupError("2026 New Ranking category cell not found")

    category_cell = data_cells[0]
    category = next(
        (
            text.strip()
            for text in category_cell.find_all(string=True, recursive=False)
            if text.strip()
        ),
        "",
    )
    new_ranking = visible_zone(category_cell)
    top = data_cells[-2].get_text(" ", strip=True) if len(data_cells) >= 3 else ""
    if not new_ranking:
        raise LookupError("visible 2026 New Ranking zone not found")
    return RankingRecord(
        title=title,
        new_ranking=new_ranking,
        category=category,
        top=top,
        impact_factor=impact_factor,
        annual_articles=annual_articles,
        url=url,
    )


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    ids_only = "--ids" in sys.argv[1:]
    journals = [arg for arg in sys.argv[1:] if arg != "--ids"] or JOURNALS

    print(
        "title\tletpub_url"
        if ids_only
        else (
            "title\tnew_ranking_2026\tcategory\ttop\t"
            "impact_factor\tannual_articles\tletpub_url"
        )
    )
    failures = 0
    for index, title in enumerate(journals, start=1):
        print(f"FETCH\t{index}/{len(journals)}\t{title}", file=sys.stderr, flush=True)
        try:
            detail_url = find_detail_url(session, title)
            if ids_only:
                print(f"{title}\t{detail_url}", flush=True)
                continue
            parse_error: Exception | None = None
            for attempt in range(5):
                try:
                    detail_html = get(session, detail_url)
                    record = parse_new_ranking(title, detail_url, detail_html)
                    break
                except LookupError as exc:
                    parse_error = exc
                    time.sleep(10 * (attempt + 1))
            else:
                raise RuntimeError(f"LetPub detail remained incomplete: {parse_error}")
            print(
                f"{record.title}\t{record.new_ranking}\t{record.category}\t"
                f"{record.top}\t{record.impact_factor}\t"
                f"{record.annual_articles}\t{record.url}",
                flush=True,
            )
        except Exception as exc:  # Keep a complete audit trail for batch runs.
            failures += 1
            print(f"ERROR\t{title}\t{exc}", file=sys.stderr, flush=True)
        if index != len(journals):
            time.sleep(4 if not ids_only else 0.25)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
