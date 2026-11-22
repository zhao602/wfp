import type { TriggerNodeConfiguration } from "../../../workflow-definition/definitionTypes";
import { isTriggerFieldVisible } from "../../nodeConfigFieldVisibility";
import { FrequencySelector } from "../../FrequencySelector";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import { Field } from "../shared/Field";

export function TriggerConfigForm({
  config,
  onChange,
}: {
  config: TriggerNodeConfiguration;
  onChange: (config: TriggerNodeConfiguration) => void;
}) {
  return (
    <div style={{ fontSize: "12px" }}>
      <Field nodeType="trigger" field="trigger_type" label="触发方式">
        <select
          className="wfp-ncp-select"
          value={config.trigger_type}
          onChange={(e) =>
            onChange({ ...config, trigger_type: e.target.value as TriggerNodeConfiguration["trigger_type"] })
          }
        >
          <option value="manual">手动</option>
          <option value="scheduled">定时</option>
          <option value="data_event">事件</option>
        </select>
      </Field>
      {/* 定时频率：仅定时触发可见（可见性规则单一来源见 nodeConfigFieldVisibility） */}
      {isTriggerFieldVisible("schedule", config) && (
        <Field nodeType="trigger" field="schedule" label="定时频率">
          <FrequencySelector
            presetConfig={config.preset_config}
            schedule={config.schedule}
            onChange={(update) =>
              onChange({
                ...config,
                preset_config: update.presetConfig,
                schedule: update.schedule ?? config.schedule,
              })
            }
          />
        </Field>
      )}
      {/* 输入模板：定时触发无运行时入口、模板不生效故隐藏；修改预期时只改谓词表一处 */}
      {isTriggerFieldVisible("input_template", config) && (
        <Field nodeType="trigger" field="input_template" label="输入模板">
          <KeyValueMapEditor
            value={config.input_template}
            onChange={(v) => onChange({ ...config, input_template: v })}
            keyLabel="参数名"
            valueLabel="触发上下文引用"
            valueColumnMode={{ kind: "freeText" }}
            sectionTitle="输入模板"
            emptyValueWarning="未绑定引用"
          />
        </Field>
      )}
    </div>
  );
}
