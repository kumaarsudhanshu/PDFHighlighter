from flask import Flask, render_template, request, send_from_directory, url_for
import fitz  # PyMuPDF
import os
import uuid
import re

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB limit

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploaded_pdfs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

        input_filename = f"{uuid.uuid4()}.pdf"
        input_path = os.path.join(UPLOAD_FOLDER, input_filename)
        output_path = input_path.replace(".pdf", "_highlighted.pdf")
        pdf_file.save(input_path)

        try:
            doc = fitz.open(input_path)
            print(f"PDF loaded successfully: {input_filename}, pages: {doc.page_count}")
        except Exception as e:
            return render_template("view_pdf.html",
                                   filename=None,
                                   matches=[],
                                   view_url=None,
                                   message=f"❌ Failed to open PDF: {e}",
                                   message_type="error")

        highlight_color = (1, 1, 0)  # Yellow
        matched_pages = []
        match_count = 0
        no_text_flag = True

        for page_num, page in enumerate(doc, start=1):
            print(f"\n--- Processing page {page_num} ---")
            try:
                blocks = page.get_text("dict")["blocks"]
            except Exception as e:
                print(f"⚠️ Error reading page {page_num}: {e}")
                continue

            page_text = ""
            for block in blocks:
                if "lines" in block and block["lines"]:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            page_text += span["text"] + "\n"

            print(f"Page {page_num} extracted text length: {len(page_text)}")

            if not page_text.strip():
                print(f"Page {page_num} has no text, skipping page.")
                continue

            no_text_flag = False

            for term in terms:
                escaped_term = re.escape(term)
                # Regex se text matching karenge
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
                    print(f"Term '{term}' found {len(matches_found)} times on page {page_num}")

                    # Highlight sirf matched text ke exact coordinates pe karna
                    highlight_rects = page.search_for(term, hit_max=64)  
                    for rect in highlight_rects:
                        highlight = page.add_highlight_annot(rect)
                        highlight.set_colors(stroke=highlight_color)
                        highlight.update()

        try:
            doc.save(output_path)
            doc.close()
            print(f"PDF saved with highlights: {output_path}")
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
