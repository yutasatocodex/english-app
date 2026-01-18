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

# --- ページ設定 (サイドバーを隠し、ワイドモード) ---
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

def group_blocks_into_screens(blocks, words_per_screen=450):
    # 1画面の文字数を増やして、広い画面を埋める
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
    st.session_state.slots = [None] * 7 # 7個限定
else:
    if len(st.session_state.slots) != 7:
        st.session_state.slots = [None] * 7

if "reader_mode" not in st.session_state:
    st.session_state.reader_mode = False
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0

# --- CSS: 全画面活用設定 (重要) ---
st.markdown("""
<style>
    /* 1. 全体の余白を削除 */
    .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 0rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    /* 2. ヘッダー・フッター削除 */
    header {display: none !important;}
    footer {display: none !important;}
    #MainMenu {display: none !important;}
    
    /* 3. ボタンのデザイン */
    .stButton button {
        height: 2.2em;
        line-height: 1;
        padding: 0 10px;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. 初期画面 (ファイル選択)
# ==========================================
if not st.session_state.reader_mode:
    st.markdown("## 📚 AI Book Reader")
    st.markdown("Select a PDF file to enter Reading Mode.")
    uploaded_file = st.file_uploader("Upload PDF", type="pdf", label_visibility="collapsed")
    
    if uploaded_file is not None:
        reader = PdfReader(uploaded_file)
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() + "\n"
        
        structured_blocks = parse_pdf_to_structured_blocks(full_text)
        # 画面サイズに合わせて単語数を450に設定
        st.session_state.all_screens = group_blocks_into_screens(structured_blocks, words_per_screen=450)
        st.session_state.current_screen_index = 0
        st.session_state.reader_mode = True
        st.rerun()

# ==========================================
# 2. 読書モード (全画面固定レイアウト)
# ==========================================
else:
    # ナビゲーション行 (最小限)
    nav_c1, nav_c2, nav_c3, nav_c4 = st.columns([0.5, 8, 0.5, 0.5])
    with nav_c1:
        if st.button("◀", key="prev"):
            if st.session_state.current_screen_index > 0:
                st.session_state.current_screen_index -= 1
                st.rerun()
    with nav_c2:
        curr = st.session_state.current_screen_index + 1
        total = len(st.session_state.all_screens)
        # プログレスバー風の表示
        st.markdown(f"<div style='text-align:center; color:#888; font-size:0.8em; padding-top:5px;'>Page {curr} / {total}</div>", unsafe_allow_html=True)
    with nav_c3:
        if st.button("▶", key="next"):
            if st.session_state.current_screen_index < len(st.session_state.all_screens) - 1:
                st.session_state.current_screen_index += 1
                st.rerun()
    with nav_c4:
        if st.button("✕"):
            st.session_state.reader_mode = False
            st.session_state.slots = [None] * 7
            st.rerun()

    # メインコンテンツ (左右分割)
    col_read, col_dict = st.columns([3, 1])

    # --- 左: 読書エリア (高さ85vh強制) ---
    with col_read:
        if st.session_state.all_screens:
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            
            html_content = """
            <style>
                .book-container {
                    background-color: #fff;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    padding: 40px;
                    font-family: 'Georgia', serif;
                    font-size: 19px;     
                    line-height: 1.7;    
                    color: #2c3e50;
                    height: 85vh; /* ★ここが重要：画面の85%の高さを確保★ */
                    overflow-y: auto;
                }
                .header-text { font-weight: bold; font-size: 1.4em; margin: 10px 0 15px 0; border-bottom: 2px solid #f0f0f0; }
                .list-item { margin-left: 20px; margin-bottom: 5px; border-left: 3px solid #eee; padding-left: 10px; }
                .p-text { margin-bottom: 20px; text-align: justify; }
                
                .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted #ccc; }
                .w:hover { color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; }
                
                /* スマホ対応 */
                @media only screen and (max-width: 768px) {
                    .book-container {
                        height: 85vh !important;
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

    # --- 右: 辞書リスト (7個固定・高さ合わせ) ---
    with col_dict:
        # 85vhの中に7個を均等に配置する計算 (約11.5vh/個)
        
        for i in range(7):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            if slot_data is None:
                # 空スロット（透明にして場所だけ確保 = レイアウト崩れ防止）
                st.markdown(f"""
                <div style="
                    height: 11.5vh;
                    margin-bottom: 0.7vh;
                    border: 1px dashed #f0f0f0; /* 薄い線 */
                    border-radius: 6px;
                "></div>
                """, unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                pron = info.get('pronunciation', '')
                
                st.markdown(f"""
                <div style="
                    height: 11.5vh;
                    border-left: 5px solid #2980b9;
                    background-color: #f8fbff;
                    padding: 8px;
                    margin-bottom: 0.7vh; /* 隙間 */
                    border-radius: 6px;
                    overflow: hidden;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:3px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:1.0em; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; max-width:85%;">{chunk}</span>
                        <span style="font-size:0.65em; color:#1a5276; background:#e1eff7; padding:1px 3px; border-radius:3px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-size:0.75em; color:#777; margin-bottom:3px;">{pron}</div>
                    <div style="font-weight:bold; font-size:0.85em; color:#333; line-height:1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow:hidden;">{info.get('meaning')}</div>
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
            curr.pop() # 古いものを削除
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
