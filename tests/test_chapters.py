"""Tests for ID3v2 chapter embedding (app/chapters.py).

Builds a small real MP3 in-process (the same lameenc encoder the pipeline
uses) so the mutagen write/read round-trip is exercised against actual
frame data, fully offline.
"""

from pathlib import Path

import pytest
from mutagen.id3 import ID3, ID3NoHeaderError

from app.chapters import write_id3_chapters
from app.tts import encode_silence


def _mp3(tmp_path: Path) -> Path:
    """A ~3-second real MP3 fixture (three seconds of silence)."""
    target = tmp_path / "episode.mp3"
    target.write_bytes(encode_silence(3.0))
    return target


def test_writes_chapters_round_trip(tmp_path):
    path = _mp3(tmp_path)
    ok = write_id3_chapters(
        path,
        [(0, "Episode Title"), (1250, "First Section"), (25000, "Last Section")],
        total_ms=30000,
    )
    assert ok is True

    tags = ID3(path)
    chaps = sorted(tags.getall("CHAP"), key=lambda c: c.element_id)
    assert [c.element_id for c in chaps] == ["chp0", "chp1", "chp2"]
    assert chaps[0].start_time == 0 and chaps[0].end_time == 1250
    assert chaps[1].start_time == 1250 and chaps[1].end_time == 25000
    assert chaps[2].start_time == 25000 and chaps[2].end_time == 30000
    assert [c.sub_frames["TIT2"].text[0] for c in chaps] == [
        "Episode Title",
        "First Section",
        "Last Section",
    ]

    toc = tags.getall("CTOC")
    assert len(toc) == 1
    assert toc[0].element_id == "toc"
    assert toc[0].child_element_ids == ["chp0", "chp1", "chp2"]
    # Top-level + ordered flags.
    assert int(toc[0].flags) & 0x03 == 0x03


def test_last_chapter_end_clamps_to_total_ms(tmp_path):
    path = _mp3(tmp_path)
    assert (
        write_id3_chapters(path, [(0, "Intro"), (5000, "Only")], total_ms=4000)
        is True
    )
    tags = ID3(path)
    chaps = sorted(tags.getall("CHAP"), key=lambda c: c.element_id)
    assert chaps[1].end_time == 5000  # never less than its own start


def test_empty_chapters_is_noop(tmp_path):
    path = _mp3(tmp_path)
    assert write_id3_chapters(path, [], total_ms=1000) is False
    # Nothing was written: a lameenc fixture has no ID3 header at all.
    with pytest.raises(ID3NoHeaderError):
        ID3(path)


def test_embedding_failure_returns_false_without_raising(tmp_path):
    """An unopenable file fails embedding without raising.

    The missing path is the remaining genuine failure path: a bogus-bytes
    file is now legitimately embeddable by design (no ID3 header -> empty
    tag, and mutagen ignores non-audio payload), so unopenable paths are
    what still exercises the failure return.
    """
    # An unopenable file (missing path) fails embedding without raising.
    missing = tmp_path / "missing.mp3"
    assert write_id3_chapters(missing, [(0, "X")], total_ms=1000) is False