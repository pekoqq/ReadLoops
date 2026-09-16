"""AI 接口层：文章生成、单词查询。"""
import json
import re
import time

import httpx

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from app.database import get_db
from app.models import Article, Word
from app.services.distill import profile_prompt_hint


def _get_settings():
    """从数据库读取设置，覆盖默认配置。"""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        s = {r["key"]: r["value"] for r in rows}
    return {
        "base_url": s.get("ai_base_url", AI_BASE_URL),
        "api_key": s.get("ai_api_key", AI_API_KEY),
        "model": s.get("ai_model", AI_MODEL),
    }


def _chat(messages, max_tokens=1500, temperature=0.7, retries=2):
    """调用 AI 聊天接口，关闭思考模式。

    deepseek-flash 等模型偶发：① 网络抖动 / 5xx；② 思考没被关住，正文为空、
    内容落到 reasoning_content。两类都按可恢复错误处理，最多重试 ``retries`` 次。
    """
    cfg = _get_settings()
    if not cfg["api_key"]:
        raise ValueError("未配置 API Key")
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg["model"],
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "thinking": {"type": "disabled"},
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            message = data["choices"][0]["message"]
            content = (message.get("content") or "").strip()
            if not content:
                # 正文为空通常是思考泄漏到 reasoning_content —— 关思考没生效，值得重试
                reasoning = (message.get("reasoning_content") or "").strip()
                raise ValueError(f"AI 返回内容为空（reasoning_content {len(reasoning)} 字）")
            return content
        except Exception as exc:  # 网络 / HTTP / 空内容都走重试
            last_exc = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
    raise last_exc


def _extract_json(text):
    """从 AI 返回内容中提取 JSON，处理 markdown 包裹等情况。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"无法从 AI 返回中提取 JSON: {text[:200]}")


def _get_recent_used_words(limit=50):
    """获取最近 N 篇文章用过的词，用于避免重复。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT target_words FROM articles ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    used = set()
    for r in rows:
        try:
            words = json.loads(r["target_words"] or "[]")
            used.update(words)
        except Exception:
            pass
    return used


def _select_new_words(target_count):
    """挑这一篇要埋的目标生词。

    已迁到 `app/services/selection.py` —— 旧实现只按 FSRS 到期 + 词频选词，
    不知道「哪些词已经掌握了」「你的目标考试是什么」「某个词已经见过几篇」，
    于是统计页算出的缺口和下一篇读什么毫无关系。新实现按掌握度分层：

        到期复习 → 重遇缺口（见过但识别未建立）→ 目标考试新词（高频优先）→ 兜底

    并明确排除 `recalled`（已经能想起来，不该再占生词额度）。
    """
    from app.services import selection
    recent_used = _get_recent_used_words(30)
    return selection.select_new_words(target_count=target_count, exclude=recent_used)


def _generate_once(topic, opening, ending, new_words):
    """单次生成文章，不做去重检测。"""
    import json as _json
    import random as _random

    # 查询目标词的词形变化
    word_families = {}
    with get_db() as conn:
        placeholders = ",".join(["?"] * len(new_words))
        rows = conn.execute(
            f"SELECT text, exchange FROM words WHERE text IN ({placeholders})",
            new_words
        ).fetchall()
        for row in rows:
            if row['exchange']:
                try:
                    ex = _json.loads(row['exchange'])
                    if ex:
                        word_families[row['text']] = ex
                except Exception:
                    pass

    # 从短语库选2-3个目标短语（高频优先，随机选）
    target_phrases = []
    with get_db() as conn:
        phrases = conn.execute(
            "SELECT text FROM phrases WHERE frequency >= 3 ORDER BY frequency DESC LIMIT 50"
        ).fetchall()
        if phrases:
            phrase_texts = [p['text'] for p in phrases]
            target_phrases = _random.sample(phrase_texts, min(3, len(phrase_texts)))

    # 构建词族描述
    family_desc = ""
    if word_families:
        family_parts = []
        for word, forms in word_families.items():
            form_strs = [f"{v}({k})" for k, v in forms.items() if k != 'lemma']
            if form_strs:
                family_parts.append(f"{word}: {', '.join(form_strs[:3])}")
        if family_parts:
            family_desc = "\n".join(family_parts)

    words_str = ", ".join(new_words)
    phrases_str = ", ".join(target_phrases) if target_phrases else "high school, years ago, long term, social media"
    # 用户在「材料」页激活过的风格画像（只有统计特征，不含原文）。
    #
    # ⚠️ 这里改过一次定位：原先叫 ACTIVE PERSONAL STYLE PROFILE，并且写着
    # "Follow this profile while still meeting the requirements below"，
    # 又排在真题基准**之前** —— 效果是让画像压过基准。而画像来自用户自己的
    # 材料，可能是一本 19 世纪小说（实测《傲慢与偏见》短句占比 29% vs 四级真题
    # 10%、平均词长 4.41 vs 4.85），整篇文风会被带偏。
    # 现在：真题基准是骨架且排在前面，画像只作为「语气微调」出现在最后。
    active_style_hint = profile_prompt_hint()
    style_profile_block = (
        "\n=== OPTIONAL REGISTER NOTE (from your own materials) ===\n"
        f"{active_style_hint}\n"
        "Treat this as a light touch ONLY. The CET-4 baseline above always wins on "
        "sentence length, sentence complexity, and vocabulary coverage.\n"
        if active_style_hint else ""
    )

    prompt = f"""Write a CET-4 style reading passage (~320 words) for a Chinese college student.

Topic: {topic}
Opening: {opening}
Ending: {ending}

=== STYLE REQUIREMENTS (measured from 273 real CET-4 passages) ===

TEXT LENGTH & SENTENCES:
- Target: ~320 words, 15-18 sentences
- Average sentence length: 19 words (p10 8, median 18, p90 32)
- Mix: 19% short sentences (<=10 words), 42% medium (11-20 words), 39% long (>20 words)
- Use compound sentences (and/but/or/so) in ~65% of sentences
- Use relative clauses (which/that/who) in ~33% of sentences
- Use adverbial clauses (because/although/if/when/while) in ~17% of sentences
- Include at least 2 long complex sentences (>25 words) with nested clauses

TOP CONJUNCTIONS TO USE NATURALLY:
and, that, as, but, or, when, who, if, so, which, because, while

VOCABULARY:
- Average word length: 4.9 letters
- Use common CET-4 level words
- Top content words in real passages: more, people, time, new, work, says, like, said, also, other, research, study
- Common phrases: high school, years ago, long term, young people, social media, climate change, work life, health care

WORD FAMILIES (use these word forms naturally in context, not just the base word):
{family_desc if family_desc else 'Use varied word forms (noun/verb/adjective) of key words naturally.'}

TARGET PHRASES (naturally include at least 2 of these):
{phrases_str}

TONE & STYLE:
- Objective, informative, journalistic tone
- Include specific numbers, data, or research findings
- Cite studies, experts, or surveys to support points
- Discuss real-world social phenomena
- NO first-person opinions (no "I think", "we should" as personal advice)
- NO exclamation marks
- NO emotional or subjective language

OPENING (MUST follow one of these patterns):
- A concrete fact or statistic: "A recent study found that..."
- A specific scene: "Across the country, more and more people..."
- A research finding: "Researchers at X university discovered that..."
- A question: "Why do people...?"
- A contrast: "Despite..., many people still..."

ENDING (MUST follow one of these patterns):
- A factual statement: "The findings suggest that..."
- A modal verb: "This may change how we...", "It could lead to..."
- A mild caveat: "But more research is needed to..."

ABSOLUTELY FORBIDDEN:
- "In today's fast-paced world"
- "In conclusion", "Therefore", "As we all know"
- "It is important to note that"
- "Nowadays", "With the development of society"
- Any AI-generated cliché or template phrase

=== TARGET WORDS ===
Naturally include these words in context (do NOT force them, do NOT list them): {words_str}
Use each target word at least once.
{style_profile_block}

Return ONLY valid JSON, no markdown, no explanation:
{{"title": "A concise title (5-8 words, title case)", "content": "The full passage (~320 words)", "new_words": ["word1", "word2"]}}"""

    try:
        raw = _chat([{"role": "user", "content": prompt}], max_tokens=1500, temperature=0.7)
        data = _extract_json(raw)
    except Exception as e:
        print(f"AI 生成失败: {e}")
        return None

    if not data:
        return None

    title = data.get("title", "Untitled")
    content = data.get("content", "")
    article_words = data.get("new_words", [])

    if not content:
        return None

    return {
        "title": title,
        "content": content,
        "new_words": article_words,
        "topic": topic,
    }


def recognize_batch_vocabulary(raw_text: str) -> dict:
    """把用户任意粘贴的词汇材料识别成结构化词条。

    AI 是增强层：能识别「word - 中文释义」「含音标的词表」「编号条目」甚至
    混合的笔记。服务不可用时返回本地解析结果，批量添加不会被 API 配置卡死。
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {"items": [], "mode": "local", "warning": "内容为空"}
    # 防止一次粘贴整本书导致无意义的大模型请求；本地兜底仍会可用。
    clipped = raw_text[:12000]
    prompt = f"""Extract English vocabulary entries from the text below.

Each entry may contain an English word or short phrase, Chinese/English meaning,
part of speech, IPA, and a level. Ignore headings, example sentences, numbering,
and non-vocabulary noise. Do not invent entries or meanings.

Return ONLY valid JSON in this exact shape:
{{"items":[{{"word":"lowercase word or phrase","meaning":"concise Chinese meaning or empty string","phonetic":"IPA or empty string","level":"CET4|CET6|other"}}]}}

Rules:
- Keep only English words or short phrases (1-4 words).
- Deduplicate case-insensitively.
- Use CET4 when level is absent.
- Preserve a user-provided meaning; leave it empty if unknown.

TEXT:
{clipped}"""
    try:
        raw = _chat([{"role": "user", "content": prompt}], max_tokens=1800, temperature=0.1)
        data = _extract_json(raw) or {}
        items = _normalize_vocab_items(data.get("items", []))
        if items:
            return {"items": items, "mode": "ai", "warning": ""}
    except Exception as exc:
        return {
            "items": _local_vocab_parse(raw_text),
            "mode": "local",
            "warning": f"AI 识别暂不可用，已使用本地解析：{str(exc)[:100]}",
        }
    return {"items": _local_vocab_parse(raw_text), "mode": "local",
            "warning": "AI 未识别出有效条目，已使用本地解析"}


def _normalize_vocab_items(items) -> list[dict]:
    """校验 AI 返回，防止模型输出句子、重复项或异常 level 直接进库。"""
    out, seen = [], set()
    valid_levels = {"CET4", "CET6", "other"}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        word = re.sub(r"\s+", " ", str(item.get("word", "")).strip().lower())
        # 英文词或至多 4 个英文单词组成的短语
        if not re.fullmatch(r"[a-z][a-z'-]*(?: [a-z][a-z'-]*){0,3}", word):
            continue
        if word in seen:
            continue
        seen.add(word)
        level = str(item.get("level", "CET4")).upper()
        if level not in valid_levels:
            level = "CET4"
        out.append({
            "word": word,
            "meaning": str(item.get("meaning", "")).strip()[:300],
            "phonetic": str(item.get("phonetic", "")).strip()[:100],
            "level": level,
        })
    return out[:300]


def _local_vocab_parse(text: str) -> list[dict]:
    """无 API 时的保底解析：宁可少识别，也不把 n./v.、例句等噪声当成词。"""
    candidates = []
    # 有换行时每行通常是一条；没有换行则按常见分隔符拆成独立词。
    lines = text.splitlines() if "\n" in text else re.split(r"[,，;；\s]+", text)
    pos_re = r"(?:n|v|vi|vt|adj|adv|prep|conj|pron|num|art)\.?"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:\(?\d+\)?[.、)\s]*)", "", line)
        # 去掉行首 IPA：/ˈkɒnsɪkwəns/ consequence: 后果
        line = re.sub(r"^/[^/]+/\s*", "", line)
        # 首选「词/短语 - 释义」格式
        dash = re.match(r"^([A-Za-z][A-Za-z' -]{0,60}?)\s*[-–—:：]\s*(.+)$", line)
        if dash:
            word = re.sub(r"\s+", " ", dash.group(1)).strip()
            candidates.append({"word": word, "meaning": dash.group(2).strip()})
            continue
        # 无破折号但含中文：取第一个英文词，余下部分作为释义（跳过词性）。
        if re.search(r"[\u4e00-\u9fff]", line):
            m = re.match(rf"^([A-Za-z][A-Za-z'-]*)(?:\s+{pos_re})?\s*(.*)$", line, re.I)
            if m:
                candidates.append({"word": m.group(1), "meaning": m.group(2).strip(" .;；，,")})
            continue
        # 纯英文：只接受单词/短语；排除单个词性缩写。
        for w in re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", line):
            if w.lower().rstrip(".") not in {"n", "v", "vi", "vt", "adj", "adv", "prep", "conj", "pron", "num", "art"}:
                candidates.append({"word": w, "meaning": ""})
    return _normalize_vocab_items(candidates)


def generate_article(target_new_words=10):
    """生成一篇英语短文，严格参考四级真题阅读风格。
    加入去重检测：如果与最近文章太相似，换主题重新生成（最多3次）。
    """
    import random

    from app.services.similarity import check_duplicate

    new_words = _select_new_words(target_new_words)

    # 题材列表
    all_topics = [
        "a social phenomenon or trend observed in modern society",
        "the impact of technology on daily life or work",
        "a health or medical study finding",
        "an education issue or learning method",
        "an environmental concern or solution",
        "a workplace or career trend",
        "a psychological finding about human behavior",
        "a cultural tradition or social change",
        "a small business or community project",
        "a relationship between family members or friends",
    ]

    openings = [
        "Start with a concrete fact or statistic",
        "Start with a specific scene or observation",
        "Start with a research finding or study result",
        "Start with a question that the article answers",
        "Start with a contrast or surprising fact",
    ]

    endings = [
        "End with a factual statement or observation",
        "End with a modal verb (may/could/should) expressing possibility or suggestion",
        "End with a mild contrast or caveat (but/however)",
    ]

    # 获取最近10篇文章用于去重检测
    with get_db() as conn:
        recent_articles = conn.execute(
            "SELECT id, title, content FROM articles ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

    # 最多尝试3次，每次换不同主题
    used_topics = []
    best_result = None
    best_similarity = 1.0

    for attempt in range(3):
        # 选择一个没用过的主题
        available_topics = [t for t in all_topics if t not in used_topics]
        if not available_topics:
            available_topics = all_topics
        topic = random.choice(available_topics)
        used_topics.append(topic)

        opening = random.choice(openings)
        ending = random.choice(endings)

        result = _generate_once(topic, opening, ending, new_words)
        if not result:
            continue

        # 去重检测
        is_dup, sim, sim_title = check_duplicate(result["content"], recent_articles, threshold=0.4)

        if not is_dup:
            # 不重复，直接用
            best_result = result
            best_similarity = sim
            break

        # 重复了，记录最好的结果（相似度最低的）
        if sim < best_similarity:
            best_result = result
            best_similarity = sim

        print(f"第{attempt+1}次生成与「{sim_title}」相似度{sim:.1%}，换主题重试")

    if not best_result:
        return None

    # 保存文章
    title = best_result["title"]
    content = best_result["content"]
    article_words = best_result["new_words"]

    word_count = len(content.split())
    now = int(time.time())

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO articles (title, content, source, word_count, target_words, new_word_count, created_at) "
            "VALUES (?, ?, 'ai', ?, ?, ?, ?)",
            (title, content, word_count, json.dumps(article_words), len(article_words), now),
        )
        article_id = cur.lastrowid

    return Article(
        id=article_id,
        title=title,
        content=content,
        word_count=word_count,
        # ⚠️ 必须带上 target_words：Article 的默认值是 "[]"，
        # 漏掉它会让调用方（API 响应、重遇记录）都拿到空列表 ——
        # 于是「文章里埋了哪些目标生词」这条信息在生成之后就丢了，
        # 重遇机制只能统计到正文里偶然出现的旧词。
        target_words=json.dumps(article_words, ensure_ascii=False),
        new_word_count=len(article_words),
        created_at=now,
    )


def get_generator(count=20, difficulty="mixed", source="all"):
    """生成单词测试题目。返回 (word, options, answer) 列表。
    difficulty: easy(高频)/medium(中频)/hard(低频)/mixed(混合)
    source: all(全部词库)/vocab(生词本)/target(测验词)
    """
    import random
    with get_db() as conn:
        # 按范围筛选正确答案的词
        where = "meaning IS NOT NULL AND meaning != ''"
        params = []
        if source == "vocab":
            where += " AND status = 'learning'"
        elif source == "target":
            where += " AND status = 'target'"
        elif source == "known":
            where += " AND status = 'known'"

        # 按难度筛选（用单词长度近似：短词简单，长词困难）
        if difficulty == "easy":
            where += " AND length(text) <= 6"
        elif difficulty == "medium":
            where += " AND length(text) BETWEEN 7 AND 9"
        elif difficulty == "hard":
            where += " AND length(text) >= 10"

        words = conn.execute(
            f"SELECT text, meaning FROM words WHERE {where} ORDER BY RANDOM() LIMIT ?",
            (*params, count * 3),
        ).fetchall()

        # 干扰项从全部有释义的词中选
        all_words = conn.execute(
            "SELECT text, meaning FROM words WHERE meaning IS NOT NULL AND meaning != '' ORDER BY RANDOM() LIMIT ?",
            (count * 5,),
        ).fetchall()

    if len(words) < count:
        count = len(words)

    result = []
    for i in range(min(count, len(words))):
        correct = words[i]
        # 选3个干扰项
        distractors = random.sample(
            [w for w in all_words if w["text"] != correct["text"]],
            min(3, len(all_words) - 1)
        )
        options = [correct["meaning"]] + [d["meaning"] for d in distractors]
        random.shuffle(options)
        result.append({
            "word": correct["text"],
            "options": options,
            "answer": correct["meaning"],
        })
    return result


def lookup_word(word):
    """查询单词释义。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, text, meaning, phonetic FROM words WHERE text = ?", (word,)
        ).fetchone()
        if row and row["meaning"]:
            return Word(
                id=row["id"],
                text=row["text"],
                meaning=row["meaning"],
                phonetic=row["phonetic"],
            )

    prompt = f"""Define the English word "{word}" for a CET-4 learner.
Return ONLY valid JSON:
{{"word": "{word}", "phonetic": "/音标/", "meaning": "中文释义，简短准确", "example": "一个例句"}}"""
    try:
        raw = _chat([{"role": "user", "content": prompt}], max_tokens=300, temperature=0.3)
        data = _extract_json(raw)
        meaning = data.get("meaning", "")
        phonetic = data.get("phonetic", "")
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM words WHERE text = ?", (word,)).fetchone()
            now = int(time.time())
            if existing:
                conn.execute(
                    "UPDATE words SET meaning=?, phonetic=?, updated_at=? WHERE id=?",
                    (meaning, phonetic, now, existing["id"]),
                )
                wid = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO words (lemma, text, type, meaning, phonetic, level, created_at, updated_at) "
                    "VALUES (?, ?, 'word', ?, ?, 'CET4', ?, ?)",
                    (word, word, meaning, phonetic, now, now),
                )
                wid = cur.lastrowid
        return Word(id=wid, text=word, meaning=meaning, phonetic=phonetic)
    except Exception as e:
        print(f"查词失败: {e}")
        return Word(id=None, text=word, meaning="查询失败", phonetic="")


def translate_sentence(text):
    """翻译英文句子 / 短语为中文。

    划词选中多个单词时走这里，而不是查词典
    （词典查不到整句，之前会直接显示「查询失败」）。
    """
    prompt = f"""Translate the following English text into natural, fluent Chinese.
Return ONLY the translation itself — no explanation, no quotes, no pinyin.

Text: {text}"""
    result = _chat([{"role": "user", "content": prompt}], max_tokens=500, temperature=0.3)
    return (result or "").strip().strip('"').strip("'")
