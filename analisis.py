"""
Capacidad de análisis de ChiquiBot (v5): análisis de sentimiento con salida estructurada.

El usuario deja una opinión sobre un partido, un equipo o un jugador con /opinion.
El modelo no responde con texto libre: devuelve un JSON que respeta el esquema de
la clase AnalisisOpinion. Pydantic valida ese JSON antes de que el bot lo use y lo
guarde en la base. Si el modelo devolviera algo con otro formato, Pydantic lanza
ValidationError y el bot avisa al usuario en lugar de guardar un dato roto.
"""

from typing import Literal

from pydantic import BaseModel, Field

from herramientas import EQUIPOS        # mismos nombres de equipos que usa la API


class AnalisisOpinion(BaseModel):
    """Resultado que debe devolver el modelo al analizar una opinión."""
    sentimiento: Literal["positivo", "negativo", "neutro"] = Field(
        description="Sentimiento general de la opinión hacia el equipo o el partido.")
    puntaje: int = Field(
        ge=1, le=5,
        description="Qué tan conforme está el hincha: 1 = muy disconforme, 3 = neutro, 5 = muy conforme.")
    equipo: str = Field(
        description="Equipo del que habla, escrito exactamente como en la lista de equipos, "
                    "o 'Ninguno' si no menciona ninguno.")
    resumen: str = Field(description="Resumen de la opinión en una oración breve, en tercera persona.")


INSTRUCCIONES = f"""
Analizás opiniones de hinchas sobre la Liga Profesional de Fútbol Argentino.
Devolvé el sentimiento, un puntaje de 1 a 5, el equipo mencionado y un resumen breve.

Para el campo equipo, usá exactamente uno de estos nombres: {", ".join(EQUIPOS)}.
Reconocé los apodos (por ejemplo, el Xeneize es Boca Juniors, el Millonario es River Plate,
el Rojo es CA Independiente, la Academia es Racing Club, el Ciclón es San Lorenzo,
el Lobo mendocino es Gimnasia y Esgrima Mendoza). Si menciona más de un equipo, elegí
aquel sobre el que opina. Si no menciona ningún equipo, escribí Ninguno.
""".strip()


async def analizar_opinion(cliente, modelo: str, texto: str) -> AnalisisOpinion:
    """
    Pide al modelo el análisis de una opinión y lo devuelve validado.
    Puede lanzar pydantic.ValidationError si la respuesta no respeta el esquema.
    """
    interaccion = await cliente.aio.interactions.create(
        model=modelo,
        system_instruction=INSTRUCCIONES,
        input=texto,
        # Salida estructurada: el modelo debe responder un JSON con este esquema.
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": AnalisisOpinion.model_json_schema(),
        },
        store=False,
    )
    # model_validate_json convierte el texto JSON en un objeto AnalisisOpinion y
    # verifica tipos y rangos (por ejemplo, que el puntaje esté entre 1 y 5).
    analisis = AnalisisOpinion.model_validate_json(interaccion.output_text)

    # El esquema no puede forzar la lista completa de equipos, así que se controla
    # en el código: un nombre que no está en la lista se guarda como "Ninguno".
    if analisis.equipo not in EQUIPOS:
        analisis.equipo = "Ninguno"
    return analisis
