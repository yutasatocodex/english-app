import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from st_click_detector import click_detector
import html
import re
import json
from openai import OpenAI

# --- ページ設定 (余白を削る) ---
st.set_page_config(layout="wide", page_title="AI Book Reader", initial_sidebar_state="collapsed")

# --- CSS: 全体の余白を極限まで削り、1画面に収める ---
st.markdown("""
<style>
    /* ページ全体の余白削除 */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    /* ヘッダー隠し */
    header {visibility: hidden;}
    /* フッター隠し */
    footer {visibility: hidden;}
    
    /* ボタンのスタイル */
    .stButton button {
        height: 2em;
        padding: 0.2em 0.5em;
        font-size: 0.9em;
    }
</style>
""", unsafe_allow_html=True)

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

# --- 設定: OpenAI ---
def analyze_chunk_with_gpt(target_word, context_sentence):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    prompt = f"""
    You are an expert English teacher.
    The user is reading this text: "{context_sentence}"
    The user clicked the word: "{target_word}"

    Your task:
    1. Identify the meaningful "chunk".
    2. Provide IPA pronunciation.
    3. Provide Japanese meaning (Concise).

    Output MUST be a JSON object:
    1. "chunk": English phrase.
    2. "pronunciation": IPA.
    3. "meaning": Japanese meaning.
    4. "pos": Part of Speech.
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
        return {"chunk": target_word, "pronunciation": "", "meaning": "Error", "pos": "-"}

# --- テキスト構造解析 ---
def parse_pdf_to_structured_blocks(text):
    if not text: return []
    lines = text.splitlines()
    blocks = []
    current_text = ""
    current_type = "p"
    sentence_endings = ('.', ',', '!', '?', ':', ';', '"', "'", '”', '’', ')', ']')

    for line in lines:
        line = line.strip()
        if not line: continue
        is_bullet = re.match(r'^([•·\-\*]|\d+\.)', line)
        is_header = line.isupper() or re.match(r'^(Chapter|Section|\d+\s+[A-Z])', line, re.IGNORECASE)

        if is_header or is_bullet:
            if current_text:
                blocks.append({"type": current_type, "text": current_text})
                current_text = ""
            if is_header:
                blocks.append({"type": "h", "text": line})
            else:
                blocks.append({"type": "li", "text": line})
        else:
            if current_text:
                if current_text.endswith("-"):
                    current_text = current_text[:-1] + line
                else:
                    current_text += " " + line
            else:
                current_text = line
                current_type = "p"
    if current_text:
        blocks.append({"type": current_type, "text": current_text})
    return blocks

def group_blocks_into_screens(blocks, words_per_screen=380):
    screens = []
    current_screen = []
    current_word_count = 0
    for block in blocks:
        block_word_count = len(block["text"].split())
        if current_word_count + block_word_count > words_per_screen and current_word_count > 100:
            screens.append(current_screen)
            current_screen = []
            current_word_count = 0
        current_screen.append(block)
        current_word_count += block_word_count
    if current_screen:
        screens.append(current_screen)
    return screens

# --- セッション初期化 ---
if "last_clicked" not in st.session_state:
    st.session_state.last_clicked = ""
# スロットを7個に制限
if "slots" not in st.session_state:
    st.session_state.slots = [None] * 7
else:
    # 数が変わった場合の調整 (10 -> 7)
    if len(st.session_state.slots) != 7:
        st.session_state.slots = [None] * 7

if "reader_mode" not in st.session_state:
    st.session_state.reader_mode = False
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0

# ==========================================
# 1. 初期画面 (アップロードのみ)
# ==========================================
if not st.session_state.reader_mode:
    st.title("📚 AI Book Reader")
    uploaded_file = st.file_uploader("Select PDF to Start Reading", type="pdf")
    
    if uploaded_file is not None:
        reader = PdfReader(uploaded_file)
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"
        
        structured_blocks = parse_pdf_to_structured_blocks(full_text)
        # 1画面の分量を調整
        st.session_state.all_screens = group_blocks_into_screens(structured_blocks, words_per_screen=380)
        st.session_state.current_screen_index = 0
        st.session_state.reader_mode = True # 読書モードへ移行
        st.rerun()

# ==========================================
# 2. 読書画面 (アップロード要素なし)
# ==========================================
else:
    # レイアウト: 左(読書) : 右(リスト) = 3 : 1
    col_main, col_side = st.columns([3, 1])

    with col_main:
        # --- 最小限のナビゲーションバー ---
        nav_c1, nav_c2, nav_c3, nav_c4 = st.columns([1, 10, 1, 1])
        with nav_c1:
            if st.button("◀", key="prev"):
                if st.session_state.current_screen_index > 0:
                    st.session_state.current_screen_index -= 1
                    st.rerun()
        with nav_c2:
            # 現在位置の表示 (例: 1 / 25)
            curr = st.session_state.current_screen_index + 1
            total = len(st.session_state.all_screens)
            st.markdown(f"<div style='text-align:center; font-size:0.9em; color:#888; padding-top:5px;'>Page {curr} / {total}</div>", unsafe_allow_html=True)
        with nav_c3:
            if st.button("▶", key="next"):
                if st.session_state.current_screen_index < len(st.session_state.all_screens) - 1:
                    st.session_state.current_screen_index += 1
                    st.rerun()
        with nav_c4:
            # 最初の画面に戻るボタン（小さく）
            if st.button("✕", help="Exit Reader"):
                st.session_state.reader_mode = False
                st.session_state.slots = [None] * 7
                st.rerun()

        # --- 読書エリア ---
        if st.session_state.all_screens:
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            
            # 高さ固定(82vh): 画面の約8割
            html_content = """
            <style>
                .book-container {
                    background-color: #fff;
                    border: 1px solid #eee;
                    border-radius: 8px;
                    padding: 30px;
                    font-family: 'Georgia', serif;
                    font-size: 19px;     
                    line-height: 1.7;    
                    color: #2c3e50;
                    height: 82vh; /* 画面高さに合わせて固定 */
                    overflow-y: auto; /* 文章が長い場合のみ内部スクロール */
                }
                .header-text { font-weight: bold; font-size: 1.3em; margin: 15px 0 10px 0; color:#000; }
                .list-item { margin-left: 15px; margin-bottom: 5px; border-left: 3px solid #eee; padding-left: 10px; }
                .p-text { margin-bottom: 15px; text-align: justify; }
                
                .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted #ccc; }
                .w:hover { color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; }
            </style>
            <div class='book-container'>
            """
            
            word_counter = 0
            for block in current_blocks:
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
                    unique_id = f"wd{word_counter}_{clean_w}"
                    safe_w = html.escape(w)
                    html_content += f"<a href='#' id='{unique_id}' class='w'>{safe_w}</a> "
                    word_counter += 1
                html_content += "</div>"
            html_content += "</div>"
            
            clicked = click_detector(html_content, key=f"det_{st.session_state.current_screen_index}")

    with col_side:
        # --- 単語リストエリア (7個) ---
        # 読書エリアと同じ高さ(82vh)に収まるように計算
        # タイトルとクリアボタン
        st.markdown("<div style='height:35px; display:flex; align-items:center;'><b>Dictionary</b></div>", unsafe_allow_html=True)
        
        # 7個のスロットを表示
        for i in range(7):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            # 1つあたりの高さ: 10vh程度
            if slot_data is None:
                st.markdown(f"""
                <div style="
                    height: 10.5vh;
                    border: 1px dashed #ddd;
                    border-radius: 6px;
                    margin-bottom: 0.8vh;
                    display: flex; align-items: center; justify-content: center;
                    color: #eee; font-size: 0.8em;
                ">Slot {i+1}</div>
                """, unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                pron = info.get('pronunciation', '')
                
                st.markdown(f"""
                <div style="
                    height: 10.5vh;
                    border-left: 4px solid #2980b9;
                    background-color: #f8fbff;
                    padding: 6px 8px;
                    margin-bottom: 0.8vh;
                    border-radius: 6px;
                    overflow: hidden;
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:2px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:0.95em; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; max-width:85%;">{chunk}</span>
                        <span style="font-size:0.6em; color:#1a5276; background:#e1eff7; padding:1px 3px; border-radius:3px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-size:0.75em; color:#777; margin-bottom:2px;">{pron}</div>
                    <div style="font-weight:bold; font-size:0.85em; color:#333; line-height:1.2; overflow:hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;">{info.get('meaning')}</div>
                </div>
                """, unsafe_allow_html=True)

    # --- クリック処理 ---
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        parts = clicked.split("_", 1)
        if len(parts) == 2:
            target_word = parts[1]
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            context_sentence = " ".join([b["text"] for b in current_blocks])
            
            result = analyze_chunk_with_gpt(target_word, context_sentence)
            
            curr = st.session_state.slots
            curr.pop() # 末尾(一番古いもの)を削除
            curr.insert(0, {"chunk": result["chunk"], "info": result})
            st.session_state.slots = curr[:7] + [None] * (7 - len(curr)) # 7個に維持
            
            client = get_gspread_client()
            if client:
                try:
                    sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                    meaning_full = f"{result['meaning']} ({result['pos']})"
                    sheet.append_row([result['chunk'], result.get('pronunciation', ''), meaning_full, context_sentence[:300]+"..." ])
                except: pass
            st.rerun()
