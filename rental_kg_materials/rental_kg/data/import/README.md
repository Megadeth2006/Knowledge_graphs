# Подготовленные файлы

Папка `<площадка>/` (yandex, avito, cian) с файлом `observations.csv` в общем формате и необязательным `manifest.json` вида `{"complete": false, "note": "..."}`. Поля формата — `src/sources.py`, правила — `docs/DATA.md`. Удобнее класть файл командой `src/import_prepared.py`. После успешного обновления проверенные записи попадают в `data/sources/`. Сырые файлы в Git не входят.
