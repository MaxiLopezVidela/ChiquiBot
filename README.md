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