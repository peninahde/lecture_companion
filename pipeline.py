"""
The AI part of Lecture Companion.

The pipeline is a small agentic workflow built from three of the
"5 workflow design patterns":

  1. Parallelization: every slide is explained at the same time by its
     own LLM call (code splits the work, code collects the results).
  2. Evaluator-optimizer: a second LLM call checks each explanation
     against the slide image and sends it back with feedback if it finds
     a mistake (at most MAX_RETRIES times, as a cost guardrail).
  3. Prompt chaining: once all slides are explained, their explanations
     are fed into one more call that writes the summary and quiz.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import pymupdf as fitz  # renders PDF pages to images and extracts their text
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
CHECK_MODEL = os.getenv("OPENAI_CHECK_MODEL", MODEL)
MAX_PAGES = int(os.getenv("MAX_PAGES", "60"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
MAX_RETRIES = 1
MOCK = os.getenv("MOCK_LLM") == "1"  # offline mode for testing / demo backup
# ponytail: one price pair for every call (gpt-5.4-mini list price, USD per 1M tokens);
# set these if OPENAI_MODEL / OPENAI_CHECK_MODEL point at a model priced differently
PRICE_IN = float(os.getenv("PRICE_IN_PER_M", "0.75"))
PRICE_OUT = float(os.getenv("PRICE_OUT_PER_M", "4.50"))


@dataclass
class Slide:
    number: int                 # 1-based page number in the PDF
    image_path: str             # high-res PNG shown in the viewer
    model_image_b64: str        # smaller PNG sent to the model
    text: str                   # text layer of the page ('' for scanned PDFs)
    build_of_next: bool = False # True if the next page is this one plus more
    explanation: str = ""
    checked: bool = False
    revised: bool = False
    error: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, response) -> None:
        self.calls += 1
        u = getattr(response, "usage", None)
        if u:
            self.input_tokens += getattr(u, "prompt_tokens", 0) or 0
            self.output_tokens += getattr(u, "completion_tokens", 0) or 0

    @property
    def cost(self) -> float:
        """Estimated USD spent so far."""
        return (self.input_tokens * PRICE_IN + self.output_tokens * PRICE_OUT) / 1_000_000


@dataclass
class Lecture:
    title: str
    slides: list[Slide] = field(default_factory=list)
    summary: str = ""
    usage: Usage = field(default_factory=Usage)


# ---------------------------------------------------------------- PDF reading

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_pdf(path: str) -> Lecture:
    """Render every page to PNG and pull out its text."""
    doc = fitz.open(path)
    if doc.page_count > MAX_PAGES:
        raise ValueError(
            f"This PDF has {doc.page_count} pages; the limit is {MAX_PAGES}. "
            "Split it or raise MAX_PAGES."
        )
    out_dir = tempfile.mkdtemp(prefix="slides_")
    title = (doc.metadata or {}).get("title") or (
        os.path.splitext(os.path.basename(path))[0].replace("_", " ")
    )
    lecture = Lecture(title=title)

    for i, page in enumerate(doc):
        big = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_path = os.path.join(out_dir, f"slide_{i + 1:03d}.png")
        big.save(img_path)
        small = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3))
        b64 = base64.b64encode(small.tobytes("png")).decode()
        lecture.slides.append(
            Slide(number=i + 1, image_path=img_path, model_image_b64=b64, text=page.get_text())
        )

    # Beamer/PowerPoint "build-up" slides: page i is a partial version of page i+1.
    for cur, nxt in zip(lecture.slides, lecture.slides[1:]):
        a, b = _normalize(cur.text), _normalize(nxt.text)
        if len(a) > 40 and a != b and _is_build(a, b):
            cur.build_of_next = True
    return lecture


def _is_build(a: str, b: str) -> bool:
    """True if every word of slide a appears, in order, in slide b."""
    words, it = a.split(), iter(b.split())
    return all(any(w == x for x in it) for w in words)


# ---------------------------------------------------------------- LLM calls

EXPLAIN_SYSTEM = """You write study notes that a student reads next to a lecture \
slide. The student can see the slide, may be new to the topic, and wants the main \
ideas made clear, not a speech. Write in {language}, in Markdown, like a sharp TA's \
notes: short, plain sentences.

LENGTH FOLLOWS CONTENT. A title, agenda or admin slide gets 1-3 sentences (say \
"Nothing to learn here." when that is true). A simple concept slide gets a short \
paragraph or a few bullets. Only a dense slide (a calculation, a protocol, a \
comparison) gets a long answer. Never pad.

WHAT TO WRITE
- Lead with the main idea in one sentence. Do not describe the slide's layout, \
colours or pictures, and do not copy its bullets. Rephrase them so they are clear.
- Use bullets for lists, steps and pros/cons. Use a small table when comparing \
several things.
- Define each technical term or acronym the first time it appears, in a short \
clause right there in the sentence (e.g. "a nonce, a random number used only once"). \
Do not add a separate glossary.
- Add what the slide leaves out when it matters: the "why" behind a rule, a quick \
everyday analogy, a skipped step, or a common mistake.
- Connect slides: say when this slide sets up or uses another one ("slide 11 shows \
why"), using the neighbouring slides' text for context.
- Mention when something is likely to come up on an exam.
- For a question slide, tell the student to try it first and point out what the \
hint is getting at. Do not give the answer.

MATH: if there is any calculation, solve it step by step. Convert units first and \
show where people usually go wrong. Give the general formula, then plug in numbers. \
Use LaTeX: $...$ inline and $$...$$ for display equations. If the slide simplifies, \
say so and show the exact version briefly.

ERRORS: if the slide has a typo or a factual error, point it out in one line.

No heading, no "This slide shows...", no closing summary. Be accurate: background \
knowledge is welcome, made-up facts are not."""

CHECK_SYSTEM = """You review a professor's explanation of one lecture slide. Compare \
it with the slide image. Reply with JSON only: {"ok": true} if it is accurate, or \
{"ok": false, "feedback": "<what is wrong and how to fix it>"} if it contains a \
factual error, a wrong calculation, or misreads the slide. Minor style issues are ok. \
The explanation is meant to teach beyond the slide, so do not flag correct background \
or examples just because they are not on the slide."""

SUMMARY_SYSTEM = """You write study material in {language} from slide-by-slide \
explanations of one lecture. Produce Markdown with exactly these sections:
## Summary - the 5-8 most important ideas, as short paragraphs or bullets.
## Glossary - a table of the key terms with one-line definitions.
## Quiz - 5 multiple-choice questions (A-D) that test understanding, not memorization.
## Answers - the correct letter and a one-sentence explanation for each question."""


def _client() -> OpenAI:
    return OpenAI()  # reads OPENAI_API_KEY from the environment


def _chat(client, model, messages, usage: Usage, json_mode=False) -> str:
    if MOCK:
        usage.calls += 1
        if json_mode:
            return '{"ok": true}'
        return "**(mock)** Explanation of this slide.\n\n$$a^2 + b^2 = c^2$$\n\n**Key terms**\n- *Mock*: offline test mode."
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    usage.add(resp)
    return resp.choices[0].message.content or ""


def _slide_content(slide: Slide, lecture: Lecture, extra: str = "") -> list:
    prev_txt = lecture.slides[slide.number - 2].text if slide.number > 1 else ""
    next_txt = lecture.slides[slide.number].text if slide.number < len(lecture.slides) else ""
    text = (
        f"Lecture: {lecture.title}\nSlide {slide.number} of {len(lecture.slides)}.\n\n"
        f"Text layer of this slide (may be garbled for Hebrew/RTL; trust the image):\n"
        f"{slide.text[:4000]}\n\n"
        f"Previous slide text (context only):\n{prev_txt[:1500]}\n\n"
        f"Next slide text (context only):\n{next_txt[:1500]}"
        f"{extra}"
    )
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{slide.model_image_b64}"}},
    ]


def explain_slide(client, slide: Slide, lecture: Lecture, language: str, check: bool) -> Slide:
    try:
        system = {"role": "system", "content": EXPLAIN_SYSTEM.format(language=language)}
        explanation = _chat(
            client, MODEL, [system, {"role": "user", "content": _slide_content(slide, lecture)}],
            lecture.usage,
        )
        if check:
            for _ in range(MAX_RETRIES):
                verdict = _check(client, slide, explanation, lecture.usage)
                if verdict.get("ok", True):
                    break
                slide.revised = True
                extra = (
                    f"\n\nYour previous explanation:\n{explanation}\n\n"
                    f"A reviewer found this problem: {verdict.get('feedback', '')}\n"
                    "Write a corrected explanation."
                )
                explanation = _chat(
                    client, MODEL,
                    [system, {"role": "user", "content": _slide_content(slide, lecture, extra)}],
                    lecture.usage,
                )
            slide.checked = True
        slide.explanation = explanation.strip()
    except Exception as e:  # one bad slide should not kill the whole lecture
        slide.error = f"{type(e).__name__}: {e}"
    return slide


def _check(client, slide: Slide, explanation: str, usage: Usage) -> dict:
    content = [
        {"type": "text", "text": f"Explanation to review:\n\n{explanation}"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{slide.model_image_b64}"}},
    ]
    raw = _chat(
        client, CHECK_MODEL,
        [{"role": "system", "content": CHECK_SYSTEM}, {"role": "user", "content": content}],
        usage, json_mode=True,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": True}


def explain_all(lecture: Lecture, language: str, check: bool, skip_builds: bool):
    """Generator: yields each slide as soon as its explanation is ready."""
    client = None if MOCK else _client()
    todo = []
    for s in lecture.slides:
        if skip_builds and s.build_of_next:
            s.explanation = f"_This page builds up to slide {s.number + 1}; see the explanation there._"
            yield s
        else:
            todo.append(s)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(explain_slide, client, s, lecture, language, check) for s in todo]
        for f in as_completed(futures):
            yield f.result()


def summarize(lecture: Lecture, language: str) -> str:
    client = None if MOCK else _client()
    body = "\n\n".join(
        f"### Slide {s.number}\n{s.explanation}"
        for s in lecture.slides if s.explanation and not s.build_of_next
    )
    lecture.summary = _chat(
        client, MODEL,
        [
            {"role": "system", "content": SUMMARY_SYSTEM.format(language=language)},
            {"role": "user", "content": f"Lecture: {lecture.title}\n\n{body[:120000]}"},
        ],
        lecture.usage,
    ).strip()
    return lecture.summary


MATCH_SYSTEM = """Pick the university class a lecture belongs to. Titles may be in \
Hebrew or English. Reply with JSON {"index": n}: the number of the best-matching class, \
or -1 if none plausibly fits."""


def match_class(lecture: Lecture, names: list[str]) -> int:
    """Index into `names` of the class this lecture most likely belongs to, or -1."""
    listing = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
    try:
        reply = _chat(
            None if MOCK else _client(), CHECK_MODEL,
            [{"role": "system", "content": MATCH_SYSTEM},
             {"role": "user", "content": f"Lecture: {lecture.title}\n\nClasses:\n{listing}"}],
            lecture.usage, json_mode=True,
        )
        i = int(json.loads(reply)["index"])
        if -1 <= i < len(names):
            return i
    except Exception:
        pass
    # ponytail: string-similarity fallback (also what MOCK_LLM uses)
    close = difflib.get_close_matches(lecture.title, names, n=1, cutoff=0.3)
    return names.index(close[0]) if close else -1


def to_markdown(lecture: Lecture) -> str:
    parts = [f"# {lecture.title}\n"]
    for s in lecture.slides:
        parts.append(f"## Slide {s.number}\n\n{s.explanation or s.error}\n")
    if lecture.summary:
        parts.append(lecture.summary)
    return "\n".join(parts)
