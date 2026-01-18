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
    2. Provide IPA pronunciation.
    3. Provide Japanese meaning (Concise).

    Output MUST be a JSON object:
    1. "chunk": English phrase.
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

# --- テキスト整形 ---
def format_text_advanced(text):
    if not text: return []
    # 改行を一度スペースに置換して繋げる（自然な流し込みのため）
    # ただし、段落の区切りっぽいやつは残したいので調整
    lines = text.splitlines()
    formatted_blocks = []
    current_paragraph = ""
    sentence_endings = ('.', ',', '!', '?', ':', ';', '"', "'", '”', '’', ')', ']')

    for line in lines:
        line = line.strip()
        if not line: continue
        is_bullet = re.match(r'^([•·\-\*]|\d+\.)', line)
        is_header = line.isupper() or re.match(r'^(Chapter|Section|\d+\s+[A-Z])', line, re.IGNORECASE)

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
    st.session_state.slots = [None] * 10
else:
    if len(st.session_state.slots) < 10:
        st.session_state.slots += [None] * (10 - len(st.session_state.slots))

# PDF全テキストと現在の「画面番号」を保存
if "all_text_chunks" not in st.session_state:
    st.session_state.all_text_chunks = []
if "current_chunk_index" not in st.session_state:
    st.session_state.current_chunk_index = 0
if "file_id" not in st.session_state:
    st.session_state.file_id = ""

# ==========================================
# アプリ画面
# ==========================================
st.title("📚 AI Book Reader (One Screen)")

# 1. ファイルアップロード & テキスト分割処理
with st.expander("📂 Upload PDF", expanded=True):
    uploaded_file = st.file_uploader("Choose PDF", type="pdf")
    
    if uploaded_file is not None:
        # ファイルが変わったらリセット
        if st.session_state.file_id != uploaded_file.name:
            st.session_state.file_id = uploaded_file.name
            reader = PdfReader(uploaded_file)
            
            # 全ページを結合して取得
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text() + "\n"
            
            # --- ここで「1画面分」に分割するロジック ---
            # 1画面 = 約350単語と定義 (iPadで見やすい量)
            words = full_text.split()
            chunk_size = 350
            chunks = []
            for i in range(0, len(words), chunk_size):
                chunk_words = words[i:i + chunk_size]
                chunks.append(" ".join(chunk_words))
            
            st.session_state.all_text_chunks = chunks
            st.session_state.current_chunk_index = 0
            st.rerun()

# 2. メイン表示部
if st.session_state.all_text_chunks:
    col_main, col_side = st.columns([4, 1])

    with col_main:
        # ページ送りボタン
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            if st.button("◀ Prev"):
                if st.session_state.current_chunk_index > 0:
                    st.session_state.current_chunk_index -= 1
                    st.rerun()
        with c3:
            if st.button("Next ▶"):
                if st.session_state.current_chunk_index < len(st.session_state.all_text_chunks) - 1:
                    st.session_state.current_chunk_index += 1
                    st.rerun()
        
        # 現在の進捗表示
        curr = st.session_state.current_chunk_index + 1
        total = len(st.session_state.all_text_chunks)
        st.progress(curr / total)
        st.caption(f"Screen {curr} / {total}")

        # テキスト表示
        current_text = st.session_state.all_text_chunks[st.session_state.current_chunk_index]
        blocks = format_text_advanced(current_text)

        html_content = """
        <style>
            /* 画面いっぱいに使い、スクロールバーを出さない(ページ内で完結させる)設計 */
            .book-container {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 20px 30px; /* 余白を削る */
                font-family: 'Georgia', serif;
                font-size: 17px;     /* 文字サイズ微調整 */
                line-height: 1.6;    /* 行間を詰める */
                color: #2c3e50;
                min-height: 600px;   /* 最低限の高さを確保 */
            }
            .header-text { font-weight: bold; font-size: 1.3em; margin: 20px 0 10px 0; border-bottom: 2px solid #eee; color:#000; }
            .list-item { margin-left: 15px; margin-bottom: 5px; border-left: 3px solid #eee; padding-left: 10px; }
            .p-text { margin-bottom: 15px; text-align: justify; }
            
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
        
        for b_idx, block in enumerate(blocks):
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
            for w_idx, w in enumerate(words):
                clean_w = w.strip(".,!?\"'()[]{}:;")
                if not clean_w:
                    html_content += w + " "
                    continue
                unique_id = f"wd{w_idx}_{clean_w}" # シンプルなID
                safe_w = html.escape(w)
                html_content += f"<a href='#' id='{unique_id}' class='w'>{safe_w}</a> "
            html_content += "</div>"
        html_content += "</div>"

        clicked = click_detector(html_content, key=f"det_{st.session_state.current_chunk_index}")

    with col_side:
        st.markdown("### 🗃️ Dict")
        if st.button("Clear", use_container_width=True):
            st.session_state.slots = [None] * 10
            st.rerun()

        for i in range(10):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            
            if slot_data is None:
                st.markdown(f"""
                <div style="height: 140px; border: 2px dashed #e0e0e0; border-radius: 6px; margin-bottom: 10px; display: flex; align-items: center; justify-content: center; color: #ccc; font-size: 0.8em;">Slot {i+1}</div>
                """, unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                pron = info.get('pronunciation', '')
                st.markdown(f"""
                <div style="height: 140px; border-left: 5px solid #2980b9; background-color: #f0f8ff; padding: 10px; margin-bottom: 10px; border-radius: 6px; overflow-y: auto;">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:5px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:1.1em;">{chunk}</span>
                        <span style="background:#d4e6f1; color:#1a5276; padding:2px 5px; border-radius:3px; font-size:0.7em;">{info.get('pos')}</span>
                    </div>
                    <div style="color:#555; font-size:0.85em; margin-bottom:6px;">{pron}</div>
                    <div style="font-weight:bold; font-size:0.95em; color:#333; line-height:1.4;">{info.get('meaning')}</div>
                </div>
                """, unsafe_allow_html=True)

    # クリック処理
    if clicked and clicked != st.session_state.last_clicked:
        st.session_state.last_clicked = clicked
        parts = clicked.split("_", 1)
        if len(parts) == 2:
            target_word = parts[1]
            # 現在表示中のテキスト全体を文脈として渡す
            context_sentence = st.session_state.all_text_chunks[st.session_state.current_chunk_index]
            
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
                    sheet.append_row([result['chunk'], result.get('pronunciation', ''), meaning_full, context_sentence[:200]+"..." ])
                except: pass
            st.rerun()

else:
    st.info("👆 Upload PDF to start reading.")
