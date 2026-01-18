import streamlit as st
import streamlit.components.v1 as components
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

# --- 設定: OpenAI ---
def analyze_chunk_with_gpt(target_word, context_sentence):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
    prompt = f"""
    You are an expert English teacher.
    The user is reading this text: "{context_sentence}"
    The user clicked the word: "{target_word}"

    Your task:
    1. Identify the meaningful "chunk" or collocation.
    2. Provide the IPA pronunciation.
    3. Provide the Japanese meaning (Short & Clear).

    Output MUST be a JSON object with these keys:
    1. "chunk": The identified phrase.
    2. "pronunciation": IPA symbols.
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
        is_short = len(line) < 80 and not line.endswith(sentence_endings)
        is_header_pattern = (line.isupper() or re.match(r'^(Chapter|Section|\d+\s+[A-Z])', line, re.IGNORECASE))
        is_header = is_short and (not is_bullet) and is_header_pattern

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

def group_blocks_into_screens(blocks, words_per_screen=400):
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
    st.session_state.slots = [None] * 10
else:
    if len(st.session_state.slots) < 10:
        st.session_state.slots += [None] * (10 - len(st.session_state.slots))
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0
if "file_id" not in st.session_state:
    st.session_state.file_id = ""

# ==========================================
# アプリ画面
# ==========================================
st.title("📚 AI Book Reader")

# 1. ファイルアップロード
with st.expander("📂 Upload PDF", expanded=True):
    uploaded_file = st.file_uploader("Choose PDF", type="pdf")
    if uploaded_file is not None:
        if st.session_state.file_id != uploaded_file.name:
            st.session_state.file_id = uploaded_file.name
            reader = PdfReader(uploaded_file)
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text() + "\n"
            structured_blocks = parse_pdf_to_structured_blocks(full_text)
            st.session_state.all_screens = group_blocks_into_screens(structured_blocks, words_per_screen=400)
            st.session_state.current_screen_index = 0
            st.rerun()

# 2. メイン表示部
if st.session_state.all_screens:
    col_main, col_side = st.columns([4, 1])

    with col_main:
        # ナビゲーション
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            if st.button("◀ Prev"):
                if st.session_state.current_screen_index > 0:
                    st.session_state.current_screen_index -= 1
                    st.rerun()
        with c3:
            if st.button("Next ▶"):
                if st.session_state.current_screen_index < len(st.session_state.all_screens) - 1:
                    st.session_state.current_screen_index += 1
                    st.rerun()
        
        curr = st.session_state.current_screen_index + 1
        total = len(st.session_state.all_screens)
        st.progress(curr / total)
        st.caption(f"Page {curr} / {total}")

        current_blocks = st.session_state.all_screens[st.session_state.current_screen_index]

        # 読書エリア：高さ1000pxで固定
        html_content = """
        <style>
            .book-container {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 40px;
                font-family: 'Georgia', serif;
                font-size: 17px;     
                line-height: 1.6;    
                color: #2c3e50;
                height: 1000px; /* 高さ固定 */
                overflow-y: auto;
            }
            .header-text { 
                font-weight: bold; 
                font-size: 1.4em; 
                margin: 25px 0 15px 0; 
                border-bottom: 2px solid #eee; 
                color: #000;
                line-height: 1.3;
            }
            .list-item { margin-left: 20px; margin-bottom: 8px; border-left: 3px solid #eee; padding-left: 10px; }
            .p-text { margin-bottom: 20px; text-align: justify; }
            
            @media only screen and (max-width: 768px) {
                .book-container {
                    padding: 15px !important;
                    font-size: 15px !important;
                    line-height: 1.5 !important;
                }
            }

            .w { 
                text-decoration: none; color: #2c3e50; cursor: pointer; 
                border-bottom: 1px dotted #ccc; transition: all 0.1s; 
            }
            .w:hover { 
                color: #d35400; border-bottom: 2px solid #d35400; background-color: #fff3e0; 
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

        clicked = click_detector(html_content, key=f"det_scr_{st.session_state.current_screen_index}")

    with col_side:
        st.markdown("### 🗃️ Dict")
        if st.button("Clear", use_container_width=True):
            st.session_state.slots = [None] * 10
            st.rerun()

        for i in range(10):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            if slot_data is None:
                # 空スロットも高さ90pxに縮小
                st.markdown(f"""
                <div style="
                    height: 90px;
                    border: 2px dashed #e0e0e0;
                    border-radius: 6px;
                    margin-bottom: 8px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: #ccc;
                    font-size: 0.8em;
                ">Slot {i+1}</div>
                """, unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                pron = info.get('pronunciation', '')
                
                # スロット高さを90pxに圧縮して、隙間込みで左側の1000pxに合わせる
                # 文字サイズを調整して情報を詰め込む
                st.markdown(f"""
                <div style="
                    height: 90px; 
                    border-left: 5px solid #2980b9; 
                    background-color: #f0f8ff; 
                    padding: 8px; 
                    margin-bottom: 8px; 
                    border-radius: 6px; 
                    overflow: hidden; /* はみ出し防止 */
                ">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:2px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:0.95em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:80%;">{chunk}</span>
                        <span style="background:#d4e6f1; color:#1a5276; padding:1px 3px; border-radius:3px; font-size:0.65em;">{info.get('pos')}</span>
                    </div>
                    <div style="font-family:'Lucida Sans Unicode', sans-serif; color:#666; font-size:0.75em; margin-bottom:2px;">{pron}</div>
                    <div style="font-weight:bold; font-size:0.85em; color:#333; line-height:1.2; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">{info.get('meaning')}</div>
                </div>
                """, unsafe_allow_html=True)

    # クリック処理
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
            st.session_state.slots = curr[:10] + [None] * (10 - len(curr))
            
            client = get_gspread_client()
            if client:
                try:
                    sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                    meaning_full = f"{result['meaning']} ({result['pos']})"
                    sheet.append_row([result['chunk'], result.get('pronunciation', ''), meaning_full, context_sentence[:300]+"..." ])
                except: pass
            st.rerun()

else:
    st.info("👆 Upload PDF to start reading.")
