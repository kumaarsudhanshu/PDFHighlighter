from flask import Flask, render_template, request, send_from_directory, url_for, make_response
import fitz  # PyMuPDF
import os
import uuid
import re
import unicodedata
import logging
import sys
import traceback

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
if not app.logger.handlers:
    app.logger.addHandler(handler)

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploaded_pdfs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

SEP_CHARS = ['\u00A0', '\u200B', '\u200C', '\u200D', '\u2060', '\uFEFF', '\t', '\n', '\r', ' ']
SLASH_GLYPHS = ['\u2215', '\u2044', '\u2571', '\u29F8', '\uFF0F', '\u2F0A', '\u2E3B', '\u3382']

def normalize_text(text, keep_space=False):
    s = unicodedata.normalize('NFKC', text)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    for ch in SEP_CHARS:
        s = s.replace(ch, ' ' if keep_space else '')
    for ch in SLASH_GLYPHS:
        s = s.replace(ch, '/')
    s = s.replace('−', '-').replace('‐', '-')
    if keep_space:
        s = re.sub(r'\s+', ' ', s)
    else:
        s = re.sub(r'\s+', '', s)
    return s.lower().strip()

def normalize_token_keep_dash_space(text: str) -> str:
    return normalize_text(text, keep_space=True)

def is_numeric_term(raw: str) -> bool:
    return re.fullmatch(r'\d+(?:[/\\-]\d+)*', raw.strip()) is not None and '.' not in raw

def split_term_tokens(raw_term: str):
    t = normalize_token_keep_dash_space(raw_term)
    parts = []
    for tok in t.split(' '):
        segs = re.split(r'(-)', tok)
        for p in segs:
            if p != '':
                parts.append(p)
    return parts

def to_loose_regex(term: str) -> str:
    flex_sep = r'[\\s\\u00A0\\u200B\\u200C\\u200D\\u2060\\uFEFF\\t\\n\\r]*'
    term_escaped = ''.join(
        flex_sep if ch in '/-' else re.escape(ch)
        for ch in term
    )
    if is_numeric_term(term):
        term_escaped = fr'(?<![\d/\\.-]){term_escaped}(?![\d/\\.-])'
    else:
        term_escaped = fr'(?<!\w){term_escaped}(?!\w)'
    return term_escaped

def replacement_spaces(match):
    return r'\\s*' + re.escape(match.group(1)) + r'\\s*'

def create_combined_regex(terms):
    numeric_terms = [t for t in terms if is_numeric_term(t)]
    if not numeric_terms:
        return None
    escaped_terms = []
    for t in numeric_terms:
        t_escaped = re.escape(t)
        t_escaped = t_escaped.replace(r'\-', r'[-\u2013\u2014]')
        t_escaped = re.sub(r'\\\s+', r'\\s*', t_escaped)
        t_escaped = re.sub(r'([-\u2013\u2014/\\])', replacement_spaces, t_escaped)
        t_escaped = re.sub(r'(\\s\*){2,}', r'\\s*', t_escaped)
        t_escaped = r'(?<![\d/\\\.])' + t_escaped + r'(?![\d/\\\.])'
        escaped_terms.append(t_escaped)
    pattern = r'(?:' + '|'.join(escaped_terms) + r')'
    return re.compile(pattern, re.IGNORECASE)

def add_highlight_quads(page, rects, color=(1,1,0), opacity=1.0):
    if not rects:
        app.logger.debug("No rectangles to highlight")
        sys.stdout.flush()
        return False
    try:
        annot = page.add_highlight_annot(rects)
        annot.set_colors(stroke=color, fill=color)
        annot.set_opacity(opacity)
        annot.update()
        return True
    except Exception as e:
        app.logger.error(f"Failed to add highlight annotation: {e}")
        sys.stdout.flush()
        try:
            for rect in rects:
                annot = page.add_highlight_annot(rect)
                annot.set_colors(stroke=color, fill=color)
                annot.set_opacity(opacity)
                annot.update()
            return True
        except Exception as e2:
            app.logger.error(f"Individual highlighting failed: {e2}")
            sys.stdout.flush()
            return False

@app.route('/', methods=['GET', 'POST'])
def index():
    app.logger.debug("New request received at /")
    sys.stdout.flush()
    if request.method == 'POST':
        app.logger.info("POST request started processing")
        sys.stdout.flush()
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

        terms_normalized = {t: normalize_text(t) for t in terms}

        input_filename = f"{uuid.uuid4()}.pdf"
        input_path = os.path.join(UPLOAD_FOLDER, input_filename)
        output_filename = f"{uuid.uuid4()}_highlighted.pdf"
        output_path = os.path.join(UPLOAD_FOLDER, output_filename)
        pdf_file.save(input_path)

        try:
            app.logger.info("Opening PDF...")
            doc = fitz.open(input_path)
            app.logger.info("PDF opened, extracting text from first page...")
            first_page_text = doc[0].get_text()
            app.logger.info(f"First page text snippet: {first_page_text[:300]!r}")
            sys.stdout.flush()
        except Exception as e:
            app.logger.error(f"Failed to open PDF: {e}")
            sys.stdout.flush()
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None, message=f"❌ Failed to open PDF: {e}", message_type="error")

        highlight_color = (1, 1, 0)
        matches_with_pages = []
        not_found_terms = set(terms)
        no_text_flag = True
        match_count = 0

        combined_regex = create_combined_regex(terms)

        try:
            for page_num, page in enumerate(doc, start=1):
                app.logger.debug(f"Processing page {page_num}...")
                sys.stdout.flush()
                words = page.get_text("words")
                if not words:
                    continue
                no_text_flag = False

                page_text_raw = page.get_text()
                page_text_norm_nospace = normalize_text(page_text_raw)
                page_text_norm_space = normalize_token_keep_dash_space(page_text_raw)

                if combined_regex:
                    for match in combined_regex.finditer(page_text_raw) or combined_regex.finditer(page_text_norm_space):
                        matched_term = match.group(0)
                        norm_matched = normalize_text(matched_term)
                        for term, norm_term in list(terms_normalized.items()):
                            if norm_matched == norm_term and term in not_found_terms:
                                inst = page.search_for(matched_term)
                                if inst and add_highlight_quads(page, inst, color=highlight_color):
                                    not_found_terms.discard(term)
                                    matches_with_pages.append((term, page_num))
                                    match_count += 1
                                break

                for term in list(not_found_terms):
                    found_in_page = False
                    norm_term = terms_normalized[term]
                    numeric_query = is_numeric_term(term)
                    has_slash = '/' in term or '\\' in term
                    has_dash = '-' in term

                    page_words_norm_full = [normalize_text(w[4]) for w in words]
                    page_rects = [fitz.Rect(w[:4]) for w in words]
                    for i, wnorm in enumerate(page_words_norm_full):
                        if wnorm == norm_term:
                            if add_highlight_quads(page, [page_rects[i]], color=highlight_color):
                                found_in_page = True
                                match_count += 1
                            break

                    if not found_in_page:
                        term_tokens_norm = [normalize_text(tk) for tk in split_term_tokens(term)]
                        win = len(term_tokens_norm)
                        if win > 1:
                            page_tokens = []
                            page_token_rects = []
                            for w_str, w_rect in zip([normalize_token_keep_dash_space(w[4]) for w in words], page_rects):
                                segs = re.split(r'(-)', w_str)
                                for seg in segs:
                                    if seg.strip():
                                        page_tokens.append(normalize_text(seg))
                                        page_token_rects.append(w_rect)
                            for i in range(len(page_tokens) - win + 1):
                                if page_tokens[i:i + win] == term_tokens_norm:
                                    rects_span = page_token_rects[i:i + win]
                                    if add_highlight_quads(page, rects_span, color=highlight_color):
                                        found_in_page = True
                                        match_count += 1
                                    break

                    if not found_in_page and (numeric_query or has_slash or has_dash):
                        pattern = to_loose_regex(term)
                        try:
                            regex_matches = list(re.finditer(pattern, page_text_raw, re.IGNORECASE)) + list(re.finditer(pattern, page_text_norm_space, re.IGNORECASE))
                            for rmatch in regex_matches:
                                matched_text = rmatch.group(0)
                                matched_norm = normalize_text(matched_text)
                                if matched_norm == norm_term:
                                    inst = page.search_for(matched_text)
                                    if inst and add_highlight_quads(page, inst, color=highlight_color):
                                        found_in_page = True
                                        match_count += 1
                                        break
                        except re.error:
                            pass

                    # FIXED: Proper word boundary check for all terms
                    if not found_in_page:
                        search_nospace = normalize_text(term)
                        search_space = normalize_token_keep_dash_space(term)
                        
                        if (search_nospace in page_text_norm_nospace or 
                            search_space in page_text_norm_space or 
                            search_nospace in normalize_text(page_text_raw)):
                            
                            # Word boundary check for all terms to prevent false positives
                            escaped_term = re.escape(term)
                            pattern = r'(?:^|[\s\W])' + escaped_term + r'(?=[\s\W]|$)'
                            
                            if re.search(pattern, page_text_raw, re.IGNORECASE):
                                inst = page.search_for(term)
                                if inst and add_highlight_quads(page, inst, color=highlight_color):
                                    found_in_page = True
                                    match_count += 1

                    if found_in_page:
                        not_found_terms.discard(term)
                        matches_with_pages.append((term, page_num))

            doc.save(output_path)
            doc.close()
        except Exception as e:
            app.logger.error(f"Exception during processing: {e}")
            traceback.print_exc()
            sys.stdout.flush()
            return render_template("view_pdf.html", filename=None, matches=[], not_found=[],
                                   view_url=None, message=f"❌ Error processing PDF: {e}", message_type="error")

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
        app.logger.info(f"Processing completed: {match_count} matches found")
        sys.stdout.flush()
        return render_template("view_pdf.html",
                               filename=os.path.basename(output_path),
                               matches=matches_with_pages,
                               not_found=sorted(not_found_terms),
                               view_url=view_url,
                               message=msg_text, message_type="success")

    return render_template("index.html", message=None, message_type=None)

@app.route('/files/<filename>')
def view_file(filename):
    app.logger.debug(f"Viewing file: {filename}")
    resp = make_response(send_from_directory(UPLOAD_FOLDER, filename))
    resp.cache_control.no_cache = True
    resp.cache_control.max_age = 0
    return resp

@app.route('/download/<filename>')
def download_file(filename):
    app.logger.debug(f"Downloading file: {filename}")
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port, debug=True)
