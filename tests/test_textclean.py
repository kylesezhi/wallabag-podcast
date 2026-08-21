"""Tests for the text cleaning pipeline (app/textclean.py)."""

import pytest

from app.config import get_settings
from app.textclean import (
    SkipArticle,
    build_tts_input,
    build_tts_input_from_article,
    clean_body,
    clean_title,
)
from app.wallabag import ArticleFull

_REQUIRED_ENV = {
    "WALLABAG_CLIENT_ID": "test_client_id",
    "WALLABAG_CLIENT_SECRET": "test_client_secret",
    "WALLABAG_USERNAME": "test_user",
    "WALLABAG_PASSWORD": "test_pass",
}


def _set_required_env(monkeypatch):
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def _article(content: str, title: str = "Intro Test") -> ArticleFull:
    return ArticleFull(
        id=1,
        title=title,
        url="https://example.com/article",
        domain_name="example.com",
        reading_time=5,
        language="en",
        tags=[],
        is_archived=False,
        is_starred=False,
        content=content,
    )


# ---------------------------------------------------------------------------
# 1. basic HTML -> text
# ---------------------------------------------------------------------------


def test_basic_html_to_text():
    html = "<p>Hello <b>world</b> and <a href='https://example.com'>friends</a>.</p>"
    assert clean_body(html, min_chars=0) == "Hello world and friends."


def test_entities_decoded():
    assert clean_body("<p>Caf&eacute; &amp; tea</p>", min_chars=0) == "Café & tea."


# ---------------------------------------------------------------------------
# 2. removed elements
# ---------------------------------------------------------------------------


def test_removed_elements():
    html = """
    <p>Keep me</p>
    <script>var x = 'bad script';</script>
    <style>.badstyle{color:red}</style>
    <nav>Navigation links</nav>
    <footer>Copyright 2024</footer>
    <aside>Sidebar junk</aside>
    <img src="x.png" alt="alt text">
    <figure><figcaption>Figure caption</figcaption><p>Figure body</p></figure>
    <table><tr><td>Table cell</td></tr></table>
    <form><input name="q"><button>Submit</button></form>
    <iframe src="https://example.com/embed"></iframe>
    <p>Keep me too</p>
    """
    result = clean_body(html, min_chars=0)
    assert "Keep me" in result
    assert "Keep me too" in result
    assert "bad script" not in result
    assert "badstyle" not in result
    assert "Navigation" not in result
    assert "Copyright" not in result
    assert "Sidebar" not in result
    assert "alt text" not in result
    assert "Figure" not in result
    assert "Table cell" not in result
    assert "Submit" not in result


def test_br_and_hr_removed():
    assert clean_body("<p>Line1<br>Line2</p><hr>", min_chars=0) == "Line1 Line2."


# ---------------------------------------------------------------------------
# 3. URL removal
# ---------------------------------------------------------------------------


def test_url_removal():
    html = "<p>Read the story at https://example.com and www.example.org for updates.</p>"
    result = clean_body(html, min_chars=0)
    assert "https://example.com" not in result
    assert "www.example.org" not in result
    assert "Read the story at" in result
    assert "for updates" in result


def test_url_at_end_of_sentence_keeps_period():
    html = "<p>See https://example.com for details.</p>"
    result = clean_body(html, min_chars=0)
    assert "example.com" not in result
    assert result == "See for details."


# ---------------------------------------------------------------------------
# 4. boilerplate removal
# ---------------------------------------------------------------------------


def test_boilerplate_removal():
    # Leading content must exceed _BOILERPLATE_TAIL_CHARS so the trailing
    # "Related articles" block falls inside the scanned tail window.
    lead = "<p>" + "The actual article content that should be spoken. " * 30 + "</p>"
    html = f"""
    {lead}
    <div class="related">
      Related articles
      <a href="https://example.com/junk1">Junk title one</a>
      <a href="https://example.com/junk2">Junk title two</a>
    </div>
    """
    result = clean_body(html, min_chars=0)
    assert "Related articles" not in result
    assert "Junk title" not in result
    assert "The actual article content" in result


def test_email_and_more_boilerplate():
    # Long enough that the trailing "Sign up for" phrase lands in the tail
    # window (last _BOILERPLATE_TAIL_CHARS chars) and gets cut.
    lead = "<p>" + "Main story content. " * 30 + "</p>"
    html = f"""
    {lead}
    <p>Contact admin@example.com for questions.</p>
    <p>The main story continues here.</p>
    <p>Sign up for our newsletter</p>
    """
    result = clean_body(html, min_chars=0)
    assert "admin@example.com" not in result
    assert "The main story continues here." in result
    assert "Sign up for" not in result


def test_trailing_comments_block_removed():
    # Long enough that "Comments" lands in the tail window and is cut.
    lead = "<p>" + "A complete article body ending normally. " * 20 + "</p>"
    html = f"""
    {lead}
    <p>Comments</p>
    <p>0 responses</p>
    """
    result = clean_body(html, min_chars=0)
    assert "Comments" not in result
    assert "A complete article body ending normally." in result


def test_mid_article_boilerplate_phrases_not_truncated():
    """Boilerplate-like wording inside real sentences must survive."""
    html = (
        "<p>The scientists continue reading the data carefully.</p>"
        "<p>They found amazing results that changed everything we know.</p>"
    )
    result = clean_body(html, min_chars=0)
    assert result == (
        "The scientists continue reading the data carefully. "
        "They found amazing results that changed everything we know."
    )


def test_mid_article_share_this_not_truncated():
    html = (
        "<p>Please share this important finding with your colleagues.</p>"
        "<p>The conclusion was groundbreaking.</p>"
    )
    result = clean_body(html, min_chars=0)
    assert result == (
        "Please share this important finding with your colleagues. "
        "The conclusion was groundbreaking."
    )


def test_mid_article_click_here_not_truncated():
    html = (
        "<p>Users often click here to learn more about the system.</p>"
        "<p>The system then displays a dashboard.</p>"
    )
    result = clean_body(html, min_chars=0)
    assert result == (
        "Users often click here to learn more about the system. "
        "The system then displays a dashboard."
    )


def test_mid_article_phrase_before_tail_not_truncated():
    """A phrase before the tail window of a long text survives untouched."""
    html = (
        "<p>" + "Real article content. " * 30 + "</p>"
        "<p>Please share this important finding with your colleagues.</p>"
        "<p>" + "More substantive article content follows. " * 20 + "</p>"
    )
    result = clean_body(html, min_chars=0)
    assert "share this" in result.lower()
    assert "substantive article content" in result.lower()
    assert "real article content" in result.lower()


# ---------------------------------------------------------------------------
# 5. whitespace normalization
# ---------------------------------------------------------------------------


def test_whitespace_normalization():
    html = "<p>Line one</p>\n\n<p>Line\t\ttwo   with   spaces</p>\n<p>Line three</p>"
    assert clean_body(html, min_chars=0) == "Line one Line two with spaces Line three."


# ---------------------------------------------------------------------------
# 6. skip guard
# ---------------------------------------------------------------------------


def test_skip_guard_raises_below_min_chars():
    with pytest.raises(SkipArticle) as excinfo:
        clean_body("<p>Too short</p>", min_chars=200)
    message = str(excinfo.value)
    assert "200" in message
    assert "10" in message  # char count reported


def test_skip_guard_passes_at_min_chars():
    body = "<p>" + "x" * 200 + "</p>"
    # Terminal punctuation appends a period, so the cleaned body is 201 chars.
    assert clean_body(body, min_chars=200) == "x" * 200 + "."


def test_clean_body_default_min_chars_from_settings(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MIN_TEXT_CHARS", "50")
    get_settings.cache_clear()
    try:
        with pytest.raises(SkipArticle):
            clean_body("<p>Hello</p>")  # 6 chars < 50
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 7. intro assembly (exact format)
# ---------------------------------------------------------------------------


def test_build_tts_input_exact():
    # min_chars=0 disables the length guard; the default (None) would raise
    # SkipArticle for this 12-char body.
    assert (
        build_tts_input("My Title", "<p>Hello world</p>", min_chars=0)
        == "[pause:0.5s] My Title [pause:1s] Hello world."
    )


def test_build_tts_input_single_spaced():
    result = build_tts_input("  Title   ", "<p>  Body   text.  </p>", min_chars=0)
    assert "  " not in result
    assert result == "[pause:0.5s] Title [pause:1s] Body text."


def test_build_tts_input_honors_min_chars():
    with pytest.raises(SkipArticle):
        build_tts_input("T", "<p>short</p>", min_chars=200)


def test_build_tts_input_default_min_chars_skips_short(monkeypatch):
    # min_chars=None must enforce Settings.MIN_TEXT_CHARS (default 200).
    _set_required_env(monkeypatch)
    get_settings.cache_clear()
    try:
        with pytest.raises(SkipArticle):
            build_tts_input("T", "<p>hi</p>")
    finally:
        get_settings.cache_clear()


def test_build_tts_input_default_min_chars_passes_long(monkeypatch):
    # A 250-char body exceeds the default Settings.MIN_TEXT_CHARS (200).
    _set_required_env(monkeypatch)
    get_settings.cache_clear()
    try:
        result = build_tts_input("T", "<p>" + "x" * 250 + "</p>")
        assert result == "[pause:0.5s] T [pause:1s] " + "x" * 250 + "."
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 8. title cleaning
# ---------------------------------------------------------------------------


def test_clean_title():
    assert clean_title("<b>Foo &amp; Bar</b>") == "Foo & Bar"


def test_clean_title_plain():
    assert clean_title("  A   plain   title  ") == "A plain title"


# ---------------------------------------------------------------------------
# 9. build_tts_input_from_article
# ---------------------------------------------------------------------------


def test_build_tts_input_from_article():
    article = _article("<p>Article body text.</p>")
    assert (
        build_tts_input_from_article(article, min_chars=0)
        == "[pause:0.5s] Intro Test [pause:1s] Article body text."
    )


def test_build_tts_input_from_article_honors_min_chars():
    article = _article("<p>short</p>")
    with pytest.raises(SkipArticle):
        build_tts_input_from_article(article, min_chars=200)


def test_build_tts_input_from_article_default_min_chars_skips_short(monkeypatch):
    _set_required_env(monkeypatch)
    get_settings.cache_clear()
    try:
        with pytest.raises(SkipArticle):
            build_tts_input_from_article(_article("<p>hi</p>"))
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# realistic Wallabag-style article
# ---------------------------------------------------------------------------


def test_realistic_wallabag_article():
    # The long second paragraph pushes the footer ("Share this" / "Related
    # articles") into the trailing boilerplate window so it gets cut.
    html = f"""
    <article>
      <h1>Inside the pipeline</h1>
      <p>The first paragraph has a <a href="https://example.com/source">linked source</a>.</p>
      <img src="https://example.com/hero.jpg" alt="Hero image">
      <p>{"A second paragraph with some <em>emphasis</em> and more content. " * 15}</p>
      <blockquote>A memorable quote from the author.</blockquote>
      <footer>
        <p>Share this article with your friends.</p>
        <p>Related articles</p>
        <ul>
          <li><a href="https://example.com/1">Unrelated story</a></li>
          <li><a href="https://example.com/2">Another story</a></li>
        </ul>
      </footer>
    </article>
    """
    result = clean_body(html, min_chars=0)
    assert "<" not in result
    assert "&" not in result
    assert "https://" not in result
    assert "Share this" not in result
    assert "Related articles" not in result
    assert "Unrelated story" not in result
    assert "Inside the pipeline" in result
    assert "linked source" in result
    assert "memorable quote" in result
