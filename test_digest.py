
from zoneinfo import ZoneInfo

from tsla_finviz_digest import (
    ArticleParser,
    ArticleSummary,
    FinvizNewsParser,
    NewsItem,
    build_plain_body,
    build_summary_lines,
    deduplicate_news,
    filter_recent,
)


SAMPLE_HTML = """
SAMPLE_NEWS_HTML = """
<html>
  <body>
    <table class="fullview-news-outer">
</html>
"""

SAMPLE_ARTICLE_HTML = """
<html>
  <head>
    <title>Tesla expands robotaxi pilot</title>
    <meta name="description" content="Tesla expands its robotaxi pilot in Austin." />
  </head>
  <body>
    <article>
      <p>Tesla said it is widening its robotaxi pilot to additional neighborhoods in Austin.</p>
      <p>The company will initially keep a safety operator in the vehicle while it gathers more data.</p>
      <p>Executives said the expansion is intended to test rider demand and operating economics.</p>
    </article>
  </body>
</html>
"""


class DigestTests(unittest.TestCase):
    def test_parser_extracts_rows(self) -> None:
        parser = FinvizNewsParser()
        parser.feed(SAMPLE_HTML)
        parser.feed(SAMPLE_NEWS_HTML)
        self.assertEqual(len(parser.items), 4)
        self.assertEqual(parser.items[0][1], "Tesla shares rise after analyst note")

    def test_recent_filter_and_deduplication(self) -> None:
        finviz_tz = ZoneInfo("America/New_York")
        parser = FinvizNewsParser()
        parser.feed(SAMPLE_HTML)
        parser.feed(SAMPLE_NEWS_HTML)

        news_items: list[NewsItem] = []
        current_date = None
        self.assertEqual(published.hour, 15)
        self.assertEqual(current_date, published)

    def test_article_parser_extracts_title_and_paragraphs(self) -> None:
        parser = ArticleParser()
        parser.feed(SAMPLE_ARTICLE_HTML)
        self.assertIn("Tesla expands robotaxi pilot", parser.article_title)
        self.assertGreaterEqual(len(parser.paragraphs), 2)

    def test_plain_body_includes_korean_summary(self) -> None:
        news = NewsItem(
            published_at=datetime(2026, 4, 17, 9, 15, tzinfo=ZoneInfo("America/New_York")),
            headline="Tesla expands robotaxi pilot",
            url="https://example.com/article",
            source="Reuters",
        )
        article_summary = ArticleSummary(
            news=news,
            article_title="Tesla expands robotaxi pilot",
            article_text="sample",
            korean_summary=["오스틴 내 서비스 구역을 확대했다.", "수요와 운영 경제성을 시험하려는 목적이다."],
        )
        body = build_plain_body(
            [article_summary],
            removed_duplicates=0,
            now=datetime(2026, 4, 18, 7, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            digest_tz=ZoneInfo("Asia/Seoul"),
        )
        self.assertIn("기사별 한글 요약", body)
        self.assertIn("오스틴 내 서비스 구역을 확대했다.", body)


if __name__ == "__main__":
    unittest.main()
