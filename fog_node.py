import time
import json
import random
import math
import requests
import boto3
from decimal import Decimal
from datetime import datetime, timezone
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════
ENDPOINT = "aq7xz4akb847r-ats.iot.us-east-1.amazonaws.com"
CLIENT_ID = "smartpark_fog_node_nyc"
TOPIC_RAW = f"sensors/{CLIENT_ID}/raw"
TOPIC_PROCESSED = f"sensors/{CLIENT_ID}/processed"

# AWS Certs
CERT_PATH = "certs/b5e9fa068f5cf7c0a1c2a983c50113812e2afde9f7a19c6ebecd3a637780ef9b-certificate.pem.crt"
KEY_PATH = "certs/b5e9fa068f5cf7c0a1c2a983c50113812e2afde9f7a19c6ebecd3a637780ef9b-private.pem.key"
CA_PATH = "certs/AmazonRootCA1.pem"

# OpenWeatherMap
OWM_API_KEY = "459aa1892fe226433d938ebeae1d2b2e"
LOCATION = "New York,US"
LAT, LON = 40.7128, -74.0060

RAW_INTERVAL = 10
FOG_INTERVAL = 30
USE_REAL_WEATHER = True

# ═══════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════
sensor_buffer = []
last_park_score = None
sequence = 0
soil_moisture = 40.0

# Initialize DynamoDB
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('SmartParkData')
print("✅ DynamoDB connected (LabRole)")

# ═══════════════════════════════════════════════════════════════════════
# WEATHER API
# ═══════════════════════════════════════════════════════════════════════

class WeatherAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.openweathermap.org/data/2.5"
        self.last_call = 0
        self.cache_duration = 600
        self.cached_data = None

    def get_current_weather(self):
        current_time = time.time()

        if self.cached_data and (current_time - self.last_call) < self.cache_duration:
            return self.cached_data

        if not USE_REAL_WEATHER or self.api_key == "YOUR_API_KEY_HERE":
            return None

        try:
            url = f"{self.base_url}/weather?q={LOCATION}&appid={self.api_key}&units=metric"
            response = requests.get(url, timeout=5)
            weather_data = response.json()

            uv_url = f"{self.base_url}/uvi?lat={LAT}&lon={LON}&appid={self.api_key}"
            uv_response = requests.get(uv_url, timeout=5)
            uv_data = uv_response.json()

            result = {
                "temperature": weather_data["main"]["temp"],
                "humidity": weather_data["main"]["humidity"],
                "wind_speed": weather_data["wind"]["speed"],
                "weather": weather_data["weather"][0]["main"],
                "uv_index": uv_data.get("value", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "openweathermap"
            }

            self.cached_data = result
            self.last_call = current_time
            print(f"🌦️  Real weather: {result['temperature']}°C, UV {result['uv_index']}")
            return result

        except Exception as e:
            print(f"⚠️  Weather API failed: {e}")
            return None

weather_api = WeatherAPI(OWM_API_KEY)

# ═══════════════════════════════════════════════════════════════════════
# SENSORS
# ═══════════════════════════════════════════════════════════════════════

def get_all_sensors():
    """Collect data from 5 sensors"""
    global soil_moisture, sequence
    sequence += 1

    real_weather = weather_api.get_current_weather()

    if real_weather:
        temp = real_weather["temperature"] + random.gauss(0, 0.5)
        humidity = real_weather["humidity"] + random.gauss(0, 1)
        uv = real_weather["uv_index"]
        wind = real_weather["wind_speed"]
        source = "real_api"
    else:
        hour = datetime.now(timezone.utc).hour
        temp = 22 + 5 * math.sin((hour - 6) * math.pi / 12) + random.gauss(0, 1)
        humidity = 70 - (temp - 22) * 2 + random.gauss(0, 3)
        humidity = max(30, min(90, humidity))
        uv = max(0, 8 * math.sin((hour - 6) * math.pi / 12)) if 6 <= hour <= 18 else 0
        uv = uv * random.uniform(0.7, 1.0)
        wind = random.uniform(0.5, 5.0)
        source = "simulated"

    # Simulated sensors
    soil_moisture += random.gauss(-0.3, 1.5)
    if random.random() < 0.05:
        soil_moisture += random.uniform(10, 20)
    soil_moisture = max(10, min(90, soil_moisture))

    base_pm25 = 30 + random.gauss(0, 8)
    dispersion = min(0.5, wind * 0.08)
    pm25 = base_pm25 * (1 - dispersion)
    pm25 = max(5, pm25)

    return {
        "deviceId": CLIENT_ID,
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": source,
        "location": LOCATION,
        "sensors": {
            "temperature_c": round(temp, 1),
            "humidity_percent": round(humidity, 1),
            "uv_index": round(uv, 1),
            "soil_moisture_percent": round(soil_moisture, 1),
            "pm25_ug_m3": round(pm25, 1),
            "wind_speed_ms": round(wind, 1)
        },
        "metadata": {
            "firmware": "SmartPark_v2.1",
            "battery": random.randint(70, 100),
            "signal_dbm": random.randint(-85, -40)
        }
    }

# Helper to convert floats to Decimals for DynamoDB
def float_to_decimal(obj):
    """Recursively convert floats to Decimals for DynamoDB"""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: float_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [float_to_decimal(i) for i in obj]
    return obj

# ═══════════════════════════════════════════════════════════════════════
# FOG PROCESSING
# ═══════════════════════════════════════════════════════════════════════

def calculate_park_score(sensors):
    scores = {
        'thermal': max(0, 100 - abs(sensors['temperature_c'] - 22) * 5),
        'comfort': max(0, 100 - abs(sensors['humidity_percent'] - 50) * 2),
        'uv_safety': 100 if sensors['uv_index'] <= 2 else max(0, 100 - (sensors['uv_index'] - 2) * 12),
        'soil': 100 if sensors['soil_moisture_percent'] < 40 else max(0, 100 - (sensors['soil_moisture_percent'] - 40)),
        'air': 100 if sensors['pm25_ug_m3'] < 35 else max(0, 100 - (sensors['pm25_ug_m3'] - 35) * 1.5)
    }

    weights = [0.25, 0.15, 0.25, 0.15, 0.20]
    final_score = sum(s * w for s, w in zip(scores.values(), weights))

    return round(final_score, 1), scores

def check_alerts(sensors, score):
    alerts = []

    if sensors['temperature_c'] > 35:
        alerts.append({"type": "HEAT_WARNING", "severity": "HIGH", "message": "Seek shade"})
    if sensors['uv_index'] >= 8:
        alerts.append({"type": "UV_WARNING", "severity": "HIGH", "message": "Use SPF 50+"})
    if sensors['pm25_ug_m3'] > 100:
        alerts.append({"type": "AIR_QUALITY", "severity": "CRITICAL", "message": "Avoid outdoor activity"})
    if sensors['soil_moisture_percent'] > 80:
        alerts.append({"type": "FLOOD_RISK", "severity": "MEDIUM", "message": "Check drainage"})

    return alerts

def get_recommendation(score):
    if score >= 80: return "Perfect picnic conditions!"
    if score >= 60: return "Good conditions, enjoy your outing."
    if score >= 40: return "Fair conditions, check specific alerts."
    if score >= 20: return "Poor conditions, consider indoor plans."
    return "Dangerous conditions, avoid outdoor exposure."

# ═══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════

def main():
    global last_park_score, sensor_buffer

    io.init_logging(getattr(io.LogLevel, 'Fatal'), 'stderr')

    print("╔════════════════════════════════════════════════════════════╗")
    print("║     🌳 SMARTPARK - FOG COMPUTING (NCI Project)            ║")
    print("║     Direct DynamoDB Write (AWS Academy Compatible)        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"Location: {LOCATION}")
    print(f"Real Weather API: {'Enabled' if USE_REAL_WEATHER else 'Disabled'}")

    print("🔌 Connecting to AWS IoT Core...")
    connection = mqtt_connection_builder.mtls_from_path(
        endpoint=ENDPOINT,
        cert_filepath=CERT_PATH,
        pri_key_filepath=KEY_PATH,
        ca_filepath=CA_PATH,
        client_id=CLIENT_ID,
        clean_session=True,
        keep_alive_secs=60
    )

    connect_future = connection.connect()
    connect_future.result()
    print("✅ Connected!")

    last_fog_process = time.time()

    print(f"⏱️  Config: Raw={RAW_INTERVAL}s | Fog={FOG_INTERVAL}s")
    print("🚀 Starting... (Press Ctrl+C to stop)")

    try:
        while True:
            current_time = time.time()

            # 1. READ SENSORS
            data = get_all_sensors()
            sensor_buffer.append(data['sensors'])

            if len(sensor_buffer) > 3:
                sensor_buffer.pop(0)

            # Send raw to IoT Core (unpack tuple correctly)
            raw_json = json.dumps(data)
            publish_future, packet_id = connection.publish(
                topic=TOPIC_RAW, 
                payload=raw_json, 
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result()

            source_icon = "🌦️" if data['data_source'] == 'real_api' else "🔧"
            print(f"{source_icon} [{data['sequence']}] IoT→AWS | "
                  f"T:{data['sensors']['temperature_c']}°C "
                  f"H:{data['sensors']['humidity_percent']}% "
                  f"UV:{data['sensors']['uv_index']} "
                  f"Soil:{data['sensors']['soil_moisture_percent']}%")

            # 2. FOG PROCESSING
            if current_time - last_fog_process >= FOG_INTERVAL and len(sensor_buffer) >= 3:

                avg_sensors = {
                    k: sum(s[k] for s in sensor_buffer) / len(sensor_buffer)
                    for k in sensor_buffer[0].keys()
                }

                score, components = calculate_park_score(avg_sensors)
                alerts = check_alerts(avg_sensors, score)
                recommendation = get_recommendation(score)

                score_change = abs(score - last_park_score) if last_park_score else 999
                should_send = score_change > 10 or len(alerts) > 0

                if should_send:
                    # Prepare processed data with DECIMALS for DynamoDB
                    processed = {
                        "deviceId": CLIENT_ID,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "layer": "fog_processed",
                        "parkScore": float_to_decimal(score),
                        "scoreChange": float_to_decimal(score_change),
                        "recommendation": recommendation,
                        "alerts": alerts,
                        "sensors": float_to_decimal({k: round(v, 1) for k, v in avg_sensors.items()}),
                        "metadata": data['metadata']
                    }

                    # Send to IoT Core (JSON can't have Decimal, so convert back)
                    proc_json = json.dumps(processed, default=lambda x: float(x) if isinstance(x, Decimal) else x)
                    proc_future, proc_packet_id = connection.publish(
                        topic=TOPIC_PROCESSED, 
                        payload=proc_json, 
                        qos=mqtt.QoS.AT_LEAST_ONCE
                    )
                    proc_future.result()

                    # Write directly to DynamoDB (now with proper Decimals)
                    try:
                        table.put_item(Item=processed)
                        print(f"   💾 DynamoDB saved: Score {score}/100")
                    except Exception as e:
                        print(f"   ⚠️  DynamoDB error: {e}")

                    alert_icon = "🔴" if alerts else "🟢"
                    print(f"   🧠 Fog→AWS: {recommendation[:50]}... {alert_icon}")
                    if alerts:
                        print(f"   🚨 Alert: {alerts[0]['message']}")

                    last_park_score = score
                else:
                    print(f"   💾 Buffered (no significant change)")

                last_fog_process = current_time

            time.sleep(RAW_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopping...")
    finally:
        print("Disconnecting from AWS IoT Core...")
        disconnect_future = connection.disconnect()
        disconnect_future.result()
        print("✅ Disconnected successfully.")

if __name__ == "__main__":
    main()