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
- **Analiza opiniones con salida estructurada** (`/opinion`): el modelo devuelve un JSON con sentimiento, puntaje de 1 a 5, equipo y resumen; **Pydantic** lo valida y se guarda en SQLite. `/opiniones` muestra el resumen de todas.
- **Flujo guiado** (`/prode`): un `ConversationHandler` conduce el pronóstico de un partido real paso a paso (partido → resultado → confirmación). La IA interpreta el resultado escrito como quieras ("gana Boca 2 a 1") y el pronóstico se guarda en SQLite. Con `/cancelar` se interrumpe en cualquier paso.
- **Evaluación** (`evaluar.py`): 10 casos de prueba propios que verifican si el modelo usa la herramienta correcta y si la respuesta cumple un criterio, con otro modelo como juez.

## Estructura

| Archivo | Para qué sirve |
|---|---|
| `bot.py` | Bot principal: handlers de Telegram y consulta a Gemini |
| `prompts.py` | Prompt de sistema (identidad, tarea, estilo, formato y honestidad) |
| `db.py` | Memoria: guarda, lee y borra el historial en SQLite |
| `herramientas.py` | Funciones propias: `obtener_partidos` (API de Bzzoiro), su declaración para el modelo y el despachador. También `proximos_partidos_liga`, que usa el prode |
| `analisis.py` | Análisis de opiniones con salida estructurada (esquema Pydantic) |
| `pronosticos.py` | Flujo guiado `/prode` con `ConversationHandler` |
| `evaluar.py` | Evaluación automática del bot |
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

- El `.env` (token y claves), la base `chiquibot.db` y el reporte `resultados_evaluacion.csv` **no se suben** al repositorio: están en el `.gitignore`.
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

### El juez de la evaluación no ve los datos de la API

En la evaluación (`evaluar.py`), los casos 1 y 3 salieron con ✘ en contenido aunque el bot respondió bien: la fecha y el rival coincidían con lo que devuelve la API. El modelo juez no recibe el resultado de la herramienta ni la fecha de hoy, así que una fecha de 2026 le parece inventada. También pasó lo contrario: en el caso 7 aprobó una respuesta que nombraba al goleador de 1967, algo que el prompt prohíbe. **Pendiente para la entrega final:** pasarle al juez la fecha actual y el resultado de la herramienta, y revisar a mano los casos que juzga.

### Alucinaciones en datos históricos

Desde el Hito 2, el modelo tiende a inventar goleadores y resultados de partidos viejos. Se redujo con reglas de honestidad en el prompt, pero no se elimina del todo (ver el caso 7 de la evaluación).
