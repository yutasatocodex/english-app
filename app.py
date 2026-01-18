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

# --- ページ設定 (サイドバーなし・ワイド) ---
st.set_page_config(layout="wide", page_title="AI Book Reader", initial_sidebar_state="collapsed")

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

# --- テキスト解析 ---
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

def group_blocks_into_screens(blocks, words_per_screen=500):
    # 画面が広くなった分、文字数も増やす (450 -> 500)
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
if "slots" not in st.session_state:
    st.session_state.slots = [None] * 7
else:
    if len(st.session_state.slots) != 7:
        st.session_state.slots = [None] * 7
if "reader_mode" not in st.session_state:
    st.session_state.reader_mode = False
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0

# --- CSS: 余白削除・全画面化 (最強設定) ---
st.markdown("""
<style>
    /* 1. Streamlit標準の余白を強制削除 */
    .block-container {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
        max-width: 100% !important;
    }
    /* ヘッダー等を消す */
    header, footer, #MainMenu {display: none !important;}
    
    /* 2. ボタンを極限まで小さく */
    .stButton button {
        height: 1.8em;
        line-height: 1;
        padding: 0 5px;
        min-height: 0px;
        border: 1px solid #ccc;
    }
    
    /* 3. 全体背景を白に */
    .stApp {
        background-color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. 初期画面
# ==========================================
if not st.session_state.reader_mode:
    st.markdown("### 📚 AI Book Reader")
    uploaded_file = st.file_uploader("Upload PDF", type="pdf")
    if uploaded_file is not None:
        reader = PdfReader(uploaded_file)
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"
        structured_blocks = parse_pdf_to_structured_blocks(full_text)
        st.session_state.all_screens = group_blocks_into_screens(structured_blocks, words_per_screen=500)
        st.session_state.current_screen_index = 0
        st.session_state.reader_mode = True
        st.rerun()

# ==========================================
# 2. 読書画面 (超・全画面モード)
# ==========================================
else:
    # 上部ナビゲーション (高さ最小)
    # 左右比率 4.5 : 1 に合わせるため、ここも調整
    nav_left, nav_right = st.columns([4.5, 1])
    
    with nav_left:
        c1, c2, c3, c4 = st.columns([0.5, 0.5, 6, 0.5])
        with c1:
            if st.button("◀", key="prev"):
                if st.session_state.current_screen_index > 0:
                    st.session_state.current_screen_index -= 1
                    st.rerun()
        with c2:
            if st.button("▶", key="next"):
                if st.session_state.current_screen_index < len(st.session_state.all_screens) - 1:
                    st.session_state.current_screen_index += 1
                    st.rerun()
        with c3:
            curr = st.session_state.current_screen_index + 1
            total = len(st.session_state.all_screens)
            st.markdown(f"<span style='color:#999; font-size:0.8em; margin-left:10px;'>Page {curr}/{total}</span>", unsafe_allow_html=True)
        with c4:
             if st.button("✕", key="close"):
                st.session_state.reader_mode = False
                st.session_state.slots = [None] * 7
                st.rerun()

    # メインコンテンツ (左右比率 4.5 : 1 で読書エリア最大化)
    col_read, col_dict = st.columns([4.5, 1])

    # --- 左: 読書エリア ---
    with col_read:
        if st.session_state.all_screens:
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            
            html_content = """
            <style>
                .book-container {
                    background-color: #fff;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 30px 40px; /* 横の余白は確保 */
                    font-family: 'Georgia', serif;
                    font-size: 19px;     
                    line-height: 1.7;    
                    color: #2c3e50;
                    height: 92vh; /* ★画面の92%まで広げる★ */
                    overflow-y: auto;
                }
                .header-text { font-weight: bold; font-size: 1.4em; margin: 10px 0 15px 0; border-bottom: 2px solid #f0f0f0; }
                .list-item { margin-left: 20px; margin-bottom: 5px; border-left: 3px solid #eee; padding-left: 10px; }
                .p-text { margin-bottom: 20px; text-align: justify; }
                
                .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted #ccc; }
                .w:hover { color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; }
                
                @media only screen and (max-width: 768px) {
                    .book-container {
                        height: 92vh !important;
                        padding: 15px !important;
                        font-size: 16px !important;
                    }
                }
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

    # --- 右: 辞書リスト ---
    with col_dict:
        # 92vhの中に7個を均等配置 (約13vh/個)
        # 隙間(margin-bottom)を考慮して調整
        
        for i in range(7):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            if slot_data is None:
                st.markdown(f"""
                <div style="
                    height: 12.8vh; /* 高さを最大活用 */
                    margin-bottom: 0.5vh;
                    border: 1px dashed #f0f0f0;
                    border-radius: 4px;
                "></div>
                """, unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                pron = info.get('pronunciation', '')
                
                st.markdown(f"""
                <div style="
                    height: 12.8vh; /* 高さを最大活用 */
                    border-left: 4px solid #2980b9;
                    background-color: #f8fbff;
                    padding: 6px 8px;
                    margin-bottom: 0.5vh;
                    border-radius: 4px;
                    overflow: hidden;
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:2px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:0.9em; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; max-width:85%;">{chunk}</span>
                        <span style="font-size:0.6em; color:#1a5276; background:#e1eff7; padding:1px 3px; border-radius:3px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-size:0.7em; color:#777; margin-bottom:2px;">{pron}</div>
                    <div style="font-weight:bold; font-size:0.8em; color:#333; line-height:1.25; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow:hidden;">{info.get('meaning')}</div>
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
            curr.pop()
            curr.insert(0, {"chunk": result["chunk"], "info": result})
            st.session_state.slots = curr[:7] + [None] * (7 - len(curr))
            
            client = get_gspread_client()
            if client:
                try:
                    sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                    meaning_full = f"{result['meaning']} ({result['pos']})"
                    sheet.append_row([result['chunk'], result.get('pronunciation', ''), meaning_full, context_sentence[:300]+"..." ])
                except: pass
            st.rerun()
