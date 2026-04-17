from tianshu.memory.chunker import chunk_text

CHUNK_SIZE = 800


def test_short_text_single_chunk():
    chunks = chunk_text("Hello world", max_chars=CHUNK_SIZE)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_empty_text_no_chunks():
    chunks = chunk_text("", max_chars=CHUNK_SIZE)
    assert chunks == []


def test_whitespace_only_no_chunks():
    chunks = chunk_text("   \n\n  ", max_chars=CHUNK_SIZE)
    assert chunks == []


def test_paragraph_boundary_split():
    p1 = "A" * 500
    p2 = "B" * 500
    text = p1 + "\n\n" + p2
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) == 2
    assert chunks[0] == p1
    assert chunks[1] == p2


def test_long_paragraph_force_split():
    text = "A" * 1600
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) == 2
    assert len(chunks[0]) == CHUNK_SIZE
    assert len(chunks[1]) == CHUNK_SIZE


def test_min_chunk_size_filter():
    chunks = chunk_text("Hi", max_chars=CHUNK_SIZE, min_chars=50)
    assert chunks == []


def test_real_content_chunking():
    text = (
        "## Deployment Lesson\n\n"
        "The CI pipeline failed because we forgot to set DATABASE_URL.\n"
        "This caused a 2-hour outage.\n\n"
        "## Recovery Steps\n\n"
        "1. Set the env var in the deployment config.\n"
        "2. Re-run the pipeline.\n"
        "3. Verify the database connection."
    )
    chunks = chunk_text(text, max_chars=CHUNK_SIZE)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) <= CHUNK_SIZE
        assert len(c) >= 10  # default min_chars
