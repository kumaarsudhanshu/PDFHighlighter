from flask import Flask, render_template, request, send_from_directory, url_for
import fitz  # PyMuPDF
import os
import uuid
import re

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB limit

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploaded_pdfs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def normalize_text(text):
    return re.sub(r"\s+", "", text).lower()

# Predefined color palette, you can extend or modify it
DEFAULT_COLORS = [
    (1, 1, 0),      # Yellow
    (0, 1, 0),      # Green
    (0, 1, 1),      # Cyan
    (1, 0, 0),      # Red
    (1, 0, 1),      # Magenta
    (0, 0, 1),      # Blue
    (1, 0.5, 0),    # Orange
]

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        pdf_file = request.files.get('pdf')
        terms_raw = request.form.get('numbers', '')

        if not pdf_file or not terms_raw.strip():
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message="⚠️ PDF file and search terms are required",
                                   message_type="error")

        terms = list(set(filter(None, [t.strip() for t in terms_raw.split(',')])))
        if not terms:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message="⚠️ Please enter at least one valid number or text",
                                   message_type="error")

        # Assign each term a color in round-robin fashion
        term_colors = {}
        for idx, term in enumerate(terms):
            term_colors[term] = DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]

        input_filename = f"{uuid.uuid4()}.pdf"
        input_path = os.path.join(UPLOAD_FOLDER, input_filename)
        output_path = input_path.replace(".pdf", "_highlighted.pdf")
        pdf_file.save(input_path)

        try:
            doc = fitz.open(input_path)
        except Exception as e:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message=f"❌ Failed to open PDF: {e}",
                                   message_type="error")

        matched_pages = []
        match_count = 0
        no_text_flag = True

        for page_num, page in enumerate(doc, start=1):
            try:
                blocks = page.get_text("dict")["blocks"]
            except Exception as e:
                continue

            page_text = ""
            for block in blocks:
                if "lines" in block and block["lines"]:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            page_text += span["text"] + "\n"

            if not page_text.strip():
                continue

            no_text_flag = False

            for term in terms:
                escaped_term = re.escape(term)
                if term.isdigit():
                    pattern = re.compile(rf"(?<!\d){escaped_term}(?!\d|\.?\d)")
                elif re.search(r'[^\w\s]', term):
                    pattern = re.compile(escaped_term, re.IGNORECASE)
                else:
                    pattern = re.compile(rf"\b{escaped_term}\b", re.IGNORECASE)

                matches_found = list(pattern.finditer(page_text))
                if matches_found:
                    match_count += len(matches_found)
                    matched_pages.append((term, page_num))

                    # Highlight all occurrences with the assigned color
                    highlight_rects = page.search_for(term)
                    normalized_term = normalize_text(term)
                    if normalized_term != term:
                        highlight_rects += page.search_for(normalized_term)

                    unique_rects = {(r.x0, r.y0, r.x1, r.y1): r for r in highlight_rects}
                    color = term_colors.get(term, (1, 1, 0))  # fallback yellow

                    for rect in unique_rects.values():
                        highlight = page.add_highlight_annot(rect)
                        highlight.set_colors(stroke=color)
                        highlight.update()

        try:
            doc.save(output_path)
            doc.close()
        except Exception as e:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message=f"❌ Error saving PDF: {e}",
                                   message_type="error")

        view_url = url_for('view_file', filename=os.path.basename(output_path), _external=True)

        if no_text_flag:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message="⚠️ PDF me text available nahi hai. Agar scanned PDF hai to OCR enable karna padega.",
                                   message_type="error")

        if not matched_pages:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message="⚠️ No exact matches found.",
                                   message_type="error")

        msg_text = f"✅ {match_count} exact matches found!"
        msg_type = "success"

        return render_template("view_pdf.html",
                               filename=os.path.basename(output_path),
                               matches=matched_pages,
                               term_colors=term_colors,
                               view_url=view_url,
                               message=msg_text,
                               message_type=msg_type)

    return render_template("index.html", message=None, message_type=None)


@app.route('/files/<filename>')
def view_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
