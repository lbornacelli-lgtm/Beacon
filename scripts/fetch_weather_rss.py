import requests
import os
from datetime import datetime

HEADERS = {"User-Agent": "BeaconWeatherStation/1.0 (ufuser@localhost)"}

SOURCES = {
    "fl_alerts": "https://api.weather.gov/alerts/active?area=FL",
    "gainesville_forecast": "https://api.weather.gov/gridpoints/JAX/48,30/forecast",
    "gainesville_hourly": "https://api.weather.gov/gridpoints/JAX/48,30/forecast/hourly",
}

output_folder = os.path.expanduser("~/weather_data")
os.makedirs(output_folder, exist_ok=True)
current_date = datetime.now().strftime("%Y-%m-%d")

def fetch_and_save(name, url):
    try:
        response = requests.get(url, timeout=15, headers=HEADERS)
        if response.status_code == 200:
            output_file = os.path.join(output_folder, f"{name}_{current_date}.json")
            with open(output_file, "wb") as f:
                f.write(response.content)
            print(f"[OK] {name} saved to {output_file}")
        else:
            print(f"[ERR] {name} failed — status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] {name} error: {e}")

if __name__ == "__main__":
    print(f"Fetching NWS data for {current_date}...")
    for name, url in SOURCES.items():
        fetch_and_save(name, url)
    print("Done.")
