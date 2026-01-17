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

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="AI Book Reader")

# --- 設定: Google連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

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
        return {"meaning": "Error", "pos": "-", "details": "Could not translate."}

# --- 📖 高度なテキスト整形ロジック ---
def format_text_advanced(text):
    if not text: return []
    
    lines = text.splitlines()
    formatted_blocks = []
    current_paragraph = ""
    
    # 見出しとみなさない末尾の文字（これらで終わる行は文章の一部とみなす）
    sentence_endings = ('.', ',', '!', '?', ':', ';', '"', "'", '”', '’', ')', ']')

    for line in lines:
        line = line.strip()
        if not line: continue
        
        # --- 判定ロジック ---
        # 1. 箇条書き判定 (•, -, *, 数字.)
        is_bullet = re.match(r'^([•·\-\*]|\d+\.)', line)
        
        # 2. 見出し判定 (厳しめに設定)
        # - 60文字以下
        # - 文末記号で終わっていない
        # - 箇条書きではない
        # - (追加) 大文字で始まっている、または数字で始まっている
        is_header = (len(line) < 60) and \
                    (not line.endswith(sentence_endings)) and \
                    (not is_bullet) and \
                    (line[0].isupper() or line[0].isdigit() or line.startswith("Chapter"))

        if is_header or is_bullet:
            # 今までの段落を吐き出す
            if current_paragraph:
                formatted_blocks.append({"type": "p", "text": current_paragraph})
                current_paragraph = ""
            
            # 今回の行を追加
            if is_header:
                formatted_blocks.append({"type": "h", "text": line})
            else:
                formatted_blocks.append({"type": "li", "text": line}) # List Item
        else:
            # 文章をつなげる処理
            if current_paragraph:
                # ハイフンで終わる場合はつなげる (ex- \n ample -> example)
                if current_paragraph.endswith("-"):
                    current_paragraph = current_paragraph[:-1] + line
                else:
                    current_paragraph += " " + line
            else:
                current_paragraph = line
    
    # 残った段落を追加
    if current_paragraph:
        formatted_blocks.append({"type": "p", "text": current_paragraph})
            
    return formatted_blocks

# --- セッション初期化 ---
if "last_clicked" not in st.session_state:
    st.session_state.last_clicked = ""
# 履歴リストを作成（新しい順に保存）
if "history" not in st.session_state:
    st.session_state.history = [] 

# ==========================================
# アプリ画面作成
# ==========================================
st.title("📚 AI Book Reader")

uploaded_file = st.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    total_pages = len(reader.pages)
    
    page_num = st.number_input("Page", 1, total_pages, 1)

    # 左右カラム作成 (左: 2.5, 右: 1 の比率)
    col_main, col_side = st.columns([2.5, 1])

    # --- 左側: 本文エリア ---
    with col_main:
        st.subheader("📄 Reading Area")
        
        page = reader.pages[page_num - 1]
        raw_text = page.extract_text()
        blocks = format_text_advanced(raw_text)

        # CSS定義（本のような見た目 + 箇条書き対応）
        html_content = """
        <style>
            .book-container {
                font-family: 'Georgia', 'Times New Roman', serif;
                font-size: 19px;
                line-height: 1.8;
                color: #2c3e50;
                background: #fff;
                padding: 40px;
                border: 1px solid #ddd;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            }
            .header-text {
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: bold;
                font-size: 1.4em;
                margin-top: 30px;
                margin-bottom: 15px;
                color: #000;
                border-bottom: 2px solid #eee;
                padding-bottom: 5px;
            }
            .list-item {
                margin-left: 20px;
                margin-bottom: 10px;
                display: block;
            }
            .p-text {
                margin-bottom: 20px;
                text-align: justify;
            }
            .w { 
                text-decoration: none; 
                color: #2c3e50; 
                cursor: pointer; 
                transition: background 0.1s;
            }
            .w:hover { 
                background-color: #fff59d; 
                border-radius: 2px;
                color: #000;
            }
        </style>
        <div class='book-container'>
        """
        
        word_counter = 0
        
        for block in blocks:
            text = block["text"]
            b_type = block["type"]
            
            # HTMLタグの開始
            if b_type == "h":
                html_content += f"<div class='header-text'>{html.escape(text)}</div>"
                continue # 見出し内の単語はクリック不可にする（誤タップ防止）
            elif b_type == "li":
                html_content += "<div class='list-item'>• "
            else:
                html_content += "<div class='p-text'>"

            # 単語ごとのリンク生成
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
            
            # HTMLタグの終了
            html_content += "</div>"
        
        html_content += "</div>"
        
        # クリック検知
        clicked = click_detector(html_content)
        
        if clicked and clicked != st.session_state.last_clicked:
            st.session_state.last_clicked = clicked
            
            target_word = clicked.split("_", 1)[1]
            st.toast(f"Searching: {target_word}", icon="🔍")
            
            # 翻訳して履歴の先頭に追加
            result = translate_word_with_gpt(target_word)
            timestamp = datetime.now().strftime("%H:%M")
            
            new_entry = {
                "word": target_word,
                "info": result,
                "time": timestamp
            }
            st.session_state.history.insert(0, new_entry) # リストの先頭に追加
            
            # シート保存
            try:
                client = get_gspread_client()
                sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                today = datetime.now().strftime("%Y-%m-%d")
                sheet.append_row([target_word, result["meaning"], today])
            except Exception:
                pass
            
            st.rerun()

    # --- 右側: 履歴表示エリア（コンパクト化） ---
    with col_side:
        st.subheader("History ⏳")
        
        # 履歴削除ボタン
        if st.button("Clear History", use_container_width=True):
            st.session_state.history = []
            st.rerun()

        history = st.session_state.history
        if history:
            for item in history:
                info = item["info"]
                # コンパクトなカードデザイン
                st.markdown(f"""
                <div style="
                    border-left: 4px solid #4CAF50;
                    background-color: #f9f9f9;
                    padding: 10px 12px;
                    margin-bottom: 10px;
                    border-radius: 4px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                ">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:bold; color:#2e7d32; font-size:1.1em;">{item['word']}</span>
                        <span style="font-size:0.7em; color:#999;">{item['time']}</span>
                    </div>
                    <div style="font-size:0.85em; color:#555; margin-top:2px;">
                        <span style="background:#e8f5e9; padding:1px 4px; border-radius:3px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-weight:bold; margin-top:5px; font-size:0.95em;">{info.get('meaning')}</div>
                    <div style="font-size:0.8em; color:#666; margin-top:2px; line-height:1.2;">{info.get('details')}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Tap words to translate.")

else:
    st.info("👈 Please upload a PDF file.")
