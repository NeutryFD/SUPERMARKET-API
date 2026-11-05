"""
Normalización de texto para búsquedas
"""

import unicodedata


def normalize(text: str) -> str:
    """Minúsculas y sin tildes ("Aceite de Oliva Virgen" -> "aceite de oliva virgen", "Ñora" -> "nora")"""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def search_text(*parts: str | None) -> str:
    """Texto en el que se busca: los campos normalizados, separados por espacios"""
    return " ".join(normalize(p) for p in parts if p)
