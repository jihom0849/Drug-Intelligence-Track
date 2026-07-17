# main.py
import streamlit as st

# =========================================================
# AFFICHAGE IMMÉDIAT
# =========================================================
st.set_page_config(page_title="Drug Intelligence Track (DIT)", layout="wide", page_icon="🔍")

# --- MODIFIER ICI : lien réel de ton site web ---
APP_NAME = "Drug Intelligence Track (DIT)"
APP_WEBSITE_URL = "https://druginteligencetrack.com"

st.title(APP_NAME)

try:
    import sqlite3
    import pandas as pd
    from PIL import Image, ExifTags
    from datetime import datetime, timedelta
    import imagehash
    import hashlib
    import os
    import uuid

    DB_PATH = "database.db"
    UPLOADS_DIR = "uploads"
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    # --- MODIFIER ICI (KR) : code administrateur donné par le développeur web ---
    ADMIN_MASTER_CODE = "DEV-ADMIN-2026-CHANGE-ME"

    MAX_FAILED_ATTEMPTS = 5
    LOCK_MINUTES = 15

    OFFICIAL_PORTAL_URL = "https://nodrugzone.mfds.go.kr/home/kor/main.do"

    # =====================================================
    # BASE DE DONNÉES
    # =====================================================
    def init_db():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS public_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                created_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS lexicon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_term TEXT,
                meaning TEXT,
                approved INTEGER DEFAULT 1
            )
        """)
        c.execute("SELECT COUNT(*) FROM lexicon")
        if c.fetchone()[0] == 0:
            # --- MODIFIER ICI (KR) : exemples de termes codés ---
            seed_data = [
                ("비타민 (vitamines)", "Terme codé générique - EXEMPLE ILLUSTRATIF", 1),
                ("사탕 (bonbons)", "Terme codé générique - EXEMPLE ILLUSTRATIF", 1),
            ]
            c.executemany("INSERT INTO lexicon (code_term, meaning, approved) VALUES (?, ?, ?)", seed_data)

        c.execute("""
            CREATE TABLE IF NOT EXISTS public_text_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                description TEXT,
                created_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def init_police_tables():
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS police_officers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                police_code TEXT UNIQUE,
                station TEXT,
                password_hash TEXT,
                salt TEXT,
                is_supervisor INTEGER DEFAULT 0,
                failed_attempts INTEGER DEFAULT 0,
                locked_until TEXT,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS reference_drugs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                substance_name TEXT,
                description TEXT,
                image_hash TEXT,
                filename TEXT,
                added_by_police_code TEXT,
                station TEXT,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS public_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                image_path TEXT,
                image_hash TEXT,
                gps_coords TEXT,
                date_taken TEXT,
                status TEXT DEFAULT 'New',
                reviewed_by TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    init_db()
    init_police_tables()

    # -----------------------------------------------------
    # Fonctions publiques (témoignages / lexique / signalement texte)
    # -----------------------------------------------------
    def insert_public_experience(content):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO public_experiences (content, created_at) VALUES (?, ?)",
                   (content, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def add_lexicon_term(term, meaning):
        # Les nouveaux termes ajoutés par le public sont en attente d'approbation (approved=0)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO lexicon (code_term, meaning, approved) VALUES (?, ?, 0)", (term, meaning))
        conn.commit()
        conn.close()

    def approve_lexicon_term(term_id):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE lexicon SET approved = 1 WHERE id = ?", (term_id,))
        conn.commit()
        conn.close()

    def delete_lexicon_term(term_id):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM lexicon WHERE id = ?", (term_id,))
        conn.commit()
        conn.close()

    def insert_text_report(category, description):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO public_text_reports (category, description, created_at) VALUES (?, ?, ?)",
                   (category, description, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def fetch_all(table_name):
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(f"SELECT * FROM {table_name} ORDER BY id DESC", conn)
        conn.close()
        return df

    # -----------------------------------------------------
    # Authentification police (hash + sel, verrouillage après échecs)
    # -----------------------------------------------------
    def hash_password(password: str, salt: str) -> str:
        # Amélioration sécurité : sha256 + sel unique par utilisateur.
        # Pour une vraie mise en production, préférer bcrypt/argon2.
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

    def register_police_officer(police_code, station, password, is_supervisor):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        salt = uuid.uuid4().hex
        try:
            c.execute("""
                INSERT INTO police_officers (police_code, station, password_hash, salt, is_supervisor, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (police_code, station, hash_password(password, salt), salt, int(is_supervisor), datetime.now().isoformat()))
            conn.commit()
            success = True
        except sqlite3.IntegrityError:
            success = False
        conn.close()
        return success

    def check_police_login(police_code, password):
        """Retourne (status, station, is_supervisor).
        status: 'ok' | 'bad_credentials' | 'locked' | 'not_found'
        """
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT password_hash, salt, station, is_supervisor, failed_attempts, locked_until
            FROM police_officers WHERE police_code = ?
        """, (police_code,))
        row = c.fetchone()

        if not row:
            conn.close()
            return "not_found", None, False

        password_hash, salt, station, is_supervisor, failed_attempts, locked_until = row

        if locked_until:
            locked_until_dt = datetime.fromisoformat(locked_until)
            if datetime.now() < locked_until_dt:
                conn.close()
                return "locked", None, False

        if hash_password(password, salt) == password_hash:
            c.execute("UPDATE police_officers SET failed_attempts = 0, locked_until = NULL WHERE police_code = ?", (police_code,))
            conn.commit()
            conn.close()
            return "ok", station, bool(is_supervisor)
        else:
            new_attempts = failed_attempts + 1
            if new_attempts >= MAX_FAILED_ATTEMPTS:
                lock_until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat()
                c.execute("UPDATE police_officers SET failed_attempts = ?, locked_until = ? WHERE police_code = ?",
                          (new_attempts, lock_until, police_code))
            else:
                c.execute("UPDATE police_officers SET failed_attempts = ? WHERE police_code = ?",
                          (new_attempts, police_code))
            conn.commit()
            conn.close()
            return "bad_credentials", None, False

    # -----------------------------------------------------
    # Fonctions police / comparaison de similarité / stockage images
    # -----------------------------------------------------
    def compute_image_hash(image: Image.Image) -> str:
        return str(imagehash.phash(image))

    def save_image_to_disk(uploaded_file) -> str:
        """Sauvegarde l'image uploadée sur disque et retourne le chemin relatif."""
        ext = os.path.splitext(uploaded_file.name)[1]
        unique_name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(UPLOADS_DIR, unique_name)
        with open(path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return path

    def insert_reference_drug(substance_name, description, image_hash, filename, police_code, station):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO reference_drugs (substance_name, description, image_hash, filename, added_by_police_code, station, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (substance_name, description, image_hash, filename, police_code, station, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def insert_public_submission(filename, image_path, image_hash, gps_coords, date_taken):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO public_submissions (filename, image_path, image_hash, gps_coords, date_taken, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filename, image_path, image_hash, gps_coords, date_taken, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def update_submission_status(submission_id, status, reviewed_by):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE public_submissions SET status = ?, reviewed_by = ? WHERE id = ?",
                   (status, reviewed_by, submission_id))
        conn.commit()
        conn.close()

    def find_similar_matches(target_hash_str, top_n=5):
        conn = sqlite3.connect(DB_PATH)
        df_ref = pd.read_sql_query("SELECT * FROM reference_drugs", conn)
        conn.close()

        if df_ref.empty:
            return pd.DataFrame()

        target_hash = imagehash.hex_to_hash(target_hash_str)
        results = []
        for _, row in df_ref.iterrows():
            try:
                ref_hash = imagehash.hex_to_hash(row["image_hash"])
                distance = target_hash - ref_hash
                max_distance = 64
                similarity_pct = round((1 - (distance / max_distance)) * 100, 1)
                results.append({
                    "substance_name": row["substance_name"],
                    "description": row["description"],
                    "similarity_pct": similarity_pct
                })
            except Exception:
                continue

        df_results = pd.DataFrame(results).sort_values("similarity_pct", ascending=False)
        return df_results.head(top_n)

    # -----------------------------------------------------
    # EXIF
    # -----------------------------------------------------
    def extract_exif(image: Image.Image):
        date_taken = None
        gps_str = None
        camera_model = None
        try:
            exif_raw = image._getexif()
            if not exif_raw:
                return None, None, None
            exif_data = {}
            for tag_id, value in exif_raw.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                exif_data[tag] = value
            date_taken = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime")
            camera_model = exif_data.get("Model")
            gps_info = exif_data.get("GPSInfo")
            if gps_info:
                gps_data = {}
                for key in gps_info.keys():
                    tag_name = ExifTags.GPSTAGS.get(key, key)
                    gps_data[tag_name] = gps_info[key]

                def convert_to_degrees(value):
                    d, m, s = value
                    return d + (m / 60.0) + (s / 3600.0)

                if "GPSLatitude" in gps_data and "GPSLongitude" in gps_data:
                    lat = convert_to_degrees(gps_data["GPSLatitude"])
                    if gps_data.get("GPSLatitudeRef") != "N":
                        lat = -lat
                    lon = convert_to_degrees(gps_data["GPSLongitude"])
                    if gps_data.get("GPSLongitudeRef") != "E":
                        lon = -lon
                    gps_str = f"{lat:.6f}, {lon:.6f}"
        except Exception:
            pass
        return date_taken, gps_str, camera_model

    def parse_gps_for_map(gps_str):
        """Convertit 'lat, lon' en (lat, lon) floats, ou None si invalide."""
        try:
            if not gps_str or gps_str == "N/A":
                return None
            lat_str, lon_str = gps_str.split(",")
            return float(lat_str.strip()), float(lon_str.strip())
        except Exception:
            return None

    # -----------------------------------------------------
    # TEXTES BILINGUES (English / Korean only — no French in UI)
    # -----------------------------------------------------
    # --- MODIFY HERE (KR) : all Korean UI text lives in the "ko" dict below ---
    TEXTS = {
        "ko": {
            "welcome": "환영합니다 — 시민 제보 및 수사 지원 플랫폼",
            "tab_investigator": "수사관 구역",
            "tab_public": "공공 구역",
            "restricted": "접근 제한",
            "login_tab": "로그인",
            "register_tab": "회원가입",
            "login_header": "경찰 로그인",
            "police_code": "경찰 코드",
            "password": "비밀번호",
            "login_button": "로그인",
            "bad_credentials": "코드 또는 비밀번호가 올바르지 않습니다.",
            "not_found": "존재하지 않는 계정입니다.",
            "locked": f"로그인 시도가 너무 많습니다. {LOCK_MINUTES}분 후 다시 시도해 주세요.",
            "register_header": "경찰 회원가입",
            "register_caption": "웹 개발자가 제공한 관리자 코드가 필요합니다.",
            "station": "소속 경찰서 (예: 강남경찰서)",
            "confirm_password": "비밀번호 확인",
            "admin_code": "관리자 코드",
            "supervisor_checkbox": "감독관 계정으로 등록 (모든 경찰서 데이터 열람 가능)",
            "create_account": "계정 생성",
            "account_created": "계정이 생성되었습니다. 이제 로그인할 수 있습니다.",
            "invalid_admin_code": "관리자 코드가 올바르지 않습니다.",
            "fields_required": "모든 필드를 입력해야 합니다.",
            "passwords_mismatch": "비밀번호가 일치하지 않습니다.",
            "code_taken": "이미 사용 중인 경찰 코드입니다.",
            "logged_in_as": "로그인됨",
            "logout": "로그아웃",
            "supervisor_badge": "감독관",
            "officer_badge": "경찰관",
            "official_resource": "공식 참고 자료",
            "dashboard_header": "대시보드",
            "metric_submissions": "총 제보 수",
            "metric_references": "참조 데이터 수",
            "metric_officers": "등록된 경찰 계정",
            "metric_pending": "검토 대기 중",
            "sub_tab_dashboard": "대시보드",
            "sub_tab_ref": "참조 데이터베이스",
            "sub_tab_review": "제보 검토",
            "sub_tab_map": "지도",
            "sub_tab_lexicon_mod": "은어 사전 관리",
            "add_reference": "참조 샘플 추가",
            "reference_photo": "참조 사진",
            "substance_name": "물질 이름",
            "description": "설명 (색상, 형태, 표시 등)",
            "save_reference": "참조 데이터베이스에 저장",
            "reference_saved": "참조가 저장되었습니다.",
            "current_references": "현재 참조 데이터베이스",
            "submissions_to_review": "검토할 제보",
            "no_submissions": "아직 제보가 없습니다.",
            "gps_label": "GPS",
            "date_photo": "촬영 날짜",
            "possible_matches": "가능한 일치 항목 (시각적 유사성만 해당)",
            "no_match": "참조 데이터베이스에서 일치 항목을 찾을 수 없습니다.",
            "similarity_warning": "⚠️ 시각적 유사성은 화학적 식별을 의미하지 않습니다. 사람에 의한/실험실 검증이 필요합니다.",
            "status_label": "상태",
            "status_new": "신규",
            "status_in_progress": "진행 중",
            "status_resolved": "해결됨",
            "status_dismissed": "기각됨",
            "update_status": "상태 업데이트",
            "export_csv": "CSV로 내보내기",
            "map_header": "제보 위치 지도",
            "map_no_data": "지도에 표시할 GPS 데이터가 없습니다.",
            "lexicon_pending": "승인 대기 중인 용어",
            "approve": "승인",
            "reject": "거부",
            "no_pending_terms": "대기 중인 용어가 없습니다.",
            "share_header": "경험 공유하기",
            "share_placeholder": "여기에 경험을 작성하세요...",
            "share_button": "제출",
            "share_success": "제출이 완료되었습니다. 감사합니다.",
            "experiences_table_header": "공유된 경험 목록",
            "lexicon_header": "은어 사전 (코드 용어)",
            "lexicon_pending_note": "새 용어는 게시되기 전에 경찰의 승인을 받아야 합니다.",
            "add_term_label": "새 용어 추가",
            "term_placeholder": "코드 용어",
            "meaning_placeholder": "의미 / 설명",
            "add_term_button": "추가",
            "term_submitted": "용어가 제출되었으며 승인을 기다리고 있습니다.",
            "emergency_header": "긴급 연락처",
            "emergency_police": "경찰 (긴급): 112",
            "emergency_medical": "응급 의료: 119",
            "emergency_drug_report": "마약류 범죄 신고 (예시 번호): 1301",
            "emergency_drug_hotline": "마약류·약물남용 전국 상담: 1342 (식품의약품안전처)",
            "emergency_official_portal": "공식 포털 바로가기: 마약청정 대한민국",
            "public_upload_header": "사진 신고하기",
            "public_upload_label": "사진 업로드",
            "public_upload_button": "경찰에 전송",
            "public_upload_success": "사진이 전송되었습니다. 신고해 주셔서 감사합니다.",
            "text_report_header": "텍스트로 신고하기 (사진 없이)",
            "text_report_category": "신고 유형",
            "text_report_desc": "설명",
            "text_report_button": "제출",
            "text_report_success": "신고가 접수되었습니다. 감사합니다.",
            "category_options": ["의심스러운 활동", "의심스러운 장소", "온라인 판매 의심", "기타"],
            "faq_header": "자주 묻는 질문",
            "faq_content": [
                ("이 앱은 무엇을 위한 것인가요?",
                 "Drug Intelligence Platform (DIP)은 시민이 의심스러운 활동을 신고하고, 경찰이 시각적 유사성을 참고하여 "
                 "제보를 검토할 수 있도록 돕는 플랫폼입니다."),
                ("제 신원이 공개되나요?",
                 "공공 구역의 신고는 익명으로 처리됩니다. 개인 식별 정보는 요구하지 않습니다."),
                ("이 앱이 마약을 화학적으로 식별할 수 있나요?",
                 "아니요. 이 앱은 사진 간 시각적 유사성만 비교하며, 화학적 식별은 실험실 분석이 필요합니다."),
            ],
        },
        "en": {
            "welcome": "Welcome — Citizen reporting & investigation support platform",
            "tab_investigator": "Investigator Zone",
            "tab_public": "Public Zone",
            "restricted": "Restricted access",
            "login_tab": "Login",
            "register_tab": "Register",
            "login_header": "Police login",
            "police_code": "Police code",
            "password": "Password",
            "login_button": "Login",
            "bad_credentials": "Incorrect code or password.",
            "not_found": "This account does not exist.",
            "locked": f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes.",
            "register_header": "Police registration",
            "register_caption": "Requires an admin code provided by the web developer.",
            "station": "Police station (e.g. Gangnam Police Station)",
            "confirm_password": "Confirm password",
            "admin_code": "Admin code",
            "supervisor_checkbox": "Register as supervisor (can view data from all stations)",
            "create_account": "Create account",
            "account_created": "Account created. You can now log in.",
            "invalid_admin_code": "Invalid admin code.",
            "fields_required": "All fields are required.",
            "passwords_mismatch": "Passwords do not match.",
            "code_taken": "This police code is already taken.",
            "logged_in_as": "Logged in as",
            "logout": "Logout",
            "supervisor_badge": "Supervisor",
            "officer_badge": "Officer",
            "official_resource": "Official reference resource",
            "dashboard_header": "Dashboard",
            "metric_submissions": "Total submissions",
            "metric_references": "Reference entries",
            "metric_officers": "Registered officers",
            "metric_pending": "Pending review",
            "sub_tab_dashboard": "Dashboard",
            "sub_tab_ref": "Reference database",
            "sub_tab_review": "Review submissions",
            "sub_tab_map": "Map",
            "sub_tab_lexicon_mod": "Lexicon moderation",
            "add_reference": "Add reference sample",
            "reference_photo": "Reference photo",
            "substance_name": "Substance name",
            "description": "Description (color, shape, markings...)",
            "save_reference": "Save to reference database",
            "reference_saved": "Reference saved.",
            "current_references": "Current reference database",
            "submissions_to_review": "Submissions to review",
            "no_submissions": "No submissions yet.",
            "gps_label": "GPS",
            "date_photo": "Photo date",
            "possible_matches": "Possible matches (visual similarity only)",
            "no_match": "No match found in the reference database.",
            "similarity_warning": "⚠️ Visual similarity does not confirm chemical identity. Human/lab verification required.",
            "status_label": "Status",
            "status_new": "New",
            "status_in_progress": "In progress",
            "status_resolved": "Resolved",
            "status_dismissed": "Dismissed",
            "update_status": "Update status",
            "export_csv": "Export to CSV",
            "map_header": "Submission location map",
            "map_no_data": "No GPS data available to display on the map.",
            "lexicon_pending": "Terms pending approval",
            "approve": "Approve",
            "reject": "Reject",
            "no_pending_terms": "No pending terms.",
            "share_header": "Share your experience",
            "share_placeholder": "Write your experience here...",
            "share_button": "Submit",
            "share_success": "Submission received. Thank you.",
            "experiences_table_header": "Shared experiences list",
            "lexicon_header": "Coded terms lexicon",
            "lexicon_pending_note": "New terms must be approved by police before appearing publicly.",
            "add_term_label": "Add new term",
            "term_placeholder": "Code term",
            "meaning_placeholder": "Meaning / description",
            "add_term_button": "Add",
            "term_submitted": "Term submitted and awaiting approval.",
            "emergency_header": "Emergency contacts",
            "emergency_police": "Police (emergency): 112",
            "emergency_medical": "Medical emergency: 119",
            "emergency_drug_report": "Drug crime report (example number): 1301",
            "emergency_drug_hotline": "National drug & substance abuse hotline: 1342 (Ministry of Food and Drug Safety)",
            "emergency_official_portal": "Official portal: Drug-Free Korea (MFDS)",
            "public_upload_header": "Report a photo",
            "public_upload_label": "Upload photo",
            "public_upload_button": "Send to police",
            "public_upload_success": "Photo sent. Thank you for your report.",
            "text_report_header": "Report by text (no photo)",
            "text_report_category": "Report type",
            "text_report_desc": "Description",
            "text_report_button": "Submit",
            "text_report_success": "Report received. Thank you.",
            "category_options": ["Suspicious activity", "Suspicious location", "Suspected online sale", "Other"],
            "faq_header": "Frequently Asked Questions",
            "faq_content": [
                ("What is this app for?",
                 "Drug Intelligence Platform (DIP) is a platform allowing citizens to report suspicious activity, and helping "
                 "police review reports using visual similarity as a reference."),
                ("Is my identity disclosed?",
                 "Reports in the Public Zone are handled anonymously. No personal identifying information is required."),
                ("Can this app chemically identify drugs?",
                 "No. This app only compares visual similarity between photos; chemical identification requires lab analysis."),
            ],
        }
    }

    # -----------------------------------------------------
    # SIDEBAR : SÉLECTEUR DE LANGUE + LIEN DU SITE
    # -----------------------------------------------------
    if "lang" not in st.session_state:
        st.session_state.lang = "ko"

    st.sidebar.subheader(APP_NAME)
    st.sidebar.markdown(f"🔗 [{APP_NAME}]({APP_WEBSITE_URL})")
    st.sidebar.divider()

    st.sidebar.subheader("Language / 언어")
    lang_choice = st.sidebar.radio(
        "Language / 언어",
        options=["Korean", "English"],
        index=0 if st.session_state.lang == "ko" else 1,
        label_visibility="collapsed"
    )
    st.session_state.lang = "ko" if lang_choice == "Korean" else "en"
    t = TEXTS[st.session_state.lang]

    st.write(t["welcome"])

    # -----------------------------------------------------
    # TABS
    # -----------------------------------------------------
    tab_investigator, tab_public = st.tabs([t["tab_investigator"], t["tab_public"]])

    # =====================================================
    # ZONE ENQUÊTEUR
    # =====================================================
    with tab_investigator:
        if "police_authenticated" not in st.session_state:
            st.session_state.police_authenticated = False
        if "police_station" not in st.session_state:
            st.session_state.police_station = None
        if "police_code_logged" not in st.session_state:
            st.session_state.police_code_logged = None
        if "police_is_supervisor" not in st.session_state:
            st.session_state.police_is_supervisor = False

        if not st.session_state.police_authenticated:
            st.warning(t["restricted"])

            login_tab, register_tab = st.tabs([t["login_tab"], t["register_tab"]])

            with login_tab:
                st.subheader(t["login_header"])
                login_code = st.text_input(t["police_code"], key="login_code")
                login_pwd = st.text_input(t["password"], type="password", key="login_pwd")
                if st.button(t["login_button"]):
                    status, station, is_supervisor = check_police_login(login_code.strip(), login_pwd)
                    if status == "ok":
                        st.session_state.police_authenticated = True
                        st.session_state.police_station = station
                        st.session_state.police_code_logged = login_code.strip()
                        st.session_state.police_is_supervisor = is_supervisor
                        st.rerun()
                    elif status == "locked":
                        st.error(t["locked"])
                    elif status == "not_found":
                        st.error(t["not_found"])
                    else:
                        st.error(t["bad_credentials"])

            with register_tab:
                st.subheader(t["register_header"])
                st.caption(t["register_caption"])
                reg_code = st.text_input(t["police_code"], key="reg_code")
                # --- MODIFY HERE (KR) : list of police stations, e.g. 강남경찰서 ---
                reg_station = st.text_input(t["station"], key="reg_station")
                reg_pwd = st.text_input(t["password"], type="password", key="reg_pwd")
                reg_pwd_confirm = st.text_input(t["confirm_password"], type="password", key="reg_pwd_confirm")
                reg_admin_code = st.text_input(t["admin_code"], type="password", key="reg_admin_code")
                reg_is_supervisor = st.checkbox(t["supervisor_checkbox"], key="reg_is_supervisor")

                if st.button(t["create_account"]):
                    if reg_admin_code != ADMIN_MASTER_CODE:
                        st.error(t["invalid_admin_code"])
                    elif not reg_code.strip() or not reg_station.strip() or not reg_pwd:
                        st.error(t["fields_required"])
                    elif reg_pwd != reg_pwd_confirm:
                        st.error(t["passwords_mismatch"])
                    else:
                        success = register_police_officer(reg_code.strip(), reg_station.strip(), reg_pwd, reg_is_supervisor)
                        if success:
                            st.success(t["account_created"])
                        else:
                            st.error(t["code_taken"])

        else:
            badge = t["supervisor_badge"] if st.session_state.police_is_supervisor else t["officer_badge"]
            st.success(f"{t['logged_in_as']}: {st.session_state.police_code_logged} — {st.session_state.police_station} ({badge})")
            if st.button(t["logout"]):
                st.session_state.police_authenticated = False
                st.session_state.police_station = None
                st.session_state.police_code_logged = None
                st.session_state.police_is_supervisor = False
                st.rerun()

            st.info(f"📌 {t['official_resource']}: [{OFFICIAL_PORTAL_URL}]({OFFICIAL_PORTAL_URL})  |  ☎ 1342")

            is_supervisor = st.session_state.police_is_supervisor
            my_station = st.session_state.police_station

            sub_tabs = st.tabs([
                t["sub_tab_dashboard"], t["sub_tab_ref"], t["sub_tab_review"],
                t["sub_tab_map"], t["sub_tab_lexicon_mod"]
            ])
            sub_tab_dashboard, sub_tab_ref, sub_tab_review, sub_tab_map, sub_tab_lex_mod = sub_tabs

            # --- DASHBOARD ---
            with sub_tab_dashboard:
                st.subheader(t["dashboard_header"])
                conn = sqlite3.connect(DB_PATH)
                total_submissions = pd.read_sql_query("SELECT COUNT(*) as n FROM public_submissions", conn)["n"][0]
                total_references = pd.read_sql_query("SELECT COUNT(*) as n FROM reference_drugs", conn)["n"][0]
                total_officers = pd.read_sql_query("SELECT COUNT(*) as n FROM police_officers", conn)["n"][0]
                pending = pd.read_sql_query("SELECT COUNT(*) as n FROM public_submissions WHERE status = 'New'", conn)["n"][0]
                conn.close()

                col1, col2, col3, col4 = st.columns(4)
                col1.metric(t["metric_submissions"], total_submissions)
                col2.metric(t["metric_references"], total_references)
                col3.metric(t["metric_officers"], total_officers)
                col4.metric(t["metric_pending"], pending)

            # --- REFERENCE DATABASE ---
            with sub_tab_ref:
                st.subheader(t["add_reference"])
                ref_file = st.file_uploader(t["reference_photo"], type=["jpg", "jpeg", "png"], key="ref_upload")
                substance_name = st.text_input(t["substance_name"])
                description = st.text_area(t["description"])

                if ref_file is not None and st.button(t["save_reference"]):
                    ref_image = Image.open(ref_file)
                    ref_hash = compute_image_hash(ref_image)
                    insert_reference_drug(
                        substance_name, description, ref_hash, ref_file.name,
                        st.session_state.police_code_logged, my_station
                    )
                    st.success(t["reference_saved"])
                    st.rerun()

                st.divider()
                st.subheader(t["current_references"])
                conn = sqlite3.connect(DB_PATH)
                if is_supervisor:
                    df_ref = pd.read_sql_query(
                        "SELECT substance_name, description, station, added_by_police_code, created_at "
                        "FROM reference_drugs ORDER BY id DESC", conn
                    )
                else:
                    df_ref = pd.read_sql_query(
                        "SELECT substance_name, description, station, added_by_police_code, created_at "
                        "FROM reference_drugs WHERE station = ? ORDER BY id DESC", conn, params=(my_station,)
                    )
                conn.close()
                st.dataframe(df_ref, use_container_width=True)

            # --- REVIEW SUBMISSIONS ---
            with sub_tab_review:
                st.subheader(t["submissions_to_review"])
                conn = sqlite3.connect(DB_PATH)
                df_sub = pd.read_sql_query("SELECT * FROM public_submissions ORDER BY id DESC", conn)
                conn.close()

                if df_sub.empty:
                    st.info(t["no_submissions"])
                else:
                    st.download_button(
                        t["export_csv"],
                        df_sub.to_csv(index=False).encode("utf-8-sig"),
                        file_name="submissions_export.csv",
                        mime="text/csv"
                    )
                    for _, row in df_sub.iterrows():
                        with st.expander(f"#{row['id']} - {row['created_at']} - [{row['status']}]"):
                            if row["image_path"] and os.path.exists(row["image_path"]):
                                st.image(row["image_path"], width=250)
                            st.write(f"**{t['gps_label']}:** {row['gps_coords'] or '-'}")
                            st.write(f"**{t['date_photo']}:** {row['date_taken'] or '-'}")

                            matches = find_similar_matches(row["image_hash"])
                            if not matches.empty:
                                st.write(f"**{t['possible_matches']}:**")
                                st.dataframe(matches, use_container_width=True)
                                st.caption(t["similarity_warning"])
                            else:
                                st.info(t["no_match"])

                            new_status = st.selectbox(
                                t["status_label"],
                                [t["status_new"], t["status_in_progress"], t["status_resolved"], t["status_dismissed"]],
                                index=0,
                                key=f"status_{row['id']}"
                            )
                            if st.button(t["update_status"], key=f"update_{row['id']}"):
                                update_submission_status(row["id"], new_status, st.session_state.police_code_logged)
                                st.rerun()

            # --- MAP ---
            with sub_tab_map:
                st.subheader(t["map_header"])
                conn = sqlite3.connect(DB_PATH)
                df_sub = pd.read_sql_query("SELECT gps_coords FROM public_submissions", conn)
                conn.close()

                coords = []
                for gps in df_sub["gps_coords"]:
                    parsed = parse_gps_for_map(gps)
                    if parsed:
                        coords.append({"lat": parsed[0], "lon": parsed[1]})

                if coords:
                    st.map(pd.DataFrame(coords))
                else:
                    st.info(t["map_no_data"])

            # --- LEXICON MODERATION ---
            with sub_tab_lex_mod:
                st.subheader(t["lexicon_pending"])
                conn = sqlite3.connect(DB_PATH)
                df_pending = pd.read_sql_query("SELECT * FROM lexicon WHERE approved = 0", conn)
                conn.close()

                if df_pending.empty:
                    st.info(t["no_pending_terms"])
                else:
                    for _, row in df_pending.iterrows():
                        col1, col2, col3 = st.columns([3, 1, 1])
                        col1.write(f"**{row['code_term']}** — {row['meaning']}")
                        if col2.button(t["approve"], key=f"approve_{row['id']}"):
                            approve_lexicon_term(row["id"])
                            st.rerun()
                        if col3.button(t["reject"], key=f"reject_{row['id']}"):
                            delete_lexicon_term(row["id"])
                            st.rerun()

    # =====================================================
    # ZONE PUBLIQUE
    # =====================================================
    with tab_public:
        public_sub_tabs = st.tabs([
            t["public_upload_header"], t["text_report_header"],
            t["share_header"], t["lexicon_header"], t["faq_header"]
        ])
        pt_photo, pt_text, pt_share, pt_lexicon, pt_faq = public_sub_tabs

        # --- Photo report ---
        with pt_photo:
            st.subheader(t["public_upload_header"])
            public_file = st.file_uploader(t["public_upload_label"], type=["jpg", "jpeg", "png"], key="public_upload")
            if public_file is not None and st.button(t["public_upload_button"]):
                pub_image = Image.open(public_file)
                pub_hash = compute_image_hash(pub_image)
                date_taken, gps_str, _ = extract_exif(pub_image)
                image_path = save_image_to_disk(public_file)
                insert_public_submission(public_file.name, image_path, pub_hash, gps_str, date_taken)
                st.success(t["public_upload_success"])

        # --- Text-only report ---
        with pt_text:
            st.subheader(t["text_report_header"])
            category = st.selectbox(t["text_report_category"], t["category_options"])
            desc = st.text_area(t["text_report_desc"], key="text_report_desc")
            if st.button(t["text_report_button"], key="text_report_submit"):
                if desc.strip():
                    insert_text_report(category, desc.strip())
                    st.success(t["text_report_success"])

        # --- Share experience ---
        with pt_share:
            st.subheader(t["share_header"])
            experience_text = st.text_area(
                t["share_header"], placeholder=t["share_placeholder"], label_visibility="collapsed"
            )
            if st.button(t["share_button"]):
                if experience_text.strip():
                    insert_public_experience(experience_text.strip())
                    st.success(t["share_success"])
                    st.rerun()

            with st.expander(t["experiences_table_header"]):
                st.dataframe(fetch_all("public_experiences"), use_container_width=True)

        # --- Lexicon ---
        with pt_lexicon:
            st.subheader(t["lexicon_header"])
            st.caption(t["lexicon_pending_note"])
            conn = sqlite3.connect(DB_PATH)
            df_lex = pd.read_sql_query("SELECT code_term, meaning FROM lexicon WHERE approved = 1 ORDER BY id DESC", conn)
            conn.close()
            st.dataframe(df_lex, use_container_width=True)

            with st.form("add_term_form"):
                st.write(t["add_term_label"])
                new_term = st.text_input(t["term_placeholder"])
                new_meaning = st.text_input(t["meaning_placeholder"])
                submitted = st.form_submit_button(t["add_term_button"])
                if submitted and new_term.strip():
                    add_lexicon_term(new_term.strip(), new_meaning.strip())
                    st.success(t["term_submitted"])

            st.divider()
            # --- MODIFY HERE (KR) : verify/update real emergency numbers ---
            st.subheader(t["emergency_header"])
            st.write(t["emergency_police"])
            st.write(t["emergency_medical"])
            st.write(t["emergency_drug_report"])
            st.write(f"☎ {t['emergency_drug_hotline']}")
            st.markdown(f"🔗 [{t['emergency_official_portal']}]({OFFICIAL_PORTAL_URL})")

        # --- FAQ ---
        with pt_faq:
            st.subheader(t["faq_header"])
            for question, answer in t["faq_content"]:
                with st.expander(question):
                    st.write(answer)

except Exception as e:
    st.error("An error occurred in the application (see details below).")
    st.exception(e)
    