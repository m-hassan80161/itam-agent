import json
from cryptography.fernet import Fernet

# 1. توليد مفتاح التشفير وحفظه في secret.key
key = Fernet.generate_key()
with open("secret.key", "wb") as key_file:
    key_file.write(key)

# 2. بيانات الإعدادات المبدئية (مع Password Hash للأدمن)
# كلمة السر المبدئية للاستئناس: Admin@123
config_data = {
    "server_ip": "10.22.28.12",
    "server_port": "3000",
    "endpoint": "/api/v1/inventory",
    "admin_password_hash": "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9"
}

# 3. تشفير البيانات وحفظها في config.enc
json_bytes = json.dumps(config_data).encode('utf-8')
cipher = Fernet(key)
encrypted_data = cipher.encrypt(json_bytes)

with open("config.enc", "wb") as config_file:
    config_file.write(encrypted_data)

print("✅ تم توليد secret.key وتشفير config.enc بنجاح!")