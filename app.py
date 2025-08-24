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

        terms_normalized = [normalize_text(t) for t in terms]

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

        max_window_size = max(len(t) for t in terms_normalized)  # max length of term in chars (approx)

        for page_num, page in enumerate(doc, start=1):
            words = page.get_text("words")
            if not words:
                continue

            no_text_flag = False

            page_words = [w[4] for w in words]
            page_norm_words = [normalize_text(w) for w in page_words]
            page_word_rects = [fitz.Rect(w[:4]) for w in words]

            for term, norm_term in zip(terms, terms_normalized):
                found_in_page = False
                window_size = norm_term.count('/') + 1  # approximate word count heuristic

                # To handle approximate multi-word term matching, try sliding window of lengths near window_size
                min_window = max(1, window_size - 1)
                max_window = window_size + 1

                for win in range(min_window, max_window + 1):
                    for i in range(len(page_words) - win + 1):
                        combined_words = ''.join(page_norm_words[i:i + win])

                        if combined_words == norm_term:
                            # Highlight all words in window combined
                            combined_rect = page_word_rects[i]
                            for j in range(i+1, i + win):
                                combined_rect |= page_word_rects[j]
                            highlight = page.add_highlight_annot(combined_rect)
                            highlight.set_colors(stroke=highlight_color)
                            highlight.update()

                            found_in_page = True
                            match_count += 1

                    if found_in_page:
                        break

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
