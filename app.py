import streamlit as st
import streamlit.components.v1 as components # JS実行用
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
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except:
        return None

# --- 設定: OpenAI翻訳 ---
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
        return {"meaning": "Error", "pos": "-", "details": "Try again."}

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
if "slots" not in st.session_state:
    st.session_state.slots = [None] * 5

# ==========================================
# アプリ画面
# ==========================================
st.title("📚 AI Book Reader")

# サイドバー設定
uploaded_file = st.sidebar.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    page_num = st.sidebar.number_input("Page", 1, len(reader.pages), 1)

    # ★スクロール位置を復元する魔法（JavaScript）★
    # これが毎回実行され、前回の位置まで自動でスクロールします
    components.html("""
    <script>
        const scrollBox = window.parent.document.getElementById('scrollable-container');
        if (scrollBox) {
            // 保存された位置があれば復元
            const savedPos = sessionStorage.getItem('scrollPos');
            if (savedPos) {
                scrollBox.scrollTop = savedPos;
            }
            // スクロールするたびに位置を保存
            scrollBox.onscroll = function() {
                sessionStorage.setItem('scrollPos', scrollBox.scrollTop);
            };
        }
    </script>
    """, height=0)

    # 画面分割
    col_main, col_side = st.columns([7, 3])

    # --- 左側：読書エリア ---
    with col_main:
        page = reader.pages[page_num - 1]
        blocks = format_text_advanced(page.extract_text())

        # HTML生成（高さを800pxに固定）
        html_content = """
        <style>
            #scrollable-container {
                height: 800px; /* ★ここを巨大にしました★ */
                overflow-y: auto;
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 40px;
                background-color: #fff;
                font-family: 'Georgia', serif;
                font-size: 20px; /* 文字も少し大きく */
                line-height: 1.9;
                color: #2c3e50;
                box-shadow: inset 0 0 10px rgba(0,0,0,0.05);
            }
            .header-text { font-weight: bold; font-size: 1.4em; margin: 40px 0 20px 0; border-bottom: 2px solid #eee; color:#000; }
            .list-item { margin-left: 20px; margin-bottom: 10px; border-left: 4px solid #eee; padding-left: 15px; }
            .p-text { margin-bottom: 25px; text-align: justify; }
            .w { 
                text-decoration: none; color: #2c3e50; cursor: pointer; 
                border-bottom: 1px dotted #ccc; transition: all 0.1s; 
            }
            .w:hover { 
                color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; 
            }
        </style>
        
        <div id='scrollable-container'>
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
        
        html_content += "</div>" # Close container
        
        # クリック検知
        clicked = click_detector(html_content, key="pdf_detector")

    # --- 右側：辞書スロット ---
    with col_side:
        st.subheader("Dictionary 🗃️")
        
        if st.button("Reset Slots", use_container_width=True):
            st.session_state.slots = [None] * 5
            st.rerun()

        for i in range(5):
            slot_data = st.session_state.slots[i]
            
            if slot_data is None:
                st.markdown(f"""
                <div style="
                    height: 130px;
                    border: 2px dashed #ddd;
                    border-radius: 8px;
                    margin-bottom: 15px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: #bbb;
                ">Slot {i+1}</div>
                """, unsafe_allow_html=True)
            else:
                word = slot_data['word']
                info = slot_data['info']
                st.markdown(f"""
                <div style="
                    height: 130px;
                    border-left: 6px solid #66bb6a;
                    background-color: #fff;
                    padding: 12px;
                    margin-bottom: 15px;
                    border-radius: 8px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
                    overflow-y: auto;
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <span style="font-weight:bold; color:#2e7d32; font-size:1.2em;">{word}</span>
                        <span style="background:#e8f5e9; color:#2e7d32; padding:2px 6px; border-radius:4px; font-size:0.8em;">{info.get('pos')}</span>
                    </div>
                    <div style="font-weight:bold; font-size:1.0em; margin-top:6px; color:#333;">{info.get('meaning')}</div>
                    <div style="font-size:0.85em; color:#666; margin-top:4px; line-height:1.3;">{info.get('details')}</div>
                </div>
                """, unsafe_allow_html=True)

    # --- クリック時の処理 ---
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        target_word = clicked.split("_", 1)[1]
        
        # 翻訳実行
        result = translate_word_with_gpt(target_word)
        
        # スロット更新 (新しいものを上に)
        current_slots = st.session_state.slots
        current_slots.pop()
        current_slots.insert(0, {"word": target_word, "info": result})
        st.session_state.slots = current_slots
        
        # スプレッドシート保存
        client = get_gspread_client()
        if client:
            try:
                sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                today = datetime.now().strftime("%Y-%m-%d")
                sheet.append_row([target_word, result["meaning"], today])
            except: pass
        
        st.rerun()

else:
    st.info("👈 Upload PDF from the sidebar.")
