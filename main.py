import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import Jugador
from contact_log import append_registro_contacto

Base.metadata.create_all(bind=engine)


def _ensure_telefono_column() -> None:
    insp = inspect(engine)
    if not insp.has_table("jugadores"):
        return
    cols = {c["name"] for c in insp.get_columns("jugadores")}
    if "telefono" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE jugadores ADD COLUMN telefono VARCHAR(30)"))


_ensure_telefono_column()


def _ensure_posicion_favorita_column() -> None:
    insp = inspect(engine)
    if not insp.has_table("jugadores"):
        return
    cols = {c["name"] for c in insp.get_columns("jugadores")}
    if "posicion_favorita" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE jugadores ADD COLUMN posicion_favorita VARCHAR(50)")
            )


_ensure_posicion_favorita_column()


def _ensure_creado_en_column() -> None:
    insp = inspect(engine)
    if not insp.has_table("jugadores"):
        return
    cols = {c["name"] for c in insp.get_columns("jugadores")}
    if "creado_en" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE jugadores ADD COLUMN creado_en DATETIME"))
            conn.execute(
                text(
                    "UPDATE jugadores SET creado_en = '2000-01-01 00:00:00.000000' "
                    "WHERE creado_en IS NULL"
                )
            )


_ensure_creado_en_column()


def _rellenar_creado_en_null() -> None:
    """Evita NULL en creado_en (legado); sin fecha no se puede marcar como nuevo de forma fiable."""
    insp = inspect(engine)
    if not insp.has_table("jugadores"):
        return
    cols = {c["name"] for c in insp.get_columns("jugadores")}
    if "creado_en" not in cols:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jugadores SET creado_en = '2000-01-01 00:00:00.000000' "
                "WHERE creado_en IS NULL"
            )
        )


_rellenar_creado_en_null()


def _ensure_contacto_guardado_en_column() -> None:
    """Contacto aún no volcado a CSV/vCard (y Google si aplica). NULL = pendiente de guardar."""
    insp = inspect(engine)
    if not insp.has_table("jugadores"):
        return
    cols = {c["name"] for c in insp.get_columns("jugadores")}
    if "contacto_guardado_en" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE jugadores ADD COLUMN contacto_guardado_en DATETIME"
                )
            )
            conn.execute(
                text(
                    "UPDATE jugadores SET contacto_guardado_en = datetime('now') "
                    "WHERE contacto_guardado_en IS NULL"
                )
            )


_ensure_contacto_guardado_en_column()


def jugador_es_nuevo(j: Jugador) -> bool:
    """True mientras no se haya guardado el contacto en agenda (listado: efecto distintivo)."""
    return getattr(j, "contacto_guardado_en", None) is None


def contar_jugadores_nuevos(jugadores: list[Jugador] | None) -> int:
    """Misma regla que la fila `is-jugador-nuevo` (contacto aún no guardado en agenda)."""
    if not jugadores:
        return 0
    return sum(1 for j in jugadores if jugador_es_nuevo(j))


def titulo_palabras(s: str | None) -> str:
    """Cada palabra con mayúscula inicial (resto en minúsculas); uso en tabla de jugadores."""
    if s is None:
        return ""
    t = str(s).strip()
    if not t:
        return ""
    return " ".join(w.capitalize() for w in t.split())


POSICIONES_VALIDAS = ("Portero", "Defensa", "Centrocampista", "Delantero")
_POS_SET = frozenset(POSICIONES_VALIDAS)


def pos_split(s: str | None) -> list[str]:
    """CSV de posiciones → lista ordenada sin duplicados."""
    if not s or not str(s).strip():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in str(s).split(","):
        p = part.strip()
        if p in _POS_SET and p not in seen:
            seen.add(p)
            out.append(p)
    return sorted(out, key=lambda x: POSICIONES_VALIDAS.index(x))


def posicion_principal(csv: str | None, favorita: str | None = None) -> str:
    """Color de fila: favorita si está entre las elegidas; si no, la más defensiva."""
    parts = pos_split(csv)
    if not parts:
        return ""
    f = (favorita or "").strip()
    if f in _POS_SET and f in parts:
        return f
    return parts[0]


def _min_rank_posicion(csv: str | None) -> int:
    ranks: list[int] = []
    for p in pos_split(csv):
        pl = p.lower()
        if pl == "portero":
            ranks.append(0)
        elif pl == "defensa":
            ranks.append(1)
        elif pl in ("centrocampista", "mediocampista"):
            ranks.append(2)
        elif pl == "delantero":
            ranks.append(3)
        else:
            ranks.append(99)
    return min(ranks) if ranks else 99


def _numeros_camiseta_permitidos(_pos_csv: str) -> set[int]:
    """Dorsales 1–11; no dependen de las posiciones marcadas."""
    return set(range(1, 12))


_US_NANP = re.compile(r"^[2-9]\d{2}[2-9]\d{6}$")


def _parse_us_phone(raw: str) -> tuple[bool, str | None]:
    """Valida número NANP. Devuelve (válido, formato (XXX) XXX-XXXX o None si vacío)."""
    s = (raw or "").strip()
    if not s:
        return True, None
    d = re.sub(r"\D", "", s)
    if not d:
        return False, None
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or not _US_NANP.match(d):
        return False, None
    return True, f"({d[0:3]}) {d[3:6]}-{d[6:10]}"


def _validar_datos_jugador(
    nombre: str,
    apellido: str,
    posicion_items: list[str],
    telefono: str,
    numero_camiseta: str,
    posicion_favorita: str = "",
) -> tuple[str | None, str | None, int | None, str, str | None]:
    """Devuelve (error, tel_formateado, número_camiseta, posiciones_csv, posicion_favorita|None)."""
    nombre = nombre.strip()
    apellido = apellido.strip()
    pos_csv = ",".join(pos_split(",".join(posicion_items)))
    orden_pos = pos_split(pos_csv)
    partes = set(orden_pos)
    fav_raw = (posicion_favorita or "").strip()
    if fav_raw and fav_raw not in _POS_SET:
        return "Posición favorita no válida.", None, None, pos_csv, None
    if fav_raw and fav_raw not in partes:
        fav_raw = ""
    fav_out: str | None = fav_raw if fav_raw else None
    if not nombre or not apellido:
        return "Completa nombre y apellido.", None, None, pos_csv, fav_out
    if not pos_csv:
        return "Marca al menos una posición.", None, None, pos_csv, fav_out
    if not fav_out or fav_out not in partes:
        fav_out = orden_pos[0]
    ok_tel, tel_fmt = _parse_us_phone(telefono)
    if not ok_tel:
        return (
            "El teléfono no es válido. Introduce 10 dígitos o déjalo vacío.",
            None,
            None,
            pos_csv,
            fav_out,
        )
    num = None
    permitidos = _numeros_camiseta_permitidos(pos_csv)
    if numero_camiseta.strip():
        try:
            num = int(numero_camiseta)
        except ValueError:
            return "Número de camiseta no válido.", None, None, pos_csv, fav_out
        if num not in permitidos:
            return (
                "El número de camiseta debe estar entre 1 y 11.",
                None,
                None,
                pos_csv,
                fav_out,
            )
    return None, tel_fmt, num, pos_csv, fav_out


def _jugadores_query_ordenados(db: Session) -> list[Jugador]:
    """Orden por la posición más «adelantada» del jugador; mismo criterio: id."""
    rows = db.query(Jugador).order_by(Jugador.id).all()
    rows.sort(key=lambda r: (_min_rank_posicion(r.posicion), r.id))
    return rows


def _prefill_form(
    nombre: str,
    apellido: str,
    telefono: str,
    posicion_csv: str,
    numero_camiseta: str,
    posicion_favorita: str = "",
) -> SimpleNamespace:
    nc = (numero_camiseta or "").strip()
    parsed: int | None = None
    if nc:
        try:
            parsed = int(nc)
        except ValueError:
            parsed = None
    parts = set(pos_split(posicion_csv))
    pf = (posicion_favorita or "").strip()
    if pf not in parts:
        pf = ""
    return SimpleNamespace(
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        telefono=(telefono or "").strip(),
        posicion=",".join(pos_split(posicion_csv)),
        numero_camiseta=parsed,
        posicion_favorita=pf or None,
    )


app = FastAPI(title="Registro de jugadores de fútbol")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["pos_split"] = pos_split
templates.env.globals["posicion_principal"] = posicion_principal
templates.env.globals["jugador_es_nuevo"] = jugador_es_nuevo
templates.env.globals["contar_jugadores_nuevos"] = contar_jugadores_nuevos
templates.env.globals["titulo_palabras"] = titulo_palabras
templates.env.filters["titulo_palabras"] = titulo_palabras
(BASE_DIR / "static").mkdir(exist_ok=True)


@app.get(
    "/static/fondo-jugadores.png",
    name="fondo_jugadores_listado",
    include_in_schema=False,
)
def servir_fondo_listado_jugadores():
    ruta = BASE_DIR / "static" / "fondo-jugadores.png"
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Imagen de fondo no encontrada")
    return FileResponse(ruta, media_type="image/png")


@app.get("/")
def portada():
    """La portada redirige al formulario: a los usuarios solo les hace falta /registrar."""
    return RedirectResponse(url="/registrar", status_code=303)


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def admin_alias_listado():
    """Compatibilidad: /admin redirige al listado (sin login)."""
    return RedirectResponse(url="/jugadores", status_code=303)


def _ctx_listado_jugadores(db: Session) -> dict:
    rows = _jugadores_query_ordenados(db)
    return {
        "jugadores": rows,
        "jugadores_nuevos_count": sum(1 for j in rows if jugador_es_nuevo(j)),
        "inline_error": None,
        "inline_edit_id": None,
        "inline_prefill": None,
        # En contexto explícito: evita UndefinedError si el env de Jinja no hereda globals.
        "jugador_es_nuevo": jugador_es_nuevo,
        "contar_jugadores_nuevos": contar_jugadores_nuevos,
        "titulo_palabras": titulo_palabras,
    }


@app.get("/jugadores", response_class=HTMLResponse)
def listar_jugadores(request: Request, db: Session = Depends(get_db)):
    ctx = _ctx_listado_jugadores(db)
    ctx["request"] = request
    return templates.TemplateResponse(
        "jugadores.html",
        ctx,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.post("/jugadores/guardar-contactos-pendientes")
def guardar_contactos_pendientes(db: Session = Depends(get_db)):
    """Escribe CSV/vCard (y Google si aplica) por cada jugador pendiente y quita el indicador «nuevo»."""
    pendientes = (
        db.query(Jugador)
        .filter(Jugador.contacto_guardado_en.is_(None))
        .order_by(Jugador.id)
        .all()
    )
    ahora = datetime.now()
    for row in pendientes:
        append_registro_contacto(row.nombre, row.apellido, row.telefono)
        row.contacto_guardado_en = ahora
    db.commit()
    return RedirectResponse(url="/jugadores", status_code=303)


@app.post("/jugadores/marcar-tel-copiado")
def marcar_tel_copiado(
    jugador_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Quita el indicador «nuevo» de contacto tras copiar el teléfono (no escribe en el CSV del club)."""
    row = db.get(Jugador, jugador_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    if row.contacto_guardado_en is None:
        row.contacto_guardado_en = datetime.now()
        db.commit()
    return JSONResponse({"ok": True})


@app.post("/jugadores/eliminar")
def eliminar_jugador(
    jugador_id: int = Form(...),
    db: Session = Depends(get_db),
):
    row = db.get(Jugador, jugador_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return RedirectResponse(url="/jugadores", status_code=303)


@app.post("/jugadores/editar", response_class=HTMLResponse)
def actualizar_jugador(
    request: Request,
    jugador_id: int = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(...),
    telefono: str = Form(""),
    posicion: Annotated[list[str], Form()] = [],
    numero_camiseta: str = Form(""),
    posicion_favorita: str = Form(""),
    db: Session = Depends(get_db),
):
    error, tel_fmt, num, pos_csv, fav = _validar_datos_jugador(
        nombre, apellido, posicion, telefono, numero_camiseta, posicion_favorita
    )
    if error:
        ctx = _ctx_listado_jugadores(db)
        ctx["request"] = request
        ctx["inline_error"] = error
        ctx["inline_edit_id"] = jugador_id
        ctx["inline_prefill"] = _prefill_form(
            nombre,
            apellido,
            telefono,
            pos_csv,
            numero_camiseta,
            posicion_favorita,
        )
        return templates.TemplateResponse(
            "jugadores.html",
            ctx,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    row = db.get(Jugador, jugador_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    row.nombre = nombre.strip()
    row.apellido = apellido.strip()
    row.telefono = tel_fmt
    row.posicion = pos_csv
    row.posicion_favorita = fav
    row.numero_camiseta = num
    db.commit()
    return RedirectResponse(url="/jugadores", status_code=303)


def _volver_lista(desde: str | None) -> bool:
    return (desde or "").strip().lower() == "jugadores"


@app.get("/registrar", response_class=HTMLResponse)
def formulario_registro(request: Request, desde: str | None = None):
    return templates.TemplateResponse(
        "registrar.html",
        {
            "request": request,
            "error": None,
            "edit_id": None,
            "jugador": None,
            "volver_a_jugadores": _volver_lista(desde),
        },
    )


@app.post("/registrar", response_class=HTMLResponse)
def crear_jugador(
    request: Request,
    nombre: str = Form(...),
    apellido: str = Form(...),
    telefono: str = Form(""),
    posicion: Annotated[list[str], Form()] = [],
    numero_camiseta: str = Form(""),
    posicion_favorita: str = Form(""),
    desde: str = Form(""),
    db: Session = Depends(get_db),
):
    volver = _volver_lista(desde)
    error, tel_fmt, num, pos_csv, fav = _validar_datos_jugador(
        nombre, apellido, posicion, telefono, numero_camiseta, posicion_favorita
    )
    if error:
        return templates.TemplateResponse(
            "registrar.html",
            {
                "request": request,
                "error": error,
                "edit_id": None,
                "jugador": _prefill_form(
                    nombre,
                    apellido,
                    telefono,
                    pos_csv,
                    numero_camiseta,
                    posicion_favorita,
                ),
                "volver_a_jugadores": volver,
            },
        )

    jugador = Jugador(
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        telefono=tel_fmt,
        posicion=pos_csv,
        posicion_favorita=fav,
        numero_camiseta=num,
        creado_en=datetime.now(),
    )
    db.add(jugador)
    db.commit()
    next_url = "/jugadores" if volver else "/registrar"
    return RedirectResponse(url=next_url, status_code=303)
