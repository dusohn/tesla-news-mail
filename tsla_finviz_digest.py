from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


FINVIZ_URL = "https://finviz.com/quote.ashx?t={ticker}&p=d"
DEFAULT_TICKER = "TSLA"
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_FINVIZ_TIMEZONE = "America/New_York"
DEFAULT_DIGEST_TIMEZONE = "Asia/Seoul"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "s",
    "said",
    "says",
    "tesla",
    "that",
    "the",
    "this",
    "to",
    "tsla",
    "vs",
    "with",
}
THEME_KEYWORDS = {
    "실적/전망": {"earnings", "revenue", "profit", "forecast", "guidance", "margin"},
    "주가/투자의견": {"stock", "shares", "price", "target", "rating", "downgrade", "upgrade"},
    "전기차 판매": {"delivery", "deliveries", "sales", "demand", "china", "ev", "vehicle"},
    "자율주행/로보택시": {"fsd", "autonomous", "robotaxi", "self-driving", "autopilot"},
    "생산/공장": {"factory", "plant", "production", "gigafactory", "battery", "supply"},
    "경영/인물": {"musk", "elon", "executive", "board"},
    "규제/리콜": {"recall", "regulator", "regulatory", "lawsuit", "probe", "safety"},
}


@dataclass(frozen=True)
class NewsItem:
    published_at: datetime
    headline: str
    url: str
    source: str


@dataclass(frozen=True)
class ArticleRecord:
    news: NewsItem
    article_title: str
    article_text: str


class FinvizNewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_news_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_index = -1
        self.in_link = False
        self.current_date_text: list[str] = []
        self.current_headline_text: list[str] = []
        self.current_source_text: list[str] = []
        self.current_link = ""
        self.items: list[tuple[str, str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = attr_map.get("class", "") or ""
        if tag == "table" and "fullview-news-outer" in classes:
            self.in_news_table = True
            self.table_depth = 1
            return
        if self.in_news_table and tag == "table":
            self.table_depth += 1
        if not self.in_news_table:
            return
        if tag == "tr":
            self.in_row = True
            self.cell_index = -1
            self.current_date_text = []
            self.current_headline_text = []
            self.current_source_text = []
            self.current_link = ""
            return
        if self.in_row and tag == "td":
            self.in_cell = True
            self.cell_index += 1
            return
        if self.in_row and self.in_cell and self.cell_index == 1 and tag == "a":
            self.in_link = True
            self.current_link = attr_map.get("href", "") or ""

    def handle_endtag(self, tag: str) -> None:
        if not self.in_news_table:
            return
        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_news_table = False
            return
        if tag == "a":
            self.in_link = False
            return
        if tag == "td":
            self.in_cell = False
            return
        if tag == "tr" and self.in_row:
            self.in_row = False
            timestamp = clean_text("".join(self.current_date_text))
            headline = clean_text("".join(self.current_headline_text))
            source = clean_text("".join(self.current_source_text))
            if timestamp and headline and self.current_link:
                self.items.append((timestamp, headline, self.current_link, source))

    def handle_data(self, data: str) -> None:
        if not self.in_news_table or not self.in_row or not self.in_cell:
            return
        if self.cell_index == 0:
            self.current_date_text.append(data)
            return
        if self.cell_index == 1:
            if self.in_link:
                self.current_headline_text.append(data)
            else:
                self.current_source_text.append(data)


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.current_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.meta_description = ""
        self.capture_tags = {"p", "article", "h1", "h2", "li"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
            return
        if tag == "meta":
            name = (attr_map.get("name", "") or "").lower()
            prop = (attr_map.get("property", "") or "").lower()
            if name == "description" or prop == "og:description":
                content = clean_text(attr_map.get("content", "") or "")
                if content and not self.meta_description:
                    self.meta_description = content
            return
        if tag in self.capture_tags:
            self.current_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
            return
        if tag in self.capture_tags and self.current_parts:
            text = clean_text(" ".join(self.current_parts))
            self.current_parts = []
            if len(text) >= 40:
                self.paragraphs.append(text)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.current_parts is not None:
            self.current_parts.append(data)

    @property
    def article_title(self) -> str:
        return clean_text(" ".join(self.title_parts))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip(" -|\t\r\n")


def build_request(url: str, data: bytes | None = None, extra_headers: dict[str, str] | None = None) -> Request:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if extra_headers:
        headers.update(extra_headers)
    return Request(url, data=data, headers=headers)


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"환경변수 {name} 값이 없습니다.")
    return value


def env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def parse_timestamp(
    raw_text: str,
    current_date: datetime | None,
    finviz_tz: ZoneInfo,
    reference_now: datetime | None = None,
) -> tuple[datetime, datetime]:
    raw_text = clean_text(raw_text)
    reference_now = reference_now or datetime.now(tz=finviz_tz)
    if re.search(r"[A-Za-z]{3}-\d{2}-\d{2}", raw_text):
        published = datetime.strptime(raw_text, "%b-%d-%y %I:%M%p").replace(tzinfo=finviz_tz)
        return published, published
    if raw_text.lower().startswith("today "):
        parsed_time = datetime.strptime(raw_text.split(" ", 1)[1], "%I:%M%p").time()
        published = datetime.combine(reference_now.date(), parsed_time, tzinfo=finviz_tz)
        return published, published
    if raw_text.lower().startswith("yesterday "):
        parsed_time = datetime.strptime(raw_text.split(" ", 1)[1], "%I:%M%p").time()
        published = datetime.combine(
            (reference_now - timedelta(days=1)).date(),
            parsed_time,
            tzinfo=finviz_tz,
        )
        return published, published
    if current_date is None:
        raise ValueError(f"날짜가 없는 시간값을 먼저 받았습니다: {raw_text}")
    parsed_time = datetime.strptime(raw_text, "%I:%M%p").time()
    published = datetime.combine(current_date.date(), parsed_time, tzinfo=finviz_tz)
    return published, current_date


def fetch_finviz_news(ticker: str, finviz_tz: ZoneInfo) -> list[NewsItem]:
    url = FINVIZ_URL.format(ticker=ticker)
    request = build_request(url)
    with urlopen(request, timeout=30) as response:
        html_text = response.read().decode("utf-8", errors="replace")

    parser = FinvizNewsParser()
    parser.feed(html_text)

    items: list[NewsItem] = []
    current_date: datetime | None = None
    reference_now = datetime.now(tz=finviz_tz)
    for raw_timestamp, headline, link, source in parser.items:
        published_at, current_date = parse_timestamp(
            raw_timestamp,
            current_date,
            finviz_tz,
            reference_now=reference_now,
        )
        items.append(
            NewsItem(
                published_at=published_at,
                headline=headline,
                url=urljoin(url, link),
                source=source.strip("() ") or "Unknown",
            )
        )
    return items


def filter_recent(items: Iterable[NewsItem], now: datetime, lookback_hours: int) -> list[NewsItem]:
    cutoff = now - timedelta(hours=lookback_hours)
    return [item for item in items if item.published_at >= cutoff]


def deduplicate_news(items: list[NewsItem]) -> tuple[list[NewsItem], int]:
    deduped: list[NewsItem] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []

    for item in sorted(items, key=lambda news: news.published_at, reverse=True):
        norm_url = normalize_url(item.url)
        norm_title = normalize_title(item.headline)
        if norm_url in seen_urls or norm_title in seen_titles:
            continue
        if any(SequenceMatcher(None, norm_title, title).ratio() >= 0.92 for title in seen_titles):
            continue
        deduped.append(item)
        seen_urls.add(norm_url)
        seen_titles.append(norm_title)

    removed = max(len(items) - len(deduped), 0)
    return deduped, removed


def top_keywords(items: list[NewsItem], limit: int = 5) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        words = set(re.findall(r"[a-zA-Z][a-zA-Z\-]{1,}", item.headline.lower()))
        for word in words:
            if word in STOPWORDS or len(word) < 3:
                continue
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [word for word, _ in ranked[:limit]]


def top_themes(items: list[NewsItem], limit: int = 3) -> list[str]:
    scores: dict[str, int] = {}
    for item in items:
        headline_lower = item.headline.lower()
        for theme, keywords in THEME_KEYWORDS.items():
            if any(keyword in headline_lower for keyword in keywords):
                scores[theme] = scores.get(theme, 0) + 1
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [theme for theme, _ in ranked[:limit]]


def extract_article_text(url: str) -> tuple[str, str]:
    request = build_request(url, extra_headers={"Referer": FINVIZ_URL.format(ticker=DEFAULT_TICKER)})
    with urlopen(request, timeout=30) as response:
        html_text = response.read().decode("utf-8", errors="replace")

    parser = ArticleParser()
    parser.feed(html_text)

    article_title = parser.article_title or parser.meta_description or url
    paragraphs = parser.paragraphs[:25]
    article_text = "\n".join(paragraphs)
    if not article_text and parser.meta_description:
        article_text = parser.meta_description
    return article_title, article_text[:12000]


def extract_response_text(response_json: dict) -> str:
    output_text = response_json.get("output_text", "")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    collected: list[str] = []
    for item in response_json.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text", "")
                    if text:
                        collected.append(text)
    return "\n".join(collected)


def summarize_digest_in_korean(records: list[ArticleRecord]) -> list[str]:
    api_key = require_env("OPENAI_API_KEY")
    model = env_or_default("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    article_blocks = []
    for index, record in enumerate(records, start=1):
        article_blocks.append(
            (
                f"[기사 {index}]\n"
                f"헤드라인: {record.news.headline}\n"
                f"출처: {record.news.source}\n"
                f"본문:\n{record.article_text}"
            )
        )

    payload = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "너는 여러 영어 경제 기사를 읽고 한글 아침 브리핑으로 정리하는 뉴스 비서다. "
                            "입력에 포함된 기사들만 바탕으로 중복 없이 핵심만 합쳐서 요약하라. "
                            "출력은 반드시 한국어 bullet 4~6개다."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "아래 기사들을 모두 읽고, 서로 겹치는 내용은 합쳐서 TSLA 관련 하루 요약 브리핑으로 작성해줘. "
                            "주가, 수요, 생산, 자율주행, 경영 이슈처럼 큰 흐름 중심으로 정리하고, "
                            "기사 접근 실패나 에러 내용은 절대 포함하지 마.\n\n"
                            + "\n\n".join(article_blocks)
                        ),
                    }
                ],
            },
        ],
        "max_output_tokens": 420,
    }
    request = build_request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        extra_headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=120) as response:
        response_json = json.loads(response.read().decode("utf-8"))

    output_text = extract_response_text(response_json).strip()
    if not output_text:
        raise ValueError("OpenAI 종합 요약 결과가 비어 있습니다.")

    lines: list[str] = []
    for raw_line in output_text.splitlines():
        line = clean_text(raw_line.lstrip("-•0123456789. "))
        if line:
            lines.append(line)
    return lines[:6]


def fallback_digest_summary(records: list[ArticleRecord]) -> list[str]:
    sentences: list[str] = []
    for record in records:
        sentences.extend(re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", record.article_text).strip()))
    cleaned = [clean_text(sentence) for sentence in sentences if len(clean_text(sentence)) >= 50]
    unique: list[str] = []
    seen: set[str] = set()
    for sentence in cleaned:
        key = normalize_title(sentence)
        if key and key not in seen:
            seen.add(key)
            unique.append(sentence)
        if len(unique) >= 5:
            break
    return unique


def collect_article_records(items: list[NewsItem]) -> tuple[list[ArticleRecord], int]:
    records: list[ArticleRecord] = []
    skipped_count = 0
    for news in items:
        try:
            article_title, article_text = extract_article_text(news.url)
        except (HTTPError, URLError):
            skipped_count += 1
            continue
        if not article_text:
            skipped_count += 1
            continue
        records.append(
            ArticleRecord(
                news=news,
                article_title=article_title,
                article_text=article_text,
            )
        )
    return records, skipped_count


def build_summary_lines(
    records: list[ArticleRecord],
    removed_duplicates: int,
    skipped_count: int,
) -> list[str]:
    if not records:
        return [
            "최근 24시간 내 본문을 읽을 수 있는 Finviz TSLA 뉴스가 없습니다.",
            "접근 가능한 기사 본문이 없어 메일에는 빈 결과가 안내됩니다.",
        ]

    news_items = [record.news for record in records]
    keywords = top_keywords(news_items)
    themes = top_themes(news_items)
    lines = [f"최근 24시간 동안 TSLA 관련 기사 {len(records)}건의 본문을 읽고 종합 요약했습니다."]
    if removed_duplicates:
        lines.append(f"중복 기사 {removed_duplicates}건은 제목과 링크 유사도 기준으로 제거했습니다.")
    if skipped_count:
        lines.append(f"본문 접근이 불가능한 기사 {skipped_count}건은 요약에서 제외했습니다.")
    if themes:
        lines.append("주요 이슈: " + ", ".join(themes))
    if keywords:
        lines.append("자주 나온 키워드: " + ", ".join(keywords))
    return lines


def format_timestamp(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def build_plain_body(
    records: list[ArticleRecord],
    digest_summary: list[str],
    removed_duplicates: int,
    skipped_count: int,
    now: datetime,
    digest_tz: ZoneInfo,
) -> str:
    lines = [
        "TSLA 최근 24시간 종합 브리핑",
        "",
        f"생성 시각: {format_timestamp(now, digest_tz)} ({digest_tz.key})",
        "",
    ]
    lines.extend(build_summary_lines(records, removed_duplicates, skipped_count))
    lines.append("")
    lines.append("한글 종합 요약:")
    if digest_summary:
        for line in digest_summary:
            lines.append(f"- {line}")
    else:
        lines.append("- 요약할 본문이 없습니다.")
    lines.append("")
    lines.append("참고 기사:")
    if records:
        for record in records[:10]:
            news = record.news
            lines.append(f"- [{format_timestamp(news.published_at, digest_tz)}] {news.headline} ({news.source})")
            lines.append(f"  {news.url}")
    else:
        lines.append("- 포함된 기사가 없습니다.")
    return "\n".join(lines)


def build_html_body(
    records: list[ArticleRecord],
    digest_summary: list[str],
    removed_duplicates: int,
    skipped_count: int,
    now: datetime,
    digest_tz: ZoneInfo,
) -> str:
    summary_html = "".join(
        f"<li>{html.escape(line)}</li>"
        for line in build_summary_lines(records, removed_duplicates, skipped_count)
    )
    digest_html = "".join(f"<li>{html.escape(line)}</li>" for line in digest_summary)
    links_html = "".join(
        (
            "<tr>"
            f"<td>{html.escape(format_timestamp(record.news.published_at, digest_tz))}</td>"
            f"<td><a href=\"{html.escape(record.news.url)}\">{html.escape(record.news.headline)}</a></td>"
            f"<td>{html.escape(record.news.source)}</td>"
            "</tr>"
        )
        for record in records[:10]
    )
    if not links_html:
        links_html = "<tr><td colspan=\"3\">포함된 기사가 없습니다.</td></tr>"

    return f"""\
<html>
  <body style="font-family: Segoe UI, Arial, sans-serif; line-height: 1.5;">
    <h2>TSLA 최근 24시간 종합 브리핑</h2>
    <p>생성 시각: {html.escape(format_timestamp(now, digest_tz))} ({html.escape(digest_tz.key)})</p>
    <ul>{summary_html}</ul>
    <h3>한글 종합 요약</h3>
    <ul>{digest_html or '<li>요약할 본문이 없습니다.</li>'}</ul>
    <h3>참고 기사</h3>
    <table border="1" cellspacing="0" cellpadding="8" style="border-collapse: collapse; width: 100%;">
      <thead style="background: #f3f4f6;">
        <tr>
          <th align="left">시간</th>
          <th align="left">헤드라인</th>
          <th align="left">출처</th>
        </tr>
      </thead>
      <tbody>{links_html}</tbody>
    </table>
  </body>
</html>
"""


def send_email(subject: str, plain_body: str, html_body: str) -> None:
    smtp_host = require_env("SMTP_HOST")
    smtp_port = int(env_or_default("SMTP_PORT", "465"))
    smtp_username = require_env("SMTP_USERNAME")
    smtp_password = require_env("SMTP_PASSWORD")
    email_from = require_env("EMAIL_FROM")
    email_to = require_env("EMAIL_TO")
    use_ssl = env_or_default("SMTP_USE_SSL", "true").lower() == "true"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def build_subject(now: datetime, digest_tz: ZoneInfo, item_count: int) -> str:
    date_text = format_timestamp(now, digest_tz)
    return f"[TSLA 뉴스] 최근 24시간 종합 브리핑 ({item_count}건) - {date_text}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finviz TSLA 뉴스 종합 브리핑 메일 발송")
    parser.add_argument("--ticker", default=env_or_default("TICKER", DEFAULT_TICKER))
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=int(env_or_default("LOOKBACK_HOURS", str(DEFAULT_LOOKBACK_HOURS))),
    )
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 본문만 출력")
    return parser.parse_args()


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")

    args = parse_args()
    finviz_tz = ZoneInfo(env_or_default("FINVIZ_TIMEZONE", DEFAULT_FINVIZ_TIMEZONE))
    digest_tz = ZoneInfo(env_or_default("DIGEST_TIMEZONE", DEFAULT_DIGEST_TIMEZONE))
    now = datetime.now(tz=digest_tz)

    try:
        all_items = fetch_finviz_news(args.ticker.upper(), finviz_tz)
        recent_items = filter_recent(all_items, now.astimezone(finviz_tz), args.lookback_hours)
        deduped_items, removed_duplicates = deduplicate_news(recent_items)
        records, skipped_count = collect_article_records(deduped_items)
        if records:
            try:
                digest_summary = summarize_digest_in_korean(records)
            except Exception:
                digest_summary = fallback_digest_summary(records)
        else:
            digest_summary = []
        subject = build_subject(now, digest_tz, len(records))
        plain_body = build_plain_body(
            records,
            digest_summary,
            removed_duplicates,
            skipped_count,
            now,
            digest_tz,
        )
        html_body = build_html_body(
            records,
            digest_summary,
            removed_duplicates,
            skipped_count,
            now,
            digest_tz,
        )
        if args.dry_run:
            print(subject)
            print("")
            print(plain_body)
            return 0
        send_email(subject, plain_body, html_body)
        print(f"메일 발송 완료: {subject}")
        return 0
    except Exception as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
