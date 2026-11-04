"""
Conexión a la base de datos (SQLAlchemy)
"""

import logging
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings

# pool_pre_ping y pool_recycle evitan el error "MySQL server has gone away"
# cuando la API lleva horas sin recibir peticiones.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=3600)

# Crea una sesión de base de datos (para cada petición) que se cierra automáticamente al terminar la petición
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    """Clase base de la que heredan todos los modelos de SQLAlchemy"""

def init_db() -> None:
    """Crea las tablas que falten. Se llama una vez, al arrancar la API."""
    from app.models import backup, product  # noqa: F401  (importa para registrar las tablas)
    Base.metadata.create_all(engine) # Crea las tablas que falten en la base de datos
    add_missing_columns(engine)


def add_missing_columns(bind: Engine) -> None:
    """create_all no modifica tablas existentes: añade con ALTER TABLE las
    columnas nuevas de los modelos (siempre opcionales) y sus índices"""
    inspector = inspect(bind)
    with bind.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existentes = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existentes:
                    continue
                tipo = column.type.compile(dialect=bind.dialect)
                connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {tipo}"))
                logging.getLogger(__name__).info("Added column %s.%s", table.name, column.name)
                for index in table.indexes:
                    if column in index.columns.values():
                        index.create(connection, checkfirst=True)
