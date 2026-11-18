"""
Evaluación sistemática de ChiquiBot (v7).

Envía al bot cada pregunta de casos_prueba.json (sin pasar por Telegram) y
evalúa la respuesta con dos criterios:
  1. Herramienta: ¿el modelo usó la función esperada? Se verifica con código.
  2. Contenido: ¿la respuesta cumple el criterio? Lo decide un segundo modelo
     que actúa como evaluador ("LLM como juez"), con salida estructurada.

Cambios de la v7:
  - Índice de datos. Antes de los casos se consulta UNA sola vez a la API todo lo
    que pueden pedir las herramientas (próximos partidos, resultados de las últimas
    semanas y tabla) y se arma un índice por equipo. Se guarda en
    indice_evaluacion.json y se reutiliza durante VIGENCIA_MINUTOS. Mientras dura,
    las herramientas del bot toman los datos de la caché en lugar de esperar a la
    API: la evaluación es más rápida y todos los casos ven los mismos datos.
  - Juez con RAG (generación aumentada con recuperación). Para cada caso se
    RECUPERAN del índice los datos de los equipos mencionados en la pregunta o en
    la respuesta (y la tabla, si se habla de posiciones), se AGREGAN a la consulta
    del juez junto con la fecha de comparación y lo que devolvieron las
    herramientas del bot, y el juez GENERA su veredicto comparando con esos datos.
    Así ya no marca como "inventada" una fecha real que no conocía.

  - Variabilidad. Los modelos no responden siempre igual, así que el resultado
    puede cambiar entre corridas. El juez usa temperatura 0 para ser lo más
    consistente posible, y con "--repeticiones N" cada caso se ejecuta N veces y
    se muestra la tasa de aciertos de cada uno (por ejemplo, 2/3 = inestable).

El resultado se muestra en la terminal y se guarda en resultados_evaluacion.csv.
Opciones:
  python evaluar.py --nuevo             ignora el índice guardado y vuelve a consultar la API
  python evaluar.py --repeticiones 3    ejecuta cada caso 3 veces
"""

import asyncio                       # permite ejecutar corrutinas desde un script
import csv                           # escritura del reporte en formato CSV
import json                          # lectura de los casos y del índice
import re                            # búsqueda de los nombres de los equipos en el texto
import sys                           # para leer la opción --nuevo
import time                          # medición de tiempos y antigüedad del índice
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field     # esquema de la respuesta del evaluador

import bot                           # se reutilizan el cliente y consultar_gemini del bot
import herramientas                  # funciones propias y caché de datos de la API

RUTA_CASOS = "casos_prueba.json"
RUTA_REPORTE = "resultados_evaluacion.csv"
RUTA_INDICE = Path("indice_evaluacion.json")

# Pausa entre casos, en segundos, para no superar la cuota de consultas por minuto.
PAUSA_SEGUNDOS = 4

# Minutos durante los que se reutiliza el índice guardado. Los partidos y la tabla
# no cambian en ese lapso (salvo que haya partidos en juego).
VIGENCIA_MINUTOS = 30

# Máximo de equipos que se recuperan del índice para un caso: alcanza para la
# pregunta y el rival, y la consulta al juez no se hace demasiado larga.
MAX_EQUIPOS_RECUPERADOS = 4

# Palabras con las que se reconoce a cada equipo en el texto (sin tildes y en
# minúsculas), incluidos los apodos más comunes. Es la "clave" del índice.
ALIAS_EQUIPOS = {
    "Aldosivi": ["aldosivi", "tiburon"],
    "Argentinos Juniors": ["argentinos", "bicho"],
    "Atlético Tucumán": ["atletico tucuman", "tucuman", "decano"],
    "Banfield": ["banfield", "taladro"],
    "Barracas Central": ["barracas"],
    "Boca Juniors": ["boca", "xeneize"],
    "CA Independiente": ["independiente", "rojo"],
    "CA Lanús": ["lanus", "granate"],
    "CA Talleres": ["talleres"],
    "Central Córdoba": ["central cordoba"],
    "Club Atlético Belgrano": ["belgrano", "pirata"],
    "Club Atlético Platense": ["platense", "calamar"],
    "Club Atlético Unión de Santa Fe": ["union", "tatengue"],
    "Defensa y Justicia": ["defensa y justicia", "halcon"],
    "Deportivo Riestra": ["riestra"],
    "Estudiantes de La Plata": ["estudiantes", "pincha"],
    "Estudiantes de Río Cuarto": ["estudiantes de rio cuarto", "rio cuarto"],
    "Gimnasia y Esgrima": ["gimnasia", "lobo"],
    "Gimnasia y Esgrima Mendoza": ["gimnasia mendoza", "gimnasia y esgrima mendoza"],
    "Huracán": ["huracan", "globo"],
    "Independiente Rivadavia": ["independiente rivadavia", "rivadavia"],
    "Instituto De Córdoba": ["instituto"],
    "Newell's Old Boys": ["newell's", "newells", "lepra"],
    "Racing Club": ["racing", "academia"],
    "River Plate": ["river", "millonario"],
    "Rosario Central": ["rosario central", "canalla"],
    "San Lorenzo": ["san lorenzo", "ciclon", "cuervo"],
    "Sarmiento": ["sarmiento"],
    "Tigre": ["tigre", "matador"],
    "Vélez Sarsfield": ["velez", "fortin"],
}

# Palabras que indican que se habla de la tabla: en ese caso también se recupera la tabla.
PALABRAS_TABLA = ["tabla", "posicion", "puesto", "puntos", "zona", "lider", "punta"]


class Juicio(BaseModel):
    """Respuesta que debe devolver el modelo evaluador."""
    cumple: bool = Field(description="true si la respuesta cumple el criterio; false si no.")
    justificacion: str = Field(description="Explicación breve, en una oración.")


PROMPT_JUEZ = """
Sos un evaluador estricto de un asistente sobre la Liga Profesional de Fútbol Argentino.
Recibís la fecha de hoy, una pregunta, la respuesta del asistente, un criterio y datos
reales de la liga: lo que devolvieron las herramientas del asistente y datos de referencia.

Reglas:
- Decidí si la respuesta cumple el criterio. Evaluá solo el criterio indicado, no el estilo ni la extensión.
- Tomá la fecha de hoy como fecha de comparación: un partido con fecha posterior es un partido futuro, no un dato inventado.
- Usá los datos reales para verificar fechas, horarios, rivales, resultados, posiciones y puntos. Si coinciden, el dato es correcto; si no coinciden o no aparecen en los datos, es inventado.
- No uses lo que creas saber de memoria sobre partidos, resultados o posiciones actuales: puede estar desactualizado.
- Si el criterio pide no dar el nombre de un jugador, cualquier nombre de jugador en la respuesta lo incumple, aunque después aclare que no está confirmado.
""".strip()


# ---------------------------------------------------------------------------
# 1. Índice de datos
# ---------------------------------------------------------------------------

async def construir_indice() -> dict:
    """
    Consulta la API una sola vez y arma el índice: para cada equipo, sus próximos
    partidos y sus últimos resultados, más la tabla completa. Los datos tienen el
    mismo formato que devuelven las herramientas al bot.
    """
    await herramientas.precargar_datos()         # todas las consultas a la API, de una vez
    # Con la caché cargada, estas llamadas ya no consultan la API: solo ordenan los datos.
    equipos = {}
    for equipo in herramientas.EQUIPOS:
        equipos[equipo] = {
            "proximos": (await herramientas.obtener_partidos(equipo, "proximos", 3)).get("proximos_partidos", []),
            "ultimos": (await herramientas.obtener_partidos(equipo, "ultimos", 3)).get("ultimos_resultados", []),
        }
    try:
        tabla = await herramientas.obtener_tabla()
    except Exception as error:                   # sin tabla, el índice igual sirve para los partidos
        tabla = {"error": f"No se pudo obtener la tabla: {type(error).__name__}"}
    return {
        "creado": time.time(),
        "fecha_comparacion": fecha_de_hoy(),
        "equipos": equipos,
        "tabla": tabla,
        "cache": herramientas.exportar_cache(),  # datos crudos, para recargar la caché del bot
    }


def fecha_de_hoy() -> str:
    """'miércoles 18/11/2026 17:46 (hora argentina)': la fecha de comparación del juez."""
    ahora = datetime.now(herramientas.HORA_ARGENTINA)
    return f"{herramientas.DIAS[ahora.weekday()]} {ahora:%d/%m/%Y %H:%M} (hora argentina)"


async def obtener_indice(forzar_nuevo: bool) -> dict | None:
    """Usa el índice guardado si es reciente; si no, lo arma de nuevo y lo guarda."""
    # Mientras dure la evaluación, la caché de las herramientas no vence.
    herramientas.CACHE_SEGUNDOS = herramientas.CACHE_TABLA_SEGUNDOS = VIGENCIA_MINUTOS * 60

    if RUTA_INDICE.exists() and not forzar_nuevo:
        indice = json.loads(RUTA_INDICE.read_text(encoding="utf-8"))
        minutos = (time.time() - indice["creado"]) / 60
        if minutos < VIGENCIA_MINUTOS:
            herramientas.importar_cache(indice["cache"])
            print(f"Índice reutilizado (armado hace {minutos:.0f} min, del {indice['fecha_comparacion']}).")
            return indice

    print("Armando el índice de datos con la API de Bzzoiro...")
    try:
        indice = await construir_indice()
    except Exception as error:
        # Sin índice la evaluación sigue: el juez solo verá lo que devolvieron las herramientas.
        print(f"No se pudo armar el índice ({type(error).__name__}). Sigo sin datos de referencia.")
        return None
    RUTA_INDICE.write_text(json.dumps(indice, ensure_ascii=False), encoding="utf-8")
    return indice


# ---------------------------------------------------------------------------
# 2. Recuperación (la "R" de RAG)
# ---------------------------------------------------------------------------

def equipos_mencionados(texto: str) -> list[str]:
    """
    Devuelve los equipos que aparecen en el texto, por nombre o apodo, en orden de aparición.
    Se buscan primero los nombres más largos y cada coincidencia se borra del texto:
    así "independiente rivadavia" se reconoce como Independiente Rivadavia y no
    también como el Independiente de Avellaneda.
    """
    normalizado = herramientas.normalizar(texto)
    alias_por_largo = sorted(((palabra, equipo) for equipo, alias in ALIAS_EQUIPOS.items() for palabra in alias),
                             key=lambda par: len(par[0]), reverse=True)
    primera_aparicion = {}                       # equipo -> posición en el texto
    for palabra, equipo in alias_por_largo:
        for coincidencia in re.finditer(rf"\b{re.escape(palabra)}\b", normalizado):
            inicio, fin = coincidencia.span()
            primera_aparicion[equipo] = min(inicio, primera_aparicion.get(equipo, inicio))
            normalizado = normalizado[:inicio] + " " * (fin - inicio) + normalizado[fin:]   # se "consume"
    return sorted(primera_aparicion, key=primera_aparicion.get)


def recuperar_datos(indice: dict | None, pregunta: str, respuesta: str) -> tuple[dict, list[str]]:
    """
    Busca en el índice los datos que sirven para juzgar este caso: los de los
    equipos mencionados (primero los de la pregunta) y la tabla si se habla de posiciones.
    Devuelve los datos y la lista de lo que se recuperó, para el reporte.
    """
    if indice is None:
        return {}, []
    equipos = equipos_mencionados(pregunta)
    equipos += [e for e in equipos_mencionados(respuesta) if e not in equipos]
    equipos = equipos[:MAX_EQUIPOS_RECUPERADOS]

    datos = {equipo: indice["equipos"][equipo] for equipo in equipos if equipo in indice["equipos"]}
    recuperado = list(datos)
    # La tabla se recupera si la PREGUNTA habla de posiciones, o si la respuesta da
    # puntos con números ("tiene 55 puntos"). No alcanza con que la respuesta diga
    # "tabla": el bot suele ofrecer "la tabla de posiciones" al final de cualquier respuesta.
    pregunta_normalizada = herramientas.normalizar(pregunta)
    if (any(re.search(rf"\b{palabra}", pregunta_normalizada) for palabra in PALABRAS_TABLA)
            or re.search(r"\d+\s*(puntos|pts)\b", herramientas.normalizar(respuesta))):
        datos["tabla_de_posiciones"] = indice["tabla"]
        recuperado.append("tabla")
    return datos, recuperado


# ---------------------------------------------------------------------------
# 3. Juez (la "A" y la "G" de RAG: se aumenta su consulta y genera el veredicto)
# ---------------------------------------------------------------------------

def a_texto(datos) -> str:
    """Convierte datos a JSON legible, conservando tildes."""
    return json.dumps(datos, ensure_ascii=False, indent=1)


async def juzgar(caso: dict, respuesta: str, resultados_herramientas: list[dict],
                 referencia: dict, fecha_comparacion: str) -> Juicio:
    """Le pide al modelo evaluador que decida si la respuesta cumple el criterio."""
    consulta = (
        f"Fecha de hoy (fecha de comparación): {fecha_comparacion}\n\n"
        f"Pregunta: {caso['pregunta']}\n\n"
        f"Respuesta del asistente: {respuesta}\n\n"
        f"Criterio: {caso['criterio']}\n\n"
        "Datos que devolvieron las herramientas del asistente en este caso:\n"
        f"{a_texto(resultados_herramientas) if resultados_herramientas else 'El asistente no usó herramientas.'}\n\n"
        "Datos de referencia recuperados del índice (datos reales de la API):\n"
        f"{a_texto(referencia) if referencia else 'No hay datos de referencia para esta pregunta.'}"
    )
    interaccion = await bot.cliente_gemini.aio.interactions.create(
        model=bot.GEMINI_MODEL,
        system_instruction=PROMPT_JUEZ,
        input=consulta,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": Juicio.model_json_schema(),
        },
        # Temperatura 0: el juez elige siempre la opción más probable, así ante la
        # misma respuesta da (casi siempre) el mismo veredicto. Al bot no se le cambia:
        # se lo evalúa tal como responde a los usuarios.
        generation_config={"temperature": 0},
        store=False,
    )
    return Juicio.model_validate_json(interaccion.output_text)


# ---------------------------------------------------------------------------
# 4. Ejecución de los casos
# ---------------------------------------------------------------------------

async def evaluar_caso(caso: dict, indice: dict | None, fecha_comparacion: str) -> dict:
    """Ejecuta un caso de prueba y devuelve una fila del reporte."""
    inicio = time.perf_counter()
    herramientas_usadas = []         # consultar_gemini agrega aquí cada función que se pidió
    resultados_herramientas = []     # ... y aquí lo que devolvió cada una
    respuesta = await bot.consultar_gemini(
        [],                          # historial vacío: cada caso es independiente
        [{"type": "text", "text": caso["pregunta"]}],
        registro=herramientas_usadas,
        resultados=resultados_herramientas,
    )
    segundos_bot = time.perf_counter() - inicio

    esperada = caso["herramienta_esperada"]
    if esperada is None:
        herramienta_ok = not herramientas_usadas      # no debía usar ninguna
    else:
        herramienta_ok = esperada in herramientas_usadas

    referencia, recuperado = recuperar_datos(indice, caso["pregunta"], respuesta)
    juicio = await juzgar(caso, respuesta, resultados_herramientas, referencia, fecha_comparacion)

    return {
        "pregunta": caso["pregunta"],
        "herramienta_esperada": esperada or "ninguna",
        "herramientas_usadas": ", ".join(herramientas_usadas) or "ninguna",
        "herramienta_ok": herramienta_ok,
        "cumple_criterio": juicio.cumple,
        "justificacion": juicio.justificacion,
        "datos_recuperados": ", ".join(recuperado) or "ninguno",
        "segundos_bot": round(segundos_bot, 1),
        "segundos_total": round(time.perf_counter() - inicio, 1),
        "respuesta": respuesta,
    }


def marca(ok: bool) -> str:
    """Devuelve un símbolo para mostrar un resultado en la terminal."""
    return "✔" if ok else "✘"


def leer_repeticiones() -> int:
    """Lee '--repeticiones N' de la línea de comandos (por defecto, 1)."""
    if "--repeticiones" in sys.argv:
        return max(1, int(sys.argv[sys.argv.index("--repeticiones") + 1]))
    return 1


async def main():
    inicio = time.perf_counter()
    with open(RUTA_CASOS, encoding="utf-8") as archivo:
        casos = json.load(archivo)
    repeticiones = leer_repeticiones()

    indice = await obtener_indice(forzar_nuevo="--nuevo" in sys.argv)
    segundos_indice = time.perf_counter() - inicio
    # Si hay índice, la fecha de comparación es la del momento en que se consultaron los datos.
    fecha_comparacion = indice["fecha_comparacion"] if indice else fecha_de_hoy()
    print(f"Fecha de comparación del juez: {fecha_comparacion}")
    if repeticiones > 1:
        print(f"Cada caso se ejecuta {repeticiones} veces: los modelos no responden siempre igual.")
    print()

    filas = []
    for repeticion in range(1, repeticiones + 1):
        if repeticiones > 1:
            print(f"--- Repetición {repeticion} de {repeticiones} ---")
        for numero, caso in enumerate(casos, start=1):       # enumerate numera desde 1
            fila = {"caso": numero, "repeticion": repeticion,
                    **await evaluar_caso(caso, indice, fecha_comparacion)}
            filas.append(fila)
            print(f"{numero}. {marca(fila['herramienta_ok'])} herramienta "
                  f"{marca(fila['cumple_criterio'])} contenido | {caso['pregunta']} "
                  f"({fila['segundos_total']} s; datos: {fila['datos_recuperados']})")
            if not fila["cumple_criterio"] or not fila["herramienta_ok"]:
                print(f"   → {fila['justificacion']} (herramientas usadas: {fila['herramientas_usadas']})")
            if len(filas) < len(casos) * repeticiones:
                await asyncio.sleep(PAUSA_SEGUNDOS)

    # Con varias repeticiones: tasa de aciertos de cada caso. Un caso es "inestable"
    # si a veces aprueba y a veces no: ahí la variación del modelo cambia el resultado.
    if repeticiones > 1:
        print("\nAciertos por caso (herramienta y contenido correctos a la vez):")
        for numero, caso in enumerate(casos, start=1):
            del_caso = [f for f in filas if f["caso"] == numero]
            bien = sum(f["herramienta_ok"] and f["cumple_criterio"] for f in del_caso)
            estado = "estable" if bien in (0, repeticiones) else "INESTABLE"
            print(f"{numero:>2}. {bien}/{repeticiones} {estado:<9} | {caso['pregunta']}")

    # Resumen: sum cuenta los True (True vale 1 y False vale 0).
    total = len(filas)
    ok_herramienta = sum(f["herramienta_ok"] for f in filas)
    ok_contenido = sum(f["cumple_criterio"] for f in filas)
    print(f"\nHerramienta correcta: {ok_herramienta}/{total} ({ok_herramienta / total:.0%})")
    print(f"Criterio cumplido:    {ok_contenido}/{total} ({ok_contenido / total:.0%})")
    print(f"Tiempo: índice {segundos_indice:.0f} s, total {time.perf_counter() - inicio:.0f} s "
          f"(incluye {PAUSA_SEGUNDOS * (total - 1)} s de pausas por la cuota de Gemini)")

    # DictWriter escribe una fila por diccionario; las claves son las columnas.
    # utf-8-sig hace que Excel muestre bien las tildes, y el punto y coma es el
    # separador que espera Excel en español: así cada dato queda en su columna.
    with open(RUTA_REPORTE, "w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]), delimiter=";")
        escritor.writeheader()
        escritor.writerows(filas)
    print(f"Reporte guardado en {RUTA_REPORTE}")


asyncio.run(main())
