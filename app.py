import streamlit as st
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from st_click_detector import click_detector
import html
import re
import json
from openai import OpenAI

# --- ページ設定（スクロール戻り対策のため、構成を固定） ---
st.set_page_config(layout="wide", page_title="AI Book Reader")

# --- 設定: Google連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except:
        return None

# --- 設定: OpenAI翻訳 (JSON) ---
def translate_word_with_gpt(text):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    prompt = f"""
    You are an English-Japanese dictionary.
    Explain the word: "{text}".
    Output MUST be a JSON object with these keys:
    1. "meaning": Japanese meaning (short & clear).
    2. "pos": Part of Speech (e.g., Verb, Noun).
    3. "details": Synonyms or nuance explanation (keep it short).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"meaning": "Translation Error", "pos": "-", "details": "Please try again."}

# --- テキスト整形 ---
def format_text_advanced(text):
    if not text: return []
    lines = text.splitlines()
    formatted_blocks = []
    current_paragraph = ""
    sentence_endings = ('.', ',', '!', '?', ':', ';', '"', "'", '”', '’', ')', ']')

    for line in lines:
        line = line.strip()
        if not line: continue
        is_bullet = re.match(r'^([•·\-\*]|\d+\.)', line)
        is_short = len(line) < 80 and not line.endswith(sentence_endings)
        is_header_pattern = (line.isupper() or re.match(r'^(Chapter|Section|\d+\s+[A-Z])', line, re.IGNORECASE))
        is_header = is_short and (not is_bullet) and is_header_pattern

        if is_header or is_bullet:
            if current_paragraph:
                formatted_blocks.append({"type": "p", "text": current_paragraph})
                current_paragraph = ""
            if is_header:
                formatted_blocks.append({"type": "h", "text": line})
            else:
                formatted_blocks.append({"type": "li", "text": line})
        else:
            if current_paragraph:
                if current_paragraph.endswith("-"):
                    current_paragraph = current_paragraph[:-1] + line
                else:
                    current_paragraph += " " + line
            else:
                current_paragraph = line
    if current_paragraph:
        formatted_blocks.append({"type": "p", "text": current_paragraph})
    return formatted_blocks

# --- セッション初期化 ---
if "last_clicked" not in st.session_state:
    st.session_state.last_clicked = ""
if "history" not in st.session_state:
    st.session_state.history = [] 

# ==========================================
# アプリ画面
# ==========================================
st.title("📚 AI Book Reader (Fixed Layout)")

uploaded_file = st.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    page_num = st.number_input("Page", 1, len(reader.pages), 1)

    # 左右カラム (左:本文 70%, 右:固定スロット 30%)
    col_main, col_side = st.columns([7, 3])

    # --------------------------------------------------------
    # 左側：本文エリア
    # --------------------------------------------------------
    with col_main:
        st.subheader("📄 Reading Area")
        page = reader.pages[page_num - 1]
        blocks = format_text_advanced(page.extract_text())

        html_content = """
        <style>
            .book-container {
                font-family: 'Georgia', serif;
                font-size: 19px;
                line-height: 1.8;
                color: #2c3e50;
                background: #fff;
                padding: 40px;
                border: 1px solid #ddd;
                border-radius: 8px;
            }
            .header-text { font-weight: bold; font-size: 1.3em; margin: 30px 0 15px 0; border-bottom: 2px solid #eee; }
            .list-item { margin-left: 20px; margin-bottom: 8px; border-left: 3px solid #eee; padding-left: 10px; }
            .p-text { margin-bottom: 20px; text-align: justify; }
            .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted transparent; transition: all 0.2s; }
            .w:hover { color: #e67e22; border-bottom: 1px solid #e67e22; background-color: rgba(255, 236, 179, 0.3); }
        </style>
        <div class='book-container'>
        """
        
        word_counter = 0
        for block in blocks:
            b_type = block["type"]
            text = block["text"]
            
            if b_type == "h":
                html_content += f"<div class='header-text'>{html.escape(text)}</div>"
                continue
            elif b_type == "li":
                html_content += "<div class='list-item'>"
            else:
                html_content += "<div class='p-text'>"

            words = text.split()
            for w in words:
                clean_w = w.strip(".,!?\"'()[]{}:;")
                if not clean_w:
                    html_content += w + " "
                    continue
                unique_id = f"{word_counter}_{clean_w}"
                safe_w = html.escape(w)
                html_content += f"<a href='#' id='{unique_id}' class='w'>{safe_w}</a> "
                word_counter += 1
            html_content += "</div>"
        html_content += "</div>"
        
        # クリック検知
        clicked = click_detector(html_content, key="pdf_detector")

    # --------------------------------------------------------
    # 右側：【重要】固定スロット方式（Pre-allocated Boxes）
    # --------------------------------------------------------
    with col_side:
        st.subheader("Dictionary Slots 🗃️")
        
        # 1. 履歴クリアボタン
        if st.button("Clear Slots"):
            st.session_state.history = []
            st.rerun()

        # 2. ここがポイント：先に「10個の空き地」を確保する
        slots = []
        for i in range(10):
            # empty()で場所だけ確保。中身はあとで入れる。
            slots.append(st.empty())

        # 3. 履歴があれば、上から順にスロットを埋める
        # (履歴が10個を超えたら、新しい順に10個だけ表示)
        current_history = st.session_state.history[:10]
        
        for i, item in enumerate(current_history):
            word = item['word']
            info = item['info']
            
            # 確保した場所(slots[i])にHTMLを流し込む
            slots[i].markdown(f"""
            <div style="
                border-left: 5px solid #66bb6a;
                background-color: #fff;
                padding: 10px;
                margin-bottom: 10px;
                border-radius: 5px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            ">
                <div style="font-weight:bold; color:#2e7d32; font-size:1.1em;">{word}</div>
                <div style="font-size:0.8em; margin: 3px 0;">
                    <span style="background:#e8f5e9; color:#2e7d32; padding:2px 6px; border-radius:4px;">{info.get('pos')}</span>
                </div>
                <div style="font-weight:bold; font-size:0.95em;">{info.get('meaning')}</div>
                <div style="font-size:0.8em; color:#666;">{info.get('details')}</div>
            </div>
            """, unsafe_allow_html=True)

    # --------------------------------------------------------
    # クリック時の処理（再描画）
    # --------------------------------------------------------
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        target_word = clicked.split("_", 1)[1]
        
        # 翻訳実行
        result = translate_word_with_gpt(target_word)
        
        # 履歴の先頭に追加
        st.session_state.history.insert(0, {"word": target_word, "info": result})
        
        # シート保存
        client = get_gspread_client()
        if client:
            try:
                sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                today = datetime.now().strftime("%Y-%m-%d")
                sheet.append_row([target_word, result["meaning"], today])
            except: pass
        
        st.rerun()

else:
    st.info("👈 Upload PDF")
