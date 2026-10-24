"""Lecture Companion: upload lecture slides, read them side by side with an
AI professor's explanation of each slide, then review a summary and quiz."""

from __future__ import annotations

import os
import tempfile

import gradio as gr
import pymupdf
from dotenv import load_dotenv

load_dotenv(override=True)

import notion_export  # noqa: E402  (after load_dotenv so env vars are set)
import pipeline  # noqa: E402

LATEX = [
    {"left": "$$", "right": "$$", "display": True},
    {"left": "$", "right": "$", "display": False},
]

# Dark: "Midnight", near-black with violet/teal glows. Light: an ombre of soft lilacs.
_SHARED = dict(block_radius="16px", input_radius="12px", button_large_radius="12px",
               button_small_radius="10px", block_shadow="none", shadow_drop="none",
               block_label_background_fill="transparent", panel_background_fill="transparent",
               checkbox_label_background_fill="transparent",
               checkbox_label_background_fill_selected="transparent",
               button_primary_text_color="#ffffff", slider_color="#7654ff",
               checkbox_background_color_selected="#7654ff", color_accent="#7c5cff")
_DARK = dict(
    body_background_fill="radial-gradient(50% 60% at 95% 0%, rgba(140,90,255,.45), transparent 70%), "
                         "radial-gradient(45% 55% at 0% 100%, rgba(24,198,176,.28), transparent 70%), "
                         "radial-gradient(40% 45% at 55% 55%, rgba(200,70,220,.14), transparent 70%), "
                         "linear-gradient(160deg, #0a0918 0%, #171038 40%, #26145a 72%, #0c2436 100%)",
    body_text_color="#eceaf6", body_text_color_subdued="#a9a6c2",
    background_fill_primary="#0f0f18", background_fill_secondary="rgba(255,255,255,.04)",
    block_background_fill="rgba(255,255,255,.035)", block_border_color="rgba(255,255,255,.10)",
    block_label_text_color="#a9a6c2", block_title_text_color="#a9a6c2",
    border_color_primary="rgba(255,255,255,.10)", panel_border_color="rgba(255,255,255,.10)",
    input_background_fill="rgba(255,255,255,.05)", input_border_color="rgba(255,255,255,.12)",
    button_primary_background_fill="linear-gradient(90deg, #7654ff, #4f7dff 55%, #18c6b0)",
    button_primary_background_fill_hover="linear-gradient(90deg, #8468ff, #6690ff 55%, #2fd6c0)",
    button_secondary_background_fill="rgba(255,255,255,.06)",
    button_secondary_background_fill_hover="rgba(255,255,255,.12)",
    button_secondary_text_color="#eceaf6", button_secondary_border_color="rgba(255,255,255,.12)",
    checkbox_background_color="rgba(255,255,255,.06)", checkbox_border_color="rgba(255,255,255,.2)",
    checkbox_label_text_color="#eceaf6",
    color_accent_soft="rgba(118,84,255,.18)",
    code_background_fill="rgba(0,0,0,.35)", link_text_color="#7fe6d7",
)
_LIGHT = dict(
    body_background_fill="radial-gradient(50% 60% at 95% 0%, rgba(230,140,255,.60), transparent 70%), "
                         "radial-gradient(45% 55% at 0% 100%, rgba(60,200,190,.35), transparent 70%), "
                         "radial-gradient(40% 45% at 55% 55%, rgba(255,140,220,.25), transparent 70%), "
                         "linear-gradient(160deg, #e6d9ff 0%, #c9adff 35%, #a583f5 70%, #8a8cf0 100%)",
    body_text_color="#231c3d", body_text_color_subdued="#4a4170",
    background_fill_primary="#f7f2ff", background_fill_secondary="rgba(255,255,255,.18)",
    block_background_fill="rgba(255,255,255,.22)", block_border_color="rgba(255,255,255,.45)",
    block_label_text_color="#4a4170", block_title_text_color="#4a4170",
    border_color_primary="rgba(255,255,255,.45)", panel_border_color="rgba(255,255,255,.45)",
    input_background_fill="rgba(255,255,255,.40)", input_border_color="rgba(110,80,210,.22)",
    button_primary_background_fill="linear-gradient(90deg, #6d4aff, #9466ff 55%, #c07dff)",
    button_primary_background_fill_hover="linear-gradient(90deg, #7c5cff, #a07aff 55%, #cb92ff)",
    button_secondary_background_fill="rgba(255,255,255,.35)",
    button_secondary_background_fill_hover="rgba(255,255,255,.6)",
    button_secondary_text_color="#2e2551", button_secondary_border_color="rgba(110,80,210,.2)",
    checkbox_background_color="#ffffff", checkbox_border_color="rgba(110,80,210,.35)",
    checkbox_label_text_color="#231c3d",
    color_accent_soft="rgba(118,84,255,.12)",
    code_background_fill="rgba(118,84,255,.08)", link_text_color="#5b3fd6",
)
THEME = gr.themes.Base(font=[gr.themes.GoogleFont("DM Sans"), "system-ui", "sans-serif"])
THEME.set(**_SHARED, **_LIGHT, **{f"{k}_dark": v for k, v in _DARK.items()})

HEAD = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Outfit:wght@500;600&display=swap">'
        # drag a PDF anywhere on the page: hand it to the upload button's hidden <input type=file>
        """<script>
addEventListener("dragover", e => { e.preventDefault(); document.body.classList.add("dragging"); });
addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
addEventListener("drop", e => {
  e.preventDefault(); document.body.classList.remove("dragging");
  const input = document.querySelector("#pdf-btn")?.parentElement.querySelector("input[type=file]");
  const file = [...e.dataTransfer.files].find(f => f.name.toLowerCase().endsWith(".pdf"));
  if (!input || !file) return;
  const dt = new DataTransfer(); dt.items.add(file);
  input.files = dt.files; input.dispatchEvent(new Event("change", { bubbles: true }));
});
</script>""")

# ponytail: panes sized to the viewport; 255px = bar + tabs + slide stepper + footer (measured). Tweak if it scrolls.
PANE = "calc(100vh - 255px)"

CSS = """
:root { --chip: rgba(110,80,210,.08); --chip-fg: #665d88; --c1: rgba(118,84,255,.14); --c1-fg: #5a3ce0;
  --c2: rgba(20,160,140,.12); --c2-fg: #0f7d6e; --glow: rgba(118,84,255,.25);
  --card: linear-gradient(180deg, rgba(255,255,255,.38), rgba(255,255,255,.18)); --card-line: rgba(255,255,255,.5); }
.dark { --chip: rgba(255,255,255,.07); --chip-fg: #a9a6c2; --c1: rgba(118,84,255,.18); --c1-fg: #c3b5ff;
  --c2: rgba(24,198,176,.16); --c2-fg: #7fe6d7; --glow: rgba(118,84,255,.35);
  --card: linear-gradient(180deg, rgba(118,84,255,.10), rgba(255,255,255,.03)); --card-line: rgba(160,140,255,.25); }
body { background-attachment: fixed; }
#top h1, #explain h3 { font-family: "Outfit", "DM Sans", sans-serif; letter-spacing: -0.02em; }
#top h1 { font-size: 2.4rem; font-weight: 600; margin: 0; }
#top .sub { color: var(--body-text-color-subdued); margin: 4px 0 0; }
#bar { align-items: center; }
#bar .chips { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; font-variant-numeric: tabular-nums; }
.chips span { padding: 5px 12px; border-radius: 999px; background: var(--chip); color: var(--chip-fg); }
.chips .c1 { background: var(--c1); color: var(--c1-fg); }
.chips .c2 { background: var(--c2); color: var(--c2-fg); }
#go { box-shadow: 0 8px 30px var(--glow); }
#slide { border-radius: 20px; }
#slide img { object-fit: contain; }
#explain { border-radius: 20px; padding: 6px 18px; background: var(--card); border: 1px solid var(--card-line); }
/* centered reading card: ~70% of the window, but never narrower than 900px or wider than the page */
#summary { width: min(100%, max(900px, 70%)); margin: 12px auto 0; border-radius: 20px; padding: 10px 32px 20px;
  background: var(--card); border: 1px solid var(--card-line); }
/* landing: hero + one row of pills centered on the screen; .compact once a lecture runs */
#top { text-align: center; }
#top:not(.compact) { min-height: calc(100vh - 120px); justify-content: center; }
#launch { width: 92%; max-width: 1500px; margin: 28px auto 0; gap: 14px; align-items: stretch; }
#launch .pill { border-radius: 16px; border: 1px solid var(--block-border-color);
  background: var(--block-background-fill); min-height: 58px; padding: 0 18px;
  display: flex; align-items: center; font-size: 16px; }
#launch .pill input, #launch .pill .wrap { background: transparent !important; border: none !important;
  box-shadow: none !important; font-size: 16px; }
#pdf-btn { justify-content: flex-start; color: var(--body-text-color); font-weight: 400;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#launch .pill label { gap: 12px; cursor: pointer; }
#launch .pill label span { font-size: 16px; white-space: nowrap; }
#launch .toggle { flex: none !important; width: auto !important; min-width: 0 !important; overflow: visible; }
#top:not(.compact) h1 { font-size: 3.6rem; }
#top:not(.compact) .sub { font-size: 1.1rem; }
#launch .pill input[type=checkbox] { appearance: none; width: 44px; height: 26px; border-radius: 999px;
  position: relative; flex: none; background: var(--chip) !important; border: 1px solid var(--card-line) !important;
  transition: background .2s; }
#launch .pill input[type=checkbox]::before { content: ""; position: absolute; top: 3px; left: 3px;
  width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: transform .2s; }
#launch .pill input[type=checkbox]:checked { background: linear-gradient(90deg, #7654ff, #18c6b0) !important; }
#launch .pill input[type=checkbox]:checked::before { transform: translateX(18px); }
.dragging #pdf-btn { outline: 2px dashed #7654ff; outline-offset: 4px; }
#go { border-radius: 16px; min-height: 58px; font-size: 17px; }
/* Hebrew panes are rtl, but code and display math still read left to right */
#explain pre, #summary pre, #explain .katex-display, #summary .katex-display { direction: ltr; text-align: left; }
.badge { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: var(--c2); color: var(--c2-fg); }
"""


# ------------------------------------------------------------------ helpers

def _view(lecture, i):
    """Image + explanation for slide i (1-based)."""
    if not lecture:
        return None, "_Upload a lecture PDF and press **Explain lecture**._"
    i = max(1, min(int(i), len(lecture.slides)))
    s = lecture.slides[i - 1]
    badges = []
    if s.checked:
        badges.append("checked by reviewer model")
    if s.revised:
        badges.append("revised after review")
    head = f"### Slide {i} / {len(lecture.slides)}"
    if badges:
        head += f"\n<span class='badge'>✓ {' · '.join(badges)}</span>"
    if s.error:
        body = f"**Couldn't explain this slide.**\n\n`{s.error}`"
    elif s.explanation:
        body = s.explanation
    else:
        body = "_Explaining this slide…_"
    return s.image_path, f"{head}\n\n{body}"


def _status(lecture, phase):
    u = lecture.usage
    done = sum(1 for s in lecture.slides if s.explanation or s.error)
    return (
        f"<div class='chips'><span class='c1'>● {phase}</span>"
        f"<span class='c2'>{done} / {len(lecture.slides)} slides</span>"
        f"<span>{u.calls} model calls · {u.input_tokens:,} in / {u.output_tokens:,} out tokens</span>"
        f"<span class='c1'>≈ ${u.cost:.2f}</span></div>"
    )


def _write_markdown(lecture):
    path = os.path.join(tempfile.mkdtemp(), f"{lecture.title[:60] or 'lecture'}.md".replace("/", "-"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(pipeline.to_markdown(lecture))
    return path


# ------------------------------------------------------------------ events

def picked(path):
    with pymupdf.open(path) as doc:
        n = len(doc)
    return path, gr.update(label=f"📄 {os.path.basename(path)} · {n} pages")


def run(pdf, language, check, skip_builds, tick):
    # bad input → toast + no-op; gr.Error would overlay every output, including the full-screen #top
    problem = None
    if not pdf:
        problem = "Upload a PDF first."
    elif not pipeline.MOCK and not os.getenv("OPENAI_API_KEY"):
        problem = "OPENAI_API_KEY isn't set (add it to .env locally, or as a Space secret)."
    else:
        try:
            lecture = pipeline.load_pdf(pdf)
        except Exception as e:
            problem = str(e)
    if problem:
        gr.Warning(problem)
        yield (gr.update(),) * 9
        return

    n = len(lecture.slides)
    slider = gr.Slider(minimum=1, maximum=max(n, 2), value=1, step=1, interactive=True)
    tick += 1
    # first yield also switches to full-page view: hide the top, show the toggle and panes
    yield (lecture, slider, tick, _status(lecture, "Explaining slides"), "", None,
           gr.update(visible=False, elem_classes=["compact"]),
           gr.update(visible=True, value=SHOW_TOP), gr.update(visible=True))
    same = (gr.update(), gr.update(), gr.update())

    for _ in pipeline.explain_all(lecture, language, check, skip_builds):
        tick += 1
        yield lecture, gr.update(), tick, _status(lecture, "Explaining slides"), "", None, *same

    yield lecture, gr.update(), tick, _status(lecture, "Writing summary and quiz"), "_Writing…_", None, *same
    try:
        summary = pipeline.summarize(lecture, language)
    except Exception as e:
        summary = f"**Couldn't write the summary.** `{e}`"
    yield lecture, gr.update(), tick + 1, _status(lecture, "Done"), summary, _write_markdown(lecture), *same


SHOW_TOP, FULL_PAGE = "⌄ Upload & settings", "⤢ Full page"


def toggle_top(label):
    showing = label == SHOW_TOP  # button offers to show the top, so it's hidden now
    return gr.update(visible=showing), FULL_PAGE if showing else SHOW_TOP, gr.update(visible=not showing)


def notion_pages(token):
    """Reload the class dropdown: NOTION_PARENT_PAGE itself plus its child pages."""
    token, parent = token or os.getenv("NOTION_TOKEN"), os.getenv("NOTION_PARENT_PAGE")
    if not (token and parent):
        return [], gr.update(choices=[], value=None,
                             info="Set NOTION_TOKEN and NOTION_PARENT_PAGE to list your classes.")
    try:
        pages = notion_export.list_pages(parent, token)
    except Exception as e:
        return [], gr.update(choices=[], value=None, info=f"Couldn't list Notion pages: {e}")
    choices = [(f"{pages[0][0]} (parent page)", pages[0][1])] + pages[1:]
    return pages, gr.update(choices=choices, value=pages[0][1], info=None)


def preselect_class(lecture, pages):
    """One cheap model call to guess which class page this lecture belongs under."""
    if not (lecture and len(pages) > 1):
        return gr.update()
    i = pipeline.match_class(lecture, [title for title, _ in pages[1:]])
    return gr.update(value=pages[i + 1][1] if i >= 0 else pages[0][1])


def to_notion(lecture, parent, token):
    if not lecture:
        raise gr.Error("Explain a lecture first.")
    token = token or os.getenv("NOTION_TOKEN")
    if not token:
        raise gr.Error("Add a Notion integration token (or set NOTION_TOKEN).")
    if not parent:
        raise gr.Error("Pick a Notion page to export under (press Refresh if the list is empty).")
    try:
        url = notion_export.export(pipeline.to_markdown(lecture), lecture.title, parent, token)
    except Exception as e:
        raise gr.Error(f"Notion export failed: {e}")
    return f"Exported. [Open in Notion]({url})"


def step(i, delta, lecture):
    if not lecture:
        return 1
    return max(1, min(int(i) + delta, len(lecture.slides)))


# ------------------------------------------------------------------ layout

with gr.Blocks(title="Lecture Companion", fill_width=True) as demo:
    lecture_state = gr.State(None)
    tick = gr.Number(0, visible=False)  # bumps whenever an explanation arrives

    pdf = gr.State(None)
    with gr.Row(elem_id="bar"):
        status = gr.HTML(scale=4)
        top_btn = gr.Button(SHOW_TOP, size="sm", scale=0, min_width=170, visible=False)
        mode_btn = gr.Button("◐ Light / dark", size="sm", scale=0, min_width=130)

    with gr.Column(elem_id="top") as top:
        gr.HTML(
            "<h1>Lecture Companion</h1>"
            "<p class='sub'>Upload lecture slides (Hebrew or English) to get study notes for "
            "every slide, then a summary and quiz.</p>"
        )
        with gr.Row(elem_id="launch"):
            pdf_btn = gr.UploadButton("📄 Choose or drop lecture PDF", file_types=[".pdf"], type="filepath",
                                      variant="secondary", elem_id="pdf-btn", elem_classes="pill",
                                      scale=3, min_width=220)
            language = gr.Dropdown([("Explain in: English", "English"), ("Explain in: Hebrew", "Hebrew")],
                                   value="English", show_label=False, container=False,
                                   elem_classes="pill", scale=2, min_width=190)
            check = gr.Checkbox(True, label="Reviewer check", container=False,
                                elem_classes=["pill", "toggle"])
            skip_builds = gr.Checkbox(True, label="Skip build-up pages", container=False,
                                      elem_classes=["pill", "toggle"])
            go = gr.Button("Explain lecture", variant="primary", scale=2, min_width=180, elem_id="go")

    with gr.Column(visible=False) as main, gr.Tabs():
        with gr.Tab("Slides"):
            with gr.Row():
                with gr.Column(scale=5):
                    image = gr.Image(label="Slide", elem_id="slide", interactive=False,
                                     show_label=False, height=PANE)
                    with gr.Row():
                        prev_btn = gr.Button("← Previous", size="sm")
                        slider = gr.Slider(1, 2, value=1, step=1, label="Slide", interactive=False)
                        next_btn = gr.Button("Next →", size="sm")
                with gr.Column(scale=4, elem_id="explain"):
                    explanation = gr.Markdown(
                        "_Upload a lecture PDF and press **Explain lecture**._",
                        latex_delimiters=LATEX, height=PANE,
                    )
        with gr.Tab("Summary & quiz"):
            summary = gr.Markdown("_Appears when all slides are explained._", latex_delimiters=LATEX,
                                  elem_id="summary")
        with gr.Tab("Export"):
            download = gr.File(label="Download everything as Markdown", interactive=False)
            gr.Markdown(
                "**Export to Notion (optional).** Create an integration at "
                "notion.so/my-integrations, connect it to your classes page "
                "(page menu → Connections) and set NOTION_PARENT_PAGE to its link. "
                "The class is guessed from the lecture title; change it if it's wrong."
            )
            notion_pages_state = gr.State([])
            with gr.Row(equal_height=True):
                notion_parent = gr.Dropdown(label="Export under (class page)", choices=[], scale=4)
                notion_refresh = gr.Button("↻ Refresh", size="sm", scale=0, min_width=110)
            notion_token = gr.Textbox(label="Integration token (leave empty to use NOTION_TOKEN)",
                                      type="password")
            notion_btn = gr.Button("Export to Notion")
            notion_out = gr.Markdown()

    go.click(run, [pdf, language, check, skip_builds, tick],
             [lecture_state, slider, tick, status, summary, download, top, top_btn, main]
             ).then(preselect_class, [lecture_state, notion_pages_state], notion_parent)
    # set text direction for the lecture's language (at run time, so switching mid-read doesn't flip panes)
    go.click(lambda lang: [gr.update(rtl=lang == "Hebrew")] * 2, language, [explanation, summary],
             queue=False)
    demo.load(notion_pages, notion_token, [notion_pages_state, notion_parent])
    notion_refresh.click(notion_pages, notion_token, [notion_pages_state, notion_parent])
    pdf_btn.upload(picked, pdf_btn, [pdf, pdf_btn])
    top_btn.click(toggle_top, top_btn, [top, top_btn, main])
    # Gradio marks dark mode with a "dark" class (on body, or the app's parent); flip it in place
    mode_btn.click(None, js="() => (document.querySelector('.dark') || document.body).classList.toggle('dark')")
    tick.change(_view, [lecture_state, slider], [image, explanation])
    slider.change(_view, [lecture_state, slider], [image, explanation])
    prev_btn.click(lambda i, l: step(i, -1, l), [slider, lecture_state], slider)
    next_btn.click(lambda i, l: step(i, 1, l), [slider, lecture_state], slider)
    notion_btn.click(to_notion, [lecture_state, notion_parent, notion_token], notion_out)


if __name__ == "__main__":
    password = os.getenv("APP_PASSWORD")
    demo.queue(default_concurrency_limit=2).launch(
        auth=(os.getenv("APP_USERNAME", "demo"), password) if password else None,
        theme=THEME,
        css=CSS,
        head=HEAD,
    )
