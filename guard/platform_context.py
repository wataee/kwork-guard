from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Optional

logger = logging.getLogger("kwork-guard.platform-context")


@dataclass
class BslMethodDoc:
    name: str
    signature: str
    description: str
    returns: str
    is_function: bool = True
    parameters: list[str] = field(default_factory=list)


@dataclass
class BslTypeDoc:
    name: str
    category: str
    description: str
    properties: list[str] = field(default_factory=list)
    methods: dict[str, BslMethodDoc] = field(default_factory=dict)


PLATFORM_KNOWLEDGE: dict[str, BslTypeDoc] = {
    "таблицазначений": BslTypeDoc(
        name="ТаблицаЗначений",
        category="Коллекции",
        description="Объект для хранения табличных данных в оперативной памяти на сервере.",
        properties=["Колонки", "Индексы"],
        methods={
            "добавить": BslMethodDoc(
                name="Добавить",
                signature="Добавить() -> СтрокаТаблицыЗначений",
                description="Добавляет новую строку в конец таблицы значений.",
                returns="СтрокаТаблицыЗначений",
                is_function=True,
            ),
            "вставить": BslMethodDoc(
                name="Вставить",
                signature="Вставить(Индекс) -> СтрокаТаблицыЗначений",
                description="Вставляет новую строку по указанному индексу.",
                returns="СтрокаТаблицыЗначений",
                is_function=True,
                parameters=["Индекс: Число - позиция вставки"],
            ),
            "удалить": BslMethodDoc(
                name="Удалить",
                signature="Удалить(СтрокаИлиИндекс)",
                description="Удаляет строку из таблицы по объекту строки или индексу.",
                returns="Неопределено",
                is_function=False,
                parameters=["СтрокаИлиИндекс: СтрокаТаблицыЗначений | Число"],
            ),
            "очистить": BslMethodDoc(
                name="Очистить",
                signature="Очистить()",
                description="Удаляет все строки из таблицы значений.",
                returns="Неопределено",
                is_function=False,
            ),
            "количество": BslMethodDoc(
                name="Количество",
                signature="Количество() -> Число",
                description="Возвращает количество строк в таблице значений.",
                returns="Число",
                is_function=True,
            ),
            "найти": BslMethodDoc(
                name="Найти",
                signature="Найти(Значение, Колонки) -> СтрокаТаблицыЗначений | Неопределено",
                description="Ищет первое вхождение значения в указанных колонках.",
                returns="СтрокаТаблицыЗначений | Неопределено",
                is_function=True,
                parameters=["Значение: Произвольный", "Колонки: Строка - имена колонок через запятую"],
            ),
            "найтистроки": BslMethodDoc(
                name="НайтиСтроки",
                signature="НайтиСтроки(ПараметрыОтбора) -> Массив",
                description="Возвращает массив строк, удовлетворяющих отбору (Структура).",
                returns="Массив строк таблицы значений",
                is_function=True,
                parameters=["ПараметрыОтбора: Структура - имя колонки и искомое значение"],
            ),
            "скопировать": BslMethodDoc(
                name="Скопировать",
                signature="Скопировать(Строки, Колонки) -> ТаблицаЗначений",
                description="Создает копию таблицы значений с возможностью отбора строк и колонок.",
                returns="ТаблицаЗначений",
                is_function=True,
                parameters=["Строки: Массив строк (опционально)", "Колонки: Строка (опционально)"],
            ),
            "скопироватьколонки": BslMethodDoc(
                name="СкопироватьКолонки",
                signature="СкопироватьКолонки(Колонки) -> ТаблицаЗначений",
                description="Создает пустую таблицу значений с заданной структурой колонок.",
                returns="ТаблицаЗначений",
                is_function=True,
                parameters=["Колонки: Строка - имена колонок"],
            ),
            "сортировать": BslMethodDoc(
                name="Сортировать",
                signature="Сортировать(Колонки, Направление)",
                description="Сортирует таблицу значений по указанным колонкам.",
                returns="Неопределено",
                is_function=False,
                parameters=["Колонки: Строка - 'Колонка1 Возр, Колонка2 Убыв'"],
            ),
            "заполнитьзначения": BslMethodDoc(
                name="ЗаполнитьЗначения",
                signature="ЗаполнитьЗначения(Значение, Колонки)",
                description="Заполняет указанную колонку или все колонки одинаковым значением.",
                returns="Неопределено",
                is_function=False,
                parameters=["Значение: Произвольный", "Колонки: Строка (опционально)"],
            ),
            "итог": BslMethodDoc(
                name="Итог",
                signature="Итог(Колонка) -> Число",
                description="Вычисляет сумму числовых значений по указанной колонке.",
                returns="Число",
                is_function=True,
                parameters=["Колонка: Строка - имя числовой колонки"],
            ),
        },
    ),
    "запрос": BslTypeDoc(
        name="Запрос",
        category="База данных",
        description="Объект для выполнения запросов к базе данных платформы 1С.",
        properties=["Текст", "МенеджерВременныхТаблиц"],
        methods={
            "выполнить": BslMethodDoc(
                name="Выполнить",
                signature="Выполнить() -> РезультатЗапроса",
                description="Выполняет запрос и возвращает объект РезультатЗапроса.",
                returns="РезультатЗапроса",
                is_function=True,
            ),
            "выполнитьпакет": BslMethodDoc(
                name="ВыполнитьПакет",
                signature="ВыполнитьПакет() -> Массив",
                description="Выполняет пакетный запрос и возвращает массив РезультатовЗапроса.",
                returns="Массив",
                is_function=True,
            ),
            "установитьпараметр": BslMethodDoc(
                name="УстановитьПараметр",
                signature="УстановитьПараметр(Имя, Значение)",
                description="Устанавливает значение параметра запроса (с префиксом & в тексте).",
                returns="Неопределено",
                is_function=False,
                parameters=["Имя: Строка", "Значение: Произвольный"],
            ),
        },
    ),
    "массив": BslTypeDoc(
        name="Массив",
        category="Коллекции",
        description="Индексированная упорядоченная коллекция значений произвольного типа.",
        properties=[],
        methods={
            "добавить": BslMethodDoc(
                name="Добавить",
                signature="Добавить(Значение)",
                description="Добавляет элемент в конец массива.",
                returns="Неопределено",
                is_function=False,
                parameters=["Значение: Произвольный"],
            ),
            "вставить": BslMethodDoc(
                name="Вставить",
                signature="Вставить(Индекс, Значение)",
                description="Вставляет элемент по указанному индексу.",
                returns="Неопределено",
                is_function=False,
                parameters=["Индекс: Число", "Значение: Произвольный"],
            ),
            "удалить": BslMethodDoc(
                name="Удалить",
                signature="Удалить(Индекс)",
                description="Удаляет элемент по индексу.",
                returns="Неопределено",
                is_function=False,
                parameters=["Индекс: Число"],
            ),
            "очистить": BslMethodDoc(
                name="Очистить",
                signature="Очистить()",
                description="Очищает массив.",
                returns="Неопределено",
                is_function=False,
            ),
            "количество": BslMethodDoc(
                name="Количество",
                signature="Количество() -> Число",
                description="Возвращает число элементов в массиве.",
                returns="Число",
                is_function=True,
            ),
            "вграница": BslMethodDoc(
                name="ВГраница",
                signature="ВГраница() -> Число",
                description="Возвращает наибольший индекс массива (Количество() - 1).",
                returns="Число",
                is_function=True,
            ),
            "найти": BslMethodDoc(
                name="Найти",
                signature="Найти(Значение) -> Число | Неопределено",
                description="Возвращает индекс первого вхождения значения или Неопределено.",
                returns="Число | Неопределено",
                is_function=True,
                parameters=["Значение: Произвольный"],
            ),
        },
    ),
    "структура": BslTypeDoc(
        name="Структура",
        category="Коллекции",
        description="Коллекция пар Ключ-Значение, где ключ является валидным идентификатором.",
        properties=[],
        methods={
            "вставить": BslMethodDoc(
                name="Вставить",
                signature="Вставить(Ключ, Значение)",
                description="Вставляет или перезаписывает значение по строковому ключу.",
                returns="Неопределено",
                is_function=False,
                parameters=["Ключ: Строка", "Значение: Произвольный (опционально)"],
            ),
            "удалить": BslMethodDoc(
                name="Удалить",
                signature="Удалить(Ключ)",
                description="Удаляет элемент по строковому ключу.",
                returns="Неопределено",
                is_function=False,
                parameters=["Ключ: Строка"],
            ),
            "очистить": BslMethodDoc(
                name="Очистить",
                signature="Очистить()",
                description="Очищает структуру.",
                returns="Неопределено",
                is_function=False,
            ),
            "количество": BslMethodDoc(
                name="Количество",
                signature="Количество() -> Число",
                description="Возвращает число элементов в структуре.",
                returns="Число",
                is_function=True,
            ),
            "свойство": BslMethodDoc(
                name="Свойство",
                signature="Свойство(Ключ, НайденноеЗначение) -> Булево",
                description="Проверяет наличие ключа и опционально записывает его значение в переменную.",
                returns="Булево",
                is_function=True,
                parameters=["Ключ: Строка", "НайденноеЗначение: Произвольный (выходной параметр)"],
            ),
        },
    ),
    "соответствие": BslTypeDoc(
        name="Соответствие",
        category="Коллекции",
        description="Коллекция пар Ключ-Значение, где ключ может быть произвольного типа.",
        properties=[],
        methods={
            "вставить": BslMethodDoc(
                name="Вставить",
                signature="Вставить(Ключ, Значение)",
                description="Вставляет пару ключ-значение в соответствие.",
                returns="Неопределено",
                is_function=False,
                parameters=["Ключ: Произвольный", "Значение: Произвольный"],
            ),
            "удалить": BslMethodDoc(
                name="Удалить",
                signature="Удалить(Ключ)",
                description="Удаляет пару по ключу.",
                returns="Неопределено",
                is_function=False,
                parameters=["Ключ: Произвольный"],
            ),
            "получить": BslMethodDoc(
                name="Получить",
                signature="Получить(Ключ) -> Произвольный | Неопределено",
                description="Возвращает значение по ключу или Неопределено.",
                returns="Произвольный | Неопределено",
                is_function=True,
                parameters=["Ключ: Произвольный"],
            ),
            "количество": BslMethodDoc(
                name="Количество",
                signature="Количество() -> Число",
                description="Возвращает количество элементов в соответствии.",
                returns="Число",
                is_function=True,
            ),
        },
    ),
    "httpсоединение": BslTypeDoc(
        name="HTTPСоединение",
        category="Интеграция",
        description="Клиент для выполнения синхронных HTTP/HTTPS запросов к внешним веб-сервисам.",
        properties=[],
        methods={
            "вызватьhttpметод": BslMethodDoc(
                name="ВызватьHTTPМетод",
                signature="ВызватьHTTPМетод(Метод, HTTPЗапрос) -> HTTPОтвет",
                description="Выполняет произвольный HTTP-запрос (GET, POST, PUT, DELETE, PATCH).",
                returns="HTTPОтвет",
                is_function=True,
                parameters=["Метод: Строка", "HTTPЗапрос: HTTPЗапрос"],
            ),
            "получить": BslMethodDoc(
                name="Получить",
                signature="Получить(HTTPЗапрос, ИмяВыходногоФайла) -> HTTPОтвет",
                description="Выполняет GET-запрос.",
                returns="HTTPОтвет",
                is_function=True,
                parameters=["HTTPЗапрос: HTTPЗапрос", "ИмяВыходногоФайла: Строка (опционально)"],
            ),
            "отправитьдляобработки": BslMethodDoc(
                name="ОтправитьДляОбработки",
                signature="ОтправитьДляОбработки(HTTPЗапрос, ИмяВыходногоФайла) -> HTTPОтвет",
                description="Выполняет POST-запрос.",
                returns="HTTPОтвет",
                is_function=True,
                parameters=["HTTPЗапрос: HTTPЗапрос", "ИмяВыходногоФайла: Строка (опционально)"],
            ),
        },
    ),
}


class PlatformContextService:
    def __init__(self, knowledge: Optional[dict[str, BslTypeDoc]] = None) -> None:
        self.knowledge = knowledge if knowledge is not None else PLATFORM_KNOWLEDGE

    def get_type_doc(self, type_name: str) -> Optional[BslTypeDoc]:
        normalized = type_name.strip().lower()
        return self.knowledge.get(normalized)

    def search_syntax(self, query: str) -> list[dict[str, str]]:
        q = query.strip().lower()
        if not q:
            return []

        results: list[dict[str, str]] = []
        for type_key, type_doc in self.knowledge.items():
            if q in type_key or q in type_doc.name.lower():
                results.append({
                    "kind": "type",
                    "name": type_doc.name,
                    "signature": f"Тип: {type_doc.name} ({type_doc.category})",
                    "description": type_doc.description,
                })

            for method_key, method_doc in type_doc.methods.items():
                if q in method_key or q in method_doc.name.lower() or q in method_doc.signature.lower():
                    results.append({
                        "kind": "method",
                        "name": f"{type_doc.name}.{method_doc.name}",
                        "signature": method_doc.signature,
                        "description": method_doc.description,
                    })

        return results

    def format_syntax_help(self, query: str) -> str:
        q = query.strip()
        if not q:
            return "Укажите имя типа или метода 1С (например, 'ТаблицаЗначений', 'Запрос.Выполнить', 'HTTPСоединение')."

        matched_type = self.get_type_doc(q)
        if matched_type:
            lines = [
                f"# Тип: {matched_type.name} ({matched_type.category})",
                f"**Описание:** {matched_type.description}",
                "",
                "## Свойства:",
                ", ".join(matched_type.properties) if matched_type.properties else "Нет общедоступных свойств",
                "",
                "## Основные методы:",
            ]
            for method in matched_type.methods.values():
                lines.append(f"- `{method.signature}`: {method.description}")
            return "\n".join(lines)

        if "." in q:
            parts = q.split(".", 1)
            parent_type = self.get_type_doc(parts[0])
            if parent_type:
                method_key = parts[1].strip().lower()
                method = parent_type.methods.get(method_key)
                if method:
                    lines = [
                        f"# Метод: {parent_type.name}.{method.name}",
                        f"**Сигнатура:** `{method.signature}`",
                        f"**Возвращает:** {method.returns}",
                        f"**Описание:** {method.description}",
                    ]
                    if method.parameters:
                        lines.append("\n**Параметры:**")
                        for p in method.parameters:
                            lines.append(f"- {p}")
                    return "\n".join(lines)

        search_res = self.search_syntax(q)
        if not search_res:
            return f"В базе синтакс-помощника ничего не найдено по запросу '{q}'."

        lines = [f"# Результаты поиска в синтакс-помощнике по запросу '{q}':", ""]
        for item in search_res[:10]:
            lines.append(f"- **{item['name']}** — `{item['signature']}`")
            lines.append(f"  {item['description']}")
        return "\n".join(lines)

    def build_tool_context_hint(self, text: str, max_items: int = 3) -> str:
        low = text.lower()
        hints: list[str] = []
        for type_key, type_doc in self.knowledge.items():
            if type_key in low or type_doc.name.lower() in low:
                sigs = [m.signature for m in list(type_doc.methods.values())[:4]]
                hints.append(f"{type_doc.name}: {', '.join(sigs)}")
                if len(hints) >= max_items:
                    break
        return "\n".join(hints)
