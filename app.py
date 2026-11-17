"""
IT-E6 Portal - Dang nhap + Dong gop de thi (Flask + SQLite + Flask-Login + Google Drive)

Cai dat:   pip install -r requirements.txt
Chay:      python app.py
Truy cap:  http://127.0.0.1:5000

Bien moi truong cho Google Drive (xem drive_storage.py):
    DRIVE_FOLDER_ID
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN   (OAuth, lay bang drive_auth.py)
    hoac GOOGLE_SERVICE_ACCOUNT_FILE                                  (Service Account + Shared Drive)

Cau truc thu muc:
    project/
    |-- app.py
    |-- drive_storage.py
    |-- drive_auth.py          (chay 1 lan de lay refresh token)
    |-- requirements.txt
    |-- database.db            (tu tao lan chay dau)
    |-- uploads/thumbs/        (chi con cache anh thumbnail; tep that nam tren Google Drive)
    |-- templates/
    |   |-- base_app.html
    |   |-- login.html
    |   |-- dashboard.html
    |   |-- upload.html
    |   `-- admin.html
    `-- static/
        |-- css/style.css
        |-- css/app.css
        `-- js/{login.js, app.js, upload.js}
"""
from functools import wraps
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import pymupdf  # PyMuPDF: doc PDF/anh, dem trang, tao thumbnail
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, stream_with_context,
                   url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from werkzeug.security import check_password_hash, generate_password_hash

import drive_storage

# ----------------------------------------------------------------------------
# CAU HINH
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATABASE = str(BASE_DIR / "database.db")
UPLOAD_DIR = BASE_DIR / "uploads"          # chi con dung cho tep cu (chua chuyen len Drive) + thumbnail
THUMB_DIR = UPLOAD_DIR / "thumbs"
UPLOAD_DIR.mkdir(exist_ok=True)
THUMB_DIR.mkdir(exist_ok=True)
# Thu muc tam: tep dang xu ly truoc khi day len Drive (luon duoc xoa sau do)
STAGING_DIR = Path(tempfile.gettempdir()) / "ite6_staging"
STAGING_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_MB = 128
MAX_THUMBS = 8                 # so trang xem truoc toi da hien thi
STAGED_TTL_HOURS = 24          # tep da tai len nhung chua luu de se bi don sau 24h
VN_TZ = timezone(timedelta(hours=7))

app = Flask(__name__)
# Khi deploy that: dat bien moi truong SECRET_KEY (chuoi ngau nhien dai)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Bat dong duoi khi chay HTTPS:
# app.config["SESSION_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB + 2) * 1024 * 1024

# Tai khoan mau (co the ghi de bang bien moi truong)
SEED_MSSV = os.environ.get("SEED_MSSV", "20235678")
SEED_EMAIL = os.environ.get("SEED_EMAIL", "20235678@sis.hust.edu.vn")
SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "ITE6@2025")
SEED_NAME = "Ban Học Tập IT-E6"

# Du lieu danh muc (chi dung de seed lan dau; sau do doc tu database)
SEED_SUBJECTS = ["Giải tích", "Đại số", "Vật lý ĐC", "Cấu trúc DL",
                 "Kỹ thuật LTr", "Kiến trúc MT", "Mạng MT", "Anh văn BKHN"]
SEED_SOURCES = ["Viện Toán Tin - Đại học Bách Khoa Hà Nội",
                "Khoa Khoa Học Máy Tính - SoICT HUST",
                "Khoa Kỹ Thuật Máy Tính - SoICT HUST",
                "Viện Vật Lý Kỹ Thuật - HUST",
                "Khoa Ngoại Ngữ - ĐHBK Hà Nội"]

COHORTS = [("K68", "K68 (Năm 1 & 2)"), ("K67", "K67"), ("K66", "K66")]
GRADING_SCALES = [("10", "Thang 10"), ("4", "Thang 4"), ("100", "Thang 100")]
STATUSES = {"draft": "Bản nháp", "pending": "Chờ duyệt", "published": "Đã công khai"}
ROLE_LABELS = {"admin": "Quản trị / Ban học tập", "student": "Sinh viên IT-E6"}

ALLOWED_EXT = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".zip": "application/zip",
}

# Hash gia de so sanh khi khong tim thay user (chong do tai khoan qua thoi gian phan hoi)
DUMMY_HASH = generate_password_hash("dummy-password-not-used")


# ----------------------------------------------------------------------------
# TIEN ICH CHUNG
# ----------------------------------------------------------------------------
def now_vn():
    return datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


app.jinja_env.filters["filesize"] = human_size
app.jinja_env.globals["status_labels"] = STATUSES


def jerr(msg, status=400, fields=None):
    body = {"ok": False, "error": msg}
    if fields:
        body["fields"] = fields
    return jsonify(body), status


def is_api():
    return request.path.startswith("/api/")


# ----------------------------------------------------------------------------
# DATABASE (SQLite)
# ----------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS "User" (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mssv          TEXT UNIQUE,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT,
    role          TEXT NOT NULL DEFAULT 'student',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Subject (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS Source (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS ExamFile (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uploader_id   INTEGER NOT NULL,
    original_name TEXT NOT NULL,
    stored_name   TEXT UNIQUE NOT NULL,
    ext           TEXT NOT NULL,
    mime          TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    page_count    INTEGER,
    width         INTEGER,
    height        INTEGER,
    text_layer    INTEGER,
    zip_entries   INTEGER,
    zip_preview   TEXT,
    drive_file_id TEXT,                 -- ID tep tren Google Drive (NULL = tep cu nam tren dia)
    exam_id       INTEGER,              -- NULL = tep dang cho (chua luu de thi)
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_examfile_sha ON ExamFile(sha256);

CREATE TABLE IF NOT EXISTS Exam (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_id      TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    subject_id    INTEGER NOT NULL,
    cohort        TEXT NOT NULL,
    source_id     INTEGER NOT NULL,
    duration_min  INTEGER NOT NULL,
    num_questions INTEGER NOT NULL,
    grading_scale TEXT NOT NULL,
    nb_th         INTEGER NOT NULL DEFAULT 0,
    vd            INTEGER NOT NULL DEFAULT 0,
    vdc           INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    uploader_id   INTEGER NOT NULL,
    file_id       INTEGER UNIQUE NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def cleanup_stale_uploads(db):
    """Xoa cac tep da tai len nhung khong duoc luu thanh de thi sau STAGED_TTL_HOURS gio."""
    limit = (datetime.now(VN_TZ) - timedelta(hours=STAGED_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.execute(
        "SELECT id, stored_name, drive_file_id FROM ExamFile WHERE exam_id IS NULL AND created_at < ?",
        (limit,)
    ).fetchall()
    for r in rows:
        remove_file_assets(r["id"], r["stored_name"], r["drive_file_id"])
        db.execute("DELETE FROM ExamFile WHERE id = ?", (r["id"],))
    if rows:
        db.commit()


def remove_file_assets(file_id, stored_name, drive_file_id=None):
    """Xoa tep tren Drive (neu co), tep cu tren dia (neu con) va cac thumbnail cache."""
    if drive_file_id:
        try:
            drive_storage.delete_file(drive_file_id)
        except Exception:
            app.logger.exception("Khong xoa duoc tep tren Drive %s", drive_file_id)
    try:
        (UPLOAD_DIR / stored_name).unlink(missing_ok=True)
        for t in THUMB_DIR.glob(f"{file_id}_*.png"):
            t.unlink(missing_ok=True)
    except OSError:
        app.logger.exception("Khong xoa duoc tep %s", stored_name)


def init_db():
    """Tao bang, migrate cot moi, seed danh muc va tai khoan mau neu chua co."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)

    # Migration: DB cu chua co cot drive_file_id
    cols = {r["name"] for r in db.execute("PRAGMA table_info(ExamFile)")}
    if "drive_file_id" not in cols:
        db.execute("ALTER TABLE ExamFile ADD COLUMN drive_file_id TEXT")

    if not db.execute('SELECT 1 FROM "User" WHERE email = ?', (SEED_EMAIL,)).fetchone():
        db.execute(
            'INSERT INTO "User" (mssv, email, password_hash, full_name, role) VALUES (?,?,?,?,?)',
            (SEED_MSSV, SEED_EMAIL, generate_password_hash(SEED_PASSWORD), SEED_NAME, "admin"),
        )
        print("=" * 60)
        print("[SEED] Da tao tai khoan mau:")
        print(f"       MSSV/Email : {SEED_MSSV}  hoac  {SEED_EMAIL}")
        print(f"       Mat khau   : {SEED_PASSWORD}")
        print("=" * 60)
    if not db.execute("SELECT 1 FROM Subject").fetchone():
        db.executemany("INSERT INTO Subject (name) VALUES (?)", [(s,) for s in SEED_SUBJECTS])
    if not db.execute("SELECT 1 FROM Source").fetchone():
        db.executemany("INSERT INTO Source (name) VALUES (?)", [(s,) for s in SEED_SOURCES])
    db.execute("UPDATE Exam SET status = 'published' WHERE status != 'published'")
    db.commit()
    cleanup_stale_uploads(db)
    db.close()


# ----------------------------------------------------------------------------
# FLASK-LOGIN
# ----------------------------------------------------------------------------
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.mssv = row["mssv"]
        self.email = row["email"]
        self.full_name = row["full_name"]
        self.role = row["role"]
        self._active = bool(row["is_active"])

    @property
    def is_active(self):
        return self._active

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def display_name(self):
        return self.full_name or self.email

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute('SELECT * FROM "User" WHERE id = ?', (user_id,)).fetchone()
    user = User(row) if row else None
    return user if user and user.is_active else None


@login_manager.unauthorized_handler
def unauthorized():
    if is_api():
        return jerr("Phiên đăng nhập đã hết hạn. Hãy tải lại trang và đăng nhập lại.", 401)
    flash(login_manager.login_message, "info")
    if request.method == "GET":
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------
# CSRF (don gian, khong can them thu vien)
# ----------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


def verify_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not hmac.compare_digest(sent, session.get("_csrf", "")):
        abort(400, description="CSRF token không hợp lệ. Hãy tải lại trang.")


# ----------------------------------------------------------------------------
# BAO MAT: header + xu ly loi
# ----------------------------------------------------------------------------
@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(400)
def handle_400(e):
    if is_api():
        return jerr(getattr(e, "description", "Yêu cầu không hợp lệ."), 400)
    return e


@app.errorhandler(413)
def handle_413(e):
    msg = f"Tệp vượt quá giới hạn {MAX_UPLOAD_MB} MB mỗi phiên tải."
    if is_api():
        return jerr(msg, 413)
    return e


def is_safe_next(target):
    """Chi cho phep redirect noi bo (chong open redirect)."""
    if not target:
        return False
    parsed = urlparse(target)
    return (not parsed.netloc and not parsed.scheme
            and target.startswith("/") and not target.startswith("//"))


IDENTIFIER_RE = re.compile(r"^(\d{8}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


# ----------------------------------------------------------------------------
# ROUTES: DANG NHAP / DANG XUAT
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    identifier = ""
    if request.method == "POST":
        verify_csrf()
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        error = None

        if not identifier or not password:
            error = "Vui lòng nhập đầy đủ MSSV/Email và mật khẩu."
        elif not IDENTIFIER_RE.match(identifier):
            error = "MSSV (8 chữ số) hoặc Email Bách Khoa không đúng định dạng."
        else:
            row = get_db().execute(
                'SELECT * FROM "User" WHERE mssv = ? OR email = ?', (identifier, identifier)
            ).fetchone()
            ok = check_password_hash(row["password_hash"] if row else DUMMY_HASH, password)
            if not row or not ok:
                error = "Sai MSSV/Email hoặc mật khẩu. Vui lòng thử lại."
            elif not row["is_active"]:
                error = "Tài khoản đã bị khóa. Liên hệ Ban cán sự lớp IT-E6."
            else:
                session.clear()  # chong session fixation
                login_user(User(row), remember=remember, duration=timedelta(days=30))
                next_url = request.args.get("next")
                return redirect(next_url if is_safe_next(next_url) else url_for("dashboard"))

        flash(error, "error")

    return render_template("login.html", identifier=identifier)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    verify_csrf()
    logout_user()
    session.clear()
    flash("Bạn đã đăng xuất khỏi cổng IT-E6.", "info")
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------
# ROUTES: TRANG CHU (danh sach de da dong gop)
# ----------------------------------------------------------------------------
DASH_PER_PAGE = 12


def page_window(page, pages):
    """Danh sach so trang can hien thi; None = dau '...'."""
    if pages <= 7:
        return list(range(1, pages + 1))
    nums = {1, pages, page - 1, page, page + 1}
    if page <= 3:
        nums |= {2, 3, 4}
    if page >= pages - 2:
        nums |= {pages - 3, pages - 2, pages - 1}
    out, prev = [], 0
    for n in sorted(x for x in nums if 1 <= x <= pages):
        if n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    # Kho chung: ai cung xem duoc tat ca de
    scope_sql = "1 = 1"
    scope_args = []

    # ---- Bo loc tu query string ----
    q = re.sub(r"\s+", " ", request.args.get("q", "")).strip()[:100]
    subject_id = to_int(request.args.get("subject"), 1, 2 ** 31)
    status = request.args.get("status", "")
    cohort = request.args.get("cohort", "")
    if status not in STATUSES:
        status = ""
    if cohort not in dict(COHORTS):
        cohort = ""
    filters = {"q": q, "subject": subject_id, "status": status, "cohort": cohort}

    where, args = [scope_sql], list(scope_args)
    if subject_id:
        where.append("e.subject_id = ?")
        args.append(subject_id)
    if status:
        where.append("e.status = ?")
        args.append(status)
    if cohort:
        where.append("e.cohort = ?")
        args.append(cohort)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(e.title LIKE ? ESCAPE '\\' OR e.vault_id LIKE ? ESCAPE '\\' "
                     "OR s.name LIKE ? ESCAPE '\\' OR src.name LIKE ? ESCAPE '\\')")
        args += [like] * 4
    where_sql = " AND ".join(where)

    from_sql = """FROM Exam e
        JOIN Subject s   ON s.id   = e.subject_id
        JOIN Source  src ON src.id = e.source_id
        JOIN ExamFile f  ON f.id   = e.file_id"""

    # ---- Phan trang ----
    total = db.execute(f"SELECT COUNT(*) {from_sql} WHERE {where_sql}", args).fetchone()[0]
    pages = max(1, -(-total // DASH_PER_PAGE))
    page = min(max(to_int(request.args.get("page"), 1, 10 ** 6) or 1, 1), pages)
    offset = (page - 1) * DASH_PER_PAGE

    exams = db.execute(
        f"""SELECT e.id, e.vault_id, e.title, e.cohort, e.status, e.num_questions,
                   e.duration_min, e.created_at, s.name AS subject_name,
                   src.name AS source_name, f.id AS file_id, f.ext
            {from_sql} WHERE {where_sql}
            ORDER BY e.id DESC LIMIT ? OFFSET ?""",
        args + [DASH_PER_PAGE, offset],
    ).fetchall()

    # ---- So lieu tong quan (khong phu thuoc bo loc) ----
    week_ago = (datetime.now(VN_TZ) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    stats = db.execute(
        f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(e.created_at >= ?), 0) AS new_week,
                   COALESCE(SUM(e.status = 'pending'), 0) AS pending
            FROM Exam e WHERE {scope_sql}""",
        [week_ago] + scope_args,
    ).fetchone()

    subjects = db.execute(
        f"""SELECT s.id, s.name, COUNT(e.id) AS n
            FROM Subject s LEFT JOIN Exam e ON e.subject_id = s.id AND {scope_sql}
            GROUP BY s.id ORDER BY s.id""",
        scope_args,
    ).fetchall()

    def link(**over):
        d = dict(filters)
        d.update(over)
        d = {k: v for k, v in d.items() if v not in (None, "")}
        return url_for("dashboard", **d)

    return render_template(
        "dashboard.html",
        exams=exams, stats=stats, subjects=subjects, link=link,
        q=q, subject_id=subject_id, status=status, cohort=cohort,
        has_filter=bool(q or subject_id or status or cohort),
        cohorts=COHORTS, total=total, page=page, pages=pages,
        pages_nav=page_window(page, pages),
        first=offset + 1 if total else 0, last=offset + len(exams),
    )


# ----------------------------------------------------------------------------
# ROUTES: TRANG DONG GOP DE THI
# ----------------------------------------------------------------------------
def new_vault_id(db):
    year = datetime.now(VN_TZ).year
    while True:
        vid = f"HUST-{year}-{secrets.token_hex(3).upper()}"
        if not db.execute("SELECT 1 FROM Exam WHERE vault_id = ?", (vid,)).fetchone():
            return vid


@app.route("/contribute")
@login_required
def contribute():
    db = get_db()
    # Tep nam tren Google Drive nen dung luong cuc bo khong con quan trong;
    # van hien thi o dia (noi dat thumbnail cache + thu muc tam) cho form hien tai.
    total, _used, free = shutil.disk_usage(UPLOAD_DIR)
    storage = {
        "free_pct": round(free / total * 100) if total else 0,
        "free_h": human_size(free),
    }
    return render_template(
        "upload.html",
        subjects=db.execute("SELECT id, name FROM Subject ORDER BY id").fetchall(),
        sources=db.execute("SELECT id, name FROM Source ORDER BY id").fetchall(),
        cohorts=COHORTS,
        scales=GRADING_SCALES,
        vault_id=new_vault_id(db),
        storage=storage,
        max_mb=MAX_UPLOAD_MB,
        max_bytes=MAX_UPLOAD_MB * 1024 * 1024,
    )


@app.route("/api/vault-id")
@login_required
def api_vault_id():
    return jsonify({"ok": True, "vault_id": new_vault_id(get_db())})


# ---- Xu ly tep -------------------------------------------------------------
class UploadError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg = msg
        self.status = status


def clean_filename(name):
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "tep-khong-ten"


def sniff_ok(head, ext):
    """Doi chieu noi dung that cua tep voi phan mo rong (chong doi duoi tep)."""
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".zip":
        return head.startswith(b"PK\x03\x04")
    return False


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_file(path, ext):
    """Doc thong tin that tu tep. Nem UploadError neu tep hong / khong an toan."""
    meta = {"page_count": None, "width": None, "height": None,
            "text_layer": None, "zip_entries": None, "zip_preview": None}
    if ext == ".pdf":
        try:
            doc = pymupdf.open(str(path))
        except Exception:
            raise UploadError("Không đọc được tệp PDF (tệp có thể bị hỏng).")
        try:
            if doc.needs_pass:
                raise UploadError("PDF đang được đặt mật khẩu. Hãy gỡ mật khẩu rồi tải lại.")
            if doc.page_count < 1:
                raise UploadError("Tệp PDF không có trang nào.")
            meta["page_count"] = doc.page_count
            sample = min(3, doc.page_count)
            chars = sum(len(doc[i].get_text().strip()) for i in range(sample))
            meta["text_layer"] = 1 if chars > 50 else 0
        finally:
            doc.close()
    elif ext in (".jpg", ".jpeg", ".png"):
        try:
            pix = pymupdf.Pixmap(str(path))
        except Exception:
            raise UploadError("Không đọc được tệp ảnh (tệp có thể bị hỏng).")
        meta.update(page_count=1, width=pix.width, height=pix.height)
    elif ext == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if not infos:
                    raise UploadError("Gói ZIP rỗng.")
                if len(infos) > 2000:
                    raise UploadError("Gói ZIP chứa quá nhiều tệp (tối đa 2000).")
                if sum(i.file_size for i in infos) > 1024 ** 3:
                    raise UploadError("Dung lượng giải nén của gói ZIP vượt quá 1 GB.")
                meta["zip_entries"] = len(infos)
                meta["zip_preview"] = "\n".join(i.filename for i in infos[:MAX_THUMBS])
        except zipfile.BadZipFile:
            raise UploadError("Gói ZIP bị hỏng.")
    return meta


def file_kind(ext):
    return "pdf" if ext == ".pdf" else "zip" if ext == ".zip" else "image"


def file_payload(row):
    kind = file_kind(row["ext"])
    thumbs = []
    if kind in ("pdf", "image"):
        thumbs = [url_for("file_thumb", file_id=row["id"], page=i)
                  for i in range(1, min(row["page_count"] or 1, MAX_THUMBS) + 1)]
    return {
        "id": row["id"],
        "name": row["original_name"],
        "ext": row["ext"].lstrip(".").upper(),
        "kind": kind,
        "size": row["size_bytes"],
        "size_h": human_size(row["size_bytes"]),
        "pages": row["page_count"],
        "width": row["width"],
        "height": row["height"],
        "text_layer": None if row["text_layer"] is None else bool(row["text_layer"]),
        "zip_entries": row["zip_entries"],
        "zip_names": (row["zip_preview"] or "").split("\n") if row["zip_preview"] else [],
        "sha_short": row["sha256"][:12].upper(),
        "thumbs": thumbs,
        "preview_url": url_for("file_preview", file_id=row["id"]) if kind != "zip" else None,
        "download_url": url_for("file_download", file_id=row["id"]),
    }


def build_thumbs(src, file_id, page_count):
    """Tao thumbnail (toi da MAX_THUMBS trang) tu tep cuc bo `src` vao THUMB_DIR."""
    doc = pymupdf.open(str(src))
    try:
        for p in range(1, min(page_count or 1, MAX_THUMBS) + 1):
            pg = doc[p - 1]
            zoom = min(2.0, 420 / max(pg.rect.width, 1))
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pix.save(str(THUMB_DIR / f"{file_id}_{p}.png"))
    finally:
        doc.close()


def _safe_drive_delete(drive_id):
    try:
        drive_storage.delete_file(drive_id)
    except Exception:
        app.logger.exception("Khong don duoc tep tren Drive %s", drive_id)


@app.route("/api/uploads", methods=["POST"])
@login_required
def api_upload():
    verify_csrf()
    db = get_db()
    cleanup_stale_uploads(db)

    f = request.files.get("file")
    if not f or not f.filename:
        return jerr("Chưa chọn tệp nào.", 400)

    original = clean_filename(f.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_EXT:
        return jerr("Định dạng không được hỗ trợ. Chỉ nhận PDF, JPG, PNG hoặc ZIP.", 415)

    stored = uuid.uuid4().hex + ext
    path = STAGING_DIR / stored          # chi la ban tam, se bi xoa o khoi finally
    drive_id = None
    try:
        f.save(path)
        size = path.stat().st_size
        if size == 0:
            raise UploadError("Tệp rỗng (0 byte).")
        with open(path, "rb") as fh:
            head = fh.read(16)
        if not sniff_ok(head, ext):
            raise UploadError("Nội dung tệp không khớp với phần mở rộng. Tệp bị từ chối.", 415)

        sha = file_sha256(path)
        dup = db.execute(
            """SELECT e.vault_id, e.title FROM ExamFile f JOIN Exam e ON e.id = f.exam_id
               WHERE f.sha256 = ? LIMIT 1""", (sha,)
        ).fetchone()
        if dup:
            raise UploadError(f"Tệp này đã có trong kho (mã {dup['vault_id']}: {dup['title']}).", 409)

        meta = inspect_file(path, ext)

        # Day len Google Drive (sau khi tep da qua moi buoc kiem tra)
        drive_id = drive_storage.upload_file(path, stored, ALLOWED_EXT[ext])

        cur = db.execute(
            """INSERT INTO ExamFile (uploader_id, original_name, stored_name, ext, mime, size_bytes,
                   sha256, page_count, width, height, text_layer, zip_entries, zip_preview,
                   created_at, drive_file_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (current_user.id, original, stored, ext, ALLOWED_EXT[ext], size, sha,
             meta["page_count"], meta["width"], meta["height"], meta["text_layer"],
             meta["zip_entries"], meta["zip_preview"], now_vn(), drive_id),
        )
        db.commit()
        file_id = cur.lastrowid

        # Tao thumbnail ngay khi tep con o may (khong bat buoc thanh cong)
        if file_kind(ext) != "zip":
            try:
                build_thumbs(path, file_id, meta["page_count"])
            except Exception:
                app.logger.exception("Khong tao duoc thumbnail luc upload")
    except UploadError as e:
        if drive_id:
            _safe_drive_delete(drive_id)
        return jerr(e.msg, e.status)
    except Exception:
        if drive_id:
            _safe_drive_delete(drive_id)
        app.logger.exception("Loi khi xu ly tep tai len")
        return jerr("Không thể xử lý tệp này. Vui lòng thử tệp khác.", 500)
    finally:
        path.unlink(missing_ok=True)

    row = db.execute("SELECT * FROM ExamFile WHERE id = ?", (file_id,)).fetchone()
    return jsonify({"ok": True, "file": file_payload(row)}), 201


@app.route("/api/uploads/<int:file_id>", methods=["DELETE"])
@login_required
def api_upload_delete(file_id):
    verify_csrf()
    db = get_db()
    row = db.execute(
        "SELECT * FROM ExamFile WHERE id = ? AND uploader_id = ?", (file_id, current_user.id)
    ).fetchone()
    if not row:
        return jerr("Không tìm thấy tệp.", 404)
    if row["exam_id"] is not None:
        return jerr("Tệp đã thuộc về một đề thi trong kho, không thể xóa tại đây.", 409)
    remove_file_assets(row["id"], row["stored_name"], row["drive_file_id"])
    db.execute("DELETE FROM ExamFile WHERE id = ?", (file_id,))
    db.commit()
    return jsonify({"ok": True})


# ---- Phuc vu tep (co kiem tra quyen) ----------------------------------------
def get_file_for_user(file_id):
    row = get_db().execute(
        """SELECT f.*, e.status AS exam_status FROM ExamFile f
           LEFT JOIN Exam e ON e.id = f.exam_id WHERE f.id = ?""", (file_id,)
    ).fetchone()
    if not row:
        abort(404)
    allowed = (row["uploader_id"] == current_user.id or current_user.is_admin
               or row["exam_status"] == "published")
    if not allowed:
        abort(404)
    return row


def serve_file(row, as_attachment):
    """Phuc vu tep: tu Google Drive (proxy co ho tro Range) hoac tu dia neu la tep cu."""
    if not row["drive_file_id"]:
        return send_from_directory(
            UPLOAD_DIR, row["stored_name"], mimetype=row["mime"],
            as_attachment=as_attachment,
            download_name=row["original_name"] if as_attachment else None,
            max_age=0)

    try:
        up = drive_storage.open_stream(row["drive_file_id"], request.headers.get("Range"))
    except Exception:
        app.logger.exception("Khong ket noi duoc Google Drive")
        abort(502)
    if up.status_code == 404:
        up.close()
        abort(404)
    if up.status_code >= 400 and up.status_code != 416:
        app.logger.error("Drive tra ve %s: %s", up.status_code, up.text[:200])
        up.close()
        abort(502)

    def generate():
        try:
            for chunk in up.iter_content(256 * 1024):
                yield chunk
        finally:
            up.close()

    resp = Response(stream_with_context(generate()), status=up.status_code, mimetype=row["mime"])
    for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if h in up.headers:
            resp.headers[h] = up.headers[h]
    if as_attachment:
        resp.headers["Content-Disposition"] = (
            "attachment; filename*=UTF-8''" + quote(row["original_name"]))
    else:
        resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"] = "private, max-age=0"
    return resp


@app.route("/files/<int:file_id>/preview")
@login_required
def file_preview(file_id):
    row = get_file_for_user(file_id)
    if file_kind(row["ext"]) == "zip":
        abort(404)
    return serve_file(row, as_attachment=False)


@app.route("/files/<int:file_id>/download")
@login_required
def file_download(file_id):
    return serve_file(get_file_for_user(file_id), as_attachment=True)


@app.route("/files/<int:file_id>/thumb/<int:page>")
@login_required
def file_thumb(file_id, page):
    row = get_file_for_user(file_id)
    kind = file_kind(row["ext"])
    if kind == "zip" or page < 1 or page > min(row["page_count"] or 1, MAX_THUMBS):
        abort(404)
    cache = THUMB_DIR / f"{row['id']}_{page}.png"
    if not cache.exists():
        # Cache bi mat: tai tep tu Drive ve 1 lan, tao lai toan bo thumbnail roi xoa ban tam
        tmp = None
        try:
            if row["drive_file_id"]:
                tmp = STAGING_DIR / f"dl_{uuid.uuid4().hex}{row['ext']}"
                drive_storage.download_to(row["drive_file_id"], tmp)
                src = tmp
            else:
                src = UPLOAD_DIR / row["stored_name"]
            build_thumbs(src, row["id"], row["page_count"])
        except Exception:
            app.logger.exception("Khong tao duoc thumbnail")
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
        if not cache.exists():
            abort(404)
    resp = send_file(cache, mimetype="image/png", max_age=3600)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---- Luu de thi -------------------------------------------------------------
def to_int(value, lo, hi):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


VAULT_RE = re.compile(r"^HUST-\d{4}-[A-Z0-9]{4,10}$")


@app.route("/api/exams", methods=["POST"])
@login_required
def api_create_exam():
    verify_csrf()
    db = get_db()
    form = request.form
    errors = {}

    # Tep
    file_id = to_int(form.get("file_id"), 1, 2 ** 31)
    frow = None
    if file_id:
        frow = db.execute(
            "SELECT * FROM ExamFile WHERE id = ? AND uploader_id = ? AND exam_id IS NULL",
            (file_id, current_user.id),
        ).fetchone()
    if not frow:
        errors["file"] = "Chưa có tệp đề thi hợp lệ. Hãy tải tệp lên trước."

    # Tieu de
    title = re.sub(r"\s+", " ", form.get("title", "")).strip()
    if not 5 <= len(title) <= 200:
        errors["title"] = "Tiêu đề cần từ 5 đến 200 ký tự."

    # Danh muc
    subject_id = to_int(form.get("subject_id"), 1, 2 ** 31)
    if not subject_id or not db.execute("SELECT 1 FROM Subject WHERE id = ?", (subject_id,)).fetchone():
        errors["subject_id"] = "Hãy chọn môn học."
    source_id = to_int(form.get("source_id"), 1, 2 ** 31)
    if not source_id or not db.execute("SELECT 1 FROM Source WHERE id = ?", (source_id,)).fetchone():
        errors["source_id"] = "Hãy chọn nguồn đề thi."
    cohort = form.get("cohort", "")
    if cohort not in dict(COHORTS):
        errors["cohort"] = "Hãy chọn khóa áp dụng."

    # Cau truc de
    duration = to_int(form.get("duration_min"), 5, 600)
    if duration is None:
        errors["duration_min"] = "Thời lượng từ 5 đến 600 phút."
    num_q = to_int(form.get("num_questions"), 1, 500)
    if num_q is None:
        errors["num_questions"] = "Số câu từ 1 đến 500."
    scale = form.get("grading_scale", "")
    if scale not in dict(GRADING_SCALES):
        errors["grading_scale"] = "Thang điểm không hợp lệ."

    # Ma tran do kho (khong bat buoc; neu co thi tong phai khop so cau)
    parts = {}
    for key in ("nb_th", "vd", "vdc"):
        raw = form.get(key, "").strip()
        val = 0 if raw == "" else to_int(raw, 0, 500)
        if val is None:
            errors[key] = "Nhập số nguyên từ 0 đến 500."
        parts[key] = val or 0
    matrix_sum = sum(parts.values())
    if "num_questions" not in errors and not any(k in errors for k in parts):
        if matrix_sum > 0 and matrix_sum != num_q:
            errors["matrix"] = f"Tổng ma trận ({matrix_sum}) phải bằng số câu hỏi ({num_q})."

    # Tai lieu la kho chung: dong gop xong ai cung xem duoc
    status = "published"

    if errors:
        return jerr("Vui lòng kiểm tra lại các trường được đánh dấu.", 422, errors)

    # Ma kho: dung ma client gui neu hop le va chua bi chiem
    vault_id = form.get("vault_id", "").strip().upper()
    if not VAULT_RE.match(vault_id) or db.execute(
            "SELECT 1 FROM Exam WHERE vault_id = ?", (vault_id,)).fetchone():
        vault_id = new_vault_id(db)

    try:
        cur = db.execute(
            """INSERT INTO Exam (vault_id, title, subject_id, cohort, source_id, duration_min,
                   num_questions, grading_scale, nb_th, vd, vdc, status, uploader_id, file_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vault_id, title, subject_id, cohort, source_id, duration, num_q, scale,
             parts["nb_th"], parts["vd"], parts["vdc"], status, current_user.id,
             frow["id"], now_vn()),
        )
        db.execute("UPDATE ExamFile SET exam_id = ? WHERE id = ?", (cur.lastrowid, frow["id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jerr("Không thể lưu đề thi (mã kho hoặc tệp đã được sử dụng). Hãy thử lại.", 409)

    flash(f"Đã đưa đề thi {vault_id} vào kho chung IT-E6. Cả lớp đều xem được.", "info")
    return jsonify({"ok": True, "vault_id": vault_id, "redirect": url_for("dashboard")}), 201

# ----------------------------------------------------------------------------
# QUAN TRI (admin)
# ----------------------------------------------------------------------------
ADMIN_PER_PAGE = 15
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            if is_api():
                return jerr("Bạn không có quyền thực hiện thao tác này.", 403)
            abort(403)
        return fn(*args, **kwargs)
    return login_required(wrapper)


def like_pat(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    tab = request.args.get("tab", "users")
    if tab not in ("users", "exams"):
        tab = "users"
    q = re.sub(r"\s+", " ", request.args.get("q", "")).strip()[:100]
    role = request.args.get("role", "")
    role = role if role in ROLE_LABELS else ""
    cohort = request.args.get("cohort", "")
    cohort = cohort if cohort in dict(COHORTS) else ""
    subject_id = to_int(request.args.get("subject"), 1, 2 ** 31)

    def link(**over):
        d = {"tab": tab, "q": q, "role": role, "subject": subject_id, "cohort": cohort}
        d.update(over)
        return url_for("admin", **{k: v for k, v in d.items() if v not in (None, "")})

    where, args = [], []
    if tab == "users":
        from_sql = 'FROM "User" u'
        if role:
            where.append("u.role = ?")
            args.append(role)
        if q:
            where.append("(u.email LIKE ? ESCAPE '\\' OR u.mssv LIKE ? ESCAPE '\\' "
                         "OR u.full_name LIKE ? ESCAPE '\\')")
            args += [like_pat(q)] * 3
    else:
        from_sql = """FROM Exam e
            JOIN Subject s   ON s.id   = e.subject_id
            JOIN Source  src ON src.id = e.source_id
            JOIN ExamFile f  ON f.id   = e.file_id
            JOIN "User"  up  ON up.id  = e.uploader_id"""
        if subject_id:
            where.append("e.subject_id = ?")
            args.append(subject_id)
        if cohort:
            where.append("e.cohort = ?")
            args.append(cohort)
        if q:
            where.append("(e.title LIKE ? ESCAPE '\\' OR e.vault_id LIKE ? ESCAPE '\\' "
                         "OR up.email LIKE ? ESCAPE '\\' OR up.full_name LIKE ? ESCAPE '\\')")
            args += [like_pat(q)] * 4
    where_sql = " AND ".join(where) or "1 = 1"

    total = db.execute(f"SELECT COUNT(*) {from_sql} WHERE {where_sql}", args).fetchone()[0]
    pages = max(1, -(-total // ADMIN_PER_PAGE))
    page = min(max(to_int(request.args.get("page"), 1, 10 ** 6) or 1, 1), pages)
    offset = (page - 1) * ADMIN_PER_PAGE

    if tab == "users":
        rows = db.execute(
            f"""SELECT u.*, (SELECT COUNT(*) FROM Exam x WHERE x.uploader_id = u.id) AS n_exams
                {from_sql} WHERE {where_sql} ORDER BY u.id DESC LIMIT ? OFFSET ?""",
            args + [ADMIN_PER_PAGE, offset]).fetchall()
    else:
        rows = db.execute(
            f"""SELECT e.id, e.vault_id, e.title, e.cohort, e.subject_id, e.source_id,
                       e.duration_min, e.num_questions, e.grading_scale, e.nb_th, e.vd, e.vdc,
                       e.created_at, s.name AS subject_name, src.name AS source_name,
                       f.id AS file_id, f.ext, f.size_bytes,
                       COALESCE(up.full_name, up.email) AS uploader_name
                {from_sql} WHERE {where_sql} ORDER BY e.id DESC LIMIT ? OFFSET ?""",
            args + [ADMIN_PER_PAGE, offset]).fetchall()

    ustats = db.execute("""SELECT COUNT(*) AS total,
                                  COALESCE(SUM(role = 'admin'), 0) AS admins,
                                  COALESCE(SUM(is_active = 0), 0) AS locked
                           FROM "User" """).fetchone()
    estats = db.execute("""SELECT COUNT(*) AS total, COALESCE(SUM(f.size_bytes), 0) AS size
                           FROM Exam e JOIN ExamFile f ON f.id = e.file_id""").fetchone()

    return render_template(
        "admin.html", tab=tab, rows=rows, q=q, role=role, cohort=cohort, subject_id=subject_id,
        link=link, total=total, page=page, pages=pages, pages_nav=page_window(page, pages),
        first=offset + 1 if total else 0, last=offset + len(rows),
        ustats=ustats, estats=estats, role_labels=ROLE_LABELS,
        cohorts=COHORTS, scales=GRADING_SCALES,
        subjects=db.execute("SELECT id, name FROM Subject ORDER BY id").fetchall(),
        sources=db.execute("SELECT id, name FROM Source ORDER BY id").fetchall(),
    )


# ---- Tai khoan --------------------------------------------------------------
def other_active_admins(db, exclude_id):
    return db.execute(
        'SELECT COUNT(*) FROM "User" WHERE role = \'admin\' AND is_active = 1 AND id != ?',
        (exclude_id,)).fetchone()[0]


def parse_user(data, db, user_id=None, creating=False):
    errors = {}
    email = str(data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 120:
        errors["email"] = "Email không đúng định dạng."
    mssv = str(data.get("mssv") or "").strip() or None
    if mssv and not re.fullmatch(r"\d{8}", mssv):
        errors["mssv"] = "MSSV gồm đúng 8 chữ số."
    full_name = re.sub(r"\s+", " ", str(data.get("full_name") or "")).strip()
    if len(full_name) > 100:
        errors["full_name"] = "Họ tên tối đa 100 ký tự."
    role = data.get("role")
    if role not in ROLE_LABELS:
        errors["role"] = "Vai trò không hợp lệ."
    password = str(data.get("password") or "")
    if creating and not password:
        errors["password"] = "Hãy nhập mật khẩu cho tài khoản mới."
    elif password and not 8 <= len(password) <= 128:
        errors["password"] = "Mật khẩu từ 8 đến 128 ký tự."
    if "email" not in errors and db.execute(
            'SELECT 1 FROM "User" WHERE email = ? AND id IS NOT ?', (email, user_id)).fetchone():
        errors["email"] = "Email này đã được dùng cho tài khoản khác."
    if mssv and "mssv" not in errors and db.execute(
            'SELECT 1 FROM "User" WHERE mssv = ? AND id IS NOT ?', (mssv, user_id)).fetchone():
        errors["mssv"] = "MSSV này đã được dùng cho tài khoản khác."
    clean = {"email": email, "mssv": mssv, "full_name": full_name or None, "role": role,
             "is_active": 1 if data.get("is_active") else 0, "password": password}
    return clean, errors


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def api_admin_user_create():
    verify_csrf()
    db = get_db()
    c, errors = parse_user(request.get_json(silent=True) or {}, db, creating=True)
    if errors:
        return jerr("Vui lòng kiểm tra lại các trường được đánh dấu.", 422, errors)
    try:
        db.execute(
            'INSERT INTO "User" (mssv, email, password_hash, full_name, role, is_active, created_at) '
            "VALUES (?,?,?,?,?,?,?)",
            (c["mssv"], c["email"], generate_password_hash(c["password"]), c["full_name"],
             c["role"], c["is_active"], now_vn()))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jerr("MSSV hoặc email đã tồn tại.", 409)
    flash(f"Đã tạo tài khoản {c['email']} ({ROLE_LABELS[c['role']]}).", "info")
    return jsonify({"ok": True}), 201


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@admin_required
def api_admin_user_update(user_id):
    verify_csrf()
    db = get_db()
    row = db.execute('SELECT * FROM "User" WHERE id = ?', (user_id,)).fetchone()
    if not row:
        return jerr("Không tìm thấy tài khoản.", 404)
    data = {k: row[k] for k in ("email", "mssv", "full_name", "role", "is_active")}
    data.update(request.get_json(silent=True) or {})   # cho phep gui mot phan (vd: chi khoa)
    c, errors = parse_user(data, db, user_id=user_id)
    if errors:
        return jerr("Vui lòng kiểm tra lại các trường được đánh dấu.", 422, errors)

    if user_id == current_user.id and (c["role"] != "admin" or not c["is_active"]):
        return jerr("Bạn không thể tự hạ quyền hoặc tự khóa tài khoản của mình.", 409)
    loses_admin = row["role"] == "admin" and row["is_active"] and (c["role"] != "admin" or not c["is_active"])
    if loses_admin and other_active_admins(db, user_id) == 0:
        return jerr("Hệ thống phải còn ít nhất một quản trị viên đang hoạt động.", 409)

    try:
        db.execute('UPDATE "User" SET mssv=?, email=?, full_name=?, role=?, is_active=? WHERE id=?',
                   (c["mssv"], c["email"], c["full_name"], c["role"], c["is_active"], user_id))
        if c["password"]:
            db.execute('UPDATE "User" SET password_hash = ? WHERE id = ?',
                       (generate_password_hash(c["password"]), user_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jerr("MSSV hoặc email đã tồn tại.", 409)
    flash(f"Đã cập nhật tài khoản {c['email']}.", "info")
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def api_admin_user_delete(user_id):
    verify_csrf()
    db = get_db()
    row = db.execute('SELECT * FROM "User" WHERE id = ?', (user_id,)).fetchone()
    if not row:
        return jerr("Không tìm thấy tài khoản.", 404)
    if user_id == current_user.id:
        return jerr("Bạn không thể xóa chính tài khoản đang đăng nhập.", 409)
    if row["role"] == "admin" and row["is_active"] and other_active_admins(db, user_id) == 0:
        return jerr("Hệ thống phải còn ít nhất một quản trị viên đang hoạt động.", 409)
    n = db.execute("SELECT COUNT(*) FROM Exam WHERE uploader_id = ?", (user_id,)).fetchone()[0]
    if n:
        return jerr(f"Tài khoản đã đóng góp {n} đề thi. Hãy khóa tài khoản thay vì xóa "
                    "(hoặc xóa các đề đó trước).", 409)
    for f in db.execute("SELECT id, stored_name, drive_file_id FROM ExamFile WHERE uploader_id = ?",
                        (user_id,)).fetchall():
        remove_file_assets(f["id"], f["stored_name"], f["drive_file_id"])
    db.execute("DELETE FROM ExamFile WHERE uploader_id = ?", (user_id,))
    db.execute('DELETE FROM "User" WHERE id = ?', (user_id,))
    db.commit()
    flash(f"Đã xóa tài khoản {row['email']}.", "info")
    return jsonify({"ok": True})


# ---- De thi -----------------------------------------------------------------
def parse_exam_meta(data, db):
    errors = {}

    def s(key):
        v = data.get(key)
        return "" if v is None else str(v).strip()

    title = re.sub(r"\s+", " ", s("title"))
    if not 5 <= len(title) <= 200:
        errors["title"] = "Tiêu đề cần từ 5 đến 200 ký tự."
    subject_id = to_int(s("subject_id"), 1, 2 ** 31)
    if not subject_id or not db.execute("SELECT 1 FROM Subject WHERE id = ?", (subject_id,)).fetchone():
        errors["subject_id"] = "Hãy chọn môn học."
    source_id = to_int(s("source_id"), 1, 2 ** 31)
    if not source_id or not db.execute("SELECT 1 FROM Source WHERE id = ?", (source_id,)).fetchone():
        errors["source_id"] = "Hãy chọn nguồn đề thi."
    cohort = s("cohort")
    if cohort not in dict(COHORTS):
        errors["cohort"] = "Hãy chọn khóa áp dụng."
    duration = to_int(s("duration_min"), 5, 600)
    if duration is None:
        errors["duration_min"] = "Thời lượng từ 5 đến 600 phút."
    num_q = to_int(s("num_questions"), 1, 500)
    if num_q is None:
        errors["num_questions"] = "Số câu từ 1 đến 500."
    scale = s("grading_scale")
    if scale not in dict(GRADING_SCALES):
        errors["grading_scale"] = "Thang điểm không hợp lệ."
    parts = {}
    for key in ("nb_th", "vd", "vdc"):
        val = 0 if s(key) == "" else to_int(s(key), 0, 500)
        if val is None:
            errors["matrix"] = "Ma trận độ khó: nhập số nguyên từ 0 đến 500."
        parts[key] = val or 0
    msum = sum(parts.values())
    if "matrix" not in errors and "num_questions" not in errors and msum > 0 and msum != num_q:
        errors["matrix"] = f"Tổng ma trận ({msum}) phải bằng số câu hỏi ({num_q})."
    clean = dict(title=title, subject_id=subject_id, source_id=source_id, cohort=cohort,
                 duration_min=duration, num_questions=num_q, grading_scale=scale, **parts)
    return clean, errors


@app.route("/api/admin/exams/<int:exam_id>", methods=["PATCH"])
@admin_required
def api_admin_exam_update(exam_id):
    verify_csrf()
    db = get_db()
    row = db.execute("SELECT vault_id FROM Exam WHERE id = ?", (exam_id,)).fetchone()
    if not row:
        return jerr("Không tìm thấy đề thi.", 404)
    c, errors = parse_exam_meta(request.get_json(silent=True) or {}, db)
    if errors:
        return jerr("Vui lòng kiểm tra lại các trường được đánh dấu.", 422, errors)
    db.execute(
        """UPDATE Exam SET title=?, subject_id=?, source_id=?, cohort=?, duration_min=?,
               num_questions=?, grading_scale=?, nb_th=?, vd=?, vdc=? WHERE id=?""",
        (c["title"], c["subject_id"], c["source_id"], c["cohort"], c["duration_min"],
         c["num_questions"], c["grading_scale"], c["nb_th"], c["vd"], c["vdc"], exam_id))
    db.commit()
    flash(f"Đã cập nhật đề thi {row['vault_id']}.", "info")
    return jsonify({"ok": True})


@app.route("/api/admin/exams/<int:exam_id>", methods=["DELETE"])
@admin_required
def api_admin_exam_delete(exam_id):
    verify_csrf()
    db = get_db()
    row = db.execute(
        """SELECT e.id, e.vault_id, f.id AS file_id, f.stored_name, f.drive_file_id
           FROM Exam e JOIN ExamFile f ON f.id = e.file_id WHERE e.id = ?""", (exam_id,)).fetchone()
    if not row:
        return jerr("Không tìm thấy đề thi.", 404)
    remove_file_assets(row["file_id"], row["stored_name"], row["drive_file_id"])
    db.execute("DELETE FROM Exam WHERE id = ?", (exam_id,))
    db.execute("DELETE FROM ExamFile WHERE id = ?", (row["file_id"],))
    db.commit()
    flash(f"Đã xóa đề thi {row['vault_id']} và tệp đính kèm.", "info")
    return jsonify({"ok": True})

# ----------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)