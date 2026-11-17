from dotenv import load_dotenv
load_dotenv()

import drive_storage
from pathlib import Path

Path("test.txt").write_text("hello")
fid = drive_storage.upload_file("test.txt", "test.txt", "text/plain")
print("Uploaded:", fid)
drive_storage.delete_file(fid)
print("Deleted OK")