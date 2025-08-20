import os
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import notion_client
import streamlit_authenticator as stauth
from cryptography.fernet import Fernet
import logging
import traceback
# ★★★ Firebase Admin SDKの公式なインポート ★★★
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import hashlib
import base64

from notion_utils import get_all_databases, get_pages_in_database
from core_logic import run_new_page_process, run_edit_page_process

# .envファイルから環境変数を読み込む (ローカル開発用)
load_dotenv()

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Streamlit UI設定 ---
st.set_page_config(page_title="Notion記事自動生成AI", layout="wide")

# --- Firestore 初期化 (Firebase Admin SDKを使用) ---
@st.cache_resource
def initialize_firestore():
    """Firebase Admin SDKを初期化し、Firestoreクライアントを返す"""
    try:
        secrets_dict = st.secrets["FIREBASE_SERVICE_ACCOUNT"]
        required_keys = [
            "type", "project_id", "private_key_id", "private_key",
            "client_email", "client_id", "auth_uri", "token_uri",
            "auth_provider_x509_cert_url", "client_x509_cert_url"
        ]
        creds_json = {key: secrets_dict[key] for key in required_keys if key in secrets_dict}
        cred = credentials.Certificate(creds_json)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logging.info("Firestore client initialized successfully via Firebase Admin SDK.")
        return db
    except Exception as e:
        logging.error(f"Firestore initialization failed: {e}")
        st.error("データベースへの接続中にエラーが発生しました。")
        st.exception(e)
        st.stop()

db = initialize_firestore()

# --- 認証情報とAPIキーの管理 ---
def generate_fernet_key(secret_string: str) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(secret_string.encode('utf-8'))
    return base64.urlsafe_b64encode(hasher.digest())

try:
    fernet_key = generate_fernet_key(st.secrets["ENCRYPTION_SECRET"])
    fernet = Fernet(fernet_key)
except KeyError:
    st.error("ENCRYPTION_SECRETが設定されていません。")
    st.stop()

@st.cache_data(ttl=600)
def fetch_config_from_firestore():
    """Firestoreからユーザー設定を読み込む"""
    try:
        users_ref = db.collection('users')
        users_docs = users_ref.stream()
        usernames_dict = {}
        for doc in users_docs:
            user_data = doc.to_dict()
            username = doc.id
            user_entry = {
                'email': user_data.get('email'), 'name': user_data.get('name'),
                'logged_in': False, 'failed_login_attempts': 0
            }
            if 'password' in user_data and user_data['password'] is not None:
                user_entry['password'] = user_data['password']
            usernames_dict[username] = user_entry
        config = {
            'credentials': {'usernames': usernames_dict},
            'cookie': {'expiry_days': 30, 'key': st.secrets["ENCRYPTION_SECRET"], 'name': 'notion_ai_cookie'},
            'preauthorized': {'emails': []}
        }
        if "oauth2" in st.secrets and "google" in st.secrets["oauth2"]:
            google_config = dict(st.secrets["oauth2"]["google"])
            config['oauth2'] = {'google': google_config}
            config['google'] = google_config
        return config
    except Exception as e:
        logging.error(f"Failed to fetch config from Firestore: {e}")
        st.error("Firestoreからの設定読み込み中にエラーが発生しました。")
        return None

def add_or_update_user_in_firestore(username, name, email, password_hash=None):
    try:
        user_ref = db.collection('users').document(username)
        user_data = {'name': name, 'email': email}
        if password_hash:
            user_data['password'] = password_hash
        user_ref.set(user_data, merge=True)
        st.cache_data.clear()
        return True
    except Exception:
        return False

def save_api_keys_to_firestore(username, notion_key, gemini_key):
    encrypted_notion = fernet.encrypt(notion_key.encode()).decode()
    encrypted_gemini = fernet.encrypt(gemini_key.encode()).decode()
    user_ref = db.collection('users').document(username)
    user_ref.update({'notion_api_key': encrypted_notion, 'gemini_api_key': encrypted_gemini})
    st.cache_data.clear()

def load_api_keys_from_firestore(username):
    user_ref = db.collection('users').document(username)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        try:
            decrypted_notion = fernet.decrypt(user_data['notion_api_key'].encode()).decode()
            decrypted_gemini = fernet.decrypt(user_data['gemini_api_key'].encode()).decode()
            return {'notion': decrypted_notion, 'gemini': decrypted_gemini}
        except (KeyError, TypeError):
            return None
    return None

def update_password_in_firestore(username, new_hashed_password):
    try:
        user_ref = db.collection('users').document(username)
        user_ref.update({'password': new_hashed_password})
        st.cache_data.clear()
        return True
    except Exception:
        return False

# --- メインアプリケーション ---
config = fetch_config_from_firestore()

if config:
    authenticator = stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        config['cookie']['expiry_days']
    )
    if "google" in config and "oauth2" in config and st.session_state["authentication_status"] is None:
        if authenticator.experimental_guest_login(provider="google", location='main', oauth2=config['oauth2']):
            st.rerun()
    authenticator.login(
        location='main',
        fields={'Form name': 'ログイン', 'Username': 'ユーザー名', 'Password': 'パスワード', 'Login': 'ログイン'}
    )
else:
    st.error("設定ファイルの読み込みに失敗しました。")
    st.stop()

if st.session_state["authentication_status"]:
    # --- ログイン成功後 ---
    add_or_update_user_in_firestore(st.session_state["username"], st.session_state["name"], st.session_state["email"])
    st.sidebar.title(f'ようこそ, *{st.session_state["name"]}* さん')
    authenticator.logout('ログアウト', 'sidebar')

    with st.sidebar.expander("APIキー設定"):
        with st.form("api_key_form", clear_on_submit=True):
            notion_key_input = st.text_input("Notion APIキー", type="password")
            gemini_key_input = st.text_input("Gemini APIキー", type="password")
            if st.form_submit_button("保存"):
                if notion_key_input and gemini_key_input:
                    save_api_keys_to_firestore(st.session_state["username"], notion_key_input, gemini_key_input)
                    st.success("APIキーを保存しました！")
                else:
                    st.warning("両方のAPIキーを入力してください。")

    with st.sidebar.expander("パスワードリセット"):
        try:
            if authenticator.reset_password(st.session_state["username"], 'パスワードリセット'):
                new_pw_hash = authenticator.credentials['usernames'][st.session_state["username"]]['password']
                update_password_in_firestore(st.session_state["username"], new_pw_hash)
                st.success('パスワードが変更されました。')
        except Exception as e:
            st.error(e)

    user_api_keys = load_api_keys_from_firestore(st.session_state["username"])

    if not user_api_keys:
        st.warning("APIキーが設定されていません。サイドバーから登録してください。")
        st.stop()

    try:
        if st.session_state.get('current_user') != st.session_state["username"] or 'clients_initialized' not in st.session_state:
            st.session_state.notion_client = notion_client.Client(auth=user_api_keys['notion'])
            genai.configure(api_key=user_api_keys['gemini'])
            st.session_state.gemini_model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
            st.session_state.gemini_lite_model = genai.GenerativeModel(os.getenv("GEMINI_LITE_MODEL", "gemini-1.5-flash-latest"))
            st.session_state.notion_client.users.me()
            st.session_state.clients_initialized = True
            st.session_state.current_user = st.session_state["username"]
    except Exception as e:
        st.error(f"APIクライアントの初期化中にエラーが発生しました: {e}")
        st.stop()

    st.title("📝 Notion記事自動生成AI")
    databases = get_all_databases(st.session_state.notion_client)
    if not databases:
        st.error("アクセス可能なNotionデータベースが見つかりませんでした。")
        st.stop()

    db_options = {db['id']: db['title'] for db in databases}
    selected_db_id = st.selectbox("1. 操作するNotionデータベースを選択", options=db_options.keys(), format_func=db_options.get)
    mode = st.radio("2. 実行する操作を選択", ("新しいページを作成する", "既存のページを編集・追記する"), horizontal=True)
    
    # --- UIコンポーネント定義 ---
    persona_options = {
        "プロのライター": "あなたはプロのライターです。", 
        "マーケティング担当者": "あなたは経験豊富なマーケティング担当者です。", 
        "技術ドキュメントライター": "あなたは正確で分かりやすい文章を書く技術ドキュメントライターです。", 
        "Wiki編集者": "あなたは中立的な観点で情報を整理し、構造化された文章を作成するWiki編集者です。",
        "カスタム": "カスタム..."
    }
    prompt_templates = {
        "記事作成": "{topic}について記事を作成してください。", 
        "要約": "{topic}について要約してください。", 
        "アイデア出し": "{topic}のアイデアを5つ提案してください。", 
        "Wikiページ作成": "{topic}について、客観的な事実に基づいたWikiページを作成してください。以下の構成で記述してください： 1. 概要 2. 歴史・背景 3. 主要な特徴 4. 関連項目",
        "カスタム": "カスタム..."
    }
    time_limit_options = {'指定しない': None, '過去1年以内': 'y', '過去1ヶ月以内': 'm', '過去1週間以内': 'w'}

    # --- メインフォーム ---
    form_key_suffix = "_new" if mode == "新しいページを作成する" else "_edit"
    
    if mode == "新しいページを作成する":
        st.subheader("新しいページを作成")
    else:
        st.subheader("既存のページを編集・追記")
        pages = get_pages_in_database(st.session_state.notion_client, selected_db_id)
        if not pages:
            st.warning("このデータベースには編集可能なページがありません。")
            st.stop()
        page_options = {p['id']: p['title'] for p in pages}
        selected_page_id = st.selectbox("編集・追記したいページを選択", options=page_options.keys(), format_func=page_options.get)

    with st.form(f"main_form{form_key_suffix}"):
        st.markdown("##### 1. AIの役割（ペルソナ）")
        selected_persona_key = st.selectbox("ペルソナを選択:", options=persona_options.keys(), key=f"persona{form_key_suffix}")
        ai_persona = st.text_input("具体的な役割:", value=persona_options[selected_persona_key]) if selected_persona_key == "カスタム" else persona_options[selected_persona_key]

        st.markdown("##### 2. 参考資料とWeb検索設定")
        uploaded_files = st.file_uploader("参考ドキュメント:", type=['pdf', 'docx', 'txt'], accept_multiple_files=True, key=f"uploader{form_key_suffix}")
        source_url = st.text_input("参考URL (ファイルがない場合):", key=f"url{form_key_suffix}")
        
        c1, c2, c3 = st.columns(3)
        search_count = c1.slider("Web検索数:", 1, 15, 5, key=f"search_count{form_key_suffix}")
        full_text_token_limit = c2.slider("トークン上限:", 5000, 150000, 20000, 5000, key=f"token_limit{form_key_suffix}")
        # ★★★ 新しいUIコンポーネント ★★★
        time_limit = c3.selectbox("Web検索の期間指定:", options=time_limit_options.keys(), key=f"time_limit{form_key_suffix}")
        time_limit_value = time_limit_options[time_limit]

        st.markdown("##### 3. AIへの指示")
        selected_template_key = st.selectbox("行いたい作業:", options=prompt_templates.keys(), key=f"template{form_key_suffix}")
        if selected_template_key == "カスタム":
            user_prompt = st.text_area("具体的な指示:", key=f"prompt_custom{form_key_suffix}")
            topic = ""
        else:
            user_prompt = prompt_templates[selected_template_key]
            topic = st.text_area("具体的なテーマやキーワード:", key=f"topic{form_key_suffix}")

        submitted = st.form_submit_button("実行", type="primary")

    if submitted:
        final_prompt = user_prompt.format(topic=topic) if topic else user_prompt
        if not final_prompt or not ai_persona:
            st.warning("ペルソナと指示の両方を入力してください。")
        else:
            status_placeholder = st.empty()
            results_placeholder = st.empty()
            if mode == "新しいページを作成する":
                run_new_page_process(
                    database_id=selected_db_id, 
                    user_prompt=final_prompt, 
                    ai_persona=ai_persona, 
                    uploaded_files=uploaded_files, 
                    source_url=source_url, 
                    search_count=search_count, 
                    full_text_token_limit=full_text_token_limit, 
                    time_limit=time_limit_value, 
                    status_placeholder=status_placeholder, 
                    results_placeholder=results_placeholder
                )
            else:
                run_edit_page_process(
                    page_id=selected_page_id, 
                    user_prompt=final_prompt, 
                    ai_persona=ai_persona, 
                    uploaded_files=uploaded_files, 
                    source_url=source_url, 
                    search_count=search_count, 
                    full_text_token_limit=full_text_token_limit, 
                    time_limit=time_limit_value, 
                    status_placeholder=status_placeholder, 
                    results_placeholder=results_placeholder
                )

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名かパスワードが間違っています')

elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
    
    with st.expander("パスワードをお忘れですか？"):
        try:
            (username_of_forgotten_password,
             email_of_forgotten_password,
             new_random_password) = authenticator.forgot_password(
                 location='main',
                 fields={'Form name': 'パスワードリセット', 'Username': 'ユーザー名', 'Submit': '送信'}
             )

            if username_of_forgotten_password:
                st.success('新しい一時パスワードが生成されました。')
                st.warning('**重要:** このパスワードを安全な場所にコピーし、ログイン後に必ずパスワードをリセットしてください。')
                st.code(new_random_password)
                
                new_password_hash = config['credentials']['usernames'][username_of_forgotten_password]['password']
                if update_password_in_firestore(username_of_forgotten_password, new_password_hash):
                    st.info('データベースのパスワードが更新されました。')
                else:
                    st.error('データベースのパスワード更新に失敗しました。')

            elif username_of_forgotten_password == False:
                st.error('ユーザー名が見つかりませんでした。')
        except Exception as e:
            st.error(e)
            
    with st.expander("ユーザー名をお忘れですか？"):
        try:
            (username_of_forgotten_username,
             email_of_forgotten_username) = authenticator.forgot_username(
                 location='main',
                 fields={'Form name': 'ユーザー名検索', 'Email': 'メールアドレス', 'Submit': '検索'}
             )
            
            if username_of_forgotten_username:
                st.success('あなたのユーザー名はこちらです:')
                st.info(username_of_forgotten_username)
            elif username_of_forgotten_username == False:
                st.error('入力されたメールアドレスに紐づくユーザーが見つかりませんでした。')
        except Exception as e:
            st.error(e)

    try:
        email, username, name = authenticator.register_user(
            location='main',
            fields={'Form name': '新規ユーザー登録', 'Username': 'ユーザー名 (半角英数字のみ)', 'Email': 'メールアドレス', 'Name': '氏名', 'Password': 'パスワード', 'Repeat password': 'パスワードを再入力', 'Register': '登録する'}
        )

        if email:
            logging.info("ユーザー登録情報のFirestore保存処理を開始します。")
            try:
                if username in config['credentials']['usernames']:
                    hashed_password = config['credentials']['usernames'][username]['password']
                    add_or_update_user_in_firestore(username, name, email, hashed_password)
                    st.success('ユーザー登録が成功しました。再度ログインしてください。')
                else:
                    logging.error(f"ユーザー '{username}' のパスワード情報がconfigに見つかりません。")
                    st.error("登録情報の取得に失敗しました。もう一度お試しください。")
            except Exception as e:
                logging.error(f"Firestore保存処理でエラーが発生しました: {traceback.format_exc()}")
                st.error("Firestoreへのユーザー登録中にエラーが発生しました。")

    except stauth.utilities.exceptions.RegisterError as e:
        error_message = str(e)
        logging.warning(f"RegisterError発生: {error_message}")
        # パスワードポリシーに関するエラーメッセージを親切に表示
        if "Password must" in error_message:
            st.error("パスワードは以下の要件を満たす必要があります：\n- 8文字以上\n- 1つ以上の小文字を含む\n- 1つ以上の大文字を含む\n- 1つ以上の数字を含む\n- 1つ以上の特殊文字を含む (@$!%*?&)")
        else:
            st.error(e)
    except Exception as e:
        logging.error(f"register_userウィジェットで予期せぬエラーが発生しました: {traceback.format_exc()}")
        st.error(f"ユーザー登録フォームの表示中に予期せぬエラーが発生しました。")