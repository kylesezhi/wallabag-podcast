"""ID3v2 chapter marker embedding for generated episode MP3s.

After a part file is renamed to its final ``{id}.mp3``, :func:`write_id3_chapters`
stamps an ordered table of contents (``CTOC``, element id ``toc``) plus one
``CHAP`` frame per chapter into the file's ID3v2 tag. Players that support MP3
chapters (Apple Podcasts, Overcast, Pocket Casts, AntennaPod, ...) use these to
show headings while listening.

Chapter start times are millisecond offsets computed by the pipeline from
per-chunk synthesis durations; the last chapter's end time is the episode's
total duration. Embedding is pure decoration: :func:`write_id3_chapters` never
raises, so a tagging failure can only log a warning and the episode is still
marked done.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mutagen.id3 import CHAP, CTOC, CTOCFlags, ID3, TIT2

logger = logging.getLogger(__name__)

# CTOC "top-level" (bit 1) + "ordered" (bit 0) flags: a single, ordered table.
_TOC_FLAGS = int(CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED)
_TOC_ELEMENT_ID = "toc"


def write_id3_chapters(
    path: Path, chapters: list[tuple[int, str]], total_ms: int
) -> bool:
    """Embed ID3v2 chapter frames into the MP3 at ``path``.

    ``chapters`` is an ordered list of ``(start_ms, title)`` pairs; the first
    is conventionally 0 (the intro chapter). The last chapter's end time is
    ``total_ms``; earlier chapters end at the next chapter's start. Returns
    True on success and False on any failure — never raises, so chapter
    tagging can never break an otherwise finished episode.
    """
    if not chapters:
        return False
    try:
        tags = ID3(path)
        child_ids: list[str] = []
        for index, (start_ms, title) in enumerate(chapters):
            element_id = f"chp{index}"
            child_ids.append(element_id)
            if index + 1 < len(chapters):
                end_ms = chapters[index + 1][0]
            else:
                end_ms = max(total_ms, start_ms)
            tags.add(
                CHAP(
                    element_id=element_id,
                    start_time=int(start_ms),
                    end_time=int(end_ms),
                    sub_frames=[TIT2(encoding=3, text=[title])],
                )
            )
        tags.add(
            CTOC(
                element_id=_TOC_ELEMENT_ID,
                flags=_TOC_FLAGS,
                child_element_ids=child_ids,
                sub_frames=[TIT2(encoding=3, text=["Chapters"])],
            )
        )
        tags.save(path, v2_version=3)
        return True
    except Exception:
        logger.warning("Failed to embed chapters in %s", path, exc_info=True)
        return False