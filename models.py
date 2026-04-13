from sqlalchemy import Column, DateTime, Integer, String

from database import Base


class Jugador(Base):
    __tablename__ = "jugadores"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    telefono = Column(String(30), nullable=True)
    numero_camiseta = Column(Integer, nullable=True)
    posicion = Column(String(200), nullable=False)
    posicion_favorita = Column(String(50), nullable=True)
    creado_en = Column(DateTime, nullable=True)
    # NULL = falta guardar en agenda (CSV/vCard); al pulsar «Guardar contacto» se rellena.
    contacto_guardado_en = Column(DateTime, nullable=True)
