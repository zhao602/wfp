// =============================================================================
// 频率预设领域模块（frequencyPreset.ts）
//
// 职责：定义触发器定时频率预设的类型、选项元数据、cron 转换与校验纯函数。
// 设计意图：零 React 依赖、零 any、零副作用、TypeScript 严格模式、O(1) 查表。
// =============================================================================

// ---------------------------------------------------------------------------
// 类型定义（与后端 FrequencyPresetEnum 逐字符对齐）
// ---------------------------------------------------------------------------

/** 频率预设标识（10 种：5 固定频率 + 5 间隔频率） */
export type FrequencyPreset =
  | "hourly"
  | "daily"
  | "weekly"
  | "monthly"
  | "yearly"
  | "interval_minutes"
  | "interval_hours"
  | "interval_days"
  | "interval_weeks"
  | "interval_months";

/** 固定频率预设（可设置具体时间参数） */
export type FixedFrequencyPreset = "hourly" | "daily" | "weekly" | "monthly" | "yearly";

/** 间隔频率预设（可设置间隔数值） */
export type IntervalFrequencyPreset =
  | "interval_minutes"
  | "interval_hours"
  | "interval_days"
  | "interval_weeks"
  | "interval_months";

/** 固定频率的具体时间参数（按预设粒度收窄可填项） */
export type SpecificTimeParameter =
  | { preset: "hourly"; minute: number }
  | { preset: "daily"; hour: number; minute: number }
  | { preset: "weekly"; day_of_week: number; hour: number; minute: number }
  | { preset: "monthly"; day_of_month: number; hour: number; minute: number }
  | { preset: "yearly"; month: number; day_of_month: number; hour: number; minute: number };

/** 间隔频率的间隔数值参数 */
export interface IntervalValueParameter {
  preset: IntervalFrequencyPreset;
  value: number;
}

/** 预设频率配置（判别联合，判别字段 = preset） */
export type PresetConfiguration = SpecificTimeParameter | IntervalValueParameter;

/** cron 反向解析结果 */
export type CronParseResult =
  | { kind: "preset"; config: PresetConfiguration }
  | { kind: "custom"; raw: string }
  | { kind: "empty" };

/** 校验结果（与既有 NameValidationResult 同构） */
export type ValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

// ---------------------------------------------------------------------------
// 选项元数据与间隔上限常量
// ---------------------------------------------------------------------------

/** 频率预设选项元数据（固定频率在前，间隔频率在后） */
export interface FrequencyPresetOption {
  value: FrequencyPreset;
  label: string;
  category: "fixed" | "interval";
  paramDescription: string;
}

export const FREQUENCY_PRESET_OPTIONS: readonly FrequencyPresetOption[] = [
  { value: "hourly", label: "每小时", category: "fixed", paramDescription: "第几分钟" },
  { value: "daily", label: "每天", category: "fixed", paramDescription: "时:分" },
  { value: "weekly", label: "每周", category: "fixed", paramDescription: "星期几 + 时:分" },
  { value: "monthly", label: "每月", category: "fixed", paramDescription: "几日 + 时:分" },
  { value: "yearly", label: "每年", category: "fixed", paramDescription: "几月几日 + 时:分" },
  { value: "interval_minutes", label: "每隔x分钟", category: "interval", paramDescription: "间隔分钟数" },
  { value: "interval_hours", label: "每隔x小时", category: "interval", paramDescription: "间隔小时数" },
  { value: "interval_days", label: "每隔x天", category: "interval", paramDescription: "间隔天数" },
  { value: "interval_weeks", label: "每隔x周", category: "interval", paramDescription: "间隔周数" },
  { value: "interval_months", label: "每隔x月", category: "interval", paramDescription: "间隔月数" },
] as const;

/** 间隔数值上限（与 spec 6.3 一致） */
const INTERVAL_LIMITS: Record<IntervalFrequencyPreset, number> = {
  interval_minutes: 59,
  interval_hours: 23,
  interval_days: 31,
  interval_weeks: 52,
  interval_months: 12,
};

/** 获取间隔数值上限（O(1) 查表） */
export function getIntervalLimit(preset: IntervalFrequencyPreset): number {
  return INTERVAL_LIMITS[preset];
}

// ---------------------------------------------------------------------------
// presetToCron：预设 → cron 字符串
// ---------------------------------------------------------------------------

/** 将预设频率配置转换为标准 5 段 cron 字符串 */
export function presetToCron(config: PresetConfiguration): string {
  switch (config.preset) {
    case "hourly":
      return `${config.minute} * * * *`;
    case "daily":
      return `${config.minute} ${config.hour} * * *`;
    case "weekly":
      return `${config.minute} ${config.hour} * * ${config.day_of_week === 7 ? 0 : config.day_of_week}`;
    case "monthly":
      return `${config.minute} ${config.hour} ${config.day_of_month} * *`;
    case "yearly":
      return `${config.minute} ${config.hour} ${config.day_of_month} ${config.month} *`;
    case "interval_minutes":
      return `*/${config.value} * * * *`;
    case "interval_hours":
      return `0 */${config.value} * * *`;
    case "interval_days":
      return `0 0 */${config.value} * *`;
    case "interval_weeks":
      return `0 0 * * */${config.value}`;
    case "interval_months":
      return `0 0 1 */${config.value} *`;
  }
}

// ---------------------------------------------------------------------------
// cronToPreset：cron 字符串 → 预设（保守匹配）
// ---------------------------------------------------------------------------

/** 判断字符串是否为纯数字 */
function isNumeric(s: string): boolean {
  return /^\d+$/.test(s);
}

// 判断字符串是否为步进格式（如 N 步进）
function isStep(s: string): boolean {
  return /^\*\/\d+$/.test(s);
}

/** 提取步进数值 */
function stepValue(s: string): number {
  return parseInt(s.slice(2), 10);
}

/** 将 cron 字符串反向解析为预设配置（保守匹配，不可映射返回 custom） */
export function cronToPreset(schedule: string | undefined): CronParseResult {
  if (schedule === undefined || schedule.trim() === "") {
    return { kind: "empty" };
  }

  const parts = schedule.trim().split(/\s+/);
  if (parts.length !== 5) {
    return { kind: "custom", raw: schedule };
  }

  const [m, h, dom, mon, dow] = parts;

  // hourly: M * * * *
  if (isNumeric(m) && h === "*" && dom === "*" && mon === "*" && dow === "*") {
    const minute = parseInt(m, 10);
    if (minute >= 0 && minute <= 59) {
      return { kind: "preset", config: { preset: "hourly", minute } };
    }
  }

  // daily: M H * * *
  if (isNumeric(m) && isNumeric(h) && dom === "*" && mon === "*" && dow === "*") {
    const minute = parseInt(m, 10);
    const hour = parseInt(h, 10);
    if (minute >= 0 && minute <= 59 && hour >= 0 && hour <= 23) {
      return { kind: "preset", config: { preset: "daily", hour, minute } };
    }
  }

  // weekly: M H * * W
  if (isNumeric(m) && isNumeric(h) && dom === "*" && mon === "*" && isNumeric(dow)) {
    const minute = parseInt(m, 10);
    const hour = parseInt(h, 10);
    const w = parseInt(dow, 10);
    if (minute >= 0 && minute <= 59 && hour >= 0 && hour <= 23 && w >= 0 && w <= 6) {
      return { kind: "preset", config: { preset: "weekly", day_of_week: w === 0 ? 7 : w, hour, minute } };
    }
  }

  // monthly: M H D * *
  if (isNumeric(m) && isNumeric(h) && isNumeric(dom) && mon === "*" && dow === "*") {
    const minute = parseInt(m, 10);
    const hour = parseInt(h, 10);
    const dayOfMonth = parseInt(dom, 10);
    if (minute >= 0 && minute <= 59 && hour >= 0 && hour <= 23 && dayOfMonth >= 1 && dayOfMonth <= 31) {
      return { kind: "preset", config: { preset: "monthly", day_of_month: dayOfMonth, hour, minute } };
    }
  }

  // yearly: M H D Mo *
  if (isNumeric(m) && isNumeric(h) && isNumeric(dom) && isNumeric(mon) && dow === "*") {
    const minute = parseInt(m, 10);
    const hour = parseInt(h, 10);
    const dayOfMonth = parseInt(dom, 10);
    const month = parseInt(mon, 10);
    if (minute >= 0 && minute <= 59 && hour >= 0 && hour <= 23 && dayOfMonth >= 1 && dayOfMonth <= 31 && month >= 1 && month <= 12) {
      return { kind: "preset", config: { preset: "yearly", month, day_of_month: dayOfMonth, hour, minute } };
    }
  }

  // interval_minutes: */V * * * *
  if (isStep(m) && h === "*" && dom === "*" && mon === "*" && dow === "*") {
    const value = stepValue(m);
    if (value >= 1 && value <= 59) {
      return { kind: "preset", config: { preset: "interval_minutes", value } };
    }
  }

  // interval_hours: 0 */V * * *
  if (m === "0" && isStep(h) && dom === "*" && mon === "*" && dow === "*") {
    const value = stepValue(h);
    if (value >= 1 && value <= 23) {
      return { kind: "preset", config: { preset: "interval_hours", value } };
    }
  }

  // interval_days: 0 0 */V * *
  if (m === "0" && h === "0" && isStep(dom) && mon === "*" && dow === "*") {
    const value = stepValue(dom);
    if (value >= 1 && value <= 31) {
      return { kind: "preset", config: { preset: "interval_days", value } };
    }
  }

  // interval_weeks: 0 0 * * */V
  if (m === "0" && h === "0" && dom === "*" && mon === "*" && isStep(dow)) {
    const value = stepValue(dow);
    if (value >= 1 && value <= 52) {
      return { kind: "preset", config: { preset: "interval_weeks", value } };
    }
  }

  // interval_months: 0 0 1 */V *
  if (m === "0" && h === "0" && dom === "1" && isStep(mon) && dow === "*") {
    const value = stepValue(mon);
    if (value >= 1 && value <= 12) {
      return { kind: "preset", config: { preset: "interval_months", value } };
    }
  }

  return { kind: "custom", raw: schedule };
}

// ---------------------------------------------------------------------------
// validateIntervalValue：间隔数值即时校验
// ---------------------------------------------------------------------------

/** 校验间隔数值为正整数且在约定上限内 */
export function validateIntervalValue(
  preset: IntervalFrequencyPreset,
  value: number,
): ValidationResult {
  if (!Number.isInteger(value) || value < 1) {
    return { ok: false, reason: "间隔数值须为正整数" };
  }
  const limit = getIntervalLimit(preset);
  if (value > limit) {
    return { ok: false, reason: `间隔数值不能超过 ${limit}` };
  }
  return { ok: true };
}

// ---------------------------------------------------------------------------
// validateSpecificTime：具体时间即时校验
// ---------------------------------------------------------------------------

/** 各月天数上限（2 月取 29 兼容闰年） */
const DAYS_IN_MONTH: readonly number[] = [
  0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
];

/** 校验固定频率的具体时间参数按预设粒度合法 */
export function validateSpecificTime(
  preset: FixedFrequencyPreset,
  params: SpecificTimeParameter,
): ValidationResult {
  switch (preset) {
    case "hourly": {
      const p = params as { preset: "hourly"; minute: number };
      if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 59) {
        return { ok: false, reason: "分钟须为 0–59 的整数" };
      }
      return { ok: true };
    }
    case "daily": {
      const p = params as { preset: "daily"; hour: number; minute: number };
      if (!Number.isInteger(p.hour) || p.hour < 0 || p.hour > 23) {
        return { ok: false, reason: "小时须为 0–23 的整数" };
      }
      if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 59) {
        return { ok: false, reason: "分钟须为 0–59 的整数" };
      }
      return { ok: true };
    }
    case "weekly": {
      const p = params as { preset: "weekly"; day_of_week: number; hour: number; minute: number };
      if (!Number.isInteger(p.day_of_week) || p.day_of_week < 1 || p.day_of_week > 7) {
        return { ok: false, reason: "星期须为 1–7 的整数" };
      }
      if (!Number.isInteger(p.hour) || p.hour < 0 || p.hour > 23) {
        return { ok: false, reason: "小时须为 0–23 的整数" };
      }
      if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 59) {
        return { ok: false, reason: "分钟须为 0–59 的整数" };
      }
      return { ok: true };
    }
    case "monthly": {
      const p = params as { preset: "monthly"; day_of_month: number; hour: number; minute: number };
      if (!Number.isInteger(p.day_of_month) || p.day_of_month < 1 || p.day_of_month > 31) {
        return { ok: false, reason: "日须为 1–31 的整数" };
      }
      if (!Number.isInteger(p.hour) || p.hour < 0 || p.hour > 23) {
        return { ok: false, reason: "小时须为 0–23 的整数" };
      }
      if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 59) {
        return { ok: false, reason: "分钟须为 0–59 的整数" };
      }
      return { ok: true };
    }
    case "yearly": {
      const p = params as { preset: "yearly"; month: number; day_of_month: number; hour: number; minute: number };
      if (!Number.isInteger(p.month) || p.month < 1 || p.month > 12) {
        return { ok: false, reason: "月须为 1–12 的整数" };
      }
      const maxDay = DAYS_IN_MONTH[p.month];
      if (!Number.isInteger(p.day_of_month) || p.day_of_month < 1 || p.day_of_month > maxDay) {
        return { ok: false, reason: `${p.month} 月没有 ${p.day_of_month} 日` };
      }
      if (!Number.isInteger(p.hour) || p.hour < 0 || p.hour > 23) {
        return { ok: false, reason: "小时须为 0–23 的整数" };
      }
      if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 59) {
        return { ok: false, reason: "分钟须为 0–59 的整数" };
      }
      return { ok: true };
    }
  }
}