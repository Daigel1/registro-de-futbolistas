"""
Sincronización opcional con Google Contactos (People API).

Un servidor no puede escribir en la agenda del móvil directamente; con OAuth2 y
un refresh token de la cuenta del administrador, cada alta puede crear un
contacto en esa cuenta de Google (luego se sincroniza con el teléfono si
tienes Contactos de Google).

Variables de entorno (las tres obligatorias para activar):
  GOOGLE_CONTACTS_CLIENT_ID
  GOOGLE_CONTACTS_CLIENT_SECRET
  GOOGLE_CONTACTS_REFRESH_TOKEN

Obtén el refresh token con: python scripts/obtener_refresh_token_google.py ruta/al/client_secret.json
(En Google Cloud: APIs habilitadas → People API; credenciales → OAuth cliente de escritorio.)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PEOPLE_SCOPE = "https://www.googleapis.com/auth/contacts"
_CREATE_URL = (
    "https://people.googleapis.com/v1/people:createContact"
    "?personFields=names%2CphoneNumbers"
)


def _google_credentials_ready() -> bool:
    cid = (os.environ.get("GOOGLE_CONTACTS_CLIENT_ID") or "").strip()
    secret = (os.environ.get("GOOGLE_CONTACTS_CLIENT_SECRET") or "").strip()
    refresh = (os.environ.get("GOOGLE_CONTACTS_REFRESH_TOKEN") or "").strip()
    return bool(cid and secret and refresh)


def sync_contact_to_google(nombre: str, apellido: str, telefono_e164: str) -> None:
    """Crea contacto en Google si hay e164 y credenciales; errores solo en log."""
    if not _google_credentials_ready():
        return
    e164 = (telefono_e164 or "").strip()
    if not e164:
        return
    nombre = (nombre or "").strip()
    apellido = (apellido or "").strip()
    if not nombre and not apellido:
        return

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.warning(
            "Google Contacts: faltan paquetes (google-auth). pip install -r requirements.txt"
        )
        return

    client_id = os.environ["GOOGLE_CONTACTS_CLIENT_ID"].strip()
    client_secret = os.environ["GOOGLE_CONTACTS_CLIENT_SECRET"].strip()
    refresh_token = os.environ["GOOGLE_CONTACTS_REFRESH_TOKEN"].strip()

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[_PEOPLE_SCOPE],
    )
    try:
        creds.refresh(Request())
    except Exception as e:
        logger.warning("Google Contacts: no se pudo refrescar el token OAuth: %s", e)
        return

    token = creds.token
    if not token:
        logger.warning("Google Contacts: token de acceso vacío tras refresh.")
        return

    person = {
        "names": [
            {
                "givenName": nombre or "Jugador",
                "familyName": apellido or "",
            }
        ],
        "phoneNumbers": [{"value": e164, "type": "mobile"}],
    }
    body = json.dumps(person).encode("utf-8")
    req = urllib.request.Request(
        _CREATE_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                logger.warning("Google Contacts: código HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        logger.warning(
            "Google Contacts: error HTTP %s — %s",
            e.code,
            detail[:800] if detail else str(e),
        )
    except OSError as e:
        logger.warning("Google Contacts: error de red: %s", e)
