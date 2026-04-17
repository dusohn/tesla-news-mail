from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from tsla_finviz_digest import (
    FinvizNewsParser,
    NewsItem,
    build_summary_lines,
    deduplicate_news,
    filter_recent,
    parse_timestamp,
)


SAMPLE_HTML = """
<html>
  <body>
    <table class="fullview-news-outer">
      <tr>
        <td width="130">Apr-17-26 09:15AM</td>
        <td>
          <a class="tab-link-news" href="https://example.com/a">Tesla shares rise after analyst note</a>
          <span>(Reuters)</span>
        </td>
      </tr>
      <tr>
        <td>08:45AM</td>
        <td>
          <a class="tab-link-news" href="https://example.com/b">Tesla shares rise after analyst note</a>
          <span>(MarketWatch)</span>
        </td>
      </tr>
      <tr>
        <td>07:30AM</td>
        <td>
          <a class="tab-link-news" href="https://example.com/c">Tesla robotaxi expansion gains attention</a>
          <span>(Barrons)</span>
        </td>
      </tr>
      <tr>
        <td>Apr-16-26 08:00AM</td>
        <td>
          <a class="tab-link-news" href="https://example.com/d">Older Tesla headline</a>
          <span>(CNBC)</span>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


class DigestTests(unittest.TestCase):
    def test_parser_extracts_rows(self) -> None:
        parser = FinvizNewsParser()
        parser.feed(SAMPLE_HTML)
        self.assertEqual(len(parser.items), 4)
        self.assertEqual(parser.items[0][1], "Tesla shares rise after analyst note")

    def test_parse_timestamp_reuses_previous_date(self) -> None:
        finviz_tz = ZoneInfo("America/New_York")
        first, current_date = parse_timestamp("Apr-17-26 09:15AM", None, finviz_tz)
        second, current_date = parse_timestamp("08:45AM", current_date, finviz_tz)
        self.assertEqual(first.date(), second.date())
        self.assertEqual(second.hour, 8)
        self.assertEqual(current_date, first)

    def test_recent_filter_and_deduplication(self) -> None:
        finviz_tz = ZoneInfo("America/New_York")
        parser = FinvizNewsParser()
        parser.feed(SAMPLE_HTML)

        news_items: list[NewsItem] = []
        current_date = None
        for raw_timestamp, headline, link, source in parser.items:
            published_at, current_date = parse_timestamp(raw_timestamp, current_date, finviz_tz)
            news_items.append(
                NewsItem(
                    published_at=published_at,
                    headline=headline,
                    url=link,
                    source=source,
                )
            )

        now = datetime(2026, 4, 17, 12, 0, tzinfo=finviz_tz)
        recent_items = filter_recent(news_items, now, 24)
        deduped, removed = deduplicate_news(recent_items)
        self.assertEqual(len(recent_items), 3)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(removed, 1)

    def test_summary_lines_include_empty_message(self) -> None:
        lines = build_summary_lines([], 0)
        self.assertIn("뉴스가 없습니다", lines[0])

    def test_parse_timestamp_supports_today_prefix(self) -> None:
        finviz_tz = ZoneInfo("America/New_York")
        reference_now = datetime(2026, 4, 17, 16, 0, tzinfo=finviz_tz)
        published, current_date = parse_timestamp(
            "Today 03:55PM",
            None,
            finviz_tz,
            reference_now=reference_now,
        )
        self.assertEqual(published.date(), reference_now.date())
        self.assertEqual(published.hour, 15)
        self.assertEqual(current_date, published)


if __name__ == "__main__":
    unittest.main()
