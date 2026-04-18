from __future__ import annotations

import argparse
import json
import html
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
class ArticleSummary:
    news: NewsItem
    article_title: str
    article_text: str
    korean_summary: list[str]


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
