"""Icono de Bomba Calor Predictor: una gráfica de consumo (amarilla) sobre
dos ondas de agua, en un cuadrado redondeado turquesa.

Uso: python3 generar.py <carpeta>  (escribe icon.png y icon@2x.png)
"""
import math
import sys
from PIL import Image, ImageDraw


def dibujar(lado: int) -> Image.Image:
    S = 4  # sobremuestreo para bordes suaves
    W = lado * S
    u = W / 256  # unidades de un lienzo de 256

    # Fondo: cuadrado redondeado con un degradado vertical suave.
    fondo = Image.new("RGBA", (W, W))
    top, bottom = (38, 182, 196), (19, 107, 122)
    df = ImageDraw.Draw(fondo)
    for y in range(W):
        t = y / (W - 1)
        df.line([(0, y), (W, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mascara = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, W - 1, W - 1], radius=56 * u, fill=255)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    img.paste(fondo, (0, 0), mascara)

    def trazo(puntos, grosor, color):
        """Línea gruesa con uniones y extremos redondeados."""
        capa = Image.new("L", (W, W), 0)
        d = ImageDraw.Draw(capa)
        pts = [(x * u, y * u) for x, y in puntos]
        d.line(pts, fill=255, width=round(grosor * u), joint="curve")
        r = grosor * u / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
        img.paste(Image.new("RGBA", (W, W), color), (0, 0), capa)

    def onda(centro, grosor, color):
        """Una onda sinusoidal: se estampa un círculo en cada punto (con
        `line` y muchos tramos cortos, PIL deja dientes en el borde)."""
        capa = Image.new("L", (W, W), 0)
        d = ImageDraw.Draw(capa)
        r = grosor * u / 2
        for k in range(1601):
            x = 50 + k * 0.1
            y = centro - 9 * math.sin(2 * math.pi * (x - 55) / 60)
            d.ellipse([x * u - r, y * u - r, x * u + r, y * u + r], fill=255)
        img.paste(Image.new("RGBA", (W, W), color), (0, 0), capa)

    # Ondas de agua: la de arriba, blanca translúcida; la de abajo, blanca.
    onda(165, 10, (160, 206, 212, 255))
    onda(189, 10, (255, 255, 255, 255))

    # Gráfica de consumo, con un punto al final.
    amarillo = (255, 219, 94, 255)
    trazo([(60, 131), (96, 98), (117, 121), (158, 71), (193, 96)], 10, amarillo)
    capa = Image.new("L", (W, W), 0)
    r = 10 * u
    ImageDraw.Draw(capa).ellipse([193 * u - r, 96 * u - r, 193 * u + r, 96 * u + r], fill=255)
    img.paste(Image.new("RGBA", (W, W), amarillo), (0, 0), capa)

    return img.resize((lado, lado), Image.LANCZOS)


destino = sys.argv[1]
dibujar(256).save(f"{destino}/icon.png", optimize=True)
dibujar(512).save(f"{destino}/icon@2x.png", optimize=True)
print("ok")
