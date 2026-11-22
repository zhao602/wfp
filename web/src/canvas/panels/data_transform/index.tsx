import type { CSSProperties } from "react";
import { darkTokens, useThemeMode } from "../../../theme/themeMode";
import type {
  DataTransformNodeConfiguration,
  DataTransformOperation,
  DataTransformOperationParams,
  DataTransformStep,
  DataTransformOutputFormat,
  DataTransformOutputFormatParams,
  MaskingCustomPattern,
} from "../../../workflow-definition/definitionTypes";
import { getPresetExpression, isPresetExpression, buildOperationChange } from "../../dataTransformPresetExpressions";
import OperationChainPanel from "../../OperationChainPanel";
import ConditionGroupEditor from "../../ConditionGroupEditor";
import {
  getOperationMeta,
  getOperationsByCategory,
  OPERATION_CATEGORIES,
  OPERATION_CATEGORY_LABELS,
  OUTPUT_FORMAT_OPTIONS,
  type ParamFieldSpec,
} from "../../dataTransformOperations";
import KeyValueMapEditor from "../../KeyValueMapEditor";
import { PanelIcon } from "../shared/PanelIcon";
import {
  Field,
  FieldCheckboxGroup,
  TipDot,
} from "../shared/Field";

// ---------------------------------------------------------------------------
// 数据转换配置表单（expand_data_transform_ops DC-09 / FR-21~FR-24）
// ---------------------------------------------------------------------------

/** 数据转换参数取值联合（DataTransformOperationParams 全字段值域，含 undefined 缺省） */
type DataTransformParamValue = DataTransformOperationParams[keyof DataTransformOperationParams];

/** 派生操作链步骤：operations 非空以链为准；否则旧配置按"链长 1"呈现（顶层 operation/params） */
function deriveDataTransformSteps(config: DataTransformNodeConfiguration): DataTransformStep[] {
  if (config.operations && config.operations.length > 0) {
    return config.operations.map((step) => ({
      operation: step.operation,
      params: step.params ?? {},
    }));
  }
  return [{ operation: config.operation, params: config.params ?? {} }];
}

/** 读取字符串型参数（非字符串值安全降级空串） */
function paramAsString(
  params: DataTransformOperationParams | undefined,
  field: keyof DataTransformOperationParams,
): string {
  const value = params ? params[field] : undefined;
  return typeof value === "string" ? value : "";
}

/** 读取字符串列表型参数（非数组或异构数组安全降级空列表） */
function paramAsStringList(
  params: DataTransformOperationParams | undefined,
  field: keyof DataTransformOperationParams,
): string[] {
  const value = params ? params[field] : undefined;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.every((item) => typeof item === "string") ? (value as string[]) : [];
}

/** 读取数值型参数（数字输入框展示字符串；非数值安全降级空串） */
function paramAsNumberString(
  params: DataTransformOperationParams | undefined,
  field: keyof DataTransformOperationParams,
): string {
  const value = params ? params[field] : undefined;
  return typeof value === "number" ? String(value) : "";
}

/** 读取键值映射型参数（非纯对象安全降级空映射） */
function paramAsRecord(
  params: DataTransformOperationParams | undefined,
  field: keyof DataTransformOperationParams,
): Record<string, string> {
  const value = params ? params[field] : undefined;
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value;
  }
  return {};
}

/** 读取自定义脱敏模板列表为「正则 → 掩码模板」映射（异构条目安全跳过） */
function paramAsPatternPairs(
  params: DataTransformOperationParams | undefined,
  field: keyof DataTransformOperationParams,
): Record<string, string> {
  const value = params ? params[field] : undefined;
  if (!Array.isArray(value)) {
    return {};
  }
  const pairs: Record<string, string> = {};
  for (const item of value) {
    if (typeof item === "object" && item !== null) {
      pairs[item.pattern] = item.mask_template;
    }
  }
  return pairs;
}

/** 「正则 → 掩码模板」映射转回 MaskingCustomPattern[]（写回 custom_patterns） */
function recordToPatternPairs(record: Record<string, string>): MaskingCustomPattern[] {
  return Object.entries(record).map(([pattern, maskTemplate]): MaskingCustomPattern => ({
    pattern,
    mask_template: maskTemplate,
  }));
}

/** 构造单字段参数补丁（动态字段名 → Partial 的唯一收窄转换点） */
function buildParamPatch(
  field: keyof DataTransformOperationParams,
  value: DataTransformParamValue,
): Partial<DataTransformOperationParams> {
  return Object.assign({} as DataTransformOperationParams, { [field]: value });
}

export function DataTransformConfigForm({
  config,
  onChange,
}: {
  config: DataTransformNodeConfiguration;
  onChange: (config: DataTransformNodeConfiguration) => void;
}) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  const steps = deriveDataTransformSteps(config);
  const operationsByCategory = getOperationsByCategory();
  const activeFormat: DataTransformOutputFormat = config.output_format ?? "dataset";
  const formatParams = config.output_format_params ?? {};

  const secondaryTextColor = isDark ? darkTokens.text.secondary : "#666";
  const borderColor = isDark ? darkTokens.surface.divider : "#e0e0e0";
  const buttonBg = isDark ? darkTokens.surface.bgHover : "#f5f5f5";
  const buttonActiveBg = isDark ? darkTokens.surface.divider : "#dcdcdc";

  /** 链写回：链长 1 保持旧单步形态（顶层 operation/params、删除 operations）；多步写 operations 并镜像首步 */
  const writeSteps = (nextSteps: DataTransformStep[]): void => {
    if (nextSteps.length === 1) {
      const only = nextSteps[0];
      const next: DataTransformNodeConfiguration = {
        ...config,
        operation: only.operation,
        params: only.params ?? {},
      };
      delete next.operations;
      onChange(next);
      return;
    }
    onChange({
      ...config,
      operations: nextSteps,
      operation: nextSteps[0].operation,
      params: nextSteps[0].params ?? {},
    });
  };

  /** 更新指定步骤参数（浅合并补丁；undefined 值键在导出序列化时剔除） */
  const updateStepParams = (stepIndex: number, patch: Partial<DataTransformOperationParams>): void => {
    writeSteps(
      steps.map((step, i) =>
        i === stepIndex ? { ...step, params: { ...(step.params ?? {}), ...patch } } : step,
      ),
    );
  };

  /** 切换步骤操作：以伪单步配置复用 buildOperationChange（仅补空、不覆盖语义逐字保持） */
  const updateStepOperation = (stepIndex: number, nextOperation: DataTransformOperation): void => {
    const step = steps[stepIndex];
    const pseudoConfig: DataTransformNodeConfiguration = {
      node_type: "data_transform",
      operation: step.operation,
      input_dataset_reference: config.input_dataset_reference,
      params: step.params ?? {},
    };
    const changed = buildOperationChange(pseudoConfig, nextOperation);
    writeSteps(
      steps.map((old, i) => (i === stepIndex ? { operation: nextOperation, params: changed.params } : old)),
    );
  };

  /** 切换输出格式：dataset 删除 output_format/output_format_params（缺省形态不落盘）；其余写入并初始化参数对象 */
  const setOutputFormat = (format: DataTransformOutputFormat): void => {
    if (format === "dataset") {
      const next: DataTransformNodeConfiguration = { ...config };
      delete next.output_format;
      delete next.output_format_params;
      onChange(next);
      return;
    }
    onChange({ ...config, output_format: format, output_format_params: config.output_format_params ?? {} });
  };

  /** 更新输出格式参数（浅合并补丁） */
  const updateFormatParams = (patch: Partial<DataTransformOutputFormatParams>): void => {
    onChange({
      ...config,
      output_format_params: { ...(config.output_format_params ?? {}), ...patch },
    });
  };

  /** 按元数据控件类型渲染单个参数字段 */
  const renderParamControl = (spec: ParamFieldSpec, step: DataTransformStep, stepIndex: number): JSX.Element => {
    const params = step.params ?? {};
    switch (spec.control) {
      case "expression": {
        const value = paramAsString(params, spec.field);
        return (
          <>
            <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
              <textarea
                className="wfp-ncp-input"
                value={value}
                onChange={(e) => updateStepParams(stepIndex, buildParamPatch(spec.field, e.target.value))}
                rows={3}
                style={{ width: "100%" }}
              />
            </Field>
            <button
              type="button"
              className="wfp-ncp-btn wfp-ncp-btn--ghost wfp-ncp-btn--icon"
              disabled={isPresetExpression(step.operation, value)}
              title="恢复默认表达式"
              aria-label="恢复默认表达式"
              style={{ marginTop: "6px" }}
              onClick={() =>
                updateStepParams(
                  stepIndex,
                  buildParamPatch(spec.field, getPresetExpression(step.operation) ?? ""),
                )
              }
            >
              <PanelIcon name="reset" size={13} />
            </button>
          </>
        );
      }
      case "textarea":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <textarea
              className="wfp-ncp-input"
              value={paramAsString(params, spec.field)}
              onChange={(e) => updateStepParams(stepIndex, buildParamPatch(spec.field, e.target.value))}
              rows={3}
              style={{ width: "100%" }}
            />
          </Field>
        );
      case "number":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <input
              type="number"
              className="wfp-ncp-input"
              value={paramAsNumberString(params, spec.field)}
              placeholder={spec.placeholder}
              onChange={(e) => {
                const raw = e.target.value.trim();
                const parsed = Number(raw);
                updateStepParams(
                  stepIndex,
                  buildParamPatch(
                    spec.field,
                    raw === "" || !Number.isFinite(parsed) ? undefined : parsed,
                  ),
                );
              }}
            />
          </Field>
        );
      case "select":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <select
              className="wfp-ncp-select"
              value={paramAsString(params, spec.field)}
              onChange={(e) =>
                updateStepParams(
                  stepIndex,
                  buildParamPatch(spec.field, e.target.value === "" ? undefined : e.target.value),
                )
              }
            >
              <option value="">—</option>
              {(spec.options ?? []).map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </Field>
        );
      case "stringList":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <input
              type="text"
              className="wfp-ncp-input"
              value={paramAsStringList(params, spec.field).join(", ")}
              placeholder={spec.placeholder}
              onChange={(e) =>
                updateStepParams(
                  stepIndex,
                  buildParamPatch(
                    spec.field,
                    e.target.value.split(",").map((s) => s.trim()).filter((s) => s.length > 0),
                  ),
                )
              }
            />
          </Field>
        );
      case "stringSet":
        return (
          <FieldCheckboxGroup nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            {(spec.options ?? []).map((option) => (
              <label key={option} style={{ display: "block" }}>
                <input
                  type="checkbox"
                  checked={paramAsStringList(params, spec.field).includes(option)}
                  onChange={(e) => {
                    const current = paramAsStringList(params, spec.field);
                    const next = e.target.checked ? [...current, option] : current.filter((item) => item !== option);
                    updateStepParams(stepIndex, buildParamPatch(spec.field, next));
                  }}
                />
                {option}
              </label>
            ))}
          </FieldCheckboxGroup>
        );
      case "kvMap":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <KeyValueMapEditor
              value={paramAsRecord(params, spec.field)}
              onChange={(next) => updateStepParams(stepIndex, buildParamPatch(spec.field, next))}
              keyLabel="旧字段名"
              valueLabel="新字段名"
              valueColumnMode={{ kind: "freeText" }}
            />
          </Field>
        );
      case "patternPairs":
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <KeyValueMapEditor
              value={paramAsPatternPairs(params, spec.field)}
              onChange={(next) => updateStepParams(stepIndex, buildParamPatch(spec.field, recordToPatternPairs(next)))}
              keyLabel="正则模式"
              valueLabel="掩码模板"
              valueColumnMode={{ kind: "freeText" }}
              emptyValueWarning="未填掩码模板"
            />
          </Field>
        );
      case "text":
      default:
        return (
          <Field nodeType="data_transform" field={spec.field} label={spec.label} index={stepIndex}>
            <input
              type="text"
              className="wfp-ncp-input"
              value={paramAsString(params, spec.field)}
              placeholder={spec.placeholder}
              onChange={(e) => updateStepParams(stepIndex, buildParamPatch(spec.field, e.target.value))}
            />
          </Field>
        );
    }
  };

  /** 渲染单步骤内容：操作选择（四分类分组）+ 降级说明 + 参数区 + 条件配置区（仅条件型操作） */
  const renderStepContent = (step: DataTransformStep, stepIndex: number): JSX.Element => {
    const meta = getOperationMeta(step.operation);
    return (
      <>
        <Field nodeType="data_transform" field="operation" label="操作类型" index={stepIndex}>
          <select
            className="wfp-ncp-select"
            value={step.operation}
            onChange={(e) => updateStepOperation(stepIndex, e.target.value as DataTransformOperation)}
          >
            {OPERATION_CATEGORIES.map((category) => (
              <optgroup key={category} label={OPERATION_CATEGORY_LABELS[category]}>
                {operationsByCategory[category].map((opMeta) => (
                  <option key={opMeta.id} value={opMeta.id}>
                    {opMeta.label}
                  </option>
                ))}
              </optgroup>
            ))}
            {!meta && step.operation ? (
              <option value={step.operation}>未知操作：{step.operation}</option>
            ) : null}
          </select>
        </Field>
        {meta ? (
          <div className="wfp-ncp-inline-row">
            <TipDot text={meta.degradableNote} label="操作降级行为说明" />
            <span className="wfp-ncp-inline-label">降级行为</span>
          </div>
        ) : step.operation ? (
          <div className="wfp-ncp-inline-row wfp-ncp-inline-row--warn">
            <span className="wfp-ncp-inline-label">未知操作：{step.operation}（保留原值，请检查配置来源）</span>
          </div>
        ) : null}
        {meta?.paramFields.map((spec) => (
          <div key={spec.field}>{renderParamControl(spec, step, stepIndex)}</div>
        ))}
        {meta?.needsCondition ? (
          <ConditionGroupEditor
            onCompile={(expression) =>
              updateStepParams(stepIndex, buildParamPatch("filter_condition", expression))
            }
          />
        ) : null}
      </>
    );
  };

  /** 输出格式分段按钮样式（aria-pressed 承载选中态） */
  const formatButtonStyle = (active: boolean): CSSProperties => ({
    fontSize: "11px",
    padding: "2px 8px",
    color: secondaryTextColor,
    background: active ? buttonActiveBg : buttonBg,
    border: `1px solid ${borderColor}`,
  });

  return (
    <div style={{ fontSize: "12px" }}>
      <FieldCheckboxGroup nodeType="data_transform" field="operations" label="操作链">
        <OperationChainPanel
          steps={steps}
          onChangeSteps={writeSteps}
          renderStep={({ step, stepIndex }) => renderStepContent(step, stepIndex)}
        />
      </FieldCheckboxGroup>
      <Field nodeType="data_transform" field="input_dataset_reference" label="输入数据集引用">
        <input
          type="text"
          className="wfp-ncp-input"
          value={config.input_dataset_reference ?? ""}
          onChange={(e) => onChange({ ...config, input_dataset_reference: e.target.value })}
        />
      </Field>
      <FieldCheckboxGroup nodeType="data_transform" field="output_format" label="输出格式">
        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
          {OUTPUT_FORMAT_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className="wfp-dtf-format"
              data-format={option.id}
              aria-pressed={activeFormat === option.id}
              style={formatButtonStyle(activeFormat === option.id)}
              onClick={() => setOutputFormat(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </FieldCheckboxGroup>
      {activeFormat !== "dataset" && (
        <>
          <div className="wfp-dtf-format-note wfp-ncp-inline-row">
            <TipDot
              text={`下游需按对应输出形态引用该节点输出（${
                OUTPUT_FORMAT_OPTIONS.find((option) => option.id === activeFormat)?.label ?? activeFormat
              }）`}
              label="输出形态引用说明"
            />
            <span className="wfp-ncp-inline-label">下游引用方式</span>
          </div>
          {activeFormat === "single_object" && (
            <Field nodeType="data_transform" field="record_index" label="目标记录索引">
              <input
                type="number"
                className="wfp-ncp-input"
                value={formatParams.record_index === undefined ? "" : String(formatParams.record_index)}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  const parsed = Number(raw);
                  updateFormatParams(
                    raw === "" || !Number.isFinite(parsed)
                      ? { record_index: undefined }
                      : { record_index: parsed },
                  );
                }}
              />
            </Field>
          )}
          {activeFormat === "text" && (
            <>
              <Field nodeType="data_transform" field="text_field" label="拼接字段名">
                <input
                  type="text"
                  className="wfp-ncp-input"
                  value={formatParams.text_field ?? ""}
                  onChange={(e) => updateFormatParams({ text_field: e.target.value })}
                />
              </Field>
              <Field nodeType="data_transform" field="text_separator" label="行间分隔符">
                <input
                  type="text"
                  className="wfp-ncp-input"
                  value={formatParams.text_separator ?? ""}
                  onChange={(e) => updateFormatParams({ text_separator: e.target.value })}
                />
              </Field>
            </>
          )}
          {activeFormat === "kv" && (
            <>
              <Field nodeType="data_transform" field="kv_key_field" label="键字段名">
                <input
                  type="text"
                  className="wfp-ncp-input"
                  value={formatParams.kv_key_field ?? ""}
                  onChange={(e) => updateFormatParams({ kv_key_field: e.target.value })}
                />
              </Field>
              <Field nodeType="data_transform" field="kv_value_field" label="值字段名">
                <input
                  type="text"
                  className="wfp-ncp-input"
                  value={formatParams.kv_value_field ?? ""}
                  onChange={(e) => updateFormatParams({ kv_value_field: e.target.value })}
                />
              </Field>
            </>
          )}
        </>
      )}
    </div>
  );
}
