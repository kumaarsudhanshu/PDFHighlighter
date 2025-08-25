from flask import Flask, render_template, request, send_from_directory, url_for, make_response
import fitz  # PyMuPDF
import os
import uuid
import re
import unicodedata

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploaded_pdfs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------------- Normalization & Helpers (UPDATED) --------------

def normalize_text(text: str) -> str:
    s = unicodedata.normalize('NFKD', text)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.replace('\u200b', '')                 # zero-width space
    s = s.replace('\u00a0', '')                 # non-breaking space
    s = s.replace('\u2013', '-')                # en-dash -> hyphen
    s = s.replace('\u2014', '-')                # em-dash -> hyphen
    s = re.sub(r'\s+', '', s)                   # remove ALL spaces
    s = s.lower().strip()
    return s

def normalize_token_keep_dash_space(text: str) -> str:
    s = unicodedata.normalize('NFKD', text)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.replace('\u200b', '')
    s = s.replace('\u00a0', ' ')
    s = s.replace('\u2013', '-')
    s = s.replace('\u2014', '-')
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s

def is_numeric_term(raw: str) -> bool:
    # Updated: Only pure numbers or number patterns without decimals
    return re.fullmatch(r'\d+(?:[/\\-]\d+)*', raw.strip()) is not None and '.' not in raw

def split_term_tokens(raw_term: str):
    t = normalize_token_keep_dash_space(raw_term)
    parts = []
    for tok in t.split(' '):
        segs = re.split(r'(-)', tok)  # keep '-' as its own token
        for p in segs:
            if p != '':
                parts.append(p)
    return parts

def to_loose_regex(term: str) -> str:
    term_escaped = re.escape(term)
    term_escaped = term_escaped.replace(r'\-', r'[-\u2013\u2014]')
    
    # FLEXIBLE SPACE HANDLING - allow optional spaces around special chars
    term_escaped = re.sub(r'\\\s+', r'\\s*', term_escaped)
    
    # Add flexible spaces around dashes and slashes
    term_escaped = re.sub(r'([-\u2013\u2014])', r'\\s*\\1\\s*', term_escaped)
    term_escaped = re.sub(r'([/\\])', r'\\s*\\1\\s*', term_escaped)
    
    # Clean up multiple \s* patterns
    term_escaped = re.sub(r'(\\s\*){2,}', r'\\s*', term_escaped)
    
    # STRICT boundaries for numeric/slash terms to avoid partial matches like 71 in 71.4 or 24 in 2410
    if is_numeric_term(term) or '/' in term or '\\' in term:
        term_escaped = r'(?<![\d/\\])' + term_escaped + r'(?![\d/\\.])'  # No digits, slashes, or dots before/after
    elif '-' in term:  # For dash terms like 2022-2026
        term_escaped = r'(?<!\w)' + term_escaped + r'(?!\w)'
    
    return term_escaped

# -------------- Highlight Helper (ORIGINAL) --------------

def add_highlight_quads(page, rects, color=(1, 1, 0), opacity=1.0):
    if not rects:
        print("No rects to highlight!")
        return False

    try:
        annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=color, fill=color) 
        annot.set_opacity(opacity)
        annot.update()
        print(f"Highlighted {len(rects)} areas.")
        return True
    except Exception as e:
        print(f"Failed to add annotation: {e}")
        try:
            for rect in rects:
                annot = page.add_highlight_annot(rect)
                annot.set_colors(stroke=color, fill=color)
                annot.set_opacity(opacity) 
                annot.update()
            print(f"Highlighted {len(rects)} areas individually.")
            return True
        except Exception as e2:
            print(f"Individual highlighting also failed: {e2}")
            return False

# -------------- Routes (UPDATED LOGIC) --------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        pdf_file = request.files.get('pdf')
        terms_raw = request.form.get('numbers', '')

        if not pdf_file or not terms_raw.strip():
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[], view_url=None,
                                   message="⚠️ PDF file and search terms are required", message_type="error")

        terms = list(set(filter(None, [t.strip() for t in terms_raw.split(',')])))
        if not terms:
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None, message="⚠️ Please enter at least one valid number or text",
                                   message_type="error")

        terms_normalized = [normalize_text(t) for t in terms]

        input_filename = f"{uuid.uuid4()}.pdf"
        input_path = os.path.join(UPLOAD_FOLDER, input_filename)
        output_filename = f"{uuid.uuid4()}_highlighted.pdf"
        output_path = os.path.join(UPLOAD_FOLDER, output_filename)
        pdf_file.save(input_path)

        try:
            doc = fitz.open(input_path)
        except Exception as e:
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None, message=f"❌ Failed to open PDF: {e}", message_type="error")

        highlight_color = (1, 1, 0)
        matches_with_pages = []
        not_found_terms = set(terms)
        no_text_flag = True
        match_count = 0

        for page_num, page in enumerate(doc, start=1):
            words = page.get_text("words")
            if not words:
                continue

            no_text_flag = False

            page_words_raw = [w[4] for w in words]
            page_rects = [fitz.Rect(w[:4]) for w in words]

            page_words_norm_full = [normalize_text(w) for w in page_words_raw]
            page_words_norm_token = [normalize_token_keep_dash_space(w) for w in page_words_raw]

            # Both normalized WITH SPACES and WITHOUT SPACES!
            page_text_raw = page.get_text()
            page_text_norm_nospace = normalize_text(page_text_raw)
            page_text_norm_space = normalize_token_keep_dash_space(page_text_raw)

            for term, norm_term in zip(terms, terms_normalized):
                found_in_page = False
                highlighted = False  # New flag to track if highlighted
                numeric_query = is_numeric_term(term)
                has_slash = '/' in term or '\\' in term
                has_dash = '-' in term

                # 1) Single-word exact equality (strict for numbers)
                for i, wnorm in enumerate(page_words_norm_full):
                    if wnorm == norm_term:
                        if add_highlight_quads(page, [page_rects[i]], color=highlight_color):
                            highlighted = True
                            found_in_page = True
                            match_count += 1
                        break

                # 2) Phrase / multi-token scan (exact tokens)
                if not found_in_page:
                    term_tokens_raw = split_term_tokens(term)
                    term_tokens_norm = [normalize_text(tk) for tk in term_tokens_raw]
                    win = len(term_tokens_norm)

                    if win > 1:
                        page_tokens = []
                        page_token_rects = []
                        for w_str, w_rect in zip(page_words_norm_token, page_rects):
                            segs = re.split(r'(-)', w_str)  # keep '-' as its own token
                            for seg in segs:
                                if seg == '' or seg == ' ':
                                    continue
                                page_tokens.append(normalize_text(seg))
                                page_token_rects.append(w_rect)

                        for i in range(0, len(page_tokens) - win + 1):
                            if page_tokens[i:i + win] == term_tokens_norm:
                                rects_span = [page_token_rects[j] for j in range(i, i + win)]
                                if add_highlight_quads(page, rects_span, color=highlight_color):
                                    highlighted = True
                                    found_in_page = True
                                    match_count += 1
                                break

                # 3) Flexible regex matching ONLY if not found yet, and STRICT for detection (UPDATED)
                if not found_in_page and (numeric_query or has_slash or has_dash):
                    pattern = to_loose_regex(term)
                    try:
                        # Search in raw and normalized text, but only mark found if match and we can highlight
                        matches = list(re.finditer(pattern, page_text_raw, re.IGNORECASE)) + list(re.finditer(pattern, page_text_norm_space, re.IGNORECASE))
                        if matches:
                            # Attempt to highlight the first match (but since we want exact, we skip if not highlightable)
                            # Note: For detection, we now require highlighting success for "found"
                            # But since highlighting is separate, we assume if regex matches exactly, it's found only if highlighted
                            # UPDATED: We don't highlight here; we only detect. But to avoid false positives, we check if it's exact
                            for match in matches:
                                # Extract matched text and check if it exactly matches normalized term (ignoring spaces)
                                matched_text = match.group(0)
                                matched_norm = normalize_text(matched_text)
                                if matched_norm == norm_term:  # Strict: no extras like .4
                                    # Now, to highlight, we need rects - but since regex doesn't give rects, we skip highlighting but mark found
                                    # Wait, problem: we need to highlight. So for regex, we use search_instances to get rects
                                    inst = page.search_for(matched_text)
                                    if inst and add_highlight_quads(page, inst, color=highlight_color):
                                        highlighted = True
                                        found_in_page = True
                                        match_count += 1
                                        break
                    except re.error as e:
                        print(f"Regex error for term '{term}': {e}")
                        pass
                        
                # 4) Text-only terms: safe substring matching (but strict for numerics - SKIP for numerics)
                if not found_in_page and not (numeric_query or has_slash or has_dash):
                    search_nospace = normalize_text(term)
                    search_space = normalize_token_keep_dash_space(term)
                    if search_nospace in page_text_norm_nospace or search_space in page_text_norm_space:
                        # For non-numeric, we still detect, but attempt highlight with search_for
                        inst = page.search_for(term)
                        if inst and add_highlight_quads(page, inst, color=highlight_color):
                            highlighted = True
                            found_in_page = True
                            match_count += 1

                # Only if highlighted or exactly found, remove from not_found
                if highlighted or found_in_page:
                    not_found_terms.discard(term)
                    matches_with_pages.append((term, page_num))

        try:
            doc.save(output_path)
            doc.close()
        except Exception as e:
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None, message=f"❌ Error saving PDF: {e}", message_type="error")

        view_url = url_for('view_file', filename=os.path.basename(output_path), _external=True) + f"?v={uuid.uuid4()}"

        if no_text_flag:
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None,
                                   message="⚠️ PDF me text available nahi hai. Agar scanned PDF hai to OCR enable karna padega.",
                                   message_type="error")

        if not matches_with_pages:
            return render_template("view_pdf.html", filename=None, matches=[], not_found=list(not_found_terms),
                                   view_url=view_url,
                                   message="⚠️ No exact matches found.",
                                   message_type="error")

        msg_text = f"✅ {match_count} total matches found!"
        return render_template("view_pdf.html",
                               filename=os.path.basename(output_path),
                               matches=matches_with_pages,
                               not_found=sorted(not_found_terms),
                               view_url=view_url,
                               message=msg_text, message_type="success")

    return render_template("index.html", message=None, message_type=None)

@app.route('/files/<filename>')
def view_file(filename):
    resp = make_response(send_from_directory(UPLOAD_FOLDER, filename))
    resp.cache_control.no_cache = True
    resp.cache_control.max_age = 0
    return resp

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
