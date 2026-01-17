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

# --- ページ設定（画面いっぱいに使う） ---
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
# 履歴スロット（5つ固定）をNoneで初期化
if "slots" not in st.session_state:
    st.session_state.slots = [None] * 5

# ==========================================
# アプリ画面 (CSSでスクロール制御)
# ==========================================
st.title("📚 AI Book Reader")

# スクロール位置を保持するためのJavaScript
# (id="scrollable-container" の位置を記憶し、リロード後に復元する)
st.markdown("""
<script>
    const scrollBox = window.parent.document.getElementById('scrollable-container');
    if (scrollBox) {
        // 保存された位置があれば復元
        const savedPos = sessionStorage.getItem('scrollPos');
        if (savedPos) scrollBox.scrollTop = savedPos;

        // スクロールするたびに位置を保存
        scrollBox.onscroll = function() {
            sessionStorage.setItem('scrollPos', scrollBox.scrollTop);
        };
    }
</script>
""", unsafe_allow_html=True)

uploaded_file = st.sidebar.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    # ページ選択はサイドバーへ移動（メイン画面を広く使うため）
    page_num = st.sidebar.number_input("Page", 1, len(reader.pages), 1)

    # 左右カラム (左:本文 70%, 右:固定スロット 30%)
    col_main, col_side = st.columns([7, 3])

    # --------------------------------------------------------
    # 左側：本文エリア (高さ固定・スクロールあり)
    # --------------------------------------------------------
    with col_main:
        st.subheader("📄 Reading Area")
        page = reader.pages[page_num - 1]
        blocks = format_text_advanced(page.extract_text())

        # HTML生成
        # id='scrollable-container' を付与してJSで制御
        html_content = """
        <style>
            #scrollable-container {
                height: 75vh; /* 画面の75%の高さを固定 */
                overflow-y: auto; /* 縦スクロール有効 */
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 30px;
                background-color: #fff;
                font-family: 'Georgia', serif;
                font-size: 19px;
                line-height: 1.8;
                color: #2c3e50;
            }
            .header-text { font-weight: bold; font-size: 1.3em; margin: 30px 0 15px 0; border-bottom: 2px solid #eee; }
            .list-item { margin-left: 20px; margin-bottom: 8px; border-left: 3px solid #eee; padding-left: 10px; }
            .p-text { margin-bottom: 20px; text-align: justify; }
            .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted transparent; transition: all 0.2s; }
            .w:hover { color: #e67e22; border-bottom: 1px solid #e67e22; background-color: #fff9c4; }
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

    # --------------------------------------------------------
    # 右側：【修正版】見える固定スロット (Visible Fixed Slots)
    # --------------------------------------------------------
    with col_side:
        st.subheader("Dictionary 🗃️")
        
        # クリアボタン
        if st.button("Reset Slots", use_container_width=True):
            st.session_state.slots = [None] * 5
            st.rerun()

        # 5つのスロットを描画
        for i in range(5):
            slot_data = st.session_state.slots[i]
            
            if slot_data is None:
                # データがない時：グレーの点線ボックスを表示（場所確保）
                st.markdown(f"""
                <div style="
                    height: 120px;
                    border: 2px dashed #ccc;
                    border-radius: 8px;
                    margin-bottom: 15px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: #aaa;
                    font-size: 0.9em;
                ">
                    Empty Slot {i+1}
                </div>
                """, unsafe_allow_html=True)
            else:
                # データがある時：翻訳カードを表示
                word = slot_data['word']
                info = slot_data['info']
                st.markdown(f"""
                <div style="
                    height: 120px; /* 高さ固定でガタつき防止 */
                    border-left: 5px solid #66bb6a;
                    background-color: #fff;
                    padding: 10px;
                    margin-bottom: 15px;
                    border-radius: 8px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                    overflow: hidden; /* はみ出し防止 */
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <span style="font-weight:bold; color:#2e7d32; font-size:1.1em;">{word}</span>
                        <span style="background:#e8f5e9; color:#2e7d32; padding:2px 6px; border-radius:4px; font-size:0.7em;">{info.get('pos')}</span>
                    </div>
                    <div style="font-weight:bold; font-size:0.9em; margin-top:5px; color:#333;">{info.get('meaning')}</div>
                    <div style="font-size:0.8em; color:#666; margin-top:3px; line-height:1.2;">{info.get('details')}</div>
                </div>
                """, unsafe_allow_html=True)

    # --------------------------------------------------------
    # クリック時の処理
    # --------------------------------------------------------
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        target_word = clicked.split("_", 1)[1]
        
        # 翻訳実行
        result = translate_word_with_gpt(target_word)
        
        # ロジック：新しい単語を一番上(0)に入れ、古いものを押し出す
        # [A, B, C, D, E] -> [New, A, B, C, D]
        current_slots = st.session_state.slots
        current_slots.pop() # 最後の要素を削除
        current_slots.insert(0, {"word": target_word, "info": result}) # 先頭に追加
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
    st.info("👈 Please upload a PDF file from the sidebar.")
