"""Tests for ID3v2 chapter embedding (app/chapters.py).

Uses the packaged silent MP3 asset as a real, offline MP3 fixture so the
mutagen write/read round-trip is exercised against actual frame data.
"""

from pathlib import Path

from mutagen.id3 import ID3

from app.chapters import write_id3_chapters


def _mp3(tmp_path: Path) -> Path:
    src = Path(__file__).parent.parent / "app" / "assets" / "gap_1s.mp3"
    target = tmp_path / "episode.mp3"
    target.write_bytes(src.read_bytes())
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
    assert ID3(path).getall("CHAP") == []


def test_embedding_failure_returns_false_without_raising(tmp_path):
    bogus = tmp_path / "not_an_mp3.mp3"
    bogus.write_bytes(b"this is not an mp3 file")
    assert write_id3_chapters(bogus, [(0, "X")], total_ms=1000) is False