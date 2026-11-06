"""A small PDF writer for OrbitWatch's printable reports (no third-party library).

A4 pages, the standard Helvetica and Helvetica-Bold fonts (every PDF reader has them, nothing is embedded), text in
Windows-1252, wrapping by the real Helvetica character widths, thin rules and shaded header rows. Enough for tables of
text, which is what the reports are; not a general layout engine.
"""
import zlib

PAGE_W, PAGE_H = 595.28, 841.89          # A4 in points
MARGIN = 42

# Helvetica advance widths (1/1000 em) for character codes 32..126, from the standard AFM.
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
      556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
      722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
      556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
      260, 334, 584]
_REPLACE = {"Δ": "dv ", "→": "->", "≥": ">=", "≤": "<=", "−": "-", "✓": "ok", "✗": "x", "◀": "<", "’": "'", "‘": "'"}


def _clean(text):
    text = str(text if text is not None else "")
    for a, b in _REPLACE.items():
        text = text.replace(a, b)
    return text.encode("cp1252", "replace").decode("cp1252")


def text_width(text, size, bold=False):
    w = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in _clean(text))
    return w * size / 1000.0 * (1.05 if bold else 1.0)


def wrap(text, width, size, bold=False):
    """Lines of `text` no wider than `width` points (long words are cut)."""
    out = []
    for para in _clean(text).split("\n"):
        line = ""
        for word in para.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, bold) <= width:
                line = candidate
                continue
            if line:
                out.append(line)
            while text_width(word, size, bold) > width:            # a word longer than the column
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return out


def _esc(text):
    return _clean(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Document:
    def __init__(self, title):
        self.title = title
        self.pages = []
        self.ops = None
        self.y = 0
        self.new_page()

    # -- drawing --------------------------------------------------------------------------------------------------
    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = PAGE_H - MARGIN

    def ensure(self, height):
        if self.y - height < MARGIN + 14:
            self.new_page()

    def text(self, x, y, s, size=9, bold=False, grey=0.0):
        self.ops.append(f"BT {grey:.2f} g /{'F2' if bold else 'F1'} {size} Tf {x:.2f} {y:.2f} Td ({_esc(s)}) Tj ET")

    def rule(self, x1, y1, x2, y2, grey=0.75, width=0.5):
        self.ops.append(f"{grey:.2f} G {width} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def shade(self, x, y, w, h, grey=0.93):
        self.ops.append(f"{grey:.2f} g {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    # -- blocks ---------------------------------------------------------------------------------------------------
    def heading(self, s, size=15):
        self.ensure(size + 10)
        self.y -= size
        self.text(MARGIN, self.y, s, size, bold=True)
        self.y -= 8

    def paragraph(self, s, size=9, grey=0.3):
        for line in wrap(s, PAGE_W - 2 * MARGIN, size):
            self.ensure(size + 3)
            self.y -= size + 3
            self.text(MARGIN, self.y, line, size, grey=grey)
        self.y -= 4

    def table(self, columns, rows, widths, size=8, header=True, highlight=None):
        """columns: header texts; rows: lists of cell texts; widths: fractions of the text width.
        highlight(row_index) -> True shades that row (e.g. values that differ)."""
        total = PAGE_W - 2 * MARGIN
        ws = [total * f / sum(widths) for f in widths]
        pad, lead = 4, size + 2.5

        def draw_row(cells, bold, shaded):
            wrapped = [wrap(c, w - 2 * pad, size, bold) for c, w in zip(cells, ws)]
            height = max(len(w) for w in wrapped) * lead + 2 * pad - 2
            if self.y - height < MARGIN + 14:
                self.new_page()
                if header and not bold:
                    draw_row(columns, True, True)
            top = self.y
            if shaded:
                self.shade(MARGIN, top - height, total, height, 0.90 if bold else 0.96)
            x = MARGIN
            for lines, w in zip(wrapped, ws):
                for i, line in enumerate(lines):
                    self.text(x + pad, top - pad - (i + 1) * lead + 3, line, size, bold)
                x += w
            self.rule(MARGIN, top - height, MARGIN + total, top - height)
            self.y = top - height

        if header:
            draw_row(columns, True, True)
        for i, r in enumerate(rows):
            draw_row([str(c) for c in r], False, bool(highlight and highlight(i)))
        self.y -= 10

    # -- output ---------------------------------------------------------------------------------------------------
    def render(self):
        n = len(self.pages)
        objs = {1: "<< /Type /Catalog /Pages 2 0 R >>",
                3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
                5: f"<< /Title ({_esc(self.title)}) /Producer (OrbitWatch) >>"}
        kids = []
        streams = {}
        for i, ops in enumerate(self.pages):
            page_id, content_id = 6 + 2 * i, 7 + 2 * i
            kids.append(f"{page_id} 0 R")
            footer = f"BT 0.45 g /F1 7.5 Tf {MARGIN:.2f} 24 Td ({_esc(self.title)}  -  page {i + 1} of {n}) Tj ET"
            data = zlib.compress("\n".join(ops + [footer]).encode("cp1252", "replace"))
            streams[content_id] = data
            objs[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                             f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>")
        objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>"
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for num in sorted(set(objs) | set(streams)):
            offsets[num] = len(out)
            if num in streams:
                out += f"{num} 0 obj\n<< /Length {len(streams[num])} /Filter /FlateDecode >>\nstream\n".encode()
                out += streams[num] + b"\nendstream\nendobj\n"
            else:
                out += f"{num} 0 obj\n{objs[num]}\nendobj\n".encode("cp1252", "replace")
        size = max(offsets) + 1
        xref = len(out)
        out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
        for num in range(1, size):
            out += f"{offsets[num]:010d} 00000 n \n".encode() if num in offsets else b"0000000000 65535 f \n"
        out += f"trailer\n<< /Size {size} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
