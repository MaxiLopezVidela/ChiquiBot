"""
Base de datos de ChiquiBot (SQLite).

Tablas:
- mensajes: historial de conversación de cada usuario (memoria).
- opiniones: opiniones de los hinchas analizadas con salida estructurada (v5).
- pronosticos: pronósticos confirmados en el flujo guiado /prode (v6).

Se usa aiosqlite para que la base no bloquee al bot.
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

-- Opiniones analizadas con salida estructurada (v5).
CREATE TABLE IF NOT EXISTS opiniones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    texto       TEXT    NOT NULL,                            -- opinión original
    sentimiento TEXT    NOT NULL,                            -- positivo, negativo o neutro
    puntaje     INTEGER NOT NULL CHECK (puntaje BETWEEN 1 AND 5),
    equipo      TEXT    NOT NULL,                            -- equipo mencionado o "Ninguno"
    resumen     TEXT    NOT NULL,
    fecha       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pronósticos del prode (v6).
CREATE TABLE IF NOT EXISTS pronosticos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,      -- número de pronóstico
    user_id         INTEGER NOT NULL,
    partido_id      TEXT    NOT NULL,                       -- id del partido en Bzzoiro
    local           TEXT    NOT NULL,
    visitante       TEXT    NOT NULL,
    fecha_partido   TEXT    NOT NULL,                       -- fecha y hora argentina
    goles_local     INTEGER NOT NULL,
    goles_visitante INTEGER NOT NULL,
    creado          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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


# ---------------------------------------------------------------------------
# Opiniones (v5)
# ---------------------------------------------------------------------------

async def guardar_opinion(user_id: int, texto: str, sentimiento: str, puntaje: int,
                          equipo: str, resumen: str) -> int:
    """Guarda una opinión ya analizada y devuelve su número."""
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute(
            "INSERT INTO opiniones (user_id, texto, sentimiento, puntaje, equipo, resumen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, texto, sentimiento, puntaje, equipo, resumen),
        )
        await db.commit()
        # lastrowid es el id que SQLite asignó a la fila recién insertada.
        return cursor.lastrowid


async def resumen_opiniones() -> dict:
    """Totales de todas las opiniones guardadas, para el comando /opiniones."""
    async with aiosqlite.connect(RUTA_DB) as db:
        cursor = await db.execute("SELECT COUNT(*), AVG(puntaje) FROM opiniones")
        total, promedio = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT sentimiento, COUNT(*) FROM opiniones GROUP BY sentimiento")
        por_sentimiento = dict(await cursor.fetchall())
        cursor = await db.execute(
            "SELECT equipo, COUNT(*), AVG(puntaje) FROM opiniones WHERE equipo != 'Ninguno' "
            "GROUP BY equipo ORDER BY COUNT(*) DESC LIMIT 5")
        por_equipo = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT sentimiento, puntaje, equipo, resumen FROM opiniones ORDER BY id DESC LIMIT 3")
        ultimas = await cursor.fetchall()
    return {"total": total, "promedio": promedio, "por_sentimiento": por_sentimiento,
            "por_equipo": por_equipo, "ultimas": ultimas}


# ---------------------------------------------------------------------------
# Pronósticos del prode (v6)
# ---------------------------------------------------------------------------

async def guardar_pronostico(user_id: int, partido_id: str, local: str, visitante: str,
                             fecha_partido: str, goles_local: int, goles_visitante: int) -> int:
    """
    Guarda un pronóstico confirmado y devuelve su número. Si el usuario ya
    tenía un pronóstico para ese partido, se reemplaza: vale el último.
    """
    async with aiosqlite.connect(RUTA_DB) as db:
        await db.execute("DELETE FROM pronosticos WHERE user_id = ? AND partido_id = ?",
                         (user_id, partido_id))
        cursor = await db.execute(
            "INSERT INTO pronosticos (user_id, partido_id, local, visitante, fecha_partido, "
            "goles_local, goles_visitante) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, partido_id, local, visitante, fecha_partido, goles_local, goles_visitante),
        )
        await db.commit()
        return cursor.lastrowid
