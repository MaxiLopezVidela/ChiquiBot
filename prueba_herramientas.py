"""
Prueba aislada de las herramientas, sin Telegram ni Gemini.
Llama a las funciones directamente y muestra lo que devuelven.
"""

import asyncio                            # permite ejecutar corrutinas desde un script
import json                               # para mostrar los resultados con formato legible

from dotenv import load_dotenv            # lee el .env (las funciones necesitan BSD_API_KEY)

load_dotenv()

from herramientas import ejecutar_funcion, obtener_partidos, obtener_tabla, proximos_partidos_liga


def mostrar(datos) -> None:
    print(json.dumps(datos, ensure_ascii=False, indent=2))


async def main():
    # Función propia 1: consultan la API real.
    mostrar(await obtener_partidos("Boca Juniors", "proximos", 2))
    mostrar(await obtener_partidos("River Plate", "ultimos", 2))

    # Próximos partidos de toda la liga: son los que ofrece el teclado de /prode.
    mostrar(await proximos_partidos_liga())

    # Función propia 2 (v7): la tabla completa y la posición de un equipo.
    mostrar(await obtener_tabla())
    mostrar(await obtener_tabla("River Plate"))

    # Casos de error: deben devolver un diccionario con la clave "error", no fallar.
    print(await obtener_partidos("Real Madrid", "proximos"))
    print(await obtener_partidos("Boca Juniors", "ayer"))
    print(await obtener_tabla("Real Madrid"))
    print(await ejecutar_funcion("funcion_inexistente", {}))

asyncio.run(main())                       # pone en marcha el bucle de eventos
