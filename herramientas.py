"""
Herramientas de ChiquiBot: funciones que el modelo puede pedir que se ejecuten.

El módulo tiene tres partes:
   1. La función propiamente dicha, que consulta la API de Bzzoiro Sports Data.
   2. Las declaraciones: la descripción de cada función en JSON Schema,
      que es lo único que el modelo ve de ellas.
   3. Un despachador que ejecuta la función pedida por el modelo.
"""

import logging                                   # registro de eventos en la terminal
import os                                        # para leer la clave de la API del .env
import time                                      # para saber cuánto hace que se guardó una página
from datetime import date, datetime, timedelta, timezone

import httpx                                     # cliente HTTP asíncrono (dependencia de python-telegram-bot)

logger = logging.getLogger("chiquibot")          # mismo registrador que bot.py

# ---------------------------------------------------------------------------
# 1. Datos fijos
# ---------------------------------------------------------------------------

URL_BZZOIRO = "https://sports.bzzoiro.com/api/v2/events/"
ID_LIGA = 85                     # Liga Profesional de Fútbol (Argentina) en Bzzoiro
TIEMPO_ESPERA = 20               # segundos máximos de espera por consulta (la API a veces tarda)
CACHE_SEGUNDOS = 300             # cada consulta a la API se reutiliza durante 5 minutos
DIAS_PROXIMOS = 14               # los próximos partidos se buscan de hoy a 14 días
SEMANAS_ATRAS = 6                # los últimos resultados se buscan hasta 6 semanas atrás

# La API devuelve las fechas en UTC; Argentina está en UTC-3 todo el año.
HORA_ARGENTINA = timezone(timedelta(hours=-3))

# Nombres de los equipos tal como los escribe la API. Se usan como enum en la
# declaración, así el modelo no puede inventar un nombre que la API no conoce.
EQUIPOS = [
    "Aldosivi", "Argentinos Juniors", "Atlético Tucumán", "Banfield",
    "Barracas Central", "Boca Juniors", "CA Independiente", "CA Lanús",
    "CA Talleres", "Central Córdoba", "Club Atlético Belgrano",
    "Club Atlético Platense", "Club Atlético Unión de Santa Fe",
    "Defensa y Justicia", "Deportivo Riestra", "Estudiantes de La Plata",
    "Estudiantes de Río Cuarto", "Gimnasia y Esgrima",
    "Gimnasia y Esgrima Mendoza", "Huracán", "Independiente Rivadavia",
    "Instituto De Córdoba", "Newell's Old Boys", "Racing Club", "River Plate",
    "Rosario Central", "San Lorenzo", "Sarmiento", "Tigre", "Vélez Sarsfield",
]

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Instancias de los playoffs, traducidas al español.
INSTANCIAS = {
    "Round of 16": "octavos de final",
    "Quarterfinals": "cuartos de final",
    "Semifinals": "semifinal",
    "Final": "final",
}

# Estados de la API que no corresponden a un partido por jugarse.
ESTADOS_DESCARTADOS = {"finished", "postponed", "canceled", "cancelled"}


# Consultas ya hechas: (desde, hasta) -> (momento de la consulta, partidos).
# Evita pedirle lo mismo a la API en cada mensaje: los partidos no cambian minuto a minuto.
_cache: dict[tuple[date, date], tuple[float, list[dict]]] = {}


def _hoy() -> date:
    """Fecha de hoy en Argentina."""
    return datetime.now(HORA_ARGENTINA).date()


async def _partidos_entre(cliente_http: httpx.AsyncClient, desde: date, hasta: date) -> list[dict]:
    """
    Devuelve los partidos de la liga entre dos fechas.

    Se consulta por rango de fechas y no "los últimos 50 partidos": cuando la API
    está cargada, una consulta de 50 partidos tardaba más de un minuto, mientras
    que una de una semana (unos 15 partidos) responde en pocos segundos.
    Si el rango se consultó hace menos de 5 minutos, se reutiliza. Si la API
    tarda demasiado, se reintenta una vez antes de dar el error.
    """
    guardado = _cache.get((desde, hasta))
    if guardado and time.monotonic() - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]

    clave = os.getenv("BSD_API_KEY")
    for intento in (1, 2):
        try:
            respuesta = await cliente_http.get(
                URL_BZZOIRO,
                headers={"Authorization": f"Token {clave}"},
                params={"league": ID_LIGA, "date_from": desde.isoformat(),
                        "date_to": hasta.isoformat(), "limit": 50},
            )
            respuesta.raise_for_status()             # un 4xx o 5xx se convierte en excepción
            break
        except httpx.TimeoutException:
            if intento == 2:
                raise                                # segundo intento fallido: se informa el error
            logger.warning("La API de partidos tardó demasiado; reintento")

    partidos = respuesta.json().get("results", [])
    _cache[(desde, hasta)] = (time.monotonic(), partidos)
    return partidos


async def _proximos_de_la_liga(cliente_http: httpx.AsyncClient) -> list[dict]:
    """Partidos de hoy a DIAS_PROXIMOS días. Una sola consulta, compartida por la función y el /prode."""
    hoy = _hoy()
    return await _partidos_entre(cliente_http, hoy, hoy + timedelta(days=DIAS_PROXIMOS))


def _fecha_argentina(fecha_iso: str) -> datetime:
    """Convierte '2026-11-22T22:00:00+00:00' a la hora de Argentina."""
    return datetime.fromisoformat(fecha_iso).astimezone(HORA_ARGENTINA)


def _resumir_partido(partido: dict, equipo: str) -> dict:
    """Deja solo los datos útiles para el modelo, con la fecha en hora argentina."""
    fecha = _fecha_argentina(partido["event_date"])
    es_local = partido["home_team"] == equipo
    instancia = partido.get("round_label") or partido.get("round_name") or ""
    resumen = {
        "fecha": f"{DIAS[fecha.weekday()]} {fecha:%d/%m/%Y}",
        "hora_argentina": f"{fecha:%H:%M}",
        "local": partido["home_team"],
        "visitante": partido["away_team"],
        "condicion": "local" if es_local else "visitante",
        "instancia": INSTANCIAS.get(instancia, instancia),
        "estado": partido["status"],
    }
    return resumen


# ---------------------------------------------------------------------------
# 2. Función
# ---------------------------------------------------------------------------

async def obtener_partidos(equipo: str, tipo: str = "proximos", cantidad: int = 3) -> dict:
    """Devuelve los próximos partidos o los últimos resultados de un equipo (Bzzoiro)."""

    # Validación de argumentos: el modelo podría equivocarse.
    if equipo not in EQUIPOS:
        return {"error": f"Equipo desconocido: {equipo}", "equipos_disponibles": EQUIPOS}
    if tipo not in ("proximos", "ultimos"):
        return {"error": f"Tipo inválido: {tipo}. Usá 'proximos' o 'ultimos'."}
    cantidad = max(1, min(int(cantidad), 5))         # se acota entre 1 y 5

    if not os.getenv("BSD_API_KEY"):
        return {"error": "Falta configurar BSD_API_KEY en el .env"}

    def es_del_equipo(p: dict) -> bool:
        return equipo in (p["home_team"], p["away_team"])

    async with httpx.AsyncClient(timeout=TIEMPO_ESPERA) as cliente_http:
        if tipo == "proximos":
            del_equipo = [p for p in await _proximos_de_la_liga(cliente_http) if es_del_equipo(p)]
        else:
            # Se recorre de a una semana hacia atrás hasta juntar los resultados pedidos.
            # Cada semana se superpone un día con la anterior (la API usa fechas en UTC),
            # y los partidos repetidos se descartan por su id.
            por_id = {}
            hoy = _hoy()
            for semana in range(1, SEMANAS_ATRAS + 1):
                desde = hoy - timedelta(days=7 * semana)
                for p in await _partidos_entre(cliente_http, desde, desde + timedelta(days=7)):
                    if es_del_equipo(p):
                        por_id[p.get("id") or p["event_date"]] = p
                terminados = [p for p in por_id.values() if p["status"] == "finished"]
                if len(terminados) >= cantidad:
                    break                            # ya hay suficientes resultados
            del_equipo = list(por_id.values())

    if tipo == "ultimos":
        jugados = sorted(
            (p for p in del_equipo if p["status"] == "finished"),
            key=lambda p: p["event_date"], reverse=True,
        )[:cantidad]
        resultados = []
        for p in jugados:
            resumen = _resumir_partido(p, equipo)
            goles_propios = p["home_score"] if resumen["condicion"] == "local" else p["away_score"]
            goles_rival = p["away_score"] if resumen["condicion"] == "local" else p["home_score"]
            resumen["resultado"] = f"{p['home_team']} {p['home_score']} - {p['away_score']} {p['away_team']}"
            if goles_propios > goles_rival:
                resumen["para_el_equipo"] = "ganó"
            elif goles_propios < goles_rival:
                resumen["para_el_equipo"] = "perdió"
            else:
                resumen["para_el_equipo"] = "empató"
            if p.get("penalty_shootout"):
                resumen["penales"] = p["penalty_shootout"]
            resultados.append(resumen)
        if not resultados:
            return {"equipo": equipo, "ultimos_resultados": [],
                    "aviso": "No encontré partidos terminados recientes de este equipo."}
        return {"equipo": equipo, "ultimos_resultados": resultados}

    # tipo == "proximos": partidos sin jugar y no reprogramados, del más cercano al más lejano.
    pendientes = sorted(
        (p for p in del_equipo
         if p["status"] not in ESTADOS_DESCARTADOS and not p.get("replaced_by")),
        key=lambda p: p["event_date"],
    )[:cantidad]
    proximos = []
    for p in pendientes:
        resumen = _resumir_partido(p, equipo)
        historial = p.get("head_to_head") or {}
        if historial.get("total_matches"):
            resumen["historial_entre_ambos"] = {
                "partidos": historial["total_matches"],
                f"ganó {p['home_team']}": historial.get("home_wins"),
                "empates": historial.get("draws"),
                f"ganó {p['away_team']}": historial.get("away_wins"),
            }
        proximos.append(resumen)
    if not proximos:
        return {"equipo": equipo, "proximos_partidos": [],
                "aviso": "No hay próximos partidos cargados para este equipo. "
                         "Puede que haya quedado eliminado o que todavía no se haya publicado la fecha."}
    return {"equipo": equipo, "proximos_partidos": proximos}


async def proximos_partidos_liga(cantidad: int = 8) -> list[dict]:
    """
    Devuelve los próximos partidos de toda la liga, del más cercano al más lejano.
    No es una herramienta del modelo: la usa el flujo guiado /prode para armar
    el teclado con los partidos que se pueden pronosticar.
    """
    if not os.getenv("BSD_API_KEY"):
        raise RuntimeError("Falta configurar BSD_API_KEY en el .env")

    async with httpx.AsyncClient(timeout=TIEMPO_ESPERA) as cliente_http:
        partidos = await _proximos_de_la_liga(cliente_http)

    ahora = datetime.now(timezone.utc)
    pendientes = sorted(
        (p for p in partidos
         if p["status"] not in ESTADOS_DESCARTADOS
         and not p.get("replaced_by")
         and datetime.fromisoformat(p["event_date"]) > ahora),   # todavía no empezó
        key=lambda p: p["event_date"],
    )[:cantidad]

    resultado = []
    for p in pendientes:
        fecha = _fecha_argentina(p["event_date"])
        resultado.append({
            # Identificador del partido. Si la API no trajera id, se arma uno con los datos.
            "id": str(p.get("id") or f"{p['home_team']}|{p['away_team']}|{p['event_date']}"),
            "local": p["home_team"],
            "visitante": p["away_team"],
            "fecha": f"{DIAS[fecha.weekday()][:3]} {fecha:%d/%m %H:%M}",   # "sáb 22/11 19:00"
        })
    return resultado


# ---------------------------------------------------------------------------
# 3. Declaraciones
# ---------------------------------------------------------------------------

# Lo único que el modelo ve de cada función. La calidad de las descripciones
# determina si el modelo elige bien la función y sus argumentos.
DECLARACIONES = [
    {
        "type": "function",
        "name": "obtener_partidos",
        "description": (
            "Consulta datos reales y actualizados de la Liga Profesional de Fútbol Argentino: "
            "los próximos partidos de un equipo (fecha, hora argentina, rival, instancia e historial "
            "entre ambos) o sus últimos resultados (marcador y si ganó, empató o perdió). "
            "Usala siempre que el usuario pregunte cuándo juega un equipo, contra quién, a qué hora, "
            "cómo salió o qué resultado tuvo un partido reciente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "equipo": {
                    "type": "string",
                    "enum": EQUIPOS,       # el modelo solo puede elegir uno de estos nombres
                    "description": (
                        "Equipo, tal como figura en la lista. 'Gimnasia y Esgrima' es el de La Plata; "
                        "'CA Independiente' es el de Avellaneda (el Rojo) e 'Independiente Rivadavia' "
                        "es el de Mendoza. Si el usuario no aclara cuál, preguntale antes de consultar."
                    ),
                },
                "tipo": {
                    "type": "string",
                    "enum": ["proximos", "ultimos"],
                    "description": "'proximos' para partidos por jugarse; 'ultimos' para resultados ya jugados.",
                },
                "cantidad": {
                    "type": "integer",
                    "description": "Cantidad de partidos, de 1 a 5. Si el usuario no la indica, usá 1.",
                },
            },
            "required": ["equipo", "tipo"],
        },
    },
]


# ---------------------------------------------------------------------------
# 4. Despachador
# ---------------------------------------------------------------------------

# Relaciona el nombre que usa el modelo con la función de Python que se ejecuta.
FUNCIONES = {
    "obtener_partidos": obtener_partidos,
}


async def ejecutar_funcion(nombre: str, argumentos: dict) -> dict:
    """Ejecuta la función pedida por el modelo y devuelve siempre un diccionario."""
    logger.info("El modelo pidió %s(%s)", nombre, argumentos)

    funcion = FUNCIONES.get(nombre)
    if funcion is None:                          # el modelo pidió una función que no existe
        return {"error": f"Función desconocida: {nombre}"}

    try:
        # **argumentos desempaqueta el diccionario como parámetros con nombre:
        # {"equipo": "River Plate"} se convierte en obtener_partidos(equipo="River Plate").
        return await funcion(**argumentos)
    except Exception as error:
        # Cualquier falla (API caída, argumentos inválidos, tiempo agotado) se
        # informa al modelo como dato, para que pueda responder al usuario.
        logger.exception("Falló la herramienta %s", nombre)
        return {"error": f"No se pudo obtener el dato: {type(error).__name__}"}
