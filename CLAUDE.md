# Integracion_Bomba_calor_predictor

Repositorio de la integración custom de Home Assistant "Bomba Calor
Predictor (SC984)" de Maxi. Es la **fuente de verdad** del código y la
fuente desde la que HACS la instala.

Es **público** a propósito, porque HACS no lee repos privados (se probó y
da 404 incluso con GitHub autenticado). El código no tiene credenciales; lo
único de casa que aparece son los `entity_id` por defecto del formulario y
los de las plantillas de la piscina en `docs/DECISIONES.md`. Las pruebas
usan entity_id inventados.

## Entorno

- Repo local: `~/Downloads/GitHub/Integracion_Bomba_calor_predictor`
- HA real por Samba: `/Volumes/config` (si no está montado:
  `osascript -e 'mount volume "smb://192.168.1.9/config"'`).
  `/Volumes/config/custom_components/bomba_calor_predictor` es donde la
  instala HACS; las plantillas que usan sus sensores están en
  `/Volumes/config/packages/piscina/`.
- Esta sesión **no tiene credenciales de GitHub**: `git add` y `git commit`
  sí, `git push` no. El push lo hace el usuario desde GitHub Desktop.
- Un clasificador de seguridad automático puede bloquear `git add` sobre
  archivos cuyo nombre suene a secreto aunque no tengan datos sensibles
  reales. Si pasa, que el usuario haga ese `git add` puntual él mismo.

## Estructura

```
custom_components/bomba_calor_predictor/
  coordinator.py  modelo SGD: lectura cada 5 min, condiciones de estabilidad, guardado
  __init__.py     alta de la entrada, servicios, learning rate en caliente
  sensor.py       los seis sensores (sin dispositivo, ver DECISIONES §R.5)
  config_flow.py  alta y opciones, con las mismas comprobaciones
  diagnostics.py  diagnóstico descargable
  services.yaml, strings.json, translations/
  brand/          icono propio (HA 2026.9 lo lee de aquí)
docs/DECISIONES.md     por qué es así; NO va en custom_components
docs/icono/generar.py  dibuja brand/icon.png e icon@2x.png
tests/test_bomba_calor_predictor.py
```

`docs/` está fuera de `custom_components/bomba_calor_predictor/` a
propósito: HACS copia esa carpeta entera a la instalación real de HA, y la
documentación no tiene que viajar.

## Antes de tocar nada, lee `docs/DECISIONES.md`

En particular:

- No cambiar los `unique_id` ni los **nombres** de los sensores: de los
  nombres salen los entity_id que usan las plantillas de la piscina (§R.6).
  Por lo mismo, los sensores no cuelgan de ningún dispositivo (§R.5).
- Solo se entrena con muestras estables; las condiciones están en
  «Solo muestras estables» y §R.1.
- Las plantillas de casa leen `sensor.bdc_prediccion_silent`/`_boost`, que
  no existen (§R.3): pendiente de que Maxi decida cómo corregirlo.

## Flujo para editar

1. Editar en el repo, dentro de `custom_components/bomba_calor_predictor/`.
2. Pasar las pruebas (abajo).
3. Commit (yo puedo). Push lo hace el usuario.
4. Subir la `version` de `manifest.json` cuando esté listo para probar: HACS
   detecta la actualización por ese número.
5. El usuario actualiza desde HACS y reinicia HA.

No copiar directamente en
`/Volumes/config/custom_components/bomba_calor_predictor` sin editar antes
en el repo: se perdería el historial de lo que realmente se probó.

## Pruebas

```bash
python3 -m venv /tmp/hav
/tmp/hav/bin/pip install homeassistant==2026.9.4 pillow
/tmp/hav/bin/python tests/test_bomba_calor_predictor.py
```

El venv de `/tmp` lo borra a medias la limpieza de macOS; si `import
homeassistant` falla, recrearlo con `python3 -m venv --clear /tmp/hav`.

## Por qué este repo es público

HACS usa una GitHub OAuth App con un único permiso, **"Access public
information (read-only)"**: no hay forma de darle acceso a repos privados
(a diferencia del Supervisor de HA, que sí admite un token en la URL para
add-ons privados).
