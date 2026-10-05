"""Constantes del componente bomba_calor_predictor."""

DOMAIN = "bomba_calor_predictor"
STORAGE_KEY = "bomba_calor_predictor_model"
STORAGE_VERSION = 1
UPDATE_INTERVAL_MINUTES = 5

# Modelo de 2 parametros:  consumo = w0 + w2*T_agua
#
# Se elimina w1*T_ext porque la bomba opera exclusivamente en verano
# (25-35 C exterior), rango donde el COP es estable y T_ext no aporta
# poder predictivo significativo adicional sobre T_agua.
# El modelo reducido converge mas rapido y con menos ruido.
PRIORS = {
    "silent": {
        "w0": 950.0,
        "w2": -3.0,   # efecto T_agua: mas fria -> mas consumo
    },
    "boost": {
        "w0": 1900.0,
        "w2": 15.0,
    },
}

# Clamps para evitar divergencia. Solo recortan la prediccion publicada; el
# entrenamiento usa la prediccion sin recortar.
# Silent llegaba hasta 1200 W, pero en casa la bomba consume 1050-1330 W en
# Silent (sube con el agua) y el modelo daba 1303 W a 26 C: la prediccion se
# quedaba pegada al tope y el umbral de encendido de la piscina, ~130 W corto.
# 1500 W deja margen hasta el agua a 32 C (~1385 W) sin solaparse con Boost.
CLAMP = {
    "silent": {"min": 300.0,  "max": 1500.0},
    "boost":  {"min": 1500.0, "max": 3000.0},
}

# Limites de los pesos (regularizacion implicita)
WEIGHT_LIMITS = {
    "w0": (-500.0, 4000.0),
    "w2": (-100.0, 100.0),
}

DEFAULT_LEARNING_RATE = 0.0001
# Mismo rango en el alta, las opciones y el servicio set_learning_rate: con 0
# el modelo no aprende nunca, y por encima de 0.1 diverge en pocas muestras.
MIN_LEARNING_RATE = 0.000001
MAX_LEARNING_RATE = 0.1
MIN_CONSUMO_VALIDO = 200.0
MARGEN_MODULACION = 1.0
MIN_TIEMPO_ESTABLE_MINUTOS = 10

# Config Flow - claves
CONF_SENSOR_T_AGUA   = "sensor_t_agua"
CONF_SENSOR_CONSUMO  = "sensor_consumo"
CONF_SENSOR_MODO     = "sensor_modo"
CONF_SWITCH_BOMBA    = "switch_bomba"
CONF_NUMBER_SETPOINT = "number_setpoint"
CONF_LEARNING_RATE   = "learning_rate"
CONF_MODO_SILENT     = "modo_silent_value"
CONF_MODO_BOOST      = "modo_boost_value"

# Valores por defecto entity_ids
DEFAULT_T_AGUA   = "sensor.temperatura_del_agua_con_bomba_encendida"
DEFAULT_CONSUMO  = "sensor.consumo_bomba_piscina"
DEFAULT_MODO     = "select.modo"
DEFAULT_BOMBA    = "switch.bomba_de_calor"
DEFAULT_SETPOINT = "number.temperatura_deseada"
DEFAULT_SILENT_V = "Silent_heat"
DEFAULT_BOOST_V  = "Boost_heat"
