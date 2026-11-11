# ChiquiBot ⚽

Bot de Telegram sobre la Liga Profesional de Fútbol Argentino.
Sistemas Inteligentes Aplicados · ITU UNCuyo · Grupo 4

**Integrantes:** Matías Fernández, Micaela Becerra, Maximiliano López, Franco Gómez

## Qué hace

- Conversa sobre la Liga Profesional (historia de los clubes, ídolos, clásicos y curiosidades) usando **Gemini** (`gemini-3.5-flash-lite`) con la **Interactions API sin estado**.
- Tiene una personalidad propia definida en el prompt de sistema (`prompts.py`): voseo, tono futbolero, respuestas breves en texto plano y sin inventar datos.
- **Recuerda la conversación** de cada usuario en una base **SQLite**, incluso si el bot se reinicia.
- Con `/olvidar`, cada usuario puede borrar su conversación.

## Estructura

| Archivo | Para qué sirve |
|---|---|
| `bot.py` | Bot principal: handlers de Telegram y consulta a Gemini |
| `prompts.py` | Prompt de sistema (identidad, tarea, estilo, formato y honestidad) |
| `db.py` | Memoria: guarda, lee y borra el historial en SQLite |
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
5. Ejecutar el bot:
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

## Seguridad y privacidad

- El `.env` (token y clave) y la base `chiquibot.db` **no se suben** al repositorio: están en el `.gitignore`.
- Mientras se use el plan gratuito de Gemini, no se deben enviar datos personales ni información sensible al bot.