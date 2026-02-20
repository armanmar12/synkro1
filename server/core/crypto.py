import base64
import hashlib
import json
from typing import Any, Dict

from cryptography.fernet import Fernet
from django.conf import settings


def _derive_key(raw_key: str) -> bytes:
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    raw_key = getattr(settings, "INTEGRATION_SECRET_KEY", "") or settings.SECRET_KEY
    return Fernet(_derive_key(raw_key))


def encrypt_payload(payload: Dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _get_fernet().encrypt(data).decode("utf-8")


def decrypt_payload(token: str) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        data = _get_fernet().decrypt(token.encode("utf-8"))
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}
