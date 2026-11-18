# ChiquiBot ⚽

Bot de Telegram sobre la Liga Profesional de Fútbol Argentino.
Sistemas Inteligentes Aplicados · ITU UNCuyo · Grupo 4

**Integrantes:** Matías Fernández, Micaela Becerra, Maximiliano López, Franco Gómez

## Qué hace

- Conversa sobre la Liga Profesional (historia de los clubes, ídolos, clásicos y curiosidades) usando **Gemini** (`gemini-3.5-flash-lite`) con la **Interactions API sin estado**.
- Tiene una personalidad propia definida en el prompt de sistema (`prompts.py`): voseo, tono futbolero, respuestas breves en texto plano y sin inventar datos.
- **Recuerda la conversación** de cada usuario en una base **SQLite**, incluso si el bot se reinicia.
- Con `/olvidar`, cada usuario puede borrar su conversación.
- **Consulta datos reales de la liga** con *function calling*: cuando le preguntan cuándo juega un equipo o cómo salió su último partido, Gemini pide la función `obtener_partidos`, el bot consulta la API de **Bzzoiro Sports Data** y el modelo responde con ese dato (fecha y hora argentina, rival, instancia, resultado).
- **Muestra la tabla de posiciones** (v7): cuando le preguntan por la tabla, en qué puesto está un equipo o cuántos puntos tiene, Gemini pide la función `obtener_tabla`, que consulta la tabla de la temporada actual en la API de Bzzoiro (`/leagues/85/standings/`) y la devuelve separada por zona.
- **Analiza opiniones con salida estructurada** (`/opinion`): el modelo devuelve un JSON con sentimiento, puntaje de 1 a 5, equipo y resumen; **Pydantic** lo valida y se guarda en SQLite. `/opiniones` muestra el resumen de todas.
- **Flujo guiado** (`/prode`): un `ConversationHandler` conduce el pronóstico de un partido real paso a paso (partido → resultado → confirmación). La IA interpreta el resultado escrito como quieras ("gana Boca 2 a 1") y el pronóstico se guarda en SQLite. Con `/cancelar` se interrumpe en cualquier paso.
- **Evaluación** (`evaluar.py`): 10 casos de prueba propios que verifican si el modelo usa la herramienta correcta y si la respuesta cumple un criterio, con otro modelo como juez. Desde la v7, el juez compara la respuesta con los datos reales (RAG) y los datos de la API se consultan una sola vez y se guardan en un índice.

## Estructura

| Archivo | Para qué sirve |
|---|---|
| `bot.py` | Bot principal: handlers de Telegram y consulta a Gemini |
| `prompts.py` | Prompt de sistema (identidad, tarea, estilo, formato y honestidad) |
| `db.py` | Memoria: guarda, lee y borra el historial en SQLite |
| `herramientas.py` | Funciones propias: `obtener_partidos` y `obtener_tabla` (API de Bzzoiro), sus declaraciones para el modelo y el despachador. También `proximos_partidos_liga`, que usa el prode, y la precarga de datos que usa la evaluación |
| `analisis.py` | Análisis de opiniones con salida estructurada (esquema Pydantic) |
| `pronosticos.py` | Flujo guiado `/prode` con `ConversationHandler` |
| `evaluar.py` | Evaluación automática del bot, con índice de datos y juez con datos reales |
| `casos_prueba.json` | Los 10 casos de prueba de la evaluación |
| `prueba_herramientas.py` | Prueba aislada de las funciones, sin Telegram ni Gemini |
| `requirements.txt` | Dependencias |
| `.env.example` | Plantilla de variables de entorno |

## Cómo correrlo

Requiere Python 3.10 o superior.

1. Clonar el repositorio:
   ```
   git clone https://github.com/MaxiLopezVidela/ChiquiBot.git
   cd ChiquiBot
   ```
2. Crear y activar el entorno virtual:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Instalar las dependencias:
   ```
   pip install -r requirements.txt
   ```
4. Crear el `.env` a partir de la plantilla y completarlo:
   ```
   copy .env.example .env
   ```
   - `TELEGRAM_TOKEN`: token del bot, obtenido con @BotFather.
   - `GEMINI_API_KEY`: clave de Google AI Studio.
   - `GEMINI_MODEL`: modelo de Gemini (por defecto, `gemini-3.5-flash-lite`).
   - `BSD_API_KEY`: clave gratuita de [Bzzoiro Sports Data](https://sports.bzzoiro.com/) (registrándose en el sitio).
5. (Opcional) Probar la función sin Telegram:
   ```
   python prueba_herramientas.py
   ```
6. (Opcional) Evaluar el bot, sin Telegram. Tarda un par de minutos y deja el reporte en `resultados_evaluacion.csv`:
   ```
   python evaluar.py
   ```
   La primera vez arma `indice_evaluacion.json` con los datos de la API y lo reutiliza durante 30 minutos. Opciones:
   - `python evaluar.py --nuevo`: vuelve a consultar la API aunque el índice esté vigente.
   - `python evaluar.py --repeticiones 3`: ejecuta cada caso 3 veces y muestra cuántas veces aprobó cada uno.
7. Ejecutar el bot:
   ```
   python bot.py
   ```
   Se detiene con `Ctrl+C`.

## Comandos

| Comando | Qué hace |
|---|---|
| `/start` | Presentación de ChiquiBot |
| `/help` | Lista de comandos |
| `/olvidar` | Borra la conversación guardada y empieza de cero |
| `/prode` | Pronosticá un partido de la liga paso a paso |
| `/cancelar` | Cancela un prode en curso |
| `/opinion <texto>` | Analiza tu opinión de un partido o un equipo y la guarda |
| `/opiniones` | Resumen de las opiniones de los hinchas |

## Seguridad y privacidad

- El `.env` (token y claves), la base `chiquibot.db`, el reporte `resultados_evaluacion.csv` y el índice `indice_evaluacion.json` **no se suben** al repositorio: están en el `.gitignore`.
- Mientras se use el plan gratuito de Gemini, no se deben enviar datos personales ni información sensible al bot.
## Dificultades y decisiones

### La API de partidos se satura en horario pico (17/11/2026)

En el ensayo del video del Hito 4 (15:37), la API de Bzzoiro no respondió dentro de los 10 segundos de espera (`httpx.ReadTimeout`) en la función propia, en el `/prode` y en tres casos de la evaluación. **El bot no se cayó**: avisó al usuario que no pudo consultar el dato y siguió funcionando. Pero sin datos no se podía mostrar la función ni el flujo guiado.

Medimos los tiempos con un script aparte (`medir_api.py`, que no forma parte del repositorio):

| Hora | Consulta | Resultado |
|---|---|---|
| 15:54 | Página principal de Bzzoiro | 23,7 s |
| 15:54 | API, 5 partidos | 8,1 s |
| 15:54 | API, 50 partidos (lo que pedía el bot) | más de 60 s ❌ |
| 16:02 | Partidos de los próximos 10 días | 9,4 s, 9 partidos ✅ |
| 16:02 | Partidos de los últimos 14 días | más de 60 s ❌ |
| 16:15 | Últimos 14 días, máximo 10 | más de 60 s ❌ |
| 16:15 | Solo una semana (5 al 9/11) | 1,1 s, 15 partidos ✅ |
| 20:40 | Semana del 10 al 17/11 | 1,1 s, 0 partidos (semana sin fecha) |
| 20:40 | Semanas del 5 al 9/11 y del 29/10 al 4/11 | 1,0 s y 2,3 s, 15 partidos cada una ✅ |

**Conclusión:** el tiempo de respuesta de la API **depende del horario**. Las mismas consultas tardaron más de 60 s a la tarde y 1 o 2 s a la noche, y a la tarde hasta la página web de Bzzoiro tardaba 24 s, sin relación con nuestro bot. Todo indica que la API se satura con el tráfico en horario pico (a esa hora había partidos en vivo de varias ligas), aunque no lo podemos confirmar porque no vemos sus servidores. Cuando está saturada, las consultas grandes son las primeras en fallar. Por eso el bot no puede controlar cuánto tarda la API, pero sí cuánto le pide y qué hace si no responde.

**Cambios en `herramientas.py`:**
- Se consulta **por rango de fechas** (`date_from` / `date_to`) y no "los últimos 50 partidos". Los próximos partidos salen de una sola consulta (de hoy a 14 días), compartida por la función propia y el `/prode`. Los últimos resultados se buscan **de a una semana hacia atrás**, hasta encontrar los del equipo (máximo 6 semanas).
- La espera por consulta pasó de 10 a **20 segundos**, con **un reintento** si la API no responde.
- Cada consulta se **guarda 5 minutos** (caché en memoria): los partidos no cambian minuto a minuto y así se evita repetir consultas.
- Al arrancar, el bot **precarga** los próximos partidos.

### El juez de la evaluación no veía los datos de la API (resuelto en la v7)

En la evaluación del Hito 4, los casos 1 y 3 salieron con ✘ en contenido aunque el bot respondió bien: la fecha y el rival coincidían con lo que devuelve la API. El modelo juez no recibía el resultado de la herramienta ni la fecha de hoy, así que una fecha de 2026 le parecía inventada (un **falso negativo**). También pasó lo contrario: en el caso 7 aprobó una respuesta que nombraba al goleador de 1967, algo que el prompt prohíbe.

**Cambios en `evaluar.py` (v7):**
- **Fecha de comparación:** el juez recibe la fecha y la hora de Argentina del momento en que se consultaron los datos, y la regla de que un partido posterior a esa fecha es futuro, no inventado.
- **Resultados de las herramientas:** `consultar_gemini` (en `bot.py`) acepta una lista `resultados` donde anota lo que devolvió cada función. El juez recibe esos datos, los mismos que vio el bot.
- **Índice y RAG:** antes de los casos, `evaluar.py` consulta una sola vez a la API los próximos partidos, los resultados de las últimas 6 semanas y la tabla (`herramientas.precargar_datos`), y arma un índice por equipo que guarda en `indice_evaluacion.json`. Para cada caso **recupera** del índice los datos de los equipos mencionados en la pregunta o la respuesta (por nombre o apodo: "el Rojo", "el Xeneize") y la tabla si se habla de posiciones, y los **agrega** a la consulta del juez, que **genera** su veredicto comparando con datos reales. Sirve también cuando el bot no usó la herramienta: el juez igual tiene el dato correcto para comparar.
- **Más rápida:** mientras el índice está vigente (30 minutos), las herramientas del bot toman los datos de la caché en lugar de esperar a la API. En la primera corrida se hacen 9 consultas a la API; en las siguientes, ninguna. El reporte muestra los segundos de cada caso y qué datos se recuperaron.
- **Regla para el caso 7:** si el criterio pide no dar el nombre de un jugador, cualquier nombre en la respuesta lo incumple, aunque después aclare que no está confirmado.

### Los resultados cambian entre corridas (v7)

Corriendo `evaluar.py` varias veces sin cambiar nada, el resultado variaba (9/10 en una corrida, 10/10 en otra). Los dos modelos, el bot y el juez, eligen cada palabra con algo de azar (la *temperatura*), así que una sola corrida no alcanza para medir.

- **Juez con temperatura 0:** así da (casi siempre) el mismo veredicto ante la misma respuesta. Al bot no se le cambia: se lo evalúa tal como responde a los usuarios.
- **`--repeticiones N`:** cada caso se ejecuta N veces y se informa su tasa de aciertos. Un caso que a veces aprueba y a veces no se marca como INESTABLE.

Con 3 repeticiones (18/11/2026) medimos el bot, ajustamos el prompt y volvimos a medir:

| Corrida | Herramienta | Criterio | Casos con fallas |
|---|---|---|---|
| Antes de los ajustes | 27/30 (90 %) | 26/30 (87 %) | 4: no preguntaba cuál Independiente (0/3). 7: nombraba al goleador de 1967 con una aclaración (2/3) |
| Ajuste 1: ejemplo de nombre ambiguo en el prompt y en la descripción de `obtener_partidos`, y regla de no dar nombres ni con aclaraciones | 30/30 (100 %) | 28/30 (93 %) | 8: empezó a preguntar "¿cuál Independiente?" también en una pregunta de historia (1/3) |
| Ajuste 2: la pregunta de cuál equipo es solo para partidos y tabla | 30/30 (100 %) | 30/30 (100 %) | Ninguno |

El ajuste 1 arregló dos casos y rompió otro, y la evaluación lo detectó enseguida: para eso sirve tenerla automatizada.

### La tabla suma el Apertura y el Clausura

La API devuelve la tabla separada por zona ("Clausura, Zona A" y "Clausura, Zona B"), pero cada equipo tiene 32 partidos jugados: suma los dos torneos del año. Es la tabla acumulada del año, separada por zona, no la del Clausura solo.

### Alucinaciones en datos históricos

Desde el Hito 2, el modelo tiende a inventar goleadores y resultados de partidos viejos. Se redujo con reglas de honestidad en el prompt, pero no se elimina del todo (ver el caso 7 de la evaluación).
