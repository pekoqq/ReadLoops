"""相似度模块测试（纯函数，无数据库依赖）。"""
from app.services import similarity as sim


def test_tokenize_drops_stopwords_and_short_words():
    tokens = sim.tokenize("The quick brown fox is on a mat")
    assert "quick" in tokens
    assert "the" not in tokens
    assert "on" not in tokens


def test_cosine_identical_vectors_is_one():
    vec = {"alpha": 1.0, "beta": 2.0}
    assert abs(sim.cosine_similarity(vec, vec) - 1.0) < 1e-9


def test_cosine_no_overlap_is_zero():
    assert sim.cosine_similarity({"alpha": 1.0}, {"beta": 1.0}) == 0.0


def test_duplicate_detection_flags_identical_text():
    text = "climate change reshapes agriculture and food production worldwide " * 8
    recent = [{"id": 1, "title": "same", "content": text}]
    is_dup, score, _ = sim.check_duplicate(text, recent, threshold=0.4)
    assert is_dup is True
    assert score > 0.9


def test_duplicate_detection_ignores_unrelated_text():
    recent = [{"id": 1, "title": "x", "content": "quantum physics experiments in laboratories"}]
    is_dup, _, _ = sim.check_duplicate(
        "cooking recipes with fresh vegetables and spices", recent, threshold=0.4
    )
    assert is_dup is False


def test_exam_similarity_without_corpus_returns_empty():
    """真题语料不随仓库分发，缺失时必须安全降级为空列表。"""
    assert sim.get_exam_similarity("some generated article text") == []
