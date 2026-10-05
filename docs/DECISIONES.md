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
- Las predicciones se limitan a 300–1500 W en Silent (1200 W hasta la v1.1.0, ver §R.4) y 1500–3000 W en Boost.

### Solo muestras estables

- Se decidió no entrenar durante arranque, transición de modo, standby, ni cuando la bomba está modulando cerca del setpoint.
- Requisitos de entrenamiento: bomba encendida, modo reconocido, valores válidos de agua/consumo/setpoint, consumo por encima de 200 W, agua al menos 1 °C por debajo del setpoint, y al menos 10 minutos desde encendido y desde el último cambio de modo.
- Motivo: evitar que el consumo transitorio de ventilador/compresor, cambios Silent↔Boost a medio camino o modulación térmica contaminen el modelo.
- Los temporizadores de estabilidad no persisten tras reiniciar Home Assistant de forma deliberada; después de un reinicio se vuelve a esperar 10 minutos antes de entrenar.
- Desde la v1.1.0 los 10 minutos se cuentan desde el `last_changed` del interruptor y del selector de modo, no desde la primera lectura que vio el cambio (ver §R.1). Tras un reinicio HA vuelve a crear los estados, así que la espera de 10 minutos se mantiene.

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
- `sensor.bdc_prediccion_consumo_silent` (aquí ponía `sensor.bdc_prediccion_silent`, que no existe: ver §R.3)
- `sensor.bdc_prediccion_consumo_boost`
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
- `last_trained` existe internamente, pero no se expone como sensor en el conjunto actual de seis entidades. Desde la v1.1.0 sale en el diagnóstico (se pierde al reiniciar).

### Limitaciones de control

- El predictor no controla directamente los dispositivos; las automatizaciones son responsables de los servicios `switch.turn_on/off` y `select.select_option`.
- La predicción Boost puede ser correcta pero no resultar en una orden de Boost si el trigger de automatización no se ejecuta. Por eso trigger y condición deben analizarse por separado.
- Un margen de subida demasiado alto hace que Boost sea conservador. El margen de bajada no impide la entrada a Boost; solo afecta las transiciones descendentes/umbral de apagado.
- Los valores iniciales de margen fueron una base de prueba (150 W subida, 300 W bajada), no una calibración cerrada. Deben afinarse con históricos de excedente, duración de Boost, importaciones y tolerancia a ciclos.

### API/dispositivo externo

- No se documentaron endpoints, autenticación, tokens, limitación de rate ni pruebas HTTP/REST con Cointra o un servicio externo.
- La integración interactúa indirectamente con entidades Home Assistant: sensor de consumo, temperatura del agua, selector de modo, switch de bomba y number de setpoint. No hay evidencia en esta conversación de una llamada API desde la integración.

## Estado recomendado para retomar

0. Hasta el 05/10/2026 las plantillas de umbrales de la piscina leían `sensor.bdc_prediccion_silent`/`_boost`, que no existen, así que nunca usaron el modelo (§R.3, ya corregido). Lo de abajo solo se puede evaluar con datos posteriores a esa fecha.
1. Mantener el apagado global existente basado en `sensor.balance_fv_produccion_consumo` si el objetivo es evitar importación de red/descarga de batería.
2. Cambiar Silent→Boost a evaluación por estado/plantilla para que cambios de `piscina_umbral_subida_boost` puedan disparar la reevaluación.
3. Aplicar el mismo mecanismo al encendido si se usa `piscina_umbral_encendido` dinámico.
4. Verificar trazas y capturar los estados anteriores durante un episodio donde debería haber Boost.
5. Solo después ajustar `piscina_margen_subida`; partir de 150 W y subirlo o bajarlo con datos reales, sin tocar `margen_bajada` para resolver un problema de entrada en Boost.

---

## R. Revisión al pasarla al estándar de las demás (v1.1.0, 05/10/2026)

Hasta la 1.0.2 el repo tenía el código y este documento, pero no pruebas,
ni traducciones que HA cargue, ni icono que HA lea, ni diagnóstico. Se puso
al nivel de Riego, Matrículas, Omada IP Groups y Cointra. Primero se
escribieron las pruebas (`tests/test_bomba_calor_predictor.py`: un HA
2026.9.4 real, con las entidades de origen puestas a mano y un reloj falso
para simular los 10 minutos de estabilidad) y se pasaron contra la 1.0.2:
todos los fallos de §R.1 salieron en rojo antes de tocar el código.

### R.1. Fallos encontrados

1. **El formulario salía con las claves en crudo** (`sensor_t_agua`,
   `entity_not_found`…): una integración custom solo carga los textos de
   `translations/<idioma>.json`; `strings.json` solo lo usa el núcleo de HA
   al compilar. Ahora hay `translations/es.json` (igual a `strings.json`) y
   `en.json`.
2. **Se podía dar de alta dos veces, y las dos entradas se pisaban el
   modelo**: todas guardan en el mismo fichero
   (`.storage/bomba_calor_predictor_model`). En casa pasó: en el registro
   quedan como borrados seis sensores `…_2` de una segunda entrada de
   septiembre. Ahora `single_config_entry: true`.
3. **Se entrenaba con la consigna desconocida.** El commit 010f6df hizo que
   `_get_float` respetase su `default`, con lo que una consigna no
   disponible pasaba a valer 28 °C y se entrenaba igualmente, aunque los
   requisitos de arriba piden «valores válidos de … setpoint» (la
   comprobación `setpoint is not None` del código quedó sin efecto). Si la
   consigna real es más baja, se aprendían muestras con la bomba
   modulando. Ahora, sin consigna, no se entrena.
4. **Un cambio de modo o un apagado entre dos lecturas no se veía.** La
   estabilidad se medía desde la primera lectura (cada 5 min) que veía el
   modo o la bomba encendida: un Silent→Boost→Silent o un off→on entre dos
   lecturas no reiniciaba la cuenta y se entrenaba con el consumo en
   transición. Con las automatizaciones de Silent↔Boost de 2 minutos es
   un caso real. Ahora se usa el `last_changed` de HA.
5. **Tras reiniciar HA no había predicción durante 5 minutos.** La primera
   lectura se hace antes de que exista la temperatura del agua; en casa se
   ve en el histórico: `unknown` de 20:20 a 20:25 y de 21:09 a 21:14 el
   04/10. Mientras, las plantillas usan su valor de reserva. Ahora se
   predice en cuanto llega la temperatura (sin entrenar).
6. **`set_learning_rate` recargaba la integración** (el listener de
   opciones recarga ante cualquier cambio), en contra de lo que dice el
   servicio («en caliente, sin reiniciar»), y reiniciaba la espera de
   estabilidad. Ahora, si solo cambia la tasa, se aplica sin recargar.
7. **Las opciones solo dejaban cambiar tres campos y no comprobaban
   nada**: una entidad mal escrita dejaba el modelo sin datos sin avisar.
   Ahora se pueden cambiar todos, con las mismas comprobaciones que el alta
   y selectores de entidad.
8. **La learning rate no tenía límites en el alta ni en las opciones** (sí
   en el servicio): con 0 el modelo no aprende nunca. Ahora el mismo rango
   en los tres sitios: 0,000001–0,1.
9. Manifest: `iot_class` decía `local_push` (calcula a partir de otras
   entidades: `calculated`), `documentation` apuntaba a
   `github.com/local/…`, sin `codeowners` ni `issue_tracker`, y una clave
   `description` que el manifest no admite.
10. La marca de prueba de HACS de la 1.0.1 (`updatetest-01`) seguía
    escribiéndose en el log en cada arranque.

### R.2. Añadido para seguir el estándar

- `diagnostics.py`: configuración en uso, pesos, muestras, predicciones,
  `last_trained`, el estado de cada entidad de origen con su `last_changed`
  y qué condición de entrenamiento faltaba en la última lectura. Es lo que
  hubo que pedir a mano en el incidente de Boost.
- Icono propio en `brand/` (el mismo dibujo que el `icon.png`/`logo.png`
  de la raíz, que nadie leía y tenía las esquinas blancas), generado por
  `docs/icono/generar.py`.
- Reauth y reconfigurar no aplican: no hay cuenta ni conexión. Las
  entidades se cambian desde Opciones.

### R.3. Las plantillas de la piscina leen sensores que no existen

Encontrado al revisar en casa, fuera de este repositorio (está en
`packages/piscina/piscina.yaml` del HA). Los sensores se llaman
`sensor.bdc_prediccion_consumo_silent` y `…_boost` (HA genera el entity_id
a partir del nombre «BDC Prediccion Consumo …»), pero las plantillas, el
docstring de `sensor.py` y este documento usaban `sensor.bdc_prediccion_silent`
y `…_boost`, que no existen. Comprobado el 05/10 con `states()`: `unknown`.
Consecuencias:

- Los umbrales usan siempre el valor de reserva del `float()`: 800 W en
  Silent y 2300 W en Boost. El de subida a Boost salía 3520 W en vez de
  ~3220 W con el modelo (2000 W), así que **Boost era más difícil de
  alcanzar de lo previsto**: encaja con el incidente «los cálculos son
  correctos pero Boost no se activa».
- «Piscina Fallo Bomba de Calor» **no puede saltar nunca**: el consumo
  esperado sale 0 y la condición exige `esperado > 0`.

**Corregido el 05/10/2026** en `packages/piscina/piscina.yaml` (decisión
de Maxi): las plantillas leen ahora `sensor.bdc_prediccion_consumo_silent`
y `…_boost`. No se renombraron los entity_id, para no tocar la integración
ni el histórico. Solo se cambiaron los nombres: los valores de reserva del
`float()` (800/2300) y los márgenes siguen igual. Entra en vigor al
recargar las entidades de plantilla o reiniciar HA.

Efecto, con los valores de ese día (Silent 1200 W, Boost 2000 W,
depuradora 1070 W, márgenes 150/300 W):

| Umbral | Antes (reserva) | Con el modelo | ¿Lo usa alguna automatización? |
|---|---|---|---|
| Encendido | 2020 W | 2420 W | sí: «Encender depuradora y bomba según excedente» |
| Subida a Boost | 3520 W | 3220 W | sí: esa misma y «Subir de Silent a Boost con excedente» |
| Apagado | 1570 W | 1970 W | no (solo se muestra) |
| Bajada a Silent | 3070 W | 2770 W | no (solo se muestra) |

Con la v1.1.1 la predicción Silent deja de estar recortada a 1200 W (§R.4):
con 1303 W, el encendido pasa a ~2520 W y el apagado a ~2070 W.

- **Arrancar exige 400 W más.** Con 2020 W la bomba arrancaba sin cubrir
  su consumo real (depuradora + Silent ≈ 2230–2400 W) y la regla «Bomba/
  Depuradora Off - < 100W» (balance FV < 100 W durante 15 min) la volvía
  a apagar: el 04/10 y el 05/10 hubo tres encendidos de solo 18–24 min
  (p. ej. 13:32→13:50 UTC del 05/10, según el logbook). Ahora arranca
  menos veces en el límite, pero sin esos ciclos.
- **El umbral de Boost baja 300 W**, pero eso apenas cambia nada mientras
  siga el fallo de `sensor.consumo_piscina` (abajo). Desde el 01/09 no
  había entrado nunca en Boost.
- **«Piscina Fallo Bomba de Calor» empieza a funcionar**: salta si, con
  la bomba encendida y sin llegar a la temperatura, consume menos del 40 %
  de lo previsto (480 W en Silent, 800 W en Boost) durante 5 min. El
  compresor arranca 2–4 min después de encender el interruptor (visto el
  05/10), así que el arranque no debería dispararla. Ninguna automatización
  ni panel la usa todavía: solo cambia su propio estado.
- Mientras la integración no tenga predicción (arranque de HA antes de la
  v1.1.0, o si falla), las plantillas vuelven a los valores de reserva,
  igual que antes.

**Encontrado después, sin corregir (lo decide Maxi):** el mismo
`piscina.yaml` lee otros dos sensores que tampoco existen. Los reales del
medidor de la piscina son `sensor.consumo_bomba_piscina` (canal A),
`sensor.consumo_depuradora` (canal B) y `sensor.meter_piscina_power_ab`
(la suma).

- `sensor.consumo_piscina` (en «Piscina Excedente Exportacion» y «Piscina
  Balance Real») vale siempre 0. El excedente es solo lo que se exporta, sin
  sumar lo que ya gasta la piscina. Con la piscina en Silent (~2400 W),
  subir a Boost exige exportar además ~3200 W, casi imposible: esta es la
  causa principal de que no entre nunca en Boost. Al arrancar no influye,
  porque con todo apagado la piscina gasta ~13 W.
- `sensor.consumo_depuradora_piscina` (en «Piscina Fallo Depuradora») vale
  siempre 0, así que esa alarma se enciende cada vez que funciona la
  depuradora: tres veces el 05/10. Ninguna automatización la usa.

### R.4. El tope de 1200 W en Silent se quedaba corto (subido a 1500 W en la v1.1.1)

Con 1136 muestras, la predicción Silent estaba pegada al tope (1200 W): el
modelo sin recortar da ~1303 W a 26 °C. El error histórico alterna entre
~−150 W y ~+130 W: el consumo real en Silent va de ~1050 W a ~1330 W (sube
según se calienta el agua; el 05/10, a 26 °C, 1305–1335 W). Con el tope,
el umbral de encendido de la piscina se quedaba ~130 W corto.

**Decidido el 05/10/2026 (v1.1.1): tope Silent a 1500 W.** Cubre el agua a
32 °C (el modelo da ~1385 W) y no se solapa con Boost, que empieza en
1500 W. El tope solo recorta la predicción publicada: el entrenamiento
siempre ha usado la predicción sin recortar, así que el modelo aprendido no
cambia. Lo comprueba la prueba «tope de la predicción Silent» (en rojo con
la v1.1.0).

### R.5. Sin dispositivo, a propósito

Se probó a agrupar los seis sensores en un dispositivo de servicio, como en
las demás integraciones. En HA 2026.9 eso hace que una instalación nueva
genere los entity_id con el nombre del dispositivo delante
(`sensor.bomba_calor_predictor_sc984_bdc_…`); en casa no cambiarían (los fija
el registro), pero cualquier reinstalación rompería las plantillas. No
compensa en una integración que solo calcula.

### R.6. Identificadores y datos que no se deben cambiar

- `unique_id`: `{entry_id}_{prediccion|error|muestras}_{silent|boost}`.
- Nombres de las entidades (de ellos salen los entity_id en una instalación
  nueva): «BDC Prediccion Consumo Silent/Boost», «BDC Error Prediccion
  Silent/Boost», «BDC Muestras Silent/Boost».
- La entrada es la versión 1; `data` con las claves del formulario y
  `options` con las que se cambien después (pisan a `data`). La de casa
  tiene además `sensor_t_ext`, de la versión de tres parámetros: no molesta.
- El modelo se guarda en `.storage/bomba_calor_predictor_model` y **no se
  borra al quitar la integración**: al darla de alta de nuevo, recupera lo
  aprendido.
