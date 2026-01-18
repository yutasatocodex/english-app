import streamlit as st
from pypdf import PdfReader
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from st_click_detector import click_detector
import html
import re
import json
import urllib.parse
import hashlib
import requests
import io
import time
from openai import OpenAI

# --- ページ設定 ---
st.set_page_config(layout="wide", page_title="AI Book Reader", initial_sidebar_state="collapsed")

# --- 設定: Google連携 ---
# スプレッドシートとドライブ両方の権限を確保
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def get_clients():
    try:
        # StreamlitのSecretsから認証情報を取得
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        
        # 1. GSpread (シート用クライアント)
        gc_client = gspread.authorize(creds)
        
        # 2. Drive API (画像アップロード用クライアント)
        service = build('drive', 'v3', credentials=creds)
        
        return gc_client, service
    except Exception as e:
        st.error(f"Google Auth Error: {e}")
        return None, None

# --- 設定: OpenAI (記憶定着プロンプト) ---
def analyze_chunk_with_gpt(target_word, context_text):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
    prompt = f"""
    The user is reading: "{context_text}"
    Target word: "{target_word}"

    Task:
    1. Identify the chunk.
    2. IPA pronunciation.
    3. Japanese meaning (Concise).
    4. Extract the ONE specific sentence containing the target word.
    5. Create a "Mnemonic Image Prompt" (English) for FLUX AI.
       - Concept: Surrealism, Visual Pun, or Exaggerated Action.
       - Make it memorable and weird.
       - Example for 'Procrastinate': "A sloth sleeping on a pile of alarm clocks, digital art".
       - NO TEXT, NO LETTERS.

    Output JSON keys: "chunk", "pronunciation", "meaning", "pos", "original_sentence", "image_prompt"
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"chunk": target_word, "pronunciation": "", "meaning": "Error", "pos": "-", "original_sentence": "", "image_prompt": ""}

# --- 画像生成 & Drive保存 (ここが心臓部) ---
def generate_and_upload_image(image_prompt, word_key, drive_service):
    # 1. Pollinations (Fluxモデル) で画像生成
    # seed固定でキャッシュを効かせる
    hash_object = hashlib.md5(word_key.encode())
    seed = int(hash_object.hexdigest(), 16) % 100000
    
    # model=flux を指定して高画質化
    refined_prompt = f"{image_prompt}, detailed, 8k, best quality, no text"
    safe_prompt = urllib.parse.quote(refined_prompt)
    image_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=512&height=512&model=flux&nologo=true&seed={seed}"
    
    try:
        # 2. 画像をダウンロード (メモリ上に保持)
        response = requests.get(image_url, timeout=20)
        
        if response.status_code == 200:
            image_data = io.BytesIO(response.content)
            
            # 3. Google Driveにアップロード
            file_metadata = {
                'name': f"{word_key}_{seed}.jpg",
                'mimeType': 'image/jpeg',
                'parents': ['1dcbr2GIzWdJPhGDw_5VG2uS-_lYveKyo']  # ★ここにあなたのフォルダIDを設定済み★
            }
            media = MediaIoBaseUpload(image_data, mimetype='image/jpeg')
            
            # Drive APIでファイル作成
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webContentLink'
            ).execute()
            
            # 4. 誰でも見れるように権限変更 (Ankiがアクセスできるように重要)
            drive_service.permissions().create(
                fileId=file.get('id'),
                body={'type': 'anyone', 'role': 'reader'},
                fields='id'
            ).execute()
            
            # ダウンロード用URLを返す
            return file.get('webContentLink')
            
    except Exception as e:
        st.warning(f"Image Upload Failed: {e}. Using direct link instead.")
        return image_url
    
    return image_url

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

def extract_sentence_python(text, word):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for s in sentences:
        if word in s:
            return s.strip()
    return text[:100] + "..."

# --- セッション初期化 ---
if "last_clicked" not in st.session_state:
    st.session_state.last_clicked = ""
if "slots" not in st.session_state:
    st.session_state.slots = [None] * 7
if "reader_mode" not in st.session_state:
    st.session_state.reader_mode = False
if "all_screens" not in st.session_state:
    st.session_state.all_screens = []
if "current_screen_index" not in st.session_state:
    st.session_state.current_screen_index = 0
if "enable_image_gen" not in st.session_state:
    st.session_state.enable_image_gen = False

# --- CSS ---
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
    .stCheckbox { padding-top: 5px; }
    .stApp { background-color: #ffffff; }
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
# 2. 読書画面
# ==========================================
else:
    nav_left, nav_right = st.columns([4.5, 1])
    
    with nav_left:
        c1, c2, c3, c4, c5 = st.columns([0.5, 0.5, 4, 1.5, 0.5])
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
            # 画像生成スイッチ (デフォルトOFF)
            st.session_state.enable_image_gen = st.checkbox("🖼️ Image Gen", value=st.session_state.enable_image_gen)
        with c5:
             if st.button("✕", key="close"):
                st.session_state.reader_mode = False
                st.session_state.slots = [None] * 7
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

    # --- 右: 辞書リスト (画像は非表示・テキストのみ) ---
    with col_dict:
        for i in range(7):
            slot_data = st.session_state.slots[i] if i < len(st.session_state.slots) else None
            if slot_data is None:
                st.markdown(f"<div style='height: 12.8vh; margin-bottom: 0.5vh; border: 1px dashed #f0f0f0; border-radius: 4px;'></div>", unsafe_allow_html=True)
            else:
                chunk = slot_data['chunk']
                info = slot_data['info']
                st.markdown(f"""
                <div style="height: 12.8vh; border-left: 4px solid #2980b9; background-color: #f8fbff; padding: 8px; margin-bottom: 0.5vh; border-radius: 4px; overflow: hidden; display: flex; flex-direction: column; justify-content: center;">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px;">
                        <span style="font-weight:bold; color:#1a5276; font-size:1.1em; overflow:hidden; white-space:nowrap; text-overflow:ellipsis;">{chunk}</span>
                        <span style="font-size:0.7em; color:#1a5276; background:#e1eff7; padding:1px 4px; border-radius:3px;">{info.get('pos')}</span>
                    </div>
                    <div style="font-size:0.8em; color:#777; margin-bottom:4px;">{info.get('pronunciation', '')}</div>
                    <div style="font-weight:bold; font-size:0.9em; color:#333; line-height:1.3; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow:hidden;">{info.get('meaning')}</div>
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
            
            with st.spinner("Analyzing..."):
                # 1. AI分析
                result = analyze_chunk_with_gpt(target_word, context_text)
                
                # 2. 一文抽出の保険
                original_sentence = result.get('original_sentence', '')
                if not original_sentence or len(original_sentence) > 150:
                    original_sentence = extract_sentence_python(context_text, target_word)
                
                # 3. 画像生成 & Driveアップロード (スイッチON時のみ)
                final_image_url = ""
                if st.session_state.enable_image_gen:
                    with st.spinner("🎨 Creating Image & Uploading to Drive..."):
                        image_prompt = result.get('image_prompt', target_word)
                        gc_client, drive_service = get_clients()
                        if drive_service:
                            final_image_url = generate_and_upload_image(image_prompt, target_word, drive_service)
                        else:
                            final_image_url = "" 
                
                # 4. シート保存
                client, _ = get_clients()
                if client:
                    try:
                        sheet = client.open(st.secrets["sheet_config"]["sheet_name"]).sheet1
                        meaning_full = f"{result['meaning']} ({result['pos']})"
                        sheet.append_row([result['chunk'], result.get('pronunciation', ''), meaning_full, original_sentence, final_image_url])
                    except: pass
                
                # 5. UI更新
                curr = st.session_state.slots
                curr.pop()
                curr.insert(0, {"chunk": result["chunk"], "info": result})
                st.session_state.slots = curr[:7] + [None] * (7 - len(curr))
                
            st.rerun()
