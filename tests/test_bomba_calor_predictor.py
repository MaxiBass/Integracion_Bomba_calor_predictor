"""Pruebas de la integración Bomba Calor Predictor. Se ejecutan sin pytest:

    /tmp/hav/bin/python tests/test_bomba_calor_predictor.py

Arrancan un Home Assistant real (el del venv) en un directorio temporal, con
la integración enlazada en `custom_components/`. Las entidades de las que
lee (temperatura del agua, consumo, modo, interruptor y consigna) son
estados puestos a mano, y el reloj del coordinator es falso para poder
simular los 10 minutos de estabilidad sin esperarlos: los cambios de estado
se fechan con ese mismo reloj.

Los entity_id de origen son inventados: este repositorio es público.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
BASE = RAIZ / "custom_components" / "bomba_calor_predictor"
DOMINIO = "bomba_calor_predictor"

T_AGUA = "sensor.agua_piscina"
T_AGUA_2 = "sensor.agua_piscina_retorno"
CONSUMO = "sensor.consumo_bomba"
MODO = "select.modo_bomba"
BOMBA = "switch.bomba_calor"
CONSIGNA = "number.consigna_agua"

DATOS_ALTA = {
    "sensor_t_agua": T_AGUA,
    "sensor_consumo": CONSUMO,
    "sensor_modo": MODO,
    "switch_bomba": BOMBA,
    "number_setpoint": CONSIGNA,
    "modo_silent_value": "Silent_heat",
    "modo_boost_value": "Boost_heat",
    "learning_rate": 0.0005,
}

fallos: list[str] = []


def comprobar(condicion: bool, etiqueta: str) -> None:
    if condicion:
        print(f"  OK    {etiqueta}")
    else:
        print(f"  FALLO {etiqueta}")
        fallos.append(etiqueta)


def importar(nombre: str):
    """El módulo de la integración, o None si todavía no existe."""
    try:
        return importlib.import_module(f"custom_components.bomba_calor_predictor.{nombre}")
    except ImportError:
        return None


# ── Ficheros estáticos ───────────────────────────────────────────────


def test_manifest_y_traducciones() -> None:
    print("\nManifest y traducciones")

    manifest = json.loads((BASE / "manifest.json").read_text("utf-8"))
    comprobar(
        "github.com/MaxiBass/" in manifest.get("documentation", "")
        and "github.com/MaxiBass/" in manifest.get("issue_tracker", ""),
        f"manifest apunta al repo ({manifest.get('documentation')})",
    )
    comprobar(manifest.get("codeowners") == ["@MaxiBass"], f"codeowners {manifest.get('codeowners')}")
    comprobar(manifest.get("iot_class") == "calculated",
              f"iot_class «calculated»: calcula a partir de otras entidades ({manifest.get('iot_class')!r})")
    comprobar(manifest.get("single_config_entry") is True, "solo se puede dar de alta una vez (comparten el modelo guardado)")
    comprobar(manifest.get("integration_type") == "service", f"integration_type {manifest.get('integration_type')!r}")
    comprobar("description" not in manifest, "sin la clave «description», que el manifest no admite")
    comprobar(
        "updatetest" not in (BASE / "__init__.py").read_text("utf-8"),
        "sin la marca de prueba de HACS en __init__.py",
    )

    carpeta = BASE / "translations"
    if not carpeta.exists():
        comprobar(False, "existe translations/ (HA no lee strings.json en una integración custom)")
        return
    es = json.loads((carpeta / "es.json").read_text("utf-8"))
    en = json.loads((carpeta / "en.json").read_text("utf-8"))
    cadenas = json.loads((BASE / "strings.json").read_text("utf-8"))

    def claves(d: dict, prefijo: str = "") -> set[str]:
        salida = set()
        for k, v in d.items():
            salida |= claves(v, f"{prefijo}{k}.") if isinstance(v, dict) else {f"{prefijo}{k}"}
        return salida

    comprobar(claves(es) == claves(en), "es.json y en.json tienen las mismas claves")
    comprobar(es == cadenas, "strings.json coincide con es.json")
    comprobar(es != en, "en.json no es una copia de es.json")

    from PIL import Image

    tamanos = {}
    for nombre in ("icon.png", "icon@2x.png"):
        ruta = BASE / "brand" / nombre
        tamanos[nombre] = Image.open(ruta).size if ruta.exists() else None
    comprobar(tamanos == {"icon.png": (256, 256), "icon@2x.png": (512, 512)},
              f"icono propio en brand/ con los tamaños de HA {tamanos}")
    comprobar(not (RAIZ / "icon.png").exists() and not (RAIZ / "logo.png").exists(),
              "sin iconos sueltos en la raíz del repo (HACS no los lee)")


# ── Home Assistant real ──────────────────────────────────────────────


class Reloj:
    """Sustituye a dt_util en el coordinator: utcnow() devuelve `ahora`."""

    def __init__(self) -> None:
        from homeassistant.util import dt as dt_util

        self._real = dt_util
        self.ahora = dt_util.utcnow()

    def utcnow(self):
        return self.ahora

    def avanzar(self, minutos: float) -> None:
        self.ahora += timedelta(minutes=minutos)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


class _Registros(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.mensajes: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensajes.append(record.getMessage())


async def _arrancar_hass(directorio: Path):
    from homeassistant import bootstrap, config_entries, core, loader
    from homeassistant.core_config import async_process_ha_core_config
    from homeassistant.setup import async_setup_component

    hass = core.HomeAssistant(str(directorio))
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    for dominio in bootstrap.CORE_INTEGRATIONS:
        assert await async_setup_component(hass, dominio, {}), dominio
    await async_process_ha_core_config(hass, {"time_zone": "Europe/Madrid"})
    hass.set_state(core.CoreState.running)
    return hass


def _entidad(hass, unique_id: str) -> str:
    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMINIO, unique_id)
    assert entity_id, f"no existe sensor {unique_id}"
    return entity_id


async def _paso(nombre: str, coro) -> None:
    """Un fallo inesperado en un apartado no impide ver los demás."""
    try:
        await coro
    except Exception as err:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        comprobar(False, f"{nombre}: excepción inesperada {err!r}")


async def _rechaza(coro) -> bool:
    """True si el formulario no acepta los datos (error en el formulario o datos inválidos)."""
    from homeassistant.data_entry_flow import InvalidData

    try:
        resultado = await coro
    except InvalidData:
        return True
    return resultado.get("type") == "form" and bool(resultado.get("errors"))


class Contexto:
    def __init__(self, hass, reloj: Reloj, registros: _Registros, directorio: Path) -> None:
        self.hass = hass
        self.reloj = reloj
        self.registros = registros
        self.directorio = directorio
        self.entrada = None

    def poner(self, entity_id: str, valor, **atributos) -> None:
        """Cambia una entidad de origen con la hora del reloj falso."""
        self.hass.states.async_set(entity_id, str(valor), atributos, timestamp=self.reloj.ahora.timestamp())

    @property
    def coord(self):
        return self.hass.data[DOMINIO][self.entrada.entry_id]

    def estado(self, unique_suffix: str):
        return self.hass.states.get(_entidad(self.hass, f"{self.entrada.entry_id}_{unique_suffix}"))

    async def ciclo(self, minutos: float = 5) -> None:
        """Avanza el reloj y hace una actualización, como cada 5 minutos en casa."""
        self.reloj.avanzar(minutos)
        await self.coord.async_refresh()
        await self.hass.async_block_till_done()

    @property
    def muestras(self) -> dict:
        return dict(self.coord.samples)


async def _recorrido(directorio: Path) -> None:
    registros = _Registros()
    logging.getLogger().addHandler(registros)
    hass = await _arrancar_hass(directorio)
    reloj = Reloj()
    coordinador = importar("coordinator")
    coordinador.dt_util = reloj
    ctx = Contexto(hass, reloj, registros, directorio)

    # La bomba se acaba de encender en Silent, calentando.
    ctx.poner(T_AGUA, 26.0, unit_of_measurement="°C")
    ctx.poner(T_AGUA_2, 24.0, unit_of_measurement="°C")
    ctx.poner(CONSUMO, 1100, unit_of_measurement="W")
    ctx.poner(MODO, "Silent_heat", options=["Silent_heat", "Boost_heat", "Auto_heat"])
    ctx.poner(BOMBA, "on")
    ctx.poner(CONSIGNA, 30)

    try:
        await _paso("alta", _alta(ctx))
        if ctx.entrada is not None:
            for nombre, funcion in (
                ("entidades", _entidades),
                ("aprendizaje", _aprendizaje),
                ("arranque", _arranque),
                ("servicios", _servicios),
                ("opciones", _opciones),
                ("diagnóstico", _diagnostico),
                ("recarga", _recarga),
            ):
                await _paso(nombre, funcion(ctx))
    finally:
        await hass.async_stop(force=True)
        logging.getLogger().removeHandler(registros)


async def _formulario(hass, datos: dict) -> dict:
    """Un alta completa por el formulario. Si la integración la acepta por
    error, se borra la entrada creada para que no estorbe a lo siguiente."""
    from homeassistant.data_entry_flow import InvalidData

    flujo = await hass.config_entries.flow.async_init(DOMINIO, context={"source": "user"})
    if flujo.get("type") != "form":
        return flujo
    try:
        resultado = await hass.config_entries.flow.async_configure(flujo["flow_id"], datos)
    except InvalidData:
        hass.config_entries.flow.async_abort(flujo["flow_id"])
        return {"type": "form", "errors": {"base": "datos no válidos para el esquema"}}
    await hass.async_block_till_done()
    if resultado.get("type") == "form":
        hass.config_entries.flow.async_abort(flujo["flow_id"])
    return resultado


async def _alta(ctx: Contexto) -> None:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import translation

    hass = ctx.hass
    print("\n  · alta por el formulario")
    textos = await translation.async_get_translations(hass, "es", "config", {DOMINIO})
    etiqueta = textos.get(f"component.{DOMINIO}.config.step.user.data.sensor_t_agua")
    esperada = json.loads((BASE / "strings.json").read_text("utf-8"))["config"]["step"]["user"]["data"]["sensor_t_agua"]
    comprobar(etiqueta == esperada,
              f"HA carga las traducciones del formulario, no salen las claves en crudo ({etiqueta!r})")

    async def rechazada(datos: dict) -> tuple[bool, dict]:
        resultado = await _formulario(hass, datos)
        aceptada = resultado.get("type") == "create_entry"
        for entrada in hass.config_entries.async_entries(DOMINIO):
            await hass.config_entries.async_remove(entrada.entry_id)
        await hass.async_block_till_done()
        return not aceptada, resultado.get("errors") or {}

    ok, errores = await rechazada({**DATOS_ALTA, "sensor_t_agua": "sensor.no_existe"})
    comprobar(ok and errores == {"sensor_t_agua": "entity_not_found"},
              f"una entidad que no existe se rechaza ({errores})")
    ok, _ = await rechazada({**DATOS_ALTA, "learning_rate": 0})
    comprobar(ok, "una learning rate de 0 se rechaza (con 0 el modelo no aprende nunca)")
    ok, _ = await rechazada({**DATOS_ALTA, "learning_rate": 5})
    comprobar(ok, "y una de 5 también (el mismo rango que el servicio: 0,000001–0,1)")

    fin = await _formulario(hass, dict(DATOS_ALTA))
    await hass.async_block_till_done()
    entrada = fin.get("result")
    comprobar(fin.get("type") == "create_entry" and entrada is not None, f"crea la entrada ({fin.get('type')})")
    if entrada is None:
        return
    ctx.entrada = entrada
    comprobar(entrada.state is ConfigEntryState.LOADED, f"la entrada carga ({entrada.state})")
    comprobar(entrada.title == "Bomba Calor Predictor SC984" and entrada.version == 1
              and set(entrada.data) == set(DATOS_ALTA),
              "título, versión 1 y datos con la misma forma que la entrada de casa (no hace falta migrar)")

    otra = await _formulario(hass, dict(DATOS_ALTA))
    comprobar(otra.get("type") == "abort" and otra.get("reason") == "single_instance_allowed",
              f"una segunda entrada se rechaza: compartirían el modelo guardado ({otra.get('type')}, {otra.get('reason')})")
    for sobrante in hass.config_entries.async_entries(DOMINIO):
        if sobrante.entry_id != entrada.entry_id:
            await hass.config_entries.async_remove(sobrante.entry_id)
    await hass.async_block_till_done()


async def _entidades(ctx: Contexto) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · entidades y avisos de HA")
    registro_e = er.async_get(hass)
    entidades = er.async_entries_for_config_entry(registro_e, entrada.entry_id)
    eid = entrada.entry_id
    esperados = {f"{eid}_{tipo}_{modo}" for tipo in ("prediccion", "error", "muestras") for modo in ("silent", "boost")}
    comprobar({e.unique_id for e in entidades} == esperados, "los 6 sensores de siempre, con los mismos unique_id")
    comprobar({e.entity_id for e in entidades} == {
        "sensor.bdc_prediccion_consumo_silent", "sensor.bdc_prediccion_consumo_boost",
        "sensor.bdc_error_prediccion_silent", "sensor.bdc_error_prediccion_boost",
        "sensor.bdc_muestras_silent", "sensor.bdc_muestras_boost",
    }, f"y los mismos nombres y entity_id que en casa {sorted(e.entity_id for e in entidades)}")

    dispositivos = dr.async_entries_for_config_entry(dr.async_get(hass), eid)
    comprobar(not dispositivos and all(e.device_id is None for e in entidades),
              "sin dispositivo: con uno, HA 2026.9 pondría su nombre delante de los entity_id nuevos")
    nombre = hass.states.get("sensor.bdc_prediccion_consumo_silent").attributes.get("friendly_name")
    comprobar(nombre == "BDC Prediccion Consumo Silent", f"el nombre visible es el de siempre ({nombre!r})")

    propios = [m for m in ctx.registros.mensajes if DOMINIO in m and "has not been tested" not in m]
    comprobar(not propios, f"ningún aviso de HA sobre la integración ({propios})")

    silent = ctx.estado("prediccion_silent")
    boost = ctx.estado("prediccion_boost")
    # Priores: silent 950 − 3·26 = 872; boost 1900 + 15·26 = 2290.
    comprobar(silent.state == "872.0" and boost.state == "2290.0",
              f"predicciones con los priores y el agua a 26 °C ({silent.state}, {boost.state})")
    comprobar(silent.attributes.get("w0_base") == 950.0 and silent.attributes.get("muestras") == 0,
              "con los pesos y las muestras como atributos")


async def _aprendizaje(ctx: Contexto) -> None:
    hass = ctx.hass
    print("\n  · aprendizaje: solo con muestras estables")

    await ctx.ciclo(5)
    comprobar(ctx.muestras == {"silent": 0, "boost": 0},
              "a los 5 minutos de cargar aún no aprende (pide 10 de estabilidad)")
    await ctx.ciclo(6)
    comprobar(ctx.muestras["silent"] == 1, f"a los 11 minutos aprende una muestra Silent ({ctx.muestras})")
    error = ctx.estado("error_silent").state
    comprobar(error == str(round(1100 - 872.0, 1)), f"error = real − predicho ({error})")
    guardado = json.loads((ctx.directorio / ".storage" / "bomba_calor_predictor_model").read_text("utf-8"))
    comprobar(guardado["data"]["samples"]["silent"] == 1, "y guarda el modelo en disco")

    ctx.poner(CONSIGNA, "unavailable")
    await ctx.ciclo(5)
    comprobar(ctx.muestras["silent"] == 1,
              f"sin consigna conocida no aprende: no se sabe si la bomba está modulando ({ctx.muestras})")
    ctx.poner(CONSIGNA, 30)

    # Cambio a Boost y vuelta a Silent entre dos lecturas: el consumo aún
    # está en transición, aunque las dos lecturas vean Silent.
    ctx.reloj.avanzar(1)
    ctx.poner(MODO, "Boost_heat", options=["Silent_heat", "Boost_heat"])
    ctx.reloj.avanzar(2)
    ctx.poner(MODO, "Silent_heat", options=["Silent_heat", "Boost_heat"])
    await ctx.ciclo(2)
    comprobar(ctx.muestras["silent"] == 1,
              f"un cambio de modo entre dos lecturas también cuenta: no aprende ({ctx.muestras})")
    await ctx.ciclo(11)
    comprobar(ctx.muestras["silent"] == 2, "pasados 10 minutos desde ese cambio, vuelve a aprender")

    ctx.reloj.avanzar(1)
    ctx.poner(BOMBA, "off")
    ctx.reloj.avanzar(1)
    ctx.poner(BOMBA, "on")
    await ctx.ciclo(3)
    comprobar(ctx.muestras["silent"] == 2, f"la bomba apagada y encendida entre dos lecturas: no aprende ({ctx.muestras})")
    await ctx.ciclo(11)

    antes = dict(ctx.muestras)
    ctx.poner(CONSUMO, 150)
    await ctx.ciclo(5)
    ctx.poner(CONSUMO, 1100)
    ctx.poner(T_AGUA, 29.5)
    await ctx.ciclo(5)
    ctx.poner(T_AGUA, 26.0)
    comprobar(ctx.muestras == antes, "ni con consumo de standby ni con el agua casi en la consigna")

    ctx.poner(MODO, "Boost_heat", options=["Silent_heat", "Boost_heat"])
    ctx.poner(CONSUMO, 2400)
    await ctx.ciclo(5)
    comprobar(ctx.muestras["boost"] == 0, "recién pasada a Boost no aprende")
    await ctx.ciclo(6)
    comprobar(ctx.muestras["boost"] == 1,
              f"a los 11 minutos del cambio real a Boost aprende, aunque la integración lo viera 5 min tarde ({ctx.muestras})")
    ctx.poner(MODO, "Silent_heat", options=["Silent_heat", "Boost_heat"])
    ctx.poner(CONSUMO, 1100)
    await hass.async_block_till_done()


async def _arranque(ctx: Contexto) -> None:
    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · arranque con el sensor del agua aún sin valor")
    ctx.poner(T_AGUA, "unavailable")
    await hass.config_entries.async_reload(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(ctx.estado("prediccion_boost").state == "unknown", "sin temperatura del agua no hay predicción")
    ctx.poner(T_AGUA, 26.0)
    await hass.async_block_till_done()
    estado = ctx.estado("prediccion_boost").state
    comprobar(estado != "unknown",
              f"en cuanto llega la temperatura hay predicción, sin esperar 5 minutos ({estado})")
    comprobar(ctx.muestras["boost"] == 1, "y el modelo guardado se conserva tras recargar")


async def _servicios(ctx: Contexto) -> None:
    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · servicios")
    coord = ctx.coord
    await hass.services.async_call(DOMINIO, "set_learning_rate", {"learning_rate": 0.0003}, blocking=True)
    await hass.async_block_till_done()
    comprobar(entrada.options.get("learning_rate") == 0.0003, "set_learning_rate guarda la tasa en las opciones")
    comprobar(ctx.coord is coord and coord._config.get("learning_rate") == 0.0003,
              "y la aplica en caliente, sin recargar (recargar reinicia los 10 min de estabilidad)")

    await hass.services.async_call(DOMINIO, "reset_modelo", {}, blocking=True)
    await hass.async_block_till_done()
    pesos = ctx.coord.weights
    comprobar(ctx.muestras == {"silent": 0, "boost": 0} and pesos["silent"] == {"w0": 950.0, "w2": -3.0}
              and pesos["boost"] == {"w0": 1900.0, "w2": 15.0},
              "reset_modelo vuelve a los priores y pone las muestras a 0")


async def _opciones(ctx: Contexto) -> None:
    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · opciones")
    flujo = await hass.config_entries.options.async_init(entrada.entry_id)
    campos = {str(k) for k in flujo["data_schema"].schema}
    comprobar(campos == set(DATOS_ALTA),
              f"se pueden cambiar todas las entidades y los valores del modo, no solo tres {sorted(campos)}")
    actuales = {**entrada.data, **entrada.options}

    comprobar(await _rechaza(hass.config_entries.options.async_configure(
        flujo["flow_id"], {k: actuales[k] for k in campos} | {"sensor_t_agua": "sensor.no_existe"}
    )), "una entidad que no existe se rechaza")
    if not hass.config_entries.options.async_progress_by_handler(entrada.entry_id):
        flujo = await hass.config_entries.options.async_init(entrada.entry_id)
    comprobar(await _rechaza(hass.config_entries.options.async_configure(
        flujo["flow_id"], {k: actuales[k] for k in campos} | {"learning_rate": 0}
    )), "una learning rate de 0 se rechaza")
    if not hass.config_entries.options.async_progress_by_handler(entrada.entry_id):
        flujo = await hass.config_entries.options.async_init(entrada.entry_id)

    fin = await hass.config_entries.options.async_configure(
        flujo["flow_id"], {k: actuales[k] for k in campos} | {"sensor_t_agua": T_AGUA_2}
    )
    await hass.async_block_till_done()
    comprobar(fin.get("type") == "create_entry", f"un cambio válido se guarda ({fin.get('type')})")
    # Priores tras el reset: boost 1900 + 15·24 = 2260.
    comprobar(ctx.estado("prediccion_boost").state == "2260.0",
              f"y se usa el sensor nuevo ({ctx.estado('prediccion_boost').state})")


async def _diagnostico(ctx: Contexto) -> None:
    from homeassistant import loader

    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · icono y diagnóstico")
    integ = await loader.async_get_integration(hass, DOMINIO)
    comprobar(integ.has_branding, "HA ve el icono propio (carpeta brand/)")
    diagnostics = importar("diagnostics")
    comprobar(diagnostics is not None, "existe diagnostics.py")
    if diagnostics is None:
        return
    diag = await diagnostics.async_get_config_entry_diagnostics(hass, entrada)
    json.dumps(diag, default=str)
    comprobar(diag["config"]["sensor_t_agua"] == T_AGUA_2 and "silent" in diag["model"]["weights"]
              and "predictions" in diag["model"],
              "trae la configuración en uso y el modelo")
    fuentes = diag.get("sources", {})
    comprobar(fuentes.get(BOMBA, {}).get("state") == "on" and "last_changed" in fuentes.get(MODO, {}),
              "y el estado de cada entidad de origen, con cuándo cambió")
    comprobar("training" in diag and "can_train" in diag["training"],
              "y si ahora mismo aprendería y por qué no (lo que se buscaba en el incidente de Boost)")


async def _recarga(ctx: Contexto) -> None:
    from homeassistant.config_entries import ConfigEntryState

    hass, entrada = ctx.hass, ctx.entrada
    print("\n  · descarga y borrado")
    await hass.config_entries.async_unload(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.NOT_LOADED and not hass.services.has_service(DOMINIO, "reset_modelo"),
              "al descargar, quita sus servicios")
    await hass.config_entries.async_setup(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(entrada.state is ConfigEntryState.LOADED and hass.services.has_service(DOMINIO, "reset_modelo"),
              "y al volver a cargar, los registra")
    await hass.config_entries.async_remove(entrada.entry_id)
    await hass.async_block_till_done()
    comprobar(not hass.config_entries.async_entries(DOMINIO), "la entrada se borra sin errores")


def test_home_assistant() -> None:
    try:
        import homeassistant  # noqa: F401
    except ImportError:
        print("\nHome Assistant no instalado: se saltan las pruebas de integración")
        return

    print("\nIntegración en un Home Assistant real")
    directorio = Path(tempfile.mkdtemp(prefix="bomba_calor_"))
    try:
        (directorio / "custom_components").mkdir()
        (directorio / "custom_components" / DOMINIO).symlink_to(BASE)
        asyncio.run(_recorrido(directorio))
    finally:
        shutil.rmtree(directorio, ignore_errors=True)


if __name__ == "__main__":
    test_manifest_y_traducciones()
    test_home_assistant()
    print()
    if fallos:
        print(f"{len(fallos)} FALLOS:")
        for f in fallos:
            print(f"  - {f}")
    else:
        print("Todo OK")
    # os._exit y no sys.exit: con HA 2026.9 y el Python 3.14 del venv, el
    # intérprete da un segfault al cerrarse si hay cualquier entrada de
    # configuración cargada (lo mismo pasa en Integracion_Matriculas).
    sys.stdout.flush()
    sys.stderr.flush()
    import os

    os._exit(1 if fallos else 0)
