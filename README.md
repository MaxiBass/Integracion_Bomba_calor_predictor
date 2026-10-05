# Integracion_Bomba_calor_predictor

Integración custom de Home Assistant: predicción adaptativa del consumo
eléctrico de una bomba de calor de piscina (SC984) mediante regresión
lineal online (SGD), con modelos independientes para los modos Silent y
Boost. No controla la bomba: publica predicciones para que las
automatizaciones decidan cuándo arrancarla o pasarla a Boost según el
excedente solar.

Historial de decisiones, pruebas y limitaciones conocidas: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Qué crea

Seis sensores:

| Entidad | Qué es |
| --- | --- |
| `sensor.bdc_prediccion_consumo_silent` / `_boost` | Consumo predicho (W) en cada modo, con los pesos y las muestras como atributos |
| `sensor.bdc_error_prediccion_silent` / `_boost` | Error de la última lectura en ese modo (real − predicho) |
| `sensor.bdc_muestras_silent` / `_boost` | Muestras con las que ha aprendido cada modo |

El modelo es `consumo = w0 + w2 · T_agua`. Cada 5 minutos calcula las dos
predicciones y, si la muestra es estable, aprende con la del modo activo:
bomba encendida y en un modo reconocido, consumo por encima de 200 W, agua
al menos 1 °C por debajo de la consigna y 10 minutos sin cambios de modo ni
de encendido. Lo aprendido se guarda y sobrevive a reinicios.

Servicios: `bomba_calor_predictor.reset_modelo` (vuelve a los valores del
fabricante) y `bomba_calor_predictor.set_learning_rate` (cambia la
velocidad de aprendizaje al momento).

En la ficha de la integración (Ajustes → Dispositivos y servicios):

- **Configurar**: cualquiera de las entidades de origen, los valores de los
  modos y la learning rate.
- **Descargar diagnóstico**: la configuración en uso, el modelo, el estado
  de cada entidad de origen y qué condición faltó para aprender en la
  última lectura.

Solo se puede dar de alta una vez.

## Instalación vía HACS

1. HACS → menú ⋮ → **Repositorios personalizados**.
2. URL: `https://github.com/MaxiBass/Integracion_Bomba_calor_predictor`,
   categoría **Integración**.
3. Instalar **Bomba Calor Predictor (SC984)** desde HACS.
4. Reiniciar Home Assistant.
5. Ajustes → Dispositivos y servicios → Añadir integración → **Bomba Calor Predictor (SC984)**,
   y elegir las entidades de temperatura del agua, consumo, modo,
   interruptor de la bomba y consigna.

## Instalación manual (sin HACS)

Copia `custom_components/bomba_calor_predictor` a
`/config/custom_components/bomba_calor_predictor` en tu HA y reinicia.

## Pruebas

```bash
python3 -m venv /tmp/hav
/tmp/hav/bin/pip install homeassistant==2026.9.4 pillow
/tmp/hav/bin/python tests/test_bomba_calor_predictor.py
```

Arrancan un Home Assistant real con las entidades de origen simuladas y un
reloj falso para no tener que esperar los 10 minutos de estabilidad.
