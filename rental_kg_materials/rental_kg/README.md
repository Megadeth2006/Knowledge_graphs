# Граф знаний для подбора съёмного жилья

Григорьев Даниил (465635), Михайлов Петр (466730). ИТМО, «Графы знаний», 2026.

Локальный поиск сопоставляет требования арендатора с объявлениями на Петроградском острове. Граф связывает цену и условия с датированной карточкой, квартиру — со зданием, а здание — с проверенной точкой OpenStreetMap.

## Каталоги

| Каталог | Содержимое |
|---|---|
| `src/` | Код: сборка графа, источники, дубли, обновление, сайт |
| `ontology/` | `shapes.ttl` (SHACL) и сгенерированная схема `ontology.ttl` |
| `queries/cq/`, `queries/app/` | Восемь запросов компетентностных вопросов и запросы сайта |
| `data/sources/`, `data/import/` | Проверенные срезы и история в Git, подготовленные файлы |
| `state/` | Версии, опубликованные кнопкой «Обновить», статус и блокировка; не в Git |
| `data/osm/`, `data/geo/` | Снимок OSM, граница острова, привязка домов |
| `data/fixtures/` | Учебные данные для вопросов 2, 3, 7 и отрицательных тестов |
| `graphs/` | OWL для Protégé и сайта |
| `results/` | Ответы запросов, сводка и отчёты SHACL |
| `web/` | Шаблоны и стили сайта |
| `tests/` | Модульные и интеграционные тесты |
| `notebooks/` | Ноутбук Embedding и его результаты |
| `docs/` | Данные, компетентностные вопросы, словарь, конспект |

## Запуск на Windows

Из каталога `rental_kg` в Git Bash. Первый раз:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

| Действие | Команда |
|---|---|
| Сайт | `.venv/Scripts/python.exe -X utf8 src/search_app.py`, затем http://127.0.0.1:8765/ |
| Сайт без сетевого запроса к «Экотону» | добавить `--offline` |
| Сборка RDF, OWL RL, SHACL, восемь запросов | `.venv/Scripts/python.exe -X utf8 src/build_graph.py` |
| Все тесты | `.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v` |
| Обновление без сайта | `.venv/Scripts/python.exe -X utf8 src/update_pipeline.py` (из сохранённой страницы: `--ecoton-html rental.html --date 2026-11-26`) |
| Перенести опубликованную версию в `data/` и `graphs/` для Git | `.venv/Scripts/python.exe -X utf8 src/releases.py --export` |
| Пример ошибки SHACL | `.venv/Scripts/python.exe -X utf8 src/shacl_example.py` |
| Ноутбук | `.venv/Scripts/python.exe -m pip install -r requirements-notebook.txt`, затем `.venv/Scripts/python.exe notebooks/run_notebook.py notebooks/embedding_kg.ipynb` или Run All в Jupyter |

**PyCharm.** Открыть каталог `rental_kg` как проект, выбрать интерпретатор `.venv`, отметить `src` как Sources Root. Готовые конфигурации лежат в `.run/`: сайт, сборка, тесты, обновление, ноутбук. Рабочий каталог у всех — корень проекта.

## Демонстрация

- **Protégé.** Открыть `graphs/observed_with_ontology.owl`. С HermiT на вкладке DL Query выбрать Instances. Выражение `'Квартира' and 'расположено в' value 'Санкт-Петербург'` возвращает 11 квартир. Учебный граф — `graphs/demo_with_ontology.owl`.
- **Поиск.** Срез «Экотона» от 26.11.2026 — пять предложений. При бюджете 220 000 ₽ и 8 минутах пешком подходят два, при 15 минутах — четыре. Фильтры площади, комнат, этажа, расстояния до супермаркета и парка. Неизвестное значение подписано явно. Время до метро считается по пешеходной сети OSM, расстояние до магазина и парка — по прямой.
- **Обновление.** Кнопка «Обновить» запускает получение, нормализацию, сопоставление дублей и проверку SHACL. Граф, срезы и история публикуются одной версией только после проверок.
- **SHACL.** Страница «Запросы и SHACL», пример «Запись отклонена до публикации».

[Данные, источники и обновление](docs/DATA.md) · [Компетентностные вопросы](docs/competency_questions.md) · [Словарь модели](docs/ontology_reference.md). Геоданные © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL.
