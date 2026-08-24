"""HTML-to-speech text cleaning pipeline (regex + BeautifulSoup only).

Turns Wallabag's raw HTML article content into clean, spoken-word prose for
Kokoro TTS. No LLM, no network. The pipeline:

1. Parse HTML with BeautifulSoup4 + lxml.
2. Remove non-content elements (script/style/nav/footer/img/form/table/...).
3. Extract text with a space separator, unescape residual entities, collapse
   whitespace, drop bare URLs/emails and trailing boilerplate.
4. Ensure terminal punctuation.
5. Assemble the exact TTS input ``[pause:0.5s] {title} [pause:1s] {body}``,
   rewriting ``Settings.PRONUNCIATIONS`` whole-word matches first.

Articles whose cleaned body is shorter than ``MIN_TEXT_CHARS`` raise
:class:`SkipArticle` so the generation pipeline can skip them.
"""

from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup

from .config import get_settings
from .wallabag import ArticleFull

# Elements removed entirely (tag + contents) before text extraction.
_REMOVE_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "aside",
    "figure",
    "figcaption",
    "img",
    "picture",
    "video",
    "audio",
    "iframe",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "svg",
    "canvas",
    "map",
    "area",
    "table",
    "hr",
    "br",
)

_WS_RE = re.compile(r"\s+")
# A space immediately before punctuation ("friends .") is an artifact of the
# get_text(separator=" ") join; drop it so text reads naturally.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.!?,;:])")

# Bare URLs in text: http/https schemes and bare "www." links. The match is
# greedy and trailing sentence punctuation (". , ! ? ...") is trimmed so a URL
# ending a sentence keeps its period.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_URL_TRAILING_PUNCT = ".,;:!?)]}"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Boilerplate phrases: the phrase and the rest of the text after it are
# removed. Kept conservative — distinctive phrases only, so ordinary sentences
# are untouched. Only the trailing portion of the text is scanned (see
# ``_BOILERPLATE_TAIL_CHARS``) so mid-article sentences that merely contain
# boilerplate-like wording survive.
_BOILERPLATE_PHRASES = (
    "related articles",
    "related posts",
    "you might also like",
    "share this",
    "subscribe to our newsletter",
    "sign up for",
    "read more:",
    "continue reading",
    "click here",
    "follow us on",
    "image: ",
    "photo: ",
    "credit:",
    "credit photo:",
)

# A trailing comments block — removed together with the other boilerplate
# phrases when it appears near the end of the text (see
# ``_BOILERPLATE_TAIL_CHARS``) so mid-article sentences mentioning "comments"
# survive.
_COMMENT_PHRASES = ("comments", "leave a comment", "add a comment")

# Boilerplate is only removed when a phrase falls within this many characters
# of the end of the text. Texts no longer than this window are never
# truncated (see ``_remove_boilerplate``).
_BOILERPLATE_TAIL_CHARS = 400


class SkipArticle(Exception):
    """The cleaned article body is too short to synthesize."""


def _collapse_ws(text: str) -> str:
    """Collapse all whitespace runs to a single space and trim."""
    return _WS_RE.sub(" ", text).strip()


def _normalize_ws(text: str) -> str:
    """Collapse whitespace and remove spaces that precede punctuation."""
    text = _collapse_ws(text)
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def _extract_text(html: str) -> str:
    """Parse HTML, drop non-content elements, and return plain text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in _REMOVE_TAGS:
        for node in soup.find_all(tag):
            node.decompose()
    return soup.get_text(separator=" ")


def _remove_urls(text: str) -> str:
    def _repl(match: re.Match) -> str:
        url = match.group(0)
        trimmed = url.rstrip(_URL_TRAILING_PUNCT)
        # Remove the URL but keep the sentence punctuation it was followed by.
        return url[len(trimmed):]

    return _URL_RE.sub(_repl, text)


def _remove_emails(text: str) -> str:
    return _EMAIL_RE.sub("", text)


def _remove_boilerplate(text: str) -> str:
    """Truncate at the earliest boilerplate phrase in the trailing portion.

    Only phrases appearing within the last ``_BOILERPLATE_TAIL_CHARS``
    characters are removed, so mid-article sentences that merely contain
    boilerplate-like wording are left untouched. A text no longer than the
    tail window is returned unchanged — the whole text would otherwise count
    as trailing and legitimate early content would be destroyed.
    """
    if not text:
        return text
    if len(text) <= _BOILERPLATE_TAIL_CHARS:
        return text
    lowered = text.lower()
    tail_start = len(text) - _BOILERPLATE_TAIL_CHARS
    tail = lowered[tail_start:]
    cut: int | None = None

    for phrase in (*_BOILERPLATE_PHRASES, *_COMMENT_PHRASES):
        idx = tail.find(phrase)
        if idx != -1:
            idx += tail_start
            if cut is None or idx < cut:
                cut = idx

    if cut is not None:
        text = text[:cut]
    return text


def _ensure_terminal_punctuation(text: str) -> str:
    if text and text[-1] not in ".!?":
        text = text + "."
    return text


# Compiled once per distinct pronunciation set; cached because build_tts_input
# calls apply_pronunciations twice per article with the same dict.
_PRONUNCIATION_CACHE: dict[tuple[str, ...], re.Pattern[str] | None] = {}


def _pronunciation_pattern(
    pronunciations: dict[str, str],
) -> re.Pattern[str] | None:
    """Return a whole-word, case-insensitive regex matching every key.

    Keys are alternated longest-first so overlapping keys prefer the longer
    match. Returns ``None`` for an empty dict.
    """
    cache_key = tuple(sorted(pronunciations))
    if cache_key in _PRONUNCIATION_CACHE:
        return _PRONUNCIATION_CACHE[cache_key]
    pattern: re.Pattern[str] | None = None
    if pronunciations:
        keys = sorted(pronunciations, key=len, reverse=True)
        joined = "|".join(re.escape(key) for key in keys)
        pattern = re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)
    _PRONUNCIATION_CACHE[cache_key] = pattern
    return pattern


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    """Rewrite whole-word matches of each key to its spoken form.

    Matching is case-insensitive ("json" and "JSON" both become the value for
    "JSON"); word boundaries keep larger words intact ("JSONParser" survives).
    An empty dict returns the text unchanged.
    """
    if not text or not pronunciations:
        return text
    pattern = _pronunciation_pattern(pronunciations)
    if pattern is None:  # pragma: no cover - guarded by the empty check above
        return text
    lookup = {key.lower(): value for key, value in pronunciations.items()}
    return pattern.sub(lambda match: lookup[match.group(0).lower()], text)


def clean_title(title: str) -> str:
    """Strip HTML from a title, collapse whitespace, and trim. Never raises."""
    try:
        soup = BeautifulSoup(title, "lxml")
        text = soup.get_text(separator=" ")
    except Exception:  # pragma: no cover - parser fallback for exotic input
        text = re.sub(r"<[^>]+>", " ", title)
    return _normalize_ws(_html.unescape(text))


def clean_body(html: str, min_chars: int | None = None) -> str:
    """Parse and clean HTML content into spoken-word prose.

    Raises :class:`SkipArticle` if the cleaned text is shorter than
    ``min_chars`` (default: ``Settings.MIN_TEXT_CHARS``).
    """
    threshold = get_settings().MIN_TEXT_CHARS if min_chars is None else min_chars

    text = _extract_text(html)
    text = _html.unescape(text)
    text = _remove_urls(text)
    text = _remove_emails(text)
    text = _remove_boilerplate(text)
    text = _normalize_ws(text)
    text = _ensure_terminal_punctuation(text)

    if len(text) < threshold:
        raise SkipArticle(
            f"Article text too short for TTS: {len(text)} chars "
            f"(minimum {threshold})"
        )
    return text


def build_tts_input(title: str, html: str, min_chars: int | None = None) -> str:
    """Assemble the exact TTS input string for an article.

    ``[pause:0.5s] {clean_title} [pause:1s] {clean_body}``. When ``min_chars``
    is ``None`` the body is length-guarded with the default
    ``Settings.MIN_TEXT_CHARS`` (raises :class:`SkipArticle`); pass an
    explicit value to override. Both title and body pass through
    :func:`apply_pronunciations` (``Settings.PRONUNCIATIONS``) before
    assembly, so the ``[pause:...]`` tokens themselves are never rewritten.
    """
    clean = clean_title(title)
    if min_chars is None:
        body = clean_body(html)
    else:
        body = clean_body(html, min_chars=min_chars)
    pronunciations = get_settings().PRONUNCIATIONS
    clean = apply_pronunciations(clean, pronunciations)
    body = apply_pronunciations(body, pronunciations)
    return f"[pause:0.5s] {clean} [pause:1s] {body}"


def build_tts_input_from_article(
    article: ArticleFull, min_chars: int | None = None
) -> str:
    """Assemble the TTS input string from a Wallabag :class:`ArticleFull`."""
    return build_tts_input(article.title, article.content, min_chars=min_chars)
