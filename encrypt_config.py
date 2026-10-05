import json
import logging

from cryptography.fernet import Fernet

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    operation = "generate the encryption key"
    try:
        # 1. توليد مفتاح التشفير وحفظه في secret.key
        key = Fernet.generate_key()

        operation = "write secret.key"
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

        operation = "serialize the configuration"
        json_bytes = json.dumps(config_data).encode("utf-8")

        operation = "encrypt the configuration"
        encrypted_data = Fernet(key).encrypt(json_bytes)

        operation = "write config.enc"
        with open("config.enc", "wb") as config_file:
            config_file.write(encrypted_data)
    except OSError:
        logger.exception(
            "Could not %s. Check the current directory and file permissions.",
            operation,
        )
        return 1
    except Exception:
        logger.exception("Unexpected error while trying to %s.", operation)
        return 1

    print("✅ تم توليد secret.key وتشفير config.enc بنجاح!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
