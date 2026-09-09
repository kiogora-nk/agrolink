"""Climate checker for BioFarm Fruits.

Fetches real forecast data from Open-Meteo (free, no API key required)
and turns it into practical farming recommendations. Results are cached
briefly so repeated checks don't hammer the API.
"""

import time
import requests

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
GEOCODING_URL = 'https://geocoding-api.open-meteo.com/v1/search'
REQUEST_TIMEOUT = 8  # seconds
CACHE_TTL = 600      # 10 minutes

_cache = {}


class ClimateError(Exception):
    """Raised with a user-friendly message when weather data can't be fetched."""


def _cached(key, fetch):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def geocode(name):
    """Resolve a place name to {'name', 'latitude', 'longitude', 'country'}.

    Raises ClimateError for unknown places or network problems."""
    name = (name or '').strip()
    if not name:
        raise ClimateError('Please enter a town or city name.')

    def fetch():
        try:
            resp = requests.get(
                GEOCODING_URL,
                params={'name': name, 'count': 1, 'language': 'en', 'format': 'json'},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get('results') or []
        except requests.RequestException:
            raise ClimateError('Could not reach the location service. '
                               'Please check your internet connection and try again.')
        if not results:
            raise ClimateError(f'We could not find "{name}". Try a nearby larger town.')
        r = results[0]
        return {
            'name': r.get('name', name),
            'latitude': r['latitude'],
            'longitude': r['longitude'],
            'country': r.get('country', ''),
        }

    return _cached(('geo', name.lower()), fetch)


def get_forecast(lat, lon):
    """Current conditions + 7-day outlook + farming advice for a point.

    Returns a plain dict ready to be served as JSON. Raises ClimateError
    on network/API problems."""
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ClimateError('Invalid coordinates.')

    def fetch():
        try:
            resp = requests.get(
                FORECAST_URL,
                params={
                    'latitude': lat_f,
                    'longitude': lon_f,
                    'current_weather': True,
                    'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum',
                    'timezone': 'auto',
                    'forecast_days': 7,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            raise ClimateError('Could not reach the weather service. '
                               'Please check your internet connection and try again.')
        if 'error' in data:
            raise ClimateError(data.get('reason') or 'The weather service rejected the request.')

        current = data.get('current_weather') or {}
        daily = data.get('daily') or {}

        temps_max = daily.get('temperature_2m_max') or []
        temps_min = daily.get('temperature_2m_min') or []
        rain = daily.get('precipitation_sum') or []
        dates = daily.get('time') or []

        forecast = [
            {
                'date': d,
                'temp_max': round(mx, 1) if mx is not None else None,
                'temp_min': round(mn, 1) if mn is not None else None,
                'precipitation': round(p, 1) if p is not None else None,
            }
            for d, mx, mn, p in zip(dates, temps_max, temps_min, rain)
        ]

        week_rain = sum(p for p in rain if p is not None)
        week_hot = sum(1 for t in temps_max if t is not None and t >= 30)
        week_cold = sum(1 for t in temps_min if t is not None and t <= 10)

        return {
            'latitude': lat_f,
            'longitude': lon_f,
            'current': {
                'temperature': round(current.get('temperature'), 1)
                if current.get('temperature') is not None else None,
                'windspeed': round(current.get('windspeed'), 1)
                if current.get('windspeed') is not None else None,
            },
            'forecast': forecast,
            'week_rain_mm': round(week_rain, 1),
            'week_hot_days': week_hot,
            'week_cold_nights': week_cold,
            'recommendations': recommendations(week_rain, week_hot, week_cold,
                                               current.get('temperature')),
        }

    return _cached(('wx', round(lat_f, 2), round(lon_f, 2)), fetch)


def recommendations(week_rain, hot_days, cold_nights, current_temp):
    """Rule-based farming advice from the weekly outlook."""
    tips = []

    if week_rain < 5:
        tips.append('Dry week ahead (under 5 mm of rain expected). Increase irrigation '
                    'and mulch around plants to hold soil moisture.')
    elif week_rain > 50:
        tips.append('Heavy rain expected this week. Check drainage channels, stake '
                    'young seedlings, and delay fertilizer application to avoid runoff.')
    else:
        tips.append('Moderate rainfall expected. A good week for transplanting '
                    'seedlings and applying fertilizer.')

    if hot_days >= 3:
        tips.append('Several hot days above 30°C. Water early morning or late '
                    'evening, and shade young dragon fruit plants to prevent sunburn.')
    if cold_nights >= 3:
        tips.append('Cold nights below 10°C expected. Protect sensitive crops '
                    'with covers and delay planting warm-season crops.')
    if current_temp is not None and current_temp >= 35:
        tips.append('It is very hot right now. Avoid field work in the midday sun '
                    'and keep both workers and livestock hydrated.')

    if not any(t.startswith('Heavy') for t in tips):
        tips.append('Inspect leaves and fruit regularly this week — changing weather '
                    'is when pests and disease appear. Use our Crop AI tool if you '
                    'spot anything unusual.')
    return tips
