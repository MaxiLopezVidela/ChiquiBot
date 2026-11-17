"""
ChiquiBot v6: bot de Telegram sobre la Liga Profesional de Fútbol Argentino.

- Conversa con Gemini (Interactions API) con un prompt de sistema propio.
- Recuerda la conversación de cada usuario en SQLite.
- Consulta partidos reales con function calling (API de Bzzoiro).
- v5: /opinion analiza opiniones con salida estructurada validada con Pydantic.
- v6: flujo guiado /prode (ConversationHandler) y evaluación con evaluar.py.
"""
import json                                  # convierte los resultados de las funciones a texto JSON
import logging
import os

from dotenv import load_dotenv
from google import genai                     # SDK oficial de Gemini
from telegram import Update
from telegram.constants import ChatAction    # acción "escribiendo..."
from pydantic import ValidationError         # error de formato en la salida estructurada
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db                                    # módulo propio: base de datos SQLite
import pronosticos                           # flujo guiado del prode (v6)
from analisis import analizar_opinion        # análisis con salida estructurada (v5)
from herramientas import DECLARACIONES, ejecutar_funcion   # funciones propias del bot
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

# Cantidad máxima de vueltas del ciclo de function calling en un mismo mensaje.
# Evita un bucle infinito si el modelo pidiera funciones una y otra vez.
MAX_VUELTAS_HERRAMIENTAS = 5

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


def armar_entrada(historial: list[tuple[str, str]], contenido_nuevo: list[dict]) -> list[dict]:
    """
    Convierte el historial guardado en la lista de pasos que espera la
    Interactions API y agrega al final el contenido nuevo del usuario.

    contenido_nuevo es una lista de bloques (por ahora, solo de texto).
    """
    tipo_de_paso = {"user": "user_input", "model": "model_output"}
    pasos = [
        {"type": tipo_de_paso[rol], "content": [{"type": "text", "text": texto}]}
        for rol, texto in historial
    ]
    # contenido_nuevo ya es una lista de bloques: no se envuelve en otra lista.
    pasos.append({"type": "user_input", "content": contenido_nuevo})
    return pasos


async def consultar_gemini(
    historial: list[tuple[str, str]],
    contenido_nuevo: list[dict],            # antes: mensaje: str
    registro: list[str] | None = None,      # nuevo: lista opcional (la usa evaluar.py)
) -> str:
    """
    Envía la conversación a Gemini y resuelve el ciclo de function calling:
    mientras el modelo pida funciones, se ejecutan y se le devuelven los
    resultados; cuando responde con texto, se devuelve ese texto.

    Si se pasa una lista en `registro`, se agrega en ella el nombre de cada
    función que pidió el modelo.
    """
    # Lista de pasos que se envía al modelo. Empieza con el historial y el
    # mensaje nuevo, y crece dentro de este turno con los pedidos de funciones
    # y sus resultados. Esos pasos intermedios no se guardan en la base.
    entrada = armar_entrada(historial, contenido_nuevo)

    for vuelta in range(MAX_VUELTAS_HERRAMIENTAS):
        interaccion = await cliente_gemini.aio.interactions.create(
            model=GEMINI_MODEL,
            system_instruction=PROMPT_SISTEMA,
            input=entrada,
            tools=DECLARACIONES,        # funciones que el modelo puede pedir
            store=False,                # la memoria la maneja el bot
        )

        # Pasos de tipo function_call: el modelo pide ejecutar una función.
        # Puede pedir más de una en la misma respuesta (por ejemplo, dos equipos).
        llamadas = [paso for paso in interaccion.steps if paso.type == "function_call"]

        if not llamadas:
            # Sin pedidos de funciones: el modelo ya redactó la respuesta final.
            return interaccion.output_text or "No se me ocurrió nada para responder 🤔"

        # Se agregan a la entrada TODOS los pasos que devolvió el modelo, sin
        # modificarlos (incluidos los de razonamiento interno). La API los
        # necesita para relacionar cada resultado con su pedido.
        for paso in interaccion.steps:
            entrada.append(paso.model_dump())

        # Se ejecuta cada función pedida y se agrega su resultado como un paso
        # function_result. call_id debe coincidir con el id del pedido.
        for llamada in llamadas:
            if registro is not None:            # solo si se pasó una lista
                registro.append(llamada.name)   # anota la herramienta pedida
            resultado = await ejecutar_funcion(llamada.name, llamada.arguments)
            entrada.append({
                "type": "function_result",
                "call_id": llamada.id,
                "name": llamada.name,
                # ensure_ascii=False conserva tildes y ñ
                "result": [{"type": "text", "text": json.dumps(resultado, ensure_ascii=False)}],
            })
        # Se repite el ciclo: el modelo recibe los resultados y decide si
        # necesita otra función o si ya puede responder.

    # Se agotaron las vueltas sin respuesta final.
    logger.warning("Se alcanzó el máximo de %d vueltas de herramientas", MAX_VUELTAS_HERRAMIENTAS)
    return "No pude completar la consulta. ¿Me la preguntás de otra forma? 🙏"


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
        "Preguntame cuándo juega tu equipo, cómo salió su último partido, "
        "o charlemos de historia, ídolos y curiosidades del fútbol argentino. "
        "También podés jugar al prode con /prode y dejarme tu opinión con /opinion.\n\n"
        "Escribí /help para ver qué puedo hacer."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Comandos disponibles:\n\n"
        "/start - Saludo inicial\n"
        "/help - Esta ayuda\n"
        "/olvidar - Borra nuestra conversación y empezamos de cero\n"
        "/prode - Pronosticá un partido de la liga paso a paso\n"
        "/cancelar - Cancela un prode en curso\n"
        "/opinion <texto> - Dejá tu opinión de un partido o un equipo y la analizo\n"
        "/opiniones - Resumen de las opiniones de los hinchas\n\n"
        "Y, sobre todo, podés escribirme lo que quieras saber sobre "
        "la Liga Profesional de Fútbol Argentino. Me acuerdo de lo que hablamos "
        "y puedo consultar los próximos partidos y los últimos resultados de cada equipo."
    )


async def olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Borra el historial del usuario."""
    borrados = await db.borrar_historial(update.effective_user.id)
    logger.info("Usuario %s borró %d mensajes", update.effective_user.id, borrados)
    await update.message.reply_text("Listo, empezamos de cero. ¿De qué querés que charlemos? ⚽")


EMOJI_SENTIMIENTO = {"positivo": "😀", "neutro": "😐", "negativo": "😠"}


async def opinion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/opinion <texto>: analiza la opinión con salida estructurada y la guarda."""
    # context.args es la lista de palabras escritas después del comando.
    texto = " ".join(context.args).strip()
    if not texto:
        await update.message.reply_text(
            "Escribí tu opinión después del comando. Por ejemplo:\n"
            "/opinion Qué partidazo hizo Boca, el equipo jugó con una garra bárbara")
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        analisis = await analizar_opinion(cliente_gemini, GEMINI_MODEL, texto)
    except ValidationError as error:
        # El modelo devolvió un JSON que no respeta el esquema: no se guarda nada.
        logger.warning("La respuesta del análisis no respetó el esquema (%d errores)", error.error_count())
        await update.message.reply_text("No pude analizar tu opinión. ¿La escribís de otra forma? 🙏")
        return
    except Exception as error:
        logger.exception("Error al analizar la opinión")
        await update.message.reply_text(describir_error(error))
        return

    numero = await db.guardar_opinion(update.effective_user.id, texto, analisis.sentimiento,
                                      analisis.puntaje, analisis.equipo, analisis.resumen)
    # El objeto validado se muestra en la terminal: es lo que se guardó en la base.
    logger.info("Opinión %d guardada en la base: %s", numero, analisis.model_dump_json())

    await update.message.reply_text(
        f"¡Gracias por tu opinión! Así la entendí:\n\n"
        f"Sentimiento: {analisis.sentimiento} {EMOJI_SENTIMIENTO[analisis.sentimiento]}\n"
        f"Puntaje: {analisis.puntaje}/5\n"
        f"Equipo: {analisis.equipo}\n"
        f"Resumen: {analisis.resumen}\n\n"
        f"Quedó guardada con el número {numero}. Mirá el resumen de todos con /opiniones."
    )


async def opiniones(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/opiniones: resumen de las opiniones guardadas en la base."""
    datos = await db.resumen_opiniones()
    if not datos["total"]:
        await update.message.reply_text("Todavía no hay opiniones. Dejá la primera con /opinion. ⚽")
        return

    s = datos["por_sentimiento"]
    lineas = [
        f"Opiniones de los hinchas: {datos['total']}",
        f"😀 {s.get('positivo', 0)}   😐 {s.get('neutro', 0)}   😠 {s.get('negativo', 0)}",
        f"Puntaje promedio: {datos['promedio']:.1f}/5",
    ]
    if datos["por_equipo"]:
        lineas.append("\nEquipos más comentados:")
        lineas += [f"- {equipo}: {cantidad} ({promedio:.1f}/5)"
                   for equipo, cantidad, promedio in datos["por_equipo"]]
    lineas.append("\nÚltimas opiniones:")
    lineas += [f"{EMOJI_SENTIMIENTO[sent]} {resumen}" for sent, _, _, resumen in datos["ultimas"]]
    await update.message.reply_text("\n".join(lineas))


async def procesar_mensaje(update: Update, contenido: list[dict], texto_para_historial: str) -> None:
    """
    Pasos comunes a cualquier mensaje: recuperar el historial, consultar al
    modelo, guardar el intercambio y responder.
    """
    user_id = update.effective_user.id
    await update.message.chat.send_action(ChatAction.TYPING)

    # Paso 1: recuperar lo que ya se habló con este usuario.
    historial = await db.obtener_historial(user_id, MAX_MENSAJES_HISTORIAL)

    # Paso 2: consultar al modelo con el historial más el contenido nuevo.
    try:
        respuesta = await consultar_gemini(historial, contenido)
    except Exception as error:
        logger.exception("Error al consultar a Gemini")
        await update.message.reply_text(describir_error(error))
        # Si falla, no se guarda nada: el historial nunca queda con una
        # pregunta sin respuesta.
        return

    # Paso 3: guardar la pregunta y la respuesta para la próxima consulta.
    await db.guardar_intercambio(user_id, texto_para_historial, respuesta)

    # Paso 4: responder, en partes si supera el límite de Telegram.
    for inicio in range(0, len(respuesta), LIMITE_TELEGRAM):
        await update.message.reply_text(respuesta[inicio:inicio + LIMITE_TELEGRAM])


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde un mensaje de texto con Gemini, teniendo en cuenta la conversación previa."""
    texto = update.message.text
    logger.info("Mensaje de %s: %s", update.effective_user.id, texto)
    # El texto se convierte en una lista con un solo bloque. En el historial
    # se guarda el mismo texto.
    await procesar_mensaje(update, [{"type": "text", "text": texto}], texto)


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

    # bot_data es un diccionario compartido por todos los handlers. Ahí se dejan
    # el cliente y el modelo para que pronosticos.py los use sin importar bot.py.
    app.bot_data["cliente_gemini"] = cliente_gemini
    app.bot_data["modelo"] = GEMINI_MODEL

    # 1. El flujo guiado va PRIMERO: mientras un prode está en curso, sus handlers
    #    deben recibir los mensajes antes que el handler general de texto.
    app.add_handler(pronosticos.crear_manejador())
    # 2. Comandos.
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("olvidar", olvidar))
    app.add_handler(CommandHandler("opinion", opinion))
    app.add_handler(CommandHandler("opiniones", opiniones))
    # 3. Texto libre, al final porque acepta casi cualquier mensaje.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    app.add_error_handler(manejar_error)

    logger.info("ChiquiBot v6 en línea con el modelo %s.", GEMINI_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# Solo se arranca el bot si el archivo se ejecuta directamente (python bot.py),
# no si otro archivo lo importa (como evaluar.py).
if __name__ == "__main__":
    main()