# Lecture Companion

Upload a lecture PDF (Hebrew or English) and read it side by side with an AI
professor: each slide gets a plain-language explanation, definitions of the
technical terms, and step-by-step math. When every slide is done, you get a
summary, a glossary and a quiz. Results can be downloaded as Markdown or
exported to Notion.

**Demo:** see the [project page](docs/index.html) for a video walkthrough and screenshots.

## How it works

```
PDF ─► render pages + extract text (PyMuPDF)
     ─► explain every slide in parallel ............ Parallelization
           └─ reviewer model checks each one,
              sends it back with feedback (max 1x) .. Evaluator-optimizer
     ─► all explanations ─► summary + glossary + quiz  Prompt chaining
```

* Each slide is sent to the model as an **image plus its text layer**, so
  diagrams, tables and right-to-left Hebrew slides are read correctly.
* Neighbouring slides' text is included as context.
* "Build-up" pages (a slide revealed in steps) are detected and skipped.

### Guardrails
* Page limit (`MAX_PAGES`, default 60) and at most one revision per slide
  keep cost bounded. Token usage and an estimated dollar cost are shown live
  in the status bar.
* Optional password (`APP_PASSWORD`) so a hosted copy can't spend your credit.
* One failing slide doesn't stop the rest.

## Run locally

```bash
cp .env.example .env          # then put your OpenAI key in .env
uv venv && uv pip install -r requirements.txt
uv run python app.py          # open http://127.0.0.1:7860
```

`MOCK_LLM=1 uv run python app.py` runs without any API calls (fake explanations),
which is useful for testing the interface or as a demo backup.

## Files

| File | What it does |
|---|---|
| `app.py` | Gradio interface: upload, side-by-side viewer, summary, export |
| `pipeline.py` | PDF rendering and the LLM workflow |
| `notion_export.py` | Converts Markdown to Notion blocks and creates a page |
| `docs/` | Project page with the demo video and screenshots |
