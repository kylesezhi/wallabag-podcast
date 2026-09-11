"""HTML-to-speech text cleaning pipeline (regex + BeautifulSoup only).

Turns Wallabag's raw HTML article content into clean, spoken-word prose for
Kokoro TTS. No LLM, no network. The pipeline:

1. Parse HTML with BeautifulSoup4 + lxml.
2. Remove non-content elements (script/style/nav/footer/img/form/table/...).
3. Replace section titles — h1-h6 headings and paragraphs whose entire text
   is bold (``<p><strong>Title</strong></p>``) — with
   ``[pause:1s] Title. [pause:1s]`` tokens that Kokoro-FastAPI interprets
   natively. The ``*_with_sections`` variants additionally prefix each token
   with a private-use sentinel (``_SECTION_MARK``) and return the title
   strings in document order, so the pipeline can align every title to a
   synthesis chunk and stamp ID3 chapter markers.
4. Extract text with a space separator, unescape residual entities, collapse
   whitespace, drop bare URLs/emails and trailing boilerplate.
5. Ensure terminal punctuation.
6. Assemble the exact TTS input ``[pause:0.5s] {title} [pause:1s] {body}``,
   rewriting ``Settings.PRONUNCIATIONS`` whole-word matches first.

``split_tts_text`` additionally splits a finished TTS input into sentence-boundary
chunks (max ``Settings.KOKORO_MAX_CHUNK_CHARS`` chars) so the generation pipeline
can synthesize long articles one bounded request at a time; sentences carrying a
section-title sentinel always begin a new chunk so chapter starts are exact.

Articles whose cleaned body is shorter than ``MIN_TEXT_CHARS`` raise
:class:`SkipArticle` so the generation pipeline can skip them.
"""

from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup, NavigableString, Tag

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

# Section titles: heading elements wrapped in pause tokens before flattening.
# Kokoro-FastAPI interprets ``[pause:Xs]`` markers natively — the same tokens
# already frame the article title in build_tts_input. Fixed durations, same
# style as those hardcoded title/body pauses.
_HEADING_PAUSE_BEFORE = "[pause:1s]"
_HEADING_PAUSE_AFTER = "[pause:1s]"
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Private-use sentinel prepended to each section-title token when the caller
# asks for chapter positions (``clean_body_with_sections`` /
# ``build_tts_input_with_sections``). It survives every cleaning transform
# untouched and marks exactly where a title starts in the flattened text, then
# is stripped again before synthesis — it must never reach Kokoro. A regex over
# the flattened text cannot locate titles reliably (the intro's trailing
# ``[pause:1s]`` would false-match body text) and the raw heading string can
# differ from the cleaned text (entities, URLs, pronunciation rewrites), so the
# mark is the position source of truth.
_SECTION_MARK = "\ue000"

# Wallabag reader output also marks section titles as paragraphs whose entire
# text is bold (<p><strong>Title</strong></p>, <b> variant included). Those
# pseudo-headings get the same treatment as real heading tags.
_BOLD_PARAGRAPH_TAGS = ("strong", "b")

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


def _all_text_is_bold(node: Tag) -> bool:
    """True when every non-whitespace string descends from <strong>/<b>.

    Partially-bold paragraphs ("<b>Lead</b> rest of sentence") return False,
    keeping ordinary emphasized prose out of the section-title path.
    """
    for part in node.descendants:
        if isinstance(part, NavigableString) and part.strip():
            if not any(a.name in _BOLD_PARAGRAPH_TAGS for a in part.parents):
                return False
    return True


def _section_title_token(text: str, marked: bool = False) -> str:
    """Pause-wrapped, period-terminated TTS fragment for one section title.

    When ``marked`` is true a private-use sentinel precedes the leading pause
    token so :func:`split_tts_text` can align the title to a chunk boundary
    and the pipeline can compute chapter start times.
    """
    mark = _SECTION_MARK if marked else ""
    return (
        f"{mark}{_HEADING_PAUSE_BEFORE} "
        f"{_ensure_terminal_punctuation(text)} {_HEADING_PAUSE_AFTER}"
    )


def _wrap_section_titles_with_pauses(
    soup: BeautifulSoup, titles: list[str] | None = None
) -> None:
    """Replace each section title with a pause-wrapped, period-terminated string.

    Section titles are h1-h6 headings plus paragraphs whose entire text is
    bold (<p><strong>Title</strong></p>). Empty/whitespace-only titles are
    skipped; nested inline markup inside a title (<h2><em>Title</em></h2>)
    is flattened by get_text. Consecutive titles produce adjacent pause
    pairs — Kokoro handles repeated tokens.

    When ``titles`` is given, each title's raw text is appended to it in
    document order and its token is prefixed with ``_SECTION_MARK`` so the
    caller can map titles to synthesis chunks (chapter markers).
    """
    marked = titles is not None
    # One document-order pass over every heading and paragraph so collected
    # titles match the order their sentinels appear in the flattened text
    # (a bold-paragraph title may precede a heading).
    for node in soup.find_all((*_HEADINGS, "p")):
        if node.name in _HEADINGS:
            text = node.get_text(" ", strip=True)
        else:
            if not _all_text_is_bold(node):
                continue
            text = node.get_text(" ", strip=True)
        if not text:
            continue
        node.replace_with(NavigableString(_section_title_token(text, marked)))
        if titles is not None:
            titles.append(text)


def _extract_text(html: str, titles: list[str] | None = None) -> str:
    """Parse HTML, drop non-content elements, and return plain text.

    When ``titles`` is given it is filled with the section titles in document
    order and their tokens carry ``_SECTION_MARK`` (see
    :func:`_wrap_section_titles_with_pauses`).
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in _REMOVE_TAGS:
        for node in soup.find_all(tag):
            node.decompose()
    _wrap_section_titles_with_pauses(soup, titles)
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


def _strip_orphan_section_mark(text: str) -> str:
    """Drop a trailing section-title mark orphaned by boilerplate truncation.

    ``_remove_boilerplate`` cuts a suffix, so a title near the end of the text
    can be partly or wholly removed while its sentinel survives. A live title
    token carries its own leading AND closing pause; a fragment after the last
    mark with fewer than two ``[pause:1s]`` tokens is an orphan (the title text
    was cut) and is dropped. Live tokens always keep their closing pause, so
    they are never mistaken for orphans regardless of what body text follows.
    """
    index = text.rfind(_SECTION_MARK)
    if index == -1:
        return text
    tail = text[index:]
    if tail.count(_HEADING_PAUSE_AFTER) >= 2:
        return text
    return text[:index].rstrip()


def has_section_mark(text: str) -> bool:
    """True when ``text`` contains a section-title sentinel."""
    return _SECTION_MARK in text


def strip_section_mark(text: str) -> str:
    """Remove every section-title sentinel so the text is TTS-safe.

    The sentinel is only a chapter-position marker; it must never reach the
    Kokoro server.
    """
    return text.replace(_SECTION_MARK, "")


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


def _clean_body(
    html: str, min_chars: int | None, titles: list[str] | None
) -> str:
    threshold = get_settings().MIN_TEXT_CHARS if min_chars is None else min_chars

    text = _extract_text(html, titles)
    text = _html.unescape(text)
    text = _remove_urls(text)
    text = _remove_emails(text)
    text = _remove_boilerplate(text)
    text = _strip_orphan_section_mark(text)
    text = _normalize_ws(text)
    text = _ensure_terminal_punctuation(text)

    if len(text) < threshold:
        raise SkipArticle(
            f"Article text too short for TTS: {len(text)} chars "
            f"(minimum {threshold})"
        )
    return text


def clean_body(html: str, min_chars: int | None = None) -> str:
    """Parse and clean HTML content into spoken-word prose.

    Raises :class:`SkipArticle` if the cleaned text is shorter than
    ``min_chars`` (default: ``Settings.MIN_TEXT_CHARS``).
    """
    return _clean_body(html, min_chars, None)


def clean_body_with_sections(
    html: str, min_chars: int | None = None
) -> tuple[str, list[str]]:
    """Like :func:`clean_body` but also return the section titles.

    Returns ``(text, titles)`` where ``titles`` holds each section title's raw
    text in document order and ``text`` carries ``_SECTION_MARK`` sentinels at
    each title position (strip them before synthesis; see
    :func:`strip_section_mark`).
    """
    titles: list[str] = []
    text = _clean_body(html, min_chars, titles)
    return text, titles


# Sentence boundary: whitespace following terminal punctuation. Splitting here
# keeps each TTS request on a natural spoken boundary. ``[pause:...]`` tokens
# contain no terminal punctuation, so a boundary can never fall inside one.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _split_oversized_sentence(sentence: str, max_chars: int) -> list[str]:
    """Hard-split one over-long sentence into word-boundary parts."""
    parts: list[str] = []
    current = ""
    for word in sentence.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) > max_chars and current:
            parts.append(current)
            current = word
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_tts_text(text: str, max_chars: int | None = None) -> list[str]:
    """Split a TTS input string into chunks of at most ``max_chars`` characters.

    Chunks are assembled from whole sentences (split after terminal
    punctuation) so every synthesized chunk ends at a natural spoken boundary.
    A single sentence longer than ``max_chars`` is hard-split at word
    boundaries. ``[pause:...]`` tokens are never cut in half. A sentence
    containing a section-title sentinel (``_SECTION_MARK``) always begins a
    new chunk, so every section title aligns with a chunk boundary — the
    pipeline uses the cumulative duration of the preceding chunks as the
    chapter's start time. Joining the returned chunks with single spaces
    reconstructs the input text exactly. When ``max_chars`` is None the
    default ``Settings.KOKORO_MAX_CHUNK_CHARS`` is used.
    """
    limit = get_settings().KOKORO_MAX_CHUNK_CHARS if max_chars is None else max_chars

    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    sentences = [s for s in _SENTENCE_BOUNDARY_RE.split(text) if s]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        # A section title (sentinel sentence) starts a new chunk. The mark may
        # sit mid-sentence — after a previous title's trailing pause token in
        # the consecutive-titles case — so test for containment, not a prefix.
        if _SECTION_MARK in sentence and current:
            chunks.append(current)
            current = ""
        if len(sentence) > limit:
            # Flush what we have, then hard-split the oversized sentence.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_oversized_sentence(sentence, limit))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_tts_input_with_sections(
    title: str, html: str, min_chars: int | None = None
) -> tuple[str, list[str]]:
    """Assemble the TTS input string and its section titles.

    Same assembly as :func:`build_tts_input`, but the returned text carries
    ``_SECTION_MARK`` sentinels at each section title (strip before synthesis;
    see :func:`strip_section_mark`) and the second element is the ordered list
    of section titles, for chapter markers. When ``min_chars`` is ``None`` the
    body is length-guarded with the default ``Settings.MIN_TEXT_CHARS``
    (raises :class:`SkipArticle`); pass an explicit value to override. Both
    title and body pass through :func:`apply_pronunciations`
    (``Settings.PRONUNCIATIONS``) before assembly, so the ``[pause:...]``
    tokens themselves are never rewritten.
    """
    clean = clean_title(title)
    body, titles = clean_body_with_sections(html, min_chars=min_chars)
    pronunciations = get_settings().PRONUNCIATIONS
    clean = apply_pronunciations(clean, pronunciations)
    body = apply_pronunciations(body, pronunciations)
    return f"[pause:0.5s] {clean} [pause:1s] {body}", titles


def build_tts_input(title: str, html: str, min_chars: int | None = None) -> str:
    """Assemble the exact TTS input string for an article.

    ``[pause:0.5s] {clean_title} [pause:1s] {clean_body}``. When ``min_chars``
    is ``None`` the body is length-guarded with the default
    ``Settings.MIN_TEXT_CHARS`` (raises :class:`SkipArticle`); pass an
    explicit value to override. Both title and body pass through
    :func:`apply_pronunciations` (``Settings.PRONUNCIATIONS``) before
    assembly, so the ``[pause:...]`` tokens themselves are never rewritten.
    """
    text, _ = build_tts_input_with_sections(title, html, min_chars=min_chars)
    return strip_section_mark(text)


def build_tts_input_from_article_with_sections(
    article: ArticleFull, min_chars: int | None = None
) -> tuple[str, list[str]]:
    """Assemble the TTS input string and section titles from an article."""
    return build_tts_input_with_sections(
        article.title, article.content, min_chars=min_chars
    )


def build_tts_input_from_article(
    article: ArticleFull, min_chars: int | None = None
) -> str:
    """Assemble the TTS input string from a Wallabag :class:`ArticleFull`."""
    return build_tts_input(article.title, article.content, min_chars=min_chars)
