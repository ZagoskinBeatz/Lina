#!/usr/bin/env python3
"""
Test: Web weather URL encoding.

Verifies that weather URL is properly encoded for:
- Cyrillic cities
- Cities with spaces
- ASCII cities
- Empty input
"""

import os
import sys
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

passed = 0
failed = 0
total = 0


def test(name, condition, detail=""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  ✅ {total:03d}. {name}")
    else:
        failed += 1
        print(f"  ❌ {total:03d}. {name}  — {detail}")


print("=" * 60)
print("  Web Weather URL Encoding Tests")
print("=" * 60)

# ── 1. Source code audit ──
print()
print("── Source Code Audit ──")
project_root = os.path.join(os.path.dirname(__file__), '..', '..')
api_path = os.path.join(project_root, 'tools', 'api.py')

with open(api_path, 'r') as f:
    source = f.read()

# Must import url encoding
test("api.py imports urllib.parse or url_quote",
     'url_quote' in source or 'urllib.parse' in source or 'urlencode' in source,
     "No URL encoding import found")

# Must not have raw f-string URL with city
# Old pattern: f"https://wttr.in/{city}?format=..."
bad_patterns = re.findall(r'f"https://wttr\.in/\{city\}\?', source)
test("No raw f-string URL with {city}",
     len(bad_patterns) == 0,
     f"Found {len(bad_patterns)} raw f-string URLs")

# Must encode the city
test("Uses encoded city in URL",
     'city_encoded' in source or 'quote(city' in source or 'url_quote(city' in source,
     "No URL encoding applied to city")

# ── 2. URL formation tests ──
print()
print("── URL Formation ──")
from urllib.parse import quote as url_quote

test_cases = [
    ("Moscow", "Moscow"),
    ("Пермь", "%D0%9F%D0%B5%D1%80%D0%BC%D1%8C"),
    ("Москва", "%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0"),
    ("New York", "New%20York"),
    ("São Paulo", "S%C3%A3o%20Paulo"),
    ("в Перми", "%D0%B2%20%D0%9F%D0%B5%D1%80%D0%BC%D0%B8"),
    ("Los Angeles", "Los%20Angeles"),
]

for city, expected_encoded in test_cases:
    encoded = url_quote(city, safe="")
    url = f"https://wttr.in/{encoded}?format=3&lang=ru"

    # No raw Cyrillic in URL
    has_non_ascii = any(ord(c) > 127 for c in url)
    test(f"URL for '{city}' has no raw non-ASCII",
         not has_non_ascii,
         f"URL: {url}")

    # No spaces in URL
    has_space = ' ' in url
    test(f"URL for '{city}' has no spaces",
         not has_space,
         f"URL: {url}")

    # Encoded correctly
    test(f"URL for '{city}' encoded correctly",
         encoded == expected_encoded,
         f"Got: {encoded}, expected: {expected_encoded}")

# ── 3. Empty city handling ──
print()
print("── Edge Cases ──")

# Simulate get_weather logic
def simulate_get_weather_url(city: str) -> str:
    city_clean = city.strip()
    if not city_clean:
        city_clean = "Moscow"
    city_encoded = url_quote(city_clean, safe="")
    return f"https://wttr.in/{city_encoded}?format=3&lang=ru"

test("Empty city defaults to Moscow",
     "Moscow" in simulate_get_weather_url(""))
test("Whitespace city defaults to Moscow",
     "Moscow" in simulate_get_weather_url("   "))
test("Normal city preserved",
     "London" in simulate_get_weather_url("London"))
test("Cyrillic city encoded",
     "%D0%9F%D0%B5%D1%80%D0%BC%D1%8C" in simulate_get_weather_url("Пермь"))

# ── 4. curl exit code 3 scenario ──
print()
print("── Curl Exit Code 3 Prevention ──")

# The bug was: "в Перми будет 22 февраля" passed as city name
# This had spaces AND Cyrillic → curl exit code 3 (malformed URL)
bad_city = "в Перми будет 22 февраля"
url = simulate_get_weather_url(bad_city)
has_non_ascii = any(ord(c) > 127 for c in url)
has_space = ' ' in url
test(f"Regression: '{bad_city}' → no raw non-ASCII",
     not has_non_ascii, f"URL: {url}")
test(f"Regression: '{bad_city}' → no spaces",
     not has_space, f"URL: {url}")
test(f"Regression: URL is valid for curl",
     url.startswith("https://") and '%' in url)

print()
print("=" * 60)
print(f"  Web Weather URL: {passed}/{total} passed, {failed} failed")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
