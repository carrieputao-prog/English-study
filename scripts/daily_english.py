#!/usr/bin/env python3
"""Generate and deliver the scheduled English study content."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WORDS_PATH = ROOT / "words0621"
STATE_PATH = ROOT / "data" / "state.json"
OUTPUT_DIR = ROOT / "daily"
TIMEZONE = ZoneInfo("Asia/Shanghai")
STORY_DAYS = {0, 3}  # Monday, Thursday
QUIZ_DAYS = {1, 2, 4, 5}


def parse_words(path: Path = WORDS_PATH) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    item_pattern = re.compile(r"^\s*\*\s+([A-Za-z]+(?:\s*/\s*[A-Za-z]+)?)\s+\([^)]*\)\s+(.+?)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = item_pattern.match(line)
        if not match:
            continue
        terms = [part.strip().lower() for part in match.group(1).split("/")]
        translation = re.sub(r"\s*\[\[.*$", "", match.group(2)).strip()
        for term in terms:
            if term not in seen:
                entries.append({"word": term, "translation": translation})
                seen.add(term)
    if len(entries) < 20:
        raise ValueError(f"词表解析结果异常：只找到 {len(entries)} 个单词")
    return entries


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "word_epoch": 1,
            "used_story_words": [],
            "last_story_words": [],
            "used_question_fingerprints": [],
            "cycle": None,
            "runs": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_story_words(
    entries: list[dict[str, str]], state: dict[str, Any], rng: random.Random
) -> list[dict[str, str]]:
    used = set(state.get("used_story_words", []))
    last = set(state.get("last_story_words", []))
    available = [entry for entry in entries if entry["word"] not in used]
    if len(available) < 10:
        # A finite word list cannot remain globally unique forever. Start a new
        # epoch only after exhausting it, while still excluding the last story.
        state["word_epoch"] = int(state.get("word_epoch", 1)) + 1
        state["used_story_words"] = []
        available = [entry for entry in entries if entry["word"] not in last]
    chosen = rng.sample(available, 10)
    chosen_words = [entry["word"] for entry in chosen]
    state["used_story_words"] = state.get("used_story_words", []) + chosen_words
    state["last_story_words"] = chosen_words
    return chosen


def gemini_json(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY（或 GOOGLE_API_KEY）")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 1.15,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"Gemini API 请求失败：HTTP {exc.code} {detail}") from exc
    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Gemini 返回格式异常：{str(result)[:1000]}") from exc


def _contains_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.IGNORECASE))


def generate_story(words: list[dict[str, str]]) -> dict[str, str]:
    vocabulary = "\n".join(f"- {item['word']}：{item['translation']}" for item in words)
    prompt = f"""你是一名英语学习内容编辑。使用下面恰好 10 个词写一篇逻辑连贯但情节荒诞的短故事。

词表：
{vocabulary}

要求：
1. mixed_story 使用简体中文叙事，每个目标英文词恰好出现一次，并立即紧跟全角括号中文释义，例如 abandon（放弃）。括号之间不能有空格。目标词保持词表原形。
2. english_story 是同一篇故事的自然、纯英文版本，情节和信息一致，10 个目标词都至少出现一次。
3. 两个版本都控制在 180 到 260 个英文词或相当篇幅；荒诞、有画面感，但不低俗。
4. title 使用简体中文，简短有趣。不要输出题目、答案或额外说明。
"""
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "mixed_story": {"type": "string"},
            "english_story": {"type": "string"},
        },
        "required": ["title", "mixed_story", "english_story"],
        "additionalProperties": False,
    }
    for _ in range(3):
        story = gemini_json(prompt, schema)
        mixed = story.get("mixed_story", "")
        english = story.get("english_story", "")
        if all(len(re.findall(rf"(?<![A-Za-z]){re.escape(item['word'])}（", mixed, re.IGNORECASE)) == 1 for item in words) and all(
            _contains_word(english, item["word"]) for item in words
        ):
            return story
        prompt += "\n上一次结果漏词或格式不符。请严格逐项检查 10 个目标词后重新生成。"
    raise RuntimeError("连续 3 次生成的故事都未通过 10 个目标词校验")


def question_fingerprint(question: dict[str, Any]) -> str:
    raw = question.get("question", "") + "|" + "|".join(question.get("options", []))
    normalized = re.sub(r"\s+", "", raw).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_questions(
    questions: list[dict[str, Any]], words: list[dict[str, str]], previous: set[str]
) -> list[str]:
    expected = {item["word"] for item in words}
    if len(questions) != 10 or {q.get("word", "").lower() for q in questions} != expected:
        raise ValueError("题目必须恰好 10 道，且每个目标词各一道")
    fingerprints = [question_fingerprint(q) for q in questions]
    if len(set(fingerprints)) != 10:
        raise ValueError("当天存在重复题目")
    if set(fingerprints) & previous:
        raise ValueError("题目与相邻测试日重复")
    for q in questions:
        options = q.get("options")
        if not isinstance(options, list) or len(options) != 4:
            raise ValueError("每道题必须有 4 个选项")
        if q.get("answer") not in {"A", "B", "C", "D"}:
            raise ValueError("答案必须是 A/B/C/D")
    return fingerprints


def generate_quiz(
    words: list[dict[str, str]],
    previous_questions: list[dict[str, Any]],
    set_number: int,
    forbidden_fingerprints: set[str] | None = None,
) -> list[dict[str, Any]]:
    vocabulary = "\n".join(f"- {item['word']}：{item['translation']}" for item in words)
    previous_text = "\n".join(f"- {q['question']}" for q in previous_questions) or "（无）"
    prompt = f"""请为下面 10 个英语词生成第 {set_number} 套自测题，每个词恰好一道，共 10 道。

词表：
{vocabulary}

相邻测试日已使用的题目（禁止复用、改写或只调整选项顺序）：
{previous_text}

要求：
1. 混合使用语境填空、英译中、中译英、近义辨析、用法判断等题型，随机排列。
2. 每题 4 个选项，答案字段只能是 A、B、C、D；干扰项合理且只有一个正确答案。
3. question 简洁清晰；explanation 使用简体中文，一句话说明原因。
4. word 必须使用词表中的小写原形，用来标记该题考查的词。
5. 10 道题彼此不重复，也不能与上面相邻测试日题目重复。
"""
    item_schema = {
        "type": "object",
        "properties": {
            "word": {"type": "string"},
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
            "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
            "explanation": {"type": "string"},
        },
        "required": ["word", "question", "options", "answer", "explanation"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {"questions": {"type": "array", "items": item_schema, "minItems": 10, "maxItems": 10}},
        "required": ["questions"],
        "additionalProperties": False,
    }
    previous_fingerprints = {question_fingerprint(q) for q in previous_questions}
    all_forbidden = previous_fingerprints | (forbidden_fingerprints or set())
    for _ in range(3):
        result = gemini_json(prompt, schema)
        questions = result.get("questions", [])
        random.SystemRandom().shuffle(questions)
        try:
            validate_questions(questions, words, all_forbidden)
            return questions
        except ValueError as exc:
            prompt += f"\n上一次结果校验失败：{exc}。请重新生成整套题。"
    raise RuntimeError("连续 3 次生成的测试题都未通过去重和完整性校验")


def render_story(run_date: date, words: list[dict[str, str]], story: dict[str, str]) -> str:
    word_line = " · ".join(f"{item['word']}（{item['translation']}）" for item in words)
    return f"""# {run_date.isoformat()}｜{story['title']}

## 本轮 10 个单词

{word_line}

## 中英穿插故事

{story['mixed_story']}

## 全英文版

{story['english_story']}
"""


def render_quiz(run_date: date, questions: list[dict[str, Any]], set_number: int) -> str:
    lines = [f"# {run_date.isoformat()}｜10 词自测（第 {set_number} 套）", ""]
    labels = "ABCD"
    for index, question in enumerate(questions, 1):
        lines.extend([f"## {index}. {question['question']}", ""])
        lines.extend(f"{labels[i]}. {option}" for i, option in enumerate(question["options"]))
        lines.append("")
    lines.extend(["## 答案与解析", ""])
    for index, question in enumerate(questions, 1):
        lines.append(f"{index}. **{question['answer']}**｜{question['explanation']}")
    return "\n".join(lines) + "\n"


def generate_for_date(run_date: date, state: dict[str, Any]) -> Path | None:
    weekday = run_date.weekday()
    if weekday == 6:
        return None
    date_key = run_date.isoformat()
    existing = state.get("runs", {}).get(date_key)
    if existing:
        return ROOT / existing["file"]

    if weekday in STORY_DAYS:
        words = select_story_words(parse_words(), state, random.SystemRandom())
        story = generate_story(words)
        content = render_story(run_date, words, story)
        state["cycle"] = {
            "id": date_key,
            "words": words,
            "previous_questions": [],
            "last_question_fingerprints": [],
        }
        kind = "story"
    elif weekday in QUIZ_DAYS:
        cycle = state.get("cycle")
        if not cycle:
            raise RuntimeError("没有可用的当前词汇循环；请先成功运行周一或周四的故事任务")
        set_number = 1 if weekday in {1, 4} else 2
        previous_questions = cycle.get("previous_questions", [])
        used_fingerprints = set(state.get("used_question_fingerprints", []))
        questions = generate_quiz(
            cycle["words"], previous_questions, set_number, used_fingerprints
        )
        fingerprints = validate_questions(
            questions, cycle["words"], set(cycle.get("last_question_fingerprints", []))
        )
        cycle["previous_questions"] = questions
        cycle["last_question_fingerprints"] = fingerprints
        state["used_question_fingerprints"] = state.get("used_question_fingerprints", []) + fingerprints
        content = render_quiz(run_date, questions, set_number)
        kind = f"quiz-{set_number}"
    else:
        raise AssertionError("未知的星期值")

    output_path = OUTPUT_DIR / run_date.strftime("%Y/%m") / f"{date_key}-{kind}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    state.setdefault("runs", {})[date_key] = {
        "kind": kind,
        "file": output_path.relative_to(ROOT).as_posix(),
    }
    save_state(state)
    return output_path


def dingtalk_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(round(time.time() * 1000))
    message = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()).decode()
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={urllib.parse.quote_plus(signature)}"


def dingtalk_payload(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    # DingTalk custom robots may require configured keywords. Keep this stable
    # prefix independent of the generated story or quiz wording.
    keyword_prefix = "英语单词学习"
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{keyword_prefix}｜{path.stem}",
            "text": f"## {keyword_prefix}\n\n{content}",
        },
    }


def notify_dingtalk(path: Path) -> None:
    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        raise RuntimeError("缺少 DINGTALK_WEBHOOK")
    payload = dingtalk_payload(path)
    request = urllib.request.Request(
        dingtalk_url(webhook, os.getenv("DINGTALK_SECRET", "")),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"钉钉推送失败：HTTP {exc.code} {detail}") from exc
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉推送失败：{result}")


def write_github_output(path: Path | None) -> None:
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as handle:
            handle.write(f"content_path={path or ''}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="按北京时间指定 YYYY-MM-DD；默认今天")
    parser.add_argument("--generate-only", action="store_true", help="只生成，不推送")
    parser.add_argument("--notify-file", type=Path, help="只推送指定 Markdown 文件")
    args = parser.parse_args()

    if args.notify_file:
        notify_dingtalk(args.notify_file.resolve())
        print(f"已推送：{args.notify_file}")
        return 0

    run_date = date.fromisoformat(args.date) if args.date else datetime.now(TIMEZONE).date()
    state = load_state()
    output = generate_for_date(run_date, state)
    write_github_output(output)
    if output is None:
        print(f"{run_date.isoformat()} 是周日，按规则轮空")
        return 0
    print(f"已生成：{output.relative_to(ROOT)}")
    if not args.generate_only:
        notify_dingtalk(output)
        print("已推送到钉钉")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # Keep CI logs concise and never print secrets.
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
