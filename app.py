from flask import Flask, render_template, request, send_from_directory, url_for
import fitz  # PyMuPDF
import os
import uuid
import re

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB limit

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploaded_pdfs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def normalize_text(text):
    return re.sub(r"\s+", "", text).lower()


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        pdf_file = request.files.get('pdf')
        terms_raw = request.form.get('numbers', '')

        if not pdf_file or not terms_raw.strip():
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=[],
                view_url=None,
                message="⚠️ PDF file and search terms are required",
                message_type="error")

        # Parse and deduplicate search terms
        terms = list(set(filter(None, [t.strip() for t in terms_raw.split(',')])))
        if not terms:
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=[],
                view_url=None,
                message="⚠️ Please enter at least one valid number or text",
                message_type="error")

        input_filename = f"{uuid.uuid4()}.pdf"
        input_path = os.path.join(UPLOAD_FOLDER, input_filename)
        output_path = input_path.replace(".pdf", "_highlighted.pdf")
        pdf_file.save(input_path)

        try:
            doc = fitz.open(input_path)
        except Exception as e:
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=[],
                view_url=None,
                message=f"❌ Failed to open PDF: {e}",
                message_type="error")

        highlight_color = (1, 1, 0)  # Yellow
        matches_with_pages = []
        not_found_terms = set(terms)
        no_text_flag = True
        match_count = 0

        for page_num, page in enumerate(doc, start=1):
            words = page.get_text("words")  # List of tuples with bounding box and word text
            if not words:
                continue

            no_text_flag = False

            # Normalize words with their rects for easy comparison
            normalized_words = [(fitz.Rect(w[:4]), normalize_text(w[4])) for w in words]

            for term in terms:
                normalized_term = normalize_text(term)
                found_in_page = False

                for rect, word_norm in normalized_words:
                    if word_norm == normalized_term:
                        highlight = page.add_highlight_annot(rect)
                        highlight.set_colors(stroke=highlight_color)
                        highlight.update()
                        found_in_page = True
                        match_count += 1

                if found_in_page:
                    not_found_terms.discard(term)
                    matches_with_pages.append((term, page_num))

        try:
            doc.save(output_path)
            doc.close()
        except Exception as e:
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=[],
                view_url=None,
                message=f"❌ Error saving PDF: {e}",
                message_type="error")

        view_url = url_for('view_file', filename=os.path.basename(output_path), _external=True)

        if no_text_flag:
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=[],
                view_url=None,
                message="⚠️ PDF me text available nahi hai. Agar scanned PDF hai to OCR enable karna padega.",
                message_type="error")

        if not matches_with_pages:
            return render_template(
                "view_pdf.html",
                filename=None,
                matches=[],
                not_found=list(not_found_terms),
                view_url=None,
                message="⚠️ No exact matches found.",
                message_type="error")

        msg_text = f"✅ {match_count} total matches found!"
        msg_type = "success"

        return render_template(
            "view_pdf.html",
            filename=os.path.basename(output_path),
            matches=matches_with_pages,
            not_found=sorted(not_found_terms),
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
