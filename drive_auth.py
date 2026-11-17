"""
Chay 1 LAN tren may cua ban de lay GOOGLE_REFRESH_TOKEN.

    pip install google-auth-oauthlib
    python drive_auth.py

Can file client_secret.json (Google Cloud Console > APIs & Services > Credentials >
Create Credentials > OAuth client ID > loai "Desktop app").
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\nDat 3 bien moi truong sau cho server:\n")
print(f"GOOGLE_CLIENT_ID={creds.client_id}")
print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")