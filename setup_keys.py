# -*- coding: utf-8 -*-
"""أضف مفتاح مزود محلياً — شغّله بنفسك، المفتاح ما بيظهرش في الشات."""
import getpass
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "keys.local.json")
EXAMPLE_FILE = os.path.join(HERE, "keys.example.json")


def load():
    path = KEYS_FILE if os.path.exists(KEYS_FILE) else EXAMPLE_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"providers": []}


def save(data):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except OSError:
        pass


def main():
    data = load()
    providers = data.setdefault("providers", [])
    print("أضف مزود ذكاء اصطناعي (Enter للإلغاء)\n")
    name = input("الاسم (مثال: groq): ").strip()
    if not name:
        return
    base_url = input("Base URL: ").strip()
    model = input("الموديل: ").strip()
    key = getpass.getpass("API key (مش هيتطبع): ").strip()
    if not base_url or not model or not key:
        print("تم الإلغاء: لازم Base URL + موديل + مفتاح.")
        return
    found = False
    for p in providers:
        if str(p.get("name", "")).lower() == name.lower():
            p.update({"base_url": base_url, "model": model,
                      "api_key": key, "enabled": True})
            found = True
            break
    if not found:
        providers.append({"name": name, "base_url": base_url, "model": model,
                          "api_key": key, "enabled": True})
    save(data)
    print(f"\nتم حفظ: {name} ({model}) في keys.local.json")
    print("المفتاح نفسه ما اطبعش. متشاركهوش في أي محادثة.")


if __name__ == "__main__":
    main()
