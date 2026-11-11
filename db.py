"""
Memoria de ChiquiBot: historial de conversación en SQLite.
Cada mensaje (del usuario o del modelo) se guarda como una fila de la tabla
`mensajes`. Se usa aiosqlite para que la base no bloquee al bot.
"""
import aiosqlite

# Archivo de la base. Se crea en la carpeta desde donde se ejecuta el bot.
# Está excluido del repo por el .gitignore (*.db).
RUTA_DB = "chiquibot.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,              -- orden de llegada
    user_id INTEGER NOT NULL,                                -- id de Telegram
    rol     TEXT    NOT NULL CHECK (rol IN ('user', 'model')), -- quién escribió
    texto   TEXT    NOT NULL,                                -- contenido
    fecha   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP       -- cuándo se guardó
);

-- Acelera la búsqueda de los mensajes de un usuario en orden.
CREATE INDEX IF NOT EXISTS idx_mensajes_usuario ON mensajes (user_id, id);
"""


async def inicializar() -> None:
    """Crea la tabla y el índice si todavía no existen."""
    async with aiosqlite.connect(RUTA_DB) as db:
        await db.executescript(ESQUEMA)
        await db.commit()


async def guardar_intercambio(user_id: int, pregunta: str, respuesta: str) -> None:
    """Guarda juntos el mensaje del usuario y la respuesta del modelo."""
    async with aiosqlite.connect(RUTA_DB) as db:
        await db.executemany(
            "INSERT INTO mensajes (user_id, rol, texto) VALUES (?, ?, ?)",
            [(user_id, "user", pregunta),
             (user_id, "model", respuesta)],
        )
        await db.commit()


async def obtener_historial(user_id: int, limite: int) -> list[tuple[str, str]]:
    """Devuelve los últimos `limite` mensajes del usuario, del más viejo al más nuevo."""
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute(
            "SELECT rol, texto FROM mensajes WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limite),
        )
        filas = await cursor.fetchall()
    # DESC trae los más recientes primero; reversed los pone en orden cronológico.
    return list(reversed(filas))


async def borrar_historial(user_id: int) -> int:
    """Borra la conversación de un usuario y devuelve cuántos mensajes eliminó."""
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute("DELETE FROM mensajes WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount