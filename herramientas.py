"""
Herramientas de ChiquiBot: funciones que el modelo puede pedir que se ejecuten.

El módulo tiene tres partes:
   1. Las funciones propiamente dichas, que consultan la API de Bzzoiro Sports Data:
      obtener_partidos (función propia 1) y obtener_tabla (función propia 2, v7).
   2. Las declaraciones: la descripción de cada función en JSON Schema,
      que es lo único que el modelo ve de ellas.
   3. Un despachador que ejecuta la función pedida por el modelo.
"""

import logging                                   # registro de eventos en la terminal
import os                                        # para leer la clave de la API del .env
import time                                      # para saber cuánto hace que se guardó una página
import unicodedata                               # para comparar nombres sin tildes
from datetime import date, datetime, timedelta, timezone

import httpx                                     # cliente HTTP asíncrono (dependencia de python-telegram-bot)

logger = logging.getLogger("chiquibot")          # mismo registrador que bot.py

# ---------------------------------------------------------------------------
# 1. Datos fijos
# ---------------------------------------------------------------------------

URL_API = "https://sports.bzzoiro.com/api/v2"   # base de todas las consultas
ID_LIGA = 85                     # Liga Profesional de Fútbol (Argentina) en Bzzoiro
TIEMPO_ESPERA = 20               # segundos máximos de espera por consulta (la API a veces tarda)
CACHE_SEGUNDOS = 300             # cada consulta de partidos se reutiliza durante 5 minutos
CACHE_TABLA_SEGUNDOS = 600       # la tabla se reutiliza 10 minutos (la API también la guarda 10)
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

# Última tabla consultada: (momento de la consulta, respuesta de la API).
_cache_tabla: dict[str, tuple[float, dict]] = {}


def _hoy() -> date:
    """Fecha de hoy en Argentina."""
    return datetime.now(HORA_ARGENTINA).date()


async def _consultar_api(cliente_http: httpx.AsyncClient, ruta: str, params: dict | None = None):
    """
    Hace un GET a la API de Bzzoiro con el token y devuelve el JSON.
    Si la API tarda demasiado, se reintenta una vez antes de dar el error.
    """
    clave = os.getenv("BSD_API_KEY")
    for intento in (1, 2):
        try:
            respuesta = await cliente_http.get(
                f"{URL_API}{ruta}",
                headers={"Authorization": f"Token {clave}"},
                params=params or {},
            )
            respuesta.raise_for_status()             # un 4xx o 5xx se convierte en excepción
            return respuesta.json()
        except httpx.TimeoutException:
            if intento == 2:
                raise                                # segundo intento fallido: se informa el error
            logger.warning("La API tardó demasiado en %s; reintento", ruta)


async def _partidos_entre(cliente_http: httpx.AsyncClient, desde: date, hasta: date) -> list[dict]:
    """
    Devuelve los partidos de la liga entre dos fechas.

    Se consulta por rango de fechas y no "los últimos 50 partidos": cuando la API
    está cargada, una consulta de 50 partidos tardaba más de un minuto, mientras
    que una de una semana (unos 15 partidos) responde en pocos segundos.
    Si el rango se consultó hace menos de 5 minutos, se reutiliza.
    """
    guardado = _cache.get((desde, hasta))
    if guardado and time.monotonic() - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]

    datos = await _consultar_api(cliente_http, "/events/", {
        "league": ID_LIGA, "date_from": desde.isoformat(),
        "date_to": hasta.isoformat(), "limit": 50,
    })
    partidos = datos.get("results", [])
    _cache[(desde, hasta)] = (time.monotonic(), partidos)
    return partidos


async def _proximos_de_la_liga(cliente_http: httpx.AsyncClient) -> list[dict]:
    """Partidos de hoy a DIAS_PROXIMOS días. Una sola consulta, compartida por la función y el /prode."""
    hoy = _hoy()
    return await _partidos_entre(cliente_http, hoy, hoy + timedelta(days=DIAS_PROXIMOS))


def _fecha_argentina(fecha_iso: str) -> datetime:
    """Convierte '2026-11-22T22:00:00+00:00' a la hora de Argentina."""
    return datetime.fromisoformat(fecha_iso).astimezone(HORA_ARGENTINA)


# Partes de las instancias de la fase regular que llegan en inglés,
# por ejemplo "Clausura, Group B · Matchday 16" o "League phase · Matchday 15".
PALABRAS_INSTANCIA = {"League phase": "fase regular", "Group": "Zona", "Matchday": "Fecha"}


def _traducir_instancia(instancia: str) -> str:
    """'Clausura, Group B · Matchday 16' -> 'Clausura, Zona B · Fecha 16'."""
    if instancia in INSTANCIAS:                  # playoffs: "Round of 16" -> "octavos de final"
        return INSTANCIAS[instancia]
    for ingles, espanol in PALABRAS_INSTANCIA.items():
        instancia = instancia.replace(ingles, espanol)
    return instancia


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
        "instancia": _traducir_instancia(instancia),
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
# Función propia 2 (v7): tabla de posiciones
# ---------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """'Vélez Sarsfield' -> 'velez sarsfield': sin tildes y en minúsculas, para comparar nombres."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.casefold().strip()


def _dato(fila: dict, *claves, defecto=None):
    """
    Devuelve el primer campo de la fila que exista, probando varios nombres.
    La documentación de Bzzoiro no detalla los nombres exactos de los campos
    de la tabla, así que se aceptan las variantes más comunes.
    """
    for clave in claves:
        if isinstance(fila, dict) and fila.get(clave) is not None:
            return fila[clave]
    return defecto


def _resumir_fila(fila: dict) -> dict:
    """Deja solo los datos útiles de una fila de la tabla, con nombres en español."""
    equipo = _dato(fila, "team_name", "team", "name")
    if isinstance(equipo, dict):                 # a veces el equipo viene como objeto
        equipo = _dato(equipo, "name", "short_name")
    resumen = {
        "posicion": _dato(fila, "position", "rank", "pos"),
        "equipo": equipo,
        "pj": _dato(fila, "played", "matches_played", "matches", "games_played", "mp"),
        "ganados": _dato(fila, "won", "wins", "w"),
        "empatados": _dato(fila, "drawn", "draws", "draw", "d"),
        "perdidos": _dato(fila, "lost", "losses", "l"),
        "goles_a_favor": _dato(fila, "goals_for", "gf", "scored", "goals_scored"),
        "goles_en_contra": _dato(fila, "goals_against", "ga", "conceded", "goals_conceded"),
        "diferencia": _dato(fila, "goal_diff", "goal_difference", "gd", "diff"),
        "puntos": _dato(fila, "pts", "points"),
    }
    # Se quitan los datos que la API no trajo, para no mostrarle al modelo campos vacíos.
    return {clave: valor for clave, valor in resumen.items() if valor is not None}


def _zonas_de_la_tabla(datos) -> list[tuple[str, list[dict]]]:
    """
    Separa la respuesta de la API en zonas: [(nombre de la zona, filas), ...].

    Según la documentación, los torneos con grupos (como la Liga Profesional,
    con las zonas A y B) devuelven una lista "groups" con una tabla por grupo,
    y los torneos de una sola tabla devuelven la lista "standings".
    """
    if isinstance(datos, list):                  # la tabla vino directamente como lista
        return [("Tabla general", datos)]

    grupos = datos.get("groups")
    if isinstance(grupos, dict):                 # {"Group A": [...], "Group B": [...]}
        grupos = [{"name": nombre, "standings": filas} for nombre, filas in grupos.items()]
    if isinstance(grupos, list) and grupos:
        zonas = []
        for numero, grupo in enumerate(grupos, start=1):
            nombre = _dato(grupo, "name", "group", "group_name", "label", defecto=f"Zona {numero}")
            filas = _dato(grupo, "standings", "rows", "table", "teams", defecto=[])
            if isinstance(filas, dict):          # un nivel más: {"standings": [...]}
                filas = _dato(filas, "standings", "rows", "table", defecto=[])
            zonas.append((str(nombre), filas))
        return zonas

    filas = _dato(datos, "standings", "rows", "table", "results")
    if isinstance(filas, list):
        # Si cada fila dice a qué grupo pertenece, se separan por grupo (en el orden en que aparecen).
        por_grupo: dict[str, list[dict]] = {}
        for fila in filas:
            por_grupo.setdefault(str(_dato(fila, "group", "group_name", defecto="Tabla general")), []).append(fila)
        return list(por_grupo.items())

    # Formato desconocido: el error muestra qué claves llegaron, para poder ajustar el código.
    raise ValueError(f"Formato de tabla inesperado. Claves recibidas: {list(datos)}")


async def _tabla_cruda(cliente_http: httpx.AsyncClient) -> dict:
    """
    Pide la tabla de la temporada actual. Primero averigua el id de la temporada
    con /leagues/85/season/ (la documentación recomienda no escribirlo fijo en el
    código, porque cambia cada año) y después pide /leagues/85/standings/.
    La respuesta se reutiliza durante CACHE_TABLA_SEGUNDOS.
    """
    guardado = _cache_tabla.get("tabla")
    if guardado and time.monotonic() - guardado[0] < CACHE_TABLA_SEGUNDOS:
        return guardado[1]

    parametros = {}
    try:
        temporada = await _consultar_api(cliente_http, f"/leagues/{ID_LIGA}/season/")
        if isinstance(temporada, dict):
            temporada = temporada.get("season") or temporada   # por si viene envuelta
            if temporada.get("id"):
                parametros["season_id"] = temporada["id"]
    except httpx.HTTPStatusError:
        # Si no se pudo averiguar la temporada, se pide la tabla sin ella (la API usa la actual).
        logger.warning("No se pudo obtener la temporada actual; pido la tabla sin season_id")

    datos = await _consultar_api(cliente_http, f"/leagues/{ID_LIGA}/standings/", parametros)
    _cache_tabla["tabla"] = (time.monotonic(), datos)
    return datos


async def obtener_tabla(equipo: str | None = None) -> dict:
    """Devuelve la tabla de posiciones de la Liga Profesional, por zona (Bzzoiro)."""

    # Validación de argumentos: el modelo podría equivocarse.
    if equipo is not None and equipo not in EQUIPOS:
        return {"error": f"Equipo desconocido: {equipo}", "equipos_disponibles": EQUIPOS}
    if not os.getenv("BSD_API_KEY"):
        return {"error": "Falta configurar BSD_API_KEY en el .env"}

    async with httpx.AsyncClient(timeout=TIEMPO_ESPERA) as cliente_http:
        datos = await _tabla_cruda(cliente_http)

    zonas = []
    en_juego = False                             # si algún equipo está jugando ahora
    for nombre, filas in _zonas_de_la_tabla(datos):
        filas = [fila for fila in filas if isinstance(fila, dict)]
        en_juego = en_juego or any(fila.get("live") for fila in filas)
        posiciones = [_resumir_fila(fila) for fila in filas]
        posiciones.sort(key=lambda f: f.get("posicion") or 99)       # de la primera a la última
        # "Group A" -> "Zona A", que es como se dice en el fútbol argentino.
        zonas.append({"zona": nombre.replace("Group", "Zona"), "posiciones": posiciones})

    if not any(zona["posiciones"] for zona in zonas):
        return {"zonas": [], "aviso": "La API no devolvió la tabla de posiciones en este momento."}

    resultado = {"zonas": zonas}
    if en_juego:                                 # la API marca con "live" a los que están jugando
        resultado["aviso"] = "Hay partidos en juego: la tabla puede cambiar cuando terminen."

    if equipo:
        buscado = normalizar(equipo)
        for zona in zonas:
            for fila in zona["posiciones"]:
                if normalizar(str(fila.get("equipo", ""))) == buscado:
                    resultado["equipo_consultado"] = {"zona": zona["zona"], **fila}
        if "equipo_consultado" not in resultado:
            resultado["aviso"] = f"{equipo} no figura en la tabla que devolvió la API."
    return resultado


async def precargar_datos() -> None:
    """
    Consulta de una sola vez todo lo que pueden pedir las herramientas y lo deja
    en la caché: los próximos partidos, los resultados de las últimas
    SEMANAS_ATRAS semanas (con los mismos rangos que usa obtener_partidos) y la tabla.
    La usa evaluar.py para armar su índice de datos antes de correr los casos.
    """
    async with httpx.AsyncClient(timeout=TIEMPO_ESPERA) as cliente_http:
        await _proximos_de_la_liga(cliente_http)
        hoy = _hoy()
        for semana in range(1, SEMANAS_ATRAS + 1):
            desde = hoy - timedelta(days=7 * semana)
            await _partidos_entre(cliente_http, desde, desde + timedelta(days=7))
        try:
            await _tabla_cruda(cliente_http)
        except httpx.HTTPError as error:         # sin tabla, igual sirven los partidos
            logger.warning("No se pudo precargar la tabla: %s", type(error).__name__)


def exportar_cache() -> dict:
    """Devuelve la caché como un diccionario que se puede guardar en JSON."""
    return {
        "partidos": {f"{desde}|{hasta}": partidos for (desde, hasta), (_, partidos) in _cache.items()},
        "tabla": _cache_tabla["tabla"][1] if "tabla" in _cache_tabla else None,
    }


def importar_cache(datos: dict) -> None:
    """Carga en la caché lo que devolvió exportar_cache, como si se acabara de consultar."""
    ahora = time.monotonic()
    for rango, partidos in datos.get("partidos", {}).items():
        desde, hasta = rango.split("|")
        _cache[(date.fromisoformat(desde), date.fromisoformat(hasta))] = (ahora, partidos)
    if datos.get("tabla") is not None:
        _cache_tabla["tabla"] = (ahora, datos["tabla"])


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
            "cómo salió o qué resultado tuvo un partido reciente. "
            # v7: sin esta aclaración, el modelo consultaba "CA Independiente" cuando le
            # preguntaban "¿Cuándo juega Independiente?" (0 de 3 en evaluar.py).
            "Excepción: si el usuario nombra solo 'Independiente', 'Gimnasia' o 'Estudiantes', sin un "
            "apodo ni una ciudad que aclare cuál es, NO la uses todavía: preguntale primero a cuál se "
            "refiere (por ejemplo, Independiente de Avellaneda o Independiente Rivadavia de Mendoza)."
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
    {
        "type": "function",
        "name": "obtener_tabla",
        "description": (
            "Consulta la tabla de posiciones real y actualizada de la Liga Profesional de Fútbol "
            "Argentino, separada por zona: posición, equipo, partidos jugados, ganados, empatados, "
            "perdidos, goles a favor y en contra, diferencia de gol y puntos. Usala siempre que el "
            "usuario pregunte por la tabla, las posiciones, quién va primero, en qué puesto está un "
            "equipo o cuántos puntos tiene."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "equipo": {
                    "type": "string",
                    "enum": EQUIPOS,
                    "description": (
                        "Opcional. Enviálo solo si el usuario nombra un equipo en ESTE mensaje "
                        "(\"¿en qué puesto está River?\"), tal como figura en la lista. Si pide la tabla "
                        "sin nombrar un equipo, no lo envíes, aunque antes se haya hablado de uno."
                    ),
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 4. Despachador
# ---------------------------------------------------------------------------

# Relaciona el nombre que usa el modelo con la función de Python que se ejecuta.
FUNCIONES = {
    "obtener_partidos": obtener_partidos,
    "obtener_tabla": obtener_tabla,
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
        # "or {}" cubre el caso de obtener_tabla sin argumentos, que puede llegar como None.
        return await funcion(**(argumentos or {}))
    except Exception as error:
        # Cualquier falla (API caída, argumentos inválidos, tiempo agotado) se
        # informa al modelo como dato, para que pueda responder al usuario.
        logger.exception("Falló la herramienta %s", nombre)
        return {"error": f"No se pudo obtener el dato: {type(error).__name__}"}
