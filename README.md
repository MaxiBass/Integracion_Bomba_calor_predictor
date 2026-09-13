# Integracion_Bomba_calor_predictor

Integración custom de Home Assistant: predicción adaptativa de consumo
eléctrico de una bomba de calor de piscina (SC984) mediante regresión
lineal online (SGD), con modelos independientes por modo Silent/Boost.

Historial de decisiones, pruebas y limitaciones conocidas: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Instalación vía HACS

1. HACS → menú ⋮ → **Repositorios personalizados**.
2. URL: `https://github.com/MaxiBass/Integracion_Bomba_calor_predictor`,
   categoría **Integración**.
3. Instalar **Bomba Calor Predictor (SC984)** desde HACS.
4. Reiniciar Home Assistant.
5. Ajustes → Dispositivos y servicios → Añadir integración → **Bomba Calor Predictor (SC984)**,
   y seleccionar los `entity_id` de temperatura de agua, consumo, modo,
   switch de la bomba y setpoint.

## Instalación manual (sin HACS)

Copia `custom_components/bomba_calor_predictor` a
`/config/custom_components/bomba_calor_predictor` en tu HA y reinicia.
