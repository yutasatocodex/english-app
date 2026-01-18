import streamlit as st
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from st_click_detector import click_detector
import html
import re
import json
from openai import OpenAI

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="AI Book Reader", initial_sidebar_state="collapsed")

# --- 設定: Google Sheets連携 ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"Google Auth Error: {e}")
        return None

# --- 設定: OpenAI (分析ロジック) ---
def analyze_chunk_with_gpt(target_word, context_text):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
    # 指示：UIには単語と意味だけ出すが、シート用に例文もしっかり抽出させる
    prompt = f"""
    The user is reading: "{context_text}"
    Target word: "{target_word}"

    Task:
    1. Identify the core word or idiom (Keep it short).
    2. IPA pronunciation.
    3. Japanese meaning (Concise).
    4. Extract the ONE specific sentence containing the target word.

    Output JSON keys: "chunk", "pronunciation", "meaning", "pos", "original_sentence"
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"chunk": target_word, "pronunciation": "", "meaning": "Error", "pos": "-", "original_sentence": ""}

# --- テキスト構造解析 ---
def parse_pdf_to_structured_blocks(text):
    if not text: return []
    lines = text.splitlines()
    blocks = []
    current_text = ""
    current_type = "p"
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
    st.session_state.slots = [None] * 9 # リストを少し増やして一覧性アップ
if "reader_mode" not in st.session_state:
    st.session_state.reader_mode = False
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0

# --- CSS: シンプル & コンパクト ---
st.markdown("""
<style>
    .block-container {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
        max-width: 100% !important;
    }
    header, footer, #MainMenu {display: none !important;}
    .stButton button {
        height: 1.8em; line-height: 1; padding: 0 5px; min-height: 0px; border: 1px solid #ccc;
    }
    .stApp { background-color: #ffffff; }
    
    /* 右側の辞書カードのスタイル（極小・高密度） */
    .dict-card {
        border-left: 4px solid #2980b9;
        background-color: #f8fbff;
        padding: 6px 8px;
        margin-bottom: 4px;
        border-radius: 4px;
        height: auto; /* 高さは中身に合わせる */
        min-height: 60px;
    }
    .dict-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 2px;
    }
    .dict-word {
        font-weight: bold;
        color: #1a5276;
        font-size: 1.0em;
    }
    .dict-pos {
        font-size: 0.7em;
        color: #1a5276;
        background: #e1eff7;
        padding: 1px 4px;
        border-radius: 3px;
    }
    .dict-meaning {
        font-weight: 500;
        font-size: 0.9em;
        color: #333;
        line-height: 1.3;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. 初期画面
# ==========================================
if not st.session_state.reader_mode:
    st.markdown("### 📚 AI Book Reader (Simple)")
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
# 2. 読書画面
# ==========================================
else:
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
                st.session_state.slots = [None] * 9
                st.rerun()

    col_read, col_dict = st.columns([4.5, 1])

    # --- 左: 読書エリア ---
    with col_read:
        if st.session_state.all_screens:
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            html_content = """
            <style>
                .book-container {
                    background-color: #fff; border: 1px solid #ddd; border-radius: 4px;
                    padding: 30px 40px; font-family: 'Georgia', serif; font-size: 19px; line-height: 1.7; color: #2c3e50;
                    height: 92vh; overflow-y: auto;
                }
                .header-text { font-weight: bold; font-size: 1.4em; margin: 10px 0 15px 0; border-bottom: 2px solid #f0f0f0; }
                .list-item { margin-left: 20px; margin-bottom: 5px; border-left: 3px solid #eee; padding-left: 10px; }
                .p-text { margin-bottom: 20px; text-align: justify; }
                .w { text-decoration: none; color: #2c3e50; cursor: pointer; border-bottom: 1px dotted #ccc; }
                .w:hover { color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; }
                @media only screen and (max-width: 768px) {
                    .book-container { height: 92vh !important; padding: 15px !important; font-size: 16px !important; }
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
                for w in text.split():
                    clean_w = w.strip(".,!?\"'()[]{}:;")
                    if not clean_w:
                        html_content += w + " "
                        continue
                    unique_id = f"wd{word_counter}_{clean_w}"
                    html_content += f"<a href='#' id='{unique_id}' class='w'>{html.escape(w)}</a> "
                    word_counter += 1
                html_content += "</div>"
            html_content += "</div>"
            clicked = click_detector(html_content, key=f"det_{st.session_state.current_screen_index}")

    # --- 右: 辞書リスト (超シンプル版: 単語・品詞・意味のみ) ---
    with col_dict:
        # スロット数を9に増やして一覧性を確保
        for i in range(9):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            if slot_data is None:
                # 空のスロット
                st.markdown(f"<div style='height: 60px; margin-bottom: 4px; border: 1px dashed #f0f0f0; border-radius: 4px;'></div>", unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                
                # 発音記号や例文は表示しない。意味だけズバリ表示。
                st.markdown(f"""
                <div class="dict-card">
                    <div class="dict-header">
                        <span class="dict-word">{chunk}</span>
                        <span class="dict-pos">{info.get('pos')}</span>
                    </div>
                    <div class="dict-meaning">{info.get('meaning')}</div>
                </div>
                """, unsafe_allow_html=True)

    # --- クリック処理 ---
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        parts = clicked.split("_", 1)
        if len(parts) == 2:
            target_word = parts[1]
            current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]
            context_text = " ".join([b["text"] for b in current_blocks])
            
            # AI分析 (高速)
            result = analyze_chunk_with_gpt(target_word, context_text)
            
            # 例文抽出 (UIには出さないが、保存用に確保)
            original_sentence = result.get('original_sentence', '')
            
            # シート保存 (単語, 発音, 意味+品詞, 例文, 画像なし)
            client = get_gspread_client()
            if client:
                try:
                    sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                    meaning_full = f"{result['meaning']} ({result['pos']})"
                    sheet.append_row([
                        result['chunk'], 
                        result.get('pronunciation', ''), 
                        meaning_full, 
                        original_sentence, # 例文はしっかり保存
                        "" # 画像URLは空欄
                    ])
                except: pass
            
            curr = st.session_state.slots
            curr.pop()
            curr.insert(0, {"chunk": result["chunk"], "info": result})
            st.session_state.slots = curr[:9] + [None] * (9 - len(curr))
            
            st.rerun()
