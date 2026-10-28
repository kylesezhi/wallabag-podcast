"""HTML-to-speech text cleaning pipeline (regex + BeautifulSoup only).

Turns Wallabag's raw HTML article content into clean, spoken-word prose for
the TTS engine. No LLM, no network. The pipeline:

1. Parse HTML with BeautifulSoup4 + lxml.
2. Remove non-content elements (script/style/nav/footer/img/form/table/...).
3. Replace section titles — h1-h6 headings and paragraphs whose entire text
   is bold (``<p><strong>Title</strong></p>``) — with
   ``[pause:1s] Title. [pause:1s]`` tokens. These tokens are app-side pause
   directives: ``split_tts_text`` places every token at the start of a
   synthesis chunk and the generation pipeline converts each such chunk into
   generated silence followed by the remaining spoken text; tokens must
   never reach the TTS server (the TTS client's defensive strip is the
   second belt). The ``*_with_sections`` variants additionally prefix each
   token
   with a private-use sentinel (``_SECTION_MARK``) and return the title
   strings in document order, so the pipeline can align every title to a
   synthesis chunk and stamp ID3 chapter markers.
4. Extract text with a space separator, unescape residual entities, collapse
   whitespace, drop bare URLs/emails and trailing boilerplate.
5. Ensure terminal punctuation.
6. Assemble the exact TTS input ``[pause:0.5s] {title} [pause:1s] {body}``,
   rewriting ``Settings.PRONUNCIATIONS`` whole-word matches first.

``split_tts_text`` additionally splits a finished TTS input into sentence-boundary
chunks (max ``Settings.TTS_MAX_CHUNK_CHARS`` chars) so the generation pipeline
can synthesize long articles one bounded request at a time. Every
``[pause:Xs]`` token starts a new chunk (the pipeline turns each into
generated silence); sentences carrying a section-title sentinel always begin
a new chunk so chapter starts are exact.

Articles whose cleaned body is shorter than ``MIN_TEXT_CHARS`` raise
:class:`SkipArticle` so the generation pipeline can skip them.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date as _date, datetime as _datetime

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
# ``[pause:Xs]`` markers are app-side pause directives — the same tokens
# already frame the article title in build_tts_input, and the TTS client
# strips them from the text before it reaches the TTS server. Fixed
# durations, same style as those hardcoded title/body pauses.
_HEADING_PAUSE_BEFORE = "[pause:1s]"
_HEADING_PAUSE_AFTER = "[pause:1s]"
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Private-use sentinel prepended to each section-title token when the caller
# asks for chapter positions (``clean_body_with_sections`` /
# ``build_tts_input_with_sections``). It survives every cleaning transform
# untouched and marks exactly where a title starts in the flattened text, then
# is stripped again before synthesis — it must never reach the TTS server. A
# regex over
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
    pairs — accepted by design, same as the repeated title/body tokens.

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
    TTS server.
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


def _parse_wallabag_date(iso: str | None) -> _date | None:
    """Parse a Wallabag date string (ISO 8601 or date-only) into a date.

    Returns ``None`` for blank, unparseable, or implausible (year ``<= 1970``)
    values — Wallabag/Graby sometimes records junk like the epoch for a
    missing ``published_at``.
    """
    if not iso:
        return None
    try:
        value = iso.strip()
        if "T" in value:
            parsed = _datetime.fromisoformat(value).date()
        else:
            parsed = _date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.year <= 1970:
        return None
    return parsed


def _spoken_date(published_at: str | None, created_at: str | None) -> str | None:
    """Format the article's date for speech, e.g. ``July 4, 2024``.

    Prefers ``published_at`` (the article's original publish date) and falls
    back to ``created_at`` (when the article was saved to Wallabag) when the
    publish date is blank, unparseable, or implausible. Returns ``None`` when
    neither yields a usable date — callers then omit the date from the intro.
    The date is kept in Wallabag's own offset (no UTC conversion) so it
    matches what the Wallabag UI shows.
    """
    parsed = _parse_wallabag_date(published_at) or _parse_wallabag_date(created_at)
    if parsed is None:
        return None
    return f"{parsed:%B} {parsed.day}, {parsed.year}"


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

# Canonical emitted-pause grammar: lowercase ``[pause:<number>s]`` where the
# number is an integer or decimal (``[pause:1s]``, ``[pause:0.5s]``). Both the
# chunk splitter and :func:`strip_leading_pauses` share this one pattern, so
# the emitted token and the parsed directive can never drift apart.
_PAUSE_TOKEN_RE = re.compile(r"\[pause:\d+(?:\.\d+)?s\]")


def _split_at_pause_tokens(sentence: str) -> list[str]:
    """Fragment one sentence at every pause token.

    Each occurrence of a ``[pause:Xs]`` token — optionally preceded directly
    by ``_SECTION_MARK`` with no space (``{mark}[pause:1s]``) — starts a new
    fragment: the text is cut immediately BEFORE the token (or before the
    mark+token unit, so the mark stays attached to its token's fragment and
    the two can never separate), and the token OPENS the new fragment. A
    fragment therefore contains at most one token (its leading one), and a
    fragment may be token-only (e.g. a closing ``[pause:1s]`` after a
    heading, or adjacent tokens). A token preceded by neither whitespace nor
    the mark (which the emitted grammar never produces) stays embedded in
    the current fragment. Whitespace around the cuts collapses away, so
    joining the fragments with single spaces reconstructs the sentence.
    """
    fragments: list[str] = []
    current = ""
    position = 0
    for match in _PAUSE_TOKEN_RE.finditer(sentence):
        cut = match.start()
        # Cut before a directly-preceding mark so the mark travels with its
        # token's fragment.
        if cut > 0 and sentence[cut - 1] == _SECTION_MARK:
            cut -= 1
        # Skip a token that is embedded in running text (no whitespace or
        # mark before it) — the emitted grammar never produces one.
        if cut != 0 and sentence[cut - 1] not in (" ", _SECTION_MARK):
            continue
        prefix = sentence[position:cut].strip()
        if prefix:
            current = f"{current} {prefix}" if current else prefix
        if current:
            fragments.append(current)
        current = sentence[cut:match.end()]
        position = match.end()
    tail = sentence[position:].strip()
    if tail:
        current = f"{current} {tail}" if current else tail
    if current:
        fragments.append(current)
    return fragments


def strip_leading_pauses(text: str) -> tuple[list[float], str]:
    """Pop every leading ``[pause:Xs]`` token from ``text``.

    Returns ``(durations, remainder)`` where ``durations`` holds each leading
    token's seconds in order (fractions supported) and ``remainder`` is the
    text after the last leading token, stripped of leading whitespace (the
    same lstripping applies when there are no tokens at all — pipeline chunks
    are whitespace-normalized, so this is always visible-neutral there).
    Only tokens at the very start (with the mark already stripped by the
    caller) are consumed — interior or trailing tokens stay in the remainder.
    No tokens yields ``([], text.lstrip())``.
    """
    durations: list[float] = []
    remainder = text.lstrip()
    while True:
        match = _PAUSE_TOKEN_RE.match(remainder)
        if match is None:
            break
        durations.append(float(match.group(0)[7:-2]))
        remainder = remainder[match.end():].lstrip()
    return durations, remainder


def _split_oversized_sentence(sentence: str, max_chars: int) -> list[str]:
    """Hard-split one over-long sentence into word-boundary parts.

    Sentences reaching this splitter are post-fragmentation pieces carrying
    at most one pause token — their LEADING one — so the first part keeps
    the token and no later part can hold an interior token.
    """
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
    punctuation) so every synthesized chunk ends at a natural spoken
    boundary; a single sentence longer than ``max_chars`` is hard-split at
    word boundaries. ``[pause:Xs]`` tokens are app-side pause directives,
    NOT text for the TTS server: within each sentence the text is fragmented
    at every token (a token may be preceded by a ``_SECTION_MARK`` sentinel,
    which stays glued to its token), and every fragment beginning with a
    token ALWAYS starts a new chunk — the generation pipeline converts such
    a chunk's leading tokens into generated silence before speaking the
    rest. Token-only fragments are legal chunks. Because each fragment
    carries at most its one leading token, no token can land mid-chunk or
    mid-part of an oversized hard-split. A fragment carrying a section-title
    sentinel therefore always begins a chunk too (the mark never separates
    from its token). Joining the returned chunks with single spaces
    reconstructs the input text exactly (the input is whitespace-normalized).
    When ``max_chars`` is None the default ``Settings.TTS_MAX_CHUNK_CHARS``
    is used.
    """
    limit = get_settings().TTS_MAX_CHUNK_CHARS if max_chars is None else max_chars

    text = text.strip()
    if not text:
        return []

    sentences = [s for s in _SENTENCE_BOUNDARY_RE.split(text) if s]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        for fragment in _split_at_pause_tokens(sentence):
            # A fragment beginning with a pause token (with or without the
            # mark) always opens its own chunk; the mark+token unit rule
            # guarantees a marked fragment also begins a chunk, but keep the
            # explicit sentinel check so both rules agree on any input.
            starts_new = _PAUSE_TOKEN_RE.match(fragment) is not None or (
                _SECTION_MARK in fragment and current
            )
            if starts_new and current:
                chunks.append(current)
                current = ""
            if len(fragment) > limit:
                # Flush what we have, then hard-split the oversized fragment.
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(_split_oversized_sentence(fragment, limit))
                continue
            candidate = f"{current} {fragment}" if current else fragment
            if len(candidate) > limit:
                chunks.append(current)
                current = fragment
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_tts_input_with_sections(
    title: str,
    html: str,
    min_chars: int | None = None,
    spoken_date: str | None = None,
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
    tokens themselves are never rewritten. When ``spoken_date`` is given
    (e.g. ``"July 4, 2024"``) it is spoken between the title and the body with
    its own short pause, also passing through ``apply_pronunciations``.
    """
    clean = clean_title(title)
    body, titles = clean_body_with_sections(html, min_chars=min_chars)
    pronunciations = get_settings().PRONUNCIATIONS
    clean = apply_pronunciations(clean, pronunciations)
    body = apply_pronunciations(body, pronunciations)
    if spoken_date:
        spoken_date = apply_pronunciations(spoken_date, pronunciations)
        return (
            f"[pause:0.5s] {clean} [pause:0.5s] {spoken_date} [pause:1s] {body}",
            titles,
        )
    return f"[pause:0.5s] {clean} [pause:1s] {body}", titles


def build_tts_input(
    title: str,
    html: str,
    min_chars: int | None = None,
    spoken_date: str | None = None,
) -> str:
    """Assemble the exact TTS input string for an article.

    ``[pause:0.5s] {clean_title} [pause:1s] {clean_body}`` — with
    ``spoken_date`` given, ``[pause:0.5s] {clean_title} [pause:0.5s] {date}
    [pause:1s] {clean_body}``. When ``min_chars`` is ``None`` the body is
    length-guarded with the default ``Settings.MIN_TEXT_CHARS`` (raises
    :class:`SkipArticle`); pass an explicit value to override. Both title and
    body pass through :func:`apply_pronunciations`
    (``Settings.PRONUNCIATIONS``) before assembly, so the ``[pause:...]``
    tokens themselves are never rewritten.
    """
    text, _ = build_tts_input_with_sections(
        title, html, min_chars=min_chars, spoken_date=spoken_date
    )
    return strip_section_mark(text)


def build_tts_input_from_article_with_sections(
    article: ArticleFull, min_chars: int | None = None
) -> tuple[str, list[str]]:
    """Assemble the TTS input string and section titles from an article."""
    return build_tts_input_with_sections(
        article.title,
        article.content,
        min_chars=min_chars,
        spoken_date=_spoken_date(article.published_at, article.created_at),
    )


def build_tts_input_from_article(
    article: ArticleFull, min_chars: int | None = None
) -> str:
    """Assemble the TTS input string from a Wallabag :class:`ArticleFull`."""
    return build_tts_input(
        article.title,
        article.content,
        min_chars=min_chars,
        spoken_date=_spoken_date(article.published_at, article.created_at),
    )
