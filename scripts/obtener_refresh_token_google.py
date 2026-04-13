"""
Uso (una sola vez por cuenta de administrador):
  pip install -r requirements.txt
  python scripts/obtener_refresh_token_google.py "C:\\ruta\\client_secret_xxxxx.json"

Descarga el JSON de «Cliente OAuth de escritorio» desde Google Cloud Console,
habilita People API y añade el alcance de contactos en la pantalla de consentimiento.

Al finalizar, copia GOOGLE_CONTACTS_REFRESH_TOKEN (y el id/secret si no los tienes)
a variables de entorno del servidor o a un .env que cargues al arrancar uvicorn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/contacts"]


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Uso: python scripts/obtener_refresh_token_google.py <ruta_client_secret.json>",
            file=sys.stderr,
        )
        return 1
    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.is_file():
        print(f"No existe el archivo: {path}", file=sys.stderr)
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Instala dependencias: pip install -r requirements.txt", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    if not creds.refresh_token:
        print(
            "No se obtuvo refresh_token. Prueba revocando acceso en "
            "https://myaccount.google.com/permissions y ejecuta de nuevo con prompt=consent.",
            file=sys.stderr,
        )
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    installed = data.get("installed") or data.get("web") or {}
    cid = installed.get("client_id", "")
    secret = installed.get("client_secret", "")

    print("\n--- Añade esto a tu entorno (no subas secretos a git) ---\n")
    print(f"GOOGLE_CONTACTS_CLIENT_ID={cid}")
    print(f"GOOGLE_CONTACTS_CLIENT_SECRET={secret}")
    print(f"GOOGLE_CONTACTS_REFRESH_TOKEN={creds.refresh_token}")
    print("\n--- Fin ---\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
