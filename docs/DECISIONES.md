# Registro de decisiones y diagnóstico — `bomba_calor_predictor`

> Documento de continuidad para otra sesión de Claude Code. Se centra en decisiones, pruebas, incidencias y limitaciones; no pretende ser un manual de usuario.

## Alcance y nomenclatura

- La integración local se llama `bomba_calor_predictor` y está orientada a una bomba de calor de piscina SC984.
- La conversación se centró exclusivamente en predicción de consumo por modo (`Silent_heat` / `Boost_heat`) y en automatizaciones Home Assistant para decidir arranque y cambio de modo según disponibilidad solar.
- No se trató una integración Cointra remota, endpoints HTTP, credenciales, tokens ni autenticación contra API. Si el nombre de trabajo era «cointra / bomba_calor_predictor», la evidencia disponible aquí cubre solo la parte `bomba_calor_predictor` local basada en entidades de Home Assistant.

## Decisiones de diseño consolidadas

### Modelo separado por modo

- Se eligieron modelos independientes para `silent` y `boost`, en vez de un único modelo con el modo como variable.
- Motivo: los dos modos tienen perfiles de consumo diferentes y era más simple y trazable mantener pesos, predicciones, errores y contador de muestras por separado.
- El coordinador calcula predicciones para ambos modos en cada actualización; no hace falta que el modo Boost esté activo para **calcular** su predicción. Solo entrena los pesos del modo que esté activo y estable.

### Simplificación del modelo lineal

- Inicialmente se documentó un modelo con temperatura exterior y temperatura de agua, y también se consideró un término de diferencia de temperaturas.
- El término de diferencia `T_agua - T_ext` se descartó porque es combinación lineal de las otras dos variables y producía colinealidad perfecta: los pesos individuales perdían interpretación y el SGD convergía peor.
- En la versión posterior se redujo aún más el modelo a `consumo = w0 + w2 * T_agua`.
- Motivo de eliminar `T_ext`: la bomba trabaja solo durante verano, aproximadamente con 25–35 °C exteriores; dentro de ese rango se consideró que el COP era suficientemente estable y que `T_ext` añadía poco valor predictivo frente al ruido y complejidad extra.
- El modelo de dos parámetros converge más rápido y con menos ruido, a cambio de no capturar variaciones de consumo atribuibles a clima fuera del supuesto de operación estival.

### SGD con protecciones

- El entrenamiento es online mediante SGD para que el modelo se adapte a la instalación real.
- Se usan priors iniciales específicos de fabricante/SC984 para Silent y Boost, clamps de predicción por modo y límites de pesos.
- Motivo: impedir divergencias y resultados físicamente absurdos mientras el modelo tiene pocas muestras o recibe muestras anómalas.
- Las predicciones se limitan a 300–1200 W en Silent y 1500–3000 W en Boost.

### Solo muestras estables

- Se decidió no entrenar durante arranque, transición de modo, standby, ni cuando la bomba está modulando cerca del setpoint.
- Requisitos de entrenamiento: bomba encendida, modo reconocido, valores válidos de agua/consumo/setpoint, consumo por encima de 200 W, agua al menos 1 °C por debajo del setpoint, y al menos 10 minutos desde encendido y desde el último cambio de modo.
- Motivo: evitar que el consumo transitorio de ventilador/compresor, cambios Silent↔Boost a medio camino o modulación térmica contaminen el modelo.
- Los temporizadores de estabilidad no persisten tras reiniciar Home Assistant de forma deliberada; después de un reinicio se vuelve a esperar 10 minutos antes de entrenar.

### Actualización cada 5 minutos

- El `DataUpdateCoordinator` actualiza cada 5 minutos.
- Motivo implícito: el proceso térmico y el consumo estable de la bomba evolucionan lentamente; una cadencia de 5 minutos limita escritura y ruido sin perder información útil para un SGD de consumo.
- Consecuencia: cambios de modo y aprendizaje no son instantáneos. Las automatizaciones de excedente no deben esperar que el predictor reaccione a segundos.

### Persistencia y configuración

- Pesos y número de muestras se guardan con `Store` bajo la clave `bomba_calor_predictor_model`.
- Se expusieron servicios para resetear el modelo y cambiar la learning rate.
- Se corrigió/pensó la persistencia de `learning_rate`: la versión actual mezcla `entry.data` y `entry.options` al iniciar, y el servicio escribe el valor actualizado en `entry.options` mediante `async_update_entry`.
- Motivo: que los cambios de learning rate no desaparezcan tras recargar/reiniciar la integración.
- La configuración se hace mediante Config Flow; se validan los entity_id esenciales al crear la entrada.

### Una única plataforma de entidades

- La integración reenvía solo la plataforma `sensor`.
- Expone seis sensores: predicción, error y muestras para cada modo.
- Motivo: la integración no controla el relé ni el selector; su responsabilidad es modelar y publicar señales. El control operativo queda en automatizaciones externas de Home Assistant.

## Diseño energético de piscina

### Carga combinada, no bomba aislada

- Se aclaró que la depuradora consume 1070 W y siempre está encendida cuando se usa la bomba de calor.
- Decisión: todos los umbrales para arrancar o cambiar de modo deben contemplar la carga combinada `depuradora + bomba`, no únicamente el consumo previsto de la bomba.
- El valor se mantiene como helper ajustable `input_number.piscina_depuradora_consumo`, con valor inicial de 1070 W, en vez de estar hardcodeado en varias automatizaciones.

### Sensores de excedente y umbrales

Se prepararon sensores template para separar señal energética y política de control:

- `sensor.piscina_excedente_exportacion`:
  - Fórmula: `(-sensor.deye_grid_ct_power) + sensor.consumo_piscina`.
  - Intención: estimar cuánta potencia FV podría absorber el circuito de piscina, añadiendo a la exportación neta la carga de piscina actualmente conectada.
- `sensor.piscina_balance_real`:
  - Fórmula: `sensor.balance_fv_produccion_consumo + sensor.consumo_piscina`.
  - Intención: expresar el balance FV como si la piscina no estuviera consumiendo.
- `sensor.piscina_umbral_encendido`:
  - `depuradora + predicción_silent + margen_subida`.
- `sensor.piscina_umbral_subida_boost`:
  - `depuradora + predicción_boost + margen_subida`.
- `sensor.piscina_umbral_bajada_silent`:
  - `depuradora + predicción_boost - margen_bajada`.
- `sensor.piscina_umbral_apagado`:
  - `depuradora + predicción_silent - margen_bajada`.

Motivo de esta estructura: los valores de consumo previstos y los márgenes cambian, pero las automatizaciones pueden seguir siendo declarativas y comparar una señal de excedente con un umbral publicado.

### Histéresis separada por dirección

- Se crearon dos helpers distintos:
  - `piscina_margen_subida`, inicial 150 W.
  - `piscina_margen_bajada`, inicial 300 W.
- Motivo: evitar oscilaciones rápidas. Exigir margen adicional para entrar/elevar modo y permitir una banda más amplia antes de bajar/apagar.
- La recomendación de partida fue 150 W / 300 W; se dejó claro que `margen_subida` afecta el arranque y Silent→Boost, mientras que `margen_bajada` afecta Boost→Silent y el umbral de apagado.
- Regla operativa sugerida: el margen de bajada debe ser mayor que el de subida; 2× era el punto de partida práctico, no una ley rígida.

### Separación entre optimización y protección

- Se concluyó que el predictor es apropiado para decidir **cuándo arrancar** y **cuándo pasar de Silent a Boost**.
- El usuario prefirió conservar el apagado basado en `sensor.balance_fv_produccion_consumo` / detección de importación de red o descarga de batería.
- Se aceptó esa decisión: para corte de protección, el balance neto real de sistema es más directo que una predicción de cada carga.
- Arquitectura resultante:
  - Entrada / selección de modo: predictor + carga de depuradora + márgenes.
  - Reducción Boost→Silent y apagado: señales de balance/importación ya existentes, con sus retardos.

## Automatizaciones: evolución y diagnóstico

### Primera aproximación: umbrales fijos de red

Se partía de automatizaciones con `sensor.deye_grid_ct_power`:

- Arranque bomba+depuradora si la red exportaba por debajo de `-2200 W` durante 1 minuto.
- Arranque en Boost si la red exportaba por debajo de `-3100 W`.
- Silent→Boost con triggers de `-950 W` y `-1250 W` durante 2 minutos.

Se consideraron insuficientes/no óptimas para el nuevo objetivo porque eran cifras fijas y no representaban explícitamente la carga variable prevista de la bomba ni la depuradora de 1070 W.

### Cambio propuesto: umbrales dinámicos

Se propuso reemplazar los umbrales fijos por:

- Arranque cuando `piscina_excedente_exportacion > piscina_umbral_encendido`.
- Arranque directamente en Boost si supera `piscina_umbral_subida_boost`.
- Silent→Boost con el mismo umbral dinámico de Boost.

Esto reutiliza las predicciones del modelo y suma siempre el coste de depuradora.

### Comportamiento inesperado: Boost no activaba

- El usuario indicó que la integración calculaba valores correctos, pero las automatizaciones no activaban Boost.
- Se evaluó si el problema era que la bomba estaba en Silent. Conclusión: no; estar en Silent no invalida la predicción Boost, porque el coordinador calcula las dos predicciones en cada ciclo.
- Sí puede haber poca calidad de modelo Boost si recibe pocas muestras, porque el SGD solo entrena el modo activo. Sin embargo, en el storage adjunto había 120 muestras Boost y 200 Silent, por lo que no parecía un modelo completamente sin entrenar.

### Causa probable: trigger `numeric_state` con umbral que cambia

El diagnóstico clave fue el comportamiento de esta forma:

```yaml
trigger:
  - trigger: numeric_state
    entity_id: sensor.piscina_excedente_exportacion
    above: sensor.piscina_umbral_subida_boost
    for:
      minutes: 2
```

- `numeric_state` dispara cuando el sensor observado cruza el umbral.
- En este caso, el umbral también es dinámico porque depende de la predicción Boost y de `piscina_margen_subida`.
- Si el excedente ya estaba por encima y luego el umbral baja, o si el helper/una predicción cambia dejando la condición verdadera, puede no existir un cruce del sensor observado y la automatización no se relanza.
- Esta es la hipótesis principal para «los cálculos son correctos pero Boost no se activa».

### Corrección propuesta para Silent→Boost

Se propuso sustituir el trigger único `numeric_state` por triggers de estado sobre todas las señales relevantes y una condición template de comparación. Patrón:

- Triggers para cambios de:
  - `sensor.piscina_excedente_exportacion`
  - `sensor.piscina_umbral_subida_boost`
  - `select.modo`
  - `switch.bomba_de_calor`
  - `switch.depuradora`
- Condiciones: temporada activa, depuradora y bomba encendidas, modo Silent, y `excedente > umbral_boost`.
- Espera de 2 minutos dentro de las acciones y revalidación de condiciones al final.
- `mode: restart` para que cualquier cambio relevante reinicie el temporizador y solo cambie a Boost tras dos minutos de condición continua.

Este patrón cubre el caso en el que el umbral cambia sin que el excedente cruce nada.

### Automatizaciones de reducción y apagado existentes

El usuario mostró:

- Boost→Silent si `sensor.balance_fv_produccion_consumo < 0 W` durante 2 minutos.
- Apagar depuradora y bomba si `sensor.balance_fv_produccion_consumo < 100 W` durante 4 minutos.

Se consideró una escalera razonable:

1. Ante pérdida de superávit neto, bajar primero de Boost a Silent.
2. Si la situación persiste/empeora, cortar ambas cargas.

Ajustes sugeridos, no necesariamente aplicados:

- Añadir condición `switch.bomba_de_calor == on` a Boost→Silent para no cambiar el selector si la bomba ya está apagada.
- Para apagado, condicionar temporada o que al menos una de las cargas esté encendida, para evitar ejecuciones innecesarias; el usuario priorizó conservar la lógica basada en balance global.

## Validaciones y datos disponibles

### Modelo persistido observado

El archivo de storage adjunto mostraba:

```json
{
  "silent": {"w0": 950.315, "w2": 6.512434, "samples": 200},
  "boost": {"w0": 1899.62, "w2": 3.87772, "samples": 120}
}
```

Interpretación:

- Hay muestras suficientes para que Boost no sea simplemente el prior sin aprender.
- Para diagnosticar un caso concreto de no activación, todavía faltaría una captura temporal de estados y trazas de automatización.

### Valores que se pidió revisar en un incidente Boost

Para una evaluación determinista de un evento fallido se pidió recopilar simultáneamente:

- `select.modo`
- `switch.bomba_de_calor`
- `switch.depuradora`
- `sensor.consumo_bomba_piscina`
- `sensor.consumo_depuradora_piscina`
- `sensor.consumo_piscina`
- `sensor.deye_grid_ct_power`
- `sensor.balance_fv_produccion_consumo`
- `sensor.piscina_excedente_exportacion`
- `sensor.piscina_umbral_encendido`
- `sensor.piscina_umbral_subida_boost`
- `sensor.bdc_prediccion_silent`
- `sensor.bdc_prediccion_boost`
- `sensor.bdc_muestras_silent`
- `sensor.bdc_muestras_boost`
- atributos `w0_base`, `w2_t_agua` y `muestras` de ambas predicciones.

También conviene consultar la traza de Home Assistant de la automatización de Silent→Boost: confirma si el trigger ocurrió, qué condición falló y si el `for` fue interrumpido.

## Limitaciones y pendientes

### Pendientes técnicos

- Confirmar mediante trazas que el fallo de Boost era exactamente el no-cruce de un umbral dinámico, en vez de una condición de temporada, selector, relé, `for` interrumpido o indisponibilidad puntual.
- Aplicar la versión robusta de Silent→Boost basada en triggers de estado + condición template + delay/revalidación, si no se ha aplicado aún.
- Aplicar el mismo patrón al arranque si se usan umbrales dinámicos; la automatización de arranque puede sufrir el mismo efecto si cambia el umbral de encendido sin que el excedente cruce.
- Verificar que `select.modo` publica exactamente `Silent_heat` y `Boost_heat`, y que esos valores coinciden con los configurados en la entrada de la integración. Una discrepancia exacta de string hace que el modo sea `None` para el coordinador y bloquea entrenamiento/error por modo.

### Limitaciones del modelo

- El modelo no incluye temperatura exterior; se asume operación estival con COP relativamente estable. Si se usa fuera de ese rango, puede perder precisión.
- El modelo aprende solo muestras estables, por diseño. Si Boost dura siempre menos de 10 minutos, no generará muestras de entrenamiento aunque el cambio de modo funcione.
- Tras reiniciar Home Assistant se descartan 10 minutos de potencial entrenamiento hasta que la bomba y el modo vuelvan a ser estables.
- Los clamps limitan salidas por seguridad: pueden ocultar una deriva de pesos si el consumo real sale persistentemente fuera de los rangos configurados.
- `last_trained` existe internamente, pero no se expone como sensor en el conjunto actual de seis entidades.

### Limitaciones de control

- El predictor no controla directamente los dispositivos; las automatizaciones son responsables de los servicios `switch.turn_on/off` y `select.select_option`.
- La predicción Boost puede ser correcta pero no resultar en una orden de Boost si el trigger de automatización no se ejecuta. Por eso trigger y condición deben analizarse por separado.
- Un margen de subida demasiado alto hace que Boost sea conservador. El margen de bajada no impide la entrada a Boost; solo afecta las transiciones descendentes/umbral de apagado.
- Los valores iniciales de margen fueron una base de prueba (150 W subida, 300 W bajada), no una calibración cerrada. Deben afinarse con históricos de excedente, duración de Boost, importaciones y tolerancia a ciclos.

### API/dispositivo externo

- No se documentaron endpoints, autenticación, tokens, limitación de rate ni pruebas HTTP/REST con Cointra o un servicio externo.
- La integración interactúa indirectamente con entidades Home Assistant: sensor de consumo, temperatura del agua, selector de modo, switch de bomba y number de setpoint. No hay evidencia en esta conversación de una llamada API desde la integración.

## Estado recomendado para retomar

1. Mantener el apagado global existente basado en `sensor.balance_fv_produccion_consumo` si el objetivo es evitar importación de red/descarga de batería.
2. Cambiar Silent→Boost a evaluación por estado/plantilla para que cambios de `piscina_umbral_subida_boost` puedan disparar la reevaluación.
3. Aplicar el mismo mecanismo al encendido si se usa `piscina_umbral_encendido` dinámico.
4. Verificar trazas y capturar los estados anteriores durante un episodio donde debería haber Boost.
5. Solo después ajustar `piscina_margen_subida`; partir de 150 W y subirlo o bajarlo con datos reales, sin tocar `margen_bajada` para resolver un problema de entrada en Boost.
