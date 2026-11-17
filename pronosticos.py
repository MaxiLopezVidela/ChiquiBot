"""
Flujo guiado de ChiquiBot (v6): el prode (/prode).

Un ConversationHandler conduce al usuario paso a paso: elegir un partido, escribir
el pronóstico y confirmarlo. En cada paso el bot sabe qué dato espera porque la
conversación tiene un ESTADO.

    Estado        Qué espera                       Validación                         Si es válido pasa a
    PARTIDO       Un partido del teclado           Está entre las opciones ofrecidas  RESULTADO
    RESULTADO     El pronóstico, como quiera       Lo interpreta el modelo; goles     CONFIRMACION
                                                   entre 0 y 15
    CONFIRMACION  "Confirmar" o "Cancelar"         -                                  Fin

La IA interviene en un solo punto: interpretar el pronóstico escrito en lenguaje
natural ("gana Boca 2 a 1", "empatan 1 a 1"). El resto se valida con código.
Los partidos salen de la API de Bzzoiro, así que solo se pronostican partidos reales.
"""

import logging                                   # registro de eventos en la terminal

from pydantic import BaseModel, Field, ValidationError
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,                         # administra conversaciones por estados
    MessageHandler,
    filters,
)

import db                                        # para guardar el pronóstico confirmado
from herramientas import proximos_partidos_liga  # partidos reales de la liga

logger = logging.getLogger("chiquibot")

# Estados de la conversación. range(3) asigna 0, 1 y 2: el valor no importa,
# solo que cada estado sea distinto.
PARTIDO, RESULTADO, CONFIRMACION = range(3)

MAX_GOLES = 15               # un pronóstico con más goles se considera un error de tipeo
PARTIDOS_EN_TECLADO = 8


# ---------------------------------------------------------------------------
# 1. Interpretación del pronóstico con IA (salida estructurada)
# ---------------------------------------------------------------------------

class PronosticoInterpretado(BaseModel):
    """Resultado que debe devolver el modelo al interpretar un pronóstico."""
    entendido: bool = Field(description="true si el texto indica un resultado concreto con goles; false si no.")
    goles_local: int = Field(ge=0, description="Goles del equipo local (0 si no se entendió).")
    goles_visitante: int = Field(ge=0, description="Goles del equipo visitante (0 si no se entendió).")


async def interpretar_pronostico(cliente, modelo: str, partido: dict, texto: str) -> PronosticoInterpretado:
    """Convierte un pronóstico escrito en lenguaje natural en goles de local y visitante."""
    # El modelo tiene que saber quién es local y quién visitante para ubicar los goles
    # aunque el usuario los nombre al revés ("gana River 2 a 0" con River visitante).
    instrucciones = (
        f"Partido: {partido['local']} (local) contra {partido['visitante']} (visitante). "
        "El usuario escribe su pronóstico del resultado. Devolvé los goles del local y del "
        "visitante. Reconocé apodos de los equipos. Si dice 'empate' con un número "
        "('empatan 1 a 1'), usalo para los dos. Si el texto no indica un resultado concreto "
        "con goles (por ejemplo, 'gana Boca' o 'ni idea'), respondé entendido=false."
    )
    interaccion = await cliente.aio.interactions.create(
        model=modelo,
        system_instruction=instrucciones,
        input=texto,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": PronosticoInterpretado.model_json_schema(),
        },
        store=False,
    )
    return PronosticoInterpretado.model_validate_json(interaccion.output_text)


def etiqueta(partido: dict) -> str:
    """Texto del botón: 'Boca Juniors vs River Plate · sáb 22/11 19:00'."""
    return f"{partido['local']} vs {partido['visitante']} · {partido['fecha']}"


# ---------------------------------------------------------------------------
# 2. Pasos de la conversación
# ---------------------------------------------------------------------------

async def inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 0 (/prode): consulta los próximos partidos y los muestra en un teclado."""
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        partidos = await proximos_partidos_liga(PARTIDOS_EN_TECLADO)
    except Exception:
        logger.exception("No se pudieron obtener los partidos para el prode")
        await update.message.reply_text("No pude consultar los partidos en este momento. Probá más tarde. 🙏")
        return ConversationHandler.END

    if not partidos:
        await update.message.reply_text(
            "Por ahora no hay partidos programados para pronosticar. "
            "Probá cuando se publique la próxima fecha. ⚽")
        return ConversationHandler.END

    # context.user_data es un diccionario propio de cada usuario que se conserva
    # entre mensajes. Se guardan las opciones ofrecidas (texto del botón -> partido)
    # para validar la elección y recuperar los datos del partido elegido.
    context.user_data["opciones"] = {etiqueta(p): p for p in partidos}
    context.user_data["prode"] = {}

    teclado = ReplyKeyboardMarkup([[texto] for texto in context.user_data["opciones"]],
                                  one_time_keyboard=True)
    logger.info("Prode: %s eligiendo partido", update.effective_user.id)
    await update.message.reply_text(
        "¡Vamos con el prode! ⚽ ¿Qué partido querés pronosticar?\n"
        "(Podés cancelar en cualquier momento con /cancelar)",
        reply_markup=teclado,
    )
    return PARTIDO                   # el próximo mensaje se procesa en el estado PARTIDO


async def elegir_partido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 1: valida que el partido sea una de las opciones del teclado."""
    partido = context.user_data.get("opciones", {}).get(update.message.text)
    if partido is None:
        logger.info("Prode: partido inválido %r", update.message.text)
        teclado = ReplyKeyboardMarkup([[texto] for texto in context.user_data.get("opciones", {})],
                                      one_time_keyboard=True)
        await update.message.reply_text("Elegí uno de los partidos del teclado, por favor. 👇", reply_markup=teclado)
        return PARTIDO               # se repite el mismo paso

    context.user_data["prode"]["partido"] = partido
    await update.message.reply_text(
        f"{partido['local']} vs {partido['visitante']}. ¿Cómo sale? Escribilo como quieras: "
        "\"gana Boca 2 a 1\", \"empatan 1 a 1\"…",
        reply_markup=ReplyKeyboardRemove(),          # quita el teclado de opciones
    )
    return RESULTADO


async def recibir_resultado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 2: interpreta el pronóstico con IA y lo valida."""
    await update.message.chat.send_action(ChatAction.TYPING)
    partido = context.user_data["prode"]["partido"]

    # El cliente de Gemini y el modelo se guardaron en bot_data al iniciar el bot.
    cliente = context.bot_data["cliente_gemini"]
    modelo = context.bot_data["modelo"]

    try:
        pronostico = await interpretar_pronostico(cliente, modelo, partido, update.message.text)
    except ValidationError:
        pronostico = None            # el modelo respondió con un formato inválido
    except Exception:
        logger.exception("Error al interpretar el pronóstico")
        await update.message.reply_text("No pude procesar el pronóstico en este momento. Probá de nuevo.")
        return RESULTADO

    logger.info("Prode: %r interpretado como %s", update.message.text, pronostico)

    if pronostico is None or not pronostico.entendido:
        await update.message.reply_text(
            "No entendí el resultado. Necesito los goles de cada equipo, "
            "por ejemplo: \"2 a 1 para el local\".")
        return RESULTADO
    if pronostico.goles_local > MAX_GOLES or pronostico.goles_visitante > MAX_GOLES:
        await update.message.reply_text(
            f"Uh, ¿tantos goles? 😅 Poné un resultado posible, de 0 a {MAX_GOLES} goles por equipo.")
        return RESULTADO

    context.user_data["prode"]["goles_local"] = pronostico.goles_local
    context.user_data["prode"]["goles_visitante"] = pronostico.goles_visitante

    teclado = ReplyKeyboardMarkup([["Confirmar", "Cancelar"]], one_time_keyboard=True)
    await update.message.reply_text(
        "Revisá tu pronóstico:\n\n"
        f"{partido['local']} {pronostico.goles_local} - {pronostico.goles_visitante} {partido['visitante']}\n"
        f"Partido: {partido['fecha']}\n\n"
        "¿Confirmás?",
        reply_markup=teclado,
    )
    return CONFIRMACION


async def confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paso 3: guarda el pronóstico si el usuario confirma."""
    prode = context.user_data.pop("prode", {})       # pop lo quita de user_data
    context.user_data.pop("opciones", None)

    if update.message.text != "Confirmar":
        await update.message.reply_text("Pronóstico descartado. ¿En qué más te ayudo?",
                                        reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END               # fin de la conversación

    partido = prode["partido"]
    numero = await db.guardar_pronostico(
        update.effective_user.id, partido["id"], partido["local"], partido["visitante"],
        partido["fecha"], prode["goles_local"], prode["goles_visitante"],
    )
    logger.info("Prode: pronóstico %d guardado: %s %d - %d %s", numero, partido["local"],
                prode["goles_local"], prode["goles_visitante"], partido["visitante"])
    await update.message.reply_text(
        f"¡Listo! Tu pronóstico quedó registrado con el número {numero}. 🎉\n"
        "Si volvés a pronosticar este partido, vale el último.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancelar: interrumpe el prode en cualquier paso."""
    context.user_data.pop("prode", None)
    context.user_data.pop("opciones", None)
    logger.info("Prode: %s canceló", update.effective_user.id)
    await update.message.reply_text("Prode cancelado. ¿En qué más te ayudo? ⚽",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 3. Armado del ConversationHandler
# ---------------------------------------------------------------------------

def crear_manejador() -> ConversationHandler:
    """Arma el ConversationHandler que se registra en bot.py."""
    texto = filters.TEXT & ~filters.COMMAND          # mensajes de texto que no son comandos
    return ConversationHandler(
        entry_points=[CommandHandler("prode", inicio)],          # cómo empieza
        states={                                                 # qué handler atiende cada estado
            PARTIDO: [MessageHandler(texto, elegir_partido)],
            RESULTADO: [MessageHandler(texto, recibir_resultado)],
            CONFIRMACION: [MessageHandler(texto, confirmar)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],        # funcionan en cualquier estado
    )
