"""
drive_storage.py - Luu tep tren Google Drive thay vi o dia may chu.

Bien moi truong can co:
    DRIVE_FOLDER_ID            ID thu muc Drive chua cac tep de thi (phan cuoi URL thu muc)

  Cach 1 - OAuth (tai khoan Gmail ca nhan, de nhat):
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN   (lay bang drive_auth.py)

  Cach 2 - Service Account (chi dung voi Shared Drive cua Google Workspace):
    GOOGLE_SERVICE_ACCOUNT_FILE   duong dan toi file JSON cua service account
"""
import os
import threading
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
FILES_URL = "https://www.googleapis.com/drive/v3/files"
CHUNK = 8 * 1024 * 1024

_creds = None
_creds_lock = threading.Lock()
_local = threading.local()


def folder_id():
    fid = os.environ.get("DRIVE_FOLDER_ID")
    if not fid:
        raise RuntimeError("Thieu bien moi truong DRIVE_FOLDER_ID")
    return fid


def _credentials():
    global _creds
    with _creds_lock:
        if _creds is None:
            sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
            if sa_file:
                _creds = service_account.Credentials.from_service_account_file(
                    sa_file, scopes=SCOPES)
            else:
                _creds = Credentials(
                    None,
                    refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ["GOOGLE_CLIENT_ID"],
                    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
                    scopes=SCOPES,
                )
        return _creds


def _service():
    """Client googleapiclient khong an toan khi dung chung giua cac luong -> moi luong 1 client."""
    if not hasattr(_local, "svc"):
        _local.svc = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
    return _local.svc


def upload_file(path, name, mime):
    """Tai tep len thu muc Drive, tra ve file id."""
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True, chunksize=CHUNK)
    req = _service().files().create(
        body={"name": name, "parents": [folder_id()]},
        media_body=media, fields="id", supportsAllDrives=True,
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=3)
    return resp["id"]


def delete_file(drive_id):
    """Xoa vinh vien khoi Drive. Bo qua neu tep da khong con."""
    try:
        _service().files().delete(fileId=drive_id, supportsAllDrives=True).execute(num_retries=3)
    except HttpError as e:
        if e.resp.status != 404:
            raise


def open_stream(drive_id, range_header=None):
    """Mo luong tai tep (ho tro Range de trinh xem PDF cuon/nhay trang). Tra ve requests.Response."""
    headers = {"Accept-Encoding": "identity"}   # tranh gzip lam sai Content-Length
    if range_header:
        headers["Range"] = range_header
    return AuthorizedSession(_credentials()).get(
        f"{FILES_URL}/{drive_id}",
        params={"alt": "media", "supportsAllDrives": "true"},
        headers=headers, stream=True, timeout=60,
    )


def download_to(drive_id, dest):
    """Tai ve 1 tep tam (dung de tao thumbnail)."""
    r = open_stream(drive_id)
    try:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                fh.write(chunk)
    finally:
        r.close()
    return Path(dest)