"""Hebrew (default) and English. What you typed into systems.yaml (names, paths) is shown as is."""
from __future__ import annotations

import time
from typing import Optional

COOKIE = "kp_lang"
LANGS = ("he", "en")

T: dict[str, dict[str, str]] = {
    "he": {
        "home": "בית",
        "greet": "מה פותחים עכשיו, {name}?",
        "greet_anon": "מה פותחים עכשיו?",
        "search_label": "חיפוש בכל המערכות",
        "search_ph": "חיפוש חכם בכל המערכות — יגיע בשלב 3",
        "flow_title": "מסלול העבודה",
        "flow_hint": "לחיצה פותחת לשונית במערכת המתאימה",
        "not_connected": "המספרים יתחברו בשלב 2",
        "quick_title": "התחלה מהירה",
        "needs_title": "צריך אותך",
        "needs_wait": "יתחבר בשלב 2. עד אז, מה שמחכה לך נמצא ב-Poirot.",
        "open_poirot": "פתיחת Poirot",
        "recent_title": "פתחת לאחרונה",
        "recent_empty": "עוד לא פתחת כלום מהפורטל.",
        "status_title": "מצב המערכות",
        "start_all": "הפעלת הכל",
        "stop_all": "עצירת הכל",
        "start": "הפעלה",
        "stop": "עצירה",
        "restart": "הפעלה מחדש",
        "log": "לוג",
        "running": "פועל",
        "external": "פועל מחוץ לפורטל",
        "external_note": "הופעל מחוץ לפורטל, עוצרים אותו מהמקום שבו הופעל",
        "starting": "עולה…",
        "crashed": "נפל",
        "stopped": "כבוי",
        "conflict": "הפורט תפוס על ידי תוכנה אחרת",
        "conflict_title": "פורט {port} תפוס על ידי תוכנה אחרת",
        "conflict_text": "על הפורט של {name} עונה תוכנה אחרת, כנראה מערכת שהופעלה ידנית על הפורט הלא נכון. סגור אותה, ואז הפעל את {name} מכאן.",
        "running_count": "{n}/{m} פועלים",
        "now": "עכשיו",
        "minutes": "{m} דק׳",
        "hours": "{h}:{m:02d} שע׳",
        "off_title": "{name} כבוי",
        "off_text": "המערכת לא רצה כרגע. אפשר להפעיל אותה מכאן.",
        "starting_title": "מפעיל את {name}…",
        "starting_text": "הדף ייפתח כאן ברגע שהמערכת תענה.",
        "crashed_title": "{name} נפל",
        "crashed_text": "המערכת נעצרה מעצמה (קוד {code}). הסיבה בלוג.",
        "error_title": "לא הצלחתי להפעיל את {name}",
        "open_log": "פתיחת הלוג",
        "new_window": "פתיחה בחלון נפרד",
        "external_link": "נפתח בחלון נפרד",
        "calc_missing": "הכתובת של המחשבון עוד לא הוגדרה ב-systems.yaml (תחת views ← calculator ← url).",
        "close_tab": "סגירת לשונית",
        "tabs": "לשוניות פתוחות",
        "systems": "מערכות",
        "footer": "Kupfer · רץ על Poirot",
        "log_title": "לוג · {name}",
        "log_empty": "הלוג ריק. הוא נכתב רק כשהמערכת מופעלת מהפורטל.",
        "log_live": "מתעדכן כל 3 שניות",
        "lang_switch": "EN",
        "lang_switch_label": "Switch to English",
        "login_title": "כניסה",
        "login_text": "כניסה אחת לפורטל ולכל המערכות שמכבדות אותה.",
        "login_key": "מפתח הכניסה",
        "login_btn": "כניסה",
        "login_wrong": "המפתח לא נכון.",
        "logout": "יציאה",
        "logout_label": "יציאה מהפורטל ומהמערכות",
    },
    "en": {
        "home": "Home",
        "greet": "What are we opening, {name}?",
        "greet_anon": "What are we opening?",
        "search_label": "Search all systems",
        "search_ph": "Smart search across all systems — coming in phase 3",
        "flow_title": "The work",
        "flow_hint": "Opens a tab in the matching system",
        "not_connected": "Counts arrive in phase 2",
        "quick_title": "Quick start",
        "needs_title": "Needs you",
        "needs_wait": "Connects in phase 2. Until then, what waits for you is in Poirot.",
        "open_poirot": "Open Poirot",
        "recent_title": "Recently opened",
        "recent_empty": "Nothing opened from the portal yet.",
        "status_title": "Systems",
        "start_all": "Start all",
        "stop_all": "Stop all",
        "start": "Start",
        "stop": "Stop",
        "restart": "Restart",
        "log": "Log",
        "running": "Running",
        "external": "Running outside the portal",
        "external_note": "Started outside the portal; stop it where it was started",
        "starting": "Starting…",
        "crashed": "Crashed",
        "stopped": "Off",
        "conflict": "Port taken by another program",
        "conflict_title": "Port {port} is taken by another program",
        "conflict_text": "Another program answers on {name}'s port - probably a system started by hand on the wrong port. Close it, then start {name} from here.",
        "running_count": "{n}/{m} running",
        "now": "just now",
        "minutes": "{m} min",
        "hours": "{h}:{m:02d} h",
        "off_title": "{name} is off",
        "off_text": "The system isn't running. You can start it from here.",
        "starting_title": "Starting {name}…",
        "starting_text": "The page opens here as soon as the system answers.",
        "crashed_title": "{name} crashed",
        "crashed_text": "The system stopped by itself (code {code}). The reason is in the log.",
        "error_title": "Couldn't start {name}",
        "open_log": "Open the log",
        "new_window": "Open in a new window",
        "external_link": "Opens in a new window",
        "calc_missing": "The calculator's address isn't set in systems.yaml yet (views → calculator → url).",
        "close_tab": "Close tab",
        "tabs": "Open tabs",
        "systems": "Systems",
        "footer": "Kupfer · runs on Poirot",
        "log_title": "Log · {name}",
        "log_empty": "The log is empty. It is written only when the system is started from the portal.",
        "log_live": "Refreshes every 3 seconds",
        "lang_switch": "עב",
        "lang_switch_label": "עברית",
        "login_title": "Sign in",
        "login_text": "One sign-in for the portal and every system that honours it.",
        "login_key": "Key",
        "login_btn": "Sign in",
        "login_wrong": "That is not the key.",
        "logout": "Sign out",
        "logout_label": "Sign out of the portal and the systems",
    },
}


def pick(cookie: Optional[str]) -> str:
    return cookie if cookie in LANGS else "he"


def translator(lang: str):
    table = T[lang]

    def t(key: str, **kw) -> str:
        s = table.get(key) or T["he"].get(key) or key
        return s.format(**kw) if kw else s

    return t


def uptime(since: Optional[float], lang: str) -> str:
    if not since:
        return ""
    t = translator(lang)
    secs = int(time.time() - since)
    if secs < 60:
        return t("now")
    mins = secs // 60
    if mins < 60:
        return t("minutes", m=mins)
    return t("hours", h=mins // 60, m=mins % 60)
