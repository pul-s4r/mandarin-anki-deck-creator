from anki_pipeline_core.models import Tag, VocabularyTerm, compute_card_content_hash


def test_vocabulary_term_fields() -> None:
    term = VocabularyTerm(term_id="t1", simplified="房租")
    assert term.term_id == "t1"
    assert term.simplified == "房租"


def test_tag_defaults() -> None:
    tag = Tag(tag_id="tag-1", term_id="t1", dimension="topic", value="Housing")
    assert tag.confirmed is False
    assert tag.source == "inferred"


def test_compute_card_content_hash_stable() -> None:
    a = compute_card_content_hash(
        simplified="x",
        traditional="",
        pinyin="",
        meaning="y",
        part_of_speech="",
        usage_notes="",
    )
    b = compute_card_content_hash(
        simplified="x",
        traditional="",
        pinyin="",
        meaning="y",
        part_of_speech="",
        usage_notes="",
    )
    assert a == b
