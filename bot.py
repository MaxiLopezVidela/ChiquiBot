import logging
import os

from dotenv import load_dotenv
from google import genai                     # SDK oficial de Gemini
from telegram import Update
from telegram.constants import ChatAction    # acción "escribiendo..."
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db                                    # módulo propio: memoria en SQLite
from prompts import PROMPT_SISTEMA           # personalidad de ChiquiBot

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Mensajes anteriores que se envían en cada consulta
# (20 = 10 preguntas y sus respuestas). Más memoria = consultas más lentas y más cuota.
MAX_MENSAJES_HISTORIAL = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("chiquibot")

LIMITE_TELEGRAM = 4096

# ---------------------------------------------------------------------------
# Conexión con Gemini
# ---------------------------------------------------------------------------
cliente_gemini = genai.Client(api_key=GEMINI_API_KEY)


def armar_entrada(historial: list[tuple[str, str]], mensaje_nuevo: str) -> list[dict]:
    """
    Convierte el historial guardado en la lista de pasos que espera la
    Interactions API y agrega al final el mensaje nuevo del usuario.
    """
    tipo_de_paso = {"user": "user_input", "model": "model_output"}
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]
    pasos.append({"type": "user_input", "content": [{"type": "text", "text": mensaje_nuevo}]})
    return pasos


async def consultar_gemini(historial: list[tuple[str, str]], mensaje: str) -> str:
    """Envía a Gemini la conversación completa y devuelve el texto de la respuesta."""
    interaccion = await cliente_gemini.aio.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=PROMPT_SISTEMA,
        input=armar_entrada(historial, mensaje),   # conversación completa
        store=False,                               # la memoria la maneja el bot
    )
    return interaccion.output_text or "No se me ocurrió nada para responder 🤔"


def describir_error(error: Exception) -> str:
    """Traduce un error de la API a un mensaje comprensible para el usuario."""
    codigo = getattr(error, "status_code", None)
    if codigo == 429:
        return "Estoy recibiendo demasiadas consultas. Probá de nuevo en un minuto, por favor. ⏳"
    if codigo in (401, 403):
        return "Tengo un problema de configuración. Avisale a quien me administra. 🔧"
    return "Tuve un problema para pensar la respuesta. ¿Me lo preguntás de nuevo? 🙏"

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nombre = update.effective_user.first_name
    logger.info("Nuevo /start de %s (id %s)", nombre, update.effective_user.id)
    await update.message.reply_text(
        f"¡Qué hacés, {nombre}! ⚽ Soy ChiquiBot, tu compañero para seguir "
        "la Liga Profesional de Fútbol Argentino.\n\n"
        "Preguntame lo que quieras sobre equipos, partidos, historia o "
        "curiosidades del fútbol argentino.\n\n"
        "Escribí /help para ver qué puedo hacer."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Comandos disponibles:\n\n"
        "/start - Saludo inicial\n"
        "/help - Esta ayuda\n"
        "/olvidar - Borra nuestra conversación y empezamos de cero\n\n"
        "Y, sobre todo, podés escribirme lo que quieras saber sobre "
        "la Liga Profesional de Fútbol Argentino. Me acuerdo de lo que hablamos."
    )


async def olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Borra el historial del usuario."""
    borrados = await db.borrar_historial(update.effective_user.id)
    logger.info("Usuario %s borró %d mensajes", update.effective_user.id, borrados)
    await update.message.reply_text("Listo, empezamos de cero. ¿De qué querés que charlemos? ⚽")


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde con Gemini, teniendo en cuenta la conversación previa."""
    user_id = update.effective_user.id
    texto = update.message.text
    logger.info("Mensaje de %s: %s", user_id, texto)

    await update.message.chat.send_action(ChatAction.TYPING)

    # Paso 1: recuperar lo que ya se habló con este usuario.
    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    # Paso 2: consultar al modelo con el historial más el mensaje nuevo.
    try:
        respuesta = await consultar_gemini(historial, texto)
    except Exception as error:
        logger.exception("Error al consultar a Gemini")
        await update.message.reply_text(describir_error(error))
        # Si falla, no se guarda nada: el historial nunca queda con una
        # pregunta sin respuesta.
        return

    # Paso 3: guardar la pregunta y la respuesta para la próxima consulta.
    await db.guardar_intercambio(user_id, texto, respuesta)

    for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
        await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra cualquier error no previsto para que el bot no se caiga."""
    logger.error("Error no manejado", exc_info=context.error)

# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
async def al_iniciar(app: Application) -> None:
    """Se ejecuta una vez, antes de recibir mensajes: crea la base si no existe."""
    await db.inicializar()
    logger.info("Base de datos lista (%s).", db.RUTA_DB)


def main() -> None:
    faltantes = [nombre for nombre, valor in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
                                               ("GEMINI_API_KEY", GEMINI_API_KEY)]
                 if not valor]
    if faltantes:
        raise SystemExit(f"Faltan variables en el .env: {', '.join(faltantes)}")

    # post_init ejecuta al_iniciar dentro del bucle de eventos, antes del primer mensaje.
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(al_iniciar).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("olvidar", olvidar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    app.add_error_handler(manejar_error)

    logger.info("ChiquiBot v3 en línea con el modelo %s.", GEMINI_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()