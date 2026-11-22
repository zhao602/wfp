// =============================================================================
// 频率选择器组件（FrequencySelector.tsx）
//
// 职责：在触发器定时模式下提供预设频率选择 + 条件性参数输入。
// 设计意图：零新依赖、零 any、复用 wfp-ncp-* 类族、深色主题覆盖。
// =============================================================================

import { useState, useEffect, useMemo } from 'react';
import type {
  FrequencyPreset,
  FixedFrequencyPreset,
  IntervalFrequencyPreset,
  PresetConfiguration,
  SpecificTimeParameter,
  CronParseResult,
  ValidationResult,
} from '../workflow-definition/frequencyPreset';
import {
  FREQUENCY_PRESET_OPTIONS,
  presetToCron,
  cronToPreset,
  validateIntervalValue,
  validateSpecificTime,
  getIntervalLimit,
} from '../workflow-definition/frequencyPreset';
import { useThemeMode, darkTokens } from '../theme/themeMode';

interface FrequencySelectorProps {
  presetConfig?: PresetConfiguration;
  schedule?: string;
  onChange: (update: { presetConfig?: PresetConfiguration; schedule?: string }) => void;
}

/** 为指定预设创建默认参数配置 */
function createDefaultConfig(preset: FrequencyPreset): PresetConfiguration {
  switch (preset) {
    case "hourly":
      return { preset: "hourly", minute: 0 };
    case "daily":
      return { preset: "daily", hour: 0, minute: 0 };
    case "weekly":
      return { preset: "weekly", day_of_week: 1, hour: 0, minute: 0 };
    case "monthly":
      return { preset: "monthly", day_of_month: 1, hour: 0, minute: 0 };
    case "yearly":
      return { preset: "yearly", month: 1, day_of_month: 1, hour: 0, minute: 0 };
    default:
      return { preset, value: 1 };
  }
}

/** 从 PresetConfiguration 提取扁平参数 */
function extractParams(config: PresetConfiguration): {
  minute: number; hour: number; day_of_week: number; day_of_month: number; month: number; value: number;
} {
  switch (config.preset) {
    case "hourly":
      return { minute: config.minute, hour: 0, day_of_week: 1, day_of_month: 1, month: 1, value: 1 };
    case "daily":
      return { minute: config.minute, hour: config.hour, day_of_week: 1, day_of_month: 1, month: 1, value: 1 };
    case "weekly":
      return { minute: config.minute, hour: config.hour, day_of_week: config.day_of_week, day_of_month: 1, month: 1, value: 1 };
    case "monthly":
      return { minute: config.minute, hour: config.hour, day_of_week: 1, day_of_month: config.day_of_month, month: 1, value: 1 };
    case "yearly":
      return { minute: config.minute, hour: config.hour, day_of_week: 1, day_of_month: config.day_of_month, month: config.month, value: 1 };
    default:
      return { minute: 0, hour: 0, day_of_week: 1, day_of_month: 1, month: 1, value: config.value };
  }
}

/** 从预设标识 + 扁平参数构建 PresetConfiguration */
function buildConfig(preset: FrequencyPreset, p: {
  minute: number; hour: number; day_of_week: number; day_of_month: number; month: number; value: number;
}): PresetConfiguration {
  switch (preset) {
    case "hourly":
      return { preset: "hourly", minute: p.minute };
    case "daily":
      return { preset: "daily", hour: p.hour, minute: p.minute };
    case "weekly":
      return { preset: "weekly", day_of_week: p.day_of_week, hour: p.hour, minute: p.minute };
    case "monthly":
      return { preset: "monthly", day_of_month: p.day_of_month, hour: p.hour, minute: p.minute };
    case "yearly":
      return { preset: "yearly", month: p.month, day_of_month: p.day_of_month, hour: p.hour, minute: p.minute };
    default:
      return { preset, value: p.value };
  }
}

/** 校验指定预设的参数 */
function validateParams(preset: FrequencyPreset, p: {
  minute: number; hour: number; day_of_week: number; day_of_month: number; month: number; value: number;
}): ValidationResult {
  const config = buildConfig(preset, p);
  if (config.preset in { hourly: 1, daily: 1, weekly: 1, monthly: 1, yearly: 1 }) {
    return validateSpecificTime(config.preset as FixedFrequencyPreset, config as SpecificTimeParameter);
  }
  return validateIntervalValue(config.preset as IntervalFrequencyPreset, (config as { preset: IntervalFrequencyPreset; value: number }).value);
}

const WEEKDAYS: readonly string[] = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function FrequencySelector({
  presetConfig,
  schedule,
  onChange,
}: FrequencySelectorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  // 从 props 推导当前解析状态
  const parseResult = useMemo<CronParseResult>(() => {
    if (presetConfig) return { kind: "preset", config: presetConfig };
    return cronToPreset(schedule);
  }, [presetConfig, schedule]);

  // 本地状态
  const [selectedPreset, setSelectedPreset] = useState<FrequencyPreset | "custom" | "">("");
  const [params, setParams] = useState({
    minute: 0, hour: 0, day_of_week: 1, day_of_month: 1, month: 1, value: 1,
  });
  const [error, setError] = useState<string | null>(null);
  const [customRaw, setCustomRaw] = useState<string>("");

  // 从 props 同步到本地状态
  useEffect(() => {
    if (parseResult.kind === "preset") {
      setSelectedPreset(parseResult.config.preset);
      setParams(extractParams(parseResult.config));
      setError(null);
    } else if (parseResult.kind === "custom") {
      setSelectedPreset("custom");
      setCustomRaw(parseResult.raw);
      setError(null);
    } else {
      setSelectedPreset("");
      setError(null);
    }
  }, [parseResult]);

  // 切换预设
  const handlePresetChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const value = e.target.value;
    if (value === "" || value === "custom") {
      setSelectedPreset(value);
      setError(null);
      return;
    }
    const preset = value as FrequencyPreset;
    const defaultConfig = createDefaultConfig(preset);
    setSelectedPreset(preset);
    setParams(extractParams(defaultConfig));
    setError(null);
    onChange({ presetConfig: defaultConfig, schedule: presetToCron(defaultConfig) });
  };

  // 参数变更
  const handleParamChange = (field: keyof typeof params, val: number): void => {
    const newParams = { ...params, [field]: val };
    setParams(newParams);
    if (selectedPreset === "" || selectedPreset === "custom") return;
    const preset = selectedPreset as FrequencyPreset;
    const result = validateParams(preset, newParams);
    if (result.ok) {
      setError(null);
      const config = buildConfig(preset, newParams);
      onChange({ presetConfig: config, schedule: presetToCron(config) });
    } else {
      setError(result.reason);
    }
  };

  const hintColor = isDark ? darkTokens.text.hint : "#9a9a9a";
  const errorColor = isDark ? darkTokens.danger : "#d32f2f";

  return (
    <div>
      <style>{`
.wfp-ncp-freq-params{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin-top:8px;}
.wfp-ncp-freq-field{display:flex;flex-direction:column;gap:2px;}
.wfp-ncp-freq-field-label{font-size:10px;font-weight:500;color:${hintColor};letter-spacing:0.06em;}
.wfp-ncp-freq-params__unit{font-size:12px;color:${hintColor};align-self:center;padding-bottom:5px;}
.wfp-ncp-freq-params__error{font-size:11px;color:${errorColor};margin-top:6px;line-height:1.4;}
.wfp-ncp-freq-params__sep{font-size:12px;color:${hintColor};align-self:center;padding-bottom:5px;}
.wfp-ncp-freq-custom{margin-top:8px;font-size:11px;color:${hintColor};line-height:1.5;}
      `}</style>
      <select
        className="wfp-ncp-select"
        value={selectedPreset}
        onChange={handlePresetChange}
      >
        <option value="">请选择频率</option>
        {FREQUENCY_PRESET_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
        {selectedPreset === "custom" && (
          <option value="custom">自定义表达式</option>
        )}
      </select>

      {/* 固定频率参数区 */}
      {selectedPreset === "hourly" && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">第几分钟</span>
            <input className="wfp-ncp-input" type="number" min={0} max={59}
              value={params.minute}
              onChange={(e) => handleParamChange("minute", Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {selectedPreset === "daily" && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">时</span>
            <input className="wfp-ncp-input" type="number" min={0} max={23}
              value={params.hour}
              onChange={(e) => handleParamChange("hour", Number(e.target.value))}
            />
          </div>
          <span className="wfp-ncp-freq-params__sep">:</span>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">分</span>
            <input className="wfp-ncp-input" type="number" min={0} max={59}
              value={params.minute}
              onChange={(e) => handleParamChange("minute", Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {selectedPreset === "weekly" && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">星期</span>
            <select className="wfp-ncp-select" value={params.day_of_week}
              onChange={(e) => handleParamChange("day_of_week", Number(e.target.value))}
            >
              {WEEKDAYS.map((label, i) => i >= 1 && (
                <option key={i} value={i}>{label}</option>
              ))}
            </select>
          </div>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">时</span>
            <input className="wfp-ncp-input" type="number" min={0} max={23}
              value={params.hour}
              onChange={(e) => handleParamChange("hour", Number(e.target.value))}
            />
          </div>
          <span className="wfp-ncp-freq-params__sep">:</span>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">分</span>
            <input className="wfp-ncp-input" type="number" min={0} max={59}
              value={params.minute}
              onChange={(e) => handleParamChange("minute", Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {selectedPreset === "monthly" && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">几日</span>
            <input className="wfp-ncp-input" type="number" min={1} max={31}
              value={params.day_of_month}
              onChange={(e) => handleParamChange("day_of_month", Number(e.target.value))}
            />
          </div>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">时</span>
            <input className="wfp-ncp-input" type="number" min={0} max={23}
              value={params.hour}
              onChange={(e) => handleParamChange("hour", Number(e.target.value))}
            />
          </div>
          <span className="wfp-ncp-freq-params__sep">:</span>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">分</span>
            <input className="wfp-ncp-input" type="number" min={0} max={59}
              value={params.minute}
              onChange={(e) => handleParamChange("minute", Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {selectedPreset === "yearly" && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">月</span>
            <input className="wfp-ncp-input" type="number" min={1} max={12}
              value={params.month}
              onChange={(e) => handleParamChange("month", Number(e.target.value))}
            />
          </div>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">日</span>
            <input className="wfp-ncp-input" type="number" min={1} max={31}
              value={params.day_of_month}
              onChange={(e) => handleParamChange("day_of_month", Number(e.target.value))}
            />
          </div>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">时</span>
            <input className="wfp-ncp-input" type="number" min={0} max={23}
              value={params.hour}
              onChange={(e) => handleParamChange("hour", Number(e.target.value))}
            />
          </div>
          <span className="wfp-ncp-freq-params__sep">:</span>
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">分</span>
            <input className="wfp-ncp-input" type="number" min={0} max={59}
              value={params.minute}
              onChange={(e) => handleParamChange("minute", Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {/* 间隔频率参数区 */}
      {(selectedPreset === "interval_minutes" ||
        selectedPreset === "interval_hours" ||
        selectedPreset === "interval_days" ||
        selectedPreset === "interval_weeks" ||
        selectedPreset === "interval_months") && (
        <div className="wfp-ncp-freq-params">
          <div className="wfp-ncp-freq-field">
            <span className="wfp-ncp-freq-field-label">间隔值</span>
            <input
              className="wfp-ncp-input"
              type="number"
              min={1}
              max={getIntervalLimit(selectedPreset)}
              value={params.value}
              onChange={(e) => handleParamChange("value", Number(e.target.value))}
            />
          </div>
          <span className="wfp-ncp-freq-params__unit">
            {selectedPreset === "interval_minutes" && "分钟"}
            {selectedPreset === "interval_hours" && "小时"}
            {selectedPreset === "interval_days" && "天"}
            {selectedPreset === "interval_weeks" && "周"}
            {selectedPreset === "interval_months" && "月"}
          </span>
        </div>
      )}

      {/* 自定义表达式兼容模式 */}
      {selectedPreset === "custom" && (
        <div className="wfp-ncp-freq-custom">
          当前 cron 表达式（只读）：
          <input className="wfp-ncp-readonly-input" readOnly value={customRaw} style={{ marginTop: '4px' }} />
        </div>
      )}

      {/* 校验错误提示 */}
      {error && <div className="wfp-ncp-freq-params__error">{error}</div>}
    </div>
  );
}