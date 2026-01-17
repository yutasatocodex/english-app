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
        return {"meaning": "Translation Error", "pos": "-", "details": "Please try again."}

# --- 📖 高度なテキスト整形ロジック (改良版) ---
def format_text_advanced(text):
    if not text: return []
    
    lines = text.splitlines()
    formatted_blocks = []
    current_paragraph = ""
    
    # 文末記号（これらで終わる行は見出しではない確率が高い）
    sentence_endings = ('.', ',', '!', '?', ':', ';', '"', "'", '”', '’', ')', ']')

    for line in lines:
        line = line.strip()
        if not line: continue
        
        # --- 判定ロジック ---
        # 1. 箇条書き判定 (•, -, *, 数字.)
        is_bullet = re.match(r'^([•·\-\*]|\d+\.)', line)
        
        # 2. 見出し判定 (誤爆を防ぐため厳格化)
        # 条件:
        # A. 80文字未満
        # B. 文末記号で終わっていない
        # C. 箇条書きではない
        # D. 「すべて大文字」 または 「Chapter/数字で始まる」 または 「タイトルっぽい単語(Introductionなど)」
        
        is_short_and_no_punct = (len(line) < 80) and (not line.endswith(sentence_endings))
        
        is_header_pattern = (
            line.isupper() or  # 全部大文字 (INTRODUCTION など)
            re.match(r'^(Chapter|Section|Part|\d+\s+[A-Z])', line, re.IGNORECASE) or # Chapter 1 など
            re.match(r'^\d+$', line) # ページ番号など単独の数字
        )

        is_header = is_short_and_no_punct and (not is_bullet) and is_header_pattern

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
                # ハイフン行末の処理 (ex- \n ample -> example)
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
# 履歴リスト（{word, info, time} の辞書を格納）
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

    # 左右カラム作成 (左:本文 70%, 右:履歴 30%)
    col_main, col_side = st.columns([7, 3])

    # --- 左側: 本文エリア ---
    with col_main:
        st.subheader("📄 Reading Area")
        
        page = reader.pages[page_num - 1]
        raw_text = page.extract_text()
        blocks = format_text_advanced(raw_text)

        # CSS定義（スクロール戻りを防ぐため、クリックしてもスタイルを変えない）
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
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            }
            .header-text {
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: bold;
                font-size: 1.3em;
                margin-top: 30px;
                margin-bottom: 15px;
                color: #000;
                border-bottom: 2px solid #eee;
                padding-bottom: 5px;
            }
            .list-item {
                margin-left: 20px;
                margin-bottom: 8px;
                padding-left: 10px;
                border-left: 3px solid #eee;
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
                border-bottom: 1px dotted transparent;
                transition: all 0.2s;
            }
            .w:hover { 
                color: #e67e22;
                border-bottom: 1px solid #e67e22;
                background-color: rgba(255, 236, 179, 0.3);
            }
        </style>
        <div class='book-container'>
        """
        
        word_counter = 0
        
        for block in blocks:
            text = block["text"]
            b_type = block["type"]
            
            # HTML構造の組み立て
            if b_type == "h":
                html_content += f"<div class='header-text'>{html.escape(text)}</div>"
                continue # 見出しはクリック対象外
            elif b_type == "li":
                html_content += "<div class='list-item'>"
            else:
                html_content += "<div class='p-text'>"

            # 単語ごとのリンク生成
            words = text.split()
            for w in words:
                clean_w = w.strip(".,!?\"'()[]{}:;")
                if not clean_w:
                    html_content += w + " "
                    continue
                
                # ID生成 (連番_単語)
                unique_id = f"{word_counter}_{clean_w}"
                safe_w = html.escape(w)
                html_content += f"<a href='#' id='{unique_id}' class='w'>{safe_w}</a> "
                word_counter += 1
            
            html_content += "</div>"
        
        html_content += "</div>"
        
        # クリック検知 (keyを固定することで再描画時の安定性を高める)
        clicked = click_detector(html_content, key="pdf_text_detector")
        
        # --- クリック時の処理 ---
        if clicked and clicked != st.session_state.last_clicked:
            st.session_state.last_clicked = clicked
            
            target_word = clicked.split("_", 1)[1]
            
            # 翻訳実行
            result = translate_word_with_gpt(target_word)
            timestamp = datetime.now().strftime("%H:%M")
            
            # 履歴の先頭に追加 (スタック形式)
            new_entry = {
                "word": target_word,
                "info": result,
                "time": timestamp
            }
            st.session_state.history.insert(0, new_entry) 
            
            # シート保存 (エラーが出ても止まらないようにする)
            try:
                client = get_gspread_client()
                sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                today = datetime.now().strftime("%Y-%m-%d")
                sheet.append_row([target_word, result["meaning"], today])
            except Exception:
                pass
            
            st.rerun()

    # --- 右側: 履歴表示エリア (タイムライン) ---
    with col_side:
        st.subheader("History ⏳")
        
        if st.button("Clear History", use_container_width=True):
            st.session_state.history = []
            st.rerun()

        history = st.session_state.history
        if history:
            for item in history:
                info = item["info"]
                word = item['word']
                
                # カードデザイン (コンパクトで見やすく)
                st.markdown(f"""
                <div style="
                    border-left: 5px solid #66bb6a;
                    background-color: #fff;
                    padding: 12px;
                    margin-bottom: 12px;
                    border-radius: 6px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.08);
                    animation: fadeIn 0.5s;
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <span style="font-weight:bold; color:#2e7d32; font-size:1.1em;">{word}</span>
                        <span style="font-size:0.7em; color:#aaa;">{item['time']}</span>
                    </div>
                    <div style="font-size:0.8em; margin-top:4px; margin-bottom:4px;">
                        <span style="background:#e8f5e9; color:#2e7d32; padding:2px 6px; border-radius:4px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-weight:bold; font-size:0.95em; color:#333;">{info.get('meaning')}</div>
                    <div style="font-size:0.8em; color:#666; margin-top:4px; line-height:1.3;">{info.get('details')}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("Tap a word to translate.")

else:
    st.info("👈 Please upload a PDF file.")
