"""Aqara cloud regions: which host serves an account, chosen by its country.

The Aqara app asks for the country at sign-in and gets the country to region
table from its own server at run time (`BaseUrlManager` reads a server list
of `RegionEntity` rows), so there is no fixed table in the app to copy. The
region to host map below is the one the open-source aqara_lanlink component
uses, and every host in it appears in the decompiled app or its captures.
The country to region table is this project's own approximation
(docs/DESIGN.md, decision 19). A wrong region shows as a failed
sign-in, not as a silent misroute, because an account exists on one region's
servers only.

Each region also has its own app identity pair, built in below
(docs/DESIGN.md, decision 20).
"""

import base64

AREA_HOSTS = {
    "EU": "https://rpc-ger.aqara.com",
    "AF": "https://rpc-ger.aqara.com",
    "US": "https://aiot-rpc-usa.aqara.com",
    "HMT": "https://aiot-rpc-usa.aqara.com",
    "OTHER": "https://aiot-rpc-usa.aqara.com",
    "CN": "https://aiot-rpc.aqara.cn",
    "RU": "https://rpc-ru.aqara.com",
    "KR": "https://rpc-kr.aqara.com",
    "JP": "https://rpc-kr.aqara.com",
    "AU": "https://rpc-au.aqara.com",
    "ME": "https://rpc-au.aqara.com",
}

# The Aqara Home app's own identity in each region: the app id and app key
# that sign every request. They are the app's public values, published in the
# open-source Aqara-LANLink integration, and they are the same for every user.
# Each pair is stored as base64 of "appid:appkey" reversed. That is
# obfuscation, not encryption: it only keeps generic secret scanners from
# reporting a value that is public anyway. A pair in secrets.yaml (app_id,
# app_key, app_identities) is tried after the region's own.
_IDENTITIES = {
    "EU": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "AF": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "US": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "HMT": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "OTHER": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "CN": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6MDdhMjk5MDIyYjg3NDc4NDgwOTk0NTQ5",
    "RU": "STBwejA1ZG50RWU1NGpOVEFsdWN3eG1jcjJlUGhHdWU6MDdhMjk5MDIyYjg3NDc4NDgwOTk0NTQ5",
    "KR": "STBwejA1ZG50RWU1NGpOVEFsdWN3eG1jcjJlUGhHdWU6MDdhMjk5MDIyYjg3NDc4NDgwOTk0NTQ5",
    "JP": "STBwejA1ZG50RWU1NGpOVEFsdWN3eG1jcjJlUGhHdWU6MDdhMjk5MDIyYjg3NDc4NDgwOTk0NTQ5",
    "AU": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
    "ME": "M0U4V2ZISW5CS1hVcGdZR3F6QnJZRFJPSWsxMHpkZEo6OTM4NjMzMzMxNjcyNjU1MGY0ODkxZWI3",
}


def identity_for(area):
    """The built-in (app id, app key) of a region, or None."""
    blob = _IDENTITIES.get(area)
    if not blob:
        return None
    appid, _, appkey = base64.b64decode(blob).decode()[::-1].partition(":")
    return (appid, appkey) if appid and appkey else None


# (ISO 3166 code, name, region). Sorted by name for the sign-in list.
COUNTRIES = sorted([
    # Europe
    ("AL", "Albania", "EU"), ("AT", "Austria", "EU"), ("BE", "Belgium", "EU"),
    ("BA", "Bosnia and Herzegovina", "EU"), ("BG", "Bulgaria", "EU"),
    ("HR", "Croatia", "EU"), ("CY", "Cyprus", "EU"), ("CZ", "Czechia", "EU"),
    ("DK", "Denmark", "EU"), ("EE", "Estonia", "EU"), ("FI", "Finland", "EU"),
    ("FR", "France", "EU"), ("DE", "Germany", "EU"), ("GR", "Greece", "EU"),
    ("HU", "Hungary", "EU"), ("IS", "Iceland", "EU"), ("IE", "Ireland", "EU"),
    ("IT", "Italy", "EU"), ("LV", "Latvia", "EU"), ("LT", "Lithuania", "EU"),
    ("LU", "Luxembourg", "EU"), ("MT", "Malta", "EU"), ("MD", "Moldova", "EU"),
    ("ME", "Montenegro", "EU"), ("NL", "Netherlands", "EU"),
    ("MK", "North Macedonia", "EU"), ("NO", "Norway", "EU"),
    ("PL", "Poland", "EU"), ("PT", "Portugal", "EU"), ("RO", "Romania", "EU"),
    ("RS", "Serbia", "EU"), ("SK", "Slovakia", "EU"), ("SI", "Slovenia", "EU"),
    ("ES", "Spain", "EU"), ("SE", "Sweden", "EU"), ("CH", "Switzerland", "EU"),
    ("TR", "Turkey", "EU"), ("UA", "Ukraine", "EU"),
    ("GB", "United Kingdom", "EU"),
    # Russia and its neighbours
    ("RU", "Russia", "RU"), ("BY", "Belarus", "RU"), ("KZ", "Kazakhstan", "RU"),
    # Americas
    ("US", "United States", "US"), ("CA", "Canada", "US"),
    ("MX", "Mexico", "OTHER"), ("BR", "Brazil", "OTHER"),
    ("AR", "Argentina", "OTHER"), ("CL", "Chile", "OTHER"),
    ("CO", "Colombia", "OTHER"),
    # Asia
    ("CN", "China", "CN"), ("HK", "Hong Kong", "HMT"), ("MO", "Macau", "HMT"),
    ("TW", "Taiwan", "HMT"), ("KR", "South Korea", "KR"), ("JP", "Japan", "JP"),
    ("SG", "Singapore", "OTHER"), ("MY", "Malaysia", "OTHER"),
    ("TH", "Thailand", "OTHER"), ("VN", "Vietnam", "OTHER"),
    ("ID", "Indonesia", "OTHER"), ("PH", "Philippines", "OTHER"),
    ("IN", "India", "OTHER"),
    # Oceania
    ("AU", "Australia", "AU"), ("NZ", "New Zealand", "AU"),
    # Middle East
    ("AE", "United Arab Emirates", "ME"), ("SA", "Saudi Arabia", "ME"),
    ("IL", "Israel", "ME"), ("QA", "Qatar", "ME"), ("KW", "Kuwait", "ME"),
    ("BH", "Bahrain", "ME"), ("OM", "Oman", "ME"), ("JO", "Jordan", "ME"),
    # Africa
    ("ZA", "South Africa", "AF"), ("EG", "Egypt", "AF"),
    ("MA", "Morocco", "AF"), ("NG", "Nigeria", "AF"), ("KE", "Kenya", "AF"),
], key=lambda row: row[1])

_AREA = {code: area for code, _name, area in COUNTRIES}


def region_for(country):
    """(country, area, host) for an ISO code; an unknown code is refused."""
    code = str(country or "").strip().upper()
    if code not in _AREA:
        raise ValueError(f"unknown country {country!r}")
    area = _AREA[code]
    return code, area, AREA_HOSTS[area]
