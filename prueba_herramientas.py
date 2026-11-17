"""
Prueba aislada de las herramientas, sin Telegram ni Gemini.
Llama a la función directamente y muestra lo que devuelve.
"""

import asyncio                            # permite ejecutar corrutinas desde un script
import json                               # para mostrar los resultados con formato legible

from dotenv import load_dotenv            # lee el .env (la función necesita BSD_API_KEY)

load_dotenv()

from herramientas import ejecutar_funcion, obtener_partidos, proximos_partidos_liga


async def main():
    # Casos normales: consultan la API real.
    print(json.dumps(await obtener_partidos("Boca Juniors", "proximos", 2), ensure_ascii=False, indent=2))
    print(json.dumps(await obtener_partidos("River Plate", "ultimos", 2), ensure_ascii=False, indent=2))

    # Próximos partidos de toda la liga: son los que ofrece el teclado de /prode.
    print(json.dumps(await proximos_partidos_liga(), ensure_ascii=False, indent=2))

    # Casos de error: deben devolver un diccionario con la clave "error", no fallar.
    print(await obtener_partidos("Real Madrid", "proximos"))
    print(await obtener_partidos("Boca Juniors", "ayer"))
    print(await ejecutar_funcion("funcion_inexistente", {}))

asyncio.run(main())                       # pone en marcha el bucle de eventos
