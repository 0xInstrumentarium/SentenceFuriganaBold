import re
from aqt import mw
from aqt.utils import showInfo, qconnect
from aqt.qt import (
    QAction, QDialog, QVBoxLayout, QLabel,
    QComboBox, QDialogButtonBox, QFormLayout, QFrame
)


# --- Helpers ---

def strip_furigana(text):
    return re.sub(r'\[.*?\]', '', text)

def strip_html(text):
    return re.sub(r'<[^>]+>', '', text)

def is_kanji(ch):
    return '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf'

def to_hiragana(text):
    return ''.join(chr(ord(c) - 0x60) if '\u30a1' <= c <= '\u30f6' else c for c in text)

def get_kanji_runs(word):
    runs = []
    current = ""
    for ch in word:
        if is_kanji(ch):
            current += ch
        else:
            if current:
                runs.append(current)
                current = ""
    if current:
        runs.append(current)
    return runs

def kana_from_expression_furigana(expr_furigana):
    """
    Derive full kana reading from ExpressionFurigana.
    e.g. "そう 言[い]う" -> "そういう"
         "あんなに"      -> "あんなに"
    """
    text = re.sub(r'[^\[\]\s]+\[([^\]]+)\]', r'\1', expr_furigana)
    text = re.sub(r'\[.*?\]', '', text)
    text = text.replace(' ', '')
    return to_hiragana(text)

def tokenize_sentence(furigana):
    """
    Split sentence into parts, keeping spaces and <br> as separate entries.
    Returns (parts, word_tokens) where:
      parts       = full list of raw strings (tokens + separators) for lossless rejoin
      word_tokens = list of (index_in_parts, plain_kana, raw_tok) for content tokens only
    """
    parts = re.split(r'( |<br>)', furigana)
    word_tokens = []
    for i, tok in enumerate(parts):
        if tok in (' ', '<br>', ''):
            continue
        plain = to_hiragana(strip_furigana(strip_html(tok)))
        if plain.strip():
            word_tokens.append((i, plain, tok))
    return parts, word_tokens


# --- Core logic ---

def bold_word_in_sentence_furigana(word, expr_furigana, sentence_furigana):
    raw_tokens, word_tokens = tokenize_sentence(sentence_furigana)

    kanji_runs = get_kanji_runs(word)
    kana_form  = kana_from_expression_furigana(expr_furigana)

    if kanji_runs:
        matched_positions = []
        for run in kanji_runs:
            for pos, (i, plain, tok) in enumerate(word_tokens):
                plain_orig = strip_furigana(strip_html(raw_tokens[i]))
                if run in plain_orig:
                    matched_positions.append(pos)

        if matched_positions:
            first, last = min(matched_positions), max(matched_positions)
            return _apply_bold_span(first, last, raw_tokens, word_tokens)
        # Kanji not present in sentence — fall through to kana match

    # Kana fallback using exact reading from ExpressionFurigana
    return _bold_kana_match(kana_form, raw_tokens, word_tokens)


def _bold_kana_match(kana_form, raw_tokens, word_tokens):
    # Exact span match
    for start in range(len(word_tokens)):
        accumulated = ""
        for end in range(start, len(word_tokens)):
            accumulated += word_tokens[end][1]
            if accumulated == kana_form:
                return _apply_bold_span(start, end, raw_tokens, word_tokens)
            if len(accumulated) >= len(kana_form):
                break

    # Prefix fallback (handles inflection)
    for start in range(len(word_tokens)):
        accumulated = ""
        for end in range(start, len(word_tokens)):
            accumulated += word_tokens[end][1]
            if kana_form.startswith(accumulated) and len(accumulated) >= 2:
                if end + 1 >= len(word_tokens):
                    return _apply_bold_span(start, end, raw_tokens, word_tokens)
                next_acc = accumulated + word_tokens[end + 1][1]
                if not kana_form.startswith(next_acc):
                    return _apply_bold_span(start, end, raw_tokens, word_tokens)
            if len(accumulated) > len(kana_form):
                break

    return None


def _apply_bold_span(first, last, raw_tokens, word_tokens):
    result = list(raw_tokens)
    span_indices = [word_tokens[p][0] for p in range(first, last + 1)]
    min_i, max_i = span_indices[0], span_indices[-1]
    inner = "".join(result[min_i:max_i + 1])
    inner = re.sub(r'<b>(.*?)</b>', r'\1', inner, flags=re.DOTALL)
    bolded = "<b>" + inner + "</b>"
    new_result = result[:min_i] + [bolded] + result[max_i + 1:]
    return "".join(new_result)


def run_bold(deck_name, note_type, word_field, expr_furigana_field, sentence_furigana_field):
    col = mw.col
    if not col:
        showInfo("No collection open.")
        return

    query = f'deck:"{deck_name}" note:"{note_type}"'
    note_ids = col.find_notes(query)

    if not note_ids:
        showInfo(f"No notes found in deck \"{deck_name}\" with note type \"{note_type}\".")
        return

    modified = 0
    skipped = 0

    for nid in note_ids:
        note = col.get_note(nid)

        if (word_field not in note
                or expr_furigana_field not in note
                or sentence_furigana_field not in note):
            skipped += 1
            continue

        word          = strip_html(note[word_field].strip())
        expr_furigana = note[expr_furigana_field]
        sent_furigana = note[sentence_furigana_field]

        # Skip if word already bolded
        already_bolded_plain = strip_furigana(strip_html(
            "".join(re.findall(r'<b>(.*?)</b>', sent_furigana, flags=re.DOTALL))
        ))
        kanji_runs = get_kanji_runs(word)
        kana_form  = kana_from_expression_furigana(expr_furigana)

        if kanji_runs and any(run in already_bolded_plain for run in kanji_runs):
            skipped += 1
            continue
        if not kanji_runs and kana_form in to_hiragana(already_bolded_plain):
            skipped += 1
            continue

        result = bold_word_in_sentence_furigana(word, expr_furigana, sent_furigana)
        if result:
            note[sentence_furigana_field] = result
            col.update_note(note)
            modified += 1
        else:
            skipped += 1

    col.save()
    showInfo(f"Done!\n\nDeck: {deck_name}\nNote type: {note_type}\n\nModified: {modified} notes\nSkipped: {skipped} notes")


# --- UI ---

class BoldFuriganaDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bold Target Word in Furigana")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self.deck_combo = QComboBox()
        self.deck_combo.setMinimumWidth(240)
        form.addRow(QLabel("Deck:"), self.deck_combo)

        self.notetype_combo = QComboBox()
        self.notetype_combo.setMinimumWidth(240)
        form.addRow(QLabel("Note type:"), self.notetype_combo)

        self.word_combo = QComboBox()
        self.word_combo.setMinimumWidth(240)
        form.addRow(QLabel("Word field:"), self.word_combo)

        self.expr_furigana_combo = QComboBox()
        self.expr_furigana_combo.setMinimumWidth(240)
        form.addRow(QLabel("Expression furigana field:"), self.expr_furigana_combo)

        self.sent_furigana_combo = QComboBox()
        self.sent_furigana_combo.setMinimumWidth(240)
        form.addRow(QLabel("Sentence furigana field:"), self.sent_furigana_combo)

        layout.addLayout(form)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        buttons = QDialogButtonBox()
        self.run_btn = buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        qconnect(self.run_btn.clicked, self._on_run)
        qconnect(cancel_btn.clicked, self.reject)
        layout.addWidget(buttons)

        self._populate_decks()
        self._populate_notetypes()
        qconnect(self.notetype_combo.currentIndexChanged, self._on_notetype_changed)

    def _populate_decks(self):
        self.deck_combo.clear()
        decks = sorted(mw.col.decks.all(), key=lambda d: d["name"].lower())
        for deck in decks:
            self.deck_combo.addItem(deck["name"])

    def _populate_notetypes(self):
        self.notetype_combo.clear()
        for nt in sorted(mw.col.models.all(), key=lambda m: m["name"].lower()):
            self.notetype_combo.addItem(nt["name"])
        self._refresh_fields()

    def _refresh_fields(self):
        name = self.notetype_combo.currentText()
        model = mw.col.models.by_name(name)
        fields = [f["name"] for f in model["flds"]] if model else []

        for combo in (self.word_combo, self.expr_furigana_combo, self.sent_furigana_combo):
            combo.clear()
        for f in fields:
            self.word_combo.addItem(f)
            self.expr_furigana_combo.addItem(f)
            self.sent_furigana_combo.addItem(f)

        for i, f in enumerate(fields):
            fl = f.lower()
            if fl in ("word", "vocab", "単語"):
                self.word_combo.setCurrentIndex(i)
            if "expression" in fl and "furigana" in fl:
                self.expr_furigana_combo.setCurrentIndex(i)
            if "sentence" in fl and "furigana" in fl:
                self.sent_furigana_combo.setCurrentIndex(i)

    def _on_notetype_changed(self):
        self._refresh_fields()

    def _on_run(self):
        deck_name           = self.deck_combo.currentText()
        note_type           = self.notetype_combo.currentText()
        word_field          = self.word_combo.currentText()
        expr_furigana_field = self.expr_furigana_combo.currentText()
        sent_furigana_field = self.sent_furigana_combo.currentText()

        if expr_furigana_field == sent_furigana_field:
            showInfo("Expression furigana and Sentence furigana fields cannot be the same.")
            return

        self.accept()
        run_bold(deck_name, note_type, word_field, expr_furigana_field, sent_furigana_field)


def open_dialog():
    if not mw.col:
        showInfo("No collection open.")
        return
    dialog = BoldFuriganaDialog(mw)
    dialog.exec()


action = QAction("Bold target word in furigana…", mw)
qconnect(action.triggered, open_dialog)
mw.form.menuTools.addAction(action)