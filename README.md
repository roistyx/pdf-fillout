# pdf-fillout

Fill out PDF forms — including the "flat" kind that have no real form
fields, just `__________` lines and little square boxes.

`fill_form.py` looks at the page and finds the fillable spots itself:

1. **Real AcroForm widgets** if the PDF has them (text, checkbox, radio).
2. **Underscore runs** in the text layer and thin drawn horizontal lines
   → text fields.
3. **Small square vector shapes** → checkboxes.

Each spot gets a best-guess label from the text next to it ("Parent phone
number", "Preschool"), and checkboxes get the question they belong to.
Values are stamped on top with Helvetica; checkboxes get a ✓.

There is a CLI and a local web UI (see [`web/`](web/README.md)).

## Install

```bash
pip3 install pymupdf                                   # CLI
pip3 install fastapi 'uvicorn[standard]' python-multipart   # web UI
```

## CLI usage

See what the tool finds:
```bash
python3 fill_form.py detect form.pdf --pretty
```
```
--- page 1
    s1  text    (309,217)  (1)Parent/Guardian Name
    s2  text    (306,262)  (2) Parent/Guardian Name
    ...
--- page 2
   s18  check   (413,199)  [Sibling(s) Currently Enrolled] Preschool
```

Get the same thing as JSON, edit in the values, and fill:
```bash
python3 fill_form.py detect form.pdf > spots.json
# set "value" on the spots you want (text string, or true for checkboxes),
# then wrap them in {"entries": [...]}  — or just hand-write entries:
python3 fill_form.py fill form.pdf filled.pdf --data entries.json
```

`entries.json`:
```json
{
  "entries": [
    {"page": 0, "type": "text",  "rect": [309, 217, 519, 232], "value": "Jane Doe"},
    {"page": 1, "type": "check", "rect": [413, 199, 428, 215], "value": true},
    {"page": 2, "type": "text",  "rect": [100, 600, 300, 616], "value": "any text, anywhere"}
  ]
}
```

Coordinates are PDF points, origin top-left. Text is auto-shrunk to fit the
rect width. Add `--flatten` to rasterize the result so the values can't be
edited or extracted afterwards.

## Web UI

```bash
cd web && python3 server.py      # http://127.0.0.1:8000
```

Drop a PDF in, type directly on the rendered pages (or in the field list
on the right — they stay in sync), click checkboxes, click any empty spot
to add free text, then **Fill ▸** and download.

## Autofill profile

Keep your recurring answers in `config/profile.json` (gitignored) and the
web UI fills matching fields in one click. Each entry is a label, a kind
(name / phone / email / address / date / language / other) and a value:

```bash
cp config/profile.example.json config/profile.json
```

Or open **Edit profile** in the web UI's sidebar and type them there;
**Save** writes the same file.

Matching is by label wording, so name entries the way forms do:
"Parent 2 phone", "Child 1 name", "Home address line 2". The matcher
uses the kind, shared words, and ordinals like "(2)" or "second", and
skips fields whose label has words the entry lacks (a "school address"
never gets your home address). Every text field also has a dropdown of
profile values of the same kind.

## Limitations

- Scanned (image-only) PDFs aren't detected — there's no text layer or
  vector drawing to find. You can still click anywhere to place text.
- Label guessing is heuristic. Hover a spot in the web UI to see what it
  thinks the label is; the placement is what matters.
- One line of text per spot. For a multi-line answer, add a free spot per
  line.
