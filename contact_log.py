"""
Registro local de contactos por cada alta de jugador.

WhatsApp no ofrece una API para «guardar en mis contactos de WhatsApp» desde un
servidor. Lo habitual es acumular vCards/CSV e importarlos en el móvil o en
Google Contacts (Contactos → Importar).

Se escribe en data/ (crea la carpeta si no existe):
  - contactos_registrados.csv  (UTF-8 con BOM en la primera creación)
  - contactos_registrados.vcf  (vCard 3.0 por registro, apto para importar)

Si defines GOOGLE_CONTACTS_* (ver google_contacts_sync.py), también se intenta
crear el contacto en Google Contactos de la cuenta autorizada.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from google_contacts_sync import sync_contact_to_google

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent
_DATA = _BASE / "data"
_CSV = _DATA / "contactos_registrados.csv"
_VCF = _DATA / "contactos_registrados.vcf"


def telefono_a_e164_us(telefono: str | None) -> str:
    """NANP guardado en BD → +1XXXXXXXXXX; vacío → cadena vacía."""
    if not telefono or not str(telefono).strip():
        return ""
    d = re.sub(r"\D", "", str(telefono))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or not d.isdigit():
        return ""
    return f"+1{d}"


def _escape_vcard_value(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "")


def append_registro_contacto(nombre: str, apellido: str, telefono: str | None) -> None:
    """Tras un registro exitoso. Fallos de disco no deben romper el flujo."""
    nombre = (nombre or "").strip()
    apellido = (apellido or "").strip()
    tel_fmt = (telefono or "").strip()
    e164 = telefono_a_e164_us(telefono)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        _DATA.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("No se pudo crear data/: %s", e)
        return

    # CSV
    try:
        nuevo = not _CSV.is_file()
        with _CSV.open("a", newline="", encoding="utf-8") as f:
            if nuevo:
                f.write("\ufeff")
            w = csv.writer(f)
            if nuevo:
                w.writerow(
                    [
                        "fecha_utc",
                        "nombre",
                        "apellido",
                        "telefono",
                        "telefono_e164",
                    ]
                )
            w.writerow([ts, nombre, apellido, tel_fmt, e164])
    except OSError as e:
        logger.warning("No se pudo escribir CSV de contactos: %s", e)

    # vCard (importable en Google Contactos / iPhone)
    if not nombre and not apellido:
        return
    fn = _escape_vcard_value(f"{nombre} {apellido}".strip())
    n_family = _escape_vcard_value(apellido)
    n_given = _escape_vcard_value(nombre)
    tel_line = f"TEL;TYPE=CELL:{e164}\r\n" if e164 else ""
    block = (
        "BEGIN:VCARD\r\n"
        "VERSION:3.0\r\n"
        f"FN:{fn}\r\n"
        f"N:{n_family};{n_given};;;\r\n"
        f"{tel_line}"
        "END:VCARD\r\n"
    )
    try:
        with _VCF.open("ab") as f:
            f.write(block.encode("utf-8"))
    except OSError as e:
        logger.warning("No se pudo escribir vCard: %s", e)

    try:
        sync_contact_to_google(nombre, apellido, e164)
    except Exception as e:
        logger.warning("Google Contacts: %s", e)
