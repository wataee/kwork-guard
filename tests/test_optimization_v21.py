from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest

from guard.bsl_linter import LintIssue, format_diagnostics_for_llm, lint_bsl_code
from guard.bsl_ls_runner import BslLanguageServerService, BslLsDiagnostic
from guard.compact_schema import (
    compact_1c_xml,
    compact_dcs_xml,
    compact_form_xml,
    compact_mdo_xml,
)
from guard.compactor import compact_mcp_result, prune_verified_bsl_history
from guard.fs_utils import atomic_write_json, atomic_write_text, write_bsl_with_bom


def test_aggressive_form_xml_compression() -> None:
    sample_form_xml = """<?xml version="1.0" encoding="UTF-8"?>
<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <items xsi:type="form:FormField">
    <name>Контрагент</name>
    <id>12</id>
    <font>Arial 10</font>
    <backColor>FFFFFF</backColor>
    <textColor>000000</textColor>
    <width>200</width>
    <height>24</height>
    <dataPath><segments>Объект.Контрагент</segments></dataPath>
    <events event="OnChange">КонтрагентПриИзменении</events>
  </items>
  <attributes>
    <name>Объект</name>
    <id>1</id>
    <valueType>ExternalDataProcessorObject.Печать</valueType>
  </attributes>
  <commands>
    <name>ВыполнитьПечать</name>
    <id>1</id>
    <action>ВыполнитьПечатьНаКлиенте</action>
  </commands>
</form:Form>
"""
    compacted = compact_form_xml(sample_form_xml)
    assert compacted["kind"] == "Form"
    assert len(compacted["items"]) == 1
    field = compacted["items"][0]
    assert field["name"] == "Контрагент"
    assert field["dataPath"] == "Объект.Контрагент"
    assert field["events"][0]["event"] == "OnChange"
    assert "font" not in field
    assert "backColor" not in field
    assert "width" not in field
    assert "id" not in field
    assert len(compacted["commands"]) == 1

    compact_json = compact_1c_xml(sample_form_xml, "Form.form")
    assert "backColor" not in compact_json
    assert "КонтрагентПриИзменении" in compact_json


def test_aggressive_dcs_and_mdo_xml_compression() -> None:
    sample_dcs = """<?xml version="1.0" encoding="UTF-8"?>
<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">
  <dataSet>
    <name>НаборДанных1</name>
    <query>ВЫБРАТЬ Номенклатура, Сумма ИЗ РегистрНакопления.Продажи</query>
  </dataSet>
  <dataSetLink>
    <sourceDataSet>НаборДанных1</sourceDataSet>
    <destinationDataSet>НаборДанных2</destinationDataSet>
  </dataSetLink>
  <calculatedField>
    <dataPath>Наценка</dataPath>
    <expression>Сумма - Себестоимость</expression>
  </calculatedField>
  <totalField>
    <dataPath>Сумма</dataPath>
    <expression>Сумма(Сумма)</expression>
  </totalField>
  <parameter>
    <name>Период</name>
  </parameter>
  <conditionalAppearance>
    <item>
      <font>Bold</font>
      <backColor>FF0000</backColor>
    </item>
  </conditionalAppearance>
  <settingsVariant>
    <name>Основной</name>
  </settingsVariant>
</DataCompositionSchema>
"""
    dcs = compact_dcs_xml(sample_dcs)
    assert "dataSets" in dcs
    assert "dataSetLinks" in dcs
    assert "calculatedFields" in dcs
    assert "totalFields" in dcs
    assert "parameters" in dcs
    assert "settings" in dcs
    assert "conditionalAppearance" not in json.dumps(dcs)

    mdo_xml = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:ExternalDataProcessor xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="12345678-1234-1234-1234-123456789abc">
  <name>ТестОбработка</name>
  <synonym><key>ru</key><value>Тест обработка</value></synonym>
  <font>Tahoma</font>
</mdclass:ExternalDataProcessor>
"""
    mdo = compact_mdo_xml(mdo_xml)
    assert mdo["name"] == "ТестОбработка"
    assert mdo["synonym"] == "Тест обработка"
    assert "font" not in mdo

    res = compact_mcp_result("dcs", sample_dcs, 5000)
    assert "conditionalAppearance" not in res
    assert "НаборДанных1" in res


def test_history_pruning_verified_modules() -> None:
    messages = [
        {"role": "system", "content": "Статические правила"},
        {"role": "user", "content": "Прочитай модуль"},
        {"role": "tool", "tool_call_id": "t1", "content": "ExternalDataProcessors/Test/ObjectModule.bsl -> old v1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call1",
                    "type": "function",
                    "function": {
                        "name": "write_module_source",
                        "arguments": json.dumps({
                            "modulePath": "ExternalDataProcessors/Test/ObjectModule.bsl",
                            "source": "Процедура Черновик()\nКонецПроцедуры",
                        }),
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call2",
                    "type": "function",
                    "function": {
                        "name": "write_module_source",
                        "arguments": json.dumps({
                            "modulePath": "ExternalDataProcessors/Test/ObjectModule.bsl",
                            "source": "Процедура Финал()\nКонецПроцедуры",
                        }),
                    },
                }
            ],
        },
    ]

    pruned = prune_verified_bsl_history(messages, {"ExternalDataProcessors/Test/ObjectModule.bsl"})
    assert pruned[0]["content"] == "Статические правила"
    assert pruned[2]["content"] == "[Collapsed: module updated & verified]"
    old_args = json.loads(pruned[3]["tool_calls"][0]["function"]["arguments"])
    assert old_args["source"] == "[Collapsed: module updated & verified]"
    final_args = json.loads(pruned[4]["tool_calls"][0]["function"]["arguments"])
    assert "Финал" in final_args["source"]


def test_linter_filtering_and_fast_hybrid_diagnostics(tmp_path: Path) -> None:
    issues = [
        LintIssue(code="HINT1", line=1, message="Мелкий хинт", severity="HINT", col=1),
        LintIssue(code="INFO1", line=2, message="Инфо", severity="INFORMATION", col=3),
        LintIssue(code="BSL002_QUERY_IN_LOOP", line=5, message="Запрос в цикле", severity="ERROR", col=2),
        LintIssue(code="BSL009_HARDCODED_GUID", line=8, message="GUID", severity="WARNING", col=4),
    ]
    formatted = format_diagnostics_for_llm(issues, include_warnings=True)
    assert "HINT1" not in formatted
    assert "INFO1" not in formatted
    assert "5:2 [BSL002_QUERY_IN_LOOP] Запрос в цикле" in formatted
    assert "8:4 [BSL009_HARDCODED_GUID] GUID" in formatted

    bsl_file = tmp_path / "Test.bsl"
    write_bsl_with_bom(bsl_file, "Функция БезВозврата()\n\tА = 1;\nКонецФункции")
    assert bsl_file.read_bytes().startswith(b"\xef\xbb\xbf")

    svc = BslLanguageServerService(tmp_path)
    with patch.object(svc, "run_diagnostics_on_file") as mock_jvm:
        fast_diags = svc.run_hybrid_diagnostics(bsl_file, final_validation=False)
        assert not mock_jvm.called
        assert any(d.code == "BSL007_MISSING_RETURN" for d in fast_diags)
        compact_str = svc.format_for_llm([
            BslLsDiagnostic(code="HINT_IGNORE", line=1, severity="HINT", message="skip"),
            *fast_diags,
        ])
        assert "HINT_IGNORE" not in compact_str
        assert "1:1 [BSL007_MISSING_RETURN]" in compact_str


def test_atomic_write_permission_error_retry_and_bom(tmp_path: Path) -> None:
    target = tmp_path / "state_atomic.json"
    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(src, dst):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("Locked by antivirus")
        return real_replace(src, dst)

    with patch("guard.fs_utils.os.replace", side_effect=flaky_replace):
        atomic_write_json(target, {"ok": True, "calls": 5})

    assert attempts["count"] == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "calls": 5}

    bsl_path = tmp_path / "Mod.bsl"
    write_bsl_with_bom(bsl_path, "Процедура Кириллица()\n\tСообщить(\"Привет\");\nКонецПроцедуры")
    raw = bsl_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Кириллица" in raw.decode("utf-8-sig")
    assert len(lint_bsl_code(raw.decode("utf-8"))) == 0


def test_xml_edge_cases_and_fs_exhaustion(tmp_path: Path) -> None:
    nested_form = """<?xml version="1.0" encoding="UTF-8"?>
<Form>
  <items>
    <name>ГруппаШапка</name>
    <childItems>
      <name>ПолеВложенное</name>
      <dataPath>Объект.Дата</dataPath>
      <events><name>ПриИзменении</name><handler>ДатаПриИзменении</handler></events>
    </childItems>
  </items>
  <synonym><item><content>Вложенный текст</content></item></synonym>
</Form>
"""
    comp = compact_form_xml(nested_form)
    assert len(comp["items"]) == 1
    assert comp["items"][0]["items"][0]["events"][0]["handler"] == "ДатаПриИзменении"

    assert compact_1c_xml("not xml text") == "not xml text"
    assert compact_1c_xml("<broken><unclosed", "Form.form") == "<broken><unclosed"

    target = tmp_path / "fail_atomic.txt"
    with patch("guard.fs_utils.os.replace", side_effect=PermissionError("Always locked")):
        with pytest.raises(PermissionError):
            atomic_write_text(target, "data", max_retries=2, base_delay=0.001)

    with patch("guard.fs_utils.os.replace", side_effect=ValueError("Unexpected")):
        with pytest.raises(ValueError):
            atomic_write_text(target, "data", max_retries=2, base_delay=0.001)

